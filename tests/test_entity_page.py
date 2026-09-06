"""What ODD is asked to show on the table's own page.

The rest of this module talks to a running ODD and is exercised by hand and by
the demo; what is pinned here is the set of links, because it is the part that
decides whether someone finds the failing rows from the catalog or has to be
told a port number.
"""
from __future__ import annotations

from pathlib import Path

import yaml

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"


def _links(contract, monkeypatch=None):
    from integrations.odd import entity_page
    return entity_page.desired_links(contract)


def test_every_contract_gets_a_way_back_to_its_page():
    from integrations.odd.entity_page import desired_links
    links = desired_links({"id": "erp.sales_orders"})
    assert len(links) == 1
    assert links[0]["url"].endswith("#contract=erp.sales_orders")


def test_a_replicated_contract_also_links_its_sync_rule():
    from integrations.odd.entity_page import desired_links
    contract = {"id": "erp.customers", "customProperties": [
        {"property": "syncTo", "value": {"server": "replica"}}]}
    assert [link["url"].rsplit("/", 1)[-1] for link in desired_links(contract)] == \
        ["#contract=erp.customers", "#sync"]


def test_the_link_is_where_a_browser_reaches_us():
    """Not the compose service name: these are followed from outside the
    network, so `http://app:8077` would be a dead link on every one of them."""
    import importlib

    from integrations.odd import entity_page
    assert entity_page.UI_URL.startswith("http")
    assert "//app:" not in entity_page.UI_URL
    importlib.reload(entity_page)


def test_the_shipped_contracts_produce_the_links_they_should():
    from integrations.odd.entity_page import desired_links
    from core.sync import sync_rule
    for path in sorted(CONTRACTS.glob("*.odcs.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        expected = 2 if sync_rule(doc) else 1
        assert len(desired_links(doc)) == expected, path.name
