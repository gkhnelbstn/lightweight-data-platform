# 0017 — A view instead of a copy, where the copy buys nothing

## Context

Three places in this stack hold the same rows twice, and they were measured
rather than argued about:

| where | rows | size | mechanism |
|---|---|---|---|
| `erp_replica.sales_orders` | 50 288 | 8.8 MB | CDC, SQL Server → Postgres |
| `erp_replica.customers` | 90 | 64 kB | logical replication |
| `dwh` — `stg.orders`, `fct.orders`, `mart.revenue_daily`, `dim.customer` | 5 598 | ~600 kB | `create table as`, per run |

A fourth place already avoids it: the daily window is a schema of *views* over
the source (ADR 0002), which is the same idea this record extends.

Two questions came out of that table. Neither is about disk — 600 kB is not an
argument for anything, and invariant 6 cuts both ways: a mechanism has to earn
its place with a row count, and so does a copy.

**The warehouse.** `stg.orders` is a projection of `raw.orders` with a `where`
on it, and `mart.revenue_daily` is a `group by` over `fct.orders` in the same
database. Both are rebuilt by `demo/medallion.py` on every run, both are stale
between two runs, and both have to be dropped in the right order because the
daily window leaves `asof_*` views on them.

**Replication.** A `syncTo` rule names a target, a row filter and a column
list. Nothing in that rule says the target has to be a *copy*. Where the source
is Postgres and the target is Postgres, `postgres_fdw` expresses the same rule
with no worker, no slot, no lag and no staleness — including across machines,
because a foreign data wrapper is an ordinary client connection. That matters:
the two databases here happen to share a container, and nothing about the
design may assume it.

The reason this is a decision and not a refactor is that a view is not a
weaker copy, it is a different thing:

* **The privacy boundary changes shape.** In copy mode a column outside the
  list *does not exist* in the target. In view mode it exists at the source
  and the target could ask for it.
* **Availability inverts.** A copy keeps serving when the source is down. A
  view does not exist without it.
* **The read load moves.** Every query against the view is a query against the
  source, over the network.

## Decision

**Copy where the copy earns it; a view where it does not — and the contract
says which.**

### The warehouse: `stg` and `mart` are views

`demo/medallion.py` builds `stg.orders` and `mart.revenue_daily` as views.
`raw` stays physical because it is the landing of a read that crossed a network
and a `raw` you cannot re-read is the whole point of landing it. `fct.orders`
stays physical because it is an as-of range join nobody wants to re-run per
query. `dim.customer` stays physical because it is Type 2 history: by
definition rows the source no longer has, which no view can produce.

Measured before and after on the same data: `dwh.stg_orders` 0.9673 and
`dwh.mart_revenue` 1.0000 — the same scores the tables produced, because a
view is the same rows.

Materialising `mart.revenue_daily` later is `create materialized view` plus a
`refresh` in the same place, and it is a decision to take *after* a dashboard
is slow rather than before. A materialized view is also the answer to the
`cascade` gotcha, since `refresh` does not drop the dependent `asof_*` views.

### Replication: `mode: view` in the rule

```yaml
customProperties:
  - property: syncTo
    value:
      server: replica
      mode: view                 # or `copy`, which is the default
      filter: "qty > 0"
      columns: [order_id, line_no, sku, qty, line_amount]
```

`core/sync_view.py` turns that into `postgres_fdw`: a server, a user mapping, a
foreign table carrying only the listed columns, and a view applying the filter.
The consumer sees the same table name with the same shape as the copy would
have produced.

**The privacy boundary is a column-level grant, and it is verified.** The
foreign server maps to a dedicated login, `sync_fdw`, which is granted
`select` on the listed columns and nothing else — `revoke all` first, so a
column removed from the list loses its grant on the next apply. After the
grant, `apply` asks the source
`has_column_privilege('sync_fdw', table, column, 'select')` for every column
the rule does not list, and refuses the rule if any of them answers true. A
boundary that was only asked for is not a boundary.

**A classified column may not be listed in a view rule at all.** In copy mode
leaving it out makes it absent; here it would merely be ungranted, which is a
weaker promise than the contract's `classification:` implies. `core/sync.py`
refuses the rule rather than quietly offering the weaker one.

**A SQL Server source cannot be a view target.** `tds_fdw` is not in the
`postgres:16-alpine` image, and adding an image for one table is exactly what
invariant 6 forbids. The rule is refused with that reason, and CDC remains the
answer there — which is also where the mass is: 8.8 MB of the 8.9 MB duplicated
in this stack is the one place a view cannot go.

**Status means something different.** There is no worker to be dead and no slot
to be behind, so `--status` runs `select 1 from <view> limit 1` and reports
`reachable`. An unreachable source is not a slow view, it is a broken one.

## Consequences

* One `syncTo` vocabulary, two mechanisms, and the four logical-replication
  preconditions apply to exactly one of them — the same trap invariant 3
  records, now with a third mechanism to keep straight.
* A view target is only as available as its source, and puts the target's read
  load on it. For `erp.order_lines` — append-only, read rarely — that is the
  right trade; for a dashboard's hot table it would not be.
* `sync_fdw`'s password lives in the user mapping on the target and in
  `SYNC_FDW_PASSWORD`. It is a login role with column-level grants and nothing
  else, which is the smallest thing that works, and it is not `dq_reader`:
  the checks read everything they are pointed at and must not be the identity
  a foreign table borrows.
* Dropping and recreating the foreign server on every apply makes a rule that
  now points elsewhere impossible to half-apply, at the cost of a moment where
  the view does not exist.
* `stg.orders` and `mart.revenue_daily` no longer exist as tables, so anything
  that assumed `pg_stat_user_tables` would show them — including a size
  measurement — sees nothing. The row counts still come from `count(*)`.

## On upgrade

* **If a view target ever needs to survive its source being down**, it is not a
  view: change the rule back to `mode: copy`, which is one word and a re-apply.
* **`tds_fdw` in the image would let a SQL Server source be a view too.** It is
  not worth an image today; if the collector image ever needs rebuilding for
  another reason, re-measure — 50 288 rows read over ODBC per query is the
  number to beat, and CDC probably still wins.
* **If `postgres_fdw` filter pushdown stops covering the row filter** (check
  `explain (verbose)` for `Remote SQL`), the view starts pulling whole tables
  across the network and the mode's economics change.
* **Delete the warehouse half** if `demo/medallion.py` is ever replaced by dbt:
  dbt has its own table/view materialisation and this decision becomes a
  `materialized:` config in its models rather than something written here.
