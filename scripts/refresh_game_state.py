#!/usr/bin/env python3
"""
Refresh the authoritative live state of today's MLB slate games from the public MLB Stats API
schedule endpoint, into data/slates/<date>/game_state.json.

WHY (2026-10-09 production review): the slate's per-game `status` is a snapshot from the official
daily run (13:09Z on 2026-10-08). Later reruns keep it, and the only producer that could mark a game
FINAL was the daily postgame settlement at ~13:00Z the next day. The app export's first-pitch gate
therefore held CLE@CWS (first pitch 2026-10-09T00:00Z) at LIVE for thirteen hours after it ended,
and the published board, health and every downstream consumer carried a stale LIVE game.

WHAT: a read-only adapter. For the slate date it fetches the schedule (every game type: the
postseason is in play), and writes, per gamePk, the feed's own abstractGameState / detailedState /
gameDate with the capture time. scripts/app_export.py reads this document as the authoritative
event status between slate fetches and settlement (settlement evidence still wins for FINAL), and
scripts/ci/pipeline_watchdog.py dispatches this refresh when a started game has no final word yet.

NEVER: a failed or empty fetch writes nothing (the previous document stays, and the export says so
through its own staleness rules); no status is invented; no model, ledger, bet or secret is touched.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from lib.edgelab import mlb_schedule  # noqa: E402
from lib.edgelab.production_date import et_date_for_instant  # noqa: E402

SOURCE = "statsapi.mlb.com/api/v1/schedule"
SCHEMA = "mlb_game_state/1.0.0"


def game_state_from_schedule(schedule_json, *, date, as_of_iso):
    """Pure. The game_state document from a raw schedule-by-date response. Games without a gamePk
    are skipped (never fabricated); a response with no games yields an empty `games` map, which the
    caller must NOT write over a previous document (see main)."""
    games = {}
    for day in (schedule_json or {}).get("dates") or []:
        for g in day.get("games") or []:
            pk = g.get("gamePk")
            if not pk:
                continue
            st = g.get("status") or {}
            games[str(pk)] = {
                "abstract_game_state": st.get("abstractGameState"),
                "detailed_state": st.get("detailedState"),
                "coded_game_state": st.get("codedGameState"),
                "start_time_utc": g.get("gameDate"),
                "game_type": g.get("gameType"),
                "game_number": g.get("gameNumber"),
            }
    return {"schema_version": SCHEMA, "date": date, "as_of": as_of_iso, "source": SOURCE, "games": games}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=None, help="slate date (YYYY-MM-DD, ET); default today ET")
    ap.add_argument("--data-root", default=os.path.join(REPO_ROOT, "data"))
    ap.add_argument("--now", default=None, help="ISO UTC override (tests)")
    args = ap.parse_args(argv)
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else datetime.now(timezone.utc)
    date = args.date or et_date_for_instant(now)
    raw = mlb_schedule.fetch_schedule_all_game_types(date)
    if raw is None:
        print(f"[refresh_game_state] {date}: schedule fetch failed; leaving the previous document in place", file=sys.stderr)
        return 0
    doc = game_state_from_schedule(raw, date=date, as_of_iso=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    if not doc["games"]:
        print(f"[refresh_game_state] {date}: the schedule lists no game; nothing written", file=sys.stderr)
        return 0
    path = os.path.join(args.data_root, "slates", date, "game_state.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    states = {}
    for g in doc["games"].values():
        states[g["abstract_game_state"]] = states.get(g["abstract_game_state"], 0) + 1
    print(f"[refresh_game_state] {date}: {len(doc['games'])} game(s) {states} -> {os.path.relpath(path, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
