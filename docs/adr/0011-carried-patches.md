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

## On upgrade

Before bumping any of these, **check whether the patch is still needed**:

1. Build the stock upstream image and run the demo. If the behaviour the patch
   fixes is now correct, delete the Dockerfile, the compose `build:` block and
   the release-workflow step in the same commit.
2. If it is still needed, re-apply and re-verify against the live stack rather
   than trusting that it still applies cleanly.
3. Add the new finding here. A patch with no issue attached is a fork nobody
   asked for.
