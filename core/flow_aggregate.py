"""Many rows into one row, one way: invoice lines become one journal entry per
invoice. Issue #81.

    # contracts/flows/invoice_totals.yaml
    id: invoice_totals
    from: billing.invoice_line
    to: ledger.journal_entry
    columns: {EntryNo: InvoiceNo}              # the group: target column <- source
    aggregates: {Total: sum(Amount), LineCount: count(*)}

**One way only.** A total has no inverse: the lines cannot be regenerated from
it, for the same reason a value map is allowed and an expression is not (ADR
0019). So a flow back is refused, and so are value maps on the lines.

**Where the sum happens.** SeaTunnel's SQL has no aggregation and no joins
(ADR 0020). The lines land in the hub's database, in schema `flow`: an
append-only inbox, a mirror of the source table kept by a trigger, and
`flow.<id>`, one row per group, recomputed whenever a line of it changes
(core/flow_aggregate.sql). A second job carries `flow.<id>` to the target with
the engine's MERGE (core/flow_sql.py) -- so an aggregating flow is two jobs,
`<id>` and `<id>_out`, both resumed like any other (ADR 0023).
"""
from __future__ import annotations

import re
from pathlib import Path

from core import flow_sql
from core import flows as flowmod
from core.mapping import _properties

AGGREGATE = re.compile(r"^\s*(sum|count|min|max|avg)\(\s*(\*|[A-Za-z_]\w*)\s*\)\s*$", re.I)
SQL = Path(__file__).with_name("flow_aggregate.sql")

# The source's physical types, as the landing tables in Postgres need them.
# ponytail: the common SQL Server and Postgres names; an unknown type lands
# as text, which sums refuse -- widen this when a contract needs another.
PG_TYPES = {"int": "integer", "integer": "integer", "bigint": "bigint", "smallint": "smallint",
            "tinyint": "smallint", "bit": "boolean", "boolean": "boolean",
            "decimal": "numeric", "numeric": "numeric", "money": "numeric",
            "float": "double precision", "real": "real", "date": "date",
            "datetime": "timestamp", "datetime2": "timestamp", "timestamp": "timestamp"}


def pg_type(physical: str | None) -> str:
    base = (physical or "text").lower().split("(")[0].strip()
    kind = PG_TYPES.get(base, "text")
    size = (physical or "").partition("(")[2]
    return f"{kind}({size}" if kind == "numeric" and size else kind


def parse(expression: str) -> tuple[str, str] | None:
    m = AGGREGATE.match(expression or "")
    return (m.group(1).lower(), m.group(2)) if m else None


def problems(flow: flowmod.Flow, flows: list[flowmod.Flow], by_id: dict[str, dict]) -> list[str]:
    source, target = by_id[flow.mapping.reference], by_id[flow.target]
    out = []
    if flowmod.hub_of(source) is not None or flowmod.hub_of(target) is not None:
        out.append(f"{flow.id}: an aggregate goes from one system to another, not "
                   f"through a hub: a total has no record to meet in")
    back = [f.id for f in flows
            if (f.mapping.reference, f.target) == (flow.target, flow.mapping.reference)]
    if back:
        out.append(f"{flow.id}: a total cannot come back as lines, so {', '.join(back)} "
                   f"cannot be its way back -- an aggregate is one way")
    if flow.mapping.values:
        out.append(f"{flow.id}: an aggregating flow maps no values; its group is copied "
                   f"and its totals are computed")
    theirs, ours = _properties(source), _properties(target)
    for column, expression in flow.aggregates.items():
        parsed = parse(expression)
        if parsed is None:
            out.append(f"{flow.id}: {column!r} is {expression!r}; an aggregate is "
                       f"sum, count, min, max or avg of one column, or count(*)")
        elif parsed[1] == "*" and parsed[0] != "count":
            out.append(f"{flow.id}: only count takes *, not {parsed[0]}")
        elif parsed[1] != "*" and parsed[1] not in theirs:
            out.append(f"{flow.id}: {expression} reads {parsed[1]!r}, which "
                       f"{source['id']} does not declare")
        if column not in ours:
            out.append(f"{flow.id}: the total {column!r} is not a column of {target['id']}")
    key = {n for n, p in ours.items() if p.get("primaryKey")}
    if set(flow.mapping.columns) != key:
        out.append(f"{flow.id}: the group ({', '.join(sorted(flow.mapping.columns))}) must be "
                   f"{target['id']}'s key ({', '.join(sorted(key))}): one row per group")
    if not [n for n, p in theirs.items() if p.get("primaryKey")]:
        out.append(f"{flow.id}: {source['id']} declares no key, so a changed or deleted "
                   f"line cannot be told from a new one")
    if landing(by_id) is None:
        out.append(f"{flow.id}: an aggregate is summed in the hub's database, and no hub "
                   f"contract says where that is")
    return out


def landing(by_id: dict[str, dict]) -> dict | None:
    """The hub's Postgres, where the lines land and are summed."""
    for contract in by_id.values():
        if flowmod.hub_of(contract) is not None:
            return next((s for s in contract["servers"]
                         if s.get("type", "").startswith("postgres")), None)
    return None


def _shape(flow: flowmod.Flow, by_id: dict[str, dict]):
    source, target = by_id[flow.mapping.reference], by_id[flow.target]
    theirs, ours = _properties(source), _properties(target)
    key = [n for n, p in theirs.items() if p.get("primaryKey")]
    aggs = {t: parse(e) for t, e in flow.aggregates.items()}
    lines = list(dict.fromkeys(key + list(flow.mapping.columns.values())
                               + [c for _, c in aggs.values() if c != "*"]))
    return source, target, theirs, ours, key, aggs, lines


def landing_contract(flow: flowmod.Flow, by_id: dict[str, dict]) -> dict:
    """`flow.<id>` as a source, for the out job and its resume checks."""
    return {"id": f"flow.{flow.id}", "servers": [{**landing(by_id), "schema": "flow"}],
            "schema": [{"name": flow.id, "physicalName": flow.id, "properties": []}]}


def install(cx, flow: flowmod.Flow, by_id: dict[str, dict]) -> None:
    """The landing tables and the trigger. Idempotent: `--apply` runs it each time."""
    from psycopg import sql
    import json
    source, target, theirs, ours, key, aggs, lines = _shape(flow, by_id)
    cx.execute(SQL.read_text(encoding="utf-8"))
    typed = sql.SQL(", ").join(sql.SQL("{} {}").format(
        sql.Identifier(c), sql.SQL(pg_type(theirs[c].get("physicalType")))) for c in lines)
    total = {"sum": "numeric", "avg": "numeric", "count": "bigint"}
    agg_cols = sql.SQL(", ").join(sql.SQL("{} {}").format(
        sql.Identifier(t), sql.SQL(total.get(fn) or pg_type(theirs[c].get("physicalType"))))
        for t, (fn, c) in aggs.items())
    group = sql.SQL(", ").join(sql.SQL("{} {}").format(
        sql.Identifier(t), sql.SQL(pg_type(ours[t].get("physicalType"))))
        for t in flow.mapping.columns)
    name = sql.Identifier(flow.id)
    cx.execute(sql.SQL("create table if not exists flow.{} ({}, primary key ({}))").format(
        sql.Identifier(f"{flow.id}_lines"), typed, sql.SQL(", ").join(map(sql.Identifier, key))))
    cx.execute(sql.SQL("create table if not exists flow.{} ({}, {}, primary key ({}))").format(
        name, group, agg_cols, sql.SQL(", ").join(map(sql.Identifier, flow.mapping.columns))))
    cx.execute(sql.SQL("alter table flow.{} replica identity full").format(name))
    inbox = sql.Identifier(f"{flow.id}_inbox")
    cx.execute(sql.SQL("create table if not exists flow.{} (id bigserial primary key, "
                       "row_kind text not null, {}, landed_at timestamptz not null "
                       "default clock_timestamp())").format(inbox, typed))
    cx.execute(sql.SQL("drop trigger if exists apply on flow.{}").format(inbox))
    cx.execute(sql.SQL("create trigger apply after insert on flow.{} for each row "
                       "execute function flow.apply({})").format(inbox, sql.Literal(flow.id)))
    cx.execute("insert into flow.spec values (%s, %s, %s, %s) on conflict (flow) do update "
               "set line_key = excluded.line_key, grp = excluded.grp, aggs = excluded.aggs",
               (flow.id, key, json.dumps(flow.mapping.columns),
                json.dumps({t: list(v) for t, v in aggs.items()})))


def jobs(flow: flowmod.Flow, by_id: dict[str, dict]) -> dict[str, dict]:
    """`<id>` lands the lines; `<id>_out` delivers the totals."""
    from core import flow_jobs as fj
    source, target, theirs, ours, key, aggs, lines = _shape(flow, by_id)
    home = landing(by_id)
    env = lambda name: fj.env(flow, name)  # noqa: E731
    quoted = ", ".join(f'"{c}"' for c in lines)
    land = {
        "env": env(flow.id),
        "source": [fj._cdc_source(source, f"{flow.id}_slot")],
        "transform": [
            {"RowKindExtractor": {"plugin_input": "src", "plugin_output": "log",
                                  "custom_field_name": "row_kind", "transform_type": "FULL"}},
            {"Sql": {"plugin_input": "log", "plugin_output": "out",
                     "query": f"SELECT row_kind, {', '.join(lines)} FROM dual"}}],
        "sink": [{"Jdbc": {"plugin_input": "out", **fj._jdbc(home),
                           "query": f'insert into flow."{flow.id}_inbox" (row_kind, {quoted}) '
                                    f"values ({', '.join('?' * (len(lines) + 1))})"}}]}

    server = fj._server(target)
    engine = "sqlserver" if server["type"] == "sqlserver" else "postgres"
    where = flow_sql.Target(
        engine, f"{server.get('schema', 'dbo' if engine == 'sqlserver' else 'public')}."
                f"{target['schema'][0]['physicalName']}",
        {n: p.get("physicalType") for n, p in ours.items()} | {"_changed": "text"})
    keyed = [(t, t) for t in flow.mapping.columns]
    carried = [(t, t) for t in aggs]
    # The total's row is the whole truth about its group: every revision of it
    # is written in full ('*'), and a row that is gone is a group that is gone.
    deliver = {
        "env": env(f"{flow.id}_out"),
        "source": [fj._cdc_source(landing_contract(flow, by_id), f"{flow.id}_out_slot")],
        "transform": [
            {"FilterRowKind": {"plugin_input": "src", "plugin_output": "live",
                               "exclude_kinds": ["UPDATE_BEFORE", "DELETE"]}},
            {"FilterRowKind": {"plugin_input": "src", "plugin_output": "dead",
                               "include_kinds": ["DELETE"]}},
            {"RowKindExtractor": {"plugin_input": "dead", "plugin_output": "dead_rows",
                                  "custom_field_name": "row_kind"}},
            {"Sql": {"plugin_input": "live", "plugin_output": "upserts",
                     "query": f"SELECT {', '.join(t for t, _ in keyed + carried)}, "
                              f"'*' AS _changed FROM dual"}},
            {"Sql": {"plugin_input": "dead_rows", "plugin_output": "deletes",
                     "query": f"SELECT {', '.join(t for t, _ in keyed)} FROM dual"}}],
        "sink": [
            {"Jdbc": {"plugin_input": "upserts", **fj._jdbc(server),
                      "query": flow_sql.merge(where, keyed, carried, {}, set(), [], False)}},
            {"Jdbc": {"plugin_input": "deletes", **fj._jdbc(server),
                      "query": flow_sql.delete(where, keyed, {})}}]}
    return {flow.id: land, f"{flow.id}_out": deliver}
