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
        # A CANCELLED bet (logged in error -- lib.edgelab.bets.cancel_placed_bet)
        # never gets CLV computed: it isn't a real wager, and its ticker
        # must not be prioritized in the quote-capture set on its behalf
        # (maintainer review finding).
        if (bet.get("recordStatus") or "ACTIVE") == "CANCELLED":
            continue
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

    # Maintainer review hardening (item 7, PR #37 review): re-verified
    # against real 2026-08-01/08-02 data that the 620/4844 (08-02) and
    # 462/4135 (08-01) "tickers with more than one ClvQuote row" are
    # already correctly resolved to exactly one isClosingQuote=True row
    # each under the current, real production capture cadence -- 0
    # ambiguous in both dates. That confirmed statistic was never about
    # isClosingQuote itself being double-set; it was the OLD replay-side
    # "last row in file order" logic (fixed separately, see
    # lib.edgelab.replay._closing_clv_by_ticker) ignoring the flag
    # entirely.
    #
    # This block closes a DIFFERENT, theoretical gap found while tracing
    # that logic, not yet observed in real committed data: checkpoint
    # classification uses is_first_of_day=(i==0) relative to THIS call's
    # own obs_list. Under real chronological capture that index is stable
    # run-to-run (a later run can only ever APPEND newer observations, so
    # index 0 never changes) -- but a backfill/reprocessing run that
    # ingests an out-of-order or previously-missed EARLIER observation for
    # a ticker (see check_snapshot_capture.py's own recovery-after-gap
    # scenarios elsewhere in this repo for why that is a real, supported
    # case, not a hypothetical one) could still shift index 0, reclassify
    # a previously-FIRST_DAILY row to a non-standard checkpoint, and drop
    # it from that run's freshly projected ticker_quotes (see the `if not
    # bet_id and checkpoint not in _STANDARD_CHECKPOINTS: continue` filter
    # above) -- orphaning its isClosingQuote flag if it had been set,
    # since finalize_closing_quotes() would never see it again to correct
    # it. Always re-running finalize_closing_quotes() over the FULL known
    # history for a ticker (existing stored rows unioned with this run's
    # freshly projected ones) closes that gap defensively, at negligible
    # cost, without depending on real capture ordering ever staying
    # perfectly monotonic.
    quotes_path = storage.resolve_partition_path("clv_quotes", date)
    existing_by_ticker = {}
    for row in storage.read_records(quotes_path):
        ticker = row.get("marketTicker")
        if ticker:
            existing_by_ticker.setdefault(ticker, {})[row["clvQuoteId"]] = row

    finalized_quotes = []
    tickers_with_closing = set()
    for ticker, ticker_quotes in by_ticker.items():
        merged_by_id = dict(existing_by_ticker.get(ticker, {}))
        for q in ticker_quotes:
            merged_by_id[q["clvQuoteId"]] = q  # freshly projected data wins on overlap
        full_history = sorted(merged_by_id.values(), key=lambda q: q["capturedAt"])
        finalized = finalize_closing_quotes(full_history, scheduled_start=scheduled_start_by_ticker.get(ticker))
        finalized_quotes.extend(finalized)
        if any(q["isClosingQuote"] for q in finalized):
            tickers_with_closing.add(ticker)

    q_updated, q_inserted = storage.upsert_records(quotes_path, finalized_quotes, "clvQuoteId")

    bet_updates = []
    clv_computed = 0
    clv_unavailable = 0
    clv_unavailable_by_reason = {}
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
                reason = result.get("unavailableReason", "UNKNOWN")
                clv_unavailable_by_reason[reason] = clv_unavailable_by_reason.get(reason, 0) + 1
            bet_updates.append(updated_bet)

    # CLV Coverage Reliability mission (catch-up pass): this run's own
    # `date` scoping above only ever matches a bet against tickers seen in
    # THIS run's observations/clv_quotes partition -- so a bet imported or
    # logged AFTER its own market's day (a historical backfill via
    # import-postmortem.yml/import-manual-bets.yml, confirmed as the real,
    # measured cause of most of this repository's decided-but-UNKNOWN-CLV
    # bets: their own gameDate's clv_quotes partition already has a
    # correctly finalized isClosingQuote row -- select_closing_quote /
    # finalize_closing_quotes ran normally on that real date -- but no run
    # of this script was ever invoked again for that historical `--date`
    # after the bet appeared) permanently never gets matched, even though
    # nothing about its data is actually missing. This pass looks up
    # EXACTLY each such bet's own recorded gameDate's already-finalized
    # closing-quote data (produced by the exact same, unmodified
    # select_closing_quote safeguards) and nothing else -- it never widens
    # what counts as a valid closing quote, never re-derives one, and
    # never touches a bet whose own gameDate partition has no finalized
    # quote (that bet correctly stays UNKNOWN).
    attempted_bet_ids = {b["betId"] for b in bet_updates}
    leftover_bets_by_gamedate = {}
    for bet in all_bets:
        if (bet.get("recordStatus") or "ACTIVE") == "CANCELLED":
            continue
        if bet.get("betId") in attempted_bet_ids or bet.get("clv") is not None:
            continue
        game_date = bet.get("gameDate")
        if not game_date or game_date == date or not bet.get("marketTicker"):
            continue
        leftover_bets_by_gamedate.setdefault(game_date, []).append(bet)

    clv_computed_catchup = 0
    for game_date, gd_bets in leftover_bets_by_gamedate.items():
        gd_by_ticker = {}
        for row in storage.read_partition("clv_quotes", game_date):
            ticker = row.get("marketTicker")
            if ticker:
                gd_by_ticker.setdefault(ticker, []).append(row)
        for bet in gd_bets:
            ticker_quotes = gd_by_ticker.get(bet["marketTicker"])
            if not ticker_quotes:
                continue  # nothing archived for this bet's own date -- stays UNKNOWN, never fabricated
            result = compute_clv_for_bet(bet, ticker_quotes)
            if result.get("clvStatus") != "VALID":
                clv_unavailable += 1
                reason = result.get("unavailableReason", "UNKNOWN")
                clv_unavailable_by_reason[reason] = clv_unavailable_by_reason.get(reason, 0) + 1
                continue
            updated_bet = dict(bet)
            updated_bet["clv"] = result["clvCents"]
            updated_bet["closingPrice"] = result["closingImpliedProbability"]
            updated_bet["clvQuoteId"] = result["clvQuoteId"]
            updated_bet["updatedAt"] = ids.utc_now_iso()
            bet_updates.append(updated_bet)
            clv_computed += 1
            clv_computed_catchup += 1

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
            "betClvComputedViaCatchup": clv_computed_catchup,
            "betClvUnavailable": clv_unavailable,
            "betClvUnavailableByReason": clv_unavailable_by_reason,
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
        f"clv_computed={clv_computed} (catchup={clv_computed_catchup}) clv_unavailable={clv_unavailable} "
        f"unavailable_by_reason={clv_unavailable_by_reason}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
