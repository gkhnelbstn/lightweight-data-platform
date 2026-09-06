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


# --- a source you cannot reach is not bad data ------------------------------

def test_errored_checks_are_not_counted_as_failures():
    """An unreachable source used to score near zero, which reads as "the data
    is terrible" when the truth is "we could not look" -- and the two want
    different people. The SLA still breaks; core/store.py does that."""
    from core.scoring import score
    good = [{"status": "pass", "dimension": "completeness", "fail_ratio": 0.0}]
    assert score(good) == 1.0
    assert score(good + [{"status": "error", "dimension": "unknown",
                          "fail_ratio": 0.0}]) == 1.0


def test_a_run_where_nothing_could_be_measured_is_not_a_failure_either():
    from core.scoring import score
    assert score([{"status": "error", "dimension": None, "fail_ratio": 0.0}]) == 1.0


def test_the_sla_is_what_an_outage_breaks():
    """Not the score: the score is about data. sla_met is about the run."""
    import inspect
    from core import store
    src = inspect.getsource(store.write_score)
    assert "errored == 0" in src
