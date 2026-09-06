# Working in this repo

Contract-driven data quality. Read `README.md` first for the why; this file is
the operating manual.

**Before changing anything that touches a dependency version, read
`docs/adr/`.** Every decision here has a record with an *On upgrade* section
saying what to check and what would let the decision be deleted. Several exist
only because an upstream project has a gap; closing one of those by deleting
our code is the best outcome available.

## Commands

```bash
pip install -e ".[dev]"
pytest -q                                                  # 107 tests, no database needed
python seed/seed.py                                        # rebuild the demo ERP data
python core/runner.py --backfill-days 44                   # rebuild the history
python core/runner.py                                      # the daily unit (today)
python core/runner.py --odd-url http://odd-platform:8080   # ...and send it to ODD
python integrations/odd/classify.py --url http://odd-platform:8080   # PII tags
python integrations/odd/curate.py --url http://odd-platform:8080  # owner, docs, glossary
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

1. **The contract is the only source of truth.** Not only the checks: the
   window predicate, the PII classification and the replication rules are all
   ODCS in `contracts/*.odcs.yaml`. Anything that edits rules — the UI
   included — edits the contract file. Never keep a rule as the primary record
   anywhere else.
2. **We do not derive or compile checks.** `datacontract test` does, for every
   engine it supports. Deriving them here again is how the engine deleted in
   2447e69 came to exist. What is ours is the list in the README: the time
   series, the score, the window, the failing rows, the ODD push.
3. **Everything engine-specific is named as such.** `core/sync.py` is logical
   replication; `core/sync_mssql.py` is CDC. A rule belonging to one engine
   must not be applied to the other — that mistake has been made twice, in
   `problems()` and in the `identity` widening.
4. **Daily runs are incremental.** Cumulative scoring makes incidents
   invisible; this was measured, see README. The predicate is the contract's to
   state (`windowPredicate`), and every contract must be windowable —
   implementing the window for Postgres only quietly made the SQL Server
   contract cumulative for forty-five days.
5. **A check that could not run is not a check that failed.** An unreachable
   source is an engineering problem: it stays out of the score and breaks the
   SLA instead. `datacontract`'s `general` rollup is not a measurement.
6. **No new infrastructure without a row count to justify it.** PostgreSQL is
   the whole stack, and replication is the database's own rather than a
   connector runtime.

## Gotchas already paid for

* `window` is a reserved word in PostgreSQL — the column is `run_window`.
* ODDRNs must be built from the full check id, not its last dotted segment;
  `customer_id.unique` and `tax_id.unique` both end in `unique` and would merge
  into one catalog entity.
* Results outlive checks. Deleting a rule from a contract leaves its history
  in `check_results`; anything reading results has to handle orphans. Within a
  single day `write_results` replaces rather than merges, or a check that
  errored once shows as an open failure for ever.
* Custom SQL in a contract must not pin the window itself — no hardcoded
  `loaded_at` predicate — and on Postgres must not qualify its schema, or the
  `asof` views are bypassed. On SQL Server it *must* say `dbo.`, which is why
  the window there is a different database.
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
* ODD's metrics API is write-once per family: the second push of the same
  family, byte-identical, is a 500 (`MetricFamilyPojo.getId()` on null). Its
  published OpenAPI also disagrees with its own models — `metric_points` is a
  list, `timestamp` is epoch seconds — and the Overview card truncates values
  to integers. Do not try to publish the score there until that is fixed.
* The contract panel lives inside ODD's Data Quality page and that is a fork
  of `odd-platform-ui`. Keep it the smallest fork that works: the SPA is one
  jar on the platform's classpath, so only the UI is rebuilt and the backend is
  untouched. The patch is two anchors in `DataQualityContent.tsx` and it
  **fails the build** when they move — do not soften that into a warning, and
  do not vendor their file.
* ODD reports an existing collector's token **masked**, so it cannot be read
  back. `odd-bootstrap.sh` reuses the token from the config it wrote last time
  and rotates only when there is no local copy — creating a collector whose
  name is taken returns no token and fails silently.
* odd-collector's mssql adapter has no schema filter: it catalogues every
  table the connected user can see. That is why it connects as `odd_collector`
  rather than `sa` — the permission grant in `deploy/mssql-cdc.sql` is the
  filter, and without it CDC's nine bookkeeping tables outnumber the five real
  ones.
* ODD wants `{"tag_name_list": [...]}` to tag an entity and `{"tags": [...]}`
  to tag a dataset field. Same platform, same release.
* A catalogue field is filled from the contract, never by hand — see ADR 0013.
  Adding a column means adding its description, and the tests enforce it.
* Entity links are the one native place our pages belong. `POST` appends and
  there is no way to read an entity's links back, so `odd_links` remembers the
  ids and later runs `PUT`.
* A publication's column list is a privacy boundary, not an optimisation: a
  column outside it never reaches the replica. Keep classified columns out.
* `fn_cdc_get_all_changes(..., 'all')` returns operations 1, 2 and 4 — no
  before image. Ask for `'all update old'` or an update that changes an
  identity column silently duplicates the row.
* The `identity` widening in a `syncTo` rule is a *logical replication*
  requirement. The CDC reader has whole rows and does not need it; a mutable
  column in the identity breaks it there.

## Adding things

**A new rule someone can pick from the form:** one entry in `core/rules.py`
(builder, dimension, description, menu label) plus its parameters. The UI reads
the catalogue, so it needs no change. Add a case to `tests/test_rules.py`.

**A new check kind:** it is datacontract-cli's, not ours — open an issue
there. What may need changing here is `core/scoring.py` (a dimension it does
not weight), `integrations/odd/from_datacontract.py` (the ODD expectation
category) and `core/sample.py` (how to show the rows it failed on). Add the
case to `tests/test_sample.py`.

**A new contract:** drop a `*.odcs.yaml` in `contracts/`. `load_contracts()`
picks it up; no code change. A Postgres source needs an `erp_daily` server
pointing at a different schema; a SQL Server one, a different database.
`tests/test_contracts.py` enforces both.

**A new source engine:** the `servers` type is datacontract's problem. Ours are
the window (`core/runner.py`), the failing-rows sampler (`core/sample.py`) and
the ODDRN generator (`integrations/odd/from_datacontract.py`). Replication is
worth adding only if the engine already has its own — do not write one.

**A new integration:** follow `integrations/odd/` — a mapper that builds the
foreign model, validated against the vendor's own models before sending.
Validation failures belong in our process, not as a 400 from someone else's
API.

## Style

Plain functions and dataclasses; pydantic only at the boundaries (contract
files, foreign APIs). Comments explain *why*, especially where a decision looks
arbitrary — the scoring blend and the window choice both have measurements
behind them. Keep modules under ~150 lines; if one grows past that it is usually
two concerns.
