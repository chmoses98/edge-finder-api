#!/usr/bin/env python3
"""
scripts/edgelab/settle_markets.py
=====================================
CLI entry point: settle every market EdgeLab observed for a date,
including markets nobody bet, and back-fill WIN/LOSS/realized-return onto
matching placed bets.

Game-level outcomes are fetched via clv_update.py's existing
fetch_mlb_linescore() (the same MLB Stats API linescore call production
already uses for F5 settlement) -- no new external integration. A game
whose gameId isn't a real MLB gamePk (the "date_away_home" fallback
identity -- see lib/edgelab/ids.py), or whose linescore fetch fails, is
recorded SETTLEMENT_UNRESOLVED with an explicit reason; nothing here
guesses a score.

Player-prop families (GitHub issue #43: pitcher_strikeouts,
pitcher_outs, hitter_hits, hitter_total_bases, hitter_hits_runs_rbis,
hitter_rbis, hitter_stolen_bases) additionally need one authoritative
MLB Stats API game-feed fetch per gamePk (lib/edgelab/mlb_boxscore.py) --
fetched AT MOST ONCE per game regardless of how many player-prop markets
that game has, and only for a game that actually has at least one (a
game with no player-prop markets never triggers this second fetch). A
fetch failure, or a game that isn't yet Final, leaves every player-prop
market on that game SETTLEMENT_UNRESOLVED with a specific reason (see
lib/edgelab/player_prop_settlement.py) -- it never blocks any other
game's settlement in the same run.

Usage:
    python3 scripts/edgelab/settle_markets.py [--date YYYY-MM-DD]
"""
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, mlb_boxscore, player_stats, storage
from lib.edgelab.settlement import (
    bet_needs_settlement_update,
    build_settlement_record,
    hypothetical_yes_return,
    merge_settlement_record,
    settle_bets_for_ticker,
    settle_market_full,
    was_market_ever_recommended,
)
from lib.research.inning_result_settlement import extract_period_score_from_linescore

try:
    from clv_update import fetch_mlb_linescore
except ImportError:
    fetch_mlb_linescore = None

# The seven families lib/edgelab/player_prop_settlement.py knows how to
# settle -- the single source of truth is player_stats.STAT_CATEGORY_BY_FAMILY
# (never duplicated as a separate hardcoded set here).
PLAYER_PROP_FAMILIES = frozenset(player_stats.STAT_CATEGORY_BY_FAMILY.keys())


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


def _fetch_player_prop_context(game, warnings):
    """
    Fetches (once) the authoritative MLB game-feed for a player-prop
    market's game, returning the {"boxscoreTeams", "playerPropGameStatus",
    "boxscoreFetchMeta"} keys lib.edgelab.settlement.settle_market_full()
    expects in game_outcome. Never raises -- a fetch failure is recorded
    as a warning and settlement is left to report
    SETTLEMENT_UNRESOLVED/"boxscore_fetch_failed" per-market, exactly
    like the existing linescore-fetch failure path above.
    """
    game_pk = (game or {}).get("mlbGamePk")
    if not game_pk:
        warnings.append("no MLB gamePk resolved for player-prop game; player props will be unresolved")
        return {"boxscoreTeams": {}, "playerPropGameStatus": None, "boxscoreFetchMeta": {}}

    try:
        feed = mlb_boxscore.fetch_game_feed(game_pk)
    except Exception as exc:  # network/API failures must never crash the run
        warnings.append(f"player-prop boxscore fetch failed for gamePk={game_pk}: {exc}")
        feed = None

    if feed is None:
        warnings.append(f"player-prop boxscore unavailable for gamePk={game_pk}")

    return {
        "boxscoreTeams": mlb_boxscore.extract_boxscore_teams(feed),
        "playerPropGameStatus": mlb_boxscore.extract_game_status(feed),
        "boxscoreFetchMeta": {
            "gamePk": game_pk,
            "sourceEndpoint": f"{mlb_boxscore.MLB_STATS_API}/game/{game_pk}/feed/live",
            "sourcePayloadHash": mlb_boxscore.payload_hash(feed),
            "fetchedAt": ids.utc_now_iso(),
        },
    }


def settle_date(date, dry_run=False):
    """
    Settle every observed market for `date`. Pure orchestration over
    already-committed EdgeLab data plus live MLB Stats API fetches --
    reused as-is by both main() (today's/yesterday's normal nightly run)
    and scripts/edgelab/backfill_player_prop_settlement.py (historical
    reprocessing), per GitHub issue #43's "reuse the normal settlement
    path, do not create a second implementation" requirement.

    dry_run=True computes and returns the exact same summary WITHOUT
    writing anything to disk (no settlements/bets/research_runs writes)
    -- for backfill's preview mode.

    Returns a summary dict: {"date", "warnings", "counts": {...},
    "byFamily": {family: {"observed","settled","void","unresolved",
    "betsUpdated"}}, "unresolvedReasonsByFamily": {family: {reason: n}}}.
    """
    run_id = ids.new_run_id("SETTLEMENT", github_run_id=os.environ.get("GITHUB_RUN_ID"))
    started_at = ids.utc_now_iso()

    markets = list(storage.read_records(storage.partition_path("markets", date)))
    games = {g["gameId"]: g for g in storage.read_records(storage.partition_path("games", date))}
    bets = list(storage.read_records(storage.singleton_path("bets", "bets.jsonl")))
    bets_by_ticker = {}
    for bet in bets:
        # A CANCELLED bet (logged in error -- lib.edgelab.bets.cancel_placed_bet)
        # is never settled: it isn't a real wager, so it must never gain a
        # result/netProfitLoss or become the Settlement record's
        # representative betId (maintainer review finding).
        if (bet.get("recordStatus") or "ACTIVE") == "CANCELLED":
            continue
        if bet.get("marketTicker"):
            bets_by_ticker.setdefault(bet["marketTicker"], []).append(bet)

    clv_quotes_by_ticker = {}
    for q in storage.read_records(storage.partition_path("clv_quotes", date)):
        clv_quotes_by_ticker.setdefault(q["marketTicker"], []).append(q)

    # Part 2 (Market Research Corpus milestone): every Settlement row also
    # records whether the market was ever recommended, so "observed but
    # never recommended" / "recommended but not placed" research queries
    # don't need a second pass over the recommendations ledger later.
    recommendations_by_ticker = {}
    for r in storage.read_records(storage.partition_path("recommendations", date)):
        if r.get("marketTicker"):
            recommendations_by_ticker.setdefault(r["marketTicker"], []).append(r)

    settlements_path = storage.partition_path("settlements", date)
    # Loaded BEFORE the loop so every market's fresh computation can be
    # merged against whatever this exact ticker/game already settled to
    # (GitHub issue #43 correction round: semantic idempotency) --
    # keyed by settlementId, which is a deterministic hash of
    # (gameId, marketTicker) and therefore stable across reruns
    # regardless of outcome.
    existing_settlements_by_id = {
        r["settlementId"]: r for r in storage.read_records(settlements_path) if r.get("settlementId")
    }

    outcome_cache = {}
    player_prop_cache = {}
    warnings = []
    settlement_records = []
    bet_updates = []
    by_family = {}
    unresolved_reasons_by_family = {}
    meaningful_settlement_changes = 0

    for market in markets:
        game_id = market.get("gameId")
        game = games.get(game_id) if game_id else None
        family = market.get("marketFamily")

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

        # The player-prop boxscore/feed fetch is lazy AND scoped to games
        # that actually have a player-prop market -- a game with none
        # never triggers it. At most one fetch per gamePk regardless of
        # how many player-prop markets that game has (issue #43's "one
        # boxscore fetch per game" requirement).
        if family in PLAYER_PROP_FAMILIES:
            if game_id not in player_prop_cache:
                player_prop_cache[game_id] = _fetch_player_prop_context(game, warnings)
            game_outcome.update(player_prop_cache[game_id])

        status, result, reason, evidence = settle_market_full(market, game_outcome)
        settled_at = ids.utc_now_iso() if status in ("SETTLED", "VOID") else None

        family_counts = by_family.setdefault(
            family, {"observed": 0, "settled": 0, "void": 0, "unresolved": 0, "betsUpdated": 0},
        )
        family_counts["observed"] += 1
        if status == "SETTLED":
            family_counts["settled"] += 1
        elif status == "VOID":
            family_counts["void"] += 1
        else:
            family_counts["unresolved"] += 1
            unresolved_reasons_by_family.setdefault(family, {}).setdefault(reason, 0)
            unresolved_reasons_by_family[family][reason] += 1

        checkpoint_prices = [
            {"checkpoint": q["checkpoint"], "clvQuoteId": q["clvQuoteId"],
             "yesPrice": (q["yesAsk"] / 100.0) if q.get("yesAsk") is not None else None,
             "hypotheticalYesReturn": hypothetical_yes_return(
                 (q["yesAsk"] / 100.0) if q.get("yesAsk") is not None else None,
                 result if status == "SETTLED" else None,
             )}
            for q in clv_quotes_by_ticker.get(market["marketTicker"], []) if q.get("checkpoint")
        ]

        # A ticker can carry MULTIPLE bets (e.g. tranches) -- every one of
        # them is settled independently by settle_bets_for_ticker(), never
        # just the first. The Settlement record itself still links a
        # single representative betId/realizedReturn, matching the
        # schema's single-valued fields; the bet LEDGER update is not
        # limited to one bet.
        #
        # settled_bets always holds one computed entry per matching bet
        # (even an unchanged one), so the representative fields below
        # stay populated on every run -- but only bets that
        # bet_needs_settlement_update() says actually changed are ever
        # written/stamped with a fresh updatedAt (GitHub issue #43
        # correction round: an unrelated, already-correct bet must
        # never be rewritten just because settlement ran again).
        matching_bets = bets_by_ticker.get(market["marketTicker"], [])
        settled_bets = settle_bets_for_ticker(matching_bets, status, result)
        bets_needing_write = []
        for original_bet, computed_bet in zip(matching_bets, settled_bets):
            if bet_needs_settlement_update(original_bet, computed_bet):
                computed_bet["updatedAt"] = ids.utc_now_iso()
                bets_needing_write.append(computed_bet)
        bet_updates.extend(bets_needing_write)
        family_counts["betsUpdated"] += len(bets_needing_write)
        representative_bet_id = matching_bets[0]["betId"] if matching_bets else None
        representative_realized_return = settled_bets[0]["netProfitLoss"] if settled_bets else None

        new_record = build_settlement_record(
            market_ticker=market["marketTicker"], game_id=game_id, market_family=family,
            settlement_status=status, result=result, settlement_source="edgelab_settle_markets",
            settled_at=settled_at, unavailable_reason=reason,
            hypothetical_returns_by_checkpoint=checkpoint_prices, bet_id=representative_bet_id,
            realized_return=representative_realized_return,
            was_recommended=was_market_ever_recommended(recommendations_by_ticker.get(market["marketTicker"], [])),
            was_placed=bool(matching_bets),
            settlement_evidence=evidence,
        )
        existing_record = existing_settlements_by_id.get(new_record["settlementId"])
        merged_record = merge_settlement_record(existing_record, new_record)
        if merged_record is not existing_record:
            meaningful_settlement_changes += 1
        settlement_records.append(merged_record)

    bets_path = storage.singleton_path("bets", "bets.jsonl")

    if dry_run:
        s_updated, s_inserted = 0, 0
    else:
        s_updated, s_inserted = storage.upsert_records(settlements_path, settlement_records, "settlementId")
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
                "settlementsMeaningfullyChanged": meaningful_settlement_changes,
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

    return {
        "date": date,
        "warnings": warnings,
        "counts": {
            "marketsConsidered": len(markets),
            "settledOrVoid": sum(1 for r in settlement_records if r["settlementStatus"] in ("SETTLED", "VOID")),
            "settlementsInserted": s_inserted,
            "settlementsUpdated": s_updated,
            # GitHub issue #43 correction round: the count that actually
            # matters for idempotency verification -- settlements whose
            # canonical content is NEW or genuinely changed (a true
            # no-op rerun reports 0 here, even though settlementsUpdated
            # above may still report every row as "touched" by the
            # underlying upsert mechanics).
            "settlementsMeaningfullyChanged": meaningful_settlement_changes,
            "betsSettled": len(bet_updates),
        },
        "byFamily": by_family,
        "unresolvedReasonsByFamily": unresolved_reasons_by_family,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    date = args.date or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    summary = settle_date(date)

    print(
        f"[settle_markets] date={date} markets={summary['counts']['marketsConsidered']} settled_or_void="
        f"{summary['counts']['settledOrVoid']} "
        f"bets_settled={summary['counts']['betsSettled']} warnings={len(summary['warnings'])}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
