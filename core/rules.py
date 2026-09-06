"""Quality rules a person can state without writing SQL.

The contract stores every rule as ODCS `type: sql`, because that is what
datacontract-cli executes -- it has no support for ODCS `library` rules, which
was checked rather than assumed. So the SQL has to come from somewhere, and
having it come from *here* rather than from a textarea is the whole point:

* **Nobody has to write SQL to say `country must be one of TR, DE, US`.** That
  is the common case and it should not require knowing which dialect the source
  speaks.
* **The generated SQL is dialect-correct.** The same rule becomes Postgres or
  T-SQL, built through sqlglot rather than string formatting, so a `'` in a
  value is a quoted literal and not an injection.
* **It needs no API token.** The raw-SQL route runs a statement someone typed
  and is behind one; this vocabulary is fixed, so there is nothing to guard
  that the read routes do not already expose.

Each rule states its own default dimension, because the dimension is what
weights the score and asking a person to pick one is asking them to know how
the score works.
"""
from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp

DIALECT = {"postgres": "postgres", "postgresql": "postgres",
           "sqlserver": "tsql", "mssql": "tsql", "mysql": "mysql"}


def _column(name: str) -> exp.Column:
    return exp.column(name)


def _lit(value: Any) -> exp.Expression:
    if isinstance(value, bool):
        return exp.convert(value)
    if isinstance(value, (int, float)):
        return exp.Literal.number(value)
    return exp.Literal.string(str(value))


def _not_null(col: str, _p: dict) -> exp.Expression:
    return exp.Is(this=_column(col), expression=exp.Null())


def _accepted_values(col: str, p: dict) -> exp.Expression:
    values = [v for v in (p.get("values") or []) if str(v) != ""]
    if not values:
        raise ValueError("accepted_values needs at least one value")
    return exp.Not(this=exp.In(this=_column(col),
                               expressions=[_lit(v) for v in values]))


def _between(col: str, p: dict) -> exp.Expression:
    if p.get("min") is None or p.get("max") is None:
        raise ValueError("between needs both min and max")
    return exp.Not(this=exp.Between(this=_column(col),
                                    low=_lit(p["min"]), high=_lit(p["max"])))


def _not_negative(col: str, _p: dict) -> exp.Expression:
    return exp.LT(this=_column(col), expression=_lit(0))


def _max_length(col: str, p: dict) -> exp.Expression:
    if p.get("length") is None:
        raise ValueError("max_length needs a length")
    return exp.GT(this=exp.func("LENGTH", _column(col)), expression=_lit(p["length"]))


def _matches(col: str, p: dict) -> exp.Expression:
    """Left as a LIKE rather than a regex: every engine spells regex
    differently and half of them need an extension, while LIKE is in the
    standard. A pattern is a value, so it is a bound literal either way."""
    if not p.get("pattern"):
        raise ValueError("matches needs a pattern")
    return exp.Not(this=exp.Like(this=_column(col), expression=_lit(p["pattern"])))


def _in_the_future(col: str, _p: dict) -> exp.Expression:
    return exp.GT(this=_column(col), expression=exp.CurrentDate())


# `sql` is the whole statement rather than a predicate, because these two are
# not "count the rows where X".
def _foreign_key(model: str, col: str, p: dict, dialect: str) -> str:
    if not p.get("table") or not p.get("column"):
        raise ValueError("foreign_key needs a table and a column")
    child, parent = exp.to_table(model).as_("c"), exp.to_table(p["table"]).as_("p")
    on = exp.EQ(this=exp.column(p["column"], "p"), expression=exp.column(col, "c"))
    query = (exp.select(exp.func("COUNT", exp.Star()))
             .from_(child)
             .join(parent, on=on, join_type="LEFT")
             .where(exp.and_(
                 exp.Is(this=exp.column(p["column"], "p"), expression=exp.Null()),
                 exp.Not(this=exp.Is(this=exp.column(col, "c"),
                                     expression=exp.Null())))))
    return query.sql(dialect=dialect)


def _unique(model: str, column: str, _p: dict, dialect: str) -> str:
    return unique_sql(model, column, dialect)


def unique_sql(model: str, column: str, dialect: str) -> str:
    """Duplicates need a GROUP BY, so it is its own shape."""
    inner = (exp.select(exp.column(column))
             .from_(exp.to_table(model))
             .group_by(exp.column(column))
             .having(exp.GT(this=exp.func("COUNT", exp.Star()), expression=_lit(1))))
    return (exp.select(exp.func("COUNT", exp.Star()))
            .from_(inner.subquery("d")).sql(dialect=dialect))


# name -> (predicate builder, default dimension, description template, menu label)
#
# The template becomes the rule's description once the values are known, and is
# what a person reads on the dashboard when it fails. The label is what the
# menu says *before* they are known, which is why it is written out rather than
# derived: a menu entry reading "a column must be one of {values}" is a bug
# report waiting to happen.
RULES: dict[str, tuple[Any, str, str, str]] = {
    "not_null": (_not_null, "completeness", "{column} must not be empty",
                 "is never empty"),
    "accepted_values": (_accepted_values, "conformity",
                        "{column} must be one of {values}",
                        "is one of a list of values"),
    "between": (_between, "accuracy", "{column} must be between {min} and {max}",
                "is between two numbers"),
    "not_negative": (_not_negative, "accuracy", "{column} must not be negative",
                     "is never negative"),
    "max_length": (_max_length, "conformity",
                   "{column} must be at most {length} characters",
                   "is no longer than a given length"),
    "matches": (_matches, "conformity", "{column} must look like {pattern}",
                "matches a pattern"),
    "not_in_the_future": (_in_the_future, "timeliness",
                          "{column} must not be in the future",
                          "is never a date in the future"),
    "foreign_key": (_foreign_key, "consistency",
                    "{column} must exist in {table}.{column_ref}",
                    "exists in another table"),
    "unique": (_unique, "uniqueness", "{column} must have no duplicates",
               "has no duplicates"),
}

# The two whose SQL is a whole statement rather than a `where` predicate: a
# duplicate needs a GROUP BY and a foreign key needs a join.
WHOLE_STATEMENT = ("foreign_key", "unique")

# What the UI needs to draw a form: which parameters each rule takes.
PARAMETERS: dict[str, list[dict[str, str]]] = {
    "not_null": [],
    "accepted_values": [{"name": "values", "type": "list",
                         "label": "Allowed values, comma separated"}],
    "between": [{"name": "min", "type": "number", "label": "Minimum"},
                {"name": "max", "type": "number", "label": "Maximum"}],
    "not_negative": [],
    "max_length": [{"name": "length", "type": "number", "label": "Maximum length"}],
    "matches": [{"name": "pattern", "type": "text",
                 "label": "Pattern, with % as the wildcard"}],
    "not_in_the_future": [],
    "foreign_key": [{"name": "table", "type": "text", "label": "Referenced table"},
                    {"name": "column", "type": "text", "label": "Referenced column"}],
    "unique": [],
}


def describe(kind: str, column: str, params: dict) -> str:
    """The rule in words. It becomes the test's name, so it is what a person
    reads on the dashboard when it fails."""
    _, _, template, _label = RULES[kind]
    values = params.get("values") or []
    return template.format(
        column=column, values=", ".join(str(v) for v in values),
        column_ref=params.get("column", ""), **{
            k: params.get(k, "") for k in ("min", "max", "length", "pattern", "table")})


def build(kind: str, model: str, column: str, params: dict,
          server_type: str = "postgres") -> tuple[str, str, str]:
    """`(description, sql, dimension)` for one form-built rule.

    The SQL counts the rows that break the rule, which is the shape every other
    rule in these contracts already has: `mustBe: 0`.
    """
    if kind not in RULES:
        raise ValueError(f"unknown rule {kind!r}")
    dialect = DIALECT.get(server_type, server_type)
    builder, dimension, _template, _label = RULES[kind]

    if kind in WHOLE_STATEMENT:
        sql = builder(model, column, params, dialect)
    else:
        predicate = builder(column, params)
        sql = (exp.select(exp.func("COUNT", exp.Star()))
               .from_(exp.to_table(model)).where(predicate).sql(dialect=dialect))

    # Parsed back to prove it is a query rather than trusting the builder --
    # datacontract will refuse anything that is not read-only anyway, and
    # finding that out here is cheaper than finding it out on the next run.
    sqlglot.parse_one(sql, read=dialect)
    return describe(kind, column, params), sql, dimension


def catalogue() -> list[dict]:
    """Everything the form needs, so the UI has no vocabulary of its own."""
    return [{"kind": kind, "dimension": dimension, "label": label,
             "parameters": PARAMETERS[kind]}
            for kind, (_, dimension, _template, label) in RULES.items()]
