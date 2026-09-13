"""core/sync_mssql.py's capture_start_lsn, against a real SQL Server with CDC.

Skips without one -- same pattern as tests/test_sync_apply.py. Needs the
Agent running (MSSQL_AGENT_ENABLED) and Developer/Enterprise edition, which
is what compose.demo.yaml's `mssql` service provides.

    docker compose -f compose.yaml -f compose.demo.yaml --profile demo up -d mssql
    pytest tests/test_sync_mssql_capture.py
"""
from __future__ import annotations

import os
import time

import pytest

pyodbc = pytest.importorskip("pyodbc")

HOST = os.getenv("MSSQL_HOST", "localhost")
PORT = os.getenv("MSSQL_PORT", "1433")
PASSWORD = os.getenv("MSSQL_SA_PASSWORD", "Str0ng!Passw0rd")
DB = "sync_mssql_capture_test"


def _connect(database: str = "master"):
    return pyodbc.connect(
        f"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={HOST},{PORT};"
        f"DATABASE={database};UID=sa;PWD={PASSWORD};"
        "TrustServerCertificate=yes;Encrypt=no", timeout=5, autocommit=True)


@pytest.fixture()
def cdc_table():
    """A throwaway database, one CDC-enabled table -- issue #17 reproduced
    without touching the real demo `erp` database."""
    def _drop_if_exists(adm) -> None:
        # single_user + rollback kicks out a connection an interrupted
        # previous run left open -- plain DROP DATABASE refuses otherwise.
        adm.execute(
            f"if db_id('{DB}') is not null begin "
            f"alter database {DB} set single_user with rollback immediate; "
            f"drop database {DB}; end")

    try:
        adm = _connect()
    except pyodbc.Error as exc:
        pytest.skip(f"no SQL Server at {HOST}:{PORT} ({exc.__class__.__name__})")
    _drop_if_exists(adm)
    adm.execute(f"create database {DB}")
    cx = _connect(DB)
    cx.execute("create table dbo.widgets (id int primary key, label nvarchar(50))")
    cx.execute("exec sys.sp_cdc_enable_db")
    cx.execute("exec sys.sp_cdc_enable_table @source_schema=N'dbo', "
              "@source_name=N'widgets', @role_name=null, "
              "@supports_net_changes=1")
    # The capture job needs a moment to pick the new instance up.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if cx.execute("select 1 from cdc.change_tables where "
                      "capture_instance = 'dbo_widgets'").fetchone():
            break
        time.sleep(0.5)
    else:
        pytest.skip("CDC capture instance never appeared -- is the Agent running?")
    try:
        yield cx
    finally:
        cx.close()
        _drop_if_exists(adm)
        adm.close()


def test_dropping_the_table_disables_cdc_and_we_detect_it(cdc_table):
    """demo/mssql-seed.sql drops and recreates its tables on every reseed,
    which silently disables CDC for them -- SQL Server does not error, and
    `sys.databases.is_cdc_enabled` stays 1 regardless (it is database-level).
    `capture_start_lsn` is how `core/sync_mssql.py` tells a healthy instance
    from a vanished one instead of handing pyodbc's opaque error upward."""
    from core.sync_mssql import capture_instance, capture_start_lsn

    instance = capture_instance("dbo", "widgets")
    assert capture_start_lsn(cdc_table, instance) is not None

    cdc_table.execute("drop table dbo.widgets")
    # cdc.change_tables does not clear the instant the source table is
    # dropped -- a fresh connection here (not the one that saw the table
    # exist) is what makes this poll actually observe the change rather than
    # a per-connection metadata cache.
    deadline = time.monotonic() + 15
    gone = None
    while time.monotonic() < deadline:
        with _connect(DB) as fresh:
            gone = capture_start_lsn(fresh, instance) is None
        if gone:
            break
        time.sleep(0.5)
    assert gone, "capture_start_lsn still saw the instance after the drop"

    # And this is the failure it stands in for: the change-reading function
    # itself is gone too, not just the catalog row.
    with pytest.raises(pyodbc.Error):
        cdc_table.execute(
            f"select top 1 * from cdc.fn_cdc_get_all_changes_{instance}"
            f"(?, ?, 'all')", b"\x00" * 10, b"\xff" * 10).fetchall()
