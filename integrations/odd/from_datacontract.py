"""Push `datacontract test` results into ODD.

The checks themselves are not ours: datacontract-cli derives them from an ODCS
contract, compiles them to SQL and runs them inside the source database, then
writes JSON with a `diagnostics` block per check carrying `failed_rows` and
`row_count`. This module is the only part that has to exist -- the translation
from that JSON to the DataEntity shapes ODD ingests.

What it buys is the reason to bother: the tests attach to the *same* dataset
ODDRN odd-collector minted for the table, and odd-collector's Superset adapter
attaches the charts to that ODDRN too. So a failing check has a blast radius,
and ODD can name the dashboards.

    datacontract test contracts/erp_mssql.odcs.yaml \\
        --output artifacts/mssql-results.json --output-format json
    python integrations/odd/from_datacontract.py \\
        contracts/erp_mssql.odcs.yaml artifacts/mssql-results.json \\
        --url http://odd-platform:8080
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from odd_models.models import (DataEntity, DataEntityList, DataEntityType,
                               DataQualityTest, DataQualityTestExpectation,
                               DataQualityTestExpectationCategory,
                               DataQualityTestRun, MetadataExtension,
                               QualityRunStatus, Tag)
from oddrn_generator import (MssqlGenerator, MysqlGenerator,
                             PostgresqlGenerator)

from integrations.odd.mapper import (SCHEMA_URL, ContractGenerator,
                                     datasource_oddrn, entity_list)

HOST = os.getenv("DQ_HOST", "dq.local")
DATASOURCE_NAME = os.getenv("ODD_DATASOURCE_NAME", "datafletch-contracts")

# The generator has to be the one odd-collector uses for that source, or the
# tests land on a dataset ODDRN nobody else refers to.
_GENERATORS = {
    "sqlserver": MssqlGenerator, "mssql": MssqlGenerator,
    "postgres": PostgresqlGenerator, "postgresql": PostgresqlGenerator,
    "mysql": MysqlGenerator,
}

_STATUS = {
    "passed": QualityRunStatus.SUCCESS,
    "failed": QualityRunStatus.FAILED,
    "warning": QualityRunStatus.FAILED,
}

# datacontract-cli reports a dimension per check; ODD buckets by category and
# counts an uncategorised test as zero on its Data Quality dashboard.
# ODD offers ASSERTION, VOLUME_ANOMALY, FRESHNESS_ANOMALY,
# COLUMN_VALUES_ANOMALY and SCHEMA_CHANGE. Only timeliness has an honest
# non-assertion home; the rest of datacontract's dimensions describe what a
# deterministic check asserts, not an anomaly someone detected.
_CATEGORY = {"timeliness": DataQualityTestExpectationCategory.FRESHNESS_ANOMALY}


def _json(url: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        url, method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read() or b"{}")


def ensure_datasource(url: str) -> str:
    """Register our data source, without which ODD refuses every ingestion.

    ODD only accepts a DataEntityList whose ``data_source_oddrn`` it already
    knows; an unknown one is a 404 ``USR002`` before any entity is looked at.
    Collectors register themselves at startup, guarded by a filter that is
    always on. We are not a collector, so this is our equivalent -- idempotent,
    so it can sit in the daily cron path.
    """
    base = url.rstrip("/")
    oddrn = datasource_oddrn(HOST)
    known = _json(f"{base}/api/datasources?page=1&size=1000").get("items", [])
    if any(d.get("oddrn") == oddrn for d in known):
        return oddrn
    try:
        _json(f"{base}/api/datasources", {
            "name": DATASOURCE_NAME, "oddrn": oddrn,
            "description": "Contract-derived data quality checks"})
        print(f"registered data source {oddrn} as {DATASOURCE_NAME!r}")
    except urllib.error.HTTPError as e:
        raise SystemExit(
            f"could not register data source {oddrn}: {e.code} "
            f"{e.read()[:200]!r} (ODD_DATASOURCE_NAME={DATASOURCE_NAME})") from e
    return oddrn


def post(url: str, body: dict) -> int:
    req = urllib.request.Request(
        url.rstrip("/") + "/ingestion/entities",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status


def dataset_oddrn(contract: dict, server_key: str | None = None) -> str:
    servers = contract.get("servers") or []
    server = next((s for s in servers if server_key in (None, s.get("server"))), None)
    if server is None:
        raise SystemExit("contract has no servers block")
    gen = _GENERATORS.get(str(server.get("type", "")).lower())
    if gen is None:
        raise SystemExit(f"no ODDRN generator for server type {server.get('type')!r}")
    schema = contract["schema"][0]
    table = schema.get("physicalName") or schema["name"]
    g = gen(host_settings=server["host"],
            databases=server["database"],
            schemas=server.get("schema", "dbo"),
            tables=table)
    return g.get_oddrn_by_path("tables")


def _reason(check: dict) -> str:
    """The counts, in the one field ODD's run model has for them."""
    d = check.get("diagnostics") or {}
    failed, rows = d.get("failed_rows"), d.get("row_count")
    if failed is None and d.get("value") is not None:
        failed = d["value"]
    if failed is not None and rows:
        pct = (float(failed) / float(rows)) * 100
        return f"{failed}/{rows} rows failed ({pct:.2f}%) dimension={check.get('dimension')}"
    return check.get("reason") or f"dimension={check.get('dimension')}"


def build(contract: dict, results: dict, ds_oddrn: str) -> list[DataEntity]:
    contract_id = contract.get("id") or contract.get("name", "contract")
    start = datetime.fromisoformat(
        results["timestampStart"].replace("Z", "+00:00")) \
        if results.get("timestampStart") else datetime.now(timezone.utc)
    end = datetime.fromisoformat(
        results["timestampEnd"].replace("Z", "+00:00")) \
        if results.get("timestampEnd") else start + timedelta(seconds=1)
    run_id = results.get("runId") or start.date().isoformat()

    entities: list[DataEntity] = []
    for check in results.get("checks", []):
        # `key` is stable across runs; `id` is a fresh uuid every time, so a
        # test keyed by it would be a new catalog object every night.
        name = check.get("key") or check["name"]
        g = ContractGenerator(host_settings=HOST, contracts=contract_id, checks=name)
        test_oddrn = g.get_oddrn_by_path("checks")

        d = check.get("diagnostics") or {}
        entities.append(DataEntity(
            oddrn=test_oddrn, name=name, type=DataEntityType.JOB,
            description=check.get("name"),
            tags=[Tag(name=f"dimension:{check.get('dimension') or 'unknown'}"),
                  Tag(name=f"category:{check.get('category') or 'unknown'}"),
                  Tag(name=f"contract:{contract_id}")],
            metadata=[MetadataExtension(schema_url=SCHEMA_URL, metadata={
                "engine": check.get("engine") or "datacontract-cli",
                "dimension": check.get("dimension"),
                "category": check.get("category"),
                "metric": d.get("metric"),
                "derived_from_contract": contract_id})],
            data_quality_test=DataQualityTest(
                suite_name=contract_id, dataset_list=[ds_oddrn],
                expectation=DataQualityTestExpectation(
                    type=check.get("type") or "assertion",
                    category=_CATEGORY.get(
                        check.get("dimension"),
                        DataQualityTestExpectationCategory.ASSERTION),
                    **{k: str(v) for k, v in (
                        ("field", check.get("field")),
                        ("dimension", check.get("dimension")),
                        ("threshold", d.get("threshold")),
                        ("implementation", check.get("implementation")),
                    ) if v is not None}))))

        g.set_oddrn_paths(contracts=contract_id, checks=name, runs=str(run_id))
        entities.append(DataEntity(
            oddrn=g.get_oddrn_by_path("runs"),
            name=f"{name}@{run_id}", type=DataEntityType.JOB_RUN,
            data_quality_test_run=DataQualityTestRun(
                data_quality_test_oddrn=test_oddrn,
                start_time=start, end_time=end,
                status=_STATUS.get(check.get("result"), QualityRunStatus.UNKNOWN),
                status_reason=_reason(check))))
    return entities


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("contract", type=Path)
    ap.add_argument("results", type=Path)
    ap.add_argument("--server", help="which server block to resolve the ODDRN from")
    ap.add_argument("--url", help="ODD Platform base url")
    ap.add_argument("--out", type=Path, help="write the payload here as well")
    a = ap.parse_args()

    contract = yaml.safe_load(a.contract.read_text(encoding="utf-8"))
    results = json.loads(a.results.read_text(encoding="utf-8"))
    ds = dataset_oddrn(contract, a.server)

    entities = build(contract, results, ds)
    body = entity_list(entities, HOST).model_dump(mode="json", exclude_none=True)
    failed = sum(1 for c in results.get("checks", []) if c.get("result") != "passed")
    print(f"{len(results.get('checks', []))} checks ({failed} failing) -> {ds}")

    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(body, indent=2))
        print(f"payload -> {a.out}")

    if a.url:
        ensure_datasource(a.url)
        try:
            print(f"POST {len(body['items'])} entities -> {post(a.url, body)}")
        except urllib.error.HTTPError as e:
            raise SystemExit(f"ingest failed: {e.code} {e.read()[:300]!r}") from e


if __name__ == "__main__":
    main()
