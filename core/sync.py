"""Keep a second database in step with a CDC-enabled source, from the contract.

No replication engine is written here, because both sources already have one.
Postgres has logical decoding -- `CREATE PUBLICATION` streams inserts, updates
and deletes to a subscriber with no process of ours in between, and since 15 a
publication carries a row filter and a column list, which is exactly "the rules
that decide what is synced". SQL Server has CDC, which writes every change into
a table you can SELECT (see core/sync_mssql.py and deploy/mssql-cdc.sql).

So what is ours is the part neither of them does: deriving those objects from
the contract, and refusing to create them when they would be wrong. That last
half is the point. Logical replication's failure mode is silent -- the initial
copy succeeds, the rows land, and then every subsequent change dies in a
background worker that only writes to the server log. It looks synced and is
not. Every rule below was found that way, on a running pair:

1.  The source needs a **replica identity**: a unique index over NOT NULL
    columns. The contract already names it -- `primaryKey` -- so a table whose
    uniqueness check is failing cannot be replicated safely, and that is not a
    coincidence to paper over.
2.  Every column in the **row filter** must be in the replica identity. An
    UPDATE or DELETE is filtered against the old row, and the old row is only
    the identity columns.
      ERROR: Column used in the publication WHERE expression is not part of
      the replica identity.
3.  The **column list** must cover the replica identity, for the same reason.
      ERROR: Column list used by the publication does not cover the replica
      identity.
4.  The **target** needs the same replica identity. This is the silent one --
    nothing fails until the first update.
      ERROR: logical replication target relation "public.customers" has
      neither REPLICA IDENTITY index nor PRIMARY KEY

A contract states its rule as an ODCS custom property:

    customProperties:
      - property: syncTo
        value:
          server: replica                    # a servers[] entry
          filter: "country = 'TR'"           # optional
          columns: [customer_id, name, country, segment]   # optional

The column list doubles as a privacy control: a column left out of it never
leaves the source, which is the same `classification:` the contract already
carries for core/sample.py.

    python core/sync.py --check          # validate every rule, change nothing
    python core/sync.py --apply
    python core/sync.py --status
"""
from __future__ import annotations

import argparse
import os

import psycopg
import sqlglot
from psycopg import sql
from sqlglot import exp

from core import store
from core.runner import TABLE_SCOPED_TYPES, load_contracts

IDENTITY_SUFFIX = "_sync_identity"
# One slot per publication, created explicitly rather than by CREATE
# SUBSCRIPTION. The implicit path deadlocks when publisher and subscriber are
# the same cluster, and doing it the same way everywhere is one code path.
SLOT_SUFFIX = "_slot"


def sync_rule(contract: dict, model: dict | None = None) -> dict | None:
    """The `syncTo` custom property, from the model or the contract."""
    for holder in (model or {}, contract):
        for prop in holder.get("customProperties") or []:
            if prop.get("property") == "syncTo":
                return dict(prop["value"])
    return None


def identity_columns(model: dict, rule: dict | None = None) -> list[str]:
    """What identifies a row for replication.

    The contract's `primaryKey` by default. A rule may widen it -- rule 2
    means a filter on a non-key column is only expressible by putting that
    column into the identity -- but never narrow it: an identity that does not
    contain the key would not identify a row.
    """
    key = [p["name"] for p in model.get("properties") or [] if p.get("primaryKey")]
    widened = (rule or {}).get("identity")
    return list(widened) if widened and set(key) <= set(widened) else key


def filter_columns(expression: str) -> set[str]:
    """The columns a row filter reads. sqlglot is already a dependency."""
    if not expression:
        return set()
    tree = sqlglot.parse_one(f"select 1 where {expression}", read="postgres")
    return {c.name for c in tree.find_all(exp.Column)}


def problems(model: dict, rule: dict) -> list[str]:
    """Every reason this rule would not work, before anything is created."""
    identity = identity_columns(model, rule)
    key = identity_columns(model)
    table = model.get("physicalName") or model["name"]
    out = []
    if rule.get("identity") and not set(key) <= set(rule["identity"]):
        out.append(f"{table}: the declared identity omits the primary key "
                   f"({', '.join(sorted(set(key) - set(rule['identity'])))}), "
                   f"so it does not identify a row")
    if not identity:
        out.append(f"{table}: the contract declares no primaryKey, so there is "
                   f"nothing to use as a replica identity")
        return out

    missing = sorted(filter_columns(rule.get("filter", "")) - set(identity))
    if missing:
        out.append(f"{table}: the row filter reads {', '.join(missing)}, which "
                   f"is not in the replica identity ({', '.join(identity)}); "
                   f"an update or delete would be rejected")

    columns = rule.get("columns")
    if columns and not set(identity) <= set(columns):
        out.append(f"{table}: the column list omits "
                   f"{', '.join(sorted(set(identity) - set(columns)))}, which "
                   f"the replica identity needs")

    declared = {p["name"] for p in model.get("properties") or []}
    unknown = sorted(set(columns or []) - declared)
    if unknown:
        out.append(f"{table}: the column list names {', '.join(unknown)}, "
                   f"which the contract does not declare")
    return out


def unsound_identity(contract: dict, rule: dict) -> list[str]:
    """Has the identity actually held, the last time anyone looked?

    The contract *declares* a primary key; the checks measure whether it is
    one. Both engines need it to be true and neither says so usefully when it
    is not -- Postgres refuses to build the unique index with a message about
    an index, and the CDC reader silently collapses the duplicates into one
    row on upsert. So it is asked here, of the results the daily run already
    stored.

    Missing results are not a failure: a contract that has never run has
    nothing to disagree with.
    """
    identity = set(identity_columns(contract["schema"][0], rule))
    try:
        with store.connect() as dq:
            rows = dq.execute(
                """select distinct on (check_id) check_id, field, status, reason
                   from check_results
                   where contract_id = %s and check_type = any(%s)
                   order by check_id, run_at desc""",
                (contract["id"], list(TABLE_SCOPED_TYPES))).fetchall()
    except Exception as e:                       # no store yet, or unreachable
        return [f"could not read the check results to confirm the identity: {e}"]
    return [f"{field}: {reason or 'the uniqueness check is failing'} -- the "
            f"identity does not hold, so replicating this table would merge "
            f"rows that are not the same row"
            for _, field, status, reason in rows
            if status == "fail" and field in identity]


def _identity_statements(model: dict, schema: str,
                         rule: dict) -> list[sql.Composed]:
    """The NOT NULL, the unique index and the REPLICA IDENTITY, in that order.

    Both ends need these, which is why they are built once and run twice.
    """
    table = model.get("physicalName") or model["name"]
    identity = identity_columns(model, rule)
    index = f"{table}{IDENTITY_SUFFIX}"
    ident = sql.Identifier
    cols = sql.SQL(", ").join(ident(c) for c in identity)
    out = [sql.SQL("alter table {}.{} alter column {} set not null").format(
        ident(schema), ident(table), ident(c)) for c in identity]
    out.append(sql.SQL(
        "create unique index if not exists {} on {}.{} ({})").format(
        ident(index), ident(schema), ident(table), cols))
    out.append(sql.SQL("alter table {}.{} replica identity using index {}").format(
        ident(schema), ident(table), ident(index)))
    return out


def publication_statement(model: dict, schema: str, rule: dict,
                          name: str) -> sql.Composed:
    ident = sql.Identifier
    table = model.get("physicalName") or model["name"]
    stmt = sql.SQL("create publication {} for table {}.{}").format(
        ident(name), ident(schema), ident(table))
    if rule.get("columns"):
        stmt += sql.SQL(" ({})").format(
            sql.SQL(", ").join(ident(c) for c in rule["columns"]))
    if rule.get("filter"):
        # The filter is the contract's own SQL, normalised through sqlglot so
        # a syntax error surfaces here rather than as a failed CREATE.
        stmt += sql.SQL(" where ({})").format(
            sql.SQL(sqlglot.parse_one(rule["filter"], read="postgres").sql(
                dialect="postgres")))
    return stmt


def publication_name(contract: dict) -> str:
    return "sync_" + str(contract["id"]).replace(".", "_")


def _dsn(server: dict, user: str, password: str) -> str:
    return (f"host={server['host']} port={server.get('port', 5432)} "
            f"dbname={server['database']} user={user} password={password}")


def _server(contract: dict, key: str) -> dict:
    found = next((s for s in contract.get("servers", [])
                  if s.get("server") == key), None)
    if not found:
        raise SystemExit(f"{contract['id']}: no servers entry named {key!r}")
    return found


def plan(contract: dict) -> dict | None:
    """Everything that would be run, as text, without running any of it."""
    rule = sync_rule(contract)
    if not rule:
        return None
    model = contract["schema"][0]
    source, target = _server(contract, "erp"), _server(contract, rule["server"])
    if source.get("type") not in ("postgres", "postgresql"):
        return {"contract": contract["id"], "engine": source.get("type"),
                "note": "not logical replication; see core/sync_mssql.py",
                "problems": [], "source": [], "target": []}
    name = publication_name(contract)
    with psycopg.connect(_dsn(source, *_credentials())) as cx:
        render = lambda s: s.as_string(cx)  # noqa: E731
        return {
            "contract": contract["id"], "engine": "logical replication",
            "publication": name,
            "problems": problems(model, rule) + unsound_identity(contract, rule),
            "source": [render(s) for s in
                       _identity_statements(model, source.get("schema", "public"), rule)]
                      + [render(publication_statement(
                          model, source.get("schema", "public"), rule, name))],
            "target": [render(s) for s in
                       _identity_statements(model, target.get("schema", "public"), rule)]
                      + [f"create subscription {name} connection '...' "
                         f"publication {name} with (create_slot = false, "
                         f"slot_name = '{name}{SLOT_SUFFIX}', copy_data = true)"],
        }


def _credentials() -> tuple[str, str]:
    """Replication is not a read-only operation, so not dq_reader."""
    return (os.getenv("SYNC_USERNAME", "postgres"),
            os.getenv("SYNC_PASSWORD", "postgres"))


def apply(contract: dict) -> dict:
    """Create the objects. Refuses outright if the rule has any problem."""
    p = plan(contract)
    if not p:
        return {}
    if p.get("note"):
        # Not logical replication, but the identity still has to hold.
        p["problems"] = unsound_identity(contract, sync_rule(contract))
        return p
    if p["problems"]:
        return p
    rule, model = sync_rule(contract), contract["schema"][0]
    source, target = _server(contract, "erp"), _server(contract, rule["server"])
    name, user, password = publication_name(contract), *_credentials()
    slot = f"{name}{SLOT_SUFFIX}"

    with psycopg.connect(_dsn(source, user, password), autocommit=True) as cx:
        for stmt in _identity_statements(model, source.get("schema", "public"), rule):
            cx.execute(stmt)
        cx.execute(sql.SQL("drop publication if exists {}").format(
            sql.Identifier(name)))
        cx.execute(publication_statement(
            model, source.get("schema", "public"), rule, name))
        exists = cx.execute("select 1 from pg_replication_slots where slot_name = %s",
                            (slot,)).fetchone()
        if not exists:
            cx.execute("select pg_create_logical_replication_slot(%s, 'pgoutput')",
                       (slot,))

    with psycopg.connect(_dsn(target, user, password), autocommit=True) as cx:
        for stmt in _identity_statements(model, target.get("schema", "public"), rule):
            cx.execute(stmt)
        cx.execute(sql.SQL("drop subscription if exists {}").format(
            sql.Identifier(name)))
        cx.execute(sql.SQL(
            "create subscription {} connection {} publication {} "
            "with (create_slot = false, slot_name = {}, copy_data = true)").format(
            sql.Identifier(name), sql.Literal(_dsn(source, user, password)),
            sql.Identifier(name), sql.Literal(slot)))
    p["applied"] = True
    return p


def status(contract: dict) -> dict | None:
    """Is it actually applying, and how far behind?

    Worth its own command: an apply worker that keeps dying restarts every few
    seconds and says so only in the server log, while the target sits at the
    row count the initial copy left it with.
    """
    rule = sync_rule(contract)
    if not rule:
        return None
    source, target = _server(contract, "erp"), _server(contract, rule["server"])
    if source.get("type") not in ("postgres", "postgresql"):
        return {"contract": contract["id"], "engine": source.get("type")}
    name, user, password = publication_name(contract), *_credentials()
    out = {"contract": contract["id"], "publication": name}
    with psycopg.connect(_dsn(source, user, password)) as cx:
        row = cx.execute(
            """select active, pg_size_pretty(pg_wal_lsn_diff(
                     pg_current_wal_lsn(), confirmed_flush_lsn))
               from pg_replication_slots where slot_name = %s""",
            (f"{name}{SLOT_SUFFIX}",)).fetchone()
    out["slot_active"], out["behind"] = row if row else (False, None)
    with psycopg.connect(_dsn(target, user, password)) as cx:
        row = cx.execute(
            "select pid is not null from pg_stat_subscription "
            "where subname = %s", (name,)).fetchone()
    out["worker_running"] = bool(row and row[0])
    # Both true and still behind is lag; slot active with no worker is the
    # silent failure this whole command exists for.
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true",
                       help="validate the rules, create nothing")
    group.add_argument("--apply", action="store_true")
    group.add_argument("--status", action="store_true")
    ap.add_argument("--contract", help="only this contract id")
    a = ap.parse_args()

    contracts = [c for c in load_contracts()
                 if a.contract in (None, c.get("id")) and sync_rule(c)]
    if not contracts:
        raise SystemExit("no contract carries a syncTo rule")

    bad = 0
    for contract in contracts:
        if a.status:
            print(status(contract))
            continue
        result = apply(contract) if a.apply else plan(contract)
        print(f"\n{result['contract']} -- {result.get('engine')}")
        if result.get("note"):
            print(f"  {result['note']}")
            continue
        for where in ("source", "target"):
            print(f"  -- on the {where}")
            for line in result[where]:
                print(f"     {line}")
        for problem in result["problems"]:
            bad += 1
            print(f"  ! {problem}")
        if result["problems"] and a.apply:
            print("  nothing was created")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
