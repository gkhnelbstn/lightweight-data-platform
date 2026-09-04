"""Compile the contracts, run every derived check as of a given date, persist
results and the contract score. This is the scheduled unit -- one call per day
per contract is what produces the trend.
"""
from __future__ import annotations

import argparse
import time
from datetime import date, timedelta
from pathlib import Path

import psycopg

from core.checks import Check, derive
from core.compilers.dbt import compile_dbt
from core.compilers.gx import compile_gx
from core.compilers.sql import compile_sql
from core.contract import DataContract, load_all
from core.scoring import score
from core import store

ROOT = Path(__file__).resolve().parent.parent


def register(contracts_dir: str = str(ROOT / "contracts")) -> list[DataContract]:
    contracts = load_all(contracts_dir)
    with store.connect() as dq:
        store.init(dq)
        for c in contracts:
            checks = derive(c)
            compiled = {ck.id: compile_sql(ck, c) for ck in checks}
            store.upsert_contract(dq, c, checks, compiled)
    return contracts


def emit_artifacts(out: Path = ROOT / "artifacts") -> list[str]:
    """Same contract, other engines. Portability is the anti-lock-in argument."""
    out.mkdir(exist_ok=True)
    written = []
    for c in load_all(str(ROOT / "contracts")):
        checks = derive(c)
        slug = c.id.replace(".", "_")
        (out / f"{slug}.dbt.schema.yml").write_text(compile_dbt(c, checks))
        (out / f"{slug}.gx.suite.json").write_text(compile_gx(c, checks))
        written += [f"{slug}.dbt.schema.yml", f"{slug}.gx.suite.json"]
    return written


def run_one(contract: DataContract, checks: list[Check], as_of: date,
            erp: psycopg.Connection, dq: psycopg.Connection,
            window: str = "incremental") -> dict:
    store.ensure_partition(dq, as_of)
    rows = []
    for ck in checks:
        sql = compile_sql(ck, contract, window)
        t0 = time.perf_counter()
        try:
            failed, total = erp.execute(sql, {"as_of": as_of}).fetchone()
        except Exception as exc:                      # a broken check is a failure
            erp.rollback()
            failed, total = 1, 1
            print(f"  ! {ck.id}: {exc}")
        ms = int((time.perf_counter() - t0) * 1000)
        total = int(total or 0)
        failed = int(failed or 0)
        ratio = (failed / total) if total else 0.0
        rows.append({"check_id": ck.id, "severity": ck.severity,
                     "failed_rows": failed, "total_rows": total,
                     "fail_ratio": round(ratio, 6),
                     "status": "pass" if failed == 0 else "fail",
                     "duration_ms": ms})

    for r in rows:
        dq.execute(
            """insert into check_results (run_at,check_id,contract_id,severity,status,
                   failed_rows,total_rows,fail_ratio,duration_ms,run_window)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               on conflict (run_at,check_id,run_window) do update set
                 status=excluded.status, failed_rows=excluded.failed_rows,
                 total_rows=excluded.total_rows, fail_ratio=excluded.fail_ratio,
                 duration_ms=excluded.duration_ms""",
            (as_of, r["check_id"], contract.id, r["severity"], r["status"],
             r["failed_rows"], r["total_rows"], r["fail_ratio"], r["duration_ms"],
             window))

    s = score(rows)
    failed_n = sum(1 for r in rows if r["status"] == "fail")
    dq.execute(
        """insert into contract_scores (run_at,contract_id,score,checks_total,
               checks_failed,sla_min,sla_met,run_window)
           values (%s,%s,%s,%s,%s,%s,%s,%s)
           on conflict (run_at,contract_id,run_window) do update set
             score=excluded.score, checks_total=excluded.checks_total,
             checks_failed=excluded.checks_failed, sla_met=excluded.sla_met""",
        (as_of, contract.id, s, len(rows), failed_n, contract.sla.min_score,
         s >= float(contract.sla.min_score), window))
    return {"contract": contract.id, "as_of": str(as_of), "score": s,
            "failed": failed_n, "total": len(rows)}


def run(as_of: date, contracts: list[DataContract] | None = None,
        window: str = "incremental") -> list[dict]:
    contracts = contracts or load_all(str(ROOT / "contracts"))
    out = []
    with store.connect(store.ERP_DSN) as erp, store.connect() as dq:
        for c in contracts:
            out.append(run_one(c, derive(c), as_of, erp, dq, window))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=str(date.today()))
    ap.add_argument("--backfill-days", type=int, default=0)
    ap.add_argument("--emit-artifacts", action="store_true")
    ap.add_argument("--window", choices=["incremental", "cumulative"],
                    default="incremental")
    a = ap.parse_args()

    contracts = register()
    if a.emit_artifacts:
        print("artifacts:", ", ".join(emit_artifacts()))

    end = date.fromisoformat(a.as_of)
    days = [end - timedelta(days=i) for i in range(a.backfill_days, -1, -1)]
    for d in days:
        for r in run(d, contracts, a.window):
            flag = "OK " if r["failed"] == 0 else "FAIL"
            print(f"{flag} {r['as_of']} {r['contract']:<20} "
                  f"score={r['score']:.4f} failed={r['failed']}/{r['total']}")


if __name__ == "__main__":
    main()
