"""The bookkeeping that makes a re-run a no-op instead of a re-send.

Selecting which days are pending needs a database and is exercised by running
the thing. What is tested here is the pair of pure functions the idempotency
actually hinges on: the key a push is logged under, and the day a payload
filename maps back to. Both are silent when wrong -- a mismatched key re-sends
45 days every night, a mismatched filename logs the wrong date.
"""
from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("odd_models")

from integrations.odd.push import _day_of, _target  # noqa: E402


def test_the_same_platform_is_one_target_however_the_url_is_typed():
    assert _target("http://localhost:8080/") == _target("http://localhost:8080")


def test_building_files_is_not_pushing_to_a_platform():
    """--out on its own must not consume the state of a real ODD instance."""
    assert _target(None) != _target("http://localhost:8080")


def test_run_filenames_round_trip_to_their_day():
    day = date(2026, 9, 5)
    assert _day_of(f"runs_{day}.json") == day


def test_the_catalog_is_not_a_day():
    """It is re-sent on every push, so logging it would poison the log."""
    assert _day_of("00_catalog.json") is None
