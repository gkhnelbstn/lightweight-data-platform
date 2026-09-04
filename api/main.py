"""Read/write API over the contract + results store.

Deliberately thin: it serves what the runner already computed and lets a data
analyst author a rule against the contract without touching the repo.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from psycopg.rows import dict_row
from pydantic import BaseModel

from core import store
from core.checks import derive
from core.compilers.sql import compile_sql, render_scope
from core.contract import DataContract
from core.runner import ROOT, register, run

app = FastAPI(title="Contract-driven data quality (spike)")


def _block_str(dumper, data):
    """Keep multi-line SQL readable when the UI writes back to the contract."""
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


class ContractDumper(yaml.SafeDumper):
    pass


ContractDumper.add_representer(str, _block_str)


def q(sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
    with psycopg.connect(store.DQ_DSN, row_factory=dict_row) as cx:
        return cx.execute(sql, params).fetchall()


@app.get("/api/overview")
def overview() -> dict:
    rows = q("""select run_at, round(avg(score), 4) as score
                from contract_scores where run_window = 'incremental'
                group by run_at order by run_at""")
    contracts = q("""
        select c.id, c.title, c.owner, c.domain, c.source_table, c.min_score,
               s.score, s.checks_total, s.checks_failed, s.sla_met, s.run_at
        from contracts c
        left join lateral (
          select * from contract_scores cs
          where cs.contract_id = c.id and cs.run_window = 'incremental'
          order by run_at desc limit 1) s on true
        order by c.id""")
    open_failures = q("""
        select r.check_id, r.contract_id, r.severity, r.failed_rows,
               r.total_rows, ch.description
        from check_results r join checks ch on ch.id = r.check_id
        where r.run_window = 'incremental' and r.status = 'fail'
          and r.run_at = (select max(run_at) from check_results
                          where run_window = 'incremental')
        order by case r.severity when 'critical' then 0 when 'major' then 1
                 else 2 end, r.failed_rows desc""")
    return {"trend": rows, "contracts": contracts, "open_failures": open_failures}


@app.get("/api/contracts/{contract_id}")
def contract_detail(contract_id: str) -> dict:
    c = q("select * from contracts where id = %s", (contract_id,))
    if not c:
        raise HTTPException(404, "unknown contract")
    checks = q("""
        select ch.*, r.status, r.failed_rows, r.total_rows, r.fail_ratio,
               r.run_at, r.duration_ms
        from checks ch
        left join lateral (
          select * from check_results cr
          where cr.check_id = ch.id and cr.run_window = 'incremental'
          order by run_at desc limit 1) r on true
        where ch.contract_id = %s
        order by case ch.severity when 'critical' then 0 when 'major' then 1
                 else 2 end, ch.id""", (contract_id,))
    trend = q("""select run_at, score, checks_failed, checks_total, sla_met
                 from contract_scores
                 where contract_id = %s and run_window = 'incremental'
                 order by run_at""", (contract_id,))
    history = q("""select check_id, run_at, status, fail_ratio
                   from check_results
                   where contract_id = %s and run_window = 'incremental'
                   order by run_at""", (contract_id,))
    return {"contract": c[0], "checks": checks, "trend": trend,
            "history": history}


@app.get("/api/checks/{check_id}/history")
def check_history(check_id: str) -> list[dict]:
    return q("""select run_at, status, failed_rows, total_rows, fail_ratio
                from check_results where check_id = %s
                  and run_window = 'incremental'
                order by run_at""", (check_id,))


@app.get("/api/artifacts/{contract_id}/{kind}", response_class=PlainTextResponse)
def artifact(contract_id: str, kind: str) -> str:
    slug = contract_id.replace(".", "_")
    name = {"dbt": f"{slug}.dbt.schema.yml", "gx": f"{slug}.gx.suite.json"}.get(kind)
    if not name:
        raise HTTPException(404, "kind must be dbt or gx")
    p = ROOT / "artifacts" / name
    if not p.exists():
        raise HTTPException(404, "artifact not generated yet")
    return p.read_text()


class RuleDraft(BaseModel):
    contract_id: str
    id: str
    severity: str = "major"
    description: str = ""
    sql: str


@app.post("/api/rules/preview")
def preview_rule(draft: RuleDraft) -> dict:
    """What an analyst gets before saving: does the SQL parse, does it run, and
    how many rows would it have failed on the last N days."""
    c = q("select spec from contracts where id = %s", (draft.contract_id,))
    if not c:
        raise HTTPException(404, "unknown contract")
    contract = DataContract.model_validate(c[0]["spec"])
    la = contract.server.loaded_at_column
    sql = render_scope(draft.sql, la, "incremental").replace(":as_of", "%(as_of)s")
    end = date.today()
    series, err = [], None
    try:
        with psycopg.connect(store.ERP_DSN) as erp:
            for i in range(13, -1, -1):
                d = end - timedelta(days=i)
                row = erp.execute(sql, {"as_of": d}).fetchone()
                series.append({"run_at": str(d), "failed_rows": int(row[0] or 0)})
    except Exception as exc:
        err = str(exc).strip().splitlines()[0]
    return {"ok": err is None, "error": err, "compiled_sql": sql,
            "preview": series}


@app.post("/api/rules")
def save_rule(draft: RuleDraft) -> dict:
    """Append the rule to the contract file itself. The contract stays the single
    source of truth -- the UI is an editor for it, not a second store."""
    prev = preview_rule(draft)
    if not prev["ok"]:
        raise HTTPException(400, prev["error"] or "invalid rule")
    path = next((p for p in (ROOT / "contracts").glob("*.contract.yaml")
                 if yaml.safe_load(p.read_text())["id"] == draft.contract_id), None)
    if path is None:
        raise HTTPException(404, "contract file not found")
    doc = yaml.safe_load(path.read_text())
    doc.setdefault("quality", [])
    doc["quality"] = [r for r in doc["quality"] if r["id"] != draft.id]
    doc["quality"].append({"id": draft.id, "type": "custom_sql",
                           "severity": draft.severity,
                           "description": draft.description,
                           "sql": draft.sql})
    path.write_text(yaml.dump(doc, Dumper=ContractDumper, sort_keys=False,
                              allow_unicode=True, width=100))
    register()
    results = run(date.today())
    return {"saved": draft.id, "file": path.name, "reran": results}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "web" / "index.html")
