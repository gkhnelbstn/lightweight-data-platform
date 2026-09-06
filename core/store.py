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
  -- What a person needs to read the row without opening the contract: the
  -- rule in words, what kind of check it was, and the sentence datacontract
  -- wrote about why it failed. `sql` is the statement that actually ran,
  -- which is also what core/sample.py rewrites to show the failing rows.
  name text not null default '',
  check_type text not null default '',
  field text,
  reason text,
  sql text,
  primary key (run_at, check_id, run_window)
) partition by range (run_at);

-- Installs that predate the columns above.
alter table check_results add column if not exists name text not null default '';
alter table check_results add column if not exists check_type text not null default '';
alter table check_results add column if not exists field text;
alter table check_results add column if not exists reason text;
alter table check_results add column if not exists sql text;

create index if not exists check_results_brin on check_results using brin (run_at);

create table if not exists odd_pushes (
  target text not null,
  run_at date not null,
  pushed_at timestamptz not null default now(),
  entities int not null,
  primary key (target, run_at)
);

-- How far core/sync_mssql.py has read each CDC change table. Restarting from
-- the change table's minimum LSN would replay the whole retained window on
-- every pass, so the position is stored rather than recomputed.
create table if not exists sync_watermarks (
  source text primary key,
  lsn bytea not null,
  updated_at timestamptz not null default now()
);

-- Which ODD link belongs to which contract. ODD appends links rather than
-- replacing them and offers no way to read an entity's links back, so the ids
-- it hands out on creation are ours to remember or the nightly run leaves a
-- growing pile of identical attachments.
create table if not exists odd_links (
  contract_id text not null,
  name text not null,
  link_id integer not null,
  primary key (contract_id, name)
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
  -- Checks that could not run. Kept apart from checks_failed because they say
  -- something different: the first is bad data, the second is a broken
  -- connection, a missing table or a rule that will not compile.
  checks_errored int not null default 0,
  primary key (run_at, contract_id, run_window)
);

alter table contract_scores add column if not exists checks_errored int not null default 0;
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
    """One run's checks. Re-running a day replaces it rather than appending.

    Replaces, not merges: a check that existed in an earlier run of the same
    day and does not exist now is deleted. Without this an errored check --
    `missing_env_DATACONTRACT_SQLSERVER_USERNAME`, from a run before the
    credentials were set -- survives every later run of that day and keeps
    showing as an open failure, because an upsert never removes anything. The
    same reasoning as the `results outlive checks` note in CLAUDE.md, one
    level down: within a day, this run is the truth.
    """
    conn.execute(
        """delete from check_results
           where run_at = %s and contract_id = %s and run_window = %s
             and check_id <> all(%s)""",
        (run_at, contract_id, window, [r["check_id"] for r in rows] or [""]))
    for r in rows:
        conn.execute(
            """insert into check_results (run_at,check_id,contract_id,dimension,status,
                   failed_rows,total_rows,fail_ratio,duration_ms,run_window,
                   name,check_type,field,reason,sql)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               on conflict (run_at,check_id,run_window) do update set
                 status=excluded.status, failed_rows=excluded.failed_rows,
                 total_rows=excluded.total_rows, fail_ratio=excluded.fail_ratio,
                 dimension=excluded.dimension, duration_ms=excluded.duration_ms,
                 name=excluded.name, check_type=excluded.check_type,
                 field=excluded.field, reason=excluded.reason, sql=excluded.sql""",
            (run_at, r["check_id"], contract_id, r["dimension"], r["status"],
             r["failed_rows"], r["total_rows"], r["fail_ratio"],
             r.get("duration_ms", 0), window,
             r.get("name", ""), r.get("check_type", ""), r.get("field"),
             r.get("reason"), r.get("sql")))


def write_score(conn, run_at: date, contract_id: str, score: float,
                total: int, failed: int, sla_min: float,
                window: str = "incremental", errored: int = 0) -> None:
    """A run meets its SLA only if it also managed to run.

    The score deliberately ignores checks that errored -- see core/scoring.py
    -- so without this an unreachable source would score 1.0 and pass.
    """
    conn.execute(
        """insert into contract_scores (run_at,contract_id,score,checks_total,
               checks_failed,sla_min,sla_met,run_window,checks_errored)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
           on conflict (run_at,contract_id,run_window) do update set
             score=excluded.score, checks_total=excluded.checks_total,
             checks_failed=excluded.checks_failed, sla_min=excluded.sla_min,
             sla_met=excluded.sla_met, checks_errored=excluded.checks_errored""",
        (run_at, contract_id, score, total, failed, sla_min,
         score >= float(sla_min) and errored == 0, window, errored))
