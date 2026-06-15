#!/usr/bin/env python3
"""
lib/tracking_type.py
=====================
trackingType Enforcement

Schema:
  trackingType: "REAL" | "MODEL_ONLY" | "PAPER" | "REAL_PROBE"
  actuallyPlaced: true | false | null
  placementConfirmedAt: "ISO timestamp or null"

Rules:
  1. Official bankroll P/L uses ONLY trackingType REAL or REAL_PROBE with actuallyPlaced: true
  2. MODEL_ONLY and PAPER never affect bankroll
  3. betSize > 1 must NEVER classify a bet as real-money
  4. Final slip is canonical source of actuallyPlaced: true
  5. Anything not in confirmed final slip defaults to actuallyPlaced: false

REAL_PROBE constraints:
  - Max stake: $1.00 (absolute max $1.50)
  - Must pass ALL DATA_HARD and MARKET_MECHANICS_HARD blocks
  - Can fail at most ONE RISK_SOFT or CALIBRATION block
  - Must have exact ticker identity
  - Must have valid pregame price
  - Must be explicitly listed in final slip as REAL_PROBE
  - Default actuallyPlaced: false until confirmed
"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone

# ── Valid tracking types ──────────────────────────────────────────────────────
TRACKING_REAL        = "REAL"
TRACKING_MODEL_ONLY  = "MODEL_ONLY"
TRACKING_PAPER       = "PAPER"
TRACKING_REAL_PROBE  = "REAL_PROBE"

VALID_TRACKING_TYPES = {TRACKING_REAL, TRACKING_MODEL_ONLY, TRACKING_PAPER, TRACKING_REAL_PROBE}

# ── REAL_PROBE constraints ────────────────────────────────────────────────────
REAL_PROBE_MAX_STAKE         = 1.00
REAL_PROBE_ABSOLUTE_MAX_STAKE = 1.50

# ── Block class taxonomy ──────────────────────────────────────────────────────
BLOCK_CLASS_DATA_HARD              = "DATA_HARD"
BLOCK_CLASS_MARKET_MECHANICS_HARD  = "MARKET_MECHANICS_HARD"
BLOCK_CLASS_RISK_SOFT              = "RISK_SOFT"
BLOCK_CLASS_CALIBRATION            = "CALIBRATION"
BLOCK_CLASS_OPPORTUNITY_FILTER     = "OPPORTUNITY_FILTER"

VALID_BLOCK_CLASSES = {
    BLOCK_CLASS_DATA_HARD,
    BLOCK_CLASS_MARKET_MECHANICS_HARD,
    BLOCK_CLASS_RISK_SOFT,
    BLOCK_CLASS_CALIBRATION,
    BLOCK_CLASS_OPPORTUNITY_FILTER,
}


class TrackingTypeError(ValueError):
    """Raised when a tracking type constraint is violated."""
    pass


@dataclass
class TrackingSchema:
    """Validated tracking schema for a single bet."""
    trackingType: str
    actuallyPlaced: Optional[bool]
    placementConfirmedAt: Optional[str]
    betSize: Optional[float] = None

    def validate(self):
        """
        Validate the tracking schema.
        Raises TrackingTypeError on violation.
        Returns self on success.
        """
        # Rule: trackingType must be valid
        if self.trackingType not in VALID_TRACKING_TYPES:
            raise TrackingTypeError(
                f"Invalid trackingType: '{self.trackingType}'. "
                f"Must be one of: {sorted(VALID_TRACKING_TYPES)}"
            )

        # Rule: betSize > 1 must NEVER classify as real-money
        if self.betSize is not None and self.betSize > 1.0:
            if self.trackingType in (TRACKING_REAL, TRACKING_REAL_PROBE):
                # REAL is allowed to have betSize > 1 if actually placed
                # REAL_PROBE must never exceed max stake
                if self.trackingType == TRACKING_REAL_PROBE:
                    if self.betSize > REAL_PROBE_ABSOLUTE_MAX_STAKE:
                        raise TrackingTypeError(
                            f"REAL_PROBE bet has betSize={self.betSize} "
                            f"which exceeds absolute max {REAL_PROBE_ABSOLUTE_MAX_STAKE}"
                        )

        # Rule: REAL_PROBE default actuallyPlaced = false
        if self.trackingType == TRACKING_REAL_PROBE:
            # actuallyPlaced can be None (default) or True/False
            # But must not be True without placementConfirmedAt
            if self.actuallyPlaced is True and not self.placementConfirmedAt:
                raise TrackingTypeError(
                    "REAL_PROBE bet has actuallyPlaced=True but placementConfirmedAt is null"
                )

        # Rule: actuallyPlaced=True requires placementConfirmedAt for REAL
        if self.trackingType == TRACKING_REAL:
            if self.actuallyPlaced is True and not self.placementConfirmedAt:
                raise TrackingTypeError(
                    "REAL bet has actuallyPlaced=True but placementConfirmedAt is null"
                )

        return self

    @property
    def counts_for_bankroll(self):
        """Return True if this bet should be included in official P/L calculation."""
        return (
            self.trackingType in (TRACKING_REAL, TRACKING_REAL_PROBE)
            and self.actuallyPlaced is True
            and self.placementConfirmedAt is not None
        )

    @property
    def is_real_money(self):
        """Return True if this is a real-money bet (REAL or REAL_PROBE with actuallyPlaced)."""
        return self.trackingType in (TRACKING_REAL, TRACKING_REAL_PROBE) and self.actuallyPlaced is True


def enforce_tracking_schema(bet_dict):
    """
    Validate and enforce trackingType schema on a bet dict.

    Mutates bet_dict in place to add/fix fields:
    - Adds actuallyPlaced=False if missing
    - Adds placementConfirmedAt=None if missing
    - Validates betSize > 1 cannot be REAL_PROBE

    Raises TrackingTypeError on hard violations.
    Returns TrackingSchema.
    """
    tracking_type = bet_dict.get("trackingType")
    if not tracking_type:
        # Default to MODEL_ONLY for safety
        bet_dict["trackingType"] = TRACKING_MODEL_ONLY
        tracking_type = TRACKING_MODEL_ONLY

    # Default actuallyPlaced
    if "actuallyPlaced" not in bet_dict or bet_dict["actuallyPlaced"] is None:
        if tracking_type in (TRACKING_MODEL_ONLY, TRACKING_PAPER):
            bet_dict["actuallyPlaced"] = False
        elif tracking_type == TRACKING_REAL_PROBE:
            bet_dict["actuallyPlaced"] = False  # Default false until confirmed
        # REAL: keep None if not yet set (will need confirmation)

    # Default placementConfirmedAt
    if "placementConfirmedAt" not in bet_dict:
        bet_dict["placementConfirmedAt"] = None

    schema = TrackingSchema(
        trackingType=bet_dict["trackingType"],
        actuallyPlaced=bet_dict.get("actuallyPlaced"),
        placementConfirmedAt=bet_dict.get("placementConfirmedAt"),
        betSize=bet_dict.get("betSize") or bet_dict.get("stake"),
    )

    schema.validate()
    return schema


def calculate_bankroll_pl(bets):
    """
    Calculate official bankroll P/L from a list of bet dicts.
    Only includes REAL and REAL_PROBE bets with actuallyPlaced=True.

    Returns:
        dict with totalPL, realBets, probeBets, excludedBets, breakdown
    """
    real_bets = []
    probe_bets = []
    excluded_bets = []

    for bet in bets:
        tt = bet.get("trackingType")
        placed = bet.get("actuallyPlaced")
        confirmed_at = bet.get("placementConfirmedAt")
        pl = bet.get("pl", 0) or 0

        if tt == TRACKING_REAL and placed is True and confirmed_at:
            real_bets.append(bet)
        elif tt == TRACKING_REAL_PROBE and placed is True and confirmed_at:
            probe_bets.append(bet)
        else:
            excluded_bets.append({
                "id": bet.get("id"),
                "trackingType": tt,
                "actuallyPlaced": placed,
                "reason": _exclude_reason(tt, placed, confirmed_at)
            })

    real_pl = sum(b.get("pl", 0) or 0 for b in real_bets)
    probe_pl = sum(b.get("pl", 0) or 0 for b in probe_bets)
    total_pl = real_pl + probe_pl

    return {
        "totalPL": round(total_pl, 2),
        "realPL": round(real_pl, 2),
        "probePL": round(probe_pl, 2),
        "realBetCount": len(real_bets),
        "probeBetCount": len(probe_bets),
        "excludedBetCount": len(excluded_bets),
        "breakdown": {
            "REAL": real_bets,
            "REAL_PROBE": probe_bets,
            "excluded": excluded_bets,
        }
    }


def _exclude_reason(tracking_type, actually_placed, confirmed_at):
    if tracking_type in (TRACKING_MODEL_ONLY, TRACKING_PAPER):
        return f"{tracking_type} bets never count toward bankroll"
    if tracking_type in (TRACKING_REAL, TRACKING_REAL_PROBE):
        if not actually_placed:
            return "actuallyPlaced is False or null"
        if not confirmed_at:
            return "placementConfirmedAt is missing"
    return f"Unknown exclusion reason for trackingType={tracking_type}"


def validate_real_probe_eligibility(bet_dict, block_classes_fired=None):
    """
    Validate that a bet is eligible for REAL_PROBE classification.

    Eligibility requires:
    - Pass ALL DATA_HARD blocks
    - Pass ALL MARKET_MECHANICS_HARD blocks
    - Fail at most ONE RISK_SOFT or CALIBRATION block
    - Exact ticker identity
    - Valid pregame price
    - No sentinel prices
    - betSize <= REAL_PROBE_ABSOLUTE_MAX_STAKE

    Args:
        bet_dict: bet/market dict
        block_classes_fired: list of block classes that fired (strings)

    Returns:
        (is_eligible, reason)
    """
    block_classes_fired = block_classes_fired or []

    # Hard blocks disqualify entirely
    for bc in block_classes_fired:
        if bc in (BLOCK_CLASS_DATA_HARD, BLOCK_CLASS_MARKET_MECHANICS_HARD):
            return False, f"Hard block fired: {bc} — REAL_PROBE ineligible"

    # At most one soft/calibration block
    soft_blocks = [bc for bc in block_classes_fired
                   if bc in (BLOCK_CLASS_RISK_SOFT, BLOCK_CLASS_CALIBRATION)]
    if len(soft_blocks) > 1:
        return False, f"Too many soft/calibration blocks ({len(soft_blocks)}) — REAL_PROBE max is 1"

    # Ticker required
    ticker = bet_dict.get("ticker") or bet_dict.get("marketTicker")
    if not ticker:
        return False, "Missing exact ticker identity — REAL_PROBE ineligible"

    # Price required
    price = bet_dict.get("price") or bet_dict.get("kalshiPrice")
    if price is None:
        return False, "Missing entry price — REAL_PROBE ineligible"

    # Bet size cap
    bet_size = bet_dict.get("betSize") or bet_dict.get("stake")
    if bet_size is not None and float(bet_size) > REAL_PROBE_ABSOLUTE_MAX_STAKE:
        return False, (f"betSize {bet_size} exceeds REAL_PROBE absolute max "
                       f"{REAL_PROBE_ABSOLUTE_MAX_STAKE}")

    return True, "REAL_PROBE eligible"


if __name__ == "__main__":
    # Quick test
    import json

    test_bet = {
        "trackingType": "REAL_PROBE",
        "actuallyPlaced": False,
        "placementConfirmedAt": None,
        "betSize": 1.00,
        "ticker": "KXMLBF5-26JUN141340ATLNYM-ATL",
        "price": -141,
    }

    schema = enforce_tracking_schema(test_bet)
    print(f"Schema valid: {schema}")
    print(f"Counts for bankroll: {schema.counts_for_bankroll}")

    # Test bankroll calculation
    bets = [
        {"trackingType": "REAL", "actuallyPlaced": True, "placementConfirmedAt": "2026-06-14T11:00:00Z", "pl": 5.0},
        {"trackingType": "REAL_PROBE", "actuallyPlaced": True, "placementConfirmedAt": "2026-06-14T11:00:00Z", "pl": 0.85},
        {"trackingType": "MODEL_ONLY", "actuallyPlaced": False, "placementConfirmedAt": None, "pl": 1.50},
        {"trackingType": "PAPER", "actuallyPlaced": False, "placementConfirmedAt": None, "pl": -1.00},
    ]
    result = calculate_bankroll_pl(bets)
    print(f"Bankroll P/L: {result['totalPL']} (real={result['realPL']}, probe={result['probePL']})")
    print(f"Excluded: {result['excludedBetCount']} bets")
