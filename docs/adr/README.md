# Architecture decisions

Every decision here was made against a running stack, and most of them were
made *after* the obvious thing failed. That is why they are written down: the
reasoning is worth more than the conclusion, because the conclusion changes
when a dependency does.

Each record has the same four parts, and the fourth is the one to read first
when a version bump is on the table:

* **Context** — what was true when the decision was made.
* **Decision** — what was chosen.
* **Consequences** — what it costs, honestly.
* **On upgrade** — what to check, what breaks, and what would let us delete
  the decision entirely.

Several of these exist only because an upstream project has a gap. Those say
so, and say what to remove when it closes. **Deleting one of these records
because upstream fixed something is a good outcome, not a loss.**

Numbers are the order they were written, not the order they were decided or
their importance. **[0012](0012-odd-not-openmetadata.md) is the one everything
else rests on** — it is last because it was written last.

**[0015](0015-the-boundary-what-is-ours.md) is the one to read first on an
upgrade** — it is the whole register of additions in one table, each with the
upstream feature that would let it be deleted.

| # | Decision | Retire when |
|---|---|---|
| [0012](0012-odd-not-openmetadata.md) | ODD Platform, because it needs no Elasticsearch | OpenMetadata drops the search-engine requirement |
| [0001](0001-odcs-and-datacontract-cli.md) | ODCS contracts, checks run by datacontract-cli | never — this is the foundation |
| [0002](0002-the-daily-window.md) | The window is a database object, per engine | `datacontract test --filter` works, or ODCS scopes a rule |
| [0003](0003-scoring.md) | Dimension-weighted score; an outage is not bad data | ODD's own score becomes weightable |
| [0004](0004-results-store.md) | Results as a partitioned time series in PostgreSQL | ODD stores run history with numbers in it |
| [0005](0005-push-to-odd.md) | Tests and runs pushed onto the table's ODDRN | — |
| [0006](0006-failing-rows.md) | Failing rows by rewriting the check's own SQL | datacontract returns failed samples for SQL rules |
| [0007](0007-pii-classification.md) | Presidio, with the finding written into the contract | ODD grows a classification model |
| [0008](0008-replication.md) | Replication is the database's own, driven by the contract | — |
| [0009](0009-fork-odd-platform-ui.md) | Fork ODD's UI to host the contract panel | ODD grows an extension point |
| [0010](0010-rule-vocabulary-and-the-token.md) | Rules from a fixed vocabulary; the token guards only raw SQL | a real identity provider is in front |
| [0011](0011-carried-patches.md) | Patches we carry for upstream projects | each PR merges |
| [0013](0013-fill-the-catalogue-from-the-contract.md) | The catalogue is filled from the contract | ODD imports ODCS directly |
| [0014](0014-declared-lineage.md) | Lineage is declared, because nothing can infer it | the loads move to dbt, whose adapter reads the real project |
| [0015](0015-the-boundary-what-is-ours.md) | The register of what is ours, and the file layout that follows it | every row above it is retired |
| [0016](0016-extension-points.md) | Rule kinds take plugins; engines and integrations stay code | datacontract-cli grows pluggable check kinds |
| [0017](0017-a-view-instead-of-a-copy.md) | A view where a copy buys nothing: `stg`, `mart`, and `syncTo mode: view` | dbt owns the warehouse materialisation |
| [0018](0018-debezium-without-kafka.md) | Debezium runs without Kafka now, and the CDC reader still stays ours | Debezium grows a native row filter |
| [0019](0019-an-integration-is-its-own-file.md) | An integration between two systems is its own file, not a line in either system's contract | ODCS grows a flow or port model |
| [0020](0020-seatunnel-carries-the-flows.md) | *Proposed:* Apache SeaTunnel can carry the flows; the pair's refusals and conflicts stay ours | SeaTunnel's image drops opengauss-jdbc; its sink gains a guarded upsert |
| [0021](0021-a-hub-decides-two-way-changes.md) | Two-way changes meet in a hub, and the latest commit wins | a tool keeps per-row sync state for SQL Server |
| [0022](0022-the-integration-has-its-own-tab.md) | The integration has a tab of its own in ODD's menu | ODD grows an extension point |
| [0023](0023-a-flow-resumes-or-refuses.md) | A flow resumes from its checkpoint, or refuses to start | SeaTunnel keeps jobs across a restart and refuses a restore it cannot honour |
| [0024](0024-aggregates-are-summed-where-they-land.md) | An aggregate is summed where its rows land, and goes one way | SeaTunnel's SQL gains aggregation |
| [0025](0025-the-golden-record-is-odds-master-data.md) | The golden record is ODD's master data, published one way | ODD gains read-only lookup tables |
| [0026](0026-a-flow-is-configured-on-the-screen.md) | A flow is configured on the screen, and states two things about how it runs | SeaTunnel gains ordered parallel readers per key |
| [0027](0027-an-api-source-is-a-poll.md) | An API source is a poll, one way, and it must say when a record changed | SeaTunnel grows an incremental API source, or an API is expected to be written back |
