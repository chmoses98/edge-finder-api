#!/usr/bin/env python3
"""
lib/edgelab/near_close_plan.py
==============================
PHASE 4/5: decide WHEN a near-close market capture is actually worth
firing, from game start times rather than from cron hope.

WHY THIS EXISTS

The capture workflows request a dense cadence -- clv_capture.yml every 10
minutes, capture-snapshots-scheduled.yml and edgelab-capture.yml every 30
-- which on paper is 28-78 slots a day. What the archive actually holds
is 5-7 timestamped snapshots per day, every day, for 13 consecutive days,
displaced from their slots and clustered away from the 17:00-23:00 UTC
window when MLB games start. GitHub drops and delays scheduled workflows,
and asking for more slots does not make more arrive.

The existing recovery workflow cannot close this gap either.
snapshot-capture-check.yml runs once daily at 06:30 UTC, after the games
are over, and rebuilds EdgeLab *snapshot records* from source data still
on disk. A live Kalshi quote at T-15 is not on disk once T-15 has passed,
so a missed near-close window is not recoverable after the fact. It has
to be captured at the time or not at all.

THE APPROACH

Stop trying to blanket the day. A coordinator wakes on a coarse cron,
asks this module which games are approaching a target window and have no
capture covering it yet, and dispatches the canonical unfiltered capture
path only when the answer is non-empty. One capture serves every game
whose window is open at that moment, because the capture is a
whole-universe snapshot, not a per-game fetch -- so the fan-out is
bounded by wake frequency, never by game count.

WHAT IT REFUSES TO DO

* It never treats a post-start moment as a capture opportunity. A window
  whose game has already begun is MISSED_LATE, and the plan says so
  rather than firing a capture whose quotes could be mistaken for
  pregame evidence.
* It never invents a start time. A game without one is SKIPPED_NO_START.
* It never claims a window is covered by a snapshot taken after first
  pitch.

Pure functions only: no clock, no network, no filesystem. The caller
passes `now`, which is what makes every case here testable.
"""

from datetime import datetime, timedelta

# Target windows, tightest first. T-15 is primary: it is the tightest
# window a 30-minute scheduled cadence can realistically hit when the
# runner is late, and TRUE_CLOSE (<=30 min, see
# docs/EDGELAB_CLOSING_QUOTE_POLICY.md) is satisfied by anything here.
# T-5 is best-effort; T-30 is the fallback that still earns TRUE_CLOSE.
TARGET_WINDOWS = (("T_MINUS_5", 5), ("T_MINUS_15", 15), ("T_MINUS_30", 30))

# How early a capture may fire and still count for a window. A capture at
# T-22 covers the T-15 target: it is nearer the close than the T-30
# fallback and still pre-start. Without a tolerance a late runner would
# satisfy nothing at all, which is the failure this module exists to fix.
WINDOW_TOLERANCE_MINUTES = 10

ACTION_CAPTURE = "CAPTURE"
ACTION_ALREADY_COVERED = "ALREADY_COVERED"
ACTION_NOT_YET_DUE = "NOT_YET_DUE"
ACTION_MISSED_LATE = "MISSED_LATE"
ACTION_SKIPPED_NO_START = "SKIPPED_NO_START"


def _parse(ts):
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def _iso(dt):
    return dt.astimezone(tz=dt.tzinfo).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def window_plan_for_game(game, now, existing_capture_times=(),
                         tolerance_minutes=WINDOW_TOLERANCE_MINUTES):
    """
    Pure. What each target window for ONE game needs right now.

    game: {"gameId", "scheduledStart", "awayTeam", "homeTeam", "status"}
    now: datetime -- the coordinator's wake time.
    existing_capture_times: capture timestamps already archived today.

    Returns a list of window dicts, each carrying its own action and the
    evidence behind it, so a health report can say WHY a window was or
    was not served rather than only that it was missing.
    """
    start = _parse(game.get("scheduledStart"))
    now = _parse(now)
    base = {
        "gameId": game.get("gameId"),
        "matchup": ("%s@%s" % (game.get("awayTeam"), game.get("homeTeam"))
                    if game.get("awayTeam") and game.get("homeTeam") else None),
        "scheduledStart": _iso(start),
    }
    if start is None:
        return [dict(base, targetWindow=label, action=ACTION_SKIPPED_NO_START,
                     intendedCaptureAt=None, minutesToStart=None,
                     reason="no scheduled start time for this game")
                for label, _ in TARGET_WINDOWS]

    captures = sorted(c for c in (_parse(t) for t in existing_capture_times) if c is not None)
    minutes_to_start = (start - now).total_seconds() / 60.0
    plans = []
    for label, offset in TARGET_WINDOWS:
        intended = start - timedelta(minutes=offset)
        # A capture covers this window if it is pre-start AND no earlier
        # than the window's own tolerance band. Post-start captures are
        # excluded here, not merely down-ranked.
        earliest = intended - timedelta(minutes=tolerance_minutes)
        covered = [c for c in captures if earliest <= c < start]
        plan = dict(base, targetWindow=label, minutesToStart=round(minutes_to_start, 2),
                    intendedCaptureAt=_iso(intended))
        if covered:
            plan.update(action=ACTION_ALREADY_COVERED,
                        coveredBy=_iso(covered[-1]),
                        coveredSecondsBeforeStart=round((start - covered[-1]).total_seconds(), 1),
                        reason="an archived capture already falls inside this window")
        elif now >= start:
            plan.update(action=ACTION_MISSED_LATE,
                        reason="first pitch has passed; a capture now is not pregame "
                               "evidence and must never be used as one")
        elif now < earliest:
            plan.update(action=ACTION_NOT_YET_DUE,
                        reason="window opens at %s" % _iso(earliest))
        else:
            plan.update(action=ACTION_CAPTURE,
                        schedulerDelaySeconds=round((now - intended).total_seconds(), 1),
                        reason="window is open, pre-start, and nothing covers it")
        plans.append(plan)
    return plans


def build_capture_plan(games, now, existing_capture_times=(),
                       tolerance_minutes=WINDOW_TOLERANCE_MINUTES):
    """
    Pure. The coordinator's whole decision for one wake.

    `shouldCapture` is true when ANY game has an open, uncovered,
    pre-start window. One capture then serves all of them: the canonical
    capture path snapshots the entire market universe in a single fetch,
    so simultaneous starts cost one capture, not N. That is what keeps a
    schedule-aware coordinator from becoming a workflow storm.
    """
    now = _parse(now)
    windows = []
    for game in games:
        windows.extend(window_plan_for_game(game, now, existing_capture_times, tolerance_minutes))

    due = [w for w in windows if w["action"] == ACTION_CAPTURE]
    by_action = {}
    for w in windows:
        by_action[w["action"]] = by_action.get(w["action"], 0) + 1
    delays = sorted(w["schedulerDelaySeconds"] for w in due if "schedulerDelaySeconds" in w)

    return {
        "evaluatedAt": _iso(now),
        "gamesConsidered": len(games),
        "gamesWithStartTime": sum(1 for g in games if _parse(g.get("scheduledStart"))),
        "shouldCapture": bool(due),
        "dueWindows": due,
        "windows": windows,
        "windowActionCounts": by_action,
        "gamesDue": sorted({w["gameId"] for w in due if w["gameId"]}),
        "reason": ("%d window(s) open and uncovered" % len(due)) if due
                  else "no open uncovered pre-start window at this wake",
        "maxSchedulerDelaySeconds": delays[-1] if delays else None,
    }
