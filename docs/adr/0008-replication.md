# 0008 — Replication is the database's own, and the contract states the rule

## Context

Keeping a second database in step with a CDC-enabled source, under rules
someone manages. The obvious answer is Debezium or a connector runtime. The
sixth invariant of this repository says no new infrastructure without a row
count to justify it.

## Decision

**No replication engine is written here, because both sources already have
one.**

* **Postgres → Postgres:** logical decoding. A publication carries a row filter
  and a column list since 15, which is precisely "the rules that decide what is
  synced". Nothing of ours sits in the stream.
* **SQL Server → Postgres:** no native path, so `core/sync_mssql.py` reads
  `cdc.fn_cdc_get_all_changes_*` — an ordinary function taking two LSNs. One
  loop, one table scan from a stored watermark. No Kafka, no connector.

The rule is an ODCS `customProperties: syncTo` entry naming a `servers` target,
a filter, an identity and a column list. The **column list is a privacy
boundary**, not an optimisation: `tax_id` is classified, is left out, and has no
column in the replica at all.

Ours is the part neither engine does: deriving those objects from the contract
and **refusing to create them when they would not work**. Logical replication
fails silently — the initial copy succeeds, the rows land, and every later
change dies in a background worker that writes only to the server log.

Four preconditions, each found by hitting it on a running pair:

1. The source needs a replica identity — a unique index over NOT NULL columns.
   The contract already names it (`primaryKey`), **so a table whose uniqueness
   check is failing cannot be replicated safely**, and `unsound_identity()`
   enforces that from the stored results.
2. Row-filter columns must be inside that identity; an update is matched
   against the old row and the old row is only those columns.
3. The column list must cover the identity.
4. The **target** needs the same identity. Nothing fails until the first update.

The target table itself is built from the contract, because nothing else
creates it.

## Consequences

* `--status` exists because a dead apply worker and a quiet one look identical.
* Rules 2 and 3 are *logical replication's*, not replication's in general. The
  CDC reader has whole rows and is bound by neither — applying them to a SQL
  Server contract reported a problem that was not one, and carrying the widened
  `identity` over to it actively broke deletes.
* `fn_cdc_get_all_changes(…, 'all')` returns operations 1, 2 and 4 only. The
  before image needs `'all update old'`, and without it an update that changes
  an identity column duplicates the row.
* SQL Server CDC is a **SQL Server Agent** feature. `sp_cdc_enable_table`
  succeeds with the Agent stopped and then nothing is ever captured.

## On upgrade

* **PostgreSQL major version:** re-read the publication rules; row filters and
  column lists arrived in 15 and their interaction with replica identity is
  exactly what bit us. `tests/test_sync.py` pins all four preconditions.
* **A new engine:** add replication only if it already has its own. Do not
  write one.
* Deleting a row from `sync_watermarks` re-snapshots that source, which is
  idempotent but not free.
