#!/usr/bin/env python3
"""
scripts/statcast_completed_game_catchup.py
==============================================
Hitter Projection Engine Phase 5 -- bounded, idempotent completed-game
Statcast catch-up. Reused by BOTH:
  (a) scripts/run_standalone_hitter_research.py (today's slate's own
      gamePks -- lib.research.statcast_pitch_store.load pitches for
      whichever of them have actually finished, before a hitter
      projection run), and
  (b) .github/workflows/statcast-postgame-archive.yml (a date-range scan
      independent of any single day's slate.json, so completed-game
      archiving keeps accumulating automatically even on a day nobody
      runs the standalone price check).

NEVER archives a game whose pitch log could be partial: `catch_up_games`
re-verifies each candidate's status with a FRESH per-game MLB feed
fetch (lib.edgelab.mlb_boxscore.fetch_game_feed/is_final_status) at
call time, never trusting a schedule listing (which can be momentarily
stale for a suspended/postponed-then-resumed game) or an input flag
alone. Already-archived games (lib.research.statcast_pitch_store.
has_game) are skipped before any network call at all, exactly matching
scripts/fetch_statcast_pitch_log.py's own idempotency contract, which
this module reuses (not reimplements) for the actual per-game ingest.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from lib.research.statcast_pitch_store import has_game  # noqa: E402
from scripts.fetch_statcast_pitch_log import fetch_and_ingest_game, fetch_json, game_pks_from_slate  # noqa: E402
from lib.edgelab.mlb_boxscore import fetch_game_feed, extract_game_status, is_final_status  # noqa: E402

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate={start}&endDate={end}&gameType=R"
_SCHEDULE_COMPLETED_STATES = frozenset({"Final", "Game Over", "Completed Early"})


def discover_completed_games_for_date_range(start_date: str, end_date: str) -> list:
    """
    Returns a sorted, deduped list of gamePks whose SCHEDULE-LISTED
    status is one of the completed states across [start_date, end_date]
    (inclusive, both 'YYYY-MM-DD'). Discovery only -- catch_up_games()
    below re-verifies each one's status with a fresh feed fetch before
    ever archiving it. Returns [] on any fetch failure (never raises,
    matching this repo's other non-fatal fetch-shell conventions).
    """
    url = MLB_SCHEDULE_URL.format(start=start_date, end=end_date)
    data = fetch_json(url)
    if not data:
        return []
    game_pks = []
    for day in data.get("dates", []):
        for g in day.get("games", []):
            status = (g.get("status") or {}).get("detailedState")
            if status in _SCHEDULE_COMPLETED_STATES:
                pk = g.get("gamePk")
                if pk is not None:
                    game_pks.append(pk)
    return sorted(set(game_pks))


def catch_up_games(game_pks: list, sleep_between: float = 0.0) -> dict:
    """
    For each candidate gamePk: ALREADY_ARCHIVED (zero network calls) if
    has_game() is already True; otherwise a fresh fetch_game_feed()
    status re-check -- DEFERRED (not archived) if that check says the
    game isn't genuinely Final yet, else delegates to
    fetch_and_ingest_game() (INGESTED / NO_PITCHES / FETCH_FAILED,
    exactly as that function already reports). Bounded and
    non-recursive -- exactly one feed check + at most one pitch-log
    fetch per candidate, never a season-wide scan.
    """
    results = []
    for game_pk in game_pks:
        if has_game(game_pk):
            results.append({"gamePk": game_pk, "status": "ALREADY_ARCHIVED"})
            continue

        feed = fetch_game_feed(game_pk)
        detailed_state = extract_game_status(feed)
        if not is_final_status(detailed_state):
            results.append({"gamePk": game_pk, "status": "DEFERRED", "detailedState": detailed_state})
            continue

        ingest_result = fetch_and_ingest_game(game_pk)
        ingest_result["gamePk"] = game_pk
        results.append(ingest_result)
        if sleep_between:
            time.sleep(sleep_between)

    summary = {
        "totalCandidates": len(game_pks),
        "alreadyArchived": sum(1 for r in results if r["status"] == "ALREADY_ARCHIVED"),
        "newlyArchived": sum(1 for r in results if r["status"] in ("INGESTED", "NO_PITCHES")),
        "failed": sum(1 for r in results if r["status"] == "FETCH_FAILED"),
        "deferred": sum(1 for r in results if r["status"] == "DEFERRED"),
        "results": results,
    }
    return summary


def catch_up_todays_slate(slate_path=None, sleep_between: float = 0.0) -> dict:
    """Convenience entry point for scripts/run_standalone_hitter_research.py -- reuses
    scripts/fetch_statcast_pitch_log.game_pks_from_slate rather than a second gamePk-extraction
    implementation. Most of today's own slate games will legitimately come back DEFERRED at a
    pregame run (they haven't been played yet) -- that's expected, not a failure."""
    return catch_up_games(game_pks_from_slate(slate_path), sleep_between=sleep_between)


def main(start_date=None, end_date=None, lookback_days=2, sleep_between=0.0):
    if start_date is None or end_date is None:
        today = datetime.now(tz=timezone.utc).date()
        end_date = end_date or (today - timedelta(days=1)).isoformat()
        start_date = start_date or (today - timedelta(days=lookback_days)).isoformat()

    discovered = discover_completed_games_for_date_range(start_date, end_date)
    summary = catch_up_games(discovered, sleep_between=sleep_between)
    summary["startDate"] = start_date
    summary["endDate"] = end_date
    summary["completedGamesDiscovered"] = len(discovered)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--lookback-days", type=int, default=2,
                         help="Used only when --start-date is omitted: scan the last N days ending yesterday (default 2, a safety net beyond a single missed daily run)")
    parser.add_argument("--sleep-between", type=float, default=0.0)
    args = parser.parse_args()
    result = main(start_date=args.start_date, end_date=args.end_date,
                  lookback_days=args.lookback_days, sleep_between=args.sleep_between)
    print(json.dumps(result, indent=2))
