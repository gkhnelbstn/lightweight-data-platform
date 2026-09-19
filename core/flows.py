"""Integration flows: rows moved between two systems neither of which is ours.

Two systems neither of which is ours -- an ERP and an accounting package, a
CRM and a billing system -- each have a table contract that describes *their*
table. The integration between them is ours, and it is its own file. It is
not a line in either system's contract, which should not have to know that
someone copies its rows elsewhere. One file per direction, under
`contracts/flows/`. That directory is outside the glob every contract reader
uses, so a flow is never windowed, scored or catalogued as if it were a
table (ADR 0019):

    # contracts/flows/crm_to_billing.yaml
    id: crm_to_billing
    from: crm.account                 # the source table's contract
    to: billing.customer              # the target table's contract
    columns: {CustomerCode: ACCOUNT_CODE, Name: TITLE, IsActive: ACTIVE}  # target: source
    values:                           # target column: {arrives: lands}
      IsActive: {Y: true, N: false}
    winsOnConflict: [CustomerCode, Name]  # target columns where this side wins
    filledByTarget: [CreatedAt]       # the target's own default fills these

A column map breaks the same ways here as in a `derivedFrom` entry, so the
one-way refusals are core/mapping.py's, shared. What only a flow has is a
**pair**: two flows between the same tables in opposite directions, each able
to pass alone while the pair corrupts data.

* **The maps must be inverses.** `Name <- TITLE` needs `TITLE <- Name` the other
  way. Otherwise an edit is never propagated and is overwritten by the next
  change from the other side, or it comes back into a different column.
* **A value map has to survive the round trip too.** It must be one-to-one,
  and the way back must be its inverse. `'Y'` and `'T'` both becoming `1`
  cannot come back as both, and a side that copies instead of translating
  writes `1` into a `char(1)` that meant `'Y'`.
* **Every column of a pair has exactly one winner.** Both sides edit the same
  customer, so ADR 0008's escape hatch, row filters proven disjoint, is
  false by construction. `winsOnConflict` does not restrict who may write. It
  decides whose value stands when both changed the same column since the last
  sync. With no winner that is settled by timing; with two, the flows
  contradict each other.

Nothing executes a flow yet. See issue #53.
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
    wins: frozenset[str] = field(default_factory=frozenset)
    filled_by_target: frozenset[str] = field(default_factory=frozenset)


def parse(doc: dict) -> Flow:
    return Flow(
        id=str(doc.get("id", "?")),
        target=str(doc.get("to", "")),
        mapping=Mapping(str(doc.get("from", "")), dict(doc.get("columns") or {}),
                        dict(doc.get("values") or {})),
        wins=frozenset(doc.get("winsOnConflict") or []),
        filled_by_target=frozenset(doc.get("filledByTarget") or []))


def load(directory: Path = FLOWS) -> list[Flow]:
    return [parse(yaml.safe_load(p.read_text(encoding="utf-8")))
            for p in sorted(directory.glob("*.yaml"))]


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


def _pair_problems(a: Flow, b: Flow) -> list[str]:
    out: list[str] = []
    for mine, theirs in a.mapping.columns.items():
        returned = b.mapping.columns.get(theirs)
        if returned is None:
            out.append(f"{a.id}: {mine!r} is filled from {theirs!r} but "
                       f"{b.id} does not map it back, so an edit made at "
                       f"{a.target} is overwritten by the next change at "
                       f"{a.mapping.reference}")
            continue
        if returned != mine:
            out.append(f"{a.id}: {mine!r} is filled from {theirs!r}, but "
                       f"{b.id} fills {theirs!r} from {returned!r}; the round "
                       f"trip moves the value into another column")
            continue
        # The rest is a fact about the pair, not about one direction, so one
        # side reports it -- or `--check` counts one problem twice.
        if a.id > b.id:
            continue
        out += _value_problems(a, b, mine, theirs)
        winners = (mine in a.wins) + (theirs in b.wins)
        if winners == 0:
            out.append(f"{a.id}: {mine!r} <-> {theirs!r} is edited on both "
                       f"sides and neither flow wins it, so a conflict is "
                       f"settled by timing")
        elif winners == 2:
            out.append(f"{a.id}: {mine!r} <-> {theirs!r} is won by both "
                       f"{a.id} and {b.id}; the two flows contradict each "
                       f"other")
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
        here = _properties(by_id[flow.target])
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
                out += _pair_problems(a, b)
    return out
