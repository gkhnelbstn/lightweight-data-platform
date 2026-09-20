"""The outage drill for ADR 0023: SeaTunnel restarts, and nothing edited while
it was down is lost.

A restart loses every job. Recovery resubmits each flow under its old id,
from its checkpoint, so what changed meanwhile arrives as changes, in commit
order. A fresh start instead re-read every table: the edit below would have
been reverted by the first-sync rule, and the delete never seen.

    docker compose exec app python demo/integration/outage.py before
    docker compose -f compose.yaml -f compose.demo.yaml restart seatunnel
    docker compose exec -e PG_USER=... -e MSSQL_USER=... -e MSSQL_PASSWORD=... \
        -e DATACONTRACT_SQLSERVER_PASSWORD=... app python demo/integration/outage.py after
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import verify as v  # noqa: E402

from core import flow_apply, flow_jobs  # noqa: E402

STATE = Path("/tmp/outage-drill.json")


def before() -> None:
    """Two customers both systems have: one to edit, one to delete."""
    base = random.randint(10_000, 99_000)
    kept, gone = [(base + i, str(random.randint(10**9, 10**10 - 1))) for i in (0, 1)]
    for code, tax in (kept, gone):
        v.sql(v.CRM, "insert into dbo.account (ACCOUNT_CODE, TITLE, TAX_NO, ACTIVE) "
                     "values (?, ?, ?, 'Y')", code, f"Drill {code}", tax)
    v.wait("both reach billing, linked", lambda: v.linked(kept[1]) and v.linked(gone[1]))
    STATE.write_text(json.dumps({"kept": kept, "gone": gone}))
    print("now restart SeaTunnel, then run this with `after`")


def after() -> None:
    state = json.loads(STATE.read_text())
    (code, tax), (gone_code, gone_tax) = state["kept"], state["gone"]
    assert not flow_apply.running(), "flows still run: restart SeaTunnel first"

    # While nothing runs: an edit on the side that is not the authority, a
    # delete, and a customer that is new.
    new_code, new_tax = code + 500, str(int(tax) + 1)
    v.sql(v.BILLING, "update dbo.customer set Name = ? where TaxId = ?", "Edited meanwhile", tax)
    v.sql(v.BILLING, "delete from dbo.customer where TaxId = ?", gone_tax)
    v.sql(v.CRM, "insert into dbo.account (ACCOUNT_CODE, TITLE, TAX_NO, ACTIVE) "
                 "values (?, ?, ?, 'Y')", new_code, "New meanwhile", new_tax)

    start = time.monotonic()
    by_id, flows = flow_jobs.load(Path("demo/integration"))
    said, refused = flow_apply.apply(by_id, flows, flow_jobs.jobs(by_id, flows))
    for line in said + [f"REFUSED: {r}" for r in refused]:
        print(line)
    v.wait("the edit made while down reaches the CRM, not reverted",
           lambda: v.crm_row(code) == ("Edited meanwhile", "Y"), seconds=120)
    v.wait("the delete made while down reaches the CRM", lambda: v.crm_row(gone_code) is None)
    v.wait("the customer made while down reaches billing", lambda: v.linked(new_tax))
    print(f"  ok  caught up {time.monotonic() - start:.0f} s after the resubmit")
    seeds = v.hub("select count(*) from hub.conflict where reason = 'seed' and at > now() - "
                  "make_interval(secs => %s)", time.monotonic() - start + 5)
    assert seeds == [(0,)], f"{seeds[0][0]} values went to the first-sync rule: a re-read"
    print("  ok  nothing went through the first-sync rule")

    for c in (code, new_code):
        v.sql(v.CRM, "delete from dbo.account where ACCOUNT_CODE = ?", c)
    v.wait("clean up", lambda: v.billing_row(tax) is None and v.billing_row(new_tax) is None)
    STATE.unlink()


if __name__ == "__main__":
    {"before": before, "after": after}[sys.argv[1]]()
