"""Flows compiled into SeaTunnel jobs: the shape each job must have.

Each assertion is a finding from ADR 0020 that the compiler must not forget:
the commit time comes from `SourceTimestamp`, not `EventTime`; before images
reach the hub only through `RowKindExtractor` and a plain insert; a delivery
drops before images; and a SQL Server target uses the generated MERGE, whose
rewrite of an unchanged value adds no CDC row.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import flow_jobs, flows

DEMO = Path(__file__).resolve().parents[1] / "demo" / "integration"


@pytest.fixture(scope="module")
def compiled():
    by_id, loaded = flow_jobs.load(DEMO)
    assert flows.problems(loaded, by_id) == []
    return flow_jobs.jobs(by_id, loaded)


def _plugins(job, stage):
    return [p["plugin_name"] for p in job[stage]]


def test_every_demo_flow_compiles(compiled):
    assert sorted(compiled) == ["billing_to_hub", "crm_to_hub",
                                "hub_to_billing", "hub_to_crm"]


def test_into_the_hub_keeps_commit_time_and_before_images(compiled):
    job = compiled["crm_to_hub"]
    assert _plugins(job, "source") == ["SqlServer-CDC"]
    assert _plugins(job, "transform") == ["Metadata", "RowKindExtractor", "Sql"]
    meta = job["transform"][0]["metadata_fields"]
    assert meta == {"SourceTimestamp": "source_ms"}      # not EventTime
    sink = job["sink"][0]
    assert sink["query"].startswith("insert into hub.customer_inbox")
    assert "generate_sink_sql" not in sink               # it drops before rows


def test_a_value_map_is_a_searched_case(compiled):
    sql = compiled["crm_to_hub"]["transform"][2]["query"]
    assert ("CASE WHEN ACTIVE = 'Y' THEN true WHEN ACTIVE = 'N' THEN false END "
            "AS active") in sql
    assert "'crm.account' AS system" in sql
    back = compiled["hub_to_crm"]["transform"][1]["query"]
    assert "CASE WHEN active = true THEN 'Y' WHEN active = false THEN 'N' END AS ACTIVE" in back


def test_out_of_the_hub_drops_before_images_and_merges(compiled):
    job = compiled["hub_to_billing"]
    assert _plugins(job, "source") == ["Postgres-CDC"]
    assert job["source"][0]["slot.name"] == "hub_to_billing_slot"
    assert job["transform"][0]["exclude_kinds"] == ["UPDATE_BEFORE"]
    sink = job["sink"][0]
    assert sink["generate_sink_sql"] is True
    assert (sink["table"], sink["primary_keys"]) == ("dbo.customer", ["CustomerCode"])


def test_credentials_are_placeholders(compiled):
    text = str(compiled)
    assert "${MSSQL_PASSWORD}" in text and "${PG_PASSWORD}" in text
    assert "Str0ng" not in text


def test_a_postgres_target_is_refused_until_it_has_a_guarded_upsert():
    by_id, loaded = flow_jobs.load(DEMO)
    by_id["billing.customer"]["servers"][0]["type"] = "postgres"
    with pytest.raises(NotImplementedError, match="guarded upsert"):
        flow_jobs.jobs(by_id, loaded)


def test_secrets_are_filled_on_the_way_out(monkeypatch):
    from core import flow_apply
    for name in flow_apply.SECRETS:
        monkeypatch.setenv(name, f"<{name}>")
    filled = flow_apply._fill({"password": "${MSSQL_PASSWORD}"})
    assert filled == {"password": "<MSSQL_PASSWORD>"}
    monkeypatch.delenv("PG_USER")
    monkeypatch.setattr(flow_apply, "SECRETS", ("MSSQL_PASSWORD",))
    with pytest.raises(SystemExit, match="PG_USER"):
        flow_apply._fill({"username": "${PG_USER}"})
