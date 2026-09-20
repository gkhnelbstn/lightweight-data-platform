# Working in this repo

Contract-driven data quality. Read `README.md` first for the why; this file is
the operating manual.

**Before changing anything that touches a dependency version, read
`docs/adr/`.** Every decision here has a record with an *On upgrade* section
saying what to check and what would let the decision be deleted. Several exist
only because an upstream project has a gap; closing one of those by deleting
our code is the best outcome available.

## Commands

`compose.yaml` is the platform; `compose.demo.yaml` is the sources the demo
needs. Anything touching SQL Server, MongoDB or Superset wants both:
`docker compose -f compose.yaml -f compose.demo.yaml --profile demo up -d`.

```bash
pip install -e ".[dev]"
pytest -q                                                  # 425 tests; the ones that need a database skip without one
docker compose exec app pytest -q tests                    # the same suite, from the app image -- see issue #7
python seed/seed.py                                        # rebuild the demo ERP data
python seed/seed.py --mutate                               # re-grade 20 customers in place
python core/runner.py --backfill-days 44                   # rebuild the history
python core/runner.py                                      # the daily unit (today)
# ...or the Run button on a contract in the Data Quality panel (#113)
python core/runner.py --odd-url http://odd-platform:8080   # ...and send it to ODD
python demo/medallion.py                                   # rebuild the demo warehouse
python demo/medallion.py --with-history                    # ...and give dim.customer a second version to keep
python integrations/odd/lineage.py --url http://odd-platform:8080   # declared lineage
python integrations/odd/classify.py --url http://odd-platform:8080   # PII tags
python integrations/odd/curate.py --url http://odd-platform:8080  # owner, docs, glossary
python integrations/odd/master_data.py --url http://odd-platform:8080  # golden records -> Master Data
uvicorn api.main:app --port 8077                           # UI + API
python core/mapping.py --check                             # validate the declared column mappings
python core/sync.py --check                                # validate the sync rules
python core/sync.py --apply                                # publication + subscription
python core/sync_mssql.py --interval 30                    # SQL Server CDC -> Postgres
python core/hub.py --init                                  # the two-way integration hub
python core/flow_jobs.py --check --contracts demo/integration  # refuse bad flows
python core/flow_jobs.py --apply --contracts demo/integration  # hub + SeaTunnel jobs (needs --profile flows)
python demo/integration/verify.py                          # drive the two-way demo and assert it
python demo/integration/outage.py before|after             # the restart drill, ADR 0023
python core/flow_jobs.py --apply --resnapshot ...          # start flows from scratch, knowingly
# ...or the same four buttons on ODD's Integration tab, which edits the flow files (ADR 0026)
```

Both databases come from the environment; nothing hardcodes a DSN:

```bash
export ERP_DSN=postgresql://postgres:postgres@localhost:5432/erp   # the data under test
export DQ_DSN=postgresql://postgres:postgres@localhost:5432/dq     # contracts, checks, results
export DQ_HOST=dq.local                                            # ODDRN identity, keep stable
```

## Releasing

Work goes to **`dev`**, not `main`. A branch opens its PR into `dev` and is
squash-merged, and **the PR title is a Conventional Commit** (`feat:`,
`fix:`, `docs:`, `ci:`...). With a squash merge the title is the commit, and
`pr-title.yml` fails a PR whose title has no type, since such a commit would
drop out of the release without a word.

A release is a PR from `dev` into `main`, **merged with a merge commit, never
squashed**. Squashing would fold every commit into one title, and release-please
reads the commits one by one. `.github/workflows/release-please.yml` then
opens a release PR on `main` (version bump, `CHANGELOG.md`); merging that tags
the release and calls `release.yml`, which publishes the images and attaches
the contracts. A GITHUB_TOKEN tag starts no workflow, which is why it is a
call and not a tag trigger. After a release, merge `main` back into `dev`, so
the version and the changelog are the same on both.

`Closes #n` in a PR into `dev` closes nothing until that commit reaches `main`,
which is the default branch.

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
* Alerting is one webhook and one POST per run (`core/alerts.py`), and what
  it reports is *newly* failing checks -- failing today, not failing on that
  contract's previous run. "Currently failing" is the same twenty every
  morning, which is how a channel gets muted. An `accepted` check never
  alerts; an `acknowledged` one still does when it newly fails. A backfill
  announces nothing: only the last day of a run is announced, because the
  other 44 already happened.
* The column profile (`core/profile.py`) is a measurement beside the checks,
  never among them: nothing in `column_profile` passes, fails, or reaches
  `core/scoring.py`. It is two numbers -- nulls and distincts -- taken in the
  same pass over the window the runner already built. Quantiles and histograms
  are deliberately absent: they need a sort over the whole table, which is a
  second daily scan of every table and therefore infrastructure (invariant 6).
* `datacontract test` gives `row_count` only for the checks it derives. A
  custom SQL rule has no denominator, so `core/runner.py` counts the table
  once per run; without it `fail_ratio` is always 0 and the volume half of the
  score does nothing.
* Logical replication fails **after** the initial copy, in a background worker
  that only writes to the server log — so a broken sync looks like a working
  one. `core/sync.py` checks all four preconditions up front; do not weaken
  that into a warning. `--status` is how you tell a dead worker from a quiet
  one -- and it reads `pg_subscription_rel` as well as the slot and the apply
  worker, because the *table sync* worker is a third one: a table stuck in the
  initial copy has an active slot, a live apply worker and zero lag while
  nothing replicates. `copy_data = true` is not idempotent, so `--apply`
  truncates the target first and `plan()` prints that truncate -- or `--check`
  describes something `--apply` does not do. Issue #35.
* The CDC reader is a poll, so something has to be running it: `sync-mssql`
  in `compose.demo.yaml`, which is the app image with a different command. A
  poll nobody started looks exactly like a poll with nothing to do -- every
  rule still reports itself configured while `last_synced` goes stale. It
  `extends` the app service rather than copying its environment, and resets
  the inherited port with `!reset` -- an empty list merges and the service
  then fails to bind 8077.
* SQL Server CDC is a *SQL Server Agent* feature. `sp_cdc_enable_table`
  succeeds with the Agent stopped and then nothing ever lands in the change
  table; `MSSQL_AGENT_ENABLED` in compose.yaml is why the demo works.
* Dropping a CDC-enabled table disables CDC for it, silently -- no error, and
  `sys.databases.is_cdc_enabled` stays 1 regardless, because that flag is
  database-level. `demo/mssql-seed.sql` drops and recreates every table on a
  reseed, so it `:r`s `deploy/mssql-cdc.sql` at the end to repair what it just
  broke; `core/sync_mssql.py`'s `capture_start_lsn` refuses a watermark older
  than the current capture instance rather than handing SQL Server an LSN it
  cannot answer for. See issue #17.
* ODD's metrics API is write-once per family: the second push of the same
  family, byte-identical, is a 500 (`MetricFamilyPojo.getId()` on null). Its
  published OpenAPI also disagrees with its own models — `metric_points` is a
  list, `timestamp` is epoch seconds — and the Overview card truncates values
  to integers. Do not try to publish the score there until that is fixed.
* The panel's `dq_*` query keys are read once at module load, because ODD's
  own Data Quality route rewrites the query string while it comes up and drops
  keys it does not know. `shared.tsx` also wraps `history.pushState` and
  `replaceState` once so the keys are merged back into whatever the page
  writes afterwards -- without it a link opens correctly and the address bar
  then stops matching the screen. It never touches their keys, and becomes a
  no-op the day they stop dropping ours. See issue #24.
* The contract panel lives inside ODD's Data Quality page and that is a fork
  of `odd-platform-ui`. Keep it the smallest fork that works: the SPA is one
  jar on the platform's classpath, so only the UI is rebuilt and the backend is
  untouched. The patch is two anchors in `DataQualityContent.tsx` and it
  **fails the build** when they move — do not soften that into a warning, and
  do not vendor their file. The integration's page is the one exception to
  "inside Data Quality": it has its own entry in ODD's menu
  (`deploy/odd-platform-integration-tab.mjs`, four anchors in `ToolbarTabs.tsx`
  and `App.tsx`, ADR 0022), and reads `GET /api/integration`, which asks
  SeaTunnel and the hub server-side. `INTEGRATION_DIR` says where the hub
  contracts are; the demo compose sets it to `demo/integration`. A log line
  there opens its record (`api/integration_detail.py`): each field with who
  set it, and the inbox history with the `outcome` `hub.merge` now returns --
  an `echo` of the hub's own delivery reads exactly like an edit otherwise.
* The UI's language is **ODD's own picker**, and Turkish is a carried patch
  (`deploy/odd-platform-tr.mjs`, ADR 0011). The panel shares ODD's i18n
  instance through `shared.tsx`'s `useT`, in its own `ldp` namespace, with
  English phrases as keys -- English needs no catalogue, and a missing
  Turkish key silently renders English, which is why
  `tests/test_panel_i18n.py` exists. Contract text is never translated:
  the rule form writes English descriptions into `*.odcs.yaml` (issue #44).
  A dynamic key (`t(c.dimension)`) is outside that test; add its entry to
  `deploy/odd-platform-ui/tr.json` by hand. Text the **server** writes once for
  everyone -- ODD link names, the alert message -- follows `LDP_LANGUAGE`
  (`core/language.py`) instead, since no viewer's picker can choose it; so
  `odd_links` keys a link by what it is (`checks`), never by its words.
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
* Lineage across a scheduler is declared (`derivedFrom`), never inferred —
  there is nothing to parse. An unresolvable reference must be reported, not
  dropped: a graph missing an edge still looks complete. See ADR 0014.
* `build_window` and `table_rows` connect to **the contract's own server**, not
  `ERP_DSN`. A warehouse contract lives in another database, and building its
  window against `erp` succeeds silently in the wrong place.
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
* A table keeps history because its contract says so, never because its
  columns look like it: `valid_from`/`valid_to`/`is_current` plus a
  `versionedBy` custom property naming the business key. The surrogate key is
  the `primaryKey` and repeats nothing, so inferring the key from the schema
  gets `dim.customer` right and the next table wrong. See `core/versions.py`.
* `dim.customer` is the one warehouse table `demo/medallion.py` does **not**
  rebuild -- it is SCD Type 2, so a change closes the current version and opens
  a new one. Its oldest version opens at `0001-01-01`, not at the source row's
  arrival date: an ERP moves `loaded_at` when it updates a row, so a re-graded
  customer would look newer than the orders they placed and 132 of them lost
  their country. `-infinity` is the natural sentinel and psycopg refuses to
  return it.
* Warehouse drops need `cascade`. The runner leaves an `asof_*` view on every
  table it checks, and a second `medallion.py` run fails on the dependency --
  the views are rebuilt by the next run anyway.
* A `syncTo` rule is a copy unless it says `mode: view`, which is
  `postgres_fdw` instead: no worker, no lag, and the target is only as
  available as the source. Three things that mode changes and ADR 0017
  records: the privacy boundary is a column-level grant on `sync_fdw` rather
  than the column's absence (so a classified column may not be listed at all,
  and `apply` verifies the grant with `has_column_privilege`), a SQL Server
  source is refused because `tds_fdw` is not in the image, and `--status` is
  `select 1 from the view` because there is no worker to ask about.
* `stg.orders` and `mart.revenue_daily` are **views**, not tables --
  `demo/medallion.py` builds them with `create view`. `raw` (a read that
  crossed a network), `fct.orders` (an as-of range join) and `dim.customer`
  (Type 2 history) stay physical. `drop_any` exists because `drop table` on a
  view and `drop view` on a table are both errors, and a warehouse built
  before ADR 0017 has tables where the script now wants views.
* **A `syncTo` rule is a replica; a `derivedFrom` column map is not.** The
  replica's shape comes from the *source* model -- `target_table_statement`
  builds it -- which is correct for a copy and a lie for a target with a schema
  of its own. `core/mapping.py` is the second shape: the **target's** contract
  states which of its columns come from where, as detail on the `derivedFrom`
  that lineage already reads (ADR 0014). A bare `derivedFrom: [id]` is
  unchanged and is never checked against a column map, so nothing that passed
  before can start failing.
* A column with no upstream is not a gap when the contract says so, and **two
  vocabularies say it because two different things do the filling**:
  `syncTo.generated` is the target database (a sequence, a default -- issue
  #45), `computedHere` is our own process (`dim.customer`'s surrogate key and
  its Type 2 `valid_from`/`is_current`, which `demo/medallion.py` computes and
  which exist in no source row). Kept apart on purpose: only the first
  survives a change of engine.
* **The privacy boundary changes shape again in a mapping.** In a `syncTo`
  column list it is physical -- a column left out has no column in the target.
  A mapping names the target column explicitly, so there is no such physics,
  and `core/mapping.py` checks instead that a classified source column does not
  land somewhere that fails to classify it. Same boundary, enforced rather than
  enforced-by-absence.
* `core/mapping.py` deliberately does **no type checking**. A mapping between
  two schemas changes types legitimately and often, so flagging every
  difference is noise; a real widening rule needs a lattice nobody has asked
  for. Issue #50 is the narrower case that *is* a bug.
* **An integration between two systems is its own file**, one per direction,
  in `contracts/flows/` (ADR 0019): `from`, `to`, `columns`, `values`,
  `match`, `linkBy`, `aggregates`, `filledByTarget`. The two systems' table contracts
  describe their tables and know nothing about it. The subdirectory is
  deliberate -- every contract reader and the CI lint glob
  `contracts/*.odcs.yaml` non-recursively, so a flow is never windowed,
  scored or catalogued. `derivedFrom` column detail is the *other* column
  map: lineage inside our own pipeline (`dim.customer`). Both share
  `core/mapping.py`'s refusals.
* **Two flows in opposite directions are a pair**, and each can pass alone
  while the pair corrupts data. `core/flows.py` checks the pair: the maps
  are inverses, every value map is one-to-one and its way back is its
  inverse, and the two sides use the same `match`. Both systems edit the
  same rows and the hub's latest commit decides (ADR 0021), which is why ADR
  0008's row-disjointness proof was replaced rather than built. The pair in `tests/test_flows.py` (a CRM and a billing
  system) is a stand-in for any two systems neither of which is ours:
  nothing serves it.
* A column coded differently on the two sides (`'Y'/'N'` against a `bit`)
  gets a **value map**, never an expression: a map can be inverted, and
  `upper(x)` or `qty * price` cannot, so a pair built on one would corrupt
  its own round trip. Expressions wait for a one-way case that needs them
  (issue #53). This is not type checking -- the map *is* the legitimate type
  difference.
* **Two-way integration runs through a hub** (ADR 0021): systems never write
  to each other. SeaTunnel lands every change append-only in
  `hub.<entity>_inbox` (before and after rows, commit time), a trigger runs
  `hub.merge` (`core/hub.sql`), and the golden record `hub.<entity>` goes
  back out to every system. The merge's order matters: an awaited value
  (`hub.expect`) is an echo first, an untouched field second, an agreement
  third, an edit of the current value fourth -- whatever the clocks say --
  and only then a conflict, where the later commit wins and `hub.conflict`
  keeps the loser. Its tests need a real Postgres (`DWH_PORT=5442` locally)
  and CI fails if they skip.
* **The first sync is not an edit.** An `INSERT` for a record the hub already
  has goes to the hub contract's `authority`, logged as `seed`. A deleted
  record leaves a `hub.tombstone`; a change for a record the hub does not have
  is checked for being our own echo *before* it may create anything -- a late
  delivery once resurrected a deleted customer. Two systems may not pair
  directly: `core/flows.py` refuses it.
* **A delivery writes what its revision changed, not the row.** Each golden
  record change sets `_changed` (`,name,` or `*`) and `_skip` (its origin),
  and the compiled `MERGE` is `CASE WHEN` changed per column -- the
  sink's generated one wrote every column and, once several flows filled one
  record, put stale values over fresh edits in a loop. Only `*` creates a
  row. A system that wins a field while another value is still on its way to
  it gets its own again (`back` in `hub.merge`); the first live run swapped a
  disputed name without it. #79.
* **`match` pins rows of a table with several per record** (an address per
  type): a filter on the way in, a constant on the way out, and both
  directions must agree. A field never set is filled without dispute, but an
  *emptied* one has a commit time and is not a gap -- confusing the two is the
  loop the live demo ran. A flow short of the hub's `required` fields owns
  only its part: its delete empties the part, and an empty part goes out as a
  delete of that row -- only when the revision *names* the field. A `*` with
  the part empty is the owner arriving first, and deleting there deletes the
  address on its way in (#80's live run).
* **Systems with different codes** (#80): the hub contract's `keys` names each
  system's code column, the record's key is the hub's own, and the codes are
  golden columns because the SeaTunnel delivery cannot look anything up. An
  unseen code is linked by the owner flow's `linkBy`: one match links, none
  creates, anything else (several, a match already coded, a null to match by,
  a part before its owner) waits in `hub.unmatched` for `hub.link`. A system
  receiving new records numbers them itself (`filledByTarget` on its key);
  the `MERGE` also matches by `linkBy` while the code is unknown, or a second
  delivery inserts the customer twice.
* **A SeaTunnel restart loses every job, and a fresh start is not a
  recovery.** It re-reads every table, the hub takes the re-read for a first
  sync, and an edit made during the outage goes to the authority while a
  delete is never seen -- both measured, both silent. `--apply` resumes each
  flow under its old id (`hub.job`) from its checkpoint, which the app reads
  from the shared `seatunnel-checkpoints` volume, and refuses when there is
  none or when SQL Server's CDC retention ran out meanwhile: SeaTunnel does
  both wrong without a word. `--resnapshot` is the knowing way through. ADR
  0023.
* **A table that changed under its flow is refused, never followed**
  (`core/flow_schema.py`): a mapped column missing from the live table, or not
  in its CDC capture instance. Compare captured columns **by id** -- a dropped
  and re-added column keeps its name in `cdc.captured_columns` under the old
  id and is never read. A dropped column empties nothing on the way in (NULL
  in both images is no edit) but fails the out-flow's `MERGE`.
* A value its value map does not know lands as NULL and cannot raise, so an
  in-flow carries `unmapped` -- the fields it happened to -- and the hub
  keeps its own value and logs `unmapped` in `hub.conflict`. A NULL from a map
  is not the system emptying the field; applying it would empty every other
  system. #84.
* **An awaited value the system already had is dropped**, not left for its
  hour: a delivery that changed nothing there never echoes, and the stale
  expectation swallowed a later edit to that exact value (an address added
  then removed, a name changed and changed back). "Already had" is the before
  image, or nothing for a new row. #84.
* **An aggregate is one way and is summed where its rows land** (ADR 0024,
  #81): `aggregates` turns `columns` into the group. The lines land in the
  hub's database (`flow.<id>_inbox` -> trigger -> `flow.<id>_lines`), every
  group a line change touches is **recomputed** into `flow.<id>`, and a second
  job, `<id>_out`, delivers it -- two jobs, both resumed like any other.
  Recompute rather than add and subtract: a moved line or a changed key is
  then just two groups. A flow back is refused: a total has no inverse.
* **A Postgres target gets a guarded MERGE** (`core/flow_sql.py`, #83):
  `WHEN MATCHED AND (t.cols) IS DISTINCT FROM (new)`. SQL Server records no
  change for an update that writes what a row holds; Postgres does, and a
  pair looped on it (ADR 0020). Its parameters are `CAST(? AS type)` from the
  target contract -- Postgres will not type a bare `?` in a `SELECT`. A
  Postgres *source* needs `REPLICA IDENTITY FULL`, no `timestamptz` column
  (SeaTunnel 2.3.13 refuses the whole table), and its slot: all three are
  checked before a job is submitted, never done for it.
* **A contract's foreign keys are ODD's ER diagram** (`relationships` on the
  property, ODCS 3.1, `integrations/odd/relationships.py`): published with the
  daily push as an `ENTITY_RELATIONSHIP`, both ends on the ODDRNs odd-collector
  minted. Not lineage -- a foreign key says which row a row belongs to, not
  which job made it. ODD 0.29.0 answers 500 for any table whose columns ever
  changed; the one-line fix is compiled in `deploy/Dockerfile.odd-platform`'s
  `api` stage (ADR 0011).
* The app image runs `uvicorn` without `--reload`, and `core/`, `api/` and
  the contracts are **mounted**. So an edit on the host is on disk inside the
  container and not in the running process: `docker compose restart app`, or
  the screen keeps answering with the code from before the edit. Measured
  twice, both times as a feature that "did not work".
* **A contract's checks can be run from the screen** (`api/runs.py`, #113):
  the same `core/runner.py` run the schedule makes, for today and one
  contract, started in a thread and watched by the panel. One at a time per
  contract -- a second run writes the same day twice. The state is in the API
  process, so a restart forgets a run in flight; what it had already written
  is in `check_results` either way.
* **A record created beside one that shares its `linkBy` value carries
  `_link = false`** (#120, ADR 0021): the out-flow's `MERGE` falls back to
  matching by `linkBy` while a system's code is unknown, and for such a record
  that fallback lands on the *other* record's row -- measured live, it
  overwrote a customer's name with another's and the systems echoed it back.
  `hub.linkable` sets the flag when the record is created; the delivery then
  inserts and the system numbers it itself. Its insert coming back is
  ambiguous for the same reason, so it waits for a person.
* **The Integration tab's one write is `hub.link`** (#111, ADR 0022): a held
  row is settled from the screen, and the hub's own refusal is the message. A
  hub card leads with its counts and one bar per hour of what arrived, and
  each log has a search box. Everything else on the tab stays read-only --
  what a field holds is the flows' to carry.
* **A flow is edited on the screen, and the file is what changes** (ADR 0026,
  `api/integration_edit.py`, #109): Check runs `core/flows.py`'s refusals
  against the edit without writing, Save round-trips the file with `ruamel` so
  its comments survive, and Apply/Stop/Restart call `core/flow_apply.py` --
  the same functions the CLI calls. A flow states two SeaTunnel settings and
  no more: `job: {checkpointInterval, rowsPerSecond}`. Not parallelism -- a
  second reader reorders one record's changes and the hub decides by commit
  order. The app service now needs `PG_USER`/`MSSQL_USER` and their passwords,
  since it is the process that fills them; `_fill` refuses an empty one,
  because SeaTunnel answers an empty username with "Unable to create a
  source". `stop()` waits for the job to be gone, or the apply after it reads
  "already running" and starts nothing.
* **A discussion about an asset is a Slack thread, and nothing else.** ODD's
  Discussions tab has one provider (`MessageProviderDto.SLACK`), so the
  channel list is empty until a workspace is connected:
  `deploy/slack-app-manifest.yaml` is the app, `ODD_SLACK_ENABLED` and
  `ODD_SLACK_TOKEN` in `.env` are the wiring, and the token is the
  workspace owner's to create. `DATACOLLABORATION_ENABLED: true` with an
  empty token refuses to start ODD ("Slack OAuth token is empty"), which is
  why both default to off. Replies arrive at `/api/slack/events`, so they
  need this platform reachable from Slack; posting does not.
* **ODD's Master Data page is the hub's golden record, one way** (ADR 0025,
  `integrations/odd/master_data.py`): a lookup table per hub entity plus
  `value_maps`, matched by key, classified columns left out. An edit made in
  ODD is overwritten by the next run -- a record changes in its system. ODD
  does not quote the names in its own `ALTER TABLE`, so a lookup column named
  `column` is a 500.
* `generated` in a `syncTo` rule is the target's half: columns that exist only
  in the replica and that the replica fills itself, so a sequence or a default
  there is what puts a value in them. They are never in `columns`, which is why
  neither reader needed changing -- but `target_table_statement` is `create
  table if not exists`, so a replica that predates the rule keeps its old shape
  and `generated_statements` is the `add column if not exists` that repairs it.
  That one is target-only; `_identity_statements` beside it runs on **both**
  ends, and a source that grew the replica's surrogate key would replicate it
  back. Issue #45.
* The `identity` widening in a `syncTo` rule is a *logical replication*
  requirement. The CDC reader has whole rows and does not need it; a mutable
  column in the identity breaks it there.

## Adding things

**A new rule someone can pick from the form:** one entry in `core/rules.py`
(builder, dimension, description, menu label) plus its parameters. The UI reads
the catalogue, so it needs no change. Add a case to `tests/test_rules.py`.
A kind that belongs to somebody else's package registers itself through the
`ldp.rules` entry point group instead — `core/rule_plugins.py`, ADR 0016. Only
the predicate shape is published: a whole-statement kind (`unique`,
`foreign_key`) is still a commit here.

**A new check kind:** it is datacontract-cli's, not ours — open an issue
there. What may need changing here is `core/scoring.py` (a dimension it does
not weight), `integrations/odd/from_datacontract.py` (the ODD expectation
category) and `core/sample.py` (how to show the rows it failed on). Add the
case to `tests/test_sample.py`.

**A database a script needs:** `core/bootstrap_db.py`, never
`deploy/db-init.sql` — that runs once, on an empty volume, and only knows the
platform's two databases. See ADR 0015.

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

## Agent skills

### Issue tracker

Issues live in GitHub Issues (`gh` CLI). See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: root `CONTEXT.md` (not yet created) + `docs/adr/`. See
`docs/agents/domain.md`.
