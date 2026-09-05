# What to adopt, what to avoid, what is left to write

Every line we write is a line nobody else maintains. This is the survey behind
that rule: what the ODD organisation actually ships, what the wider ecosystem
covers, and which pieces are healthy enough to depend on.

Health figures are stars, last push and licence, read on 2026-09-05.

## The ODD organisation is two live repositories and a graveyard

| repository | health | verdict |
|---|---|---|
| `odd-platform` | 1427★ · pushed today · Apache-2.0 | **adopt** — the catalog itself |
| `odd-collectors` (monorepo: collector + SDK + AWS/Azure/GCP) | pushed 2026-01 | **adopt** — the live home for adapters |
| `opendatadiscovery-specification` | 151★ · 2026-06 · Apache-2.0 | **adopt** — the ingestion contract |
| `odd-dbt` | 2★ · 2024-06 | avoid — dead bridge |
| `odd-great-expectations` | 3★ · 2024-09 | avoid — dead bridge |
| `odd-cli` | 1★ · 2024-06 | avoid |
| `charts` (Helm) | 10★ · 2024-02 | **there is no maintained Helm chart.** Deployment is compose |
| `odd-collector-profiler` | 4★ · 2024-04 | works, but stale and a 4.4 GB image |
| `odd-spark-adapter`, `odd-airflow-2`, `odd-collector-aws-stack`, … | 2021–2024 | archived in all but name |

Two of the dead ones are **our own dependencies**, and that is the sharpest
risk in this stack:

| we import | health |
|---|---|
| `oddrn-generator` | **5★ · 2024-07** |
| `odd-models` | **3★ · 2024-10** |

`integrations/odd/mapper.py` is built on both. They are small, pure-Pydantic and
pinned to a spec that is still maintained, so this is a slow risk rather than an
urgent one — but it is the thing to watch, not odd-platform.

Also worth knowing before committing: `odd-team`, a repository describing an
"AI maintainer team … coordinating audit and gap-closing across ODD
repositories", is active. The commit stream is real; the maintenance model is
unusual.

## The collectors are the reason to be here

One monorepo, one config file per source, 64 MB of memory:

**Databases** postgresql · mysql · mssql · oracle · snowflake · redshift ·
clickhouse · databricks · trino · presto · hive · duckdb · mongodb · cassandra ·
scylladb · neo4j · elasticsearch · opensearch · vertica · singlestore ·
cockroachdb · couchbase · tarantool · sqlite · odbc
**BI** **superset** · metabase · tableau · redash · mode · cubejs
**Pipelines** dbt · airbyte · fivetran · kafka
**ML** mlflow · kubeflow · feast · sagemaker
**AWS** athena · glue · s3 · dynamodb · kinesis · quicksight · dms · sqs

### The Superset chain, verified in the source

`odd-collector/adapters/superset/mappers/` maps a dashboard and a chart to
`DataEntity(type=DASHBOARD, data_consumer=DataConsumer(inputs=[…]))`, and
`datasets.py` resolves the dataset's backing table through
`ExternalDbGenerator` — an ODDRN in the *source database's* namespace, not
Superset's. So the chain is real and complete:

```
postgres table ─> superset dataset ─> chart ─> dashboard
```

Our contract's checks attach to the same table ODDRN. A failing check therefore
has downstream dashboards, and "which dashboards does this break" is a config
file, not a feature to build.

**One trap.** That only works if `ExternalDbGenerator` mints the same host
segment we do. It is the same string-matching problem that forked our catalog
in two before `ODD_PG_HOST` existed — worth checking on the first Superset pull
rather than after a month of split entities.

### What the dbt adapter is, and is not

It reads dbt's `catalog.json` over HTTP and maps **models as catalog entities**.
It does not carry test results. Bringing dbt test outcomes into ODD is what
`odd-dbt` was for, and `odd-dbt` is dead.

## Outside the organisation

| project | health | what it gives |
|---|---|---|
| **datacontract-cli** | 1058★ · pushed today · MIT | contract → checks → executed in the source database, results as JSON with `failed_rows` / `row_count`, 26 export formats including `dbt-models` and `great-expectations` |
| **ODCS** (Bitol / Linux Foundation) | 1117★ · 2026-09 · Apache-2.0 | the contract format itself, vendor-neutral |
| `ibis` + `sqlglot` (datacontract-cli's engine) | 6654★ / 9586★ · both pushed today | the checks are compiled to SQL and run in the source database — no engine to operate |
| **elementary** | 2406★ · 2026-09 · Apache-2.0 | dbt-native: test history in the warehouse, anomaly detection, a report UI, alerting |
| dbt-core | 13783★ · Apache-2.0 | the transformation layer elementary requires |
| Great Expectations | 11770★ · Apache-2.0 | a target we can export to; heavy to operate as a runtime |
| Soda Core | 2421★ · **licence NOASSERTION** | avoid. The open-source part verifies contracts; observability needs Soda Cloud and an agent |

A note on "is it built on dbt or GX". datacontract-cli is **not** — its runtime
is ibis and sqlglot, and it exports *to* dbt and GX rather than running them.
That is the lighter arrangement: nothing to deploy, checks execute inside
PostgreSQL, and the dbt/GX exports remain available for anyone who wants to run
them in their own stack.

## Two stacks, and the question that picks between them

Everything turns on **whether there is a dbt project**.

### A — no dbt (recommended for an ERP replica)

```
odd-platform + odd-collector      catalog, search, glossary, alerts,
                                  Superset/Metabase impact analysis
datacontract-cli + ODCS           contract → checks → run in Postgres
this repository                   results as a time series, severity-weighted
                                  score, incremental window, push to ODD,
                                  the screen an analyst authors rules in
```

~1.2 GiB idle, four services, one compose file.

### B — with dbt

Add `dbt-core` and `elementary`. Elementary then owns **three of the four things
this repository exists for**: test history, a trend UI, and alerting — and adds
anomaly detection we do not have. What would remain here is the contract, the
ODD push, and the rule-authoring screen.

The catch: dbt is a transformation tool. Adopting it to obtain elementary means
running a transformation layer over an ERP replica that may not need one. That
is a bigger commitment than the gap it closes, so **A unless a dbt project
already exists** — and if one appears later, B is a strict improvement and the
contract survives the move, because `datacontract export dbt-models` is one
command.

## What is left for us in either case

1. Results as a time series — nothing above stores a run under a date except
   elementary, and elementary needs dbt.
2. Severity-weighted score and SLA over time.
3. The incremental window. datacontract-cli's `--filter` is exactly this and is
   **broken in 1.1.3**, the current PyPI release: it emits a nameless
   `DROP VIEW IF EXISTS` and every filtered check errors. Worth reporting
   upstream; until then the window is ours by accident.
4. The push to ODD.
5. Rule authoring. ODD's UI has no "create test" anywhere in it, and
   datacontract-cli is a CLI.

Items 1–4 are roughly 500 lines. Item 5 is the single HTML file. Everything
else in this repository — the contract model, the derivation, the dbt and GX
compilers — has a maintained equivalent and should eventually go.
