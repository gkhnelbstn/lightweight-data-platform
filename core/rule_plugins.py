"""Rule kinds contributed from outside this repository.

`core/rules.py` was already a registry -- a dict of kind to builder, served to
the UI by `/api/rules/catalogue`, which draws the form from whatever comes
back. The only part that was not plugin-shaped was the registration itself,
which meant a new kind was an edit to this repo. A package declares

    [project.entry-points."ldp.rules"]
    iban_checksum = "my_pkg.rules:iban_checksum"

and the named object is either a `RuleKind` or something that returns one.

Nothing here is discovery for its own sake: the group is read once, at import,
and a failure in it is raised rather than logged. A catalogue that has silently
lost a kind is the worst outcome available -- the form simply would not offer
it, and nobody would know why. See ADR 0016.
"""
from __future__ import annotations

from importlib.metadata import entry_points
from typing import Callable

GROUP = "ldp.rules"


def load_plugins(register: Callable, group: str = GROUP) -> list[str]:
    """Register every kind in the entry point group. Returns their names."""
    loaded = []
    for entry in entry_points(group=group):
        try:
            rule = entry.load()
            if callable(rule):
                rule = rule()
            register(rule)
        except Exception as exc:  # noqa: BLE001 -- re-raised with the culprit
            raise RuntimeError(
                f"rule plugin {entry.name!r} ({entry.value}) did not "
                f"register: {exc}") from exc
        loaded.append(entry.name)
    return loaded
