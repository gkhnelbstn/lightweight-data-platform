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
