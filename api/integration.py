"""The two-way integration, as ODD's Integration tab shows it. #78, ADR 0021.

Read-only, and asked server-side so no credential reaches the browser. Three
sources, each allowed to be down without the others -- a missing piece is a
message on the tab, not a 500 for all of it:

* the hub contracts and their flow files: what is connected to what;
* SeaTunnel's REST API: whether each flow's job runs, and what it moved;
* the hub database: what arrived and how late, what lost a conflict, what
  waits for a person, and what was deleted.

SeaTunnel's own console names jobs by id and shows vertices; this names them
by flow and says which systems they connect.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from fastapi import APIRouter
from psycopg import sql
from psycopg.rows import dict_row

from core import flow_jobs, flow_schema, sample
from core import flows as flowmod
from core.bootstrap_db import admin_dsn
from core.mapping import _properties

router = APIRouter()
ROOT = Path(__file__).resolve().parents[1]
# Where the hub contracts and their flows live: `contracts/` in a deployment,
# the demo's own directory in this repository's compose.
DIRECTORY = ROOT / os.getenv("INTEGRATION_DIR", "contracts")
SEATUNNEL = os.getenv("SEATUNNEL_URL", "http://seatunnel:8080")
LIMIT = 50


def _get(path: str):
    with urllib.request.urlopen(f"{SEATUNNEL}{path}", timeout=5) as resp:
        return json.loads(resp.read() or b"[]")


def jobs() -> tuple[dict[str, dict], str | None]:
    """Each flow's newest job, by name: the running one, else how the last
    one ended. A flow never submitted has none."""
    try:
        finished, running = _get("/finished-jobs"), _get("/running-jobs")
    except Exception as exc:
        return {}, f"{exc.__class__.__name__}: {exc}"
    newest: dict[str, dict] = {}
    for job in sorted(finished, key=lambda j: j.get("finishTime") or ""):
        newest[job.get("jobName")] = job
    for job in running:
        newest[job.get("jobName")] = job
    out = {}
    for name, job in newest.items():
        metrics = job.get("metrics") or {}
        out[name] = {
            "status": job.get("jobStatus"), "id": job.get("jobId"),
            "started": job.get("startTime"), "finished": job.get("finishTime"),
            "error": root_cause(job.get("errorMsg")),
            "read": int(metrics.get("SourceReceivedCount") or 0),
            "written": sum(int(v) for v in (metrics.get("TableSinkWriteCount") or {}).values())}
    return out, None


def root_cause(error: str | None) -> str | None:
    """SeaTunnel's error is a Java stack trace of kilobytes; its last
    `Caused by` is the sentence a person needs ("Invalid column name ...")."""
    if not error:
        return error
    causes = [line.strip()[len("Caused by: "):] for line in error.splitlines()
              if line.strip().startswith("Caused by: ")]
    last = (causes[-1] if causes else error.splitlines()[0]).split(": ", 1)[-1]
    return last[:300]


def _ms(ms: int | None) -> str | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def _masked(value, field: str, hidden: set[str]):
    """A classified value is never shown, here as in failing rows
    (core/sample.py): a conflict over a tax number shows that there was one."""
    if isinstance(value, dict):
        return {k: sample.MASK if k in hidden and v is not None else v
                for k, v in value.items()}
    return sample.MASK if field in hidden and value is not None else value


def hub_state(contract: dict) -> dict:
    """What the hub database says about one entity."""
    entity = contract["schema"][0]["name"]
    server = next(s for s in contract["servers"] if s["type"].startswith("postgres"))
    key = [n for n, p in _properties(contract).items() if p.get("primaryKey")][0]
    codes = flowmod.keys_of(contract)
    golden, inbox = sql.Identifier(entity), sql.Identifier(f"{entity}_inbox")
    dsn = admin_dsn(server["host"], server.get("port", 5432), server["database"])
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=3) as cx:
        # ponytail: a pass over the whole inbox; it is append-only and grows,
        # so an index on (system, id) is the fix when this page gets slow.
        arriving = cx.execute(sql.SQL(
            "select system, count(*) filter (where landed_at > now() - interval '1 hour') "
            "as last_hour, (array_agg(source_ms order by id desc))[1] as source_ms, "
            "max(landed_at) as landed_at from hub.{} group by system").format(inbox)).fetchall()
        conflicts = cx.execute(
            "select at, key, field, kept, kept_by, kept_ms, lost, lost_by, lost_ms, reason "
            "from hub.conflict where entity = %s order by id desc limit %s",
            (entity, LIMIT)).fetchall()
        held = cx.execute("select system, local, reason, row, at from hub.unmatched "
                          "where entity = %s order by at desc", (entity,)).fetchall()
        deleted = cx.execute(
            "select key, deleted_ms, deleted_by, aliases from hub.tombstone "
            "where entity = %s order by deleted_ms desc limit %s", (entity, LIMIT)).fetchall()
        # A record is its systems' codes to a person, not the hub's number.
        ids = [c["key"].get(key) for c in conflicts]
        labels = {r[key]: {s: r[c] for s, c in codes.items()} for r in cx.execute(
            sql.SQL("select {}, {} from hub.{} where {} = any(%s)").format(
                sql.Identifier(key),
                sql.SQL(", ").join(map(sql.Identifier, codes.values())) or sql.SQL("null"),
                golden, sql.Identifier(key)), (ids,)).fetchall()} if codes else {}
        records = cx.execute(sql.SQL("select count(*) as n from hub.{}").format(golden)
                             ).fetchone()["n"]
        # One bar per hour, so "is anything arriving" is a glance rather than
        # a number that could be an hour or a week old. ponytail: a pass over
        # the inbox, like `arriving` above and cheap for the same reason --
        # an index on (landed_at) is the fix when it is not.
        activity = cx.execute(sql.SQL(
            "select date_trunc('hour', landed_at) as hour, system, count(*) as n "
            "from hub.{} where landed_at > now() - interval '24 hours' "
            "group by 1, 2 order by 1").format(inbox)).fetchall()
    hidden = sample.classified(contract)
    for h in held:
        h["row"] = _masked(h["row"], "", hidden)
    for row in arriving:
        row["committed_at"] = _ms(row.pop("source_ms"))
    for d in deleted:
        d["deleted_at"] = _ms(d.pop("deleted_ms"))
        aliases = d.pop("aliases") or {}
        d["codes"] = {s: aliases.get(c) for s, c in codes.items()} or None
    gone = {d["key"].get(key): d["codes"] for d in deleted}
    for c in conflicts:
        k = c["key"].get(key)
        c["codes"] = labels.get(k) or gone.get(k)
        c["kept_at"], c["lost_at"] = _ms(c.pop("kept_ms")), _ms(c.pop("lost_ms"))
        c["kept"], c["lost"] = (_masked(c["kept"], c["field"], hidden),
                                _masked(c["lost"], c["field"], hidden))
    for row in activity:
        row["hour"] = row["hour"].isoformat()
    return {"records": records, "arriving": {r["system"]: r for r in arriving},
            "activity": activity, "conflicts": conflicts, "held": held,
            "deleted": deleted}


@router.get("/api/integration")
def integration() -> dict:
    """Every hub, its systems and their flows, and what each run and hub say."""
    try:
        by_id, flows = flow_jobs.load(DIRECTORY)
    except Exception as exc:
        return {"hubs": [], "problems": [f"{DIRECTORY}: {exc}"], "seatunnel_error": None}
    running, st_error = jobs()
    unreachable: dict[str, str] = {}   # a server asked once per request
    hubs = []
    for cid, contract in sorted(by_id.items()):
        spec = flowmod.hub_of(contract)
        if spec is None:
            continue
        systems: dict[str, dict] = {}
        for f in flows:
            if cid not in (f.target, f.mapping.reference):
                continue
            into = f.target == cid
            table = f.mapping.reference if into else f.target
            row = systems.setdefault(table, {
                "table": table, "system": flowmod.system_of(table),
                "title": (by_id.get(table) or {}).get("name") or table,
                "in": [], "out": [], "drift": []})
            row["in" if into else "out"].append(
                {"flow": f.id, "match": f.match, "job": running.get(f.id)})
            # The table changed under the flow: refused on --apply, shown here
            # while it runs (core/flow_schema.py).
            host = (flow_schema.server_of(f, by_id) or {}).get("host")
            if host in unreachable:
                continue
            try:
                row["drift"] += flow_schema.problems(f, by_id, timeout=3)
            except Exception as exc:
                unreachable[host] = exc.__class__.__name__
                row["drift"].append(f"{table}: schema not readable ({unreachable[host]})")
        hub = {"id": cid, "title": contract.get("name") or cid,
               "authority": spec.get("authority"), "codes": flowmod.keys_of(contract),
               "systems": sorted(systems.values(), key=lambda s: s["table"])}
        try:
            hub.update(hub_state(contract))
        except Exception as exc:
            hub["hub_error"] = f"{exc.__class__.__name__}: {exc}"
        hubs.append(hub)
    return {"hubs": hubs, "totals": [totals(f, by_id, running) for f in flows if f.aggregates],
            "problems": flowmod.problems(flows, by_id), "seatunnel_error": st_error}


def totals(flow: flowmod.Flow, by_id: dict[str, dict], running: dict) -> dict:
    """A one-way aggregate (#81): its two jobs, and how many lines and groups
    are summed where they land (core/flow_aggregate.py)."""
    from core import flow_aggregate
    row = {"flow": flow.id, "from": flow.mapping.reference, "to": flow.target,
           "group": flow.mapping.columns, "aggregates": flow.aggregates,
           "jobs": {"in": running.get(flow.id), "out": running.get(f"{flow.id}_out")}}
    server = flow_aggregate.landing(by_id)
    try:
        with psycopg.connect(admin_dsn(server["host"], server.get("port", 5432),
                                       server["database"]), connect_timeout=3) as cx:
            row["lines"], row["groups"], landed = cx.execute(sql.SQL(
                "select (select count(*) from flow.{}), (select count(*) from flow.{}), "
                "(select max(landed_at) from flow.{})").format(
                    sql.Identifier(f"{flow.id}_lines"), sql.Identifier(flow.id),
                    sql.Identifier(f"{flow.id}_inbox"))).fetchone()
        row["landed_at"] = landed.isoformat() if landed else None
    except Exception as exc:
        row["error"] = f"{exc.__class__.__name__}: {exc}"
    return row
