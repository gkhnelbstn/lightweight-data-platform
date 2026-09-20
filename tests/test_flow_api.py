"""An API source: a poll, one way, and it must say when a record changed.

#97, ADR 0027. There is no change log behind an HTTP endpoint, so the things
a CDC source gets from its connector -- the row kind and the commit time --
are stated here instead, and the refusals are what stops the ones that cannot
be stated honestly from running at all.

No database and no network: the contracts are built here and the job is read
as the compiler writes it.
"""
from __future__ import annotations

import copy

import pytest

from core import flow_jobs, flows

API = {
    "id": "loyalty.member",
    "servers": [{"server": "loyalty", "type": "api",
                 "location": "http://loyalty:8099/members"}],
    "customProperties": [
        {"property": "api",
         "value": {"contentField": "$.members.*", "changedAt": "updated_ms"}}],
    "schema": [{"name": "member", "physicalName": "member", "properties": [
        {"name": "member_no", "physicalType": "bigint", "primaryKey": True,
         "required": True, "description": "The loyalty scheme's own number."},
        {"name": "full_name", "physicalType": "text", "required": True,
         "description": "Member name."},
        {"name": "vkn", "physicalType": "text", "classification": "pii",
         "description": "Tax identifier."},
        {"name": "grade", "physicalType": "text", "description": "Loyalty grade."},
        {"name": "updated_ms", "physicalType": "bigint", "required": True,
         "description": "When the scheme last changed this member, epoch ms."},
    ]}]}

HUB = {
    "id": "hub.customer",
    "servers": [{"server": "hub", "type": "postgres", "host": "db", "port": 5432,
                 "database": "hub", "schema": "hub"}],
    "customProperties": [
        {"property": "hub",
         "value": {"authority": "loyalty.member", "keys": {"loyalty": "loyalty_code"}}}],
    "schema": [{"name": "customer", "physicalName": "customer", "properties": [
        {"name": "customer_id", "physicalType": "bigint", "primaryKey": True,
         "required": True, "description": "The hub's own key."},
        {"name": "loyalty_code", "physicalType": "bigint",
         "description": "The loyalty scheme's number for this customer."},
        {"name": "name", "physicalType": "text", "required": True,
         "description": "Customer name."},
        {"name": "tax_id", "physicalType": "text", "classification": "pii",
         "description": "Tax identifier."},
        {"name": "tier", "physicalType": "text", "description": "Loyalty tier."},
    ]}]}

FLOW = {"id": "loyalty_to_hub", "from": "loyalty.member", "to": "hub.customer",
        "columns": {"loyalty_code": "member_no", "name": "full_name",
                    "tax_id": "vkn", "tier": "grade"},
        "linkBy": ["tax_id"], "job": {"pollSeconds": 20}}


def check(api=None, hub=None, doc=None):
    by_id = {"loyalty.member": api or API, "hub.customer": hub or HUB}
    return by_id, [flows.parse(doc or FLOW)]


@pytest.fixture(scope="module")
def job():
    by_id, loaded = check()
    assert flows.problems(loaded, by_id) == []
    return flow_jobs.jobs(by_id, loaded)["loyalty_to_hub"]


def test_the_source_is_the_endpoint_polled(job):
    source, = job["source"]
    assert source["plugin_name"] == "Http"
    assert source["url"] == "http://loyalty:8099/members"
    assert source["content_field"] == "$.members.*"
    # `pollSeconds` is a job setting like checkpointInterval (ADR 0026).
    assert source["poll_interval_millis"] == 20_000
    # SeaTunnel parses the answer itself, so it is told the shape -- the
    # fields this flow carries and the one saying when the record changed.
    assert source["schema"] == {"fields": {
        "member_no": "bigint", "full_name": "string", "vkn": "string",
        "grade": "string", "updated_ms": "bigint"}}


def test_the_row_kind_and_the_commit_time_are_stated_not_read(job):
    """A CDC source gets both from its connector (`Metadata`,
    `RowKindExtractor`); a poll has neither to read."""
    assert [p["plugin_name"] for p in job["transform"]] == ["Sql"]
    query = job["transform"][0]["query"]
    assert query.startswith("SELECT 'POLL' AS row_kind, updated_ms AS source_ms, "
                            "'loyalty.member' AS system")
    assert job["sink"][0]["query"].startswith("insert into hub.customer_inbox")


def test_an_api_that_cannot_say_when_a_record_changed_is_refused():
    """Poll time is when we noticed. An API given it would win every dispute
    it takes part in, including the ones where its value is the stale one."""
    api = copy.deepcopy(API)
    del api["customProperties"][0]["value"]["changedAt"]
    by_id, loaded = check(api=api)
    assert any("changedAt" in p for p in flows.problems(loaded, by_id))


def test_a_change_time_that_is_not_epoch_milliseconds_is_refused():
    api = copy.deepcopy(API)
    api["schema"][0]["properties"][-1]["physicalType"] = "timestamp"
    by_id, loaded = check(api=api)
    assert any("epoch milliseconds" in p for p in flows.problems(loaded, by_id))


def test_a_column_an_answer_cannot_carry_is_refused():
    api = copy.deepcopy(API)
    api["schema"][0]["properties"][3]["physicalType"] = "timestamp"
    by_id, loaded = check(api=api)
    assert any("an API answer cannot carry" in p for p in flows.problems(loaded, by_id))


def test_nothing_is_written_to_an_api():
    """The way back would need a write, an echo the hub can recognise and a
    commit time for it. None of the three exists."""
    back = {"id": "hub_to_loyalty", "from": "hub.customer", "to": "loyalty.member",
            "columns": {"member_no": "loyalty_code", "full_name": "name"}}
    by_id, loaded = check(doc=back)
    assert any("nothing here writes to one" in p for p in flows.problems(loaded, by_id))


def test_an_api_source_goes_into_a_hub_and_nowhere_else():
    table = copy.deepcopy(HUB)
    table["customProperties"] = []
    by_id, loaded = check(hub=table)
    assert any("goes into a hub and nowhere else" in p
               for p in flows.problems(loaded, by_id))
