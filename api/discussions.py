"""Discussions about an asset, in Google Chat. ADR 0028

ODD's Discussions tab has one provider, Slack. This team talks in Google Chat,
so the tab is ours instead (deploy/odd-platform-discussions.mjs): a message
written on an asset's page is kept here and posted to a Google Chat space,
one thread per asset (`threadKey`), with a link back to the asset.

**The spaces are configured on the screen**, and that is where the security
work is. A space is an incoming-webhook URL, which is a credential -- anyone
holding it can post to the space -- and a URL the server will POST to, which
is an SSRF door if it can be anything. So:

* only `https://chat.googleapis.com/v1/spaces/<id>/messages?key=..&token=..`
  is accepted, and a redirect is never followed;
* the URL never leaves the server: the screen gets the space id and a mask;
* adding or removing a space needs the API token, like raw SQL does.

Writing a message needs no token, as a note on a check needs none: it goes
only to a space someone with the token chose.

**One way.** An incoming webhook can post and cannot read, so a reply typed
in Google Chat stays there; the thread is linked from here. Reading replies
needs a Chat app with a public endpoint, which this platform does not have.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from urllib.parse import parse_qs, urlsplit

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from core import store

router = APIRouter()
SPACE_PATH = re.compile(r"/v1/spaces/([A-Za-z0-9_-]+)/messages")
SCHEMA = """
create table if not exists chat_space (
  id serial primary key, name text not null unique,
  webhook_url text not null, created_at timestamptz not null default now());
create table if not exists discussion (
  id bigserial primary key, entity_id bigint not null, entity_name text,
  space_id int references chat_space (id) on delete set null,
  author text not null, body text not null,
  created_at timestamptz not null default now(),
  delivered boolean not null default false, error text);
create index if not exists discussion_entity on discussion (entity_id, created_at desc);
"""


def _authorised(authorization: str = Header(default="")) -> None:
    from api.main import authorised   # api.main imports this module
    authorised(authorization)


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with psycopg.connect(store.DQ_DSN, row_factory=dict_row) as cx:
        cx.execute(SCHEMA)
        cur = cx.execute(sql, params)
        return cur.fetchall() if cur.description else []


def webhook_space(url: str) -> str | None:
    """The space id of a Google Chat incoming webhook, or None if it is not one."""
    try:
        s = urlsplit(url.strip())
        port = s.port
    except ValueError:
        return None
    query = parse_qs(s.query)
    m = SPACE_PATH.fullmatch(s.path)
    if (s.scheme != "https" or s.hostname != "chat.googleapis.com"
            or port not in (None, 443) or s.username or s.password or not m
            or not query.get("key") or not query.get("token")):
        return None
    return m.group(1)


def masked(url: str) -> str:
    return f"chat.googleapis.com/v1/spaces/{webhook_space(url)}/messages?key=…&token=…"


def chat_payload(entity_id: int, entity_name: str, author: str, body: str,
                 odd_url: str) -> dict:
    """One message, in the thread that belongs to this asset."""
    link = f"{odd_url.rstrip('/')}/dataentities/{entity_id}"
    return {"text": f"*{author}* · <{link}|{entity_name}>\n{body}",
            "thread": {"threadKey": f"odd-entity-{entity_id}"}}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002 -- stdlib shape
        return None


def deliver(url: str, payload: dict) -> str | None:
    """POST it; the error text, or None when it arrived."""
    target = f"{url}&messageReplyOption=REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"
    request = urllib.request.Request(
        target, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json; charset=UTF-8"})
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=10):
            return None
    except Exception as exc:  # noqa: BLE001 -- stored and shown, never raised
        return f"{exc.__class__.__name__}: {exc}"[:300]


class SpaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    webhook_url: str = Field(max_length=500)


class MessageIn(BaseModel):
    space_id: int
    author: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=4000)
    entity_name: str = Field(default="", max_length=300)


@router.get("/api/discussions/spaces")
def spaces() -> list[dict]:
    return [{"id": r["id"], "name": r["name"], "target": masked(r["webhook_url"])}
            for r in _q("select id, name, webhook_url from chat_space order by name")]


@router.post("/api/discussions/spaces", dependencies=[Depends(_authorised)])
def add_space(space: SpaceIn) -> dict:
    if webhook_space(space.webhook_url) is None:
        raise HTTPException(422, "not a Google Chat incoming-webhook URL "
                                 "(https://chat.googleapis.com/v1/spaces/…/messages?key=…&token=…)")
    try:
        [row] = _q("insert into chat_space (name, webhook_url) values (%s, %s) returning id",
                   (space.name.strip(), space.webhook_url.strip()))
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, f"a space named {space.name!r} exists") from None
    return {"id": row["id"], "name": space.name, "target": masked(space.webhook_url)}


# POST rather than DELETE: the API's CORS allows GET and POST only.
@router.post("/api/discussions/spaces/{space_id}/delete", dependencies=[Depends(_authorised)])
def delete_space(space_id: int) -> dict:
    _q("delete from chat_space where id = %s", (space_id,))
    return {"deleted": space_id}


@router.get("/api/discussions/{entity_id}")
def messages(entity_id: int) -> list[dict]:
    return _q("""select d.id, d.author, d.body, d.created_at, d.delivered, d.error,
                        s.name as space
                   from discussion d left join chat_space s on s.id = d.space_id
                  where d.entity_id = %s order by d.created_at desc limit 200""", (entity_id,))


@router.post("/api/discussions/{entity_id}")
def post_message(entity_id: int, msg: MessageIn) -> dict:
    found = _q("select webhook_url from chat_space where id = %s", (msg.space_id,))
    if not found:
        raise HTTPException(404, "no such space")
    name = msg.entity_name or f"entity {entity_id}"
    error = deliver(found[0]["webhook_url"], chat_payload(
        entity_id, name, msg.author, msg.body,
        os.getenv("DQ_ODD_UI_URL", "http://localhost:8080")))
    [row] = _q("""insert into discussion (entity_id, entity_name, space_id, author, body,
                                          delivered, error)
                  values (%s, %s, %s, %s, %s, %s, %s) returning id, created_at""",
               (entity_id, name, msg.space_id, msg.author, msg.body, error is None, error))
    return {**row, "delivered": error is None, "error": error}
