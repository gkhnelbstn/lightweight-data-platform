# lightweight-data-platform

Contract-driven data quality for people whose data does not justify a data
platform. Two contracts, 23 derived checks, 45 days of history, one PostgreSQL
database. No Elasticsearch, no Kafka, no Airflow, no TimescaleDB.

```
contract.yaml  ->  derived checks  ->  daily run (as-of date)  ->  time series  ->  score / SLA
                        |
                        +--> dbt schema.yml   (emitted, for teams that run dbt)
                        +--> GX suite JSON    (emitted, for teams that run Great Expectations)
                        +--> ODD payloads     (pushed, for catalog / lineage / glossary)
```

## Why

Every open-source catalog that covers catalog + quality + lineage + glossary
carries infrastructure sized for a company that has a data platform team.
OpenMetadata's documented production minimum is a search cluster (2 vCPU / 8 GiB
per node, three nodes), a database (4 vCPU / 16 GiB) and an Airflow (4 vCPU /
16 GiB). For a 25 GB ERP replica that is not a trade-off, it is a joke.

The tools that *are* light — dbt tests, Great Expectations — have the opposite
problem: they tell you what is broken today and forget it tomorrow, they have no
scheduler, and a business analyst cannot read them.

This project takes a different starting point: **the data contract is the source
of truth, and everything else is derived from it.**

## What is actually different here

1. **Nobody writes a test.** `required: true` becomes a not_null check,
   `allowed: [...]` becomes accepted_values, `references:` becomes a
   relationship check, `min`/`max` become a range check. Business rules live in
   the same file as SQL. Change the contract and the tests change with it.
2. **The same contract compiles to other engines.** dbt `schema.yml` and a
   Great Expectations suite are generated from it (~60 lines each). A team that
   already runs dbt can take the contract and run it in their own stack — which
   is the honest answer to "aren't we locked into your tool?"
3. **Results are a time series, not a status light.** Every run is stored
   as-of a date, so quality has a trend, a severity-weighted score, and an SLA
   that can actually be breached.
4. **An analyst can add a rule from the UI** — write SQL, see the last 14 days
   it would have failed on, save. Saving writes the rule back into the contract
   file. The contract stays the single source of truth; the UI is an editor for
   it, not a second store.

## Quick start

Needs PostgreSQL 14+ and Python 3.10+.

```bash
pip install -e ".[dev]"

export ERP_DSN=postgresql://postgres:postgres@localhost:5432/erp
export DQ_DSN=postgresql://postgres:postgres@localhost:5432/dq
createdb erp && createdb dq

python seed/seed.py                                    # 45 days of ERP-ish data
python core/runner.py --backfill-days 44 --emit-artifacts
uvicorn api.main:app --port 8077                       # http://localhost:8077
```

Daily operation is two cron lines:

```bash
python core/runner.py                                        # today's run
python integrations/odd/push.py --url http://localhost:8080  # only if ODD is used
```

## Layout

| path | what it is |
|---|---|
| `contracts/*.contract.yaml` | the contracts — schema, quality rules, SLA |
| `core/contract.py` | contract model (pydantic) |
| `core/checks.py` | contract -> canonical, engine-neutral checks |
| `core/compilers/sql.py` | check -> PostgreSQL (`failed_rows`, `total_rows`) |
| `core/compilers/dbt.py` | check -> dbt `schema.yml` |
| `core/compilers/gx.py` | check -> Great Expectations suite JSON |
| `core/runner.py` | register, compile, execute as-of a date, persist |
| `core/scoring.py` | severity-weighted score (incident + volume terms) |
| `core/store.py` | DDL, monthly partitions, writes |
| `api/main.py` | read API + analyst rule authoring |
| `web/index.html` | single-file UI, no build step |
| `integrations/odd/` | push to ODD Platform (catalog, lineage, glossary) |
| `deploy/` | ODD compose file and its runbook |
| `docs/odd-gap-analysis.md` | what ODD models natively and what it does not |

## Three things this actually established

**1. The scoring window decides whether the dashboard is useful.**
Scoring cumulatively (`loaded_at <= as_of`) produced a flat line: 0.9993 ->
0.9936 across two real incidents, never breaching SLA, because every new bad row
is diluted by the whole history. Scoring the daily increment
(`loaded_at = as_of`) turns the same incidents into visible drops — 0.989
baseline, 0.87 during the outage, recovery to 0.935. Same data, same checks.
A quality dashboard built on the cumulative window is decorative.

**2. A pure row-ratio score is useless; so is a pure pass/fail score.**
`score = 0.6 * severity-weighted pass/fail + 0.4 * severity-weighted (1 - fail_ratio)`.
Ratio alone reads one bad row in a million as 0.999999. Binary alone cannot tell
a typo from an outage.

**3. Generating engine artifacts is cheap; running the engines is not.**
The dbt and GX compilers are ~60 lines each and remove the lock-in argument
entirely. Executing the checks in-process against PostgreSQL is ~90 lines.
Embedding dbt or GX as a runtime dependency would have been an order of
magnitude more work and more operational surface for no additional signal at
this data volume — especially now that Soda Core has moved to the Elastic
License and both dbt and Great Expectations sit under one vendor.

## ODD Platform integration

`integrations/odd/` maps contracts, derived checks and run history onto the ODD
specification (`DataEntity` / `DataQualityTest` / `DataQualityTestRun`), the same
shape `odd-dbt` produces. Dataset ODDRNs are plain PostgreSQL ODDRNs, so pushed
tests land on the same catalog objects ODD's own collector discovers.

```bash
python integrations/odd/push.py --out artifacts/odd          # build + validate
python integrations/odd/push.py --url http://localhost:8080  # ingest what is new
python integrations/odd/push.py --url ... --since 2026-08-01 # re-send from a date
python integrations/odd/push.py --url ... --all              # re-send everything
```

Ingestion is incremental by default: the platform's own data source is
registered if missing, and only run dates that have not reached that platform
are sent, so the same line covers the first 45-day backfill and the daily cron
next to `core/runner.py`. What was sent is logged in `odd_pushes` per target.
Steady state is 48 entities a night — the catalog and the newest day, both of
which can still change — against 1060 for a full rebuild.

1060 entities validate against `odd-models`. `deploy/RUN-odd-trial.md` is the
runbook. The division of labour it implies: **ODD owns** catalog, lineage,
glossary, ownership, alert lifecycle and search; **this project owns** contracts,
check derivation, artifact emission, scoring window, score and SLA. See
`docs/odd-gap-analysis.md` for why, and for what a run against a real instance
changed: ODD's run model has no row counters and ignores our severity, but it
does compute a score of its own — an unweighted latest-run pass ratio, shown on
the dataset page, next to no trend at all.

## Deliberate omissions

* **No TimescaleDB.** Monthly range partitions plus a BRIN index on `run_at`
  cover this. At ~23 checks x 365 days the results table gains ~8k rows a year.
  TimescaleDB's useful parts (continuous aggregates, retention policies,
  compression) are Community/TSL licensed, so adopting it early trades an
  Elasticsearch-sized dependency for a license restriction.
* **No Elasticsearch.** Catalog search over this many objects is `tsvector` +
  `pg_trgm` territory.
* **No React build.** A single HTML file with inline SVG renders the same screens
  with zero toolchain.

## Status and limitations

Early. This is a working vertical slice, not a product.

* Custom SQL is executed as written. A real deployment needs a read-only role, a
  statement timeout and an AST check — `/api/rules/preview` is validation, not a
  sandbox.
* `{{scope}}` / `{{scope:alias}}` is string substitution, not a parser.
* Lineage, glossary and CDC are not modelled here; the ODD integration is the
  current answer for the first two.
* Alerting, ownership routing and multi-datasource support are not built.

## License

MIT.
