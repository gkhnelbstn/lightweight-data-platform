# ODD Platform vs the contract layer — verified against a running instance

> **Note.** This audit was written against the pre-ODCS codebase and is
> kept as the record of what was verified against a running ODD instance,
> not as a map of the current tree. `push.py`, `stats.py`,
> `deploy/odd-compose.yaml` and `*.contract.yaml` no longer exist: the push
> lives in `integrations/odd/from_datacontract.py`, the stack is the root
> `compose.yaml`, and the contracts are `contracts/*.odcs.yaml`. The
> findings about ODD itself still hold.

The first version of this document was written from the ODD specification and
`odd-dbt`'s source, without ever starting the platform. This version is the
same claims re-checked against a real instance. Every verdict below is
`confirmed`, `partly confirmed` or `refuted`, and says what was actually seen.

**Trial setup.** `ghcr.io/opendatadiscovery/odd-platform:latest` from
`deploy/odd-compose.yaml`, `AUTH_TYPE=DISABLED`, empty database. Pushed: 2
contracts, 23 derived checks, 45 days of runs (2026-07-23 .. 2026-09-05) —
46 payloads, 1060 entities. ERP on `localhost:5442`, `DQ_HOST=dq.local`.

---

## 0. What the old document did not know: ingestion does not work out of the box

Not a claim in the original — an omission, and the first thing that happened.

`POST /ingestion/entities` returned **404** before looking at a single entity:

```
{"code":"USR002","message":"DataSource with oddrn //datafletch/host/dq.local is not found"}
```

ODD only accepts a `DataEntityList` whose `data_source_oddrn` it already knows.
Collectors register themselves at startup; we are not a collector, so nothing
had. The runbook's "10-minute path from zero" quietly assumed a step that does
not exist.

Two further details, both costing time:

* `POST /ingestion/datasources` — the endpoint the name suggests — returns
  **500** with `AccessDeniedException: Token is missed`. The compose file sets
  `AUTH_INGESTION_FILTER_ENABLED: "false"`; that setting does not disable the
  ingestion filter on this path in this build.
* `POST /api/datasources` (the platform API, not the ingestion API) works with
  auth disabled, is idempotent-by-name (a repeat is a resolvable 400
  `USR003 Data source with this name already exists`), and returns a collector
  token we do not need.

`push.py` now registers the data source itself if it is missing, so the trial is
one command against an empty platform. Registration is the honest fix: the
payloads were never malformed — `odd-models` had validated all 1060 entities
before the first POST, and every payload was accepted unchanged once the data
source existed.

---

## 1. "No row counters. The volume signal can only be shipped as free text in `status_reason`."

**Verdict: refuted for the platform, confirmed for the run model.**

This was the document's central claim and it was wrong, because only ODD's
run model had been looked at. The dataset model holds numbers:

* **`DataSet.rows_number`** — the table's row count, part of the catalog payload.
  We never set it, which is why every table read `Rows 0`.
* **`POST /ingestion/entities/datasets/stats`** — per-column `nulls_count`,
  `unique_count`, `low_value`, `high_value`, stored as structured JSON and
  rendered on the column panel. After pushing them, `customer_id` reads
  **`Unique 399 | Missing 75 | Min 1 | Max 400`** — and `Missing 75` is exactly
  what `customer_id.not_null` counts.

So the numbers our checks produce do have a first-class home; we were sending
them to the wrong place. `integrations/odd/stats.py` now fills it.

What has no home is narrower than claimed, and still real: a **per-run**
`failed_rows` tied to one check execution on one day. That is the run model,
and there the original claim stands:

`DataQualityTestRun` really is `{data_quality_test_oddrn, start_time, end_time,
status, status_reason}` — there is no numeric field, so the daily per-check
counts have nowhere to go but the string.

The `/ingestion/metrics` endpoint looks like the answer and is not. It accepts
a `MetricSet` with Prometheus-shaped gauge points, returns **201**, and stores
nothing: `metric_family`, `metric_series` and `metric_point` stay empty, with
no error in the log, on the default `metrics.storage: INTERNAL_POSTGRES` and
with `METRICS_EXPORT_ENABLED=true` alike. The epic that would implement it
(#1180, "Revisiting Metrics API") has been open since 2022. Treat the Metrics
API as absent.

A second correction: the original text implied the string makes the volume
signal invisible. It does not. `status_reason` is a first-class column in a
check's **History** tab, so the daily row counts read as a legible series:

![Run history with row counts as free text](odd-run-history.png)

45 daily runs, each showing `6/70 rows failed (8.57%) severity=major`. The data
is there and a human can read it. What ODD cannot do with it is anything else:
no sort by fail ratio, no threshold, no chart, no aggregate, no "which check
degraded most this week" — to ODD each of those is an opaque string. The
distinction that mattered to us (a typo vs an outage) survives only in the
reader's head.

Two smaller findings in the same area:

* The dataset overview showed **Rows 0** because we never sent
  `DataSet.rows_number`; nothing derives one from the runs. Fixed.
* The inline test-report panel pages the history 10 runs at a time
  (`/api/dataentities/{id}/runs?page=1&size=10`). All 45 are stored and the
  full list is on the check's own page; the panel just does not show them.

## 2. "No severity on the run or the test. Severity is an operator setting inside the platform, not part of the ingested payload."

**Verdict: confirmed, and now precise about where it bites.**

ODD *does* store and display what we send: the expectation body appears verbatim
under **Parameters** on each test, including `"severity": "critical"`. So the
contract's severity is visible.

It is also ignored. ODD keeps its own **Severity** control — a dropdown on each
test — and it defaults to `MAJOR` regardless of the payload:

![Contract severity critical, ODD severity MAJOR](odd-severity.png)

`net_amount.not_null` is `critical` in `contracts/sales_orders.contract.yaml`,
reads `"severity": "critical"` in ODD's own Parameters box, and sits at `MAJOR`
in the control that ODD actually uses. The two never meet.

One correction to the runbook, which guessed this was set per dataset: it is
**per test**. With 23 checks that is 23 dropdowns to align by hand, and nothing
keeps them aligned when the contract changes. Alerts do not carry severity at
all (see §6).

## 3. "No score, no SLA evaluation. Pushed as dataset metadata, i.e. decoration."

**Verdict: partly refuted — wrong in both directions.**

**Wrong the first way: ODD does score.** The dataset overview carries a
`Test report — 80% score, 15 tests` panel and an `SLA 12/15` widget. The
original document said ODD "counts passing and failing tests"; it also divides
them and calls the result a score, and it has a widget literally labelled SLA.

That score is not ours and does not replace ours. It is an unweighted pass
ratio over the **latest** run only: a failing `critical` not_null and a failing
`minor` cosmetic rule each cost 1/15, and one failed row weighs the same as
four thousand. Our score is severity-weighted and blends pass/fail with fail
ratio precisely because that flattening is what made a dashboard decorative.
Both numbers can be on screen at once and mean different things, which is worse
than only having one.

**Wrong the second way: `contract_sla_min_score` is not decoration, it is
discarded.** ODD's stored metadata for `sales_orders` is exactly one field:

```
contract_id (STRING) = "erp.sales_orders"
```

`contract_sla_min_score = 0.95` was sent and is simply not there. A direct
probe — one entity carrying a string, an int, a bool, a float and a list —
came back with the string, the int and the bool stored and **the float and the
list dropped without an error**. So:

* every numeric threshold in a contract (`min`, `max`, `min_score`) is lost on
  the metadata path;
* `allowed: [...]` is lost on the metadata path too — `currency.accepted_values`
  has no `values` in its stored metadata.

Both survive on the *expectation* path, because `check_entity` stringifies
params: the test's Parameters box does show
`"values": "['TRY', 'USD', 'EUR']"`. That is luck, not design. Anything we want
ODD to keep must be a string, an int or a bool.

## 4. "No scoring window. Our incremental-vs-cumulative distinction is not expressible."

**Verdict: confirmed, and understated.**

The platform-wide **Data Quality** dashboard has no time axis of any kind. Two
donuts and a category table, all reflecting the latest run:

![ODD Data Quality dashboard](odd-quality-dashboard.png)

45 days of history are in the database and none of it is on this page. There is
no trend line, no per-day view, no way to ask "which days were bad" — so the
question the runbook posed ("does the platform-wide view distinguish the
incident days?") has a flat answer: **no**, and not because the window is wrong
but because there is no window.

One thing on this page was **our** bug, not ODD's. Before the fix the dashboard
read `Total Tests 0` with all 23 checks ingested and visible on their datasets.
ODD buckets quality tests by `DataQualityTestExpectation.category`, and we were
sending none, so every test fell outside every bucket and was counted nowhere.
Setting the category (`FRESHNESS_ANOMALY` for freshness, `ASSERTION` for the
rest — ODD's other categories describe anomaly detection, which a deterministic
contract check is not) produces the screenshot above: 23 tests, 22 assertions
(19 pass / 3 fail), 1 freshness. The old document's claim was right about the
window and would have been right about the dashboard for the wrong reason.

## 5. "No contract. There is no contract entity and no link from 'this test exists' to 'because the contract says so'."

**Verdict: confirmed.**

`contract_id` survives as string metadata and `contract:erp.sales_orders` as a
tag; both are searchable and both appear as facets on the landing page. Neither
is an object. There is no contract page, no version, no diff, nothing that
breaks when a rule is removed. The suite name (`erp.sales_orders`) is the only
grouping ODD understands, and it is a label.

## 6. Alerts — the part the original document assumed rather than checked

**Verdict: works, and is the strongest thing ODD gave us.**

After one 45-day backfill into an empty platform: **3 open, 10
`RESOLVED_AUTOMATICALLY`, 0 resolved by hand** —

```bash
curl -s "localhost:8080/api/alerts/list?type=ALL&status=OPEN&page=1&size=500" | jq length
curl -s "localhost:8080/api/alerts/list?type=ALL&status=RESOLVED_AUTOMATICALLY&page=1&size=500" | jq length
```

An alert opens on a check's first failure, does not duplicate on subsequent
failures (it appends to its own history —
`Test customer_id.not_null@2026-09-02 failed with status FAILED`), and closes
by itself when the next run of that check passes. Confirmed by pushing a single
passing run for a failing check and watching the open count drop 3 → 2 without
touching anything.

This is real lifecycle behaviour that we have not built and would not want to.
Two caveats:

* Every alert is titled `Failed DQ test`. The failing check's name is one click
  away under "Show history"; three simultaneous alerts on one table are
  indistinguishable in the list.
* Alerts carry no severity, so a `critical` not_null and a `minor` cosmetic rule
  produce identical rows.

## 7. Catalog fidelity — the mapping table, re-checked

| our concept | ODD concept | claimed | actual |
|---|---|---|---|
| dataset from contract schema | `DataEntity(TABLE, DataSet(field_list))` | full | **partly** — name, type, logical type, primary key and column description all land and render; `is_nullable` is sent as `false` and comes back `null` from `/api/datasets/{id}/structure`, with no UI affordance |
| derived check | `DataEntity(JOB, DataQualityTest)` | full | **confirmed**, once `expectation.category` is set (§4) |
| daily run | `DataEntity(JOB_RUN, DataQualityTestRun)` | partial | **confirmed partial** (§1) |
| contract domain / severity / origin | `tags` | full | **confirmed** — 8 tags, all ours, rendering as landing-page facets (`domain:sales`, `severity:critical 15`) |
| contract ownership | `owner` | full | **refuted** — `owner` is sent on all 25 catalog entities (`sales-ops`, `master-data`) and ODD keeps none of it: `ownership: null` on every entity, `/api/owners` empty, "Owners — Not created" in the UI (§8) |
| test ↔ dataset link | ODDRN in `dataset_list` | full | **confirmed** — the test page links to `sales_orders` and the dataset counts the test |
| contract metadata | `MetadataExtension` | full | **refuted** — strings, ints and bools only (§3) |
| table row count | `DataSet.rows_number` | not claimed | **available and now sent** (§1) |
| per-column null / unique / min / max | `DataSetFieldStat` | not claimed | **available and now sent** (§1) |
| `references:` foreign key | `ERDRelationship` | not claimed | **available and now sent**; stored correctly, unreadable on 0.29.0 (§7b) |
| per-run numeric facts | — | — | **absent**; `/ingestion/metrics` is a stub (§1) |

The dataset ODDRNs are still plain PostgreSQL ODDRNs, and the
merge-with-collector argument now holds — but only after a fix, and not on the
terms the README claimed. See §7b.

## 7b. Running odd-collector alongside us — what actually happens

The original claimed pushed tests "land on the same catalog objects ODD's own
collector discovers". That was never exercised. It is now, and the first run
produced **two of every table**:

```
//postgresql/host/ldp-pg/databases/erp/.../tables/sales_orders     collector
//postgresql/host/localhost:5442/databases/erp/.../sales_orders    us
```

An ODDRN is matched by string. The collector builds the host segment from its
bare `host:` config; `oddrn-generator` appended the port. Nothing merged, so
the collector's copy held the real schema and ours held all 23 tests. Fixed by
minting the host the way the collector does, with `ODD_PG_HOST` to override it
— which is the normal case, since the collector usually reaches the database
under a name our DSN does not share. After the fix the tests, the runs and the
column stats all land on the collector's object: 15 tests on `sales_orders`,
12 columns carrying stats.

Three further things that only show up with both running:

**Whoever writes last owns the shared fields.** Tags, metadata and the column
list are replaced wholesale by each push. Our tags vanish on the collector's
next cycle and its `oid` / `is_insertable_into` metadata vanishes on ours.

**Schema revisions thrash.** ODD versions a dataset whenever its structure
changes, and the two sources never agree: the contract governs 6 columns of 7,
the collector reports `int8` where `information_schema` says `bigint`, calls
every column nullable and finds no primary key. Four revisions appeared in half
an hour; at a 10-minute pull that is ~144 a day of pure noise. Two *identical*
pushes create no revision, so the churn is entirely the disagreement.

Matching the column list is not achievable — the type names and the nullability
come from different places — and ODD **rejects a `TABLE` entity with no
`dataset` block**, so annotating a dataset someone else owns is not expressible:

```
USR001 Data entity ... has TABLE type. One or several properties must be filled: [dataset]
```

Hence `push.py --no-datasets`: when a collector owns the tables, we stop
declaring them and push only tests, runs and column stats, which address the
dataset by ODDRN and never needed us to declare it. Verified: three pushes and
a collector cycle left the revision number unmoved.

**ERD relationships are write-only on 0.29.0.** The contract's `references:` is
published as a column-level `ERDRelationship` and stored correctly. Reading it
back is a 500:

```
IllegalStateException: Duplicate key .../tables/sales_orders/columns/customer_id
  at ReactiveRelationshipsRepositoryImpl.extractErdDetails:248
```

`dataset_field` rows are versioned, so one column ODDRN legitimately has
several rows; `extractErdDetails` collects them with `Collectors.toMap` and no
merge function. Any table whose structure has ever been versioned trips it.
Worth reporting upstream.

## 7c. The project itself, as of 2026-09-05

Relevant to adopting it, and not visible from the code we push.

* **`odd-platform` is active** — 1427 stars, commits the same day. Every one of
  the last 25 is `feat(search)` or `fix(search)`: unified cross-kind search,
  saved searches, query operators (quoted phrase, `-exclusion`, `or`), a
  favorites scope, a snapshotted popularity score. Postgres full-text is where
  the investment is going, and there is still no Elasticsearch in the compose.
* **`:latest` lags the work.** The image is `0.29.0`, built 2026-06-26. None of
  the July–September search commits are in it. Run a release behind, or build
  `main`.
* **Collectors moved.** `odd-collector` looks abandoned since 2023; the live
  home is the `odd-collectors` monorepo (SDK + AWS + Azure + GCP), 2026-01.
* **A collector needs a token.** `POST /ingestion/datasources` is guarded by a
  filter that is *always* active regardless of `auth.ingestion.filter.enabled`,
  so odd-collector fails at startup with the same 500 we hit until a collector
  is created via `POST /api/collectors` and its token put in the config.
* **The shipped ingestion surface is unauthenticated.** Issue #1740 (closed
  2026-06) states it plainly: `auth.ingestion.filter.enabled` defaults to
  false, and even when true only `/ingestion/entities` was covered —
  `/ingestion/alerts`, `/ingestion/metrics`,
  `/ingestion/entities/datasets/stats` and `/ingestion/entities/degs/children`
  had no filter at all. Anyone with network reach could inject alerts or
  overwrite stats. Close `/ingestion/**` at the network layer.
* **Maintenance model.** There is an `odd-team` repository describing an "AI
  maintainer team" coordinating audit and gap-closing across ODD repos, active
  the same week. The activity is real; the model is unusual enough to know
  about before depending on it.

## 8. Glossary, lineage and owners — the parts we did not build

The runbook asked whether these are worth not building. All three are empty on
the running instance, but for three different reasons, and only one of them is
ODD's doing.

**Ownership: we send it, ODD drops it.** `dataset_entity` and `check_entity`
both set `owner=contract.info.owner`, so `sales-ops` and `master-data` are on
all 25 catalog entities in `00_catalog.json`. In ODD: `ownership` is `null` on
every entity, `/api/owners` returns zero, and the dataset page offers
"+ Add Owner". Ownership in this build is a platform-side object with its own
`/api/owners` endpoint and a role per assignment — the ingestion `owner` string
is not a way in. So this is not "we chose not to build it"; it is a mapping
that looks like it works and does not. Closing it means teaching `push.py` a
second, non-ingestion API, which is a bigger commitment than it sounds: the
contract would become the source of truth for ODD's ownership graph too.

**Lineage: we send none, and it stays that way.** Upstream and downstream are
both `{"nodes": [], "edges": []}`. Expected — we push no `DataTransformer`, and
a contract does not describe a job, which is what `inputs`/`outputs` model.
Column-level lineage is not in ODD's ingestion model at all (`DataTransformer`
is dataset-level), and the issues that would add it — #1033 backend, #1067
frontend — have been open since 2022.

What the contract *does* state is a foreign key, and that is a different ODD
concept: `ENTITY_RELATIONSHIP` carrying a column-level `ERDRelationship`. We
publish it now (§7b), so `customer_id references customers.customer_id` is an
edge in the catalog rather than only a check. Cheapest edge available —
nothing to discover, the contract says it.

**Glossary: nothing on either side.** `/api/terms` is empty and no term is
attached to any dataset or column; the Dictionary is a feature we have not
used rather than one that failed. Same for namespaces — every entity reads
"not in any namespace". This is the clearest "worth not building" of the
three: a curated glossary is editorial work with a UI, ownership and search
behind it, and reproducing that is a project, not a module.

The honest summary: **glossary yes, lineage yes, ownership not yet.** Two of
the three are worth adopting untouched. The third is currently a silent hole —
the contract names an owner, the payload carries it, and no one in ODD can see
it.

## Practical notes from the first version — all held

* Run ODDRNs unique per execution (we use the run date). No collisions in 1035
  run entities.
* Check ODDRNs built from the full check id, not the last segment.
  `customer_id.unique` and `tax_id.unique` are two distinct entities in the
  catalog, as intended.
* Results outlive checks; `push.py` joins against live checks and reports
  retired ones instead of pushing orphan runs.

---

## Does the split still hold?

The original split:

* **ODD owns:** catalog objects, lineage, glossary, ownership, tags, alert
  lifecycle, per-run history, search.
* **The contract layer owns:** contracts as source of truth, check derivation,
  artifact emission, scoring window, severity-weighted score, SLA, trend.

**It holds, with two lines redrawn.**

**Severity moves fully to us.** The original had severity as a shared concern —
we declare it, ODD stores it as a tag. In practice ODD has its own severity
field that ignores ours and defaults to `MAJOR` on every test, and its alerts
ignore severity entirely. Two severities that never agree is worse than one, so
ODD's is a field to leave alone, and severity-based routing cannot be built on
ODD's alerts as they stand.

**Score is now contested, not vacant.** The original assumed ODD had no score
and ours would sit in the empty space. ODD has a score, on the dataset page, in
the place a user looks first. Ours is not visible there and cannot be — no
numeric run fact, no float metadata. So the split is not "we own scoring
because ODD has none"; it is "ODD shows an unweighted latest-run pass ratio and
we show a severity-weighted trend, and a user who sees both without being told
the difference will trust the wrong one." Either our UI has to own the score
narrative explicitly, or ODD's panel needs to be read as what it is.

**Ownership is on ODD's side of the line but nothing crosses it.** The split
listed ownership under "ODD owns" and that is still where it belongs — but it
is currently owned by nobody. We send `owner` and ODD discards it (§8), so the
line is drawn correctly and unimplemented, which is the one place this document
would previously have told a reader something false.

Everything else survived contact. Alert lifecycle is better than assumed and is
worth not building. Catalog, structure, tags, search and the ODDRN join key all
worked on the first push. Glossary and lineage are empty because we send
nothing, not because anything failed — and lineage is the obvious next thing to
send, since the contract's `references:` already states an edge ODD would draw.
The ingestion layer itself is a straight fit: 1060 entities, validated locally,
accepted unchanged — the only thing missing was a data source nobody had told
us to create.
