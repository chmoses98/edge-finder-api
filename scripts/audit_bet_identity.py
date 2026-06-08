#!/usr/bin/env python3
"""
PHASE 1 — BET IDENTITY AUDIT
Audits all bets in bets.json for CLV readiness.

Identity status values:
  READY_FOR_CLV       — has marketTicker + betTimestamp + scheduledStartTime
  MISSING_MARKET_TICKER — no marketTicker stored
  MISSING_SERIES_TICKER — has marketTicker but no seriesTicker
  MISSING_TIMESTAMP   — has ticker but no betTimestamp
  AMBIGUOUS_MARKET    — market field maps to multiple Kalshi types
  INVALID_MATCH       — game/market fields insufficient for any matching
"""
import json, sys, os
from datetime import datetime, timezone

BETS_PATH = os.path.join(os.path.dirname(__file__), "..", "bets.json")

MARKET_MAP = {
    "ML": "ML", "MONEYLINE": "ML",
    "F5 ML": "F5 ML", "F5": "F5 ML", "F5ML": "F5 ML",
    "RL": "Run Line", "Run Line": "Run Line", "RUN LINE": "Run Line", "RUNLINE": "Run Line",
    "F5 RL": "F5 RL",
    "Total": "Total", "TOTAL": "Total", "Game Total": "Total", "GAME TOTAL": "Total",
    "TT": "Team Total", "Team Total": "Team Total", "TEAM TOTAL": "Team Total",
    "NRFI": "NRFI",
    "YRFI": "YRFI",
    "K Prop": "K Prop", "K PROP": "K Prop",
    "Pitcher Prop": "Pitcher Prop",
}

CLV_SUPPORTED_MARKETS = {"ML", "F5 ML", "Run Line", "F5 RL", "Total", "Team Total", "NRFI", "YRFI"}


def classify_bet(b):
    market_raw = (b.get("market") or "").strip()
    market = MARKET_MAP.get(market_raw, market_raw)
    game = b.get("game", "")
    bet_side = b.get("bet") or b.get("betSide") or ""
    entry_price = b.get("betTimeLine") or b.get("price")
    market_ticker = b.get("marketTicker")
    series_ticker = b.get("seriesTicker")
    event_ticker = b.get("eventTicker")
    bet_timestamp = b.get("loggedAt") or b.get("betTimestamp")
    scheduled_start = b.get("scheduledStartTime")

    if not game or not market_raw:
        status = "INVALID_MATCH"
    elif market_ticker and series_ticker and bet_timestamp and scheduled_start:
        status = "READY_FOR_CLV"
    elif market_ticker and bet_timestamp and scheduled_start:
        status = "MISSING_SERIES_TICKER"
    elif market_ticker and not bet_timestamp:
        status = "MISSING_TIMESTAMP"
    elif not market_ticker:
        status = "MISSING_MARKET_TICKER"
    else:
        status = "AMBIGUOUS_MARKET"

    return {
        "id": b.get("id"),
        "date": b.get("date"),
        "game": game,
        "market": market,
        "side": bet_side,
        "line": b.get("line"),
        "entryPrice": entry_price,
        "betTimestamp": bet_timestamp,
        "scheduledStartTime": scheduled_start,
        "marketTicker": market_ticker,
        "seriesTicker": series_ticker,
        "eventTicker": event_ticker,
        "clvStatus": b.get("clvStatus"),
        "clvSource": b.get("clvSource"),
        "identityStatus": status,
        "clvSupported": market in CLV_SUPPORTED_MARKETS,
    }


def run_audit(bets_path=None):
    path = bets_path or BETS_PATH
    with open(path) as f:
        bets = json.load(f)

    results = [classify_bet(b) for b in bets]

    # Tally
    tally = {
        "READY_FOR_CLV": 0,
        "MISSING_MARKET_TICKER": 0,
        "MISSING_SERIES_TICKER": 0,
        "MISSING_TIMESTAMP": 0,
        "AMBIGUOUS_MARKET": 0,
        "INVALID_MATCH": 0,
    }
    for r in results:
        tally[r["identityStatus"]] = tally.get(r["identityStatus"], 0) + 1

    clv_supported = sum(1 for r in results if r["clvSupported"])
    needs_backfill = tally["MISSING_MARKET_TICKER"] + tally["MISSING_SERIES_TICKER"] + tally["MISSING_TIMESTAMP"]

    summary = {
        "total_bets": len(bets),
        "ready_for_clv": tally["READY_FOR_CLV"],
        "missing_identifiers": len(bets) - tally["READY_FOR_CLV"],
        "requiring_backfill": needs_backfill,
        "invalid_or_ambiguous": tally["INVALID_MATCH"] + tally["AMBIGUOUS_MARKET"],
        "clv_supported_markets": clv_supported,
        "breakdown": tally,
    }

    return results, summary


def main():
    results, summary = run_audit()

    print("=" * 60)
    print("PHASE 1 — BET IDENTITY AUDIT")
    print("=" * 60)
    print(f"Total bets:              {summary['total_bets']}")
    print(f"Ready for CLV:           {summary['ready_for_clv']}")
    print(f"Missing identifiers:     {summary['missing_identifiers']}")
    print(f"Requiring backfill:      {summary['requiring_backfill']}")
    print(f"Invalid/ambiguous:       {summary['invalid_or_ambiguous']}")
    print(f"CLV-supported markets:   {summary['clv_supported_markets']}")
    print()
    print("Breakdown by status:")
    for k, v in summary["breakdown"].items():
        print(f"  {k}: {v}")

    # Show sample problematic bets
    missing = [r for r in results if r["identityStatus"] == "MISSING_MARKET_TICKER"]
    print(f"\nSample MISSING_MARKET_TICKER bets (first 5 of {len(missing)}):")
    for r in missing[:5]:
        print(f"  {r['id']} | {r['game']} | {r['market']} | {r['side']}")

    # Write audit report
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "summary": summary, "bets": results}
    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "identity_audit.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nAudit written to: {out_path}")

    return results, summary


if __name__ == "__main__":
    main()
