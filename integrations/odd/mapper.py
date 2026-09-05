"""Map contracts, derived checks and run results onto the ODD specification.

ODD models a quality test as DataEntity(type=JOB) carrying a DataQualityTest,
and each execution as DataEntity(type=JOB_RUN) carrying a DataQualityTestRun --
the same shape odd-dbt and odd-great-expectations produce. Datasets are
addressed by ODDRN, so the tests we push land on the very same catalog objects
ODD's own Postgres collector discovers.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

from odd_models.models import (
    DataEntity, DataEntityList, DataEntityType, DataQualityTest,
    DataQualityTestExpectation, DataQualityTestExpectationCategory,
    DataQualityTestRun, DataSet, DataSetField, DataSetFieldType,
    MetadataExtension, QualityRunStatus, Tag, Type,
)
from oddrn_generator import Generator, PostgresqlGenerator
from oddrn_generator.path_models import BasePathsModel, DependenciesMap
from oddrn_generator.server_models import HostnameModel
from pydantic import Field

from core.checks import Check
from core.contract import DataContract

SCHEMA_URL = ("https://raw.githubusercontent.com/opendatadiscovery/opendatadiscovery-specification"
              "/main/specification/extensions/datafletch.json#/definitions/Contract")

_PG_TYPE = {
    "bigint": Type.TYPE_INTEGER, "integer": Type.TYPE_INTEGER,
    "int": Type.TYPE_INTEGER, "numeric": Type.TYPE_NUMBER,
    "float": Type.TYPE_NUMBER, "text": Type.TYPE_STRING,
    "varchar": Type.TYPE_STRING, "date": Type.TYPE_DATETIME,
    "timestamp": Type.TYPE_DATETIME, "boolean": Type.TYPE_BOOLEAN,
}

_STATUS = {"pass": QualityRunStatus.SUCCESS, "fail": QualityRunStatus.FAILED}

# ODD's platform-wide Data Quality dashboard buckets tests by expectation
# category and shows nothing for tests that have none -- an uncategorised test
# is ingested, visible on its dataset, and invisible on the dashboard. Only
# freshness has a non-assertion category that is honest here; ODD's remaining
# categories (VOLUME_ANOMALY, COLUMN_VALUES_ANOMALY, SCHEMA_CHANGE) describe
# anomaly detection, which is not what a deterministic contract check does.
_CATEGORY = {"freshness": DataQualityTestExpectationCategory.FRESHNESS_ANOMALY}
_DEFAULT_CATEGORY = DataQualityTestExpectationCategory.ASSERTION


class ContractPathsModel(BasePathsModel):
    """//datafletch/host/<host>/contracts/<id>/checks/<check>/runs/<date>"""
    contracts: Optional[str] = None
    checks: Optional[str] = None
    runs: Optional[str] = None

    @classmethod
    def _deps(cls) -> DependenciesMap:
        return {"contracts": ("contracts",),
                "checks": ("contracts", "checks"),
                "runs": ("contracts", "checks", "runs")}

    dependencies_map: DependenciesMap = Field(
        default_factory=lambda: ContractPathsModel._deps())


class ContractGenerator(Generator):
    source = "datafletch"
    paths_model = ContractPathsModel
    server_model = HostnameModel


def pg_host(dsn: str) -> str:
    """The host segment of the dataset ODDRNs, as odd-collector would mint it.

    An ODDRN is matched by string, so a dataset only merges with the one
    ODD's own Postgres collector discovers if this segment is byte-identical
    to the collector's. The collector uses the bare hostname from its config
    -- no port -- so appending one forks every table into two catalog objects,
    the collector's copy holding the schema and ours holding the tests.

    ODD_PG_HOST overrides it outright, which is the normal case rather than
    the exception: the collector reaches the database by a name we do not
    share (a container name, a service DNS entry) while our DSN says
    localhost. Set it to whatever the collector's `host:` says.
    """
    return os.getenv("ODD_PG_HOST") or (urlparse(dsn).hostname or "localhost")


def pg_generator(dsn: str, table: str, schema: str = "public") -> PostgresqlGenerator:
    return PostgresqlGenerator(
        host_settings=pg_host(dsn),
        databases=(urlparse(dsn).path or "/").lstrip("/"),
        schemas=schema, tables=table)


def dataset_oddrn(dsn: str, contract: DataContract) -> str:
    return pg_generator(dsn, contract.server.table,
                        contract.server.schema_).get_oddrn_by_path("tables")


def column_oddrns(dsn: str, contract: DataContract) -> dict[str, str]:
    """`{column name: oddrn}` -- the key ODD's dataset-stats payload is keyed by."""
    g = pg_generator(dsn, contract.server.table, contract.server.schema_)
    out = {}
    for f in contract.schema_.fields:
        g.set_oddrn_paths(tables_columns=f.name)
        out[f.name] = g.get_oddrn_by_path("tables_columns")
    return out


def dataset_entity(dsn: str, contract: DataContract,
                   rows_number: int | None = None,
                   columns: list[tuple[str, str]] | None = None) -> DataEntity:
    """The contract already describes the schema, so it can seed the catalog on
    its own -- no collector required to see the table in ODD.

    *columns* is the table's real column list when we have a connection to read
    it. The contract governs a subset, and publishing that subset as the
    dataset's structure makes every collector cycle mint a new schema revision
    (see `stats.table_columns`). Contract metadata -- description, key,
    nullability -- is overlaid onto the columns it does name.
    """
    g = pg_generator(dsn, contract.server.table, contract.server.schema_)
    declared = {f.name: f for f in contract.schema_.fields}
    listing = columns or [(f.name, f.type) for f in contract.schema_.fields]
    fields = []
    for name, pg_type in listing:
        f = declared.get(name)
        g.set_oddrn_paths(tables_columns=name)
        fields.append(DataSetField(
            oddrn=g.get_oddrn_by_path("tables_columns"), name=name,
            description=f.description if f else None,
            type=DataSetFieldType(
                type=_PG_TYPE.get(pg_type.lower(), Type.TYPE_UNKNOWN),
                logical_type=pg_type,
                is_nullable=not (f.required if f else False)),
            is_primary_key=bool(f and f.unique and f.required),
            enum_values=None))
    return DataEntity(
        oddrn=g.get_oddrn_by_path("tables"), name=contract.server.table,
        type=DataEntityType.TABLE, owner=contract.info.owner,
        description=contract.info.description,
        tags=[Tag(name=f"contract:{contract.id}"),
              Tag(name=f"domain:{contract.info.domain or 'unknown'}")],
        metadata=[MetadataExtension(schema_url=SCHEMA_URL, metadata={
            "contract_id": contract.id,
            "contract_sla_min_score": float(contract.sla.min_score)})],
        # ODD shows "Rows 0" until something tells it otherwise; nothing
        # derives a row count from the runs we push.
        dataset=DataSet(field_list=fields, rows_number=rows_number))


def check_entity(host: str, dsn: str, contract: DataContract,
                 check: Check) -> DataEntity:
    # the check id minus the contract prefix -- NOT the last dotted segment,
    # which collides (customer_id.unique and tax_id.unique both end in 'unique')
    name = check.id.replace(contract.id + ".", "")
    g = ContractGenerator(host_settings=host, contracts=contract.id, checks=name)
    return DataEntity(
        oddrn=g.get_oddrn_by_path("checks"),
        name=name,
        type=DataEntityType.JOB, owner=contract.info.owner,
        description=check.description,
        tags=[Tag(name=f"severity:{check.severity}"),
              Tag(name=f"origin:{check.origin}")],
        metadata=[MetadataExtension(schema_url=SCHEMA_URL, metadata={
            "severity": check.severity, "origin": check.origin,
            "column": check.column, "kind": check.kind,
            "derived_from_contract": contract.id, **{
                k: v for k, v in check.params.items() if v is not None}})],
        data_quality_test=DataQualityTest(
            suite_name=contract.id,
            dataset_list=[dataset_oddrn(dsn, contract)],
            expectation=DataQualityTestExpectation(
                type=check.kind,
                category=_CATEGORY.get(check.kind, _DEFAULT_CATEGORY),
                severity=check.severity,
                column=check.column, **{
                    k: str(v) for k, v in check.params.items()
                    if v is not None})))


def run_entity(host: str, contract_id: str, check_name: str, result: dict) -> DataEntity:
    g = ContractGenerator(host_settings=host, contracts=contract_id,
                          checks=check_name, runs=str(result["run_at"]))
    start = datetime.combine(result["run_at"], datetime.min.time(),
                             tzinfo=timezone.utc)
    return DataEntity(
        oddrn=g.get_oddrn_by_path("runs"),
        name=f"{check_name}@{result['run_at']}",
        type=DataEntityType.JOB_RUN,
        data_quality_test_run=DataQualityTestRun(
            data_quality_test_oddrn=g.get_oddrn_by_path("checks"),
            start_time=start,
            end_time=start + timedelta(milliseconds=int(result["duration_ms"])),
            status=_STATUS.get(result["status"], QualityRunStatus.UNKNOWN),
            # ODD's run model has no row counters, so the only place the volume
            # signal fits is this free-text field. See docs/odd-gap-analysis.md
            status_reason=(f"{result['failed_rows']}/{result['total_rows']} rows failed "
                           f"({float(result['fail_ratio']) * 100:.2f}%) "
                           f"severity={result['severity']}")))


def datasource_oddrn(host: str) -> str:
    """The data source every payload is ingested under.

    ODD rejects an ingestion whose ``data_source_oddrn`` it does not know
    (404 ``USR002``). A collector registers itself; we have no collector, so
    push.py registers this oddrn before the first POST.
    """
    return ContractGenerator(host_settings=host).get_data_source_oddrn()


def entity_list(items: list[DataEntity], host: str) -> DataEntityList:
    return DataEntityList(data_source_oddrn=datasource_oddrn(host), items=items)
