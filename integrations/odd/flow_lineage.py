"""The integration in the catalogue: every flow a job between two tables.

A flow (ADR 0019) moves one system's rows into a hub or out of one, and
SeaTunnel runs it, so nothing ODD collects knows it exists. The tables a flow
touches are often in no collected source either: Siber's test server is read
by CDC and catalogued by nobody, and the hub is the platform's own database.
The flows ran, and the catalogue showed neither end nor the job between them.

So each flow becomes a `DataTransformer` from its source table to its
target, and each table a flow touches is published from its contract into a
data source for its server -- **unless a collector already owns that
source**. ODD gives a data source registered through its API a token and one
a collector registered none, and a collector's table has every column where
the contract states the mapped ones only: publishing ours over it would give
the table a new structure version on every run of either. There, only the
job is published, on the collector's table.

    python integrations/odd/flow_lineage.py --url http://odd-platform:8080 \\
        --contracts demo/integration
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from odd_models.models import (DataEntity, DataEntityList, DataEntityType,
                               DataSet, DataSetField, DataSetFieldType,
                               DataTransformer, Type)

from core import flow_jobs, flow_schema
from core import flows as flowmod
from core.mapping import _properties
from core.runner import HOST
from integrations.odd.entity_page import _get, _send
from integrations.odd.from_datacontract import (_generator, dataset_oddrn,
                                                ensure_datasource, post)
from integrations.odd.mapper import entity_list

_TYPES = {"int": Type.TYPE_INTEGER, "integer": Type.TYPE_INTEGER,
          "smallint": Type.TYPE_INTEGER, "tinyint": Type.TYPE_INTEGER,
          "bigint": Type.TYPE_INTEGER, "numeric": Type.TYPE_NUMBER,
          "decimal": Type.TYPE_NUMBER, "money": Type.TYPE_NUMBER,
          "float": Type.TYPE_NUMBER, "real": Type.TYPE_NUMBER,
          "bit": Type.TYPE_BOOLEAN, "boolean": Type.TYPE_BOOLEAN,
          "date": Type.TYPE_DATETIME, "datetime": Type.TYPE_DATETIME,
          "datetime2": Type.TYPE_DATETIME, "smalldatetime": Type.TYPE_DATETIME,
          "timestamp": Type.TYPE_DATETIME, "timestamptz": Type.TYPE_DATETIME}


def server(contract: dict) -> dict | None:
    """The database a contract's table lives in; an API has none."""
    return next((s for s in contract.get("servers") or []
                 if s.get("type") in ("sqlserver", "postgres", "postgresql")), None)


def source_oddrn(contract: dict) -> str:
    """The data source a table belongs to: its server's database."""
    s = server(contract)
    return _generator(str(s["type"]).lower())(
        host_settings=s["host"], databases=s["database"]).get_oddrn_by_path("databases")


def describe(contract: dict, flow_ids: set[str]) -> tuple[str, str]:
    """`(name, description)` of that data source: the contract's server, and
    where it is -- a name alone says nothing about which machine it is. ODD
    keeps 255 characters of it, so the flows are counted, not listed: each
    one is a job on the lineage of the tables it touches."""
    s, where = server(contract), flow_schema.where(contract)
    if flowmod.hub_of(contract) is not None:
        role = (f"Integration hub (ADR 0021) at {where}, schema {s.get('schema', 'hub')}: "
                f"changes land in hub.<entity>_inbox, hub.merge keeps the golden record.")
    else:
        role = f"A system in the integration, at {where}."
    return str(s["server"]), (f"{role} {len(flow_ids)} SeaTunnel flow(s); tables "
                              f"catalogued from their contracts.")


def dataset(contract: dict) -> DataEntity:
    """A table as its contract declares it."""
    oddrn = dataset_oddrn(contract)
    fields = [DataSetField(
        oddrn=f"{oddrn}/columns/{name}", name=name, description=p.get("description"),
        is_primary_key=bool(p.get("primaryKey")),
        type=DataSetFieldType(
            type=_TYPES.get(str(p.get("physicalType", "")).lower(), Type.TYPE_STRING),
            logical_type=p.get("physicalType") or "unknown",
            is_nullable=not p.get("required")))
        for name, p in _properties(contract).items()]
    table = contract["schema"][0]
    return DataEntity(oddrn=oddrn, name=table.get("physicalName") or table["name"],
                      description=contract.get("name"), type=DataEntityType.TABLE,
                      dataset=DataSet(field_list=fields))


def job(flow: flowmod.Flow, by_id: dict[str, dict]) -> tuple[DataEntity | None, list[str]]:
    """The flow as a job between its two tables, or what could not be placed."""
    ends = {}
    for ref in (flow.mapping.reference, flow.target):
        contract = by_id.get(ref)
        if contract is not None and server(contract) is not None:
            ends[ref] = dataset_oddrn(contract)
    missing = [r for r in (flow.mapping.reference, flow.target) if r not in ends]
    if missing:
        return None, missing
    return DataEntity(
        oddrn=f"//datafletch/host/{HOST}/transformers/{flow.id}", name=flow.id,
        description=f"SeaTunnel job: {flow.mapping.reference} to {flow.target}, "
                    f"{len(flow.mapping.columns)} columns mapped.",
        type=DataEntityType.JOB,
        data_transformer=DataTransformer(inputs=[ends[flow.mapping.reference]],
                                         outputs=[ends[flow.target]])), []


def build(by_id: dict[str, dict], flows: list[flowmod.Flow]):
    """`(tables by data source, jobs, unplaced)` for every flow."""
    placed = {ref: c for f in flows for ref in (f.mapping.reference, f.target)
              if (c := by_id.get(ref)) is not None and server(c) is not None}
    # One data source per server, however many of its tables the flows touch.
    uses: dict[str, set[str]] = {}
    for f in flows:
        for ref in {f.mapping.reference, f.target} & placed.keys():
            uses.setdefault(source_oddrn(placed[ref]), set()).add(f.id)
    tables: dict[tuple[str, str, str], list[DataEntity]] = {}
    for _, contract in sorted(placed.items()):
        oddrn = source_oddrn(contract)
        tables.setdefault((oddrn, *describe(contract, uses[oddrn])), []).append(
            dataset(contract))
    jobs, unplaced = [], {}
    for flow in flows:
        entity, missing = job(flow, by_id)
        if entity is not None:
            jobs.append(entity)
        if missing:
            unplaced[flow.id] = missing
    return tables, jobs, unplaced


def publish(url: str, directory: Path) -> list[str]:
    """Register what is missing, publish what is ours, and say what was done."""
    url, said = url.rstrip("/"), []
    tables, jobs, unplaced = build(*flow_jobs.load(directory))
    known = {d["oddrn"]: d for d in
             _get(f"{url}/api/datasources?page=1&size=1000").get("items", [])}
    for (oddrn, name, description), entities in tables.items():
        if oddrn in known and not known[oddrn].get("token"):
            said.append(f"{name}: a collector owns {oddrn}, tables left to it")
            continue
        if oddrn not in known:
            _send(f"{url}/api/datasources",
                  {"name": name, "oddrn": oddrn, "description": description})
        elif known[oddrn].get("description") != description:
            # Ours, and the flows using it changed since it was registered.
            _send(f"{url}/api/datasources/{known[oddrn]['id']}",
                  {"name": known[oddrn]["name"], "description": description}, "PUT")
        post(url, DataEntityList(data_source_oddrn=oddrn, items=entities)
             .model_dump(mode="json", exclude_none=True))
        said.append(f"{name}: {len(entities)} table(s) in {oddrn}")
    for flow_id, missing in unplaced.items():
        # Named, not dropped: a graph missing an edge still looks complete.
        said.append(f"! {flow_id}: no table for {', '.join(missing)}")
    if jobs:
        ensure_datasource(url)
        post(url, entity_list(jobs, HOST).model_dump(mode="json", exclude_none=True))
    return said + [f"published {len(jobs)} flow(s)"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Publish the integration's flows to ODD.")
    ap.add_argument("--url", default="http://odd-platform:8080")
    ap.add_argument("--contracts", default=os.getenv("INTEGRATION_DIR", "contracts"))
    a = ap.parse_args()
    for line in publish(a.url, Path(a.contracts)):
        print(f"  {line}")


if __name__ == "__main__":
    main()
