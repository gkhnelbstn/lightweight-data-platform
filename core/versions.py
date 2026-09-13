"""Reading a Type 2 dimension: which versions of a thing the warehouse kept.

`dim.customer` is the most carefully written table in this repo -- the merge,
the half-open `[valid_from, valid_to)` interval, and the `0001-01-01` opening
date that cost 132 orders their country the first time it was got wrong -- and
none of it was observable anywhere. The contract describes the columns, the
rules check the intervals hold, and nobody could look at a customer and see
what changed about them. See issue #27.

Nothing here derives or infers: a table carries versions when its contract
declares the three interval columns and names the business key in a
`versionedBy` custom property. A UI that guessed the key from "the required
integer that is not the primary key" would be right on this table and wrong on
the next one -- invariant 1, the contract is the only source of truth.
"""
from __future__ import annotations

from typing import Any

from psycopg import sql

INTERVAL = ("valid_from", "valid_to", "is_current")


def spec(doc: dict) -> dict | None:
    """What this contract says about its versions, or None if it has none."""
    model = (doc.get("schema") or [{}])[0]
    properties = model.get("properties") or []
    names = {p.get("name") for p in properties}
    key = next((cp.get("value") for cp in doc.get("customProperties") or []
                if cp.get("property") == "versionedBy"), None)
    if not key or not set(INTERVAL) <= names or key not in names:
        return None
    return {
        "contract_id": doc.get("id"),
        "title": doc.get("name") or doc.get("id"),
        "table": model.get("physicalName") or model.get("name"),
        "key": key,
        # What actually varies between two versions. The surrogate key and the
        # interval columns differ by definition, so showing them as "what
        # changed" would bury the one column that really did.
        "attributes": [p["name"] for p in properties
                       if p.get("name") not in (*INTERVAL, key)
                       and not p.get("primaryKey")
                       and p.get("name") != "loaded_at"],
        "server": next((s for s in doc.get("servers") or []
                        if s.get("server") == "erp"), None),
    }


def _table(spec: dict) -> sql.Identifier:
    return sql.Identifier(spec["table"])


def summary(cx, spec: dict) -> dict:
    """How much history there is. Zero closed versions means the merge has
    never had a change to record -- see `medallion.py --with-history`."""
    row = cx.execute(sql.SQL(
        "select count(*), count(distinct {key}), "
        "count(*) filter (where not is_current), min(valid_from), "
        "max(valid_to) from {table}").format(
            key=sql.Identifier(spec["key"]), table=_table(spec))).fetchone()
    return {"versions": row[0], "keys": row[1], "closed": row[2],
            "earliest": row[3], "latest_change": row[4]}


def changed_keys(cx, spec: dict, limit: int = 100) -> list[dict]:
    """The keys worth opening: the ones with more than one version.

    A dimension where every row is still its first version has nothing to show
    and says so, rather than listing two thousand customers that never changed.
    """
    rows = cx.execute(sql.SQL(
        "select {key}, count(*), max(valid_to) from {table} "
        "group by 1 having count(*) > 1 order by 3 desc nulls last, 1 "
        "limit %s").format(
            key=sql.Identifier(spec["key"]), table=_table(spec)),
        (limit,)).fetchall()
    return [{"key": r[0], "versions": r[1], "last_changed": r[2]} for r in rows]


def annotate(rows: list[dict], attributes: list[str]) -> list[dict]:
    """Say what changed between each version and the one before it.

    A diff against the *previous row*, so it is done here rather than in SQL:
    a window function that returns a set of changed column names is more SQL
    than this deserves. The first version changed nothing -- there was nothing
    to change from, and calling every column "changed" would read as though
    the customer had just been re-graded on every attribute at once.
    """
    previous: dict | None = None
    for version in rows:
        version["changed"] = [] if previous is None else [
            c for c in attributes if version[c] != previous[c]]
        previous = version
    return rows


def versions(cx, spec: dict, key: Any) -> list[dict]:
    """Every version of one thing, oldest first, each with what changed."""
    columns = [spec["key"], *spec["attributes"], *INTERVAL]
    rows = cx.execute(sql.SQL(
        "select {cols} from {table} where {key} = %s order by valid_from"
    ).format(cols=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
             table=_table(spec), key=sql.Identifier(spec["key"])),
        (key,)).fetchall()
    return annotate([dict(zip(columns, r)) for r in rows], spec["attributes"])
