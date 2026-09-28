# Bringing the whole thing up on a new machine

Written to be followed top to bottom by somebody — or some agent — who has the
repository and nothing else. Every feature this platform has is a layer here,
each with what it needs, the command that starts it, and the command that says
whether it worked. Stop after any layer: they build on each other in this
order and nothing below is required by anything above.

`README.md` says *why* each of these exists and `docs/tutorial.md` teaches the
data-quality half by using it. This file is the mechanical part, and it is the
one to open when the answer to "does it work here" has to be yes today.

---

## 0. What the host needs

* **Docker** with Compose v2.24 or newer. The compose files use `!reset` and
  `!override`, which older versions ignore silently — the symptom is a service
  failing to bind a port another one already has.
* **4 vCPU / 8 GiB / 100 GB.** Measured idle: ODD 924 MB, its Postgres 147 MB,
  collector 64 MB, SeaTunnel ~450 MB, SQL Server capped at 2 GB. ODD still
  starts capped at 1 GB if the box is smaller.
* **Python 3.10+** only if you want to run the suite or the CLIs from the host
  rather than `docker compose exec app`. Everything below works without it.
* **Free ports.** Nothing else may hold these:

  | port | what |
  |---|---|
  | 8080 | ODD Platform — the catalogue, the contract panel, the Integration tab |
  | 8077 | the platform's own API; `/` redirects to `/docs` |
  | 5442 | the platform's PostgreSQL (`erp`, `dq`, `hub`, `dwh`, `shop`, …) |
  | 5433 | ODD's own PostgreSQL |
  | 8089 | Superset (`admin` / `admin`) |
  | 8081 | SeaTunnel's console |
  | 1433 | SQL Server (the demo's CRM, billing and ledger) |
  | 27017 | MongoDB (the FX reference feed) |
  | 8099 | the demo's loyalty API, the HTTP source |

Nothing here reaches the internet except two things, and both are named in
§7: the FX feed the Mongo seed pulls, and Slack if you turn discussions on.

## 1. The one file that does not travel

`.env` is gitignored. Copy the example and fill in what you need — every key
has a working default except the two Slack lines, which are deliberately off:

```bash
cp .env.example .env
```

Leave it as it is for a first bring-up. §7 says what each key buys.

## 2. Layer 1 — the platform

This is what the project *is*: a catalogue, a contract panel, the runner and
their two databases. No SQL Server, no BI tool.

```bash
docker compose up -d db odd-db odd-platform
```

ODD takes a minute or two. Wait for it, because the next step talks to it:

```bash
docker compose ps odd-platform          # wait for (healthy)
```

Then mint the collector's token and write its configs. ODD guards
`POST /ingestion/datasources` with a filter that is always on, so a collector
cannot register itself and dies at startup with a bare 500 until this is run:

```bash
./deploy/odd-bootstrap.sh
docker compose up -d                    # + collector + app
```

Seed something to look at, and run the contracts over it:

```bash
docker compose exec app python seed/seed.py
docker compose exec app python core/runner.py --backfill-days 44 \
    --odd-url http://odd-platform:8080
```

**Worked when:** http://localhost:8080 shows the catalogue, its Data Quality
page has the contract panel with a score per contract, and http://localhost:8077/docs
answers.

## 3. Layer 2 — the demo's other sources

A SQL Server, a MongoDB and a Superset, kept in a second compose file so that
what the platform requires cannot be misread.

```bash
docker compose -f compose.yaml -f compose.demo.yaml --profile demo up -d
./deploy/odd-bootstrap.sh --demo
```

From here on **every** `docker compose` command needs both files. Forgetting
the second one is the most common mistake and the error is `no such service`.
Set it once instead:

```bash
export COMPOSE_FILE=compose.yaml:compose.demo.yaml
```

Seed them. The SQL Server password lives in the container's environment, so
the inner quoting matters — it has to be expanded there, not by your shell:

```bash
docker compose exec -T mssql sh -c \
  '/opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$MSSQL_SA_PASSWORD" -C -i /demo/mssql-seed.sql'
docker compose exec app python demo/mongo-seed.py
```

The warehouse a scheduler would build, the dashboards on it, and the lineage
that joins them:

```bash
docker compose exec app python demo/medallion.py
docker compose exec app python demo/superset-assets.py
docker compose exec app python integrations/odd/lineage.py --url http://odd-platform:8080
```

**Worked when:** Superset at http://localhost:8089 (`admin` / `admin`) has
dashboards, and ODD's lineage graph joins the ERP tables to them.

## 4. Layer 3 — replication

Change data capture and the `syncTo` rules the contracts carry. The CDC script
also creates the least-privilege login the collector uses: its permissions are
what keep CDC's own bookkeeping tables out of the catalogue.

```bash
docker compose exec -T mssql sh -c \
  '/opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$MSSQL_SA_PASSWORD" -C -d erp -i /deploy/mssql-cdc.sql'
docker compose exec app python core/sync.py --check     # prints what it would do
docker compose exec app python core/sync.py --apply
```

`--check` first is the habit worth keeping everywhere in this repository: it
prints every statement it would run and every reason it will not.

The CDC reader is a poll, so something has to run it. `sync-mssql` is that
something and it is already up with the `demo` profile — it is the app image
with a different command.

**Worked when:**

```bash
docker compose exec app python core/sync.py --status
```

...reports a live slot, a live apply worker and no table stuck in its initial
copy. A poll nobody started looks exactly like a poll with nothing to do, so
check this rather than assuming.

## 5. Layer 4 — the integration hub and its flows

Two-way integration between systems that are not ours: a CRM and a billing
package on SQL Server, a shop on Postgres, meeting in a hub. SeaTunnel moves
the rows; the hub decides what a record is.

SeaTunnel is opt-in because an idle cluster is ~450 MB:

```bash
docker compose --profile demo --profile flows up -d
```

Create the systems and their data:

```bash
docker compose exec -T mssql sh -c \
  '/opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$MSSQL_SA_PASSWORD" -C -i /demo/integration/setup.sql'
docker compose exec -T db psql -q -U postgres -f - < demo/integration/shop_setup.sql
```

Then compile the flows and submit them. `--check` refuses a bad set without
writing anything; `--apply` creates the hub, registers the systems and starts
the jobs:

```bash
docker compose exec app python core/flow_jobs.py --check --contracts demo/integration
docker compose exec app python core/flow_jobs.py --apply --contracts demo/integration
```

**Worked when:**

```bash
docker compose exec app python demo/integration/verify.py
```

...prints `ok` for every line and ends with `no echo loop (… inbox rows,
unchanged for 30 s), nothing held`. It takes a few minutes; it drives the
whole demo and asserts it.

The Integration tab is in ODD's own menu, beside Catalogue and Data Quality.
SeaTunnel's console is http://localhost:8081.

## 6. Layer 5 — the API source

A loyalty scheme that exposes its members over HTTP and nothing else: the
platform reads it and never writes to it. Its records live in a database of
its own that no flow touches — reading the endpoint instead is the point.

```bash
docker compose exec -T db psql -q -U postgres -f - < demo/integration/loyalty_setup.sql
docker compose --profile demo up -d loyalty
docker compose exec app python core/flow_jobs.py --apply --only loyalty_to_hub \
    --contracts demo/integration
```

**Worked when:** `curl -s http://localhost:8099/members` lists two members, and
within a minute the hub has linked one of them to the customer the other three
systems already hold — `verify.py`'s last three lines assert exactly this.

An API source needs a patched SeaTunnel image, and the build carries it: the
stock `Http` source waits out its poll holding the checkpoint lock, so a
streaming job never checkpoints and is failed at the timeout. If you build the
image somewhere that cannot reach github.com, that patch is what fails.

## 7. Everything that talks to something outside this machine

| what | needs | where it is set | without it |
|---|---|---|---|
| **FX rates** (`demo/mongo-seed.py`) | `api.frankfurter.dev`, no key | — | the Mongo source has no data; nothing else cares |
| **Slack discussions** (ODD) | a Slack app and a bot token | `ODD_SLACK_ENABLED`, `ODD_SLACK_TOKEN` in `.env` | ODD's Discussions tab offers no channel |
| **Alerting** (`core/alerts.py`) | one incoming webhook URL | `DQ_ALERT_URL` | nothing is announced; everything else is unchanged |
| **the raw-SQL rule route** | a token you chose | `DQ_API_TOKEN` | one is generated and printed at startup |
| **image builds** | github.com, Maven Central, npm | — | the SeaTunnel and ODD images cannot be built; both carry patches |

**Slack, in full.** `deploy/slack-app-manifest.yaml` is the app to create. The
bot token is the workspace owner's to mint, and the bot must then be invited
to each channel. Replies arrive at `/api/slack/events`, so answering a thread
needs this platform reachable from Slack; posting does not. Both variables
must be set together — `DATACOLLABORATION_ENABLED: true` with an empty token
makes ODD refuse to start with *"Slack OAuth token is empty"*.

Everything else — the databases, SeaTunnel, the collector, Superset, the
loyalty API — talks only inside the compose network and by service name. That
is also why the contracts say `host: db` rather than `localhost`: the name in
a contract is the name the *containers* resolve, and it ends up in the ODDRNs.

## 8. The environment, in one table

Only the ones worth setting. Everything else has a default that is right
inside compose.

| variable | default | read by |
|---|---|---|
| `ERP_DSN`, `DQ_DSN` | the compose Postgres | the runner, the API, the store |
| `DQ_HOST` | `dq.local` | ODDRN identity — **keep it stable**, it is the catalogue's notion of who we are |
| `DQ_ODD_UI_URL` | `http://localhost:8080` | the links written into ODD, which a browser follows, not a container |
| `DQ_CORS_ORIGINS` | `http://localhost:8080` | which origins may call the API |
| `DQ_API_TOKEN` | generated | the raw-SQL rule route |
| `DQ_ALERT_URL` | empty | where a newly failing check is announced |
| `DQ_MIN_SCORE` | `0.95` | the score an SLA breach is below |
| `LDP_LANGUAGE` | `en` (`tr` in compose) | text the **server** writes once for everyone; the UI follows ODD's own picker per viewer |
| `INTEGRATION_DIR` | `contracts` (`demo/integration` in compose) | where the Integration tab looks for hub contracts |
| `PG_USER`, `PG_PASSWORD`, `MSSQL_USER`, `MSSQL_PASSWORD` | the demo's | what a flow's job is given to reach the systems; the job files never carry a credential |
| `SEATUNNEL_URL` | `http://seatunnel:8080` | where flows are submitted |
| `HUB_HOST`, `HUB_PORT` | `db`, `5432` | `core/hub.py` |
| `ODD_SLACK_ENABLED`, `ODD_SLACK_TOKEN` | off | discussions |
| `FLOW_CHECKPOINT_MS` | `3000` | a flow's default checkpoint interval |

## 9. Running it

**Daily**, one line in cron. It rebuilds the day's views, runs every contract
through `datacontract test` against its own server type, stores the results as
a time series, scores them, and sends the runs to ODD attached to the table:

```bash
docker compose exec -T app python core/runner.py --odd-url http://odd-platform:8080
```

That same command publishes the hub's golden records to ODD's Master Data
page. The other catalogue pushes are separate and rarely change:

```bash
docker compose exec app python integrations/odd/classify.py --url http://odd-platform:8080
docker compose exec app python integrations/odd/curate.py --url http://odd-platform:8080
docker compose exec app python integrations/odd/lineage.py --url http://odd-platform:8080
```

**Periodically**, drop the log nobody is reading. Three tables grow with what
happens and nothing else bounds them; `hub.tombstone` is deliberately not one
of them:

```bash
docker compose exec app python core/hub.py --prune --days 30
```

**After a restart of SeaTunnel** — and a restart loses every job — resume them
rather than starting fresh. A fresh start re-reads every table, the hub takes
the re-read for a first sync, and an edit made during the outage goes to the
authority while a delete is never seen:

```bash
docker compose exec app python core/flow_jobs.py --apply --contracts demo/integration
```

That resumes each flow under its old id from its checkpoint, and **refuses**
when it cannot. `--resnapshot` is the knowing way through a refusal, and it
says what it costs. The same four buttons are on the Integration tab.

## 10. Is it all up?

```bash
docker compose ps                                              # every service, and healthy
docker compose exec app pytest -q tests                        # the suite, from the image
docker compose exec app python core/sync.py --status           # replication
docker compose exec app python core/flow_jobs.py --check --contracts demo/integration
docker compose exec app python demo/integration/verify.py      # the whole integration, end to end
curl -s http://localhost:8099/members                          # the API source
```

The suite reports a handful of skips without the databases it needs; from the
app image against the demo Postgres nothing should skip except the four that
always do.

## 11. When it does not work

| symptom | what it is |
|---|---|
| `no such service: seatunnel` | one compose file. Both are needed: `-f compose.yaml -f compose.demo.yaml` |
| the collector dies at startup with a 500 | `./deploy/odd-bootstrap.sh` was not run, or was run before ODD was healthy |
| ODD refuses to start, *"Slack OAuth token is empty"* | `ODD_SLACK_ENABLED=true` with no token. Set both or neither |
| a screen answers with code you just changed away from | the app image runs `uvicorn` without `--reload` and the source is mounted: `docker compose restart app` |
| `Factory initialize failed - Unable to create a source` | an empty `PG_USER` or `MSSQL_USER` in the app's environment |
| `--apply` fails with a bare `HTTP 500` | a checkpoint a crash left half-written. It is refused with that sentence now; `--resnapshot` is the way through |
| a flow says `already running` and nothing starts | a stop that had not finished. `stop()` waits for the job to be gone; if you stopped it by hand, wait |
| the Integration tab's chart looks busy while nothing happens | rows from before the trigger stopped keeping no-ops: `delete from hub.<entity>_inbox where outcome = 'unchanged'` |
| Turkish characters mangled through `sqlcmd` | the string needs `N''`, or the file needs `-f 65001` |
| the suite fails six alert tests | compose sets `LDP_LANGUAGE: tr`; the alert tests pin English themselves, so this means an older image — rebuild |

`CLAUDE.md`'s *Gotchas already paid for* is the long version of this table, and
every line in it cost somebody an afternoon.
