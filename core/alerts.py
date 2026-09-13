"""Say out loud that something broke, once per run, to one webhook.

A quality platform whose failures are only visible to whoever happens to open
a page is a reporting tool rather than a control. That is the reason this
exists at all, and it is also why it is this small: invariant 6 wants a reason
for new infrastructure, and the reason justifies a POST, not a notification
service.

`DQ_ALERT_URL` unset means no alerting, silently, the same way `--odd-url`
does. Set it to a Slack or Teams incoming webhook -- both accept
`{"text": ...}` -- or to one line of glue in front of anything that does not.

Three things it reports, and they are deliberately different sentences:

* **a contract below its SLA**, because that is the promise the contract makes;
* **checks that newly started failing** -- failing today and passing on that
  contract's previous run. Not "checks that are failing": that is the same
  twenty every morning, which is how an alert channel gets muted and then
  deleted;
* **replication that is not moving**, from the same status the panel shows.

What it does not do is in the issue: no email (SMTP credentials, bounces and a
From address are three problems for one message), no per-check subscriptions
(that needs identity -- ADR 0010), and no alert history (the run log already
records what happened; an alert that fired is not a second kind of fact).

An **accepted** failure never alerts, which is the point of #29: it is a
failure somebody decided to live with. An *acknowledged* one still can, but
only when it newly fails -- somebody looking at it once does not make the next
outage unremarkable.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import date

ALERT_URL = os.getenv("DQ_ALERT_URL", "").strip()


def newly_failing(conn, contract_id: str, run_at: date,
                  window: str = "incremental") -> list[str]:
    """Checks failing on `run_at` that were not failing on the run before it.

    A check with no previous run is new, and a new check that fails is news.
    `accepted` is excluded here rather than by the caller because it is the
    same exclusion the Checks tab makes by default, and two places deciding
    what counts as a failure is how they drift apart.
    """
    rows = conn.execute(
        """with previous as (
               select max(run_at) as run_at from check_results
                where contract_id = %(contract)s and run_window = %(window)s
                  and run_at < %(run_at)s
           )
           select c.check_id, coalesce(c.name, c.check_id)
             from check_results c
             left join check_status s on s.check_id = c.check_id
             left join check_results before
               on before.check_id = c.check_id
              and before.contract_id = c.contract_id
              and before.run_window = c.run_window
              and before.run_at = (select run_at from previous)
            where c.contract_id = %(contract)s and c.run_window = %(window)s
              and c.run_at = %(run_at)s and c.status = 'fail'
              and coalesce(s.state, 'open') <> 'accepted'
              and coalesce(before.status, 'pass') <> 'fail'
            order by c.check_id""",
        {"contract": contract_id, "window": window, "run_at": run_at}).fetchall()
    return [name for _check_id, name in rows]


def sync_problems(statuses: list[dict]) -> list[str]:
    """One line per replication rule that is not moving data.

    Written over the status dict rather than over the engine, because there
    are now several ways to not be moving: a dead apply worker, a table stuck
    in the initial copy, a poll nobody started, and -- once a view target is
    a thing -- an unreachable source. A key that is not there is not a
    problem, so a status from an engine this does not know about stays quiet
    instead of crying wolf.
    """
    out = []
    for status in statuses or []:
        contract = status.get("contract", "?")
        if status.get("copying"):
            out.append(f"{contract}: {', '.join(status['copying'])} stuck in "
                       f"the initial copy -- nothing is replicating")
        elif status.get("streaming") is False:
            out.append(f"{contract}: the apply worker is not running")
        elif status.get("reachable") is False:
            out.append(f"{contract}: the view's source is unreachable")
    return out


def compose(as_of: date, contracts: list[dict], breaches: list[dict],
            new_failures: dict[str, list[str]], sync: list[str]) -> str | None:
    """The message, or None when there is nothing to say.

    Nothing to say is the common case and the one worth getting right: a run
    where nothing changed must send nothing at all.
    """
    lines = []
    for row in breaches:
        why = (f"{row['errored']} checks could not run"
               if row.get("errored") else f"score {row['score']:.4f} "
               f"below {float(row['sla_min']):.2f}")
        lines.append(f"SLA missed: {row['contract']} -- {why}")
    for contract_id, names in sorted(new_failures.items()):
        shown = ", ".join(names[:3])
        more = f" (+{len(names) - 3} more)" if len(names) > 3 else ""
        lines.append(f"Newly failing in {contract_id}: {shown}{more}")
    lines.extend(sync)
    if not lines:
        return None
    return f"*Contract quality {as_of}* -- {len(contracts)} contracts\n" + \
        "\n".join(f"• {line}" for line in lines)


def send(text: str, url: str | None = None) -> bool:
    """POST it. A webhook that will not answer is not worth failing a run for:
    the results are already stored and already in ODD."""
    target = url or ALERT_URL
    if not target:
        return False
    body = json.dumps({"text": text}).encode()
    request = urllib.request.Request(
        target, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15):
            return True
    except Exception as exc:  # noqa: BLE001 -- reported, never raised
        print(f"WARN alert not delivered ({exc})", flush=True)
        return False
