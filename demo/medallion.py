"""Build a medallion warehouse the way a scheduler would, and say so to ODD.

This stands in for the Prefect flow in the scenario this demo exists to answer:
tables are read out of the ERP databases, landed as `raw`, cleaned into `stg`,
modelled as `fct` and `dim`, aggregated into `mart`, and the marts are what
Superset charts.

It runs *outside* the databases on purpose, because that is the honest shape of
the problem. Postgres cannot query another database, the ERP is partly SQL
Server, and a scheduler moving rows in Python leaves no trace in either engine.
**Nothing can infer this lineage** -- there is no view definition to parse and
no foreign key to follow. Something has to declare it, which is why the
warehouse contracts carry `derivedFrom` and integrations/odd/lineage.py
publishes it.

    docker compose exec app python demo/medallion.py

Idempotent: every step is `create table ... as` behind a drop, so a re-run
rebuilds the warehouse from whatever the sources hold now.
"""
from __future__ import annotations

import os

import psycopg

ERP_DSN = os.getenv("ERP_DSN", "postgresql://postgres:postgres@db:5432/erp")
DWH_DSN = os.getenv("DWH_DSN", "postgresql://postgres:postgres@db:5432/dwh")

SCHEMAS = ("raw", "stg", "fct", "dim", "mart")


def read(cx, sql: str) -> tuple[list[str], list[tuple]]:
    cur = cx.execute(sql)
    return [d[0] for d in cur.description], cur.fetchall()


def land(dwh, schema: str, table: str, columns: list[str], types: list[str],
         rows: list[tuple]) -> int:
    """Land a result set as a table. `raw` keeps whatever the source gave."""
    from psycopg import sql as S

    target = S.SQL("{}.{}").format(S.Identifier(schema), S.Identifier(table))
    dwh.execute(S.SQL("drop table if exists {}").format(target))
    dwh.execute(S.SQL("create table {} ({})").format(
        target, S.SQL(", ").join(
            S.SQL("{} {}").format(S.Identifier(c), S.SQL(t))
            for c, t in zip(columns, types))))
    if rows:
        with dwh.cursor() as cur:
            with cur.copy(S.SQL("copy {} ({}) from stdin").format(
                    target, S.SQL(", ").join(S.Identifier(c) for c in columns))) as copy:
                for row in rows:
                    copy.write_row(row)
    return len(rows)


def main() -> None:
    # The warehouse is the demo's, so the demo makes it -- deploy/db-init.sql
    # is the product's two databases and runs once, on an empty volume.
    from core.bootstrap_db import ensure_database, grant_reader

    host = os.getenv("DWH_HOST", "db")
    port = int(os.getenv("DWH_PORT", "5432"))
    warehouse = os.getenv("DWH_NAME", "dwh")
    if ensure_database(host, port, warehouse):
        print(f"  created database {warehouse}")
    # asof_* is where the runner materialises a windowed view of a mart; the
    # reader has to see those too or every warehouse check fails on permission.
    grant_reader(host, port, warehouse,
                 list(SCHEMAS) + [f"asof_{s}" for s in SCHEMAS])

    with psycopg.connect(ERP_DSN) as erp, psycopg.connect(DWH_DSN, autocommit=True) as dwh:
        for schema in SCHEMAS:
            dwh.execute(f'create schema if not exists "{schema}"')

        # --- raw: what the source gave, unchanged -------------------------
        counts = {}
        orders_cols, orders = read(erp, """
            select order_id, customer_id, order_date, status, currency,
                   net_amount, loaded_at from sales_orders""")
        counts["raw.orders"] = land(
            dwh, "raw", "orders", orders_cols,
            ["bigint", "bigint", "date", "text", "text", "numeric", "date"], orders)

        cust_cols, customers = read(erp, """
            select customer_id, name, country, segment, loaded_at from customers""")
        counts["raw.customers"] = land(
            dwh, "raw", "customers", cust_cols,
            ["bigint", "text", "text", "text", "date"], customers)

        # --- stg: cleaned. The one place a rule about the source pays off --
        dwh.execute("""
            drop table if exists stg.orders;
            create table stg.orders as
            select order_id, customer_id, order_date, status, currency,
                   net_amount, loaded_at
            from raw.orders
            where customer_id is not null and status <> 'CANCELLED'""")

        dwh.execute("""
            drop table if exists dim.customer;
            create table dim.customer as
            select customer_id, name, country,
                   coalesce(segment, 'UNKNOWN') as segment
            from raw.customers""")

        # --- fct: modelled ------------------------------------------------
        dwh.execute("""
            drop table if exists fct.orders;
            create table fct.orders as
            select o.order_id, o.customer_id, c.country, c.segment,
                   o.order_date, o.currency, o.net_amount
            from stg.orders o
            left join dim.customer c on c.customer_id = o.customer_id""")

        # --- mart: what a dashboard reads ---------------------------------
        dwh.execute("""
            drop table if exists mart.revenue_daily;
            create table mart.revenue_daily as
            select order_date, currency, country,
                   count(*) as orders, sum(net_amount) as revenue
            from fct.orders
            group by order_date, currency, country""")

        for name in ("stg.orders", "dim.customer", "fct.orders", "mart.revenue_daily"):
            schema, table = name.split(".")
            counts[name] = dwh.execute(
                f'select count(*) from "{schema}"."{table}"').fetchone()[0]

    width = max(len(k) for k in counts)
    for name, n in counts.items():
        print(f"  {name:<{width}}  {n:>7} rows")


if __name__ == "__main__":
    main()
