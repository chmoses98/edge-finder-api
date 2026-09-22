#!/usr/bin/env python3
"""
tests/edgelab/test_capture_health_report.py
===========================================
Coverage for scripts/edgelab/build_capture_health_report.py.

The report exists to keep REQUESTED and DELIVERED apart. For 13
consecutive days the schedule asked for 28-78 capture slots and the
archive received 5-7, and because nothing reported the two numbers
side by side, CLV was scored for those days against quotes a median
14.4 hours before first pitch.

So the tests that matter are the ones that stop the two numbers from
quietly merging: a window is delivered only when an archived pregame
capture covers it, an off-day never dilutes the rate, and a post-start
snapshot is never counted as coverage.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.edgelab.build_capture_health_report import (
    SCHEMA_VERSION, evidence_from_observations, replay_instant, roll_up, summarize_day,
)

DATE = "2026-09-20"
START = "2026-09-20T17:40:00Z"


def _game(game_id="g1", start=START):
    return {"gameId": game_id, "scheduledStart": start, "awayTeam": "CHC", "homeTeam": "CIN"}


def _obs(ticker, captured, start=START, **kw):
    row = {"marketTicker": ticker, "capturedAt": captured, "scheduledStart": start,
           "yesAsk": 0.42, "marketStatus": "active"}
    row.update(kw)
    return row


def _day(games=None, captures=(), evidence=None):
    return summarize_day(DATE, games if games is not None else [_game()],
                         list(captures), evidence or _empty_evidence())


def _empty_evidence():
    return evidence_from_observations([])


# --------------------------------------------------------------------------
# requested vs delivered
# --------------------------------------------------------------------------

def test_a_day_with_no_captures_reports_every_window_as_requested_and_none_delivered():
    """The exact shape of the 13-day outage: the ask stands, nothing arrives."""
    day = _day()
    assert day["windows"]["targeted"] == 3          # T-5, T-15, T-30
    assert day["windows"]["delivered"] == 0
    assert day["windows"]["deliveredPct"] == 0.0
    assert day["delivery"]["capturesArchived"] == 0


def test_delivered_counts_only_windows_an_archived_capture_actually_covered():
    """T-35 reaches the T-30 band and nothing tighter: the other two stay unmet."""
    day = _day(captures=["2026-09-20T17:05:00Z"])
    assert day["windows"]["targeted"] == 3
    assert day["windows"]["delivered"] == 1
    assert day["windows"]["deliveredPct"] == 33.3


def test_one_near_close_capture_serves_every_window_behind_it():
    """The three windows are nested attempts at the same TRUE_CLOSE quote.
    A capture at T-14 is strictly better than the T-30 fallback it skipped,
    so scoring it as one window out of three would understate real coverage."""
    day = _day(captures=["2026-09-20T17:26:00Z"])
    assert day["windows"]["delivered"] == 3
    assert day["windows"]["deliveredPct"] == 100.0
    assert day["delivery"]["capturesArchived"] == 1  # one capture, three windows


def test_a_post_start_snapshot_is_never_counted_as_delivery():
    """A snapshot exists in the archive, but it is not pregame evidence."""
    day = _day(captures=["2026-09-20T18:10:00Z"])
    assert day["delivery"]["capturesArchived"] == 1  # it is on disk, and we say so
    assert day["windows"]["delivered"] == 0          # and it covers nothing
    assert day["windows"]["missedLate"] == 3


def test_scheduler_delay_is_reported_against_each_window_and_may_be_negative():
    """Early is fine; the number is recorded rather than clamped, so drift shows."""
    late = _day(captures=["2026-09-20T17:29:00Z"])   # four minutes behind T-15
    assert late["delivery"]["schedulerDelaySeconds"]["median"] == 240.0
    early = _day(captures=["2026-09-20T17:23:00Z"])  # two minutes ahead of it
    assert early["delivery"]["schedulerDelaySeconds"]["median"] == -120.0


def test_a_game_with_no_start_time_is_skipped_not_silently_scored_as_missed():
    day = _day(games=[_game(), _game("g2", start=None)])
    assert day["slate"]["games"] == 2
    assert day["slate"]["gamesWithoutStartTime"] == 1
    assert day["windows"]["skippedNoStartTime"] == 3
    assert day["windows"]["targeted"] == 3           # only the resolvable game


# --------------------------------------------------------------------------
# evidence classification
# --------------------------------------------------------------------------

def test_market_evidence_splits_true_close_from_pre_close_only():
    evidence = evidence_from_observations([
        _obs("A", "2026-09-20T17:20:00Z"),           # 20 min out -> TRUE_CLOSE
        _obs("B", "2026-09-20T06:00:00Z"),           # ~12 h out  -> PRE_CLOSE
    ])
    assert evidence["marketsArchived"] == 2
    assert evidence["trueCloseMarkets"] == 1
    assert evidence["preCloseOnlyMarkets"] == 1


def test_the_nearest_pregame_observation_wins_for_a_market():
    evidence = evidence_from_observations([
        _obs("A", "2026-09-20T06:00:00Z"),
        _obs("A", "2026-09-20T17:25:00Z"),
        _obs("A", "2026-09-20T19:00:00Z"),           # post-start, ignored
    ])
    assert evidence["marketsArchived"] == 1
    assert evidence["trueCloseMarkets"] == 1
    assert evidence["secondsBeforeStart"]["median"] == 900.0


def test_a_market_seen_only_after_first_pitch_has_no_prestart_evidence():
    evidence = evidence_from_observations([_obs("A", "2026-09-20T19:00:00Z")])
    assert evidence["marketsArchived"] == 1
    assert evidence["marketsWithNoPrestartEvidence"] == 1
    assert evidence["trueCloseMarkets"] == 0
    assert evidence["preCloseOnlyMarkets"] == 0


def test_a_market_with_no_resolvable_start_is_reported_separately_not_as_covered():
    """Unknown start is its own bucket; it must never inflate either side."""
    evidence = evidence_from_observations([_obs("A", "2026-09-20T17:25:00Z", start=None)])
    assert evidence["startUnresolvedMarkets"] == 1
    assert evidence["trueCloseMarkets"] == 0
    assert evidence["preCloseOnlyMarkets"] == 0


def test_a_quote_with_no_price_at_all_is_not_evidence():
    evidence = evidence_from_observations([
        {"marketTicker": "A", "capturedAt": "2026-09-20T17:25:00Z", "scheduledStart": START},
    ])
    assert evidence["marketsWithNoPrestartEvidence"] == 1
    assert evidence["trueCloseMarkets"] == 0


def test_a_closed_market_is_not_pregame_evidence():
    evidence = evidence_from_observations(
        [_obs("A", "2026-09-20T17:25:00Z", marketStatus="closed")])
    assert evidence["marketsWithNoPrestartEvidence"] == 1


# --------------------------------------------------------------------------
# trailing roll-up
# --------------------------------------------------------------------------

def test_an_off_day_never_dilutes_the_delivery_rate():
    """A quiet week must not be able to hide a bad night."""
    played = _day(captures=["2026-09-20T17:26:00Z"])
    off = _day(games=[])
    assert roll_up([played, off, off], 3) == roll_up([played], 3) | {"days": 3}


def test_roll_up_totals_are_sums_of_the_days_not_averages_of_rates():
    busy = summarize_day(DATE, [_game("g%d" % i) for i in range(10)],
                         ["2026-09-20T17:05:00Z"], _empty_evidence())   # T-30 only
    quiet = _day(captures=["2026-09-20T17:26:00Z"])                     # all three
    trailing = roll_up([busy, quiet], 2)
    assert trailing["windowsTargeted"] == 33
    assert trailing["windowsDelivered"] == 13       # 10 of 30, then 3 of 3
    # Averaging the two days' rates would read 66.7% and flatter the worse day.
    assert trailing["windowDeliveryPct"] == 39.4
    assert trailing["datesWithASlate"] == 2


def test_true_close_share_is_reported_against_markets_archived():
    evidence = evidence_from_observations(
        [_obs("A", "2026-09-20T17:20:00Z"), _obs("B", "2026-09-20T06:00:00Z")])
    trailing = roll_up([_day(evidence=evidence)], 1)
    assert trailing["marketsArchived"] == 2
    assert trailing["trueCloseMarkets"] == 1
    assert trailing["trueClosePct"] == 50.0


def test_rates_are_none_rather_than_zero_when_there_is_nothing_to_divide_by():
    """A week with no slate is unknown coverage, not perfect or total failure."""
    trailing = roll_up([_day(games=[])], 1)
    assert trailing["windowDeliveryPct"] is None
    assert trailing["trueClosePct"] is None
    assert trailing["datesWithASlate"] == 0


# --------------------------------------------------------------------------
# the replay instant
# --------------------------------------------------------------------------

def test_a_late_game_is_judged_after_its_own_first_pitch_not_at_midnight_utc():
    """A west-coast game on `date` starts the NEXT day in UTC. Judged at
    23:59:59Z its windows are still NOT_YET_DUE, so they fall out of both
    the delivered and the missed column -- shrinking the miss count on
    exactly the late games that were hardest to cover."""
    late = _game("g1", start="2026-09-21T01:10:00Z")
    day = summarize_day(DATE, [late], [], _empty_evidence())
    assert day["windows"]["targeted"] == 3
    assert day["windows"]["missedLate"] == 3
    assert day["windows"]["unresolvedAtReplay"] == 0


def test_every_targeted_window_resolves_to_delivered_or_missed():
    """The two columns must account for the whole ask, on any slate."""
    games = [_game("g1"), _game("g2", start="2026-09-21T01:10:00Z"),
             _game("g3", start="2026-09-20T23:05:00Z")]
    day = summarize_day(DATE, games, ["2026-09-20T17:26:00Z"], _empty_evidence())
    w = day["windows"]
    assert w["delivered"] + w["missedLate"] == w["targeted"] == 9
    assert w["unresolvedAtReplay"] == 0


def test_replay_never_falls_before_end_of_the_utc_day():
    """An all-afternoon slate still gets judged on the whole day's captures."""
    assert replay_instant(DATE, [_game()]).isoformat() == "2026-09-20T23:59:59+00:00"
    assert replay_instant(DATE, []).isoformat() == "2026-09-20T23:59:59+00:00"


def test_a_game_with_an_unparseable_start_does_not_shorten_the_replay():
    assert replay_instant(DATE, [_game("g1", start="not-a-timestamp"),
                                 _game("g2", start="2026-09-21T01:10:00Z")]
                          ).isoformat() == "2026-09-21T01:11:00+00:00"


def test_schema_version_is_pinned():
    assert SCHEMA_VERSION == "capture_health_v1"
