"""Make a set of flows run: the hub's tables, its systems, and the jobs.

Separate from core/flow_jobs.py because that one only turns contracts into
job configs and is testable without a database; this one talks to Postgres
and to SeaTunnel's REST API, and does nothing a person could not do by hand
in the same order:

1. create the hub database and its merge (core/hub.sql);
2. for each hub contract, the golden table, its inbox and its authority;
3. register every system that has flows both in and out -- only those echo
   the hub's writes back, so only those are awaited (ADR 0021);
4. submit each job that is not already running, by name -- resuming it from
   its last checkpoint when it ran before, or refusing to (core/flow_resume.py,
   ADR 0023).

Credentials are never written into a job file: configs carry `${PG_USER}`
and friends, filled from the environment on the way to SeaTunnel.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request

import psycopg

from core import flow_aggregate, flow_resume, flow_schema
from core import flows as flowmod
from core import hub
from core.bootstrap_db import admin_dsn, ensure_database
from core.mapping import _properties

SEATUNNEL = os.getenv("SEATUNNEL_URL", "http://seatunnel:8080")
SECRETS = ("PG_USER", "PG_PASSWORD", "MSSQL_USER", "MSSQL_PASSWORD")


def _fill(config: dict) -> dict:
    text = json.dumps(config)
    # Empty is as unset as unset. An empty username reaches SeaTunnel as a
    # valid config and comes back as "Factory initialize failed - Unable to
    # create a source", which says nothing about a missing password.
    missing = [n for n in SECRETS if "${" + n + "}" in text and not os.getenv(n)]
    if missing:
        raise SystemExit(f"unset in the environment: {', '.join(sorted(missing))}")
    for name in SECRETS:
        text = text.replace("${" + name + "}", os.getenv(name, ""))
    left = re.findall(r"\$\{[A-Z_]+\}", text)
    if left:
        raise SystemExit(f"unset in the environment: {', '.join(sorted(set(left)))}")
    return json.loads(text)


def _http(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        f"{SEATUNNEL}{path}", method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def running() -> set[str]:
    return {j.get("jobName") for j in (_http("GET", "/running-jobs") or [])}


def register(by_id: dict[str, dict], flows: list[flowmod.Flow]) -> None:
    for cid, contract in by_id.items():
        spec = flowmod.hub_of(contract)
        if spec is None:
            continue
        server = next(s for s in contract["servers"] if s["type"].startswith("postgres"))
        ensure_database(server["host"], server.get("port", 5432), server["database"])
        props = _properties(contract)
        entity = contract["schema"][0]["name"]
        key = [n for n, p in props.items() if p.get("primaryKey")]
        keys = flowmod.keys_of(contract) or None
        with psycopg.connect(admin_dsn(server["host"], server.get("port", 5432),
                                       server["database"]), autocommit=True) as cx:
            hub.init(cx)
            # With codes of their own, the key is the hub's: no row brings it.
            hub.register_entity(
                cx, entity, key, {n: p["physicalType"] for n, p in props.items()},
                spec["authority"], keys=keys,
                required=[n for n, p in props.items()
                          if (p.get("required") or p.get("primaryKey"))
                          and not (keys and n in key)])
            into = {f.mapping.reference for f in flows if f.target == cid}
            out = {f.target for f in flows if f.mapping.reference == cid}
            for system in sorted(into & out):
                # What it receives: the hub columns its flows back read.
                fields = sorted({src for f in flows
                                 if f.mapping.reference == cid and f.target == system
                                 for src in f.mapping.columns.values()})
                link_by = next((list(f.link_by) for f in flows if f.link_by
                                and f.mapping.reference == system and f.target == cid), None)
                hub.register_system(cx, entity, system, fields, link_by)
        print(f"hub {cid}: {entity} on {server['host']}/{server['database']}, "
              f"systems {sorted(into & out)}")
    for flow in flows:
        if flow.aggregates:
            server = flow_aggregate.landing(by_id)
            with psycopg.connect(admin_dsn(server["host"], server.get("port", 5432),
                                           server["database"]), autocommit=True) as cx:
                flow_aggregate.install(cx, flow, by_id)
            print(f"aggregate {flow.id}: summed in flow.{flow.id} on "
                  f"{server['host']}/{server['database']}")


def _hub_dsn(by_id: dict[str, dict], flow: flowmod.Flow) -> str:
    """The database of the hub this flow goes into or comes out of -- or,
    for an aggregate, where it is summed."""
    into = flowmod.hub_of(by_id.get(flow.target)) is not None
    out = flowmod.hub_of(by_id.get(flow.mapping.reference)) is not None
    server = (next(s for s in by_id[flow.target if into else flow.mapping.reference]["servers"]
                   if s["type"].startswith("postgres"))
              if into or out else flow_aggregate.landing(by_id))
    return admin_dsn(server["host"], server.get("port", 5432), server["database"])


def jobs_of(by_id: dict[str, dict], flow: flowmod.Flow) -> list[tuple[str, dict]]:
    """Each job a flow runs, and the table it reads: one for most flows, two
    for an aggregate -- the lines in, and its totals out of the hub."""
    if flow.aggregates:
        return [(flow.id, by_id[flow.mapping.reference]),
                (f"{flow.id}_out", flow_aggregate.landing_contract(flow, by_id))]
    return [(flow.id, by_id[flow.mapping.reference])]


def apply(by_id: dict[str, dict], flows: list[flowmod.Flow],
          configs: dict[str, dict], resnapshot: bool = False,
          only: set[str] | None = None) -> tuple[list[str], list[str]]:
    """Start what is not running, and say what happened: the lines a person
    reads, and the refusals. The CLI prints both; the Integration tab shows
    them beside the flow (#109).

    `only` names the jobs to start. Every flow is still registered, because
    which systems echo the hub's writes is a property of the whole set
    (ADR 0021) -- starting one flow must not make its system look one-way."""
    register(by_id, flows)
    already = running()
    said, refused = [], []
    for flow in flows:
        for name in {n for n, _ in jobs_of(by_id, flow)} & already:
            if only is None or name in only:
                said.append(f"{name}: already running")
        pending = [(n, source) for n, source in jobs_of(by_id, flow)
                   if n not in already and (only is None or n in only)]
        if not pending:
            continue
        # A table that changed under its flow is refused, never followed.
        drift = flow_schema.problems(flow, by_id)
        if drift:
            refused += drift
            continue
        for name, source in pending:
            with psycopg.connect(_hub_dsn(by_id, flow), autocommit=True) as cx:
                row = cx.execute("select job_id from hub.job where flow = %s",
                                 (name,)).fetchone()
                job_id = row[0] if row else None
                checkpoint = flow_resume.last_checkpoint_ms(job_id) if job_id else None
                live = checkpoint and not resnapshot
                oldest = flow_resume.oldest_change_ms(source) if live else None
                gone = bool(live) and flow_resume.slot_missing(source, f"{name}_slot")
                action, why = flow_resume.plan(name, job_id, checkpoint, oldest,
                                               resnapshot, slot_missing=gone)
                if action == "refuse":
                    refused.append(why)
                    continue
                if why:
                    said.append(why)
                query = f"/submit-job?jobName={name}" + (
                    f"&jobId={job_id}&isStartWithSavePoint=true" if action == "resume" else "")
                answer = _http("POST", query, _fill(configs[name]))
                cx.execute("insert into hub.job (flow, job_id) values (%s, %s) "
                           "on conflict (flow) do update set job_id = excluded.job_id, "
                           "submitted_at = now()", (name, int(answer["jobId"])))
            said.append(f"{name}: {'resumed' if action == 'resume' else 'started'} "
                        f"{answer}")
    return said, refused


def stop(names: set[str] | None = None, wait: int = 60) -> list[str]:
    """Stop the running jobs with these names, or every running job -- with a
    savepoint, so the next --apply resumes exactly there.

    Then wait for them to be gone. Taking the savepoint takes seconds, and
    SeaTunnel keeps reporting the job as running while it does: an --apply
    that followed immediately read "already running" and started nothing,
    leaving the flow stopped."""
    stopping = set()
    said = []
    for job in _http("GET", "/running-jobs") or []:
        if names is None or job.get("jobName") in names:
            _http("POST", "/stop-job", {"jobId": int(job["jobId"]),
                                        "isStopWithSavePoint": True})
            stopping.add(job.get("jobName"))
            said.append(f"{job.get('jobName')}: stopped")
    for _ in range(wait):
        left = stopping & running()
        if not left:
            return said
        time.sleep(1)
    return said + [f"{n}: still stopping after {wait}s" for n in sorted(stopping & running())]
