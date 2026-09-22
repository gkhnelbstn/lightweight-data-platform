# 0030. A contract's page shows the agreement, each promise beside what was measured

Date: 2026-09-22

## Status

Accepted. Covers most of issue #144.

## Context

The contract panel showed columns, checks, a rule form and an audit trail.
Nothing on it said what makes the file a contract: who owns it, what the data
is for and may be used for, how often it is checked, what was promised, and
what sits next to the table. OpenMetadata's contract page, which the Siber
migration replaced, answered all of these. Here the answers were in the YAML,
and for the eleven Siber contracts they were in custom properties
(`legacySla`, `legacyTerms`, `steward`), because the migration had nowhere
better to put them.

ODCS 3.1 has a field for most of them: `team`, `description`, `slaProperties`
(with `scheduler`/`schedule`), `roles` and `support`.

## Decision

**The panel opens on an Overview tab** (`deploy/odd-platform-ui/ContractOverview.tsx`),
read from `agreement` in `GET /api/contracts/{id}` (`api/contract_agreement.py`):

* **Who**: the owner (`tenant`, as `curate.py` already treats it) and the team.
* **What for**: purpose, usage, limitations, use cases, domain and tags.
* **Which rules**: the rules written into the contract with their latest
  result, grouped by dimension. The checks datacontract derives from the
  schema are one line, and the contract's `semantics` are its business rules.
* **How often, and what was promised**: every `slaProperties` entry, and
  beside it what the runs measured, where something measures it. The score
  floor is shown against the latest score, the frequency against the last
  run, completeness against the completeness checks, and availability against
  the runs the source answered. Latency and time of availability are marked
  as not measured here, rather than filled with a number from somewhere else.
* **Terms**: roles and their access, classification, personal data,
  breaking-change and deprecation policy, and support.
* **Where, and what is related**: the server and table; foreign keys in both
  directions, each linked to the contract that covers the other table; and
  whatever ODD's catalogue has one step up or down the table's lineage.
  Lineage is fetched with ODD's own API from the browser, because the panel is
  part of ODD's page and so shares its session. A table with hundreds of views
  on it is listed with a search, not drawn.

**Nothing on the tab edits.** The contract stays the only source of truth
(invariant 1). Editing these fields belongs to the same round-trip the rule
form uses, and waits for someone to ask for it.

**ODCS fields, not custom ones.** Terms with no ODCS field keep plain custom
property names: `dataClassification`, `privacy`, `breakingChangePolicy`,
`deprecationPolicy`, `useCases` and `semantics`.

## Consequences

* A contract that states nothing shows that it states nothing. The demo
  contracts have a purpose and a frequency, and most of them have no usage.
* The Siber contracts were moved onto these fields outside this repository,
  since they are not in it. All eleven pass `datacontract lint`.
* "How often" is a promise plus the run history. Nothing in this platform
  schedules the runner (see README). A contract that promises daily and was
  run once shows exactly that.
* Issue #144 also asked `tests/test_contracts.py` to require purpose, usage and
  an SLA of every contract. That waits until the demo contracts state their
  usage.

## On upgrade

If ODD gains a contract model, or imports ODCS, move this tab there. The
measured-beside-promised pairing is ours either way.
