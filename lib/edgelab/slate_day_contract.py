"""
lib/edgelab/slate_day_contract.py
=================================
The off-day contract: telling a VALID zero-game MLB date apart from a
pipeline that failed to collect a real slate.

WHY THIS EXISTS
---------------
2026-09-28 was an MLB off-day -- the gap between the regular season and the
Wild Card round. Every artifact the pipeline produced that day was correctly
empty: data/slate.json had ``games: []``, every Kalshi registry snapshot held
0 markets. And every surface that looked at them called it a failure:

  * scripts/post_fetch_gate.py wrote ``FAILED_STALE_DATE -- slate.json has
    no games``, exactly what it writes when the schedule fetch is down;
  * api/slate.js reports ``scheduleSource: 'none'`` both when the MLB Stats
    API says "no games today" and when both schedule sources failed;
  * three deterministic tests turned red, because they used "the latest
    committed live data" as a fixture and assumed it contains games.

An empty slate is not evidence of anything by itself. The only authority on
whether there SHOULD have been games is the MLB schedule for that date, so
every verdict here is a function of (artifact, schedule evidence):

  schedule says games > 0, artifact has them       -> GAME_DAY_COLLECTED  (ok)
  schedule says games > 0, artifact is empty       -> GAME_DAY_COLLECTION_FAILED
  schedule says 0 games,   artifact is empty       -> OFF_DAY             (ok)
  schedule says 0 games,   artifact has games      -> OFF_DAY_INCONSISTENT
  schedule unknown (fetch failed / malformed)      -> SCHEDULE_UNKNOWN
  artifact dated for another day than the evidence -> EVIDENCE_DATE_MISMATCH
  artifact missing its games/markets list or date  -> MALFORMED_ARTIFACT

Only GAME_DAY_COLLECTED and OFF_DAY are ``ok``. Missing schedule evidence is
NEVER read as an off-day: an outage of the schedule endpoint and a real
off-day look identical in every artifact, which is the whole problem.

Pure: no I/O, no network, no clock. The network adapter is
lib.edgelab.mlb_schedule.fetch_schedule_all_game_types.
"""

GAMES_SCHEDULED = "GAMES_SCHEDULED"
NO_GAMES_SCHEDULED = "NO_GAMES_SCHEDULED"
SCHEDULE_UNKNOWN = "SCHEDULE_UNKNOWN"

GAME_DAY_COLLECTED = "GAME_DAY_COLLECTED"
GAME_DAY_COLLECTION_FAILED = "GAME_DAY_COLLECTION_FAILED"
OFF_DAY = "OFF_DAY"
OFF_DAY_INCONSISTENT = "OFF_DAY_INCONSISTENT"
EVIDENCE_DATE_MISMATCH = "EVIDENCE_DATE_MISMATCH"
MALFORMED_ARTIFACT = "MALFORMED_ARTIFACT"

OK_VERDICTS = frozenset({GAME_DAY_COLLECTED, OFF_DAY})

#: Regular season plus every postseason round (Wild Card, Division Series,
#: League Championship, World Series). A postseason date is a game day; the
#: repo's older ``gameType=R`` schedule calls would call it an off-day.
SCHEDULE_GAME_TYPES = ("R", "F", "D", "L", "W")

#: A game on the schedule that will not be played that day. A date whose every
#: game is one of these has nothing to price or collect.
NOT_PLAYED_STATES = frozenset({"Postponed", "Cancelled"})

SCHEDULE_SOURCE = "statsapi.mlb.com/api/v1/schedule"


def schedule_evidence(date, response, fetched_at=None, source=SCHEDULE_SOURCE):
    """
    Pure: reduce one MLB Stats API schedule response to evidence for `date`.

    ``response`` is the parsed JSON, or None when the fetch failed. A response
    that does not carry a ``dates`` list is malformed and yields UNKNOWN --
    never NO_GAMES. The API answers a genuine off-day with ``dates: []``.
    """
    evidence = {
        "date": date,
        "source": source,
        "fetchedAt": fetched_at,
        "gameTypes": list(SCHEDULE_GAME_TYPES),
        "status": SCHEDULE_UNKNOWN,
        "scheduledGames": None,
        "playableGames": None,
        "reason": None,
    }
    if response is None:
        evidence["reason"] = "schedule fetch failed"
        return evidence
    if not isinstance(response, dict) or not isinstance(response.get("dates"), list):
        evidence["reason"] = "schedule response malformed (no 'dates' list)"
        return evidence

    scheduled = playable = 0
    for day in response["dates"]:
        if not isinstance(day, dict) or day.get("date") != date:
            continue
        for game in day.get("games") or []:
            if not isinstance(game, dict):
                continue
            if game.get("gameType") and game["gameType"] not in SCHEDULE_GAME_TYPES:
                continue
            scheduled += 1
            state = (game.get("status") or {}).get("detailedState")
            if state not in NOT_PLAYED_STATES:
                playable += 1

    evidence["scheduledGames"] = scheduled
    evidence["playableGames"] = playable
    evidence["status"] = GAMES_SCHEDULED if playable > 0 else NO_GAMES_SCHEDULED
    evidence["reason"] = ("%d playable MLB game(s) scheduled" % playable if playable
                          else "no playable MLB games scheduled (%d on the schedule)" % scheduled)
    return evidence


def playable_games_by_date(response, start_date, end_date):
    """
    Pure: reduce one MLB Stats API schedule RANGE response to
    {date: playable game count} for EVERY date in [start_date, end_date]
    (ISO strings, inclusive). A date absent from the response's ``dates``
    list has 0 games -- the API omits days with nothing scheduled. Counting
    rules are schedule_evidence()'s: only SCHEDULE_GAME_TYPES, and games in
    NOT_PLAYED_STATES do not count.

    Returns None when the response is missing or malformed (no ``dates``
    list, an unparseable bound): an unknown schedule is never "no games".
    """
    from datetime import date, timedelta
    if not isinstance(response, dict) or not isinstance(response.get("dates"), list):
        return None
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except (TypeError, ValueError):
        return None
    if end < start:
        return None
    counts = {}
    d = start
    while d <= end:
        counts[d.isoformat()] = 0
        d += timedelta(days=1)
    for day in response["dates"]:
        if not isinstance(day, dict) or day.get("date") not in counts:
            continue
        for game in day.get("games") or []:
            if not isinstance(game, dict):
                continue
            if game.get("gameType") and game["gameType"] not in SCHEDULE_GAME_TYPES:
                continue
            if (game.get("status") or {}).get("detailedState") in NOT_PLAYED_STATES:
                continue
            counts[day["date"]] += 1
    return counts


def _verdict(verdict, reason, **detail):
    return dict({"verdict": verdict, "ok": verdict in OK_VERDICTS, "reason": reason}, **detail)


def _classify(artifact, items_key, date_key, evidence, what):
    if not isinstance(artifact, dict) or not isinstance(artifact.get(items_key), list):
        return _verdict(MALFORMED_ARTIFACT, "%s has no '%s' list" % (what, items_key))
    artifact_date = artifact.get(date_key)
    if not artifact_date:
        return _verdict(MALFORMED_ARTIFACT, "%s has no '%s' field" % (what, date_key))
    count = len(artifact[items_key])

    status = (evidence or {}).get("status")
    if status not in (GAMES_SCHEDULED, NO_GAMES_SCHEDULED):
        return _verdict(SCHEDULE_UNKNOWN,
                        "cannot tell an off-day from a collection failure: schedule evidence "
                        "is %s" % ((evidence or {}).get("reason") or "absent"),
                        count=count)
    if evidence.get("date") != artifact_date:
        return _verdict(EVIDENCE_DATE_MISMATCH,
                        "%s is dated %s but the schedule evidence is for %s"
                        % (what, artifact_date, evidence.get("date")), count=count)

    if status == GAMES_SCHEDULED:
        if count > 0:
            return _verdict(GAME_DAY_COLLECTED, "%d %s on a %d-game day"
                            % (count, items_key, evidence.get("playableGames") or 0), count=count)
        return _verdict(GAME_DAY_COLLECTION_FAILED,
                        "%s has no %s but the MLB schedule has %d playable game(s) on %s"
                        % (what, items_key, evidence.get("playableGames") or 0, artifact_date),
                        count=count)
    if count == 0:
        return _verdict(OFF_DAY, "no MLB games scheduled on %s; an empty %s is correct"
                        % (artifact_date, what), count=count)
    return _verdict(OFF_DAY_INCONSISTENT,
                    "%s has %d %s on %s, a date the MLB schedule says has no games"
                    % (what, count, items_key, artifact_date), count=count)


def classify_slate_day(slate, evidence):
    """Pure: a data/slate.json-shaped dict against schedule evidence."""
    return _classify(slate, "games", "date", evidence, "slate")


def classify_snapshot_day(snapshot, evidence):
    """Pure: a data/kalshi_registry_snapshots/kalshi_search_*.json dict
    against schedule evidence."""
    return _classify(snapshot, "markets", "date", evidence, "Kalshi snapshot")
