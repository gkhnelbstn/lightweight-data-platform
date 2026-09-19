"""Make a set of flows run: the hub's tables, its systems, and the jobs.

Separate from core/flow_jobs.py because that one only turns contracts into
job configs and is testable without a database; this one talks to Postgres
and to SeaTunnel's REST API, and does nothing a person could not do by hand
in the same order:

1. create the hub database and its merge (core/hub.sql);
2. for each hub contract, the golden table, its inbox and its authority;
3. register every system that has flows both in and out -- only those echo
   the hub's writes back, so only those are awaited (ADR 0021);
4. submit each job that is not already running, by name.

Credentials are never written into a job file: configs carry `${PG_USER}`
and friends, filled from the environment on the way to SeaTunnel.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

import psycopg

from core import flows as flowmod
from core import hub
from core.bootstrap_db import admin_dsn, ensure_database
from core.mapping import _properties

SEATUNNEL = os.getenv("SEATUNNEL_URL", "http://seatunnel:8080")
SECRETS = ("PG_USER", "PG_PASSWORD", "MSSQL_USER", "MSSQL_PASSWORD")


def _fill(config: dict) -> dict:
    text = json.dumps(config)
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
        with psycopg.connect(admin_dsn(server["host"], server.get("port", 5432),
                                       server["database"]), autocommit=True) as cx:
            hub.init(cx)
            hub.register_entity(
                cx, entity, [n for n, p in props.items() if p.get("primaryKey")],
                {n: p["physicalType"] for n, p in props.items()}, spec["authority"])
            into = {f.mapping.reference for f in flows if f.target == cid}
            out = {f.target for f in flows if f.mapping.reference == cid}
            for system in sorted(into & out):
                hub.register_system(cx, entity, system)
        print(f"hub {cid}: {entity} on {server['host']}/{server['database']}, "
              f"systems {sorted(into & out)}")


def apply(by_id: dict[str, dict], flows: list[flowmod.Flow],
          configs: dict[str, dict]) -> None:
    register(by_id, flows)
    already = running()
    for name, config in configs.items():
        if name in already:
            print(f"{name}: already running")
            continue
        answer = _http("POST", f"/submit-job?jobName={name}", _fill(config))
        print(f"{name}: submitted {answer}")
