"""The Integration tab's route (#78): what it says when parts of it are down.

Each of its three sources can be unreachable without the others, and the tab
has to say which rather than fail as a whole. Runs without SeaTunnel or a hub
database -- that absence is the case under test.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from api import integration as api  # noqa: E402

DEMO = Path(__file__).resolve().parents[1] / "demo" / "integration"


def test_flows_are_grouped_by_system_table_even_with_everything_down(monkeypatch):
    monkeypatch.setattr(api, "DIRECTORY", DEMO)
    monkeypatch.setattr(api, "SEATUNNEL", "http://127.0.0.1:9")
    monkeypatch.setattr(api, "hub_state", lambda c: (_ for _ in ()).throw(OSError("down")))
    asked = []
    monkeypatch.setattr(api.flow_schema, "problems",
                        lambda f, by_id, timeout: asked.append(f.id) or (_ for _ in ()).throw(OSError()))
    got = api.integration()
    assert got["problems"] == []
    assert got["seatunnel_error"]
    [hub] = got["hubs"]
    assert hub["hub_error"] == "OSError: down"
    assert hub["codes"] == {"crm": "crm_code", "billing": "billing_code"}
    address = next(s for s in hub["systems"] if s["table"] == "crm.account_address")
    assert sorted(f["flow"] for f in address["in"]) == [
        "crm_invoice_address_to_hub", "crm_shipping_address_to_hub"]
    assert {f["match"]["ADDR_TYPE"] for f in address["out"]} == {"INV", "SHP"}
    assert all(f["job"] is None for s in hub["systems"] for f in s["in"] + s["out"])
    # One SQL Server for both systems: asked once, not once per flow.
    assert len(asked) == 1
    assert any("schema not readable" in d for s in hub["systems"] for d in s["drift"])


def test_a_classified_value_is_never_shown():
    """tax_id is pii in the hub contract: a conflict over it, or a held row
    carrying it, says so without the value -- as failing rows do."""
    hidden = {"tax_id"}
    assert api._masked("111", "tax_id", hidden) == api.sample.MASK
    assert api._masked("Acme", "name", hidden) == "Acme"
    assert api._masked({"name": "Acme", "tax_id": "111"}, "*", hidden) == {
        "name": "Acme", "tax_id": api.sample.MASK}
    assert api._masked(None, "tax_id", hidden) is None


def test_a_flow_shows_its_newest_job(monkeypatch):
    finished = [{"jobName": "crm_to_hub", "jobStatus": "FAILED", "finishTime": "2026-09-19 10:00:00",
                 "errorMsg": "boom"},
                {"jobName": "crm_to_hub", "jobStatus": "CANCELED", "finishTime": "2026-09-19 11:00:00"},
                {"jobName": "hub_to_crm", "jobStatus": "CANCELED", "finishTime": "2026-09-19 09:00:00"}]
    running = [{"jobName": "hub_to_crm", "jobStatus": "RUNNING", "startTime": "2026-09-19 12:00:00",
                "metrics": {"SourceReceivedCount": "7",
                            "TableSinkWriteCount": {"Sink[0].a": "2", "Sink[1].a": "3"}}}]
    monkeypatch.setattr(api, "_get", lambda path: running if "running" in path else finished)
    jobs, error = api.jobs()
    assert error is None
    assert jobs["crm_to_hub"]["status"] == "CANCELED"
    assert (jobs["hub_to_crm"]["status"], jobs["hub_to_crm"]["read"],
            jobs["hub_to_crm"]["written"]) == ("RUNNING", 7, 5)


def test_a_failed_job_says_its_root_cause_not_its_stack():
    trace = ("java.lang.RuntimeException: java.util.concurrent.ExecutionException: boom\n"
             "\tat org.apache.seatunnel.Foo.bar(Foo.java:1)\n"
             "Caused by: org.apache.seatunnel.JdbcConnectorException: ErrorCode:[COMMON-10]\n"
             "Caused by: java.sql.BatchUpdateException: Invalid column name 'TAX_NO'.\n"
             "\tat com.microsoft.sqlserver.X(X.java:2)")
    assert api.root_cause(trace) == "Invalid column name 'TAX_NO'."
    assert api.root_cause(None) is None
