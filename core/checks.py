"""Contract -> canonical checks.

This is the whole thesis of the spike: nobody writes tests by hand. The contract
declares intent; every check below is *derived*. The canonical Check is engine
neutral, so the same check can be compiled to SQL, to a dbt test, or to a Great
Expectations expectation without changing the contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.contract import DataContract, Severity


@dataclass(frozen=True)
class Check:
    id: str
    contract_id: str
    kind: str
    severity: Severity
    description: str
    column: str | None = None
    origin: str = "schema"
    params: dict[str, Any] = field(default_factory=dict)


def derive(contract: DataContract) -> list[Check]:
    out: list[Check] = []
    cid = contract.id

    for f in contract.schema_.fields:
        if f.required:
            out.append(Check(
                id=f"{cid}.{f.name}.not_null", contract_id=cid, kind="not_null",
                severity="critical", column=f.name,
                description=f"{f.name} must not be null",
            ))
        if f.unique:
            out.append(Check(
                id=f"{cid}.{f.name}.unique", contract_id=cid, kind="unique",
                severity="critical", column=f.name,
                description=f"{f.name} must be unique",
            ))
        if f.allowed:
            out.append(Check(
                id=f"{cid}.{f.name}.accepted_values", contract_id=cid,
                kind="accepted_values", severity="major", column=f.name,
                description=f"{f.name} must be one of {f.allowed}",
                params={"values": f.allowed},
            ))
        if f.min is not None or f.max is not None:
            out.append(Check(
                id=f"{cid}.{f.name}.range", contract_id=cid, kind="range",
                severity="major", column=f.name,
                description=f"{f.name} must be between {f.min} and {f.max}",
                params={"min": f.min, "max": f.max},
            ))
        if f.references:
            out.append(Check(
                id=f"{cid}.{f.name}.relationship", contract_id=cid,
                kind="relationship", severity="critical", column=f.name,
                description=(f"{f.name} must exist in "
                             f"{f.references.table}.{f.references.column}"),
                params={"to_table": f.references.table,
                        "to_column": f.references.column},
            ))

    for rule in contract.quality:
        out.append(Check(
            id=f"{cid}.{rule.id}", contract_id=cid, kind=rule.type,
            severity=rule.severity, origin="quality",
            description=rule.description or rule.id,
            params={"sql": rule.sql, "max_lag_days": rule.max_lag_days,
                    "min_rows": rule.min_rows},
        ))
    return out
