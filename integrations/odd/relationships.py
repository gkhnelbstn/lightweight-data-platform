"""A contract's foreign keys, drawn on ODD's ER diagram.

ODCS 3.1 states a foreign key on the column that holds it:

    - name: customer_id
      relationships:
        - type: foreignKey
          to: customers.customer_id

ODD models the same fact as an ENTITY_RELATIONSHIP carrying a column-level
`ERDRelationship`, and draws it on Data Modelling > Relationships and on the
dataset's own page. Nothing has to be discovered: the contract says it. This
is not lineage -- a foreign key says which row a row belongs to, not which job
made it (ADR 0014).

The relationship is only drawn when ODD can read it back, which 0.29.0 cannot
for a table whose columns ever changed: see deploy/Dockerfile.odd-platform and
ADR 0011. Cardinality is ONE_TO_EXACTLY_ONE and `is_identifying` is false,
because that is all a foreign key claims: a child row points at one parent.

The target is a table of the same server: `to` names a table and a column in
it, as ODCS writes it, and a foreign key into another database is not a thing
either engine has.
"""
from __future__ import annotations

import os

from odd_models.models import (CardinalityType, DataEntity, DataEntityType,
                               DataRelationship, ERDRelationship,
                               RelationshipType, Tag)

from integrations.odd.from_datacontract import _generator
from integrations.odd.mapper import ContractGenerator

HOST = os.getenv("DQ_HOST", "dq.local")


def _column(contract: dict, server_key: str, table: str, column: str) -> tuple[str, str]:
    """The table's and the column's ODDRN, as odd-collector mints them."""
    server = next(s for s in contract["servers"] if s.get("server") == server_key)
    g = _generator(str(server["type"]).lower())(
        host_settings=server["host"], databases=server["database"],
        schemas=server.get("schema", "dbo"), tables=table, tables_columns=column)
    return g.get_oddrn_by_path("tables"), g.get_oddrn_by_path("tables_columns")


def foreign_keys(contract: dict) -> list[tuple[str, str, str, str]]:
    """(table, column, target table, target column) for each foreign key."""
    out = []
    for schema in contract.get("schema") or []:
        table = schema.get("physicalName") or schema["name"]
        for prop in schema.get("properties") or []:
            for rel in prop.get("relationships") or []:
                if rel.get("type", "foreignKey") != "foreignKey":
                    continue
                for to in rel["to"] if isinstance(rel["to"], list) else [rel["to"]]:
                    target, _, column = to.rpartition(".")
                    out.append((table, prop.get("physicalName") or prop["name"],
                                target.rpartition(".")[2], column))
    return out


def entities(contract: dict, server_key: str = "erp") -> list[DataEntity]:
    out = []
    for table, column, target, target_column in foreign_keys(contract):
        src_table, src_column = _column(contract, server_key, table, column)
        tgt_table, tgt_column = _column(contract, server_key, target, target_column)
        # The ODDRN a relationship had before the contracts moved to ODCS, so
        # the one already in ODD is updated rather than left beside a twin.
        oddrn = ContractGenerator(host_settings=HOST, contracts=contract["id"],
                                  checks=f"rel.{column}").get_oddrn_by_path("checks")
        out.append(DataEntity(
            oddrn=oddrn, name=f"{table}.{column} -> {target}.{target_column}",
            type=DataEntityType.ENTITY_RELATIONSHIP,
            description=f"{column} must exist in {target}",
            tags=[Tag(name=f"contract:{contract['id']}")],
            data_relationship=DataRelationship(
                relationship_type=RelationshipType.ERD,
                source_dataset_oddrn=src_table, target_dataset_oddrn=tgt_table,
                details=ERDRelationship(
                    source_dataset_field_oddrns_list=[src_column],
                    target_dataset_field_oddrns_list=[tgt_column],
                    cardinality=CardinalityType.ONE_TO_EXACTLY_ONE,
                    is_identifying=False,
                    relationship_entity_name="ERDRelationship"))))
    return out
