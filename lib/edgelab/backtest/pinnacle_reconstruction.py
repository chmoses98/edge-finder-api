"""
lib/edgelab/backtest/pinnacle_reconstruction.py
====================================================================
MLB-RSCH-0008 PIT reconstruction: per-game Pinnacle snapshot selection
and de-vigging.

Fixes the exact data-quality gap the historical sharp-market audit
found (docs/EDGELAB_HISTORICAL_SHARP_MARKET_AUDIT.md §7b): a blanket
daily snapshot risks capturing a near-final or in-progress price for
early games on a day with late games. Every function here operates on
ONE game at a time against a set of candidate snapshots, selecting the
CLOSEST snapshot strictly before that game's own scheduled start,
subject to a hard preregistered maximum lookback -- never a later
game's snapshot, never an in-progress or post-start price.
"""
import math

# Preregistered, fixed -- never tuned from observed results (mission:
# "Preregister that maximum before evaluating results"). A snapshot
# further than this many minutes before scheduled first pitch is not
# used as this game's "closest valid pregame snapshot" even if it's
# the only one available -- the game is simply excluded, not
# approximated with a stale price.
MAX_MINUTES_BEFORE_START = 60


def minutes_before_start(snapshot_timestamp_epoch, commence_time_epoch):
    """Positive means the snapshot is strictly before commence_time (a
    real pregame observation); zero or negative means at-or-after
    (in-progress/post-start), rejected by the caller, never used."""
    return (commence_time_epoch - snapshot_timestamp_epoch) / 60.0


def select_closest_pregame_snapshot(snapshots_for_game, commence_time_epoch, max_minutes_before=MAX_MINUTES_BEFORE_START):
    """
    Pure. `snapshots_for_game`: list of {"requestedAt": epoch_seconds,
    "bookmakers": [...]} candidates already known to be FOR THIS GAME
    (caller's responsibility -- this function never matches games, only
    selects among a game's own candidate snapshots). Returns the single
    snapshot dict with the smallest positive minutesBeforeStart within
    (0, max_minutes_before], or None if no candidate qualifies -- never
    a negative (post-start) or over-the-limit (too-early) snapshot,
    never a different game's data.
    """
    eligible = []
    for snap in snapshots_for_game:
        minutes = minutes_before_start(snap["requestedAt"], commence_time_epoch)
        if 0 < minutes <= max_minutes_before:
            eligible.append((minutes, snap))
    if not eligible:
        return None
    eligible.sort(key=lambda pair: pair[0])
    best_minutes, best_snap = eligible[0]
    result = dict(best_snap)
    result["minutesBeforeStart"] = round(best_minutes, 2)
    return result


def reject_reason(snapshots_for_game, commence_time_epoch, max_minutes_before=MAX_MINUTES_BEFORE_START):
    """Diagnostic-only (never used for selection): why a game has no
    qualifying snapshot -- NO_CANDIDATES, ALL_POST_START, ALL_TOO_EARLY,
    or MIXED_NONE_QUALIFYING. Pure reporting, doesn't change eligibility."""
    if not snapshots_for_game:
        return "NO_CANDIDATES"
    minutes_list = [minutes_before_start(s["requestedAt"], commence_time_epoch) for s in snapshots_for_game]
    if all(m <= 0 for m in minutes_list):
        return "ALL_POST_START"
    if all(m > max_minutes_before for m in minutes_list):
        return "ALL_TOO_EARLY"
    return "MIXED_NONE_QUALIFYING"


# ── Two-sided de-vig (moneyline) ────────────────────────────────────────

def american_to_implied_probability(american_price):
    """Standard American-odds-to-implied-probability conversion (raw,
    vig-inclusive)."""
    if american_price is None:
        return None
    if american_price > 0:
        return 100.0 / (american_price + 100.0)
    return -american_price / (-american_price + 100.0)


def devig_two_sided(price_a, price_b):
    """
    Pure. Multiplicative (proportional) de-vig of a two-sided market --
    the standard, simplest, most defensible method (each side's raw
    implied probability divided by the sum of both, so they sum to
    exactly 1.0). Returns (fair_prob_a, fair_prob_b, overround) or
    (None, None, None) if either price is missing. overround =
    raw_implied_a + raw_implied_b - 1.0 (the vig, as a fraction).
    """
    if price_a is None or price_b is None:
        return None, None, None
    raw_a = american_to_implied_probability(price_a)
    raw_b = american_to_implied_probability(price_b)
    total = raw_a + raw_b
    if total <= 0:
        return None, None, None
    return round(raw_a / total, 6), round(raw_b / total, 6), round(total - 1.0, 6)


def matched_total_line(snapshot_totals_market, target_line):
    """
    Pure. Returns (over_price, under_price) for the market's Over/Under
    outcomes ONLY if BOTH sides quote EXACTLY `target_line` (the same
    threshold) -- never compares a model's line to a different
    Pinnacle line (mission: "Do not compare model Over 8.5 to Pinnacle
    Over 9. Only exact-line comparisons are eligible."). Returns
    (None, None) if the market doesn't carry both sides at that exact
    point, or the market is missing.
    """
    if not snapshot_totals_market:
        return None, None
    over_price = under_price = None
    for outcome in snapshot_totals_market.get("outcomes") or []:
        if outcome.get("point") != target_line:
            continue
        if outcome.get("name") == "Over":
            over_price = outcome.get("price")
        elif outcome.get("name") == "Under":
            under_price = outcome.get("price")
    return over_price, under_price
