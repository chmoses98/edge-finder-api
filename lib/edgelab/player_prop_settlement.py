#!/usr/bin/env python3
"""
lib/edgelab/player_prop_settlement.py
========================================
Pure settlement decision for the seven pitcher/hitter player-prop
market families (GitHub issue #43): pitcher_strikeouts, pitcher_outs,
hitter_hits, hitter_total_bases, hitter_hits_runs_rbis, hitter_rbis,
hitter_stolen_bases.

Deliberately RE-DERIVES team/player/threshold fresh from the market's
own marketTicker/eventTicker/title (via
lib.research.player_prop_parser.parse_player_prop_market) rather than
trusting whatever a Market dimension record happened to have persisted
at ingestion time. marketTicker/eventTicker/title have been present on
every Market record since Phase 1, long before this module existed, so
a player-prop market ingested before issue #43 shipped is settleable on
its very first rerun -- no separate re-ingestion/backfill-of-ingestion
step is required, only a settlement rerun (see
scripts/edgelab/backfill_player_prop_settlement.py).

CONTRACT SEMANTICS (issue #43): every one of these seven families is a
literal Kalshi "N+" contract -- YES iff the actual final statistic is
>= N. This is DELIBERATELY NOT the game-total family's "actual > N.5"
framing (lib/edgelab/settlement.py's FAMILY_GAME_TOTAL/FAMILY_TEAM_TOTAL/
FAMILY_WINNING_MARGIN branches) -- equality is a YES, never a push, and
there is no half-point line to compare against.

PARTICIPATION / DNP / VOID (issue #43 finding, real-data audit): this
repository's own archived Kalshi snapshots
(data/kalshi_registry_snapshots/*.json) do not capture any Kalshi
rules-text or void-condition field for any of these seven series --
every raw market record in every snapshot this phase examined has
exactly {event_ticker, market_ticker, title, subtitle, open_time,
close_time, market_type, status, snapshot_ts, yes_bid, yes_ask, mid,
implied_pct, american_odds, last_price, volume, open_interest} and
nothing describing settlement/void behavior. No Kalshi participation
rule can therefore be directly verified from evidence this repository
actually has. Per the issue's own explicit instruction, a player who did
not appear in the final boxscore is therefore ALWAYS left
SETTLEMENT_UNRESOLVED with reason
"player_not_resolved_zero_candidates" (see player resolution below) --
this module implements NO automatic VOID/NO path for a non-participating
player, and must not gain one without first capturing real Kalshi rule
evidence.
"""
from lib.edgelab import mlb_boxscore, player_resolution, player_stats
from lib.research.player_prop_parser import parse_player_prop_market

SETTLED = "SETTLED"
SETTLEMENT_UNRESOLVED = "SETTLEMENT_UNRESOLVED"


def _stat_for_candidate(boxscore_teams, candidate, stat_category):
    side = candidate.get("side")
    players = ((boxscore_teams.get(side) or {}).get("players")) or {}
    for player in players.values():
        person = player.get("person") or {}
        if person.get("id") == candidate.get("playerId"):
            return (player.get("stats") or {}).get(stat_category) or {}
    return {}


def _evidence_candidates(candidates):
    return [
        {"playerId": c.get("playerId"), "playerName": c.get("playerName"),
         "side": c.get("side"), "jerseyNumber": c.get("jerseyNumber")}
        for c in candidates
    ]


def settle_player_prop_market(market, game_status, boxscore_teams, away_abbr=None, home_abbr=None,
                               kalshi_official_result=None, fetch_meta=None):
    """
    market: a Market dimension record dict for one player-prop ticker
      (must have marketFamily/marketTicker/eventTicker/title/gameId).
    game_status: the game's authoritative `detailedState`
      (lib.edgelab.mlb_boxscore.extract_game_status) -- NOT the
      slate-ingested pregame status game-level settlement uses.
    boxscore_teams: lib.edgelab.mlb_boxscore.extract_boxscore_teams(feed)
      for this exact gamePk, or {} if the feed fetch failed.
    away_abbr/home_abbr: this game's team abbreviations.
    kalshi_official_result: optional "YES"/"NO"/None -- see module
      docstring's participation-rule finding; always None today (no
      ingestion path in this repository captures a Kalshi official
      result field yet). Included so the conflict-detection path below
      activates automatically the moment such a field exists, without
      requiring a code change.
    fetch_meta: optional dict (gamePk, sourceEndpoint, sourcePayloadHash,
      fetchedAt) supplied by the caller for evidence -- this function
      makes no network calls itself and is otherwise pure.

    Returns (settlementStatus, result, unavailableReason, evidence).
    evidence is always a dict (never None) -- see
    data/edgelab/schema_v1/settlement.schema.json's settlementEvidence.
    """
    fetch_meta = fetch_meta or {}
    family = market.get("marketFamily")
    evidence = {
        "gamePk": fetch_meta.get("gamePk"),
        "gameStatus": game_status,
        "sourceSystem": "MLB_STATS_API",
        "sourceEndpoint": fetch_meta.get("sourceEndpoint"),
        "sourcePayloadHash": fetch_meta.get("sourcePayloadHash"),
        "fetchedAt": fetch_meta.get("fetchedAt"),
        "playerId": None,
        "playerName": None,
        "teamAbbreviation": None,
        "rawPlayerToken": None,
        "statCategory": player_stats.STAT_CATEGORY_BY_FAMILY.get(family),
        "statFields": {},
        "actualValue": None,
        "threshold": None,
        "comparisonOperator": "AT_LEAST",
        "kalshiOfficialResult": kalshi_official_result,
        "resolutionStatus": None,
        "resolutionReason": None,
        "candidates": [],
    }

    if family not in player_stats.STAT_CATEGORY_BY_FAMILY:
        return SETTLEMENT_UNRESOLVED, None, "unrecognized_player_prop_family", evidence

    if not mlb_boxscore.is_final_status(game_status):
        return SETTLEMENT_UNRESOLVED, None, "game_not_final", evidence

    if not boxscore_teams:
        return SETTLEMENT_UNRESOLVED, None, "boxscore_fetch_failed", evidence

    prop = parse_player_prop_market(
        market.get("marketTicker"), market.get("eventTicker"), market.get("title"),
        away_team=away_abbr, home_team=home_abbr,
    )
    if prop["parseStatus"] != "PARSED" or not prop["displayNameRaw"]:
        return SETTLEMENT_UNRESOLVED, None, "player_prop_market_not_parseable", evidence

    evidence["rawPlayerToken"] = prop["rawPlayerToken"]
    evidence["teamAbbreviation"] = prop["teamAbbr"]
    evidence["threshold"] = prop["threshold"]

    resolution = player_resolution.resolve_player_in_game(
        boxscore_teams, prop["normalizedNameVariants"], team_abbr=prop["teamAbbr"],
        away_abbr=away_abbr, home_abbr=home_abbr, ticker_numeric_suffix=prop["tokenNumericSuffix"],
    )
    evidence["resolutionStatus"] = resolution["status"]
    evidence["candidates"] = _evidence_candidates(resolution["candidates"])
    if resolution["corroboratedBy"]:
        evidence["resolutionReason"] = f"resolved_via_{resolution['corroboratedBy']}_corroboration"

    if resolution["status"] == player_resolution.NOT_FOUND:
        return SETTLEMENT_UNRESOLVED, None, "player_not_resolved_zero_candidates", evidence
    if resolution["status"] == player_resolution.AMBIGUOUS:
        return SETTLEMENT_UNRESOLVED, None, "player_not_resolved_ambiguous_candidates", evidence

    candidate = resolution["candidate"]
    evidence["playerId"] = candidate.get("playerId")
    evidence["playerName"] = candidate.get("playerName")

    stat_category = player_stats.STAT_CATEGORY_BY_FAMILY[family]
    stat = _stat_for_candidate(boxscore_teams, candidate, stat_category)

    # A player who officially appeared (candidate came FROM this exact
    # game's boxscore) but recorded zero in the relevant stat settles
    # normally below as NO -- extract_stat_value treats 0 as a perfectly
    # valid value, never as "missing".
    actual_value, _resolved_category, stat_fields, reason = player_stats.extract_stat_value(
        family,
        stat if stat_category == "batting" else None,
        stat if stat_category == "pitching" else None,
    )
    evidence["statFields"] = stat_fields

    if reason is not None:
        return SETTLEMENT_UNRESOLVED, None, reason, evidence

    evidence["actualValue"] = actual_value
    mlb_result = "YES" if actual_value >= prop["threshold"] else "NO"

    if kalshi_official_result is not None and str(kalshi_official_result).upper() != mlb_result:
        evidence["resolutionReason"] = "kalshi_and_mlb_stat_results_disagree"
        return SETTLEMENT_UNRESOLVED, None, "kalshi_mlb_result_conflict", evidence

    return SETTLED, mlb_result, None, evidence
