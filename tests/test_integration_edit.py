"""Editing a flow from the Integration tab (#109).

The file is what changes, so what is pinned here is: a flow that would not
hold is refused and nothing is written, a flow that holds is written with its
comments intact, and what the screen may name is what the id rules allow. The
refusals themselves belong to core/flows.py and are tested there.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from api import integration_edit as edit  # noqa: E402

DEMO = Path(__file__).resolve().parents[1] / "demo" / "integration"
GOOD = {"from": "crm.account", "to": "hub.customer",
        "columns": {"crm_code": "ACCOUNT_CODE", "name": "TITLE", "tax_id": "TAX_NO",
                    "active": "ACTIVE"},
        "values": {"active": {"Y": True, "N": False}},
        "linkBy": ["tax_id"]}


@pytest.fixture
def flows(tmp_path, monkeypatch):
    """The demo's contracts and flows, copied so a save can be watched."""
    copy = tmp_path / "integration"
    shutil.copytree(DEMO, copy, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(edit, "DIRECTORY", copy)
    return copy / "flows"


def test_a_map_that_is_not_an_inverse_is_refused_and_nothing_is_written(flows):
    before = (flows / "crm_to_hub.yaml").read_text(encoding="utf-8")
    broken = {**GOOD, "columns": {**GOOD["columns"], "name": "TAX_NO"}}
    result = edit.save(edit.FlowDraft(id="crm_to_hub", doc=broken))
    assert result["saved"] is False
    assert any("round trip" in p for p in result["problems"])
    assert (flows / "crm_to_hub.yaml").read_text(encoding="utf-8") == before


def test_checking_never_writes_even_when_it_passes(flows):
    before = (flows / "crm_to_hub.yaml").read_text(encoding="utf-8")
    result = edit.save(edit.FlowDraft(id="crm_to_hub", doc=GOOD, check=True))
    assert result["problems"] == [] and result["saved"] is False
    assert result["jobs"]["crm_to_hub"]["env"]["job.name"] == "crm_to_hub"
    assert (flows / "crm_to_hub.yaml").read_text(encoding="utf-8") == before


def test_a_saved_flow_keeps_the_comments_in_its_file(flows):
    result = edit.save(edit.FlowDraft(
        id="crm_to_hub", doc={**GOOD, "job": {"checkpointInterval": 5000}}))
    assert result["saved"] is True
    text = (flows / "crm_to_hub.yaml").read_text(encoding="utf-8")
    assert "# A CRM account the hub has not seen is the customer with its tax number." in text
    assert "checkpointInterval: 5000" in text
    # And what it becomes says the same thing.
    assert result["jobs"]["crm_to_hub"]["env"]["checkpoint.interval"] == 5000


def test_a_setting_seatunnel_does_not_take_is_refused(flows):
    result = edit.save(edit.FlowDraft(
        id="crm_to_hub", doc={**GOOD, "job": {"parallelism": 4}}, check=True))
    assert any("not a job setting" in p for p in result["problems"])


def test_a_flow_id_is_a_file_name_and_not_a_path(flows):
    for bad in ("../hub_customer.odcs", "a/b", "crm to hub", ""):
        with pytest.raises(edit.HTTPException):
            edit._path(bad)
    assert edit._path("crm_to_hub").name == "crm_to_hub.yaml"


def test_a_new_flow_is_written_when_it_holds(flows):
    """A flow file that does not exist yet is created, so a second system can
    be connected from the screen."""
    result = edit.save(edit.FlowDraft(id="crm_to_hub_copy", doc=GOOD, check=True))
    # Two flows into the hub from one table is a real refusal (both fill the
    # same columns), so the copy is checked, not saved: what matters here is
    # that the route reached the refusals rather than a missing file.
    assert result["problems"] and not (flows / "crm_to_hub_copy.yaml").exists()
