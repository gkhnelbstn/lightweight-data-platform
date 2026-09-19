"""Does a system's table still have what its flows map? #84, ADR 0023.

A flow and its contract are the source of truth (invariant 1), so a table
that changed under a running flow is refused and reported, never followed.
What each change does was measured on the demo:

* **a column added** is invisible. A CDC capture instance keeps the columns it
  was created with, so nothing breaks; the contract is merely out of date.
* **a mapped column dropped** empties nothing on the way in, because CDC keeps
  it as NULL in the before *and* after image, and an unchanged field is not an
  edit. A new record arrives without it, though, and on the way out the MERGE
  fails with "Invalid column name". The job goes FAILED with four kilobytes
  of stack trace.
* **a mapped column that CDC does not capture** never sends a change,
  silently. Re-adding a dropped column is the easy way there: the capture
  instance still lists the name, under the old column's id, so a check by
  name passes and the new column is never read. It is compared by id.

So `--apply` refuses a flow whose mapped columns are not all in the live table
and, for a flow reading SQL Server CDC, in its capture instance. The
Integration tab shows the same list while the flow runs.
"""
from __future__ import annotations

from core import flows as flowmod
from core.flow_resume import mssql


def mapped(flow: flowmod.Flow, by_id: dict[str, dict]) -> tuple[dict, list[str], bool]:
    """The system-side table's contract, the columns the flow maps on it, and
    whether the flow reads it (in) or writes it (out)."""
    into = flowmod.hub_of(by_id.get(flow.target)) is not None
    if into:
        return by_id[flow.mapping.reference], sorted(
            {*flow.mapping.columns.values(), *flow.match}), True
    return by_id[flow.target], sorted({*flow.mapping.columns, *flow.match}), False


def server_of(flow: flowmod.Flow, by_id: dict[str, dict]) -> dict | None:
    """The SQL Server a flow's system-side table lives on, if it is one."""
    contract = mapped(flow, by_id)[0]
    return next((s for s in contract.get("servers") or []
                 if s.get("type") == "sqlserver"), None)


def problems(flow: flowmod.Flow, by_id: dict[str, dict],
             timeout: int = 30) -> list[str]:
    contract, columns, reads = mapped(flow, by_id)
    server = server_of(flow, by_id)
    if server is None:
        return []
    schema = server.get("schema", "dbo")
    table = contract["schema"][0]["physicalName"]
    with mssql(server, timeout) as cx:
        cur = cx.cursor()
        live = dict(cur.execute(
            "select name, column_id from sys.columns where object_id = object_id(?)",
            f"{schema}.{table}").fetchall())
        captured = dict(cur.execute(
            "select c.column_name, c.column_id from cdc.captured_columns c "
            "join cdc.change_tables t on t.object_id = c.object_id "
            "where t.capture_instance = ?", f"{schema}_{table}").fetchall()) if reads else live
    out = [f"{flow.id}: {contract['id']} has no column {c} any more"
           for c in columns if c not in live]
    out += [f"{flow.id}: {contract['id']}.{c} is not captured by CDC "
            f"({schema}_{table}), so its changes never arrive"
            for c in columns if c in live and captured.get(c) != live[c]]
    return out
