#!/usr/bin/env python3
"""MLB-ALPHA-0001 Section J: immutable capture of Kalshi's OWN settlement,
plus a read-only cross-check against this repo's canonical grade.

Write-once raw snapshots; a normalized projection; then a comparison that
ALERTS and QUARANTINES on disagreement and NEVER overwrites either source.

RESEARCH ONLY. Read-only with respect to canonical settlement and the
wager ledger.
"""

import argparse
import glob
import gzip
import json
import os
import sys
import urllib.request
from datetime import datetime

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
RAW_DIR = os.path.join(REPO, "data", "kalshi_settled_snapshots")
EXCHANGE_DIR = os.path.join(REPO, "data", "edgelab", "exchange_settlements")
REPORT_DIR = os.path.join(REPO, "data", "edgelab", "reports")

from lib.edgelab.exchange_settlement import (  # noqa: E402
    compare_settlements, summarize)

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ["KXMLBF5TOTAL", "KXMLBTOTAL", "KXMLBF5SPREAD", "KXMLBSPREAD",
          "KXMLBGAME", "KXMLBF5", "KXMLBTEAMTOTAL"]


def http_json(url, timeout=25):
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "edge-finder-edgelab-research/1.0",
            "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        print("  fetch failed %s: %s" % (url, exc))
        return None


def capture(date):
    """Write-once raw snapshots per series; returns {ticker: result}."""
    stamp = datetime.utcnow().strftime("%H%MZ")
    out_dir = os.path.join(RAW_DIR, date)
    os.makedirs(out_dir, exist_ok=True)
    results = {}
    for series in SERIES:
        js = http_json("%s/markets?series_ticker=%s&status=settled&limit=1000"
                       % (KALSHI_API, series))
        if js is None:
            continue
        path = os.path.join(out_dir, "settled_%s_%s.json" % (series, stamp))
        if os.path.exists(path):                    # never overwrite a snapshot
            path = path.replace(".json", "_%s.json" % datetime.utcnow().strftime("%S"))
        with open(path, "w") as fh:
            json.dump({"capturedAt": datetime.utcnow().isoformat() + "Z",
                       "endpoint": "%s/markets?series_ticker=%s&status=settled"
                                   % (KALSHI_API, series),
                       "seriesTicker": series, "body": js}, fh, indent=1)
        for m in (js.get("markets") or []):
            t = m.get("ticker") or m.get("market_ticker")
            if t:
                results[t] = m.get("result") or m.get("settlement_result")
        print("  %s -> %d settled markets (%s)" % (series, len(js.get("markets") or []), path))
    return results


def canonical_for(date):
    out = {}
    for p in glob.glob(os.path.join(REPO, "data", "edgelab", "settlements",
                                    "%s.jsonl*" % date)):
        opener = gzip.open if p.endswith(".gz") else open
        with opener(p, "rt") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if r.get("settlementStatus") == "SETTLED":
                    out[r["marketTicker"]] = r.get("result")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="game date YYYY-MM-DD")
    args = ap.parse_args()

    print("capturing exchange settlements for", args.date)
    exchange = capture(args.date)
    os.makedirs(EXCHANGE_DIR, exist_ok=True)
    norm = os.path.join(EXCHANGE_DIR, "%s.jsonl.gz" % args.date)
    with gzip.open(norm, "wt") as fh:
        for t, res in sorted(exchange.items()):
            fh.write(json.dumps({"marketTicker": t, "exchangeResult": res,
                                 "gameDate": args.date}, sort_keys=True) + "\n")
    print("normalized ->", norm)

    comparison = compare_settlements(canonical_for(args.date), exchange)
    summary = summarize(comparison)
    os.makedirs(REPORT_DIR, exist_ok=True)
    report = os.path.join(REPORT_DIR, "exchange_settlement_mismatches_%s.json" % args.date)
    with open(report, "w") as fh:
        json.dump({"gameDate": args.date, "summary": summary,
                   "quarantined": [comparison[t] for t in summary["quarantinedTickers"]],
                   "note": ("read-only: neither canonical settlement nor the "
                            "wager ledger is modified; quarantined rows do not "
                            "count toward research checkpoints")},
                  fh, indent=2, sort_keys=True)
    print("compared %d tickers | agreement %.4f | quarantined %d -> %s"
          % (summary["tickersCompared"], summary["agreementRate"],
             summary["quarantinedCount"], report))
    return 1 if summary["quarantinedCount"] else 0


if __name__ == "__main__":
    sys.exit(main())
