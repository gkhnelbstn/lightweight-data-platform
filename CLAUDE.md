# Working in this repo

Contract-driven data quality. Read `README.md` first for the why; this file is
the operating manual.

## Commands

```bash
pip install -e ".[dev]"
pytest -q                                                  # 64 tests, no database needed
python seed/seed.py                                        # rebuild the demo ERP data
python core/runner.py --backfill-days 44 --emit-artifacts  # rebuild history + artifacts
python core/runner.py                                      # the daily unit (today)
python integrations/odd/push.py --out artifacts/odd        # build + validate ODD payloads
uvicorn api.main:app --port 8077                           # UI + API
python core/sync.py --check                                # validate the sync rules
python core/sync.py --apply                                # publication + subscription
python core/sync_mssql.py --interval 30                    # SQL Server CDC -> Postgres
```

Both databases come from the environment; nothing hardcodes a DSN:

```bash
export ERP_DSN=postgresql://postgres:postgres@localhost:5432/erp   # the data under test
export DQ_DSN=postgresql://postgres:postgres@localhost:5432/dq     # contracts, checks, results
export DQ_HOST=dq.local                                            # ODDRN identity, keep stable
```

## Invariants — break these and the design stops making sense

1. **The contract is the only source of truth.** Checks are derived in
   `core/checks.py`, never hand-written and never stored as the primary record.
   Anything that edits rules (including the UI) edits the contract file.
2. **Checks stay engine-neutral.** `Check` knows nothing about SQL. Anything
   engine-specific belongs in `core/compilers/`.
3. **Every compiled statement returns exactly `failed_rows, total_rows`.** The
   runner and the score depend on it.
4. **Daily runs are incremental** (`loaded_at = as_of`). Cumulative scoring makes
   incidents invisible — this was measured, see README. Freshness is the one
   check that is deliberately never windowed.
5. **dbt and Great Expectations are output formats, not dependencies.** Do not
   add them to `dependencies`; do not import them at runtime.
6. **No new infrastructure without a row count to justify it.** PostgreSQL is
   the whole stack.

## Gotchas already paid for

* `window` is a reserved word in PostgreSQL — the column is `run_window`.
* ODDRNs must be built from the full check id, not its last dotted segment;
  `customer_id.unique` and `tax_id.unique` both end in `unique` and would merge
  into one catalog entity.
* Results outlive checks. Deleting a rule from a contract leaves its history in
  `check_results`; anything reading results must join `checks` or handle orphans
  (`integrations/odd/push.py` does the former and reports the latter).
* Custom SQL in a contract must scope itself with `{{scope}}` or
  `{{scope:alias}}`, never a hardcoded `loaded_at` predicate, or the window
  switch silently does nothing.
* `psycopg` (v3), not `psycopg2`. `conn.execute(...)` returns a cursor.
* T-SQL has no `search_path`. The window is a schema on Postgres and a
  *database* on SQL Server, because a rule written `dbo.sales_orders` cannot
  see a second schema. `CREATE DATABASE` also refuses to run inside pyodbc's
  implicit transaction — set `autocommit` first.
* `datacontract test` gives `row_count` only for the checks it derives. A
  custom SQL rule has no denominator, so `core/runner.py` counts the table
  once per run; without it `fail_ratio` is always 0 and the volume half of the
  score does nothing.
* Logical replication fails **after** the initial copy, in a background worker
  that only writes to the server log — so a broken sync looks like a working
  one. `core/sync.py` checks all four preconditions up front; do not weaken
  that into a warning. `--status` is how you tell a dead worker from a quiet
  one.
* SQL Server CDC is a *SQL Server Agent* feature. `sp_cdc_enable_table`
  succeeds with the Agent stopped and then nothing ever lands in the change
  table; `MSSQL_AGENT_ENABLED` in compose.yaml is why the demo works.
* A publication's column list is a privacy boundary, not an optimisation: a
  column outside it never reaches the replica. Keep classified columns out.
* `fn_cdc_get_all_changes(..., 'all')` returns operations 1, 2 and 4 — no
  before image. Ask for `'all update old'` or an update that changes an
  identity column silently duplicates the row.
* The `identity` widening in a `syncTo` rule is a *logical replication*
  requirement. The CDC reader has whole rows and does not need it; a mutable
  column in the identity breaks it there.

## Adding things

**A new check kind:** add derivation in `core/checks.py`, then a branch in
`core/compilers/sql.py` (required) and in `dbt.py` / `gx.py` (best effort — an
unmappable kind should be skipped, not faked). Add a case to
`tests/test_derivation.py::test_every_check_compiles_to_two_column_sql`.

**A new contract:** drop a `*.contract.yaml` in `contracts/`. `core/runner.py
register()` picks it up; no code change.

**A new integration:** follow `integrations/odd/` — a mapper that builds the
foreign model plus a push script that validates before sending. Validation
failures belong in our process, not as a 400 from someone else's API.

## Style

Plain functions and dataclasses; pydantic only at the boundaries (contract
files, foreign APIs). Comments explain *why*, especially where a decision looks
arbitrary — the scoring blend and the window choice both have measurements
behind them. Keep modules under ~150 lines; if one grows past that it is usually
two concerns.
