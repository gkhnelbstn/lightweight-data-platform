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
