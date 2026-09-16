"""What a contract says about rows that arrive from another schema.

`syncTo` is a replica: the same table, narrowed. `target_table_statement`
builds the target from the *source* model, so the target has no shape of its
own -- which is correct for a replica and a lie for anything else.

The other shape is an integration: two schemas that each exist on their own
terms, connected by a stream. A row becoming a document, a column becoming
two, a target whose key is its own. No engine's built-in replication does
that -- built-in replication produces a replica by definition -- so ADR 0008's
"add replication only if the engine already has its own" cannot decide it.
See ADR 0018 and issue #53.

The transports exist either way, and are somebody else's. What nothing else
does is refuse a mapping that would not work, which is the same thing
`core/sync.py`'s `problems()` is for and the same argument ADR 0008 makes:
*"Ours is the part neither engine does."*

So the mapping is declared in the **target's** contract, next to its own key
and its own checks, as column detail on the `derivedFrom` this repository
already reads for lineage (ADR 0014). An entry stays a bare reference when
there is nothing to say:

    customProperties:
      - property: derivedFrom
        value:
          - dwh.stg_orders                    # whole table, as before
          - contract: erp.customers           # ...or with a column map
            columns:
              customer_key: customer_id       # target column: source column
              country_code: country

A target usually has columns of its own that no upstream fills -- a surrogate
key, SCD Type 2's `valid_from` and `is_current`, an arrival timestamp. Those
are not gaps, and the contract says so once:

      - property: computedHere
        value: [customer_key, valid_from, valid_to, is_current]

which is also the list a reader wants: exactly the columns this table invents
rather than carries.

Nothing here moves a row. `problems()` below is what refuses one.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Mapping:
    """One upstream contract and, where stated, how its columns land here."""
    reference: str
    columns: dict[str, str] = field(default_factory=dict)

    @property
    def detailed(self) -> bool:
        return bool(self.columns)


def declared(contract: dict) -> list[Mapping]:
    """The `derivedFrom` entries, whichever form each one takes.

    A bare string is what every contract wrote before this existed and still
    means the same thing -- this table comes from that one, with nothing said
    about columns. Only an entry that *has* a column map is checked against
    one, so adding this vocabulary refuses nothing that used to pass.
    """
    out: list[Mapping] = []
    for prop in contract.get("customProperties") or []:
        if prop.get("property") != "derivedFrom":
            continue
        value = prop["value"]
        for entry in (value if isinstance(value, list) else [value]):
            if isinstance(entry, dict):
                out.append(Mapping(str(entry.get("contract", "")),
                                   dict(entry.get("columns") or {})))
            else:
                out.append(Mapping(str(entry)))
    return out


def _properties(contract: dict) -> dict[str, dict]:
    model = (contract.get("schema") or [{}])[0]
    return {p["name"]: p for p in model.get("properties") or []}


def filled_here(contract: dict) -> set[str]:
    """Columns this table fills itself, so their having no upstream is not a gap.

    Two vocabularies say this, because two different things do the filling and
    both already existed. `syncTo.generated` is the target *database* filling a
    column -- a sequence or a default, issue #45. `computedHere` is this
    table's own process filling one: `dim.customer`'s surrogate key and its
    Type 2 `valid_from` / `is_current` are computed by demo/medallion.py and
    exist in no source row at all.

    Keeping them separate rather than merging into one list is deliberate: a
    reader of the contract can tell which columns the database invents from
    which ones our code does, and only the first survives a change of engine.
    """
    out: set[str] = set()
    for prop in contract.get("customProperties") or []:
        if prop.get("property") == "syncTo":
            out |= set((prop.get("value") or {}).get("generated") or {})
        elif prop.get("property") == "computedHere":
            value = prop.get("value") or []
            out |= set(value if isinstance(value, list) else [value])
    return out


def problems(contract: dict, by_id: dict[str, dict]) -> list[str]:
    """Every reason a declared mapping would not hold, before anything moves.

    Each rule below is a thing that fails *silently* otherwise: a column that
    nothing fills, a classified value that crosses, a key that cannot match a
    row. None of them raise on their own -- which is why they are checked here
    and not discovered in the target.

    Deliberately no type checking. A mapping between two schemas changes types
    legitimately and often, so flagging every difference would be noise, and a
    real widening/narrowing rule needs a lattice nobody has asked for yet.
    See issue #50 for the narrower case that *is* a bug.
    """
    here = _properties(contract)
    table = (contract.get("schema") or [{}])[0].get("name", contract.get("id", "?"))
    generated = filled_here(contract)
    out: list[str] = []
    covered: set[str] = set()

    for mapping in declared(contract):
        upstream = by_id.get(mapping.reference)
        if upstream is None:
            # ADR 0014: an unresolvable reference is reported, never dropped --
            # a graph missing an edge still looks complete.
            out.append(f"{table}: derivedFrom names {mapping.reference!r}, "
                       f"which is not a contract this platform loads")
            continue
        if not mapping.detailed:
            continue
        there = _properties(upstream)

        for target, source in mapping.columns.items():
            if target not in here:
                out.append(f"{table}: the mapping fills {target!r}, which this "
                           f"contract does not declare")
            if source not in there:
                out.append(f"{table}: the mapping reads {source!r} from "
                           f"{mapping.reference}, which does not declare it")
                continue
            if target in covered:
                out.append(f"{table}: {target!r} is filled twice; one of the "
                           f"two sources would silently win")
            covered.add(target)

            # The column list in a syncTo rule is a privacy boundary (ADR 0008)
            # because a column left out has no column in the target. A mapping
            # has no such physics -- it names the target column explicitly --
            # so the boundary has to be stated and checked instead.
            classification = there[source].get("classification")
            if classification and here.get(target, {}).get(
                    "classification") != classification:
                out.append(
                    f"{table}: {source!r} is classified {classification!r} at "
                    f"{mapping.reference} and {target!r} here is not; a "
                    f"classified value may not lose its classification by "
                    f"being copied")

    if any(m.detailed for m in declared(contract)):
        required = {n for n, p in here.items()
                    if p.get("required") or p.get("primaryKey")}
        gaps = sorted(required - covered - generated)
        if gaps:
            out.append(f"{table}: {', '.join(gaps)} is required here and no "
                       f"mapping fills it; the contract promises a column "
                       f"nothing puts a value in")
        key = sorted({n for n, p in here.items() if p.get("primaryKey")}
                     - covered - generated)
        if key:
            out.append(f"{table}: the primary key {', '.join(key)} is not "
                       f"filled by any mapping, so a row arriving twice "
                       f"cannot be matched to the one already here")
    return out


def main() -> None:
    """`python core/mapping.py --check` -- every declared mapping, refused or not.

    The same shape as `core/sync.py --check`: validate, create nothing, and
    exit non-zero so CI fails on a mapping nobody would otherwise notice.
    """
    import argparse

    from core.runner import load_contracts

    ap = argparse.ArgumentParser(description="Validate declared column mappings.")
    ap.add_argument("--check", action="store_true", required=True,
                    help="validate the mappings, change nothing")
    ap.add_argument("--contract", help="only this contract id")
    ap.parse_args()

    contracts = load_contracts()
    by_id = {c["id"]: c for c in contracts}
    bad = 0
    for contract in contracts:
        found = problems(contract, by_id)
        detailed = [m for m in declared(contract) if m.detailed]
        if not found and not detailed:
            continue
        print(f"\n{contract['id']}")
        for mapping in detailed:
            print(f"  from {mapping.reference}: "
                  f"{', '.join(f'{t} <- {s}' for t, s in mapping.columns.items())}")
        for line in found:
            print(f"  REFUSED: {line}")
        bad += len(found)
    print(f"\n{bad} problem(s)")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
