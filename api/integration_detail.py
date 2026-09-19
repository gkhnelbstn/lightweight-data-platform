"""One record, or one held row, opened from the Integration tab.

A line in the conflict log says a value lost; it does not say what the record
looks like now, who set each field, or what led there. This is that second
look, read-only like the tab (api/integration.py):

* **a record**: every field with the system that set it and when, its code in
  each system, the changes that reached the hub for it -- an update as
  `from -> to`, not two rows of before and after -- and its conflicts;
* **a held row**: why the hub could not place it, the records its rule
  matched, and the `hub.link` call that settles each choice.

Classified values are masked here as everywhere (core/sample.py).
"""
from __future__ import annotations

import json

import psycopg
from fastapi import APIRouter, HTTPException
from psycopg import sql
from psycopg.rows import dict_row

from api import integration as tab
from core import flow_jobs, sample
from core import flows as flowmod
from core.bootstrap_db import admin_dsn
from core.hub import META
from core.mapping import _properties

router = APIRouter()
HISTORY = 60


def _hub(hub_id: str) -> tuple[dict, str, str, dict[str, str], set[str]]:
    by_id, _ = flow_jobs.load(tab.DIRECTORY)
    contract = by_id.get(hub_id)
    if flowmod.hub_of(contract) is None:
        raise HTTPException(404, f"{hub_id} is not a hub")
    key = [n for n, p in _properties(contract).items() if p.get("primaryKey")][0]
    return (contract, contract["schema"][0]["name"], key, flowmod.keys_of(contract),
            sample.classified(contract))


def _connect(contract: dict):
    server = next(s for s in contract["servers"] if s["type"].startswith("postgres"))
    return psycopg.connect(admin_dsn(server["host"], server.get("port", 5432),
                                     server["database"]),
                           row_factory=dict_row, connect_timeout=3)


def _show(field: str, value, hidden: set[str]):
    return sample.MASK if field in hidden and value is not None else value


def history(rows: list[dict], skip: set[str], hidden: set[str]) -> list[dict]:
    """Inbox rows, oldest first, as what happened: an update's before and
    after image become one entry listing only the fields that changed."""
    out, before = [], {}
    for r in rows:
        carried = [f for f in (r["fields"] or "").split(",") if f and f not in skip]
        values = {f: r["row"].get(f) for f in carried}
        # What the hub did with it (core/hub.sql): applied, echo of its own
        # write, lost to a later edit, held... None for rows older than that.
        entry = {"at": r["landed_at"], "committed_at": tab._ms(r["source_ms"]),
                 "system": r["system"], "outcome": r["row"].get("outcome")}
        if r["row_kind"] == "UPDATE_BEFORE":
            before[r["system"]] = values
            continue
        if r["row_kind"] == "UPDATE_AFTER":
            old = before.pop(r["system"], {})
            entry["kind"] = "update"
            entry["changes"] = [{"field": f, "from": _show(f, old.get(f), hidden),
                                 "to": _show(f, v, hidden)}
                                for f, v in values.items() if old.get(f) != v]
        else:
            entry["kind"] = "insert" if r["row_kind"] == "INSERT" else "delete"
            entry["changes"] = [{"field": f, "from": None, "to": _show(f, v, hidden)}
                                for f, v in values.items()
                                if v is not None and entry["kind"] == "insert"]
        out.append(entry)
    return out


@router.get("/api/integration/record")
def record(hub: str, key: str) -> dict:
    contract, entity, keycol, codes, hidden = _hub(hub)
    wanted = json.loads(key)
    skip = {keycol, *codes.values(), *META}
    with _connect(contract) as cx:
        row = cx.execute(sql.SQL("select to_jsonb(g) as g from hub.{} g where {} = %s").format(
            sql.Identifier(entity), sql.Identifier(keycol)), (wanted.get(keycol),)).fetchone()
        golden = row["g"] if row else None
        gone = None if golden else cx.execute(
            "select deleted_ms, deleted_by, aliases from hub.tombstone "
            "where entity = %s and key = %s::jsonb", (entity, key)).fetchone()
        known = golden or (gone or {}).get("aliases") or {}
        mine = {s: known.get(c) for s, c in codes.items()} if codes else {keycol: wanted.get(keycol)}
        # A system's rows are found by its own code: that is all its rows carry.
        where = [sql.SQL("(split_part(system, '.', 1) = {} and {} = {})").format(
                     sql.Literal(s), sql.Identifier(codes[s]), sql.Literal(v))
                 for s, v in mine.items() if v is not None and s in codes]
        if not codes:
            where = [sql.SQL("{} = {}").format(sql.Identifier(keycol), sql.Literal(wanted.get(keycol)))]
        rows = cx.execute(sql.SQL(
            "select * from (select id, system, row_kind, source_ms, landed_at, fields, "
            "to_jsonb(i) as row from hub.{} i where {} order by id desc limit {}) x order by id"
        ).format(sql.Identifier(f"{entity}_inbox"), sql.SQL(" or ").join(where),
                 sql.Literal(HISTORY))).fetchall() if where else []
        conflicts = cx.execute(
            "select at, field, kept, kept_by, kept_ms, lost, lost_by, lost_ms, reason "
            "from hub.conflict where entity = %s and key = %s::jsonb order by id desc",
            (entity, key)).fetchall()
    fields = {} if not golden else {
        f: {"value": _show(f, v, hidden), "by": golden["_by"].get(f),
            "at": tab._ms(int(golden["_at"][f])) if f in golden["_at"] else None}
        for f, v in golden.items() if f not in skip}
    for c in conflicts:
        c["kept"], c["lost"] = (tab._masked(c["kept"], c["field"], hidden),
                                tab._masked(c["lost"], c["field"], hidden))
        c["kept_at"], c["lost_at"] = tab._ms(c.pop("kept_ms")), tab._ms(c.pop("lost_ms"))
    return {"key": wanted, "codes": mine, "fields": fields,
            "deleted": gone and {"at": tab._ms(gone["deleted_ms"]), "by": gone["deleted_by"]},
            "history": history(rows, skip, hidden), "conflicts": conflicts}


@router.get("/api/integration/held")
def held(hub: str, system: str, local: str) -> dict:
    contract, entity, keycol, codes, hidden = _hub(hub)
    with _connect(contract) as cx:
        row = cx.execute("select row, reason, at from hub.unmatched where entity = %s "
                         "and system = %s and local = %s::jsonb", (entity, system, local)).fetchone()
        if row is None:
            raise HTTPException(404, "not held any more")
        rule = (cx.execute("select link_by from hub.system where entity = %s and system = %s",
                           (entity, system)).fetchone() or {}).get("link_by") or []
        values = {c: row["row"].get(c) for c in rule}
        candidates = [] if not rule or None in values.values() else cx.execute(
            sql.SQL("select to_jsonb(g) as g from hub.{} g where {} order by {}").format(
                sql.Identifier(entity),
                sql.SQL(" and ").join(sql.SQL("{}::text = {}").format(
                    sql.Identifier(c), sql.Literal(str(v))) for c, v in values.items()),
                sql.Identifier(keycol))).fetchall()
    skip = {keycol, *codes.values(), *META}

    def link(record_key) -> str:
        target = "" if record_key is None else f", '{json.dumps(record_key)}'"
        return f"select hub.link('{entity}', '{system}', '{local}'{target});"

    return {
        "reason": row["reason"], "at": row["at"],
        "row": {f: _show(f, v, hidden) for f, v in row["row"].items() if f not in META},
        "rule": {c: _show(c, v, hidden) for c, v in values.items()},
        "candidates": [{"key": {keycol: g[keycol]},
                        "codes": {s: g.get(c) for s, c in codes.items()},
                        "fields": {f: _show(f, v, hidden) for f, v in g.items() if f not in skip},
                        "link": link({keycol: g[keycol]})}
                       for g in (c["g"] for c in candidates)],
        "link_new": link(None)}
