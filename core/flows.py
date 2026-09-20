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

**Different codes, one record (#80).** When the systems do not share a key,
the hub contract names the column holding each system's own
(`hub: {keys: {crm: crm_code, billing: billing_code}}`), the golden record's
key is the hub's, and the flow that owns the record says how a row under a
code the hub has not seen finds it:

    from: billing.customer                 from: hub.customer
    to: hub.customer                       to: billing.customer
    columns: {billing_code: CustomerCode,  columns: {CustomerCode: billing_code,
              tax_id: TaxId, ...}                    TaxId: tax_id, ...}
    linkBy: [tax_id]                       filledByTarget: [CustomerCode]

The hub cannot make up a system's code, so a system that receives new records
assigns its own (`filledByTarget`), and its insert coming back is what links
it.

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
    # Hub columns that identify a record under a key the hub has not seen.
    link_by: tuple[str, ...] = ()
    # Target column: `sum(Amount)` and the like -- many rows into one, one way
    # (core/flow_aggregate.py, #81). `columns` is then the group.
    aggregates: dict = field(default_factory=dict)
    # What SeaTunnel is told about running this flow, and nothing else: how
    # often it checkpoints, and how fast it may read. Not parallelism -- a
    # second reader reorders one key's changes, and the hub's rule is the
    # order they were committed in (ADR 0021).
    job: dict = field(default_factory=dict)


def parse(doc: dict) -> Flow:
    return Flow(
        id=str(doc.get("id", "?")),
        target=str(doc.get("to", "")),
        mapping=Mapping(str(doc.get("from", "")), dict(doc.get("columns") or {}),
                        dict(doc.get("values") or {})),
        filled_by_target=frozenset(doc.get("filledByTarget") or []),
        match=dict(doc.get("match") or {}),
        link_by=tuple(doc.get("linkBy") or ()),
        aggregates=dict(doc.get("aggregates") or {}),
        job=dict(doc.get("job") or {}))


def load(directory: Path = FLOWS) -> list[Flow]:
    return [parse(yaml.safe_load(p.read_text(encoding="utf-8")))
            for p in sorted(directory.glob("*.yaml"))]


def api_of(contract: dict | None) -> dict | None:
    """The `api` custom property, when this contract is an HTTP source.

    An API answers with the record as it is now: no change log, so no before
    image and no commit time of its own (ADR 0027). `contentField` is the
    JSON path to the records in the answer, and `changedAt` the field
    carrying when that record last changed, in epoch milliseconds.
    """
    for prop in (contract or {}).get("customProperties") or []:
        if prop.get("property") == "api":
            return prop.get("value") or {}
    return None


def hub_of(contract: dict | None) -> dict | None:
    """The `hub` custom property, when this contract is a hub's golden record."""
    for prop in (contract or {}).get("customProperties") or []:
        if prop.get("property") == "hub":
            return prop.get("value") or {}
    return None


def keys_of(contract: dict | None) -> dict[str, str]:
    """System to the hub column holding its own key, when they differ (#80)."""
    return dict((hub_of(contract) or {}).get("keys") or {})


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
    hub_key = set(_keys(hub)) | set(keys_of(hub).values())
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
        keys = keys_of(contract)
        # With keys of their own, the golden key is the hub's: nothing fills it.
        required = _required(contract) - (set(_keys(contract)) if keys else set())
        key = set(_keys(contract)) | set(keys.values())
        out += [f"{cid}: keys names {c!r} for {s}, which is not a column of this "
                f"hub, or is its own key" for s, c in sorted(keys.items())
                if c not in _properties(contract) or c in _keys(contract)]
        by_system: dict[str, list[Flow]] = {}
        for f in into:
            by_system.setdefault(system_of(f.mapping.reference), []).append(f)
        out += _crosswalk_problems(cid, keys, required, key, into,
                                   [f for f in flows if f.mapping.reference == cid])
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


def _crosswalk_problems(cid: str, keys: dict, required: set[str], key: set[str],
                        into: list[Flow], back: list[Flow]) -> list[str]:
    """Systems with codes of their own (#80): each fills its code, the record's
    owner says how an unseen code finds its record, and a system that gets new
    records assigns their codes itself."""
    if not keys:
        return [f"{f.id}: linkBy only matters when the systems keep keys of "
                f"their own (hub keys), and {cid} names none" for f in into if f.link_by]
    out: list[str] = []
    for f in into:
        alias = keys.get(system_of(f.mapping.reference))
        if alias is None:
            out.append(f"{f.id}: {cid} has no column for "
                       f"{system_of(f.mapping.reference)}'s own key (hub keys)")
        elif alias not in f.mapping.columns:
            out.append(f"{f.id}: {system_of(f.mapping.reference)}'s key is not "
                       f"mapped into {alias!r}, so a row cannot find its record")
        if set(f.mapping.columns) & set(key) - set(keys.values()):
            out.append(f"{f.id}: the key of {cid} is the hub's own when systems "
                       f"keep keys of theirs; no flow fills it")
        if not required <= set(f.mapping.columns):
            if f.link_by:
                out.append(f"{f.id}: linkBy is never used here -- a flow carrying "
                           f"part of a record waits for the one that owns it")
            continue
        if not f.link_by:
            out.append(f"{f.id}: a row under a code the hub has not seen needs "
                       f"linkBy to find its record, or every unseen code is a "
                       f"new record -- a duplicate of one it has under another")
        loose = sorted(set(f.link_by) - (set(f.mapping.columns) - key))
        if loose:
            out.append(f"{f.id}: linkBy names {', '.join(loose)}, which this "
                       f"flow does not carry as a field")
    for f in back:
        if not required <= set(f.mapping.columns.values()):
            continue
        made = sorted(t for t, src in f.mapping.columns.items()
                      if src in keys.values() and t not in f.filled_by_target)
        if made:
            out.append(f"{f.id}: a record new to {f.target} has no {', '.join(made)} "
                       f"yet, and the hub cannot make up {system_of(f.target)}'s "
                       f"code; {f.target} must assign it (filledByTarget)")
    return out


# The SeaTunnel settings a flow may state, and what each one must be.
JOB_SETTINGS = {"checkpointInterval": (1_000, 600_000), "rowsPerSecond": (1, 1_000_000),
                "pollSeconds": (1, 86_400)}


def _job_problems(flow: Flow) -> list[str]:
    out = []
    for name, value in flow.job.items():
        if name not in JOB_SETTINGS:
            out.append(f"{flow.id}: {name!r} is not a job setting; a flow sets "
                       f"{' or '.join(sorted(JOB_SETTINGS))}")
            continue
        low, high = JOB_SETTINGS[name]
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            out.append(f"{flow.id}: {name} is {value!r}; it is a whole number "
                       f"between {low} and {high}")
    return out


# What an API record may carry: SeaTunnel is told the shape of the answer,
# and a type it cannot read is a job that fails at the first poll.
API_TYPES = {"text": "string", "varchar": "string", "string": "string",
             "int": "int", "integer": "int", "bigint": "bigint",
             "boolean": "boolean"}


def _api_problems(flow: Flow, source: dict, target: dict) -> list[str]:
    """An HTTP source is a poll, one way, and it must be able to say when a
    record changed (ADR 0027)."""
    spec = api_of(source)
    out: list[str] = []
    if api_of(target) is not None:
        return [f"{flow.id}: {flow.target} is an API, and nothing here writes "
                f"to one; an API source goes one way, into a hub (ADR 0027)"]
    if spec is None:
        return out
    if hub_of(target) is None:
        out.append(f"{flow.id}: an API source goes into a hub and nowhere "
                   f"else, because that is what decides what a poll means")
    props = _properties(source)
    if not spec.get("contentField"):
        out.append(f"{flow.id}: {source['id']} does not say contentField, the "
                   f"JSON path to the records in the answer")
    at = spec.get("changedAt")
    if not at:
        out.append(f"{flow.id}: {source['id']} does not say changedAt. The hub "
                   f"decides a conflict by commit time, and poll time is when "
                   f"we noticed -- an API given it would win every dispute it "
                   f"takes part in, including the ones where its value is the "
                   f"stale one (ADR 0027)")
    elif at not in props:
        out.append(f"{flow.id}: changedAt names {at!r}, which {source['id']} "
                   f"does not declare")
    elif props[at].get("physicalType") not in ("bigint", "int", "integer"):
        out.append(f"{flow.id}: changedAt {at!r} is "
                   f"{props[at].get('physicalType')!r}; it is epoch "
                   f"milliseconds, a whole number, because converting a "
                   f"timestamp is the API's half of the job, not ours")
    poll = flow.job.get("pollSeconds")
    every = flow.job.get("checkpointInterval")
    if poll and every and every <= poll * 1000:
        out.append(f"{flow.id}: checkpointInterval is {every} ms and the poll "
                   f"is {poll * 1000} ms. A polling source can take a "
                   f"checkpoint only between listings, so closer together "
                   f"they queue behind the sleep and one expires -- which "
                   f"SeaTunnel answers by failing the job")
    out += [f"{flow.id}: {source['id']} declares {n!r} as "
            f"{p.get('physicalType')!r}, which an API answer cannot carry "
            f"({', '.join(sorted(set(API_TYPES)))})"
            for n, p in props.items() if n in flow.mapping.columns.values()
            and p.get("physicalType") not in API_TYPES]
    return out


def problems(flows: list[Flow], by_id: dict[str, dict]) -> list[str]:
    """Every reason these flows would not hold, before anything moves."""
    out: list[str] = []
    for flow in flows:
        out += _job_problems(flow)
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
        out += _api_problems(flow, source, target)
        here = _properties(target)
        if hub_of(target) is not None:
            # Several systems, and several tables of one system, fill a hub;
            # each needs the key, and the record's owner is checked per system.
            filled: set[str] = set()
            out += column_problems(flow.id, here, flow.mapping, source, filled)
            # With codes of their own, the flow fills its code instead
            # (_crosswalk_problems).
            gaps = [] if keys_of(target) else sorted(set(_keys(target)) - filled)
            if gaps:
                out.append(f"{flow.id}: the hub key {', '.join(gaps)} is not "
                           f"filled, so a row cannot find its record")
            out += _match_problems(flow, source, target, into_hub=True)
        else:
            rows = (flow.target, tuple(sorted(flow.match.items())))
            out += column_problems(flow.id, here, flow.mapping, source,
                                   covered.setdefault(rows, set()))
            covered[rows] |= set(flow.match)
            if flow.aggregates:
                from core import flow_aggregate
                out += flow_aggregate.problems(flow, flows, by_id)
                covered[rows] |= set(flow.aggregates)
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
                    and a.match == b.match and not (a.aggregates or b.aggregates)):
                out += _pair_problems(a, b, by_id)
    return out + _hub_problems(flows, by_id)
