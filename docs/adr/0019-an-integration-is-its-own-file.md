# 0019 — An integration between two systems is its own file

## Context

Issue #53's case is two products neither of which is ours, integrated in
both directions. The example given was an ERP and an accounting package on
SQL Server; the same shape is a CRM and a billing system, or any two systems
of record. Their schemas differ, their codes differ (`ACTIVE 'Y'/'N'` against
`IsActive bit`), and both sides edit the same customer.

The first shape put each direction in the **target's table contract**, as
column detail on `derivedFrom`, with a `masteredHere` list beside it (#55, #64,
#68). It worked, and the refusals were right. But it made one system's
contract say "this table is derived from the other's", and the other say the
reverse:

* **A vendor's contract carried our integration policy.** A system's table
  contract should describe that table: its columns, its classification, its
  checks. That someone copies it into another system, and which side wins a
  conflict, is a fact about the integration, owned by whoever runs it. It is
  not a fact about the table. Invariant 1 says the contract is the source of truth; it
  does not say every truth belongs in the same contract.
* **`derivedFrom` means lineage.** On `dim.customer` it says how our own
  process built a table. On a two-way pair it said something else, "rows
  move here from there", in the same words, and a cycle of `derivedFrom` edges is what
  ODD would have drawn from it.

Decided in conversation: treat the two directions as **two separate
integration contracts**.

## Decision

**An integration flow is its own file, one per direction, in
`contracts/flows/`.** `core/flows.py` reads it:

```yaml
# contracts/flows/crm_to_billing.yaml
id: crm_to_billing
from: crm.account                 # the source table's contract
to: billing.customer              # the target table's contract
columns: {CustomerCode: ACCOUNT_CODE, Name: TITLE, IsActive: ACTIVE}  # target: source
values:                           # target column: {arrives: lands}
  IsActive: {Y: true, N: false}
winsOnConflict: [CustomerCode, Name]  # target columns where this side wins
filledByTarget: [CreatedAt]       # the target's own default fills these
```

The table contracts it names describe their tables and nothing else.

*Since ADR 0021* a two-way flow's other end is a hub, never the other system,
and `winsOnConflict` is gone: the hub's latest commit decides. A flow into or
out of a table with several rows per record also says `match` (#79), e.g.
`match: {ADDR_TYPE: INV}`. `demo/integration/flows/` has the current shape.

**Not ODCS, and not in the ODCS glob.** A flow has no schema, no server and no
checks; it is a rule about two contracts, the way `syncTo` is. Every contract
reader here uses `contracts/*.odcs.yaml` non-recursively: the runner, the API,
curation, lineage, and the CI lint step. So a subdirectory keeps a flow from
being windowed, scored or catalogued as if it were a table, and no reader had
to change. It still ships with the contracts (`release.yml` tars
`contracts/`).

**Two column maps, drawn apart on purpose:**

| | `derivedFrom` column detail | integration flow |
|---|---|---|
| says | how our pipeline built this table | rows move between two systems |
| lives in | the target's own contract | its own file |
| example | `dim.customer` from `erp.customers` | CRM → billing |
| moves rows | no, our process already did | yes, once an executor exists |

A column map breaks the same ways in both, so the refusals are shared:
`core/mapping.py`'s `column_problems` and `gap_problems`. The first covers an
undeclared column, an unfilled key or required column, a column filled twice
and a classified value that lands unclassified. The second is also run across
every flow into the same target, so two flows filling one column are caught.

**The pair is two flows in opposite directions.** Its refusals move here
unchanged from `core/two_way.py`, which this replaces:

* the maps are inverses;
* every value map is one-to-one and its way back is its inverse;
* every column is won by exactly one flow.

`winsOnConflict` replaces `masteredHere`, which only the test fixture ever
used. Winning is per target column of one flow, so it is a fact the
integration states, not the table.

**Value maps, not expressions.** A map can be inverted, and `upper(x)` or
`qty * price` cannot, so a two-way pair built on an expression would corrupt
its own round trip. Expressions wait for a one-way case that needs them.

## Consequences

* `python core/mapping.py --check` checks the flows as well, so CI refuses a
  bad flow the same way it refuses a bad mapping.
* The pair is still hypothetical -- a CRM and a billing system standing in
  for any two systems -- and lives in `tests/test_flows.py`. `contracts/flows/` does not exist until a real flow
  does; `flows.load()` returns nothing without it.
* Nothing executes a flow yet. The executor reads the landing log (#56,
  filtered on write) and applies one flow per target; that is the next record.
* A lineage edge for a flow, most likely an ODD `DataTransformer` between the
  two datasets rather than a `derivedFrom` cycle, is also not built yet.
  It is now: [ADR 0029](0029-a-flow-is-a-job-in-the-catalogue.md).

## On upgrade

* **ODCS grows a flow, port or data-product model.** ODCS 3 describes one
  dataset per contract; the Open Data Product Standard has input and output
  ports. If either can state a column map between two contracts, move the
  flow into it and delete this format. The refusals stay ours either way.
