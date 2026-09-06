"""Reading SQL Server's change table in the right order.

Everything that talks to a database is left to the integration job; what is
pinned here is the ordering, which is the part that was wrong twice. Both
mistakes produced a target that looked populated and was not: once a cancelled
order that stayed, once a renamed order that existed under both names.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from core.sync_mssql import (DELETE, INSERT, UPDATE_AFTER,  # noqa: E402
                             UPDATE_BEFORE, capture_instance, plan_changes)

COLUMNS = ["order_id", "status", "net_amount"]
IDENTITY = ["order_id"]
CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"


def _plan(rows):
    return plan_changes(rows, COLUMNS, IDENTITY)


def test_an_insert_is_an_upsert():
    assert _plan([(INSERT, 1, "OPEN", 10)]) == [("apply", [1, "OPEN", 10])]


def test_a_delete_carries_only_the_key():
    assert _plan([(DELETE, 1, "OPEN", 10)]) == [("delete", [1])]


def test_the_before_image_is_not_applied_on_its_own():
    """Operation 3 is context for the row after it, never a change itself."""
    assert _plan([(UPDATE_BEFORE, 1, "OPEN", 10),
                  (UPDATE_AFTER, 1, "OPEN", 20)]) == \
        [("apply", [1, "OPEN", 20])]


def test_an_update_that_changes_the_key_deletes_the_old_row():
    """Otherwise one order exists twice: under its old id and its new one."""
    assert _plan([(UPDATE_BEFORE, 1, "OPEN", 10),
                  (UPDATE_AFTER, 2, "OPEN", 10)]) == \
        [("delete", [1]), ("apply", [2, "OPEN", 10])]


def test_a_before_image_does_not_leak_into_the_next_change():
    """The stash has to be cleared, or an unrelated insert two rows later gets
    a spurious delete in front of it."""
    assert _plan([(UPDATE_BEFORE, 1, "OPEN", 10),
                  (UPDATE_AFTER, 1, "SHIPPED", 10),
                  (INSERT, 9, "OPEN", 5)]) == \
        [("apply", [1, "SHIPPED", 10]), ("apply", [9, "OPEN", 5])]


def test_order_is_preserved():
    """CDC is ordered by LSN and applying it out of order is a lost update."""
    assert [w for w, _ in _plan([(INSERT, 1, "OPEN", 10),
                                 (UPDATE_BEFORE, 1, "OPEN", 10),
                                 (UPDATE_AFTER, 1, "SHIPPED", 10),
                                 (DELETE, 1, "SHIPPED", 10)])] == \
        ["apply", "apply", "delete"]


def test_the_capture_instance_is_sql_servers_own_naming():
    assert capture_instance("dbo", "sales_orders") == "dbo_sales_orders"


def test_the_sql_server_rule_keeps_the_identity_at_the_key():
    """The Postgres rule widens the identity to make its filter expressible;
    doing the same here broke it, because cancelling an order changed its key
    and the delete then looked for a row that no longer had that name."""
    from core.sync import identity_columns, sync_rule
    doc = yaml.safe_load(
        (CONTRACTS / "erp_mssql.odcs.yaml").read_text(encoding="utf-8"))
    rule = sync_rule(doc)
    assert identity_columns(doc["schema"][0], rule) == ["order_id"]
    assert "status" in rule["filter"] and "status" not in identity_columns(
        doc["schema"][0], rule)
