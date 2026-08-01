#!/usr/bin/env python3
"""
scripts/edgelab/collect_clv.py
==================================
CLI entry point: project the day's MarketObservation quotes into the
CLV-focused ClvQuote store, select each market's closing quote, and
compute CLV for every pending placed bet whose market now has a closing
quote.

Makes NO Kalshi API calls -- projects from data/edgelab/observations/
<date>.jsonl, which ingest_market_observations.py already populated from
existing snapshot files.

Usage:
    python3 scripts/edgelab/collect_clv.py [--date YYYY-MM-DD]
"""
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage
from lib.edgelab.clv import compute_clv_for_bet, finalize_closing_quotes, project_observations_to_clv_quotes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    date = args.date or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    run_id = ids.new_run_id("CLV_COLLECTION", github_run_id=os.environ.get("GITHUB_RUN_ID"))
    started_at = ids.utc_now_iso()

    observations = list(storage.read_records(storage.partition_path("observations", date, compressed=True)))
    all_bets = list(storage.read_records(storage.singleton_path("bets", "bets.jsonl")))
    bets_by_ticker = {}
    for bet in all_bets:
        if bet.get("marketTicker"):
            bets_by_ticker.setdefault(bet["marketTicker"], []).append(bet)

    placed_bet_tickers = {ticker: bets[0]["betId"] for ticker, bets in bets_by_ticker.items()}
    scheduled_start_by_ticker = {
        obs["marketTicker"]: obs.get("scheduledStart")
        for obs in observations if obs.get("scheduledStart")
    }

    quotes = project_observations_to_clv_quotes(observations, placed_bet_tickers, run_id)

    by_ticker = {}
    for q in quotes:
        by_ticker.setdefault(q["marketTicker"], []).append(q)

    finalized_quotes = []
    tickers_with_closing = set()
    for ticker, ticker_quotes in by_ticker.items():
        finalized = finalize_closing_quotes(ticker_quotes, scheduled_start=scheduled_start_by_ticker.get(ticker))
        finalized_quotes.extend(finalized)
        if any(q["isClosingQuote"] for q in finalized):
            tickers_with_closing.add(ticker)

    quotes_path = storage.partition_path("clv_quotes", date)
    q_updated, q_inserted = storage.upsert_records(quotes_path, finalized_quotes, "clvQuoteId")

    bet_updates = []
    clv_computed = 0
    clv_unavailable = 0
    for ticker in tickers_with_closing:
        ticker_quotes = [q for q in finalized_quotes if q["marketTicker"] == ticker]
        for bet in bets_by_ticker.get(ticker, []):
            if bet.get("status") != "pending" and bet.get("clv") is not None:
                continue
            result = compute_clv_for_bet(bet, ticker_quotes)
            updated_bet = dict(bet)
            if result.get("clvStatus") == "VALID":
                updated_bet["clv"] = result["clvCents"]
                updated_bet["closingPrice"] = result["closingImpliedProbability"]
                updated_bet["clvQuoteId"] = result["clvQuoteId"]
                updated_bet["updatedAt"] = ids.utc_now_iso()
                clv_computed += 1
            else:
                clv_unavailable += 1
            bet_updates.append(updated_bet)

    bets_path = storage.singleton_path("bets", "bets.jsonl")
    if bet_updates:
        storage.upsert_records(bets_path, bet_updates, "betId")

    run_record = {
        "schemaVersion": "1",
        "runId": run_id,
        "runType": "CLV_COLLECTION",
        "startedAt": started_at,
        "completedAt": ids.utc_now_iso(),
        "status": "success",
        "sourceWorkflow": os.environ.get("GITHUB_WORKFLOW"),
        "githubRunId": os.environ.get("GITHUB_RUN_ID"),
        "inputFiles": [storage.partition_path("observations", date, compressed=True), bets_path],
        "outputFiles": [quotes_path, bets_path],
        "counts": {
            "quotesConsidered": len(quotes),
            "quotesInserted": q_inserted,
            "quotesUpdated": q_updated,
            "tickersWithClosingQuote": len(tickers_with_closing),
            "betClvComputed": clv_computed,
            "betClvUnavailable": clv_unavailable,
        },
        "errors": [],
        "warnings": [],
        "createdAt": started_at,
        "provenance": {
            "sourceSystem": "edgelab_cli",
            "sourceFile": __file__,
            "sourceKey": date,
            "capturedAt": started_at,
            "ingestedAt": started_at,
        },
    }
    storage.append_records(storage.partition_path("research_runs", date), [run_record], "runId")

    print(
        f"[collect_clv] date={date} quotes={len(quotes)} closing_tickers={len(tickers_with_closing)} "
        f"clv_computed={clv_computed} clv_unavailable={clv_unavailable}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
