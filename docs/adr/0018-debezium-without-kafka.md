# 0018 — Debezium without Kafka exists, and the answer still does not change

## Context

ADR 0008 refused a replication engine, and issue #45 refused one again. Both
refused the same thing for the same reason, and it was Kafka:

> "Debezium plus a stream processor is the industry answer and is exactly what
> invariant 6 exists to refuse — no row count here comes close to justifying
> Kafka." — #45

**That reason has expired.** Debezium Server is a standalone application: no
Kafka, no Kafka Connect. Its sink list now carries JDBC as a native Debezium
Server sink, so SQL Server → Postgres is one process and one container.

A decision that survives only because its premise was never re-checked is not a
decision, so it was re-taken against the running stack rather than re-argued.
This record is what the prototype found. Issue #49 carries the full run.

### What the prototype did

Debezium Server `:latest`, against the demo's `mssql → erp_replica` pair,
writing into an isolated `dbz_probe` schema beside the replica that
`core/sync_mssql.py` maintains. Both were then compared on the same data.

It works. The snapshot completed, 58 667 rows landed, and the sink created its
own table with the right six columns and `order_id` as the primary key. Nothing
below is an argument that it does not work.

Two things are worth knowing before anyone repeats it: the JDBC sink is newer
than `:3.3.0.Final`, which fails at startup with `No Debezium consumer named
'jdbc' is available`; and the sink credentials are
`debezium.sink.jdbc.connection.username`, not `...jdbc.username`, which fails
validation while naming nothing.

## Decision

**Keep the hand-written CDC reader for the replica case. The Kafka premise is
dead; the conclusion is not — but it now rests on four different reasons, and
they are better ones.**

*For the replica case* is load-bearing. See "What this record does not
decide" below: an integration between two different schemas is a separate question that
none of the four reasons touch.

Each of these is a difference in *what the replica is*, not a benchmark.

### 1. The row filter would stop being SQL

`filter: "status <> 'CANCELLED'"` is SQL today, and `apply_changes`
deliberately refuses to evaluate it itself:

> "Whether a row still belongs in the target is decided by asking Postgres [...]
> rather than reimplementing the predicate here. Being subtly wrong about NULL
> semantics or collation is a worse trade than a round trip."

Debezium's SQL Server connector has `table.include.list` and
`column.include.list`. It has **no row filter**. Row filtering is the `Filter`
SMT, which is distributed separately (`debezium-scripting-<version>.tar.gz`)
together with a JSR-223 engine, and evaluates a **Groovy** expression in the
JVM.

So the predicate that decides which rows leave the source would move from
SQL's three-valued logic to Groovy's truthiness, in a language the contract
does not otherwise speak, in an image we would have to build. A row filter that
is quietly wrong about NULL does not throw. It copies rows that should not have
crossed.

**This is about the row filter and nothing else.** Changing the *shape* of a
row needs no scripting at all: `ReplaceField` is a core Kafka Connect SMT and
is already on the stock image's classpath, so renaming and dropping columns
works out of the box. Measured on the same probe --

    debezium.transforms.rename.type=org.apache.kafka.connect.transforms.ReplaceField$Value
    debezium.transforms.rename.renames=order_id:order_no,net_amount:total
    debezium.transforms.rename.exclude=currency

58 667 rows landed with `net_amount` arriving as `total` and `currency` absent,
with no jar, no script engine and no Dockerfile. Only `Filter` and
content-based routing need `debezium-scripting`.

One thing that measurement also found, because it is the silent kind:
`ReplaceField$Value` leaves the *key* alone, and `primary.key.mode=record_key`
builds the key column from it -- so a renamed key column arrives twice, once
under each name, and every row count still matches. Renaming a key needs
`ReplaceField$Key` beside it.

`status` is `required: true` in the shipped contract, so this rule would not
hit it. The next contract might, and nothing would say so.

### 2. The replica's shape would follow the source, not the contract

The contract declares `physicalType: decimal`. Our path produced `numeric`;
Debezium produced `numeric(14,2)`, because Debezium reads the live SQL Server
schema and we read the contract.

Debezium's column is the more accurate one. Ours is the one invariant 1 asks
for. A contract that under-specifies a column is a gap that should be visible in
the replica, not silently corrected from whatever the source happens to be
today — that is the whole argument of ADR 0013 applied to shape instead of
documentation.

(The mapper also discards a precision the contract *does* state. That is our
bug and it is #50, not an argument here.)

### 3. Deletes change meaning

With `ExtractNewRecordState`, a delete arrives as a `__deleted` column rather
than a `DELETE`. `core/sync_mssql.py` issues a real one. Soft-deleted rows in a
replica that a warehouse reads from is a different replica, and the column list
being a privacy boundary makes it sharper: a row deleted at the source would
still be there, holding the columns it was allowed to carry.

This is configurable. It is listed because it is the default, and because
"configure it back" is a thing someone has to know to do.

### 4. The cost is per pipeline, and the plan is many pipelines

Measured on this stack, same data, same source:

| | `core/sync_mssql.py` | Debezium Server |
|---|---|---|
| resident memory | **33.65 MiB** | **893.1 MiB** |
| image | 1.45 GB, shared with the app | 2.16 GB, its own |
| instances | one process per contract | **one container per connector** |

One Debezium Server instance runs exactly one connector. At N streams that is
N × ~900 MiB against N × ~34 MiB. Invariant 6 asks for a row count to justify
infrastructure; here the multiplier is the pipeline count, and it points the
same way.

### What this does not decide

**Postgres → Postgres was never in question.** Logical decoding does the work
and nothing of ours is in the stream; Debezium there would trade zero moving
parts for one.

**Nothing here is a claim that the reader is better software.** Debezium is a
maintained CDC implementation and ours is 371 lines. The four points above are
about the replica the two produce, not about their quality.

## What this record does *not* decide, and the reason it cannot

Everything above compares two ways of producing **a replica**: the same table,
narrowed. Same column names, same shape, fewer rows or fewer columns.

That is not the only thing a `syncTo`-shaped rule gets asked for. The other
shape is an **integration**: two schemas that each exist on their own terms,
connected by a stream. A row becoming a document, a column becoming two, a
target whose key is its own.

The four reasons above say nothing about that case, and neither does the
prototype. Worth stating here rather than leaving the record to be cited for a
question it never asked.

Every tool measured in this thread is a replica tool, and each says so in its
own way:

* SQL Server peer-to-peer **forbids row and column filters**, because its
  premise is that every node is identical (#54);
* Debezium's JDBC sink `schema.evolution` mirrors the source's shape into the
  target -- that is what it is for;
* pgEdge Spock is a logical replication extension, a replica by construction;
* **`target_table_statement` builds the target from the *source* model.** Ours
  is a replica tool too, and `syncTo` is named correctly for what it does.

Which is why ADR 0008's rule cannot settle an integration case:

> "**A new engine:** add replication only if it already has its own. Do not
> write one."

**No engine has "its own" schema-to-schema integration.** Built-in replication
produces a replica -- that is what built-in replication *is*. The rule is not
incomplete; it answers a different question, and it answers that one correctly.

So the open work is not another transport. Transports exist for every pair. It
is where the mapping between two schemas lives -- invariant 1 leaves only the
contract -- and what refuses a mapping that would silently drop a column,
widen a type, or produce a document missing a field the target's own contract
requires. `problems()` does that for a replica rule and has no counterpart for
a mapping. That refusal is ours under every tool on this list, which is why it
is worth building before choosing one. See #53.

## Consequences

* `core/sync_mssql.py` stays ours, with everything ADR 0008 already said about
  it — including that its failures are the silent kind.
* Row filters stay SQL, evaluated by Postgres, which is why `apply_changes`
  costs a round trip per row it is unsure about.
* The replica keeps following the contract rather than the source, so an
  under-specified `physicalType` shows up as an under-specified column. #50 is
  the part of that which is a bug.
* **Replication stays one implementation, so it stays un-pluggable.**
  `core/engines/__init__.py` says a registry is a promise about an interface and
  wants a second implementation before making one; ADR 0016 says the same about
  catalog integrations. Adopting Debezium would have been that second
  implementation. Not adopting it means the question is closed for now, not
  merely deferred.
* This record costs a re-check whenever Debezium releases, which is the point
  of *On upgrade* below.

## On upgrade

* **Delete this record, and ADR 0008's SQL Server half with it, if Debezium
  grows a native row filter** — one that is not a scripting SMT and not a
  separate download. That single change removes reason 1 outright and makes
  reasons 2 and 3 configuration rather than divergence. It is the most likely
  of the four to close.
* **Re-measure the memory** rather than trusting the table above. It was taken
  on `:latest` in September 2026 with one connector and a 58 667-row snapshot.
  A Debezium that idles near the reader's footprint makes reason 4 disappear.
* **If a second replication transport arrives for any other reason**, revisit
  the plugin question with it, not before — that is the trigger ADR 0016 names,
  and this decision is what removed the candidate.
* **The Debezium Management Platform is not a path in.** It targets Kubernetes
  only, installs by Helm, wants 8 GB of RAM and pulls in an operator and an
  OpenTelemetry stack. The old `debezium-ui` was archived in September 2025.
  If CDC ever needs a screen, `Replication.tsx` already has one and already
  branches on `status.engine`.
