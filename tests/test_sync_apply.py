"""core/sync.py --apply, against a real PostgreSQL with logical replication.

Skips without one -- see tests/test_medallion_scd2.py for the pattern. This
specifically needs `wal_level=logical`, which only the docker compose `db`
service sets (see compose.yaml); a bare local Postgres for `pip install
-e ".[dev]"` + `pytest -q` will not have it, and this test skips there too.

    docker compose up -d db
    pytest tests/test_sync_apply.py
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

import pytest

psycopg = pytest.importorskip("psycopg")

from core.store import ERP_DSN  # noqa: E402

_u = urlparse(ERP_DSN)
HOST = _u.hostname or "localhost"
PORT = _u.port or 5432
SOURCE_DB = "sync_apply_test_source"
TARGET_DB = "sync_apply_test_target"


def _contract() -> dict:
    return {
        "id": "test.sync_apply",
        "customProperties": [
            {"property": "syncTo", "value": {"server": "target"}}],
        "schema": [{
            "name": "widgets", "physicalName": "widgets",
            "properties": [{"name": "id", "primaryKey": True},
                           {"name": "label"}],
        }],
        "servers": [
            {"server": "erp", "type": "postgres", "host": HOST, "port": PORT,
             "database": SOURCE_DB, "schema": "public"},
            {"server": "target", "type": "postgres", "host": HOST,
             "port": PORT, "database": TARGET_DB, "schema": "public"},
        ],
    }


def _teardown(adm) -> None:
    """Drop both scratch databases, in the order that actually finishes.

    WITH (FORCE) terminates ordinary backends, but Postgres refuses outright
    to drop a database that owns an enabled subscription, force or not --
    `pg_subscription` is a *shared* catalog, so the subscription has to be
    dropped from inside the target database itself. That, in turn, drops the
    slot it was using on the source, which is what let the source's walsender
    disconnect and the source database become droppable too.
    """
    from core.bootstrap_db import admin_dsn

    if adm.execute("select 1 from pg_database where datname = %s",
                   (TARGET_DB,)).fetchone():
        with psycopg.connect(admin_dsn(HOST, PORT, TARGET_DB),
                             autocommit=True) as t:
            for (sub,) in t.execute(
                    "select s.subname from pg_subscription s "
                    "join pg_database d on d.oid = s.subdbid "
                    "where d.datname = current_database()").fetchall():
                # A run left mid-regression (issue #16 itself) can leave a
                # subscription pointing at a slot that is already gone;
                # DROP SUBSCRIPTION then tries to drop it too and fails.
                # Detach first so this cleans up either state.
                try:
                    t.execute(f'alter subscription "{sub}" disable')
                    t.execute(f'alter subscription "{sub}" '
                             f'set (slot_name = none)')
                except psycopg.Error:
                    pass  # autocommit: the failed statement did not stick
                t.execute(f'drop subscription "{sub}"')
    adm.execute(f'drop database if exists "{TARGET_DB}" with (force)')
    adm.execute(f'drop database if exists "{SOURCE_DB}" with (force)')


@pytest.fixture()
def scratch():
    """Two throwaway databases on the same server -- apply() replicates
    between them the same way it would between a contract's real erp and
    replica servers."""
    from core.bootstrap_db import admin_dsn, ensure_database
    try:
        with psycopg.connect(admin_dsn(HOST, PORT), connect_timeout=2,
                             autocommit=True) as cx:
            _teardown(cx)  # a slot left by an interrupted previous run
    except psycopg.OperationalError as exc:
        pytest.skip(f"no PostgreSQL at {HOST}:{PORT} ({exc.__class__.__name__})")
    ensure_database(HOST, PORT, SOURCE_DB)
    ensure_database(HOST, PORT, TARGET_DB)
    src = psycopg.connect(admin_dsn(HOST, PORT, SOURCE_DB), autocommit=True)
    src.execute("create table widgets (id bigint, label text)")
    src.execute("insert into widgets values (1, 'a')")
    try:
        yield src
    finally:
        src.close()
        with psycopg.connect(admin_dsn(HOST, PORT), autocommit=True) as adm:
            _teardown(adm)


def _wait_until(predicate, timeout=10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.5)
    return False


def test_apply_twice_stays_applied(scratch):
    """Issue #16: re-applying an already-correct rule used to drop the
    source's replication slot -- `DROP SUBSCRIPTION`'s default behaviour --
    leaving `--status` reporting a dead worker with nothing to say why. The
    bug needed nothing to have changed: this contract, applied twice in a
    row, is exactly the case."""
    from core import sync

    contract = _contract()

    sync.apply(contract)
    up = _wait_until(lambda: (sync.status(contract) or {}).get("worker_running"))
    assert up, "first apply never came up"

    sync.apply(contract)  # the regression: this used to break a working pair
    up = _wait_until(lambda: (sync.status(contract) or {}).get("worker_running"))
    assert up, "re-applying an unchanged rule left the subscription dead"

    status = sync.status(contract)
    assert status["slot_active"] is True
    assert status["worker_running"] is True
