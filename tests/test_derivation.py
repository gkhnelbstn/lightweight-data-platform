"""Contract -> checks -> SQL. No database required."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.checks import derive
from core.compilers.dbt import compile_dbt
from core.compilers.gx import compile_gx
from core.compilers.sql import compile_sql, render_scope, scope_predicate
from core.contract import DataContract, load_all

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"


@pytest.fixture(scope="module")
def orders() -> DataContract:
    return DataContract.load(CONTRACTS / "sales_orders.contract.yaml")


def test_every_contract_loads():
    contracts = load_all(CONTRACTS)
    assert contracts, "no contracts found"
    assert all(c.id and c.info.owner for c in contracts)


def test_schema_fields_derive_their_checks(orders):
    kinds = {c.kind for c in derive(orders)}
    assert {"not_null", "unique", "accepted_values", "range",
            "relationship"} <= kinds


def test_check_ids_are_unique_across_all_contracts():
    """Two columns can carry the same rule; the ids must still differ, or
    downstream systems keyed by id silently merge them."""
    ids = [c.id for con in load_all(CONTRACTS) for c in derive(con)]
    assert len(ids) == len(set(ids))


def test_quality_rules_keep_their_severity(orders):
    by_id = {c.id: c for c in derive(orders)}
    freshness = by_id["erp.sales_orders.freshness_daily"]
    assert freshness.severity == "critical"
    assert freshness.origin == "quality"


@pytest.mark.parametrize("window,op", [("incremental", "="), ("cumulative", "<=")])
def test_scope_predicate_follows_the_window(window, op):
    assert scope_predicate("loaded_at", window) == f"loaded_at {op} %(as_of)s"
    assert scope_predicate("loaded_at", window, "o") == f"o.loaded_at {op} %(as_of)s"


def test_render_scope_expands_both_token_forms():
    sql = "where {{scope}} and x join y on {{scope:o}}"
    out = render_scope(sql, "loaded_at", "incremental")
    assert "{{" not in out
    assert "loaded_at = %(as_of)s" in out and "o.loaded_at = %(as_of)s" in out


def test_every_check_compiles_to_two_column_sql(orders):
    for check in derive(orders):
        sql = compile_sql(check, orders)
        assert "failed_rows" in sql and "total_rows" in sql
        assert "%(as_of)s" in sql


def test_freshness_ignores_the_incremental_window(orders):
    """Freshness asks 'is the table current', which a one-day slice cannot answer."""
    check = next(c for c in derive(orders) if c.kind == "freshness")
    assert "loaded_at <= %(as_of)s" in compile_sql(check, orders, "incremental")


def test_artifacts_are_generated_for_other_engines(orders):
    import json
    import yaml
    checks = derive(orders)
    suite = json.loads(compile_gx(orders, checks))
    assert suite["expectations"]
    assert all(e["meta"]["severity"] for e in suite["expectations"])
    model = yaml.safe_load(compile_dbt(orders, checks))["models"][0]
    assert model["name"] == orders.server.table
    assert any(c["tests"] for c in model["columns"])
