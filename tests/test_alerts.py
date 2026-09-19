"""What the platform says out loud, and -- more often -- what it does not.

A channel that repeats the same twenty failures every morning gets muted and
then deleted, so the case pinned hardest here is the quiet one.
"""
from __future__ import annotations

from datetime import date

import pytest

from core import alerts

TODAY = date(2026, 9, 14)


@pytest.fixture(autouse=True)
def english(monkeypatch):
    """The phrases below are English; the demo compose runs the app with
    `LDP_LANGUAGE: tr`, and `docker compose exec app pytest` inherits it."""
    from core import language
    monkeypatch.setattr(language, "LANGUAGE", "en")


def test_a_run_with_nothing_new_says_nothing():
    """The common case. `compose` returning None is what stops the POST."""
    assert alerts.compose(TODAY, [{"id": "erp.customers"}], [], {}, []) is None


def test_a_missed_sla_says_which_promise_and_why():
    text = alerts.compose(
        TODAY, [{}],
        [{"contract": "erp.customers", "score": 0.81, "errored": 0,
          "sla_min": 0.95}], {}, [])
    assert "SLA missed: erp.customers" in text
    assert "0.8100" in text and "0.95" in text


def test_a_run_that_could_not_run_is_not_a_low_score():
    """Invariant 5: an unreachable source is an engineering problem, and the
    message has to say that rather than implying the data is bad."""
    text = alerts.compose(
        TODAY, [{}],
        [{"contract": "erp.mssql.sales_orders", "score": 0.0, "errored": 24,
          "sla_min": 0.9}], {}, [])
    assert "24 checks could not run" in text
    assert "below" not in text


def test_newly_failing_checks_are_listed_and_the_rest_counted():
    text = alerts.compose(
        TODAY, [{}], [],
        {"erp.customers": ["a", "b", "c", "d", "e"]}, [])
    assert "Newly failing in erp.customers: a, b, c (+2 more)" in text


@pytest.mark.parametrize("status, expected", [
    ({"contract": "erp.customers", "copying": ["customers"]},
     "stuck in the initial copy"),
    ({"contract": "erp.customers", "streaming": False}, "apply worker"),
    ({"contract": "erp.order_lines", "reachable": False}, "unreachable"),
])
def test_each_way_of_not_replicating_gets_its_own_sentence(status, expected):
    assert expected in alerts.sync_problems([status])[0]


def test_a_healthy_rule_and_an_unknown_engine_stay_quiet():
    """A status this does not understand must not read as a problem."""
    assert alerts.sync_problems([
        {"contract": "a", "streaming": True, "copying": []},
        {"contract": "b", "engine": "sqlserver", "last_synced": "2026-09-14"},
    ]) == []


def test_no_url_means_no_alerting_and_no_error():
    assert alerts.send("anything", url="") is False


def test_the_message_is_written_in_the_deployments_language(monkeypatch):
    """Issue #44: written once for everyone, so the deployment picks it."""
    from core import language
    monkeypatch.setattr(language, "LANGUAGE", "tr")
    text = alerts.compose(TODAY, [{"id": "erp.customers"}], [],
                          {"erp.customers": ["a", "b", "c", "d"]}, [])
    assert "Kontrat kalitesi" in text and "1 kontrat" in text
    assert "erp.customers içinde yeni hatalar: a, b, c (+1 tane daha)" in text
