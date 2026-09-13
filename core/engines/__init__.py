"""What an engine owes this platform, in one place per engine.

`datacontract test` owns the `servers` type -- it compiles the checks and runs
them, for twenty engines we will never write code for. Four things are ours,
and they used to be four `if server["type"] in ("sqlserver", "mssql")` branches
in three different modules written at three different times:

    connect        a connection to the source
    build_window   the day's view of it (a schema on Postgres, a database on
                   SQL Server -- see core/runner.py for why)
    count_rows     the denominator the volume half of the score needs
    dialect        which SQL the compiled check has to be read back as

CLAUDE.md's invariant 3 exists because of exactly that shape: *"A rule
belonging to one engine must not be applied to the other -- that mistake has
been made twice."* Twice, with two engines. Adding a third meant finding three
`if` statements by grep; now it means reading one file that says what an engine
owes, and writing another like it. See issue #34.

A module registers by being imported here and naming its aliases. That is
deliberately not an entry-point plugin system: a registry is a promise about an
interface, and this one has had two implementations for a week. See issue #32.
"""
from __future__ import annotations

from types import ModuleType

from . import postgres, sqlserver

_ENGINES: dict[str, ModuleType] = {
    alias: module
    for module in (postgres, sqlserver)
    for alias in module.ALIASES
}


def engine(server: dict | str) -> ModuleType:
    """The module for a server block, or for a bare ODCS type name."""
    kind = server if isinstance(server, str) else (server.get("type") or "")
    try:
        return _ENGINES[kind]
    except KeyError:
        raise LookupError(
            f"no engine wired up for a {kind!r} server -- add "
            f"core/engines/{kind}.py; known: {', '.join(sorted(_ENGINES))}"
        ) from None


def known(kind: str) -> bool:
    return kind in _ENGINES


def dialect(kind: str) -> str:
    """The sqlglot dialect for a server type.

    Falls back to the type name itself rather than raising: a check compiled
    for an engine nothing here implements can still be *read*, and sqlglot
    knows more dialects than this package does.
    """
    return _ENGINES[kind].DIALECT if kind in _ENGINES else kind
