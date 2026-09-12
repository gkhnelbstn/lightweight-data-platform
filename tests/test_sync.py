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

def test_the_logical_replication_rules_do_not_apply_to_a_cdc_source():
    """Reported as a problem on the SQL Server contract until the engine was
    passed in -- and it is not one: the CDC reader has the whole row."""
    rule = {"filter": "country = 'TR'", "columns": ["customer_id", "name"]}
    assert problems(_model(), rule)                      # postgres: two of them
    assert problems(_model(), rule, "sqlserver") == []


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


# --- the target nothing else creates ----------------------------------------

def test_the_target_table_comes_from_the_contract():
    """Logical replication replicates into a table that must already exist,
    and the CDC reader upserts into one. It was created by hand while this was
    written, so a clean install of the whole stack failed at the first sync."""
    from core.sync import target_table_statement
    stmt = target_table_statement(
        _model(), "public",
        {"columns": ["customer_id", "name", "country"],
         "identity": ["customer_id", "country"]}, "postgres").as_string()
    assert 'create table if not exists "public"."customers"' in stmt
    assert '"customer_id"' in stmt and '"country"' in stmt


def test_only_the_replicated_columns_exist_in_the_target():
    """The privacy boundary is physical, not a filter: a classified column left
    out of the rule has no column in the replica to leak from."""
    from core.sync import target_table_statement
    stmt = target_table_statement(
        _model(), "public", {"columns": ["customer_id", "name"]},
        "postgres").as_string()
    assert "tax_id" not in stmt


def test_identity_columns_are_not_null_in_the_target():
    """A replica identity index has to be over NOT NULL columns."""
    from core.sync import target_table_statement
    stmt = target_table_statement(
        _model(), "public",
        {"columns": ["customer_id", "name"]}, "postgres").as_string()
    assert '"customer_id" text not null' in stmt or "not null" in stmt


def test_sql_server_types_are_translated():
    """The contract states physical types in the *source's* dialect, so
    replicating SQL Server into Postgres needs a translation -- `int` and
    `decimal` are not Postgres spellings."""
    from core.sync import target_table_statement
    model = {"name": "sales_orders", "physicalName": "sales_orders",
             "properties": [
                 {"name": "order_id", "physicalType": "bigint", "primaryKey": True},
                 {"name": "customer_id", "physicalType": "int"},
                 {"name": "net_amount", "physicalType": "decimal"},
                 {"name": "currency", "physicalType": "char"}]}
    stmt = target_table_statement(model, "public", {}, "sqlserver").as_string()
    assert "integer" in stmt and "numeric" in stmt
    assert " int," not in stmt and "decimal" not in stmt


# --- authoring a rule that does not exist on disk yet -----------------------
# The write path issue #10 asks for: the same four preconditions, run against
# a proposed rule rather than one already saved. `unsound_identity` needs a
# reachable store, which the accepted cases stub out -- the point of these
# tests is author_rule wiring problems() and unsound_identity() together and
# refusing to build a rule that names a server the contract does not have,
# not re-proving what test_sync.py already covers for each of those two.

def _contract(engine="postgres", **kw):
    # Not "erp.customers": that id is a real contract, and unsound_identity
    # queries check_results by contract id -- a hermetic test cannot share it,
    # or it would see whatever the store happens to hold whenever one is
    # actually reachable.
    base = {"id": "test.customers", "schema": [_model()],
           "servers": [{"server": "erp", "type": engine},
                       {"server": "replica", "type": "postgres"}]}
    base.update(kw)
    return base


def test_authoring_a_rule_for_a_server_the_contract_does_not_have_is_refused():
    from core.sync import author_rule
    rule, bad = author_rule(_contract(), "nowhere")
    assert rule == {}
    assert "nowhere" in bad[0] and "servers[]" in bad[0]


def test_authoring_reuses_problems_for_a_key_the_contract_lacks(monkeypatch):
    from core import sync
    monkeypatch.setattr(sync, "unsound_identity", lambda *a: [])
    keyless = _contract()
    keyless["schema"][0] = _model(properties=[{"name": "a"}, {"name": "b"}])
    rule, bad = sync.author_rule(keyless, "replica")
    assert any("primaryKey" in p for p in bad)


def test_authoring_reuses_problems_for_a_filter_outside_the_identity(monkeypatch):
    from core import sync
    monkeypatch.setattr(sync, "unsound_identity", lambda *a: [])
    rule, bad = sync.author_rule(_contract(), "replica", row_filter="country = 'TR'")
    assert any("replica identity" in p for p in bad)


def test_authoring_reuses_problems_for_a_column_list_missing_the_identity(monkeypatch):
    from core import sync
    monkeypatch.setattr(sync, "unsound_identity", lambda *a: [])
    rule, bad = sync.author_rule(_contract(), "replica", columns=["name"])
    assert any("column list omits" in p for p in bad)


def test_authoring_asks_whether_the_identity_has_actually_held(monkeypatch):
    """A rule with no other problem is still refused if the identity has been
    seen failing -- author_rule has to reach unsound_identity too, not stop at
    problems(). Stubbed rather than pointed at a real store: whether *that*
    check itself is right is test_sync.py's existing coverage, not this one's."""
    from core import sync
    monkeypatch.setattr(sync, "unsound_identity",
                        lambda *a: ["customer_id: the uniqueness check is failing"])
    rule, bad = sync.author_rule(_contract(), "replica")
    assert any("uniqueness check is failing" in p for p in bad)


def test_a_sound_postgres_rule_is_accepted(monkeypatch):
    from core import sync
    monkeypatch.setattr(sync, "unsound_identity", lambda *a: [])
    rule, bad = sync.author_rule(_contract("postgres"), "replica",
                                 columns=["customer_id", "name", "country"])
    assert bad == []
    assert rule == {"server": "replica",
                    "columns": ["customer_id", "name", "country"]}


def test_a_sound_sqlserver_rule_is_accepted(monkeypatch):
    """Rules 2 and 3 are logical replication's; a filter column outside the
    identity is not a problem for a CDC source -- see ADR 0008."""
    from core import sync
    monkeypatch.setattr(sync, "unsound_identity", lambda *a: [])
    rule, bad = sync.author_rule(_contract("sqlserver"), "replica",
                                 row_filter="country = 'TR'")
    assert bad == []
    assert rule == {"server": "replica", "filter": "country = 'TR'"}
