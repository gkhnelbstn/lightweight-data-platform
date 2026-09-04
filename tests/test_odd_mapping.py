"""The ODD payloads must validate against odd-models, not just look plausible."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

odd_models = pytest.importorskip("odd_models")
from odd_models.models import DataEntityList, DataEntityType  # noqa: E402

from core.checks import derive  # noqa: E402
from core.contract import load_all  # noqa: E402
from integrations.odd.mapper import (check_entity, dataset_entity,  # noqa: E402
                                     entity_list, run_entity)

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"
DSN = "postgresql://u:p@localhost:5432/erp"
HOST = "dq.test"


def _entities():
    items = []
    for c in load_all(CONTRACTS):
        items.append(dataset_entity(DSN, c))
        for ck in derive(c):
            items.append(check_entity(HOST, DSN, c, ck))
    return items


def test_catalog_payload_validates():
    payload = entity_list(_entities(), HOST)
    assert DataEntityList.model_validate(payload.model_dump(mode="json"))


def test_check_oddrns_do_not_collide():
    jobs = [e for e in _entities() if e.type == DataEntityType.JOB]
    assert len({e.oddrn for e in jobs}) == len(jobs)


def test_tests_point_at_postgres_dataset_oddrns():
    for e in _entities():
        if e.data_quality_test:
            assert all(o.startswith("//postgresql/")
                       for o in e.data_quality_test.dataset_list)


def test_run_links_back_to_its_check():
    contract = load_all(CONTRACTS)[0]
    check = derive(contract)[0]
    name = check.id.replace(contract.id + ".", "")
    run = run_entity(HOST, contract.id, name, {
        "run_at": date(2026, 9, 4), "status": "fail", "failed_rows": 3,
        "total_rows": 100, "fail_ratio": 0.03, "duration_ms": 12,
        "severity": check.severity})
    job = check_entity(HOST, DSN, contract, check)
    assert run.data_quality_test_run.data_quality_test_oddrn == job.oddrn
    assert run.type == DataEntityType.JOB_RUN
    # the volume signal has nowhere else to go in ODD's run model
    assert "3/100 rows failed" in run.data_quality_test_run.status_reason
