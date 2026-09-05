"""Weighted quality score for one contract on one run date.

Two components, deliberately:
  * incident term  -- did the check pass at all (binary, weighted)
  * volume term    -- how much of the data was affected (1 - fail_ratio)

A pure volume score is what most home-grown dashboards use, and it is close to
useless: one bad row in a million still reads as 0.999999, so nothing ever
breaches SLA. A pure binary score is the opposite -- it cannot tell a typo from
an outage. The blend keeps both signals.
"""
from __future__ import annotations

INCIDENT_WEIGHT = 0.6
VOLUME_WEIGHT = 0.4

# ODCS labels a rule with a quality *dimension* -- what kind of wrong it is --
# rather than a severity, which is what it costs. The contract stays
# vendor-neutral and the cost is an operator decision, so the weights live here.
#
# The ordering is the argument: a row that should exist and does not, or a key
# that is not unique, breaks a join and therefore everything downstream of it.
# A value outside its allowed set is wrong but usually still joins. `unknown`
# is deliberately the lightest -- an unclassified check should not dominate a
# score by accident.
DIMENSION_WEIGHT: dict[str, int] = {
    "completeness": 5,   # rows or values missing
    "uniqueness": 5,     # duplicate keys
    "consistency": 5,    # referential integrity, cross-table agreement
    "timeliness": 5,     # the data did not arrive
    "accuracy": 3,       # values disagree with a computation
    "conformity": 3,     # values outside a declared set or range
    "coverage": 3,
    "schema": 3,         # datacontract's category for structural checks
    "unknown": 1,
}
DEFAULT_WEIGHT = 3


def score(results: list[dict]) -> float:
    if not results:
        return 1.0
    inc_num = vol_num = den = 0.0
    for r in results:
        w = DIMENSION_WEIGHT.get(r.get("dimension") or "unknown", DEFAULT_WEIGHT)
        inc_num += w * (1.0 if r["status"] == "pass" else 0.0)
        vol_num += w * (1.0 - float(r["fail_ratio"]))
        den += w
    if not den:
        return 1.0
    return round(INCIDENT_WEIGHT * (inc_num / den)
                 + VOLUME_WEIGHT * (vol_num / den), 4)
