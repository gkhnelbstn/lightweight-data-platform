"""The window as built, against a real PostgreSQL when there is one.

`test_contracts.py` checks that a contract *declares* a daily server. That kept
passing for a week while `build_window` built nothing for the two warehouse
models ADR 0017 turned into views, and the runner quietly scored them over the
whole table (#46). This checks the relation exists and filters. It skips with
no server reachable, like tests/test_medallion_scd2.py.
"""
from __future__ import annotations

import os
from datetime import date

import pytest

psycopg = pytest.importorskip("psycopg")

HOST = os.getenv("DWH_HOST", "localhost")
PORT = int(os.getenv("DWH_PORT", "5432"))
SCRATCH = "dq_window_test"
DAY = date(2026, 1, 2)


@pytest.fixture()
def db():
    from core.bootstrap_db import admin_dsn, ensure_database
    try:
        with psycopg.connect(admin_dsn(HOST, PORT), connect_timeout=2,
                             autocommit=True) as cx:
            cx.execute(f'drop database if exists "{SCRATCH}" with (force)')
    except psycopg.OperationalError as exc:
        pytest.skip(f"no PostgreSQL at {HOST}:{PORT} ({exc.__class__.__name__})")
    ensure_database(HOST, PORT, SCRATCH)
    dsn = admin_dsn(HOST, PORT, SCRATCH)
    with psycopg.connect(dsn, autocommit=True) as cx:
        cx.execute("""
            create schema src;
            create table src.raw (id int, loaded_at date);
            insert into src.raw values (1, '2026-01-01'), (2, '2026-01-02');
            create view src.orders as select * from src.raw""")
    try:
        yield dsn
    finally:
        with psycopg.connect(admin_dsn(HOST, PORT), autocommit=True) as adm:
            adm.execute(f'drop database if exists "{SCRATCH}" with (force)')


def test_a_model_that_is_a_view_gets_a_filtered_window(db):
    from core.engines.postgres import build_window
    contract = {"schema": [{"name": "orders", "physicalName": "orders"}]}
    made = build_window(contract, DAY, {}, "src", "asof_src", "loaded_at",
                        "incremental", dsn_override=db)
    assert made == 2                                  # the view and the table
    with psycopg.connect(db) as cx:
        rows = cx.execute("select id from asof_src.orders").fetchall()
    assert rows == [(2,)]


def test_a_window_that_built_nothing_is_an_error_not_a_whole_table_score(
        monkeypatch):
    """No database: the runner is asked what it does when `build_window`
    returns 0 for a contract that declares one. It used to run on the real
    tables and store that as `incremental`."""
    from core import runner

    contract = {"id": "t.orders",
                "servers": [{"server": "erp", "type": "postgres",
                             "schema": "src"},
                            {"server": runner.DAILY_SERVER, "type": "postgres",
                             "schema": "asof_src"}],
                "schema": [{"name": "orders"}]}
    monkeypatch.setattr(runner, "build_window", lambda *a, **k: 0)

    def must_not_run(*a, **k):
        raise AssertionError("checks ran with no window")
    monkeypatch.setattr(runner, "run_contract", must_not_run)
    monkeypatch.setattr(runner, "table_rows", must_not_run)
    for name in ("init", "ensure_partition", "write_results", "write_score"):
        monkeypatch.setattr(runner.store, name, lambda *a, **k: None)
    monkeypatch.setattr(runner.store, "connect", _NullConnection)

    [result] = runner.run(DAY, [contract])
    assert result["errored"] == 1 and result["failed"] == 0


class _NullConnection:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
