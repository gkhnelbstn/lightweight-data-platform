"""Flows compiled into SeaTunnel jobs: the shape each job must have.

Each assertion is a finding from ADR 0020 that the compiler must not forget:
the commit time comes from `SourceTimestamp`, not `EventTime`; before images
reach the hub only through `RowKindExtractor` and a plain insert; a delivery
drops before images; and a SQL Server target uses the generated MERGE, whose
rewrite of an unchanged value adds no CDC row.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import flow_jobs, flows

DEMO = Path(__file__).resolve().parents[1] / "demo" / "integration"


@pytest.fixture(scope="module")
def compiled():
    by_id, loaded = flow_jobs.load(DEMO)
    assert flows.problems(loaded, by_id) == []
    return flow_jobs.jobs(by_id, loaded)


def _plugins(job, stage):
    return [p["plugin_name"] for p in job[stage]]


def test_every_demo_flow_compiles(compiled):
    assert sorted(compiled) == [
        "billing_to_hub", "crm_invoice_address_to_hub", "crm_shipping_address_to_hub",
        "crm_to_hub", "hub_to_billing", "hub_to_crm", "hub_to_crm_invoice_address",
        "hub_to_crm_shipping_address", "hub_to_shop", "invoice_totals", "invoice_totals_out",
        "loyalty_to_hub", "shop_to_hub"]


def test_the_shop_is_written_with_the_guarded_postgres_merge(compiled):
    """#83: the third system is Postgres, so its out-flow is the MERGE that
    updates only what differs; its own numbering is left to it."""
    merge, delete = (sink["query"] for sink in compiled["hub_to_shop"]["sink"])
    assert merge.startswith('MERGE INTO "public"."customer" AS t USING (SELECT '
                            'CAST(? AS bigint) AS "customer_no"')
    assert ('ON (t."customer_no" = s."customer_no" OR (s."customer_no" IS NULL AND '
            's."_link" AND t."vat_number" = s."vat_number"))') in merge
    assert "IS DISTINCT FROM" in merge
    assert 'THEN INSERT ("full_name", "vat_number", "is_active", "city")' in merge
    assert delete == 'DELETE FROM "public"."customer" WHERE "customer_no" = CAST(? AS bigint)'
    source = compiled["shop_to_hub"]["source"][0]
    assert source["plugin_name"] == "Postgres-CDC" and source["slot.name"] == "shop_to_hub_slot"


def test_into_the_hub_keeps_commit_time_and_before_images(compiled):
    job = compiled["crm_to_hub"]
    assert _plugins(job, "source") == ["SqlServer-CDC"]
    assert _plugins(job, "transform") == ["Metadata", "RowKindExtractor", "Sql"]
    meta = job["transform"][0]["metadata_fields"]
    assert meta == {"SourceTimestamp": "source_ms"}      # not EventTime
    sink = job["sink"][0]
    assert sink["query"].startswith("insert into hub.customer_inbox")
    assert "generate_sink_sql" not in sink               # it drops before rows


def test_a_value_map_is_a_searched_case(compiled):
    sql = compiled["crm_to_hub"]["transform"][2]["query"]
    assert ("CASE WHEN ACTIVE = 'Y' THEN true WHEN ACTIVE = 'N' THEN false END "
            "AS active") in sql
    assert "'crm.account' AS system" in sql
    assert "'crm_code,name,tax_id,active' AS fields" in sql
    back = compiled["hub_to_crm"]["transform"][3]["query"]
    assert "CASE WHEN active = true THEN 'Y' WHEN active = false THEN 'N' END AS ACTIVE" in back


def test_out_of_the_hub_writes_only_what_a_revision_changed(compiled):
    job = compiled["hub_to_billing"]
    assert _plugins(job, "source") == ["Postgres-CDC"]
    assert job["source"][0]["slot.name"] == "hub_to_billing_slot"
    assert _plugins(job, "transform") == ["FilterRowKind", "FilterRowKind",
                                          "RowKindExtractor", "Sql", "Sql"]
    assert job["transform"][0]["exclude_kinds"] == ["UPDATE_BEFORE", "DELETE"]
    upserts = job["transform"][3]["query"]
    # Not back to where the change came from, only when it touched a field
    # billing receives, and not before the record has what billing requires.
    assert "(_skip IS NULL OR _skip <> 'billing.customer')" in upserts
    assert "POSITION(',name,', _changed) > 0" in upserts
    # billing numbers a customer it receives itself (#80): no code to wait for.
    assert upserts.endswith("name IS NOT NULL AND active IS NOT NULL")
    merge, delete = (sink["query"] for sink in job["sink"])
    assert merge.startswith("MERGE [dbo].[customer] WITH (HOLDLOCK) AS t")
    new = "s._changed = '*' OR CHARINDEX(',billing_code,', s._changed) > 0"
    assert (f"t.[Name] = CASE WHEN {new} OR CHARINDEX(',name,', s._changed) > 0 "
            "THEN s.[Name] ELSE t.[Name] END") in merge
    assert f"WHEN NOT MATCHED AND ({new}) THEN INSERT ([Name], " in merge
    assert delete == "DELETE FROM [dbo].[customer] WHERE [CustomerCode] = ?"
    assert not any(sink.get("generate_sink_sql") for sink in job["sink"])


def test_credentials_are_placeholders(compiled):
    text = str(compiled)
    assert "${MSSQL_PASSWORD}" in text and "${PG_PASSWORD}" in text
    assert "Str0ng" not in text


def test_a_postgres_target_updates_only_what_actually_differs():
    """#83, ADR 0020: an update that writes unchanged values is a change to
    Postgres's logical decoding, and a two-way pair loops on it. The MERGE
    there is guarded, and its parameters are typed."""
    by_id, loaded = flow_jobs.load(DEMO)
    by_id["billing.customer"]["servers"][0].update(type="postgres", schema="public")
    merge, delete = (sink["query"] for sink in flow_jobs.jobs(by_id, loaded)
                     ["hub_to_billing"]["sink"])
    assert merge.startswith('MERGE INTO "public"."customer" AS t USING (SELECT '
                            'CAST(? AS varchar) AS "CustomerCode", CAST(? AS nvarchar) AS "Name"')
    assert ('WHEN MATCHED AND (t."Name", t."TaxId", t."IsActive", t."InvoiceCity", '
            't."ShippingCity") IS DISTINCT FROM (CASE WHEN') in merge
    assert "strpos(s._changed, ',name,') > 0" in merge and "CHARINDEX" not in merge
    assert delete == 'DELETE FROM "public"."customer" WHERE "CustomerCode" = CAST(? AS varchar)'


def test_secrets_are_filled_on_the_way_out(monkeypatch):
    from core import flow_apply
    for name in flow_apply.SECRETS:
        monkeypatch.setenv(name, f"<{name}>")
    filled = flow_apply._fill({"password": "${MSSQL_PASSWORD}"})
    assert filled == {"password": "<MSSQL_PASSWORD>"}
    monkeypatch.delenv("PG_USER")
    monkeypatch.setattr(flow_apply, "SECRETS", ("MSSQL_PASSWORD",))
    with pytest.raises(SystemExit, match="PG_USER"):
        flow_apply._fill({"username": "${PG_USER}"})


def test_a_kind_of_row_is_merged_when_present_and_deleted_when_absent(compiled):
    """#79: no invoice address in the hub means no INV row in the CRM -- but
    only a revision that changed the invoice address may say so."""
    job = compiled["hub_to_crm_invoice_address"]
    present, emptied = job["transform"][3]["query"], job["transform"][5]["query"]
    # Also the revision that gives the record its CRM code (#80): the rows
    # could not be written before it.
    touched = ("(_changed = '*' OR POSITION(',crm_code,', _changed) > 0 "
               "OR POSITION(',invoice_city,', _changed) > 0)")
    assert touched in present
    # Emptied only by a revision naming the field: a '*' with the address
    # still empty is the owner arriving first, not the address going away.
    assert "WHERE (_skip IS NULL OR _skip <> 'crm.account_address') AND "            "(POSITION(',invoice_city,', _changed) > 0) AND" in emptied
    assert ", 'INV' AS ADDR_TYPE" in present
    assert present.endswith("crm_code IS NOT NULL AND (invoice_city IS NOT NULL)")
    assert emptied.endswith("crm_code IS NOT NULL AND invoice_city IS NULL")
    merge, deleted, gone = (sink["query"] for sink in job["sink"])
    assert "ON t.[ACCOUNT_CODE] = s.[ACCOUNT_CODE] AND t.[ADDR_TYPE] = s.[ADDR_TYPE]" in merge
    assert "WHEN NOT MATCHED THEN INSERT" in merge
    assert deleted == gone == ("DELETE FROM [dbo].[account_address] "
                               "WHERE [ACCOUNT_CODE] = ? AND [ADDR_TYPE] = 'INV'")


def test_a_kind_of_row_is_read_with_its_match(compiled):
    sql = compiled["crm_invoice_address_to_hub"]["transform"][2]["query"]
    assert sql.endswith("WHERE ADDR_TYPE = 'INV'")
    assert "'crm_code,invoice_city' AS fields" in sql


def test_a_record_billing_has_not_numbered_yet_is_found_by_its_rule(compiled):
    """#80: until billing's insert comes back, the hub has no billing code for
    the record, so a second delivery would insert it twice. It matches the row
    by the pair's linkBy instead -- at the first sync, the row billing had all
    along."""
    merge = compiled["hub_to_billing"]["sink"][0]["query"]
    assert ("ON (t.[CustomerCode] = s.[CustomerCode] OR (s.[CustomerCode] IS NULL "
            "AND s._link = 1 AND t.[TaxId] = s.[TaxId]))") in merge
    assert "[CustomerCode]" not in merge.split("THEN INSERT")[1]


def test_a_value_outside_the_value_map_is_named_not_only_nulled(compiled):
    """#84: the CASE of a value map has no ELSE, so an unknown value lands as
    NULL. The flow also says which field it happened to, for the hub."""
    sql = compiled["crm_to_hub"]["transform"][2]["query"]
    assert ("CASE WHEN ACTIVE IS NOT NULL AND (CASE WHEN ACTIVE = 'Y' THEN true "
            "WHEN ACTIVE = 'N' THEN false END) IS NULL THEN 'active,' ELSE '' END "
            "AS unmapped") in sql
    assert compiled["crm_to_hub"]["sink"][0]["query"].endswith(
        "active, unmapped) values (?, ?, ?, ?, ?, ?, ?, ?, ?)")
    assert "unmapped" not in compiled["billing_to_hub"]["transform"][2]["query"]


def test_a_flows_settings_reach_seatunnels_env(compiled):
    """`job:` in the flow file is the job's env: the checkpoint interval, and
    SeaTunnel's own read limit when the flow asks for one (ADR 0026)."""
    from core import flow_jobs, flows as flowmod
    flow = flowmod.parse({"id": "x", "from": "a", "to": "b", "columns": {},
                          "job": {"checkpointInterval": 5000, "rowsPerSecond": 400}})
    assert flow_jobs.env(flow, "x") == {
        "job.mode": "STREAMING", "parallelism": 1, "job.name": "x",
        "checkpoint.interval": 5000, "read_limit.rows_per_second": 400}
    # A flow that says nothing gets the platform's default and no limit.
    plain = flowmod.parse({"id": "y", "from": "a", "to": "b", "columns": {}})
    assert flow_jobs.env(plain, "y") == {
        "job.mode": "STREAMING", "parallelism": 1, "job.name": "y",
        "checkpoint.interval": flow_jobs.CHECKPOINT_MS}


def test_only_a_flow_that_can_fall_back_to_linkby_reads_the_hubs_permission(compiled):
    """#120: the fallback is what finds the row a system had all along. A
    record the hub created beside one with the same value may not be found
    that way, so the hub says per record whether it may -- and only the flows
    that can fall back ask."""
    billing = compiled["hub_to_billing"]
    assert "_changed, _link FROM dual" in billing["transform"][3]["query"]
    assert "? AS _link" in billing["sink"][0]["query"]
    # The CRM's address flow has no code of its own to wait for, so it never
    # falls back and never reads it.
    address = compiled["hub_to_crm_invoice_address"]
    assert "_link" not in address["transform"][3]["query"]
    assert "_link" not in address["sink"][0]["query"]
