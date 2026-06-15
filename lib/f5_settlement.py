#!/usr/bin/env python3
"""
lib/f5_settlement.py
=====================
F5 ML Settlement Logic

Settlement source hierarchy:
  1. PRIMARY: MLB linescore API /api/v1/game/{gamePk}/linescore — sum innings 1-5
  2. FALLBACK: Final boxscore (cross-check only)
  3. NEVER: Raw RBI event summation (unless linescore unavailable, with explicit flag)

Tie handling:
  - Away wins F5 ML ONLY if away F5 score > home F5 score
  - Home wins F5 ML ONLY if home > away
  - Tie (equal after 5 innings): F5 ML = LOSS for both sides
    (unless Kalshi contract explicitly says refund — which is non-standard)

Regressions addressed:
  - NYY@TOR June 14: F5 score 2-2 → NYY F5 ML Away = LOSS (correct)
  - TB@LAA June 14: RBI discrepancy → linescore must override raw RBI
"""

import json
from typing import Optional, Tuple


class F5SettlementError(Exception):
    """Raised when F5 settlement cannot be completed reliably."""
    pass


# ── Settlement result constants ───────────────────────────────────────────────
F5_RESULT_WIN  = "WIN"
F5_RESULT_LOSS = "LOSS"
F5_RESULT_PUSH = "PUSH"       # Only when contract explicitly refunds ties
F5_RESULT_VOID = "VOID"       # Game incomplete (< 5 innings)
F5_RESULT_PENDING = "PENDING"  # Insufficient data to settle


def extract_f5_score_from_linescore(linescore_data) -> Tuple[Optional[int], Optional[int]]:
    """
    Extract F5 scores from MLB linescore API response.

    Args:
        linescore_data: dict from /api/v1/game/{gamePk}/linescore

    Returns:
        (away_f5_score, home_f5_score) or (None, None) if insufficient data
    """
    innings = linescore_data.get("innings", [])
    if not innings:
        return None, None

    # Innings are 1-indexed in the API response
    # We want innings 1-5
    away_runs = 0
    home_runs = 0
    max_inning = 0

    for inning in innings:
        inning_num = inning.get("num") or inning.get("ordinalNum")
        if inning_num is None:
            continue

        try:
            num = int(inning_num)
        except (TypeError, ValueError):
            continue

        if num > 5:
            continue  # Only count innings 1-5
        if num > max_inning:
            max_inning = num

        away = inning.get("away", {})
        home = inning.get("home", {})

        away_r = away.get("runs")
        home_r = home.get("runs")

        if away_r is not None:
            try:
                away_runs += int(away_r)
            except (TypeError, ValueError):
                pass

        if home_r is not None:
            try:
                home_runs += int(home_r)
            except (TypeError, ValueError):
                pass

    if max_inning < 5:
        # Fewer than 5 innings completed
        return None, None

    return away_runs, home_runs


def settle_f5_ml(
    away_f5_score: int,
    home_f5_score: int,
    bet_side: str,
    kalshi_refunds_ties: bool = False,
    source: str = "linescore",
) -> dict:
    """
    Settle a F5 ML bet.

    Args:
        away_f5_score: Away team runs after 5 innings
        home_f5_score: Home team runs after 5 innings
        bet_side: "away" or "home"
        kalshi_refunds_ties: if True, treat tie as PUSH (non-standard)
        source: data source used for settlement

    Returns:
        dict with:
            result: WIN | LOSS | PUSH | VOID
            awayF5: int
            homeF5: int
            betSide: str
            isTie: bool
            source: str
            notes: str
    """
    is_tie = (away_f5_score == home_f5_score)
    side = bet_side.lower()

    result_notes = f"F5 score: Away={away_f5_score}, Home={home_f5_score}. Source: {source}."

    if is_tie:
        if kalshi_refunds_ties:
            result = F5_RESULT_PUSH
            notes = f"{result_notes} TIE — Kalshi contract explicitly refunds ties → PUSH."
        else:
            # Standard: tie = LOSS for F5 ML
            result = F5_RESULT_LOSS
            notes = f"{result_notes} TIE after 5 innings → F5 ML = LOSS (standard Kalshi rule)."
    elif side == "away":
        if away_f5_score > home_f5_score:
            result = F5_RESULT_WIN
        else:
            result = F5_RESULT_LOSS
        notes = result_notes
    elif side == "home":
        if home_f5_score > away_f5_score:
            result = F5_RESULT_WIN
        else:
            result = F5_RESULT_LOSS
        notes = result_notes
    else:
        raise F5SettlementError(f"Unknown bet_side: {bet_side}. Must be 'away' or 'home'.")

    return {
        "result": result,
        "awayF5": away_f5_score,
        "homeF5": home_f5_score,
        "betSide": bet_side,
        "isTie": is_tie,
        "source": source,
        "notes": notes,
        "fallbackUsed": source != "linescore",
    }


def settle_f5_from_linescore_api(linescore_data, bet_side: str, kalshi_refunds_ties: bool = False) -> dict:
    """
    Settle F5 ML using linescore API data (primary source).

    Returns settlement dict or raises F5SettlementError if insufficient data.
    """
    away_score, home_score = extract_f5_score_from_linescore(linescore_data)

    if away_score is None or home_score is None:
        raise F5SettlementError(
            "Linescore does not contain 5 complete innings — cannot settle F5 ML. "
            "Game may be incomplete."
        )

    return settle_f5_ml(
        away_f5_score=away_score,
        home_f5_score=home_score,
        bet_side=bet_side,
        kalshi_refunds_ties=kalshi_refunds_ties,
        source="linescore",
    )


def settle_f5_from_boxscore_fallback(
    away_f5_score: int,
    home_f5_score: int,
    bet_side: str,
    fallback_reason: str = "linescore_unavailable",
    kalshi_refunds_ties: bool = False,
) -> dict:
    """
    Settle F5 ML from boxscore data (fallback only).
    Always flags fallbackUsed=True.

    This should only be called when linescore API is unavailable.
    """
    result = settle_f5_ml(
        away_f5_score=away_f5_score,
        home_f5_score=home_f5_score,
        bet_side=bet_side,
        kalshi_refunds_ties=kalshi_refunds_ties,
        source=f"boxscore_fallback:{fallback_reason}",
    )
    result["fallbackUsed"] = True
    result["fallbackReason"] = fallback_reason
    result["notes"] = (
        f"[FALLBACK USED: {fallback_reason}] " + result.get("notes", "")
    )
    return result


def validate_rbi_vs_linescore(rbi_away: int, rbi_home: int, linescore_away: int, linescore_home: int) -> dict:
    """
    Cross-check RBI event totals against linescore run totals.
    Linescore ALWAYS wins in case of discrepancy.

    Returns dict with:
        match: bool
        linescoreAway: int
        linescoreHome: int
        rbiAway: int
        rbiHome: int
        discrepancy: bool
        notes: str
    """
    match = (rbi_away == linescore_away and rbi_home == linescore_home)
    discrepancy = not match

    notes = "RBI and linescore agree." if match else (
        f"DISCREPANCY: RBI Away={rbi_away} vs Linescore Away={linescore_away}; "
        f"RBI Home={rbi_home} vs Linescore Home={linescore_home}. "
        f"Linescore values used for settlement."
    )

    return {
        "match": match,
        "linescoreAway": linescore_away,
        "linescoreHome": linescore_home,
        "rbiAway": rbi_away,
        "rbiHome": rbi_home,
        "discrepancy": discrepancy,
        "notes": notes,
    }


if __name__ == "__main__":
    # Regression test: NYY@TOR June 14 — F5 tied 2-2 → NYY Away = LOSS
    test_linescore = {
        "innings": [
            {"num": 1, "away": {"runs": 0}, "home": {"runs": 1}},
            {"num": 2, "away": {"runs": 1}, "home": {"runs": 0}},
            {"num": 3, "away": {"runs": 1}, "home": {"runs": 0}},
            {"num": 4, "away": {"runs": 0}, "home": {"runs": 1}},
            {"num": 5, "away": {"runs": 0}, "home": {"runs": 0}},
        ]
    }
    result = settle_f5_from_linescore_api(test_linescore, bet_side="away")
    print("NYY@TOR F5 settlement:")
    print(json.dumps(result, indent=2))
    assert result["result"] == "LOSS", f"Expected LOSS, got {result['result']}"
    assert result["isTie"] == True
    print("✓ Tie correctly graded as LOSS")
