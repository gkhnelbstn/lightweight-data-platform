# Running ODD Platform against this project

The stack: **odd-platform** for the catalog, **odd-collector** to discover the
source schema, **odd-collector-profiler** (optional) for column statistics, and
this project for contracts, checks and their history.

Measured idle, on a machine with RAM to spare: platform 924 MB (627 MB when
capped at 1 GB, and it still starts), its Postgres 147 MB, collector 64 MB,
profiler 451 MB. **4 vCPU / 8 GiB / 100 GB is the target box.** ODD's own
database was 12 MB for 2 tables, 23 checks and 45 days of runs — this is
metadata, so the size of the source data does not enter into it.

## 1. Bring it up

```bash
docker compose -f deploy/odd-compose.yaml up -d odd-db odd-platform
./deploy/odd-bootstrap.sh                      # data source + collector tokens
docker compose -f deploy/odd-compose.yaml up -d
```

`odd-bootstrap.sh` exists because a collector **cannot register itself**:
`POST /ingestion/datasources` is guarded by a filter that is always on,
whatever `auth.ingestion.filter.enabled` says, so odd-collector dies at startup
with a bare 500 until it has a token minted through `POST /api/collectors`.

For column statistics as well — string lengths, means, inferred types — add the
profiler. It is opt-in because the image is 4.4 GB and it idles at ~450 MB:

```bash
docker compose -f deploy/odd-compose.yaml --profile profiling up -d
```

## 2. Have the project's data ready

```bash
export ERP_DSN=postgresql://postgres:postgres@localhost:5442/erp
export DQ_DSN=postgresql://postgres:postgres@localhost:5442/dq
python seed/seed.py
python core/runner.py --backfill-days 44
```

## 3. Push

```bash
export ODD_PG_HOST=ldp-pg                      # what the collector calls the database
python integrations/odd/push.py --out artifacts/odd            # build + validate
python integrations/odd/push.py --url http://localhost:8080 --no-datasets
```

**`ODD_PG_HOST` is not optional when a collector runs.** A dataset ODDRN is
matched by string, and the collector mints the host segment from its bare
`host:` config. Give it anything else — `localhost`, a port — and every table
becomes two catalog objects: the collector's with the schema, ours with the
tests.

**`--no-datasets` is not optional either.** ODD versions a dataset whenever its
structure changes, and the two writers never agree: the contract governs 6
columns of 7, the collector reports `int8` where `information_schema` says
`bigint`, calls every column nullable and finds no primary key. Left to fight,
they mint a schema revision on every pull. With the flag we send tests, runs
and column statistics only; all three address the dataset by ODDRN and never
needed us to declare it.

| payload | endpoint | what it is |
|---|---|---|
| `00_catalog.json` | `/ingestion/entities` | 23 checks + the contract's foreign keys as ERD relationships |
| `01_stats_*.json` | `/ingestion/entities/datasets/stats` | per-column nulls / uniques / min / max |
| `runs_<date>.json` x45 | `/ingestion/entities` | one run per check per day |

## 4. What to look at, in this order

1. **Catalog → sales_orders → Test reports.** All 15 checks, each with its
   45-day history.
2. **Structure → a numeric column.** `Unique 399 | Missing 75 | Min 1 | Max 400`
   — the same counts the checks compute, as numbers rather than text.
3. **A check's own page, not the inline panel.** The panel pages 10 runs at a
   time; the page lists all 45 with `status_reason` as a column.
4. **Severity.** Compare the `Parameters` box (our `critical`) with the
   `Severity` dropdown beside it (ODD's `MAJOR`). Different fields; only the
   second is ODD's.
5. **Alerts.** Filter by `Resolved automatically` as well as `Open` — the
   closing is the interesting half.
6. **Data Quality dashboard.** Counts, no time axis. It cannot show an
   incident day.

## 5. Tear down

```bash
docker compose -f deploy/odd-compose.yaml --profile profiling down -v
```

## Security

`/ingestion/**` is unauthenticated in this compose. odd-platform issue #1740
says it plainly: `auth.ingestion.filter.enabled` defaults to false, and even
when true it covered only `/ingestion/entities` — `/ingestion/alerts`,
`/ingestion/metrics` and `/ingestion/entities/datasets/stats` had no filter at
all, so anyone with network reach could inject alerts or overwrite statistics.
Keep the platform on a private network. This is a trial runbook, not a
production one.
