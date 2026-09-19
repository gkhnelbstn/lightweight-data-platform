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
CRM = {"host": "mssql", "database": "crm"}
BILLING = {"host": "mssql", "database": "billing"}
SEEDED = ("1234567890", "2345678901", "3456789012", "4567890123", "5678901234",
          "6789012345")


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
    return hub("select count(*) from hub.customer_inbox")[0][0]


def linked(tax) -> bool:
    return bool(hub("select 1 from hub.customer where tax_id = %s and crm_code is not "
                    "null and billing_code is not null", tax))


def main() -> None:
    wait("the first sync settles", lambda: all(linked(t) for t in SEEDED))
    rows = hub("select count(*) from hub.customer where tax_id = any(%s)", list(SEEDED))
    assert rows == [(6,)], f"{rows[0][0]} hub records for six seeded customers"
    assert hub("select system, reason from hub.unmatched") == []
    print("  ok  six customers linked across two numberings, not twelve")

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
    wait("billing numbered it itself, and the hub linked both codes", lambda: linked(tax))
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

    sql(BILLING, "update dbo.customer set IsActive = 0 where TaxId = ?", tax)
    wait("a billing edit reaches the CRM, recoded", lambda: crm_row(code) == ("Yeni Müşteri", "N"))

    sql(CRM, "update dbo.account set TITLE = ? where ACCOUNT_CODE = ?", "From CRM", code)
    time.sleep(1)
    sql(BILLING, "update dbo.customer set Name = ? where TaxId = ?", "From billing", tax)
    wait("concurrent edits converge on the later commit",
         lambda: crm_row(code) == ("From billing", "N")
         and billing_row(tax) == ("From billing", False))
    lost = hub("select lost, lost_by from hub.conflict where key = %s and reason = 'edit' "
               "order by id desc limit 1", f'{{"customer_id": {record}}}')
    assert lost == [("From CRM", "crm.account")], lost
    print("  ok  the losing value is kept in hub.conflict")

    sql(BILLING, "delete from dbo.customer where TaxId = ?", tax)
    wait("a billing delete reaches the CRM", lambda: crm_row(code) is None)

    # The other way round: billing numbers a new customer, the CRM its own.
    other = str(int(tax) + 1)
    sql(BILLING, "insert into dbo.customer (Name, TaxId, IsActive) values (?, ?, 1)",
        "Yeni Fatura Müşterisi", other)
    wait("a billing customer reaches the CRM under a CRM code, linked in the hub",
         lambda: linked(other))
    sql(CRM, "delete from dbo.account where TAX_NO = ?", other)
    wait("a CRM delete takes it out of billing", lambda: billing_row(other) is None)

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
