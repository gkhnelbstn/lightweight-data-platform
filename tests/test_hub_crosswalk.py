"""The hub's crosswalk, against a real PostgreSQL. #80, ADR 0021.

The same customer is `1` in the CRM and `B-7` in billing. The golden record
has a key of its own, and each system's key is a column of it. Each test plays
SeaTunnel, as tests/test_hub.py does, and skips without a server:

    DWH_PORT=5442 pytest tests/test_hub_crosswalk.py
"""
from __future__ import annotations

import os

import pytest

psycopg = pytest.importorskip("psycopg")

HOST = os.getenv("DWH_HOST", "localhost")
PORT = int(os.getenv("DWH_PORT", "5432"))
SCRATCH = "hub_crosswalk_test"
COLUMNS = {"customer_id": "bigint", "crm_code": "int", "billing_code": "text",
           "name": "text", "tax_id": "text", "city": "text"}


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
    h.register_entity(cx, "customer", ["customer_id"], COLUMNS, authority="crm.account",
                      required=["name"], keys={"crm": "crm_code", "billing": "billing_code"})
    h.register_system(cx, "customer", "crm.account", ["crm_code", "name", "tax_id"],
                      link_by=["tax_id"])
    h.register_system(cx, "customer", "crm.address", ["crm_code", "city"])
    h.register_system(cx, "customer", "billing.customer",
                      ["billing_code", "name", "tax_id", "city"], link_by=["tax_id"])
    try:
        yield cx
    finally:
        cx.close()
        with psycopg.connect(admin_dsn(HOST, PORT), autocommit=True) as adm:
            adm.execute(f'drop database if exists "{SCRATCH}" with (force)')


def send(cx, system, kind, ms, **row):
    cols = list(row)
    cx.execute(f"insert into hub.customer_inbox (system, row_kind, source_ms, fields, "
               f"{', '.join(cols)}) values (%s, %s, %s, %s, {', '.join(['%s'] * len(cols))})",
               (system, kind, ms, ",".join(cols), *row.values()))


def records(cx):
    return cx.execute("select crm_code, billing_code, name, city from hub.customer "
                      "order by customer_id").fetchall()


def held(cx):
    return cx.execute("select system, local, reason from hub.unmatched "
                      "order by system").fetchall()


def conflicts(cx):
    return cx.execute("select field, reason from hub.conflict order by id").fetchall()


def expected(cx, system):
    return cx.execute("select field, value from hub.expect where system = %s order by seq",
                      (system,)).fetchall()


CRM = {"crm_code": 1, "name": "Acme", "tax_id": "111"}
BILLING = {"billing_code": "B-7", "name": "Acme", "tax_id": "111"}


def test_the_first_sync_links_one_customer_under_two_codes(hub):
    send(hub, "crm.account", "INSERT", 100, **CRM)
    send(hub, "billing.customer", "INSERT", 105, **BILLING, city=None)
    assert records(hub) == [(1, "B-7", "Acme", None)]
    assert conflicts(hub) == [] and held(hub) == []


def test_a_customer_matching_nothing_is_a_new_record(hub):
    send(hub, "crm.account", "INSERT", 100, **CRM)
    send(hub, "billing.customer", "INSERT", 105, billing_code="B-8", name="Other",
         tax_id="222", city="Ankara")
    assert records(hub) == [(1, None, "Acme", None), (None, "B-8", "Other", "Ankara")]
    assert expected(hub, "crm.account") == [("name", "Other"), ("tax_id", "222")]


def test_the_echo_of_a_record_the_hub_made_links_it_quietly(hub):
    """The CRM's new customer reaches billing, which gives it a code of its
    own; the insert coming back is how the hub learns it."""
    send(hub, "crm.account", "INSERT", 100, **CRM)
    send(hub, "billing.customer", "INSERT", 200, **BILLING, city=None)
    assert records(hub) == [(1, "B-7", "Acme", None)]
    assert expected(hub, "billing.customer") == [] and conflicts(hub) == []
    # The link is news to no one: billing made it, the CRM has no use for it.
    assert hub.execute("select _changed, _skip from hub.customer").fetchone() == (
        ",billing_code,", "billing.customer")


def test_the_authority_wins_a_linked_first_sync(hub):
    send(hub, "billing.customer", "INSERT", 100, **{**BILLING, "name": "Acme Ltd"}, city=None)
    send(hub, "crm.account", "INSERT", 105, **CRM)
    assert records(hub) == [(1, "B-7", "Acme", None)]
    assert conflicts(hub) == [("name", "seed")]


def test_nothing_to_match_by_is_held_not_created(hub):
    send(hub, "billing.customer", "INSERT", 100, **{**BILLING, "tax_id": None}, city=None)
    assert records(hub) == []
    assert held(hub) == [("billing.customer", {"billing_code": "B-7"}, "unmatchable")]


def test_a_second_key_of_one_system_for_one_record_is_refused(hub):
    send(hub, "crm.account", "INSERT", 100, **CRM)
    send(hub, "crm.account", "INSERT", 105, **{**CRM, "crm_code": 2, "name": "Acme again"})
    assert records(hub) == [(1, None, "Acme", None)]
    assert held(hub) == [("crm.account", {"crm_code": 2}, "taken")]


def test_a_person_decides_what_the_rule_cannot(hub):
    send(hub, "crm.account", "INSERT", 100, **CRM)
    send(hub, "crm.account", "INSERT", 105, **{**CRM, "crm_code": 2, "name": "Acme branch"})
    # A different customer after all: a record of its own.
    hub.execute("select hub.link('customer', 'crm.account', '{\"crm_code\": 2}')")
    assert records(hub) == [(1, None, "Acme", None), (2, None, "Acme branch", None)]
    # Two records now share the tax identifier, so billing's cannot pick one.
    send(hub, "billing.customer", "INSERT", 110, **BILLING, city=None)
    assert held(hub) == [("billing.customer", {"billing_code": "B-7"}, "ambiguous")]
    first = hub.execute("select customer_id from hub.customer where crm_code = 1").fetchone()[0]
    hub.execute("select hub.link('customer', 'billing.customer', '{\"billing_code\": \"B-7\"}', "
                "%s::jsonb)", (f'{{"customer_id": {first}}}',))
    assert records(hub)[0] == (1, "B-7", "Acme", None)
    assert held(hub) == []


def test_a_record_is_linked_to_one_key_per_system(hub):
    send(hub, "crm.account", "INSERT", 100, **CRM)
    send(hub, "crm.account", "INSERT", 105, **{**CRM, "crm_code": 2})
    first = hub.execute("select customer_id from hub.customer").fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException, match="already"):
        hub.execute("select hub.link('customer', 'crm.account', '{\"crm_code\": 2}', %s::jsonb)",
                    (f'{{"customer_id": {first}}}',))


def test_a_part_waits_for_the_row_that_places_it(hub):
    """An address can say nothing about who the customer is; it waits for
    its customer row, then follows it."""
    send(hub, "crm.address", "INSERT", 100, crm_code=1, city="Ankara")
    assert records(hub) == []
    assert held(hub) == [("crm.address", {"crm_code": 1}, "part")]
    send(hub, "crm.account", "INSERT", 101, **CRM)
    assert records(hub) == [(1, None, "Acme", "Ankara")]
    assert held(hub) == []


def test_a_part_follows_its_row_into_a_record_another_system_made(hub):
    send(hub, "billing.customer", "INSERT", 100, **BILLING, city=None)
    send(hub, "crm.address", "INSERT", 110, crm_code=1, city="Ankara")
    send(hub, "crm.account", "INSERT", 111, **CRM)
    assert records(hub) == [(1, "B-7", "Acme", "Ankara")]
    assert held(hub) == []


def test_a_late_change_under_a_deleted_records_key_meets_its_tombstone(hub):
    send(hub, "crm.account", "INSERT", 100, **CRM)
    send(hub, "billing.customer", "INSERT", 105, **BILLING, city=None)
    send(hub, "crm.account", "DELETE", 300, **CRM)
    assert records(hub) == []
    # billing edited before it saw the delete: it loses, and makes nothing.
    send(hub, "billing.customer", "UPDATE_BEFORE", 250, **BILLING, city=None)
    send(hub, "billing.customer", "UPDATE_AFTER", 250, **BILLING, city="İzmir")
    assert records(hub) == [] and held(hub) == []


def test_a_held_row_deleted_is_forgotten(hub):
    send(hub, "billing.customer", "INSERT", 100, **{**BILLING, "tax_id": None}, city=None)
    send(hub, "billing.customer", "DELETE", 110, **{**BILLING, "tax_id": None}, city=None)
    assert held(hub) == []
