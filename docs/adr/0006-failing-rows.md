# 0006 — Failing rows by rewriting the check's own SQL

## Context

"522 orders disagree with their lines" is where an investigation starts and
none of them end. Neither dependency answers *which*: `datacontract test`
reports counts, and ODD's run model has no numeric field at all, let alone a
row.

## Decision

`core/sample.py` takes the statement the check actually ran — datacontract
hands it back in `implementation` — parses it with **sqlglot** and rewrites it
into the rows it counted: keep the FROM and the WHERE, replace the projection
with `*`, add a limit.

Going through a parse tree rather than string surgery is what makes this
portable: emitting the same tree as T-SQL turns the `LIMIT` into a `TOP`, so
one function samples both engines.

It refuses rather than guesses. A grouped query aggregates the rows away; a
freshness rule (`select case when max(loaded_at) < … then 1 else 0 end`) has no
failing row at all — the *absence* of rows is the failure. Both return nothing
and say why. `field_required` compiles to `SUM(CASE WHEN x IS NULL …)`, which
has no WHERE to keep, so its predicate is stated in `BY_TYPE` instead.

The sample runs **in the window the result was measured in**, or the count and
the rows under it disagree.

Columns the contract marks `classification:` are masked.

## Consequences

* `sqlglot` is declared directly in `pyproject.toml` even though
  datacontract-cli already pulls it in, because this imports it. A transitive
  dependency that disappears would take the sample rows with it.
* The `asof` views hold whichever day was built into them last, so the sample
  route rebuilds the window for the result's date before reading. Skipping that
  made a check stored as passing return a failing row after a backfill.
* Sampling exposes source rows to anyone who can reach the API. That is
  deliberate and bounded — the reader role is SELECT-only with a statement
  timeout — and it is why the compose says to keep this on a private network.

## On upgrade

* **sqlglot:** run `tests/test_sample.py`. It pins the rewrite, the dialect
  translation and the two refusals.
* **datacontract-cli:** if `implementation` stops holding the compiled SQL, or
  if it starts returning failed samples itself, this module gets smaller or
  disappears. Prefer their samples over our rewrite if they arrive.
* A new `check["type"]` that compiles to an aggregate needs a line in
  `BY_TYPE`.
