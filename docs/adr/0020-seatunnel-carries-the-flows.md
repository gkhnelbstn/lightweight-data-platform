# 0020 — Apache SeaTunnel can carry the flows; the pair stays ours

**Proposed.** This record describes a prototype and what it found. Adopting
SeaTunnel is new infrastructure (invariant 6), so the decision is taken
explicitly, not by merging this file.

## Context

ADR 0019 made an integration its own file (`contracts/flows/`), and
`core/flows.py` refuses a flow, or a pair of flows, that would corrupt data.
Nothing moves a row yet. The requirement behind issue #53:

* any source to any target: SQL Server, Postgres (and TimescaleDB), MongoDB,
  and HTTP APIs;
* streaming, not a nightly copy;
* renamed columns, recoded values and changed types;
* both directions between two systems of record.

Writing an executor for every engine pair is the per-pair cost #56 warned
about. The question was whether an open-source project can carry the rows,
so that the code liability is someone else's.

### What was ruled out, with the reason

* **SymmetricDS Community.** It was the one open-source tool with
  bi-directional sync, transforms and conflict resolution. From 3.17 (March
  2026) *"support for Microsoft SQL Server and Oracle database platforms has
  moved to SymmetricDS Pro"* (Jumpmind release notes), so the community
  edition cannot do the SQL Server side at all.
* **Redpanda Connect.** `microsoft_sql_server_cdc` and `postgres_cdc` both
  need an enterprise licence.
* **TapData Community.** Apache 2.0, with bi-directional tasks, but *"does not
  currently support active-active conflict resolution. Avoid modifying the
  same data on both sides simultaneously."* It also runs on a MongoDB
  metadata store with 5 GB of memory, and keeps its pipelines in that store,
  not in files, which is invariant 1's problem.
* **Debezium Server.** ADR 0018 measured it at 893 MiB per pipeline, one
  connector per instance. Its row filter is Groovy, and it has no loop
  prevention.
* **Flink CDC.** A Flink cluster is a larger runtime than the problem.

**Apache SeaTunnel** is Apache 2.0, and 2.3.13 was released on 2026-03-14. In
one image it ships:
- CDC sources for SQL Server, Postgres, MongoDB and MySQL;
- a JDBC sink that turns CDC row kinds into insert, update and delete, and
  upserts on `primary_keys`;
- a MongoDB sink;
- an HTTP source with polling and paging;
- a SQL transform with `CASE WHEN`, `CAST`, `COALESCE` and date functions.

It runs as one standalone engine ("Zeta") without Kafka or Flink.

## What the prototype did

Everything ran against the demo stack, with `apache/seatunnel:2.3.13` on the
compose network.

**One flow, SQL Server to Postgres and MongoDB.**
- Source: SQL Server CDC on `erp.dbo.customers`.
- Transform: one SQL step that renames columns, recodes `segment` through
  `CASE`, and leaves `tax_id` and `email` behind.
- Sinks: a JDBC upsert into Postgres and a MongoDB upsert, from the same job.

| | result |
|---|---|
| initial snapshot | 2 000 rows in Postgres, 2 000 documents in MongoDB |
| update, delete and insert on SQL Server | all three in both targets within **~5 s** (the checkpoint interval) |
| classified columns | never left the source: the projection is the boundary |
| memory, one job in local mode | ~500 MiB |
| memory, a cluster with no jobs | 449 MiB |
| memory, the same cluster running two flows | 657 MiB, so ~104 MiB per flow |
| image | 6.97 GB on disk, once |

So the shape is the opposite of Debezium Server's. The runtime costs a lot
once and a flow costs little after that.

**Most of the 6.97 GB is connectors nobody here uses.** `connectors/` is 2.4 GB
for 84 connectors, and `lib/` is 403 MB, mostly Hadoop, AWS and Hive. A
derived image was built on `eclipse-temurin:8-jre`. It keeps `bin`,
`config` and `starter`, and from `lib/` only the transforms jar, the Hadoop
uber jar (checkpoint storage) and the two JDBC drivers. Its seven connectors
are CDC base, SQL Server CDC, Postgres CDC, MongoDB CDC, JDBC, MongoDB and
HTTP. The result was **1.28 GB**, and it ran both prototypes unchanged:

* the SQL Server CDC job wrote 2 000 rows to Postgres and 2 000 documents to
  MongoDB, at the same ~500 MiB;
* the Postgres CDC flow snapshotted, then carried an update.

Because it never copies `opengauss-jdbc`, the Postgres driver conflict below
does not arise in it.

`deploy/Dockerfile.seatunnel` is that image made reproducible. It keeps only
`starter/seatunnel-starter.jar`, not the Flink and Spark starters beside it,
and adds the carried commit below. It is **893 MB** and ran the commit-time
spike at 439 MiB.

### Three things that would have gone wrong silently

1. **The official image cannot write to Postgres.** `lib/` ships
   `opengauss-jdbc-5.1.0.jar`, which contains its own
   `org/postgresql/core/v3/ConnectionFactoryImpl` and shadows the real
   pgjdbc. Against Postgres 16 with `scram-sha-256` every connection fails
   with `Protocol error. Session setup failed.`. The message names neither
   the driver nor the cause, and the server logs a connection that never
   authenticates. Removing that one jar fixes it. Upstream already knows:
   apache/seatunnel#10242 is open, and #11510 was closed as fixed in August
   2026, after 2.3.13. A derived image removes the jar until a release
   carries the fix.
2. **A value outside the value map becomes NULL.** The demo's segments are
   `KEY/RETAIL/MID/SMB`, and the map written for the test covered `SMB`,
   `MID` and `ENT`. 1 000 rows landed with no tier and nothing complained,
   because `CASE` without `ELSE` is NULL. Whatever compiles a flow's
   `values` into SQL must not produce that. A check on the target that the
   column is filled wherever the source was is the data-quality half of the
   same rule.
3. **Auto-created targets are not the contract's.** With
   `CREATE_SCHEMA_WHEN_NOT_EXIST` the sink made `nvarchar(120)` into
   `varchar(480)` and `char(2)` into `varchar(4)`. The target table belongs
   to the target's contract. It is created from that contract, and SeaTunnel
   only writes into it.

## Echo in a two-way pair

Two flows in opposite directions write into each other. What stops a change
bouncing back for ever is whether writing an unchanged value produces a
change event.

* **SQL Server target: the echo dies by itself.** A `MERGE` or `UPDATE` that
  sets every column to the value it already has adds no row to the CDC change
  table: 1 row before, 1 row after, for both.
* **Postgres target: the generated upsert loops for ever.** By hand, a plain
  `insert ... on conflict do update` with identical values emits one change
  to logical decoding, and the same statement guarded with
  `where (t.a, t.b) is distinct from (excluded.a, excluded.b)` emits none.
  Through SeaTunnel it was a pair of Postgres tables and two flows in
  opposite directions, each recoding a `'Y'/'N'` column as a boolean:

  | sink | what happened |
  |---|---|
  | generated upsert (`generate_sink_sql`) | `n_tup_upd` went 25 → 45 in 30 s with **no** edit anywhere; the pair never settles |
  | guarded upsert in `query`, alone | settled after the snapshot, then looped again after the first edit |
  | guarded upsert **and** `FilterRowKind` dropping `UPDATE_BEFORE` | one edit on each side, exactly 2 updates per table, unchanged 30 s later |

  The middle row is the subtle one. In `query` mode the sink runs the
  statement for every row it receives, including an update's *before*
  image, so it writes the old value and then the new one. Each is a real
  change, and the pair oscillates. With `generate_sink_sql` the sink knows
  row kinds; with a custom statement it does not.

Two more costs of a Postgres *source*:

* **`REPLICA IDENTITY FULL` is required.** SeaTunnel's Postgres CDC refuses to
  start without it. On a table that belongs to another system that is an
  `ALTER` on their table, and every update then logs the whole old row.
* **It leaves things behind.** A job creates a replication slot and a
  publication, `dbz_publication`, and neither goes when the job stops. A slot
  nobody reads holds WAL for ever: the silent-resource shape issue #35 was
  about.

Deletes were excluded from the guarded runs. A custom `query` has one
statement, so a delete needs its own branch: `FilterRowKind` keeping `DELETE`
into a second sink whose statement deletes by key.

Together with `core/flows.py`'s refusals, this is what makes a pair
self-terminating. The inverse maps and one-to-one value maps guarantee the
round trip reproduces the *identical* value. An identical value is then a
no-op on SQL Server natively. On Postgres it is a no-op only when the write
is guarded and before images are dropped, which is what a flow compiler has
to emit for a Postgres target in a pair.

**Built (#83).** `core/flow_sql.py` compiles the Postgres target as a `MERGE`
(Postgres 15+) of the same shape as SQL Server's. Its `WHEN MATCHED` carries
`(t.cols) IS DISTINCT FROM (new values)`, its parameters carry their types,
and deletes are the separate branch described above. The demo's third
system, a shop on Postgres, runs the whole of `verify.py` with no loop.
Two things about a Postgres *source* were found on the way, and both are
checked before a job is submitted (`core/flow_schema.py`, `core/flow_resume.py`):

* SeaTunnel 2.3.13's Postgres CDC will not start on a table with a
  `timestamptz` column, mapped or not ("Unsupported type: TIMESTAMP_TZ"
  behind a bare HTTP 500).
* A resumed job whose slot has gone makes a new one at the current position
  and skips what changed since its checkpoint. That is refused like a purged
  SQL Server change (ADR 0023).

## What this does not decide

* **Conflicts.** Nothing here tested what happens when both sides change the
  same column between two syncs, and nothing in SeaTunnel expresses
  `winsOnConflict`. Its SQL transform is single-table with no joins, so it
  cannot compare against the target first. A pair whose sides edit the same
  column still needs per-row state that no adoptable tool provides for SQL
  Server.
* **Whether the flow compiler exists.** The proposal is that
  `contracts/flows/*.yaml` compiles into SeaTunnel job configs and is refused
  by `core/flows.py` first, the way `syncTo` compiles into a publication. That
  compiler is not written.

## What a conflict rule needs, and how it is got

Both sides of a pair edit the same fields, and that cannot be prevented, so
conflicts need a rule. The rule chosen in conversation is **the latest edit
wins**. That needs each change's commit time, and it helps to have the value
the edit replaced. Spikes on the demo measured what SeaTunnel actually hands
over for one `UPDATE` on SQL Server:

| | SQL Server (`cdc` change table, `lsn_time_mapping`) | SeaTunnel |
|---|---|---|
| read time | | `EventTime`: 1.4 s and 2.7 s after the commit in two runs |
| commit time | `tran_end_time` `1789819613703` | `SourceTimestamp` `1789819613703`, **with #10667 carried** |
| before image | `__$operation = 3` | `UPDATE_BEFORE`, then `UPDATE_AFTER` |

**The `Http` source sleeps holding the checkpoint lock** (#126). `pollNext`
takes `output.getCheckpointLock()` around the whole poll, and the wait between
listings is inside it -- so a streaming job's checkpoint barrier, which needs
that same lock (`SourceFlowLifeCycle#triggerBarrier`), never got it and the
job was failed at the timeout while the reader was still polling. The engine's
own comment says the window between two polls is tight enough to need a
`Thread.sleep(0L)`; a poll interval turns it into one narrow window every ten
seconds, and `synchronized` is not fair. `Object.wait(long)` releases the
monitor while it waits, so the patch is one word, carried in
`deploy/Dockerfile.seatunnel` behind an anchor. `dev` has the same code, so
there is nothing upstream to cherry-pick.

**A change to a key column is a delete and an insert, on both engines** (#129).
Measured after the question was raised by `match`, which pins one of a table's
several rows per record (#79) and relies on this: a change of the pinned value
has to reach both flows of the pair whole, and it does only because each of
them sees a whole event -- the flow that had the row a `DELETE`, the flow that
gains it an `INSERT`. SQL Server does it because CDC records a key update that
way; Postgres does it too, which was not obvious, since a table read by a flow
carries `REPLICA IDENTITY FULL` and the whole old row is therefore in the WAL:

    update shop.customer set customer_no = 7777 where customer_no = 5024

    hub.customer_inbox  DELETE  shop_code 5024  'Anahtar Denemesi'  deleted
    hub.customer_inbox  INSERT  shop_code 7777  'Anahtar Denemesi'  created

So the rule `core/flows.py` states -- what `match` pins must be part of the
table's key -- is not engine-specific, and invariant 3 has nothing to say
here. It also means a system that renumbers a record loses the crosswalk for
it: the hub sees the record deleted and another one arriving.

* **`EventTime` is when SeaTunnel read the change.** Its source,
  `SeaTunnelRowDebeziumDeserializeSchema`, sets it from `fetchTimestamp`, and
  SQL Server's capture job polls every 5 s. Ordering edits by it is wrong
  whenever they are closer than that. After an outage it is wrong by the
  length of the outage, because the whole backlog is read "now".
* **`SourceTimestamp` is the commit time, and 2.3.13 does not have it.** It
  was added in apache/seatunnel#10667 (April 2026, after 2.3.13); asking
  2.3.13 for it fails with *"metadata fields 'SourceTimestamp' ... not
  found"*. That one commit is cherry-picked onto 2.3.13 in
  `deploy/Dockerfile.seatunnel`, which rebuilds the two jars it touches
  (`seatunnel-starter`, `connector-cdc-base`). With it, the value equals
  `tran_end_time` to the millisecond (at most 1 ms apart, which is SQL
Server's `datetime` rounding). The same branch is kept in the fork,
  `gkhnelbstn/seatunnel`, as `ldp/2.3.13-source-timestamp`. It is a carried
  patch (ADR 0011) of an already-merged upstream commit, so it goes at the
  next release rather than on a maintainer's decision.
* **The before image is there. The first spike lost it downstream.** 2.3.13's
  deserializer emits `UPDATE_BEFORE` for every engine. A console sink showed
  both rows for SQL Server. The first spike wrote through the JDBC sink's
  generated SQL with no primary key, and only the after row survived that. With
  `RowKindExtractor` and a plain `insert` statement, every row lands in commit
  order, each with its commit time:

  | row_kind | source_ms | segment |
  |---|---|---|
  | UPDATE_BEFORE | 1789819717070 | RETAIL |
  | UPDATE_AFTER | 1789819717070 | SMB |
  | UPDATE_BEFORE | 1789819717080 | SMB |
  | UPDATE_AFTER | 1789819717080 | RETAIL |

  That append-only shape is how changes should reach whatever merges them.

So "latest edit wins" means *latest committed wins*, on the released
connectors plus one carried commit. No reader of ours is needed for it.

## On upgrade

* **SeaTunnel drops `opengauss-jdbc` from `lib/`, or relocates its
  packages:** delete the line in the derived image that removes it.
* **A SeaTunnel release carries `SourceTimestamp` (apache/seatunnel#10667):**
  delete the `src` and `build` stages of `deploy/Dockerfile.seatunnel`, and
  the two `COPY --from=build` lines, and re-run the commit-time spike against
  the release.
* **SeaTunnel's JDBC sink gains a guarded upsert** (`is distinct from`, or
  "skip unchanged rows"): the compiler stops generating a custom `query` for
  a Postgres target in a pair.
