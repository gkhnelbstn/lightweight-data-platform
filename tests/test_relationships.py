"""A contract's foreign keys, as ODD's ER diagram is told them.

What is pinned is the reading of ODCS 3.1 `relationships` and the ODDRNs: a
relationship is only drawn when both ends land on the dataset and column
ODDRNs odd-collector minted, and it keeps the ODDRN it had before the
contracts moved to ODCS, so the one already in ODD is updated in place.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from integrations.odd import relationships

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"


def _load(name):
    return yaml.safe_load((CONTRACTS / name).read_text(encoding="utf-8"))


def test_the_contracts_state_their_foreign_keys():
    keys = {c["id"]: relationships.foreign_keys(c)
            for c in map(_load, sorted(p.name for p in CONTRACTS.glob("*.odcs.yaml")))}
    assert keys["erp.sales_orders"] == [("sales_orders", "customer_id", "customers", "customer_id")]
    assert keys["erp.mssql.order_lines"] == [
        ("sales_order_lines", "order_id", "sales_orders", "order_id"),
        ("sales_order_lines", "product_id", "products", "product_id")]


def test_both_ends_land_on_the_collectors_oddrns():
    (entity,) = relationships.entities(_load("erp_postgres.odcs.yaml"))
    rel = entity.data_relationship
    base = "//postgresql/host/db/databases/erp/schemas/public/tables"
    assert rel.source_dataset_oddrn == f"{base}/sales_orders"
    assert rel.target_dataset_oddrn == f"{base}/customers"
    assert rel.details.source_dataset_field_oddrns_list == [f"{base}/sales_orders/columns/customer_id"]
    assert rel.details.target_dataset_field_oddrns_list == [f"{base}/customers/columns/customer_id"]


def test_the_relationship_keeps_the_oddrn_it_already_has_in_odd():
    (entity,) = relationships.entities(_load("erp_postgres.odcs.yaml"))
    assert entity.oddrn.endswith("/contracts/erp.sales_orders/checks/rel.customer_id")


def test_a_relationship_that_is_not_a_foreign_key_is_not_drawn_as_one():
    contract = {"id": "x", "schema": [{"name": "t", "properties": [
        {"name": "a", "relationships": [{"type": "other", "to": "u.b"}]}]}]}
    assert relationships.foreign_keys(contract) == []
