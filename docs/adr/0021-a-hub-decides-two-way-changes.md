# 0021 — Two-way changes meet in a hub, and the latest commit wins

## Context

Two systems of record share records, and both sides edit them: the same
customer, the same fields. That cannot be prevented (#53). What goes wrong
without a design is known by now:

* **Echo.** A value written into a system comes back through that system's
  CDC as if someone had edited it. Between two Postgres tables SeaTunnel's
  generated upsert loops for ever (ADR 0020).
* **Late echo.** A value written into a system can come back after the record
  has moved on. Taken for an edit, it overwrites the newer value with the older
  one, and it does so with a *newer* commit time, so no timestamp rule saves
  it.
* **Conflict.** Two systems change the same field within the few seconds a
  change takes to cross. Something has to decide, and the rule chosen in
  conversation is **the latest edit wins**.

Every one of these needs per-row state: what was agreed, when each field last
changed, and which writes are still on their way back. SeaTunnel keeps none of
it, and no adoptable tool keeps it for SQL Server (ADR 0020). This is the part
that has to be ours, so it is kept to one place and to SQL.

## Decision

**Systems never write to each other.** Each writes into a hub, the hub decides
what the record now is, and the hub's record goes back out to every system:

```
system A ──SeaTunnel──▶ hub.<entity>_inbox ──trigger──▶ hub.<entity> ──SeaTunnel──▶ system A, B, …
system B ──SeaTunnel──▶        (append-only)            (golden record)
```

* **Inbox.** SeaTunnel lands every change append-only. `RowKindExtractor`
  keeps each update as its before row and after row, `Metadata` adds
  `SourceTimestamp`, which is the commit time with the carried commit (ADR
  0020), and a plain `insert` keeps the rows in commit order. The inbox is
  also the retained change log #56 asked for.
* **Merge.** An `after insert` trigger on the inbox runs `hub.merge`
  (`core/hub.sql`) in the same transaction. There is no process of ours in the
  stream. Changes to one record are serialised with an advisory lock.
* **Golden record.** `hub.<entity>` holds the canonical row, plus `_at`
  (each field's commit time), `_by` (the system that set it) and `_rev`. It has
  `replica identity full`, which SeaTunnel's Postgres CDC needs in order to
  carry it back out.

### The merge, field by field

For a change from system S, a field goes through these tests in order:

1. **Is the value one the hub sent to S and is still waiting for?** Then this
   is the echo. It is forgotten, along with anything older awaited for that
   field, and nothing else happens. The queue is `hub.expect`, in send order,
   which is what makes a late echo harmless.
2. **Did S's before and after images agree on the field?** Then S did not
   touch it.
3. **Is the new value already the hub's value?** Then everyone agrees.
4. **Did S edit the value everyone has** (its before image equals the hub's
   value)? Then this is an ordinary edit, and it is applied whatever the clocks
   say. A server whose clock is behind still gets its edits through.
5. **Otherwise S edited something stale, and that is a conflict.** The later
   commit wins; a tie goes to the system name, so every replay decides the
   same way. Either way a row goes into `hub.conflict` with both values,
   both systems and both times. If S lost, the next delivery corrects S (a
   `_rev` bump forces one), and that correction is awaited as an echo.

Whatever the hub changes, it awaits from every *other* system. It does not
await it from S, which already has the value and would produce no change.
`hub.expect_add` never queues the same value twice in a row, and anything
awaited for more than an hour is dropped: a write that produced no change
would otherwise wait for ever.

**Deletes** follow the same rule against the newest field. A delete committed
after every edit removes the record everywhere. One committed before an edit
it did not see is refused and logged, and the record is sent back to the
system that deleted it.

A deleted record leaves a **tombstone** (`hub.tombstone`) with the delete's
commit time. Without it, a change for a record the hub no longer has looks
like a new record. The live demo found exactly that: a winner's delivery came
back after the record was deleted and recreated it. So for a record the hub
does not have, the merge first consumes awaited values; if nothing genuine is
left, it stops there. A genuine edit then meets the delete by commit time.
An edit newer than the delete brings the record back, and an older one loses
to it. Both are logged.

**The first sync** is not an edit. Both systems already hold the same record,
each arrives as an `INSERT`, and commit times say nothing about which value is
right. So an `INSERT` for a record the hub already has goes to the hub
contract's **authority** (`hub: {authority: crm.account}`, decided in
conversation). Its values stand, and every difference is logged with reason
`seed`. The same rule covers two systems creating one key.

### The hub is a contract, and the jobs are compiled from flows

* The golden record's shape is an ODCS contract whose `hub` custom property
  names the authority (`demo/integration/hub_customer.odcs.yaml`). Each system
  has a flow in and a flow out (ADR 0019, core/flows.py). Two systems can no
  longer pair directly: `core/flows.py` refuses it, because only a hub can
  tell an echo from an edit. `winsOnConflict` is gone, since the commit time
  decides.
* `core/flow_jobs.py` compiles each flow into a SeaTunnel job:
  * in: CDC, `Metadata` (`SourceTimestamp`) and `RowKindExtractor`, the map as
    SQL, then a plain insert into the inbox;
  * out: the golden record's CDC, before images dropped, the reverse map, and
    the sink's generated `MERGE` into the system.
* `core/flow_apply.py` creates the hub, registers the entity and the systems
  that have both directions, and submits the jobs through SeaTunnel's REST
  API. Credentials stay placeholders until that request.

### Measured, on the demo

Two SQL Server databases, `crm` and `billing`, with different names, types and
codes. `demo/integration/verify.py` repeats the run on demand.

| | |
|---|---|
| first sync, 5 + 5 customers, 3 identical, 1 disputed, 1 on each side only | 6 in both; the disputed one took the CRM's value, logged `seed` |
| an edit, either direction, through the hub | 6–9 s |
| the same field edited on both sides 1 s apart | both converge on the later commit; the loser is in `hub.conflict` |
| insert, delete | 5–10 s |
| idle after all of it | inbox unchanged for 30 s: no echo loop |
| SeaTunnel with the four jobs | 669 MiB |

### Known limits

* A value awaited from a system can be superseded before the job that would
  deliver it starts. At the first sync, the out-flow's snapshot reads the
  final golden record, so the intermediate value is never sent. The awaited
  value is then never consumed. It is harmless unless that system edits the
  field to exactly that value within the hour it lives.
* A value outside a value map lands as NULL (ADR 0020). The hub contract's
  checks have to catch it.
* Postgres *targets* are not compiled yet. They need the guarded upsert and a
  delete branch of ADR 0020, and the compiler refuses them rather than emit
  a looping job.
* **Many to one is not built yet** (#53): several tables into one record, a
  crosswalk for differing codes, and aggregation, which is one-way only.

### What this record does not decide

* **Where the hub runs.** It is a Postgres database like `erp` and `dq`
  (`python core/hub.py --init`). One per deployment is assumed.
* **Losing systems whose writes are rejected by their own rules.** If the hub
  sends a value the target's constraints refuse, the SeaTunnel job fails.
  Surfacing that is the compiler's and the panel's job.

## Consequences

* The conflict rule has a measurable cost: `hub.conflict` is the list of every
  value that lost. It belongs on the panel next to the checks.
* `tests/test_hub.py` plays SeaTunnel against a real Postgres: new records,
  echoes, late echoes, both conflict orders, a clock behind, and both kinds of
  delete. CI runs it in the job that has a Postgres, and fails if it skips.
  Five mutations of the merge each fail it: no echo check, no bypass for
  current-value edits, a flipped comparison, deletes ignoring time, and no
  de-duplication.
* The hub is a new database, but not new infrastructure (invariant 6). It is
  the Postgres the stack already runs.

## On upgrade

* **PostgreSQL 18** detects conflicts in its own logical replication, but it
  does not resolve them and it only covers Postgres to Postgres. It changes
  nothing here.
* **SeaTunnel** gaining per-row state or origin tagging would not replace this
  either. The hub is what makes three or more systems work: each one talks to
  the hub, not to every other system.
