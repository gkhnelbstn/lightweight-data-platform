# 0007 — Presidio finds the PII, the contract records it

## Context

ODD has no classification of its own — issue
[odd-platform#130](https://github.com/opendatadiscovery/odd-platform/issues/130)
was closed in 2021 without one — so this gap has to be filled somewhere.

## Decision

Microsoft **Presidio** (MIT, ~10k stars) rather than hand-written patterns: it
already recognises emails, IBANs, credit cards, phone numbers and IP addresses
with the validation each deserves. The finding is written back into the
contract as ODCS `classification:`, and pushed to ODD as first-class,
searchable column tags.

The contract is where it belongs, because that is what makes **every** reader
honour it: `core/sample.py` masks a classified column in the failing-rows view,
and `core/sync.py` leaves it out of the replica entirely.

Three decisions inside it, each measured:

* **The small spaCy model.** Presidio defaults to `en_core_web_lg`, 425 MB.
  Column values are homogeneous — a column of IBANs is not free text — so the
  NLP half earns very little here. `en_core_web_sm` (15 MB) finds the same
  columns and drops a false `MEDICAL_LICENSE` on an IBAN. Total ~265 MB.
* **Highest-scoring reading per value.** A ten-digit VKN looks like a phone
  number to a generic recogniser; without this the column was also tagged
  `PHONE_NUMBER`.
* **Coverage gates, then every type above a share is reported.** A `tax_id`
  column in a Turkish ERP genuinely holds VKN for companies and TCKN for sole
  traders, and calling it only the more common one would be wrong about the
  data. The first attempt gated on per-type confidence and found nothing.

Turkish TCKN and VKN are ours because Presidio has neither, and they are
`PatternRecognizer`s **with checksum validators** rather than regexes that
would match any eleven digits.

## Consequences

* The seed produces checksum-valid identifiers. Invalid ones made the demo find
  nothing, which looked exactly like the classifier being broken.
* ODD's tag API wants `{"tags": [...]}`; `{"tagNameList": ...}` is a 500. There
  is no lookup-by-ODDRN, so the entity id is resolved through search and
  matched on the ODDRN exactly — matching on name alone is wrong, because
  `customers` is also the name of several checks.

## On upgrade

* **Presidio:** re-run `python integrations/odd/classify.py` against the seeded
  demo. `tests/test_contracts.py` pins the two checksums, so a broken validator
  fails the suite, but a changed *recogniser* would only show as columns going
  untagged.
* Offer TR_TCKN / TR_VKN upstream once they have run against real data long
  enough to be worth someone else's maintenance. If Presidio adopts them,
  delete ours.
* If ODD grows a classification model, push into that instead of tags.
