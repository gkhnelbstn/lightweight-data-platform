"""Synthetic ERP snapshot with a believable quality history.

Rows carry loaded_at, so a check can be replayed for any past day and the trend
is computed rather than faked. Incidents are scripted:
  day 15  upstream release starts emitting NULL customer_id and status 'PENDING'
  day 22  a rounding bug breaks header-vs-lines reconciliation
  day 30  status mapping fixed
  day 35  a CDC replay duplicates order ids
  day 38  duplicates cleaned, rounding bug fixed
"""
from __future__ import annotations

import os
import random
from datetime import date, timedelta

import psycopg

DSN = os.getenv("ERP_DSN", "postgresql://postgres:postgres@localhost:5432/erp")
DAYS = 45
random.seed(7)

DDL = """
drop table if exists sales_order_lines, sales_orders, customers cascade;
create table customers (
  customer_id bigint, name text, country text, tax_id text, segment text,
  loaded_at date not null);
create table sales_orders (
  order_id bigint, customer_id bigint, order_date date, status text,
  currency text, net_amount numeric, loaded_at date not null);
create table sales_order_lines (
  order_id bigint, line_no int, sku text, qty int, line_amount numeric,
  loaded_at date not null);
create index on sales_orders (loaded_at);
create index on sales_order_lines (loaded_at);
create index on customers (loaded_at);
"""

COUNTRIES = ["TR", "DE", "US", "NL"]
SEGMENTS = ["SMB", "MID", "ENT"]
STATUSES = ["OPEN", "SHIPPED", "INVOICED", "CANCELLED"]


def main() -> None:
    end = date.today()
    start = end - timedelta(days=DAYS - 1)
    with psycopg.connect(DSN, autocommit=True) as cx:
        cx.execute(DDL)
        cust, orders, lines = [], [], []

        for i in range(1, 401):
            cust.append((i, f"Customer {i:03d}", random.choice(COUNTRIES),
                         f"TR{9000000000 + i}", random.choice(SEGMENTS), start))

        oid = 1000
        for d in range(DAYS):
            day = start + timedelta(days=d)
            for _ in range(random.randint(60, 90)):
                oid += 1
                cid = random.randint(1, 400)
                status = random.choice(STATUSES)
                amount = round(random.uniform(50, 25000), 2)

                if 15 <= d < 30 and random.random() < 0.06:
                    status = "PENDING"                # not in the allowed set
                if 15 <= d and random.random() < 0.03:
                    cid = None                        # required field violated
                if status == "CANCELLED" and random.random() < 0.15:
                    pass                              # keeps a non-zero amount
                elif status == "CANCELLED":
                    amount = 0

                orders.append((oid, cid, day, status, random.choice(["TRY", "USD", "EUR"]),
                               amount, day))

                n = random.randint(1, 4)
                part = round(amount / n, 2)
                split = [part] * (n - 1) + [round(amount - part * (n - 1), 2)]
                if 22 <= d < 38 and random.random() < 0.12:
                    split[0] = round(split[0] + random.uniform(1, 50), 2)  # rounding bug
                for k, la in enumerate(split, start=1):
                    lines.append((oid, k, f"SKU-{random.randint(1, 300):03d}",
                                  random.randint(1, 20), la, day))

                if 35 <= d < 38 and random.random() < 0.04:
                    orders.append((oid, cid, day, status, "TRY", amount, day))  # CDC replay

        with cx.cursor() as cur:
            cur.executemany("insert into customers values (%s,%s,%s,%s,%s,%s)", cust)
            cur.executemany("insert into sales_orders values (%s,%s,%s,%s,%s,%s,%s)", orders)
            cur.executemany("insert into sales_order_lines values (%s,%s,%s,%s,%s,%s)", lines)
        cx.execute("analyze")
        print(f"customers={len(cust)} orders={len(orders)} lines={len(lines)} "
              f"range={start}..{end}")


if __name__ == "__main__":
    main()
