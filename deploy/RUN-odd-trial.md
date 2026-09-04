# Trying ODD Platform against this spike

The spike already produces everything ODD needs. This is the 10-minute path from
zero to "our contract-derived checks and their 45-day history are visible in
someone else's catalog".

## 1. Start ODD

```bash
docker compose -f deploy/odd-compose.yaml up -d
# wait for health, then open http://localhost:8080
curl -s localhost:8080/actuator/health
```

Needs Docker with access to `ghcr.io`. First start pulls ~400 MB and the platform
runs its own migrations, so give it a minute.

## 2. Have the spike's data ready

```bash
export ERP_DSN=postgresql://postgres:postgres@localhost:5432/erp
export DQ_DSN=postgresql://postgres:postgres@localhost:5432/dq
python3 seed/seed.py
PYTHONPATH=. python3 core/runner.py --backfill-days 44
```

## 3. Push into ODD

```bash
PYTHONPATH=. python3 integrations/odd/push.py --out artifacts/odd     # build + validate only
PYTHONPATH=. python3 integrations/odd/push.py --url http://localhost:8080
```

`DQ_HOST` (default `dq.local`) is the host segment of the ODDRNs this tool mints.
Keep it stable — it is the identity of every check and run in ODD.

What gets sent:

| payload | entities | ODD type |
|---|---|---|
| `00_catalog.json` | 2 tables with their columns, 23 checks | `TABLE`, `JOB` |
| `runs_<date>.json` x45 | one run per check per day | `JOB_RUN` |

The dataset ODDRNs are plain Postgres ODDRNs
(`//postgresql/host/<host>/databases/erp/schemas/public/tables/sales_orders`), the
same ones ODD's own `odd-collector` mints for that database. Run the collector
later and it merges onto these objects instead of creating duplicates.

## 4. What to look at, in this order

1. **Catalog → sales_orders → Test reports.** Are all 15 checks there, and does
   each show its 45-day run history?
2. **Quality dashboard.** Does the platform-wide view distinguish the incident
   days, or does it only count failing tests?
3. **A single check's history.** Can you see *how many rows* failed on a given
   day, or only pass/fail? (See `docs/odd-gap-analysis.md` — the answer is
   pass/fail plus whatever we crammed into `status_reason`.)
4. **Severity.** Our contract says `critical`/`major`/`minor`. Find where that
   ends up in the UI, and whether ODD's own severity has to be set by hand per
   dataset.
5. **Alerts.** Does a failing critical check open an alert, and does it close by
   itself when the next run passes?
6. **Glossary + lineage + owners.** The parts we did not build. Judge them on
   whether they are worth not building.

## 5. Tear down

```bash
docker compose -f deploy/odd-compose.yaml down -v
```
