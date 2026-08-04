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

PARSER INTEGRITY GATE (issue #43 correction round): a market is left
SETTLEMENT_UNRESOLVED, never settled on a partially-trusted parse, when
any of the following hold -- each with its own specific reason, and
each preserving the parser's own evidence fields regardless:
  - the ticker and title thresholds disagree (player_prop_threshold_mismatch)
  - the ticker's team token doesn't match either known side, or no team
    context was available at all (player_prop_team_unresolved) -- this
    NEVER falls back to searching both rosters; see
    lib.edgelab.player_resolution's module docstring
  - the player token doesn't match the expected shape at all
    (player_prop_token_malformed)
  - the title doesn't match the expected "Name: N+ stat?" shape, or no
    title was available (player_prop_market_not_parseable)
  - the title's own stat wording doesn't match what this market's
    family expects (player_prop_stat_text_family_mismatch) -- verified
    against the EXACT wording Kalshi uses for all seven families (see
    lib.research.player_prop_parser.FAMILY_STAT_TEXT)
  - the title's parenthetical team tag conflicts with the ticker-derived
    team (player_prop_parenthetical_team_conflict)
A market is never settled merely because its title contains a name that
happens to uniquely match a player somewhere in the game -- team
scoping is derived from the ticker itself, and a hard structural
integrity failure is checked BEFORE player resolution is ever attempted.

PARTICIPATION VERIFICATION (issue #43 correction round): a player
NAME-matched within the correct team's boxscore listing is not
necessarily a player who actually played -- every active-roster player
is listed in the boxscore whether used or not. lib.edgelab.
player_participation.verify_participation() requires POSITIVE
authoritative evidence (gamesPlayed/gamesPitched/inningsPitched) of a
real appearance before a zero-valued stat is trusted as a genuine NO;
missing/inconclusive evidence is SETTLEMENT_UNRESOLVED/
"player_participation_unverified", never inferred either way from a
zero-filled stat object alone. A pinch runner who scored/stole a base
without ever batting still has gamesPlayed >= 1 and is correctly
verified for hitter_hits_runs_rbis/hitter_stolen_bases even at zero
plate appearances (see that module's docstring).

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
not appear in the final boxscore at all is left SETTLEMENT_UNRESOLVED
with reason "player_not_resolved_zero_candidates" (see player
resolution below), and a player who is listed but never verifiably
participated is left SETTLEMENT_UNRESOLVED with reason
"player_participation_unverified" (see above) -- this module implements
NO automatic VOID/NO path for a non-participating or unverified player
in either case, and must not gain one without first capturing real
Kalshi rule evidence.
"""
from lib.edgelab import mlb_boxscore, player_participation, player_resolution, player_stats
from lib.research.player_prop_parser import TEAM_RESOLVED, parse_player_prop_market

SETTLED = "SETTLED"
SETTLEMENT_UNRESOLVED = "SETTLEMENT_UNRESOLVED"


def _player_entry_for_candidate(boxscore_teams, candidate):
    """The FULL boxscore player entry (person/jerseyNumber/stats/...) for a resolved candidate, or {} if somehow absent."""
    side = candidate.get("side")
    players = ((boxscore_teams.get(side) or {}).get("players")) or {}
    for player in players.values():
        person = player.get("person") or {}
        if person.get("id") == candidate.get("playerId"):
            return player
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
    kalshi_official_result: optional "YES"/"NO"/None -- always None in
      this repository's actual settlement runs today: no ingestion path
      captures a Kalshi official-result field, and
      scripts/edgelab/settle_markets.py never populates
      game_outcome["kalshiOfficialResultsByTicker"] (grep-verified --
      that key is never set anywhere in this codebase). The comparison
      LOGIC below (this parameter, and the conflict-detection branch it
      feeds) is prepared and unit-tested, but is NOT automatically
      wired up -- capturing a real Kalshi official result and threading
      it through game_outcome would still require deliberate future
      ingestion + orchestration work in settle_markets.py. Passing this
      parameter directly (as this module's own tests do) exercises the
      comparison logic today; nothing in the actual settlement run does
      so yet.
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
        "participationStatus": None,
        "participationEvidence": {},
    }

    if family not in player_stats.STAT_CATEGORY_BY_FAMILY:
        return SETTLEMENT_UNRESOLVED, None, "unrecognized_player_prop_family", evidence

    if not mlb_boxscore.is_final_status(game_status):
        return SETTLEMENT_UNRESOLVED, None, "game_not_final", evidence

    if not boxscore_teams:
        return SETTLEMENT_UNRESOLVED, None, "boxscore_fetch_failed", evidence

    prop = parse_player_prop_market(
        market.get("marketTicker"), market.get("eventTicker"), market.get("title"),
        away_team=away_abbr, home_team=home_abbr, family=family,
    )
    evidence["rawPlayerToken"] = prop["rawPlayerToken"]
    evidence["teamAbbreviation"] = prop["teamAbbr"]
    evidence["threshold"] = prop["threshold"]

    # Parser-integrity gate (GitHub issue #43 correction round): every
    # one of these leaves the market unresolved rather than settling on
    # a partially-trusted parse. Order matters only for which single
    # reason is reported when multiple issues coincide -- structural
    # failures are checked before the more specific cross-checks.
    if prop["parseStatus"] != "PARSED":
        return SETTLEMENT_UNRESOLVED, None, "player_prop_market_not_parseable", evidence
    if prop["tokenMalformed"]:
        return SETTLEMENT_UNRESOLVED, None, "player_prop_token_malformed", evidence
    if prop["teamResolutionStatus"] != TEAM_RESOLVED:
        # Covers both "ticker's team token matches neither known side"
        # AND "no team context available at all" -- settlement always
        # supplies away_abbr/home_abbr, so this is never silently
        # downgraded into searching both rosters (see
        # lib.edgelab.player_resolution's module docstring: team
        # scoping must come from the ticker, never be skipped).
        return SETTLEMENT_UNRESOLVED, None, "player_prop_team_unresolved", evidence
    if prop["thresholdMismatch"]:
        return SETTLEMENT_UNRESOLVED, None, "player_prop_threshold_mismatch", evidence
    if prop["titleParseStatus"] != "PARSED" or not prop["displayNameRaw"]:
        return SETTLEMENT_UNRESOLVED, None, "player_prop_market_not_parseable", evidence
    if prop["statTextFamilyMismatch"]:
        return SETTLEMENT_UNRESOLVED, None, "player_prop_stat_text_family_mismatch", evidence
    if prop["parentheticalTeamConflict"]:
        return SETTLEMENT_UNRESOLVED, None, "player_prop_parenthetical_team_conflict", evidence

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
    player_entry = _player_entry_for_candidate(boxscore_teams, candidate)

    # Participation gate (GitHub issue #43 correction round): being
    # returned by player resolution only proves this player's NAME
    # matched within the correct team's boxscore listing -- it does NOT
    # prove they actually played. Every active-roster player is listed
    # in the boxscore whether used or not, so a zero-filled stat object
    # is indistinguishable from a bench player who never entered unless
    # authoritative participation evidence (gamesPlayed/gamesPitched/
    # inningsPitched -- see lib.edgelab.player_participation) says
    # otherwise. This is checked BEFORE stat extraction: a settlement
    # must never reach a "zero stat -> NO" conclusion for a player whose
    # participation itself is unverified.
    participation_status, participation_reason, participation_evidence = player_participation.verify_participation(
        player_entry, stat_category,
    )
    evidence["participationStatus"] = participation_status
    evidence["participationEvidence"] = participation_evidence
    if participation_status != player_participation.RESOLVED:
        return SETTLEMENT_UNRESOLVED, None, participation_reason, evidence

    stat = (player_entry.get("stats") or {}).get(stat_category) or {}

    # A player whose participation is POSITIVELY VERIFIED above but who
    # recorded zero in the relevant stat settles normally below as NO --
    # extract_stat_value treats 0 as a perfectly valid value, never as
    # "missing".
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
