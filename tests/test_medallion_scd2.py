"""The Type 2 merge, against a real PostgreSQL when there is one.

Everything else here runs without a database, and this still does: with no
server reachable it skips. But the merge is three statements whose whole
correctness is in how `is distinct from`, the close and the insert interact,
and asserting on SQL text would test nothing. So when the stack is up, it runs.

    docker compose -f compose.yaml -f compose.demo.yaml --profile demo up -d
    pytest tests/test_medallion_scd2.py
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

psycopg = pytest.importorskip("psycopg")

HOST = os.getenv("DWH_HOST", "localhost")
PORT = int(os.getenv("DWH_PORT", "5432"))
SCRATCH = "dq_scd2_test"

DAY1 = date(2026, 1, 1)
DAY2 = date(2026, 2, 1)
# The first version of a customer opens here rather than on the day the source
# row arrived -- see SCD2_OPEN. Not `-infinity`, which psycopg cannot return.
DAWN = date(1, 1, 1)


@pytest.fixture()
def dwh():
    """A throwaway database with the two schemas the merge names."""
    from core.bootstrap_db import admin_dsn, ensure_database
    try:
        with psycopg.connect(admin_dsn(HOST, PORT), connect_timeout=2,
                             autocommit=True) as cx:
            cx.execute(f'drop database if exists "{SCRATCH}" with (force)')
    except psycopg.OperationalError as exc:
        pytest.skip(f"no PostgreSQL at {HOST}:{PORT} ({exc.__class__.__name__})")
    ensure_database(HOST, PORT, SCRATCH)
    cx = psycopg.connect(admin_dsn(HOST, PORT, SCRATCH), autocommit=True)
    cx.execute("create schema raw; create schema dim")
    cx.execute("""create table raw.customers (
                    customer_id bigint, name text, country text,
                    segment text, loaded_at date)""")
    try:
        yield cx
    finally:
        cx.close()
        with psycopg.connect(admin_dsn(HOST, PORT), autocommit=True) as adm:
            adm.execute(f'drop database if exists "{SCRATCH}" with (force)')


def source(cx, rows):
    cx.execute("truncate raw.customers")
    with cx.cursor() as cur:
        cur.executemany("insert into raw.customers values (%s,%s,%s,%s,%s)", rows)


def versions(cx, customer_id=1):
    return cx.execute(
        """select segment, valid_from, valid_to, is_current from dim.customer
            where customer_id = %s order by valid_from""", (customer_id,)).fetchall()


def test_a_first_load_opens_one_version_covering_everything_before_it(dwh):
    """Not at `loaded_at`: an ERP moves that when it updates a row, so the
    first version of a customer re-graded yesterday would start yesterday and
    every order they placed before it would resolve to nothing."""
    from demo.medallion import merge_dim_customer
    source(dwh, [(1, "Acme", "TR", "SMB", DAY1)])
    assert merge_dim_customer(dwh) == (1, 0)
    assert versions(dwh) == [("SMB", DAWN, None, True)]


def test_an_unchanged_source_adds_nothing(dwh):
    """The demo re-runs the warehouse build; a rebuild is not a change."""
    from demo.medallion import merge_dim_customer
    source(dwh, [(1, "Acme", "TR", "SMB", DAY1)])
    merge_dim_customer(dwh)
    assert merge_dim_customer(dwh) == (0, 0)
    assert len(versions(dwh)) == 1


def test_a_change_closes_the_old_version_and_opens_a_new_one(dwh):
    """The ERP overwrote SMB; the warehouse still knows January was SMB."""
    from demo.medallion import merge_dim_customer
    source(dwh, [(1, "Acme", "TR", "SMB", DAY1)])
    merge_dim_customer(dwh)
    source(dwh, [(1, "Acme", "TR", "ENT", DAY2)])
    assert merge_dim_customer(dwh) == (1, 1)
    assert versions(dwh) == [("SMB", DAWN, DAY2, False),
                             ("ENT", DAY2, None, True)]


def test_the_intervals_do_not_overlap(dwh):
    """Two versions covering one day would duplicate every order of that day
    in the as-of join, which is the fct uniqueness rule's failure mode."""
    from demo.medallion import merge_dim_customer
    source(dwh, [(1, "Acme", "TR", "SMB", DAY1)])
    merge_dim_customer(dwh)
    for i, seg in enumerate(("MID", "ENT"), start=1):
        source(dwh, [(1, "Acme", "TR", seg, DAY2 + timedelta(days=i))])
        merge_dim_customer(dwh)
    overlaps = dwh.execute(
        """select count(*) from dim.customer a join dim.customer b
             on a.customer_id = b.customer_id
            and a.customer_key <> b.customer_key
            and a.valid_from < coalesce(b.valid_to, 'infinity'::date)
            and b.valid_from < coalesce(a.valid_to, 'infinity'::date)""").fetchone()[0]
    assert overlaps == 0
    assert len(versions(dwh)) == 3


def test_an_unsegmented_customer_does_not_reopen_every_run(dwh):
    """Null compares equal to nothing, so an untouched null would look like a
    change on every single run. It is landed as UNKNOWN for that reason."""
    from demo.medallion import merge_dim_customer
    source(dwh, [(1, "Acme", "TR", None, DAY1)])
    merge_dim_customer(dwh)
    source(dwh, [(1, "Acme", "TR", None, DAY2)])
    assert merge_dim_customer(dwh) == (0, 0)
    assert versions(dwh) == [("UNKNOWN", DAWN, None, True)]


def test_an_order_older_than_the_dimension_still_resolves(dwh):
    """The regression this cost: the demo re-grades customers in the ERP, and
    the first warehouse build after that lost 132 orders' country because the
    only version on file started the day of the re-grade."""
    from demo.medallion import merge_dim_customer
    source(dwh, [(1, "Acme", "TR", "ENT", DAY2)])          # re-graded, watermark moved
    merge_dim_customer(dwh)
    unresolved = dwh.execute(
        """select count(*) from (values (%s::date)) o(order_date)
             left join dim.customer c
               on o.order_date >= c.valid_from
              and (c.valid_to is null or o.order_date < c.valid_to)
            where c.customer_key is null""", (DAY1,)).fetchone()[0]
    assert unresolved == 0
