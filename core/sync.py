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
          generated:                         # optional
            replica_row_id: bigint generated always as identity
            replicated_at: timestamptz not null default now()

`generated` is the target's half of the rule: columns that exist only in the
target and that the target fills itself. The source never has them, so nothing
sends them -- a sequence, a default or a trigger on the target is what puts a
value there, which is what issue #45 found missing. They are target-side by
construction, which is why this needs no change to the readers: a column the
contract does not declare is not in `columns`, so neither the publication nor
core/sync_mssql.py's upsert ever names it.

Verified against both paths rather than reasoned about: under logical
replication the apply worker inserts without naming them, so `generated always
as identity` fires and an UPDATE leaves the value it already assigned alone.

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


def mode(rule: dict | None) -> str:
    """`copy` (the default) or `view`. See ADR 0017.

    A copy is replicated -- a publication and a subscription, or the CDC
    reader. A view is `postgres_fdw`: the same filter and column list, read
    through to the source instead of duplicated. The two have different
    failure modes and a different privacy story, so the contract says which.
    """
    return (rule or {}).get("mode", "copy")


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


def problems(model: dict, rule: dict, engine: str = "postgres") -> list[str]:
    """Every reason this rule would not work, before anything is created.

    Rules 2 and 3 are logical replication's, not replication's in general:
    they exist because Postgres matches an update against the replica identity
    alone. core/sync_mssql.py reads whole rows out of the change table and is
    bound by neither, so applying them to a SQL Server source reports a
    problem that is not one.
    """
    replicated = engine in ("postgres", "postgresql")
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
    if missing and replicated:
        out.append(f"{table}: the row filter reads {', '.join(missing)}, which "
                   f"is not in the replica identity ({', '.join(identity)}); "
                   f"an update or delete would be rejected")

    columns = rule.get("columns")
    if columns and replicated and not set(identity) <= set(columns):
        out.append(f"{table}: the column list omits "
                   f"{', '.join(sorted(set(identity) - set(columns)))}, which "
                   f"the replica identity needs")

    declared = {p["name"] for p in model.get("properties") or []}
    unknown = sorted(set(columns or []) - declared)
    if unknown:
        out.append(f"{table}: the column list names {', '.join(unknown)}, "
                   f"which the contract does not declare")

    # `generated` names columns the target fills. Every rule below is about a
    # name that is in the wrong half of the sync: the target cannot generate
    # something the source is also sending, and nothing that reads a source row
    # can read a column only the target has. Engine-neutral, unlike rules 2 and
    # 3 above -- this is the shape of the target table, which both paths share.
    generated = rule.get("generated") or {}
    # Both halves of the reason, because they are different failures. A name
    # that is also being replicated is emitted twice and `create table` fails
    # at the first sync. A name the contract declares but the rule leaves out
    # -- a column held back for privacy -- would not fail: it would quietly
    # reappear in the target meaning something else entirely, which is worse.
    clash = sorted(set(generated) & set(columns or declared))
    if clash:
        out.append(f"{table}: {', '.join(clash)} is both a replicated column "
                   f"and a generated one, so the target would declare it "
                   f"twice and `create table` would fail at the first sync")
    shadowed = sorted((set(generated) & declared) - set(clash))
    if shadowed:
        out.append(f"{table}: {', '.join(shadowed)} is a column the contract "
                   f"declares and this rule does not replicate; a generated "
                   f"column may not reuse the name of one held back")
    keyed = sorted(set(generated) & set(identity))
    if keyed:
        out.append(f"{table}: {', '.join(keyed)} is generated by the target, "
                   f"so the source never sends it and it cannot be part of "
                   f"the identity rows are matched on")
    filtered = sorted(set(generated) & filter_columns(rule.get("filter", "")))
    if filtered:
        out.append(f"{table}: the row filter reads {', '.join(filtered)}, "
                   f"which the target generates; the filter is evaluated "
                   f"against the source row, which does not have it")
    blank = sorted(n for n, f in generated.items() if not str(f or "").strip())
    if blank:
        out.append(f"{table}: {', '.join(blank)} is generated but does not say "
                   f"how; give it a column definition")
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


def author_rule(contract: dict, server: str, row_filter: str | None = None,
                columns: list[str] | None = None,
                identity: list[str] | None = None) -> tuple[dict, list[str]]:
    """A proposed `syncTo` rule, checked the way one already on disk is.

    `/api/sync` runs `problems()` and `unsound_identity()` against the rule a
    contract already carries; this is the same two checks against one that
    does not exist yet, which is what makes a write endpoint safe to add
    without a second, weaker copy of ADR 0008's four preconditions -- see
    issue #10. An empty problem list means the rule is sound; it is the
    caller's job to persist it.
    """
    if not any(s.get("server") == server for s in contract.get("servers", [])):
        return {}, [f"{server!r} is not a servers[] entry on this contract"]
    rule = {"server": server}
    if row_filter:
        rule["filter"] = row_filter
    if columns:
        rule["columns"] = columns
    if identity:
        rule["identity"] = identity
    engine = next((s for s in contract.get("servers", [])
                   if s.get("server") == "erp"), {}).get("type", "")
    bad = (problems(contract["schema"][0], rule, engine)
           + unsound_identity(contract, rule))
    return rule, bad


# The contract states a physical type per property, in the *source's* dialect.
# Replicating SQL Server into Postgres therefore needs a translation, and only
# for the types the contracts actually use -- guessing at the rest would be a
# type mapping library nobody asked for.
TSQL_TO_PG = {"int": "integer", "smallint": "smallint", "bigint": "bigint",
              "bit": "boolean", "char": "text", "nchar": "text",
              "varchar": "text", "nvarchar": "text", "text": "text",
              "date": "date", "datetime": "timestamp",
              "datetime2": "timestamp", "decimal": "numeric",
              "numeric": "numeric", "float": "double precision",
              "money": "numeric", "uniqueidentifier": "uuid"}
# The types whose parenthesised part means the same thing in Postgres. A string
# length is dropped on purpose -- every textual type becomes `text` -- but
# decimal(14,2) is not a longer spelling of numeric: a bare numeric accepts a
# scale the source would reject, so the replica could hold a value the source
# cannot. Issue #50. datetime2(n) is left out: SQL Server allows n up to 7 and
# Postgres's timestamp(n) stops at 6.
KEEPS_ARGUMENTS = {"decimal", "numeric"}


def target_table_statement(model: dict, schema: str, rule: dict,
                           source_type: str) -> sql.Composed:
    """The target table, as the contract describes it.

    Nothing creates this: logical replication replicates into a table that has
    to be there already, and the CDC reader upserts into one. It was created by
    hand while this was being built, which meant a clean install of the whole
    stack would have failed at the first sync.

    Only the replicated columns, so a column left out of the rule does not
    exist in the target at all -- the privacy boundary is physical. `generated`
    is the one exception and it does not weaken that: those columns come from
    the target, never from the source, so nothing crosses the boundary to fill
    them.
    """
    columns = rule.get("columns") or [p["name"] for p in model["properties"]]
    identity = set(identity_columns(model, rule))
    types = {p["name"]: p.get("physicalType") or "text"
             for p in model.get("properties") or []}
    mssql = source_type in ("sqlserver", "mssql")
    defs = []
    for name in columns:
        physical = str(types.get(name, "text")).lower()
        if mssql:
            base, paren, args = physical.partition("(")
            physical = TSQL_TO_PG.get(base.strip(), "text")
            if paren and base.strip() in KEEPS_ARGUMENTS:
                physical += "(" + args
        defs.append(sql.SQL("{} {}").format(
            sql.Identifier(name), sql.SQL(physical))
            + (sql.SQL(" not null") if name in identity else sql.SQL("")))
    # Raw SQL from the contract, like `physicalType` above it: the fragment is
    # the point -- `bigint generated always as identity` and `timestamptz not
    # null default now()` are different enough that a keyword for each would be
    # a vocabulary we then maintain. problems() is what refuses a name that
    # would collide with a declared column.
    for name, fragment in (rule.get("generated") or {}).items():
        defs.append(sql.SQL("{} {}").format(
            sql.Identifier(name), sql.SQL(str(fragment))))
    return sql.SQL("create table if not exists {}.{} ({})").format(
        sql.Identifier(schema), sql.Identifier(
            model.get("physicalName") or model["name"]),
        sql.SQL(", ").join(defs))


def generated_statements(model: dict, schema: str,
                         rule: dict) -> list[sql.Composed]:
    """`generated` columns, added to a target that already exists.

    Separate from target_table_statement because that one is `create table if
    not exists`: a replica created before the rule gained a generated column
    keeps its old shape, and the feature would silently do nothing on every
    deployment that already ran. Target-only, unlike _identity_statements --
    that one runs on both ends, and the source must not grow these columns.

    `add column if not exists` fills existing rows: measured on the demo's
    50 288-row replica, the sequence numbered every one of them and a second
    run is a NOTICE, not an error.
    """
    table = model.get("physicalName") or model["name"]
    return [sql.SQL("alter table {}.{} add column if not exists {} {}").format(
        sql.Identifier(schema), sql.Identifier(table),
        sql.Identifier(name), sql.SQL(str(fragment)))
        for name, fragment in (rule.get("generated") or {}).items()]


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


def truncate_statement(model: dict, schema: str) -> sql.Composed:
    """Empty the target before the subscription copies into it again.

    `copy_data = true` is not idempotent: the replica identity index the
    target needs is exactly what the second copy collides with, and the table
    sync worker then dies on a duplicate key every few seconds for ever while
    the apply worker stays up. See issue #35. Unconditional -- the copy
    repopulates the table either way, and a branch on "is it empty" only
    hides which case was taken.
    """
    return sql.SQL("truncate {}.{}").format(
        sql.Identifier(schema),
        sql.Identifier(model.get("physicalName") or model["name"]))


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
    if mode(rule) == "view":
        from core import sync_view
        with psycopg.connect(_dsn(source, *_credentials())) as cx:
            render = lambda s: s.as_string(cx)  # noqa: E731
            return {
                "contract": contract["id"], "engine": "view (postgres_fdw)",
                "problems": sync_view.problems(
                    model, rule, contract, source.get("type")),
                "source": [render(s) for s in sync_view.source_statements(
                    model, source.get("schema", "public"), rule)],
                "target": [render(s) for s in sync_view.target_statements(
                    model, dict(rule, contract_id=contract["id"]), source,
                    target.get("schema", "public"))],
            }
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
            "target": [render(target_table_statement(
                           model, target.get("schema", "public"), rule,
                           source.get("type")))]
                      + [render(s) for s in
                       generated_statements(model, target.get("schema", "public"), rule)]
                      + [render(s) for s in
                       _identity_statements(model, target.get("schema", "public"), rule)]
                      + [render(truncate_statement(
                           model, target.get("schema", "public")))]
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
    if mode(rule) == "view":
        return _apply_view(contract, p, model, rule, source, target)
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

    # Nothing else makes it: db-init.sql runs once on an empty volume, long
    # before a contract names a replica.
    from core.bootstrap_db import ensure_database, grant_reader
    if ensure_database(target["host"], target.get("port", 5432),
                       target["database"]):
        grant_reader(target["host"], target.get("port", 5432),
                     target["database"], [target.get("schema", "public")])

    with psycopg.connect(_dsn(target, user, password), autocommit=True) as cx:
        cx.execute(target_table_statement(
            model, target.get("schema", "public"), rule, source.get("type")))
        for stmt in generated_statements(model, target.get("schema", "public"), rule):
            cx.execute(stmt)
        for stmt in _identity_statements(model, target.get("schema", "public"), rule):
            cx.execute(stmt)
        # DROP SUBSCRIPTION also drops the slot on the *publisher* by default --
        # harmless the first time (nothing to lose yet), but fatal on a re-apply:
        # it takes down the exact slot the source block above just confirmed
        # exists, and CREATE SUBSCRIPTION below expects it to still be there
        # (create_slot = false). Detaching first makes DROP SUBSCRIPTION leave
        # the slot alone, so a re-apply of an already-correct rule stays applied
        # instead of landing exactly the silent failure this module exists to
        # prevent -- see issue #16.
        # pg_subscription is a *shared* catalog -- visible from every database
        # in the cluster -- so the name alone is not enough to know it is this
        # database's subscription rather than another contract's of the same
        # name on a different target.
        if cx.execute(
                "select 1 from pg_subscription s join pg_database d "
                "on d.oid = s.subdbid where d.datname = current_database() "
                "and s.subname = %s", (name,)).fetchone():
            cx.execute(sql.SQL("alter subscription {} disable").format(
                sql.Identifier(name)))
            cx.execute(sql.SQL(
                "alter subscription {} set (slot_name = none)").format(
                sql.Identifier(name)))
        cx.execute(sql.SQL("drop subscription if exists {}").format(
            sql.Identifier(name)))
        # After the drop, or the truncate contends with the table sync worker
        # the subscription still has running.
        cx.execute(truncate_statement(model, target.get("schema", "public")))
        cx.execute(sql.SQL(
            "create subscription {} connection {} publication {} "
            "with (create_slot = false, slot_name = {}, copy_data = true)").format(
            sql.Identifier(name), sql.Literal(_dsn(source, user, password)),
            sql.Identifier(name), sql.Literal(slot)))
    p["applied"] = True
    return p


def _apply_view(contract: dict, p: dict, model: dict, rule: dict,
                source: dict, target: dict) -> dict:
    """No publication, no subscription: a granted role and a foreign table."""
    from core import sync_view
    from core.bootstrap_db import ensure_database, grant_reader

    user, password = _credentials()
    with psycopg.connect(_dsn(source, user, password), autocommit=True) as cx:
        sync_view.ensure_role(cx)
        for stmt in sync_view.source_statements(
                model, source.get("schema", "public"), rule):
            cx.execute(stmt)
        # The grant is the privacy boundary in this mode, so it is verified
        # rather than assumed -- see ADR 0017.
        leaked = sync_view.ungranted(
            cx, model, source.get("schema", "public"), rule, contract)
    if leaked:
        p["problems"] = [f"{model.get('physicalName') or model['name']}: "
                         f"{sync_view.FDW_ROLE} can still read "
                         f"{', '.join(leaked)} after the grant"]
        return p

    if ensure_database(target["host"], target.get("port", 5432),
                       target["database"]):
        grant_reader(target["host"], target.get("port", 5432),
                     target["database"], [target.get("schema", "public")])
    with psycopg.connect(_dsn(target, user, password), autocommit=True) as cx:
        # A copy-mode target may be sitting under the name this view wants.
        kind = cx.execute(
            "select relkind from pg_class where oid = to_regclass(%s)",
            (f"{target.get('schema', 'public')}."
             f"{model.get('physicalName') or model['name']}",)).fetchone()
        if kind:
            cx.execute(sql.SQL("drop {} if exists {}.{} cascade").format(
                sql.SQL("view" if kind[0] == "v" else "table"),
                sql.Identifier(target.get("schema", "public")),
                sql.Identifier(model.get("physicalName") or model["name"])))
        for stmt in sync_view.target_statements(
                model, dict(rule, contract_id=contract["id"]), source,
                target.get("schema", "public")):
            cx.execute(stmt)
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
    if mode(rule) == "view":
        from core import sync_view
        return {"contract": contract["id"], **sync_view.status(
            contract["schema"][0], target, target.get("schema", "public"),
            *_credentials())}
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
        # The apply worker being up is not the same question as whether the
        # table is streaming. A table stuck in the initial copy ('i' or 'd')
        # has a live apply worker, an active slot and zero lag, and replicates
        # nothing -- issue #35, where a re-apply put it there permanently.
        # 'r' is ready and 's' is synchronised; anything else is not flowing.
        out["copying"] = sorted(
            r[0] for r in cx.execute(
                """select srrelid::regclass::text from pg_subscription_rel r
                   join pg_subscription s on s.oid = r.srsubid
                   where s.subname = %s and r.srsubstate not in ('r', 's')""",
                (name,)).fetchall())
    out["streaming"] = out["worker_running"] and not out["copying"]
    # Both true and still behind is lag; slot active with no worker, or a
    # table that never left the copy, is the silent failure this command
    # exists for.
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
