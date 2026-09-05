"""Table and column statistics for ODD's dataset-stats ingestion.

ODD's *run* model has nowhere to put a number -- that much is true and is why
`status_reason` carries the volume signal as text. Its *dataset* model does:
`DataSet.rows_number` for the table, and `POST /ingestion/entities/datasets/stats`
for per-column `nulls_count`, `unique_count`, `low_value`, `high_value`. Those
are stored as structured JSON and rendered on the column panel, so the numbers
our checks already compute stop being strings the moment they are sent here
instead.

This is a profile, not a check: it is the whole table as of now, unwindowed.
The contract still decides *which* columns are worth profiling -- the ones it
names -- so nothing here invents a rule.
"""
from __future__ import annotations

from typing import Any

import psycopg

from core.contract import DataContract

# ODD splits column stats by type, and the field must match the column or the
# panel stays empty. Anything outside these two buckets (text, boolean, dates)
# has its own stat shape that carries no null/unique count worth the query.
_NUMERIC = ("bigint", "integer", "int", "smallint")
_DECIMAL = ("numeric", "decimal", "float", "double precision", "real")


def _stat_key(pg_type: str) -> str | None:
    t = pg_type.lower()
    if t in _NUMERIC:
        return "integer_stats"
    if t in _DECIMAL:
        return "number_stats"
    return None


def table_columns(cx: psycopg.Connection, contract: DataContract) -> list[tuple[str, str]]:
    """Every column the table really has, in its real order.

    The contract governs a subset -- it names the columns with rules on them,
    not the whole table. Pushing that subset as the dataset's structure makes
    us disagree with odd-collector, and ODD versions a dataset every time its
    structure changes: collector says 7 columns, we say 6, and the pair mint a
    new revision on every cycle. ODD does not version an unchanged structure,
    so agreeing about the column list is the whole fix.
    """
    return cx.execute(
        """select column_name, data_type from information_schema.columns
           where table_schema = %s and table_name = %s
           order by ordinal_position""",
        (contract.server.schema_, contract.server.table)).fetchall()


def profile(cx: psycopg.Connection, contract: DataContract) -> tuple[int, dict[str, dict]]:
    """Return `(rows_number, {column_name: field_stat})` for one contract.

    One pass per profiled column rather than one wide query: the column list
    comes from a contract that a person edits, and a single statement built by
    concatenating those names is the place an injection would live.
    """
    table = f'{contract.server.schema_}."{contract.server.table}"'
    rows = cx.execute(f"select count(*) from {table}").fetchone()[0]

    stats: dict[str, dict] = {}
    for f in contract.schema_.fields:
        key = _stat_key(f.type)
        if key is None:
            continue
        nulls, uniques, low, high = cx.execute(
            f'select count(*) filter (where "{f.name}" is null),'
            f' count(distinct "{f.name}"), min("{f.name}"), max("{f.name}")'
            f" from {table}").fetchone()
        stats[f.name] = {key: {
            "nulls_count": int(nulls), "unique_count": int(uniques),
            "low_value": None if low is None else float(low),
            "high_value": None if high is None else float(high)}}
    return int(rows), stats


def stats_payload(dataset_oddrn: str, column_oddrns: dict[str, str],
                  stats: dict[str, dict]) -> dict[str, Any]:
    """The body `POST /ingestion/entities/datasets/stats` expects.

    Keyed by column ODDRN, so a column ODD has not seen is simply ignored --
    which is what makes this safe to send before the collector has run.
    """
    return {"items": [{
        "dataset_oddrn": dataset_oddrn,
        "fields": {column_oddrns[name]: stat
                   for name, stat in stats.items() if name in column_oddrns}}]}
