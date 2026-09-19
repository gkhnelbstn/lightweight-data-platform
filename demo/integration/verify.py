"""Drive the running two-way demo and assert what ADR 0021 promises.

Uses a fresh customer code every run, so it can be repeated against a stack
that has already been played with:

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


def sql(server: dict, statement: str, *args):
    with mssql_connect(server) as cx:
        cx.autocommit = True
        cur = cx.cursor().execute(statement, *args)
        return cur.fetchall() if cur.description else None


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


def billing_row(code):
    rows = sql(BILLING, "select Name, IsActive from dbo.customer where CustomerCode = ?", code)
    return (rows[0][0], bool(rows[0][1])) if rows else None


def inbox() -> int:
    with psycopg.connect(HUB) as cx:
        return cx.execute("select count(*) from hub.customer_inbox").fetchone()[0]


def main() -> None:
    code = random.randint(10_000, 99_999)
    print(f"customer {code}")

    sql(CRM, "insert into dbo.account (ACCOUNT_CODE, TITLE, ACTIVE) values (?, ?, 'Y')",
        code, "Yeni Müşteri")
    wait("a CRM insert reaches billing", lambda: billing_row(code) == ("Yeni Müşteri", True))

    sql(BILLING, "update dbo.customer set IsActive = 0 where CustomerCode = ?", code)
    wait("a billing edit reaches the CRM, recoded", lambda: crm_row(code) == ("Yeni Müşteri", "N"))

    sql(CRM, "update dbo.account set TITLE = ? where ACCOUNT_CODE = ?", "From CRM", code)
    time.sleep(1)
    sql(BILLING, "update dbo.customer set Name = ? where CustomerCode = ?", "From billing", code)
    wait("concurrent edits converge on the later commit",
         lambda: crm_row(code) == ("From billing", "N") and billing_row(code) == ("From billing", False))
    with psycopg.connect(HUB) as cx:
        lost = cx.execute("select lost, lost_by from hub.conflict where key = %s "
                          "and reason = 'edit' order by id desc limit 1",
                          (f'{{"code": {code}}}',)).fetchone()
    assert lost == ("From CRM", "crm.account"), lost
    print("  ok  the losing value is kept in hub.conflict")

    sql(BILLING, "delete from dbo.customer where CustomerCode = ?", code)
    wait("a billing delete reaches the CRM", lambda: crm_row(code) is None)

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
    print(f"  ok  no echo loop ({last} inbox rows, unchanged for 30 s)")


if __name__ == "__main__":
    main()
