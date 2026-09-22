"""The integration's flows in the catalogue, from the demo's flow files.

What is pinned: every flow is a job from the table it reads to the table it
writes, on the ODDRNs odd-collector mints for them; a table no collector owns
is published from its contract; one a collector owns is left to it; and a
flow that cannot be placed is named. A live ODD is exercised by the demo.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import flow_jobs
from integrations.odd import flow_lineage

DEMO = Path(__file__).resolve().parents[1] / "demo" / "integration"
CRM = "//mssql/host/mssql/databases/crm"
HUB = "//postgresql/host/db/databases/hub"


@pytest.fixture(scope="module")
def built():
    return flow_lineage.build(*flow_jobs.load(DEMO))


def test_every_flow_is_a_job_from_its_source_table_to_its_target(built):
    _, jobs, _ = built
    by_name = {j.name: j.data_transformer for j in jobs}
    assert by_name["crm_to_hub"].inputs == [f"{CRM}/schemas/dbo/tables/account"]
    assert by_name["crm_to_hub"].outputs == [f"{HUB}/schemas/hub/tables/customer"]
    # The way back is its own job, so the pair reads as the cycle it is.
    assert by_name["hub_to_crm"].inputs == by_name["crm_to_hub"].outputs


def test_a_flow_with_no_table_to_place_is_named_not_dropped(built):
    """The loyalty API is an endpoint, not a table (ADR 0027)."""
    _, jobs, unplaced = built
    assert unplaced == {"loyalty_to_hub": ["loyalty.member"]}
    assert "loyalty_to_hub" not in {j.name for j in jobs}


def test_one_data_source_per_server_says_where_it_is(built):
    tables, _, _ = built
    sources = {oddrn: (name, text, [e.name for e in entities])
               for (oddrn, name, text), entities in tables.items()}
    name, text, crm_tables = sources[CRM]
    assert (name, sorted(crm_tables)) == ("crm", ["account", "account_address"])
    assert "sqlserver mssql:1433, database crm" in text and "6 SeaTunnel flow(s)" in text
    assert sources[HUB][1].startswith("Integration hub (ADR 0021) at postgres db:5432")
    # ODD stores a data source's description in a varchar(255): longer is a 500.
    assert max(len(text) for _, text, _ in sources.values()) <= 255


def test_a_table_is_published_as_its_contract_declares_it(built):
    tables, _, _ = built
    hub = next(e for (oddrn, _, _), es in tables.items() if oddrn == HUB for e in es)
    fields = {f.name: f for f in hub.dataset.field_list}
    key = [n for n, f in fields.items() if f.is_primary_key]
    assert key == ["customer_id"]
    assert fields["customer_id"].oddrn == f"{hub.oddrn}/columns/customer_id"
    assert not fields["customer_id"].type.is_nullable


def test_a_collectors_source_keeps_its_tables(monkeypatch):
    """A collector's table has every column; ours would replace them with the
    mapped ones on every run. Only the jobs go on it."""
    posted, sent = {}, []
    monkeypatch.setattr(flow_lineage, "_get", lambda url: {"items": [
        {"id": 1, "oddrn": CRM, "name": "crm", "token": None}]})
    monkeypatch.setattr(flow_lineage, "_send",
                        lambda url, body, method="POST": sent.append((method, url, body)))
    monkeypatch.setattr(flow_lineage, "post", lambda url, body: posted.setdefault(
        body["data_source_oddrn"], body["items"]))
    monkeypatch.setattr(flow_lineage, "ensure_datasource", lambda url: None)
    said = flow_lineage.publish("http://odd", DEMO)
    assert f"crm: a collector owns {CRM}, tables left to it" in said
    assert CRM not in posted
    assert [o["name"] for o in posted[HUB]] == ["customer"]
    assert ("POST", "http://odd/api/datasources") in {(m, u) for m, u, _ in sent}
    jobs = next(items for ds, items in posted.items() if ds.startswith("//datafletch"))
    assert "crm_to_hub" in {j["name"] for j in jobs}
