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
* **Revision.** Each change to the golden record also says what it changed
  (`_changed`, `,name,city,`, or `*` for all of it) and which system it came
  from (`_skip`). A delivery writes only those fields, and not to that system.
  Why, and why without it the live demo looped, is under *Several tables*.

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

Whatever the hub changes, it awaits from every *other* system that receives
the field. It does not await it from S, which already has the value and would
produce no change -- unless another value is still on its way to S. Then that
delivery lands after S's edit, so S is sent its own value again and it is
awaited (`back` in the merge).
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
`seed`. The same rule covers two systems creating one key. Any table of the
authority's system counts (`crm.account_address` is the CRM too), and when the
authority's snapshot arrives second, the other system's value is already on
its way to it -- so the authority gets its own value again. The live demo
found this one: the disputed name swapped sides.

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
    a `MERGE` the compiler writes. Each column is `CASE WHEN` the revision
    changed it `THEN` the new value `ELSE` the target's own, so a revision
    never writes an unchanged field over an edit the target made meanwhile.
    Revisions from the target itself are filtered out (`_skip`), and so are
    records still missing a column the target requires: the part of a record
    that arrived first waits for the rest rather than failing a `NOT NULL`.
    Only a revision marked `*` creates a row. Deletes are a separate branch,
    by key.
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
| a customer and its address in one CRM transaction | one billing row, 10 s |
| a CRM address edit; a billing column set, then cleared | 7–9 s; the CRM row is created, then deleted |
| a CRM address row deleted | billing's column emptied, the customer kept, 8 s |
| idle after all of it, eight jobs | inbox unchanged for 30 s |
| first sync, two numberings, 4 customers in both, 1 in each only | 6 records, every one with both codes, nothing held |
| a customer new to either system | numbered by that system, linked in the hub, 6–9 s |

### Several tables, one record (#79)

A record can live in several tables of one system and one row of another: the
CRM keeps a customer in `account` and its addresses in `account_address`, one
row per type, and billing keeps both cities as columns. Each address type is
its own flow pair, and **`match`** pins the rows it means:

```yaml
from: crm.account_address            from: hub.customer
to: hub.customer                     to: crm.account_address
match: {ADDR_TYPE: INV}              match: {ADDR_TYPE: INV}
columns: {code: ACCOUNT_CODE,        columns: {ACCOUNT_CODE: code,
          invoice_city: CITY}                  CITY: invoice_city}
```

On the way in `match` is a filter; on the way out it is a constant, part of
the `MERGE`'s join and of the delete's key. `core/flows.py` requires it
wherever the system table's key is wider than the hub's, requires both
directions to use the same one, and refuses two tables of one system filling
the same field.

* **Owner and parts.** A flow carrying every field the hub contract requires
  owns the record: its insert creates it and its delete deletes it. The hub
  learns which fields those are from the contract (`hub.entity.required`) and
  which fields each system receives from its out-flows (`hub.system.fields`),
  so nothing is awaited from a system that never gets the field.
* **A part arriving first starts the record.** Two tables written in one
  transaction reach the hub through two jobs, in either order. The part makes a
  record with the rest empty; deliveries hold it back until the required
  fields are there, and the revision that completes it is marked `*`, because
  to every target it is new.
* **A never-set field is filled, not disputed.** An `INSERT` bringing a value
  for a field no one has set is a gap, not a first-sync conflict.
* **An emptied field is not a gap.** It has a commit time. The first live run
  looped because it was treated as one: a delete emptied a field, an older
  delivery recreated the row, the recreated row refilled the field, and round
  it went. A never-set field has no commit time; that is the whole test.
* **A row going away empties its part.** A delete from a flow that does not
  carry the required fields becomes an update to empty. On the way out an
  empty part is a delete of that row, not a row with a `NULL` city -- but only
  when the revision names the field. A revision new to the target (`*`) with
  the part still empty is the owner's row arriving before its address, and
  deleting the target's row then deletes the address on its way in. #80's
  live run found this; #79's had passed by the luck of arrival order.

The loop also showed that a delivery of the whole row is wrong once several
flows write one record: a revision triggered by the address carried the
customer's name as well, and landed over an edit made in the meantime. Hence
`_changed` and the compiler's own `MERGE`.

### Different codes, one record (#80)

The same customer is `1` in the CRM and `120.01.014` in billing. The hub
contract names the column holding each system's own code, and the record's key
is the hub's own:

```yaml
customProperties:
  - property: hub
    value: {authority: crm.account, keys: {crm: crm_code, billing: billing_code}}
```

**The codes are columns of the golden record, not a table beside it.** The
delivery back is a SeaTunnel job reading the golden record's CDC, and it
cannot look anything up; the target's code has to be in the row it reads.
Each is a unique column, so one code cannot name two records, and one record
cannot hold two codes of one system. A deleted record's codes go into its
tombstone, so a late change under one of them still meets the delete.

**A row under a code the hub has not seen** (`hub.resolve`):

* the flow that owns the record declares how to find it, `linkBy: [tax_id]`,
  and `core/flows.py` refuses an owner flow without one;
* exactly one record matches and has no code of this system: it is that
  record, and the code is linked;
* none matches: it is a new record, with a new hub key;
* several match, the match already has another code of this system, or the
  row has nothing to match by (a null tax number): it is **held** in
  `hub.unmatched` and nothing is created. A person decides with
  `hub.link(entity, system, code[, record])`, and what was held is replayed;
* a part (an address) under an unseen code cannot say who the customer is. It
  is held until its owner's row places the code, then replayed.

None matching could also be a customer who exists under another code with a
different tax number. The two are indistinguishable; the rule defines who is
the same customer. A duplicate is only ever made by a rule that said so, and
a missing value to match by is held rather than guessed.

**A record new to a system** needs that system's code, which the hub cannot
make up. So a system receiving new records assigns its own (the flow lists
the key in `filledByTarget`, and `core/flows.py` refuses one that does not).
The compiled `MERGE` leaves the key out of the insert, and the insert coming
back through the target's CDC is matched by the rule and linked. Until then
the golden record has no code there, so the `MERGE` also matches the target's
row by the pair's `linkBy` (`s.CustomerCode IS NULL AND t.TaxId = s.TaxId`):
a second delivery before the link updates rather than inserts again, and at
the first sync it finds the row the target had all along. The revision that
links a code is new to that system's other tables, which could not be written
without it: they read the key's column in `_changed` like `*`.

### Known limits

* **An awaited value that never comes back** (#84). Measured after a first
  sync and a `verify.py` run: eight were left. Seven were empty values
  awaited from the CRM's address table for addresses it has no row for.
  One was a value the CRM already held, where the out-flow's snapshot read
  the final record and the delivery changed nothing. Either kind swallowed a
  later edit to exactly that value, and both cases were reproduced in tests:
  an address added and then removed, and a name changed and then changed back.
  Now a value the system already had is dropped from what the hub awaits,
  since delivering it there cannot produce a change. That value is its before
  image, or nothing at all for a new row. The hour stays as garbage
  collection, swept on every `expect_add` rather than only when the same
  field is next consumed.
* **A value outside a value map** lands as NULL (ADR 0020), and SeaTunnel's
  SQL cannot raise. So the in-flow also says which fields it happened to
  (`unmapped`, a `CASE` per mapped column). The hub keeps its own value, since a
  NULL there is not the system emptying the field, and logs the loss in
  `hub.conflict` with reason `unmapped`, which the Integration tab shows (#84).
  The system keeps its unknown value until a person decides. Overwriting a
  code someone typed on purpose would be just as silent. On the way out, a hub
  value the target's map lacks still lands as NULL. The maps are checked to be
  inverses, so only a third system's value could get there.
* **Many to one is partly built** (#53). Done: several tables into one record
  (#79), differing codes (#80), and a third system (#82) on a third engine,
  Postgres (#83). Aggregation, which is one-way only (#81), is not. With three
  systems, every system has its own code. A conflict on one field between all
  three converges on the latest commit, and both losers are logged. The first
  sync took the authority's spelling of a name over two others. Measured by
  `verify.py`.
* A system's code is one column. A composite local key waits for a system
  that has one.
* A system whose codes are typed by people cannot receive new records: there
  is nothing to put in `filledByTarget`. Its rows still link and sync.


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
  delete, then parts of a record, emptied fields and what each revision
  tells its deliveries. CI runs it in the job that has a Postgres, and fails
  if it skips. Mutations of the merge each fail it: no echo check, no bypass
  for current-value edits, a flipped comparison, deletes ignoring time, no
  de-duplication, gap-filling an emptied field, no partial delete, and no
  redelivery to a winner with a value still on its way.
  `tests/test_hub_crosswalk.py` does the same for #80, and each of these
  mutations fails it: no refusal of a second code, never linking by the rule,
  no tombstone lookup by code, no replay after a link or a creation, the link
  not written, and parts not held.
* The hub is a new database, but not new infrastructure (invariant 6). It is
  the Postgres the stack already runs.

## On upgrade

* **PostgreSQL 18** detects conflicts in its own logical replication, but it
  does not resolve them and it only covers Postgres to Postgres. It changes
  nothing here.
* **SeaTunnel** gaining per-row state or origin tagging would not replace this
  either. The hub is what makes three or more systems work: each one talks to
  the hub, not to every other system.
