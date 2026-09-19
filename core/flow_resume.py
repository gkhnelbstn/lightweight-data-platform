"""Where a stopped flow resumes, and when it must not. #84, ADR 0023.

SeaTunnel keeps no job across its own restart: after one, every flow is gone
(measured). A flow submitted again from scratch re-reads its whole table, and
the hub cannot tell a re-read from an edit. A record both systems hold arrives
as an INSERT, so the first-sync rule decides it: the authority's value stands,
and an edit the other system made while the flow was down is reverted. A row
deleted meanwhile is not in the re-read at all, so its delete never reaches the
hub. Both were measured on the demo, and both are silent.

So a flow resumes from its last checkpoint, under the job id it had, and reads
what changed while it was down in commit order. Two things make that unsafe,
and SeaTunnel reports neither:

* **no checkpoint for the id** -- it starts from scratch anyway;
* **a SQL Server source whose CDC retention ran out while the flow was down**
  -- it resumes past the purged changes.

Both refuse. `--resnapshot` is the deliberate way through, and says what it
costs.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

# SeaTunnel's checkpoint storage, mounted read-only here (compose.demo.yaml).
CHECKPOINTS = Path(os.getenv("SEATUNNEL_CHECKPOINTS", "/seatunnel/checkpoints"))


def last_checkpoint_ms(job_id: int, root: Path | None = None) -> int | None:
    """When the job last checkpointed. Its files are `<epoch ms>-...ser`, and
    a running job checkpoints every few seconds, busy or not."""
    folder = (root or CHECKPOINTS) / str(job_id)
    if not folder.is_dir():
        return None
    stamps = [p.name.split("-", 1)[0] for p in folder.glob("*.ser")]
    return max((int(s) for s in stamps if s.isdigit()), default=None)


def _at(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def plan(flow: str, job_id: int | None, checkpoint_ms: int | None,
         oldest_change_ms: int | None, resnapshot: bool) -> tuple[str, str | None]:
    """`fresh`, `resume` or `refuse`, and what to say about it."""
    if job_id is None:
        return "fresh", None
    if resnapshot:
        return "fresh", (f"{flow}: re-read from scratch as asked. A record both systems "
                         f"hold goes to the authority, and a row deleted while it was "
                         f"down stays deleted on one side only")
    if checkpoint_ms is None:
        return "refuse", (f"{flow}: job {job_id} has no checkpoint left, and resuming it "
                          f"would silently re-read the table from scratch: edits made "
                          f"meanwhile would lose to the authority and deletes would be "
                          f"missed. --resnapshot accepts that")
    if oldest_change_ms is not None and oldest_change_ms > checkpoint_ms:
        return "refuse", (f"{flow}: its CDC keeps changes from {_at(oldest_change_ms)} "
                          f"on, but the flow stopped at {_at(checkpoint_ms)}; what "
                          f"changed in between was purged by retention and would be "
                          f"skipped silently. --resnapshot re-reads the table instead")
    return "resume", None


def mssql(server: dict, timeout: int = 30):
    """A connection to a system's SQL Server: as the flows' own user when
    applying them, as the platform's reporting user from the API."""
    import pyodbc
    driver = os.getenv("DATACONTRACT_SQLSERVER_DRIVER", "ODBC Driver 18 for SQL Server")
    user = os.getenv("MSSQL_USER") or os.getenv("DATACONTRACT_SQLSERVER_USERNAME", "sa")
    password = (os.getenv("MSSQL_PASSWORD")
                or os.getenv("DATACONTRACT_SQLSERVER_PASSWORD", ""))
    return pyodbc.connect(
        f"DRIVER={{{driver}}};SERVER={server['host']},{server.get('port', 1433)};"
        f"DATABASE={server['database']};UID={user};PWD={password};"
        "TrustServerCertificate=yes;Encrypt=no", timeout=timeout)


def oldest_change_ms(contract: dict) -> int | None:
    """For a SQL Server source, when the oldest change its CDC still keeps was
    committed: anything earlier was purged by retention. None for any other
    source -- a Postgres slot keeps its WAL until it is read."""
    server = next((s for s in contract.get("servers") or []
                   if s.get("type") == "sqlserver"), None)
    if server is None:
        return None
    instance = f"{server.get('schema', 'dbo')}_{contract['schema'][0]['physicalName']}"
    with mssql(server) as cx:
        # The mapping table is in the server's local time; shift it to UTC.
        row = cx.cursor().execute(
            "select datediff_big(ms, '1970-01-01', dateadd(minute, "
            "datediff(minute, getdate(), getutcdate()), "
            "sys.fn_cdc_map_lsn_to_time(sys.fn_cdc_get_min_lsn(?))))", instance).fetchone()
    return row[0] if row and row[0] is not None else None
