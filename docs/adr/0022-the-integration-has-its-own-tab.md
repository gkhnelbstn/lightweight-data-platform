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

### A log line opens its record

A conflict line says a value lost, but not what the record is now or how it
got there. Every line of the conflicts, waiting and deleted lists opens in
place (`deploy/odd-platform-ui/RecordDetail.tsx`,
`api/integration_detail.py`):

* **a record** shows each field's value, the system that set it and when,
  and its code in every system. Its history lists what each system sent,
  with an update as `from → to` rather than a before row and an after row,
  and **what the hub did with it**;
* **a held row** shows why the hub could not place it, the records its rule
  matched, and the `hub.link` call that settles each choice, filled in.

"What the hub did" had not been recorded. `hub.merge` now returns it, and the
inbox keeps it in `outcome`: `applied`, `created`, `deleted`, `lost` to a later
edit, `held`, `unchanged`, or `echo`. `echo` is the one that mattered: the hub's
own delivery coming back through a system's CDC reads exactly like that system
editing the field, and on the first sync the history of a disputed name was
four such lines.

## The tab answers at a glance, filters, and settles (#111)

Three things were the difference between a page that lists what the hub knows
and one somebody uses:

* **A header that answers the first question.** Records, how many flows
  actually run, the slowest arrival, and how much waits for a person -- all
  counts the tab already had, one tab-click away each. Beside them, one bar
  per hour of the changes that reached the hub over the last day: a count
  cannot say whether 33 an hour is normal, and a shape can.
* **A search box over each log.** Conflicts, held rows and deletions grow.
  One box over the row's own words -- a code, a system, a field -- rather
  than a filter per column, because that is what a person arrives knowing.
* **A decision, taken here.** A held row showed its candidate records and
  printed the `hub.link` call for someone to paste into psql. The button runs
  the same function, and the hub's refusals -- a record that already holds
  another code from this system -- come back as the sentence rather than a
  500. It is the one write on this tab, and it says which record a code
  belongs to, never what a field holds.

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
