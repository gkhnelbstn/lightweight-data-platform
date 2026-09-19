"""Compile integration flows into SeaTunnel jobs, and start them. ADR 0021.

A system in a two-way integration has two flows, into its hub and back out
(core/flows.py). Each becomes one SeaTunnel job:

* **in**: the system's CDC, with `Metadata` adding the commit time and
  `RowKindExtractor` keeping each update as its before and after rows. The
  column and value map is applied as SQL, and the rows are appended to
  `hub.<entity>_inbox` with a plain insert, so they keep their order. The
  merge happens in the hub (core/hub.sql).
* **out**: the golden record's CDC. Before images are dropped, the reverse map
  is applied, and a MERGE the compiler writes updates only the fields that
  revision changed, never back to the system the change came from. Rewriting
  a value a SQL Server table already holds adds no CDC row (ADR 0020), and
  the hub awaits the rest (ADR 0021).

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


def _where(conditions: list[str]) -> str:
    return f" WHERE {' AND '.join(conditions)}" if conditions else ""


def inbound(flow: flowmod.Flow, by_id: dict[str, dict]) -> dict:
    """`fields` tells the hub which columns this flow carries: a table holding
    part of a record leaves the others null, and those nulls are not values.
    `match` takes one kind of row from a table with several per record."""
    hub, entity = by_id[flow.target], by_id[flow.target]["schema"][0]["name"]
    cols = list(flow.mapping.columns)
    sql = (f"SELECT row_kind, source_ms, {_literal(flow.mapping.reference)} AS system, "
           f"{_literal(','.join(cols))} AS fields, {_projection(flow)} FROM dual"
           + _where([f"{c} = {_literal(v)}" for c, v in flow.match.items()]))
    insert = (f"insert into hub.{entity}_inbox (row_kind, source_ms, system, fields, "
              f"{', '.join(cols)}) values ({', '.join('?' * (len(cols) + 4))})")
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


def outbound(flow: flowmod.Flow, by_id: dict[str, dict],
             link_by: tuple[str, ...] = ()) -> dict:
    """The golden record back into a system, one revision at a time.

    Each revision says what it changed (`_changed`) and which system it came
    from (`_skip`, ADR 0021). So the delivery is a MERGE our compiler writes,
    not the sink's generated one: it updates only the changed fields -- a
    revision carries every column, and writing the unchanged ones would put
    stale values over an edit the target made meanwhile -- and it does not
    write back to the system the change came from, which already has it.

    A record still missing what the target requires is held back, so the part
    of a record that arrived first waits for the rest rather than failing a
    NOT NULL. Deletes are their own branch, by key. A flow with `match` writes
    one kind of row of a table with several per record (#79): its constants
    are written back, and a kind with no value has no row, so it is deleted
    rather than left empty.

    A target that keeps codes of its own (#80) assigns them to the records it
    receives (`filledByTarget`): the insert leaves the code out, and matches a
    row it already has by `link_by` -- the pair's rule -- so a record the hub
    has not linked yet is not inserted twice. A revision that brings a record's
    code for this system is new to it, like `*`: the rows it could not be
    written before can be now.
    """
    target = by_id[flow.target]
    server = _server(target)
    if server["type"] != "sqlserver":
        raise NotImplementedError(
            f"{flow.id}: only SQL Server targets are compiled yet; a Postgres "
            f"target needs the guarded upsert of ADR 0020")
    props = _properties(target)
    key = [n for n, p in props.items() if p.get("primaryKey")]
    table = f"{server.get('schema', 'dbo')}.{target['schema'][0]['physicalName']}"
    cols = flow.mapping.columns                      # target column: hub column
    keyed = [(t, s) for t, s in cols.items() if t in key]
    carried = [(t, s) for t, s in cols.items() if t not in key]
    assigned = {t for t, _ in keyed if t in flow.filled_by_target}
    ours = f"(_skip IS NULL OR _skip <> {_literal(flow.target)})"
    waits = [f"{s} IS NOT NULL" for t, s in cols.items() if t not in assigned
             and (props.get(t, {}).get("required") or props.get(t, {}).get("primaryKey"))]
    present = ([f"({' OR '.join(f'{s} IS NOT NULL' for _, s in carried)})"]
               if flow.match and carried else [])
    constants = "".join(f", {_literal(v)} AS {c}" for c, v in flow.match.items())
    # Only revisions that changed something this flow writes: an unrelated
    # revision must not recreate or delete a row the target changed meanwhile.
    touched = "(" + " OR ".join(["_changed = '*'"] + [
        f"POSITION({_literal(',' + s + ',')}, _changed) > 0" for _, s in keyed + carried]) + ")"
    # Emptying is said by name only. A revision new to the target ('*', or
    # the one bringing its key) with a part still empty says nothing about
    # that part: the owner's row can reach the hub before its address does,
    # and deleting the target's row then deletes the address on its way in.
    named = "(" + " OR ".join(
        f"POSITION({_literal(',' + s + ',')}, _changed) > 0" for _, s in carried) + ")"
    new = " OR ".join(["s._changed = '*'"] + [
        f"CHARINDEX({_literal(',' + s + ',')}, s._changed) > 0" for _, s in keyed])

    written = list(cols) + list(flow.match)
    using = ", ".join(f"? AS [{c}]" for c in written) + ", ? AS _changed"
    on = " AND ".join(f"t.[{c}] = s.[{c}]" for c in [t for t, _ in keyed] + list(flow.match))
    linked = [t for t, s in cols.items() if s in link_by]
    if assigned and linked:
        on = (f"({on} OR ({' AND '.join(f's.[{c}] IS NULL' for c in sorted(assigned))} AND "
              + " AND ".join(f"t.[{c}] = s.[{c}]" for c in linked) + "))")
    sets = ", ".join(
        f"t.[{t}] = CASE WHEN {new} OR CHARINDEX({_literal(',' + s + ',')}, "
        f"s._changed) > 0 THEN s.[{t}] ELSE t.[{t}] END" for t, s in carried)
    inserted = [c for c in written if c not in assigned]
    merge = (f"MERGE {table} WITH (HOLDLOCK) AS t USING (SELECT {using}) AS s ON {on} "
             + (f"WHEN MATCHED THEN UPDATE SET {sets} " if sets else "")
             # A missing record is created only by a revision that is new to
             # everyone ('*'): otherwise it was deleted here and not yet in
             # the hub. A kind of row is created when its value changes.
             + ("WHEN NOT MATCHED " if flow.match else f"WHEN NOT MATCHED AND ({new}) ")
             + f"THEN INSERT ({', '.join(f'[{c}]' for c in inserted)}) "
               f"VALUES ({', '.join(f's.[{c}]' for c in inserted)});")
    delete = (f"DELETE FROM {table} WHERE "
              + " AND ".join([f"[{t}] = ?" for t, _ in keyed]
                             + [f"[{c}] = {_literal(v)}" for c, v in flow.match.items()]))
    keys_only = ", ".join(f"{s} AS {t}" for t, s in keyed)
    key_known = [f"{s} IS NOT NULL" for _, s in keyed]

    transform = [
        {"FilterRowKind": {"plugin_input": "src", "plugin_output": "live",
                           "exclude_kinds": ["UPDATE_BEFORE", "DELETE"]}},
        {"FilterRowKind": {"plugin_input": "src", "plugin_output": "dead",
                           "include_kinds": ["DELETE"]}},
        # A query sink runs its statement for every row, whatever its kind;
        # making deletes plain rows says so rather than relying on it.
        {"RowKindExtractor": {"plugin_input": "dead", "plugin_output": "dead_rows",
                              "custom_field_name": "row_kind"}},
        {"Sql": {"plugin_input": "live", "plugin_output": "upserts",
                 "query": f"SELECT {_projection(flow)}{constants}, _changed FROM dual"
                          + _where([ours, touched] + waits + present)}},
        {"Sql": {"plugin_input": "dead_rows", "plugin_output": "deletes",
                 "query": f"SELECT {keys_only} FROM dual" + _where([ours] + key_known)}}]
    jdbc = _jdbc(server)
    sink = [{"Jdbc": {"plugin_input": "upserts", **jdbc, "query": merge}},
            {"Jdbc": {"plugin_input": "deletes", **jdbc, "query": delete}}]
    if present:
        transform.append(
            {"Sql": {"plugin_input": "live", "plugin_output": "emptied",
                     "query": f"SELECT {keys_only} FROM dual"
                              + _where([ours, named] + key_known
                                       + [f"{s} IS NULL" for _, s in carried])}})
        sink.append({"Jdbc": {"plugin_input": "emptied", **jdbc, "query": delete}})
    return {
        "env": {"job.mode": "STREAMING", "checkpoint.interval": CHECKPOINT_MS,
                "parallelism": 1, "job.name": flow.id},
        "source": [_cdc_source(by_id[flow.mapping.reference], f"{flow.id}_slot")],
        "transform": transform,
        "sink": sink}


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
        pair = next((f for f in flows if (f.mapping.reference, f.target, f.match)
                     == (flow.target, flow.mapping.reference, flow.match)), None)
        out[flow.id] = _rest(inbound(flow, by_id) if into_hub else outbound(
            flow, by_id, pair.link_by if pair else ()))
    return out


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Integration flows as SeaTunnel jobs.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--print", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--stop", action="store_true",
                      help="stop the running jobs of these flows")
    ap.add_argument("--contracts", type=Path, default=Path("contracts"))
    args = ap.parse_args()

    by_id, flows = load(args.contracts)
    if args.stop:
        from core import flow_apply
        flow_apply.stop({f.id for f in flows})
        return
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
