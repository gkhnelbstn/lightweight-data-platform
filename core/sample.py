"""Show the rows a failed check counted.

A check says 150 orders disagree with their lines. The next question is always
*which* ones, and until now the answer was to go and write the query by hand.

datacontract already hands back the SQL it ran, in `check["implementation"]`,
and almost every one of them is `select count(*) ... where ...`. So the rows
are one rewrite away: keep the FROM and the WHERE, replace the projection with
`*`, add a limit. sqlglot does it -- already installed, since datacontract-cli
is built on it -- and because the rewrite goes through a parse tree rather than
string surgery, emitting it back as T-SQL turns the LIMIT into a TOP.

Two checks have no rows to point at, and say so rather than guessing:

* `field_required` compiles to `SUM(CASE WHEN x IS NULL ...)`, an aggregate
  over the whole table with no WHERE to keep. The predicate is trivial, so it
  is stated in `BY_TYPE` instead.
* A freshness rule is `select case when max(loaded_at) < ... then 1 else 0
  end`. There is no failing row -- the absence of rows is the failure.
"""
from __future__ import annotations

import os

import sqlglot
from sqlglot import exp

LIMIT = int(os.getenv("DQ_SAMPLE_LIMIT", "20"))
MASK = "\u2022\u2022\u2022"

# ODCS server type -> the dialect the check was compiled to and has to be read
# back as. `implementation` is already in the source's dialect.
DIALECT = {"postgres": "postgres", "postgresql": "postgres",
           "sqlserver": "tsql", "mssql": "tsql", "mysql": "mysql"}

# Checks datacontract compiles to an aggregate with nothing to keep.
BY_TYPE = {"field_required": "{f} IS NULL", "field_not_null": "{f} IS NULL"}


def rows_query(check: dict, table: str, server_type: str,
               limit: int = LIMIT) -> str | None:
    """The SQL that returns the rows this check failed on, or None."""
    dialect = DIALECT.get(server_type, server_type)
    predicate = BY_TYPE.get(check.get("check_type") or check.get("type"))
    if predicate and check.get("field"):
        text = (f"SELECT * FROM {table} WHERE "
                + predicate.format(f=_quote(check["field"], dialect)))
    else:
        text = check.get("sql") or check.get("implementation") or ""
    if not text.strip():
        return None
    try:
        tree = sqlglot.parse_one(text, read=dialect)
    except Exception:
        return None
    # Only a plain SELECT can have its projection swapped for `*`. A grouped
    # one aggregates the rows away, so there is nothing left to show.
    if not isinstance(tree, exp.Select) or tree.args.get("group"):
        return None
    if not tree.args.get("where"):
        return None
    tree.set("expressions", [exp.Star()])
    return tree.limit(limit).sql(dialect=dialect)


def _quote(name: str, dialect: str) -> str:
    return exp.column(name).sql(dialect=dialect)


def classified(contract: dict) -> set[str]:
    """Columns the contract marks as regulated. Their values are never shown.

    `classification` is standard ODCS. integrations/odd/classify.py is what
    finds them; writing the finding back into the contract is what makes every
    reader of the contract -- this one included -- honour it.
    """
    out: set[str] = set()
    for model in contract.get("schema", []):
        for prop in model.get("properties") or []:
            if prop.get("classification"):
                out.add(prop["name"])
    return out
