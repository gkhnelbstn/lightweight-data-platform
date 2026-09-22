"""The Integration tab's route (#78): what it says when parts of it are down.

Each of its three sources can be unreachable without the others, and the tab
has to say which rather than fail as a whole. Runs without SeaTunnel or a hub
database -- that absence is the case under test.
"""
from __future__ import annotations

import json
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
    assert hub["codes"] == {"crm": "crm_code", "billing": "billing_code",
                            "shop": "shop_code", "loyalty": "loyalty_code"}
    address = next(s for s in hub["systems"] if s["table"] == "crm.account_address")
    assert sorted(f["flow"] for f in address["in"]) == [
        "crm_invoice_address_to_hub", "crm_shipping_address_to_hub"]
    assert {f["match"]["ADDR_TYPE"] for f in address["out"]} == {"INV", "SHP"}
    assert all(f["job"] is None for s in hub["systems"] for f in s["in"] + s["out"])
    # Two servers -- SQL Server for the CRM and billing, Postgres for the
    # shop -- each asked once, not once per flow.
    assert len(asked) == 2
    assert any("schema not readable" in d for s in hub["systems"] for d in s["drift"])
    # Which machine each end is on: "hub" alone did not say.
    assert hub["where"] == "postgres db:5432, database hub"
    assert address["where"] == "sqlserver mssql:1433, database crm"
    loyalty = next(s for s in hub["systems"] if s["table"] == "loyalty.member")
    assert loyalty["where"].startswith("api ") and "?" not in loyalty["where"]


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


def test_a_record_history_reads_as_what_happened():
    """An update's before and after images are one line of what changed, a
    classified value stays masked, and each line says what the hub did."""
    from api.integration_detail import history
    row = lambda kind, sys, ms, outcome, **v: {  # noqa: E731
        "row_kind": kind, "system": sys, "source_ms": ms, "landed_at": "t",
        "fields": "crm_code,name,tax_id", "row": {"crm_code": 1, "outcome": outcome, **v}}
    rows = [row("INSERT", "crm.account", 1, "created", name="Acme", tax_id="111"),
            row("UPDATE_BEFORE", "billing.customer", 2, "before", name="Acme", tax_id="111"),
            row("UPDATE_AFTER", "billing.customer", 2, "echo", name="Acme Ltd", tax_id="111"),
            row("DELETE", "crm.account", 3, "deleted", name="Acme Ltd", tax_id="111")]
    out = history(rows, skip={"crm_code"}, hidden={"tax_id"})
    assert [(h["system"], h["kind"], h["outcome"]) for h in out] == [
        ("crm.account", "insert", "created"), ("billing.customer", "update", "echo"),
        ("crm.account", "delete", "deleted")]
    assert out[0]["changes"] == [{"field": "name", "from": None, "to": "Acme"},
                                 {"field": "tax_id", "from": None, "to": api.sample.MASK}]
    assert out[1]["changes"] == [{"field": "name", "from": "Acme", "to": "Acme Ltd"}]


# --- settling a held row from the screen (#111) -----------------------------

def _linker(monkeypatch, answer):
    """The link route against a stand-in hub database."""
    from api import integration_detail as detail

    class Cursor:
        def fetchone(self):
            if isinstance(answer, Exception):
                raise answer
            return {"record": answer}

    class Connection:
        calls: list = []

        def execute(self, sql, params):
            Connection.calls.append((sql, params))
            return Cursor()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(detail, "_hub", lambda hub_id: ({}, "customer", "customer_id",
                                                        {"crm": "crm_code"}, set()))
    monkeypatch.setattr(detail, "_connect", lambda contract: Connection())
    return detail, Connection


def test_linking_a_held_row_calls_the_hubs_own_function(monkeypatch):
    detail, connection = _linker(monkeypatch, {"customer_id": 7})
    got = detail.link(detail.Link(hub="hub.customer", system="crm.account",
                                  local={"crm_code": 42}, record={"customer_id": 7}))
    assert got == {"record": {"customer_id": 7}}
    sql, params = connection.calls[-1]
    assert "hub.link" in sql
    assert params[:2] == ("customer", "crm.account")
    assert json.loads(params[2]) == {"crm_code": 42} and json.loads(params[3]) == {"customer_id": 7}


def test_a_row_with_no_record_named_becomes_one_of_its_own(monkeypatch):
    detail, connection = _linker(monkeypatch, {"customer_id": 8})
    detail.link(detail.Link(hub="hub.customer", system="crm.account", local={"crm_code": 43}))
    assert connection.calls[-1][1][3] is None


def test_the_hubs_refusal_is_the_message_not_a_500(monkeypatch):
    import psycopg
    boom = psycopg.errors.RaiseException(
        "hub: record {\"customer_id\": 3} is already 91 in crm.account\nCONTEXT: PL/pgSQL")
    detail, _ = _linker(monkeypatch, boom)
    with pytest.raises(Exception) as caught:
        detail.link(detail.Link(hub="hub.customer", system="crm.account",
                                local={"crm_code": 44}, record={"customer_id": 3}))
    assert caught.value.status_code == 400
    assert "already 91 in crm.account" in caught.value.detail
    assert "CONTEXT" not in caught.value.detail
