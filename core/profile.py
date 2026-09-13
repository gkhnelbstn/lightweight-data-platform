"""Two numbers per column per day: how many were null, how many were distinct.

Issue #30. The visible half of a catalog's data quality is usually a profiler
-- null fractions, distinct counts, min/max, quantiles, a histogram per column
-- and this deliberately implements the first two and stops.

Why those two and not the rest:

* they are one more pass over a table the runner is already reading, in the
  window it already built. Quantiles and histograms need a sort over the full
  table, which is the expensive half and the half a warehouse engine is
  supposed to do for you. Invariant 6: no new infrastructure without a row
  count to justify it, and a second full scan of eleven tables a day is
  infrastructure whether or not it comes in a container.
* they are the two that say something a check cannot. `field_required` is
  pass/fail on nulls; the null *fraction* is the same measurement continuous,
  which is what shows a column degrading three days before it breaks. A
  distinct count is what says a `unique` check is about to start failing.

This is not deriving checks (invariant 2). Nothing here passes or fails,
nothing reaches `core/scoring.py`, and no profile row ever becomes a
`check_result`. It is a measurement beside the checks, not another one of
them.

**It profiles the window, not the table.** Daily runs are incremental
(invariant 4) and a cumulative profile is the same trap as a cumulative score:
a column that went 90% null this morning is invisible in a mean over
forty-five days. The cost is that a distinct count over one day's arrivals
does not answer "is this key about to collide with one loaded last week" --
that question wants the whole table, which is the scan this refuses to do.
"""
from __future__ import annotations

from datetime import date

from core import engines, store

# The runner calls this, so importing it back at module scope is a cycle --
# the same lazy import core/engines uses for the contract helpers that happen
# to live there. See issue #34.


def profile(contract: dict, server_key: str | None = None) -> list[dict]:
    """Every declared column of every table this contract names."""
    from core.runner import DAILY_SERVER, _server, _tables

    server = (_server(contract, server_key or DAILY_SERVER)
              or _server(contract, "erp"))
    if server is None:
        return []
    schema = server.get("schema", "public")
    engine = engines.engine(server)

    by_table = {(m.get("physicalName") or m["name"]): m
                for m in contract.get("schema", [])}
    out: list[dict] = []
    for _, table in _tables(contract):
        model = by_table.get(table) or {}
        columns = [p["name"] for p in model.get("properties") or []]
        measured = engine.profile_columns(server, schema, table, columns)
        for column, stats in measured.items():
            out.append({"table": table, "column": column, **stats})
    return out


def write(contract: dict, as_of: date, rows: list[dict],
          window: str = "incremental") -> int:
    if not rows:
        return 0
    with store.connect() as dq:
        store.write_profile(dq, as_of, contract["id"], rows, window)
    return len(rows)


def collect(contract: dict, as_of: date, server_key: str | None = None,
            window: str = "incremental") -> int:
    """Profile and store, or say nothing at all.

    Best effort on purpose, like `table_rows`: a profile is a nicety and
    failing to take one must not fail a run that measured the contract fine.
    """
    try:
        return write(contract, as_of, profile(contract, server_key), window)
    except Exception:
        return 0
