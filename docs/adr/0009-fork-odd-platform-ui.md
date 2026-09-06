# 0009 — Fork ODD's UI to put the contract panel on its Data Quality page

## Context

ODD reports quality and cannot edit it: there is **no "create test" anywhere in
its UI**, because a test arrives through ingestion and belongs to whatever
produced it. The contract behind those tests is editable, and the place that
belongs is next to the dashboard saying a check failed.

Everything short of a fork was tried first and is recorded here so it is not
tried again:

* **Entity links** (`POST /api/dataentities/{id}/links`) render as an
  *Attachments* card. Real, native, and still just a link to another port.
* **Metrics** (`/ingestion/metrics`) render on the entity Overview and would be
  the right home for a score — but a family can only be written once. See
  ADR 0005.
* **No plugin system, no embed, no custom tab.** Checked against the running
  platform's API surface and its source, not assumed. The `injector/` directory
  in their repo is a demo-data loader, not an extension point.

## Decision

Fork `odd-platform-ui`, and keep it the **smallest fork that can work**.

The SPA ships as one jar on the platform's classpath —
`/app/libs/odd-platform-ui-<version>.jar`, a plain Vite build under `static/`,
named explicitly in `/app/jib-classpath-file`. So only the UI is rebuilt and
that single file is replaced: **no Gradle, no Java, no backend patch.**

`deploy/Dockerfile.odd-platform` clones upstream at the version we run,
generates their TypeScript API client with their own generator image, copies
our panel in, applies a two-line patch and repacks the jar.

The patch is `deploy/odd-platform-dq-panel.mjs`, and it **fails the build** when
an anchor moves rather than silently reverting whatever upstream did to the
page. Their file is never vendored.

The panel is written against ODD's own `Button`, `Input`, `Typography` and
theme, and **type-checks against them**. That is the point of forking rather
than injecting, and it paid immediately: `tsc` caught an `await` inside a
non-async state updater before the image was ever built.

## Consequences

* This is the most expensive thing in the repository to maintain, and it pins
  an ODD version.
* The panel calls our API cross-origin, so the API sets CORS — named origins,
  never `*`.
* The API base URL is resolved at runtime (`location.hostname:8077`, overridable
  by `window.__DQ_API__`) rather than baked in by Vite, so one image works
  everywhere.
* `web/index.html` still exists and is now a second UI. It is useful when ODD
  is not running; it is also double maintenance.

## On upgrade

Bumping ODD Platform is **the** reason this record exists:

1. Set `ODD_VERSION` in `deploy/Dockerfile.odd-platform` and rebuild. Everything
   else follows from the tag.
2. If the build fails in `odd-platform-dq-panel.mjs`, upstream moved the
   anchor. **Re-read `DataQualityContent.tsx` and update the patch** — do not
   pin the old version and move on.
3. If `tsc` fails, their component API changed. Fix the panel against it; that
   is what type-checking is for.
4. Check the jar name still matches `/app/jib-classpath-file`, and that the
   Vite `outDir` is still `build/ui` (it is not `dist`).
5. pnpm is pinned because the lockfile is 9.0 and corepack hands out whatever
   is current. If upstream's lockfile version changes, change the pin.

**Delete this fork the day ODD grows an extension point.**
