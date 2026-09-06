"""The lineage nothing can infer, and therefore nothing can check but us.

A wrong edge here is worse than a missing one: the graph still looks complete,
and someone reads a blast radius that is not the real one. So the two failure
modes worth pinning are an unresolved reference being *reported* rather than
dropped, and a contract that declares nothing producing nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytest.importorskip("odd_models")
from integrations.odd.lineage import build, declared, resolve  # noqa: E402

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"


def _load(name: str) -> dict:
    return yaml.safe_load((CONTRACTS / name).read_text(encoding="utf-8"))


def _all() -> list[dict]:
    return [yaml.safe_load(p.read_text(encoding="utf-8"))
            for p in sorted(CONTRACTS.glob("*.odcs.yaml"))]


# --- what a contract says ---------------------------------------------------

def test_a_contract_states_its_upstream_and_optionally_its_sql():
    upstream, sql = declared(_load("dwh_stg_orders.odcs.yaml"))
    assert upstream == ["erp.sales_orders"]
    assert sql and "from raw.orders" in sql


def test_a_source_contract_declares_nothing():
    upstream, sql = declared(_load("erp_postgres.odcs.yaml"))
    assert upstream == [] and sql is None


def test_a_reference_is_a_contract_id_or_an_oddrn():
    """Contract ids are preferred because they survive a host or schema change;
    an ODDRN is the escape hatch for a table that has no contract."""
    by_id = {c["id"]: c for c in _all()}
    assert resolve("erp.sales_orders", by_id).endswith("/tables/sales_orders")
    assert resolve("//somewhere/else", by_id) == "//somewhere/else"
    assert resolve("erp.not_a_contract", by_id) is None


# --- what gets published ----------------------------------------------------

def test_only_contracts_that_declare_one_become_a_transformer():
    entities, unresolved = build(_all())
    declaring = [c["id"] for c in _all() if declared(c)[0]]
    assert len(entities) == len(declaring)
    assert not unresolved


def test_a_transformer_points_from_its_upstream_to_its_own_table():
    entities, _ = build(_all())
    by_name = {e.data_transformer.outputs[0]: e for e in entities}
    mart = next(o for o in by_name if o.endswith("/mart/tables/revenue_daily"))
    assert by_name[mart].data_transformer.inputs[0].endswith("/fct/tables/orders")


def test_two_upstreams_become_two_inputs():
    """A fact table joins a staged table to a dimension, and both edges have to
    be there or the blast radius is understated."""
    entities, _ = build(_all())
    fact = next(e for e in entities
                if e.data_transformer.outputs[0].endswith("/fct/tables/orders"))
    assert len(fact.data_transformer.inputs) == 2


def test_an_unresolvable_reference_is_reported_not_dropped():
    """Silently dropping it leaves a graph that looks complete and is not."""
    contract = {"id": "x.y", "name": "X", "servers": [
        {"server": "erp", "type": "postgres", "host": "h", "port": 5432,
         "database": "d", "schema": "s"}],
        "schema": [{"name": "t", "physicalName": "t"}],
        "customProperties": [{"property": "derivedFrom",
                              "value": ["nope.missing"]}]}
    entities, unresolved = build([contract])
    assert entities == []
    assert unresolved == {"x.y": ["nope.missing"]}


def test_the_medallion_chain_is_unbroken():
    """Source to mart, hop by hop. This is the chain the whole question --
    which dashboards break -- is answered by."""
    entities, _ = build(_all())
    edges = {e.data_transformer.outputs[0]: set(e.data_transformer.inputs)
             for e in entities}

    def find(suffix: str) -> str:
        return next(o for o in edges if o.endswith(suffix))

    stg = find("/stg/tables/orders")
    fct = find("/fct/tables/orders")
    mart = find("/mart/tables/revenue_daily")
    assert any(i.endswith("/public/tables/sales_orders") for i in edges[stg])
    assert stg in edges[fct]
    assert fct in edges[mart]
