"""A table that changed under its flow is refused, never followed. #84.

The catalogue answers are faked: what matters is how they are read, and the
live cases -- a dropped column, a re-added one -- were measured on the demo
(core/flow_schema.py says what each did).
"""
from __future__ import annotations

from pathlib import Path

from core import flow_jobs, flow_schema

DEMO = Path(__file__).resolve().parents[1] / "demo" / "integration"


def _fake(monkeypatch, live: dict, captured: dict):
    class Cursor:
        def execute(self, sql, *_):
            self.rows = list((captured if "cdc." in sql else live).items())
            return self

        def fetchall(self):
            return self.rows

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(flow_schema, "mssql", lambda server, timeout=30: Connection())


def _flow(name):
    by_id, flows = flow_jobs.load(DEMO)
    return next(f for f in flows if f.id == name), by_id


ACCOUNT = {"ACCOUNT_CODE": 1, "TITLE": 2, "TAX_NO": 3, "ACTIVE": 4}


def test_an_unchanged_table_is_silent(monkeypatch):
    _fake(monkeypatch, ACCOUNT, ACCOUNT)
    assert flow_schema.problems(*_flow("crm_to_hub")) == []


def test_a_column_added_is_not_a_problem(monkeypatch):
    _fake(monkeypatch, {**ACCOUNT, "NOTE": 5}, ACCOUNT)
    assert flow_schema.problems(*_flow("crm_to_hub")) == []


def test_a_mapped_column_dropped_is_refused_both_ways(monkeypatch):
    live = {k: v for k, v in ACCOUNT.items() if k != "TAX_NO"}
    _fake(monkeypatch, live, ACCOUNT)
    assert flow_schema.problems(*_flow("crm_to_hub")) == [
        "crm_to_hub: crm.account has no column TAX_NO any more"]
    assert flow_schema.problems(*_flow("hub_to_crm")) == [
        "hub_to_crm: crm.account has no column TAX_NO any more"]


def test_a_column_re_added_is_not_captured_though_its_name_is(monkeypatch):
    _fake(monkeypatch, {**ACCOUNT, "TAX_NO": 6}, ACCOUNT)
    [problem] = flow_schema.problems(*_flow("crm_to_hub"))
    assert "TAX_NO is not captured by CDC (dbo_account)" in problem
    # Writing needs no capture: the out-flow is fine.
    assert flow_schema.problems(*_flow("hub_to_crm")) == []
