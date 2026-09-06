# 0002 — The window is a database object, and it differs per engine

## Context

Scoring a contract over its whole table makes incidents invisible. This was
measured, not assumed: over a 44-day backfill, cumulative scoring produced one
score improvement against seventeen for the daily window. A score that only
ratchets is a score nobody looks at twice.

`datacontract test --filter` is meant to be exactly this and was broken in
1.1.3 — it emitted a nameless `DROP VIEW IF EXISTS` — and the ibis API beneath
it, `Table.alias`, is documented by ibis as not public and due for removal.

## Decision

The window is a **database object the contract addresses through a second
`servers` entry**, so it is standard SQL and standard ODCS with nothing to
patch.

* **Postgres:** a schema of views (`asof`) over one day's arrivals. An
  unqualified `sales_orders` in a rule resolves to the view through
  `search_path`.
* **SQL Server:** a separate **database** (`erp_asof`), not a schema. T-SQL has
  no `search_path` — an unqualified name resolves through the *user's* default
  schema — and the rules in a T-SQL contract are written `dbo.sales_orders`
  anyway, so a second schema is invisible to them. This was built as a schema
  first and proved it: the views were correct, held 1,333 rows against the
  table's 58,667, and changed nothing.

Which rows the window contains is the contract's to state, as
`customProperties: windowPredicate`. The default is the arrival watermark
`loaded_at = <day>`.

Table-level invariants (`field_unique`, `field_primary_key`) are re-run
**unwindowed** and replace the windowed result. Scoped to one day, a uniqueness
check only sees duplicates that arrive in the same batch — ours passed for 45
days on a table with 8 duplicate primary keys in it.

## Consequences

* Every contract must be windowable, and `tests/test_contracts.py` enforces it.
  Implementing the window for Postgres alone quietly made the SQL Server
  contract cumulative for forty-five days.
* The `asof` views are **mutable shared state**: they hold whichever day was
  built into them last. Anything that reads through them for a *stored* result
  must rebuild them for that result's date first — `api/main.py`'s sample route
  does.
* A custom rule must not pin the window itself, and on Postgres must not
  qualify its schema.

## On upgrade

* **datacontract-cli:** if `--filter` becomes usable *and* correct, this whole
  mechanism can go. Check [datacontract-cli#1593](https://github.com/datacontract/datacontract-cli/issues/1593)
  too — per-rule scoping in ODCS would retire `TABLE_SCOPED_TYPES` and the
  second unwindowed pass with it.
* **A new source engine:** it needs a window before it needs anything else.
  Ask first whether an unqualified name resolves through a session setting
  (schema works) or through the user (database is needed).
* **SQL Server:** `CREATE DATABASE` cannot run inside pyodbc's implicit
  transaction. Set `autocommit` first.
