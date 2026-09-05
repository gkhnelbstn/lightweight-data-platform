"""The contracts are ODCS now, so what is worth testing changed.

Deriving checks and compiling SQL is datacontract-cli's job and is tested
there. What is left here is the small amount this project still decides: that
the contracts are valid ODCS, that every one of them can be windowed, and that
the pieces the window depends on line up. None of it needs a database.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"
ODCS = sorted(CONTRACTS.glob("*.odcs.yaml"))

pytestmark = pytest.mark.skipif(not ODCS, reason="no ODCS contracts")


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", ODCS, ids=lambda p: p.stem)
def test_contract_is_odcs(path: Path):
    doc = _load(path)
    assert doc.get("kind") == "DataContract"
    assert str(doc.get("apiVersion", "")).startswith("v3."), doc.get("apiVersion")
    for required in ("id", "servers", "schema"):
        assert doc.get(required), f"{path.name} has no {required}"


@pytest.mark.parametrize("path", ODCS, ids=lambda p: p.stem)
def test_contract_validates_against_the_standard(path: Path):
    """The point of adopting ODCS is that someone else defines what is valid."""
    odcs = pytest.importorskip("open_data_contract_standard.model")
    doc = _load(path)
    doc.pop("_path", None)
    assert odcs.OpenDataContractStandard.model_validate(doc)


@pytest.mark.parametrize("path", ODCS, ids=lambda p: p.stem)
def test_every_quality_rule_declares_a_dimension(path: Path):
    """The score weights by dimension, so an undeclared one is scored as
    `unknown` -- the lightest weight, which quietly discounts the rule."""
    from core.scoring import DIMENSION_WEIGHT
    for model in _load(path).get("schema", []):
        for rule in model.get("quality") or []:
            dim = rule.get("dimension")
            assert dim, f"{rule.get('description')!r} has no dimension"
            assert dim in DIMENSION_WEIGHT, f"unweighted dimension {dim!r}"


def test_postgres_contracts_carry_a_daily_server():
    """The window is a second `servers` entry pointing at a schema of views.
    Without it a contract is scored over its whole history, which is the thing
    that made the trend unreadable."""
    from core.runner import DAILY_SERVER
    for path in ODCS:
        doc = _load(path)
        servers = {s.get("server"): s for s in doc.get("servers", [])}
        if not any(s.get("type") in ("postgres", "postgresql")
                   for s in servers.values()):
            continue
        assert DAILY_SERVER in servers, f"{path.name} cannot be windowed"
        assert servers[DAILY_SERVER]["schema"] != servers["erp"]["schema"]


def test_window_schema_is_not_the_source_schema():
    """Views named after their tables in the same schema would be a loop."""
    from core.runner import WINDOW_SCHEMA
    assert WINDOW_SCHEMA != "public"
