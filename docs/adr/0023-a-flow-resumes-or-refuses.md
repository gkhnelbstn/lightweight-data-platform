# 0023 — A flow resumes from its checkpoint, or refuses to start

## Context

An outage is ordinary: SeaTunnel's container restarts, or its host does.
Three things were measured on the demo (#84):

* **A restart loses every job.** The cluster keeps no job state across its own
  restart. Its checkpoints stay on disk, while the container's filesystem
  lasts.
* **A fresh start re-reads every table, and the hub cannot tell a re-read from
  an edit.** `--apply` used to resubmit each flow from scratch. A record both
  systems hold then arrives as an `INSERT`, and the first-sync rule decides it
  (ADR 0021): billing's edit made during the outage was reverted to the CRM's
  value and logged as `seed`. A row deleted during the outage is not in the
  re-read at all. Its delete never reached the hub, and the customer stayed in
  the CRM and the hub after billing had removed it. Both failures were silent.
* **Resuming from a checkpoint is correct.** SeaTunnel resumes a job when it
  is submitted with its old id and `isStartWithSavePoint=true`. The four edits
  made during the outage arrived as changes, in commit order: an edit, a
  delete, a new customer and a CRM edit. Nothing was reverted.

Two cases make resuming unsafe, and SeaTunnel reports neither:

* **An id with no checkpoint.** SeaTunnel starts the job from scratch, as a
  fresh submit would, and says nothing.
* **A SQL Server source whose CDC retention ran out during the outage.** The
  job resumes past the purged changes, and a CRM edit was lost without an
  error. This was simulated with `sp_cdc_cleanup_change_table`.

## Decision

**A flow resumes under the job id it last ran as, or it refuses to start.**
`core/flow_resume.py` decides; `core/flow_apply.py` does it.

* `hub.job` keeps each flow's job id. `--apply` resubmits a stopped flow under
  that id with `isStartWithSavePoint=true`, and starts a flow that never ran
  from scratch.
* SeaTunnel's checkpoint storage is a volume shared read-only with the app
  (`seatunnel-checkpoints`). Before resuming, the app reads the time of the
  job's last checkpoint from its file names. **No checkpoint: refused.**
* For a SQL Server source, the app compares that time with the oldest change
  the capture instance still keeps (`fn_cdc_get_min_lsn`). **Older checkpoint:
  refused.** A capture instance recreated by a reseed refuses the same way,
  which is right, because the old position means nothing in the new instance
  (#17). A Postgres source keeps its WAL in the slot until it is read, so it
  has nothing to check.
* `--stop` now stops with a savepoint, so the next `--apply` resumes exactly
  there.
* `--resnapshot` is the deliberate way through a refusal. It starts from
  scratch and says what that costs.

### A table that changed under its flow

The flow and its contract are the source of truth (invariant 1), so a schema
change is refused and reported, never followed. What each change does was
measured, with the flows running:

* **A column added** is invisible. A CDC capture instance keeps the columns it
  was created with, so nothing breaks, and only the contract is out of date.
* **A mapped column dropped** empties nothing on the way in. CDC keeps it as
  NULL in both the before and the after image, and an unchanged field is not
  an edit. On the way out, though, the `MERGE` fails with `Invalid column
  name`, and the job's error is four kilobytes of stack trace.
* **The column added back** looks captured by name and is not. The capture
  instance lists the old column's id, so the new one is never read, silently.

`core/flow_schema.py` compares each flow's mapped columns with the live table,
and for a flow reading CDC with the capture instance, **by column id**.
`--apply` refuses a flow that fails the comparison. The Integration tab lists
the same problems under the system while the flow runs, and gives a failed job's
last `Caused by` instead of its stack. Following a real schema change means
changing the contract and the flow, then a new capture instance, and that
comes to a re-read, which is `--resnapshot`.

## Consequences

* The outage drill is `demo/integration/outage.py`: create two customers,
  restart SeaTunnel, then edit, delete and insert. The resubmit caught up in
  **15 s**, and nothing went through the first-sync rule. With the checkpoints
  hidden, all eight flows were refused and none started.
* A refusal stops the whole `--apply`. Flows that can resume do; the refused
  ones are listed with their reasons, and the command exits non-zero.
* CDC retention is now an operating limit to state: SQL Server's default is
  three days. An outage longer than that is refused. The alternative, a
  re-read, puts every difference through the authority.

## Known limits

* A flow whose compiled job changed since its checkpoint resumes the old
  position under the new job. If SeaTunnel rejects the restore, the way on is
  `--resnapshot`.
* SeaTunnel keeps three checkpoints per job (`max-retained`). The volume is
  what keeps them across a restart; deleting it is the same as losing them.

## On upgrade

* **SeaTunnel keeping jobs across a restart** (a persistent IMap store) would
  make most resubmits unnecessary, but the refusals would still stand.
* **SeaTunnel refusing to restore an id with no checkpoint**, or failing a
  CDC source whose position is gone, would let the two checks here go. Check
  both before deleting them.
