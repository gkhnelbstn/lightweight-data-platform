"""The two statements an out-flow runs in its target, per engine. #83, ADR 0020.

The shape is the same on both engines (core/flow_jobs.py says why): a MERGE
that writes only what the revision changed and creates a row only when the
revision is new to the target, and a DELETE by key.

One difference decides the rest. SQL Server records no change for an update
that writes what a row already holds, so its MERGE may rewrite unchanged
values and nothing echoes. Postgres records one, and a two-way pair then
loops for ever: ADR 0020 measured `n_tup_upd` climbing with nothing edited.
So the Postgres MERGE updates only when some column actually differs --
`IS DISTINCT FROM`, the guarded upsert the prototype settled with -- and its
parameters carry their types, since Postgres will not guess a `?`'s type
inside a `SELECT`.
"""
from __future__ import annotations

from dataclasses import dataclass


def _literal(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


@dataclass(frozen=True)
class Target:
    engine: str                      # "sqlserver" or "postgres"
    table: str                       # schema.table, unquoted
    types: dict[str, str]            # column: physical type, for Postgres casts


def _q(target: Target, name: str) -> str:
    return f"[{name}]" if target.engine == "sqlserver" else f'"{name}"'


def _table(target: Target) -> str:
    return ".".join(_q(target, part) for part in target.table.split("."))


def _has(target: Target, needle: str, haystack: str) -> str:
    """`needle` (a comma-wrapped column name) occurs in the revision's list."""
    if target.engine == "sqlserver":
        return f"CHARINDEX({_literal(needle)}, {haystack}) > 0"
    return f"strpos({haystack}, {_literal(needle)}) > 0"


def _param(target: Target, column: str) -> str:
    if target.engine == "sqlserver":
        return "?"
    return f"CAST(? AS {target.types.get(column) or 'text'})"


def merge(target: Target, keyed: list[tuple[str, str]], carried: list[tuple[str, str]],
          match: dict, assigned: set[str], linked: list[str], replace_only: bool) -> str:
    """`keyed` and `carried` are (target column, hub column). `assigned` are
    key columns the target fills itself; `linked` the target columns of the
    pair's `linkBy`; `replace_only` a flow of one kind of row (`match`), which
    is created whenever its value changes."""
    q = lambda c: _q(target, c)  # noqa: E731
    written = [t for t, _ in keyed + carried] + list(match)
    using = ", ".join(f"{_param(target, c)} AS {q(c)}" for c in written) + \
        f", {_param(target, '_changed')} AS _changed"
    on = " AND ".join(f"t.{q(c)} = s.{q(c)}" for c in [t for t, _ in keyed] + list(match))
    if assigned and linked:
        on = (f"({on} OR ({' AND '.join(f's.{q(c)} IS NULL' for c in sorted(assigned))} AND "
              + " AND ".join(f"t.{q(c)} = s.{q(c)}" for c in linked) + "))")
    new = " OR ".join(["s._changed = '*'"] + [
        _has(target, f",{s},", "s._changed") for _, s in keyed])
    value = {t: f"CASE WHEN {new} OR {_has(target, f',{s},', 's._changed')} "
                f"THEN s.{q(t)} ELSE t.{q(t)} END" for t, s in carried}
    inserted = [c for c in written if c not in assigned]
    create = ("WHEN NOT MATCHED " if replace_only else f"WHEN NOT MATCHED AND ({new}) ") + \
        f"THEN INSERT ({', '.join(q(c) for c in inserted)}) " \
        f"VALUES ({', '.join(f's.{q(c)}' for c in inserted)})"
    if target.engine == "sqlserver":
        sets = ", ".join(f"t.{q(t)} = {v}" for t, v in value.items())
        return (f"MERGE {_table(target)} WITH (HOLDLOCK) AS t USING (SELECT {using}) AS s "
                f"ON {on} " + (f"WHEN MATCHED THEN UPDATE SET {sets} " if sets else "")
                + create + ";")
    # Postgres: an update that changes nothing is still a change to logical
    # decoding, and it comes back as an edit -- so there is none.
    guard = (f"WHEN MATCHED AND ({', '.join(f't.{q(t)}' for t in value)}) IS DISTINCT FROM "
             f"({', '.join(value.values())}) THEN UPDATE SET "
             + ", ".join(f"{q(t)} = {v}" for t, v in value.items()) + " ") if value else ""
    return f"MERGE INTO {_table(target)} AS t USING (SELECT {using}) AS s ON {on} " + guard + create


def delete(target: Target, keyed: list[tuple[str, str]], match: dict) -> str:
    q = lambda c: _q(target, c)  # noqa: E731
    return (f"DELETE FROM {_table(target)} WHERE "
            + " AND ".join([f"{q(t)} = {_param(target, t)}" for t, _ in keyed]
                           + [f"{q(c)} = {_literal(v)}" for c, v in match.items()]))
