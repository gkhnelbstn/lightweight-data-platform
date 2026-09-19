"""Two contracts that map into each other: what makes a two-way pair safe.

`core/mapping.py` checks one direction -- the target says where its columns
come from. Two-way is two such mappings pointing at each other, and each one
can pass on its own while the pair still corrupts data. So what is checked
here is the *pair*:

    # siber.cari                          # zirve.hesap
    derivedFrom:                          derivedFrom:
      - contract: zirve.hesap               - contract: siber.cari
        columns: {UNVAN: Ad}                  columns: {Ad: UNVAN}
    masteredHere: [UNVAN]                 masteredHere: [Bakiye]

**The two maps must be inverses.** `UNVAN <- Ad` on one side needs
`Ad <- UNVAN` on the other. Otherwise an edit takes one of two wrong paths.
If nothing maps it back, the edit is never propagated and is overwritten by
the next change from the other side. If something maps it back into a
different column, the round trip moves the value to the wrong place. Both are
silent.

**Every two-way column has exactly one master.** Both sides may edit any
column -- that is the case this exists for. So ADR 0008's escape hatch, row
filters proven disjoint, is false by construction: the same customer card is
edited in both places. `masteredHere` does not restrict who may write. It
decides who **wins** when both sides changed the same column since the last
sync, which is the one moment a pair has to choose. With no master that
choice is made silently, by timing. With two masters the contracts
contradict each other.

**A value map has to survive the round trip too.** `AKTIF` coded `'E'/'H'`
on one side and `Durum` a `bit` on the other is the same column in two
spellings, and `values` says how to translate (core/mapping.py). Two ways
that goes wrong silently. The map is not one-to-one: `'E'` and `'Y'` both
become `1`, and `1` cannot come back as both. Or the way back is not the
inverse of the way there: one side maps and the other copies, and `1` comes
back as `'1'` into a `char(1)` that meant `'E'`.

Nothing executes a pair yet. See issue #53 and ADR 0008.
"""
from __future__ import annotations

from core.mapping import declared


def mastered_here(contract: dict) -> set[str]:
    """The columns whose value wins a conflict on this side of a pair."""
    out: set[str] = set()
    for prop in contract.get("customProperties") or []:
        if prop.get("property") == "masteredHere":
            value = prop.get("value") or []
            out |= set(value if isinstance(value, list) else [value])
    return out


def _mapping_from(contract: dict, reference: str):
    for mapping in declared(contract):
        if mapping.reference == reference and mapping.detailed:
            return mapping
    return None


def _value_problems(me: str, there: str, mine: str, theirs: str,
                    forward: dict | None, back: dict | None) -> list[str]:
    """`forward` turns their value into ours; `back` turns ours into theirs."""
    if forward is None and back is None:
        return []
    if forward is None or back is None:
        side = me if forward is None else there
        return [f"{me}: {mine!r} <-> {there}.{theirs} translates values in one "
                f"direction only ({side} copies them), so a round trip writes "
                f"a translated value back untranslated"]
    out = []
    for name, m in ((me, forward), (there, back)):
        if len(set(m.values())) != len(m):
            out.append(f"{name}: the value map for {mine!r} <-> {theirs!r} "
                       f"sends two values to one, which cannot come back")
    if not out and back != {v: k for k, v in forward.items()}:
        out.append(f"{me}: the value map for {mine!r} is not the inverse of "
                   f"{there}'s for {theirs!r}, so a round trip changes the value")
    return out


def problems(contract: dict, by_id: dict[str, dict]) -> list[str]:
    """Every reason a two-way pair this contract belongs to would not hold.

    A one-way mapping gets nothing from here. An unresolvable reference is
    `core/mapping.py`'s to report, not ours to report twice.
    """
    me = contract.get("id", "?")
    out: list[str] = []
    for mapping in declared(contract):
        other = by_id.get(mapping.reference)
        reverse = _mapping_from(other, me) if other and mapping.detailed else None
        if reverse is None:
            continue
        back = reverse.columns
        there = mapping.reference

        for mine, theirs in mapping.columns.items():
            returned = back.get(theirs)
            if returned is None:
                out.append(f"{me}: {mine!r} is filled from {there}.{theirs} "
                           f"but nothing maps it back, so an edit made here "
                           f"is overwritten by the next change there")
            elif returned != mine:
                out.append(f"{me}: {mine!r} is filled from {there}.{theirs}, "
                           f"which maps back into {returned!r}; the round "
                           f"trip moves the value into another column")
            elif me < there:
                # A fact about the pair, like the master rule below.
                out += _value_problems(me, there, mine, theirs,
                                       mapping.values.get(mine),
                                       reverse.values.get(theirs))

        # The master rule is a fact about the pair, so only one side reports
        # it -- or a single problem is counted twice by `--check`.
        if me > there:
            continue
        ours, others = mastered_here(contract), mastered_here(other)
        for mine, theirs in mapping.columns.items():
            owners = (mine in ours) + (theirs in others)
            if owners == 0:
                out.append(f"{me}: {mine!r} <-> {there}.{theirs} is edited "
                           f"on both sides and masteredHere names neither, so "
                           f"a conflict is settled by timing")
            elif owners == 2:
                out.append(f"{me}: {mine!r} <-> {there}.{theirs} is mastered "
                           f"on both sides; the two contracts contradict "
                           f"each other")
    return out
