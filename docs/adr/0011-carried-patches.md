# 0011 — Patches we carry, and the conditions for deleting them

## Context

Carrying a patch to someone else's project is a debt. It is worth taking when
the alternative is working around a bug in our own code, and it is only worth
taking if it is also **sent upstream**, because that is what ends it.

## Decision

Every carried patch is a thin Dockerfile over the upstream image, never a fork
of the whole project, and every one has an open issue or PR attached.

### `deploy/Dockerfile.odd-collector` — Superset adapter

odd-collector's Superset adapter built dataset ODDRNs that did not match what
its own database adapters mint, so a Superset chart never linked to the table
behind it — which is the entire "which dashboards break" feature.

Sent as [odd-collectors#136](https://github.com/opendatadiscovery/odd-collectors/pull/136),
with MySQL added and the Postgres adaptee moved onto the same base, after being
verified against the live stack. Snowflake and BigQuery were left out
deliberately: their ODDRNs need account and project identifiers Superset does
not carry.

**Delete this image when #136 merges.**

### `deploy/Dockerfile.odd-platform` — the contract panel

A UI fork, not a patch. It has its own record: ADR 0009.

### `deploy/odd-platform-lineage-icon.mjs` — one icon per lineage node

Every node in a lineage graph drew the *root's* data source icon: a SQL Server
table feeding a Superset chart showed two SQL Server icons, and the same graph
rooted on the chart showed two Superset ones. The API is right either way —
`GET /api/dataentities/{id}/lineage/upstream` returns the correct `data_source`
per node — so this is the renderer.

`DatasourceLogo` draws an SVG-mode logo as `<filter id='logo'>` plus a
`<rect filter='url(#logo)'>`, and a lineage graph puts every node in one SVG
document. Duplicate ids resolve to the first in document order, so every rect
took the first node's image. The fix is one id per image.

Three anchored lines, in the same shape as the panel patch and failing the
build the same way. It rides in the fork that already exists rather than being
a second image, because the file it changes is compiled into the same SPA.
Reported as
[odd-platform#1898](https://github.com/opendatadiscovery/odd-platform/issues/1898).

**Delete the script and the `RUN` line that calls it when #1898 is fixed
upstream** — tracked in
[#19](https://github.com/gkhnelbstn/lightweight-data-platform/issues/19).

### `deploy/Dockerfile.odd-platform` — the ER diagram of a versioned table

ODD's Data Modelling > Relationships page, and the Relationships tab of every
dataset, answered **500** for a relationship whose table had ever changed
shape:

    java.lang.IllegalStateException: Duplicate key
      //postgresql/.../tables/sales_orders/columns/customer_id
      at ReactiveRelationshipsRepositoryImpl.extractErdDetails:248

ODD keeps a `dataset_field` row per structure version, so one column ODDRN
legitimately has several rows -- `customer_id` had three here, after the
column was made `NOT NULL`. `extractErdDetails` collects them with
`Collectors.toMap` and no merge function. The data is stored correctly; this
is the read path only, and any table whose columns were ever re-typed hits it.

The fix is a merge function that keeps the newest row, and it is one line. It
is the first patch here that is **not** in the UI, because the bug is not: the
`api` stage of `deploy/Dockerfile.odd-platform` compiles that single class
with `javac` against the platform image's own `/app/classes` and `/app/libs`
and the Lombok their build declares, then drops the result back into
`/app/classes`. Still no Gradle, no source tree of theirs vendored, and the
build fails when the anchor line moves.

Reported upstream as
[odd-platform#1880](https://github.com/opendatadiscovery/odd-platform/issues/1880)
before the patch existed, and the write-up there has the same reproduction.

**Delete the `api` stage and its `COPY` when a release carries the fix** --
tracked in
[#8](https://github.com/gkhnelbstn/lightweight-data-platform/issues/8).

### `deploy/odd-platform-tr.mjs` — Turkish in ODD's language picker

ODD already switches language, through a picker that lists `LANGUAGES_MAP`
and an `i18n.ts` that loads one catalogue per entry. It has no Turkish. The
panel lives in the same i18n instance, so adding Turkish *there* changes the
whole page, ODD's screens as well as ours. A switch of our own would have left
half the screen in the other language, which is what issue #44 set out to end.

The patch has three anchored lines:

* `i18n.ts`: the import and the `resources` entry;
* `constants.ts`: `LANGUAGES_MAP` and `LANG_TO_COUNTRY_CODE_MAP`.

The `Lang` type is derived from the map, so it needs no change. There is also
one file, `deploy/odd-platform-locale-tr.json`, which has all 739 of ODD's
keys. The registration has to be upstream's: `i18n.ts` rejects a stored
language that is not in its `resources`, so Turkish added later from the
panel would not survive a reload.

The panel's own words are a separate catalogue in a separate namespace,
`deploy/odd-platform-ui/tr.json`, which is ours to keep. Nothing in it goes
upstream. `tests/test_panel_i18n.py` fails when a literal key has no Turkish
entry, because i18next falls back to English silently.

**Delete the script, `odd-platform-locale-tr.json` and the Dockerfile lines
that use them when ODD ships a Turkish catalogue.** Offered upstream as
[odd-platform#1904](https://github.com/opendatadiscovery/odd-platform/pull/1904),
against `main` (746 keys, and the calendar's `BCP47` map as well).

### `deploy/Dockerfile.seatunnel` — SourceTimestamp on 2.3.13

The conflict rule for integration flows is "the latest edit wins", which needs
each change's commit time. SeaTunnel 2.3.13 only exposes when it *read* the
change (`EventTime`). The commit time is `SourceTimestamp`, added upstream in
apache/seatunnel#10667 after the release. ADR 0020 has the measurements.

This is the cheapest kind of carried patch there is: one upstream commit,
already merged, cherry-picked onto the release tag. The Dockerfile clones
`apache/seatunnel` at 2.3.13, applies it, and rebuilds only the two jars it
changes. It fails the build if the pick stops applying. The same branch is in
the fork, `gkhnelbstn/seatunnel`, as `ldp/2.3.13-source-timestamp`.

**Delete the `src` and `build` stages when a SeaTunnel release contains
#10667.** Nothing is reported, because nothing needs to be: it is already
merged.

### Worked around without a patch

odd-collector's `mssql` adapter enumerates every `BASE TABLE` it can see and
has no schema filter, so enabling CDC put nine of SQL Server's bookkeeping
tables into the catalogue beside five real ones. Rather than widen the carried
patch for what is a feature request, the collector now connects as a
least-privilege login: `information_schema` only shows what the user may see,
so **the permission grant is the filter**. It also stops a metadata collector
being sysadmin, which it should never have been.

## Reported and not patched

* [odd-platform#1880](https://github.com/opendatadiscovery/odd-platform/issues/1880)
  — ERD read fails with a 500 when two sources describe the same column
  differently, which is exactly the arrangement ODDRNs encourage. Reproduced
  deterministically: one `dataset_field` row → 200, two from disagreeing
  writers → 500.
* [odd-platform#1882](https://github.com/opendatadiscovery/odd-platform/issues/1882)
  — metric ingestion is write-once per family: the second write of the same
  family, byte-identical, is a 500 (`MetricFamilyPojo.getId()` on null). Filed
  with a narrowed three-line reproduction; the original is in
  `integrations/odd/entity_page.py`.
* [ibis#12108](https://github.com/ibis-project/ibis/issues/12108) — sqlglot 30
  renamed `Drop.this` to `Drop.tables` and ignores unknown kwargs, which breaks
  five ibis backends.
* [datacontract-cli#1592](https://github.com/datacontract/datacontract-cli/issues/1592),
  [#1593](https://github.com/datacontract/datacontract-cli/issues/1593) — the
  broken `--filter`, and per-rule scoping in ODCS.
* [odd-collectors#135](https://github.com/opendatadiscovery/odd-collectors/issues/135)
  — the issue behind the PR above.
* [odd-platform#1898](https://github.com/opendatadiscovery/odd-platform/issues/1898)
  — reported *and* now patched here; see above.

## On upgrade

Before bumping any of these, **check whether the patch is still needed**:

1. Build the stock upstream image and run the demo. If the behaviour the patch
   fixes is now correct, delete the Dockerfile, the compose `build:` block and
   the release-workflow step in the same commit.
2. If it is still needed, re-apply and re-verify against the live stack rather
   than trusting that it still applies cleanly.
3. Add the new finding here. A patch with no issue attached is a fork nobody
   asked for.
