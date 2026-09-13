"""core/versions.py: a table keeps history because its contract says so.

No database. What is worth pinning here is the *declaration* -- which
contract qualifies and which columns are the ones that vary -- because
getting that wrong is silent: a UI that guessed would show the surrogate key
as "what changed" and nobody would notice it was wrong. See issue #27.
"""
from __future__ import annotations

import yaml

from core.runner import CONTRACTS
from core.versions import annotate, spec


def _contract(name: str) -> dict:
    return yaml.safe_load((CONTRACTS / name).read_text(encoding="utf-8"))


def test_the_type_2_contract_declares_its_key_and_its_attributes():
    got = spec(_contract("dwh_dim_customer.odcs.yaml"))

    assert got is not None
    assert got["key"] == "customer_id"
    assert got["table"] == "customer"
    # `customer_key` is the surrogate -- one per version -- and the interval
    # columns differ between two versions by definition. Neither is news.
    assert got["attributes"] == ["name", "country", "segment"]


def test_a_table_without_intervals_is_not_versioned():
    """Every other contract here describes a table that is rebuilt, not
    merged into. None of them should appear on the History tab."""
    assert spec(_contract("erp_customers.odcs.yaml")) is None
    assert spec(_contract("dwh_fct_orders.odcs.yaml")) is None


def test_the_interval_columns_alone_are_not_enough():
    """A contract can have the columns and still not say which column is the
    business key, and guessing it is exactly what this refuses to do."""
    doc = _contract("dwh_dim_customer.odcs.yaml")
    doc["customProperties"] = [cp for cp in doc["customProperties"]
                               if cp["property"] != "versionedBy"]
    assert spec(doc) is None


def test_the_first_version_changed_nothing():
    versions = annotate([
        {"segment": "SMB", "country": "TR"},
        {"segment": "MID", "country": "TR"},
        {"segment": "MID", "country": "DE"},
    ], ["segment", "country"])

    assert [v["changed"] for v in versions] == [[], ["segment"], ["country"]]
