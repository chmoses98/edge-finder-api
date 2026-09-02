#!/usr/bin/env python3
"""MLB-ALPHA-0001 Sections E/F/I/K: C01-PIT prospective shadow collector.

RESEARCH ONLY. Places no orders, feeds no recommendation, staking or
risk-gate path. Append-only.

WHAT IT DOES, per invocation (intended cadence: every 10 minutes):
  1. TRIGGER PASS  (stream `c01pit_trigger_v1`)
     Fetch open KXMLBF5TOTAL markets, keep those inside [T-60, T-0), and
     create the FIRST qualifying official entry per contract. One entry per
     contract, ever -- enforced against the append-only log.
  2. OBSERVATIONAL PASS  (stream `c01pit_observational_v1`)
     Persist every other quote seen, flagged canTriggerC01Pit=false, for
     CLV, spread dynamics, depth and a genuinely LATER closing quote.
     These may never create or alter an entry.

Section K: because observational captures continue after the trigger fires
and up to first pitch, the closing quote is a real post-entry observation
rather than the entry itself -- the defect that made holdout CLV
identically zero.

Depth (Section I): the market-listing endpoint this repo uses exposes no
size/depth field at all. When `--orderbook` is passed the collector also
reads the documented public order-book endpoint per triggered ticker and
stores top-of-book sizes; otherwise every row is flagged
DEPTH_UNAVAILABLE. Read-only either way.

Storage (append-only, never rewritten):
  data/edgelab/research_artifacts/mlb_alpha_0001/shadow/entries.jsonl
  data/edgelab/research_artifacts/mlb_alpha_0001/shadow/observations.jsonl
"""

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0001")
SHADOW_DIR = os.path.join(ART, "shadow")
ENTRIES = os.path.join(SHADOW_DIR, "entries.jsonl")
OBSERVATIONS = os.path.join(SHADOW_DIR, "observations.jsonl")

from lib.edgelab.mlb_alpha_shadow import (  # noqa: E402
    evaluate_observation, TRIGGER_STREAM_ID, ELIGIBLE, SERIES_PREFIX)

OBSERVATIONAL_STREAM_ID = "c01pit_observational_v1"
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ["KXMLBF5TOTAL"]


def http_json(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "edge-finder-edgelab-research/1.0",
            "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        print("  fetch failed %s: %s" % (url, exc))
        return None


def already_entered():
    """Tickers with an official entry already recorded. The FIRST-quote rule
    means a contract can never receive a second entry."""
    seen = set()
    if os.path.exists(ENTRIES):
        with open(ENTRIES) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        seen.add(json.loads(line)["marketTicker"])
                    except Exception:
                        continue
    return seen


def append(path, record):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as fh:                       # append-only, never "w"
        fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")


def fetch_open_markets():
    out = []
    for series in SERIES:
        js = http_json("%s/markets?series_ticker=%s&status=open&limit=1000"
                       % (KALSHI_API, series))
        for m in ((js or {}).get("markets") or []):
            out.append(m)
    return out


def fetch_top_of_book(ticker):
    """Documented public order-book endpoint. Read-only; never places orders."""
    js = http_json("%s/markets/%s/orderbook?depth=1" % (KALSHI_API, ticker))
    book = (js or {}).get("orderbook") or {}
    def top(levels):
        if not levels:
            return None, None
        price, size = levels[0][0], levels[0][1]
        return price, size
    yes_px, yes_sz = top(book.get("yes"))
    no_px, no_sz = top(book.get("no"))
    return {"yesBidSize": yes_sz, "noBidSize": no_sz,
            "orderbookYesTopPrice": yes_px, "orderbookNoTopPrice": no_px}


def to_observation(raw, captured_at, run_id, with_depth=False):
    ticker = raw.get("ticker") or raw.get("market_ticker")
    obs = {
        "marketTicker": ticker,
        "eventTicker": raw.get("event_ticker"),
        "capturedAt": captured_at,
        "yesBid": raw.get("yes_bid"), "yesAsk": raw.get("yes_ask"),
        "noBid": raw.get("no_bid"), "noAsk": raw.get("no_ask"),
        "volume": raw.get("volume"), "openInterest": raw.get("open_interest"),
        "marketStatus": raw.get("status"),
        "threshold": _rung(ticker),
        "captureId": "%s:%s" % (run_id, ticker),
        "provenance": {"sourceSystem": "kalshi_public_markets",
                       "endpoint": "%s/markets" % KALSHI_API,
                       "runId": run_id, "capturedAt": captured_at},
    }
    if with_depth and ticker:
        obs.update(fetch_top_of_book(ticker))
    return obs


def _rung(ticker):
    if not ticker or "-" not in ticker:
        return None
    tail = ticker.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orderbook", action="store_true",
                    help="also read the public order-book endpoint for depth")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    now = datetime.utcnow()
    run_id = "C01PIT_SHADOW_%s" % now.strftime("%Y%m%dT%H%M%SZ")
    captured_at = now.replace(microsecond=0)

    raws = [m for m in fetch_open_markets()
            if (m.get("ticker") or m.get("market_ticker") or "").startswith(SERIES_PREFIX)]
    print("open KXMLBF5TOTAL markets fetched:", len(raws))

    entered = already_entered()
    n_entries = n_obs = 0
    for raw in raws:
        obs = to_observation(raw, captured_at, run_id, with_depth=args.orderbook)

        # 2. OBSERVATIONAL: always persisted, never able to trigger.
        obs_row = dict(obs, triggerStream=OBSERVATIONAL_STREAM_ID,
                       canTriggerC01Pit=False, shadowRole="OBSERVATIONAL")
        if not args.dry_run:
            append(OBSERVATIONS, obs_row)
        n_obs += 1

        # 1. TRIGGER: only for contracts with no official entry yet.
        if obs["marketTicker"] in entered:
            continue
        rec = evaluate_observation(obs, TRIGGER_STREAM_ID)
        if rec.get("eligibility") != ELIGIBLE:
            continue
        rec["runId"] = run_id
        if not args.dry_run:
            append(ENTRIES, rec)
        entered.add(obs["marketTicker"])
        n_entries += 1
        print("  ENTRY %s @ %sc (T-%.1f min)" % (
            rec["marketTicker"], rec["entryExecutableCents"], rec["minutesToStart"]))

    print("observational rows: %d | new official entries: %d%s"
          % (n_obs, n_entries, " (dry run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
