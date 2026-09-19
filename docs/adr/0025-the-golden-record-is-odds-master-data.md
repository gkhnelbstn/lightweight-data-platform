# 0025 — The golden record is ODD's master data, published one way

## Context

ODD has a Master Data page. It lists *lookup tables*: small tables a person
browses in the catalogue, each with its own page and its own dataset entity.
The page was empty. The hub (ADR 0021) already holds the one table in this
platform that is master data by any definition: the golden customer, one row
per customer, with every system's code for it (#80).

## Decision

**`integrations/odd/master_data.py` publishes each hub's golden record as a
lookup table, `<entity>_master`, and every flow's value map as `value_maps`.**
The runner does it after its ODD push (`--odd-url`), and the script runs it
alone.

* **One way.** ODD lets anyone edit a lookup table and nothing can lock it.
  An edit there is overwritten by the next run, and the table's description
  says so. A golden record changes in a system and reaches the hub through its
  flow; a value map changes in its flow file. Taking edits back would make the
  catalogue a third system in the hub, without a flow, a commit time or a
  conflict rule.
* **Matched by key.** Rows are added, changed and deleted by the golden
  record's key, so a second run changes nothing, and a record deleted in the
  hub leaves the table.
* **Classified columns stay out.** `tax_id` is `pii` in the hub contract and
  is never copied: the classification is the boundary here as it is for a
  publication (ADR 0017).
* **Only what fits.** A hub with more than 10,000 records is left alone and
  the run says so. A lookup table is not where a million customers belong.

## Consequences

* The Master Data page shows `customer_master` (7 rows in the demo) and
  `value_maps` (4 codes).
* Found on the way: ODD does not quote the column names in its own
  `ALTER TABLE`, so a lookup column named `column` is a syntax error and a
  500. The value map's column is `target_column`, and a test keeps reserved
  words out.
* The copy is as fresh as the last run. A live mirror would need a trigger in
  the hub calling ODD, which is a network call inside a transaction.

## On upgrade

If ODD gains read-only lookup tables, or an import of a table it does not own,
use it and delete the overwrite. If ODD starts quoting identifiers, the column
names can be anything.
