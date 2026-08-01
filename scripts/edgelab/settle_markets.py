#!/usr/bin/env python3
"""
scripts/edgelab/settle_markets.py
=====================================
CLI entry point: settle every market EdgeLab observed for a date,
including markets nobody bet, and back-fill WIN/LOSS/realized-return onto
matching placed bets.

Game outcomes are fetched via clv_update.py's existing
fetch_mlb_linescore() (the same MLB Stats API linescore call production
already uses for F5 settlement) -- no new external integration. A game
whose gameId isn't a real MLB gamePk (the "date_away_home" fallback
identity -- see lib/edgelab/ids.py), or whose linescore fetch fails, is
recorded SETTLEMENT_UNRESOLVED with an explicit reason; nothing here
guesses a score.

Usage:
    python3 scripts/edgelab/settle_markets.py [--date YYYY-MM-DD]
"""
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage
from lib.edgelab.settlement import (
    build_settlement_record,
    derive_bet_result,
    hypothetical_yes_return,
    realized_return_for_bet,
    settle_market,
)
from lib.research.inning_result_settlement import extract_period_score_from_linescore

try:
    from clv_update import fetch_mlb_linescore
except ImportError:
    fetch_mlb_linescore = None


def build_game_outcome_from_linescore(linescore, game_status):
    if not linescore:
        return None
    teams = linescore.get("teams") or {}
    away_runs = (teams.get("away") or {}).get("runs")
    home_runs = (teams.get("home") or {}).get("runs")
    completed_innings = len([i for i in (linescore.get("innings") or []) if i.get("away", {}).get("runs") is not None or i.get("home", {}).get("runs") is not None])

    period_scores = {}
    for horizon, through in (("F3", 3), ("F5", 5), ("F7", 7)):
        a, h = extract_period_score_from_linescore(linescore, through)
        if a is not None:
            period_scores[horizon] = (a, h)

    first_inning = None
    fa, fh = extract_period_score_from_linescore(linescore, 1)
    if fa is not None:
        first_inning = (fa, fh)

    return {
        "awayRuns": away_runs,
        "homeRuns": home_runs,
        "completedInnings": completed_innings,
        "gameStatus": game_status,
        "periodScores": period_scores,
        "firstInningRuns": first_inning,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    date = args.date or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    run_id = ids.new_run_id("SETTLEMENT", github_run_id=os.environ.get("GITHUB_RUN_ID"))
    started_at = ids.utc_now_iso()

    markets = list(storage.read_records(storage.partition_path("markets", date)))
    games = {g["gameId"]: g for g in storage.read_records(storage.partition_path("games", date))}
    bets = list(storage.read_records(storage.singleton_path("bets", "bets.jsonl")))
    bets_by_ticker = {}
    for bet in bets:
        if bet.get("marketTicker"):
            bets_by_ticker.setdefault(bet["marketTicker"], []).append(bet)

    clv_quotes_by_ticker = {}
    for q in storage.read_records(storage.partition_path("clv_quotes", date)):
        clv_quotes_by_ticker.setdefault(q["marketTicker"], []).append(q)

    outcome_cache = {}
    warnings = []
    settlement_records = []
    bet_updates = []

    for market in markets:
        game_id = market.get("gameId")
        game = games.get(game_id) if game_id else None

        if game_id not in outcome_cache:
            outcome = None
            if game and game.get("mlbGamePk") and fetch_mlb_linescore:
                try:
                    linescore = fetch_mlb_linescore(game["mlbGamePk"])
                    outcome = build_game_outcome_from_linescore(linescore, game.get("status"))
                    if outcome is None:
                        warnings.append(f"linescore unavailable for gameId={game_id} (gamePk={game['mlbGamePk']})")
                except Exception as exc:  # network/API failures must never crash the run
                    warnings.append(f"linescore fetch failed for gameId={game_id}: {exc}")
            elif game_id:
                warnings.append(f"no MLB gamePk resolved for gameId={game_id}; settlement will be unresolved")
            outcome_cache[game_id] = outcome or {"gameStatus": (game or {}).get("status")}

        game_outcome = dict(
            outcome_cache[game_id],
            awayAbbr=(game or {}).get("awayTeam"),
            homeAbbr=(game or {}).get("homeTeam"),
        )

        status, result, reason = settle_market(market, game_outcome)
        settled_at = ids.utc_now_iso() if status in ("SETTLED", "VOID") else None

        checkpoint_prices = [
            {"checkpoint": q["checkpoint"], "clvQuoteId": q["clvQuoteId"],
             "yesPrice": (q["yesAsk"] / 100.0) if q.get("yesAsk") is not None else None,
             "hypotheticalYesReturn": hypothetical_yes_return(
                 (q["yesAsk"] / 100.0) if q.get("yesAsk") is not None else None,
                 result if status == "SETTLED" else None,
             )}
            for q in clv_quotes_by_ticker.get(market["marketTicker"], []) if q.get("checkpoint")
        ]

        matching_bets = bets_by_ticker.get(market["marketTicker"], [])
        bet_id = matching_bets[0]["betId"] if matching_bets else None
        realized_return = None
        if matching_bets and status == "SETTLED":
            bet = matching_bets[0]
            bet_result = derive_bet_result(result, bet.get("side") or "YES")
            realized_return = realized_return_for_bet(bet.get("stake"), bet.get("entryPrice"), bet_result)
            updated_bet = dict(bet)
            updated_bet["result"] = bet_result
            updated_bet["status"] = "settled"
            updated_bet["netProfitLoss"] = realized_return
            updated_bet["returnAmount"] = realized_return
            updated_bet["updatedAt"] = ids.utc_now_iso()
            bet_updates.append(updated_bet)

        settlement_records.append(build_settlement_record(
            market_ticker=market["marketTicker"], game_id=game_id, market_family=market.get("marketFamily"),
            settlement_status=status, result=result, settlement_source="edgelab_settle_markets",
            settled_at=settled_at, unavailable_reason=reason,
            hypothetical_returns_by_checkpoint=checkpoint_prices, bet_id=bet_id, realized_return=realized_return,
        ))

    settlements_path = storage.partition_path("settlements", date)
    s_updated, s_inserted = storage.upsert_records(settlements_path, settlement_records, "settlementId")

    bets_path = storage.singleton_path("bets", "bets.jsonl")
    if bet_updates:
        storage.upsert_records(bets_path, bet_updates, "betId")

    run_record = {
        "schemaVersion": "1",
        "runId": run_id,
        "runType": "SETTLEMENT",
        "startedAt": started_at,
        "completedAt": ids.utc_now_iso(),
        "status": "success" if not warnings else "partial",
        "sourceWorkflow": os.environ.get("GITHUB_WORKFLOW"),
        "githubRunId": os.environ.get("GITHUB_RUN_ID"),
        "inputFiles": [storage.partition_path("markets", date), storage.partition_path("games", date)],
        "outputFiles": [settlements_path, bets_path],
        "counts": {
            "marketsConsidered": len(markets),
            "settlementsInserted": s_inserted,
            "settlementsUpdated": s_updated,
            "betsSettled": len(bet_updates),
        },
        "errors": [],
        "warnings": warnings,
        "createdAt": started_at,
        "provenance": {
            "sourceSystem": "edgelab_cli", "sourceFile": __file__, "sourceKey": date,
            "capturedAt": started_at, "ingestedAt": started_at,
        },
    }
    storage.append_records(storage.partition_path("research_runs", date), [run_record], "runId")

    print(
        f"[settle_markets] date={date} markets={len(markets)} settled_or_void="
        f"{sum(1 for r in settlement_records if r['settlementStatus'] in ('SETTLED', 'VOID'))} "
        f"bets_settled={len(bet_updates)} warnings={len(warnings)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
