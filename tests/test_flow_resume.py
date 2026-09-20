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


def test_a_checkpoint_seatunnel_cannot_read_is_a_refusal_not_a_traceback(monkeypatch):
    """#125: a checkpoint truncated by a crash exists, so it is resumed from
    and SeaTunnel answers the submit with a bare HTTP 500. Reported like the
    other two refusals -- the flow, what happened, and `--resnapshot` -- the
    operator reads one sentence instead of a `urllib` stack trace and the
    SeaTunnel server log."""
    import urllib.error

    from core import flow_apply, flows

    class FakeCx:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, statement, args=None):
            assert statement.startswith("select job_id"), "nothing is recorded"
            return type("R", (), {"fetchone": staticmethod(lambda: (7,))})()

    flow = flows.parse({"id": "f", "from": "a.t", "to": "hub.c", "columns": {"x": "y"}})
    monkeypatch.setattr(flow_apply, "register", lambda *a: None)
    monkeypatch.setattr(flow_apply, "running", lambda: set())
    monkeypatch.setattr(flow_apply, "jobs_of", lambda by_id, f: [("f", {})])
    monkeypatch.setattr(flow_apply, "_hub_dsn", lambda by_id, f: "")
    monkeypatch.setattr(flow_apply.flow_schema, "problems", lambda f, by_id: [])
    monkeypatch.setattr(flow_apply.psycopg, "connect",
                        lambda dsn, autocommit=True: FakeCx())
    monkeypatch.setattr(flow_apply.flow_resume, "last_checkpoint_ms", lambda job: 1)
    monkeypatch.setattr(flow_apply.flow_resume, "oldest_change_ms", lambda source: None)
    monkeypatch.setattr(flow_apply.flow_resume, "slot_missing", lambda source, slot: False)

    def refused(method, path, body=None):
        assert "isStartWithSavePoint=true" in path
        raise urllib.error.HTTPError(path, 500, "Server Error", None, None)

    monkeypatch.setattr(flow_apply, "_http", refused)
    said, problems = flow_apply.apply({}, [flow], {"f": {}})
    assert said == [] and len(problems) == 1
    assert "--resnapshot" in problems[0] and "HTTP 500" in problems[0]
