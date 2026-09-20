"""Running one contract's checks from the screen. #113

The daily unit is `core/runner.py`, on a schedule. What was missing is the
other occasion: someone has just fixed the data, or just added a rule, and
wants to know now -- and until this they had to find a terminal and a command
line with two environment variables in it.

It is the same function the CLI calls, for one contract and today, so a run
started here and a run started by the schedule are the same run and land in
the same tables. Nothing about what a check *is* lives here.

**One at a time, per contract.** A second run of the same contract while one
is in flight would write the same day twice (`write_results` replaces within
a day, so the last one wins) and double the load on the source for no answer.

**ponytail: the state is in this process.** A restart forgets that a run was
in flight; the results it had already written are in the database, and the
next look at the contract shows them. A durable queue is what to build when
there is a second API process, and not before.
"""
from __future__ import annotations

import threading
from datetime import date

from fastapi import APIRouter, HTTPException

router = APIRouter()
RUNS: dict[str, dict] = {}
LOCK = threading.Lock()


def _run(contract_id: str) -> None:
    from core.runner import load_contracts, run
    try:
        contracts = [c for c in load_contracts() if c.get("id") == contract_id]
        [result] = run(date.today(), contracts)
        state = {"state": "done", "result": result}
    except Exception as exc:   # a failed run is an answer too, and it is shown
        state = {"state": "failed", "error": f"{exc.__class__.__name__}: {exc}"}
    with LOCK:
        RUNS[contract_id] = {**RUNS.get(contract_id, {}), **state,
                             "finished": date.today().isoformat()}


@router.post("/api/contracts/{contract_id}/run")
def start(contract_id: str) -> dict:
    """Start today's run for this contract, and answer straight away."""
    from core.runner import load_contracts
    if not any(c.get("id") == contract_id for c in load_contracts()):
        raise HTTPException(404, f"no contract {contract_id!r}")
    with LOCK:
        if (RUNS.get(contract_id) or {}).get("state") == "running":
            raise HTTPException(409, "this contract is already running")
        RUNS[contract_id] = {"state": "running", "started": date.today().isoformat()}
    threading.Thread(target=_run, args=(contract_id,), daemon=True).start()
    return RUNS[contract_id]


@router.get("/api/contracts/{contract_id}/run")
def state(contract_id: str) -> dict:
    """What that run is doing. `idle` for a contract nobody started here."""
    return RUNS.get(contract_id) or {"state": "idle"}
