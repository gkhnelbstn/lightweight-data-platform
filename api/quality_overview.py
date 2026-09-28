"""The quality half of the Data Quality page's overview. #145

The Platform overview tab used to show ODD's own donuts and nothing else:
how many tests passed, counted from everything ingested. The questions the
page is opened with were not there -- which contracts are below their SLA,
is it getting better, which kind of wrong is it, and has this been failing
for a day or for a month. Every answer was already stored, in
`contract_scores` and `check_results`; this puts them in one response.

The score shown is the stored one. Errored checks stay out of it and are
counted on their own (invariant 5); an *accepted* failure is left out of the
aging, because accepting it is a decision to live with it (#29).
"""
from __future__ import annotations

from datetime import date
from typing import Any

import psycopg
from fastapi import APIRouter
from psycopg.rows import dict_row

from core import store
from core.runner import load_contracts
from core.scoring import DEFAULT_WEIGHT, DIMENSION_WEIGHT

router = APIRouter()

TREND_DAYS = 30
AGES = (("new", 1), ("week", 7), ("month", 30), ("older", None))


def _q(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with psycopg.connect(store.DQ_DSN, row_factory=dict_row) as cx:
        return cx.execute(sql, params).fetchall()


def age_bucket(since: date, latest: date) -> str:
    """How long a failure has lasted, in the buckets a person triages by."""
    days = (latest - since).days + 1
    for name, limit in AGES:
        if limit is None or days <= limit:
            return name
    return "older"


def aging(failures: list[dict], latest: date) -> dict[str, int]:
    """Open failures by how long each has been failing without a pass."""
    out = {name: 0 for name, _ in AGES}
    for f in failures:
        if f["state"] != "accepted" and f["since"]:
            out[age_bucket(f["since"], latest)] += 1
    return out


def domain_trend(scores: list[dict], domain_of: dict[str, str]) -> list[dict]:
    """Each domain's mean score per run date, oldest first."""
    days: dict[str, dict[date, list[float]]] = {}
    for s in scores:
        domain = domain_of.get(s["contract_id"]) or "—"
        days.setdefault(domain, {}).setdefault(s["run_at"], []).append(float(s["score"]))
    return [{"domain": d, "points": [{"run_at": day, "score": round(sum(v) / len(v), 4)}
                                     for day, v in sorted(by_day.items())]}
            for d, by_day in sorted(days.items())]


def contract_rows(contracts: list[dict], latest: dict, previous: dict) -> list[dict]:
    """One row per contract, worst first; a contract never run sorts last."""
    rows = []
    for c in contracts:
        now, before = latest.get(c["id"]), previous.get(c["id"])
        rows.append({
            "id": c["id"], "title": c.get("name") or c["id"], "domain": c.get("domain"),
            "score": float(now["score"]) if now else None,
            "previous": float(before["score"]) if before else None,
            "sla_min": float(now["sla_min"]) if now and now["sla_min"] is not None else None,
            "sla_met": now["sla_met"] if now else None,
            "checks_total": now["checks_total"] if now else 0,
            "checks_failed": now["checks_failed"] if now else 0,
            "checks_errored": (now or {}).get("checks_errored") or 0,
            "run_at": now["run_at"] if now else None,
        })
    return sorted(rows, key=lambda r: (r["score"] is None, r["score"] or 0))


@router.get("/api/overview/quality")
def quality_overview() -> dict:
    scores = _q("""select contract_id, run_at, score, checks_total, checks_failed,
                          checks_errored, sla_met, sla_min
                     from contract_scores
                    where run_window = 'incremental'
                      and run_at > current_date - %s
                    order by contract_id, run_at desc""", (TREND_DAYS,))
    latest: dict[str, dict] = {}
    previous: dict[str, dict] = {}
    for s in scores:
        if s["contract_id"] not in latest:
            latest[s["contract_id"]] = s
        elif s["contract_id"] not in previous:
            previous[s["contract_id"]] = s

    failures = _q("""
        with last as (select contract_id, max(run_at) as run_at from check_results
                       where run_window = 'incremental' group by contract_id),
             cur as (select r.* from check_results r
                       join last l on l.contract_id = r.contract_id and l.run_at = r.run_at
                      where r.run_window = 'incremental')
        select c.check_id, c.contract_id, c.dimension, c.status, c.failed_rows,
               c.total_rows, c.name, c.run_at, coalesce(s.state, 'open') as state,
               (select min(x.run_at) from check_results x
                 where x.check_id = c.check_id and x.run_window = 'incremental'
                   and x.status = 'fail'
                   and x.run_at > coalesce((select max(p.run_at) from check_results p
                                             where p.check_id = c.check_id
                                               and p.run_window = 'incremental'
                                               and p.status = 'pass'), '0001-01-01')
               ) as since
          from cur c left join check_status s on s.check_id = c.check_id""")

    contracts = load_contracts()
    as_of = max((f["run_at"] for f in failures), default=None)
    failing = [f for f in failures if f["status"] == "fail"]
    dims: dict[str, dict] = {}
    for f in failures:
        d = dims.setdefault(f["dimension"], {"total": 0, "failing": 0, "errored": 0})
        d["total"] += 1
        d["failing"] += f["status"] == "fail"
        d["errored"] += f["status"] == "error"

    return {
        "as_of": as_of,
        "kpis": {
            "contracts": len(contracts),
            "at_sla": sum(1 for r in latest.values() if r["sla_met"]),
            "checks": len(failures),
            "failing": len(failing),
            "errored": sum(1 for f in failures if f["status"] == "error"),
            "newly_failing": sum(1 for f in failing if f["since"] == f["run_at"]
                                 and f["state"] != "accepted"),
            "accepted": sum(1 for f in failing if f["state"] == "accepted"),
        },
        "contracts": contract_rows(contracts, latest, previous),
        "dimensions": [{"dimension": k, "weight": DIMENSION_WEIGHT.get(k, DEFAULT_WEIGHT), **v}
                       for k, v in sorted(dims.items(), key=lambda kv: -kv[1]["failing"])],
        "aging": aging(failing, as_of) if as_of else {n: 0 for n, _ in AGES},
        "domains": domain_trend(scores, {c["id"]: c.get("domain") for c in contracts}),
        "worst": sorted(({k: f[k] for k in ("check_id", "contract_id", "dimension", "name",
                                            "failed_rows", "total_rows", "since", "state")}
                         for f in failing), key=lambda f: -f["failed_rows"])[:8],
    }
