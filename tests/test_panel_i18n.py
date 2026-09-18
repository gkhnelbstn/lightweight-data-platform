"""The Turkish catalogues, checked where the build cannot check them.

A key missing from a catalogue is not an error anywhere: i18next falls back
to the English key and the screen quietly goes half-and-half again, which is
the thing issue #44 set out to end. `tsc` cannot see it either -- the keys are
strings. So this reads the panel's literal `t('...')` calls the way upstream's
own key-parity test reads theirs, and checks that every one has a Turkish
entry, with the same `{{placeholders}}` and the same `<c>` tags.

A dynamic key such as `t(c.dimension)` is out of reach of this test, as it is
of upstream's; those entries are in tr.json by hand.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
PANEL = DEPLOY / "odd-platform-ui"
CALL = re.compile(r"""(?<![\w$.])(?:t|tr)\(\s*(['"])(.+?)(?<!\\)\1\s*[,)]""", re.S)
CODE = re.compile(r"""\bk=(['"])(.+?)\1""")
PLACEHOLDER = re.compile(r"{{\w+}}")


def _load(path: Path) -> dict[str, str]:
    return json.loads(path.read_text(encoding="utf-8"))


def _used() -> set[str]:
    keys: set[str] = set()
    for path in PANEL.glob("*.tsx"):
        text = path.read_text(encoding="utf-8")
        keys |= {m.group(2).replace('\\"', '"') for m in CALL.finditer(text)}
        keys |= {m.group(2) for m in CODE.finditer(text)}
    return keys


def test_every_panel_key_has_a_turkish_entry():
    missing = sorted(_used() - set(_load(PANEL / "tr.json")))
    assert missing == []


def test_the_scan_finds_the_keys_it_should():
    """Guards the regex: if it stops matching, the test above passes vacuously."""
    used = _used()
    assert "Needs attention" in used
    assert "Contract service unreachable: {{error}}" in used
    assert len(used) > 100


def test_translations_keep_their_placeholders_and_tags():
    for path in (PANEL / "tr.json", DEPLOY / "odd-platform-locale-tr.json"):
        for key, value in _load(path).items():
            assert set(PLACEHOLDER.findall(key)) == set(PLACEHOLDER.findall(value)), key
            assert key.count("<c>") == value.count("<c>"), key
