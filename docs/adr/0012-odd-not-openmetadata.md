# 0012 — OpenDataDiscovery, because it does not need Elasticsearch

*Numbered last, decided first. This is the choice everything else rests on.*

## Context

The catalog, search, glossary, lineage, alerting and schema discovery all had
to come from somewhere. The candidates were OpenMetadata, DataHub, Amundsen and
OpenDataDiscovery.

OpenMetadata is the most complete of them. It also **requires Elasticsearch or
OpenSearch** — `searchType: elasticsearch|opensearch`, not optional — and that
is a cost this project has already declined once: roughly **$90 a month on AWS**
for the search cluster alone, before anything else runs. A measurement on a
laptop put OpenMetadata at 8–16 GB of RAM in practice, not the ~2 GB the docs
suggest.

DataHub carries the same shape of dependency. Amundsen is quiet.

## Decision

**OpenDataDiscovery Platform**, whose search is PostgreSQL full-text. The whole
stack is then PostgreSQL, which is also the sixth invariant of this repository:
no new infrastructure without a row count to justify it.

Measured, not read off a page: ODD Platform idles at ~924 MB (627 MB when
capped at 1 GB, and it still starts), its Postgres at 147 MB, the collector at
64 MB, the optional profiler at 451 MB. **4 vCPU / 8 GiB / 100 GB is the target
box.** Its database was 12 MB for two tables, 23 checks and 45 days of runs —
it stores metadata, so the size of the source data does not enter into it.

## Consequences

* ODD is thinner than OpenMetadata, and the gaps it leaves are why most of the
  other records in this directory exist. That trade was made knowingly.
* **Column-level lineage is the one gap with no answer.** Checked against
  0.29.0 rather than remembered: `DataTransformer` carries `inputs`, `outputs`,
  `sql` and `source_code_url` — lists of dataset ODDRNs — and the lineage the
  API returns is `DataEntityLineageEdge {source_id, target_id}`. Entity to
  entity, with no column anywhere in either. So it cannot be closed by writing
  more code here; the model has no field to put it in. Table-level lineage, the
  BI chain ("which dashboards break") and column-level *relationships* (foreign
  keys, as `ERDRelationship`) all work.
* ODD Platform is maintained by a bot plus roughly one human: 63 of its last 86
  commits are `odd-contributor[bot]`. `docs/stack-choices.md` has the figures.
  This is the real risk of the choice, and it is larger than the Elasticsearch
  saving is comfortable.
* Two of our own dependencies, `oddrn-generator` (5★) and `odd-models` (3★),
  have not been released since 2024. They are pinned.

## On upgrade

This decision gets revisited when — and only when — one of these becomes true:

1. **OpenMetadata drops the search-engine requirement.** Not "has a smaller
   footprint", not "looks better": the requirement disappearing is the
   condition, because the $90 is the reason. Check
   `searchType` in their configuration.
2. **ODD stops being maintained.** Watch the commit mix. A bot-only quarter is
   the signal.
3. **Column-level lineage becomes load-bearing** for a real decision here,
   rather than a thing that would be nice to have.

Do not revisit it because a demo looked nicer. The cost that drove it is a
monthly bill, and it does not go away.
