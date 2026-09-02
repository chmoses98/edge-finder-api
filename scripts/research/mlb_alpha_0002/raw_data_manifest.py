#!/usr/bin/env python3
"""MLB-ALPHA-0002: build the RAW-DATA MANIFEST for the recovered Kalshi
exchange history.

The recovered candles/trade tape is ~500 MB and must NOT enter ordinary
Git history (the repository is already ~1.1 GB). Git keeps this manifest
-- everything needed to identify, re-fetch, verify and reproduce the raw
payload -- while the payload itself lives as immutable GitHub Release
assets.

The manifest records, per file: SHA256, byte size, row count, gameDate,
and the exact API query that produced it, plus the archive-level SHA256s,
the endpoint versions, the ticker universe and the schema. A hydration
run is correct iff every SHA256 matches.

RESEARCH ONLY. Read-only over the artifacts.
"""

import argparse
import gzip
import hashlib
import json
import os
import sys
from collections import Counter

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002")
HIST = os.path.join(ART, "kalshi_history")
OUT = os.path.join(ART, "raw_data_manifest.json")

RELEASE_TAG = "MLB-ALPHA-0002-KALSHI-RAW-V1"
ASSETS = ("mlb-alpha-0002-kalshi-candles-v1.tar",
          "mlb-alpha-0002-kalshi-trades-v1.tar",
          "recovery_manifest.json.gz")


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def count_rows(path):
    n = 0
    with gzip.open(path, "rt") as fh:
        for line in fh:
            if line.strip():
                n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive-dir", default="", help="directory holding the built .tar assets")
    args = ap.parse_args()

    files, totals = [], Counter()
    for sub in ("candles", "trades"):
        d = os.path.join(HIST, sub)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".jsonl.gz"):
                continue
            p = os.path.join(d, name)
            rows = count_rows(p)
            files.append({"kind": sub, "path": "kalshi_history/%s/%s" % (sub, name),
                          "gameDate": name[:10], "bytes": os.path.getsize(p),
                          "tickerRecords": rows, "sha256": sha256_file(p)})
            totals[sub + "_files"] += 1
            totals[sub + "_bytes"] += os.path.getsize(p)
            totals[sub + "_tickerRecords"] += rows

    man_path = os.path.join(HIST, "recovery_manifest.json")
    tickers = {}
    if os.path.exists(man_path):
        with open(man_path) as fh:
            tickers = json.load(fh).get("tickers", {})
    fam = Counter(v.get("family") for v in tickers.values())
    dates = sorted({v.get("gameDate") for v in tickers.values() if v.get("gameDate")})

    archives = []
    if args.archive_dir:
        for a in ASSETS:
            p = os.path.join(args.archive_dir, a)
            if os.path.exists(p):
                archives.append({"asset": a, "bytes": os.path.getsize(p), "sha256": sha256_file(p)})

    doc = {
        "programId": "MLB-ALPHA-0002",
        "datasetId": RELEASE_TAG,
        "purpose": ("Immutable raw record of Kalshi's own exchange history for the settled "
                    "August 2026 MLB universe: 1-minute candlesticks and the public trade tape. "
                    "Kept OUT of ordinary Git history; published as GitHub Release assets."),
        "source": {
            "api": "https://api.elections.kalshi.com/trade-api/v2",
            "authentication": "none (public GET endpoints only)",
            "endpoints": {
                "candles": "GET /series/{series}/markets/{ticker}/candlesticks"
                           "?start_ts={start}&end_ts={end}&period_interval=1",
                "trades": "GET /markets/trades?ticker={ticker}&min_ts={start}&max_ts={end}"
                          "&limit=1000[&cursor={cursor}]",
            },
            "queryWindow": {"startOffsetHoursBeforeScheduledStart": 72,
                            "endOffsetHoursAfterScheduledStart": 8,
                            "periodIntervalMinutes": 1},
            "recoveredBy": "scripts/research/mlb_alpha_0002/recover_kalshi_history.py",
            "recoveredOnDates": "2026-09-02 (four resumable rounds via research-sharp-market-probe.yml)",
        },
        "tickerUniverse": {"contracts": len(tickers), "byFamily": dict(fam),
                           "gameDates": dates, "dateCount": len(dates),
                           "selection": ("every SETTLED contract in data/edgelab/settlements whose "
                                         "event ticker resolves via lib.edgelab.mlb_alpha_identity, "
                                         "restricted to the core liquid families")},
        "schema": {
            "candlesFile": {"format": "gzipped JSONL, one line per contract",
                            "line": ["ticker", "family", "gameDate", "startTs", "endTs",
                                     "fetchedAt", "candlesticks[]"],
                            "candlestick": ["end_period_ts", "yes_bid{open,high,low,close}_dollars",
                                            "yes_ask{...}_dollars", "price{...}", "volume_fp",
                                            "open_interest_fp"]},
            "tradesFile": {"format": "gzipped JSONL, one line per contract",
                           "line": ["ticker", "family", "gameDate", "startTs", "endTs",
                                    "fetchedAt", "trades[]"],
                           "trade": ["trade_id", "ticker", "created_time", "count_fp",
                                     "yes_price_dollars", "no_price_dollars", "taker_side",
                                     "taker_outcome_side", "taker_book_side", "is_block_trade"]},
            "preservation": "raw API JSON preserved verbatim; never re-shaped",
        },
        "totals": {"candleFiles": totals["candles_files"], "candleBytes": totals["candles_bytes"],
                   "tradeFiles": totals["trades_files"], "tradeBytes": totals["trades_bytes"],
                   "rawBytes": totals["candles_bytes"] + totals["trades_bytes"]},
        "release": {"tag": RELEASE_TAG, "assets": ASSETS,
                    "publishedBy": ".github/workflows/research-publish-raw-dataset.yml",
                    "status": "PENDING_PUBLICATION" if not archives else "ARCHIVES_BUILT",
                    "note": ("Release creation needs a workflow that exposes GITHUB_TOKEN; no such "
                             "workflow exists on the default branch yet, so publication happens on "
                             "the first dispatch after the activation PR merges.")},
        "archives": archives,
        "hydration": {"script": "scripts/research/mlb_alpha_0002/hydrate_raw_dataset.py",
                      "verification": "every per-file SHA256 in files[] must match after extraction"},
        "files": files,
    }
    with open(OUT, "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("wrote %s" % OUT)
    print("  contracts=%d dates=%d candleFiles=%d tradeFiles=%d rawBytes=%d (%.1f MB)" % (
        len(tickers), len(dates), totals["candles_files"], totals["trades_files"],
        doc["totals"]["rawBytes"], doc["totals"]["rawBytes"] / 1048576.0))
    for a in archives:
        print("  asset %-44s %12d  %s" % (a["asset"], a["bytes"], a["sha256"][:16]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
