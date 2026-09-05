"""Metadata + results store.

Plain PostgreSQL. Monthly range partitions and a BRIN index give us the time
series behaviour we need at this data volume; no TimescaleDB, so no TSL license
surface. Swap in hypertables the day the row count actually justifies it.
"""
from __future__ import annotations

import os
from datetime import date

import psycopg

DQ_DSN = os.getenv("DQ_DSN", "postgresql://postgres:postgres@localhost:5432/dq")
ERP_DSN = os.getenv("ERP_DSN", "postgresql://postgres:postgres@localhost:5432/erp")

DDL = """
create table if not exists check_results (
  run_at date not null,
  check_id text not null,
  contract_id text not null,
  dimension text not null default 'unknown',
  status text not null,
  failed_rows bigint not null,
  total_rows bigint not null,
  fail_ratio numeric not null,
  duration_ms integer not null,
  run_window text not null default 'incremental',
  primary key (run_at, check_id, run_window)
) partition by range (run_at);

create index if not exists check_results_brin on check_results using brin (run_at);

create table if not exists odd_pushes (
  target text not null,
  run_at date not null,
  pushed_at timestamptz not null default now(),
  entities int not null,
  primary key (target, run_at)
);

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


def write_results(conn, run_at: date, contract_id: str, rows: list[dict],
                  window: str = "incremental") -> None:
    """One run's checks. Re-running a day replaces it rather than appending."""
    for r in rows:
        conn.execute(
            """insert into check_results (run_at,check_id,contract_id,dimension,status,
                   failed_rows,total_rows,fail_ratio,duration_ms,run_window)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               on conflict (run_at,check_id,run_window) do update set
                 status=excluded.status, failed_rows=excluded.failed_rows,
                 total_rows=excluded.total_rows, fail_ratio=excluded.fail_ratio,
                 dimension=excluded.dimension, duration_ms=excluded.duration_ms""",
            (run_at, r["check_id"], contract_id, r["dimension"], r["status"],
             r["failed_rows"], r["total_rows"], r["fail_ratio"],
             r.get("duration_ms", 0), window))


def write_score(conn, run_at: date, contract_id: str, score: float,
                total: int, failed: int, sla_min: float,
                window: str = "incremental") -> None:
    conn.execute(
        """insert into contract_scores (run_at,contract_id,score,checks_total,
               checks_failed,sla_min,sla_met,run_window)
           values (%s,%s,%s,%s,%s,%s,%s,%s)
           on conflict (run_at,contract_id,run_window) do update set
             score=excluded.score, checks_total=excluded.checks_total,
             checks_failed=excluded.checks_failed, sla_min=excluded.sla_min,
             sla_met=excluded.sla_met""",
        (run_at, contract_id, score, total, failed, sla_min,
         score >= float(sla_min), window))
