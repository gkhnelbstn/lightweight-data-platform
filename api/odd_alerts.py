"""ODD's own alerts, said in a chat that ODD cannot talk to.

ODD sends an alert -- a failed test, a schema change, a job that broke -- to
Slack, to e-mail, or as its own JSON to a generic webhook. Google Chat is none
of these: its incoming webhook wants `{"text": ...}` and answers ODD's JSON
with a 400. So ODD's generic webhook points here, and this turns the message
into one line of text and sends it the way core/alerts.py sends ours, to
`DQ_ODD_ALERT_URL` or, unset, to the same `DQ_ALERT_URL`.

The route takes no token: ODD's generic webhook sends no headers, and the only
thing a caller can do is post text to the one chat this server is configured
for. Keep 8077 on a private network, as compose.yaml already says.
"""
from __future__ import annotations

import os

from fastapi import APIRouter

from core import alerts

router = APIRouter()

EVENT = {"CREATED": "opened", "REOPENED": "reopened",
         "RESOLVED": "resolved", "RESOLVED_AUTOMATICALLY": "resolved by itself"}


def _url() -> str:
    return (os.getenv("DQ_ODD_ALERT_URL") or alerts.ALERT_URL).strip()


def compose(message: dict, odd_url: str) -> str:
    """One chat message for one ODD alert event.

    What happened, to what, where it lives, what ODD said about it, and how
    many things downstream are affected -- the last being the one fact a
    chat reader cannot get without opening ODD.
    """
    entity = message.get("data_entity") or {}
    kind = (message.get("alert_type") or "ALERT").replace("_", " ").lower()
    event = EVENT.get(message.get("event_type") or "", "changed")
    where = " / ".join(p for p in (entity.get("namespace_name"),
                                   entity.get("data_source_name")) if p)
    name = entity.get("name") or f"entity {entity.get('id', '?')}"
    link = f"{odd_url.rstrip('/')}/dataentities/{entity['id']}" if entity.get("id") else ""
    lines = [f"*ODD alert {event}:* {kind} on *{name}*"
             + (f" ({where})" if where else "")]
    lines += [f"- {c['description']}" for c in message.get("alert_chunks") or []
              if c.get("description")][:5]
    downstream = message.get("downstream") or []
    if downstream:
        lines.append(f"{len(downstream)} downstream: "
                     + ", ".join(d.get("name", "?") for d in downstream[:5]))
    if link:
        lines.append(f"<{link}|Open in ODD>")
    return "\n".join(lines)


@router.post("/api/alerts/odd")
def relay(message: dict) -> dict:
    """Receive ODD's generic webhook and pass it on as text."""
    url = _url()
    if not url:
        return {"delivered": False, "reason": "no DQ_ODD_ALERT_URL or DQ_ALERT_URL"}
    text = compose(message, os.getenv("DQ_ODD_UI_URL", "http://localhost:8080"))
    return {"delivered": alerts.send(text, url)}
