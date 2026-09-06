# 0013 — The catalogue is filled from the contract, not by hand

## Context

A collector brings the *shape* of a source: tables, columns, types, lineage.
Everything a catalogue is actually for — who owns this, what is it for, what
does this column mean, what may I do with it — arrives as **"Not created"** and
stays that way, because no collector knows any of it.

The usual answer is that someone fills it in through the UI. That work is never
done, is never reviewed, and drifts from the thing it describes.

## Decision

The contract already carries all of it, so it is pushed into ODD's own places
rather than typed into them:

| contract | ODD |
|---|---|
| `domain` | namespace on the data source, and a `domain:` tag |
| `name` | the entity's business name |
| `tenant` | an owner, and its ownership of the entity |
| `description.purpose` + model description | the entity's description |
| `property.description` | the column's description |
| quality dimensions | dictionary terms, linked to the entity |
| quality rules | query examples on the dataset |
| `servers`, `customProperties`, `slaProperties` | metadata on the entity |

Two things follow from putting it in the contract rather than the UI:

* **It is reviewed.** A column description arrives through the same pull
  request as the rule that checks that column.
* **It cannot drift.** `tests/test_catalogue.py` fails when a column has no
  description, when a contract has no owner or purpose, and when a tag claims
  something the contract does not carry.

The dictionary is the quality vocabulary itself — each dimension, what it
means, and the weight it carries in the score — in its own `data quality`
namespace rather than the contract's domain, because `completeness` means the
same thing to sales as to finance.

## Consequences

* Every push is idempotent **by lookup**, not by remembering ids: each of these
  endpoints has a listing to check against, which the entity links in ADR 0009
  did not. Running it three times in a row changes nothing the second and third
  time, and that is asserted rather than assumed.
* Tags are unioned rather than replaced. `PUT` replaces the list, and a tag
  someone added by hand is not ours to delete.
* It runs as part of the daily push, so a contract change reaches the catalogue
  without anyone remembering to do it.
* ODD's API is inconsistent here and the code says so: tags on an *entity* want
  `{"tag_name_list": [...]}`, tags on a dataset *field* want `{"tags": [...]}`.
  Same platform, same release.

## On upgrade

* Bumping ODD: re-run `python integrations/odd/curate.py` and read the
  counts. A field that stops being accepted shows as a 4xx, not as silence.
* ODD drops floats and lists from metadata silently, so everything is rendered
  to a string before it is sent. If that is ever fixed, sending typed values is
  better — but check the entity page afterwards rather than trusting the 201.
* If ODD grows an import for ODCS directly, most of this module is deletable.
* The module is `curate.py`, not `catalogue.py`. `catalogue` is a PyPI package
  spaCy's registry depends on, and a module of that name here shadowed it the
  moment anything in the directory ran as a script — `classify.py` died with
  `module 'catalogue' has no attribute 'create'`. Do not name a module after a
  dependency.
