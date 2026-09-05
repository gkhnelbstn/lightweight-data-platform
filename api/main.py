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

import os
import subprocess
import tempfile
from hmac import compare_digest
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from psycopg.rows import dict_row
from pydantic import BaseModel

from core import store
from core.runner import (CONTRACTS, DAILY_SERVER, ROOT,  # noqa: F401
                         TABLE_SCOPED_TYPES, load_contracts, run)
from core.scoring import DIMENSION_WEIGHT

app = FastAPI(title="Contract-driven data quality on ODD")

DIMENSIONS = sorted(DIMENSION_WEIGHT)

# The two write routes compile a person's SQL and run it against the source.
# That is the one thing here worth a door, so it fails closed: with no token
# configured they refuse rather than run. Reads are open, and the compose says
# to keep the whole thing on a private network either way.
API_TOKEN = os.getenv("DQ_API_TOKEN") or ""


def authorised(authorization: str = Header(default="")) -> None:
    if not API_TOKEN:
        raise HTTPException(
            503, "DQ_API_TOKEN is not set; rule authoring is disabled")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not compare_digest(token, API_TOKEN):
        raise HTTPException(401, "bad or missing bearer token")


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
               r.total_rows, r.run_at, r.name, r.check_type, r.field, r.reason
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
               failed_rows, total_rows, run_at, name, check_type, field,
               reason, sql
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


def _source_conn(server: dict):
    """A connection to the source, as the role datacontract itself uses.

    Not ERP_DSN: that is the owner, and this runs a rewritten version of a
    rule someone wrote in the UI. `dq_reader` can only SELECT and gives up
    after 60 seconds -- see deploy/db-init.sql.
    """
    kind = server.get("type")
    if kind in ("postgres", "postgresql"):
        return psycopg.connect(
            host=server["host"], port=server.get("port", 5432),
            dbname=server["database"],
            # The statement is unqualified -- `from sales_orders` -- exactly as
            # datacontract compiled it, so the schema has to arrive the same
            # way datacontract supplies it: on the connection.
            options=f"-csearch_path={server.get('schema', 'public')}",
            user=os.getenv("DATACONTRACT_POSTGRES_USERNAME", "postgres"),
            password=os.getenv("DATACONTRACT_POSTGRES_PASSWORD", ""))
    if kind in ("sqlserver", "mssql"):
        import pyodbc
        driver = os.getenv("DATACONTRACT_SQLSERVER_DRIVER",
                           "ODBC Driver 18 for SQL Server")
        return pyodbc.connect(
            f"DRIVER={{{driver}}};SERVER={server['host']},"
            f"{server.get('port', 1433)};DATABASE={server['database']};"
            f"UID={os.getenv('DATACONTRACT_SQLSERVER_USERNAME', 'sa')};"
            f"PWD={os.getenv('DATACONTRACT_SQLSERVER_PASSWORD', '')};"
            "TrustServerCertificate=yes;Encrypt=no", timeout=30)
    raise HTTPException(400, f"no sampler wired up for a {kind} server")


@app.get("/api/checks/{check_id}/sample")
def check_sample(check_id: str) -> dict:
    """The rows behind a failed check.

    "150 orders disagree with their lines" is where every investigation
    starts and none of them end. This is the same statement the check ran,
    rewritten to return what it counted -- see core/sample.py.

    Columns the contract classifies are masked. The classification is the
    contract's, so marking a column in the .yaml is enough to keep it out of
    here and out of anything else that reads the contract.
    """
    from core import sample

    rows = q("""select check_id, contract_id, check_type, field, sql, name,
                       reason, failed_rows, run_window, run_at
                from check_results where check_id = %s
                order by run_at desc limit 1""", (check_id,))
    if not rows:
        raise HTTPException(404, f"no result stored for {check_id}")
    check = rows[0]

    doc = yaml.safe_load(
        _contract_file(check["contract_id"]).read_text(encoding="utf-8"))
    # The rows have to come from the same window the result did, or the count
    # in the UI and the rows under it disagree. An incremental result was
    # measured against the day's views -- except for the table-level
    # invariants, which core/runner.py deliberately re-runs unwindowed.
    windowed = (check["run_window"] == "incremental"
                and check["check_type"] not in TABLE_SCOPED_TYPES)
    server = next((s for s in doc.get("servers", [])
                   if s.get("server") == (DAILY_SERVER if windowed else "erp")),
                  None) or doc["servers"][0]
    key = check_id[len(check["contract_id"]) + 1:]
    model = next((m for m in doc.get("schema", [])
                  if key.startswith(m["name"])), doc["schema"][0])
    table = model.get("physicalName") or model["name"]
    if server.get("schema") and server.get("type") in ("sqlserver", "mssql"):
        table = f"{server['schema']}.{table}"

    statement = sample.rows_query(check, table, server.get("type"))
    if statement is None:
        return {"check_id": check_id, "name": check["name"], "sql": check["sql"],
                "reason": check["reason"], "failed_rows": check["failed_rows"],
                "run_at": str(check["run_at"]), "scope": server["server"],
                "rows": [], "columns": [], "masked": [],
                "note": "Bu kontrolun gosterilecek satiri yok: butun tabloyu "
                        "toplayan bir kural (ornegin tazelik), hatanin kendisi "
                        "satirin yoklugu."}

    hidden = sample.classified(doc)
    with _source_conn(server) as cx:
        cur = cx.execute(statement)
        columns = [d[0] for d in cur.description]
        data = [[sample.MASK if c in hidden else _plain(v)
                 for c, v in zip(columns, row)] for row in cur.fetchall()]
    return {"check_id": check_id, "name": check["name"],
            "reason": check["reason"], "failed_rows": check["failed_rows"],
            "run_at": str(check["run_at"]), "scope": server["server"],
            "sql": statement, "columns": columns, "rows": data,
            "masked": sorted(hidden & set(columns))}


def _plain(v):
    """psycopg hands back dates and Decimals; the browser wants strings."""
    return v if v is None or isinstance(v, (int, float, str, bool)) else str(v)


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


@app.post("/api/rules/preview", dependencies=[Depends(authorised)])
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


@app.post("/api/rules", dependencies=[Depends(authorised)])
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
