"""contract_audit: what changed, when -- never who. See issue #12.

Runs against a real PostgreSQL when there is one, the same way
tests/test_medallion_scd2.py does: with no server reachable it skips, because
asserting on the DDL text would not prove the insert actually lands.

    docker compose -f compose.yaml -f compose.demo.yaml --profile demo up -d
    pytest tests/test_store.py
"""
from __future__ import annotations

import os

import pytest

psycopg = pytest.importorskip("psycopg")

HOST = os.getenv("DWH_HOST", "localhost")
PORT = int(os.getenv("DWH_PORT", "5432"))
SCRATCH = "dq_store_test"


@pytest.fixture()
def dq():
    """A throwaway database, initialised the way the app does on first use."""
    from core import store
    from core.bootstrap_db import admin_dsn, ensure_database
    try:
        with psycopg.connect(admin_dsn(HOST, PORT), connect_timeout=2,
                             autocommit=True) as cx:
            cx.execute(f'drop database if exists "{SCRATCH}" with (force)')
    except psycopg.OperationalError as exc:
        pytest.skip(f"no PostgreSQL at {HOST}:{PORT} ({exc.__class__.__name__})")
    ensure_database(HOST, PORT, SCRATCH)
    cx = store.connect(admin_dsn(HOST, PORT, SCRATCH))
    store.init(cx)
    try:
        yield cx
    finally:
        cx.close()
        with psycopg.connect(admin_dsn(HOST, PORT), autocommit=True) as adm:
            adm.execute(f'drop database if exists "{SCRATCH}" with (force)')


def rows(cx, contract_id="erp.customers"):
    return cx.execute(
        """select change_type, action, description, value, caller_label
             from contract_audit where contract_id = %s
            order by run_at""", (contract_id,)).fetchall()


def test_a_new_rule_is_logged_created(dq):
    from core import store
    store.write_audit(dq, "erp.customers", "quality_rule", "created",
                      "country must be TR, DE or US",
                      {"query": "select 1", "dimension": "conformity"})
    assert rows(dq) == [("quality_rule", "created",
                        "country must be TR, DE or US",
                        {"query": "select 1", "dimension": "conformity"}, None)]


def test_replacing_a_rule_is_logged_replaced_not_created(dq):
    """The caller decides created vs replaced -- see api/main.py's `existed`
    check -- write_audit only records what it is told."""
    from core import store
    store.write_audit(dq, "erp.customers", "sync_rule", "created",
                      "replica", {"server": "replica"})
    store.write_audit(dq, "erp.customers", "sync_rule", "replaced",
                      "replica", {"server": "replica", "filter": "country = 'TR'"})
    actions = [a for _, a, *_ in rows(dq)]
    assert actions == ["created", "replaced"]


def test_caller_label_is_free_text_and_optional(dq):
    """Never a verified identity -- see ADR 0010 and the column's own comment
    in core/store.py. Absent by default, carried through unmodified when given."""
    from core import store
    store.write_audit(dq, "erp.customers", "quality_rule", "created",
                      "no label", {"query": "select 1"})
    store.write_audit(dq, "erp.customers", "quality_rule", "created",
                      "with label", {"query": "select 1"},
                      caller_label="someone testing locally")
    labels = {desc: label for _, _, desc, _, label in rows(dq)}
    assert labels["no label"] is None
    assert labels["with label"] == "someone testing locally"


def test_audit_rows_are_scoped_to_their_own_contract(dq):
    from core import store
    store.write_audit(dq, "erp.customers", "quality_rule", "created",
                      "a", {"query": "select 1"})
    store.write_audit(dq, "dwh.fct_orders", "quality_rule", "created",
                      "b", {"query": "select 1"})
    assert len(rows(dq, "erp.customers")) == 1
    assert len(rows(dq, "dwh.fct_orders")) == 1
