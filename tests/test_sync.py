"""The rules that decide whether a table can be replicated at all.

Postgres enforces every one of these, but three of them only at the moment a
row changes -- long after the objects were created and the initial copy made
it look like it worked. So they are checked here, before anything is created,
and each test names the error it is standing in for.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytest.importorskip("sqlglot")
from core.sync import (filter_columns, identity_columns,  # noqa: E402
                       problems, publication_name, publication_statement,
                       sync_rule)

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"


def _model(**kw) -> dict:
    base = {"name": "customers", "physicalName": "customers", "properties": [
        {"name": "customer_id", "primaryKey": True},
        {"name": "name"}, {"name": "country"}, {"name": "tax_id"},
        {"name": "segment"}]}
    base.update(kw)
    return base


# --- what identifies a row --------------------------------------------------

def test_the_identity_is_the_contracts_primary_key():
    assert identity_columns(_model()) == ["customer_id"]


def test_a_rule_may_widen_the_identity():
    """Rule 2 leaves no other way to filter on a non-key column."""
    rule = {"identity": ["customer_id", "country"]}
    assert identity_columns(_model(), rule) == ["customer_id", "country"]


def test_a_rule_may_not_narrow_it():
    """An identity without the key does not identify a row, so it is ignored
    and reported rather than quietly used."""
    rule = {"identity": ["country"]}
    assert identity_columns(_model(), rule) == ["customer_id"]
    assert any("primary key" in p for p in problems(_model(), rule))


def test_a_table_with_no_key_cannot_be_replicated():
    model = _model(properties=[{"name": "a"}, {"name": "b"}])
    assert problems(model, {"server": "replica"})
    assert "primaryKey" in problems(model, {"server": "replica"})[0]


# --- the three Postgres only raises when a row changes ----------------------

def test_a_filter_column_outside_the_identity_is_refused():
    """ERROR: Column used in the publication WHERE expression is not part of
    the replica identity."""
    found = problems(_model(), {"filter": "country = 'TR'"})
    assert any("country" in p and "replica identity" in p for p in found)


def test_the_same_filter_is_fine_once_the_identity_covers_it():
    assert problems(_model(), {"filter": "country = 'TR'",
                               "identity": ["customer_id", "country"],
                               "columns": ["customer_id", "country"]}) == []


def test_a_column_list_must_cover_the_identity():
    """ERROR: Column list used by the publication does not cover the replica
    identity."""
    found = problems(_model(), {"columns": ["name", "segment"]})
    assert any("column list omits" in p for p in found)


def test_a_column_the_contract_does_not_declare_is_refused():
    found = problems(_model(), {"columns": ["customer_id", "nope"]})
    assert any("nope" in p for p in found)


def test_filter_columns_are_read_from_the_sql_not_guessed():
    assert filter_columns("country = 'TR' and segment <> 'ENT'") == \
        {"country", "segment"}
    assert filter_columns("") == set()


# --- the statement ----------------------------------------------------------

def test_the_publication_carries_the_filter_and_the_column_list():
    stmt = publication_statement(
        _model(), "public",
        {"filter": "country = 'TR'",
         "columns": ["customer_id", "name", "country", "segment"]},
        "sync_erp_customers").as_string()
    assert '"customer_id", "name", "country", "segment"' in stmt
    assert "country = 'TR'" in stmt
    # the whole point of the column list: the classified column never leaves
    assert "tax_id" not in stmt


def test_the_publication_name_survives_a_dotted_contract_id():
    assert publication_name({"id": "erp.customers"}) == "sync_erp_customers"


# --- the contract in the repository ----------------------------------------

def test_the_logical_replication_rules_do_not_apply_to_a_cdc_source():
    """Reported as a problem on the SQL Server contract until the engine was
    passed in -- and it is not one: the CDC reader has the whole row."""
    rule = {"filter": "country = 'TR'", "columns": ["customer_id", "name"]}
    assert problems(_model(), rule)                      # postgres: two of them
    assert problems(_model(), rule, "sqlserver") == []


def test_the_shipped_rule_would_actually_work():
    doc = yaml.safe_load(
        (CONTRACTS / "erp_customers.odcs.yaml").read_text(encoding="utf-8"))
    rule = sync_rule(doc)
    assert rule, "the customers contract should carry a syncTo rule"
    assert problems(doc["schema"][0], rule) == []
    assert {s["server"] for s in doc["servers"]} >= {rule["server"]}


def test_a_classified_column_is_not_replicated():
    """Belt and braces with core/sample.py: masking it in the UI is no use if
    the whole column was copied into another database."""
    from core.sample import classified
    doc = yaml.safe_load(
        (CONTRACTS / "erp_customers.odcs.yaml").read_text(encoding="utf-8"))
    rule = sync_rule(doc)
    assert not (classified(doc) & set(rule.get("columns") or []))


# --- the target nothing else creates ----------------------------------------

def test_the_target_table_comes_from_the_contract():
    """Logical replication replicates into a table that must already exist,
    and the CDC reader upserts into one. It was created by hand while this was
    written, so a clean install of the whole stack failed at the first sync."""
    from core.sync import target_table_statement
    stmt = target_table_statement(
        _model(), "public",
        {"columns": ["customer_id", "name", "country"],
         "identity": ["customer_id", "country"]}, "postgres").as_string()
    assert 'create table if not exists "public"."customers"' in stmt
    assert '"customer_id"' in stmt and '"country"' in stmt


def test_only_the_replicated_columns_exist_in_the_target():
    """The privacy boundary is physical, not a filter: a classified column left
    out of the rule has no column in the replica to leak from."""
    from core.sync import target_table_statement
    stmt = target_table_statement(
        _model(), "public", {"columns": ["customer_id", "name"]},
        "postgres").as_string()
    assert "tax_id" not in stmt


def test_identity_columns_are_not_null_in_the_target():
    """A replica identity index has to be over NOT NULL columns."""
    from core.sync import target_table_statement
    stmt = target_table_statement(
        _model(), "public",
        {"columns": ["customer_id", "name"]}, "postgres").as_string()
    assert '"customer_id" text not null' in stmt or "not null" in stmt


def test_sql_server_types_are_translated():
    """The contract states physical types in the *source's* dialect, so
    replicating SQL Server into Postgres needs a translation -- `int` and
    `decimal` are not Postgres spellings."""
    from core.sync import target_table_statement
    model = {"name": "sales_orders", "physicalName": "sales_orders",
             "properties": [
                 {"name": "order_id", "physicalType": "bigint", "primaryKey": True},
                 {"name": "customer_id", "physicalType": "int"},
                 {"name": "net_amount", "physicalType": "decimal"},
                 {"name": "currency", "physicalType": "char"}]}
    stmt = target_table_statement(model, "public", {}, "sqlserver").as_string()
    assert "integer" in stmt and "numeric" in stmt
    assert " int," not in stmt and "decimal" not in stmt


# --- columns the target fills itself (issue #45) -----------------------------

def test_a_generated_column_is_added_to_the_target():
    """The half of issue #45 that was genuinely missing: a sequence or default
    on the target never fired, because every column was sent verbatim."""
    from core.sync import target_table_statement
    stmt = target_table_statement(
        _model(), "public",
        {"columns": ["customer_id", "name"],
         "generated": {"replica_row_id": "bigint generated always as identity"}},
        "postgres").as_string()
    assert '"replica_row_id" bigint generated always as identity' in stmt


def test_a_generated_column_is_not_sent_by_the_reader():
    """Nothing has to exclude it: it is not a contract property, so it is not
    in the column list either path builds its statements from."""
    rule = {"generated": {"replica_row_id": "bigint generated always as identity"}}
    columns = rule.get("columns") or [p["name"] for p in _model()["properties"]]
    assert "replica_row_id" not in columns


def test_a_generated_column_may_not_shadow_a_replicated_one():
    """Both halves would emit it and `create table` fails on the duplicate --
    at the first sync, which is the failure target_table_statement exists to
    prevent."""
    found = problems(_model(), {"columns": ["customer_id", "name"],
                                "generated": {"name": "text default ''"}})
    assert any("declare it twice" in p for p in found)


def test_a_generated_column_may_not_reuse_a_held_back_columns_name():
    """tax_id is left out of the rule to keep it out of the replica. Reusing
    the name would not fail the create -- it would put a column called tax_id
    in the target meaning something else, which is worse than an error."""
    found = problems(_model(), {"columns": ["customer_id", "name"],
                                "generated": {"tax_id": "text default ''"}})
    assert any("held back" in p for p in found)
    assert not any("declare it twice" in p for p in found)


def test_a_generated_column_may_not_be_part_of_the_identity():
    """Rows are matched on the identity, and the source never sends this one --
    there would be nothing to match."""
    found = problems(_model(), {"identity": ["customer_id", "replica_row_id"],
                                "generated": {"replica_row_id": "bigint"}})
    assert any("cannot be part of" in p for p in found)


def test_a_filter_may_not_read_a_generated_column():
    """The filter is evaluated against the source row, which does not have
    it -- see the `keep` predicate in core/sync_mssql.py."""
    found = problems(_model(), {"filter": "replica_row_id > 0",
                                "identity": ["customer_id"],
                                "generated": {"replica_row_id": "bigint"}})
    assert any("evaluated" in p for p in found)


def test_a_generated_column_must_say_how():
    found = problems(_model(), {"generated": {"replica_row_id": "  "}})
    assert any("does not say" in p for p in found)


def test_an_existing_target_gains_the_generated_column():
    """`create table if not exists` leaves a replica that predates the rule in
    its old shape, so the feature would do nothing wherever it had already
    run. Measured on the demo's 50 288-row replica: every row got a number."""
    from core.sync import generated_statements
    stmts = [s.as_string() for s in generated_statements(
        _model(), "public",
        {"generated": {"replica_row_id": "bigint generated always as identity"}})]
    assert stmts == ['alter table "public"."customers" add column if not exists '
                     '"replica_row_id" bigint generated always as identity']


def test_nothing_is_altered_when_the_rule_has_no_generated_block():
    """Every rule that predates issue #45 keeps emitting exactly what it did."""
    from core.sync import generated_statements
    assert generated_statements(_model(), "public", {"columns": ["name"]}) == []


def test_generated_columns_are_never_added_to_the_source():
    """_identity_statements runs on both ends; this one must not. A source that
    grew the target's surrogate key would be replicating a column back."""
    import inspect
    from core import sync
    body = inspect.getsource(sync.plan)
    target_half = body.split('"target"', 1)[1]
    assert "generated_statements" in target_half
    assert "generated_statements" not in body.split('"target"', 1)[0]


def test_the_shipped_mssql_rule_still_holds_with_its_generated_column():
    """The demo case. sqlserver, because the CDC reader is what applies it."""
    doc = yaml.safe_load((CONTRACTS / "erp_mssql.odcs.yaml").read_text(encoding="utf-8"))
    rule = sync_rule(doc)
    assert rule["generated"]
    assert problems(doc["schema"][0], rule, "sqlserver") == []


def test_a_stated_precision_survives_translation():
    """decimal(14,2) is a different type from a bare numeric, which accepts a
    scale the source would reject. A string length is dropped on purpose --
    every textual type becomes text. Issue #50."""
    from core.sync import target_table_statement
    model = {"name": "sales_orders", "physicalName": "sales_orders",
             "properties": [
                 {"name": "order_id", "physicalType": "bigint", "primaryKey": True},
                 {"name": "net_amount", "physicalType": "decimal(14,2)"},
                 {"name": "rate", "physicalType": "numeric(9, 4)"},
                 {"name": "name", "physicalType": "nvarchar(200)"}]}
    stmt = target_table_statement(model, "public", {}, "sqlserver").as_string()
    assert '"net_amount" numeric(14,2)' in stmt
    assert '"rate" numeric(9, 4)' in stmt
    assert '"name" text,' in stmt or stmt.endswith('"name" text)')


# --- authoring a rule that does not exist on disk yet -----------------------
# The write path issue #10 asks for: the same four preconditions, run against
# a proposed rule rather than one already saved. `unsound_identity` needs a
# reachable store, which the accepted cases stub out -- the point of these
# tests is author_rule wiring problems() and unsound_identity() together and
# refusing to build a rule that names a server the contract does not have,
# not re-proving what test_sync.py already covers for each of those two.

def _contract(engine="postgres", **kw):
    # Not "erp.customers": that id is a real contract, and unsound_identity
    # queries check_results by contract id -- a hermetic test cannot share it,
    # or it would see whatever the store happens to hold whenever one is
    # actually reachable.
    base = {"id": "test.customers", "schema": [_model()],
           "servers": [{"server": "erp", "type": engine},
                       {"server": "replica", "type": "postgres"}]}
    base.update(kw)
    return base


def test_authoring_a_rule_for_a_server_the_contract_does_not_have_is_refused():
    from core.sync import author_rule
    rule, bad = author_rule(_contract(), "nowhere")
    assert rule == {}
    assert "nowhere" in bad[0] and "servers[]" in bad[0]


def test_authoring_reuses_problems_for_a_key_the_contract_lacks(monkeypatch):
    from core import sync
    monkeypatch.setattr(sync, "unsound_identity", lambda *a: [])
    keyless = _contract()
    keyless["schema"][0] = _model(properties=[{"name": "a"}, {"name": "b"}])
    rule, bad = sync.author_rule(keyless, "replica")
    assert any("primaryKey" in p for p in bad)


def test_authoring_reuses_problems_for_a_filter_outside_the_identity(monkeypatch):
    from core import sync
    monkeypatch.setattr(sync, "unsound_identity", lambda *a: [])
    rule, bad = sync.author_rule(_contract(), "replica", row_filter="country = 'TR'")
    assert any("replica identity" in p for p in bad)


def test_authoring_reuses_problems_for_a_column_list_missing_the_identity(monkeypatch):
    from core import sync
    monkeypatch.setattr(sync, "unsound_identity", lambda *a: [])
    rule, bad = sync.author_rule(_contract(), "replica", columns=["name"])
    assert any("column list omits" in p for p in bad)


def test_authoring_asks_whether_the_identity_has_actually_held(monkeypatch):
    """A rule with no other problem is still refused if the identity has been
    seen failing -- author_rule has to reach unsound_identity too, not stop at
    problems(). Stubbed rather than pointed at a real store: whether *that*
    check itself is right is test_sync.py's existing coverage, not this one's."""
    from core import sync
    monkeypatch.setattr(sync, "unsound_identity",
                        lambda *a: ["customer_id: the uniqueness check is failing"])
    rule, bad = sync.author_rule(_contract(), "replica")
    assert any("uniqueness check is failing" in p for p in bad)


def test_a_sound_postgres_rule_is_accepted(monkeypatch):
    from core import sync
    monkeypatch.setattr(sync, "unsound_identity", lambda *a: [])
    rule, bad = sync.author_rule(_contract("postgres"), "replica",
                                 columns=["customer_id", "name", "country"])
    assert bad == []
    assert rule == {"server": "replica",
                    "columns": ["customer_id", "name", "country"]}


def test_a_sound_sqlserver_rule_is_accepted(monkeypatch):
    """Rules 2 and 3 are logical replication's; a filter column outside the
    identity is not a problem for a CDC source -- see ADR 0008."""
    from core import sync
    monkeypatch.setattr(sync, "unsound_identity", lambda *a: [])
    rule, bad = sync.author_rule(_contract("sqlserver"), "replica",
                                 row_filter="country = 'TR'")
    assert bad == []
    assert rule == {"server": "replica", "filter": "country = 'TR'"}


def test_the_target_is_emptied_before_the_copy_runs_again():
    """`copy_data = true` is not idempotent. The replica identity index the
    target is required to have is exactly what a second copy collides with,
    and the table sync worker then dies on a duplicate key every few seconds
    while the apply worker stays up and the slot stays active -- issue #35."""
    from core.sync import truncate_statement
    assert truncate_statement(_model(), "public").as_string() == \
        'truncate "public"."customers"'


# --- a target that is a view rather than a copy (ADR 0017) ------------------

def _view_rule(**over):
    return {"server": "replica", "mode": "view", "filter": "qty > 0",
            "columns": ["order_id", "line_no", "sku"], **over}


def test_copy_is_the_mode_a_rule_that_does_not_say_gets():
    from core.sync import mode
    assert mode({"server": "replica"}) == "copy"
    assert mode(_view_rule()) == "view"


def test_the_view_applies_the_filter_and_the_column_list():
    """Same rule, same shape on the other side -- the difference is that the
    rows are not there twice."""
    from core.sync_view import target_statements
    stmts = [s.as_string() for s in target_statements(
        _model(), _view_rule(contract_id="erp.order_lines"),
        {"host": "db", "port": 5432, "database": "erp", "schema": "public"},
        "public")]
    view = stmts[-1]
    assert view.startswith('create view "public"."customers" as select')
    assert '"order_id", "line_no", "sku"' in view
    assert view.endswith("where qty > 0")
    assert any("foreign data wrapper postgres_fdw" in s for s in stmts)


def test_the_grant_is_the_privacy_boundary_and_names_only_the_listed_columns():
    """In view mode a column outside the list is not absent from the target,
    it is ungranted at the source -- so the grant is the boundary."""
    from core.sync_view import source_statements
    stmts = [s.as_string() for s in source_statements(
        _model(), "public", _view_rule())]
    assert any(s.startswith("revoke all on table") for s in stmts)
    grant = next(s for s in stmts if s.startswith("grant select"))
    assert '"order_id", "line_no", "sku"' in grant and "tax_id" not in grant


def test_a_view_target_is_refused_for_a_sql_server_source():
    """postgres_fdw is what the image has; tds_fdw would be a new image for
    one table (invariant 6)."""
    from core.sync_view import problems
    out = problems(_model(), _view_rule(), {"schema": [_model()]}, "sqlserver")
    assert any("tds_fdw" in p for p in out)


def test_a_classified_column_may_not_be_in_a_view_rule():
    import yaml as _yaml
    from core.sync_view import problems
    doc = _yaml.safe_load(
        (CONTRACTS / "erp_customers.odcs.yaml").read_text(encoding="utf-8"))
    model = doc["schema"][0]
    out = problems(model, _view_rule(columns=["customer_id", "tax_id"]),
                   doc, "postgres")
    assert any("tax_id" in p and "classified" in p for p in out)


def test_the_shipped_view_rule_would_actually_work():
    doc = yaml.safe_load(
        (CONTRACTS / "erp_order_lines.odcs.yaml").read_text(encoding="utf-8"))
    rule = sync_rule(doc)
    from core.sync import mode
    from core.sync_view import problems
    assert mode(rule) == "view"
    assert problems(doc["schema"][0], rule, doc, "postgres") == []
    assert {s["server"] for s in doc["servers"]} >= {rule["server"]}


def test_a_view_rule_is_not_held_to_logical_replications_preconditions(monkeypatch):
    """erp.order_lines filters on qty, outside its (order_id, line_no) key.
    That is a copy's problem, not a view's -- the panel's listing reported it
    anyway until `/api/sync` and plan() shared rule_problems(). The identity
    having held is a copy's question too: nothing is upserted to merge."""
    from core import sync
    monkeypatch.setattr(sync, "unsound_identity",
                        lambda *a: ["sentinel: the identity does not hold"])
    doc = yaml.safe_load(
        (CONTRACTS / "erp_order_lines.odcs.yaml").read_text(encoding="utf-8"))
    rule = sync_rule(doc)
    assert any("replica identity" in p
               for p in problems(doc["schema"][0], rule, "postgres"))
    assert sync.rule_problems(doc, rule, "postgres") == []
    copy = {k: v for k, v in rule.items() if k != "mode"}
    assert any("replica identity" in p
               for p in sync.rule_problems(doc, copy, "postgres"))
    assert "sentinel: the identity does not hold" in \
        sync.rule_problems(doc, copy, "postgres")
