"""
lib/edgelab/checkpoints.py
=============================
Classifies a captured quote by its distance from scheduled first pitch,
into the standardized research checkpoints from EdgeLab Phase 1 section D.

GitHub Actions cron cannot fire at an exact minute (the scheduled
capture workflows run every 10-30 minutes -- see the audit in
docs/EDGELAB_PHASE1.md). So EdgeLab does not try to schedule a job for
"exactly T-30"; it classifies whatever was actually captured, after the
fact, by nearest target.

FIRST_DAILY and LINEUP_CONFIRMATION are not time-distance buckets -- the
caller determines those contextually (first observation of this market
today; lineup status flipped to confirmed at capture time) and passes
them in as flags, checked before any time-distance logic runs.
"""

from datetime import datetime

MINUTE_TARGETS = (
    ("T_MINUS_90", 90),
    ("T_MINUS_60", 60),
    ("T_MINUS_30", 30),
    ("T_MINUS_15", 15),
    ("T_MINUS_5", 5),
)

# The finest-grained scheduled capture (clv_capture.yml) polls every 10
# minutes, so a tolerance smaller than half that (5 min) would leave real
# 10-minute-cadence ticks unclassified between adjacent targets in the
# 5/15 gap; 7.5 min comfortably covers every workflow's actual cadence
# without letting two adjacent targets both claim the same tick.
DEFAULT_TOLERANCE_MINUTES = 7.5


def _parse(ts):
    if isinstance(ts, datetime):
        return ts
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def classify_checkpoint(
    captured_at,
    scheduled_start=None,
    *,
    is_first_of_day=False,
    is_lineup_confirmation=False,
    tolerance_minutes=DEFAULT_TOLERANCE_MINUTES,
):
    """
    Returns one of: FIRST_DAILY, LINEUP_CONFIRMATION, T_MINUS_90,
    T_MINUS_60, T_MINUS_30, T_MINUS_15, T_MINUS_5, POST_START,
    INTERMEDIATE (a real quote that just doesn't land near any target --
    still preserved, never dropped).

    `captured_at`/`scheduled_start` accept either datetime objects or ISO
    8601 strings. scheduled_start=None (start time not yet known) always
    yields INTERMEDIATE unless a contextual flag applies, since distance
    can't be computed.
    """
    if is_first_of_day:
        return "FIRST_DAILY"
    if is_lineup_confirmation:
        return "LINEUP_CONFIRMATION"
    if scheduled_start is None:
        return "INTERMEDIATE"

    captured_dt = _parse(captured_at)
    scheduled_dt = _parse(scheduled_start)
    delta_minutes = (scheduled_dt - captured_dt).total_seconds() / 60.0

    if delta_minutes < 0:
        return "POST_START"

    best_label, best_diff = None, None
    for label, target in MINUTE_TARGETS:
        diff = abs(delta_minutes - target)
        if diff <= tolerance_minutes and (best_diff is None or diff < best_diff):
            best_label, best_diff = label, diff
    return best_label or "INTERMEDIATE"


def select_closing_quote(observations, scheduled_start=None, actual_start=None):
    """
    Given a list of observation-like dicts (each with 'capturedAt' and a
    'marketStatus') for ONE market, return the one that is the official
    closing quote: the final valid tradable quote strictly before market
    suspension or actual game start -- whichever is earlier/applicable.

    A quote counts as a valid tradable candidate only if its marketStatus
    is 'active' (or unset/unknown, treated as active -- never assumed
    suspended without evidence) AND its capturedAt is strictly before
    actual_start (preferred) or scheduled_start (fallback, when actual
    start is not yet known). Never invents a quote: returns None if no
    candidate qualifies, and the caller is responsible for recording
    settlementStatus/clv unavailability with a reason in that case.

    When NEITHER actual_start NOR scheduled_start is known, "pre-start"
    cannot be verified for any observation, so this returns None rather
    than falling back to "the chronologically last observation" -- that
    fallback previously let a snapshot captured hours after first pitch
    (once its game's start time failed to resolve) be misclassified as a
    genuine pregame closing quote (see the KXMLBHRR-...-KWAN38-5 case
    documented in data/edgelab/reports/market_price_calibration_audit.md).
    Unresolved timing is explicit and ineligible, never guessed.
    """
    start_bound = actual_start or scheduled_start
    if start_bound is None:
        return None
    start_dt = _parse(start_bound)

    candidates = []
    for obs in observations:
        status = (obs.get("marketStatus") or "active").lower()
        if status not in ("active", "unknown"):
            continue
        captured_dt = _parse(obs["capturedAt"])
        if captured_dt >= start_dt:
            continue
        candidates.append((captured_dt, obs))

    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    return candidates[-1][1]
