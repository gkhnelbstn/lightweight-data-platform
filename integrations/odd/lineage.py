"""Publish the lineage nothing can infer.

A collector can read a view definition and a foreign key. It cannot read a
Prefect flow, an Airflow DAG or a Python script that selects from one database
and inserts into another -- and that is how most warehouses are actually
built. The transformation leaves no trace in either engine, so the only place
that knows it is whatever declared it.

Here that is the contract:

    customProperties:
      - property: derivedFrom
        value: [erp.sales_orders]          # contract ids, or raw ODDRNs
      - property: derivedBy
        value: "select ... from ..."       # optional, shown on the job

Each contract that declares one becomes a `DataTransformer` whose inputs are
the upstream datasets and whose output is its own table. ODD then draws the
chain, and -- because the tests already hang off the same table ODDRNs -- a
failing check on a source has a downstream that reaches whatever Superset
charts at the end of it. That is the whole "which dashboards break" question,
and it needs no column-level lineage to answer.

    python integrations/odd/lineage.py --url http://odd-platform:8080
"""
from __future__ import annotations

import argparse

from core.runner import HOST, load_contracts
from integrations.odd.from_datacontract import (dataset_oddrn,
                                                ensure_datasource, post)
from integrations.odd.mapper import entity_list


def declared(contract: dict) -> tuple[list[str], str | None]:
    """`(upstream, sql)` as the contract states them."""
    upstream, sql = [], None
    for prop in contract.get("customProperties") or []:
        if prop.get("property") == "derivedFrom":
            value = prop["value"]
            upstream = list(value) if isinstance(value, list) else [value]
        elif prop.get("property") == "derivedBy":
            sql = str(prop["value"])
    return upstream, sql


def resolve(reference: str, by_id: dict[str, dict]) -> str | None:
    """A reference is either a contract id or an ODDRN.

    Contract ids are preferred: they survive a host or schema change, which an
    ODDRN written into a yaml does not. An ODDRN is the escape hatch for a
    table that has no contract -- a landing table, someone else's source.
    """
    if reference.startswith("//"):
        return reference
    upstream = by_id.get(reference)
    return dataset_oddrn(upstream, "erp") if upstream else None


def transformer(contract: dict, by_id: dict[str, dict]):
    """One job per contract that says where it came from."""
    from odd_models.models import DataEntity, DataEntityType, DataTransformer

    upstream, sql = declared(contract)
    if not upstream:
        return None, []
    inputs, missing = [], []
    for reference in upstream:
        oddrn = resolve(reference, by_id)
        (inputs if oddrn else missing).append(oddrn or reference)
    if not inputs:
        return None, missing

    name = str(contract["id"]).replace(".", "_")
    return DataEntity(
        # Its own namespace under our host, so a job is never mistaken for
        # something a collector found.
        oddrn=f"//datafletch/host/{HOST}/transformers/{name}",
        name=contract.get("name") or contract["id"],
        type=DataEntityType.JOB,
        metadata=None,
        data_transformer=DataTransformer(
            inputs=inputs, outputs=[dataset_oddrn(contract, "erp")], sql=sql),
    ), missing


def build(contracts: list[dict]) -> tuple[list, dict[str, list[str]]]:
    by_id = {c["id"]: c for c in contracts}
    entities, unresolved = [], {}
    for contract in contracts:
        entity, missing = transformer(contract, by_id)
        if entity is not None:
            entities.append(entity)
        if missing:
            unresolved[contract["id"]] = missing
    return entities, unresolved


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://odd-platform:8080")
    a = ap.parse_args()

    entities, unresolved = build(load_contracts())
    for cid, missing in unresolved.items():
        # Named rather than swallowed: a lineage edge that silently did not
        # appear is worse than one that failed loudly, because the graph still
        # looks complete.
        print(f"  ! {cid}: cannot resolve {', '.join(missing)}")
    if not entities:
        print("no contract declares derivedFrom")
        return

    body = entity_list(entities, HOST).model_dump(mode="json", exclude_none=True)
    ensure_datasource(a.url)
    post(a.url, body)
    for entity in entities:
        transform = entity.data_transformer
        print(f"  {', '.join(i.rsplit('/', 1)[-1] for i in transform.inputs)}"
              f"  ->  {transform.outputs[0].rsplit('/', 1)[-1]}")
    print(f"published {len(entities)} transformer(s)")


if __name__ == "__main__":
    main()
