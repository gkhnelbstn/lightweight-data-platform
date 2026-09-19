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
HTTP. The result is **1.28 GB**, and it runs both prototypes unchanged:

* the SQL Server CDC job wrote 2 000 rows to Postgres and 2 000 documents to
  MongoDB, at the same ~500 MiB;
* the Postgres CDC flow snapshotted, then carried an update.

Because it never copies `opengauss-jdbc`, the Postgres driver conflict below
does not arise in it.

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

## What a conflict rule would need, and 2.3.13 does not give

Both sides of a pair edit the same fields, and that cannot be prevented, so
conflicts need a rule. The rule chosen in conversation is **the latest edit
wins**. That needs each change's commit time, and a spike on the demo
measured what SeaTunnel actually hands over. It was one `UPDATE` on SQL
Server, landed in Postgres through `Metadata` and `RowKindExtractor`:

| | commit (`cdc.lsn_time_mapping`) | what SeaTunnel said |
|---|---|---|
| time | `11:06:06.380` | `EventTime` `11:06:07.823`, `Delay` 1 443 ms |
| before image | present in the change table (`__$operation = 3`) | **not emitted**, `UPDATE_AFTER` only |

* **`EventTime` is when SeaTunnel read the change, not when it committed.**
  Its own source (`SeaTunnelRowDebeziumDeserializeSchema`) sets it from
  `fetchTimestamp`. The commit time is `SourceTimestamp`, which was added in
  apache/seatunnel#10667 (April 2026) and is not in 2.3.13: asking for it
  fails with *"metadata fields 'SourceTimestamp' ... not found"*. The spike's
  gap was 1.4 s, and SQL Server's capture job polls every 5 s, so ordering
  two edits by `EventTime` is wrong whenever they are closer than that.
* **No before image for SQL Server.** The same deserializer emits
  `UPDATE_BEFORE` on the development branch, and the Postgres pair above
  received one. For SQL Server under 2.3.13 only the after image arrived.
  A merge step that decides by comparing against a stored copy per system
  works without it. One that compares against the before image does not.

So with the released SeaTunnel, "latest edit wins" on SQL Server can only
mean *latest captured wins*. `core/sync_mssql.py` already has exact commit
times (`sys.fn_cdc_map_lsn_to_time`) and before images (`'all update old'`),
so capture from SQL Server is the one step where our reader still knows more
than the adopted tool.

## On upgrade

* **SeaTunnel drops `opengauss-jdbc` from `lib/`, or relocates its
  packages:** delete the line in the derived image that removes it.
* **A SeaTunnel release carries `SourceTimestamp` (apache/seatunnel#10667)
  and `UPDATE_BEFORE` for SQL Server:** re-run the spike. If both arrive, the
  SQL Server capture step no longer needs our reader.
* **SeaTunnel's JDBC sink gains a guarded upsert** (`is distinct from`, or
  "skip unchanged rows"): the compiler stops generating a custom `query` for
  a Postgres target in a pair.
