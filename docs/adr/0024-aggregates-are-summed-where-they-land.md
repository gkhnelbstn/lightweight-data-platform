# 0024 — An aggregate is summed where its rows land, and goes one way

## Context

#81 asked for many rows into one: invoice lines in billing become one journal
entry per invoice in the accounting package. Two things set it apart from
everything the flows did before:

* **A total has no inverse.** The lines cannot be regenerated from a sum, so
  this is never a pair. It is the same reason a value map is allowed and an
  expression is not (ADR 0019).
* **SeaTunnel's SQL has no aggregation and no joins** (ADR 0020). A job sees
  one row at a time, so no transform can say what an invoice now totals.

## Decision

**A flow may declare `aggregates`, and then its `columns` are the group:**

```yaml
id: invoice_totals
from: billing.invoice_line
to: ledger.journal_entry
columns: {EntryNo: InvoiceNo}
aggregates: {Total: sum(Amount), LineCount: count(*), CustomerCode: max(CustomerCode)}
```

**The rows land in the hub's database and are summed there**
(`core/flow_aggregate.py`, `core/flow_aggregate.sql`):

* The first job (`<id>`) lands every line change, append-only, in
  `flow.<id>_inbox`.
* A trigger keeps a mirror of the source table (`flow.<id>_lines`). It
  **recomputes every group the change touches** into `flow.<id>`, one row per
  group. Recomputing, rather than adding and subtracting, keeps every case
  right with one rule: an edited line, a deleted one, a line moved to another
  invoice (its key changed), and a line whose group column changed while its
  key did not. A group with no lines left loses its row.
* The recomputation is guarded with `IS DISTINCT FROM`, so an unchanged total
  is not rewritten. A rewrite is a change to logical decoding, and the next
  job would deliver it again for nothing.
* The second job (`<id>_out`) reads `flow.<id>` through Postgres CDC and
  writes the target with the engine's `MERGE` (`core/flow_sql.py`). Every
  revision of a total is written in full, and a deleted total deletes the
  entry.

Both jobs are ordinary jobs: they resume from their checkpoints and are
refused under the same conditions (ADR 0023). `core/flow_schema.py` checks both
ends, the lines read by CDC and the totals written.

**Refused by `core/flows.py`:**

* a flow back from the target to the source;
* a value map;
* an aggregate that is not `sum`, `count`, `min`, `max` or `avg` of one
  column, or `count(*)`;
* a group that is not exactly the target's key;
* a source with no key, because a changed line could not be told from a new
  one;
* a hub at either end.

## Consequences

* `tests/test_flow_aggregate.py` runs the trigger against a real Postgres,
  in CI's hub job, and fails there if it skips. Four mutations of the trigger
  each fail it: an emptied group keeping its row, no handling of a changed
  key, no guard on unchanged totals, and a regrouped line's old group not
  recomputed.
* The demo's billing has `invoice_line`, and a SQL Server `ledger` receives
  `journal_entry`. `verify.py` runs an invoice through every kind of line
  change and ends with no entry once the invoice has no lines. Each step took
  8–12 s live.
* The Integration tab lists one-way totals below the hubs: both jobs, the
  lines held, and the totals.

## Known limits

* Every line change recomputes its whole group, so a bulk insert of *n*
  lines into one invoice sums *n* times. That is fine for invoices and wrong
  for a group of millions, where an incremental sum would be the fix.
* A lines table is a copy of the source table in the hub's database. That is
  the cost of summing without a second engine (invariant 6).
* Lineage between the lines and the totals is not declared yet. The issue
  wants it as a `derivedFrom`, and nothing reads flows for lineage today.
