"""Compile integration flows into SeaTunnel jobs, and start them. ADR 0021.

A system in a two-way integration has two flows, into its hub and back out
(core/flows.py). Each becomes one SeaTunnel job:

* **in**: the system's CDC, with `Metadata` adding the commit time and
  `RowKindExtractor` keeping each update as its before and after rows. The
  column and value map is applied as SQL, and the rows are appended to
  `hub.<entity>_inbox` with a plain insert, so they keep their order. The
  merge happens in the hub (core/hub.sql).
* **out**: the golden record's CDC. Before images are dropped, the reverse map
  is applied, and the result is written into the system. A SQL Server target
  uses the sink's generated `MERGE`: rewriting a value it already holds adds
  no CDC row, so our own delivery does not echo (ADR 0020).

Nothing here is a rule the flows do not already state; the compiler only
spells them in SeaTunnel's words. `--check` runs core/flows.py's refusals
first and compiles nothing if there are any.

    python core/flow_jobs.py --check  --contracts demo/integration
    python core/flow_jobs.py --print  --contracts demo/integration
    python core/flow_jobs.py --apply  --contracts demo/integration
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

from core import flows as flowmod
from core.mapping import _properties

CHECKPOINT_MS = int(os.getenv("FLOW_CHECKPOINT_MS", "3000"))


def _literal(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _expression(source: str, values: dict | None) -> str:
    """A column, or a value map as a searched CASE. There is no ELSE: an
    unmapped value becomes NULL, which the hub contract's checks catch
    (ADR 0020, "a value outside the value map")."""
    if not values:
        return source
    whens = " ".join(f"WHEN {source} = {_literal(a)} THEN {_literal(b)}"
                     for a, b in values.items())
    return f"CASE {whens} END"


def _projection(flow: flowmod.Flow, prefix: str = "") -> str:
    return prefix + ", ".join(
        f"{_expression(src, flow.mapping.values.get(tgt))} AS {tgt}"
        for tgt, src in flow.mapping.columns.items())


def _server(contract: dict) -> dict:
    return next(s for s in contract["servers"]
                if s.get("type") in ("sqlserver", "postgres", "postgresql"))


def _jdbc(server: dict, database: str | None = None) -> dict:
    db = database or server["database"]
    if server["type"] == "sqlserver":
        return {"url": f"jdbc:sqlserver://{server['host']}:{server.get('port', 1433)};"
                       f"databaseName={db};encrypt=false;trustServerCertificate=true",
                "driver": "com.microsoft.sqlserver.jdbc.SQLServerDriver",
                "username": "${MSSQL_USER}", "password": "${MSSQL_PASSWORD}"}
    return {"url": f"jdbc:postgresql://{server['host']}:{server.get('port', 5432)}/{db}",
            "driver": "org.postgresql.Driver",
            "username": "${PG_USER}", "password": "${PG_PASSWORD}"}


def _cdc_source(contract: dict, slot: str) -> dict:
    server, table = _server(contract), contract["schema"][0]["physicalName"]
    schema, db = server.get("schema", "dbo"), server["database"]
    jdbc = _jdbc(server)
    common = {"plugin_output": "src", "username": jdbc["username"],
              "password": jdbc["password"], "database-names": [db],
              "table-names": [f"{db}.{schema}.{table}"], "url": jdbc["url"],
              "startup.mode": "initial"}
    if server["type"] == "sqlserver":
        return {"SqlServer-CDC": common}
    return {"Postgres-CDC": {**common, "slot.name": slot,
                             "decoding.plugin.name": "pgoutput"}}


def inbound(flow: flowmod.Flow, by_id: dict[str, dict]) -> dict:
    hub, entity = by_id[flow.target], by_id[flow.target]["schema"][0]["name"]
    cols = list(flow.mapping.columns)
    sql = (f"SELECT row_kind, source_ms, {_literal(flow.mapping.reference)} AS system, "
           f"{_projection(flow)} FROM dual")
    insert = (f"insert into hub.{entity}_inbox (row_kind, source_ms, system, "
              f"{', '.join(cols)}) values ({', '.join('?' * (len(cols) + 3))})")
    return {
        "env": {"job.mode": "STREAMING", "checkpoint.interval": CHECKPOINT_MS,
                "parallelism": 1, "job.name": flow.id},
        "source": [_cdc_source(by_id[flow.mapping.reference], f"{flow.id}_slot")],
        "transform": [
            {"Metadata": {"plugin_input": "src", "plugin_output": "meta",
                          "metadata_fields": {"SourceTimestamp": "source_ms"}}},
            {"RowKindExtractor": {"plugin_input": "meta", "plugin_output": "log",
                                  "custom_field_name": "row_kind",
                                  "transform_type": "FULL"}},
            {"Sql": {"plugin_input": "log", "plugin_output": "out", "query": sql}}],
        "sink": [{"Jdbc": {"plugin_input": "out", **_jdbc(_server(hub)),
                           "query": insert}}]}


def outbound(flow: flowmod.Flow, by_id: dict[str, dict]) -> dict:
    target = by_id[flow.target]
    server = _server(target)
    key = [p["name"] for p in _properties(target).values() if p.get("primaryKey")]
    if server["type"] != "sqlserver":
        raise NotImplementedError(
            f"{flow.id}: only SQL Server targets are compiled yet; a Postgres "
            f"target needs the guarded upsert of ADR 0020")
    return {
        "env": {"job.mode": "STREAMING", "checkpoint.interval": CHECKPOINT_MS,
                "parallelism": 1, "job.name": flow.id},
        "source": [_cdc_source(by_id[flow.mapping.reference], f"{flow.id}_slot")],
        "transform": [
            {"FilterRowKind": {"plugin_input": "src", "plugin_output": "after",
                               "exclude_kinds": ["UPDATE_BEFORE"]}},
            {"Sql": {"plugin_input": "after", "plugin_output": "out",
                     "query": f"SELECT {_projection(flow)} FROM dual"}}],
        "sink": [{"Jdbc": {"plugin_input": "out", **_jdbc(server),
                           "generate_sink_sql": True, "database": server["database"],
                           "table": f"{server.get('schema', 'dbo')}."
                                    f"{target['schema'][0]['physicalName']}",
                           "primary_keys": key,
                           "schema_save_mode": "IGNORE",
                           "data_save_mode": "APPEND_DATA"}}]}


def load(directory: Path) -> tuple[dict[str, dict], list[flowmod.Flow]]:
    by_id = {}
    for path in sorted(directory.glob("*.odcs.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        by_id[doc["id"]] = doc
    return by_id, flowmod.load(directory / "flows")


def _rest(config: dict) -> dict:
    """`{"Jdbc": {...}}` to `{"plugin_name": "Jdbc", ...}`, the shape the REST
    API and JSON job files take."""
    return {"env": config["env"], **{
        stage: [{"plugin_name": name, **opts}
                for item in config[stage] for name, opts in item.items()]
        for stage in ("source", "transform", "sink")}}


def jobs(by_id: dict[str, dict], flows: list[flowmod.Flow]) -> dict[str, dict]:
    out = {}
    for flow in flows:
        into_hub = flowmod.hub_of(by_id.get(flow.target)) is not None
        out[flow.id] = _rest(inbound(flow, by_id) if into_hub else outbound(flow, by_id))
    return out


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Integration flows as SeaTunnel jobs.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--print", action="store_true")
    mode.add_argument("--apply", action="store_true")
    ap.add_argument("--contracts", type=Path, default=Path("contracts"))
    args = ap.parse_args()

    by_id, flows = load(args.contracts)
    found = flowmod.problems(flows, by_id)
    for line in found:
        print(f"REFUSED: {line}")
    if found:
        raise SystemExit(1)
    if args.check:
        print(f"{len(flows)} flow(s), 0 problems")
    elif args.print:
        print(json.dumps(jobs(by_id, flows), indent=2))
    else:
        from core import flow_apply
        flow_apply.apply(by_id, flows, jobs(by_id, flows))


if __name__ == "__main__":
    main()
