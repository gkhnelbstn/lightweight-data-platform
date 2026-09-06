# 0004 — Results as a partitioned time series in PostgreSQL

## Context

`datacontract test` runs and forgets: no as-of date, no storage, no trend. ODD
keeps run history but its run model has **no numeric field at all** — a run is
a status and a reason, so "16 of 3376 rows" cannot be stored there and cannot
be charted from there.

## Decision

`check_results` and `contract_scores` in a plain PostgreSQL database, monthly
range partitions and a BRIN index on `run_at`. No TimescaleDB: at this row
count it buys nothing and brings a licence surface.

A result is stored so it can be **read on its own** — the rule in words
(`name`), what kind of check it was, the field, the sentence datacontract wrote
about why it failed (`reason`), and the statement that actually ran (`sql`).
Storing only counts meant a stored result could not be understood without
opening the contract and guessing.

Re-running a day **replaces** it. An upsert alone leaves behind any check that
existed in an earlier run of the same day and does not exist now — that is how
`missing_env_DATACONTRACT_SQLSERVER_USERNAME`, from a run before the
credentials were set, stayed on the dashboard as an open failure.

## Consequences

* Results outlive checks. Deleting a rule from a contract leaves its history
  behind; anything reading results has to handle orphans.
* `sql` is the column `core/sample.py` rewrites into the failing rows, so it is
  load-bearing, not decoration.
* `window` is a reserved word in PostgreSQL — the column is `run_window`.

## On upgrade

* PostgreSQL major versions: nothing here is version-specific except
  `add column if not exists`, which is ancient.
* If ODD ever stores numbers on a run, re-read this. As of 0.29.0 it does not,
  and its metrics API cannot take a second write (ADR 0005).
* New columns go in `DDL` **and** as `alter table … add column if not exists`,
  because `create table if not exists` will not add them to an existing
  install.
