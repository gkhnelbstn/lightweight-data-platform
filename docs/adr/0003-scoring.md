# 0003 — A dimension-weighted score, and an outage is not bad data

## Context

ODD divides passing tests by total tests and calls it a score. A missing
foreign key and a cosmetic rule then cost the same, and one bad row weighs as
much as four thousand.

## Decision

`0.6 × weighted pass/fail + 0.4 × weighted (1 − fail_ratio)`, weighted by the
ODCS quality **dimension**.

* A pure volume score is what most home-grown dashboards use and is close to
  useless: one bad row in a million reads as 0.999999 and nothing ever breaches
  an SLA.
* A pure binary score cannot tell a typo from an outage.
* Dimension rather than severity: ODCS labels a rule with *what kind of wrong*
  it is; what that **costs** is an operator decision, so the weights live in
  `core/scoring.py` and the contract stays vendor-neutral. Completeness,
  uniqueness, consistency and timeliness break joins, so they cost more.

**Checks that errored are excluded from the score.** An unreachable source used
to score near zero, which reads as "the data is terrible" when the truth is
"we could not look" — and the two want different people. `sla_met` therefore
also requires the run to have run, and `checks_errored` is stored and shown
separately.

`datacontract`'s `general` rollup is not a check. When a source cannot be
reached it is the *only* thing returned, with `result: failed` and an ODBC
error for a reason; storing that as a data failure is how an outage came to
look like bad data.

## Consequences

* Both halves of the blend have to be real. `datacontract test` reports
  `row_count` only for the checks it derives, so a custom SQL rule arrives with
  a denominator of zero — which silently deletes the volume half. `core/runner.py`
  counts the table once per run, in the window the checks ran in.
* A dimension the weights do not know scores as `unknown`, the lightest weight.
  `tests/test_contracts.py` refuses a rule with no dimension for that reason.

## On upgrade

* Adding a dimension: `DIMENSION_WEIGHT` in `core/scoring.py`, and nothing
  else. The ODD expectation category in
  `integrations/odd/from_datacontract.py` may need a branch.
* If `datacontract` starts returning `row_count` for custom SQL rules,
  `table_rows()` in `core/runner.py` becomes dead code — delete it rather than
  leaving two denominators.
* If ODD's own score becomes weightable, this is worth re-reading. It was not
  at 0.29.0.
