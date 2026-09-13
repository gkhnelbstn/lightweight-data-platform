"""PostgreSQL: what this platform does for itself. See core/engines/__init__.py."""
from __future__ import annotations

import os
from datetime import date

import psycopg
from psycopg import sql

ALIASES = ("postgres", "postgresql")
DIALECT = "postgres"


def oddrn_generator():
    """Imported here rather than at module scope: `oddrn-generator` is the
    `odd` extra, and `core` must install without it."""
    from oddrn_generator import PostgresqlGenerator
    return PostgresqlGenerator


def dsn(server: dict) -> str:
    """Where this contract's tables actually live.

    `ERP_DSN` is only right while every contract is in one database. A
    warehouse contract points at `dwh`, and building its window against `erp`
    would create the views in the wrong place -- and silently, because `create
    schema if not exists` succeeds anywhere.
    """
    user = os.getenv("SYNC_USERNAME", "postgres")
    password = os.getenv("SYNC_PASSWORD", "postgres")
    return (f"host={server['host']} port={server.get('port', 5432)} "
            f"dbname={server['database']} user={user} password={password}")


def connect(server: dict):
    return psycopg.connect(dsn(server))


def count_rows(server: dict, schema: str, tables: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    with psycopg.connect(dsn(server)) as cx:
        for t in tables:
            out[t] = cx.execute(sql.SQL("select count(*) from {}.{}").format(
                sql.Identifier(schema), sql.Identifier(t))).fetchone()[0]
    return out


def build_window(contract: dict, as_of: date, source: dict, src_schema: str,
                 win_schema: str, loaded_at: str, window: str,
                 dsn_override: str | None = None) -> int:
    """A schema of views over one day's arrivals.

    A *schema*, because `search_path` makes an unqualified `sales_orders`
    resolve to the view -- which is why a Postgres contract's custom SQL must
    not qualify its schema, or the window is bypassed. SQL Server has no
    search_path and needs a database instead; see core/engines/sqlserver.py.
    """
    from core.runner import _tables, window_predicate

    made = 0
    with psycopg.connect(dsn_override or dsn(source), autocommit=True) as cx:
        cx.execute(f'create schema if not exists "{win_schema}"')
        named = {physical for _, physical in _tables(contract)}
        by_table = {(m.get("physicalName") or m["name"]): m
                    for m in contract.get("schema", [])}
        # Every table in the source schema is mirrored: the ones the contract
        # names get the day's rows, the rest are passed through so joins work.
        rows = cx.execute(
            """select table_name from information_schema.tables
               where table_schema = %s and table_type = 'BASE TABLE'""",
            (src_schema,)).fetchall()
        for (table,) in rows:
            has_window = cx.execute(
                """select 1 from information_schema.columns
                   where table_schema = %s and table_name = %s
                     and column_name = %s""",
                (src_schema, table, loaded_at)).fetchone()
            # A view definition cannot take a bind parameter, so the date is
            # composed in as a literal -- psycopg quotes it, and `as_of` is a
            # date object rather than anything a caller typed.
            stmt = sql.SQL("create or replace view {win}.{tbl} as "
                           "select * from {src}.{tbl}").format(
                win=sql.Identifier(win_schema), src=sql.Identifier(src_schema),
                tbl=sql.Identifier(table))
            if has_window and table in named:
                if window == "incremental":
                    template = window_predicate(contract, by_table.get(table))
                else:
                    # the comparison the daily window exists to beat
                    template = "{col} <= {day}"
                stmt = stmt + sql.SQL(" where ") + sql.SQL(template).format(
                    col=sql.Identifier(loaded_at), day=sql.Literal(as_of))
            cx.execute(stmt)
            made += 1
    return made


def profile_columns(server: dict, schema: str, table: str,
                    columns: list[str]) -> dict[str, dict]:
    """Null count and distinct count per column, in one scan.

    One `select` with two aggregates per column rather than a query per
    column: the table is read once either way, and the runner is already
    reading it. See core/profile.py for why this is the only shape of
    profiling here.
    """
    if not columns:
        return {}
    parts = [sql.SQL("count(*)")]
    for c in columns:
        parts.append(sql.SQL("count({0})").format(sql.Identifier(c)))
        parts.append(sql.SQL("count(distinct {0})").format(sql.Identifier(c)))
    stmt = sql.SQL("select {} from {}.{}").format(
        sql.SQL(", ").join(parts), sql.Identifier(schema), sql.Identifier(table))
    with psycopg.connect(dsn(server)) as cx:
        row = cx.execute(stmt).fetchone()
    return _shape(row, columns)


def _shape(row, columns: list[str]) -> dict[str, dict]:
    rows = row[0]
    out = {}
    for i, c in enumerate(columns):
        present, distinct = row[1 + i * 2], row[2 + i * 2]
        out[c] = {"rows": rows, "nulls": rows - present, "distinct": distinct}
    return out
