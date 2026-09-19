"""Integration flows: rows moved between systems neither of which is ours.

Two systems -- an ERP and an accounting package, a CRM and a billing system --
each have a table contract that describes *their* table. The integration is
ours, and it is its own file, one per direction, under `contracts/flows/`
(ADR 0019). That directory is outside the glob every contract reader uses, so
a flow is never windowed, scored or catalogued as if it were a table.

Systems never write to each other. Both sides of a two-way integration meet in
a **hub**: a contract with a `hub` custom property, whose table is the golden
record (ADR 0021). Each system table has a flow into the hub and a flow back:

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

**One system, several tables (#79).** A record can live across several tables
of one system -- a customer and its addresses -- and in one row of another. A
table with several rows per record takes one of them with `match`, which pins
the system's own key columns that the hub's key does not cover. It is a filter
on the way in and a constant on the way back, so both directions must agree,
and a pair is found by table *and* match:

    from: crm.account_address              from: hub.customer
    to: hub.customer                       to: crm.account_address
    match: {ADDR_TYPE: INV}                match: {ADDR_TYPE: INV}
    columns: {code: ACCOUNT_CODE,          columns: {ACCOUNT_CODE: code,
              invoice_city: CITY}                    CITY: invoice_city}

A flow that carries all of what the hub record requires owns the record: its
inserts create it, its deletes delete it. One that carries only part of it
fills and empties that part (core/hub.sql).

What only flows have is a **pair**, the two directions between a system table
and its hub:

* **The maps must be inverses.** `code <- ACCOUNT_CODE` needs
  `ACCOUNT_CODE <- code` back, or an edit is never propagated, or it comes
  back into a different column.
* **A value map has to survive the round trip:** one-to-one, and the way back
  is its inverse.
* **Two systems may not pair with each other directly.** Nothing would
  remember what was sent, so an echo could not be told from an edit.
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
    match: dict = field(default_factory=dict)


def parse(doc: dict) -> Flow:
    return Flow(
        id=str(doc.get("id", "?")),
        target=str(doc.get("to", "")),
        mapping=Mapping(str(doc.get("from", "")), dict(doc.get("columns") or {}),
                        dict(doc.get("values") or {})),
        filled_by_target=frozenset(doc.get("filledByTarget") or []),
        match=dict(doc.get("match") or {}))


def load(directory: Path = FLOWS) -> list[Flow]:
    return [parse(yaml.safe_load(p.read_text(encoding="utf-8")))
            for p in sorted(directory.glob("*.yaml"))]


def hub_of(contract: dict | None) -> dict | None:
    """The `hub` custom property, when this contract is a hub's golden record."""
    for prop in (contract or {}).get("customProperties") or []:
        if prop.get("property") == "hub":
            return prop.get("value") or {}
    return None


def system_of(contract_id: str) -> str:
    """`crm.account` and `crm.account_address` are both the CRM."""
    return contract_id.split(".", 1)[0]


def _keys(contract: dict) -> list[str]:
    return [n for n, p in _properties(contract).items() if p.get("primaryKey")]


def _required(contract: dict) -> set[str]:
    return {n for n, p in _properties(contract).items()
            if p.get("required") or p.get("primaryKey")}


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


def _match_problems(flow: Flow, table: dict, hub: dict, into_hub: bool) -> list[str]:
    """The system table's key, less what the hub's key covers, must be pinned."""
    hub_key = set(_keys(hub))
    if into_hub:
        covered = {src for tgt, src in flow.mapping.columns.items() if tgt in hub_key}
    else:
        covered = {tgt for tgt, src in flow.mapping.columns.items() if src in hub_key}
    out = [f"{flow.id}: match names {c!r}, which {table['id']} does not declare"
           for c in sorted(set(flow.match) - set(_properties(table)))]
    loose = sorted(set(_keys(table)) - covered - set(flow.match))
    if loose:
        out.append(f"{flow.id}: {table['id']} has several rows per record "
                   f"(its key {', '.join(loose)} is not the hub's); pin "
                   f"{'it' if len(loose) == 1 else 'them'} with match, or "
                   f"every row overwrites the same record")
    return out


def _hub_problems(flows: list[Flow], by_id: dict[str, dict]) -> list[str]:
    out: list[str] = []
    for cid, contract in by_id.items():
        hub = hub_of(contract)
        if hub is None:
            continue
        into = [f for f in flows if f.target == cid and f.mapping.reference in by_id]
        authority = hub.get("authority")
        if into and authority not in {f.mapping.reference for f in into}:
            out.append(f"{cid}: authority {authority!r} is not a system with "
                       f"a flow into this hub, so the first sync has no "
                       f"system to take disputed values from")
        required, key = _required(contract), set(_keys(contract))
        by_system: dict[str, list[Flow]] = {}
        for f in into:
            by_system.setdefault(system_of(f.mapping.reference), []).append(f)
        for system, fs in sorted(by_system.items()):
            if not any(required <= set(f.mapping.columns) for f in fs):
                out.append(f"{cid}: no flow from {system} carries everything the "
                           f"record requires ({', '.join(sorted(required))}), so "
                           f"nothing from {system} can create or delete one")
            # A table read with one match must be written back with it.
            for table in sorted({f.mapping.reference for f in fs}):
                ins = {tuple(sorted(f.match.items())) for f in fs
                       if f.mapping.reference == table}
                outs = {tuple(sorted(f.match.items())) for f in flows
                        if f.target == table and f.mapping.reference == cid}
                if outs and ins != outs:
                    out.append(f"{cid}: {table} is read with match {sorted(ins)} "
                               f"but written back with {sorted(outs)}; a row "
                               f"taken one way comes back as another")
            seen: dict[str, str] = {}
            for f in fs:
                for col in sorted(set(f.mapping.columns) - key):
                    if col in seen:
                        out.append(f"{cid}: {col!r} is filled by both {seen[col]} "
                                   f"and {f.id}, two tables of {system}; one of "
                                   f"them would silently win")
                    seen[col] = f.id
    return out


def problems(flows: list[Flow], by_id: dict[str, dict]) -> list[str]:
    """Every reason these flows would not hold, before anything moves."""
    out: list[str] = []
    # A system table is filled per match: two flows writing different rows of
    # one table do not fill anything twice.
    covered: dict[tuple, set[str]] = {}
    for flow in flows:
        missing = [ref for ref in (flow.mapping.reference, flow.target)
                   if ref not in by_id]
        if missing:
            # ADR 0014: an unresolvable reference is reported, never dropped.
            out += [f"{flow.id}: names {ref!r}, which is not a contract this "
                    f"platform loads" for ref in missing]
            continue
        source, target = by_id[flow.mapping.reference], by_id[flow.target]
        here = _properties(target)
        if hub_of(target) is not None:
            # Several systems, and several tables of one system, fill a hub;
            # each needs the key, and the record's owner is checked per system.
            filled: set[str] = set()
            out += column_problems(flow.id, here, flow.mapping, source, filled)
            gaps = sorted(set(_keys(target)) - filled)
            if gaps:
                out.append(f"{flow.id}: the hub key {', '.join(gaps)} is not "
                           f"filled, so a row cannot find its record")
            out += _match_problems(flow, source, target, into_hub=True)
        else:
            rows = (flow.target, tuple(sorted(flow.match.items())))
            out += column_problems(flow.id, here, flow.mapping, source,
                                   covered.setdefault(rows, set()))
            covered[rows] |= set(flow.match)
            if hub_of(source) is not None:
                out += _match_problems(flow, target, source, into_hub=False)

    for (target, match), filled in covered.items():
        into = [f for f in flows
                if f.target == target and tuple(sorted(f.match.items())) == match]
        out += gap_problems(into[0].id, _properties(by_id[target]), filled,
                            set().union(*(f.filled_by_target for f in into)))

    for a in flows:
        for b in flows:
            if ((a.mapping.reference, a.target) == (b.target, b.mapping.reference)
                    and a.match == b.match):
                out += _pair_problems(a, b, by_id)
    return out + _hub_problems(flows, by_id)
