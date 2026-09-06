"""Apply SQL Server's CDC change table to a Postgres target.

Postgres to Postgres needs no process of ours -- see core/sync.py. SQL Server
to Postgres has no native path, so this is the one place a loop is justified,
and it is still only a read: `sp_cdc_enable_table` makes SQL Server write every
change into `cdc.fn_cdc_get_all_changes_<instance>`, which is an ordinary
function you SELECT from between two LSNs. deploy/mssql-cdc.sql turns it on.

The rule is the same `syncTo` custom property core/sync.py reads, so a source
that changes engine does not change how it is described.

Two things this does that a naive poller gets wrong.

**A row that leaves the filter is deleted, not skipped.** If a customer moves
from TR to DE and the rule says `country = 'TR'`, filtering the change stream
would simply not see the update and would leave a stale row in the target for
ever. So nothing is filtered out of the stream: every change is read, and the
filter decides *upsert or delete*, which is what logical replication does.

**The watermark is stored, not recomputed.** Restarting from
`fn_cdc_get_min_lsn` would replay the whole retained window every time; upserts
would survive it but deletes of rows already gone would not be idempotent in
any useful sense, and the run would grow with the retention period.

    python core/sync_mssql.py --once
    python core/sync_mssql.py --interval 30
"""
from __future__ import annotations

import argparse
import os
import time

import psycopg
from psycopg import sql

from core import store
from core.runner import load_contracts
from core.sync import identity_columns, sync_rule

# What `__$operation` means, from the CDC docs. The before image (3) is not
# redundant: it is the only way to find the row when an update changes one of
# the identity columns, and an update that is not applied to the right row is
# a duplicate rather than a change.
DELETE, INSERT, UPDATE_BEFORE, UPDATE_AFTER = 1, 2, 3, 4

BATCH = int(os.getenv("SYNC_BATCH", "5000"))


def mssql_connect(server: dict):
    """Its own rather than shared with api/main.py: that one connects as the
    read-only reporting role, this one is a different credential and a
    different lifetime, and a shared helper would have to know which."""
    import pyodbc
    driver = os.getenv("DATACONTRACT_SQLSERVER_DRIVER",
                       "ODBC Driver 18 for SQL Server")
    return pyodbc.connect(
        f"DRIVER={{{driver}}};SERVER={server['host']},"
        f"{server.get('port', 1433)};DATABASE={server['database']};"
        f"UID={os.getenv('DATACONTRACT_SQLSERVER_USERNAME', 'sa')};"
        f"PWD={os.getenv('DATACONTRACT_SQLSERVER_PASSWORD', '')};"
        "TrustServerCertificate=yes;Encrypt=no", timeout=60)


def capture_instance(schema: str, table: str) -> str:
    """SQL Server's default name for the change table of one source table."""
    return f"{schema}_{table}"


def read_watermark(cx, key: str) -> bytes | None:
    row = cx.execute("select lsn from sync_watermarks where source = %s",
                     (key,)).fetchone()
    return bytes(row[0]) if row else None


def write_watermark(cx, key: str, lsn: bytes) -> None:
    cx.execute(
        """insert into sync_watermarks (source, lsn) values (%s, %s)
           on conflict (source) do update set lsn = excluded.lsn,
                                              updated_at = now()""",
        (key, lsn))


def snapshot(mssql, schema: str, table: str, columns: list[str],
             expression: str | None) -> list[tuple]:
    """The table as it stands, shaped like a stream of inserts.

    CDC only records changes from the moment it was enabled, so without this
    the target would hold whatever happened since and nothing before it. This
    is `copy_data = true` for a source that has no subscription to give us
    one. The max LSN is read *before* the snapshot, so anything that changes
    while it runs is replayed afterwards rather than lost -- upserts and
    deletes are both idempotent, so replaying is free.
    """
    picked = ", ".join(f"[{c}]" for c in columns)
    where = f" where {expression}" if expression else ""
    rows = mssql.cursor().execute(
        f"select {picked} from [{schema}].[{table}]{where}").fetchall()
    return [(INSERT, *r) for r in rows]


def changes(mssql, instance: str, columns: list[str],
            since: bytes | None) -> tuple[list[tuple], bytes | None]:
    """Every change between the watermark and now, oldest first."""
    cur = mssql.cursor()
    to_lsn = cur.execute("select sys.fn_cdc_get_max_lsn()").fetchval()
    if to_lsn is None:
        return [], None          # capture has not run yet
    from_lsn = (cur.execute("select sys.fn_cdc_increment_lsn(?)", since).fetchval()
                if since else
                cur.execute("select sys.fn_cdc_get_min_lsn(?)", instance).fetchval())
    if from_lsn is None or from_lsn > to_lsn:
        return [], to_lsn
    picked = ", ".join(f"[{c}]" for c in columns)
    rows = cur.execute(
        f"select top {BATCH} [__$operation], {picked} "
        # 'all' returns operations 1, 2 and 4 only -- measured, not assumed.
        # The before image has to be asked for by name, and without it an
        # update that changes an identity column cannot be applied.
        f"from cdc.fn_cdc_get_all_changes_{instance}(?, ?, 'all update old') "
        f"order by [__$start_lsn], [__$seqval]", from_lsn, to_lsn).fetchall()
    return [tuple(r) for r in rows], to_lsn


def plan_changes(rows: list[tuple], columns: list[str],
                 identity: list[str]) -> list[tuple[str, list]]:
    """The change stream as `("delete", key)` / `("apply", values)` intents.

    Pure, because the ordering is the part that is easy to get wrong and hard
    to notice: the before image has to be carried to the row that follows it,
    and an update that moves a row to a different key has to delete the old one
    or the target ends up holding both.
    """
    positions = [columns.index(c) for c in identity]
    out: list[tuple[str, list]] = []
    was: list | None = None
    for row in rows:
        op, values = row[0], list(row[1:])
        if op == UPDATE_BEFORE:
            was = [values[i] for i in positions]
            continue
        key = [values[i] for i in positions]
        if op == UPDATE_AFTER and was is not None and was != key:
            out.append(("delete", was))
        was = None
        out.append(("delete", key) if op == DELETE else ("apply", values))
    return out


def apply_changes(pg, schema: str, table: str, columns: list[str],
                  identity: list[str], rows: list[tuple],
                  expression: str | None) -> dict:
    """Run the intents, in order, in one transaction.

    Whether a row still belongs in the target is decided by asking Postgres --
    `select exists (select 1 from (select <values>) where <filter>)` -- rather
    than reimplementing the predicate here. Being subtly wrong about NULL
    semantics or collation is a worse trade than a round trip.
    """
    ident = sql.Identifier
    counts = {"upsert": 0, "delete": 0}
    target = sql.SQL("{}.{}").format(ident(schema), ident(table))
    keys = sql.SQL(", ").join(ident(c) for c in identity)
    upsert = sql.SQL(
        "insert into {} ({}) values ({}) on conflict ({}) do update set {}"
    ).format(
        target, sql.SQL(", ").join(ident(c) for c in columns),
        sql.SQL(", ").join(sql.Placeholder() * len(columns)), keys,
        sql.SQL(", ").join(
            sql.SQL("{0} = excluded.{0}").format(ident(c))
            for c in columns if c not in identity))
    delete = sql.SQL("delete from {} where {}").format(
        target, sql.SQL(" and ").join(
            sql.SQL("{} = %s").format(ident(c)) for c in identity))
    where = sql.SQL(" where ") + sql.SQL(expression) if expression else sql.SQL("")
    keep = sql.SQL("select exists (select 1 from (select {}) as r{})").format(
        sql.SQL(", ").join(sql.SQL("%s as {}").format(ident(c)) for c in columns),
        where)
    positions = [columns.index(c) for c in identity]

    with pg.transaction():
        for what, payload in plan_changes(rows, columns, identity):
            if what == "delete":
                pg.execute(delete, payload)
                counts["delete"] += 1
                continue
            # An update that moves a row out of the filter is a delete here,
            # not a change to ignore -- otherwise the target keeps it for ever.
            if expression and not pg.execute(keep, payload).fetchone()[0]:
                pg.execute(delete, [payload[i] for i in positions])
                counts["delete"] += 1
            else:
                pg.execute(upsert, payload)
                counts["upsert"] += 1
    return counts


def sync_once(contract: dict) -> dict:
    from core.sync import _credentials, _server, unsound_identity
    rule = sync_rule(contract)
    # Duplicates in the source do not fail here, they merge -- two orders
    # become one row and nothing says so. Ask the checks first.
    unsound = unsound_identity(contract, rule)
    if unsound:
        return {"contract": contract["id"], "refused": unsound}
    model = contract["schema"][0]
    source, target = _server(contract, "erp"), _server(contract, rule["server"])
    table = model.get("physicalName") or model["name"]
    columns = rule.get("columns") or [p["name"] for p in model["properties"]]
    identity = identity_columns(model, rule)
    instance = capture_instance(source.get("schema", "dbo"), table)
    key = f"{contract['id']}:{instance}"

    with store.connect() as dq:
        store.init(dq)
        since = read_watermark(dq, key)
    with mssql_connect(source) as ms:
        if since is None:
            to_lsn = ms.cursor().execute("select sys.fn_cdc_get_max_lsn()").fetchval()
            if to_lsn is None:
                return {"contract": contract["id"],
                        "note": "cdc capture has not run yet"}
            rows = snapshot(ms, source.get("schema", "dbo"), table, columns,
                            rule.get("filter"))
            first = True
        else:
            rows, to_lsn = changes(ms, instance, columns, since)
            first = False
    if to_lsn is None:
        return {"contract": contract["id"], "note": "cdc capture has not run yet"}

    user, password = _credentials()
    with psycopg.connect(
            f"host={target['host']} port={target.get('port', 5432)} "
            f"dbname={target['database']} user={user} password={password}") as pg:
        # A snapshot was filtered by the source in its WHERE clause, so
        # re-testing every row here would be one round trip per row for an
        # answer already known.
        counts = apply_changes(pg, target.get("schema", "public"), table,
                               columns, identity, rows,
                               None if first else rule.get("filter"))
    with store.connect() as dq:
        write_watermark(dq, key, to_lsn)
    return {"contract": contract["id"],
            "mode": "snapshot" if first else "changes",
            "rows": len(rows), **counts, "lsn": to_lsn.hex()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, help="seconds between passes")
    ap.add_argument("--contract", help="only this contract id")
    a = ap.parse_args()

    contracts = [c for c in load_contracts()
                 if a.contract in (None, c.get("id")) and sync_rule(c)
                 and next((s for s in c["servers"] if s["server"] == "erp"),
                          {}).get("type") in ("sqlserver", "mssql")]
    if not contracts:
        raise SystemExit("no SQL Server contract carries a syncTo rule")

    while True:
        for contract in contracts:
            print(sync_once(contract), flush=True)
        if a.once or not a.interval:
            return
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
