"""A contract's agreement as its page shows it (#144).

Pinned: each promise is shown with what the runs measured against it, and
only where something measures it; foreign keys read both ways, each with the
contract covering the other table; ODCS 3.1's team object and the 3.0 array
read as one shape; and the location never carries more than names.
"""
from __future__ import annotations

from datetime import date

from api.contract_agreement import agreement, measured, period, relations, team

SERVER = {"server": "erp", "type": "sqlserver", "host": "10.0.0.1", "port": 1433,
          "database": "erp", "schema": "dbo"}


def contract(cid: str, table: str, properties: list[dict], **extra) -> dict:
    return {"id": cid, "servers": [SERVER],
            "schema": [{"name": table, "physicalName": table, "properties": properties}],
            **extra}


DAY = [{"run_at": date(2026, 9, 21), "score": 0.89, "sla_met": False, "checks_errored": 0}]


def test_a_promise_carries_what_the_runs_measured_against_it():
    daily = {"property": "frequency", "value": 1, "unit": "d"}
    on_time = measured(daily, DAY, [], date(2026, 9, 22))
    assert (on_time["days"], on_time["met"], on_time["runs"]) == (1, True, 1)
    assert measured(daily, DAY, [], date(2026, 9, 24))["met"] is False
    floor = measured({"property": "minScore", "value": 0.95}, DAY, [], date(2026, 9, 22))
    assert (floor["value"], floor["met"]) == (0.89, False)
    checks = [{"dimension": "completeness", "status": "pass"},
              {"dimension": "completeness", "status": "fail"},
              {"dimension": "conformity", "status": "fail"}]
    assert measured({"property": "completeness", "value": 100}, DAY, checks,
                    date(2026, 9, 22)) == {"kind": "checks", "passed": 1, "total": 2,
                                           "met": False}


def test_a_promise_nothing_measures_is_not_guessed_at():
    assert measured({"property": "latency", "value": 24, "unit": "h"}, DAY, [],
                    date(2026, 9, 22)) is None
    # No run yet: nothing to say about the floor or the frequency either.
    assert measured({"property": "minScore", "value": 0.95}, [], [], date(2026, 9, 22)) is None
    assert period(24, "h").days == 1 and period("soon", "d") is None


def test_foreign_keys_read_both_ways_with_the_contract_behind_each_table():
    country = contract("x.country", "country", [{"name": "country_id", "primaryKey": True}])
    firm = contract("x.firm", "firm", [
        {"name": "firm_id"},
        {"name": "country_id", "relationships": [{"type": "foreignKey",
                                                  "to": "country.country_id"}]}])
    elsewhere = {**contract("y.firm", "firm", firm["schema"][0]["properties"]),
                 "servers": [{**SERVER, "host": "10.0.0.2"}]}
    everything = [country, firm, elsewhere]
    assert relations(firm, everything)["references"] == [
        {"column": "country_id", "table": "country", "to_column": "country_id",
         "contract": "x.country"}]
    # The same table on another server is another table.
    assert relations(country, everything)["referenced_by"] == [
        {"column": "country_id", "table": "firm", "to_column": "country_id",
         "contract": "x.firm"}]


def test_both_odcs_team_shapes_read_as_one():
    members = [{"username": "a@x", "role": "owner"}]
    assert team({"team": {"name": "DQ", "members": members}}) == {
        "name": "DQ", "members": [{"username": "a@x", "name": None, "role": "owner"}]}
    assert team({"team": members})["members"][0]["role"] == "owner"
    assert team({}) == {"name": None, "members": []}


def test_the_agreement_is_the_contracts_own_words():
    doc = contract("x.firm", "firm", [], tenant="owner@x", status="active",
                   description={"purpose": "Parties.", "usage": "Billing."},
                   slaProperties=[{"property": "minScore", "value": 0.95}],
                   customProperties=[{"property": "privacy", "value": "KVKK."},
                                     {"property": "dataClassification", "value": "pii"},
                                     {"property": "useCases", "value": ["CRM"]}])
    got = agreement(doc, [doc], DAY, [], date(2026, 9, 22))
    assert got["owner"] == "owner@x"
    assert got["description"] == {"purpose": "Parties.", "usage": "Billing.", "limitations": None}
    assert [t["key"] for t in got["terms"]] == ["dataClassification", "privacy"]
    assert got["use_cases"] == ["CRM"]
    assert got["sla"][0]["measured"]["kind"] == "score"
    assert set(got["location"]) == {"type", "host", "port", "database", "schema", "table"}
    assert got["runs"] == [{"as_of": "2026-09-21", "met": False, "errored": False}]
