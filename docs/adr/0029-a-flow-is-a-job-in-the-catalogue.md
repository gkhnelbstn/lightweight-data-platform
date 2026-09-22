# 0029. A flow is a job in the catalogue, between tables its contracts describe

Date: 2026-09-22

## Status

Accepted. Builds what ADR 0019 left as "not built yet": a lineage edge for a
flow.

## Context

Siber's test server (172.20.16.78) feeds three hubs by SQL Server CDC. The
flows ran in SeaTunnel and the Integration tab showed them, but in ODD:

* the test server was not a data source. No collector reads it, and adding
  one would catalogue its 3 700 tables a second time, since they already come
  from production.
* the hub was not a data source either. It is a database in the platform's
  own Postgres (`db`, database `hub`), and nothing said so. The Integration
  tab named it "hub" and no more.
* nothing joined the two. ODD collects what a database can describe, and a
  SeaTunnel job leaves no trace in either database.

## Decision

**Each flow is a `DataTransformer`** (`integrations/odd/flow_lineage.py`),
from the table it reads to the table it writes, on the ODDRNs odd-collector
mints for them. It is published under our own data source, like declared
lineage (ADR 0014). A two-way pair is two jobs, so the graph shows the cycle
it is.

**A table a flow touches is published from its contract when no collector
owns its server.** Each such server becomes a data source named by the
contract's server key. Its description says where the server is: engine,
host, port, database, and for the hub its schema and how a change becomes a
golden record. The columns are the ones the contract declares, which for a
system are the ones the flows carry. That is the rule from ADR 0013 (fill the
catalogue from the contract) applied to a table nobody collects.

**A collector's source is left to the collector.** ODD gives a data source
registered through its API a token, and one a collector registered none. A
collector's table has every column, and publishing ours over it would replace
them with the mapped ones on every run of either. Each replacement is a new
structure version. On a collector's source only the job is published.

The runner publishes this after its ODD push, as it does master data
(ADR 0025), so a flow added since the last run is on the graph after the next
one. The Integration tab says the same thing in its own place: each hub and
each system shows where it is (`flow_schema.where`).

## Consequences

* Siber's test server and the hub are in ODD's data sources and Directory.
  The lineage reads `sbr_firma` → `siber_test_firma_to_hub` → `hub.firma`.
* An API source has no table, so its flow is not placed. The run names it
  rather than dropping it (`! loyalty_to_hub: no table for loyalty.member`),
  for the reason ADR 0014 gives: a graph missing an edge still looks
  complete.
* A data source description is `varchar(255)` in ODD, and a longer one is a
  500. The flows are counted there, not listed; each one is a job on the
  graph anyway.
* A table published here holds only the mapped columns. If a collector later
  catalogues that server, its data source keeps the token we registered, so
  this module keeps publishing too. Delete that data source first, and the
  collector registers it again as its own.

## On upgrade

If SeaTunnel or ODD grows lineage for SeaTunnel jobs (an OpenLineage emitter,
say), publish the jobs from there and keep only the tables nobody collects.
If ODD stops marking API-registered data sources with a token, the ownership
check needs another signal: it would otherwise overwrite a collector's
tables.
