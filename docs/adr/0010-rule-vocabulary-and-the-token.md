# 0010 — Rules from a fixed vocabulary, and the token guards only raw SQL

## Context

Two complaints, and they turn out to have one answer.

1. Authoring a rule meant writing SQL. "country must be one of TR, DE, US" is
   the common case and should not require knowing which dialect the source
   speaks.
2. The UI asked for an API token, which meant an operator had to invent one
   with `openssl rand` and then paste it on every visit. A secret someone has
   to invent is a secret that ends up being `admin`.

## Decision

**A fixed rule vocabulary, compiled server-side.** `core/rules.py` holds the
rule kinds — `not_null`, `unique`, `accepted_values`, `between`,
`not_negative`, `max_length`, `matches`, `not_in_the_future`, `foreign_key` —
each with a default dimension and the parameters it takes. The UI fetches the
vocabulary from `/api/rules/catalogue` and holds none of its own.

The SQL is built through **sqlglot expressions**, never string formatting, so a
`'` in a value is a quoted literal and the same rule emits Postgres or T-SQL
correctly: `LENGTH` becomes `LEN`, `CURRENT_DATE` becomes `GETDATE()`,
`COUNT(*)` becomes `COUNT_BIG(*)`.

ODCS `library` rules would have been better than generating SQL at all, but
**datacontract-cli does not support them** — it handles `type: sql` and a
deprecated Soda `custom`. Checked in its source, not assumed.

**The form route needs no token.** The vocabulary is fixed and the statement is
composed here, so there is nothing to smuggle in that the read routes do not
already expose. Only the raw-SQL escape hatch is guarded.

**The token is generated, not requested.** If `DQ_API_TOKEN` is unset the
service mints one on first use, keeps it in `api_tokens`, and prints it at
startup.

## Consequences

* Adding a rule kind is one entry in `core/rules.py`. The UI picks it up.
* Route handlers must not call each other: `Depends` runs when FastAPI routes a
  request, not when one Python function calls another, so a handler calling a
  guarded handler would *look* authorised and not be. The implementations are
  `_preview` / `_save`, and the guarded routes are thin wrappers over them.
* A generated token is only as private as the logs.
* `matches` is a `LIKE`, not a regex: every engine spells regex differently and
  half need an extension, while `LIKE` is in the standard.

## On upgrade

* **If datacontract-cli learns ODCS `library` rules**, emit those instead of
  SQL and most of `core/rules.py` disappears. Check
  `engines/checks/create_checks.py::_quality_rule_checks` for the supported
  `quality.type` values.
* **sqlglot:** `tests/test_rules.py` pins the generated SQL per dialect,
  including the escaping of a value containing a quote.
* **If a real identity provider ever fronts this**, drop the token entirely and
  use it — the token exists because there is no user system, not because a
  shared secret is a good design.
