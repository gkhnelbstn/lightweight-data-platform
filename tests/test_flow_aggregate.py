"""Many rows into one row, one way (#81). The refusals need nothing; the sum
itself runs against a real PostgreSQL, like tests/test_hub.py, and skips
without one:

    DWH_PORT=5442 pytest tests/test_flow_aggregate.py
"""
from __future__ import annotations

import copy
import os
from decimal import Decimal
from pathlib import Path

import pytest

from core import flow_aggregate, flow_jobs, flows

DEMO = Path(__file__).resolve().parents[1] / "demo" / "integration"
HOST = os.getenv("DWH_HOST", "localhost")
PORT = int(os.getenv("DWH_PORT", "5432"))
SCRATCH = "flow_aggregate_test"


def _demo():
    by_id, loaded = flow_jobs.load(DEMO)
    return by_id, loaded, next(f for f in loaded if f.id == "invoice_totals")


def _problems(**changes):
    by_id, loaded, flow = _demo()
    doc = {"id": "invoice_totals", "from": "billing.invoice_line", "to": "ledger.journal_entry",
           "columns": {"EntryNo": "InvoiceNo"},
           "aggregates": {"Total": "sum(Amount)", "LineCount": "count(*)"},
           "filledByTarget": ["PostedAt"], **changes}
    others = [f for f in loaded if f.id != "invoice_totals"]
    return flows.problems(others + [flows.parse(doc)], by_id)


def test_the_demo_aggregate_is_sound():
    assert _problems() == []


def test_a_total_cannot_come_back_as_lines():
    by_id, loaded, flow = _demo()
    back = flows.parse({"id": "totals_back", "from": "ledger.journal_entry",
                        "to": "billing.invoice_line", "columns": {"InvoiceNo": "EntryNo"}})
    got = flows.problems(loaded + [back], by_id)
    assert any("a total cannot come back as lines" in p for p in got), got


def test_an_aggregate_is_one_of_five_functions_of_one_column():
    got = _problems(aggregates={"Total": "sum(Amount * 2)", "LineCount": "count(*)"})
    assert any("an aggregate is sum, count, min, max or avg" in p for p in got), got
    got = _problems(aggregates={"Total": "sum(*)", "LineCount": "count(*)"})
    assert any("only count takes *" in p for p in got), got


def test_the_group_is_the_targets_key():
    got = _problems(columns={"EntryNo": "InvoiceNo", "CustomerCode": "CustomerCode"})
    assert any("must be ledger.journal_entry's key" in p for p in got), got


def test_a_required_total_nothing_computes_is_a_gap():
    got = _problems(aggregates={"Total": "sum(Amount)"})
    assert any("LineCount is required here" in p for p in got), got


def test_both_jobs_compile():
    by_id, loaded, flow = _demo()
    jobs = flow_aggregate.jobs(flow, by_id)
    assert set(jobs) == {"invoice_totals", "invoice_totals_out"}
    assert jobs["invoice_totals_out"]["source"][0]["Postgres-CDC"]["table-names"] == [
        "hub.flow.invoice_totals"]


# --- the sum, against Postgres ------------------------------------------------

psycopg = pytest.importorskip("psycopg")


@pytest.fixture()
def landed():
    from core.bootstrap_db import admin_dsn, ensure_database
    try:
        with psycopg.connect(admin_dsn(HOST, PORT), connect_timeout=2, autocommit=True) as cx:
            cx.execute(f'drop database if exists "{SCRATCH}" with (force)')
    except psycopg.OperationalError as exc:
        pytest.skip(f"no PostgreSQL at {HOST}:{PORT} ({exc.__class__.__name__})")
    ensure_database(HOST, PORT, SCRATCH)
    cx = psycopg.connect(admin_dsn(HOST, PORT, SCRATCH), autocommit=True)
    by_id, _, flow = _demo()
    flow_aggregate.install(cx, flow, by_id)
    try:
        yield cx
    finally:
        cx.close()
        with psycopg.connect(admin_dsn(HOST, PORT), autocommit=True) as adm:
            adm.execute(f'drop database if exists "{SCRATCH}" with (force)')


def line(cx, kind, invoice, no, amount, customer="C1"):
    cx.execute('insert into flow."invoice_totals_inbox" (row_kind, "InvoiceNo", "LineNumber", '
               '"Amount", "CustomerCode") values (%s, %s, %s, %s, %s)',
               (kind, invoice, no, amount, customer))


def totals(cx):
    return cx.execute('select "EntryNo", "Total", "LineCount" from flow.invoice_totals '
                      'order by 1').fetchall()


def test_lines_become_one_total_per_invoice(landed):
    line(landed, "INSERT", "F1", 1, "1200.00")
    line(landed, "INSERT", "F1", 2, "350.50")
    line(landed, "INSERT", "F2", 1, "980.00")
    assert totals(landed) == [("F1", Decimal("1550.50"), 2), ("F2", Decimal("980.00"), 1)]


def test_an_updated_line_changes_its_total(landed):
    line(landed, "INSERT", "F1", 1, "100.00")
    line(landed, "UPDATE_BEFORE", "F1", 1, "100.00")
    line(landed, "UPDATE_AFTER", "F1", 1, "120.00")
    assert totals(landed) == [("F1", Decimal("120.00"), 1)]


def test_a_deleted_line_leaves_its_total_and_the_last_takes_the_entry(landed):
    line(landed, "INSERT", "F1", 1, "100.00")
    line(landed, "INSERT", "F1", 2, "50.00")
    line(landed, "DELETE", "F1", 2, "50.00")
    assert totals(landed) == [("F1", Decimal("100.00"), 1)]
    line(landed, "DELETE", "F1", 1, "100.00")
    assert totals(landed) == []


def test_a_line_moved_to_another_invoice_leaves_one_total_for_the_other(landed):
    """Its key changed: the old line goes from its group, the new one joins."""
    line(landed, "INSERT", "F1", 1, "100.00")
    line(landed, "INSERT", "F1", 2, "50.00")
    line(landed, "UPDATE_BEFORE", "F1", 2, "50.00")
    line(landed, "UPDATE_AFTER", "F2", 1, "50.00")
    assert totals(landed) == [("F1", Decimal("100.00"), 1), ("F2", Decimal("50.00"), 1)]


def test_a_total_that_did_not_change_is_not_rewritten(landed):
    """A rewrite is a change to logical decoding, which the out job would
    deliver again for nothing -- a replayed line must not cause one."""
    line(landed, "INSERT", "F1", 1, "100.00")
    before = landed.execute("select xmin::text from flow.invoice_totals").fetchone()
    line(landed, "INSERT", "F1", 1, "100.00")
    assert landed.execute("select xmin::text from flow.invoice_totals").fetchone() == before


def test_a_line_whose_group_changes_leaves_its_old_total(landed):
    """Grouped by a column that is not part of the line's key -- a total per
    customer -- a line re-assigned to another customer keeps its key, and
    the customer it left must be recomputed as well."""
    by_id, _, _ = _demo()
    by_id = copy.deepcopy(by_id)
    for prop in by_id["ledger.journal_entry"]["schema"][0]["properties"]:
        prop["primaryKey"] = prop["name"] == "CustomerCode"
    per_customer = flows.parse({"id": "customer_totals", "from": "billing.invoice_line",
                                "to": "ledger.journal_entry",
                                "columns": {"CustomerCode": "CustomerCode"},
                                "aggregates": {"Total": "sum(Amount)", "LineCount": "count(*)"}})
    flow_aggregate.install(landed, per_customer, by_id)
    row = ('insert into flow."customer_totals_inbox" (row_kind, "InvoiceNo", "LineNumber", '
           '"Amount", "CustomerCode") values (%s, %s, %s, %s, %s)')
    landed.execute(row, ("INSERT", "F1", 1, "100.00", "C1"))
    landed.execute(row, ("INSERT", "F1", 2, "50.00", "C1"))
    landed.execute(row, ("UPDATE_BEFORE", "F1", 2, "50.00", "C1"))
    landed.execute(row, ("UPDATE_AFTER", "F1", 2, "50.00", "C2"))
    assert landed.execute('select "CustomerCode", "Total", "LineCount" from '
                          'flow.customer_totals order by 1').fetchall() == [
        ("C1", Decimal("100.00"), 1), ("C2", Decimal("50.00"), 1)]
