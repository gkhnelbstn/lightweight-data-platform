# 0014 — Lineage is declared by the contract, because nothing can infer it

## Context

The question this whole repository is pointed at is "a check failed — what
breaks?". Answering it needs a path from the failing table to a dashboard.

A collector can find some of that path on its own: a view definition, a foreign
key, and — through the Superset adapter — which chart reads which table. What
it cannot find is the middle, and the middle is where most warehouses live: a
Prefect flow, an Airflow DAG or a Python script that selects out of one
database and inserts into another. That transformation leaves **no trace in
either engine**. There is nothing to parse.

`demo/medallion.py` is deliberately built that way rather than as a set of
views, because that is the honest shape of the problem: Postgres cannot query
another database, and half the ERP is SQL Server.

## Decision

The contract declares it.

```yaml
customProperties:
  - property: derivedFrom
    value: [erp.sales_orders]      # contract ids, or raw ODDRNs
  - property: derivedBy
    value: "select ... from raw.orders where customer_id is not null"
```

`integrations/odd/lineage.py` turns each declaring contract into a
`DataTransformer` whose inputs are the upstream datasets and whose output is
its own table. ODD draws the chain, and because the quality tests already hang
off the same table ODDRNs, a failing check has a downstream that reaches the
Superset chart at the end of it.

Two choices inside that:

* **A reference is a contract id by preference**, an ODDRN only as an escape
  hatch. A contract id survives a host, schema or database change; an ODDRN
  written into a yaml is a copy of a fact that lives somewhere else, and it
  goes stale silently.
* **An unresolvable reference is reported, not dropped.** A graph that quietly
  loses an edge still looks complete, and someone then reads a blast radius
  that is smaller than the real one. That is worse than an error.

## Consequences

* Lineage is only as complete as the contracts. A table nobody wrote a contract
  for is a hole in the graph — which is the same trade as ADR 0013 and has the
  same answer: put it under contract.
* This is **dataset-level**, and dataset-level is all "which dashboards break"
  needs. Column-level is a different question and ODD cannot represent it at
  all; see ADR 0012.
* The declaration can drift from the code that actually does the load. Nothing
  here can detect that — `derivedBy` is documentation, not the executed
  statement. If the loader is ever rewritten as dbt, its own adapter would be
  the better source and this module should shrink.

## On upgrade

* **odd-collector:** it has a `dbt` adapter and, at 0.29.0, no Prefect one. If
  the transformations move to dbt, prefer that adapter over these declarations
  — it reads the real project rather than a statement of intent.
* **ODD:** `DataTransformer` is `inputs` / `outputs` / `sql` /
  `source_code_url`. If a column-level field ever appears there, this is the
  module that would carry it.
* Verify a change with the walk rather than the picture: ODD's lineage canvas
  does not fit a long chain in one view, and the graph is easier to read from
  `/api/dataentities/{id}/lineage/downstream?lineage_depth=10`.
