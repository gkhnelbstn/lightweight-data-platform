"""Which URLs may become a discussion space, and what a message looks like.

A space is a URL the server POSTs to, entered on a screen: anything other
than a Google Chat incoming webhook is an SSRF door, and the URL is itself a
credential that must not come back to the screen. ADR 0028.
"""
from __future__ import annotations

import pytest

from api.discussions import chat_payload, masked, webhook_space

GOOD = ("https://chat.googleapis.com/v1/spaces/AAQA1b2C3d4/messages"
        "?key=FAKE-KEY-FOR-TESTS&token=abc123")


def test_a_google_chat_webhook_is_accepted():
    assert webhook_space(GOOD) == "AAQA1b2C3d4"


@pytest.mark.parametrize("url", [
    GOOD.replace("https://", "http://"),                                  # clear text
    GOOD.replace("chat.googleapis.com", "chat.googleapis.com.evil.io"),   # lookalike host
    GOOD.replace("chat.googleapis.com", "169.254.169.254"),               # metadata service
    GOOD.replace("https://", "https://user:pw@"),                         # userinfo
    GOOD.replace("chat.googleapis.com", "chat.googleapis.com:8443"),      # other port
    GOOD.replace("/messages", "/messages/../../admin"),                   # other path
    GOOD.replace("&token=abc123", ""),                                    # no token
    "https://hooks.slack.com/services/T/B/X",                             # another service
    "not a url",
])
def test_anything_else_is_refused(url):
    assert webhook_space(url) is None


def test_the_screen_never_gets_the_credential():
    shown = masked(GOOD)
    assert "AAQA1b2C3d4" in shown
    assert "FAKE-KEY" not in shown and "abc123" not in shown


def test_a_message_lands_in_its_assets_thread_with_a_link_back():
    body = chat_payload(239, "sbr_firma", "Gökhan", "Vergi no boş 17 firma var.",
                        "http://odd.local:8080/")
    assert body["thread"] == {"threadKey": "odd-entity-239"}
    assert body["text"].startswith("*Gökhan* · <http://odd.local:8080/dataentities/239|sbr_firma>")
    assert body["text"].endswith("Vergi no boş 17 firma var.")
