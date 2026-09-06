"""The rules that decide whether a table can be replicated at all.

Postgres enforces every one of these, but three of them only at the moment a
row changes -- long after the objects were created and the initial copy made
it look like it worked. So they are checked here, before anything is created,
and each test names the error it is standing in for.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytest.importorskip("sqlglot")
from core.sync import (filter_columns, identity_columns,  # noqa: E402
                       problems, publication_name, publication_statement,
                       sync_rule)

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"


def _model(**kw) -> dict:
    base = {"name": "customers", "physicalName": "customers", "properties": [
        {"name": "customer_id", "primaryKey": True},
        {"name": "name"}, {"name": "country"}, {"name": "tax_id"},
        {"name": "segment"}]}
    base.update(kw)
    return base


# --- what identifies a row --------------------------------------------------

def test_the_identity_is_the_contracts_primary_key():
    assert identity_columns(_model()) == ["customer_id"]


def test_a_rule_may_widen_the_identity():
    """Rule 2 leaves no other way to filter on a non-key column."""
    rule = {"identity": ["customer_id", "country"]}
    assert identity_columns(_model(), rule) == ["customer_id", "country"]


def test_a_rule_may_not_narrow_it():
    """An identity without the key does not identify a row, so it is ignored
    and reported rather than quietly used."""
    rule = {"identity": ["country"]}
    assert identity_columns(_model(), rule) == ["customer_id"]
    assert any("primary key" in p for p in problems(_model(), rule))


def test_a_table_with_no_key_cannot_be_replicated():
    model = _model(properties=[{"name": "a"}, {"name": "b"}])
    assert problems(model, {"server": "replica"})
    assert "primaryKey" in problems(model, {"server": "replica"})[0]


# --- the three Postgres only raises when a row changes ----------------------

def test_a_filter_column_outside_the_identity_is_refused():
    """ERROR: Column used in the publication WHERE expression is not part of
    the replica identity."""
    found = problems(_model(), {"filter": "country = 'TR'"})
    assert any("country" in p and "replica identity" in p for p in found)


def test_the_same_filter_is_fine_once_the_identity_covers_it():
    assert problems(_model(), {"filter": "country = 'TR'",
                               "identity": ["customer_id", "country"],
                               "columns": ["customer_id", "country"]}) == []


def test_a_column_list_must_cover_the_identity():
    """ERROR: Column list used by the publication does not cover the replica
    identity."""
    found = problems(_model(), {"columns": ["name", "segment"]})
    assert any("column list omits" in p for p in found)


def test_a_column_the_contract_does_not_declare_is_refused():
    found = problems(_model(), {"columns": ["customer_id", "nope"]})
    assert any("nope" in p for p in found)


def test_filter_columns_are_read_from_the_sql_not_guessed():
    assert filter_columns("country = 'TR' and segment <> 'ENT'") == \
        {"country", "segment"}
    assert filter_columns("") == set()


# --- the statement ----------------------------------------------------------

def test_the_publication_carries_the_filter_and_the_column_list():
    stmt = publication_statement(
        _model(), "public",
        {"filter": "country = 'TR'",
         "columns": ["customer_id", "name", "country", "segment"]},
        "sync_erp_customers").as_string()
    assert '"customer_id", "name", "country", "segment"' in stmt
    assert "country = 'TR'" in stmt
    # the whole point of the column list: the classified column never leaves
    assert "tax_id" not in stmt


def test_the_publication_name_survives_a_dotted_contract_id():
    assert publication_name({"id": "erp.customers"}) == "sync_erp_customers"


# --- the contract in the repository ----------------------------------------

def test_the_shipped_rule_would_actually_work():
    doc = yaml.safe_load(
        (CONTRACTS / "erp_customers.odcs.yaml").read_text(encoding="utf-8"))
    rule = sync_rule(doc)
    assert rule, "the customers contract should carry a syncTo rule"
    assert problems(doc["schema"][0], rule) == []
    assert {s["server"] for s in doc["servers"]} >= {rule["server"]}


def test_a_classified_column_is_not_replicated():
    """Belt and braces with core/sample.py: masking it in the UI is no use if
    the whole column was copied into another database."""
    from core.sample import classified
    doc = yaml.safe_load(
        (CONTRACTS / "erp_customers.odcs.yaml").read_text(encoding="utf-8"))
    rule = sync_rule(doc)
    assert not (classified(doc) & set(rule.get("columns") or []))
