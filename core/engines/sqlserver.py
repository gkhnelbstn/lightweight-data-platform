"""SQL Server. See core/engines/__init__.py."""
from __future__ import annotations

from datetime import date

ALIASES = ("sqlserver", "mssql")
DIALECT = "tsql"


def oddrn_generator():
    from oddrn_generator import MssqlGenerator
    return MssqlGenerator


def connect(server: dict):
    from core.sync_mssql import mssql_connect
    return mssql_connect(server)


def count_rows(server: dict, schema: str, tables: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    with connect(server) as cx:
        for t in tables:
            out[t] = cx.cursor().execute(
                f"select count(*) from [{schema}].[{t}]").fetchval()
    return out


def build_window(contract: dict, as_of: date, source: dict, src_schema: str,
                 win_schema: str, loaded_at: str, window: str,
                 dsn_override: str | None = None) -> int:
    """The same schema of views, in T-SQL -- except it is a database.

    Without a window a SQL Server contract is scored over its whole table every
    day, which is the cumulative scoring this project exists to argue against,
    quietly reintroduced by having implemented the window for one engine only.
    Its score sat at 0.8957 for forty-five days without moving.

    The Postgres window is a *schema* because `search_path` makes an
    unqualified `sales_orders` resolve to the view. SQL Server has no
    search_path -- an unqualified name resolves through the user's default
    schema -- and the rules in a T-SQL contract are written `dbo.sales_orders`
    anyway, so a second schema is invisible to them. A second *database* is
    not: `erp_asof.dbo.sales_orders` is what `dbo.sales_orders` means once the
    connection is pointed at it, and the contract needs no rewriting.
    """
    from core.runner import _tables, contract_window_database, window_predicate

    window_db = contract_window_database(contract) or f"{source['database']}_asof"
    with connect(source) as cx:
        # CREATE DATABASE cannot run inside a transaction, and pyodbc opens one
        # for you: "CREATE DATABASE statement not allowed within
        # multi-statement transaction."
        cx.autocommit = True
        cx.cursor().execute(
            f"if db_id('{window_db}') is null "
            f"exec('create database [{window_db}]')")

    made = 0
    named = {physical for _, physical in _tables(contract)}
    by_table = {(m.get("physicalName") or m["name"]): m
                for m in contract.get("schema", [])}
    with connect(source) as src, connect({**source, "database": window_db}) as win:
        tables = [r[0] for r in src.cursor().execute(
            "select table_name from information_schema.tables "
            "where table_schema = ? and table_type = 'BASE TABLE'",
            src_schema).fetchall()]
        cur = win.cursor()
        for table in tables:
            windowed = src.cursor().execute(
                "select 1 from information_schema.columns where table_schema = ? "
                "and table_name = ? and column_name = ?",
                src_schema, table, loaded_at).fetchone()
            stmt = (f"create or alter view [{win_schema}].[{table}] as select * "
                    f"from [{source['database']}].[{src_schema}].[{table}]")
            if windowed and table in named:
                template = (window_predicate(contract, by_table.get(table))
                            if window == "incremental" else "{col} <= {day}")
                # `as_of` is a date object and the column name comes from
                # information_schema, so neither is caller text.
                stmt += " where " + template.format(
                    col=f"[{loaded_at}]", day=f"'{as_of.isoformat()}'")
            cur.execute(stmt)
            made += 1
        win.commit()
    return made


def profile_columns(server: dict, schema: str, table: str,
                    columns: list[str]) -> dict[str, dict]:
    """As postgres.profile_columns, in T-SQL. Bracket quoting rather than
    psycopg's composer, the same way build_window above does it -- the column
    names come from the contract, not from anything a caller typed."""
    if not columns:
        return {}
    parts = ["count(*)"]
    for c in columns:
        parts.append(f"count([{c}])")
        parts.append(f"count(distinct [{c}])")
    stmt = f"select {', '.join(parts)} from [{schema}].[{table}]"
    with connect(server) as cx:
        row = cx.cursor().execute(stmt).fetchone()
    rows = row[0]
    return {c: {"rows": rows, "nulls": rows - row[1 + i * 2],
                "distinct": row[2 + i * 2]}
            for i, c in enumerate(columns)}
