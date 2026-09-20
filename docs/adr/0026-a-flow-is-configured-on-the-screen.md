# 0026 — A flow is configured on the screen, and states two things about how it runs

## Context

The Integration tab (ADR 0022) could only be read. The ask was to configure
the integration — and SeaTunnel — from it, and the obvious reading of that
breaks invariant 1: a screen that pushes job configuration at SeaTunnel makes
the running job the source of truth, and the flow file a description of
something that used to be true.

There is also the second half of the ask: whether SeaTunnel's features are
used for anything.

## Decision

**The screen edits the flow file, and nothing else.** `api/integration_edit.py`
loads `contracts/flows/<id>.yaml`, offers its map, its value maps, its `match`,
`linkBy`/`filledByTarget` and its job settings, and writes the file back:

* **Check before save.** The same refusals `--check` makes (`core/flows.py`),
  plus the compile, run against the edited flow *without writing it*. A map
  that is not an inverse, a column a contract does not declare, a value map
  that sends two values to one: refused on screen, in the same words.
* **The file keeps its comments.** `ruamel` round-trips the document and the
  edit changes one key at a time, so the sentence explaining why a map is the
  way it is survives an edit made by someone who never opened the file. Block
  style replaces flow style on a line the edit touches; nothing else moves.
* **Apply, stop, restart and re-snapshot call `core/flow_apply.py`** — the
  functions `--apply` and `--stop` call. A flow started from the screen is the
  same flow started from the CLI, resumed from the same checkpoint (ADR 0023),
  refused for the same reasons.

**A flow states exactly two things about how SeaTunnel runs it** (`job:` in
the file, `JOB_SETTINGS` in `core/flows.py`):

```yaml
job: {checkpointInterval: 5000, rowsPerSecond: 400}
```

* `checkpointInterval` — how often the job checkpoints. It is what a resume
  costs at most, and what the engine spends when nothing is happening.
* `rowsPerSecond` — SeaTunnel's own `read_limit.rows_per_second`. A first read
  of a large table is the one thing a flow does that can take a source
  database down with it.

Anything else in `job:` is refused, by name, with the two it takes.

## What of SeaTunnel is used, and what is deliberately not

Used: CDC sources for SQL Server and Postgres, `Metadata` for the commit time,
`RowKindExtractor` and `FilterRowKind` to keep before and after images apart,
`Sql` for the column and value map, the `Jdbc` sink with a compiled statement,
checkpoints and savepoints, the REST API for submit, stop, resume and metrics.

Not used, and why:

* **Parallelism.** A second reader reorders one record's changes. The hub
  decides by commit time (ADR 0021) and a re-ordered pair of edits to one row
  is exactly the case it cannot recover. Fixed at 1, not offered.
* **Schema evolution** (`schema-changes.enabled`). A table that changed under
  its flow is refused, never followed (`core/flow_schema.py`): a column
  quietly appearing in a golden record is a worse outcome than a stopped flow.
* **Exactly-once sinks** (XA). Both sinks are idempotent by construction --
  an inbox row the hub merges to the same value, and a guarded `MERGE`
  (ADR 0020) -- so the cost buys nothing.
* **Several tables in one job.** Resume, refusal and drift are per flow
  (ADR 0023); one job per flow is what makes them so.
* **`startup.mode` as a setting.** Whether a flow starts from a checkpoint or
  reads the table again is ADR 0023's decision, taken from the checkpoint and
  the CDC retention, not a box on a form. `Read from the start again` is the
  deliberate way past it, and it asks first.
* **A dead-letter queue.** SeaTunnel's JDBC sink has none; the hub's inbox is
  where an unmergeable row lands instead (`hub.unmatched`, `hub.conflict`).

## Consequences

* The app service needs the flow credentials, since it is now the process
  that fills them (`compose.demo.yaml`). They are still never written into a
  job file.
* `_fill` refuses an *empty* credential as well as a missing one: an empty
  username reaches SeaTunnel as valid config and comes back as
  "Factory initialize failed — Unable to create a source", which names
  neither.
* `stop()` waits for the job to be gone. Taking a savepoint takes seconds,
  and an apply that followed immediately read "already running" and started
  nothing — measured, and the flow stayed stopped.
* Editing is not guarded by the token (ADR 0010): a flow file carries no SQL
  anyone typed. Its columns are the contracts' own and its values are
  literals the compiler escapes. Starting and stopping jobs is open for the
  same reason the rule editor is; a deployment that needs more needs
  authentication in front of the whole panel, not a second token here.
* Deleting a flow is not offered. Deleting the file is a one-line thing to do
  where the files are, and a button that stops a stream and leaves the other
  direction running is not.

## On upgrade

If SeaTunnel gains ordered parallel readers per key, parallelism becomes a
setting worth having. If it gains a dead-letter sink, the hub's `unmatched`
table is no longer the only place a stuck row can sit.
