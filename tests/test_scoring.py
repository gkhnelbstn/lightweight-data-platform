from core.scoring import score


def _r(sev: str, status: str, ratio: float = 0.0) -> dict:
    return {"dimension": sev, "status": status, "fail_ratio": ratio}


def test_all_passing_is_one():
    assert score([_r("completeness", "pass"), _r("unknown", "pass")]) == 1.0


def test_empty_is_one():
    assert score([]) == 1.0


def test_severity_is_weighted():
    critical = score([_r("completeness", "fail", 0.5), _r("unknown", "pass")])
    minor = score([_r("unknown", "fail", 0.5), _r("completeness", "pass")])
    assert critical < minor


def test_a_single_bad_row_still_moves_the_score():
    """The reason the score is not a pure row ratio: one bad row in a large
    table would otherwise read as 0.999999 and never breach an SLA."""
    assert score([_r("completeness", "fail", 0.000001)]) < 0.5


def test_volume_still_matters():
    small = score([_r("accuracy", "fail", 0.01), _r("accuracy", "pass")])
    large = score([_r("accuracy", "fail", 0.90), _r("accuracy", "pass")])
    assert small > large
