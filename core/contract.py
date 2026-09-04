"""Data contract model. Deliberately engine-agnostic: the contract is the source
of truth, every check and every generated artifact is derived from it."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

Severity = Literal["critical", "major", "minor"]
SEVERITY_WEIGHT: dict[str, int] = {"critical": 5, "major": 3, "minor": 1}


class Reference(BaseModel):
    table: str
    column: str


class Field_(BaseModel):
    name: str
    type: str
    description: str | None = None
    required: bool = False
    unique: bool = False
    allowed: list[str] | None = None
    min: float | None = None
    max: float | None = None
    references: Reference | None = None


class QualityRule(BaseModel):
    id: str
    type: Literal["freshness", "custom_sql", "row_count"]
    severity: Severity = "major"
    description: str | None = None
    sql: str | None = None
    max_lag_days: int | None = None
    min_rows: int | None = None


class Info(BaseModel):
    title: str
    owner: str
    domain: str | None = None
    description: str | None = None


class Server(BaseModel):
    type: str
    database: str
    schema_: str = Field(default="public", alias="schema")
    table: str
    loaded_at_column: str = "loaded_at"

    model_config = {"populate_by_name": True}


class Schema(BaseModel):
    fields: list[Field_]


class SLA(BaseModel):
    min_score: float = 0.95


class DataContract(BaseModel):
    apiVersion: str
    kind: str
    id: str
    info: Info
    server: Server
    schema_: Schema = Field(alias="schema")
    quality: list[QualityRule] = []
    sla: SLA = SLA()

    model_config = {"populate_by_name": True}

    @classmethod
    def load(cls, path: str | Path) -> "DataContract":
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))


def load_all(directory: str | Path = "contracts") -> list[DataContract]:
    return sorted(
        (DataContract.load(p) for p in Path(directory).glob("*.contract.yaml")),
        key=lambda c: c.id,
    )
