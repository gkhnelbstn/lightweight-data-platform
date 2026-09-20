# 0027. An API source is a poll, and it goes one way

Date: 2026-09-20

## Status

Accepted. Extends ADR 0021 (the hub) and ADR 0020 (SeaTunnel carries the
flows). Closes #97, split out of #84.

## Context

Every source the platform reads so far has a change log: SQL Server's CDC
capture tables, PostgreSQL's logical decoding. The hub's rules are built on
what a change log gives -- a before image, so an untouched field can be told
from an emptied one, and a commit time, so the later edit wins (ADR 0021).

An HTTP API has none of that. It answers with the record as it is now. There
is no before image, no commit time unless the payload carries one, and a
record that stops being listed is not an event. Reaching for a CDC tool here
gets nothing: Debezium and SeaTunnel's CDC connectors are change-stream
readers and an endpoint has no stream (#53).

## Decision

### The transport is SeaTunnel's `Http` source, polling

`connector-http-base` is already in the image, so this is a flow like any
other: the compiler spells it in SeaTunnel's words and nothing new runs
(invariant 6). `poll_interval_millis` is what makes it a streaming job, so an
API flow resumes from its checkpoint like every other flow (ADR 0023).

### An API source has a contract, and the contract says how to read it

Invariant 1 does not bend for a source without a table: a flow's `from` is a
contract id, and the contract is where the API's record shape lives. ODCS has
a server type for it, so the endpoint is the server's `location`, and the two
things SeaTunnel needs that ODCS has no field for are a custom property:

    servers:
      - {server: loyalty, type: api, location: "http://loyalty:8099/members"}
    customProperties:
      - property: api
        value: {contentField: "$.members.*", changedAt: updated_at}

`contentField` is the JSON path to the records in the answer. `changedAt` is
the next decision.

### An API that cannot say when a record changed is refused

The hub decides a conflict by commit time. Poll time is not a commit time: it
is when we noticed, which is always newer than every CDC edit that has not
been polled yet. An API given the poll time would win every dispute it takes
part in, including the ones where its value is the stale one -- and it would
win them silently, which is the failure this repository refuses everywhere
else (invariant 5: a check that could not run is not a check that failed).

So the contract names the field carrying the record's own change time
(`changedAt`), and `core/flows.py` refuses an API source without one. The
refusal says what to do about it: the API adds the field, or its records do
not take part in a two-way hub.

### It goes one way, and it deletes nothing

A flow *from* a hub *to* an API server is refused. Two-way through an HTTP
endpoint would need a write, an echo the hub can recognise and a commit time
for it; none of those exists here.

A record that disappears from a listing is not a delete. Detecting one needs
the whole listing compared against the hub, and a paging error then reads
exactly like every record having vanished -- so it is not done by default,
and it is not done at all until something asks for it.

An API source is still a system of the hub, because that is where its
`linkBy` rule lives -- and it is registered as receiving **no** fields
(`hub.system.fields = '{}'`). That is not a new flag: the hub already awaits
only the fields a system receives, so a system that receives none is awaited
for nothing, and no expectation is ever written for a delivery that will
never be made.

### The previous poll is the before image

Every poll delivers every record it lists, so without a before image the hub
would read each poll as an edit of every field. Where the hub agrees with the
API that costs nothing -- an already-agreed field changes nothing. Where the
hub holds a *different* value, because another system changed it and the API
was never written back, the poll disagrees for ever: one `hub.conflict` row
per record per poll, the loser always the same stale value.

So the hub keeps the last row each API source delivered for a record, and
that is the before image of the next poll (`hub.pending_before`, the table
that already holds an update's before image between its two rows). A field
that did not move between two polls is then not an edit, exactly as it is not
for a CDC source, and the first poll after a real change in the API is one
edit of one field.

It is remembered **only once the row is placed**. A poll still waiting in
`hub.unmatched` has changed nothing yet, and kept as a before image it makes
the next answer -- the same one, because nothing changed in the system either
-- look like no news: the record it finally finds is linked and left empty, or
never created at all. Measured on the demo before the order was fixed.

This is the answer to both of #97's "must not happen": a poll that returns
the same record twice is not two edits, and a paging error that returns
nothing is not a delete, because nothing here deletes.

### Its checkpoints are spaced by its poll

A polling reader sleeps between listings and can take a checkpoint only
between them. With checkpoints closer together than the poll they queue
behind the sleep, one expires, and SeaTunnel answers that by failing the
whole job -- measured, with `checkpoint.interval` at the three seconds a CDC
flow uses and a ten-second poll. So an API flow's interval defaults to twice
its poll and a shorter stated one is refused, and its `checkpoint.timeout`
outlasts a poll in flight.

There is little to resume anyway: the next poll returns the whole listing, so
an API flow that restarts from scratch loses nothing but the before image of
its last answer -- one poll's worth of "no news", not data.

## Consequences

* An API source can create records, link them by `linkBy` and fill fields,
  exactly like a CDC source. It cannot delete one.
* Its rows carry `changedAt` as `source_ms`, so they are ordered against CDC
  edits by the same rule as everything else.
* The poll interval is a load question for the other side, so it is a flow
  setting (`job: {pollSeconds}`) and is editable on the Integration tab
  (ADR 0026).
* **It took a patch of our own to run at all** (#126). SeaTunnel 2.3.13's
  `Http` source waits out `poll_interval_millis` holding the checkpoint lock,
  which is the lock the barrier needs, so the checkpoint never completed and
  the job was failed at the timeout while the reader was still polling.
  Raising the interval or the timeout cannot help a contended lock.
  `Object.wait` frees the monitor while it waits, `Thread.sleep` does not,
  and that word is the whole patch -- carried in
  `deploy/Dockerfile.seatunnel` behind an anchor, like ADR 0011's.
* The hub's inbox never goes quiet while an API flow runs, so the demo's
  echo-loop check counts rows that *did* something: a poll repeating a member
  is `unchanged`, and a settled pair still has to stop writing.
* `demo/integration/loyalty_api.py` is the demo's API: the app image with a
  different command, like `sync-mssql` (ADR 0020). Its records live in a
  database of its own, which no flow reads -- `demo/integration/verify.py`
  changes a member there and watches the hub.

## On upgrade

* SeaTunnel's `Http` source gained `poll_interval_millis` before 2.3.13 and
  the option names are checked against the connector jar, not the website.
  Check them again on a SeaTunnel bump -- and check #126 first: a bump is the
  most likely thing to fix it.
* If SeaTunnel ever grows an incremental API source (a cursor the server
  honours), the poll here becomes a full listing that is cheaper to replace
  than to keep.
* This decision can be deleted the day an API source is expected to write
  back -- but that is a different design, not a wider one.
