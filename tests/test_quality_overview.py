"""The overview's arithmetic, without a database: the SQL gathers the rows,
and what is pinned here is what they are turned into. #145"""
from __future__ import annotations

from datetime import date

from api.quality_overview import age_bucket, aging, contract_rows, domain_trend

DAY = date(2026, 9, 21)


def test_a_failure_that_started_today_is_new():
    assert age_bucket(DAY, DAY) == "new"


def test_the_buckets_are_inclusive_of_their_last_day():
    assert age_bucket(date(2026, 9, 15), DAY) == "week"    # 7 days
    assert age_bucket(date(2026, 9, 14), DAY) == "month"   # 8 days
    assert age_bucket(date(2026, 8, 23), DAY) == "month"   # 30 days
    assert age_bucket(date(2026, 8, 22), DAY) == "older"   # 31 days


def test_an_accepted_failure_is_not_aging_it_was_decided():
    failures = [{"since": DAY, "state": "open"},
                {"since": date(2026, 7, 1), "state": "accepted"},
                {"since": date(2026, 7, 1), "state": "acknowledged"}]
    assert aging(failures, DAY) == {"new": 1, "week": 0, "month": 0, "older": 1}


def test_a_domain_scores_the_mean_of_its_contracts_per_day():
    scores = [{"contract_id": "a", "run_at": DAY, "score": 0.9},
              {"contract_id": "b", "run_at": DAY, "score": 0.7},
              {"contract_id": "c", "run_at": DAY, "score": 1.0}]
    trend = domain_trend(scores, {"a": "ops", "b": "ops", "c": "fleet"})
    assert trend == [{"domain": "fleet", "points": [{"run_at": DAY, "score": 1.0}]},
                     {"domain": "ops", "points": [{"run_at": DAY, "score": 0.8}]}]


def test_contracts_are_worst_first_and_the_unrun_last():
    contracts = [{"id": "good"}, {"id": "bad"}, {"id": "never"}]
    now = {"good": {"score": 0.99, "sla_min": 0.95, "sla_met": True,
                    "checks_total": 10, "checks_failed": 0, "run_at": DAY},
           "bad": {"score": 0.80, "sla_min": 0.95, "sla_met": False,
                   "checks_total": 10, "checks_failed": 3, "run_at": DAY}}
    rows = contract_rows(contracts, now, {"bad": {"score": 0.85}})
    assert [r["id"] for r in rows] == ["bad", "good", "never"]
    assert rows[0]["previous"] == 0.85 and rows[2]["score"] is None
