"""
lib/edgelab/settlement.py
============================
Settlement linkage (Phase 1 section I): settle every OBSERVED eligible
market, not only placed bets, so unbet market families (F5 spreads,
alternate totals, team totals, pitcher/player props) remain researchable
via price-dependent hypothetical returns.

Pure functions only, deliberately -- reuses the repo's existing settle_*
logic rather than re-deriving it:
  - lib.research.inning_result_settlement.settle_inning_result for F3/F5/F7
    three-way result markets, gated on lib.research.market_taxonomy's
    HORIZON_MARKET_STATUS[scope]["outcomeStructureStatus"] -- currently
    CONFIRMED_THREE_WAY for all of F3/F5/F7, so all three settle the same
    way; if a future change ever retracts a horizon's confirmation, this
    module needs no change -- settle_inning_result() falls back to
    SETTLEMENT_UNRESOLVED/"structure_unverified" on its own.
  - Full-game moneyline/total/team-total/winning-margin/first-inning-run
    settlement is implemented directly here (simple final-score
    comparisons; no existing library function covers these families).
  - Pitcher/hitter prop families (strikeouts, outs, hits, total bases,
    HRR, RBIs, stolen bases) have NO settlement implementation in this
    repo at all -- Phase 1 is explicit and honest about that gap
    (SETTLEMENT_UNRESOLVED, unavailableReason="player_prop_settlement_not_implemented"),
    not a fabricated result. See docs/EDGELAB_PHASE1.md's Phase 2
    recommendations.

Fetching the actual game_outcome (final score, per-period score, game
status) is a CLI/wiring concern (scripts/edgelab/settle_markets.py),
kept out of this module so the settlement decision itself stays pure and
directly testable, matching every settle_* function elsewhere in this
repo.
"""

from lib.edgelab import ids
from lib.edgelab import SCHEMA_VERSION
from lib.research.inning_result_settlement import SETTLEMENT_UNRESOLVED, settle_inning_result
from lib.research.market_taxonomy import (
    FAMILY_FIRST_INNING_RUN,
    FAMILY_GAME_RESULT,
    FAMILY_GAME_TOTAL,
    FAMILY_INNING_RESULT,
    FAMILY_INNING_TOTAL,
    FAMILY_TEAM_TOTAL,
    FAMILY_WINNING_MARGIN,
)

_VOIDABLE_STATUSES = {"Postponed", "Cancelled", "Suspended"}
_PLAYER_PROP_FAMILIES = {
    "pitcher_strikeouts", "pitcher_outs", "hitter_hits", "hitter_total_bases",
    "hitter_hits_runs_rbis", "hitter_rbis", "hitter_stolen_bases",
}


def settle_market(market, game_outcome):
    """
    market: dict with at least marketFamily, marketHorizon, team, threshold.
    game_outcome: {"awayRuns", "homeRuns", "awayAbbr", "homeAbbr",
    "completedInnings", "gameStatus", "periodScores": {"F3"/"F5"/"F7": (away,home)},
    "firstInningRuns": (away,home) or None}. Any key may be missing/None.

    Returns (settlementStatus, result, unavailableReason):
      settlementStatus in {"SETTLED", "VOID", "SETTLEMENT_UNRESOLVED"}
      result in {"YES","NO","AWAY","HOME","TIE", None}
      unavailableReason non-None only when settlementStatus != "SETTLED"
      and not a clean VOID.
    """
    game_status = game_outcome.get("gameStatus")
    if game_status in _VOIDABLE_STATUSES:
        return "VOID", None, None

    family = market.get("marketFamily")
    horizon = market.get("marketHorizon") or "F5"

    if family in _PLAYER_PROP_FAMILIES:
        return "SETTLEMENT_UNRESOLVED", None, "player_prop_settlement_not_implemented"

    if family in (FAMILY_GAME_RESULT, FAMILY_INNING_RESULT):
        if family == FAMILY_GAME_RESULT:
            away, home = game_outcome.get("awayRuns"), game_outcome.get("homeRuns")
            if away is None or home is None or game_status != "Final":
                return "SETTLEMENT_UNRESOLVED", None, "missing_final_score"
            winner = "TIE" if away == home else ("AWAY" if away > home else "HOME")
        else:
            period = (game_outcome.get("periodScores") or {}).get(horizon)
            if period is None:
                return "SETTLEMENT_UNRESOLVED", None, f"missing_period_score_{horizon}"
            away, home = period
            raw_result, reason = settle_inning_result(horizon, away, home, game_outcome.get("completedInnings"), game_status)
            if raw_result == SETTLEMENT_UNRESOLVED:
                return "SETTLEMENT_UNRESOLVED", None, reason
            winner = raw_result.upper()

        # Every Kalshi moneyline/result ticker is its OWN binary market for
        # ONE specific team (or the tie outcome) -- resolve to that ticker's
        # own YES/NO rather than the raw 3-way game winner.
        ticker_outcome = market.get("outcome")  # classify_market()'s 'Win'/'Tie'
        if ticker_outcome == "Tie":
            return "SETTLED", ("YES" if winner == "TIE" else "NO"), None

        ticker_team = market.get("team")
        away_abbr, home_abbr = game_outcome.get("awayAbbr"), game_outcome.get("homeAbbr")
        if not ticker_team or ticker_team not in (away_abbr, home_abbr):
            return "SETTLEMENT_UNRESOLVED", None, "ticker_team_not_resolved"
        ticker_side = "AWAY" if ticker_team == away_abbr else "HOME"
        return "SETTLED", ("YES" if winner == ticker_side else "NO"), None

    if family == FAMILY_FIRST_INNING_RUN:
        first_inning = game_outcome.get("firstInningRuns")
        if first_inning is None:
            return "SETTLEMENT_UNRESOLVED", None, "missing_first_inning_score"
        away, home = first_inning
        return "SETTLED", ("YES" if (away + home) > 0 else "NO"), None

    if family in (FAMILY_GAME_TOTAL, FAMILY_INNING_TOTAL, FAMILY_TEAM_TOTAL, FAMILY_WINNING_MARGIN):
        threshold = market.get("threshold")
        if threshold is None:
            return "SETTLEMENT_UNRESOLVED", None, "missing_threshold"

        if family == FAMILY_INNING_TOTAL:
            period = (game_outcome.get("periodScores") or {}).get(horizon)
            if period is None:
                return "SETTLEMENT_UNRESOLVED", None, f"missing_period_score_{horizon}"
            away, home = period
        else:
            away, home = game_outcome.get("awayRuns"), game_outcome.get("homeRuns")
            if away is None or home is None or game_status != "Final":
                return "SETTLEMENT_UNRESOLVED", None, "missing_final_score"

        if family == FAMILY_GAME_TOTAL:
            return "SETTLED", ("YES" if (away + home) > threshold else "NO"), None

        team = market.get("team")
        away_abbr, home_abbr = game_outcome.get("awayAbbr"), game_outcome.get("homeAbbr")
        if not team or team not in (away_abbr, home_abbr):
            return "SETTLEMENT_UNRESOLVED", None, "team_not_resolved"
        team_runs, opp_runs = (away, home) if team == away_abbr else (home, away)

        if family == FAMILY_TEAM_TOTAL:
            return "SETTLED", ("YES" if team_runs > threshold else "NO"), None
        return "SETTLED", ("YES" if (team_runs - opp_runs) > threshold else "NO"), None  # winning_margin

    return "SETTLEMENT_UNRESOLVED", None, "unrecognized_market_family"


def hypothetical_yes_return(yes_price, result):
    """
    Price-dependent hypothetical return per $1 staked on the YES side at
    `yes_price` (0-1 fraction). result is the settle_market() result:
    win for YES if result == 'YES' (or 'AWAY'/'HOME' callers have already
    mapped to their own YES/NO framing before calling this). None/'VOID'
    -> 0 (no return either way). Never fabricates a return for a missing
    price -- returns None instead.
    """
    if yes_price is None or yes_price <= 0 or yes_price >= 1:
        return None
    if result is None:
        return 0.0
    if result == "YES":
        return round((1.0 - yes_price) / yes_price, 4)
    if result == "NO":
        return -1.0
    return 0.0


def derive_bet_result(settlement_result, bet_side):
    """
    settlement_result: Settlement.result ('YES'/'NO'/None -- None means
    not yet settled or unresolved). bet_side: PlacedBet.side ('YES'/'NO').
    Returns 'WIN'/'LOSS'/None -- None (not a guess) when settlement_result
    is None, i.e. still pending.
    """
    if settlement_result not in ("YES", "NO") or bet_side not in ("YES", "NO"):
        return None
    return "WIN" if settlement_result == bet_side else "LOSS"


def realized_return_for_bet(stake, entry_price, bet_result):
    """
    Actual dollar return (not counting stake) for a settled bet. Kalshi
    contracts pay $1 per contract on a win; stake/entry_price gives the
    (fractional) contract count bought at entry_price (0-1 implied prob).
    WIN -> stake * (1/entry_price - 1); LOSS -> -stake; PUSH/VOID -> 0.
    Never fabricates a number when a required input is missing.
    """
    if bet_result is None or stake is None:
        return None
    if bet_result in ("PUSH", "VOID"):
        return 0.0
    if bet_result == "LOSS":
        return round(-stake, 4)
    if bet_result == "WIN":
        if not entry_price or entry_price <= 0:
            return None
        return round(stake * (1.0 / entry_price - 1.0), 4)
    return None


def build_settlement_record(market_ticker, game_id, market_family, settlement_status, result,
                             settlement_source, settled_at, unavailable_reason=None,
                             hypothetical_returns_by_checkpoint=None, bet_id=None,
                             realized_return=None, source="edgelab_settlement", provenance=None):
    now = ids.utc_now_iso()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "settlementId": ids.build_settlement_id(game_id, market_ticker),
        "gameId": game_id,
        "marketTicker": market_ticker,
        "marketFamily": market_family,
        "outcome": result,
        "settlementStatus": settlement_status,
        "unavailableReason": unavailable_reason,
        "settlementSource": settlement_source,
        "settledAt": settled_at,
        "result": result if result in ("YES", "NO") else None,
        "hypotheticalReturnsByCheckpoint": hypothetical_returns_by_checkpoint or [],
        "betId": bet_id,
        "realizedReturn": realized_return,
        "createdAt": now,
        "updatedAt": now,
        "source": source,
        "validationStatus": "valid",
        "provenance": provenance or {
            "sourceSystem": source,
            "sourceFile": None,
            "sourceKey": market_ticker,
            "capturedAt": settled_at,
            "ingestedAt": now,
        },
    }
