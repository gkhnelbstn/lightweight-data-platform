"""Build ODD ingestion payloads from the spike store and (optionally) POST them.

    python3 integrations/odd/push.py --out artifacts/odd            # build + validate
    python3 integrations/odd/push.py --url http://localhost:8080    # and ingest

Every payload is validated against odd-models before it leaves this process, so
a broken mapping fails here rather than as a 400 from the platform.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from core import store
from core.checks import derive
from core.contract import load_all
from integrations.odd.mapper import (check_entity, dataset_entity, entity_list,
                                     run_entity)

ROOT = Path(__file__).resolve().parents[2]
HOST = os.getenv("DQ_HOST", "dq.local")


def build() -> dict[str, list]:
    contracts = load_all(str(ROOT / "contracts"))
    datasets, checks, runs_by_day = [], [], defaultdict(list)

    with psycopg.connect(store.DQ_DSN, row_factory=dict_row) as cx:
        # Results outlive checks: a rule removed from a contract keeps its
        # history. Only push runs whose check still exists, or ODD gets runs
        # pointing at a JOB oddrn that was never ingested.
        results = cx.execute(
            """select r.run_at, r.check_id, r.contract_id, r.severity, r.status,
                      r.failed_rows, r.total_rows, r.fail_ratio, r.duration_ms
               from check_results r
               join checks c on c.id = r.check_id
               where r.run_window = 'incremental'
               order by r.run_at""").fetchall()
        orphans = cx.execute(
            """select count(*) as n, count(distinct check_id) as checks
               from check_results r
               where not exists (select 1 from checks c where c.id = r.check_id)"""
        ).fetchone()
    if orphans["n"]:
        print(f"note: {orphans['n']} result rows from {orphans['checks']} retired "
              f"check(s) kept in history, not pushed")

    for c in contracts:
        datasets.append(dataset_entity(store.ERP_DSN, c))
        for ck in derive(c):
            checks.append(check_entity(HOST, store.ERP_DSN, c, ck))

    for r in results:
        name = r["check_id"].replace(r["contract_id"] + ".", "")
        runs_by_day[str(r["run_at"])].append(
            run_entity(HOST, r["contract_id"], name, r))

    return {"datasets": datasets, "checks": checks, "runs": dict(runs_by_day)}


def payloads() -> list[tuple[str, dict]]:
    b = build()
    out = [("00_catalog.json",
            entity_list(b["datasets"] + b["checks"], HOST).model_dump(
                mode="json", exclude_none=True))]
    for day in sorted(b["runs"]):
        out.append((f"runs_{day}.json",
                    entity_list(b["runs"][day], HOST).model_dump(
                        mode="json", exclude_none=True)))
    return out


def post(url: str, body: dict) -> int:
    req = urllib.request.Request(
        url.rstrip("/") + "/ingestion/entities",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "artifacts" / "odd"))
    ap.add_argument("--url", help="ODD Platform base url, e.g. http://localhost:8080")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    files = payloads()
    total = 0
    for name, body in files:
        (out / name).write_text(json.dumps(body, indent=2))
        total += len(body["items"])
    print(f"{len(files)} payloads, {total} entities -> {out}")

    if a.url:
        for name, body in files:
            try:
                code = post(a.url, body)
                print(f"  POST {name:22} {code} ({len(body['items'])} entities)")
            except urllib.error.HTTPError as e:
                print(f"  POST {name:22} {e.code} {e.read()[:200]!r}")
                break


if __name__ == "__main__":
    main()
