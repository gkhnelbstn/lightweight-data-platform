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

import json
from pathlib import Path

import psycopg
from psycopg import sql

HUB_SQL = Path(__file__).with_name("hub.sql")

# The golden record's bookkeeping. Not part of any system's shape, never
# delivered, and never a column name a contract may use.
META = ("_at", "_by", "_rev", "_changed", "_skip", "_link")


def init(cx: psycopg.Connection) -> None:
    cx.execute(HUB_SQL.read_text(encoding="utf-8"))


def register_entity(cx: psycopg.Connection, name: str, key: list[str],
                    columns: dict[str, str], authority: str,
                    required: list[str] | None = None,
                    keys: dict[str, str] | None = None) -> None:
    """The golden table, its inbox, and the trigger between them.

    `columns` is the canonical shape -- column name to Postgres type -- that
    every system's flow maps into and out of. The golden table keeps the whole
    old row on update (`replica identity full`), because that is what
    SeaTunnel's Postgres CDC needs to carry it back out.

    `keys` is for systems that do not share a key (#80): per system, the
    column holding its own key. The golden key is then the hub's, and each of
    those columns is unique -- one record per local key, one local key per
    record and system.
    """
    clash = sorted(set(columns) & {*META, "id", "system", "row_kind",
                                   "source_ms", "landed_at", "fields", "unmapped",
                                   "outcome"})
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
        "_changed text not null default '*', _skip text, "
        "_link boolean not null default true, "
        "primary key ({}))").format(
            golden, cols, sql.SQL(", ").join(map(sql.Identifier, key))))
    # What each revision changed and where it came from: a delivery writes
    # only those fields, and not back to their source (ADR 0021).
    # `_link` says whether the systems may match this record by the pair's
    # `linkBy` while its code there is unknown (#120): true for every record
    # that was already here, which is the behaviour they had.
    cx.execute(sql.SQL("alter table hub.{} add column if not exists _changed text "
                       "not null default '*', add column if not exists _skip text, "
                       "add column if not exists _link boolean not null default true"
                       ).format(golden))
    cx.execute(sql.SQL("alter table hub.{} replica identity full").format(golden))
    cx.execute(sql.SQL(
        "create table if not exists hub.{} (id bigserial primary key, "
        "system text not null, row_kind text not null, source_ms bigint not null, "
        "fields text, {}, landed_at timestamptz not null default clock_timestamp())"
    ).format(inbox, cols))
    # A system joining a hub that already runs brings its code column (#82):
    # `create table if not exists` would leave both tables without it.
    for column, kind in columns.items():
        for table in (golden, inbox):
            cx.execute(sql.SQL("alter table hub.{} add column if not exists {} {}").format(
                table, sql.Identifier(column), sql.SQL(kind)))
    cx.execute(sql.SQL("alter table hub.{} add column if not exists fields text").format(inbox))
    # The fields whose value the flow's value map did not know (#84).
    cx.execute(sql.SQL("alter table hub.{} add column if not exists unmapped text").format(inbox))
    # What the hub did with each row: applied, echo, lost, held... (the tab's history).
    cx.execute(sql.SQL("alter table hub.{} add column if not exists outcome text").format(inbox))
    cx.execute(sql.SQL("drop trigger if exists merge on hub.{}").format(inbox))
    cx.execute(sql.SQL(
        "create trigger merge after insert on hub.{} for each row "
        "execute function hub.on_inbox({})").format(inbox, sql.Literal(name)))
    for column in (keys or {}).values():
        cx.execute(sql.SQL("create unique index if not exists {} on hub.{} ({})").format(
            sql.Identifier(f"{name}_{column}"), golden, sql.Identifier(column)))
    cx.execute("insert into hub.entity (name, key, authority, required, keys) "
               "values (%s, %s, %s, %s, %s) on conflict (name) do update set "
               "key = excluded.key, authority = excluded.authority, "
               "required = excluded.required, keys = excluded.keys",
               (name, key, authority, list(required or key),
                None if keys is None else json.dumps(keys)))


def register_system(cx: psycopg.Connection, entity: str, system: str,
                    fields: list[str] | None = None,
                    link_by: list[str] | None = None) -> None:
    """A system that receives the golden record, and so will echo it back.

    `fields` is what it receives, when that is only part of the record: a
    table of addresses beside a table of customers. None is all of it.
    `link_by` is how its rows under a key the hub has not seen find their
    record (#80).
    """
    cx.execute("insert into hub.system (entity, system, fields, link_by) "
               "values (%s, %s, %s, %s) on conflict (entity, system) do update "
               "set fields = excluded.fields, link_by = excluded.link_by",
               (entity, system, fields, link_by))


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
