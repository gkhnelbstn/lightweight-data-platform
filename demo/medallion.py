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
rebuilds the warehouse from whatever the sources hold now. `dim.customer` is
the exception -- it is Type 2, so it is merged into rather than rebuilt, and a
re-run over unchanged sources adds no version.
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
    # cascade: the runner leaves `asof_*` views on these tables, and they are
    # rebuilt by the next run -- without it a second warehouse build fails.
    dwh.execute(S.SQL("drop table if exists {} cascade").format(target))
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


# --- the Type 2 merge ---------------------------------------------------------
# The one table here that is *not* rebuilt. A customer re-graded from SMB to ENT
# has to leave July's revenue where it was, and a dimension that is dropped every
# run cannot: the old segment stops existing at the source and here at the same
# moment. So the current version is closed and a new one opened, and `fct.orders`
# joins as of the order date. The intervals are stated as rules in
# `contracts/dwh_dim_customer.odcs.yaml`.

SCD2_DDL = """
    create table if not exists dim.customer (
      customer_key bigint generated always as identity primary key,
      customer_id  bigint  not null,
      name         text,
      country      text,
      segment      text,
      valid_from   date    not null,
      valid_to     date,
      is_current   boolean not null default true,
      loaded_at    date    not null
    )"""

# Half-open intervals: [valid_from, valid_to). The closing date and the next
# version's opening date are the same day, which is what stops the as-of join
# matching two rows -- the failure the fct uniqueness rule catches.
# `s.loaded_at > d.valid_from` keeps a same-day change from opening a
# zero-length version, which would leave that day with no version at all.
SCD2_CLOSE = """
    update dim.customer d
       set valid_to = s.loaded_at, is_current = false
      from raw.customers s
     where d.customer_id = s.customer_id
       and d.is_current
       and s.loaded_at > d.valid_from
       and (d.name, d.country, d.segment) is distinct from
           (s.name, s.country, coalesce(s.segment, 'UNKNOWN'))"""

# New customers, and the versions the close above just retired. `UNKNOWN` rather
# than null because null compares equal to nothing, so an unsegmented customer
# would look changed every single run.
#
# The *first* version of a customer opens at -infinity, not at the day the row
# arrived. `loaded_at` in the ERP moves when a row is updated, so a customer
# re-graded yesterday looks like it arrived yesterday -- and every order they
# placed before that would match no version and lose its country. Which is
# exactly what happened: 132 orders, on the first build after a re-grade. We
# did not observe when this customer began, only that this is the oldest
# version we have, so it covers everything before the next change.
#
# `0001-01-01` rather than `-infinity`: psycopg refuses to hand an infinite date
# back to Python at all, and the API reads this table.
SCD2_OPEN = """
    insert into dim.customer (customer_id, name, country, segment,
                              valid_from, loaded_at)
    select s.customer_id, s.name, s.country,
           coalesce(s.segment, 'UNKNOWN'),
           case when exists (select 1 from dim.customer x
                              where x.customer_id = s.customer_id)
                then s.loaded_at else date '0001-01-01' end,
           s.loaded_at
      from raw.customers s
      left join dim.customer d
        on d.customer_id = s.customer_id and d.is_current
     where d.customer_key is null"""


def merge_dim_customer(dwh) -> tuple[int, int]:
    """Close changed versions, open new ones. Returns (opened, closed).

    Order matters: closing first is what makes a changed customer look new to
    the insert.
    """
    # A warehouse built before the dimension was Type 2 has a table of the same
    # name with none of the interval columns. There is no history in it to
    # preserve -- it was a copy of the source -- so it is rebuilt rather than
    # migrated. Nothing else here is dropped conditionally.
    type1 = dwh.execute(
        """select 1 from information_schema.tables t
            where t.table_schema = 'dim' and t.table_name = 'customer'
              and not exists (select 1 from information_schema.columns c
                               where c.table_schema = 'dim'
                                 and c.table_name = 'customer'
                                 and c.column_name = 'is_current')""").fetchone()
    if type1:
        dwh.execute("drop table dim.customer cascade")
    dwh.execute(SCD2_DDL)
    closed = dwh.execute(SCD2_CLOSE).rowcount
    opened = dwh.execute(SCD2_OPEN).rowcount
    return opened, closed


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
            drop table if exists stg.orders cascade;
            create table stg.orders as
            select order_id, customer_id, order_date, status, currency,
                   net_amount, loaded_at
            from raw.orders
            where customer_id is not null and status <> 'CANCELLED'""")

        # --- dim: Type 2, so the warehouse keeps what the ERP overwrote ---
        opened, closed = merge_dim_customer(dwh)

        # --- fct: modelled ------------------------------------------------
        dwh.execute("""
            drop table if exists fct.orders cascade;
            create table fct.orders as
            select o.order_id, o.customer_id, c.country, c.segment,
                   o.order_date, o.currency, o.net_amount
            from stg.orders o
            left join dim.customer c
              on c.customer_id = o.customer_id
             and o.order_date >= c.valid_from
             and (c.valid_to is null or o.order_date < c.valid_to)""")

        # --- mart: what a dashboard reads ---------------------------------
        dwh.execute("""
            drop table if exists mart.revenue_daily cascade;
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
    print(f"  {'dim.customer':<{width}}  {opened:>7} versions opened, {closed} closed")


if __name__ == "__main__":
    main()
