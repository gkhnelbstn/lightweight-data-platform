# lightweight-data-platform

Contract-driven data quality on top of **OpenDataDiscovery**. The catalog,
search, glossary, alerting and schema discovery are ODD's. The contracts, the
daily run, the score and the trend are here. Eleven contracts, 263 checks a day,
45 days of history, PostgreSQL.

**New here?** [`docs/architecture.md`](docs/architecture.md) is how it works,
in diagrams. [`docs/tutorial.md`](docs/tutorial.md) is eight lessons that each
end in something you can see.

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
| column profiling | **odd-collector-profiler** | string lengths, means, inferred types. The two numbers that belong beside a *check* -- nulls and distincts, in the day's window -- are taken in the pass the runner already makes; the sort-bound half stays theirs |
| alert lifecycle | **ODD Platform** | opens on failure, closes itself on the next pass. Measured: 3 open, 10 auto-resolved over a 45-day backfill. What it does not do is *leave the platform* -- one webhook here does that, below |
| contract format | **ODCS** (Bitol / Linux Foundation) | adopted — `contracts/*.odcs.yaml` |
| deriving and running the checks | **datacontract-cli** (MIT) | adopted — 27 checks on Postgres, 24 on SQL Server, executed in the source database |
| dbt / Great Expectations / 24 more exports | **datacontract-cli** | `export dbt-models`, `export great-expectations` |
| BI impact — "which dashboards break" | **odd-collector** Superset/Metabase/Tableau adapters | dashboards arrive as `DataConsumer(inputs)` pointing at the source table, so a failing check has downstream dashboards. Config, not code |

`docs/stack-choices.md` has the health figures behind each row, and the two
things to watch: `oddrn-generator` (5★, 2024) and `odd-models` (3★, 2024) are
our own dependencies and the least maintained part of the stack, and there is
no maintained Helm chart, so deployment is compose.

### What is actually still missing

These seven are why this repository exists. Nothing above does them. Together
they are about 2,500 lines: `core/runner.py`, `core/store.py`,
`core/scoring.py`, `core/sample.py`, `core/profile.py`, `core/versions.py`,
`core/engines/`, `integrations/odd/`.

1. **Results as a time series.** `datacontract test` runs and forgets: no
   as-of date, no storage, no trend. Here every run is stored under its date in
   a monthly-partitioned table, so quality has a history that can be charted
   and an SLA that can be breached.

   Two numbers per declared column go in beside them -- nulls and distincts,
   taken in the same pass over the same window (`core/profile.py`), so the
   Schema tab reads `tax_id · 44 rows · 4 null (9.1%) · 40 distinct` and says
   when that fraction rose. Neither number is a check: nothing in
   `column_profile` passes, fails, or reaches the score. Quantiles and
   histograms are deliberately absent -- they want a sort over the whole
   table, which is a second daily scan of every table and therefore
   infrastructure.
2. **A weighted score.** ODD divides passing tests by total tests and calls it
   a score; a missing key and a cosmetic rule cost the same, and one bad row
   weighs as much as four thousand.
   `0.6 * weighted pass/fail + 0.4 * weighted (1 - fail_ratio)`, weighted by
   the ODCS quality *dimension* — completeness, uniqueness, consistency and
   timeliness break joins, so they cost more than conformity.

   Both halves have to be real. `datacontract test` reports `row_count` for
   the checks it derives and not for the SQL a person wrote, so every custom
   rule arrived with a denominator of zero and a `fail_ratio` of zero — which
   silently deleted the volume half: a rule failing on one row and the same
   rule failing on seven hundred scored identically. The table is counted once
   per run, in the window the checks ran in.

   Checks that *errored* are excluded from it. An unreachable source used to
   score near zero, which reads as "the data is terrible" when the truth is
   "we could not look" — and the two want different people. The SLA still
   breaks: `sla_met` requires the run to have run, and `checks_errored` is
   stored and shown separately.
3. **The daily window.** A schema of views over one day's arrivals, addressed
   through a second `servers` entry in the contract. On SQL Server it is a
   second *database* instead: T-SQL has no `search_path`, an unqualified name
   resolves through the user's default schema, and the rules in a T-SQL
   contract are written `dbo.sales_orders` anyway — so a second schema is
   invisible to them and only a second database reaches them. Implementing the
   window for Postgres alone had quietly reintroduced exactly the cumulative
   scoring this project argues against: the SQL Server contract was checked
   against its whole table every day and its score did not move for
   forty-five. datacontract-cli's
   `--filter` is meant to be this and is **broken in 1.1.3** — a nameless
   `DROP VIEW IF EXISTS` — and the ibis API under it, `Table.alias`, is
   documented by ibis as not public and due for removal. Views are standard
   SQL and standard ODCS, with nothing to patch.
4. **The push to ODD.** Turning `datacontract test` output into
   `DataQualityTest` / `DataQualityTestRun` on the *table's* ODDRN, so a
   failing check inherits the dashboards downstream of it — and putting a way
   back to these pages on that same entity, as ODD's own *Attachments*. This
   is a plugin for a catalog, not a second catalog, and it should not have to
   be found by knowing a port number.

   The score belongs there too and cannot go: ODD's metrics API takes a family
   once and answers the second write with a `NullPointerException`, so a daily
   value is impossible. Measured rather than read; `integrations/odd/entity_page.py`
   records what a fix would need to know.
5. **A catalogue with something in it.** A collector brings the *shape* of a
   source — tables, columns, types, lineage. Everything a catalogue is actually
   for arrives as "Not created": who owns this, what is it for, what does this
   column mean. The contract knows all of it, so
   `integrations/odd/curate.py` pushes the owner, the purpose, every column
   description, the quality vocabulary as dictionary terms, the rules as query
   examples, and the SLA and sync rule as metadata — into ODD's own places for
   them. Filled from the contract, it is reviewed in a pull request and cannot
   drift; `tests/test_catalogue.py` fails when a column has no description.

   ![A catalogue entry filled from its contract](docs/odd-catalogue-entity.png)
6. **The failing rows.** "522 orders disagree with their lines" is where an
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

7. **An acknowledged failure.** A red check somebody has looked at and a red
   check nobody has seen are different facts, and neither tool records the
   difference: `datacontract test` has one status per run, and ODD's alerts
   close themselves on the next passing run rather than when a person answers
   for one. `check_status` holds three states and a note -- open is the
   absence of one, *acknowledged* is somebody has seen it, *accepted* is a
   failure being lived with, which the list hides by default. It is keyed to
   the check and not to the run, because a check that fails again tomorrow is
   the same problem somebody already looked at. An accepted check still counts
   in the score -- muting a row and muting a measurement are different
   decisions -- and there is no *who*: ADR 0010, no identity provider, so a
   signature would be a lie.

Plus the one interface neither has: **an analyst can author a rule.** ODD's UI
annotates what was ingested — there is no "create test" anywhere in it, because
a test arrives through ingestion and belongs to whatever produced it — and
datacontract-cli is a CLI. Writing SQL, compiling it against the source without
saving, seeing what it would have caught, and saving it back into the contract
file is the one thing this repository has that neither dependency does.

**It lives on ODD's own Data Quality page**, not on a second port. ODD has no
plugin system, no embed and no custom tab, so that is a fork — and a small one:
the SPA ships as a single jar on the platform's classpath, so only the UI is
rebuilt, with two lines against upstream and a panel of our own. No Gradle, no
Java, no backend patch. `deploy/Dockerfile.odd-platform` says what it costs and
when to retire it.

![The contract panel on ODD's Data Quality page](docs/odd-data-quality-contracts.png)

The panel is written against ODD's own components and type-checks against them,
which is the point of forking rather than injecting: `tsc` caught a real bug in
it before the image was ever built. Select a contract to see the rules behind
its tests, add another, or open the rows a failing check counted.

**Adding a rule does not mean writing SQL.** Pick a column, pick a rule — is
never empty, is one of a list, is between two numbers, has no duplicates,
exists in another table — and the service composes the statement, in the
source's own dialect. `LENGTH` becomes `LEN` on SQL Server, `CURRENT_DATE`
becomes `GETDATE()`, and a value containing a quote is a quoted literal rather
than an injection, because the SQL is built from sqlglot expressions and never
from a format string. The vocabulary lives in `core/rules.py` and the UI
fetches it, so adding a rule kind is a change in one place.

The score over time and the replication rules are on that page too, which is
what finally retired the standalone UI on :8077 — it was a second copy of the
same thing, and two of anything is two to maintain. That port now serves the
API and a page saying where the panel is.

Writing SQL by hand is still there as an escape hatch, and it is the only thing
that needs the API token — the form does not, because a fixed vocabulary has
nothing to smuggle in. **The token is not something to invent, either:** the
service mints one on first use, keeps it, and prints it at startup. See
[ADR 0010](docs/adr/0010-rule-vocabulary-and-the-token.md).

![The contract UI](docs/contract-ui.png)

Reached from ODD, not from a port number — the table's own page carries the way
in, as ODD's own *Attachments* card:

![Our pages on ODD's entity page](docs/odd-entity-links.png)

### Telling someone, once

A contract can miss its SLA, an apply worker can die and a CDC poll can stop,
and until there was a webhook every one of those was discovered by a person
opening a page. ODD's alert lifecycle is real and is adopted -- it opens on
failure and closes itself on the next passing run -- but it lives inside the
platform, and a quality platform whose failures are only visible to whoever
happens to look is a reporting tool rather than a control.

`DQ_ALERT_URL` is one Slack, Teams or Google Chat incoming webhook -- all three
accept `{"text": ...}` -- and `core/alerts.py` is 135 lines. One POST per run, and only for the day just finished -- a backfill
is rebuilding history that has already happened and has nothing to announce.

What it says is the part worth arguing about. **Not "these checks are
failing"** -- that is the same twenty every morning, which is how a channel
gets muted and then deleted. It reports:

* a contract **below its SLA**, and whether that is a low score or checks that
  could not run at all -- invariant 5, kept apart in the sentence as well as in
  the score;
* checks that **newly started failing**: failing today and not failing on that
  contract's previous run;
* replication that is **not moving**, in whichever of the ways it can manage
  that -- a dead apply worker, a table stuck in the initial copy, an
  unreachable source.

ODD raises alerts of its own -- a failed test, a schema change -- and can send
them to Slack, to e-mail, or as its own JSON to a generic webhook, which a
Google Chat or Teams webhook refuses. `ODD_ALERTS_TO_CHAT=true` points that
generic webhook at `/api/alerts/odd` (`api/odd_alerts.py`), which turns each
alert into one line -- what happened, to which entity, what is downstream, a
link back -- and sends it to `DQ_ODD_ALERT_URL`, or `DQ_ALERT_URL` when that
is unset.

An *accepted* failure never alerts, which is what accepting one means
([#29](https://github.com/gkhnelbstn/lightweight-data-platform/issues/29)). An
*acknowledged* one still does when it newly fails: looking at something once
does not make the next outage unremarkable.

Not in scope, and each for a reason: email (SMTP credentials, bounce handling
and a From address are three problems for one message), per-check
subscriptions (that needs identity -- ADR 0010) and an alert history page (the
run log already records what happened; an alert that fired is not a second
kind of fact).

### Talking about an asset

ODD has a Discussions tab on every data entity, and it is a Slack thread:
the message is posted into a channel and its replies come back onto the page.
Slack is the only provider it has, so the tab offers no channel until a
workspace is connected -- and the token that connects one is a credential
belonging to whoever owns that workspace, not something this repository can
carry.

What is here is the wiring and the app. `deploy/slack-app-manifest.yaml`
creates the Slack app with the four scopes ODD uses (`channels:read` for the
channel list, `chat:write` for the message, `users:read` for who replied,
`channels:history` for the replies), and `compose.yaml` reads two variables
from `.env`:

```
ODD_SLACK_ENABLED=true
ODD_SLACK_TOKEN=xoxb-...
```

Then invite the bot to the channels discussions may go to -- ODD lists only
the channels it is a member of. Two things are worth knowing before the first
try: replies arrive through Slack's Events API, which posts to
`/api/slack/events` on this platform, so on a laptop messages go out and
replies do not come back until the platform is reachable from Slack; and
`DATACOLLABORATION_ENABLED: true` with an empty token stops ODD from starting
at all, which is why both variables default to off.

### Which dashboards break

A quality failure is only interesting if you can follow it. The demo now
carries the shape most warehouses actually have — a scheduler reading the ERP
databases and building a medallion warehouse in Postgres, with the marts
charted in Superset:

```
erp.sales_orders          (Postgres, under contract)
  -> Staged Orders        stg.orders     drops cancelled and customerless rows
    -> Orders Fact        fct.orders     joined to the dimension as of the order date
      -> Daily Revenue    mart.revenue_daily
        -> "Gunluk Ciro (mart)"          a Superset chart

erp.customers             (Postgres, under contract)
  -> Customer Dimension   dim.customer   Type 2: one row per version
    -> Orders Fact        fct.orders
```

That chain is read straight out of ODD, and it is the answer to the question:
a failing check on `sales_orders` has a downstream that ends at a dashboard.

**Nothing can infer it.** `demo/medallion.py` builds those tables in Python
because Postgres cannot query another database and half the ERP is SQL Server
— which is exactly why a scheduler is doing it in the first place. There is no
view definition to parse and no foreign key to follow. So the contract declares
it:

```yaml
customProperties:
  - property: derivedFrom
    value: [erp.sales_orders]
  - property: derivedBy
    value: "select ... from raw.orders where customer_id is not null"
```

`integrations/odd/lineage.py` publishes one `DataTransformer` per contract that
declares one. A reference is a **contract id** by preference — it survives a
host or schema change, which an ODDRN written into a yaml does not — with a raw
ODDRN as the escape hatch for a table that has no contract. An unresolvable
reference is reported rather than dropped, because a graph that silently loses
an edge still looks complete.

This is dataset-level lineage, and dataset-level is all "which dashboards
break" needs. Column-level is a different question, and ODD cannot answer it —
see [ADR 0012](docs/adr/0012-odd-not-openmetadata.md).

![The first hops of the chain](docs/lineage-medallion.png)

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
   it is the same fact twice, and it is enforced rather than remarked upon:
   `--check` reads the stored results and refuses a table whose key has been
   measured and does not hold. Neither engine says so usefully on its own --
   Postgres refuses to build the index with a message about an index, and the
   CDC reader silently merges the duplicates into one row.
2. Every column in the row filter must be inside the replica identity, since an
   update is matched against the old row and the old row is only those columns.
   A rule may widen the identity to say so; it may never narrow it.
3. The column list must cover the replica identity.
4. The target needs the same replica identity. This is the silent one.

Rules 2 and 3 are *logical replication's*, not replication's in general, and
the UI reported them against the SQL Server contract until the engine was
passed in. The CDC reader has the whole row out of the change table and is
bound by neither.

`--status` exists for the same reason: it reports whether the apply worker is
actually running and how far behind the slot is, rather than letting a dead
worker look like a quiet one. That was not enough on its own. A live apply
worker, an active slot and zero lag say nothing about whether a *table* is
streaming: the initial copy runs in a second worker, and one that dies -- on
the very replica-identity index the target is required to have, because
`copy_data` had been asked to copy into a table that already held the rows --
restarts every five seconds for ever while all three of those stay green. So
`--status` reads `pg_subscription_rel` too, and a table that never left the
copy reads as not streaming rather than as healthy; `--apply` empties the
target before the copy, which is also printed by `--check`, or the two
describe different things. Issue #35.

The target table is built from the contract as well. Nothing else creates it
-- logical replication replicates into a table that must already exist, and
the CDC reader upserts into one -- so a clean install would otherwise fail at
the first sync. Only the replicated columns are created, which is what makes
the next paragraph physical rather than a filter. The contract states physical
types in the *source's* dialect, so replicating SQL Server into Postgres
translates them.

The column list doubles as a privacy control. `tax_id` is classified in the
contract, is left out of the list, and so **does not exist in the replica at
all** -- masking a column in the UI is no use if the whole column was copied
into another database.

### A copy is not the only way to keep it in step

Nothing in a `syncTo` rule says the target has to be a *copy*. Where both ends
are Postgres, `mode: view` expresses the same rule as a `postgres_fdw` foreign
table and a view over it: same name, same columns, same filter, and the rows
exist once. No worker, no slot, no lag, nothing to be stale between two runs of
anything — and it works across machines, because a foreign data wrapper is an
ordinary client connection.

It is a decision rather than an optimisation, because three things change
([ADR 0017](docs/adr/0017-a-view-instead-of-a-copy.md)):

* **The privacy boundary becomes a grant.** In copy mode a column outside the
  list does not exist in the target. Here it exists at the source, so the
  foreign server maps to a dedicated `sync_fdw` login holding column-level
  `select` on the listed columns and nothing else — and `--apply` asks the
  source `has_column_privilege` for every column the rule leaves out, refusing
  the rule if any of them answers yes. A boundary that was only asked for is
  not a boundary. A column the contract classifies may not be listed in a view
  rule at all.
* **The target is only as available as the source**, and carries its read load.
* **There is nothing to be behind**, so `--status` reads through the view and
  reports whether that worked.

A SQL Server source is refused: `tds_fdw` is not in the image, and an image for
one table is what invariant 6 exists to prevent. That is also where the mass
is — 8.8 MB of the 8.9 MB duplicated in this stack is the CDC target, the one
place a view cannot go. `erp.order_lines` carries the shipped example.

The warehouse follows the same rule. `stg.orders` and `mart.revenue_daily` are
views: one is a projection of `raw`, the other an aggregate of `fct` in the
same database, and both used to be rebuilt on every run and stale in between.
`raw` stays physical because it is a read that crossed a network, `fct.orders`
because it is an as-of range join nobody wants per query, and `dim.customer`
because Type 2 history is by definition rows the source no longer has. The
scores did not move: `dwh.stg_orders` 0.9673 and `dwh.mart_revenue` 1.0000,
before and after.

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

`compose.yaml` is the platform. `compose.demo.yaml` is the sources the demo
runs against, kept separate so that what this project *is* cannot be misread as
requiring a SQL Server and a BI tool.

What follows is the data-quality half, which is where to start. **Everything
else — the integration hub, its flows, the API source, Slack, master data,
the pruning — is in [docs/setup.md](docs/setup.md)**, layer by layer, each
with the command that says whether it worked.

```bash
docker compose up -d db odd-db odd-platform     # wait for ODD to come up
./deploy/odd-bootstrap.sh                       # collector tokens + configs
docker compose up -d                            # + collector + app

docker compose exec app python seed/seed.py                      # 45 days of ERP-ish data
docker compose exec app python core/runner.py --backfill-days 44 \
    --odd-url http://odd-platform:8080
```

* ODD — http://localhost:8080 — the contract panel is on its Data Quality page
* http://localhost:8077 — the API that panel calls; `/` redirects to its
  generated `/docs`

That is the Postgres half. The second source, the replication and the chain
that ends at a dashboard are in the second file:

```bash
docker compose -f compose.yaml -f compose.demo.yaml --profile demo up -d
./deploy/odd-bootstrap.sh --demo                 # add them to the collector
# note the inner quoting: the password lives in the container's environment,
# so it has to be expanded there rather than by your shell
docker compose exec -T mssql sh -c '/opt/mssql-tools18/bin/sqlcmd     -S localhost -U sa -P "$MSSQL_SA_PASSWORD" -C -i /demo/mssql-seed.sql'
docker compose exec app python demo/mongo-seed.py            # FX rates from a public API

# the warehouse a scheduler would build, the dashboards on top of it, and the
# lineage that joins them -- the chain that answers "which dashboards break"
docker compose exec app python demo/medallion.py
docker compose exec app python demo/superset-assets.py
docker compose exec app python integrations/odd/lineage.py --url http://odd-platform:8080

# change data capture, and the sync rules the contracts carry
# turns CDC on, and creates the least-privilege login the collector uses --
# its permissions are what keep CDC's own bookkeeping out of the catalogue
docker compose exec -T mssql sh -c '/opt/mssql-tools18/bin/sqlcmd     -S localhost -U sa -P "$MSSQL_SA_PASSWORD" -C -d erp -i /deploy/mssql-cdc.sql'
docker compose exec app python core/sync.py --check          # nothing is created yet
docker compose exec app python core/sync.py --apply
docker compose exec app python core/sync_mssql.py --interval 30
```

* Superset — http://localhost:8089 (`admin` / `admin`)

`--check` first is the habit worth keeping: it prints every statement it would
run and every reason it will not.

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
| `core/rules.py` | the rule vocabulary, and the SQL it compiles to per dialect |
| `core/rule_plugins.py` | rule kinds a package outside this repo registers |
| `core/alerts.py` | one webhook, one POST per run, only what is new |
| `core/sample.py` | rewrite a check's SQL into the rows it counted |
| `core/profile.py` | nulls and distincts per declared column, in the day's window |
| `core/versions.py` | what a contract says about its own history, and reading it |
| `core/engines/` | one module per source engine: the window, the sampler, the counts |
| `core/sync.py` | derive a Postgres publication/subscription from the contract |
| `core/sync_mssql.py` | apply SQL Server's CDC change table to a Postgres target |
| `core/sync_view.py` | the same rule as a `postgres_fdw` view, where a copy buys nothing |
| `deploy/mssql-cdc.sql` | turn on SQL Server CDC for the demo tables |
| `api/main.py` | read API + analyst rule authoring, writing ODCS |
| `integrations/odd/` | ODDRN vocabulary, the datacontract → ODD bridge, PII classification |
| `integrations/odd/entity_page.py` | the links ODD shows on the table's own page |
| `integrations/odd/curate.py` | owner, purpose, column meanings, glossary, query examples |
| `deploy/Dockerfile.odd-collector` | odd-collector plus two fixes to its Superset adapter |
| `deploy/Dockerfile.odd-platform` | ODD with the contract panel on its Data Quality page |
| `deploy/odd-platform-ui/` | that panel — React, in ODD's own design system |
| `compose.yaml` | the platform: ODD, a collector, the two databases, the app |
| `compose.demo.yaml` | the sources the demo needs: SQL Server, MongoDB, Superset |
| `demo/` | the seed, the medallion warehouse and the Superset assets |
| `Dockerfile`, `deploy/` | the images and the runbook |
| `docs/architecture.md` | how it works, in diagrams — start here |
| `docs/tutorial.md` | eight lessons, each ending in something you can see |
| `docs/adr/` | why each of these decisions exists, and what would retire it |
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

* **The window still sees inserts, not corrections.** `loaded_at` is a
  watermark: a row corrected in place keeps its original one. A contract whose
  table is updated in place widens its own window —

  ```yaml
  customProperties:
    - property: windowPredicate
      value: "{col} = {day} or updated_at::date = {day}"
  ```

  — and a source with neither a watermark nor CDC has genuinely invisible
  deletes, which no amount of contract fixes. (Replication is a different
  question and does use CDC; see below.)
* **Bi-directional replication has no conflict resolution, by decision.**
  Subscriptions are created with `origin = none` (PG16), so a two-way pair does
  not loop — that much is verified. Two writers touching the same row is
  last-writer-wins, and a real conflict stops the apply worker silently. A real
  policy would mean proving two `syncTo` row filters touch disjoint rows —
  a small theorem prover over arbitrary SQL, and a wrong "yes" there is worse
  than the feature not existing. Nothing here is bi-directional, so it stays
  out of scope until a real pair needs it:
  [ADR 0008](docs/adr/0008-replication.md#bi-directional-replication-is-out-of-scope),
  decided in
  [#6](https://github.com/gkhnelbstn/lightweight-data-platform/issues/6).
* **Custom SQL is executed as written.** The checks connect as `dq_reader` --
  `SELECT` only, `default_transaction_read_only`, a 60s `statement_timeout` --
  so a rule cannot write or hang. That is a smaller blast radius, not a
  sandbox: it can still read every column it is granted and cost a table scan.
* **Authenticated where SQL a person typed would otherwise run or ship.**
  `/api/rules` and `/api/rules/preview` compile and run raw SQL; `/api/sync/rules`
  turns a typed row filter into a publication's `WHERE` clause. All three
  require `DQ_API_TOKEN` and refuse when it is unset. `/api/rules/structured`
  needs none — the vocabulary is fixed and composed server-side, so there is
  nothing to smuggle in. Reads are open, and ODD's `/ingestion/**` is open by
  its own design (issue #1740). Private network.
* **No column-level lineage, by decision.** ODD's ingestion model has none —
  `DataTransformer` is dataset-level — checked against 0.29.0 rather than
  assumed: there is no field for a column pair. Modelling it ourselves (parsing
  `derivedBy` with sqlglot) is real work with no concrete demand behind it yet,
  so the decision is to do neither, and ask instead:
  [odd-platform#1895](https://github.com/opendatadiscovery/odd-platform/issues/1895),
  decided in
  [#4](https://github.com/gkhnelbstn/lightweight-data-platform/issues/4).
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

* **Keeping a second database in step.** The contract carries the rule
  (`syncTo`: target, row filter, replica identity, column list) and each engine
  applies it with its own mechanism — a Postgres publication and subscription,
  or SQL Server's CDC change table read forward from a stored LSN. Nothing of
  ours sits in the stream. The four Postgres preconditions are checked before
  anything is created, because logical replication fails *after* the initial
  copy in a worker that only writes to the server log.
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
  and tags it in ODD — `pii:TR_NATIONAL_ID`, `pii:EMAIL_ADDRESS` — as first-class,
  searchable tags. The recognisers are Microsoft's **Presidio** (MIT, ~10k
  stars) rather than patterns of our own; the Turkish identifiers were ours
  because Presidio had none, and they validate the checksum rather than
  matching eleven digits. It costs ~265 MB in the image, which is the small
  spaCy model rather than the 425 MB default — a column of identifiers is not
  free text, so the NLP half earns very little here. TCKN has since shipped
  upstream independently
  ([microsoft/presidio#1995](https://github.com/microsoft/presidio/pull/1995));
  VKN was offered
  ([presidio#2250](https://github.com/data-privacy-stack/presidio/pull/2250),
  the project having moved) and is tracked in
  [#5](https://github.com/gkhnelbstn/lightweight-data-platform/issues/5) —
  ours gets deleted, not kept alongside, once either ships in a release.
* **The customer dimension overwrote history.** `dim.customer` was Type 1:
  dropped and rebuilt from the source every run, so a re-graded customer's
  earlier segment stopped existing the moment sales changed it, and a closed
  month's revenue could move between segments after the fact. It is Type 2
  now — a surrogate key, a half-open `[valid_from, valid_to)` interval,
  `is_current` — and `fct.orders` joins as of the order date:
  [#9](https://github.com/gkhnelbstn/lightweight-data-platform/issues/9).
* **`syncTo` rules were read-only.** `/api/sync` could report whether a
  replication rule was sound but not create or change one — that meant
  hand-editing a contract's YAML. `POST /api/sync/rules` authors one the way a
  quality rule already is: rejected before writing, with the specific reason,
  against ADR 0008's own four preconditions run on the rule as proposed:
  [#10](https://github.com/gkhnelbstn/lightweight-data-platform/issues/10).
* **A saved rule left no record of what it replaced.** Beyond git blame on a
  file a script also writes, nothing said what a rule used to be. Every write
  through `/api/rules`, `/api/rules/structured` or `/api/sync/rules` now logs
  to `contract_audit` — what changed, when, and an optional free-text label —
  read back at `GET /api/contracts/{id}/audit`. Deliberately not *who*: there
  is no identity provider (ADR 0010), so a verified-looking column would be
  lying:
  [#12](https://github.com/gkhnelbstn/lightweight-data-platform/issues/12).
* **The test suite had no in-container equivalent.** `pytest -q` worked only
  against a host install; the app image carried neither `tests/` nor `pytest`.
  Both are there now, the same way `core/` and `api/` are bind-mounted rather
  than only baked in: `docker compose exec app pytest -q tests`:
  [#7](https://github.com/gkhnelbstn/lightweight-data-platform/issues/7).
* **`check_results` and `contract_scores` had no index a real query used.**
  Both primary keys lead with `run_at`; every hot-path query filters
  `contract_id`, `check_id` or `run_window` instead, so none of it was a
  usable prefix -- measured with `EXPLAIN ANALYZE` against the shipped demo
  data, every one of them a sequential scan across every `check_results`
  partition. Four indexes now match the shapes actually queried; a
  partitioned index reaches every future monthly partition
  `ensure_partition()` creates without anything else changing.
* **The Contracts panel had no search, no sort, and two backend additions
  with nothing to drive them.** Eleven contracts and no way to find one
  except scrolling, next to a platform whose own catalog is full-text search
  end to end -- confirmed, not assumed: `odd-platform`'s schema carries a
  `tsvector`-backed `search_vector` and a per-kind vector on nearly every
  table. Client-side filtering and column-header sort now cover the panel's
  own list; `POST /api/sync/rules` (#10) and `GET /api/contracts/{id}/audit`
  (#12) each get the form and the display they were missing, in
  `ContractPanel` next to the quality-rule form they sit beside.

* **The panel was one long scroll, and half of what the API returned was
  rendered nowhere.** Checks have their own screen now -- every check on the
  platform in one filterable table, opening on *Not passing*, with the kind of
  check, the table it looks at, its SQL or its assertion in words, its run
  history and the rows it failed on. The contract's own sections became tabs
  rather than five screens of column, and `GET /api/checks` marks a check
  *stale* when its contract has run since without it, because results outlive
  the rules that produced them:
  [#21](https://github.com/gkhnelbstn/lightweight-data-platform/pull/21).
* **A failing check could not be acknowledged.** Four `order_id` uniqueness
  checks were red three days running and there was nowhere to say "known, the
  join is the cause". `check_status` and `POST /api/checks/{id}/status` are
  the smallest thing that stops the next person rediscovering them:
  [#29](https://github.com/gkhnelbstn/lightweight-data-platform/issues/29).
* **The warehouse dimension had history and nothing read it.**
  `core/versions.py` reads what the contract declares -- the interval columns
  plus a `versionedBy` business key -- rather than inferring it from column
  names, which gets `dim.customer` right and the next table wrong. The panel's
  History tab shows the versions of a key side by side.
* **Replication reported that it was configured, not that anything arrived.**
  `/api/sync` now carries the row counts on both sides, the CDC reader's last
  ten passes and when it last read, so a poll nobody started stops looking
  like a poll with nothing to do -- and `sync-mssql` is a service in
  `compose.demo.yaml` rather than a command somebody has to remember.
* **A re-apply stopped replication for good and `--status` called it
  healthy.** The table sync worker died on a duplicate key every five seconds
  while the slot stayed active and the apply worker stayed up:
  [#35](https://github.com/gkhnelbstn/lightweight-data-platform/issues/35).
* **The same `if source is sqlserver` sat in three files.** The window, the
  sampler and the ODDRN generator each branched on the engine name;
  `core/engines/` is one module per engine and the branch is a lookup:
  [#34](https://github.com/gkhnelbstn/lightweight-data-platform/issues/34).

### Reported upstream

| what | where |
|---|---|
| ERD relationships unreadable when a column has two `dataset_field` rows | [odd-platform#1880](https://github.com/opendatadiscovery/odd-platform/issues/1880) |
| the Superset adapter fixes, as a PR | [odd-collectors#136](https://github.com/opendatadiscovery/odd-collectors/pull/136) |
| `Table.sql()` on postgres emits a nameless `DROP VIEW` (sqlglot 30 renamed `Drop.this`) | [ibis#12108](https://github.com/ibis-project/ibis/issues/12108) |
| `datacontract test --filter` unusable on postgres because of the above | [datacontract-cli#1592](https://github.com/datacontract/datacontract-cli/issues/1592) |
| a row filter is all-or-nothing; uniqueness needs to opt out | [datacontract-cli#1593](https://github.com/datacontract/datacontract-cli/issues/1593) |
| odd-collector's Superset adapter: int ids, and lineage only for postgresql/sqlite | [odd-collectors#135](https://github.com/opendatadiscovery/odd-collectors/issues/135) |
| metric ingestion is write-once per family: the second write is an NPE | [odd-platform#1882](https://github.com/opendatadiscovery/odd-platform/issues/1882) |
| column-level lineage edges, alongside the existing dataset-level ones | [odd-platform#1895](https://github.com/opendatadiscovery/odd-platform/issues/1895) |
| every lineage node draws the root's data source icon (one SVG, one filter id) | [odd-platform#1898](https://github.com/opendatadiscovery/odd-platform/issues/1898) — patched here, three anchored lines |

## Where this goes next

The direction is still to keep shrinking the part we maintain. The first
version of this list said "adopt ODCS and let `datacontract test` derive the
checks" and "add authentication"; both are done, and what is left is smaller
and mostly other people's to merge.

1. **Delete `deploy/Dockerfile.odd-collector`**
   ([#1](https://github.com/gkhnelbstn/lightweight-data-platform/issues/1)) when
   [odd-collectors#136](https://github.com/opendatadiscovery/odd-collectors/pull/136)
   merges. Carrying a patch is a debt, and the point of sending it upstream is
   to stop paying it.
2. **Move the window into the contract proper**
   ([#2](https://github.com/gkhnelbstn/lightweight-data-platform/issues/2)) if
   [datacontract-cli#1593](https://github.com/datacontract/datacontract-cli/issues/1593)
   lands — per-rule scoping would retire `TABLE_SCOPED_TYPES` and the second
   unwindowed pass with it.
3. **Land the Turkish identifiers in Presidio.** TCKN shipped upstream on its
   own; VKN is an open PR
   ([presidio#2250](https://github.com/data-privacy-stack/presidio/pull/2250)).
   Delete ours once either ships in a release, tracked in
   [#5](https://github.com/gkhnelbstn/lightweight-data-platform/issues/5).
4. **Backfill the SQL Server history** before 2026-08-16, which is still
   recorded as errored from the period when that source did not exist.
5. **Publish the score as an ODD metric**
   ([#3](https://github.com/gkhnelbstn/lightweight-data-platform/issues/3))
   once its metrics API accepts a second write to the same family
   ([odd-platform#1882](https://github.com/opendatadiscovery/odd-platform/issues/1882)).

Every one of these is an open issue, and each says what "done" means and what
to delete when it is. Column-level lineage and bi-directional replication's
conflict policy used to be on this list; both are now decisions rather than
open questions -- see "Status and limitations" above.

## License

MIT.
