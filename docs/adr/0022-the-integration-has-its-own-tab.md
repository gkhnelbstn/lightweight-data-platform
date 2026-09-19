# 0022 — The integration has a tab of its own in ODD's menu

## Context

The two-way integration (ADR 0020, 0021) ran with nothing about it on screen.
SeaTunnel's console, on :8081, names jobs by id and draws their vertices; it
does not say which systems a job connects, and it cannot say what only the hub
knows: whether changes are arriving and how late, which values lost a
conflict, which rows wait for a person (#80), and what was deleted.

The request (#78) was for an understandable interface, **on a tab of its
own**. Three places were possible:

* **A tab inside our Data Quality panel.** No new anchors, since the panel is
  already there (ADR 0009). But the integration is not data quality, and a
  fifth tab under a page opened to ask "what failed" is where nobody looks
  for "is billing receiving the CRM's changes".
* **A link to SeaTunnel's console.** No code, and none of the answers.
* **An entry in ODD's own menu**, beside Data Quality. Costs new anchors in
  two more of their files.

## Decision

An entry in ODD's menu, `/integration`, and a page of ours behind it.

`deploy/odd-platform-integration-tab.mjs` adds four single-line anchors:

* in `ToolbarTabs.tsx`, an import and the menu item;
* in `App.tsx`, a lazy import and the route.

It has the same shape as the panel's patch: an anchor that moves **fails the
build**, rather than the tab quietly disappearing. The page is
`deploy/odd-platform-ui/Integration.tsx`, built from ODD's own components like
the panel, and it reads one route, `GET /api/integration` (`api/integration.py`).
That route asks three sources server-side, so no credential reaches the
browser:

* the flow files and hub contracts, for what connects to what;
* SeaTunnel's REST API, for each flow's job, named by flow, and what it
  read and wrote;
* the hub database, for arrivals and their delay, conflicts, held rows and
  tombstones.

Each source can be down without the others. The page then says which one is
down, rather than failing as a whole. A column the hub contract classifies is
masked in conflicts and held rows, as it is in failing rows
(`core/sample.py`): a conflict over a tax number shows that there was one, not
the number.

The menu label is in our `ldp` namespace, so it switches language with ODD's
picker like the panel does. That needs the catalogue registered before the menu
first renders, so the registration moved from `shared.tsx` to `nav.ts`, which
the menu imports. The catalogue, about ten kilobytes, is now in ODD's main
bundle rather than the panel's chunk.

The page is read-only. What connects to what is the flow files' to say (ADR
0019). Linking a held row to a record is `hub.link` on the hub database; a
button for it waits for someone to need it from the page.

## Consequences

* The fork now touches three of ODD's files instead of one, still by anchor,
  never by vendoring. ADR 0009's cost grows by exactly these four lines.
* `INTEGRATION_DIR` tells the API where the hub contracts are. It defaults to
  `contracts/`, and the demo compose points it at `demo/integration/`.
* `tests/test_integration_api.py` covers the route with SeaTunnel and the hub
  both down, the choice of each flow's newest job, and the masking. CI builds
  the ODD image on every pull request, so a moved anchor fails there.

## On upgrade

* If the build fails in `odd-platform-integration-tab.mjs`, upstream moved the
  menu or the route table. Re-read `ToolbarTabs.tsx` and `App.tsx` and move the
  anchors. Do not pin the old version.
* The menu marks a tab selected when the path *contains* its `value`.
  `integration` is safe only while no other ODD path contains the word. At
  0.29.0, `/management/integrations` does, and the Management entry comes
  later in the list, so it wins there. Check this if the order changes.
* Delete this patch, and the panel's, the day ODD grows an extension point
  (ADR 0009).
