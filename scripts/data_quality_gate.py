#!/usr/bin/env python3
"""
scripts/data_quality_gate.py
==============================
DATA_QUALITY_GATE — sits between edge calculation and final bet classification.

Computes a dataQualityStatus for each candidate bet. Does NOT change model
probabilities, thresholds, staking, edge formulas, or calibration.

Statuses:
  OK_REAL_ELIGIBLE        — all required inputs current, complete, validated
  PAPER_ONLY_DATA_WARNING — data incomplete but not stale
  REJECT_DATA_MISSING     — required data missing
  REJECT_STALE_DATE       — stale date detected
  REJECT_TICKER_MISMATCH  — ticker doesn't map to requested game
  REJECT_PITCHER_MISMATCH — pitcher identity not verified
  REJECT_ODDS_MISSING     — required odds source missing
  REJECT_LINEUP_RULE      — lineup status violates production rules
  REJECT_BULLPEN_DATA_MISSING — bullpen/pitcher data missing for dependent market

Rules:
  1. Stale date → block ALL bets (no paper, no real, no output)
  2. Ticker/date mapping fails → REJECT_TICKER_MISMATCH (never real-money)
  3. Pitcher mismatch → REJECT_PITCHER_MISMATCH (never real-money unless verified)
  4. Required odds source missing → REJECT_ODDS_MISSING or paper per existing rules
  5. Lineup violation → REJECT_LINEUP_RULE
  6. Bullpen/pitcher data missing for dependent market → REJECT_BULLPEN_DATA_MISSING

No candidate can be real-money without:
  - requested slate date
  - game date
  - exact matchup
  - market type
  - exact ticker
  - entry price
  - source timestamp
  - validation status
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

# ── Constants ─────────────────────────────────────────────────────────────────
ET = timezone(timedelta(hours=-4))

STATUSES = {
    "OK_REAL_ELIGIBLE",
    "PAPER_ONLY_DATA_WARNING",
    "REJECT_DATA_MISSING",
    "REJECT_STALE_DATE",
    "REJECT_TICKER_MISMATCH",
    "REJECT_PITCHER_MISMATCH",
    "REJECT_ODDS_MISSING",
    "REJECT_LINEUP_RULE",
    "REJECT_BULLPEN_DATA_MISSING",
}

# Markets that require bullpen/pitcher data
BULLPEN_DEPENDENT_MARKETS = {
    "NRFI", "YRFI", "F5_ML_Away", "F5_ML_Home",
    "F5 ML", "NRFI/YRFI", "nrfi_yrfi", "f5_moneyline",
}


def _parse_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return None


def _kalshi_date_from_requested(requested_date):
    """Convert YYYY-MM-DD to Kalshi ticker date like 26JUN13."""
    months = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
    try:
        d = datetime.strptime(requested_date, "%Y-%m-%d")
        return str(d.year)[2:] + months[d.month - 1] + str(d.day).zfill(2)
    except Exception:
        return None


def check_ticker_date(ticker, requested_date):
    """
    Return True if ticker's embedded date matches requested_date.
    Returns False if mismatch or unparseable.
    """
    if not ticker or not requested_date:
        return False
    expected = _kalshi_date_from_requested(requested_date)
    if not expected:
        return False
    return expected in ticker


def classify_bet(
    candidate,
    requested_date,
    slate_date=None,
    fetch_status=None,
    away_abbr=None,
    home_abbr=None,
    away_pitcher_name=None,
    home_pitcher_name=None,
    has_pitcher_savant=None,
    lineup_confirmed=None,
    has_bullpen_data=None,
    kalshi_ticker=None,
    entry_price=None,
    source_timestamp=None,
    validated_matchup=None,
):
    """
    Classify a candidate bet according to the data quality gate.

    Parameters
    ----------
    candidate : dict
        The candidate bet dict (must have at minimum 'market', 'betSide' or similar)
    requested_date : str
        The YYYY-MM-DD date this model run was requested for
    slate_date : str
        The date field from data/slate.json
    fetch_status : str
        The status field from data/fetch_status.json (e.g. "OK", "FAILED_STALE_DATE")
    away_abbr, home_abbr : str
        Team abbreviations for this game
    away_pitcher_name, home_pitcher_name : str or None
        Confirmed probable pitcher names; None = TBD/unverified
    has_pitcher_savant : bool or None
        Whether pitcherSavant data is present for the relevant side
    lineup_confirmed : bool or None
        Whether lineups have been confirmed for this game
    has_bullpen_data : bool or None
        Whether bullpen data is available for bullpen-dependent markets
    kalshi_ticker : str or None
        The exact Kalshi market ticker for this bet
    entry_price : float or None
        The bet entry price
    source_timestamp : str or None
        ISO timestamp of when the market data was fetched
    validated_matchup : bool or None
        Whether the game matchup has been validated against the registry

    Returns
    -------
    dict with keys:
        dataQualityStatus : str  (one of STATUSES)
        reason : str             (human-readable explanation)
        realMoneyEligible : bool (False for any REJECT_* status)
        paperEligible : bool     (False only for REJECT_STALE_DATE)
        requiredFieldsMissing : list[str]
    """
    reasons = []
    missing_fields = []
    status = "OK_REAL_ELIGIBLE"

    market = candidate.get("market", "") or ""
    bet_side = candidate.get("betSide", "") or candidate.get("bet", "") or ""

    # Resolve explicit params from candidate dict if not provided as explicit args
    # (allows calling classify_bet(candidate, date) without all explicit params)
    if away_abbr is None:
        away_abbr = candidate.get("awayAbbr") or candidate.get("away")
    if home_abbr is None:
        home_abbr = candidate.get("homeAbbr") or candidate.get("home")
    if away_pitcher_name is None:
        away_pitcher_name = candidate.get("awayPitcher")
    if home_pitcher_name is None:
        home_pitcher_name = candidate.get("homePitcher")
    if has_pitcher_savant is None:
        has_pitcher_savant = candidate.get("hasPitcherSavant")
    if lineup_confirmed is None:
        lineup_confirmed = candidate.get("lineupConfirmed")
    if has_bullpen_data is None:
        has_bullpen_data = candidate.get("hasBullpenData")
    if kalshi_ticker is None:
        kalshi_ticker = candidate.get("ticker") or candidate.get("kalshiTicker")
    if entry_price is None:
        entry_price = candidate.get("price") or candidate.get("entryPrice")
    if source_timestamp is None:
        source_timestamp = candidate.get("sourceTimestamp") or candidate.get("fetchedAt")
    if validated_matchup is None:
        validated_matchup = candidate.get("validatedMatchup")

    # ── Rule 1: Stale date → block ALL bets ───────────────────────────────────
    if fetch_status and fetch_status not in ("OK", None):
        return {
            "dataQualityStatus": "REJECT_STALE_DATE",
            "reason": f"fetch_status={fetch_status!r} is not OK — stale date blocks all bets",
            "realMoneyEligible": False,
            "paperEligible": False,
            "requiredFieldsMissing": ["fetch_status=OK"],
        }

    if slate_date and requested_date and slate_date != requested_date:
        return {
            "dataQualityStatus": "REJECT_STALE_DATE",
            "reason": (
                f"slate_date={slate_date!r} != requested_date={requested_date!r} "
                f"— stale date blocks all bets (paper and real)"
            ),
            "realMoneyEligible": False,
            "paperEligible": False,
            "requiredFieldsMissing": ["matching slate date"],
        }

    # ── Rule 2: Ticker/date mapping ───────────────────────────────────────────
    if kalshi_ticker and requested_date:
        if not check_ticker_date(kalshi_ticker, requested_date):
            return {
                "dataQualityStatus": "REJECT_TICKER_MISMATCH",
                "reason": (
                    f"Kalshi ticker {kalshi_ticker!r} does not contain "
                    f"the date component for {requested_date} — "
                    f"ticker does not map to requested game"
                ),
                "realMoneyEligible": False,
                "paperEligible": True,
                "requiredFieldsMissing": ["ticker date match"],
            }

    if validated_matchup is False:
        status = "REJECT_TICKER_MISMATCH"
        reasons.append("Matchup validation against registry failed")

    # ── Rule 3: Pitcher mismatch (before generic DATA_MISSING check) ──────────
    # Markets that require confirmed pitcher NAME and Savant data
    # (NRFI/YRFI only need bullpen data, not named starters)
    pitcher_name_dependent = any(
        kw in market.lower() or kw in bet_side.lower()
        for kw in ["k prop", "strikeout", "er", "earned run", "f5"]
    )
    if pitcher_name_dependent:
        if away_pitcher_name is None or home_pitcher_name is None:
            if status == "OK_REAL_ELIGIBLE":
                status = "REJECT_PITCHER_MISMATCH"
            reasons.append("Pitcher identity not confirmed for pitcher-dependent market")
        if has_pitcher_savant is False:
            if status == "OK_REAL_ELIGIBLE":
                status = "REJECT_PITCHER_MISMATCH"
            reasons.append("pitcherSavant data missing for pitcher-dependent market")

    # ── Rule 6: Bullpen data for dependent markets (before DATA_MISSING) ─────
    if market in BULLPEN_DEPENDENT_MARKETS or any(m in market.lower() for m in ["nrfi", "yrfi", "f5"]):
        if has_bullpen_data is False:
            if status == "OK_REAL_ELIGIBLE":
                status = "REJECT_BULLPEN_DATA_MISSING"
            reasons.append(
                f"Bullpen/pitcher data missing for {market} market — "
                "cannot evaluate first-inning or first-5 projection"
            )

    # ── Rule 4: Required odds source ─────────────────────────────────────────
    if entry_price is None:
        if status == "OK_REAL_ELIGIBLE":
            status = "REJECT_ODDS_MISSING"
        reasons.append("Entry price (odds) missing")

    # ── Rule 5: Lineup ────────────────────────────────────────────────────────
    if lineup_confirmed is False:
        if status == "OK_REAL_ELIGIBLE":
            status = "PAPER_ONLY_DATA_WARNING"
        reasons.append("Lineup not yet confirmed — paper only until lineups post")

    # ── Required fields (checked last so specific statuses are set first) ─────
    if not requested_date:
        missing_fields.append("requestedDate")
    if not kalshi_ticker:
        missing_fields.append("kalshiTicker")
    if entry_price is None:
        missing_fields.append("entryPrice")
    if not source_timestamp:
        missing_fields.append("sourceTimestamp")
    if not away_abbr or not home_abbr:
        missing_fields.append("teamMatchup")
    if not market:
        missing_fields.append("marketType")

    if missing_fields:
        if status == "OK_REAL_ELIGIBLE":
            status = "REJECT_DATA_MISSING"
        reasons.append(f"Missing required fields: {missing_fields}")

    # ── Compute eligibility ───────────────────────────────────────────────────
    real_eligible = status == "OK_REAL_ELIGIBLE"
    paper_eligible = status not in ("REJECT_STALE_DATE",)

    return {
        "dataQualityStatus": status,
        "reason": "; ".join(reasons) if reasons else "All data quality checks passed",
        "realMoneyEligible": real_eligible,
        "paperEligible": paper_eligible,
        "requiredFieldsMissing": missing_fields,
    }


def classify_all_bets(candidates, requested_date, slate_date=None, fetch_status_str=None):
    """
    Run the data quality gate on a list of candidate bets.

    Parameters
    ----------
    candidates : list[dict]
        Each dict is a candidate bet, expected to have fields like:
        market, betSide, kalshiTicker, entryPrice, awayAbbr, homeAbbr, etc.
    requested_date : str
        YYYY-MM-DD
    slate_date : str or None
        Date from slate.json
    fetch_status_str : str or None
        Status string from fetch_status.json

    Returns
    -------
    list[dict]  — candidates with 'dataQualityStatus', 'realMoneyEligible', etc. added
    """
    results = []
    for c in candidates:
        gate_result = classify_bet(
            candidate=c,
            requested_date=requested_date,
            slate_date=slate_date,
            fetch_status=fetch_status_str,
            away_abbr=c.get("awayAbbr") or c.get("away"),
            home_abbr=c.get("homeAbbr") or c.get("home"),
            away_pitcher_name=c.get("awayPitcher"),
            home_pitcher_name=c.get("homePitcher"),
            has_pitcher_savant=c.get("hasPitcherSavant"),
            lineup_confirmed=c.get("lineupConfirmed"),
            has_bullpen_data=c.get("hasBullpenData"),
            kalshi_ticker=c.get("ticker") or c.get("kalshiTicker"),
            entry_price=c.get("price") or c.get("entryPrice"),
            source_timestamp=c.get("sourceTimestamp") or c.get("fetchedAt"),
            validated_matchup=c.get("validatedMatchup"),
        )
        merged = dict(c)
        merged.update(gate_result)
        results.append(merged)
    return results


def abort_if_stale(requested_date, slate_date=None, fetch_status_str=None):
    """
    Abort immediately if stale-date conditions are detected.
    Call this before any bet-writing or model output.
    """
    if fetch_status_str and fetch_status_str not in ("OK", None):
        print(
            f"STALE SLATE ABORT: requested={requested_date} "
            f"actual={fetch_status_str} source=data/fetch_status.json",
            file=sys.stderr
        )
        sys.exit(1)

    if slate_date and requested_date and slate_date != requested_date:
        print(
            f"STALE SLATE ABORT: requested={requested_date} "
            f"actual={slate_date} source=data/slate.json",
            file=sys.stderr
        )
        sys.exit(1)


if __name__ == "__main__":
    # Quick self-test
    print("data_quality_gate self-test:")

    # Test 1: OK case
    c1 = {
        "market": "ML",
        "betSide": "AWAY",
        "awayAbbr": "NYY",
        "homeAbbr": "BOS",
        "ticker": "KXMLBGAME-26JUN131905NYYBOS-NYY",
        "price": -120,
        "sourceTimestamp": "2026-06-13T18:00:00Z",
        "lineupConfirmed": True,
    }
    r1 = classify_bet(c1, "2026-06-13", slate_date="2026-06-13")
    print(f"  Test 1 (valid ML): {r1['dataQualityStatus']} — {r1['reason']}")

    # Test 2: stale ticker
    c2 = dict(c1)
    c2["ticker"] = "KXMLBGAME-26JUN121905NYYBOS-NYY"  # June 12 ticker
    r2 = classify_bet(c2, "2026-06-13", slate_date="2026-06-13")
    print(f"  Test 2 (stale ticker): {r2['dataQualityStatus']} — {r2['reason']}")

    # Test 3: stale slate
    c3 = dict(c1)
    r3 = classify_bet(c3, "2026-06-13", slate_date="2026-06-12")
    print(f"  Test 3 (stale slate): {r3['dataQualityStatus']} — {r3['reason']}")

    print("Self-test done.")
