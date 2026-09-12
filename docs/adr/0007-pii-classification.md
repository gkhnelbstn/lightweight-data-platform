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

Turkish TCKN and VKN are ours because Presidio had neither, and they are
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
* **TCKN is upstream, independently of us.** `TrNationalIdRecognizer` /
  `TR_NATIONAL_ID` merged in microsoft/presidio#1995 — same NVI checksum,
  disabled by default like ours. Not yet used here: `>=2.2` is what
  `presidio-analyzer>=2.2` in `pyproject.toml` pins, and this needs pinning
  forward to whichever release contains #1995 first, which has not been
  checked.
* **VKN offered upstream.** `TrTaxIdRecognizer` / `TR_TAX_ID`, same shape,
  contributed as microsoft/presidio#2250 — tracked in
  [issue #5](https://github.com/gkhnelbstn/lightweight-data-platform/issues/5).
  Note the project moved: `microsoft/presidio` now redirects to
  `data-privacy-stack/presidio`; the PR lives there. The fork this needed
  (`gkhnelbstn/presidio` on GitHub) is contribution plumbing only — nothing
  here builds or runs it, unlike a category-4 carried patch in
  [0015](0015-the-boundary-what-is-ours.md#4-patches-we-carry-for-someone-else) —
  and is safe to delete once the PR merges or is closed.
* **When either lands in a release:** bump `presidio-analyzer` past it, delete
  the matching `Tckn`/`Vkn` class and its test in `classify.py` /
  `tests/test_classify.py`, and register the upstream recognizer instead.
  Do both at once only if both have shipped; otherwise drop whichever has.
* Considered, once VKN turned out to need contributing rather than already
  existing: swapping Presidio for a Turkish-specific PII library instead of
  carrying two recognizers until upstream catches up. Rejected -- a TR-only
  library would still need building or wrapping something to catch IBANs,
  credit cards, emails, phone numbers and IP addresses the way Presidio
  already does, so it replaces forty checksum-validated lines with a second
  system to maintain rather than removing the first.
* If ODD grows a classification model, push into that instead of tags.
