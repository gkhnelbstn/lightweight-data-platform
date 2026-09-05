# lightweight-data-platform

Contract-driven data quality on top of **OpenDataDiscovery**. The catalog,
search, glossary, alerting and schema discovery are ODD's. The contracts, the
daily run, the score and the trend are here. Two contracts, 23 derived checks,
45 days of history, PostgreSQL.

```
contract.yaml ──> derived checks ──> daily run (as-of date) ──> time series ──> score / SLA
                                            │
                                            └──> ODD Platform: catalog, tests,
                                                 run history, column stats,
                                                 ER relationships, alerts
```

## The position

Every line we write is a line the community does not maintain for us. So the
question this project keeps asking is not "what can we build" but **"what is
still missing after ODD and datacontract-cli have done their part"**.

### Adopt, do not build

| need | what covers it | why not us |
|---|---|---|
| catalog, search, glossary, ownership, RBAC | **ODD Platform** (Apache-2.0, active) | Postgres full-text, no Elasticsearch. Their last 25 commits are all search |
| schema discovery | **odd-collector** | 64 MB, one config file |
| column profiling | **odd-collector-profiler** | string lengths, means, inferred types |
| alert lifecycle | **ODD Platform** | opens on failure, closes itself on the next pass. Measured: 3 open, 10 auto-resolved over a 45-day backfill |
| contract format | **ODCS** (Bitol / Linux Foundation) | adopted — `contracts/*.odcs.yaml` |
| deriving and running the checks | **datacontract-cli** (MIT) | adopted — 27 checks on Postgres, 24 on SQL Server, executed in the source database |
| dbt / Great Expectations / 24 more exports | **datacontract-cli** | `export dbt-models`, `export great-expectations` |
| BI impact — "which dashboards break" | **odd-collector** Superset/Metabase/Tableau adapters | dashboards arrive as `DataConsumer(inputs)` pointing at the source table, so a failing check has downstream dashboards. Config, not code |

`docs/stack-choices.md` has the health figures behind each row, and the two
things to watch: `oddrn-generator` (5★, 2024) and `odd-models` (3★, 2024) are
our own dependencies and the least maintained part of the stack, and there is
no maintained Helm chart, so deployment is compose.

### What is actually still missing

These four are why this repository exists. Nothing above does them. Together
they are about 400 lines: `core/runner.py`, `core/store.py`,
`core/scoring.py`, `integrations/odd/`.

1. **Results as a time series.** `datacontract test` runs and forgets: no
   as-of date, no storage, no trend. Here every run is stored under its date in
   a monthly-partitioned table, so quality has a history that can be charted
   and an SLA that can be breached.
2. **A weighted score.** ODD divides passing tests by total tests and calls it
   a score; a missing key and a cosmetic rule cost the same, and one bad row
   weighs as much as four thousand.
   `0.6 * weighted pass/fail + 0.4 * weighted (1 - fail_ratio)`, weighted by
   the ODCS quality *dimension* — completeness, uniqueness, consistency and
   timeliness break joins, so they cost more than conformity.
3. **The daily window.** A schema of views over one day's arrivals, addressed
   through a second `servers` entry in the contract. datacontract-cli's
   `--filter` is meant to be this and is **broken in 1.1.3** — a nameless
   `DROP VIEW IF EXISTS` — and the ibis API under it, `Table.alias`, is
   documented by ibis as not public and due for removal. Views are standard
   SQL and standard ODCS, with nothing to patch.
4. **The push to ODD.** Turning `datacontract test` output into
   `DataQualityTest` / `DataQualityTestRun` on the *table's* ODDRN, so a
   failing check inherits the dashboards downstream of it.

Plus the one interface neither has: **an analyst can author a rule.** ODD's UI
annotates what was ingested — there is no "create test" anywhere in it — and
datacontract-cli is a CLI. `web/index.html` writes SQL, shows the last 14 days
it would have failed on, and saves the rule back into the contract file.

![The contract UI](docs/contract-ui.png)

## Quick start

Everything is one compose file.

```bash
docker compose up -d db odd-db odd-platform     # wait for ODD to come up
./deploy/odd-bootstrap.sh                       # collector tokens + configs
docker compose up -d                            # + collector + app

docker compose exec app python seed/seed.py                      # 45 days of ERP-ish data
docker compose exec app python core/runner.py --backfill-days 44
docker compose exec app python integrations/odd/push.py \
    --url http://odd-platform:8080 --no-datasets
```

* contract UI — http://localhost:8077
* ODD — http://localhost:8080

Column profiling is opt-in, because the image is 4.4 GB and idles at ~450 MB:

```bash
docker compose --profile profiling up -d
```

Daily operation is one line in cron:

```bash
docker compose exec -T app python core/runner.py --odd-url http://odd-platform:8080
```

It rebuilds the day's views, runs every `contracts/*.odcs.yaml` through
`datacontract test` against its own server type, stores the results as a time
series, scores them, and sends the runs to ODD attached to the table.

**Sizing.** Measured idle: ODD 924 MB (627 MB when capped at 1 GB, and it still
starts), its Postgres 147 MB, collector 64 MB, profiler 451 MB. **4 vCPU /
8 GiB / 100 GB is the target box.** ODD's database was 12 MB for 2 tables, 23
checks and 45 days of runs — it stores metadata, so the size of the source data
does not enter into it.

## Layout

| path | what it is |
|---|---|
| `contracts/*.odcs.yaml` | the contracts, in the Open Data Contract Standard |
| `core/runner.py` | build the day's views, run `datacontract test`, persist, score, push |
| `core/scoring.py` | dimension-weighted score |
| `core/store.py` | DDL, monthly partitions, writes |
| `api/main.py` | read API + analyst rule authoring, writing ODCS |
| `web/index.html` | single-file UI, no build step |
| `integrations/odd/` | ODDRN vocabulary and the datacontract → ODD bridge |
| `deploy/Dockerfile.odd-collector` | odd-collector plus two fixes to its Superset adapter |
| `compose.yaml`, `Dockerfile`, `deploy/` | the stack and its runbook |
| `docs/odd-gap-analysis.md` | what ODD does and does not do, verified against a running instance |
| `docs/stack-choices.md` | which projects to depend on, with their health figures |

## Three things this established

**1. Cumulative scoring is a ratchet, and that is why the window exists.**
Re-measured on the current engine, 45 days, same data, same checks, only the
predicate on the views changed:

| window | days the score improved | days it worsened | biggest improvement |
|---|---|---|---|
| `loaded_at <= as_of` | **1** | 3 | 0.0297 |
| `loaded_at = as_of` | **17** | 10 | 0.0895 |

Scoring the whole history means a defect that appears once is counted forever:
the series steps down and stays there, recovering once in 44 transitions. The
daily window shows the drop *and* the recovery. The earlier phrasing of this
finding — "a flat line that never breaches SLA" — was measured on a different
seed and overstated it; the mechanism is the ratchet, not the flatness.

**2. A pure row-ratio score is useless; so is a pure pass/fail score.**
Ratio alone reads one bad row in a million as 0.999999. Binary alone cannot
tell a typo from an outage.

**3. ODD holds more numbers than its run model suggests.** `DataQualityTestRun`
has no numeric field, so per-run counts travel as text in `status_reason` — but
`DataSet.rows_number` and `POST /ingestion/entities/datasets/stats` take row
counts and per-column `nulls_count` / `unique_count` / min / max as structured
values that the UI renders. `/ingestion/metrics` looks like the answer and is
not: it returns 201 and stores nothing, and its epic has been open since 2022.

## Operating notes that cost time to find

* **`ODD_PG_HOST` must match what odd-collector calls the database.** A dataset
  ODDRN is matched by string; a mismatch forks every table into two catalog
  objects, the collector's with the schema and ours with the tests. Inside this
  compose both are `db`, which is most of the reason it is one compose.
* **Push with `--no-datasets` when a collector runs.** ODD versions a dataset
  whenever its structure changes and the two writers never agree — the contract
  governs 6 columns of 7, the collector says `int8` where `information_schema`
  says `bigint`. Left alone they mint a schema revision every pull.
* **A collector cannot register itself.** `POST /ingestion/datasources` is
  guarded by a filter that is always on, so odd-collector dies at startup until
  it is handed a token minted through `POST /api/collectors`.
  `deploy/odd-bootstrap.sh` does that and writes both configs — the collector
  and the profiler spell the same database differently (`postgresql` vs
  `postgres`, `user` vs `username`) and each mistake is an opaque crash.

## Status and limitations

Early. A working vertical slice, not a product.

* **`unique` is still windowed.** Every check now runs against the day's view,
  including uniqueness, so a duplicate that arrives on a later day than its
  original is not seen. The old engine had the same hole; adopting ODCS did not
  close it, it moved it. The fix is a per-rule choice of window, which ODCS has
  no field for.
* **No CDC.** `loaded_at = as_of` is a watermark: it sees inserts, not updates
  or deletes.
* **One data source.** The DSN comes from the environment, not the contract.
* **Custom SQL is executed as written.** No read-only role, no statement
  timeout, no AST check. `/api/rules/preview` is validation, not a sandbox.
* **Neither UI has authentication.** Anyone who reaches 8077 can run SQL
  against the source; anyone who reaches 8080 can inject alerts into ODD
  (issue #1740). Private network only.
* **ODD's ERD relationships are write-only on 0.29.0.** They store correctly
  and 500 on read — `Collectors.toMap` over versioned `dataset_field` rows.

## Where this goes next

The honest direction is to keep shrinking the part we maintain:

1. **Adopt ODCS as the contract format** and let `datacontract test` derive and
   execute the checks. It already returns `failed_rows` and `row_count` per
   check as structured JSON — the same two numbers `core/compilers/sql.py`
   exists to produce. That would retire our contract model, our derivation and
   both artifact compilers, and hand us 24 export formats we never wrote.
2. **Keep** the results store, the score, the window and the ODD push. Those
   four are the whole remaining product.
3. **Authentication** before anyone else uses it.

## License

MIT.
