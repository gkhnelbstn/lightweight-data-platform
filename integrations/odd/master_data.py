"""The hub's golden records on ODD's Master Data page.

ODD keeps reference data as *lookup tables*: a table a person browses in the
catalogue, with its own page and its own dataset entity. The hub (ADR 0021)
is where the golden record lives. This copies the one into the other, one way,
so the Master Data page shows each customer once, with every system's code for
it -- the cross-reference a data steward looks for first:

    hub.customer                   ODD lookup table `customer_master`
    customer_id, crm_code, ...     one column each, the key unique
    one row per golden record      one row each, matched by the key

and every flow's value map as `value_maps`: which code one system writes for
the value another one means (`Y` in the CRM is `true` in the hub).

**One way.** A lookup table is editable in ODD, and an edit there is
overwritten by the next run: a golden record changes in a system and reaches
the hub through its flow, never from the catalogue. The table's description
says so, since nothing in ODD can lock it.

**Classified columns stay out.** The contract's `classification` is the
privacy boundary here as everywhere (ADR 0017): `tax_id` is not copied.

    python integrations/odd/master_data.py --url http://odd-platform:8080
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg
from psycopg import sql

from core import flow_jobs, sample
from core import flows as flowmod
from core.bootstrap_db import admin_dsn
from core.mapping import _properties
from integrations.odd.curate import ensure_namespace
from integrations.odd.entity_page import _get, _send

NAMESPACE = "master data"
# ponytail: every golden record is copied on every run. Right for a hub of
# thousands; a lookup table is not where a million customers belong, so past
# this the table is left alone and the run says so.
MAX_ROWS = 10_000
KIND = {"int": "INTEGER", "integer": "INTEGER", "smallint": "INTEGER",
        "bigint": "DECIMAL", "numeric": "DECIMAL", "decimal": "DECIMAL",
        "boolean": "BOOLEAN", "bit": "BOOLEAN", "date": "DATE"}


def _kind(physical: str | None) -> str:
    return KIND.get((physical or "").lower().split("(")[0].strip(), "VARCHAR")


def _text(value) -> str | None:
    if value is None:
        return None
    return str(value).lower() if isinstance(value, bool) else str(value)


def columns(contract: dict) -> dict[str, dict]:
    """The hub's columns that may leave it: everything but the classified."""
    hidden = sample.classified(contract)
    return {n: p for n, p in _properties(contract).items() if n not in hidden}


def golden(contract: dict) -> dict:
    """One hub entity as a lookup table: its columns, its key, its rows."""
    entity = contract["schema"][0]["name"]
    props = columns(contract)
    key = [n for n, p in props.items() if p.get("primaryKey")]
    server = next(s for s in contract["servers"] if s["type"].startswith("postgres"))
    with psycopg.connect(admin_dsn(server["host"], server.get("port", 5432),
                                   server["database"]), connect_timeout=5) as cx:
        rows = cx.execute(sql.SQL("select {} from hub.{} order by {} limit %s").format(
            sql.SQL(", ").join(map(sql.Identifier, props)), sql.Identifier(entity),
            sql.SQL(", ").join(map(sql.Identifier, key))), (MAX_ROWS + 1,)).fetchall()
    return {"name": f"{entity}_master", "key": key,
            "description": f"{contract.get('name') or contract['id']}: the golden record "
                           f"from {contract['id']}, one row per record with every system's "
                           f"code. Published by the platform; change a record in its "
                           f"system, not here -- the next run overwrites an edit.",
            "columns": {n: (_kind(p.get("physicalType")), p.get("description"))
                        for n, p in props.items()},
            "rows": [dict(zip(props, map(_text, r))) for r in rows]}


def value_maps(flows: list[flowmod.Flow]) -> dict:
    """Every flow's value map, one row per code: the reference data a pair of
    systems agrees on through the hub."""
    rows = [{"flow": f.id, "target_column": column, "from_value": _text(a),
             "to_value": _text(b)}
            for f in flows for column, pairs in sorted(f.mapping.values.items())
            for a, b in pairs.items()]
    # `target_column`, not `column`: ODD does not quote the names it gives
    # its own ALTER TABLE, so a reserved word is a syntax error there.
    return {"name": "value_maps", "key": ["flow", "target_column", "from_value"], "rows": rows,
            "description": "Which code one system writes for the value another means, "
                           "from each flow's `values`. Published by the platform; edit "
                           "the flow file, not this table.",
            "columns": {"flow": ("VARCHAR", "The flow that maps the value."),
                        "target_column": ("VARCHAR", "The column it writes, on the flow's target."),
                        "from_value": ("VARCHAR", "The value as the flow's source holds it."),
                        "to_value": ("VARCHAR", "The value as the flow writes it.")}}


def find(url: str, name: str) -> dict | None:
    search = _send(f"{url}/api/referencedata/search", {"query": name})
    for item in _get(f"{url}/api/referencedata/search/{search['search_id']}/results"
                     f"?page=1&size=100").get("items", []):
        if item.get("name") == name:
            return item
    return None


def publish(url: str, table: dict) -> str:
    """Create the lookup table if it is missing, add columns it lacks, and
    make its rows the given ones: add, change and delete by key."""
    url = url.rstrip("/")
    if len(table["rows"]) > MAX_ROWS:
        return f"{table['name']}: over {MAX_ROWS} rows, left alone"
    found = find(url, table["name"])
    if found is None:
        ensure_namespace(url, NAMESPACE)
        found = _send(f"{url}/api/referencedata/table", {
            "name": table["name"], "description": table["description"],
            "namespace_name": NAMESPACE})
    tid = found["table_id"]
    have = {f["name"] for f in found.get("fields") or []}
    missing = [{"name": n, "field_type": kind, "description": text or "",
                "is_nullable": n not in table["key"]}
               for n, (kind, text) in table["columns"].items() if n not in have]
    if missing:
        found = _send(f"{url}/api/referencedata/table/{tid}/columns", missing)
    ids = {f["name"]: f["field_id"] for f in found["fields"]}
    names = {v: k for k, v in ids.items()}

    current, page = {}, 1
    while True:
        got = _get(f"{url}/api/referencedata/table/{tid}/data?page={page}&size=500")
        for row in got.get("items", []):
            values = {names.get(i["field_id"]): i.get("value") for i in row["items"]}
            current[tuple(values.get(k) for k in table["key"])] = (row["row_id"], values)
        if not (got.get("page_info") or {}).get("hasNext"):
            break
        page += 1

    def form(values: dict) -> dict:
        return {"items": [{"field_id": ids[n], "value": v} for n, v in values.items()
                          if v is not None]}

    added = changed = 0
    new = []
    for values in table["rows"]:
        key = tuple(values.get(k) for k in table["key"])
        row_id, now = current.pop(key, (None, None))
        if row_id is None:
            new.append(form(values))
        elif any(now.get(n) != v for n, v in values.items()):
            _send(f"{url}/api/referencedata/table/{tid}/data/{row_id}", form(values), "PATCH")
            changed += 1
    if new:
        _send(f"{url}/api/referencedata/table/{tid}/data", new)
        added = len(new)
    for row_id, _ in current.values():
        _send(f"{url}/api/referencedata/table/{tid}/data/{row_id}", None, "DELETE")
    return (f"{table['name']}: {len(table['rows'])} rows, {added} added, "
            f"{changed} changed, {len(current)} deleted")


def tables(directory: Path) -> list[dict]:
    by_id, flows = flow_jobs.load(directory)
    out = [golden(c) for _, c in sorted(by_id.items()) if flowmod.hub_of(c) is not None]
    codes = value_maps(flows)
    return out + ([codes] if codes["rows"] else [])


def main() -> None:
    ap = argparse.ArgumentParser(description="Publish the hub's golden records to ODD.")
    ap.add_argument("--url", required=True)
    ap.add_argument("--contracts", default=os.getenv("INTEGRATION_DIR", "contracts"))
    args = ap.parse_args()
    for table in tables(Path(args.contracts)):
        print(publish(args.url, table))


if __name__ == "__main__":
    main()
