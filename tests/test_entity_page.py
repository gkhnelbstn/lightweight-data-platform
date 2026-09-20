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
    """Both questions, and they are different ones: the checks of this table,
    and the contract behind them. Issue #23."""
    from integrations.odd.entity_page import desired_links
    links = desired_links({"id": "erp.sales_orders"})
    assert len(links) == 2
    assert links[0]["url"].endswith(
        "/data-quality?dq_checks_contract=erp.sales_orders")
    assert links[1]["url"].endswith("/data-quality?dq_contract=erp.sales_orders")


def test_the_links_go_to_the_panel_and_not_to_the_retired_ui():
    """ADR 0009 moved the panel into ODD, and #26 turned what was left on the
    API's port into a redirect to its OpenAPI docs. A link pointing there
    would land a person on a route list."""
    from integrations.odd.entity_page import desired_links
    for link in desired_links({"id": "erp.customers"}):
        assert "/data-quality?" in link["url"]
        assert "#contract=" not in link["url"]


def test_a_replicated_contract_also_links_its_sync_rule():
    from integrations.odd.entity_page import desired_links
    contract = {"id": "erp.customers", "customProperties": [
        {"property": "syncTo", "value": {"server": "replica"}}]}
    assert [link["url"].split("?")[-1] for link in desired_links(contract)] == [
        "dq_checks_contract=erp.customers",
        "dq_contract=erp.customers", "dq_tab=Replication"]


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
        expected = 3 if sync_rule(doc) else 2
        assert len(desired_links(doc)) == expected, path.name


def test_a_link_is_found_by_what_it_is_and_named_in_the_deployments_language(monkeypatch):
    """Issue #44: the names follow LDP_LANGUAGE, so `odd_links` keys the links
    by what they are -- or a language change would add a second set."""
    from core import language
    from integrations.odd.entity_page import desired_links
    contract = {"id": "erp.customers",
                "customProperties": [{"property": "syncTo", "value": {}}]}
    monkeypatch.setattr(language, "LANGUAGE", "en")
    english = desired_links(contract)
    monkeypatch.setattr(language, "LANGUAGE", "tr")
    turkish = desired_links(contract)
    assert [l["key"] for l in english] == [l["key"] for l in turkish] == [
        "checks", "contract", "sync"]
    assert [l["name"] for l in turkish] == [
        "Kontroller", "Veri kalitesi (kontrat)", "Senkron kuralı"]
    assert english[0]["name"] == "Checks"
