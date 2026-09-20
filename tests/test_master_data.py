"""The hub's golden records on ODD's Master Data page.

`publish` is run against a stand-in for ODD's lookup-table API that keeps its
tables in a dict: what is pinned is that the table ends up holding exactly the
given rows -- matched by key, never duplicated -- and that the privacy
boundary holds. A live ODD is exercised by the demo.
"""
from __future__ import annotations

import itertools
import re

import pytest

from core import flows as flowmod
from integrations.odd import master_data


class FakeOdd:
    def __init__(self):
        self.tables, self.ids = {}, itertools.count(1)

    def get(self, url):
        tid = int(re.search(r"/table/(\d+)/data", url).group(1))
        rows = self.tables[tid]["rows"]
        return {"items": [{"row_id": rid, "items": [{"field_id": f, "value": v}
                                                    for f, v in r.items()]}
                          for rid, r in rows.items()], "page_info": {"hasNext": False}}

    def send(self, url, body, method="POST"):
        if url.endswith("/api/referencedata/table"):
            tid = next(self.ids)
            self.tables[tid] = {"table_id": tid, "name": body["name"], "fields": [], "rows": {}}
            return self.tables[tid]
        tid = int(re.search(r"/table/(\d+)", url).group(1))
        table = self.tables[tid]
        if url.endswith("/columns"):
            table["fields"] += [{"name": c["name"], "field_id": next(self.ids)} for c in body]
            return table
        row = re.search(r"/data/(\d+)$", url)
        if method == "DELETE":
            del table["rows"][int(row.group(1))]
        elif method == "PATCH":
            table["rows"][int(row.group(1))] = {i["field_id"]: i["value"] for i in body["items"]}
        else:
            for r in body:
                table["rows"][next(self.ids)] = {i["field_id"]: i["value"] for i in r["items"]}
        return None

    def rows(self, name):
        table = next(t for t in self.tables.values() if t["name"] == name)
        names = {f["field_id"]: f["name"] for f in table["fields"]}
        return sorted(({names[f]: v for f, v in r.items()} for r in table["rows"].values()),
                      key=lambda r: r["customer_id"])


@pytest.fixture
def odd(monkeypatch):
    fake = FakeOdd()
    monkeypatch.setattr(master_data, "_get", fake.get)
    monkeypatch.setattr(master_data, "_send", fake.send)
    monkeypatch.setattr(master_data, "ensure_namespace", lambda *a: None)
    monkeypatch.setattr(master_data, "find", lambda url, name: next(
        (t for t in fake.tables.values() if t["name"] == name), None))
    return fake


def _table(rows):
    return {"name": "customer_master", "key": ["customer_id"], "description": "",
            "columns": {"customer_id": ("DECIMAL", None), "name": ("VARCHAR", None)},
            "rows": rows}


def test_the_table_ends_up_holding_exactly_the_golden_records(odd):
    master_data.publish("http://odd", _table([{"customer_id": "1", "name": "Acme"},
                                              {"customer_id": "2", "name": "Delta"}]))
    report = master_data.publish("http://odd", _table([{"customer_id": "1", "name": "Acme"},
                                                       {"customer_id": "3", "name": "Nova"}]))
    assert odd.rows("customer_master") == [{"customer_id": "1", "name": "Acme"},
                                           {"customer_id": "3", "name": "Nova"}]
    assert report.endswith("1 added, 0 changed, 1 deleted")


def test_an_edit_made_in_odd_is_overwritten_not_kept(odd):
    """One way: a golden record changes in a system, never in the catalogue."""
    master_data.publish("http://odd", _table([{"customer_id": "1", "name": "Acme"}]))
    table = next(iter(odd.tables.values()))
    row = next(iter(table["rows"].values()))
    row[table["fields"][1]["field_id"]] = "edited in ODD"
    report = master_data.publish("http://odd", _table([{"customer_id": "1", "name": "Acme"}]))
    assert odd.rows("customer_master") == [{"customer_id": "1", "name": "Acme"}]
    assert report.endswith("0 added, 1 changed, 0 deleted")


def test_a_second_run_changes_nothing(odd):
    rows = [{"customer_id": "1", "name": "Acme"}]
    master_data.publish("http://odd", _table(rows))
    assert master_data.publish("http://odd", _table(rows)).endswith(
        "0 added, 0 changed, 0 deleted")


def test_a_hub_too_big_for_a_lookup_table_is_left_alone(odd, monkeypatch):
    monkeypatch.setattr(master_data, "MAX_ROWS", 1)
    report = master_data.publish("http://odd", _table([{"customer_id": "1"},
                                                       {"customer_id": "2"}]))
    assert "left alone" in report and not odd.tables


def test_value_maps_are_one_row_per_code_and_avoid_odds_reserved_words():
    """ODD does not quote the column names of its own ALTER TABLE, so a
    column called `column` is a syntax error there (measured)."""
    flow = flowmod.parse({"id": "crm_to_hub", "from": "crm.account", "to": "hub.customer",
                          "columns": {"active": "ACTIVE"},
                          "values": {"active": {"Y": True, "N": False}}})
    table = master_data.value_maps([flow])
    assert table["rows"] == [
        {"flow": "crm_to_hub", "target_column": "active", "from_value": "Y", "to_value": "true"},
        {"flow": "crm_to_hub", "target_column": "active", "from_value": "N", "to_value": "false"}]
    reserved = {"column", "table", "select", "order", "group", "user", "from", "to"}
    assert not reserved & set(table["columns"])


def test_a_classified_column_never_reaches_the_catalogue():
    """The contract's classification is the privacy boundary (ADR 0017)."""
    import yaml
    from pathlib import Path
    hub = yaml.safe_load((Path(__file__).resolve().parents[1] / "demo" / "integration"
                          / "hub_customer.odcs.yaml").read_text(encoding="utf-8"))
    kept = master_data.columns(hub)
    assert "tax_id" not in kept
    assert {"customer_id", "crm_code", "billing_code", "shop_code", "name"} <= set(kept)
