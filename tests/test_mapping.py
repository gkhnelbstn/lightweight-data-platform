"""The refusals for a mapping between two schemas.

Every one of these fails silently otherwise: a column nothing fills, a
classified value that crosses, a key that cannot match a row. None raise on
their own, which is the reason they are checked before anything moves --
the same argument `tests/test_sync.py` makes for a replica rule.
"""
from __future__ import annotations

from core.mapping import Mapping, declared, filled_here, problems


def _source(**kw) -> dict:
    base = {"id": "erp.customers", "schema": [{"name": "customers", "properties": [
        {"name": "customer_id", "primaryKey": True, "required": True},
        {"name": "name", "required": True},
        {"name": "country"},
        {"name": "tax_id", "classification": "pii"}]}]}
    base.update(kw)
    return base


def _target(columns: dict | None = None, properties=None, **kw) -> dict:
    entry: dict | str = ({"contract": "erp.customers", "columns": columns}
                         if columns is not None else "erp.customers")
    base = {
        "id": "dwh.customer",
        "customProperties": [{"property": "derivedFrom", "value": [entry]}],
        "schema": [{"name": "customer", "properties": properties or [
            {"name": "customer_key", "primaryKey": True, "required": True},
            {"name": "display_name", "required": True},
            {"name": "country_code"}]}]}
    base.update(kw)
    return base


BY_ID = {"erp.customers": _source()}
GOOD = {"customer_key": "customer_id", "display_name": "name",
        "country_code": "country"}


# --- the vocabulary -------------------------------------------------------

def test_a_bare_reference_still_means_what_it_meant():
    """Every contract on disk wrote `derivedFrom: [id]`. Adding column detail
    must not start refusing them."""
    assert declared(_target()) == [Mapping("erp.customers")]
    assert problems(_target(), BY_ID) == []


def test_a_detailed_entry_carries_its_columns():
    assert declared(_target(GOOD)) == [Mapping("erp.customers", GOOD)]


def test_both_forms_coexist_in_one_list():
    contract = _target(GOOD)
    contract["customProperties"][0]["value"].append("dwh.stg_orders")
    assert [m.reference for m in declared(contract)] == [
        "erp.customers", "dwh.stg_orders"]


# --- the refusals ---------------------------------------------------------

def test_an_unresolvable_reference_is_reported_not_dropped():
    """ADR 0014: a graph missing an edge still looks complete."""
    found = problems(_target(GOOD), {})
    assert any("not a contract this platform loads" in p for p in found)


def test_a_source_column_the_upstream_does_not_declare():
    found = problems(_target({**GOOD, "country_code": "nope"}), BY_ID)
    assert any("which does not declare it" in p for p in found)


def test_a_target_column_this_contract_does_not_declare():
    found = problems(_target({**GOOD, "nope": "country"}), BY_ID)
    assert any("does not declare" in p and "nope" in p for p in found)


def test_a_required_column_no_mapping_fills():
    """The contract promises a column and nothing puts a value in it."""
    found = problems(_target({"customer_key": "customer_id"}), BY_ID)
    assert any("promises a column" in p for p in found)
    assert any("display_name" in p for p in found)


def test_a_primary_key_no_mapping_fills():
    """Without it a row arriving twice cannot be matched -- not idempotent."""
    found = problems(_target({"display_name": "name"}), BY_ID)
    assert any("cannot be matched" in p for p in found)


def test_one_source_column_may_fill_two_target_columns():
    """Denormalising is the point of a mapping, not a mistake."""
    assert problems(_target({**GOOD, "country_code": "name"}), BY_ID) == []


def test_two_upstreams_may_not_fill_one_column():
    """A dict cannot express this -- it takes two derivedFrom entries, which
    is the real shape: two upstreams whose column maps overlap."""
    contract = _target(GOOD)
    contract["customProperties"][0]["value"].append(
        {"contract": "erp.orders", "columns": {"country_code": "ship_country"}})
    by_id = {**BY_ID, "erp.orders": {"id": "erp.orders", "schema": [
        {"name": "orders", "properties": [{"name": "ship_country"}]}]}}
    found = problems(contract, by_id)
    assert any("filled twice" in p for p in found)


def test_a_classified_column_may_not_lose_its_classification():
    """A syncTo column list is a privacy boundary because a column left out has
    no column in the target. A mapping names the target column, so there is no
    such physics -- the boundary has to be checked instead."""
    found = problems(_target({**GOOD, "country_code": "tax_id"}), BY_ID)
    assert any("may not lose its classification" in p for p in found)


def test_the_same_classification_on_both_sides_is_fine():
    target = _target({**GOOD, "country_code": "tax_id"}, properties=[
        {"name": "customer_key", "primaryKey": True, "required": True},
        {"name": "display_name", "required": True},
        {"name": "country_code", "classification": "pii"}])
    assert problems(target, BY_ID) == []


def test_a_generated_column_is_not_a_gap():
    """It has no source by construction -- issue #45, PR #48."""
    target = _target(GOOD, properties=[
        {"name": "customer_key", "primaryKey": True, "required": True},
        {"name": "display_name", "required": True},
        {"name": "country_code"},
        {"name": "replica_row_id", "required": True}])
    target["customProperties"].append(
        {"property": "syncTo",
         "value": {"server": "replica",
                   "generated": {"replica_row_id": "bigint generated always as identity"}}})
    assert problems(target, BY_ID) == []


def test_a_good_mapping_is_silent():
    assert problems(_target(GOOD), BY_ID) == []


def test_computed_columns_are_not_gaps():
    """A surrogate key and SCD Type 2 bookkeeping exist in no source row."""
    target = _target({"display_name": "name"}, properties=[
        {"name": "customer_key", "primaryKey": True, "required": True},
        {"name": "display_name", "required": True}])
    target["customProperties"].append(
        {"property": "computedHere", "value": ["customer_key"]})
    assert filled_here(target) == {"customer_key"}
    assert problems(target, BY_ID) == []


def test_both_vocabularies_count_as_filled():
    """`syncTo.generated` is the database filling a column; `computedHere` is
    our own process. Different things, one question."""
    target = _target(GOOD)
    target["customProperties"] += [
        {"property": "computedHere", "value": ["a"]},
        {"property": "syncTo", "value": {"generated": {"b": "bigint"}}}]
    assert filled_here(target) == {"a", "b"}


def test_the_shipped_dim_customer_mapping_holds():
    """dim.customer is the real case: a different schema, its own key, and
    tax_id deliberately not crossing at all."""
    from pathlib import Path

    import yaml
    root = Path(__file__).resolve().parents[1] / "contracts"
    by_id = {(d := yaml.safe_load(p.read_text(encoding="utf-8")))["id"]: d
             for p in sorted(root.glob("*.odcs.yaml"))}
    dim = by_id["dwh.dim_customer"]
    mapped = [m for m in declared(dim) if m.detailed]
    assert mapped, "dim.customer should declare column detail"
    assert "tax_id" not in mapped[0].columns.values()
    assert problems(dim, by_id) == []


def test_the_shipped_mapping_refuses_a_classified_column():
    """The same contract, with tax_id pulled across -- the one mistake the
    column list in a syncTo rule makes physically impossible and a mapping
    does not."""
    import copy
    from pathlib import Path

    import yaml
    root = Path(__file__).resolve().parents[1] / "contracts"
    by_id = {(d := yaml.safe_load(p.read_text(encoding="utf-8")))["id"]: d
             for p in sorted(root.glob("*.odcs.yaml"))}
    dim = copy.deepcopy(by_id["dwh.dim_customer"])
    for prop in dim["customProperties"]:
        if prop["property"] == "derivedFrom":
            prop["value"][0]["columns"]["name"] = "tax_id"
    found = problems(dim, {**by_id, "dwh.dim_customer": dim})
    assert any("may not lose its classification" in p for p in found)


def test_every_shipped_contract_still_passes():
    """None of them declare a column map yet, so none may start failing."""
    from pathlib import Path

    import yaml
    root = Path(__file__).resolve().parents[1] / "contracts"
    docs = [yaml.safe_load(p.read_text(encoding="utf-8"))
            for p in sorted(root.glob("*.odcs.yaml"))]
    by_id = {d["id"]: d for d in docs}
    for doc in docs:
        assert problems(doc, by_id) == [], doc["id"]
