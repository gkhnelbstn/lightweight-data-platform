"""Fill in the parts of ODD's catalogue that ingestion leaves empty.

A collector brings the *shape* of a source: tables, columns, types, lineage.
Everything a catalogue is actually for -- who owns this, what is it for, what
does this column mean, what may I do with it -- arrives as "Not created" and
stays that way, because no collector knows any of it.

The contract does know. It has a `tenant`, a `domain`, a purpose, a description
per column, custom properties, an SLA and a set of quality rules. So this
pushes those into ODD's own places for them rather than inventing a parallel
one:

    contract                     ODD
    ---------------------------  --------------------------------------------
    domain                       an ODD domain holding its tables, a tag, and
                                 the source's namespace when it has only one
    name                         the entity's business name
    tenant                       owner, and its ownership of the entity
    description.purpose          the entity's description
    property.description         the column's description
    quality dimensions           dictionary terms, linked to the entity
    quality rules                query examples on the dataset
    servers / customProperties   metadata on the entity
    slaProperties                metadata on the entity

Everything here is idempotent, and by lookup rather than by remembering ids:
each of these endpoints has a listing to check against, which the entity links
in entity_page.py did not.

    python integrations/odd/curate.py --url http://odd-platform:8080

Named `curate` rather than `catalogue`: `catalogue` is a PyPI package that
spaCy's registry depends on, and a module of that name in this directory
shadows it the moment anything here is run as a script -- which broke
classify.py with `module 'catalogue' has no attribute 'create'`.
"""
from __future__ import annotations

import argparse
import urllib.error

from core.runner import load_contracts
from core.scoring import DIMENSION_WEIGHT
from integrations.odd.entity_page import _get, _send, entity_id
from integrations.odd.from_datacontract import dataset_oddrn

# Where the quality vocabulary lives in ODD's dictionary. Its own namespace,
# not the contract's domain: `completeness` means the same thing to sales and
# to finance, and duplicating it per domain is how a glossary stops being one.
TERM_NAMESPACE = "data quality"

DIMENSION_MEANING = {
    "completeness": "Rows or values that should be there and are not.",
    "uniqueness": "Keys that repeat. Breaks every join that assumes they do not.",
    "consistency": "Two places that must agree and do not -- referential "
                   "integrity, or a total against its parts.",
    "timeliness": "The data did not arrive, or did not arrive in time.",
    "accuracy": "A value that disagrees with a computation over other values.",
    "conformity": "A value outside the set or the range it was declared to be in.",
    "coverage": "How much of the expected population is present at all.",
    "schema": "The structure itself: a column that is missing, or has changed type.",
    "unknown": "A rule that did not say which of the above it is. Scored "
               "lightest on purpose, so an unclassified check cannot dominate.",
}


def _page(url: str, path: str, size: int = 200) -> list[dict]:
    sep = "&" if "?" in path else "?"
    return _get(f"{url.rstrip('/')}{path}{sep}page=1&size={size}").get("items", [])


# --- the pieces -------------------------------------------------------------

def ensure_namespace(url: str, name: str) -> None:
    if not name:
        return
    if any(n["name"] == name for n in _page(url, "/api/namespaces")):
        return
    _send(f"{url.rstrip('/')}/api/namespaces", {"name": name})


def ensure_owner(url: str, name: str) -> int | None:
    """ODD's owner records are global; the contract's `tenant` is the team."""
    if not name:
        return None
    for owner in _page(url, f"/api/owners?query={name}"):
        if owner["name"] == name:
            return owner["id"]
    return _send(f"{url.rstrip('/')}/api/owners", {"name": name}).get("id")


def set_ownership(url: str, eid: int, entity: dict, owner: str) -> bool:
    """The team that owns the contract owns the table. Skipped when it is
    already recorded, because POSTing it again is a second owner."""
    if not owner:
        return False
    for existing in entity.get("ownership") or []:
        if (existing.get("owner") or {}).get("name") == owner:
            return False
    _send(f"{url.rstrip('/')}/api/dataentities/{eid}/ownership",
          {"owner_name": owner, "title_name": "Data owner"})
    return True


def set_description(url: str, eid: int, entity: dict, contract: dict) -> bool:
    """The contract's purpose, plus what the model says about itself."""
    model = contract["schema"][0]
    parts = [(contract.get("description") or {}).get("purpose"),
             model.get("description")]
    text = " ".join(p.strip() for p in parts if p)
    if not text or entity.get("internal_description") == text:
        return False
    _send(f"{url.rstrip('/')}/api/dataentities/{eid}/description",
          {"internal_description": text}, method="PUT")
    return True


def describe_fields(url: str, eid: int, contract: dict) -> int:
    """A column description is the thing a catalogue is most often opened for,
    and the only place that knows it is the contract."""
    structure = _get(f"{url.rstrip('/')}/api/datasets/{eid}/structure")
    by_name = {f["name"]: f for f in structure.get("field_list", [])}
    written = 0
    for prop in contract["schema"][0].get("properties") or []:
        field, text = by_name.get(prop["name"]), prop.get("description")
        if not field or not text or field.get("internal_description") == text:
            continue
        _send(f"{url.rstrip('/')}/api/datasetfields/{field['id']}/description",
              {"description": text}, method="PUT")
        written += 1
    return written


def set_business_name(url: str, eid: int, entity: dict, contract: dict) -> bool:
    """`sales_orders` is what the database calls it. `Sales Orders` is what a
    person looking for it calls it, and the contract already says so."""
    name = contract.get("name")
    if not name or entity.get("internal_name") == name:
        return False
    _send(f"{url.rstrip('/')}/api/dataentities/{eid}/name",
          {"internal_name": name}, method="PUT")
    return True


def entity_tags(contract: dict) -> list[str]:
    """What someone would filter the catalogue by, from what the contract knows."""
    source = next((s for s in contract.get("servers", [])
                   if s.get("server") == "erp"), {})
    tags = ["under-contract"]
    if contract.get("domain"):
        tags.append(f"domain:{contract['domain']}")
    if source.get("type"):
        tags.append(f"engine:{source['type']}")
    if any(p.get("property") == "syncTo"
           for p in contract.get("customProperties") or []):
        tags.append("replicated")
    if any(p.get("classification")
           for p in contract["schema"][0].get("properties") or []):
        tags.append("has-classified-columns")
    return tags


def set_tags(url: str, eid: int, entity: dict, contract: dict) -> int:
    """`PUT` replaces the list, so ours are unioned with whatever is already
    there -- a tag someone added by hand is not ours to delete.

    Note the field name: this endpoint wants `tag_name_list`, while the one on
    a dataset *field* wants `tags`. Same platform, same release.
    """
    have = {t["name"] for t in entity.get("tags") or []}
    want = have | set(entity_tags(contract))
    if want == have:
        return 0
    _send(f"{url.rstrip('/')}/api/dataentities/{eid}/tags",
          {"tag_name_list": sorted(want)}, method="PUT")
    return len(want - have)


def source_domain(contracts: list[dict], source_oddrn: str) -> str | None:
    """The one domain every contracted table of a source belongs to, or None.

    A namespace on a data source shows on every entity it holds -- all 3 700
    tables of an ERP -- so it may only name a domain the whole source is in. An
    ERP whose contracts span five domains used to take the first contract's,
    and every table in it read `yard_operations`.
    """
    found = {c.get("domain") for c in contracts
             if dataset_oddrn(c, "erp").startswith(source_oddrn + "/")}
    return found.pop() if len(found) == 1 else None


def set_datasource_namespace(url: str, entity: dict, domain: str | None,
                             ours: set[str]) -> bool:
    """The namespace shows on every entity of the source, and the collector
    has no way to know it -- it is a business fact, not a schema one.

    `ours` is every domain a contract names: a namespace that is one of them
    and no longer the source's single domain was written here and is taken
    back. One somebody chose by hand is left alone.
    """
    source = entity.get("data_source") or {}
    current = (source.get("namespace") or {}).get("name")
    if not source.get("id") or current == domain:
        return False
    if current and current not in ours:
        return False
    body = {"name": source["name"]}
    if domain:
        body["namespace_name"] = domain
    _send(f"{url.rstrip('/')}/api/datasources/{source['id']}", body, method="PUT")
    return True


# ODD's own group type for a business domain; its Catalog page lists these.
DOMAIN_TYPE = {"id": 22, "name": "DOMAIN"}


def _ref(url: str, eid: int) -> dict:
    """A group member as ODD validates it: an oddrn alone is a 400 for want of
    `id`, `is_stale` and `status`."""
    e = _get(f"{url.rstrip('/')}/api/dataentities/{eid}")
    return {"id": e["id"], "oddrn": e["oddrn"], "is_stale": e.get("is_stale", False),
            "status": e.get("status"), "entity_classes": e.get("entity_classes")}


def tables_by_domain(contracts: list[dict]) -> dict[str, list[str]]:
    """Each domain's contracted tables, by the ODDRN the collector gave them."""
    out: dict[str, set[str]] = {}
    for c in contracts:
        if c.get("domain"):
            out.setdefault(c["domain"], set()).add(dataset_oddrn(c, "erp"))
    return {d: sorted(oddrns) for d, oddrns in sorted(out.items())}


def sync_domains(url: str, contracts: list[dict]) -> dict[str, int]:
    """One ODD domain per contract domain, holding that domain's tables.

    A namespace labels; a domain *groups* -- it is what ODD's Catalog opens on
    and what a person browses by. Its members are replaced on every run, so a
    table whose contract moved domain moves with it. A table the collector has
    not catalogued yet is left out rather than refused.
    """
    base = url.rstrip("/")
    have = {(d.get("domain") or {}).get("internal_name")
            or (d.get("domain") or {}).get("external_name"): d["domain"]["id"]
            for d in _get(f"{base}/api/dataentitygroups/domains").get("items", [])
            if d.get("domain")}
    out = {}
    for domain, oddrns in tables_by_domain(contracts).items():
        members = [_ref(url, eid) for eid in
                   (entity_id(url, o) for o in oddrns) if eid is not None]
        form = {"name": domain, "namespace_name": domain, "type": DOMAIN_TYPE,
                "entities": members}
        ensure_namespace(url, domain)
        if domain in have:
            _send(f"{base}/api/dataentitygroups/{have[domain]}", form, method="PUT")
        else:
            _send(f"{base}/api/dataentitygroups", form)
        out[domain] = len(members)
    return out


def metadata_values(contract: dict) -> dict[str, str]:
    """What the contract knows that ODD has nowhere else to put."""
    out = {"contract_id": contract["id"], "contract_version": contract.get("version", "")}
    source = next((s for s in contract.get("servers", [])
                   if s.get("server") == "erp"), {})
    out["source_engine"] = source.get("type", "")
    for prop in contract.get("slaProperties") or []:
        out[f"sla_{prop['property']}"] = f"{prop['value']}{prop.get('unit', '')}"
    for prop in contract.get("customProperties") or []:
        value = prop["value"]
        if isinstance(value, dict):
            value = ", ".join(f"{k}={v}" for k, v in value.items())
        out[prop["property"]] = str(value)
    return {k: v for k, v in out.items() if v}


def write_metadata(url: str, eid: int, entity: dict, contract: dict) -> int:
    """ODD stores metadata as strings, and drops floats and lists silently --
    so everything is rendered to a string here rather than discovered missing
    on the page later."""
    existing = {(m.get("field") or {}).get("name")
                for m in entity.get("metadata_field_values") or []}
    new = [{"name": k, "type": "STRING", "value": v}
           for k, v in metadata_values(contract).items() if k not in existing]
    if new:
        _send(f"{url.rstrip('/')}/api/dataentities/{eid}/metadata", new)
    return len(new)


def ensure_terms(url: str) -> dict[str, int]:
    """The quality vocabulary as a dictionary, with the weight each dimension
    carries. A person reading `uniqueness` on a failing check can then find out
    what it means and why it costs what it costs."""
    ensure_namespace(url, TERM_NAMESPACE)
    have = {t["name"]: t["id"] for t in _page(url, "/api/terms")}
    for name, meaning in DIMENSION_MEANING.items():
        if name in have:
            continue
        definition = f"{meaning} Weighted {DIMENSION_WEIGHT[name]} in the score."
        created = _send(f"{url.rstrip('/')}/api/terms",
                        {"name": name, "namespace_name": TERM_NAMESPACE,
                         "definition": definition})
        have[name] = created["id"]
    return have


def link_terms(url: str, eid: int, contract: dict, terms: dict[str, int]) -> int:
    """Only the dimensions this contract actually measures."""
    used = {rule.get("dimension") for rule in
            (contract["schema"][0].get("quality") or []) if rule.get("dimension")}
    linked = 0
    for name in sorted(used):
        term_id = terms.get(name)
        if term_id is None:
            continue
        try:
            _send(f"{url.rstrip('/')}/api/dataentities/{eid}/terms",
                  {"term_id": term_id})
            linked += 1
        except urllib.error.HTTPError as e:
            if e.code not in (400, 409):   # already linked
                raise
    return linked


def add_query_examples(url: str, eid: int, contract: dict) -> int:
    """The contract's rules are queries against this dataset, which is what a
    query example is. Someone investigating a failure gets the statement
    without going to the yaml."""
    have = {q.get("definition") for q in
            _get(f"{url.rstrip('/')}/api/queryexample/dataset/{eid}").get("items", [])}
    added = 0
    for rule in contract["schema"][0].get("quality") or []:
        definition, query = rule.get("description"), rule.get("query")
        if not definition or not query or definition in have:
            continue
        created = _send(f"{url.rstrip('/')}/api/queryexample",
                        {"definition": definition, "query": query})
        _send(f"{url.rstrip('/')}/api/dataentities/{eid}/queryexample",
              {"query_example_id": created["id"]})
        added += 1
    return added


# --- one contract, everything -----------------------------------------------

def fill(url: str, contract: dict, terms: dict[str, int],
         contracts: list[dict] | None = None) -> dict:
    oddrn = dataset_oddrn(contract, "erp")
    eid = entity_id(url, oddrn)
    if eid is None:
        return {"contract": contract["id"], "note": "not in ODD yet -- has the "
                                                    "collector run?"}
    contracts = contracts if contracts is not None else load_contracts()
    ensure_owner(url, contract.get("tenant"))
    entity = _get(f"{url.rstrip('/')}/api/dataentities/{eid}")
    source = (entity.get("data_source") or {}).get("oddrn") or ""
    domain = source_domain(contracts, source) if source else None
    ensure_namespace(url, domain)
    return {
        "contract": contract["id"],
        "namespace": set_datasource_namespace(
            url, entity, domain, {c.get("domain") for c in contracts} - {None}),
        "business_name": set_business_name(url, eid, entity, contract),
        "tags": set_tags(url, eid, entity, contract),
        "owner": set_ownership(url, eid, entity, contract.get("tenant")),
        "description": set_description(url, eid, entity, contract),
        "columns": describe_fields(url, eid, contract),
        "metadata": write_metadata(url, eid, entity, contract),
        "terms": link_terms(url, eid, contract, terms),
        "query_examples": add_query_examples(url, eid, contract),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://odd-platform:8080")
    ap.add_argument("--contract", help="only this contract id")
    a = ap.parse_args()

    terms = ensure_terms(a.url)
    print(f"dictionary: {len(terms)} quality terms in {TERM_NAMESPACE!r}")
    contracts = load_contracts()
    for contract in contracts:
        if a.contract in (None, contract.get("id")):
            print(fill(a.url, contract, terms, contracts))
    print(f"domains: {sync_domains(a.url, contracts)}")


if __name__ == "__main__":
    main()
