"""What we send ODD has to be what ODD and odd-collector already agree on.

ODD matches everything by ODDRN, as strings. The failure mode is silent: a
dataset ODDRN that differs by a port or a host name forks the catalog in two,
the collector's copy holding the schema and ours holding the tests, and nothing
errors. So these tests are mostly about identifiers.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

odd_models = pytest.importorskip("odd_models")
from odd_models.models import (DataEntityList, DataEntityType,  # noqa: E402
                               DataQualityTestExpectationCategory)

from integrations.odd.from_datacontract import (build,  # noqa: E402
                                                dataset_oddrn)
from integrations.odd.mapper import (datasource_oddrn, entity_list,  # noqa: E402
                                     pg_host)

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"
DSN = "postgresql://u:p@db:5432/erp"
HOST = "dq.test"


def _contract(name: str) -> dict:
    return yaml.safe_load((CONTRACTS / name).read_text(encoding="utf-8"))


def _results(checks: list[dict]) -> dict:
    return {"runId": "2026-09-05", "timestampStart": "2026-09-05T03:00:00Z",
            "timestampEnd": "2026-09-05T03:00:01Z", "checks": checks}


def _check(**kw) -> dict:
    base = {"key": "sales_orders__order_id__field_unique",
            "name": "Check that unique field order_id has no duplicate values",
            "category": "schema", "type": "field_unique", "field": "order_id",
            "dimension": "uniqueness", "result": "failed",
            "reason": "Actual duplicate_count(order_id) was 8, expected = 0",
            "diagnostics": {"metric": "duplicate_count", "value": 8,
                            "row_count": 3376, "failed_rows": 16}}
    base.update(kw)
    return base


# --- identifiers ------------------------------------------------------------

def test_dataset_host_carries_no_port():
    """odd-collector builds the host segment from its bare `host:` config. A
    port here forks every table into two catalog objects."""
    oddrn = dataset_oddrn(_contract("erp_postgres.odcs.yaml"), "erp")
    host = oddrn.split("/host/")[1].split("/")[0]
    assert ":" not in host, oddrn


def test_odd_pg_host_overrides_the_dsn(monkeypatch):
    monkeypatch.setenv("ODD_PG_HOST", "erp-db.internal")
    assert pg_host(DSN) == "erp-db.internal"
    monkeypatch.delenv("ODD_PG_HOST")
    assert pg_host(DSN) == "db"


def test_server_type_picks_the_generator():
    """The tests must land on the ODDRN odd-collector's own adapter mints for
    that source, which is a different scheme per database."""
    pg = dataset_oddrn(_contract("erp_postgres.odcs.yaml"), "erp")
    ms = dataset_oddrn(_contract("erp_mssql.odcs.yaml"), "erp")
    assert pg == "//postgresql/host/db/databases/erp/schemas/public/tables/sales_orders"
    assert ms == "//mssql/host/mssql/databases/erp/schemas/dbo/tables/sales_orders"


def test_windowed_server_is_not_used_for_the_oddrn():
    """Checks run over a view of one day, but they are about the table. The
    catalog object -- and everything downstream of it -- is the table."""
    contract = _contract("erp_postgres.odcs.yaml")
    assert "/schemas/public/" in dataset_oddrn(contract, "erp")
    assert "/schemas/asof/" in dataset_oddrn(contract, "erp_daily")


def test_payload_declares_the_datasource_push_registers():
    entities = build(_contract("erp_postgres.odcs.yaml"),
                     _results([_check()]), "//postgresql/host/db/x")
    payload = entity_list(entities, HOST)
    assert payload.data_source_oddrn == datasource_oddrn(HOST)
    assert payload.data_source_oddrn == f"//datafletch/host/{HOST}"


# --- entities ---------------------------------------------------------------

def test_each_check_becomes_a_test_and_a_run_that_links_back():
    ds = "//postgresql/host/db/databases/erp/schemas/public/tables/sales_orders"
    entities = build(_contract("erp_postgres.odcs.yaml"), _results([_check()]), ds)
    jobs = [e for e in entities if e.type == DataEntityType.JOB]
    runs = [e for e in entities if e.type == DataEntityType.JOB_RUN]
    assert len(jobs) == len(runs) == 1
    assert runs[0].data_quality_test_run.data_quality_test_oddrn == jobs[0].oddrn
    assert jobs[0].data_quality_test.dataset_list == [ds]


def test_the_run_carries_the_counts_odd_has_no_field_for():
    """ODD's run model has no numeric column, so the volume signal travels in
    status_reason or not at all."""
    entities = build(_contract("erp_postgres.odcs.yaml"), _results([_check()]),
                     "//postgresql/host/db/x")
    reason = next(e for e in entities
                  if e.type == DataEntityType.JOB_RUN).data_quality_test_run.status_reason
    assert "16/3376" in reason


def test_tests_are_keyed_by_something_stable_across_runs():
    """datacontract gives each check a fresh uuid per run; keying on it would
    make a new catalog object every night."""
    c = _contract("erp_postgres.odcs.yaml")
    a = build(c, _results([_check(id="uuid-1")]), "//x")
    b = build(c, _results([_check(id="uuid-2")]), "//x")
    assert [e.oddrn for e in a if e.type == DataEntityType.JOB] == \
           [e.oddrn for e in b if e.type == DataEntityType.JOB]


def test_every_test_carries_an_expectation_category():
    """An uncategorised test is ingested and counts as zero on ODD's
    platform-wide Data Quality dashboard."""
    entities = build(_contract("erp_postgres.odcs.yaml"),
                     _results([_check(), _check(key="freshness",
                                                dimension="timeliness")]),
                     "//x")
    for e in entities:
        if e.data_quality_test:
            cat = e.data_quality_test.expectation.category
            assert isinstance(cat, DataQualityTestExpectationCategory)


def test_timeliness_is_the_one_non_assertion_category():
    entities = build(_contract("erp_postgres.odcs.yaml"),
                     _results([_check(key="fresh", dimension="timeliness"),
                               _check(key="uniq", dimension="uniqueness")]),
                     "//x")
    by_name = {e.name: e for e in entities if e.data_quality_test}
    assert by_name["fresh"].data_quality_test.expectation.category == \
        DataQualityTestExpectationCategory.FRESHNESS_ANOMALY
    assert by_name["uniq"].data_quality_test.expectation.category == \
        DataQualityTestExpectationCategory.ASSERTION


def test_payload_validates_against_odd_models():
    entities = build(_contract("erp_mssql.odcs.yaml"), _results([_check()]),
                     dataset_oddrn(_contract("erp_mssql.odcs.yaml"), "erp"))
    payload = entity_list(entities, HOST)
    assert DataEntityList.model_validate(payload.model_dump(mode="json"))
