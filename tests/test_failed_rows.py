"""How many rows a check failed on, from what `datacontract test` reports.

A custom SQL rule reports the value its query returned. For `mustBe: 0` that
value is the count of bad rows, and for a threshold on the table's size it is
the size itself -- a row count rule that passed on 366 rows was stored as 366
failed rows out of 366, and every contract with one lost ~5 points of score to
a check that had passed.
"""
from __future__ import annotations

from core.runner import failed_rows


def test_reported_failed_rows_win():
    check = {"result": "failed", "diagnostics": {"failed_rows": 7, "value": 99}}
    assert failed_rows(check) == 7


def test_the_value_of_a_failed_sql_rule_is_its_bad_rows():
    assert failed_rows({"result": "failed", "diagnostics": {"value": 12}}) == 12


def test_a_passed_threshold_rule_failed_on_nothing():
    # `SELECT COUNT(*) FROM t` with `mustBeGreaterOrEqualTo: 330`, passing.
    assert failed_rows({"result": "passed", "diagnostics": {"value": 366}}) == 0


def test_no_diagnostics_is_all_or_nothing():
    assert failed_rows({"result": "passed"}) == 0
    assert failed_rows({"result": "failed"}) == 1
