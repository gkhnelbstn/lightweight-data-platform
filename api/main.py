"""Read/write API over the ODCS contracts and the results store.

Deliberately thin: it serves what the runner already computed, and lets an
analyst add a rule to a contract without touching the repository. That second
half is the reason it exists at all -- ODD's UI annotates what was ingested and
has no "create test" anywhere in it, and datacontract-cli is a CLI.

A saved rule is appended to the contract's `quality` list as ODCS, which is the
same file `datacontract test` reads. The contract stays the single source of
truth; this is an editor for it, not a second store.
"""
from __future__ import annotations

import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from psycopg.rows import dict_row
from pydantic import BaseModel

from core import store
from core.runner import CONTRACTS, ROOT, load_contracts, run
from core.scoring import DIMENSION_WEIGHT

app = FastAPI(title="Contract-driven data quality on ODD")

DIMENSIONS = sorted(DIMENSION_WEIGHT)


def _block_str(dumper, data):
    """Keep multi-line SQL readable when the UI writes back to the contract."""
    return dumper.represent_scalar("tag:yaml.org,2002:str", data,
                                   style="|" if "\n" in data else None)


class ContractDumper(yaml.SafeDumper):
    pass


ContractDumper.add_representer(str, _block_str)


def q(sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
    with psycopg.connect(store.DQ_DSN, row_factory=dict_row) as cx:
        return cx.execute(sql, params).fetchall()


def _contract_file(contract_id: str) -> Path:
    for path in CONTRACTS.glob("*.odcs.yaml"):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if doc.get("id") == contract_id:
            return path
    raise HTTPException(404, f"no contract with id {contract_id}")


def _summary(contract: dict) -> dict:
    model = (contract.get("schema") or [{}])[0]
    server = next((s for s in contract.get("servers", [])
                   if s.get("server") == "erp"), None) or \
        (contract.get("servers") or [{}])[0]
    return {
        "id": contract.get("id"),
        "title": contract.get("name") or contract.get("id"),
        "owner": contract.get("tenant"),
        "domain": contract.get("domain"),
        "source_table": model.get("physicalName") or model.get("name"),
        "server_type": server.get("type"),
        "rules": len(model.get("quality") or []),
        "properties": len(model.get("properties") or []),
    }


@app.get("/api/overview")
def overview() -> dict:
    trend = q("""select run_at, round(avg(score), 4) as score
                 from contract_scores where run_window = 'incremental'
                 group by run_at order by run_at""")
    latest = {r["contract_id"]: r for r in q("""
        select distinct on (contract_id)
               contract_id, score, checks_total, checks_failed, sla_met,
               sla_min, run_at
        from contract_scores where run_window = 'incremental'
        order by contract_id, run_at desc""")}

    contracts = []
    for c in load_contracts():
        row = _summary(c)
        row.update(latest.get(c.get("id"), {}))
        contracts.append(row)

    failures = q("""
        select r.check_id, r.contract_id, r.dimension, r.failed_rows,
               r.total_rows, r.run_at
        from check_results r
        join (select contract_id, max(run_at) as run_at from check_results
              where run_window = 'incremental' group by contract_id) last
          on last.contract_id = r.contract_id and last.run_at = r.run_at
        where r.run_window = 'incremental' and r.status <> 'pass'
        order by r.failed_rows desc limit 20""")

    return {"trend": trend, "contracts": contracts, "open_failures": failures,
            "dimensions": DIMENSIONS}


@app.get("/api/contracts/{contract_id}")
def contract_detail(contract_id: str) -> dict:
    path = _contract_file(contract_id)
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    model = (doc.get("schema") or [{}])[0]

    checks = q("""
        select distinct on (check_id) check_id, dimension, status,
               failed_rows, total_rows, run_at
        from check_results
        where contract_id = %s and run_window = 'incremental'
        order by check_id, run_at desc""", (contract_id,))
    history = q("""
        select run_at, check_id, status, failed_rows
        from check_results
        where contract_id = %s and run_window = 'incremental'
        order by run_at""", (contract_id,))

    return {"contract": _summary(doc),
            "properties": model.get("properties") or [],
            "rules": model.get("quality") or [],
            "checks": checks, "history": history,
            "file": path.name}


@app.get("/api/checks/{check_id}/history")
def check_history(check_id: str) -> list[dict]:
    return q("""select run_at, status, failed_rows, total_rows, fail_ratio
                from check_results
                where check_id = %s and run_window = 'incremental'
                order by run_at""", (check_id,))


class RuleDraft(BaseModel):
    contract_id: str
    description: str
    query: str
    dimension: str = "conformity"
    must_be: int = 0


def _run_datacontract(path: Path, server: str = "erp") -> dict:
    """One `datacontract test`, returned as its results document."""
    import json
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "r.json"
        subprocess.run(
            ["datacontract", "test", str(path), "--server", server,
             "--output", str(out), "--output-format", "json"],
            capture_output=True, text=True, cwd=str(ROOT))
        if not out.exists():
            raise HTTPException(400, "datacontract could not read the contract")
        return json.loads(out.read_text(encoding="utf-8"))


@app.post("/api/rules/preview")
def preview_rule(draft: RuleDraft) -> dict:
    """Run the rule without saving it.

    The draft is written to a copy of the contract in a temporary directory and
    tested there, so a rule that does not compile never reaches the real file.
    """
    if draft.dimension not in DIMENSION_WEIGHT:
        raise HTTPException(400, f"unknown dimension {draft.dimension!r}")
    path = _contract_file(draft.contract_id)
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    model = doc["schema"][0]
    model.setdefault("quality", []).append(
        {"type": "sql", "description": draft.description,
         "query": draft.query, "mustBe": draft.must_be,
         "dimension": draft.dimension})

    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / path.name
        probe.write_text(yaml.dump(doc, Dumper=ContractDumper, sort_keys=False,
                                   allow_unicode=True, width=100),
                         encoding="utf-8")
        results = _run_datacontract(probe)

    mine = [c for c in results.get("checks", [])
            if c.get("name") == draft.description]
    if not mine:
        return {"ok": False, "error": "the rule produced no check",
                "checks": len(results.get("checks", []))}
    check = mine[0]
    d = check.get("diagnostics") or {}
    return {"ok": check.get("result") != "error",
            "result": check.get("result"),
            "reason": check.get("reason"),
            "failed_rows": d.get("failed_rows", d.get("value")),
            "row_count": d.get("row_count"),
            "compiled_sql": check.get("implementation")}


@app.post("/api/rules")
def save_rule(draft: RuleDraft) -> dict:
    """Append the rule to the contract file, then re-run the contract."""
    prev = preview_rule(draft)
    if not prev["ok"]:
        raise HTTPException(400, prev.get("reason") or "invalid rule")

    path = _contract_file(draft.contract_id)
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    model = doc["schema"][0]
    rules = model.setdefault("quality", [])
    rules[:] = [r for r in rules if r.get("description") != draft.description]
    rules.append({"type": "sql", "description": draft.description,
                  "query": draft.query, "mustBe": draft.must_be,
                  "dimension": draft.dimension})
    path.write_text(yaml.dump(doc, Dumper=ContractDumper, sort_keys=False,
                              allow_unicode=True, width=100), encoding="utf-8")

    doc["_path"] = str(path)
    return {"saved": draft.description, "file": path.name,
            "reran": run(date.today(), [doc])}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "web" / "index.html")
