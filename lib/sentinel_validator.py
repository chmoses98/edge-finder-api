#!/usr/bin/env python3
"""
lib/sentinel_validator.py
==========================
Sentinel Price Validator

Rejects these values anywhere in official betting data:
  19900, -19900, 100000, -100000
  Any absolute value >= 19000 (Kalshi API sentinel for unavailable markets)
  Impossible probability/price values

Used as a hard-fail gate before committing any official slate fields.
"""

import json

# ── Sentinel constants ────────────────────────────────────────────────────────
SENTINEL_AMERICAN_PRICES = {19900, -19900, 100000, -100000}
SENTINEL_ABS_THRESHOLD = 19000  # any abs(american_price) >= this is sentinel

# Fields to always check for sentinels in bet/market objects
PRICE_FIELDS = {
    "price", "kalshiPrice", "entry_price", "betTimeLine", "closingLine",
    "closingPrice", "yes_price", "last_price", "close_price",
    "awayML", "homeML", "f5Away", "f5Home", "kalshiML", "kalshiF5ML",
}

# Fields to check for probability sentinels (should be 0-100 scale)
PROB_FIELDS = {
    "modelProb", "modelPct", "kalshiPct", "kvfPct",
    "awayProb", "homeProb", "f5AwayProb", "f5HomeProb",
}


class SentinelValidationError(ValueError):
    """Raised when sentinel prices are detected in official betting data."""
    def __init__(self, message, offending_fields=None):
        super().__init__(message)
        self.offending_fields = offending_fields or []


def is_sentinel_american(value):
    """
    Return True if value is a sentinel American odds value.
    Sentinel = 19900, -19900, 100000, -100000, or abs >= 19000.
    """
    if value is None:
        return False
    try:
        v = float(value)
        if v in SENTINEL_AMERICAN_PRICES:
            return True
        if abs(v) >= SENTINEL_ABS_THRESHOLD:
            return True
        return False
    except (TypeError, ValueError):
        return False


def is_sentinel_probability(value):
    """
    Return True if a probability value is impossible/sentinel.
    Probability should be in range (0, 1) or (0, 100) — 0 or 100 exactly = sentinel.
    """
    if value is None:
        return False
    try:
        v = float(value)
        # On 0-1 scale: 0 or 1 exactly = settlement sentinel
        if v == 0.0 or v == 1.0:
            return True
        # On 0-100 scale: 0 or 100 exactly = settlement sentinel
        if v == 100.0:
            return True
        return False
    except (TypeError, ValueError):
        return False


def scan_for_sentinels(obj, path="", price_fields=None, prob_fields=None):
    """
    Recursively scan an object for sentinel prices.

    Returns list of dicts: [{path, value, type}]
    """
    if price_fields is None:
        price_fields = PRICE_FIELDS
    if prob_fields is None:
        prob_fields = PROB_FIELDS

    found = []

    if isinstance(obj, dict):
        for k, v in obj.items():
            sub = f"{path}.{k}" if path else k
            if k in price_fields and isinstance(v, (int, float)):
                if is_sentinel_american(v):
                    found.append({"path": sub, "value": v, "type": "sentinel_american_price"})
            elif k in prob_fields and isinstance(v, (int, float)):
                if is_sentinel_probability(v):
                    found.append({"path": sub, "value": v, "type": "sentinel_probability"})
            else:
                found.extend(scan_for_sentinels(v, sub, price_fields, prob_fields))

    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(scan_for_sentinels(v, f"{path}[{i}]", price_fields, prob_fields))

    return found


def validate_no_sentinels(obj, context="object", raise_on_error=True):
    """
    Validate that an object contains no sentinel prices.

    Args:
        obj: dict or list to scan
        context: description for error message
        raise_on_error: if True, raise SentinelValidationError; else return (is_valid, offenders)

    Returns:
        (True, []) if no sentinels found
        (False, [offenders]) if sentinels found and raise_on_error=False
    Raises:
        SentinelValidationError if sentinels found and raise_on_error=True
    """
    offenders = scan_for_sentinels(obj)
    if offenders:
        msg = (
            f"Sentinel prices detected in {context}. "
            f"Found {len(offenders)} invalid field(s): "
            + "; ".join(f"{o['path']}={o['value']}" for o in offenders[:5])
        )
        if raise_on_error:
            raise SentinelValidationError(msg, offending_fields=offenders)
        return False, offenders

    return True, []


def validate_bet_for_real_money(bet, raise_on_error=True):
    """
    Validate a single bet dict for sentinel prices before any real-money classification.

    This is a hard gate — no bet with sentinel prices may be classified as real-money.

    Returns:
        (True, []) if valid
        (False, [offenders]) if invalid and raise_on_error=False
    Raises:
        SentinelValidationError if invalid and raise_on_error=True
    """
    context = f"bet {bet.get('id', bet.get('ticker', 'unknown'))}"
    return validate_no_sentinels(bet, context=context, raise_on_error=raise_on_error)


def validate_slate_for_sentinels(slate_data, raise_on_error=True):
    """
    Validate an entire slate dict for sentinel prices.
    Hard-fails or quarantines before commit if sentinels appear.

    Returns:
        (True, {}) if valid
        (False, {gamePk: [offenders]}) if invalid
    """
    games = slate_data.get("games", [])
    bad_games = {}

    for game in games:
        gpk = str(game.get("gameId") or game.get("gamePk") or "unknown")
        offenders = scan_for_sentinels(game)
        if offenders:
            bad_games[gpk] = offenders

    if bad_games:
        msg = (
            f"Sentinel prices found in {len(bad_games)} game(s): "
            + ", ".join(bad_games.keys())
        )
        if raise_on_error:
            raise SentinelValidationError(msg, offending_fields=bad_games)
        return False, bad_games

    return True, {}


# ── Convenience function used in tests ───────────────────────────────────────

def reject_sentinel(value, field_name="price"):
    """
    Simple guard: raise SentinelValidationError if value is a sentinel.
    Use before storing any price in official slate data.
    """
    if is_sentinel_american(value):
        raise SentinelValidationError(
            f"Sentinel price {value} rejected in field '{field_name}'",
            offending_fields=[{"path": field_name, "value": value, "type": "sentinel_american_price"}]
        )
    return value


if __name__ == "__main__":
    # Quick smoke test
    test = {
        "games": [
            {"gameId": "111", "markets": [{"price": -141, "modelProb": 0.56}]},
            {"gameId": "222", "markets": [{"price": 19900, "modelProb": 0.5}]},
        ]
    }
    is_valid, bad = validate_slate_for_sentinels(test, raise_on_error=False)
    print(f"Valid: {is_valid}")
    print(f"Bad games: {list(bad.keys())}")
