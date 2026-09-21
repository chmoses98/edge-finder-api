#!/usr/bin/env python3
"""
tests/edgelab/test_near_close_plan.py
=====================================
Coverage for lib/edgelab/near_close_plan.py -- the schedule-aware
near-close capture coordinator.

The cases that matter are the refusals: a post-start wake must never
produce a capture that could be mistaken for pregame evidence, and a
game with no known start must never be guessed at.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.near_close_plan import (
    ACTION_ALREADY_COVERED, ACTION_CAPTURE, ACTION_MISSED_LATE,
    ACTION_NOT_YET_DUE, ACTION_SKIPPED_NO_START,
    build_capture_plan, window_plan_for_game,
)

START = "2026-09-20T17:40:00Z"


def _game(game_id="g1", start=START, away="CHC", home="CIN"):
    return {"gameId": game_id, "scheduledStart": start, "awayTeam": away, "homeTeam": home}


def _actions(plans):
    return {p["targetWindow"]: p["action"] for p in plans}


def test_normal_t15_capture_is_due_in_its_window():
    plans = window_plan_for_game(_game(), "2026-09-20T17:25:00Z")
    assert _actions(plans)["T_MINUS_15"] == ACTION_CAPTURE
    t15 = next(p for p in plans if p["targetWindow"] == "T_MINUS_15")
    assert t15["intendedCaptureAt"] == "2026-09-20T17:25:00Z"
    assert t15["schedulerDelaySeconds"] == 0.0


def test_a_late_runner_still_serves_the_window_and_records_its_delay():
    """GitHub is habitually late; the window must survive that, visibly."""
    plans = window_plan_for_game(_game(), "2026-09-20T17:31:00Z")
    t15 = next(p for p in plans if p["targetWindow"] == "T_MINUS_15")
    assert t15["action"] == ACTION_CAPTURE
    assert t15["schedulerDelaySeconds"] == 360.0     # six minutes behind intent


def test_missed_t15_is_recovered_at_t5():
    """The T-5 window is its own opportunity, not a consolation prize."""
    plans = window_plan_for_game(_game(), "2026-09-20T17:36:00Z")
    a = _actions(plans)
    assert a["T_MINUS_5"] == ACTION_CAPTURE
    assert a["T_MINUS_15"] == ACTION_CAPTURE        # still open under tolerance
    assert build_capture_plan([_game()], "2026-09-20T17:36:00Z")["shouldCapture"] is True


def test_after_first_pitch_every_window_refuses():
    plans = window_plan_for_game(_game(), "2026-09-20T17:41:00Z")
    assert set(_actions(plans).values()) == {ACTION_MISSED_LATE}
    plan = build_capture_plan([_game()], "2026-09-20T17:41:00Z")
    assert plan["shouldCapture"] is False
    assert "not pregame evidence" in plans[0]["reason"]


def test_a_post_start_capture_never_counts_as_covering_a_window():
    """The archive may hold a later snapshot; it is not pregame evidence."""
    plans = window_plan_for_game(_game(), "2026-09-20T17:30:00Z",
                                 existing_capture_times=["2026-09-20T17:50:00Z"])
    assert _actions(plans)["T_MINUS_15"] == ACTION_CAPTURE


def test_an_existing_prestart_capture_suppresses_duplicate_work():
    plans = window_plan_for_game(_game(), "2026-09-20T17:30:00Z",
                                 existing_capture_times=["2026-09-20T17:26:00Z"])
    t15 = next(p for p in plans if p["targetWindow"] == "T_MINUS_15")
    assert t15["action"] == ACTION_ALREADY_COVERED
    assert t15["coveredSecondsBeforeStart"] == 840.0


def test_duplicate_coordinator_wakes_are_idempotent():
    """A second wake with the first wake's capture archived does nothing."""
    first = build_capture_plan([_game()], "2026-09-20T17:25:00Z")
    assert first["shouldCapture"] is True
    second = build_capture_plan([_game()], "2026-09-20T17:26:00Z",
                                existing_capture_times=["2026-09-20T17:25:00Z"])
    assert second["shouldCapture"] is False


def test_too_early_is_not_yet_due_rather_than_a_wasted_capture():
    plan = build_capture_plan([_game()], "2026-09-20T15:00:00Z")
    assert plan["shouldCapture"] is False
    assert plan["windowActionCounts"][ACTION_NOT_YET_DUE] == 3


def test_a_game_with_no_start_time_is_skipped_never_guessed():
    plans = window_plan_for_game(_game(start=None), "2026-09-20T17:25:00Z")
    assert set(_actions(plans).values()) == {ACTION_SKIPPED_NO_START}
    assert all(p["intendedCaptureAt"] is None for p in plans)


def test_simultaneous_starts_cost_one_capture_not_one_per_game():
    """The capture is a whole-universe snapshot; fan-out must not scale."""
    games = [_game("g%d" % i, away="A%d" % i, home="H%d" % i) for i in range(8)]
    plan = build_capture_plan(games, "2026-09-20T17:25:00Z")
    assert plan["shouldCapture"] is True
    assert len(plan["gamesDue"]) == 8          # all eight need it...
    # ...and the coordinator's answer is still a single boolean, so the
    # workflow fires once.
    assert isinstance(plan["shouldCapture"], bool)


def test_a_doubleheader_is_two_independent_games_with_their_own_windows():
    legs = [_game("dh-1", "2026-09-20T17:10:00Z"), _game("dh-2", "2026-09-20T21:10:00Z")]
    at_first = build_capture_plan(legs, "2026-09-20T16:55:00Z")
    assert at_first["gamesDue"] == ["dh-1"]     # leg 2 is hours away
    at_second = build_capture_plan(legs, "2026-09-20T20:55:00Z")
    assert at_second["gamesDue"] == ["dh-2"]    # leg 1 long finished


def test_a_changed_start_time_is_re_evaluated_from_the_new_start():
    """Plans are derived per wake, so a pushed-back start simply re-plans."""
    delayed = _game(start="2026-09-20T19:40:00Z")
    assert build_capture_plan([delayed], "2026-09-20T17:25:00Z")["shouldCapture"] is False
    assert build_capture_plan([delayed], "2026-09-20T19:25:00Z")["shouldCapture"] is True


def test_an_empty_slate_asks_for_nothing():
    plan = build_capture_plan([], "2026-09-20T17:25:00Z")
    assert plan["shouldCapture"] is False
    assert plan["gamesConsidered"] == 0
    assert plan["dueWindows"] == []


def test_a_slate_with_no_start_times_at_all_never_fires_a_capture():
    plan = build_capture_plan([_game(start=None), _game("g2", start=None)],
                              "2026-09-20T17:25:00Z")
    assert plan["shouldCapture"] is False
    assert plan["gamesWithStartTime"] == 0


def test_mixed_slate_fires_only_for_the_game_that_needs_it():
    games = [_game("due", "2026-09-20T17:40:00Z"),
             _game("early", "2026-09-20T23:40:00Z"),
             _game("started", "2026-09-20T16:00:00Z"),
             _game("unknown", None)]
    plan = build_capture_plan(games, "2026-09-20T17:25:00Z")
    assert plan["gamesDue"] == ["due"]
    counts = plan["windowActionCounts"]
    assert counts[ACTION_SKIPPED_NO_START] == 3
    assert counts[ACTION_MISSED_LATE] == 3
