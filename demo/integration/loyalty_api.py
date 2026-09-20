"""The loyalty scheme's API: the demo's HTTP source. #97, ADR 0027.

It stands in for somebody else's system. The platform reads it over HTTP and
never writes to it, and it answers with the members as they are now -- no
change log, no before images, no deletes. Its own store is a database the
flow never touches; reading the endpoint instead is the point of the exercise.

It is the app image with a different command, like `sync-mssql`:

    uvicorn demo.integration.loyalty_api:app --host 0.0.0.0 --port 8099
"""
from __future__ import annotations

import os

import psycopg
from fastapi import FastAPI

DSN = os.getenv("LOYALTY_DSN",
                "host=db dbname=loyalty user=postgres password=postgres")

FIELDS = ("member_no", "full_name", "vkn", "is_active", "tier", "updated_ms")

app = FastAPI(title="Loyalty scheme")


@app.get("/members")
def members() -> dict:
    """Every member the scheme has, in one answer.

    No paging: a page the caller never asked for is a record that looks
    deleted, and nothing here deletes anyway (ADR 0027). `updated_ms` is when
    the scheme last changed the member, in epoch milliseconds -- the hub
    orders it against the other systems' commit times by that number, so
    converting it is the scheme's half of the job, not the platform's.
    """
    with psycopg.connect(DSN) as cx:
        rows = cx.execute(
            "select member_no, full_name, vkn, is_active, tier, "
            "(extract(epoch from updated_at) * 1000)::bigint "
            "from member order by member_no").fetchall()
    return {"members": [dict(zip(FIELDS, row)) for row in rows]}
