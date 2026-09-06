"""Run the contracts as of a date, store the results, score them.

The checks are not derived here any more. `datacontract test` reads an ODCS
contract, derives the schema checks from the properties, compiles them and the
hand-written SQL rules to the server's dialect, runs them inside the source
database, and writes JSON with `failed_rows` and `row_count` per check. That is
the same two-column contract core/compilers/sql.py used to produce, from a tool
that also speaks SQL Server, MySQL, Snowflake and twenty other things.

What is still ours, and why:

**The window.** Scoring the daily increment rather than the whole table is the
one measured decision in this project -- cumulative scoring flattened two real
incidents into a line that never moved. datacontract-cli's `--filter` is exactly
this and is broken in 1.1.3 (a nameless `DROP VIEW IF EXISTS`), and the ibis API
it is built on, `Table.alias`, is documented by ibis as not public and due for
removal. So the window is a database object instead: a schema of views over one
day's arrivals, which the contract addresses through a second `servers` entry.
Standard ODCS, standard SQL, nothing to patch and nothing to fork.

**The time series and the score.** `datacontract test` runs and forgets.

**Which window each check means.** A row filter is all-or-nothing, and checks
divide into two kinds that want opposite windows. `not_null` scoped to today
answers "did today's load bring nulls"; unscoped, a null from three weeks ago
keeps it red forever. Uniqueness is the other way round: scoped to one day it
only sees duplicates that arrive in the same batch, so a row duplicating a key
loaded last week passes. That is not hypothetical -- our windowed uniqueness
check passed for 45 days on a table with 8 duplicate primary keys in it. So
table-level invariants are re-run unwindowed and their results replace the
windowed ones. Raised upstream as
https://github.com/datacontract/datacontract-cli/issues/1593; until ODCS can
say it per rule, the list below is the local answer.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from datetime import date, timedelta
from pathlib import Path

import psycopg
import yaml
from psycopg import sql

from core import store
from core.scoring import score

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "contracts"

# The server block whose schema holds the daily views, and the one that holds
# the real tables. A contract without the first is run unwindowed.
DAILY_SERVER = os.getenv("DQ_DAILY_SERVER", "erp_daily")
# The default window: rows that arrived on the run date. A contract whose table
# is updated in place says so itself -- see `window_predicate`.
DEFAULT_PREDICATE = "{col} = {day}"
HOST = os.getenv("DQ_HOST", "dq.local")

# Checks whose meaning is the whole table, not the day's arrivals. datacontract
# names them in `check["type"]`; anything not listed follows the window.
TABLE_SCOPED_TYPES = frozenset({"field_unique", "field_primary_key"})
WINDOW_SCHEMA = os.getenv("DQ_WINDOW_SCHEMA", "asof")


def load_contracts(directory: Path = CONTRACTS) -> list[dict]:
    """Every ODCS contract in the directory, oldest name first."""
    out = []
    for path in sorted(directory.glob("*.odcs.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        doc["_path"] = str(path)
        out.append(doc)
    return out


def _server(contract: dict, key: str) -> dict | None:
    return next((s for s in contract.get("servers", [])
                 if s.get("server") == key), None)


def _tables(contract: dict) -> list[tuple[str, str]]:
    """`(logical name, physical name)` for every model in the contract."""
    return [(m["name"], m.get("physicalName") or m["name"])
            for m in contract.get("schema", [])]


def window_predicate(contract: dict, model: dict | None = None) -> str:
    """The SQL that decides which rows a day's view contains.

    `loaded_at = <day>` is a watermark: it sees inserts and nothing else. A row
    corrected in place after it landed keeps its original `loaded_at` and is
    never re-checked, which is the honest limit of not having CDC.

    Rather than pretend otherwise, the predicate is the contract's to state, as
    an ODCS custom property on the model or at the top level:

        customProperties:
          - property: windowPredicate
            value: "{col} = {day} or updated_at::date = {day}"

    `{col}` is the watermark column and `{day}` the run date, both quoted by
    psycopg. A source with real CDC points its contract at the change table and
    windows on the change timestamp instead; a source without one and without
    an `updated_at` cannot express updates, and should say so in its README
    rather than in a comment here.
    """
    for holder in (model or {}, contract):
        for prop in holder.get("customProperties") or []:
            if prop.get("property") == "windowPredicate":
                return str(prop["value"])
    return DEFAULT_PREDICATE


def build_window(contract: dict, as_of: date, dsn: str = None,
                 window: str = "incremental") -> int:
    """Rebuild the day's views, and return how many were created.

    Every table the contract names gets a view of the same name restricted to
    the rows that arrived on *as_of*. Anything those queries join to and the
    contract does not name -- sales_order_lines, say -- is mirrored unfiltered,
    so a rule that joins still resolves.

    `cumulative` widens the predicate to everything up to *as_of*, which exists
    to be compared against: scoring the whole history flattens an incident into
    a line that does not move, and that comparison is the reason the daily
    window is the default.
    """
    daily = _server(contract, DAILY_SERVER)
    if daily is None:
        return 0
    source = _server(contract, "erp") or contract["servers"][0]
    loaded_at = os.getenv("DQ_LOADED_AT_COLUMN", "loaded_at")
    src_schema = source.get("schema", "public")
    win_schema = daily.get("schema", WINDOW_SCHEMA)
    if source.get("type") in ("sqlserver", "mssql"):
        return _build_window_mssql(contract, as_of, source, src_schema,
                                   win_schema, loaded_at, window)

    made = 0
    with psycopg.connect(dsn or store.ERP_DSN, autocommit=True) as cx:
        cx.execute(f'create schema if not exists "{win_schema}"')
        named = {physical for _, physical in _tables(contract)}
        by_table = {(m.get("physicalName") or m["name"]): m
                    for m in contract.get("schema", [])}
        # Every table in the source schema is mirrored: the ones the contract
        # names get the day's rows, the rest are passed through so joins work.
        rows = cx.execute(
            """select table_name from information_schema.tables
               where table_schema = %s and table_type = 'BASE TABLE'""",
            (src_schema,)).fetchall()
        for (table,) in rows:
            has_window = cx.execute(
                """select 1 from information_schema.columns
                   where table_schema = %s and table_name = %s
                     and column_name = %s""",
                (src_schema, table, loaded_at)).fetchone()
            # A view definition cannot take a bind parameter, so the date is
            # composed in as a literal -- psycopg quotes it, and `as_of` is a
            # date object rather than anything a caller typed.
            stmt = sql.SQL("create or replace view {win}.{tbl} as "
                           "select * from {src}.{tbl}").format(
                win=sql.Identifier(win_schema), src=sql.Identifier(src_schema),
                tbl=sql.Identifier(table))
            if has_window and table in named:
                if window == "incremental":
                    template = window_predicate(contract, by_table.get(table))
                else:
                    # the comparison the daily window exists to beat
                    template = "{col} <= {day}"
                stmt = stmt + sql.SQL(" where ") + sql.SQL(template).format(
                    col=sql.Identifier(loaded_at), day=sql.Literal(as_of))
            cx.execute(stmt)
            made += 1
    return made


def _build_window_mssql(contract: dict, as_of: date, source: dict,
                        src_schema: str, win_schema: str, loaded_at: str,
                        window: str) -> int:
    """The same schema of views, in T-SQL -- except it is a database.

    Without a window a SQL Server contract is scored over its whole table every
    day, which is the cumulative scoring this project exists to argue against,
    quietly reintroduced by having implemented the window for one engine only.
    Its score sat at 0.8957 for forty-five days without moving.

    The Postgres window is a *schema* because `search_path` makes an
    unqualified `sales_orders` resolve to the view. SQL Server has no
    search_path -- an unqualified name resolves through the user's default
    schema -- and the rules in a T-SQL contract are written `dbo.sales_orders`
    anyway, so a second schema is invisible to them. A second *database* is
    not: `erp_asof.dbo.sales_orders` is what `dbo.sales_orders` means once the
    connection is pointed at it, and the contract needs no rewriting.
    """
    from core.sync_mssql import mssql_connect

    window_db = contract_window_database(contract) or f"{source['database']}_asof"
    with mssql_connect(source) as cx:
        # CREATE DATABASE cannot run inside a transaction, and pyodbc opens one
        # for you: "CREATE DATABASE statement not allowed within
        # multi-statement transaction."
        cx.autocommit = True
        cx.cursor().execute(
            f"if db_id('{window_db}') is null "
            f"exec('create database [{window_db}]')")

    made = 0
    named = {physical for _, physical in _tables(contract)}
    by_table = {(m.get("physicalName") or m["name"]): m
                for m in contract.get("schema", [])}
    with mssql_connect(source) as src,             mssql_connect({**source, "database": window_db}) as win:
        tables = [r[0] for r in src.cursor().execute(
            "select table_name from information_schema.tables "
            "where table_schema = ? and table_type = 'BASE TABLE'",
            src_schema).fetchall()]
        cur = win.cursor()
        for table in tables:
            windowed = src.cursor().execute(
                "select 1 from information_schema.columns where table_schema = ? "
                "and table_name = ? and column_name = ?",
                src_schema, table, loaded_at).fetchone()
            stmt = (f"create or alter view [{win_schema}].[{table}] as select * "
                    f"from [{source['database']}].[{src_schema}].[{table}]")
            if windowed and table in named:
                template = (window_predicate(contract, by_table.get(table))
                            if window == "incremental" else "{col} <= {day}")
                # `as_of` is a date object and the column name comes from
                # information_schema, so neither is caller text.
                stmt += " where " + template.format(
                    col=f"[{loaded_at}]", day=f"'{as_of.isoformat()}'")
            cur.execute(stmt)
            made += 1
        win.commit()
    return made


def contract_window_database(contract: dict) -> str | None:
    """The database the daily server points at, when it names a different one."""
    daily, source = _server(contract, DAILY_SERVER), _server(contract, "erp")
    if daily and source and daily.get("database") != source.get("database"):
        return daily.get("database")
    return None


def table_rows(contract: dict, server_key: str) -> dict[str, int]:
    """How many rows each of the contract's tables holds, in the window it was
    checked in.

    datacontract reports `row_count` for the checks it derives and not for the
    SQL a person wrote, so every custom rule arrived with `total_rows = 0` and
    a `fail_ratio` of zero. That silently deleted the volume half of the score:
    a rule failing on one row and the same rule failing on seven hundred scored
    identically, which is the opposite of what the blend is for.
    """
    server = _server(contract, server_key) or _server(contract, "erp")
    if server is None:
        return {}
    schema = server.get("schema", "public")
    tables = [physical for _, physical in _tables(contract)]
    out: dict[str, int] = {}
    try:
        if server.get("type") in ("sqlserver", "mssql"):
            from core.sync_mssql import mssql_connect
            with mssql_connect(server) as cx:
                for t in tables:
                    out[t] = cx.cursor().execute(
                        f"select count(*) from [{schema}].[{t}]").fetchval()
        else:
            with psycopg.connect(store.ERP_DSN) as cx:
                for t in tables:
                    out[t] = cx.execute(sql.SQL("select count(*) from {}.{}").format(
                        sql.Identifier(schema), sql.Identifier(t))).fetchone()[0]
    except Exception:
        # A count is a nicety; failing to get one must not fail the run.
        return {}
    return out


def run_contract(contract: dict, as_of: date, windowed: bool = True) -> dict:
    """`datacontract test` for one contract, as a parsed results document."""
    server = DAILY_SERVER if (windowed and _server(contract, DAILY_SERVER)) else "erp"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "results.json"
        cmd = ["datacontract", "test", contract["_path"],
               "--server", server, "--output", str(out), "--output-format", "json"]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              cwd=str(ROOT), env={**os.environ,
                                                  "PYTHONIOENCODING": "utf-8"})
        if not out.exists():
            # No results file at all means the contract could not be read; the
            # stderr is the only thing that says why.
            raise SystemExit(
                f"datacontract produced no results for {contract['_path']}:\n"
                f"{proc.stdout[-2000:]}{proc.stderr[-2000:]}")
        return json.loads(out.read_text(encoding="utf-8"))


def merge_table_scoped(windowed: dict, unwindowed: dict) -> dict:
    """Replace the windowed result of every table-level invariant.

    Keyed by `check["key"]`, which datacontract keeps stable across runs and
    across servers -- the same check against the view and against the table
    carries the same key.
    """
    replacements = {c.get("key"): c for c in unwindowed.get("checks", [])
                    if c.get("type") in TABLE_SCOPED_TYPES}
    if not replacements:
        return windowed
    merged = dict(windowed)
    merged["checks"] = [replacements.get(c.get("key"), c)
                        for c in windowed.get("checks", [])]
    return merged


def persist(results: dict, contract: dict, as_of: date,
            window: str = "incremental",
            row_counts: dict[str, int] | None = None) -> list[dict]:
    """Store one run's checks and return the rows the score is computed from."""
    contract_id = contract.get("id") or contract.get("name")
    rows = []
    for check in results.get("checks", []):
        d = check.get("diagnostics") or {}
        failed = d.get("failed_rows")
        if failed is None:
            failed = d.get("value") if d.get("value") is not None else (
                0 if check.get("result") == "passed" else 1)
        # A custom rule has no row_count of its own; the table it is about is
        # the denominator, counted once per run in the same window.
        total = d.get("row_count") or (row_counts or {}).get(
            check.get("model") or "", 0) or _only_count(row_counts)
        rows.append({
            "check_id": f"{contract_id}.{check.get('key') or check['name']}",
            "dimension": check.get("dimension") or "unknown",
            "failed_rows": int(failed), "total_rows": int(total),
            "fail_ratio": round(int(failed) / total, 6) if total else 0.0,
            # A check that errored is an engineering problem, not a data one.
            # `general` is datacontract's run-level rollup rather than a check:
            # when the source cannot be reached it is the *only* thing returned,
            # with result "failed" and an ODBC error for a reason. Storing that
            # as a data failure is how an outage came to look like bad data.
            "status": ("error" if check.get("type") == "general" else
                       {"passed": "pass", "failed": "fail"}.get(
                           check.get("result"), "error")),
            "duration_ms": 0,
            # Kept so a result can be read on its own. `sql` in particular:
            # it is what ran, and core/sample.py rewrites it into the rows.
            "name": check.get("name") or "",
            "check_type": check.get("type") or "",
            "field": check.get("field"),
            "reason": check.get("reason"),
            "sql": check.get("implementation"),
        })

    with store.connect() as dq:
        store.init(dq)
        store.ensure_partition(dq, as_of)
        store.write_results(dq, as_of, contract_id, rows, window)
        s = score(rows)
        failed_n = sum(1 for r in rows if r["status"] == "fail")
        errored_n = sum(1 for r in rows if r["status"] == "error")
        store.write_score(dq, as_of, contract_id, s, len(rows), failed_n,
                          _min_score(contract), window, errored_n)
    return rows


def _only_count(row_counts: dict[str, int] | None) -> int:
    """Most contracts describe one table, and datacontract does not always say
    which model a custom rule belongs to. One table is unambiguous; more than
    one is left at zero rather than guessed at."""
    return list(row_counts.values())[0] if row_counts and len(row_counts) == 1 else 0


def push_to_odd(contract: dict, results: dict, url: str) -> int:
    """Send the run to ODD, attached to the table rather than the day's view.

    The checks execute over `asof`, but what they are about -- and what the
    Superset charts downstream point at -- is the table in `public`. Sending
    the view's ODDRN would put the tests on a catalog object nothing else
    refers to.
    """
    from integrations.odd.from_datacontract import (build, dataset_oddrn,
                                                    ensure_datasource, post)
    from integrations.odd.mapper import entity_list

    from integrations.odd.catalogue import ensure_terms, fill
    from integrations.odd.entity_page import sync_links

    ds = dataset_oddrn(contract, "erp")
    body = entity_list(build(contract, results, ds), HOST).model_dump(
        mode="json", exclude_none=True)
    ensure_datasource(url)
    post(url, body)
    # Everything a catalogue is for and a collector cannot know -- owner,
    # purpose, column meanings, the vocabulary -- plus the way back to the
    # pages ODD has no model for. Not worth failing the run over: the results
    # are already stored and already in ODD.
    try:
        sync_links(url, contract, ds)
        fill(url, contract, ensure_terms(url))
    except Exception as e:
        print(f"WARN {contract['id']}: catalogue not updated ({e})", flush=True)
    return len(body["items"])


def _has_table_scoped(contract: dict) -> bool:
    """Does anything in this contract declare a table-level invariant?"""
    for model in contract.get("schema", []):
        for prop in model.get("properties") or []:
            if prop.get("unique") or prop.get("primaryKey"):
                return True
    return False


def _min_score(contract: dict) -> float:
    """`slaProperties` is where ODCS puts a promise; ours is a floor on the score."""
    for prop in contract.get("slaProperties", []) or []:
        if prop.get("property") in ("minScore", "min_score"):
            return float(prop["value"])
    return float(os.getenv("DQ_MIN_SCORE", "0.95"))


def run(as_of: date, contracts: list[dict] | None = None,
        window: str = "incremental", odd_url: str | None = None) -> list[dict]:
    contracts = contracts if contracts is not None else load_contracts()
    out = []
    for c in contracts:
        # Both windows go through the views; only the predicate differs.
        #
        # A source that cannot be reached must not take the whole run down
        # with it: the window is built by connecting to the source, so before
        # this the SQL Server contract crashed `core/runner.py` on any machine
        # without a SQL Server -- CI included. Falling back to unwindowed lets
        # `datacontract test` fail on its own terms, which is how the run comes
        # to be recorded as errored rather than as a stack trace.
        try:
            windowed = bool(build_window(c, as_of, window=window))
        except Exception as e:
            print(f"WARN {as_of} {c.get('id')}: no window ({e})", flush=True)
            windowed = False
        results = run_contract(c, as_of, windowed=windowed)
        if windowed and _has_table_scoped(c):
            # A second pass against the real tables, for the checks a daily
            # window would make meaningless.
            results = merge_table_scoped(results, run_contract(c, as_of,
                                                               windowed=False))
        counts = table_rows(c, DAILY_SERVER if windowed else "erp")
        rows = persist(results, c, as_of, window, counts)
        s = score(rows)
        if odd_url:
            push_to_odd(c, results, odd_url)
        out.append({"contract": c.get("id"), "as_of": str(as_of), "score": s,
                    "failed": sum(1 for r in rows if r["status"] == "fail"),
                    "errored": sum(1 for r in rows if r["status"] == "error"),
                    "total": len(rows)})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of", default=str(date.today()))
    ap.add_argument("--backfill-days", type=int, default=0)
    ap.add_argument("--window", choices=["incremental", "cumulative"],
                    default="incremental")
    ap.add_argument("--contract", help="run only this contract id")
    ap.add_argument("--odd-url", help="also push each run to this ODD Platform")
    a = ap.parse_args()

    contracts = [c for c in load_contracts()
                 if a.contract in (None, c.get("id"))]
    if not contracts:
        raise SystemExit("no ODCS contracts found in contracts/")

    end = date.fromisoformat(a.as_of)
    for i in range(a.backfill_days, -1, -1):
        day = end - timedelta(days=i)
        for r in run(day, contracts, a.window, odd_url=a.odd_url):
            # An errored run is neither OK nor a quality failure: nothing was
            # measured, so the score says nothing and the flag should not
            # pretend otherwise.
            flag = ("ERR" if r["errored"] else
                    "OK" if r["failed"] == 0 else "FAIL")
            note = f" errored={r['errored']}" if r["errored"] else ""
            print(f"{flag:<4} {r['as_of']} {r['contract']:<20} "
                  f"score={r['score']:.4f} failed={r['failed']}/{r['total']}{note}")


if __name__ == "__main__":
    main()
