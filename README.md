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

These five are why this repository exists. Nothing above does them. Together
they are about 470 lines: `core/runner.py`, `core/store.py`,
`core/scoring.py`, `core/sample.py`, `integrations/odd/`.

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
5. **The failing rows.** "522 orders disagree with their lines" is where an
   investigation starts and none of them end. Neither tool answers *which*:
   `datacontract test` reports counts, and ODD's run model has no numeric
   field at all, let alone a row. `core/sample.py` takes the statement the
   check actually ran — datacontract hands it back in `implementation` — and
   rewrites it through sqlglot into the rows it counted: keep the FROM and the
   WHERE, drop the aggregate, add a limit. Because the rewrite goes through a
   parse tree rather than string surgery, emitting it back as T-SQL turns the
   `LIMIT` into a `TOP`, so the same code samples SQL Server.

   It is sampled in the same window the result was measured in, or the count
   and the rows under it disagree — and columns the contract marks
   `classification:` are masked, which is what `integrations/odd/classify.py`
   writes back once it has found them.

Plus the one interface neither has: **an analyst can author a rule.** ODD's UI
annotates what was ingested — there is no "create test" anywhere in it — and
datacontract-cli is a CLI. `web/index.html` writes SQL, compiles it against
the source without saving, shows what it would have caught, and then saves the
rule back into the contract file.

![The contract UI](docs/contract-ui.png)

### Keeping a second database in step

A contract can also say where its table is replicated to, and under what rule:

```yaml
customProperties:
  - property: syncTo
    value:
      server: replica                 # a servers[] entry
      filter: "country = 'TR'"
      identity: [customer_id, country]
      columns: [customer_id, name, country, segment]
```

`core/sync.py` turns that into a Postgres publication and subscription. **No
replication engine is written here** -- Postgres has logical decoding and since
15 a publication carries a row filter and a column list, which is precisely
"the rules that decide what is synced". Nothing of ours sits in the stream.

What is ours is refusing to create objects that would not work, because logical
replication fails *silently*: the initial copy succeeds, the rows land, and
every subsequent change then dies in a background worker that writes only to
the server log. It looks synced and is not. Four rules, all found on a running
pair and all checked by `python core/sync.py --check` before anything is
created:

1. The source needs a replica identity -- a unique index over NOT NULL columns.
   The contract already names it (`primaryKey`), **so a table whose uniqueness
   check is failing cannot be replicated safely.** That is not a coincidence;
   it is the same fact twice.
2. Every column in the row filter must be inside the replica identity, since an
   update is matched against the old row and the old row is only those columns.
   A rule may widen the identity to say so; it may never narrow it.
3. The column list must cover the replica identity.
4. The target needs the same replica identity. This is the silent one.

`--status` exists for the same reason: it reports whether the apply worker is
actually running and how far behind the slot is, rather than letting a dead
worker look like a quiet one.

The column list doubles as a privacy control. `tax_id` is classified in the
contract, is left out of the list, and so **does not exist in the replica at
all** -- masking a column in the UI is no use if the whole column was copied
into another database.

For SQL Server the mechanism is CDC rather than logical decoding.
`deploy/mssql-cdc.sql` turns it on -- and it is the *Agent*, not the T-SQL,
that people forget: `sp_cdc_enable_table` returns success with the Agent
stopped and then nothing is ever captured.

There is no native SQL Server to Postgres path, so `core/sync_mssql.py` is the
one loop in this repository. It is still only a read -- `cdc.fn_cdc_get_all_
changes_<instance>` is an ordinary function taking two LSNs -- and it adds no
infrastructure, which is the sixth invariant's actual test: no Debezium, no
Kafka, no connector runtime, one table scan from a stored watermark. Three
things it does that a naive poller does not:

* **A row that leaves the filter is deleted, not skipped.** Cancel an order
  under `status <> 'CANCELLED'` and filtering the stream would simply not see
  it, leaving the row in the target for ever. Nothing is filtered out of the
  stream; the filter decides upsert *or delete*.
* **It asks for the before image.** `fn_cdc_get_all_changes(..., 'all')`
  returns operations 1, 2 and 4 only -- measured, not assumed. Without
  `'all update old'` an update that changes an identity column cannot be
  applied to the right row, and the order exists under both names.
* **It takes an initial snapshot.** CDC records changes from the moment it was
  enabled, so the first pass copies the table as it stands. The max LSN is read
  *before* the snapshot, so anything changing during it is replayed rather than
  lost -- upserts and deletes are both idempotent, so replaying costs nothing.

The same `syncTo` rule describes both engines, but it does not mean quite the
same thing in each, and assuming it did produced a wrong answer: the widened
`identity` exists to satisfy logical replication, which matches an update
against the replica identity alone. The CDC reader has whole rows and does not
need it -- and putting a mutable column in the identity there actively broke
it, because cancelling an order changed its key.

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
| `core/sample.py` | rewrite a check's SQL into the rows it counted |
| `core/sync.py` | derive a Postgres publication/subscription from the contract |
| `core/sync_mssql.py` | apply SQL Server's CDC change table to a Postgres target |
| `api/main.py` | read API + analyst rule authoring, writing ODCS |
| `web/index.html` | single-file UI, no build step |
| `integrations/odd/` | ODDRN vocabulary, the datacontract → ODD bridge, PII classification |
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

* **No CDC.** `loaded_at` is a watermark: it sees inserts. A row corrected in
  place keeps its original watermark, and a deleted row leaves nothing behind.
  A contract whose table is updated in place can widen its own window —

  ```yaml
  customProperties:
    - property: windowPredicate
      value: "{col} = {day} or updated_at::date = {day}"
  ```

  — and a source with real CDC points its contract at the change table and
  windows on the change timestamp. SQL Server has that built in
  (`sys.sp_cdc_enable_table`) and it needs no new infrastructure. Deletes on a
  source with neither are genuinely invisible, and no amount of contract says
  otherwise.
* **Custom SQL is executed as written.** The checks connect as `dq_reader` --
  `SELECT` only, `default_transaction_read_only`, a 60s `statement_timeout` --
  so a rule cannot write or hang. That is a smaller blast radius, not a
  sandbox: it can still read every column it is granted and cost a table scan.
* **Only the write routes are authenticated.** `/api/rules` and
  `/api/rules/preview` compile and run a person's SQL, so they require
  `DQ_API_TOKEN` and refuse when it is unset. Reads are open, and ODD's
  `/ingestion/**` is open by its own design (issue #1740). Private network.
* **No column-level lineage.** ODD's ingestion model has none — `DataTransformer`
  is dataset-level — and the issues that would add it have been open since 2022.
  Table-level lineage works, including the BI chain, and the contract's foreign
  keys are published as column-level ERD relationships, but that is a different
  thing from "which column feeds which".
* **ODD's ERD relationships are write-only when two sources describe a column
  differently.** Reported with a reproduction as
  [odd-platform#1880](https://github.com/opendatadiscovery/odd-platform/issues/1880).
* **One upstream fix is carried as a patch**, and it is now a pull request:
  [odd-collectors#136](https://github.com/opendatadiscovery/odd-collectors/pull/136).
  When it merges, `deploy/Dockerfile.odd-collector` should be deleted rather
  than maintained.

### Closed, and how

* **`unique` scoped to a single day** meant a duplicate arriving later than its
  original was never seen -- the check passed for 45 days on a table holding 8
  duplicate primary keys. Table-level invariants now re-run against the real
  tables and their results replace the windowed ones: `field_unique` reports
  `16/3376` where it used to report `0/70`. The proper fix is a per-rule scope
  in the contract, raised as
  [datacontract-cli#1593](https://github.com/datacontract/datacontract-cli/issues/1593).
* **One data source.** Contracts now carry their own `servers` block, so the
  same runner checks PostgreSQL and SQL Server in one pass, each through its
  own dialect.
* **A broken check looked like broken data.** A check that errors is stored as
  `status = 'error'`, not as a failure with `1/1` rows.
* **No PII classification.** `integrations/odd/classify.py` samples each column
  and tags it in ODD — `pii:TR_TCKN`, `pii:EMAIL_ADDRESS` — as first-class,
  searchable tags. The recognisers are Microsoft's **Presidio** (MIT, ~10k
  stars) rather than patterns of our own; the Turkish identifiers are ours
  because Presidio has none, and they validate the checksum rather than
  matching eleven digits. It costs ~265 MB in the image, which is the small
  spaCy model rather than the 425 MB default — a column of identifiers is not
  free text, so the NLP half earns very little here.

### Reported upstream

| what | where |
|---|---|
| ERD relationships unreadable when a column has two `dataset_field` rows | [odd-platform#1880](https://github.com/opendatadiscovery/odd-platform/issues/1880) |
| the Superset adapter fixes, as a PR | [odd-collectors#136](https://github.com/opendatadiscovery/odd-collectors/pull/136) |
| `Table.sql()` on postgres emits a nameless `DROP VIEW` (sqlglot 30 renamed `Drop.this`) | [ibis#12108](https://github.com/ibis-project/ibis/issues/12108) |
| `datacontract test --filter` unusable on postgres because of the above | [datacontract-cli#1592](https://github.com/datacontract/datacontract-cli/issues/1592) |
| a row filter is all-or-nothing; uniqueness needs to opt out | [datacontract-cli#1593](https://github.com/datacontract/datacontract-cli/issues/1593) |
| odd-collector's Superset adapter: int ids, and lineage only for postgresql/sqlite | [odd-collectors#135](https://github.com/opendatadiscovery/odd-collectors/issues/135) |

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
