"""Integration flows: rows moved between systems neither of which is ours.

Two systems -- an ERP and an accounting package, a CRM and a billing system --
each have a table contract that describes *their* table. The integration is
ours, and it is its own file, one per direction, under `contracts/flows/`
(ADR 0019). That directory is outside the glob every contract reader uses, so
a flow is never windowed, scored or catalogued as if it were a table.

Systems never write to each other. Both sides of a two-way integration meet in
a **hub**: a contract with a `hub` custom property, whose table is the golden
record (ADR 0021). Each system has a flow into the hub and a flow back out:

    # contracts/flows/crm_to_hub.yaml       # contracts/flows/hub_to_crm.yaml
    id: crm_to_hub                          id: hub_to_crm
    from: crm.account                       from: hub.customer
    to: hub.customer                        to: crm.account
    columns: {code: ACCOUNT_CODE,           columns: {ACCOUNT_CODE: code,
              active: ACTIVE}                         ACTIVE: active}
    values: {active: {Y: true, N: false}}   values: {ACTIVE: {true: Y, false: N}}
                                            filledByTarget: [CREATED_AT]

The hub decides conflicts by commit time, and the hub contract names the
system whose values win the *first* sync, when both already hold the same
record (`hub: {authority: crm.account}`).

A column map breaks the same ways here as in a `derivedFrom` entry, so the
one-way refusals are core/mapping.py's, shared. What only flows have is a
**pair**, the two directions between a system and its hub:

* **The maps must be inverses.** `code <- ACCOUNT_CODE` needs
  `ACCOUNT_CODE <- code` back. Otherwise an edit is never propagated, or it
  comes back into a different column.
* **A value map has to survive the round trip.** It must be one-to-one, and
  the way back must be its inverse: `'Y'` and `'T'` both becoming `true`
  cannot both come back.
* **Two systems may not pair with each other directly.** Nothing would
  remember what was sent, so an echo could not be told from an edit (ADR
  0021). A pair has a hub on one side.

Several systems filling the same hub column is the point of a hub, so it is
not "filled twice". Each flow into a hub has to fill the hub's required
columns by itself, because any one system can create a record there.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from core.mapping import Mapping, _properties, column_problems, gap_problems

FLOWS = Path(__file__).resolve().parents[1] / "contracts" / "flows"


@dataclass(frozen=True)
class Flow:
    """One direction: `mapping.reference` is the source contract."""
    id: str
    target: str
    mapping: Mapping
    filled_by_target: frozenset[str] = field(default_factory=frozenset)


def parse(doc: dict) -> Flow:
    return Flow(
        id=str(doc.get("id", "?")),
        target=str(doc.get("to", "")),
        mapping=Mapping(str(doc.get("from", "")), dict(doc.get("columns") or {}),
                        dict(doc.get("values") or {})),
        filled_by_target=frozenset(doc.get("filledByTarget") or []))


def load(directory: Path = FLOWS) -> list[Flow]:
    return [parse(yaml.safe_load(p.read_text(encoding="utf-8")))
            for p in sorted(directory.glob("*.yaml"))]


def hub_of(contract: dict | None) -> dict | None:
    """The `hub` custom property, when this contract is a hub's golden record."""
    for prop in (contract or {}).get("customProperties") or []:
        if prop.get("property") == "hub":
            return prop.get("value") or {}
    return None


def _value_problems(a: Flow, b: Flow, mine: str, theirs: str) -> list[str]:
    """`a` turns their value into ours on the way in; `b` takes it back."""
    forward, back = a.mapping.values.get(mine), b.mapping.values.get(theirs)
    if forward is None and back is None:
        return []
    if forward is None or back is None:
        copier = a.id if forward is None else b.id
        return [f"{a.id}: {mine!r} <-> {theirs!r} translates values in one "
                f"direction only ({copier} copies them), so a round trip "
                f"writes a translated value back untranslated"]
    out = [f"{flow.id}: the value map for {col!r} sends two values to one, "
           f"which cannot come back"
           for flow, col, m in ((a, mine, forward), (b, theirs, back))
           if len(set(m.values())) != len(m)]
    if not out and back != {v: k for k, v in forward.items()}:
        out.append(f"{a.id}: the value map for {mine!r} is not the inverse "
                   f"of {b.id}'s for {theirs!r}, so a round trip changes "
                   f"the value")
    return out


def _pair_problems(a: Flow, b: Flow, by_id: dict[str, dict]) -> list[str]:
    if not (hub_of(by_id.get(a.target)) or hub_of(by_id.get(b.target))):
        # Reported by one side only, or `--check` counts it twice.
        return [] if a.id > b.id else [
            f"{a.id}: {a.mapping.reference} and {a.target} write to each "
            f"other directly; two-way integration goes through a hub, or an "
            f"echo cannot be told from an edit (ADR 0021)"]
    out: list[str] = []
    for mine, theirs in a.mapping.columns.items():
        returned = b.mapping.columns.get(theirs)
        if returned is None:
            out.append(f"{a.id}: {mine!r} is filled from {theirs!r} but "
                       f"{b.id} does not map it back, so an edit made at "
                       f"{a.target} is overwritten by the next change at "
                       f"{a.mapping.reference}")
        elif returned != mine:
            out.append(f"{a.id}: {mine!r} is filled from {theirs!r}, but "
                       f"{b.id} fills {theirs!r} from {returned!r}; the round "
                       f"trip moves the value into another column")
        elif a.id < b.id:
            out += _value_problems(a, b, mine, theirs)
    return out


def _hub_problems(flows: list[Flow], by_id: dict[str, dict]) -> list[str]:
    out: list[str] = []
    for cid, contract in by_id.items():
        hub = hub_of(contract)
        if hub is None:
            continue
        sources = {f.mapping.reference for f in flows if f.target == cid}
        authority = hub.get("authority")
        if sources and authority not in sources:
            out.append(f"{cid}: authority {authority!r} is not a system with "
                       f"a flow into this hub, so the first sync has no "
                       f"system to take disputed values from")
    return out


def problems(flows: list[Flow], by_id: dict[str, dict]) -> list[str]:
    """Every reason these flows would not hold, before anything moves."""
    out: list[str] = []
    covered: dict[str, set[str]] = {}
    for flow in flows:
        missing = [ref for ref in (flow.mapping.reference, flow.target)
                   if ref not in by_id]
        if missing:
            # ADR 0014: an unresolvable reference is reported, never dropped.
            out += [f"{flow.id}: names {ref!r}, which is not a contract this "
                    f"platform loads" for ref in missing]
            continue
        target = by_id[flow.target]
        here = _properties(target)
        if hub_of(target) is not None:
            # Every system fills the hub; each must be able to create a record.
            filled: set[str] = set()
            out += column_problems(flow.id, here, flow.mapping,
                                   by_id[flow.mapping.reference], filled)
            out += gap_problems(flow.id, here, filled, set(flow.filled_by_target))
        else:
            out += column_problems(flow.id, here, flow.mapping,
                                   by_id[flow.mapping.reference],
                                   covered.setdefault(flow.target, set()))

    for target, filled in covered.items():
        into = [f for f in flows if f.target == target]
        out += gap_problems(into[0].id, _properties(by_id[target]), filled,
                            set().union(*(f.filled_by_target for f in into)))

    for a in flows:
        for b in flows:
            if (a.mapping.reference, a.target) == (b.target, b.mapping.reference):
                out += _pair_problems(a, b, by_id)
    return out + _hub_problems(flows, by_id)
