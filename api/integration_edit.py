"""Editing a flow, and running it, from the Integration tab. #109

Invariant 1 decides the shape: the contract is the source of truth and the UI
is an editor for it, so saving a flow rewrites its file in `contracts/flows/`
(ADR 0019) and nothing else. Nothing here talks to SeaTunnel by itself either
-- `core/flow_apply.py` does, the same function the CLI runs, so a flow
started from the screen and one started from `--apply` are the same flow.

Three things are offered and no more:

* **check** -- `core/flows.py`'s refusals and the compile, against the edited
  file without writing it. A flow that would corrupt a round trip is refused
  here exactly as `--check` refuses it.
* **save** -- write the file, once it passes. The comments in it survive:
  ruamel round-trips the document rather than dumping a fresh one.
* **apply / stop** -- what the CLI does. Stopping takes a savepoint, applying
  resumes from it (ADR 0023), and `resnapshot` is the knowing way past a
  checkpoint that cannot be used.

What a flow may say about *running* is two settings (`core/flows.py`'s
`JOB_SETTINGS`): how often SeaTunnel checkpoints, and how fast it may read.
Parallelism is not offered, because a second reader reorders one key's
changes and the hub decides by commit order (ADR 0021).
"""
from __future__ import annotations

import io

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from ruamel.yaml import YAML

from core import flow_apply, flow_jobs, flow_schema
from core import flows as flowmod
from core.mapping import _properties
from api.integration import DIRECTORY, jobs as job_state

router = APIRouter()
ID = "abcdefghijklmnopqrstuvwxyz0123456789_"
yaml_rt = YAML()
yaml_rt.preserve_quotes = True


class FlowDraft(BaseModel):
    id: str
    doc: dict
    check: bool = False


class JobAction(BaseModel):
    flow: str | None = None
    action: str  # apply | stop | restart | resnapshot


def _path(flow_id: str):
    if not flow_id or set(flow_id) - set(ID):
        raise HTTPException(400, "a flow id is lower case letters, digits and _")
    return DIRECTORY / "flows" / f"{flow_id}.yaml"


def _columns(contract: dict) -> list[dict]:
    return [{"name": name, "type": p.get("physicalType"),
             "required": bool(p.get("required") or p.get("primaryKey")),
             "key": bool(p.get("primaryKey")), "classification": p.get("classification"),
             "description": p.get("description")}
            for name, p in _properties(contract).items()]


def _sides(by_id: dict[str, dict], doc: dict) -> dict:
    """Both contracts a flow names, as the form offers them: their columns,
    and whether each end is a hub."""
    out = {}
    for side, cid in (("from", doc.get("from")), ("to", doc.get("to"))):
        contract = by_id.get(cid)
        out[side] = None if contract is None else {
            "id": cid, "name": contract.get("name"),
            "hub": flowmod.hub_of(contract) is not None,
            "keys": flowmod.keys_of(contract),
            "columns": _columns(contract)}
    return out


def _compiled(by_id: dict[str, dict], flows: list[flowmod.Flow],
              flow_id: str) -> tuple[dict, list[str]]:
    """What this flow becomes in SeaTunnel's words, or why it does not."""
    try:
        every = flow_jobs.jobs(by_id, flows)
    except Exception as exc:  # a compile that cannot happen is a refusal too
        return {}, [f"{flow_id}: does not compile ({exc.__class__.__name__}: {exc})"]
    return {n: c for n, c in every.items() if n in (flow_id, f"{flow_id}_out")}, []


def _merge(keep, doc: dict) -> None:
    """Make the loaded document say what `doc` says, key by key.

    Not `keep = doc`: ruamel hangs a comment off the node it follows, so
    replacing a whole mapping takes the comments inside it with it. Changing
    one value at a time leaves every comment the edit did not touch -- which
    is most of them, since a flow file's comments explain the map rather than
    a single value."""
    for key in [k for k in keep if k not in doc]:
        del keep[key]
    for key, value in doc.items():
        if isinstance(value, dict) and isinstance(keep.get(key), dict):
            _merge(keep[key], value)
        elif key not in keep or keep[key] != value:
            keep[key] = value


@router.get("/api/integration/flow")
def flow(id: str) -> dict:
    """One flow: its file, both ends' columns, the jobs it compiles to, and
    anything that would refuse it right now."""
    by_id, flows = flow_jobs.load(DIRECTORY)
    flow = next((f for f in flows if f.id == id), None)
    if flow is None:
        raise HTTPException(404, f"no flow {id!r}")
    path = _path(id)
    doc = yaml_rt.load(path.read_text(encoding="utf-8"))
    running, _ = job_state()
    compiled, broken = _compiled(by_id, flows, id)
    return {"id": id, "file": str(path.relative_to(DIRECTORY.parent)),
            "doc": dict(doc), "yaml": path.read_text(encoding="utf-8"),
            "sides": _sides(by_id, dict(doc)), "jobs": compiled,
            "settings": flowmod.JOB_SETTINGS,
            "state": {name: running.get(name) for name in compiled},
            "problems": [p for p in flowmod.problems(flows, by_id)
                         if p.startswith(f"{id}:")] + broken,
            "drift": flow_schema.problems(flow, by_id, timeout=3)}


@router.get("/api/integration/contracts")
def contracts() -> dict:
    """The contracts a new flow can name, so the form is a choice and not a
    text field."""
    by_id, _ = flow_jobs.load(DIRECTORY)
    return {"contracts": [{"id": cid, "name": c.get("name"),
                           "hub": flowmod.hub_of(c) is not None,
                           "columns": _columns(c)}
                          for cid, c in sorted(by_id.items())]}


@router.post("/api/integration/flow")
def save(draft: FlowDraft) -> dict:
    """Refuse first, then write. `check: true` refuses without writing."""
    path = _path(draft.id)
    by_id, flows = flow_jobs.load(DIRECTORY)
    doc = {"id": draft.id, **{k: v for k, v in draft.doc.items()
                              if k != "id" and v not in (None, {}, [])}}
    edited = flowmod.parse(doc)
    others = [f for f in flows if f.id != draft.id]
    compiled, broken = _compiled(by_id, others + [edited], draft.id)
    problems = flowmod.problems(others + [edited], by_id) + broken
    if problems or draft.check:
        return {"saved": False, "problems": problems, "jobs": compiled}
    # The file, not a fresh dump: a flow file's comments say why a map is the
    # way it is, and an edit through the screen must not delete them.
    keep = yaml_rt.load(path.read_text(encoding="utf-8")) if path.exists() else {}
    _merge(keep, doc)
    out = io.StringIO()
    yaml_rt.dump(keep, out)
    path.write_text(out.getvalue(), encoding="utf-8")
    return {"saved": True, "problems": [], "jobs": compiled,
            "yaml": path.read_text(encoding="utf-8")}


@router.post("/api/integration/jobs")
def run(action: JobAction) -> dict:
    """Apply or stop, as the CLI does it. `restart` picks up an edit: it
    stops with a savepoint and starts again from it. `resnapshot` throws the
    checkpoint away and re-reads the table, knowingly (ADR 0023)."""
    if action.action not in ("apply", "stop", "restart", "resnapshot"):
        raise HTTPException(400, f"{action.action!r} is not apply, stop, restart "
                                 f"or resnapshot")
    by_id, flows = flow_jobs.load(DIRECTORY)
    chosen = [f for f in flows if action.flow in (None, f.id)]
    if not chosen:
        raise HTTPException(404, f"no flow {action.flow!r}")
    names = {n for f in chosen for n, _ in flow_apply.jobs_of(by_id, f)}
    said: list[str] = []
    if action.action != "apply":
        said += flow_apply.stop(names)
    if action.action == "stop":
        return {"said": said, "refused": []}
    refusals = flowmod.problems(flows, by_id)
    if refusals:
        return {"said": said, "refused": refusals}
    lines, refused = flow_apply.apply(
        by_id, flows, flow_jobs.jobs(by_id, flows),
        resnapshot=action.action == "resnapshot",
        only=names if action.flow else None)
    return {"said": said + lines, "refused": refused}
