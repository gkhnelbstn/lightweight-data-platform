"""What the contract tells ODD about itself.

The pushing is exercised against a running platform; what is pinned here is
what gets pushed, because a catalogue full of the wrong facts is worse than an
empty one — someone would believe it.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.scoring import DIMENSION_WEIGHT
from integrations.odd.curate import (DIMENSION_MEANING, entity_tags,
                                        metadata_values)

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"
ODCS = sorted(CONTRACTS.glob("*.odcs.yaml"))


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# --- the dictionary ---------------------------------------------------------

def test_every_dimension_the_score_weights_has_a_meaning():
    """The glossary is what someone reads after a check fails, so a dimension
    that can appear on a failing check and has no entry is the one case that
    matters."""
    assert set(DIMENSION_MEANING) == set(DIMENSION_WEIGHT)


def test_no_meaning_is_left_empty():
    for name, meaning in DIMENSION_MEANING.items():
        assert meaning.strip().endswith("."), name


# --- what each contract publishes about itself ------------------------------

@pytest.mark.parametrize("path", ODCS, ids=lambda p: p.stem)
def test_a_catalogue_entry_says_who_owns_it_and_what_it_is(path: Path):
    """Owner and purpose are the two questions a catalogue exists to answer,
    and neither a collector nor a profiler can answer either."""
    doc = _load(path)
    assert doc.get("tenant"), f"{path.name} has no owner to publish"
    assert (doc.get("description") or {}).get("purpose"), f"{path.name} has no purpose"
    assert doc.get("domain"), f"{path.name} has no domain to namespace it by"


@pytest.mark.parametrize("path", ODCS, ids=lambda p: p.stem)
def test_every_column_is_described(path: Path):
    """A column description is the thing a catalogue is most often opened for.
    The contract is the only place that knows it, so an undescribed column
    stays undescribed for ever."""
    model = _load(path)["schema"][0]
    missing = [p["name"] for p in model.get("properties") or []
               if not p.get("description")]
    assert not missing, f"{path.name}: {', '.join(missing)}"


@pytest.mark.parametrize("path", ODCS, ids=lambda p: p.stem)
def test_the_tags_are_facts_the_contract_actually_carries(path: Path):
    doc = _load(path)
    tags = entity_tags(doc)
    assert "under-contract" in tags
    assert f"domain:{doc['domain']}" in tags
    replicated = any(p.get("property") == "syncTo"
                     for p in doc.get("customProperties") or [])
    assert ("replicated" in tags) == replicated
    classified = any(p.get("classification")
                     for p in doc["schema"][0].get("properties") or [])
    assert ("has-classified-columns" in tags) == classified


@pytest.mark.parametrize("path", ODCS, ids=lambda p: p.stem)
def test_metadata_is_all_strings(path: Path):
    """ODD drops floats and lists silently -- `contract_sla_min_score` vanished
    that way once. Everything is rendered before it is sent."""
    values = metadata_values(_load(path))
    assert values["contract_id"] == _load(path)["id"]
    for key, value in values.items():
        assert isinstance(value, str), f"{key} is {type(value).__name__}"
        assert value != "", key


def test_a_replicated_contract_publishes_its_sync_rule_as_metadata():
    """The rule is a dict in the contract; it has to arrive readable rather
    than as `{'server': 'replica', ...}` or not at all."""
    doc = _load(CONTRACTS / "erp_customers.odcs.yaml")
    value = metadata_values(doc).get("syncTo")
    assert value and "server=replica" in value
