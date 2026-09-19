"""Integration flows, one file per direction, and the pair two of them make.

The pair is a stand-in for any two systems neither of which is ours -- an
ERP and an accounting package, a CRM and a billing system (issue #53). It is
built to have the problems every such pair has: different column names, a
classified identifier on both sides, a column coded `'Y'/'N'` on one side and
as a bit on the other, and both sides editing the same customer. The table
contracts know nothing about the integration; the flows carry all of it
(ADR 0019). Nothing lives in `contracts/` because nothing serves this pair.
"""
from __future__ import annotations

import copy

from core import flows


def _table(cid, name, columns):
    return {"id": cid, "schema": [{"name": name, "properties": columns}]}


CRM = _table("crm.account", "ACCOUNT", [
    {"name": "ACCOUNT_CODE", "primaryKey": True, "required": True},
    {"name": "TITLE", "required": True},
    {"name": "TAX_NO", "classification": "pii"},
    {"name": "ACTIVE"},
    {"name": "BALANCE"}])

BILLING = _table("billing.customer", "Customer", [
    {"name": "CustomerCode", "primaryKey": True, "required": True},
    {"name": "Name", "required": True},
    {"name": "TaxId", "classification": "pii"},
    {"name": "IsActive"},
    {"name": "Balance"},
    {"name": "CreatedAt", "required": True}])

BY_ID = {c["id"]: c for c in (CRM, BILLING)}

TO_BILLING = {
    "id": "crm_to_billing", "from": "crm.account", "to": "billing.customer",
    "columns": {"CustomerCode": "ACCOUNT_CODE", "Name": "TITLE", "TaxId": "TAX_NO",
                "IsActive": "ACTIVE", "Balance": "BALANCE"},
    "values": {"IsActive": {"Y": True, "N": False}},
    "winsOnConflict": ["CustomerCode", "Name", "TaxId", "IsActive"],
    "filledByTarget": ["CreatedAt"]}

TO_CRM = {
    "id": "billing_to_crm", "from": "billing.customer", "to": "crm.account",
    "columns": {"ACCOUNT_CODE": "CustomerCode", "TITLE": "Name", "TAX_NO": "TaxId",
                "ACTIVE": "IsActive", "BALANCE": "Balance"},
    "values": {"ACTIVE": {True: "Y", False: "N"}},
    "winsOnConflict": ["BALANCE"]}


def _check(*docs, by_id=BY_ID) -> list[str]:
    return flows.problems([flows.parse(d) for d in docs], by_id)


def _with(doc, **changes):
    out = copy.deepcopy(doc)
    out.update(changes)
    return out


# --- the vocabulary --------------------------------------------------------

def test_a_flow_file_reads_into_a_mapping():
    flow = flows.parse(TO_BILLING)
    assert (flow.mapping.reference, flow.target) == ("crm.account", "billing.customer")
    assert flow.mapping.values == {"IsActive": {"Y": True, "N": False}}
    assert flow.wins == {"CustomerCode", "Name", "TaxId", "IsActive"}


def test_the_table_contracts_say_nothing_about_the_integration():
    """The point of ADR 0019: a vendor's contract describes its table only."""
    for table in (CRM, BILLING):
        assert "customProperties" not in table


def test_no_flows_directory_means_no_flows(tmp_path):
    assert flows.load(tmp_path / "missing") == []


def test_a_flow_file_on_disk(tmp_path):
    (tmp_path / "crm_to_billing.yaml").write_text(
        "id: crm_to_billing\nfrom: crm.account\nto: billing.customer\n"
        "columns: {Name: TITLE}\nvalues: {IsActive: {Y: true, N: false}}\n",
        encoding="utf-8")
    [flow] = flows.load(tmp_path)
    assert flow.mapping.columns == {"Name": "TITLE"}
    assert flow.mapping.values == {"IsActive": {"Y": True, "N": False}}


# --- one direction ----------------------------------------------------------

def test_a_sound_pair_is_silent():
    assert _check(TO_BILLING, TO_CRM) == []


def test_an_unresolvable_table_is_reported():
    assert _check(_with(TO_BILLING, to="billing.missing")) == [
        "crm_to_billing: names 'billing.missing', which is not a contract this "
        "platform loads"]


def test_the_one_way_refusals_are_mappings():
    """Shared with derivedFrom: a column the target does not declare."""
    doc = copy.deepcopy(TO_BILLING)
    doc["columns"]["Names"] = "TITLE"
    assert any("fills 'Names', which this contract does not declare" in p
               for p in _check(doc))


def test_a_classified_value_may_not_land_unclassified():
    billing = copy.deepcopy(BILLING)
    billing["schema"][0]["properties"][2].pop("classification")
    found = _check(TO_BILLING, by_id={**BY_ID, "billing.customer": billing})
    assert any("may not lose its classification" in p for p in found)


def test_a_required_target_column_nothing_fills():
    found = _check(_with(TO_BILLING, filledByTarget=[]))
    assert found == ["crm_to_billing: CreatedAt is required here and no "
                     "mapping fills it; the contract promises a column "
                     "nothing puts a value in"]


def test_two_flows_into_one_column():
    other = {"id": "erp_to_billing", "from": "crm.account", "to": "billing.customer",
             "columns": {"Name": "TITLE"}}
    assert any("'Name' is filled twice" in p for p in _check(TO_BILLING, other))


# --- the pair ----------------------------------------------------------------

def test_a_column_nothing_maps_back():
    doc = copy.deepcopy(TO_CRM)
    del doc["columns"]["BALANCE"]
    found = _check(TO_BILLING, doc)
    assert ("crm_to_billing: 'Balance' is filled from 'BALANCE' but "
            "billing_to_crm does not map it back, so an edit made at "
            "billing.customer is overwritten by the next change at crm.account"
            ) in found


def test_a_round_trip_into_another_column():
    doc = copy.deepcopy(TO_CRM)
    doc["columns"]["BALANCE"] = "Name"
    found = _check(TO_BILLING, doc)
    assert ("crm_to_billing: 'Balance' is filled from 'BALANCE', but "
            "billing_to_crm fills 'BALANCE' from 'Name'; the round trip moves "
            "the value into another column") in found


def test_a_column_neither_flow_wins():
    found = _check(_with(TO_BILLING, winsOnConflict=["CustomerCode", "Name", "IsActive"]),
                   TO_CRM)
    assert found == ["billing_to_crm: 'TAX_NO' <-> 'TaxId' is edited on both "
                     "sides and neither flow wins it, so a conflict is "
                     "settled by timing"]


def test_a_column_both_flows_win():
    found = _check(TO_BILLING, _with(TO_CRM, winsOnConflict=["BALANCE", "TITLE"]))
    assert any("'TITLE' <-> 'Name' is won by both" in p for p in found)


def test_a_pair_problem_is_reported_once():
    """A fact about the pair, so `--check` must not count it twice."""
    assert len(_check(_with(TO_BILLING, winsOnConflict=["CustomerCode", "Name",
                                                      "IsActive"]), TO_CRM)) == 1


def test_a_value_map_on_one_side_only():
    found = _check(TO_BILLING, _with(TO_CRM, values={}))
    assert found == ["billing_to_crm: 'ACTIVE' <-> 'IsActive' translates values "
                     "in one direction only (billing_to_crm copies them), so "
                     "a round trip writes a translated value back "
                     "untranslated"]


def test_a_value_map_that_sends_two_values_to_one():
    doc = _with(TO_BILLING, values={"IsActive": {"Y": True, "N": False, "T": True}})
    assert any("sends two values to one" in p for p in _check(doc, TO_CRM))


def test_value_maps_that_are_not_inverses():
    doc = _with(TO_CRM, values={"ACTIVE": {True: "N", False: "Y"}})
    assert _check(TO_BILLING, doc) == [
        "billing_to_crm: the value map for 'ACTIVE' is not the inverse of "
        "crm_to_billing's for 'IsActive', so a round trip changes the value"]
