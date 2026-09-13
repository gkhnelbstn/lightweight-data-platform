"""Rules a person picks instead of writing.

The SQL is generated, so the generator is the thing to pin: it has to quote,
it has to be right in both dialects, and it has to refuse a rule that is
missing what it needs rather than emitting something that half works.
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlglot")
from core.rules import BUILTIN, build, catalogue, describe  # noqa: E402

TABLE = "sales_orders"


def sql(kind, column="country", params=None, engine="postgres"):
    return build(kind, TABLE, column, params or {}, engine)[1]


# --- what it generates ------------------------------------------------------

def test_a_value_containing_a_quote_is_a_literal_not_an_injection():
    """The whole reason this goes through sqlglot expressions rather than an
    f-string."""
    out = sql("accepted_values", params={"values": ["TR", "O'Brien"]})
    assert "'O''Brien'" in out
    assert out.count("SELECT") == 1


def test_the_same_rule_is_correct_in_both_dialects():
    """`LENGTH` is `LEN` in T-SQL and `COUNT(*)` is `COUNT_BIG(*)`. Emitting
    from a parse tree is what makes that free."""
    assert "LENGTH" in sql("max_length", params={"length": 11})
    tsql = sql("max_length", params={"length": 11}, engine="sqlserver")
    assert "LEN(" in tsql and "COUNT_BIG" in tsql


def test_a_date_rule_uses_each_engines_own_today():
    assert "CURRENT_DATE" in sql("not_in_the_future", column="order_date")
    assert "GETDATE()" in sql("not_in_the_future", column="order_date",
                              engine="sqlserver")


def test_every_rule_counts_the_rows_that_break_it():
    """`mustBe: 0` is the shape every other rule in these contracts has, so a
    generated one must not be the exception."""
    params = {"accepted_values": {"values": ["a"]},
              "between": {"min": 0, "max": 1},
              "max_length": {"length": 3},
              "matches": {"pattern": "a%"},
              "foreign_key": {"table": "customers", "column": "customer_id"}}
    # The built-ins only: a plugin's kinds are its own to test, and this one
    # would have to know their parameters to call them.
    for kind in (r["kind"] for r in catalogue() if r["kind"] in BUILTIN):
        out = sql(kind, params=params.get(kind))
        assert out.upper().startswith("SELECT COUNT"), f"{kind}: {out}"


def test_uniqueness_needs_a_group_by_and_gets_one():
    out = sql("unique", column="order_id")
    assert "GROUP BY" in out and "HAVING" in out


def test_a_foreign_key_is_a_left_join_looking_for_nothing():
    out = sql("foreign_key", column="customer_id",
              params={"table": "customers", "column": "customer_id"})
    assert "LEFT JOIN" in out and "IS NULL" in out


# --- what it refuses --------------------------------------------------------

@pytest.mark.parametrize("kind,params", [
    ("accepted_values", {"values": []}),
    ("between", {"min": 0}),
    ("max_length", {}),
    ("matches", {"pattern": ""}),
    ("foreign_key", {"table": "customers"}),
])
def test_a_rule_missing_its_parameters_is_refused(kind, params):
    """Better a 400 than a rule that silently means something else."""
    with pytest.raises(ValueError):
        build(kind, TABLE, "country", params)


def test_an_unknown_rule_is_refused():
    with pytest.raises(ValueError):
        build("drop_table", TABLE, "country", {})


# --- what the UI is told ----------------------------------------------------

def test_the_catalogue_is_the_only_vocabulary():
    """The form has none of its own, so every rule has to describe itself."""
    from core.scoring import DIMENSION_WEIGHT
    entries = catalogue()
    assert entries
    for entry in entries:
        assert entry["dimension"] in DIMENSION_WEIGHT, entry["kind"]
        assert entry["label"] and "{" not in entry["label"]
        for parameter in entry["parameters"]:
            assert {"name", "type", "label"} <= set(parameter)
            assert parameter["type"] in ("text", "number", "list")


def test_the_description_is_what_a_person_reads_when_it_fails():
    assert describe("accepted_values", "country", {"values": ["TR", "DE"]}) == \
        "country must be one of TR, DE"
    assert describe("between", "net_amount", {"min": 0, "max": 100}) == \
        "net_amount must be between 0 and 100"


# --- kinds from outside this repo (ADR 0016) --------------------------------

def _kind(**over):
    from sqlglot import exp
    from core.rules import RuleKind
    return RuleKind(**{
        "kind": "is_upper",
        "builder": lambda c, _p: exp.NEQ(this=exp.func("UPPER", exp.column(c)),
                                         expression=exp.column(c)),
        "dimension": "conformity",
        "description": "{column} must be upper case",
        "label": "is upper case", **over})


def test_a_registered_kind_is_in_the_catalogue_and_builds_sql():
    """The dict was always the registry; this is the registration a package
    outside this repo can reach."""
    from core.rules import PARAMETERS, RULES, register
    register(_kind())
    try:
        assert "is_upper" in {r["kind"] for r in catalogue()}
        description, statement, dimension = build(
            "is_upper", "customers", "country", {})
        assert statement.upper().startswith("SELECT COUNT")
        assert (description, dimension) == ("country must be upper case",
                                            "conformity")
    finally:
        RULES.pop("is_upper"), PARAMETERS.pop("is_upper")


@pytest.mark.parametrize("over, why", [
    ({"kind": "unique"}, "already registered"),
    ({"dimension": "vibes"}, "not one the score weights"),
    ({"label": "is {what}"}, "cannot be a template"),
    ({"description": "{column} must be {threshold}"}, "not a parameter"),
    ({"parameters": [{"name": "n", "type": "slider", "label": "N"}]},
     "not a form field"),
])
def test_a_kind_that_would_break_the_form_is_refused(over, why):
    """At registration, not when somebody fills the form in: a catalogue that
    lost a kind, or offers one that explodes, is worse than one that refuses
    to load."""
    from core.rules import register
    with pytest.raises(ValueError, match=why):
        register(_kind(**over))


def test_a_plugin_that_raises_names_itself():
    """A silently missing rule kind is the failure this must not have."""
    from core.rule_plugins import load_plugins
    class _Entry:
        name, value = "iban_checksum", "my_pkg.rules:iban_checksum"
        def load(self):
            raise ImportError("no module named my_pkg")
    import core.rule_plugins as plugins
    original = plugins.entry_points
    plugins.entry_points = lambda group: [_Entry()]
    try:
        with pytest.raises(RuntimeError, match="iban_checksum"):
            load_plugins(lambda rule: None)
    finally:
        plugins.entry_points = original
