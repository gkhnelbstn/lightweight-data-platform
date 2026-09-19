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
COLUMNS = {"code": "int", "name": "text", "active": "boolean", "city": "text"}


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
    h.register_entity(cx, "customer", ["code"], COLUMNS, authority="crm",
                      required=["code", "name"])
    # crm keeps the customer and its address in two tables (#79).
    h.register_system(cx, "customer", "crm", ["code", "name", "active"])
    h.register_system(cx, "customer", "crm_address", ["code", "city"])
    h.register_system(cx, "customer", "billing")
    try:
        yield cx
    finally:
        cx.close()
        with psycopg.connect(admin_dsn(HOST, PORT), autocommit=True) as adm:
            adm.execute(f'drop database if exists "{SCRATCH}" with (force)')


def send(cx, system, kind, ms, **row):
    """One inbox row, carrying exactly the fields its flow declares."""
    cols = list(row)
    cx.execute(f"insert into hub.customer_inbox (system, row_kind, source_ms, fields, "
               f"{', '.join(cols)}) values (%s, %s, %s, %s, {', '.join(['%s'] * len(cols))})",
               (system, kind, ms, ",".join(cols), *row.values()))


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


def test_a_delivery_echoing_back_after_a_delete_does_not_resurrect_it(hub):
    """Found live: billing won a conflict, then deleted the record; the
    winner's delivery to crm came back after the delete and recreated it."""
    send(hub, "crm", "INSERT", 100, **ACME)
    send(hub, "billing", "INSERT", 105, **ACME)
    update(hub, "billing", 200, ACME, {**ACME, "name": "Won"})
    send(hub, "billing", "DELETE", 300, **{**ACME, "name": "Won"})
    update(hub, "crm", 310, ACME, {**ACME, "name": "Won"})      # the delivery, late
    assert golden(hub) is None
    assert conflicts(hub) == []


def test_an_edit_older_than_a_delete_loses_to_it(hub):
    send(hub, "crm", "INSERT", 100, **ACME)
    send(hub, "billing", "INSERT", 105, **ACME)
    send(hub, "billing", "DELETE", 300, **ACME)
    update(hub, "crm", 250, ACME, {**ACME, "name": "Edited before the delete"})
    assert golden(hub) is None
    assert conflicts(hub)[-1][:2] == ("*", None)


def test_an_edit_newer_than_a_delete_brings_the_record_back(hub):
    send(hub, "crm", "INSERT", 100, **ACME)
    send(hub, "billing", "INSERT", 105, **ACME)
    send(hub, "billing", "DELETE", 300, **ACME)
    update(hub, "crm", 350, ACME, {**ACME, "name": "Edited after the delete"})
    assert golden(hub)[0] == "Edited after the delete"
    field, kept, kept_by, lost, lost_by = conflicts(hub)[-1]
    assert (field, kept["name"], kept_by, lost, lost_by) == (
        "*", "Edited after the delete", "crm", None, "billing")
    assert ("name", "Edited after the delete") in expected(hub, "billing")


def test_first_sync_the_authority_wins_when_it_arrives_second(hub):
    """billing's snapshot lands first; crm's, the authority, disagrees."""
    send(hub, "billing", "INSERT", 900, **{**ACME, "name": "Acme (billing)"})
    send(hub, "crm", "INSERT", 800, **ACME)
    assert golden(hub)[0] == "Acme"
    assert conflicts(hub) == [("name", "Acme", "crm", "Acme (billing)", "billing")]
    assert hub.execute("select reason from hub.conflict").fetchone()[0] == "seed"
    assert expected(hub, "billing") == [("name", "Acme")]


def test_first_sync_the_authority_wins_when_it_arrives_first(hub):
    send(hub, "crm", "INSERT", 800, **ACME)
    send(hub, "billing", "INSERT", 900, **{**ACME, "name": "Acme (billing)"})
    assert golden(hub)[0] == "Acme"
    assert conflicts(hub) == [("name", "Acme", "crm", "Acme (billing)", "billing")]
    # billing is corrected, and the correction is awaited once.
    assert expected(hub, "billing") == [("name", "Acme")]


def test_first_sync_of_identical_records_is_silent(hub):
    send(hub, "billing", "INSERT", 900, **ACME)
    send(hub, "crm", "INSERT", 800, **ACME)
    assert conflicts(hub) == []
    assert expected(hub, "crm") == []


# --- one system, several tables (#79) ---------------------------------------

def city(cx, code=1):
    return cx.execute("select name, city from hub.customer where code = %s",
                      (code,)).fetchone()


def test_a_table_carrying_part_of_a_record_touches_only_its_fields(hub):
    send(hub, "crm", "INSERT", 100, **ACME)
    send(hub, "crm_address", "INSERT", 101, code=1, city="Ankara")
    assert city(hub) == ("Acme", "Ankara")
    update(hub, "crm_address", 200, {"code": 1, "city": "Ankara"}, {"code": 1, "city": "İzmir"})
    assert city(hub) == ("Acme", "İzmir")


def test_part_of_a_record_is_awaited_only_from_systems_that_receive_it(hub):
    send(hub, "crm", "INSERT", 100, **ACME)
    send(hub, "billing", "INSERT", 105, **ACME)
    send(hub, "crm_address", "INSERT", 110, code=1, city="Ankara")
    assert expected(hub, "crm") == []
    assert expected(hub, "billing") == [("city", "Ankara")]
    update(hub, "billing", 200, {**ACME, "city": "Ankara"}, {**ACME, "city": "Bursa"})
    assert expected(hub, "crm_address") == [("city", "Bursa")]
    assert expected(hub, "crm") == []


def test_the_part_arriving_before_the_record_starts_it(hub):
    send(hub, "crm_address", "INSERT", 100, code=1, city="Ankara")
    send(hub, "crm", "INSERT", 101, **ACME)
    assert city(hub) == ("Acme", "Ankara")


def test_deleting_part_of_a_record_empties_only_that_part(hub):
    send(hub, "crm", "INSERT", 100, **ACME)
    send(hub, "crm_address", "INSERT", 101, code=1, city="Ankara")
    send(hub, "crm_address", "DELETE", 200, code=1, city="Ankara")
    assert city(hub) == ("Acme", None)


def test_deleting_the_record_takes_its_parts_quietly(hub):
    send(hub, "crm", "INSERT", 100, **ACME)
    send(hub, "crm_address", "INSERT", 101, code=1, city="Ankara")
    send(hub, "crm", "DELETE", 300, **ACME)
    assert golden(hub) is None
    # The address row going away with it neither resurrects nor complains.
    send(hub, "crm_address", "DELETE", 310, code=1, city="Ankara")
    assert golden(hub) is None
    assert [c for c in conflicts(hub) if c[0] == "*"] == []


# --- what a revision tells the deliveries (found live, #79) -----------------

def revision(cx, code=1):
    return cx.execute("select _changed, _skip from hub.customer where code = %s",
                      (code,)).fetchone()


def test_an_emptied_part_is_not_refilled_by_an_older_row(hub):
    """A deleted address row emptied the field at t=200; the same row
    reappearing from before that (a stale delivery) must not refill it --
    that is the loop the live demo ran."""
    send(hub, "crm", "INSERT", 100, **ACME)
    send(hub, "crm_address", "INSERT", 101, code=1, city="Ankara")
    send(hub, "crm_address", "DELETE", 200, code=1, city="Ankara")
    send(hub, "crm_address", "INSERT", 150, code=1, city="Ankara")
    assert city(hub) == ("Acme", None)
    send(hub, "crm_address", "INSERT", 250, code=1, city="İzmir")
    assert city(hub) == ("Acme", "İzmir")


def test_a_revision_names_its_origin_and_what_changed(hub):
    send(hub, "crm", "INSERT", 100, **ACME)
    assert revision(hub) == ("*", "crm")
    send(hub, "billing", "INSERT", 105, **ACME)
    update(hub, "billing", 200, ACME, {**ACME, "name": "Acme Ltd"})
    # Delivered to everyone but billing, and only the name is written.
    assert revision(hub) == (",name,", "billing")


def test_a_loser_is_delivered_to(hub):
    send(hub, "crm", "INSERT", 100, **ACME)
    send(hub, "billing", "INSERT", 105, **ACME)
    update(hub, "crm", 200, ACME, {**ACME, "name": "From CRM"})
    update(hub, "billing", 190, ACME, {**ACME, "name": "From billing"})
    assert revision(hub) == (",name,", None)


def test_a_winner_with_a_delivery_still_on_its_way_gets_its_value_again(hub):
    """billing's B is on its way to crm when crm, not having seen it, commits
    C later. C wins -- but B will land on crm after, so crm must get C again,
    or it keeps B while the hub says C."""
    send(hub, "crm", "INSERT", 100, **ACME)
    send(hub, "billing", "INSERT", 105, **ACME)
    update(hub, "billing", 200, ACME, {**ACME, "name": "B"})
    update(hub, "crm", 300, ACME, {**ACME, "name": "C"})
    assert golden(hub)[0] == "C"
    assert revision(hub) == (",name,", None)
    assert expected(hub, "crm") == [("name", "B"), ("name", "C")]
    assert expected(hub, "billing") == [("name", "C")]


def test_an_authority_seeding_second_gets_its_value_again(hub):
    """billing's snapshot created the record and is on its way to crm; crm's
    own snapshot, the authority's, wins the seed -- but billing's value lands
    on crm after, so crm must get its own again. Found live: the two swapped."""
    send(hub, "billing", "INSERT", 900, **{**ACME, "name": "Acme (billing)"})
    send(hub, "crm", "INSERT", 800, **ACME)
    assert revision(hub) == (",name,", None)
    assert expected(hub, "crm") == [("name", "Acme (billing)"), ("name", "Acme")]


def test_the_revision_that_completes_a_record_is_new_to_everyone(hub):
    """The address arrived first and the record waited for its name; the
    revision that brings the name is the first a target can create."""
    send(hub, "crm_address", "INSERT", 100, code=1, city="Ankara")
    send(hub, "crm", "INSERT", 101, **ACME)
    assert revision(hub)[0] == "*"


def test_an_unknown_entity_is_refused(hub):
    with pytest.raises(psycopg.errors.RaiseException, match="not a registered entity"):
        hub.execute("select hub.merge('supplier', 'crm', 'INSERT', 1, %s)",
                    (json.dumps({"code": 1}),))


def test_reserved_columns_are_refused():
    from core import hub as h
    with pytest.raises(ValueError, match="reserved"):
        h.register_entity(None, "x", ["id"], {"id": "int", "_rev": "int"}, "a")


# --- a value its value map did not know (#84) --------------------------------

def test_a_value_outside_the_value_map_is_kept_out_and_logged(hub):
    """CRM's ACTIVE is 'X': the flow's map lands it as NULL and says so. The
    hub keeps its own value -- a NULL here is not the CRM emptying the field --
    and the loss is on record."""
    send(hub, "crm", "INSERT", 100, **ACME)
    hub.execute("insert into hub.customer_inbox (system, row_kind, source_ms, fields, "
                "code, name, active, unmapped) values ('crm', 'UPDATE_BEFORE', 200, "
                "'code,name,active', 1, 'Acme', true, null), ('crm', 'UPDATE_AFTER', 200, "
                "'code,name,active', 1, 'Acme Corp', null, 'active,')")
    assert golden(hub)[:2] == ("Acme Corp", True)
    assert hub.execute("select field, kept, lost, lost_by, reason from hub.conflict"
                       ).fetchall() == [("active", True, None, "crm", "unmapped")]
    assert ("active", None) not in expected(hub, "billing")


# --- an awaited value that can never come back (#84) --------------------------

def test_an_address_added_then_removed_is_not_swallowed_by_a_stale_empty(hub):
    """billing made the record with no city, so the hub awaited an empty city
    from the CRM's address table -- which has no row to empty and never
    answers. The CRM then adds an address and removes it: the removal is an
    edit, not that old echo."""
    send(hub, "billing", "INSERT", 100, **ACME, city=None)
    send(hub, "crm", "INSERT", 101, **ACME)
    send(hub, "crm_address", "INSERT", 200, code=1, city="Bursa")
    assert city(hub) == ("Acme", "Bursa")
    send(hub, "crm_address", "DELETE", 300, code=1, city="Bursa")
    assert city(hub) == ("Acme", None)


def test_a_value_the_system_already_had_is_not_awaited_from_it(hub):
    """An awaited value the system already holds can never come back as a
    change: delivering it there changed nothing. Awaiting it anyway would
    swallow a later edit to exactly that value."""
    send(hub, "crm", "INSERT", 100, **ACME)
    hub.execute("insert into hub.expect (system, entity, key, field, value) values "
                "('crm', 'customer', '{\"code\": 1}', 'name', '\"Acme\"')")
    update(hub, "crm", 200, ACME, {**ACME, "name": "Acme X"})
    update(hub, "crm", 300, {**ACME, "name": "Acme X"}, ACME)
    assert golden(hub)[0] == "Acme"


def test_an_hour_old_awaited_value_goes_whatever_record_it_is_for(hub):
    hub.execute("insert into hub.expect (system, entity, key, field, value, created_at) "
                "values ('crm', 'customer', '{\"code\": 99}', 'name', '\"x\"', "
                "now() - interval '2 hours')")
    send(hub, "billing", "INSERT", 100, **ACME)
    assert hub.execute("select count(*) from hub.expect where key = '{\"code\": 99}'"
                       ).fetchone()[0] == 0


# --- what became of each row, for the tab's history ------------------------

def outcomes(cx, system):
    return [r[0] for r in cx.execute(
        "select outcome from hub.customer_inbox where system = %s and row_kind <> "
        "'UPDATE_BEFORE' order by id", (system,)).fetchall()]


def test_each_row_says_what_the_hub_did_with_it(hub):
    """A history line reads "our own write coming back" or "lost to a later
    edit", not only "billing updated the name"."""
    send(hub, "crm", "INSERT", 100, **ACME)                               # created
    send(hub, "billing", "INSERT", 105, **ACME)                           # our delivery, back
    update(hub, "crm", 200, ACME, {**ACME, "name": "Acme Ltd"})           # applied
    update(hub, "billing", 150, ACME, {**ACME, "name": "Acme stale"})     # lost
    update(hub, "billing", 210, ACME, {**ACME, "name": "Acme Ltd"})       # echo
    assert outcomes(hub, "crm") == ["created", "applied"]
    assert outcomes(hub, "billing") == ["echo", "lost", "echo"]
