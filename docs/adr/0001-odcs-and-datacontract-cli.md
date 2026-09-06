# 0001 — ODCS contracts, checks run by datacontract-cli

## Context

The first version of this repository had its own contract format, its own
check model, its own SQL compiler and its own dbt/Great-Expectations
exporters. About 900 lines. All of it worked, and all of it was a second
implementation of something a funded project already maintained.

Meanwhile the Open Data Contract Standard (Bitol, Linux Foundation) had become
the format, and `datacontract-cli` (MIT) derived checks from it, compiled them
per dialect and executed them in the source database — for Postgres, SQL
Server, Snowflake, BigQuery and about twenty others.

## Decision

Contracts are ODCS (`contracts/*.odcs.yaml`). `datacontract test` derives and
executes every check. The engine, the check model, the compilers and the
exporters were deleted (commit `2447e69`).

What stays here is only what neither ODD nor datacontract-cli does, and the
README lists it: the time series, the score, the window, the failing rows, the
push to ODD, and rule authoring.

## Consequences

* `datacontract-cli` is a **runtime dependency**, not a dev tool. CI caught
  this the hard way when it was missing from `pyproject.toml`.
* Its behaviour is our behaviour. When it changes what it returns, our results
  change. `core/runner.py::persist` is the whole surface where that shows.
* Its check `key` is what we store as `check_id`. It is stable across runs and
  across servers, which is what makes the time series joinable — the per-run
  `id` is a fresh uuid and must never be used for this.
* Anything it cannot do, we do *around* it, never by re-deriving checks.

## On upgrade

Bumping `datacontract-cli`:

1. Run `python core/runner.py --as-of <a day with known failures>` and diff the
   stored `check_id` values against the previous run. **A changed `key` breaks
   every time series silently** — old rows stay, new rows appear beside them.
2. Check `check["type"]` values against `TABLE_SCOPED_TYPES` in
   `core/runner.py` and against `BY_TYPE` in `core/sample.py`.
3. Check that `diagnostics` still carries `failed_rows` / `row_count`, and that
   `implementation` still holds the compiled SQL — `core/sample.py` rewrites it.
4. `general` is their run-level rollup, not a check. See ADR 0003.

Adding a check kind is *their* problem, not ours. Open an issue there.
