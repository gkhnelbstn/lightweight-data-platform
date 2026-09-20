"""Running one contract's checks from the screen (#113).

The run itself is `core/runner.py`'s and is tested there. What is pinned here
is the bookkeeping around it: an unknown contract is a 404, a second run of a
contract already running is refused rather than queued, and a run that raised
says so instead of looking like one that never finished.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from api import runs  # noqa: E402


@pytest.fixture(autouse=True)
def empty():
    runs.RUNS.clear()
    yield
    runs.RUNS.clear()


@pytest.fixture
def contracts(monkeypatch):
    monkeypatch.setattr("core.runner.load_contracts", lambda *a, **k: [{"id": "erp.customers"}])


def test_a_contract_nobody_declares_is_not_run(contracts):
    with pytest.raises(Exception) as caught:
        runs.start("erp.nothing")
    assert caught.value.status_code == 404


def test_a_second_run_of_the_same_contract_is_refused(contracts, monkeypatch):
    """Two runs of one contract on one day write the same rows twice and
    double the load on the source for no second answer."""
    monkeypatch.setattr(runs.threading, "Thread", lambda **k: type(
        "Idle", (), {"start": lambda self: None})())
    assert runs.start("erp.customers")["state"] == "running"
    with pytest.raises(Exception) as caught:
        runs.start("erp.customers")
    assert caught.value.status_code == 409


def test_a_finished_run_reports_what_it_found(contracts, monkeypatch):
    monkeypatch.setattr("core.runner.run", lambda day, cs: [
        {"contract": "erp.customers", "as_of": str(day), "score": 1.0,
         "failed": 0, "errored": 0, "total": 18}])
    runs._run("erp.customers")
    assert runs.state("erp.customers")["state"] == "done"
    assert runs.state("erp.customers")["result"]["total"] == 18


def test_a_run_that_raised_says_so(contracts, monkeypatch):
    def boom(day, cs):
        raise RuntimeError("no database")

    monkeypatch.setattr("core.runner.run", boom)
    runs._run("erp.customers")
    got = runs.state("erp.customers")
    assert got["state"] == "failed" and "no database" in got["error"]


def test_a_contract_nobody_started_here_is_idle():
    assert runs.state("erp.customers") == {"state": "idle"}
