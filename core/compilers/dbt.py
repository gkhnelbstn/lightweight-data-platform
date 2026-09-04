"""Check -> dbt schema.yml.

Emitted, not executed. The point is portability: if the team already runs dbt,
the contract can drive dbt's own test suite instead of this engine.
"""
from __future__ import annotations

import yaml

from core.checks import Check
from core.contract import DataContract

_COL_TEST = {
    "not_null": lambda p: "not_null",
    "unique": lambda p: "unique",
    "accepted_values": lambda p: {"accepted_values": {"values": p["values"]}},
    "relationship": lambda p: {"relationships": {
        "to": f"ref('{p['to_table']}')", "field": p["to_column"]}},
    "range": lambda p: {"dbt_utils.accepted_range": {
        k: v for k, v in (("min_value", p.get("min")),
                          ("max_value", p.get("max"))) if v is not None}},
}


def compile_dbt(contract: DataContract, checks: list[Check]) -> str:
    cols: dict[str, dict] = {}
    for f in contract.schema_.fields:
        cols[f.name] = {"name": f.name, "data_type": f.type, "tests": []}
        if f.description:
            cols[f.name]["description"] = f.description

    model_tests: list = []
    for c in checks:
        if c.kind in _COL_TEST and c.column:
            cols[c.column]["tests"].append(_COL_TEST[c.kind](c.params))
        elif c.kind == "custom_sql":
            model_tests.append({"dbt_utils.expression_is_true": {
                "expression": f"/* see contract rule {c.id} */ true",
                "config": {"severity": "error" if c.severity == "critical"
                           else "warn"}}})
        elif c.kind == "freshness":
            model_tests.append({"__freshness__": {
                "loaded_at_field": contract.server.loaded_at_column,
                "error_after": {"count": c.params.get("max_lag_days") or 1,
                                "period": "day"}}})

    model = {"name": contract.server.table,
             "description": contract.info.description,
             "columns": [c for c in cols.values() if c["tests"] or
                         c.get("description")]}
    if model_tests:
        model["tests"] = model_tests
    return yaml.safe_dump({"version": 2, "models": [model]}, sort_keys=False)
