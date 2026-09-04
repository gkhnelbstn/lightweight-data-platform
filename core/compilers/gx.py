"""Check -> Great Expectations suite (GX Core 1.x JSON shape).

Also emitted, not executed. Same contract, second engine.
"""
from __future__ import annotations

import json

from core.checks import Check
from core.contract import DataContract


def compile_gx(contract: DataContract, checks: list[Check]) -> str:
    exps: list[dict] = []
    for c in checks:
        meta = {"contract_id": c.contract_id, "check_id": c.id,
                "severity": c.severity, "notes": c.description}
        if c.kind == "not_null":
            exps.append({"type": "expect_column_values_to_not_be_null",
                         "kwargs": {"column": c.column}, "meta": meta})
        elif c.kind == "unique":
            exps.append({"type": "expect_column_values_to_be_unique",
                         "kwargs": {"column": c.column}, "meta": meta})
        elif c.kind == "accepted_values":
            exps.append({"type": "expect_column_values_to_be_in_set",
                         "kwargs": {"column": c.column,
                                    "value_set": c.params["values"]},
                         "meta": meta})
        elif c.kind == "range":
            kw = {"column": c.column}
            if c.params.get("min") is not None:
                kw["min_value"] = c.params["min"]
            if c.params.get("max") is not None:
                kw["max_value"] = c.params["max"]
            exps.append({"type": "expect_column_values_to_be_between",
                         "kwargs": kw, "meta": meta})
        elif c.kind == "custom_sql":
            exps.append({"type": "unexpected_rows_expectation",
                         "kwargs": {"unexpected_rows_query": c.params["sql"]},
                         "meta": meta})
    return json.dumps({"name": contract.id.replace(".", "_") + "_suite",
                       "meta": {"generated_from": contract.id,
                                "generator": "dq-spike"},
                       "expectations": exps}, indent=2)
