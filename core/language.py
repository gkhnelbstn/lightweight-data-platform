"""The language of the text the server writes once, for everyone. Issue #44.

The UI follows ODD's own language picker, per viewer (ADR 0011). Some text is
written by the server instead: the link names on a table's catalogue page, and
the alert message. Each is written once per deployment and read by everyone,
so no viewer's choice can pick its language -- the deployment does, with
`LDP_LANGUAGE` (`en` or `tr`). Before this, the links were Turkish and the
alert English, and nobody had chosen that mix.

English phrases are the keys, as in the panel's catalogue: English needs no
entry, and a phrase with no Turkish entry is written in English.
"""
from __future__ import annotations

import os

LANGUAGE = os.getenv("LDP_LANGUAGE", "en").strip().lower()

TURKISH = {
    # The Attachments links on a table's page in ODD.
    "Checks": "Kontroller",
    "Data quality (contract)": "Veri kalitesi (kontrat)",
    "Replication rule": "Senkron kuralı",
    # The alert message (core/alerts.py).
    "Contract quality {as_of}": "Kontrat kalitesi {as_of}",
    "{n} contracts": "{n} kontrat",
    "SLA missed: {contract} -- {why}": "SLA kaçırıldı: {contract} -- {why}",
    "{n} checks could not run": "{n} kontrol çalışamadı",
    "score {score} below {minimum}": "skor {score}, alt sınır {minimum}",
    "Newly failing in {contract}: {checks}": "{contract} içinde yeni hatalar: {checks}",
    "(+{n} more)": "(+{n} tane daha)",
    "{contract}: {tables} stuck in the initial copy -- nothing is replicating":
        "{contract}: {tables} ilk kopyada takılı -- hiçbir şey kopyalanmıyor",
    "{contract}: the apply worker is not running":
        "{contract}: uygulama işçisi çalışmıyor",
    "{contract}: the view's source is unreachable":
        "{contract}: görünümün kaynağına ulaşılamıyor",
}


def say(phrase: str, **values) -> str:
    """The phrase in the deployment's language, with its values filled in."""
    text = TURKISH.get(phrase, phrase) if LANGUAGE == "tr" else phrase
    return text.format(**values)
