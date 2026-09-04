"""Metadata + results store.

Plain PostgreSQL. Monthly range partitions and a BRIN index give us the time
series behaviour we need at this data volume; no TimescaleDB, so no TSL license
surface. Swap in hypertables the day the row count actually justifies it.
"""
from __future__ import annotations

import json
import os
from datetime import date

import psycopg

DQ_DSN = os.getenv("DQ_DSN", "postgresql://postgres:postgres@localhost:5432/dq")
ERP_DSN = os.getenv("ERP_DSN", "postgresql://postgres:postgres@localhost:5432/erp")

DDL = """
create table if not exists contracts (
  id text primary key,
  title text not null,
  owner text not null,
  domain text,
  source_table text not null,
  min_score numeric not null default 0.95,
  spec jsonb not null,
  updated_at timestamptz not null default now()
);

create table if not exists checks (
  id text primary key,
  contract_id text not null references contracts(id) on delete cascade,
  kind text not null,
  severity text not null,
  column_name text,
  origin text not null,
  description text,
  params jsonb not null default '{}'::jsonb,
  compiled_sql text
);

create table if not exists check_results (
  run_at date not null,
  check_id text not null,
  contract_id text not null,
  severity text not null,
  status text not null,
  failed_rows bigint not null,
  total_rows bigint not null,
  fail_ratio numeric not null,
  duration_ms integer not null,
  run_window text not null default 'incremental',
  primary key (run_at, check_id, run_window)
) partition by range (run_at);

create index if not exists check_results_brin on check_results using brin (run_at);

create table if not exists contract_scores (
  run_at date not null,
  contract_id text not null,
  score numeric not null,
  checks_total int not null,
  checks_failed int not null,
  sla_min numeric not null,
  sla_met boolean not null,
  run_window text not null default 'incremental',
  primary key (run_at, contract_id, run_window)
);
"""


def connect(dsn: str = DQ_DSN) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=True)


def ensure_partition(conn: psycopg.Connection, day: date) -> None:
    start = day.replace(day=1)
    end = (start.replace(year=start.year + 1, month=1) if start.month == 12
           else start.replace(month=start.month + 1))
    name = f"check_results_{start:%Y_%m}"
    conn.execute(
        f"create table if not exists {name} partition of check_results "
        f"for values from ('{start}') to ('{end}')")


def init(conn: psycopg.Connection) -> None:
    conn.execute(DDL)


def upsert_contract(conn, contract, checks, compiled: dict[str, str]) -> None:
    conn.execute(
        """insert into contracts (id,title,owner,domain,source_table,min_score,spec,updated_at)
           values (%s,%s,%s,%s,%s,%s,%s,now())
           on conflict (id) do update set title=excluded.title, owner=excluded.owner,
             domain=excluded.domain, source_table=excluded.source_table,
             min_score=excluded.min_score, spec=excluded.spec, updated_at=now()""",
        (contract.id, contract.info.title, contract.info.owner,
         contract.info.domain, contract.server.table, contract.sla.min_score,
         json.dumps(contract.model_dump(by_alias=True, mode="json"))))
    conn.execute("delete from checks where contract_id = %s", (contract.id,))
    for c in checks:
        conn.execute(
            """insert into checks (id,contract_id,kind,severity,column_name,origin,
                                   description,params,compiled_sql)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (c.id, c.contract_id, c.kind, c.severity, c.column, c.origin,
             c.description, json.dumps(c.params), compiled.get(c.id)))
