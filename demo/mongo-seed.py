"""Pull FX rates from a public API into MongoDB.

Not an arbitrary API: the ERP prices orders in TRY, USD and EUR, so a revenue
figure in a single currency depends on these rates. That makes the demo's
question a real one -- when the rate feed misses a day, which dashboards are
wrong? -- rather than two unrelated datasets in one catalog.

stdlib plus pymongo. Run it inside the app container:
    docker compose exec app python demo/mongo-seed.py
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import date, timedelta

from pymongo import MongoClient

API = "https://api.frankfurter.dev/v1"
MONGO_URI = os.getenv("MONGO_URI", "mongodb://root:rootpass@mongo:27017/?authSource=admin")
DAYS = int(os.getenv("FX_DAYS", "45"))


def fetch(start: date, end: date) -> dict:
    url = f"{API}/{start}..{end}?base=EUR&symbols=TRY,USD,GBP"
    # The default urllib agent is refused by the CDN in front of the API with a
    # bare 403. Every real API ingestion has one of these.
    req = urllib.request.Request(url, headers={
        "User-Agent": "lightweight-data-platform/0.1 (+demo fx loader)",
        "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main() -> None:
    end = date.today()
    start = end - timedelta(days=DAYS)
    payload = fetch(start, end)

    client = MongoClient(MONGO_URI)
    db = client["reference"]
    rates = db["fx_rates"]
    rates.drop()

    docs = []
    for day, quote in sorted(payload["rates"].items()):
        docs.append({
            "_id": day,
            "rate_date": day,
            "base": payload["base"],
            "source": "frankfurter.dev",
            # kept as a subdocument rather than flattened: the catalog should
            # show what the API actually returns
            "rates": {k: float(v) for k, v in quote.items()},
            "retrieved_at": date.today().isoformat(),
        })
    if docs:
        rates.insert_many(docs)

    # A second collection with a shape a relational catalog cannot describe:
    # one document per currency pair per day, which is how a consumer would
    # rather read it.
    flat = db["fx_rates_flat"]
    flat.drop()
    flat.insert_many([
        {"rate_date": d["rate_date"], "base": d["base"], "quote": cur, "rate": val}
        for d in docs for cur, val in d["rates"].items()
    ])

    covered = {d["rate_date"] for d in docs}
    wanted = {str(start + timedelta(days=i)) for i in range((end - start).days + 1)}
    missing = sorted(wanted - covered)

    print(f"fx_rates      {rates.count_documents({}):>6} documents")
    print(f"fx_rates_flat {flat.count_documents({}):>6} documents")
    print(f"range         {min(covered)} .. {max(covered)}")
    # Weekends have no fix; that is the API being honest, not a defect. Worth
    # printing because a freshness check has to know the difference.
    print(f"days with no fix (weekends/holidays): {len(missing)}")


if __name__ == "__main__":
    main()
