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


def _fetch_authoritative_game_context(game, warnings):
    """
    Fetches (at most once per gamePk, for EVERY game that has one -- not
    only games with player-prop markets) the live MLB Stats API game
    feed and returns the CURRENT authoritative game status derived from
    it, alongside the boxscore context player-prop settlement needs.

    Root-cause fix: the archived Game.status field (data/edgelab/games/
    <date>.jsonl) is captured once, at initial Kalshi-registry ingest
    time, and is never refreshed afterward. Every non-player-prop family
    (game_result, inning_result F3/F5/F7, game_total, team_total,
    winning_margin, first_inning_run) used to be settled against that
    frozen snapshot value even though this same script ALSO fetches a
    live linescore every run -- so a game captured while "Pre-Game"/
    "Scheduled"/"In Progress"/"Delayed" stayed stuck SETTLEMENT_UNRESOLVED
    forever even once it actually finished and its final score was
    already being fetched successfully. Player-prop settlement never had
    this bug: it already called lib.edgelab.mlb_boxscore.fetch_game_feed
    and used the feed's OWN status (extract_game_status) -- this reuses
    that exact same authoritative source and call (never a duplicate
    fetch for a game that also has player props -- see
    tests/edgelab/test_settle_markets_script.py's
    test_authoritative_status_fetch_reused_for_player_props) for every
    other family too, instead of duplicating a second status source.

    Identity safety: before trusting anything else in the feed, cross-
    checks the feed's own away/home team abbreviations
    (lib.edgelab.mlb_boxscore.extract_teams) against the archived Game
    record's awayTeam/homeTeam for the SAME stored gamePk. A gamePk that
    doesn't actually describe the matchup we archived it for is a real
    identity conflict (a stale/incorrect gamePk mapping) -- never
    silently trusted; the archived status is kept as-is and the conflict
    is recorded as an explicit warning so it's visible, never hidden.
    Never fuzzy-matches an alternate gamePk to "fix" this -- a conflict
    is reported, not resolved by guessing.

    Falls back to the archived status (never a crash, never a fabricated
    value) when there's no gamePk to resolve, the live fetch fails, or
    an identity conflict is detected.
    """
    game_pk = (game or {}).get("mlbGamePk")
    archived_status = (game or {}).get("status")
    empty_context = {"gameStatus": archived_status, "boxscoreTeams": {}, "boxscoreFetchMeta": {}}
    if not game_pk:
        # The existing "no MLB gamePk resolved for gameId=..." warning
        # (below, in the linescore-fetch block) already covers this case
        # -- never duplicate it here.
        return empty_context

    try:
        feed = mlb_boxscore.fetch_game_feed(game_pk)
    except Exception as exc:  # network/API failures must never crash the run
        warnings.append(f"authoritative game-feed fetch failed for gamePk={game_pk}: {exc}")
        feed = None

    if feed is None:
        warnings.append(f"authoritative game feed unavailable for gamePk={game_pk}; falling back to archived status {archived_status!r}")
        return {**empty_context, "boxscoreFetchMeta": {"gamePk": game_pk}}

    live_away, live_home = mlb_boxscore.extract_teams(feed)
    archived_away, archived_home = (game or {}).get("awayTeam"), (game or {}).get("homeTeam")
    if live_away and live_home and archived_away and archived_home and (live_away != archived_away or live_home != archived_home):
        warnings.append(
            f"gamePk={game_pk} identity conflict: archived matchup {archived_away}@{archived_home} "
            f"does not match live feed matchup {live_away}@{live_home} -- keeping archived status "
            f"{archived_status!r}, never settling against a mismatched game"
        )
        return {**empty_context, "boxscoreFetchMeta": {"gamePk": game_pk, "identityConflict": True}}

    return {
        "gameStatus": mlb_boxscore.extract_game_status(feed) or archived_status,
        "boxscoreTeams": mlb_boxscore.extract_boxscore_teams(feed),
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
    game_context_cache = {}
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

        # Fetched once per gamePk regardless of family (root-cause fix:
        # every family needs the CURRENT authoritative status, not just
        # player props -- see _fetch_authoritative_game_context). At most
        # one live-feed fetch per gamePk even though it now feeds both
        # the general gameStatus override below AND the player-prop
        # boxscore context (issue #43's original "one boxscore fetch per
        # game" bound still holds, just for a broader set of callers).
        if game_id not in game_context_cache:
            game_context_cache[game_id] = _fetch_authoritative_game_context(game, warnings)
        game_context = game_context_cache[game_id]

        if game_id not in outcome_cache:
            outcome = None
            if game and game.get("mlbGamePk") and fetch_mlb_linescore:
                try:
                    linescore = fetch_mlb_linescore(game["mlbGamePk"])
                    outcome = build_game_outcome_from_linescore(linescore, game_context["gameStatus"])
                    if outcome is None:
                        warnings.append(f"linescore unavailable for gameId={game_id} (gamePk={game['mlbGamePk']})")
                except Exception as exc:  # network/API failures must never crash the run
                    warnings.append(f"linescore fetch failed for gameId={game_id}: {exc}")
            elif game_id:
                warnings.append(f"no MLB gamePk resolved for gameId={game_id}; settlement will be unresolved")
            outcome_cache[game_id] = outcome or {"gameStatus": game_context["gameStatus"]}

        game_outcome = dict(
            outcome_cache[game_id],
            awayAbbr=(game or {}).get("awayTeam"),
            homeAbbr=(game or {}).get("homeTeam"),
        )

        if family in PLAYER_PROP_FAMILIES:
            game_outcome.update({
                "boxscoreTeams": game_context["boxscoreTeams"],
                "playerPropGameStatus": game_context["gameStatus"],
                "boxscoreFetchMeta": game_context["boxscoreFetchMeta"],
            })

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
        settled_bets = settle_bets_for_ticker(matching_bets, status, result, now=ids.utc_now_iso())
        bets_needing_write = []
        for original_bet, computed_bet in zip(matching_bets, settled_bets):
            if bet_needs_settlement_update(original_bet, computed_bet):
                computed_bet["updatedAt"] = ids.utc_now_iso()
                bets_needing_write.append(computed_bet)
            # A confirmed manual receipt (lib.edgelab.bets.confirm_realized_return)
            # that DISAGREES with the just-computed objective settlement is
            # always flagged in this run's warnings -- regardless of whether
            # the comparison itself changed since last run (an unresolved
            # disagreement must stay visible on every run, not just the one
            # where it first appeared) -- see
            # lib.edgelab.settlement.compare_confirmed_receipt_to_settlement.
            # Neither side is ever overwritten here; this is reporting only.
            comparison = computed_bet.get("confirmedReceiptSettlementComparison")
            if comparison and not comparison["agrees"]:
                warnings.append(
                    f"confirmed-receipt/settlement DISAGREEMENT for betId={computed_bet.get('betId')} "
                    f"ticker={market['marketTicker']}: objective result={comparison['objectiveResult']} "
                    f"netProfitLoss={comparison['objectiveNetProfitLoss']} vs confirmed receipt implied "
                    f"result={comparison['confirmedReceiptImpliedResult']} "
                    f"netProfitLoss={comparison['confirmedReceiptNetProfitLoss']} -- both preserved, neither overwritten"
                )
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
