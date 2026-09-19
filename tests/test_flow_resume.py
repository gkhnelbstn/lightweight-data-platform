"""Where a stopped flow resumes, and when it must refuse. #84, ADR 0023.

Both refusals guard something SeaTunnel does silently: resume an id with no
checkpoint from scratch, and resume a SQL Server source past changes that CDC
retention purged. Both were reproduced on the demo before this was written.
"""
from __future__ import annotations

from core import flow_resume

HOUR = 3_600_000


def test_a_flow_that_never_ran_starts_fresh():
    assert flow_resume.plan("crm_to_hub", None, None, None, False) == ("fresh", None)


def test_a_flow_with_a_checkpoint_resumes():
    assert flow_resume.plan("crm_to_hub", 7, 10 * HOUR, 2 * HOUR, False) == ("resume", None)


def test_no_checkpoint_left_is_refused_not_rerun_from_scratch():
    action, why = flow_resume.plan("crm_to_hub", 7, None, None, False)
    assert action == "refuse" and "no checkpoint" in why and "--resnapshot" in why


def test_an_outage_longer_than_cdc_retention_is_refused():
    action, why = flow_resume.plan("crm_to_hub", 7, 2 * HOUR, 10 * HOUR, False)
    assert action == "refuse" and "purged by retention" in why


def test_resnapshot_is_the_deliberate_way_through_and_says_what_it_costs():
    action, why = flow_resume.plan("crm_to_hub", 7, None, None, True)
    assert action == "fresh" and "authority" in why


def test_the_last_checkpoint_is_read_from_the_file_names(tmp_path):
    job = tmp_path / "1153711258761428993"
    job.mkdir()
    for name in ("1789834796018-361-1-1195.ser", "1789834805017-567-1-1198.ser", "notes.txt"):
        (job / name).write_text("")
    assert flow_resume.last_checkpoint_ms(1153711258761428993, tmp_path) == 1789834805017
    assert flow_resume.last_checkpoint_ms(42, tmp_path) is None


def test_only_a_sql_server_source_has_a_retention_to_check():
    hub = {"servers": [{"type": "postgres"}], "schema": [{"physicalName": "customer"}]}
    assert flow_resume.oldest_change_ms(hub) is None


def test_a_postgres_source_whose_slot_is_gone_is_refused():
    """A resumed job makes a fresh slot at the current position: what changed
    since its checkpoint would be skipped."""
    action, why = flow_resume.plan("shop_to_hub", 7, 10 * HOUR, None, False, slot_missing=True)
    assert action == "refuse" and "replication slot is gone" in why
