"""The ODDRN vocabulary this project speaks to ODD in.

ODD addresses everything by ODDRN and matches them as strings, so the only
thing that has to be right here is that the identifiers we mint are the ones
odd-collector mints for the same objects. The entities themselves are built in
`from_datacontract.py`, from what `datacontract test` reports.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

from odd_models.models import DataEntity, DataEntityList
from oddrn_generator import Generator, PostgresqlGenerator
from oddrn_generator.path_models import BasePathsModel, DependenciesMap
from oddrn_generator.server_models import HostnameModel
from pydantic import Field


SCHEMA_URL = ("https://raw.githubusercontent.com/opendatadiscovery/opendatadiscovery-specification"
              "/main/specification/extensions/datafletch.json#/definitions/Contract")





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


def datasource_oddrn(host: str) -> str:
    """The data source every payload is ingested under.

    ODD rejects an ingestion whose ``data_source_oddrn`` it does not know
    (404 ``USR002``). A collector registers itself; we have no collector, so
    push.py registers this oddrn before the first POST.
    """
    return ContractGenerator(host_settings=host).get_data_source_oddrn()


def entity_list(items: list[DataEntity], host: str) -> DataEntityList:
    return DataEntityList(data_source_oddrn=datasource_oddrn(host), items=items)
