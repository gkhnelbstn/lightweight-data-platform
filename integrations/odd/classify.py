"""Classify columns that hold regulated data, and tag them in ODD.

ODD has no classification of its own -- issue opendatadiscovery/odd-platform#130
was closed in 2021 without one -- so this is the gap that has to be filled
somewhere. It is filled with Microsoft's Presidio (MIT, ~10k stars) rather than
hand-written patterns: it already recognises emails, IBANs, credit cards, phone
numbers and IP addresses, with the validation each of those deserves.

Two decisions worth stating.

**The small spaCy model, and a fixed entity list.** Presidio pulls a language
model on first use and defaults to `en_core_web_lg`, which is 425 MB. Column
values are homogeneous -- a column of IBANs is not free text -- so the NLP half
earns very little here. With `en_core_web_sm` (15 MB) and the entity list
restricted, the same columns are found and a false `MEDICAL_LICENSE` on an IBAN
goes away.

**Turkish identifiers are ours.** Presidio has no TCKN or VKN recognizer, and
both are checksum-validated, so they are `PatternRecognizer`s with a validator
rather than a regex that would match any eleven digits. Worth offering upstream
once they have run against real data for a while.

    python integrations/odd/classify.py --url http://odd-platform:8080
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

import psycopg
import yaml

from core import store
from core.runner import CONTRACTS, load_contracts
from integrations.odd.from_datacontract import dataset_oddrn

# What we look for. Everything else Presidio can find is either free-text
# oriented or too loose to be useful on a column of values.
ENTITIES = ["EMAIL_ADDRESS", "IBAN_CODE", "CREDIT_CARD", "PHONE_NUMBER",
            "IP_ADDRESS", "TR_TCKN", "TR_VKN"]

SAMPLE = int(os.getenv("DQ_CLASSIFY_SAMPLE", "200"))
# The gate is how much of the sample is regulated data *at all*. Below it the
# hit is a coincidence -- an order note that happens to contain an email is not
# an email column.
THRESHOLD = float(os.getenv("DQ_CLASSIFY_THRESHOLD", "0.8"))
# Once a column is through the gate, every type holding at least this share is
# reported. A `tax_id` column in a Turkish ERP genuinely holds VKN for
# companies and TCKN for sole traders, and calling it only the more common one
# would be wrong about the data.
MIN_SHARE = float(os.getenv("DQ_CLASSIFY_MIN_SHARE", "0.1"))


def tckn_is_valid(value: str) -> bool:
    """Turkish national identity number. Eleven digits with two check digits."""
    if len(value) != 11 or not value.isdigit() or value[0] == "0":
        return False
    d = [int(c) for c in value]
    odd, even = d[0] + d[2] + d[4] + d[6] + d[8], d[1] + d[3] + d[5] + d[7]
    if (odd * 7 - even) % 10 != d[9]:
        return False
    return sum(d[:10]) % 10 == d[10]


def vkn_is_valid(value: str) -> bool:
    """Turkish tax number. Ten digits, one check digit, its own algorithm."""
    if len(value) != 10 or not value.isdigit():
        return False
    d = [int(c) for c in value]
    total = 0
    for i in range(9):
        tmp = (d[i] + 10 - (i + 1)) % 10
        total += tmp if tmp == 9 else (tmp * pow(2, 9 - i)) % 9
    return (10 - total % 10) % 10 == d[9]


def build_analyzer():
    """Presidio with the small model, plus the two Turkish identifiers."""
    from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    engine = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
    }).create_engine()
    analyzer = AnalyzerEngine(nlp_engine=engine)

    class Tckn(PatternRecognizer):
        def __init__(self):
            super().__init__(supported_entity="TR_TCKN", patterns=[
                Pattern("tckn", r"\b[1-9][0-9]{10}\b", 0.3)])

        def validate_result(self, pattern_text: str):
            return tckn_is_valid(pattern_text)

    class Vkn(PatternRecognizer):
        def __init__(self):
            super().__init__(supported_entity="TR_VKN", patterns=[
                Pattern("vkn", r"\b[0-9]{10}\b", 0.3)])

        def validate_result(self, pattern_text: str):
            return vkn_is_valid(pattern_text)

    analyzer.registry.add_recognizer(Tckn())
    analyzer.registry.add_recognizer(Vkn())
    return analyzer


def sample_column(cx, schema: str, table: str, column: str) -> list[str]:
    """Non-null values, as text, capped. `tablesample` would be cheaper on a
    large table but is not deterministic enough for a report someone reruns."""
    from psycopg import sql
    rows = cx.execute(sql.SQL(
        "select {col}::text from {sch}.{tbl} where {col} is not null limit {n}"
    ).format(col=sql.Identifier(column), sch=sql.Identifier(schema),
             tbl=sql.Identifier(table), n=sql.Literal(SAMPLE))).fetchall()
    return [r[0] for r in rows if r[0]]


def classify_column(analyzer, values: list[str]) -> tuple[list[str], float]:
    """Which regulated types a column holds, and what share of it they cover.

    Returns every type above `MIN_SHARE`, but only when the values that matched
    *something* clear `THRESHOLD` -- so a column is judged on how much of it is
    regulated, not on how confident any single type is.
    """
    if not values:
        return [], 0.0
    hits = Counter()
    matched = 0
    for value in values:
        results = analyzer.analyze(text=value, language="en", entities=ENTITIES)
        if not results:
            continue
        # One value is one thing. A ten-digit VKN also looks like a phone
        # number to a generic recognizer, so keep only the strongest reading --
        # the checksum-validated ones score 1.0 and win.
        best = max(r.score for r in results)
        found = {r.entity_type for r in results if r.score >= best}
        matched += 1
        hits.update(found)
    covered = matched / len(values)
    if covered < THRESHOLD:
        return [], covered
    return ([e for e, n in hits.most_common() if n / len(values) >= MIN_SHARE],
            covered)


def entity_id(url: str, oddrn: str) -> int | None:
    """ODD's numeric id for an ODDRN.

    There is no lookup-by-ODDRN in the API, so this goes through search: query
    for the table name, then match the ODDRN exactly. Matching on the name
    alone would be wrong -- `customers` is also the name of several checks.
    """
    base = url.rstrip("/")
    name = oddrn.rsplit("/", 1)[-1]
    search_id = _post(f"{base}/api/search", {"query": name, "filters": {}})["search_id"]
    page = 1
    while True:
        results = _get(f"{base}/api/search/{search_id}/results?page={page}&size=50")
        items = results.get("items", [])
        for item in items:
            if item.get("oddrn") == oddrn:
                return item["id"]
        if not (results.get("page_info") or {}).get("has_next"):
            return None
        page += 1


def field_ids(url: str, dataset_oddrn_: str) -> dict[str, int]:
    """`{column oddrn: dataset_field id}` for one dataset."""
    eid = entity_id(url, dataset_oddrn_)
    if eid is None:
        return {}
    structure = _get(f"{url.rstrip('/')}/api/datasets/{eid}/structure")
    return {f["oddrn"]: f["id"] for f in structure.get("field_list", [])}


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def tag_field(url: str, field_id: int, tags: list[str]) -> int:
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/datasetfields/{field_id}/tags",
        data=json.dumps({"tags": tags}).encode(), method="PUT",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


def main() -> None:
    import urllib.parse  # noqa: F401  (used by field_ids)

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", help="ODD Platform base url; omit to only report")
    ap.add_argument("--contract", help="classify only this contract id")
    a = ap.parse_args()

    analyzer = build_analyzer()
    findings: list[tuple[str, str, str, float]] = []

    with psycopg.connect(store.ERP_DSN) as cx:
        for contract in load_contracts():
            if a.contract not in (None, contract.get("id")):
                continue
            server = next((s for s in contract.get("servers", [])
                           if s.get("server") == "erp"), None)
            if not server or server.get("type") not in ("postgres", "postgresql"):
                continue  # only the postgres sources are reachable from here
            model = contract["schema"][0]
            table = model.get("physicalName") or model["name"]
            schema = server.get("schema", "public")

            for prop in model.get("properties") or []:
                values = sample_column(cx, schema, table, prop["name"])
                entities, ratio = classify_column(analyzer, values)
                if entities:
                    findings.append((contract["id"], table, prop["name"],
                                     entities, ratio))

    for cid, table, column, entities, ratio in findings:
        print(f"  {table}.{column:<14} {', '.join(entities):<24} "
              f"{ratio:.0%} of {len(entities) and SAMPLE} sampled")
    if not findings:
        print("no columns matched above the threshold")
        return

    if not a.url:
        return

    import urllib.parse
    tagged = 0
    by_contract: dict[str, list] = {}
    for cid, table, column, entities, ratio in findings:
        by_contract.setdefault(cid, []).append((column, entities))
    contracts = {c["id"]: c for c in load_contracts()}
    for cid, cols in by_contract.items():
        ds = dataset_oddrn(contracts[cid], "erp")
        try:
            ids = field_ids(a.url, ds)
        except urllib.error.HTTPError as e:
            print(f"  ! {cid}: cannot resolve fields ({e.code})")
            continue
        if not ids:
            print(f"  ! {cid}: not in ODD yet -- has the collector run?")
            continue
        for column, entities in cols:
            fid = ids.get(f"{ds}/columns/{column}")
            if fid is None:
                print(f"  ! {column}: not in ODD's structure yet")
                continue
            tag_field(a.url, fid, [f"pii:{e}" for e in entities])
            tagged += 1
    print(f"tagged {tagged} column(s) in ODD")


if __name__ == "__main__":
    main()
