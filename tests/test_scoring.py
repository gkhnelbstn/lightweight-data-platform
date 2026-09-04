from core.scoring import score


def _r(sev: str, status: str, ratio: float = 0.0) -> dict:
    return {"severity": sev, "status": status, "fail_ratio": ratio}


def test_all_passing_is_one():
    assert score([_r("critical", "pass"), _r("minor", "pass")]) == 1.0


def test_empty_is_one():
    assert score([]) == 1.0


def test_severity_is_weighted():
    critical = score([_r("critical", "fail", 0.5), _r("minor", "pass")])
    minor = score([_r("minor", "fail", 0.5), _r("critical", "pass")])
    assert critical < minor


def test_a_single_bad_row_still_moves_the_score():
    """The reason the score is not a pure row ratio: one bad row in a large
    table would otherwise read as 0.999999 and never breach an SLA."""
    assert score([_r("critical", "fail", 0.000001)]) < 0.5


def test_volume_still_matters():
    small = score([_r("major", "fail", 0.01), _r("major", "pass")])
    large = score([_r("major", "fail", 0.90), _r("major", "pass")])
    assert small > large
