"""core/profile.py against a real PostgreSQL, the way tests/test_store.py does.

Two numbers per column per day, and the thing worth pinning is that they are
numbers about *the window*, not the table -- a cumulative profile is the same
trap as a cumulative score, and it would look identical in every other way.
See issue #30.
"""
from __future__ import annotations

import os
from datetime import date

import pytest

psycopg = pytest.importorskip("psycopg")

HOST = os.getenv("DWH_HOST", "localhost")
PORT = int(os.getenv("DWH_PORT", "5432"))
SCRATCH = "dq_profile_test"


@pytest.fixture()
def scratch():
    """A database holding one table with a known shape: 4 rows, one null
    country, two distinct segments."""
    from core.bootstrap_db import admin_dsn, ensure_database
    try:
        with psycopg.connect(admin_dsn(HOST, PORT), connect_timeout=2,
                             autocommit=True) as cx:
            cx.execute(f'drop database if exists "{SCRATCH}" with (force)')
    except psycopg.OperationalError as exc:
        pytest.skip(f"no PostgreSQL at {HOST}:{PORT} ({exc.__class__.__name__})")
    ensure_database(HOST, PORT, SCRATCH)
    with psycopg.connect(admin_dsn(HOST, PORT, SCRATCH), autocommit=True) as cx:
        cx.execute("create schema shop")
        cx.execute("""create table shop.customers (
                        customer_id int, country text, segment text)""")
        cx.execute("""insert into shop.customers values
                        (1, 'TR', 'SMB'), (2, 'TR', 'MID'),
                        (3, null, 'SMB'), (4, 'DE', 'SMB')""")
    yield
    with psycopg.connect(admin_dsn(HOST, PORT), autocommit=True) as cx:
        cx.execute(f'drop database if exists "{SCRATCH}" with (force)')


def _contract() -> dict:
    return {
        "id": "test.customers",
        "servers": [{"server": "erp", "type": "postgres", "host": HOST,
                     "port": PORT, "database": SCRATCH, "schema": "shop"}],
        "schema": [{"name": "customers", "physicalName": "customers",
                    "properties": [{"name": "customer_id"}, {"name": "country"},
                                   {"name": "segment"}]}],
    }


def test_nulls_and_distincts_per_column(scratch):
    from core.profile import profile

    by_column = {r["column"]: r for r in profile(_contract(), "erp")}

    assert by_column["country"] == {
        "table": "customers", "column": "country",
        "rows": 4, "nulls": 1, "distinct": 2}
    # count(distinct) ignores nulls, which is the answer a uniqueness check
    # would give too -- three distinct values would be the wrong number.
    assert by_column["segment"]["distinct"] == 2
    assert by_column["customer_id"]["nulls"] == 0


def test_a_column_the_contract_does_not_declare_is_not_profiled(scratch):
    """The contract decides what is measured, here as everywhere else
    (invariant 1). A column nobody wrote down is not quietly profiled."""
    from core.profile import profile

    contract = _contract()
    contract["schema"][0]["properties"] = [{"name": "country"}]
    assert [r["column"] for r in profile(contract, "erp")] == ["country"]


def test_collect_never_fails_a_run(scratch):
    """A profile is a nicety, like the row counts beside it: an unreachable
    source must not fail a run that measured the contract fine."""
    from core.profile import collect

    contract = _contract()
    contract["servers"][0]["database"] = "no_such_database_here"
    assert collect(contract, date(2026, 1, 1), "erp") == 0
