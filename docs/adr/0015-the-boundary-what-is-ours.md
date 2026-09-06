# 0015 — The boundary: what is ours, and where it lives

## Context

Twelve records now describe individual decisions, and each is defensible on its
own. What none of them answers is the question someone asks on arriving: *what
did you actually add, and how much of this would disappear if the upstream
projects grew one feature each?*

That question has a practical edge. Every line here is a line the community
does not maintain for us, so the register of additions is also the register of
liabilities. It should be possible to read it in one page and to check it
against the repository, rather than inferring it from a directory listing where
`core/runner.py` and `deploy/Dockerfile.odd-platform` look like the same kind
of thing.

The same boundary was invisible in the deployment. `compose.yaml` held the
platform *and* a SQL Server, a MongoDB and a Superset, all behind
`--profile demo`. A profile is easy to miss in a 250-line file, and the honest
reading of that file was "this project requires a BI tool and two more
databases" — which is false, and is exactly the impression a project arguing
for a small stack cannot afford to give.

## Decision

**One register, and a file layout that agrees with it.**

Everything in this repository is one of four things. Nothing is a fifth.

### 1. Ours, because nothing upstream does it

| what | where | retired by |
|---|---|---|
| results as a dated time series | `core/store.py` | ODD storing run history with numbers in it — [0004](0004-results-store.md) |
| a dimension-weighted score, and `error` as a third status | `core/scoring.py` | ODD's own score becoming weightable — [0003](0003-scoring.md) |
| the daily window, per engine | `core/runner.py` | `datacontract test --filter`, or ODCS scoping a rule — [0002](0002-the-daily-window.md) |
| the rows a check counted | `core/sample.py` | datacontract returning failed samples for SQL rules — [0006](0006-failing-rows.md) |
| tests and runs on the table's own ODDRN | `integrations/odd/from_datacontract.py`, `mapper.py` | — [0005](0005-push-to-odd.md) |

### 2. Ours, because the contract can say it and nothing reads it yet

| what | where | retired by |
|---|---|---|
| PII detection, written back into the contract | `integrations/odd/classify.py` | ODD growing a classification model — [0007](0007-pii-classification.md) |
| replication rules → Postgres publications | `core/sync.py` | — [0008](0008-replication.md) |
| replication rules → SQL Server CDC | `core/sync_mssql.py` | — [0008](0008-replication.md) |
| declared lineage → `DataTransformer` | `integrations/odd/lineage.py` | the loads moving to dbt — [0014](0014-declared-lineage.md) |
| the catalogue entry, filled from the contract | `integrations/odd/curate.py` | ODD importing ODCS directly — [0013](0013-fill-the-catalogue-from-the-contract.md) |
| the links on the table's page | `integrations/odd/entity_page.py` | — |

### 3. Ours, because a person has to be able to do it without us

| what | where | retired by |
|---|---|---|
| a fixed rule vocabulary, compiled server-side | `core/rules.py` | — [0010](0010-rule-vocabulary-and-the-token.md) |
| the API that authors ODCS, and the token it generates itself | `api/main.py` | a real identity provider in front — [0010](0010-rule-vocabulary-and-the-token.md) |
| the contract panel inside ODD's Data Quality page | `deploy/odd-platform-ui/`, `deploy/Dockerfile.odd-platform`, `deploy/odd-platform-dq-panel.mjs` | ODD growing an extension point — [0009](0009-fork-odd-platform-ui.md) |

### 4. Patches we carry for someone else

`deploy/Dockerfile.odd-collector` and `deploy/superset-mssql-lineage.patch.py`
exist only until the PRs behind them merge. They are listed with their issue
numbers in [0011](0011-carried-patches.md) and are the first thing to try
deleting on any version bump.

**Everything else in the tree is borrowed, configured, or demonstration.** ODD
Platform, odd-collector, the profiler, `datacontract-cli`, ODCS, Postgres. The
seed data, the medallion warehouse, the Superset assets and the SQL Server
schema are demonstration — they exist to make the four categories above
observable, and they are not the product.

**So the demo is a separate compose file.** `compose.yaml` is the platform:
`db`, `app`, `odd-db`, `odd-platform`, `odd-collector`, and the profiler behind
a profile. `compose.demo.yaml` is SQL Server, MongoDB and Superset. They
compose:

```bash
docker compose -f compose.yaml -f compose.demo.yaml --profile demo up -d
```

A consequence that had to be handled rather than argued away: `db-init.sql`
runs once, on an empty volume, and can only create the databases the *platform*
needs. The demo databases — `erp_replica` for the sync rules, `dwh` for the
medallion chain — are therefore created by the scripts that use them, through
`core/bootstrap_db.py`. Asking an operator to run a `CREATE DATABASE` by hand
before a documented command works is how a quick start turns into a support
thread.

## Consequences

* **The register has to be maintained.** A new module that is none of these
  four things is a signal, not a paperwork problem: either it belongs upstream
  or the boundary moved and this record is wrong.
* **The demo is now two commands rather than one flag**, and someone will
  eventually run `docker compose up -d` and wonder where Superset went. The
  header of each file says; the README and `docs/tutorial.md` say.
* **A shrinking register is the goal.** Five of the fourteen rows have a named
  upstream that would delete them. Deleting one is the best available outcome
  and should be treated as progress, not as lost work.
* This record duplicates a line from each of the others. That is deliberate —
  the duplication is one table that can be read in a minute, and it goes stale
  loudly, because a module that no longer exists is visible here.

## On upgrade

* Read this table first and ask, per row, whether the *retired by* column has
  come true. That is a faster pass than reading twelve records.
* `deploy/Dockerfile.odd-collector` and the Superset patch (category 4) are the
  cheapest wins and should be re-checked on every collector release.
* If a bump adds a service, decide which file it belongs in before adding it:
  does the platform need it to work, or does the demo need it to be legible? If
  the answer is "the demo", it goes in `compose.demo.yaml`, and the databases
  it needs go through `core/bootstrap_db.py`, not `deploy/db-init.sql`.
