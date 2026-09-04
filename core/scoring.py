"""Severity-weighted quality score for one contract on one run date.

Two components, deliberately:
  * incident term  -- did the check pass at all (binary, severity weighted)
  * volume term    -- how much of the data was affected (1 - fail_ratio)

A pure volume score is what most home-grown dashboards use, and it is close to
useless: one bad row in a million still reads as 0.999999, so nothing ever
breaches SLA. A pure binary score is the opposite -- it cannot tell a typo from
an outage. The blend keeps both signals.
"""
from __future__ import annotations

from core.contract import SEVERITY_WEIGHT

INCIDENT_WEIGHT = 0.6
VOLUME_WEIGHT = 0.4


def score(results: list[dict]) -> float:
    if not results:
        return 1.0
    inc_num = vol_num = den = 0.0
    for r in results:
        w = SEVERITY_WEIGHT[r["severity"]]
        inc_num += w * (1.0 if r["status"] == "pass" else 0.0)
        vol_num += w * (1.0 - float(r["fail_ratio"]))
        den += w
    if not den:
        return 1.0
    return round(INCIDENT_WEIGHT * (inc_num / den)
                 + VOLUME_WEIGHT * (vol_num / den), 4)
