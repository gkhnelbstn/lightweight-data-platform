# 0019 — An integration between two systems is its own file

## Context

Issue #53's case is two products on SQL Server, Siber (ERP) and Zirve
(accounting), integrated in both directions. Their schemas differ, their codes
differ (`AKTIF 'E'/'H'` against `Durum bit`), and both sides edit the same
customer card.

The first shape put each direction in the **target's table contract**, as
column detail on `derivedFrom`, with a `masteredHere` list beside it (#55, #64,
#68). It worked, and the refusals were right. But it made Siber's contract say
"this table is derived from Zirve's", and Zirve's say the reverse:

* **A vendor's contract carried our integration policy.** Siber's table
  contract should describe Siber's table: its columns, its classification, its
  checks. That someone copies it into Zirve, and which side wins a conflict, is
  a fact about the integration, owned by whoever runs it. It is not a fact
  about the table. Invariant 1 says the contract is the source of truth; it
  does not say every truth belongs in the same contract.
* **`derivedFrom` means lineage.** On `dim.customer` it says how our own
  process built a table. On Siber↔Zirve it said something else, "rows move here
  from there", in the same words, and a cycle of `derivedFrom` edges is what
  ODD would have drawn from it.

Decided in conversation: treat the two directions as **two separate
integration contracts**.

## Decision

**An integration flow is its own file, one per direction, in
`contracts/flows/`.** `core/flows.py` reads it:

```yaml
# contracts/flows/siber_to_zirve.yaml
id: siber_to_zirve
from: siber.cari                  # the source table's contract
to: zirve.hesap                   # the target table's contract
columns: {HesapKodu: CARI_KOD, Ad: UNVAN, Durum: AKTIF}   # target: source
values:                           # target column: {arrives: lands}
  Durum: {E: true, H: false}
winsOnConflict: [HesapKodu, Ad]   # target columns where this side wins
filledByTarget: [KayitTarihi]     # the target's own default fills these
```

The table contracts it names describe their tables and nothing else.

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
| example | `dim.customer` from `erp.customers` | Siber → Zirve |
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
* The Siber/Zirve pair is still hypothetical and lives in
  `tests/test_flows.py`. `contracts/flows/` does not exist until a real flow
  does; `flows.load()` returns nothing without it.
* Nothing executes a flow yet. The executor reads the landing log (#56,
  filtered on write) and applies one flow per target; that is the next record.
* A lineage edge for a flow, most likely an ODD `DataTransformer` between the
  two datasets rather than a `derivedFrom` cycle, is also not built yet.

## On upgrade

* **ODCS grows a flow, port or data-product model.** ODCS 3 describes one
  dataset per contract; the Open Data Product Standard has input and output
  ports. If either can state a column map between two contracts, move the
  flow into it and delete this format. The refusals stay ours either way.
