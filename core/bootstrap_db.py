"""Create a Postgres database and grant the reader role on it.

`deploy/db-init.sql` makes the two databases the product needs and runs once,
when the volume is empty. Anything created later -- a replication target, a
warehouse a demo builds -- cannot come from there, and asking an operator to
run a `CREATE DATABASE` by hand before a script works is how a quick start
turns into a support thread.

So the thing that needs a database makes it. `CREATE DATABASE` cannot run
inside a transaction, and it cannot run from a connection to the database being
created, which is why this is its own small module rather than two lines
repeated in three places.
"""
from __future__ import annotations

import os

import psycopg
from psycopg import sql

# Schemas a warehouse builds into. Granting on each is what lets the checks run
# against a mart, which is the point of putting a mart under contract at all.
READER = os.getenv("DATACONTRACT_POSTGRES_USERNAME", "dq_reader")


def admin_dsn(host: str, port: int, database: str = "postgres") -> str:
    user = os.getenv("SYNC_USERNAME", "postgres")
    password = os.getenv("SYNC_PASSWORD", "postgres")
    return (f"host={host} port={port} dbname={database} "
            f"user={user} password={password}")


def ensure_database(host: str, port: int, name: str) -> bool:
    """`True` when it had to be created."""
    with psycopg.connect(admin_dsn(host, port), autocommit=True) as cx:
        exists = cx.execute("select 1 from pg_database where datname = %s",
                            (name,)).fetchone()
        if exists:
            return False
        cx.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    return True


def grant_reader(host: str, port: int, name: str, schemas: list[str]) -> None:
    """Let the read-only role see the schemas, and whatever appears in them
    later -- a warehouse table is created by a loader, long after this runs."""
    with psycopg.connect(admin_dsn(host, port, name), autocommit=True) as cx:
        role = sql.Identifier(READER)
        if not cx.execute("select 1 from pg_roles where rolname = %s",
                          (READER,)).fetchone():
            return
        cx.execute(sql.SQL("grant connect on database {} to {}").format(
            sql.Identifier(name), role))
        for schema in schemas:
            s = sql.Identifier(schema)
            cx.execute(sql.SQL("create schema if not exists {}").format(s))
            cx.execute(sql.SQL("grant usage on schema {} to {}").format(s, role))
            cx.execute(sql.SQL(
                "grant select on all tables in schema {} to {}").format(s, role))
            cx.execute(sql.SQL("alter default privileges in schema {} "
                               "grant select on tables to {}").format(s, role))
