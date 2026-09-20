"""Drive the running two-way demo and assert what ADR 0021 promises.

The two systems number customers differently (#80), so billing's rows are
found by tax number, and each run uses a fresh CRM code and tax number, so it
can be repeated against a stack that has already been played with:

    docker compose -f compose.yaml -f compose.demo.yaml --profile demo --profile flows up -d
    sqlcmd ... -i demo/integration/setup.sql
    docker compose exec -e PG_USER=postgres -e PG_PASSWORD=postgres \
        -e MSSQL_USER=sa -e MSSQL_PASSWORD=... app \
        python core/flow_jobs.py --apply --contracts demo/integration
    docker compose exec -e MSSQL_PASSWORD=... app python demo/integration/verify.py
"""
from __future__ import annotations

import os
import random
import time

import psycopg

from core.sync_mssql import mssql_connect

HUB = os.getenv("HUB_DSN", "host=db dbname=hub user=postgres password=postgres")
SHOP = os.getenv("SHOP_DSN", "host=db dbname=shop user=postgres password=postgres")
LOYALTY = os.getenv("LOYALTY_DSN",
                    "host=db dbname=loyalty user=postgres password=postgres")
CRM = {"host": "mssql", "database": "crm"}
BILLING = {"host": "mssql", "database": "billing"}
LEDGER = {"host": "mssql", "database": "ledger"}
SEEDED = ("1234567890", "2345678901", "3456789012", "4567890123", "5678901234",
          "6789012345", "7890123456")


def sql(server: dict, statement: str, *args):
    with mssql_connect(server) as cx:
        cx.autocommit = True
        cur = cx.cursor().execute(statement, *args)
        return cur.fetchall() if cur.description else None


def hub(statement: str, *args):
    with psycopg.connect(HUB) as cx:
        return cx.execute(statement, args).fetchall()


def wait(what: str, check, seconds: int = 60):
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        if check():
            print(f"  ok  {what} ({time.monotonic() - start:.0f} s)")
            return
        time.sleep(1)
    raise AssertionError(f"{what}: not within {seconds} s")


def crm_row(code):
    rows = sql(CRM, "select TITLE, ACTIVE from dbo.account where ACCOUNT_CODE = ?", code)
    return tuple(rows[0]) if rows else None


def billing(tax, columns):
    rows = sql(BILLING, f"select {columns} from dbo.customer where TaxId = ?", tax)
    # A second row for one customer is the duplicate the crosswalk prevents.
    assert len(rows) <= 1, f"billing has {len(rows)} rows for tax number {tax}"
    return tuple(rows[0]) if rows else None


def billing_row(tax):
    row = billing(tax, "Name, IsActive")
    return (row[0], bool(row[1])) if row else None


def billing_cities(tax):
    return billing(tax, "InvoiceCity, ShippingCity")


def crm_address(code, kind):
    rows = sql(CRM, "select CITY from dbo.account_address "
                    "where ACCOUNT_CODE = ? and ADDR_TYPE = ?", code, kind)
    return rows[0][0] if rows else None


def inbox() -> int:
    """Every row the hub kept, which is every row it did something with: one
    it did nothing with is dropped by the trigger. So a poll repeating a
    member does not make a settled hub look like a loop, and this can go back
    to counting the table."""
    return hub("select count(*) from hub.customer_inbox")[0][0]


def shop_row(tax):
    """The shop's row for a tax number: name, active, city (#82)."""
    with psycopg.connect(SHOP) as cx:
        rows = cx.execute("select full_name, is_active, city from customer "
                          "where vat_number = %s", (tax,)).fetchall()
    assert len(rows) <= 1, f"the shop has {len(rows)} rows for tax number {tax}"
    return rows[0] if rows else None


def shop_sql(statement: str, *args) -> None:
    with psycopg.connect(SHOP, autocommit=True) as cx:
        cx.execute(statement, args)


def loyalty_sql(statement: str, *args) -> None:
    with psycopg.connect(LOYALTY, autocommit=True) as cx:
        cx.execute(statement, args)


def member(field: str, no: int = 9000):
    rows = hub(f"select {field} from hub.customer where loyalty_code = %s", no)
    return rows[0][0] if rows else None


def polled() -> None:
    """An API source, one way (#97, ADR 0027). The loyalty scheme has no
    change log, so the flow polls its listing: every member, every time. What
    must hold is that a poll repeating a member is not an edit -- the previous
    poll is the before image -- and that a real change still arrives."""
    wait("a member the other systems hold is found by its tax number, and "
         "brings the tier no other system keeps",
         lambda: member("tier") is not None)
    was = member("_rev")
    time.sleep(25)
    assert member("_rev") == was, "a poll that repeated every member moved the record"
    print(f"  ok  polls that repeat a member change nothing (revision {was})")
    now = "Platin" if member("tier") != "Platin" else "Altın"
    loyalty_sql("update member set tier = %s where member_no = 9000", now)
    wait("a change in the loyalty scheme reaches the hub", lambda: member("tier") == now)
    assert member("_rev") == was + 1, "one change, one revision"
    print("  ok  and one change is one revision")


def linked(tax) -> bool:
    """One hub record holding all three systems' codes."""
    return bool(hub("select 1 from hub.customer where tax_id = %s and crm_code is not "
                    "null and billing_code is not null and shop_code is not null", tax))


def entry(invoice):
    rows = sql(LEDGER, "select Total, LineCount from dbo.journal_entry where EntryNo = ?",
               invoice)
    return (float(rows[0][0]), rows[0][1]) if rows else None


def totals() -> None:
    """Invoice lines in billing, one journal entry per invoice in the ledger
    (#81): many rows into one, one way, kept right through every kind of
    change to a line."""
    a, b = (f"V-{random.randint(10_000, 99_999)}" for _ in range(2))
    sql(BILLING, "set xact_abort on; begin tran; "
                 "insert into dbo.invoice_line values (?, 1, '120.01.014', 100.00); "
                 "insert into dbo.invoice_line values (?, 2, '120.01.014', 250.25); "
                 "insert into dbo.invoice_line values (?, 3, '120.01.014', 49.75); "
                 "insert into dbo.invoice_line values (?, 1, '120.01.012', 10.00); commit",
        a, a, a, b)
    wait("an invoice's lines become one journal entry",
         lambda: entry(a) == (400.00, 3) and entry(b) == (10.00, 1))
    sql(BILLING, "update dbo.invoice_line set Amount = 300.25 where InvoiceNo = ? "
                 "and LineNumber = 2", a)
    wait("a changed line changes the total", lambda: entry(a) == (450.00, 3))
    sql(BILLING, "update dbo.invoice_line set InvoiceNo = ?, LineNumber = 2 where "
                 "InvoiceNo = ? and LineNumber = 3", b, a)
    wait("a line moved to another invoice leaves one total for the other",
         lambda: entry(a) == (400.25, 2) and entry(b) == (59.75, 2))
    sql(BILLING, "delete from dbo.invoice_line where InvoiceNo = ? and LineNumber = 1", a)
    wait("a deleted line leaves its total", lambda: entry(a) == (300.25, 1))
    sql(BILLING, "delete from dbo.invoice_line where InvoiceNo in (?, ?)", a, b)
    wait("an invoice with no lines left has no journal entry",
         lambda: entry(a) is None and entry(b) is None)


def main() -> None:
    wait("the first sync settles", lambda: all(linked(t) for t in SEEDED))
    rows = hub("select count(*) from hub.customer where tax_id = any(%s)", list(SEEDED))
    assert rows == [(7,)], f"{rows[0][0]} hub records for seven seeded customers"
    assert hub("select system, reason from hub.unmatched") == []
    print("  ok  seven customers linked across three numberings, not twenty-one")

    code, tax = random.randint(10_000, 99_999), str(random.randint(10**9, 10**10 - 1))
    print(f"customer {code}, tax number {tax}")

    # One CRM transaction, two tables: two flows, and either may land first.
    sql(CRM, "set xact_abort on; begin tran; "
             "insert into dbo.account (ACCOUNT_CODE, TITLE, TAX_NO, ACTIVE) "
             "values (?, ?, ?, 'Y'); "
             "insert into dbo.account_address values (?, 'INV', ?); commit",
        code, "Yeni Müşteri", tax, code, "Sivas")
    wait("a CRM customer and its address arrive in billing as one row",
         lambda: billing_row(tax) == ("Yeni Müşteri", True)
         and billing_cities(tax) == ("Sivas", None))
    wait("the shop has it too, under a number of its own",
         lambda: shop_row(tax) == ("Yeni Müşteri", True, "Sivas"))
    wait("every system numbered it itself, and the hub linked all three codes",
         lambda: linked(tax))
    record = hub("select customer_id from hub.customer where crm_code = %s", code)[0][0]

    sql(CRM, "update dbo.account_address set CITY = ? where ACCOUNT_CODE = ? "
             "and ADDR_TYPE = 'INV'", "Kayseri", code)
    wait("a CRM address row edit reaches billing's column",
         lambda: billing_cities(tax) == ("Kayseri", None))

    sql(BILLING, "update dbo.customer set ShippingCity = ? where TaxId = ?", "Mersin", tax)
    wait("a billing column becomes a CRM address row",
         lambda: crm_address(code, "SHP") == "Mersin")

    sql(BILLING, "update dbo.customer set ShippingCity = null where TaxId = ?", tax)
    wait("clearing it in billing deletes the CRM row, rather than emptying it",
         lambda: crm_address(code, "SHP") is None
         and not sql(CRM, "select 1 from dbo.account_address where ACCOUNT_CODE = ? "
                          "and ADDR_TYPE = 'SHP'", code))

    sql(CRM, "delete from dbo.account_address where ACCOUNT_CODE = ? and ADDR_TYPE = 'INV'", code)
    wait("deleting the CRM address row empties billing's column, not the customer",
         lambda: billing_cities(tax) == (None, None) and billing_row(tax) is not None)

    # A code the CRM's value map does not know: the flow lands it as NULL,
    # and the hub keeps its own value rather than emptying billing's (#84).
    sql(CRM, "update dbo.account set ACTIVE = 'X' where ACCOUNT_CODE = ?", code)
    wait("a value outside the value map is logged, not written as empty",
         lambda: hub("select 1 from hub.conflict where key = %s and field = 'active' "
                     "and reason = 'unmapped'", f'{{"customer_id": {record}}}'))
    assert billing_row(tax) == ("Yeni Müşteri", True), billing_row(tax)
    sql(CRM, "update dbo.account set ACTIVE = 'Y' where ACCOUNT_CODE = ?", code)

    sql(BILLING, "update dbo.customer set IsActive = 0 where TaxId = ?", tax)
    wait("a billing edit reaches the CRM, recoded, and the shop",
         lambda: crm_row(code) == ("Yeni Müşteri", "N") and shop_row(tax)[1] is False)

    # The third system (#82): an edit made in the shop reaches both others.
    shop_sql("update customer set city = %s where vat_number = %s", "Adana", tax)
    wait("a shop edit reaches billing's column and the CRM's address row",
         lambda: billing_cities(tax) == ("Adana", None) and crm_address(code, "INV") == "Adana")

    # Three systems edit one field a second apart: the latest commit wins
    # everywhere, and both others' values are kept as losers.
    sql(CRM, "update dbo.account set TITLE = ? where ACCOUNT_CODE = ?", "From CRM", code)
    time.sleep(1)
    shop_sql("update customer set full_name = %s where vat_number = %s", "From shop", tax)
    time.sleep(1)
    sql(BILLING, "update dbo.customer set Name = ? where TaxId = ?", "From billing", tax)
    wait("a three-way conflict converges on the latest commit in all three",
         lambda: crm_row(code) == ("From billing", "N")
         and billing_row(tax) == ("From billing", False)
         and shop_row(tax)[0] == "From billing")
    lost = hub("select lost, lost_by from hub.conflict where key = %s and reason = 'edit' "
               "and field = 'name' order by id desc limit 2", f'{{"customer_id": {record}}}')
    assert sorted(lost) == [("From CRM", "crm.account"), ("From shop", "shop.customer")], lost
    print("  ok  both losing values are kept in hub.conflict")

    sql(BILLING, "delete from dbo.customer where TaxId = ?", tax)
    wait("a billing delete reaches the CRM and the shop",
         lambda: crm_row(code) is None and shop_row(tax) is None)

    # The other way round: billing numbers a new customer, the CRM its own.
    other = str(int(tax) + 1)
    sql(BILLING, "insert into dbo.customer (Name, TaxId, IsActive) values (?, ?, 1)",
        "Yeni Fatura Müşterisi", other)
    wait("a billing customer reaches the CRM and the shop under their own codes, "
         "linked in the hub", lambda: linked(other))
    shop_sql("delete from customer where vat_number = %s", other)
    wait("a shop delete takes it out of billing and the CRM",
         lambda: billing_row(other) is None
         and not sql(CRM, "select 1 from dbo.account where TAX_NO = ?", other))

    totals()
    polled()

    # The last delivery's echo is still on its way; it is recognised, but it
    # lands. Let it, then watch: a loop keeps writing, a settled pair does not.
    last, quiet = inbox(), 0
    while quiet < 10:
        time.sleep(1)
        now = inbox()
        quiet, last = (quiet + 1, last) if now == last else (0, now)
    time.sleep(30)
    after = inbox()
    assert after == last, f"the inbox grew from {last} to {after} with nothing edited: a loop"
    assert hub("select system, reason from hub.unmatched") == []
    print(f"  ok  no echo loop ({last} inbox rows, unchanged for 30 s), nothing held")


if __name__ == "__main__":
    main()
