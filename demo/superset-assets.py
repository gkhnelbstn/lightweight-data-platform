"""Create the Superset databases, datasets, charts and dashboards the demo needs.

They used to exist only on whichever machine someone had clicked them into,
which made the last hop of the lineage chain -- the one that answers "which
dashboards break" -- the one part of the demo nobody else could reproduce.

Idempotent by name: anything already there is left alone, so this can be run
after every `docker compose up`.

    docker compose exec app python demo/superset-assets.py
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE = os.getenv("SUPERSET_URL", "http://superset:8088")
USER = os.getenv("SUPERSET_USER", "admin")
PASSWORD = os.getenv("SUPERSET_PASSWORD", "admin")

MSSQL_URI = os.getenv(
    "SUPERSET_MSSQL_URI",
    "mssql+pymssql://sa:Str0ng!Passw0rd@mssql:1433/erp")
DWH_URI = os.getenv(
    "SUPERSET_DWH_URI",
    "postgresql+psycopg2://postgres:postgres@db:5432/dwh")

DATABASES = [("ERP (MSSQL)", MSSQL_URI), ("Warehouse (Postgres)", DWH_URI)]

# (database, schema, table)
DATASETS = [
    ("ERP (MSSQL)", "dbo", "sales_orders"),
    ("ERP (MSSQL)", "dbo", "customers"),
    ("ERP (MSSQL)", "dbo", "sales_order_lines"),
    # The end of the medallion chain, and the reason this file exists: a
    # failing check on the ERP has to be followable all the way to here.
    ("Warehouse (Postgres)", "mart", "revenue_daily"),
]

CHARTS = [
    ("Gunluk Ciro (net_amount)", "sales_orders", "echarts_timeseries_line",
     {"x_axis": "order_date", "metrics": [{"expressionType": "SIMPLE",
      "column": {"column_name": "net_amount"}, "aggregate": "SUM",
      "label": "SUM(net_amount)"}], "groupby": []}),
    ("Para Birimine Gore Ciro", "sales_orders", "pie",
     {"groupby": ["currency"], "metric": {"expressionType": "SIMPLE",
      "column": {"column_name": "net_amount"}, "aggregate": "SUM",
      "label": "SUM(net_amount)"}}),
    ("Musteri Segmentleri", "customers", "pie",
     {"groupby": ["segment"], "metric": {"expressionType": "SIMPLE",
      "column": {"column_name": "customer_id"}, "aggregate": "COUNT",
      "label": "COUNT(customer_id)"}}),
    ("Gunluk Ciro (mart)", "revenue_daily", "echarts_timeseries_line",
     {"x_axis": "order_date", "metrics": [{"expressionType": "SIMPLE",
      "column": {"column_name": "revenue"}, "aggregate": "SUM",
      "label": "SUM(revenue)"}], "groupby": ["country"]}),
]

DASHBOARDS = {
    "Satis Genel Bakis": ["Gunluk Ciro (net_amount)", "Para Birimine Gore Ciro",
                          "Musteri Segmentleri"],
    "Ciro (ambardan)": ["Gunluk Ciro (mart)"],
}


class Superset:
    def __init__(self) -> None:
        self.token = self._json("POST", "/api/v1/security/login", {
            "username": USER, "password": PASSWORD,
            "provider": "db", "refresh": True})["access_token"]
        # Superset's API rejects a POST without the CSRF token even when the
        # bearer is valid, and the token is bound to the session cookie -- so
        # both have to travel together.
        self.cookie = ""
        self.csrf = self._csrf()

    def _csrf(self) -> str:
        req = urllib.request.Request(
            f"{BASE}/api/v1/security/csrf_token/",
            headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            self.cookie = "; ".join(
                c.split(";")[0] for c in r.headers.get_all("Set-Cookie") or [])
            return json.loads(r.read())["result"]

    def _json(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if getattr(self, "token", None):
            headers["Authorization"] = f"Bearer {self.token}"
        if getattr(self, "csrf", None):
            headers["X-CSRFToken"] = self.csrf
            headers["Referer"] = BASE
        if getattr(self, "cookie", ""):
            headers["Cookie"] = self.cookie
        req = urllib.request.Request(
            BASE + path, method=method, headers=headers,
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            # Superset says *why* in the body and only the status code in the
            # exception, which is the difference between a fix and a guess.
            raise RuntimeError(
                f"{method} {path} -> {e.code}: {e.read().decode()[:400]}") from None

    def list(self, kind: str, key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        page = 0
        while True:
            r = self._json("GET", f"/api/v1/{kind}/?q=(page:{page},page_size:100)")
            for item in r.get("result", []):
                out[item[key]] = item["id"]
            if len(out) >= r.get("count", 0) or not r.get("result"):
                return out
            page += 1

    def create(self, kind: str, body: dict) -> int:
        return self._json("POST", f"/api/v1/{kind}/", body)["id"]


def main() -> None:
    ss = Superset()
    made: list[str] = []

    databases = ss.list("database", "database_name")
    for name, uri in DATABASES:
        if name not in databases:
            databases[name] = ss.create("database", {
                "database_name": name, "sqlalchemy_uri": uri,
                "expose_in_sqllab": True})
            made.append(f"database {name}")

    datasets = ss.list("dataset", "table_name")
    for database, schema, table in DATASETS:
        if table not in datasets:
            datasets[table] = ss.create("dataset", {
                "database": databases[database], "schema": schema,
                "table_name": table})
            made.append(f"dataset {schema}.{table}")

    charts = ss.list("chart", "slice_name")
    for title, table, viz, params in CHARTS:
        if title in charts or table not in datasets:
            continue
        charts[title] = ss.create("chart", {
            "slice_name": title, "viz_type": viz,
            "datasource_id": datasets[table], "datasource_type": "table",
            "params": json.dumps({"viz_type": viz, **params})})
        made.append(f"chart {title}")

    dashboards = ss.list("dashboard", "dashboard_title")
    for title, slices in DASHBOARDS.items():
        if title not in dashboards:
            dashboards[title] = ss.create("dashboard", {"dashboard_title": title})
            made.append(f"dashboard {title}")
        for slice_title in slices:
            if slice_title in charts:
                # A chart joins a dashboard from the chart's side.
                try:
                    ss._json("PUT", f"/api/v1/chart/{charts[slice_title]}",
                             {"dashboards": [dashboards[title]]})
                except urllib.error.HTTPError as e:
                    print(f"  ! {slice_title} -> {title}: {e.code}")

    print("\n".join(f"  created {m}" for m in made) if made
          else "  everything already present")


if __name__ == "__main__":
    main()
