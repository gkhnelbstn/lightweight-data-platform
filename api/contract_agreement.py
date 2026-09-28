"""A contract's agreement as its page shows it: who, what for, how often, and what was promised.

The panel showed a contract's columns and checks and nothing of what makes
it a contract, so "who owns this", "how fresh is it promised to be" and "may I
use it for X" meant opening the YAML (#144). Every answer here is read from
the contract's own ODCS fields -- `team`, `description`, `slaProperties`,
`roles`, `support` -- and nothing is edited (invariant 1). The one other input
is what the runs measured, set beside the promise it answers: the score floor
against the latest score, the check frequency against the last run.

A promise nothing here measures (latency, a time of availability) is shown
as promised and marked unmeasured, rather than left out or guessed at.
"""
from __future__ import annotations

from datetime import date, timedelta

from core.mapping import _properties

# Custom properties that are the contract's terms of use: ODCS has no field
# for these, so they keep plain names and are shown in this order.
TERMS = ("dataClassification", "privacy", "breakingChangePolicy", "deprecationPolicy")
_UNITS = {"h": 1 / 24, "hour": 1 / 24, "hours": 1 / 24, "d": 1, "day": 1, "days": 1,
          "w": 7, "week": 7, "weeks": 7, "y": 365, "year": 365, "years": 365}


def _custom(doc: dict) -> dict:
    return {p.get("property"): p.get("value") for p in doc.get("customProperties") or []}


def period(value, unit) -> timedelta | None:
    """An SLA's `value` + `unit` as a length of time; None for anything else."""
    days = _UNITS.get(str(unit or "").lower())
    try:
        return timedelta(days=float(value) * days) if days else None
    except (TypeError, ValueError):
        return None


def measured(prop: dict, scores: list[dict], checks: list[dict], today: date) -> dict | None:
    """What the runs say about one promise, or None where nothing measures it.

    `scores` are the contract's daily rows, newest first; `checks` the latest
    result of each check."""
    name = prop.get("property")
    if name in ("minScore", "min_score"):
        if not scores:
            return None
        latest = scores[0]
        return {"kind": "score", "value": float(latest["score"]),
                "met": bool(latest["sla_met"]), "as_of": str(latest["run_at"])}
    if name == "frequency":
        every = period(prop.get("value"), prop.get("unit"))
        if every is None or not scores:
            return None
        age = (today - scores[0]["run_at"]).days
        # A daily run is dated the day it checks, so yesterday's is on time.
        return {"kind": "last_run", "days": age, "met": age <= max(every.days, 1),
                "runs": len(scores), "as_of": str(scores[0]["run_at"])}
    if name == "completeness":
        mine = [c for c in checks if c.get("dimension") == "completeness"]
        if not mine:
            return None
        passed = sum(c.get("status") == "pass" for c in mine)
        return {"kind": "checks", "passed": passed, "total": len(mine),
                "met": passed == len(mine)}
    if name == "availability" and scores:
        # The source answering when it was checked: the one availability
        # this platform sees. An errored run is one it did not.
        ok = sum(not s.get("checks_errored") for s in scores)
        target = float(prop["value"]) if isinstance(prop.get("value"), (int, float)) else None
        return {"kind": "answered", "ok": ok, "total": len(scores),
                "met": None if target is None else ok * 100 >= target * len(scores)}
    return None


def team(doc: dict) -> dict:
    """ODCS 3.1's team object, or the 3.0 array it replaced, as one shape."""
    raw = doc.get("team")
    members = raw.get("members") if isinstance(raw, dict) else raw
    return {"name": raw.get("name") if isinstance(raw, dict) else None,
            "members": [{"username": m.get("username"), "name": m.get("name"),
                         "role": m.get("role")} for m in members or []]}


def _table(contract: dict) -> str:
    model = (contract.get("schema") or [{}])[0]
    return str(model.get("physicalName") or model.get("name") or "")


def _host(contract: dict) -> tuple:
    server = next(iter(contract.get("servers") or []), {})
    return server.get("host"), server.get("database")


def relations(doc: dict, contracts: list[dict]) -> dict:
    """Foreign keys both ways: the tables this one points at, and the ones
    pointing at it -- each with the contract that covers it, where one does."""
    same = [c for c in contracts if _host(c) == _host(doc)]
    by_table = {_table(c).lower(): c.get("id") for c in same}

    def outgoing(contract: dict) -> list[dict]:
        out = []
        for column, prop in _properties(contract).items():
            for rel in prop.get("relationships") or []:
                target = str(rel.get("to", ""))
                if rel.get("type", "foreignKey") != "foreignKey" or "." not in target:
                    continue
                table, to_column = target.rsplit(".", 1)
                out.append({"column": column, "table": table, "to_column": to_column,
                            "contract": by_table.get(table.lower())})
        return out

    mine = _table(doc).lower()
    incoming = [{"column": r["column"], "table": _table(c), "to_column": r["to_column"],
                 "contract": c.get("id")}
                for c in same if c.get("id") != doc.get("id")
                for r in outgoing(c) if r["table"].lower() == mine]
    return {"references": outgoing(doc), "referenced_by": incoming}


def agreement(doc: dict, contracts: list[dict], scores: list[dict], checks: list[dict],
              today: date | None = None) -> dict:
    """Everything the contract's page shows about the agreement itself."""
    today = today or date.today()
    custom = _custom(doc)
    description = doc.get("description") or {}
    server = next((s for s in doc.get("servers") or [] if s.get("server") == "erp"),
                  next(iter(doc.get("servers") or []), {}))
    sla = [{**{k: p.get(k) for k in ("property", "value", "unit", "element", "driver",
                                      "description", "scheduler", "schedule")},
            "measured": measured(p, scores, checks, today)}
           for p in doc.get("slaProperties") or []]
    return {
        "status": doc.get("status"), "version": doc.get("version"),
        "api_version": doc.get("apiVersion"), "domain": doc.get("domain"),
        "tags": doc.get("tags") or [], "owner": doc.get("tenant"), "team": team(doc),
        "description": {k: description.get(k) for k in ("purpose", "usage", "limitations")},
        "use_cases": custom.get("useCases") or [],
        "semantics": custom.get("semantics") or [],
        "terms": [{"key": k, "value": custom[k]} for k in TERMS if custom.get(k)],
        "roles": doc.get("roles") or [], "support": doc.get("support") or [],
        "sla": sla,
        # Names, engines and places, never a credential: servers carry none.
        "location": {k: server.get(k) for k in ("type", "host", "port", "database", "schema")}
                    | {"table": _table(doc)},
        "runs": [{"as_of": str(s["run_at"]), "met": bool(s["sla_met"]),
                  "errored": bool(s.get("checks_errored"))} for s in reversed(scores)],
        **relations(doc, contracts),
    }
