"""The hub of a two-way integration: one golden record per entity. ADR 0021.

Two systems both edit the same records, and that cannot be prevented. So
neither writes to the other. Each writes its changes into the hub, the hub
decides what the record now is, and the hub's record goes back out to every
system. SeaTunnel moves the rows both ways (ADR 0020); what it cannot do is
remember, and the remembering is `core/hub.sql`:

* **the golden record**, `hub.<entity>`, with each field's commit time
  (`_at`) and the system that set it (`_by`);
* **what is still on its way back**, `hub.expect`, so a write of ours that
  comes back through a system's CDC is recognised as an echo, even when it
  arrives after the record has moved on;
* **every conflict**, `hub.conflict`. Two systems changed the same field
  without seeing each other's change; the later commit won, and the losing
  value is kept.

The merge runs in a trigger on `hub.<entity>_inbox`, so there is no process of
ours in the stream: SeaTunnel inserts, Postgres merges in the same
transaction.

    python core/hub.py --init          # create the hub database and its functions
"""
from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg import sql

HUB_SQL = Path(__file__).with_name("hub.sql")

# The golden record's bookkeeping. Not part of any system's shape, never
# delivered, and never a column name a contract may use.
META = ("_at", "_by", "_rev")


def init(cx: psycopg.Connection) -> None:
    cx.execute(HUB_SQL.read_text(encoding="utf-8"))


def register_entity(cx: psycopg.Connection, name: str, key: list[str],
                    columns: dict[str, str]) -> None:
    """The golden table, its inbox, and the trigger between them.

    `columns` is the canonical shape -- column name to Postgres type -- that
    every system's flow maps into and out of. The golden table keeps the whole
    old row on update (`replica identity full`), because that is what
    SeaTunnel's Postgres CDC needs to carry it back out.
    """
    clash = sorted(set(columns) & {*META, "id", "system", "row_kind",
                                   "source_ms", "landed_at"})
    if clash:
        raise ValueError(f"{name}: {', '.join(clash)} is reserved in the hub")
    if not set(key) <= set(columns):
        raise ValueError(f"{name}: the key {key} is not among its columns")
    cols = sql.SQL(", ").join(sql.SQL("{} {}").format(sql.Identifier(c), sql.SQL(t))
                              for c, t in columns.items())
    golden, inbox = sql.Identifier(name), sql.Identifier(f"{name}_inbox")
    cx.execute(sql.SQL(
        "create table if not exists hub.{} ({}, _at jsonb not null default '{{}}', "
        "_by jsonb not null default '{{}}', _rev bigint not null default 0, "
        "primary key ({}))").format(
            golden, cols, sql.SQL(", ").join(map(sql.Identifier, key))))
    cx.execute(sql.SQL("alter table hub.{} replica identity full").format(golden))
    cx.execute(sql.SQL(
        "create table if not exists hub.{} (id bigserial primary key, "
        "system text not null, row_kind text not null, source_ms bigint not null, "
        "{}, landed_at timestamptz not null default clock_timestamp())").format(
            inbox, cols))
    cx.execute(sql.SQL("drop trigger if exists merge on hub.{}").format(inbox))
    cx.execute(sql.SQL(
        "create trigger merge after insert on hub.{} for each row "
        "execute function hub.on_inbox({})").format(inbox, sql.Literal(name)))
    cx.execute("insert into hub.entity values (%s, %s) on conflict (name) "
               "do update set key = excluded.key", (name, key))


def register_system(cx: psycopg.Connection, entity: str, system: str) -> None:
    """A system that receives the golden record, and so will echo it back."""
    cx.execute("insert into hub.system values (%s, %s) on conflict do nothing",
               (entity, system))


def main() -> None:
    import argparse
    import os

    from core.bootstrap_db import admin_dsn, ensure_database

    ap = argparse.ArgumentParser(description="The two-way integration hub.")
    ap.add_argument("--init", action="store_true", required=True)
    ap.parse_args()
    host, port = os.getenv("HUB_HOST", "db"), int(os.getenv("HUB_PORT", "5432"))
    ensure_database(host, port, "hub")
    with psycopg.connect(admin_dsn(host, port, "hub"), autocommit=True) as cx:
        init(cx)
    print(f"hub ready on {host}:{port}/hub")


if __name__ == "__main__":
    main()
