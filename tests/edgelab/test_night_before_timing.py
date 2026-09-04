#!/usr/bin/env python3
"""
tests/edgelab/test_night_before_timing.py
=========================================
Coverage for lib/edgelab/research/night_before_timing.py -- the
research-only classification/pricing layer behind
docs/EDGELAB_MLB_NIGHT_BEFORE_TIMING_RESEARCH_2026_09.md.

The properties tested here are the ones the study's conclusions actually
rest on, not incidental behaviour:

  * The event-ticker start-time reconstruction reads the embedded HHMM as
    EASTERN wall clock. Every lead-time number in the report is derived
    from this, so a UTC/ET mix-up would silently shift every horizon by
    4-5 hours and invent "night-before" coverage that does not exist.
  * A missing timestamp yields UNKNOWN, never a fabricated pregame
    classification (spec requirement 2: "Never infer that a quote was
    pregame without evidence").
  * NO entry price is 100 - yesBid, the exact Kalshi binary identity, and
    is never silently replaced by a midpoint (spec requirement 3).
  * An unsettled market scores None, never 0.0, so a void market can never
    be counted as a break-even wager (spec requirement 5).
  * Horizon selection never reaches forward past its own decision moment
    (spec requirement 13: no hindsight).
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.research import night_before_timing as nbt  # noqa: E402


# --------------------------------------------------------------------------
# Scheduled start reconstruction
# --------------------------------------------------------------------------

def test_event_ticker_start_is_eastern_wall_clock():
    start = nbt.scheduled_start_from_event_ticker("KXMLBGAME-26AUG152138KCLAA")
    assert start is not None
    # 21:38 ET on 2026-08-15 == 01:38 UTC on 2026-08-16 (EDT, UTC-4).
    assert start.astimezone(timezone.utc).isoformat() == "2026-08-16T01:38:00+00:00"


def test_event_ticker_start_handles_every_month_token():
    for token, month in nbt._MONTHS.items():
        start = nbt.scheduled_start_from_event_ticker(f"KXMLBGAME-26{token}101305AAABBB")
        assert start is not None and start.month == month


def test_event_ticker_start_returns_none_rather_than_guessing():
    for bad in (None, "", "NOT-A-TICKER", "KXMLBGAME-26XXX152138KCLAA",
                "KXMLBGAME-26AUG152578KCLAA", "KXMLBGAME-26FEB302138KCLAA"):
        assert nbt.scheduled_start_from_event_ticker(bad) is None


def test_hours_to_first_pitch_is_none_when_either_timestamp_unknown():
    assert nbt.hours_to_first_pitch(None, "2026-08-15T21:38:00Z") is None
    assert nbt.hours_to_first_pitch("2026-08-15T04:00:00Z", None) is None
    # A naive (offset-less) timestamp is unusable, not assumed UTC.
    assert nbt.hours_to_first_pitch("2026-08-15T04:00:00", "2026-08-15T21:38:00Z") is None


def test_hours_to_first_pitch_signs_and_magnitude():
    assert nbt.hours_to_first_pitch("2026-08-15T04:00:00Z", "2026-08-15T21:00:00Z") == 17.0
    assert nbt.hours_to_first_pitch("2026-08-15T22:00:00Z", "2026-08-15T21:00:00Z") == -1.0


# --------------------------------------------------------------------------
# Axis 1: lead-time horizon
# --------------------------------------------------------------------------

def test_lead_time_horizon_buckets():
    cases = {
        -0.01: nbt.HORIZON_POST_START,
        0.0: "T_MINUS_0_4",
        3.99: "T_MINUS_0_4",
        4.0: "T_MINUS_4_8",
        8.0: "T_MINUS_8_12",
        12.0: "T_MINUS_12_18",
        17.99: "T_MINUS_12_18",
        18.0: "T_MINUS_18_24",
        23.99: "T_MINUS_18_24",
        24.0: "T_MINUS_24_PLUS",
        400.0: "T_MINUS_24_PLUS",
    }
    for hours, expected in cases.items():
        assert nbt.classify_lead_time_horizon(hours) == expected, hours


def test_unknown_timing_is_never_silently_pregame():
    assert nbt.classify_lead_time_horizon(None) == nbt.HORIZON_UNKNOWN


def test_every_horizon_label_is_in_the_declared_order():
    labels = {label for label, _, _ in nbt.LEAD_TIME_HORIZONS}
    labels |= {nbt.HORIZON_POST_START, nbt.HORIZON_UNKNOWN}
    assert labels == set(nbt.LEAD_TIME_HORIZON_ORDER)


# --------------------------------------------------------------------------
# Axis 2: calendar context
# --------------------------------------------------------------------------

def _ctx(captured_utc, start_utc):
    return nbt.classify_calendar_context(captured_utc, start_utc)


def test_calendar_context_distinguishes_previous_evening_from_overnight():
    # 7:10 PM ET game on 2026-08-15 == 23:10 UTC.
    start = "2026-08-15T23:10:00Z"
    # 2026-08-14 20:00 ET == 2026-08-15 00:00 UTC -> previous calendar evening.
    assert _ctx("2026-08-15T00:00:00Z", start) == nbt.CALENDAR_PREVIOUS_EVENING
    # 2026-08-15 02:00 ET == 2026-08-15 06:00 UTC -> overnight on game day.
    assert _ctx("2026-08-15T06:00:00Z", start) == nbt.CALENDAR_OVERNIGHT
    # 2026-08-15 09:00 ET -> game-day morning.
    assert _ctx("2026-08-15T13:00:00Z", start) == nbt.CALENDAR_GAME_DAY_MORNING
    # 2026-08-15 14:00 ET -> game-day afternoon.
    assert _ctx("2026-08-15T18:00:00Z", start) == nbt.CALENDAR_GAME_DAY_AFTERNOON
    # 2026-08-15 19:00 ET -> game-day evening.
    assert _ctx("2026-08-15T23:00:00Z", start) == nbt.CALENDAR_GAME_DAY_EVENING


def test_calendar_context_same_lead_time_can_be_two_different_moments():
    """The reason this axis exists at all: identical 19h leads, different days."""
    late_game = "2026-08-16T02:10:00Z"   # 10:10 PM ET on 2026-08-15
    early_game = "2026-08-15T17:10:00Z"  # 1:10 PM ET on 2026-08-15
    late_entry = "2026-08-15T07:10:00Z"  # 3:10 AM ET 08-15 -> overnight
    early_entry = "2026-08-14T22:10:00Z"  # 6:10 PM ET 08-14 -> previous evening
    assert nbt.hours_to_first_pitch(late_entry, late_game) == 19.0
    assert nbt.hours_to_first_pitch(early_entry, early_game) == 19.0
    assert _ctx(late_entry, late_game) == nbt.CALENDAR_OVERNIGHT
    assert _ctx(early_entry, early_game) == nbt.CALENDAR_PREVIOUS_EVENING


def test_calendar_context_earlier_and_after_and_unknown():
    start = "2026-08-15T23:10:00Z"
    # 2026-08-14 15:00 ET -> D-1 but before 6 PM -> EARLIER.
    assert _ctx("2026-08-14T19:00:00Z", start) == nbt.CALENDAR_EARLIER
    # 2026-08-13 -> two days out.
    assert _ctx("2026-08-13T19:00:00Z", start) == nbt.CALENDAR_EARLIER
    # 2026-08-16 -> after game day.
    assert _ctx("2026-08-16T19:00:00Z", start) == nbt.CALENDAR_AFTER_GAME_DAY
    assert _ctx(None, start) == nbt.CALENDAR_UNKNOWN
    assert _ctx("2026-08-15T00:00:00Z", None) == nbt.CALENDAR_UNKNOWN


def test_night_before_contexts_exclude_game_day_daytime():
    assert nbt.CALENDAR_GAME_DAY_MORNING not in nbt.NIGHT_BEFORE_CALENDAR_CONTEXTS
    assert nbt.NIGHT_BEFORE_CALENDAR_CONTEXTS == {
        nbt.CALENDAR_PREVIOUS_EVENING, nbt.CALENDAR_OVERNIGHT
    }


# --------------------------------------------------------------------------
# Axis 3: executable prices
# --------------------------------------------------------------------------

def _obs(bid, ask, **extra):
    row = {"yesBid": bid, "yesAsk": ask}
    row.update(extra)
    return row


def test_yes_entry_is_the_displayed_ask_never_the_midpoint():
    obs = _obs(52.0, 56.0)
    assert nbt.yes_ask_cents(obs) == 56.0
    assert nbt.yes_ask_cents(obs) != 54.0  # the midpoint, explicitly not used


def test_no_entry_is_the_exact_binary_complement_of_the_yes_bid():
    assert nbt.no_ask_cents(_obs(52.0, 56.0)) == 48.0
    assert nbt.no_ask_cents(_obs(1.0, 3.0)) == 99.0
    assert nbt.no_ask_cents(_obs(None, 56.0)) is None


def test_yes_and_no_entry_prices_sum_to_more_than_par_by_the_spread():
    """Buying both sides costs 100 + spread -- the spread is a real cost, not free."""
    obs = _obs(52.0, 56.0)
    assert nbt.yes_ask_cents(obs) + nbt.no_ask_cents(obs) == 104.0


def test_exit_prices_are_bids_not_asks():
    obs = _obs(52.0, 56.0)
    assert nbt.executable_exit_price_cents(obs, nbt.SIDE_YES) == 52.0
    assert nbt.executable_exit_price_cents(obs, nbt.SIDE_NO) == 44.0
    assert nbt.executable_entry_price_cents(obs, "SOMETHING_ELSE") is None


def test_book_usability_reasons_are_explicit():
    assert nbt.book_usability(_obs(52.0, 56.0))[0] == nbt.USABLE
    assert nbt.book_usability(_obs(None, 56.0))[0] == nbt.UNUSABLE_MISSING_BOOK
    assert nbt.book_usability(_obs(56.0, 52.0))[0] == nbt.UNUSABLE_CROSSED_BOOK
    assert nbt.book_usability(_obs(0.0, 1.0))[0] == nbt.UNUSABLE_NON_TRADABLE_BOUND
    assert nbt.book_usability(_obs(99.0, 100.0))[0] == nbt.UNUSABLE_NON_TRADABLE_BOUND
    assert nbt.book_usability(_obs(10.0, 40.0))[0] == nbt.UNUSABLE_WIDE_SPREAD
    assert nbt.book_usability(_obs(10.0, 40.0), max_spread_cents=None)[0] == nbt.USABLE
    assert nbt.book_usability(_obs(52.0, 56.0))[1] == 4.0


def test_realized_return_matches_binary_contract_payout():
    # Bought YES at 40c, YES settled: risked 40 to make 60.
    assert nbt.realized_return_per_contract(40.0, "YES", "YES") == 1.5
    # Bought YES at 40c, NO settled: total loss of stake.
    assert nbt.realized_return_per_contract(40.0, "NO", "YES") == -1.0
    # Bought NO at 60c, NO settled.
    assert abs(nbt.realized_return_per_contract(60.0, "NO", "NO") - (40.0 / 60.0)) < 1e-12


def test_unsettled_market_scores_none_not_breakeven():
    for result in (None, "VOID", "", "PUSH", "UNKNOWN"):
        assert nbt.realized_return_per_contract(40.0, result, "YES") is None
    assert nbt.realized_return_per_contract(None, "YES", "YES") is None
    assert nbt.realized_return_per_contract(0.0, "YES", "YES") is None


def test_realized_return_is_not_fee_adjusted():
    """Headline economics use the displayed price as the cost basis, by spec."""
    assert nbt.realized_return_per_contract(50.0, "YES", "YES") == 1.0


# --------------------------------------------------------------------------
# Staleness
# --------------------------------------------------------------------------

def test_stale_flags_mark_repeated_books_only():
    rows = [
        _obs(50.0, 51.0, volume=1.0, openInterest=1.0),
        _obs(50.0, 51.0, volume=1.0, openInterest=1.0),
        _obs(50.0, 51.0, volume=1.0, openInterest=1.0),
        _obs(50.0, 52.0, volume=1.0, openInterest=1.0),
    ]
    assert nbt.stale_flags(rows) == [nbt.FRESH, nbt.FRESH, nbt.STALE_REPEATED_BOOK, nbt.FRESH]


# --------------------------------------------------------------------------
# Horizon selection (hindsight safety)
# --------------------------------------------------------------------------

def _row(captured, lead, ctx=nbt.CALENDAR_GAME_DAY_MORNING):
    return {"capturedAt": captured, "hoursBeforeStart": lead, "calendarContext": ctx}


def test_earliest_at_least_takes_the_first_qualifying_quote_not_the_best_one():
    timeline = [
        _row("2026-08-15T04:00:00Z", 19.0),
        _row("2026-08-15T05:00:00Z", 18.0),
        _row("2026-08-15T12:00:00Z", 11.0),
    ]
    picked = nbt.select_earliest_at_least(timeline, 12.0)
    assert picked["capturedAt"] == "2026-08-15T04:00:00Z"
    assert nbt.select_earliest_at_least(timeline, 24.0) is None


def test_nearest_to_target_respects_tolerance_and_never_uses_post_start():
    timeline = [
        _row("2026-08-15T20:00:00Z", 1.6),
        _row("2026-08-15T21:00:00Z", 0.6),
        _row("2026-08-15T23:00:00Z", -1.0),
    ]
    assert nbt.select_nearest_to_target(timeline, 1.5, 0.25)["hoursBeforeStart"] == 1.6
    assert nbt.select_nearest_to_target(timeline, 6.0, 0.25) is None
    # A post-start row can never be selected, even if it is numerically nearest.
    assert nbt.select_nearest_to_target(timeline, -1.0, 0.25) is None


def test_first_game_day_skips_overnight_and_previous_evening():
    timeline = [
        _row("2026-08-15T02:00:00Z", 21.0, nbt.CALENDAR_PREVIOUS_EVENING),
        _row("2026-08-15T06:00:00Z", 17.0, nbt.CALENDAR_OVERNIGHT),
        _row("2026-08-15T13:00:00Z", 10.0, nbt.CALENDAR_GAME_DAY_MORNING),
        _row("2026-08-15T18:00:00Z", 5.0, nbt.CALENDAR_GAME_DAY_AFTERNOON),
    ]
    assert nbt.select_first_game_day(timeline)["capturedAt"] == "2026-08-15T13:00:00Z"


def test_closing_is_the_last_pregame_quote_never_a_post_start_one():
    timeline = [
        _row("2026-08-15T20:00:00Z", 1.0),
        _row("2026-08-15T22:00:00Z", 0.1),
        _row("2026-08-15T23:30:00Z", -1.5),
    ]
    assert nbt.select_closing(timeline)["capturedAt"] == "2026-08-15T22:00:00Z"
    assert nbt.select_closing([_row("2026-08-15T23:30:00Z", -1.5)]) is None


def test_at_or_before_never_reaches_forward_past_the_decision_moment():
    timeline = [
        _row("2026-08-15T18:00:00Z", 5.0),
        _row("2026-08-15T19:00:00Z", 4.0),
        _row("2026-08-15T20:00:00Z", 3.0),
    ]
    picked = nbt.select_at_or_before(timeline, "2026-08-15T19:30:00Z")
    assert picked["capturedAt"] == "2026-08-15T19:00:00Z"
    # Nothing exists at or before the cutoff -> None, never the next quote after it.
    assert nbt.select_at_or_before(timeline, "2026-08-15T17:00:00Z") is None
    assert nbt.select_at_or_before(timeline, None) is None


# --------------------------------------------------------------------------
# Physical MLB game identity
#
# The correction these tests lock in: an earlier revision clustered its
# bootstrap confidence intervals on `eventTicker`, which is per-SERIES, not
# per-game. One physical game is priced by ~17 Kalshi series, so that
# treated a single game as ~17 independent observations and understated
# every interval. `gameId` is not a safe substitute either -- the corpus
# stores it in two incompatible formats.
# --------------------------------------------------------------------------

def test_every_series_for_one_game_shares_one_physical_game_key():
    """The whole point: different series, same baseball game, same key."""
    same_game = [
        "KXMLBGAME-26AUG152138KCLAA",
        "KXMLBTOTAL-26AUG152138KCLAA",
        "KXMLBSPREAD-26AUG152138KCLAA",
        "KXMLBF5-26AUG152138KCLAA",
        "KXMLBKS-26AUG152138KCLAA",
        "KXMLBHRR-26AUG152138KCLAA",
    ]
    keys = {nbt.physical_game_key(t) for t in same_game}
    assert keys == {"26AUG152138KCLAA"}


def test_different_games_do_not_collide():
    assert nbt.physical_game_key("KXMLBGAME-26AUG152138KCLAA") != \
        nbt.physical_game_key("KXMLBGAME-26AUG152138KCLAB")
    assert nbt.physical_game_key("KXMLBGAME-26AUG152138KCLAA") != \
        nbt.physical_game_key("KXMLBGAME-26AUG161938KCLAA")


def test_doubleheader_legs_stay_separate_games():
    """
    Kalshi marks the legs G1/G2. Stripping that marker would merge two
    genuinely independent games into one cluster.
    """
    leg1 = nbt.physical_game_key("KXMLBGAME-26SEP041410DETCLEG1")
    leg2 = nbt.physical_game_key("KXMLBGAME-26SEP041915DETCLEG2")
    assert leg1 and leg2 and leg1 != leg2


def test_physical_game_key_never_guesses():
    for bad in (None, "", "NOPE", "KXMLBGAME-2Z", "KXMLBGAME-", 12345,
                "KXMLBGAME-26XXX152138KCLAA"):
        assert nbt.physical_game_key(bad) is None


def test_physical_game_key_agrees_with_the_ticker_start_reconstruction():
    """Both read the same embedded stamp, so they must agree on what a game is."""
    ticker = "KXMLBGAME-26AUG152138KCLAA"
    assert nbt.physical_game_key(ticker).startswith("26AUG152138")
    assert nbt.scheduled_start_from_event_ticker(ticker) is not None
