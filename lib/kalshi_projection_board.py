#!/usr/bin/env python3
"""
lib/kalshi_projection_board.py
=================================
Stage 1 of the unified full-market projection engine for GAME-DERIVED MLB
Kalshi markets (docs/PROJECTION_BOARD.md). Pitcher/hitter prop families
are untouched -- out of scope for this stage.

WHY THIS MODULE ADDS NO NEW MODEL
------------------------------------
Every fair probability on this board comes from the exact same
statistical engine production already uses. This module reuses:

  - scripts/discover_kalshi_mlb_markets.discover() for contract parsing,
    game-matching, and per-line pricing (it already evaluates EVERY
    archived alternate rung, not just best_line()) via
    lib.kalshi_probability_adapters.adapt_contract(), which itself reuses
    scripts/build_market_ledger.py's poisson_pmf/p_team_wins/p_over_total
    and lib.research.three_way_projection's tie-retaining F3/F5/F7 model
    (away+tie+home sum to 1 by construction, never renormalized).
  - scripts/executable_price.py (get_executable_prices,
    executable_price_cents_to_american) for executable/post-friction
    pricing -- the SAME helper build_market_ledger.py itself imports.

This module (the "board" layer) adds exactly three things on top:

  1. A PRE-GATE filter+shape step: narrows discover()'s contracts to the
     Stage-1 game-derived families, and reshapes each into the board's
     display schema (natural label, American odds both sides, raw vs.
     executable edge). A downstream PASS/PAPER/Rejected status is
     attached as ADVISORY metadata only (attach_automated_recommendation)
     -- it can never remove a row from this board.
  2. Complementary-side synthesis for the four families where Kalshi's
     own contract is a single two-sided ticker (game_total, inning_total,
     team_total, first_inning_run): discover() only ever prices the YES
     side (Over / YRFI). This module also prices the NO side (Under /
     NRFI) from the complementary probability (1 - p) and the SAME
     ticker's own noBid/noAsk price, so both sides of every two-sided
     market are visible, not just the one side production evaluates.
  3. Lightweight, non-fatal internal coherence checks (three-way sum to
     1, monotonic alternate lines, complementary YES/NO sum to 1) whose
     findings are surfaced in the board summary's coherenceWarnings list
     rather than hidden -- an empty list is the expected, healthy case.

NEVER FABRICATES: a contract discover() reports UNSUPPORTED or
MISSING_DATA keeps that exact status on the board (projectionStatus
NOT_MODELED / MISSING_DATA, with limitationReason carrying the reason).
This module never invents a probability for a family/period it cannot
support.
"""

from scripts.executable_price import get_executable_prices, executable_price_cents_to_american
from lib.kalshi_probability_adapters import STATUS_SUPPORTED, STATUS_UNSUPPORTED, STATUS_MISSING_DATA
from lib.research.market_taxonomy import (
    FAMILY_GAME_RESULT, FAMILY_INNING_RESULT, FAMILY_WINNING_MARGIN,
    FAMILY_GAME_TOTAL, FAMILY_INNING_TOTAL, FAMILY_TEAM_TOTAL, FAMILY_FIRST_INNING_RUN,
)

# Stage 1 scope, verbatim from the target-family list: full-game ML,
# F3/F5/F7 Away/Tie/Home, full-game run lines/winning margins, game and
# F3/F5/F7 inning totals at every threshold, team totals at every
# threshold, NRFI/YRFI. Pitcher/hitter prop families (already classified
# by lib.research.market_taxonomy) are deliberately excluded.
STAGE1_FAMILIES = frozenset({
    FAMILY_GAME_RESULT, FAMILY_INNING_RESULT, FAMILY_WINNING_MARGIN,
    FAMILY_GAME_TOTAL, FAMILY_INNING_TOTAL, FAMILY_TEAM_TOTAL, FAMILY_FIRST_INNING_RUN,
})

# Families whose Kalshi contract is a single two-sided ticker (YES=Over/
# YRFI, NO=Under/NRFI) -- discover() only ever prices the YES side; see
# module docstring point 2.
_COMPLEMENT_FAMILIES = frozenset({
    FAMILY_GAME_TOTAL, FAMILY_INNING_TOTAL, FAMILY_TEAM_TOTAL, FAMILY_FIRST_INNING_RUN,
})

_COMPLEMENT_SIDE = {"Over": "Under", "Yes": "No"}

# Families whose _expected_ledger_market_names() name can genuinely refer
# to more than one Kalshi ticker (an alternate-line ladder) -- for these,
# a ticker mismatch against the matched ledger row means the automated
# gate evaluated a DIFFERENT rung, and is reported as such. Families NOT
# in this set (full-game ML, F5 Away/Home, NRFI/YRFI) have exactly one
# possible ticker per ledger-row name, so a name match is authoritative
# on its own -- the ledger row's own `ticker` field can legitimately be
# None (e.g. a Rejected/Missing Data row that never resolved one) without
# that meaning "a different rung was evaluated instead."
_LADDER_LEDGER_FAMILIES = frozenset({FAMILY_WINNING_MARGIN, FAMILY_GAME_TOTAL, FAMILY_TEAM_TOTAL})

_PROJECTION_STATUS_MAP = {
    STATUS_SUPPORTED: "PROJECTED",
    STATUS_UNSUPPORTED: "NOT_MODELED",
    STATUS_MISSING_DATA: "MISSING_DATA",
}

_HORIZON_LABEL = {"full_game": "Game", "F3": "F3", "F5": "F5", "F7": "F7", "F1": "1st Inning"}

_SUM_TOLERANCE_PCT = 0.05  # percentage points -- float/rounding slack, not a real inconsistency
_MONOTONIC_TOLERANCE_PCT = 1e-6


# ── Advisory linkage to the automated recommendation engine ─────────────────

def _expected_ledger_market_names(family, period, side, subject_id, away_team, home_team):
    """
    marketLedger 'market' name(s) production would have evaluated for this
    exact (family, period, side) combination -- used ONLY to look up
    advisory status. Returns [] when this family/side/period was never in
    scripts/build_market_ledger.py's REQUIRED_MARKETS at all (e.g. F3/F7
    winners, Game_Total/team_total Under, any inning_total, alternate RL
    lines) -- that is a real, honest "the automated gate never evaluates
    this" answer, not a bug.
    """
    if family == FAMILY_GAME_RESULT and period == "full_game":
        if side == "Away":
            return ["ML_Away"]
        if side == "Home":
            return ["ML_Home"]
    if family == FAMILY_INNING_RESULT and period == "F5":
        if side == "Away":
            return ["F5_ML_Away"]
        if side == "Home":
            return ["F5_ML_Home"]
        # Tie leg: handled separately via the nested f5TieContract lookup
        # in attach_automated_recommendation, not a top-level ledger row.
    if family == FAMILY_TEAM_TOTAL and side == "Over":
        if subject_id == away_team:
            return ["TT_Away_Over"]
        if subject_id == home_team:
            return ["TT_Home_Over"]
    if family == FAMILY_GAME_TOTAL and period == "full_game" and side == "Over":
        return ["Game_Total"]
    if family == FAMILY_WINNING_MARGIN and period == "full_game":
        if subject_id == away_team:
            return ["RL_Away"]
        if subject_id == home_team:
            return ["RL_Home"]
    if family == FAMILY_FIRST_INNING_RUN:
        return ["YRFI"] if side == "Yes" else ["NRFI"]
    return []


def build_market_ledger_index(games):
    """
    Pure. Reads (never mutates) every game's already-computed
    marketLedger (scripts/build_market_ledger.py's output) into lookup
    tables for advisory matching:
      - byName:   {(gameId, marketLedgerName) -> row}
      - byTicker: {(gameId, ticker) -> row}   (first row wins on a
                   collision -- collisions are not expected since each
                   REQUIRED_MARKETS row carries a distinct ticker)
      - f5Tie:    {(gameId, tieTicker) -> f5TieContract sub-dict}
    """
    by_name, by_ticker, f5_tie = {}, {}, {}
    for g in games or []:
        gid = g.get("gameId")
        for row in (g.get("marketLedger") or []):
            name = row.get("market")
            ticker = row.get("ticker") or row.get("marketTicker")
            if name:
                by_name[(gid, name)] = row
            if ticker and (gid, ticker) not in by_ticker:
                by_ticker[(gid, ticker)] = row
            tie = row.get("f5TieContract")
            if tie and tie.get("ticker"):
                f5_tie[(gid, tie["ticker"])] = tie
    return {"byName": by_name, "byTicker": by_ticker, "f5Tie": f5_tie}


def _unmatched_advisory():
    return {
        "matched": False,
        "matchedAtSameThreshold": False,
        "automatedStatus": "NOT_GOVERNED_BY_AUTOMATED_LEDGER",
        "automatedConfidence": None,
        "automatedGatesFired": [],
        "automatedRejectionReason": None,
        "note": "no automated recommendation row exists for this market family/side/threshold",
    }


def attach_automated_recommendation(row, ledger_index):
    """
    Advisory-only linkage to scripts/build_market_ledger.py's own
    evaluation of this exact ticker -- NEVER used to filter, hide, or
    reorder board rows (requirement: a downstream PASS/PAPER/Rejected
    status must never remove a market from this board). Matching is by
    ticker, scoped to the matched game; a market present in the ledger at
    a DIFFERENT threshold (e.g. best_line moved) is reported as
    "NOT_BEST_LINE_ALTERNATE_RUNG", not silently treated as a match.
    """
    gid, ticker = row["gameId"], row["marketTicker"]
    family, period, side = row["marketFamily"], row["horizon"], row["side"]

    if family == FAMILY_INNING_RESULT and period == "F5" and side == "Tie":
        tie = ledger_index["f5Tie"].get((gid, ticker))
        if tie:
            return {
                "matched": True,
                "matchedAtSameThreshold": True,
                "automatedStatus": "PRICED_INFORMATIONAL_NOT_RECOMMENDABLE",
                "automatedConfidence": None,
                "automatedGatesFired": [],
                "automatedRejectionReason": None,
                "note": ("F5 Tie leg is priced by production but deliberately never "
                         "real-money recommendable -- see docs/F5_THREE_WAY_PRICING.md."),
            }
        return _unmatched_advisory()

    for name in _expected_ledger_market_names(family, period, side, row["subjectId"], row["awayTeam"], row["homeTeam"]):
        ledger_row = ledger_index["byName"].get((gid, name))
        if not ledger_row:
            continue
        if family in _LADDER_LEDGER_FAMILIES:
            same_ticker = (ledger_row.get("ticker") == ticker or ledger_row.get("marketTicker") == ticker)
        else:
            # Non-ladder family: exactly one possible ticker per name, so
            # the name match alone is authoritative (see
            # _LADDER_LEDGER_FAMILIES docstring above).
            same_ticker = True
        return {
            "matched": True,
            "matchedAtSameThreshold": same_ticker,
            "automatedStatus": ledger_row.get("status") if same_ticker else "NOT_BEST_LINE_ALTERNATE_RUNG",
            "automatedConfidence": ledger_row.get("confidence") if same_ticker else None,
            "automatedGatesFired": ledger_row.get("gatesFired") if same_ticker else [],
            "automatedRejectionReason": ledger_row.get("rejectionReason") if same_ticker else None,
            "note": None if same_ticker else (
                f"automated gate evaluated {name!r} at ticker {ledger_row.get('ticker')!r}, "
                f"a different rung than this board row"
            ),
        }
    return _unmatched_advisory()


# ── Display fields ───────────────────────────────────────────────────────────

def _display_threshold(family, line):
    """
    Natural sportsbook-style threshold. game_total/inning_total tickers
    encode a strict integer "over N" (see
    lib/research/market_taxonomy.py's _total_line_from_suffix) which is
    numerically identical, for integer run counts, to the conventional
    half-run "Over N.5" display -- winning_margin/team_total lines are
    already half-run values as parsed, so they pass through unchanged.
    """
    if line is None:
        return None
    if family in (FAMILY_GAME_TOTAL, FAMILY_INNING_TOTAL):
        return round(line + 0.5, 2)
    return round(line, 2)


def natural_display_label(family, period, side, subject_id, away_team, home_team, display_threshold):
    hz = _HORIZON_LABEL.get(period, period or "")
    team_for_side = away_team if side == "Away" else (home_team if side == "Home" else None)

    if family == FAMILY_GAME_RESULT:
        return f"{team_for_side or subject_id or side} Moneyline"
    if family == FAMILY_INNING_RESULT:
        if side == "Tie":
            return f"{hz} Tie"
        return f"{team_for_side or subject_id or side} {hz} Win"
    if family == FAMILY_WINNING_MARGIN:
        team = subject_id or side
        if display_threshold is not None:
            return f"{team} wins by more than {display_threshold} runs"
        return f"{team} winning margin"
    if family == FAMILY_GAME_TOTAL:
        return f"Game Total {side} {display_threshold}" if display_threshold is not None else f"Game Total {side}"
    if family == FAMILY_INNING_TOTAL:
        return f"{hz} Total {side} {display_threshold}" if display_threshold is not None else f"{hz} Total {side}"
    if family == FAMILY_TEAM_TOTAL:
        team = subject_id or "Team"
        return f"{team} Team Total {side} {display_threshold}" if display_threshold is not None else f"{team} Team Total {side}"
    if family == FAMILY_FIRST_INNING_RUN:
        return "YRFI" if side == "Yes" else "NRFI"
    return f"{family} {period} {side}"


def _prob_to_american(prob_0_1):
    if prob_0_1 is None:
        return None
    return executable_price_cents_to_american(round(prob_0_1 * 100, 4))


# ── Row construction ─────────────────────────────────────────────────────────

def _board_row_from_contract(contract, side, fair_prob, model_status, reason,
                              price_cents, mid_cents, alternate_line, ledger_index):
    family = contract["marketFamily"]
    period = contract["period"]
    subject_id = contract["subjectId"]
    away_team, home_team = contract["awayTeam"], contract["homeTeam"]
    line = contract["line"]
    display_threshold = _display_threshold(family, line)

    row = {
        "gameId": contract["gameId"],
        "marketTicker": contract["ticker"],
        "seriesTicker": contract["seriesTicker"],
        "eventTicker": contract["eventTicker"],
        "date": contract["date"],
        "awayTeam": away_team,
        "homeTeam": home_team,
        "marketFamily": family,
        "horizon": period,
        "side": side,
        "subjectId": subject_id,
        "thresholdRaw": line,
        "threshold": display_threshold,
        "displayLabel": natural_display_label(family, period, side, subject_id, away_team, home_team, display_threshold),
        "executableMarketPriceCents": price_cents,
        "marketAmericanOdds": executable_price_cents_to_american(price_cents) if price_cents is not None else None,
        "modelFairProbabilityPct": round(fair_prob * 100, 3) if fair_prob is not None else None,
        "modelFairAmericanOdds": _prob_to_american(fair_prob),
        "rawEdgePct": round(fair_prob * 100 - mid_cents, 3) if (fair_prob is not None and mid_cents is not None) else None,
        "executableEdgePct": round(fair_prob * 100 - price_cents, 3) if (fair_prob is not None and price_cents is not None) else None,
        "projectionStatus": _PROJECTION_STATUS_MAP.get(model_status, "NOT_MODELED"),
        "limitationReason": reason,
        "alternateLine": alternate_line,
        "volume": contract.get("volume"),
        "marketStatus": contract.get("marketStatus"),
        "closeTime": contract.get("closeTime"),
        "isComplementaryLeg": side in ("Under", "No"),
    }
    row["automatedRecommendation"] = attach_automated_recommendation(row, ledger_index)
    return row


def _rows_for_contract(contract, ledger_index):
    """One or two board rows for a single discover()d contract: the
    primary (production-priced) side, plus a synthesized complementary
    side for the four single-ticker two-sided families."""
    ex = get_executable_prices(contract.get("yesBid"), contract.get("yesAsk"),
                                contract.get("noBid"), contract.get("noAsk"))
    primary_side = contract.get("side")
    fair_prob = (contract["fairProbabilityPct"] / 100.0) if contract.get("fairProbabilityPct") is not None else None
    model_status = contract.get("modelSupportStatus")
    reason = contract.get("unsupportedReason")
    alternate_line = contract.get("alternateLine")

    rows = [_board_row_from_contract(
        contract, primary_side, fair_prob, model_status, reason,
        ex["yes_ask"], ex["mid"], alternate_line, ledger_index,
    )]

    family = contract.get("marketFamily")
    if family in _COMPLEMENT_FAMILIES and primary_side in _COMPLEMENT_SIDE:
        comp_side = _COMPLEMENT_SIDE[primary_side]
        comp_fair = (1.0 - fair_prob) if fair_prob is not None else None
        comp_mid = (100.0 - ex["mid"]) if ex["mid"] is not None else None
        rows.append(_board_row_from_contract(
            contract, comp_side, comp_fair, model_status, reason,
            ex["no_ask"], comp_mid, alternate_line, ledger_index,
        ))
    return rows


# ── Coherence self-checks (surfaced, never hidden) ───────────────────────────

def _run_coherence_checks(rows):
    """
    Non-fatal internal consistency checks over the finished board.
    Returns a list of human-readable warning strings -- empty in the
    healthy case. Never raises; a violation is reported for manual
    review, never silently corrected or dropped.
    """
    warnings = []

    # F3/F5/F7 Away+Tie+Home sum to 1 (100%).
    three_way = {}
    for r in rows:
        if r["marketFamily"] == FAMILY_INNING_RESULT and r["projectionStatus"] == "PROJECTED":
            three_way.setdefault((r["gameId"], r["horizon"]), {})[r["side"]] = r["modelFairProbabilityPct"]
    for (gid, horizon), sides in three_way.items():
        if {"Away", "Tie", "Home"} <= sides.keys():
            total = sum(sides.values())
            if abs(total - 100.0) > _SUM_TOLERANCE_PCT:
                warnings.append(f"{horizon} Away+Tie+Home for game {gid} sum to {total:.3f}%, not 100%")

    # Total Over probabilities decline monotonically as thresholds rise
    # (game_total and inning_total alike).
    total_groups = {}
    for r in rows:
        if (r["marketFamily"] in (FAMILY_GAME_TOTAL, FAMILY_INNING_TOTAL) and r["side"] == "Over"
                and r["projectionStatus"] == "PROJECTED" and r["threshold"] is not None):
            total_groups.setdefault((r["gameId"], r["marketFamily"], r["horizon"]), []).append(
                (r["threshold"], r["modelFairProbabilityPct"]))
    for (gid, family, horizon), points in total_groups.items():
        points.sort()
        for (t1, p1), (t2, p2) in zip(points, points[1:]):
            if t2 > t1 and p2 > p1 + _MONOTONIC_TOLERANCE_PCT:
                warnings.append(
                    f"{horizon} {family} Over probability rose from {p1:.3f}% at {t1} to "
                    f"{p2:.3f}% at {t2} for game {gid} (should decline as threshold rises)")

    # Team-total Over probabilities decline monotonically as thresholds rise.
    tt_groups = {}
    for r in rows:
        if (r["marketFamily"] == FAMILY_TEAM_TOTAL and r["side"] == "Over"
                and r["projectionStatus"] == "PROJECTED" and r["threshold"] is not None):
            tt_groups.setdefault((r["gameId"], r["subjectId"]), []).append(
                (r["threshold"], r["modelFairProbabilityPct"]))
    for (gid, team), points in tt_groups.items():
        points.sort()
        for (t1, p1), (t2, p2) in zip(points, points[1:]):
            if t2 > t1 and p2 > p1 + _MONOTONIC_TOLERANCE_PCT:
                warnings.append(
                    f"team total Over probability rose from {p1:.3f}% at {t1} to {p2:.3f}% at "
                    f"{t2} for {team} in game {gid} (should decline as threshold rises)")

    # Complementary YES/NO sides of the same ticker sum to 1 (100%).
    ticker_sides = {}
    for r in rows:
        if r["marketFamily"] in _COMPLEMENT_FAMILIES and r["projectionStatus"] == "PROJECTED":
            ticker_sides.setdefault(r["marketTicker"], {})[r["side"]] = r["modelFairProbabilityPct"]
    for ticker, sides in ticker_sides.items():
        if len(sides) == 2:
            total = sum(sides.values())
            if abs(total - 100.0) > _SUM_TOLERANCE_PCT:
                warnings.append(f"complementary YES/NO sides for ticker {ticker} sum to {total:.3f}%, not 100%")

    return warnings


def _count_by(rows, key):
    counts = {}
    for r in rows:
        counts[r[key]] = counts.get(r[key], 0) + 1
    return counts


# ── Top-level entry point ────────────────────────────────────────────────────

def build_projection_board(date_str, contracts, games):
    """
    Pure. Builds the Stage 1 pre-gate full-market projection board from
    already-discovered contracts (scripts.discover_kalshi_mlb_markets.
    discover()'s output) and the slate's games (for marketLedger advisory
    linkage). Returns (rows, summary) -- never raises, never drops a
    contract silently: every Stage-1-family contract produces at least
    one row, regardless of projectionStatus or automated-gate outcome.
    """
    ledger_index = build_market_ledger_index(games)
    rows = []
    for c in contracts:
        if c.get("marketFamily") not in STAGE1_FAMILIES:
            continue
        rows.extend(_rows_for_contract(c, ledger_index))

    summary = {
        "date": date_str,
        "totalRows": len(rows),
        "projected": sum(1 for r in rows if r["projectionStatus"] == "PROJECTED"),
        "notModeled": sum(1 for r in rows if r["projectionStatus"] == "NOT_MODELED"),
        "missingData": sum(1 for r in rows if r["projectionStatus"] == "MISSING_DATA"),
        "byMarketFamily": _count_by(rows, "marketFamily"),
        "coherenceWarnings": _run_coherence_checks(rows),
    }
    return rows, summary
