"""
lib/edgelab/research/night_before_timing.py
===========================================
RESEARCH ONLY. Pure, side-effect-free primitives for the MLB
night-before / early-execution timing study
(docs/EDGELAB_MLB_NIGHT_BEFORE_TIMING_RESEARCH_2026_09.md).

This module changes NO production behavior. It does not import, wrap,
mutate, or re-export anything from the production betting path: no
probability model, no recommendation qualification, no edge threshold,
no confidence tier, no Bet Up To logic, no stake sizing, no bankroll
logic, no market eligibility, no lineup gate, no slate output, no
settlement write path. It is an ADDITIVE research layer that reads the
already-archived, immutable EdgeLab corpus and classifies it.

Deliberately NOT a modification of lib/edgelab/checkpoints.py. The
production checkpoint vocabulary (FIRST_DAILY, LINEUP_CONFIRMATION,
T_MINUS_90/60/30/15/5, POST_START, INTERMEDIATE) is tuned for a
narrow ~7.5-minute tolerance around a handful of near-first-pitch
targets, and 155,768 of the corpus's 473,130 archived observations
fall into the single catch-all bucket INTERMEDIATE. That vocabulary
cannot distinguish an observation 20 hours before first pitch from one
3 hours before -- exactly the distinction this study exists to make.
Rather than widen production semantics (which would silently reclassify
every historical row that production code and prior research reports
already cite), this module adds a SECOND, independent classification
axis alongside it. Production checkpoints keep their meaning; research
horizons are new labels on the same immutable rows.

Three independent axes are computed here, and they are deliberately not
collapsed into one label:

  1. LEAD-TIME HORIZON  -- hours between capture and scheduled first
     pitch (T_MINUS_24_PLUS ... T_MINUS_0_4, POST_START).
  2. CALENDAR CONTEXT   -- where the capture sits in wall-clock ET
     relative to the game's own calendar date (PREVIOUS_CALENDAR_EVENING,
     OVERNIGHT, GAME_DAY_MORNING, ...). A 20-hour lead on a 10:10 PM ET
     game is an OVERNIGHT capture at 2 AM; a 20-hour lead on a 1:10 PM
     ET game would be a PREVIOUS_CALENDAR_EVENING capture at 5 PM. Those
     are different real-world decision moments and the study must never
     conflate them.
  3. EXECUTABILITY      -- whether the archived quote is actually usable
     as a hypothetical fill, and at what price.

None of these three infers anything it cannot prove. A quote is never
assumed pregame without a scheduled start; a price is never assumed
executable without both sides of a top-of-book.
"""

import re
from datetime import datetime, timedelta

try:  # pragma: no cover - zoneinfo is stdlib on the 3.11 runners this repo uses
    from zoneinfo import ZoneInfo

    EASTERN = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    EASTERN = None


# ---------------------------------------------------------------------------
# Scheduled first pitch
# ---------------------------------------------------------------------------

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# 'KXMLBGAME-26AUG152138KCLAA' -> ('26', 'AUG', '15', '2138')
_TICKER_DATETIME = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(\d{4})")


def scheduled_start_from_event_ticker(event_ticker):
    """
    Deterministic reconstruction of scheduled first pitch from the date/time
    digits Kalshi embeds in every MLB event ticker. Returns a timezone-aware
    datetime, or None when the ticker carries no parseable stamp.

    The embedded 'HHMM' is EASTERN wall-clock, not UTC -- the same convention
    lib/kalshi_ticker_time.py documents ("'1940' = 7:40 PM ET"). This module
    does not take that on trust: the study's own audit
    (scripts/edgelab/run_night_before_timing_research.py, stage `coverage`)
    re-verifies it against the corpus's authoritative `scheduledStart` field
    on every observation that carries one, and the report records the result.
    At the time of writing that check passed on 5,562 of 5,562 distinct
    events with a delta of exactly 0 minutes, so this reconstruction is
    classified as DETERMINISTIC_RECONSTRUCTION -- never as originally
    captured evidence.

    This matters because `scheduledStart` is populated on only 54.2% of
    archived observations, while the event ticker is present on 100.0%.
    Reconstructing lets the study classify every row instead of silently
    dropping the 45.8% that production happened not to stamp.
    """
    if not event_ticker or not isinstance(event_ticker, str) or EASTERN is None:
        return None
    match = _TICKER_DATETIME.search(event_ticker)
    if not match:
        return None
    yy, mon, dd, hhmm = match.groups()
    month = _MONTHS.get(mon)
    if month is None:
        return None
    hour, minute = int(hhmm[:2]), int(hhmm[2:])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    try:
        return datetime(2000 + int(yy), month, int(dd), hour, minute, tzinfo=EASTERN)
    except ValueError:
        return None


def parse_timestamp(value):
    """ISO-8601 (with 'Z' or an offset) or datetime -> aware datetime, else None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def hours_to_first_pitch(captured_at, scheduled_start):
    """
    Positive = captured before first pitch. None when either side is unknown
    -- never 0, never a guess. A caller that gets None must treat the
    observation's pregame status as UNPROVEN, not as pregame.
    """
    captured = parse_timestamp(captured_at)
    start = parse_timestamp(scheduled_start)
    if captured is None or start is None:
        return None
    return (start - captured).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# Axis 1: lead-time horizon
# ---------------------------------------------------------------------------

HORIZON_UNKNOWN = "UNKNOWN_TIMING"
HORIZON_POST_START = "POST_START"

# (label, lower_bound_hours_inclusive, upper_bound_hours_exclusive)
LEAD_TIME_HORIZONS = (
    ("T_MINUS_0_4", 0.0, 4.0),
    ("T_MINUS_4_8", 4.0, 8.0),
    ("T_MINUS_8_12", 8.0, 12.0),
    ("T_MINUS_12_18", 12.0, 18.0),
    ("T_MINUS_18_24", 18.0, 24.0),
    ("T_MINUS_24_PLUS", 24.0, None),
)

LEAD_TIME_HORIZON_ORDER = (
    HORIZON_POST_START,
    "T_MINUS_0_4",
    "T_MINUS_4_8",
    "T_MINUS_8_12",
    "T_MINUS_12_18",
    "T_MINUS_18_24",
    "T_MINUS_24_PLUS",
    HORIZON_UNKNOWN,
)


def classify_lead_time_horizon(hours_before_start):
    """
    Bucket a lead time. `None` in -> HORIZON_UNKNOWN out (the study never
    infers that a quote was pregame without evidence of both timestamps).
    """
    if hours_before_start is None:
        return HORIZON_UNKNOWN
    if hours_before_start < 0:
        return HORIZON_POST_START
    for label, low, high in LEAD_TIME_HORIZONS:
        if hours_before_start >= low and (high is None or hours_before_start < high):
            return label
    return HORIZON_UNKNOWN


# ---------------------------------------------------------------------------
# Axis 2: calendar context (wall-clock ET, relative to the game's own date)
# ---------------------------------------------------------------------------

CALENDAR_UNKNOWN = "UNKNOWN_CALENDAR"
CALENDAR_EARLIER = "EARLIER_THAN_PREVIOUS_EVENING"      # 2+ days out, or before 6 PM on D-1
CALENDAR_PREVIOUS_EVENING = "PREVIOUS_CALENDAR_EVENING"  # 18:00-23:59 ET on D-1
CALENDAR_OVERNIGHT = "OVERNIGHT"                         # 00:00-05:59 ET on game day
CALENDAR_GAME_DAY_MORNING = "GAME_DAY_MORNING"           # 06:00-11:59 ET on game day
CALENDAR_GAME_DAY_AFTERNOON = "GAME_DAY_AFTERNOON"       # 12:00-17:59 ET on game day
CALENDAR_GAME_DAY_EVENING = "GAME_DAY_EVENING"           # 18:00-23:59 ET on game day
CALENDAR_AFTER_GAME_DAY = "AFTER_GAME_DAY"

CALENDAR_CONTEXT_ORDER = (
    CALENDAR_EARLIER,
    CALENDAR_PREVIOUS_EVENING,
    CALENDAR_OVERNIGHT,
    CALENDAR_GAME_DAY_MORNING,
    CALENDAR_GAME_DAY_AFTERNOON,
    CALENDAR_GAME_DAY_EVENING,
    CALENDAR_AFTER_GAME_DAY,
    CALENDAR_UNKNOWN,
)

# The colloquial "night before" the user is asking about. Both of these are
# decision moments at which a human could realistically sit down and place a
# wager for tomorrow's slate; neither is game-day-daytime.
NIGHT_BEFORE_CALENDAR_CONTEXTS = frozenset(
    {CALENDAR_PREVIOUS_EVENING, CALENDAR_OVERNIGHT}
)

PREVIOUS_EVENING_START_HOUR_ET = 18


def classify_calendar_context(captured_at, scheduled_start):
    """
    Where does this capture sit in ET wall-clock time, relative to the
    calendar date of the game it prices?

    This axis exists because lead-time alone is ambiguous about the real
    decision moment. A 19-hour lead is an OVERNIGHT 2 AM capture for a
    9:10 PM game but a PREVIOUS_CALENDAR_EVENING 6 PM capture for a 1 PM
    game. "Can I bet the night before?" is a question about the second
    kind of moment as much as the first, and the study reports them apart.
    """
    captured = parse_timestamp(captured_at)
    start = parse_timestamp(scheduled_start)
    if captured is None or start is None or EASTERN is None:
        return CALENDAR_UNKNOWN

    captured_et = captured.astimezone(EASTERN)
    game_date = start.astimezone(EASTERN).date()
    day_offset = (captured_et.date() - game_date).days

    if day_offset > 0:
        return CALENDAR_AFTER_GAME_DAY
    if day_offset < -1:
        return CALENDAR_EARLIER
    if day_offset == -1:
        if captured_et.hour >= PREVIOUS_EVENING_START_HOUR_ET:
            return CALENDAR_PREVIOUS_EVENING
        return CALENDAR_EARLIER
    if captured_et.hour < 6:
        return CALENDAR_OVERNIGHT
    if captured_et.hour < 12:
        return CALENDAR_GAME_DAY_MORNING
    if captured_et.hour < 18:
        return CALENDAR_GAME_DAY_AFTERNOON
    return CALENDAR_GAME_DAY_EVENING


# ---------------------------------------------------------------------------
# Axis 3: executable prices
# ---------------------------------------------------------------------------

SIDE_YES = "YES"
SIDE_NO = "NO"

# Kalshi quotes are integer cents 1..99. 0 and 100 are not tradable prices:
# a contract quoted at 0 bid / 100 ask has no counterparty on that side.
MIN_TRADABLE_CENTS = 1
MAX_TRADABLE_CENTS = 99

# Top-of-book width beyond which a displayed price is not a credible fill.
# 15 cents is deliberately generous -- the study reports results at several
# widths rather than hard-coding one, and this constant is only the default
# "obviously unusable" screen, not a strategy parameter.
DEFAULT_MAX_USABLE_SPREAD_CENTS = 15

UNUSABLE_MISSING_BOOK = "MISSING_TOP_OF_BOOK"
UNUSABLE_NON_TRADABLE_BOUND = "NON_TRADABLE_PRICE_BOUND"
UNUSABLE_CROSSED_BOOK = "CROSSED_OR_INVERTED_BOOK"
UNUSABLE_WIDE_SPREAD = "SPREAD_EXCEEDS_LIMIT"
USABLE = "USABLE"


def _cents(value):
    """Corpus prices are already integer-valued cents stored as floats."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def book_usability(observation, max_spread_cents=DEFAULT_MAX_USABLE_SPREAD_CENTS):
    """
    Returns (status, spread_cents). Status is USABLE or one of the
    UNUSABLE_* reasons -- an explicit, enumerable reason every time, never a
    silent drop.

    Only `yesBid`/`yesAsk` are consulted. The archived corpus carries
    `noBid`/`noAsk` on 0 of 473,130 observations (the upstream
    /api/kalshisearch response has no NO-side fields at all), so a NO price
    is always derived, never read. See `no_ask_cents`.
    """
    bid = _cents(observation.get("yesBid"))
    ask = _cents(observation.get("yesAsk"))
    if bid is None or ask is None:
        return UNUSABLE_MISSING_BOOK, None
    if ask < bid:
        return UNUSABLE_CROSSED_BOOK, ask - bid
    spread = ask - bid
    if not (MIN_TRADABLE_CENTS <= bid <= MAX_TRADABLE_CENTS):
        return UNUSABLE_NON_TRADABLE_BOUND, spread
    if not (MIN_TRADABLE_CENTS <= ask <= MAX_TRADABLE_CENTS):
        return UNUSABLE_NON_TRADABLE_BOUND, spread
    if max_spread_cents is not None and spread > max_spread_cents:
        return UNUSABLE_WIDE_SPREAD, spread
    return USABLE, spread


def yes_ask_cents(observation):
    """
    The executable cost of a hypothetical YES purchase: the contemporaneous
    YES ask, exactly as displayed. Never the midpoint, never the last trade.
    """
    return _cents(observation.get("yesAsk"))


def no_ask_cents(observation):
    """
    The executable cost of a hypothetical NO purchase.

    DERIVED, and the derivation is stated rather than assumed. The archive
    has no NO-side field on any row, so this uses the Kalshi binary-contract
    identity: one YES contract and one NO contract on the same market always
    settle to exactly $1.00 between them, so buying NO at price p is the
    same trade as selling YES at (100 - p). The best available price to BUY
    NO is therefore 100 minus the best resting bid to buy YES:

        no_ask = 100 - yes_bid

    This is contractually exact, not an approximation, for TOP OF BOOK. Its
    one real limitation is depth, not price: it inherits whatever size rests
    at the best YES bid, and the archive stores no depth at all. The study
    therefore treats every derived NO entry as top-of-book-only and never
    claims capacity beyond it.

    The identity is not merely asserted here -- the study's own audit stage
    checks it against the corpus's `mid` and `yesAsk`/`yesBid` invariants,
    and the report records that a directly-captured NO quote to validate
    against does not exist anywhere in the archive.
    """
    bid = _cents(observation.get("yesBid"))
    if bid is None:
        return None
    return 100.0 - bid


def executable_entry_price_cents(observation, side):
    """Executable ask for `side` ('YES' or 'NO'), or None if unavailable."""
    if side == SIDE_YES:
        return yes_ask_cents(observation)
    if side == SIDE_NO:
        return no_ask_cents(observation)
    return None


def executable_exit_price_cents(observation, side):
    """
    The price a holder of `side` could realistically SELL at -- the bid on
    that side. Used only for mark-to-market / execution-value reporting, and
    always reported separately from settlement-based realized return so the
    two are never conflated.
    """
    bid = _cents(observation.get("yesBid"))
    ask = _cents(observation.get("yesAsk"))
    if side == SIDE_YES:
        return bid
    if side == SIDE_NO:
        # Selling NO == buying YES; the NO bid is 100 - yes_ask.
        return None if ask is None else 100.0 - ask
    return None


def realized_return_per_contract(entry_price_cents, settled_result, side):
    """
    Hypothetical realized return on ONE contract bought at
    `entry_price_cents`, as a fraction of the amount risked.

    A Kalshi binary contract settles at 100 cents if the side wins and 0 if
    it loses. Return = (100 - entry)/entry on a win, -1.0 on a loss.

    No fee adjustment is applied. The user's real cost basis for this
    workflow is the displayed executable contract price, so that is what
    this uses. Fee sensitivity is reported by the study as a separate,
    clearly-labelled optional analysis and never folded into the headline.

    Returns None -- never 0.0 -- when the market did not settle to a clean
    YES/NO, so an unsettled or void market can never be silently scored as a
    break-even bet.
    """
    if entry_price_cents is None or entry_price_cents <= 0:
        return None
    if settled_result not in (SIDE_YES, SIDE_NO):
        return None
    if side not in (SIDE_YES, SIDE_NO):
        return None
    won = settled_result == side
    if won:
        return (100.0 - entry_price_cents) / entry_price_cents
    return -1.0


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------

STALE_REPEATED_BOOK = "REPEATED_IDENTICAL_BOOK"
FRESH = "FRESH"


def stale_flags(observations, min_repeats_for_stale=3):
    """
    Marks runs of consecutive observations on one market whose entire
    top-of-book is byte-identical to the previous one. A quote that has not
    moved across several captures spanning hours is not proof of a dead
    market, but it is the only staleness signal this archive can support
    (there is no per-quote timestamp from the exchange, only our capture
    time), so it is reported as a flag rather than used to delete rows.

    `observations` must already be sorted by capture time. Returns a list of
    flags parallel to the input.
    """
    flags = []
    run_key = None
    run_length = 0
    for obs in observations:
        key = (_cents(obs.get("yesBid")), _cents(obs.get("yesAsk")),
               _cents(obs.get("volume")), _cents(obs.get("openInterest")))
        if key == run_key:
            run_length += 1
        else:
            run_key, run_length = key, 1
        flags.append(STALE_REPEATED_BOOK if run_length >= min_repeats_for_stale else FRESH)
    return flags


# ---------------------------------------------------------------------------
# Horizon selection over a linked contract timeline
# ---------------------------------------------------------------------------

# Research entry/comparison points. Each is resolved from the SAME immutable
# per-contract observation timeline, so a movement measured between any two of
# them is a movement in one contract's own executable price -- never a
# comparison across different contracts or different games.
POINT_EARLY_18H = "EARLY_18H_PLUS"
POINT_EARLY_12H = "EARLY_12H_PLUS"
POINT_EARLY_8H = "EARLY_8H_PLUS"
POINT_FIRST_GAME_DAY = "FIRST_GAME_DAY"
POINT_T_MINUS_90 = "T_MINUS_90"
POINT_LINEUP_CONFIRMATION = "LINEUP_CONFIRMATION"
POINT_T_MINUS_30 = "T_MINUS_30"
POINT_CLOSING = "CLOSING"


def select_earliest_at_least(timeline, min_hours):
    """
    Earliest usable observation whose lead is >= `min_hours`.

    "Earliest" (not "closest to the threshold") is deliberate: this models a
    bettor who acts as soon as a price is available at that horizon, which is
    the only version of the policy that uses no future information. Picking
    the best-priced row in the window would be hindsight selection.
    """
    best = None
    for row in timeline:
        lead = row.get("hoursBeforeStart")
        if lead is None or lead < min_hours:
            continue
        if best is None or row["capturedAt"] < best["capturedAt"]:
            best = row
    return best


def select_nearest_to_target(timeline, target_hours, tolerance_hours):
    """
    Observation whose lead is closest to `target_hours`, within
    `tolerance_hours`, and strictly pregame. Returns None when nothing lands
    in the window -- the study reports that as missing coverage rather than
    substituting a more distant quote.
    """
    best, best_gap = None, None
    for row in timeline:
        lead = row.get("hoursBeforeStart")
        if lead is None or lead < 0:
            continue
        gap = abs(lead - target_hours)
        if gap > tolerance_hours:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = row, gap
    return best


def select_first_game_day(timeline):
    """
    First pregame observation taken during game-day daylight hours
    (>= 06:00 ET on the game's own date). This is Policy B's entry: a bettor
    who deliberately sleeps on it and looks at the board in the morning.
    """
    best = None
    for row in timeline:
        if row.get("calendarContext") not in (
            CALENDAR_GAME_DAY_MORNING,
            CALENDAR_GAME_DAY_AFTERNOON,
            CALENDAR_GAME_DAY_EVENING,
        ):
            continue
        lead = row.get("hoursBeforeStart")
        if lead is None or lead < 0:
            continue
        if best is None or row["capturedAt"] < best["capturedAt"]:
            best = row
    return best


def select_closing(timeline):
    """
    Last pregame observation on this contract. Reuses the intent of
    lib/edgelab/checkpoints.select_closing_quote (final valid tradable quote
    strictly before first pitch, never "the chronologically last row") but
    operates on this study's own pre-filtered timeline rows, which already
    carry a proven `hoursBeforeStart`.
    """
    best = None
    for row in timeline:
        lead = row.get("hoursBeforeStart")
        if lead is None or lead < 0:
            continue
        if best is None or row["capturedAt"] > best["capturedAt"]:
            best = row
    return best


def select_at_or_before(timeline, cutoff_iso):
    """
    Last pregame observation at or before `cutoff_iso` -- used to price the
    lineup-confirmation moment, whose timestamp comes from an independent
    store (ModelEvaluation rows carrying checkpoint=LINEUP_CONFIRMATION)
    rather than from the price archive itself.

    Uses the last quote AT OR BEFORE the confirmation time, never the first
    one after it: a bettor reacting to confirmed lineups can only transact at
    a price that already exists, and reaching forward for the next capture
    would import information published after the decision moment.
    """
    cutoff = parse_timestamp(cutoff_iso)
    if cutoff is None:
        return None
    best = None
    for row in timeline:
        lead = row.get("hoursBeforeStart")
        if lead is None or lead < 0:
            continue
        captured = parse_timestamp(row["capturedAt"])
        if captured is None or captured > cutoff:
            continue
        if best is None or row["capturedAt"] > best["capturedAt"]:
            best = row
    return best
