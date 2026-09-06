# 0005 — Tests and runs on the table's ODDRN, and what ODD would not take

## Context

ODD matches everything by ODDRN, **as strings**. The failure mode is silent: an
ODDRN differing by a port or a host name forks the catalog in two — the
collector's copy holding the schema, ours holding the tests — and nothing
errors.

## Decision

Each check becomes a `DataQualityTest` and each run a `DataQualityTestRun`,
attached to the ODDRN **odd-collector's own adapter mints for that source**, so
the tests land on the same catalog object as the schema and inherit the
dashboards downstream of it.

Everything is validated against `odd-models` before sending. A validation
failure belongs in our process, not as a 400 from someone else's API.

Hard-won details, each of which was a silent failure first:

* The host segment carries **no port**. With one, every table forked into two
  catalog objects. `ODD_PG_HOST` exists to make our host match the collector's.
* ODDRNs are built from the **full check id**, not its last dotted segment:
  `customer_id.unique` and `tax_id.unique` both end in `unique` and would merge
  into one entity.
* A test is keyed on datacontract's stable `key`, never the per-run uuid, or
  every night creates a new catalog object.
* `expectation.category` is required in practice: an uncategorised test is
  ingested and then counts as **zero** on ODD's platform-wide Data Quality
  dashboard. Only timeliness maps to `FRESHNESS_ANOMALY`; the rest are
  `ASSERTION`.
* ODD drops float and list metadata silently. `contract_sla_min_score`
  disappeared until it was probed with typed values.
* `--no-datasets`: when a collector owns the tables, sending dataset entities
  too produced ~144 schema revisions a day.

## Consequences

* Anything that changes how an ODDRN is built is a catalog fork, not a rename.
  `tests/test_odd_mapping.py` exists almost entirely for this.
* The score cannot go to ODD. Its metrics API takes a family **once**; the
  second write of the same family — byte-identical metadata, new value — is a
  500 (`MetricFamilyPojo.getId()` on null), and a daily score is a second write
  by definition. Two further mismatches between ODD's published OpenAPI and its
  own models are recorded in `integrations/odd/entity_page.py`.

## On upgrade

Bumping ODD Platform:

1. Re-check that `POST /ingestion/entities` still accepts the payload
   `tests/test_odd_mapping.py` builds.
2. If the metrics NPE is fixed, `integrations/odd/entity_page.py` says exactly
   what a working push needs (`metric_points` is a list, `timestamp` is epoch
   seconds, the card truncates to an integer so publish a percentage).
3. Watch for a `DataTransformer` that carries column-level lineage. That is the
   one gap in this stack with no answer.

Bumping odd-collector: re-check the ODDRN it mints per source type against
`integrations/odd/from_datacontract.py::dataset_oddrn`.
