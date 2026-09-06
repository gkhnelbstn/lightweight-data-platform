"""Put the run on ODD's own entity page, rather than beside it on another port.

The tests and their runs already land in ODD as `DataQualityTest` /
`DataQualityTestRun`. What did not was everything the contract UI computes --
the score, how many checks failed, and the way into the pages ODD has no model
for. Those lived on :8077 and had to be found on purpose, which is the wrong
shape: this is a plugin for a catalog, not a second catalog.

ODD has two native places for it. One works.

**Metrics do not.** `POST /ingestion/metrics` attaches Prometheus-shaped
families to an ODDRN and the entity Overview renders them, which is exactly
the right home for a score -- but a family can only be written *once*. Pushing
the same family a second time, byte-identical metadata and a new value,
answers 500:

    java.lang.NullPointerException: Cannot invoke
    "MetricFamilyPojo.getId()" because "metricFamilyPojo" is null

A daily score is a second push by definition, so this is not usable and no
code here attempts it. Measured, not read: a brand new family returns 201, an
existing one 500, and the two together 500. Three further things had to be
measured the same way, worth writing down for whoever picks this up when it is
fixed -- the platform's published OpenAPI disagrees with its own models on two
of them:

* the field is `metric_points` (a list), not `metric_point`;
* `timestamp` is epoch seconds, not the ISO string the schema advertises;
* the Overview card truncates the value to an integer, so a 0..1 score renders
  as `0` and would have to be published as a percentage.

**Links work.** `POST /api/dataentities/{id}/links` shows up as an *Attachments*
card on the same page. It appends rather than replaces -- posting the same
link every night would pile up duplicates -- and `GET /api/links` returns an
empty list rather than the entity's links, so there is nothing to reconcile
against. The ids we created are therefore ours to remember: `odd_links` holds
them and the second run does a `PUT` instead of a second `POST`.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from core import store

# Where a person's browser reaches the contract UI. Not the compose service
# name: these URLs are followed from outside the network, not from inside it.
UI_URL = os.getenv("DQ_UI_URL", "http://localhost:8077").rstrip("/")


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def _send(url: str, body: dict, method: str = "POST"):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


def entity_id(url: str, oddrn: str) -> int | None:
    """ODD's numeric id for an ODDRN.

    There is no lookup-by-ODDRN in the API, so this goes through search: query
    for the table name, then match the ODDRN exactly. Matching on the name
    alone would be wrong -- `customers` is also the name of several checks.
    """
    base = url.rstrip("/")
    name = oddrn.rsplit("/", 1)[-1]
    search_id = _send(f"{base}/api/search", {"query": name, "filters": {}})["search_id"]
    page = 1
    while True:
        results = _get(f"{base}/api/search/{search_id}/results?page={page}&size=50")
        for item in results.get("items", []):
            if item.get("oddrn") == oddrn:
                return item["id"]
        if not (results.get("page_info") or {}).get("has_next"):
            return None
        page += 1


# --- links ------------------------------------------------------------------

def desired_links(contract: dict) -> list[dict]:
    """The pages ODD has no model for, reached from the entity that has one.

    Deliberately few. An Attachments card with eight links in it is a menu,
    and the point is to get someone to the one page that answers "which rows".
    """
    links = [{"name": "Veri kalitesi (kontrat)",
              "url": f"{UI_URL}/#contract={contract['id']}"}]
    for prop in contract.get("customProperties") or []:
        if prop.get("property") == "syncTo":
            links.append({"name": "Senkron kurali", "url": f"{UI_URL}/#sync"})
    return links


def sync_links(url: str, contract: dict, oddrn: str) -> int:
    """Create the links once, then keep them up to date.

    `POST` appends, so calling it every night would leave a growing pile of
    identical attachments. The ids come back from the first `POST` and are
    kept in `odd_links`; after that the same link is a `PUT`.
    """
    eid = entity_id(url, oddrn)
    if eid is None:
        return 0
    base = f"{url.rstrip('/')}/api/dataentities/{eid}/links"
    known = {}
    with store.connect() as dq:
        store.init(dq)
        known = {name: lid for name, lid in dq.execute(
            "select name, link_id from odd_links where contract_id = %s",
            (contract["id"],)).fetchall()}

    made = 0
    for link in desired_links(contract):
        if link["name"] in known:
            try:
                _send(f"{base}/{known[link['name']]}", link, method="PUT")
                made += 1
                continue
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
                # deleted in the UI; fall through and make it again
        created = _send(base, {"items": [link]})
        new_id = (created or [{}])[0].get("id")
        if new_id is None:
            continue
        with store.connect() as dq:
            dq.execute(
                """insert into odd_links (contract_id, name, link_id)
                   values (%s, %s, %s)
                   on conflict (contract_id, name) do update
                     set link_id = excluded.link_id""",
                (contract["id"], link["name"], new_id))
        made += 1
    return made
