# ODD Platform vs the contract layer — what maps, what does not

Established by mapping this spike onto the ODD specification and validating 1060
entities against `odd-models`. No guesswork: every statement below is either the
shape of `odd_models.models` or the behaviour of `odd_dbt`'s own mapper.

## What ODD models natively

| our concept | ODD concept | fidelity |
|---|---|---|
| dataset from contract schema | `DataEntity(type=TABLE, dataset=DataSet(field_list))` | full — columns, types, nullability, PK |
| derived check | `DataEntity(type=JOB, data_quality_test=DataQualityTest)` | full — suite_name, dataset_list, expectation |
| daily run | `DataEntity(type=JOB_RUN, data_quality_test_run=DataQualityTestRun)` | partial — see below |
| contract ownership / domain | `owner`, `tags` | full |
| test ↔ dataset link | ODDRN in `dataset_list` | full, and merges with `odd-collector`'s own Postgres ODDRNs |

## What has no home in the ODD run model

`DataQualityTestRun` is `{data_quality_test_oddrn, start_time, end_time, status,
status_reason}`. That is all.

1. **No row counters.** There is no `failed_rows` / `total_rows` / `fail_ratio`.
   The volume signal — the thing that separates a typo from an outage — can only
   be shipped as free text in `status_reason` (we send
   `"10/64 rows failed (15.62%) severity=major"`). ODD cannot aggregate it,
   chart it, or threshold on it, because to ODD it is a string.
2. **No severity on the run or the test.** Severity is an operator setting inside
   the platform, not part of the ingested payload. Our contract declares it;
   ODD will not read it. We ship it as a tag (`severity:critical`) and in
   `metadata`, which makes it searchable but not actionable.
3. **No score, no SLA evaluation.** ODD counts passing and failing tests. The
   severity-weighted score and `sla.min_score` from the contract have nowhere to
   go. Pushed as dataset metadata (`contract_sla_min_score`), i.e. decoration.
4. **No scoring window.** Our incremental-vs-cumulative distinction — the single
   thing that decided whether the trend was legible — is not expressible.
   ODD stores what it is sent.
5. **No contract.** There is no contract entity, no versioning of intent, and no
   link from "this test exists" to "because the contract says so". We encode it
   as `metadata.derived_from_contract` and a `contract:<id>` tag.

## Consequence for the build

ODD is a good **catalog, lineage, glossary and run-history** substrate, and
adopting it removes most of what a home-grown platform would spend a year on.
It is not a data-quality *scoring* system, and it has no opinion about contracts.

The division that follows:

* **ODD owns:** catalog objects, lineage, glossary, ownership, tags, alert
  lifecycle, per-run history and the search UI over all of it.
* **The contract layer owns:** contracts as source of truth, check derivation,
  artifact emission (dbt/GX/SQL), scoring window, severity-weighted score, SLA
  evaluation, trend.

That is a real split, not a fudge: the two stores answer different questions and
the ODDRN is the join key between them. The cost is that the score lives outside
ODD, so a user gets "which tests failed" in ODD and "how good is this contract
doing over time" in ours — until ODD grows numeric run facts.

## Practical notes found while mapping

* Run ODDRNs must be unique per execution; `odd-dbt` uses the dbt invocation id,
  we use the run date (one run per check per day by design).
* Check ODDRNs must be built from the full check id, not its last dotted segment
  — `customer_id.unique` and `tax_id.unique` both end in `unique` and would
  silently merge into one entity in the catalog.
* Results outlive checks. A rule deleted from a contract keeps its history in our
  store; pushing those runs would reference a `JOB` that was never ingested, so
  `push.py` joins against live checks and reports the retired ones instead.
