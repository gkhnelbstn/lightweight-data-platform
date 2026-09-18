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

## Bi-directional replication is out of scope

`origin = none` on the subscription (PostgreSQL 16) makes a two-way pair
possible without the change echoing back and forth forever — that much was
verified on the running stack. Two things stood in the way after that, and
they are different enough to track separately:

1. **No conflict policy.** Two writers touching the same row is
   last-writer-wins at best, and a genuine conflict — a duplicate key on the
   subscriber — stops the apply worker and waits for a person.
2. **Silence.** That stop happens in a background process that only writes to
   the server log, so nothing reports it.

**PostgreSQL 18 closes the second and not the first.** It detects and names
conflicts — `insert_exists`, `update_origin_differs`, `update_exists`,
`update_missing`, `delete_origin_differs`, `delete_missing`,
`multiple_unique_conflicts` — and counts them in `pg_stat_subscription_stats`,
so a stopped worker becomes a named error and a queryable counter.
`update_origin_differs` and `delete_origin_differs` only exist in a
multi-origin topology and need `track_commit_timestamp` on the subscriber. It
does **not** resolve anything: there is no `conflict_resolver` on
`CREATE SUBSCRIPTION`, and a constraint violation still stops replication
until someone fixes the data or runs `ALTER SUBSCRIPTION ... SKIP`. Issue #52.

**This stack runs PostgreSQL 16** (`compose.yaml`), so here both still stand.

A real policy would be a `syncTo` pair rejected unless the two sides'
row filters can be shown to touch disjoint rows — `country = 'TR'` on one
side and `country = 'DE'` on the other, proven from the filter expressions
rather than assumed. That proof is a small theorem prover over arbitrary SQL
predicates, and a wrong "yes" is worse than the feature not existing: it is
exactly the silent-corruption shape this ADR exists to avoid, and there is no
contract in this repository that needs it to find out whether it works.

So: **bi-directional stays out of scope until a real pair needs it.**
Nothing here builds toward it, and nothing should — `core/sync.py` treats
every `syncTo` rule as one direction, and a person wiring up two rules that
point at each other gets exactly what is described above, unguarded.
Revisit this the day a contract actually wants two-way sync; the disjointness
proof gets designed against that real pair, not a hypothetical one.

### Amended: a two-way integration pair, designed against a stand-in

The case came, and one condition above had to give. The pair is Siber (ERP)
and Zirve (accounting) on SQL Server, issue #53. It is two different
products, so it is integration, not replication (see *On upgrade*). Their
real schemas are not available, so it was decided to **design against a
hypothetical pair** built to have the same problems. The fixture in
`tests/test_two_way.py` has different column names, a classified identifier
on both sides, and both sides editing the same customer card.

That last property rules out the disjointness proof entirely. It is not merely
expensive: the rows are *the same* rows by construction. What replaces it is
disjointness by **column** rather than by row. Each column of the pair names
exactly one side in `masteredHere`, and that side's value wins when both
changed the column since the last sync. Both sides may still write every
column; mastership only answers the conflict. Checking it is set membership,
not a theorem prover. `core/two_way.py` refuses a pair when:

* one side's map is not the inverse of the other side's; or
* a column is mastered by neither side, or by both.

Scope of this amendment:

* It applies to integration pairs declared with `derivedFrom` column maps
  only.
* Two `syncTo` rules pointing at each other remain exactly as unguarded as
  described above.
* Nothing executes a pair yet. The executor, and value or type
  transformations such as `'E'/'H'` against a `bit`, are still open in #53.
* The stand-in is a stand-in. When a real schema arrives, re-check it
  against the fixture's assumptions before trusting the refusals.

## Consequences

* `--status` exists because a dead apply worker and a quiet one look identical.
  On PostgreSQL 18 `pg_stat_subscription_stats` counts conflicts by type, which
  is a second, cheaper way to tell them apart — and it applies to the
  one-directional rules this repository already runs, not only to a two-way
  pair. Not available on the 16 this stack uses.
* Bi-directional is unguarded, not merely unbuilt — see above. `--status` still
  tells a stopped worker from a quiet one either way, which is the one piece
  of this that has to work regardless of direction.
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

* **The Kafka premise in the Context above expired.** Debezium Server runs
  without Kafka and its JDBC sink makes database-to-database one container, so
  "no row count justifies Kafka" no longer refuses anything. The decision was
  re-taken against a running prototype and did not change, for four reasons
  that are not this one -- see **ADR 0018**. Read that before citing this
  record's Context as a reason for anything.
* **PostgreSQL major version:** re-read the publication rules; row filters and
  column lists arrived in 15 and their interaction with replica identity is
  exactly what bit us. `tests/test_sync.py` pins all four preconditions.
* **PostgreSQL 18:** conflicts become named and counted
  (`pg_stat_subscription_stats`). Worth adding to `core/sync.py --status` the
  day the stack moves to 18: it answers "is the worker stopped on a conflict"
  directly instead of by inference. It does not make bi-directional safe — the
  conflict policy is still missing, see above.
* **A new engine:** add replication only if it already has its own. Do not
  write one.
* **Two different products on the same engine are not this record's case.**
  Siber and Zirve, both on SQL Server, have different schemas, so this is
  integration rather than replication. Every engine's own replication needs
  identical schemas on both ends: SQL Server peer-to-peer says so outright,
  and merge replication publishes the same articles everywhere. "Use the
  engine's own" therefore cannot decide it. That case is issue #53, where the
  target's contract declares the column map (`core/mapping.py`). See ADR 0018,
  *What this does not decide*.
* Deleting a row from `sync_watermarks` re-snapshots that source, which is
  idempotent but not free.
