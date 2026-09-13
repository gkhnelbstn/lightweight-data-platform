"""A `syncTo` target that is a view over the source, not a copy of it.

`mode: view` in the rule. Nothing is replicated: the target database gets a
`postgres_fdw` foreign table and a view that applies the same row filter and
column list the copy mode would have applied, so every consumer sees the same
name with the same shape and reads through to the source instead of a stale
copy of it. The databases do not have to be on the same machine -- a foreign
data wrapper is a client connection like any other -- they just have to be
reachable when somebody queries.

What changes with the mode, and is the reason it is a decision rather than a
default (ADR 0017):

* **The privacy boundary moves from absence to a grant.** In copy mode a column
  outside the list does not exist in the target. Here it exists at the source
  and the target could ask for it, so the mapped role is granted `select` on
  the listed columns *only*, and `apply` verifies that afterwards rather than
  trusting the grant to have worked.
* **The target is only as available as the source.** A copy keeps serving when
  the source is down; a view does not.
* **Every read crosses the network.** `postgres_fdw` pushes the filter down,
  but the source now carries the target's read load.

None of the four logical-replication preconditions apply -- there is no
replica identity to match an update against, because there are no updates.
"""
from __future__ import annotations

import os

import psycopg
import sqlglot
from psycopg import sql

FOREIGN_SCHEMA = "remote"
# One login role for every view-mode rule, with a column-level grant per table.
# It is the privacy boundary, so it is deliberately not the role the checks or
# the replication run as: those can read everything they are pointed at.
FDW_ROLE = "sync_fdw"


def server_name(contract_id: str) -> str:
    return "fdw_" + str(contract_id).replace(".", "_")


def _table(model: dict) -> str:
    return model.get("physicalName") or model["name"]


def _columns(model: dict, rule: dict) -> list[str]:
    return list(rule.get("columns") or [p["name"] for p in model["properties"]])


def _password() -> str:
    return os.getenv("SYNC_FDW_PASSWORD", "sync_fdw")


def source_statements(model: dict, schema: str, rule: dict) -> list[sql.Composed]:
    """The column-level grant that is the privacy boundary in this mode."""
    ident, table = sql.Identifier, _table(model)
    columns = _columns(model, rule)
    return [
        sql.SQL("revoke all on table {}.{} from {}").format(
            ident(schema), ident(table), ident(FDW_ROLE)),
        sql.SQL("grant usage on schema {} to {}").format(
            ident(schema), ident(FDW_ROLE)),
        sql.SQL("grant select ({}) on {}.{} to {}").format(
            sql.SQL(", ").join(ident(c) for c in columns),
            ident(schema), ident(table), ident(FDW_ROLE)),
    ]


def target_statements(model: dict, rule: dict, source: dict,
                      target_schema: str) -> list[sql.Composed]:
    """The wrapper, the foreign table and the view, in that order.

    The server is dropped and recreated rather than patched: its options carry
    the host and the database, and a rule that now points somewhere else must
    not leave a view reading the old place. `cascade` takes the user mapping,
    the foreign table and the view with it; all three are rebuilt below.
    """
    ident, table = sql.Identifier, _table(model)
    server = server_name(rule.get("contract_id", table))
    columns = _columns(model, rule)
    types = {p["name"]: p.get("physicalType") or "text"
             for p in model.get("properties") or []}
    out = [
        sql.SQL("create extension if not exists postgres_fdw"),
        sql.SQL("create schema if not exists {}").format(ident(FOREIGN_SCHEMA)),
        sql.SQL("drop server if exists {} cascade").format(ident(server)),
        sql.SQL("create server {} foreign data wrapper postgres_fdw "
                "options (host {}, port {}, dbname {})").format(
            ident(server), sql.Literal(source["host"]),
            sql.Literal(str(source.get("port", 5432))),
            sql.Literal(source["database"])),
        sql.SQL("create user mapping for current_user server {} "
                "options (user {}, password {})").format(
            ident(server), sql.Literal(FDW_ROLE), sql.Literal(_password())),
        sql.SQL("create foreign table {}.{} ({}) server {} "
                "options (schema_name {}, table_name {})").format(
            ident(FOREIGN_SCHEMA), ident(table),
            sql.SQL(", ").join(
                sql.SQL("{} {}").format(ident(c), sql.SQL(str(types.get(c, "text"))))
                for c in columns),
            ident(server), sql.Literal(source.get("schema", "public")),
            sql.Literal(table)),
    ]
    view = sql.SQL("create view {}.{} as select {} from {}.{}").format(
        ident(target_schema), ident(table),
        sql.SQL(", ").join(ident(c) for c in columns),
        ident(FOREIGN_SCHEMA), ident(table))
    if rule.get("filter"):
        view += sql.SQL(" where {}").format(sql.SQL(
            sqlglot.parse_one(rule["filter"], read="postgres").sql(dialect="postgres")))
    out.append(view)
    return out


def problems(model: dict, rule: dict, contract: dict, engine: str) -> list[str]:
    """Why this rule cannot be a view. None of them are replication's rules."""
    from core.sample import classified

    table, out = _table(model), []
    if engine not in ("postgres", "postgresql"):
        out.append(f"{table}: mode 'view' needs a foreign data wrapper for "
                   f"{engine}, and only postgres_fdw ships in this image -- "
                   f"tds_fdw would be a new image for one table (invariant 6)")
    exposed = sorted(classified(contract) & set(_columns(model, rule)))
    if exposed:
        out.append(f"{table}: {', '.join(exposed)} is classified and the column "
                   f"list includes it; in this mode the column is not absent "
                   f"from the target, it is only ungranted")
    declared = {p["name"] for p in model.get("properties") or []}
    unknown = sorted(set(rule.get("columns") or []) - declared)
    if unknown:
        out.append(f"{table}: the column list names {', '.join(unknown)}, "
                   f"which the contract does not declare")
    return out


def ungranted(cx, model: dict, schema: str, rule: dict,
              contract: dict) -> list[str]:
    """Columns the mapped role can still read that the rule does not list.

    Run after the grant, on the source, because a privacy boundary that was
    only asked for is not a boundary. `has_column_privilege` answers for the
    role rather than for the statement we think we ran.
    """
    listed = set(_columns(model, rule))
    table = f'{schema}.{_table(model)}'
    leaked = []
    for prop in model.get("properties") or []:
        if prop["name"] in listed:
            continue
        if cx.execute("select has_column_privilege(%s, %s, %s, 'select')",
                      (FDW_ROLE, table, prop["name"])).fetchone()[0]:
            leaked.append(prop["name"])
    return leaked


def ensure_role(cx) -> None:
    """The login the foreign server maps to. Created here so a clean install
    works; its grants are per table and come from `source_statements`."""
    if not cx.execute("select 1 from pg_roles where rolname = %s",
                      (FDW_ROLE,)).fetchone():
        cx.execute(sql.SQL("create role {} login password {}").format(
            sql.Identifier(FDW_ROLE), sql.Literal(_password())))
    else:
        cx.execute(sql.SQL("alter role {} login password {}").format(
            sql.Identifier(FDW_ROLE), sql.Literal(_password())))


def status(model: dict, target: dict, schema: str, user: str,
           password: str) -> dict:
    """Reading through is the only status that means anything here.

    A view whose source is unreachable is not a slow view, it is a broken one,
    and the query is the only thing that knows.
    """
    from core.sync import _dsn

    out = {"mode": "view", "table": _table(model)}
    try:
        with psycopg.connect(_dsn(target, user, password),
                             connect_timeout=5) as cx:
            cx.execute(sql.SQL("select 1 from {}.{} limit 1").format(
                sql.Identifier(schema), sql.Identifier(_table(model))))
        out["reachable"] = True
    except Exception as exc:  # noqa: BLE001 -- any failure is the same answer
        out["reachable"] = False
        out["error"] = str(exc).strip().splitlines()[0]
    return out
