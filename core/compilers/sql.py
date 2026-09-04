"""Check -> executable Postgres SQL.

Every compiled statement returns exactly two columns: failed_rows, total_rows.
`as_of` makes a run reproducible for any past day, which is what turns a pile of
test results into an actual time series.
"""
from __future__ import annotations

import re

from core.checks import Check
from core.contract import DataContract

def scope_predicate(loaded_at: str, window: str, alias: str = "") -> str:
    """incremental = only what arrived on the run date, cumulative = everything
    up to it. Incremental is what makes an incident visible on the day it lands;
    cumulative dilutes it into a huge denominator."""
    col = f"{alias}.{loaded_at}" if alias else loaded_at
    op = "=" if window == "incremental" else "<="
    return f"{col} {op} %(as_of)s"


def render_scope(sql: str, loaded_at: str, window: str) -> str:
    """Expand {{scope}} / {{scope:alias}} tokens inside contract-authored SQL."""
    def sub(m: "re.Match[str]") -> str:
        return scope_predicate(loaded_at, window, m.group(1) or "")
    return re.sub(r"\{\{\s*scope(?::([a-zA-Z_][\w]*))?\s*\}\}", sub, sql)


def compile_sql(check: Check, contract: DataContract,
                window: str = "incremental") -> str:
    t = f'{contract.server.schema_}."{contract.server.table}"'
    la = contract.server.loaded_at_column
    scope = scope_predicate(la, window)
    col = f'"{check.column}"' if check.column else None
    p = check.params

    if check.kind == "not_null":
        return (f"select count(*) filter (where {col} is null) as failed_rows, "
                f"count(*) as total_rows from {t} where {scope}")

    if check.kind == "unique":
        return (f"select coalesce(sum(c),0) as failed_rows, "
                f"(select count(*) from {t} where {scope}) as total_rows from ("
                f"select count(*) c from {t} where {scope} and {col} is not null "
                f"group by {col} having count(*) > 1) d")

    if check.kind == "accepted_values":
        vals = ", ".join(f"'{v}'" for v in p["values"])
        return (f"select count(*) filter (where {col} is not null "
                f"and {col}::text not in ({vals})) as failed_rows, "
                f"count(*) as total_rows from {t} where {scope}")

    if check.kind == "range":
        conds = []
        if p.get("min") is not None:
            conds.append(f"{col} < {p['min']}")
        if p.get("max") is not None:
            conds.append(f"{col} > {p['max']}")
        pred = " or ".join(conds)
        return (f"select count(*) filter (where {col} is not null and ({pred})) "
                f"as failed_rows, count(*) as total_rows from {t} where {scope}")

    if check.kind == "relationship":
        rt = f'{contract.server.schema_}."{p["to_table"]}"'
        return (f"select count(*) filter (where r.\"{p['to_column']}\" is null "
                f"and s.{col} is not null) as failed_rows, count(*) as total_rows "
                f"from {t} s left join {rt} r "
                f"on r.\"{p['to_column']}\" = s.{col} and r.{la} <= %(as_of)s "
                f"where s.{scope}")

    if check.kind == "freshness":
        # freshness is always evaluated against the full table, never a window
        lag = p.get("max_lag_days") or 1
        return (f"select case when max({la}) is null "
                f"or max({la}) < (%(as_of)s::date - {lag}) then 1 else 0 end "
                f"as failed_rows, 1 as total_rows from {t} "
                f"where {la} <= %(as_of)s")

    if check.kind == "row_count":
        return (f"select case when count(*) < {p.get('min_rows') or 1} then 1 "
                f"else 0 end as failed_rows, 1 as total_rows from {t} "
                f"where {scope}")

    if check.kind == "custom_sql":
        inner = render_scope(p["sql"], la, window)
        inner = inner.replace(":as_of", "%(as_of)s").strip().rstrip(";")
        return (f"select (select failed_rows from ({inner}) q) as failed_rows, "
                f"(select count(*) from {t} where {scope}) as total_rows")

    raise ValueError(f"unsupported check kind: {check.kind}")
