"""Integration flows, one file per direction, and the pairs they make.

The systems are a stand-in for any two systems neither of which is ours -- an
ERP and an accounting package, a CRM and a billing system (issue #53). They
have the problems every such pair has: different column names, a classified
identifier on both sides, a column coded `'Y'/'N'` on one side and as a bit on
the other, and both sides editing the same customer. The table contracts know
nothing about the integration; the flows carry all of it (ADR 0019), and the
two systems meet in a hub rather than writing to each other (ADR 0021).
"""
from __future__ import annotations

import copy

from core import flows


def _table(cid, name, columns, **extra):
    return {"id": cid, "schema": [{"name": name, "properties": columns}], **extra}


CRM = _table("crm.account", "ACCOUNT", [
    {"name": "ACCOUNT_CODE", "primaryKey": True, "required": True},
    {"name": "TITLE", "required": True},
    {"name": "TAX_NO", "classification": "pii"},
    {"name": "ACTIVE"}])

BILLING = _table("billing.customer", "Customer", [
    {"name": "CustomerCode", "primaryKey": True, "required": True},
    {"name": "Name", "required": True},
    {"name": "TaxId", "classification": "pii"},
    {"name": "IsActive"},
    {"name": "CreatedAt", "required": True}])

HUB = _table("hub.customer", "customer", [
    {"name": "code", "primaryKey": True, "required": True},
    {"name": "name", "required": True},
    {"name": "tax_id", "classification": "pii"},
    {"name": "active"}],
    customProperties=[{"property": "hub", "value": {"authority": "crm.account"}}])

BY_ID = {c["id"]: c for c in (CRM, BILLING, HUB)}

CRM_IN = {"id": "crm_to_hub", "from": "crm.account", "to": "hub.customer",
          "columns": {"code": "ACCOUNT_CODE", "name": "TITLE", "tax_id": "TAX_NO",
                      "active": "ACTIVE"},
          "values": {"active": {"Y": True, "N": False}}}
CRM_OUT = {"id": "hub_to_crm", "from": "hub.customer", "to": "crm.account",
           "columns": {"ACCOUNT_CODE": "code", "TITLE": "name", "TAX_NO": "tax_id",
                       "ACTIVE": "active"},
           "values": {"ACTIVE": {True: "Y", False: "N"}}}
BILLING_IN = {"id": "billing_to_hub", "from": "billing.customer", "to": "hub.customer",
              "columns": {"code": "CustomerCode", "name": "Name", "tax_id": "TaxId",
                          "active": "IsActive"}}
BILLING_OUT = {"id": "hub_to_billing", "from": "hub.customer", "to": "billing.customer",
               "columns": {"CustomerCode": "code", "Name": "name", "TaxId": "tax_id",
                           "IsActive": "active"},
               "filledByTarget": ["CreatedAt"]}
ALL = (CRM_IN, CRM_OUT, BILLING_IN, BILLING_OUT)


def _check(*docs, by_id=BY_ID) -> list[str]:
    return flows.problems([flows.parse(d) for d in docs], by_id)


def _with(doc, **changes):
    out = copy.deepcopy(doc)
    out.update(changes)
    return out


# --- the vocabulary --------------------------------------------------------

def test_a_flow_file_reads_into_a_mapping():
    flow = flows.parse(CRM_IN)
    assert (flow.mapping.reference, flow.target) == ("crm.account", "hub.customer")
    assert flow.mapping.values == {"active": {"Y": True, "N": False}}


def test_the_hub_is_a_contract_with_a_hub_property():
    assert flows.hub_of(HUB) == {"authority": "crm.account"}
    assert flows.hub_of(CRM) is None


def test_the_table_contracts_say_nothing_about_the_integration():
    for table in (CRM, BILLING):
        assert "customProperties" not in table


def test_no_flows_directory_means_no_flows(tmp_path):
    assert flows.load(tmp_path / "missing") == []


def test_a_flow_file_on_disk(tmp_path):
    (tmp_path / "crm_to_hub.yaml").write_text(
        "id: crm_to_hub\nfrom: crm.account\nto: hub.customer\n"
        "columns: {name: TITLE}\nvalues: {active: {Y: true, N: false}}\n",
        encoding="utf-8")
    [flow] = flows.load(tmp_path)
    assert flow.mapping.columns == {"name": "TITLE"}
    assert flow.mapping.values == {"active": {"Y": True, "N": False}}


# --- one direction ----------------------------------------------------------

def test_two_systems_through_a_hub_are_silent():
    assert _check(*ALL) == []


def test_an_unresolvable_table_is_reported():
    assert _check(_with(CRM_IN, to="hub.missing")) == [
        "crm_to_hub: names 'hub.missing', which is not a contract this "
        "platform loads"]


def test_the_one_way_refusals_are_mappings():
    doc = copy.deepcopy(CRM_IN)
    doc["columns"]["names"] = "TITLE"
    assert any("fills 'names', which this contract does not declare" in p
               for p in _check(doc))


def test_a_classified_value_may_not_land_unclassified():
    hub = copy.deepcopy(HUB)
    hub["schema"][0]["properties"][2].pop("classification")
    found = _check(CRM_IN, by_id={**BY_ID, "hub.customer": hub})
    assert any("may not lose its classification" in p for p in found)


def test_several_systems_filling_one_hub_column_is_the_point():
    assert not any("filled twice" in p for p in _check(CRM_IN, BILLING_IN))


def test_some_flow_of_each_system_must_carry_the_whole_record():
    doc = copy.deepcopy(BILLING_IN)
    del doc["columns"]["name"]
    assert ("hub.customer: no flow from billing carries everything the record "
            "requires (code, name), so nothing from billing can create or delete "
            "one") in _check(CRM_IN, doc)


def test_every_flow_into_a_hub_carries_its_key():
    doc = copy.deepcopy(BILLING_IN)
    del doc["columns"]["code"]
    assert ("billing_to_hub: the hub key code is not filled, so a row cannot "
            "find its record") in _check(doc)


def test_a_required_system_column_nothing_fills():
    found = _check(_with(BILLING_OUT, filledByTarget=[]))
    assert found == ["hub_to_billing: CreatedAt is required here and no "
                     "mapping fills it; the contract promises a column "
                     "nothing puts a value in"]


def test_two_flows_into_one_system_column():
    other = {"id": "erp_to_billing", "from": "hub.customer", "to": "billing.customer",
             "columns": {"Name": "name"}}
    assert any("'Name' is filled twice" in p for p in _check(BILLING_OUT, other))


# --- pairs ---------------------------------------------------------------------

def test_two_systems_may_not_pair_directly():
    direct_in = {"id": "crm_to_billing", "from": "crm.account", "to": "billing.customer",
                 "columns": {"CustomerCode": "ACCOUNT_CODE", "Name": "TITLE"},
                 "filledByTarget": ["CreatedAt"]}
    direct_out = {"id": "billing_to_crm", "from": "billing.customer", "to": "crm.account",
                  "columns": {"ACCOUNT_CODE": "CustomerCode", "TITLE": "Name"}}
    found = _check(direct_in, direct_out)
    assert [p for p in found if "directly" in p] == [
        "billing_to_crm: billing.customer and crm.account write to each other "
        "directly; two-way integration goes through a hub, or an echo cannot "
        "be told from an edit (ADR 0021)"]


def test_a_column_nothing_maps_back():
    doc = copy.deepcopy(CRM_OUT)
    del doc["columns"]["ACTIVE"]
    del doc["values"]["ACTIVE"]
    assert ("crm_to_hub: 'active' is filled from 'ACTIVE' but hub_to_crm does "
            "not map it back, so an edit made at hub.customer is overwritten by "
            "the next change at crm.account") in _check(CRM_IN, doc)


def test_a_round_trip_into_another_column():
    doc = copy.deepcopy(CRM_OUT)
    doc["columns"]["TITLE"] = "tax_id"
    assert any("fills 'TITLE' from 'tax_id'" in p for p in _check(CRM_IN, doc))


def test_a_value_map_on_one_side_only():
    found = _check(CRM_IN, _with(CRM_OUT, values={}))
    assert found == ["crm_to_hub: 'active' <-> 'ACTIVE' translates values in one "
                     "direction only (hub_to_crm copies them), so a round trip "
                     "writes a translated value back untranslated"]


def test_a_value_map_that_sends_two_values_to_one():
    doc = _with(CRM_IN, values={"active": {"Y": True, "N": False, "T": True}})
    assert any("sends two values to one" in p for p in _check(doc, CRM_OUT))


def test_value_maps_that_are_not_inverses():
    doc = _with(CRM_OUT, values={"ACTIVE": {True: "N", False: "Y"}})
    assert _check(CRM_IN, doc) == [
        "crm_to_hub: the value map for 'active' is not the inverse of "
        "hub_to_crm's for 'ACTIVE', so a round trip changes the value"]


def test_a_pair_problem_is_reported_once():
    assert len(_check(CRM_IN, _with(CRM_OUT, values={}))) == 1


# --- the hub -------------------------------------------------------------------

def test_the_authority_must_be_a_system_with_a_flow_in():
    hub = copy.deepcopy(HUB)
    hub["customProperties"][0]["value"]["authority"] = "erp.customers"
    found = _check(*ALL, by_id={**BY_ID, "hub.customer": hub})
    assert found == ["hub.customer: authority 'erp.customers' is not a system "
                     "with a flow into this hub, so the first sync has no system "
                     "to take disputed values from"]


# --- one system, several tables (#79) ---------------------------------------

ADDRESS = _table("crm.account_address", "account_address", [
    {"name": "ACCOUNT_CODE", "primaryKey": True, "required": True},
    {"name": "ADDR_TYPE", "primaryKey": True, "required": True},
    {"name": "CITY"}])
HUB_WITH_CITIES = copy.deepcopy(HUB)
HUB_WITH_CITIES["schema"][0]["properties"] += [{"name": "invoice_city"},
                                               {"name": "shipping_city"}]
WIDE = {**BY_ID, "crm.account_address": ADDRESS, "hub.customer": HUB_WITH_CITIES}


def _address(kind, field, *, match=True):
    m = {"match": {"ADDR_TYPE": kind}} if match else {}
    return ({"id": f"crm_{kind}_to_hub", "from": "crm.account_address",
             "to": "hub.customer", **m,
             "columns": {"code": "ACCOUNT_CODE", field: "CITY"}},
            {"id": f"hub_to_crm_{kind}", "from": "hub.customer",
             "to": "crm.account_address", **m,
             "columns": {"ACCOUNT_CODE": "code", "CITY": field}})


def test_two_tables_of_one_system_through_a_hub_are_silent():
    docs = (*ALL, *_address("INV", "invoice_city"), *_address("SHP", "shipping_city"))
    assert _check(*docs, by_id=WIDE) == []


def test_a_table_with_several_rows_per_record_needs_a_match():
    found = _check(*_address("INV", "invoice_city", match=False), by_id=WIDE)
    assert ("crm_INV_to_hub: crm.account_address has several rows per record "
            "(its key ADDR_TYPE is not the hub's); pin it with match, or every "
            "row overwrites the same record") in found


def test_a_match_on_an_undeclared_column():
    inbound, _ = _address("INV", "invoice_city")
    inbound["match"] = {"KIND": "INV"}
    assert any("match names 'KIND'" in p for p in _check(inbound, by_id=WIDE))


def test_two_tables_of_one_system_may_not_fill_one_field():
    inbound, _ = _address("INV", "name")
    found = _check(CRM_IN, inbound, by_id=WIDE)
    assert any("'name' is filled by both" in p and "two tables of crm" in p
               for p in found)


def test_read_with_one_match_and_written_back_with_another():
    inbound, _ = _address("INV", "invoice_city")
    _, outbound = _address("SHP", "invoice_city")
    found = _check(CRM_IN, inbound, outbound, by_id=WIDE)
    assert any("read with match" in p and "written back with" in p for p in found)
