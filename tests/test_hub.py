"""The hub's merge, against a real PostgreSQL. ADR 0021.

Each test plays SeaTunnel: it inserts rows into the inbox the way a system's
CDC would deliver them -- before image, after image, commit time -- and reads
what the hub decided. Two systems, `crm` and `billing`, share one customer
record. Skips without a server, like tests/test_store.py:

    docker compose -f compose.yaml -f compose.demo.yaml --profile demo up -d
    DWH_PORT=5442 pytest tests/test_hub.py
"""
from __future__ import annotations

import json
import os

import pytest

psycopg = pytest.importorskip("psycopg")

HOST = os.getenv("DWH_HOST", "localhost")
PORT = int(os.getenv("DWH_PORT", "5432"))
SCRATCH = "hub_test"
COLUMNS = {"code": "int", "name": "text", "active": "boolean"}


@pytest.fixture()
def hub():
    from core import hub as h
    from core.bootstrap_db import admin_dsn, ensure_database
    try:
        with psycopg.connect(admin_dsn(HOST, PORT), connect_timeout=2,
                             autocommit=True) as cx:
            cx.execute(f'drop database if exists "{SCRATCH}" with (force)')
    except psycopg.OperationalError as exc:
        pytest.skip(f"no PostgreSQL at {HOST}:{PORT} ({exc.__class__.__name__})")
    ensure_database(HOST, PORT, SCRATCH)
    cx = psycopg.connect(admin_dsn(HOST, PORT, SCRATCH), autocommit=True)
    h.init(cx)
    h.register_entity(cx, "customer", ["code"], COLUMNS)
    for system in ("crm", "billing"):
        h.register_system(cx, "customer", system)
    try:
        yield cx
    finally:
        cx.close()
        with psycopg.connect(admin_dsn(HOST, PORT), autocommit=True) as adm:
            adm.execute(f'drop database if exists "{SCRATCH}" with (force)')


def send(cx, system, kind, ms, **row):
    cx.execute("insert into hub.customer_inbox (system, row_kind, source_ms, code, name, active) "
               "values (%s, %s, %s, %s, %s, %s)",
               (system, kind, ms, row["code"], row["name"], row["active"]))


def update(cx, system, ms, old, new):
    send(cx, system, "UPDATE_BEFORE", ms, **old)
    send(cx, system, "UPDATE_AFTER", ms, **new)


def golden(cx, code=1):
    return cx.execute("select name, active, _by from hub.customer where code = %s",
                      (code,)).fetchone()


def expected(cx, system):
    return cx.execute("select field, value from hub.expect where system = %s order by seq",
                      (system,)).fetchall()


def conflicts(cx):
    return cx.execute("select field, kept, kept_by, lost, lost_by from hub.conflict "
                      "order by id").fetchall()


ACME = {"code": 1, "name": "Acme", "active": True}


def test_a_new_record_is_expected_back_from_everyone_else(hub):
    send(hub, "crm", "INSERT", 100, **ACME)
    assert golden(hub)[:2] == ("Acme", True)
    assert expected(hub, "billing") == [("name", "Acme"), ("active", True)]
    assert expected(hub, "crm") == []


def test_the_delivery_coming_back_changes_nothing(hub):
    send(hub, "crm", "INSERT", 100, **ACME)
    send(hub, "billing", "INSERT", 105, **ACME)     # SeaTunnel wrote it there
    assert expected(hub, "billing") == []
    assert conflicts(hub) == []
    assert golden(hub)[2] == {"name": "crm", "active": "crm"}


def test_an_edit_of_the_current_value_goes_everywhere_else(hub):
    send(hub, "crm", "INSERT", 100, **ACME)
    send(hub, "billing", "INSERT", 105, **ACME)
    update(hub, "billing", 200, ACME, {**ACME, "name": "Acme Ltd"})
    assert golden(hub)[0] == "Acme Ltd"
    assert expected(hub, "crm") == [("name", "Acme Ltd")]
    assert conflicts(hub) == []


def test_an_untouched_field_is_not_an_edit(hub):
    send(hub, "crm", "INSERT", 100, **ACME)
    send(hub, "billing", "INSERT", 105, **ACME)
    update(hub, "billing", 200, ACME, {**ACME, "active": False})
    assert expected(hub, "crm") == [("active", False)]


def test_a_stale_delivery_coming_back_late_is_still_an_echo(hub):
    """The hub sent 'B' then 'C' to crm; crm reports them after both were
    decided. Neither may be read as crm editing the record."""
    send(hub, "crm", "INSERT", 100, **ACME)
    send(hub, "billing", "INSERT", 105, **ACME)
    update(hub, "billing", 200, ACME, {**ACME, "name": "B"})
    update(hub, "billing", 300, {**ACME, "name": "B"}, {**ACME, "name": "C"})
    update(hub, "crm", 400, ACME, {**ACME, "name": "B"})
    update(hub, "crm", 401, {**ACME, "name": "B"}, {**ACME, "name": "C"})
    assert golden(hub)[0] == "C"
    assert expected(hub, "crm") == []
    assert conflicts(hub) == []


def test_editing_the_current_value_wins_even_behind_the_clock(hub):
    """billing saw 'Acme' and changed it. Its server clock is behind crm's,
    so the commit time is earlier than the value it replaced -- but it edited
    what everyone had, so it is an edit, not a conflict."""
    send(hub, "crm", "INSERT", 500, **ACME)
    send(hub, "billing", "INSERT", 505, **ACME)
    update(hub, "billing", 300, ACME, {**ACME, "name": "Acme Ltd"})
    assert golden(hub)[0] == "Acme Ltd"
    assert conflicts(hub) == []


def test_concurrent_edits_the_later_commit_wins_whatever_arrives_first(hub):
    send(hub, "crm", "INSERT", 100, **ACME)
    send(hub, "billing", "INSERT", 105, **ACME)
    # billing committed later (t=210) but its change reaches the hub second.
    update(hub, "crm", 200, ACME, {**ACME, "name": "From CRM"})
    update(hub, "billing", 210, ACME, {**ACME, "name": "From billing"})
    assert golden(hub)[0] == "From billing"
    assert conflicts(hub) == [("name", "From billing", "billing", "From CRM", "crm")]
    assert expected(hub, "crm") == [("name", "From billing")]


def test_concurrent_edits_the_earlier_commit_loses_and_is_corrected(hub):
    send(hub, "crm", "INSERT", 100, **ACME)
    send(hub, "billing", "INSERT", 105, **ACME)
    update(hub, "crm", 200, ACME, {**ACME, "name": "From CRM"})
    # billing committed earlier (t=190) and arrives after: it lost.
    update(hub, "billing", 190, ACME, {**ACME, "name": "From billing"})
    assert golden(hub)[0] == "From CRM"
    assert conflicts(hub) == [("name", "From CRM", "crm", "From billing", "billing")]
    # billing is sent the winner, and its arrival there is an echo.
    assert ("name", "From CRM") in expected(hub, "billing")
    rev = hub.execute("select _rev from hub.customer").fetchone()[0]
    update(hub, "billing", 250, {**ACME, "name": "From billing"}, {**ACME, "name": "From CRM"})
    assert golden(hub)[0] == "From CRM"
    assert hub.execute("select _rev from hub.customer").fetchone()[0] == rev
    assert len(conflicts(hub)) == 1


def test_a_delete_later_than_every_edit_goes_everywhere(hub):
    send(hub, "crm", "INSERT", 100, **ACME)
    send(hub, "billing", "INSERT", 105, **ACME)
    send(hub, "crm", "DELETE", 300, **ACME)
    assert golden(hub) is None
    assert expected(hub, "billing") == [("*", None)]
    send(hub, "billing", "DELETE", 310, **ACME)     # the delivery, echoed
    assert expected(hub, "billing") == []


def test_a_delete_older_than_an_edit_is_refused_and_undone(hub):
    send(hub, "crm", "INSERT", 100, **ACME)
    send(hub, "billing", "INSERT", 105, **ACME)
    update(hub, "billing", 300, ACME, {**ACME, "name": "Kept"})
    send(hub, "crm", "DELETE", 250, **ACME)           # committed before the edit
    assert golden(hub)[0] == "Kept"
    assert conflicts(hub)[-1][0] == "*"
    assert sorted(expected(hub, "crm")) == [("active", True), ("name", "Kept")]


def test_an_unknown_entity_is_refused(hub):
    with pytest.raises(psycopg.errors.RaiseException, match="not a registered entity"):
        hub.execute("select hub.merge('supplier', 'crm', 'INSERT', 1, %s)",
                    (json.dumps({"code": 1}),))


def test_reserved_columns_are_refused():
    from core import hub as h
    with pytest.raises(ValueError, match="reserved"):
        h.register_entity(None, "x", ["id"], {"id": "int", "_rev": "int"})
