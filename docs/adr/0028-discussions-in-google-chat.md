# 0028. An asset's discussion is posted to Google Chat, one way

Date: 2026-09-22

## Status

Accepted. Replaces, for this deployment, the Slack wiring README describes
under "Talking about an asset". That wiring stays in the image and is simply
not reached.

## Context

ODD's Discussions tab is built on its data-collaboration service, and that
service has one provider: Slack (`MessageProviderDto.SLACK`). With no Slack
workspace connected, the tab offers no channel, so the tab is useless. The
team that runs this platform talks in Google Chat.

Adding a provider to ODD means changing its backend: the service, the event
receiver and the channel model. The fork has deliberately stayed at one
backend class (ADR 0009, ADR 0011). The contract service already stores
per-check notes and already posts to chat webhooks (`core/alerts.py`,
`api/odd_alerts.py`).

## Decision

**The tab is ours.** One anchored line
(`deploy/odd-platform-discussions.mjs`) points the tab's lazy import at
`deploy/odd-platform-ui/Discussions.tsx`. ODD's route, tab and visibility
rules stay as they are. Messages are kept by the contract service
(`api/discussions.py`, tables `chat_space` and `discussion` in `dq`). Each
message is posted to a Google Chat space through its incoming webhook, in one
thread per asset (`threadKey: odd-entity-<id>`), with a link back to the
asset.

**Spaces are configured on the screen.** The security of the feature lives
here. A space is an incoming-webhook URL, which is two things at once:

* **A credential.** Anyone holding the URL can post to the space. The URL is
  therefore kept on the server and returned only masked.
* **A place the server will POST to.** If any URL were accepted, this would
  open a server-side request forgery (SSRF) path. So only
  `https://chat.googleapis.com/v1/spaces/<id>/messages?key=…&token=…` is
  accepted, on port 443 with no userinfo, and a redirect is never followed.

Adding or removing a space needs the API token, as raw SQL does (ADR 0010).
Writing a message does not, just as a note on a check does not: a message
can only go to a space that someone holding the token chose.

**One way.** An incoming webhook can post but cannot read, so a reply typed in
Google Chat stays in Google Chat. The thread there is the conversation, and
the tab is its entry point and its record of what was sent from here.

## Consequences

* The Discussions tab works with no Slack workspace. It shows what was said
  from ODD, including a message Google Chat refused, which is kept with the
  refusal.
* Replies do not come back. Bringing them back needs a Google Chat *app*
  with a publicly reachable event endpoint and a service account. That is a
  deployment this platform does not have, and it is the day to revisit this
  decision.
* A deployment that does have Slack loses ODD's native tab while this patch
  is carried. Remove the one line to get it back.

## On upgrade

Check whether ODD's data-collaboration service has gained a provider model,
or Google Chat itself. If it has, delete the patch and `Discussions.tsx`, and
move the spaces into ODD's own configuration.
