"""Turning a check back into the rows it counted.

The rewrite is the only clever thing in core/sample.py, so it is the thing
worth pinning: it has to keep the FROM and the WHERE, drop the aggregate, and
survive being emitted as a different dialect. It must also refuse rather than
guess -- a check that aggregates the whole table has no failing row to show,
and inventing one would be worse than saying so.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytest.importorskip("sqlglot")
from core.sample import classified, rows_query  # noqa: E402

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"


def _check(**kw) -> dict:
    base = {"check_type": "model_quality_sql", "field": None,
            "sql": "select count(*) from sales_orders "
                   "where status = 'CANCELLED' and net_amount <> 0"}
    base.update(kw)
    return base


def test_the_count_becomes_the_rows():
    q = rows_query(_check(), "sales_orders", "postgres")
    assert q.startswith("SELECT *")
    assert "COUNT" not in q.upper()
    assert "status = 'CANCELLED'" in q
    assert "net_amount <> 0" in q
    assert "LIMIT" in q


def test_the_limit_follows_the_dialect():
    """SQL Server has no LIMIT. Going through a parse tree rather than string
    surgery is what makes this free."""
    q = rows_query(_check(sql="select count(*) from dbo.sales_orders "
                              "where currency not in ('TRY','USD','EUR')"),
                   "dbo.sales_orders", "sqlserver")
    assert "TOP" in q and "LIMIT" not in q


def test_joins_and_subqueries_survive():
    """The rule that matters most is the one comparing a header to its lines;
    a rewrite that dropped the join would answer the wrong question."""
    q = rows_query(_check(sql="""
        select count(*) from sales_orders o
          join (select order_id, sum(line_amount) as line_total
                from sales_order_lines group by order_id) l
            on l.order_id = o.order_id
        where abs(o.net_amount - l.line_total) > 0.01"""),
        "sales_orders", "postgres")
    assert "JOIN" in q and "line_total" in q and "SELECT *" in q


def test_an_aggregate_with_no_predicate_has_no_rows():
    """Freshness: the failure *is* the absence of rows."""
    assert rows_query(_check(sql="select case when max(loaded_at) < "
                                 "current_date - 1 then 1 else 0 end "
                                 "from sales_orders"),
                      "sales_orders", "postgres") is None


def test_a_grouped_query_is_refused():
    """`select *` under a GROUP BY is not valid SQL and not an answer."""
    assert rows_query(_check(sql="select count(*) from sales_orders "
                                 "where status = 'OPEN' group by currency"),
                      "sales_orders", "postgres") is None


def test_required_is_stated_rather_than_rewritten():
    """datacontract compiles it to SUM(CASE WHEN ... IS NULL), which has no
    WHERE to keep. The predicate is trivial, so sample.py carries it."""
    q = rows_query(_check(check_type="field_required", field="customer_id",
                          sql="SELECT SUM(CASE WHEN customer_id IS NULL "
                              "THEN 1 ELSE 0 END) FROM sales_orders"),
                   "sales_orders", "postgres")
    assert "customer_id IS NULL" in q
    assert "SUM" not in q.upper()


def test_nonsense_sql_is_refused_not_raised():
    assert rows_query(_check(sql="not sql at all ((("),
                      "t", "postgres") is None


# --- what must never be shown -----------------------------------------------

def test_the_contract_is_what_hides_a_column():
    """classify.py finds the PII; writing it into the contract is what makes
    every reader honour it, this one included."""
    doc = yaml.safe_load(
        (CONTRACTS / "erp_customers.odcs.yaml").read_text(encoding="utf-8"))
    assert classified(doc) == {"tax_id"}


@pytest.mark.parametrize("path", sorted(CONTRACTS.glob("*.odcs.yaml")),
                         ids=lambda p: p.stem)
def test_every_classified_column_is_a_real_column(path: Path):
    """A typo in `classification` would silently unmask the column."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    names = {p["name"] for m in doc.get("schema", [])
             for p in m.get("properties") or []}
    assert classified(doc) <= names
