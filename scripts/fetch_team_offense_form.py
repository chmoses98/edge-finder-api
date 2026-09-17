#!/usr/bin/env python3
"""
scripts/fetch_team_offense_form.py
======================================
Capture every MLB team's RECENT OFFENSIVE FORM as a distribution, not as
a single rolling mean, and write it to data/team_offense_form.json for
scripts/enrich_data.py to attach to each slate game.

    MIL  L7: 5.1 avg | 3.0 med | 3,4,2,20,5,1,1 | 3/7 >=4 | 2/7 >=5 |
             max 20 (56% of L7 runs) | OUTLIER-DEPENDENT | TT overs 2/7

The slate has only ever carried `last7RpG` / `last15RpG` (calendar-day
means from api/teamstats.js). A mean over a handful of games is
dominated by its largest value, so a single 20-run night can make an
offense that has not cleared 5 runs in any other recent game read as
"hot". This capture makes that impossible to miss: the actual scores,
the median, threshold-clear counts, the max and its share of the window,
a trimmed mean, an EWMA, and -- where this repository's own Kalshi
archive supports it -- how often the team beat its OWN closing team
total.

WHAT IT DOES NOT DO
-------------------
It changes no production weight. scripts/enrich_data.py's
compute_offense_baseline() (the L7/L15/season blend that feeds run
projections) is untouched and does not read this file. Everything here
is additive handicapping CONTEXT -- which is precisely the status the
research findings support (docs/RESEARCH_OFFENSIVE_FORM.md: raw recent
form showed no out-of-sample predictive lift, so nothing about it has
been promoted into the model).

SOURCES
-------
  runs        ONE ranged MLB Stats API schedule call
              (lib.edgelab.mlb_schedule.fetch_schedule_range -- this
              repository's single canonical schedule adapter), parsed by
              the existing extract_team_games_from_schedule().
  team totals this repository's own archived Kalshi observations
              (data/edgelab/observations/<date>.jsonl[.gz]), turned into
              a per-date implied line by lib.team_total_line_history.
              NO Kalshi API call is made here. A date with no usable
              ladder simply has no line for that game -- never imputed.

Usage:
    python3 scripts/fetch_team_offense_form.py [--as-of YYYY-MM-DD] [--lookback-days 30]
    python3 scripts/fetch_team_offense_form.py --no-market-lines
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.edgelab import mlb_schedule, storage
from lib.edgelab.bullpen_usage import COMPLETED_STATUSES
from lib.team_offense_form import FORM_WINDOWS, build_form, format_form_line, hot_label
from lib.team_total_line_history import margin_vs_line, team_lines_for_date

OUTPUT_PATH = os.path.join("data", "team_offense_form.json")

# Enough calendar days to fill the widest game window (10) with margin
# for off-days, without pulling a whole season every slate run.
DEFAULT_LOOKBACK_DAYS = 30


def parse_team_game_log(schedule_json):
    """
    Pure. {teamAbbr: [ {date, gamePk, gameNumber, runsScored, runsAllowed,
    opponent, side}, ... chronological ... ]} from a ranged schedule
    response. Only COMPLETED games with a recorded score are included --
    a postponed, suspended or in-progress game is skipped, never scored
    as 0.
    """
    by_team = {}
    for date_entry in (schedule_json or {}).get("dates", []):
        date = date_entry.get("date")
        for game in date_entry.get("games", []):
            if (game.get("status") or {}).get("detailedState") not in COMPLETED_STATUSES:
                continue
            teams = game.get("teams") or {}
            for side, opposite in (("away", "home"), ("home", "away")):
                own, opp = teams.get(side) or {}, teams.get(opposite) or {}
                team_id = (own.get("team") or {}).get("id")
                abbr = mlb_schedule.TEAM_ID_TO_ABBR.get(team_id)
                if not abbr or own.get("score") is None:
                    continue
                by_team.setdefault(abbr, []).append({
                    "date": date,
                    "gamePk": game.get("gamePk"),
                    "gameNumber": game.get("gameNumber") or 1,
                    "side": side,
                    "runsScored": int(own["score"]),
                    "runsAllowed": int(opp["score"]) if opp.get("score") is not None else None,
                    "opponent": mlb_schedule.TEAM_ID_TO_ABBR.get((opp.get("team") or {}).get("id")),
                })
    for abbr in by_team:
        by_team[abbr].sort(key=lambda g: (g["date"] or "", g["gameNumber"], g["gamePk"] or 0))
    return by_team


def load_observations_for_date(date):
    """Already-archived Kalshi observations for one date. Empty when that
    partition does not exist -- never a fetch, never an error."""
    path = storage.resolve_partition_path("observations", date)
    if not os.path.exists(path):
        path = storage.partition_path("observations", date, compressed=True)
    return list(storage.read_records(path))


def build_line_index(dates):
    """
    {date: {team: implied_line}} from the archived observations for each
    date, plus a coverage report. Dates with no archive contribute
    nothing and are counted, so the artifact can state honestly how much
    market-relative coverage it actually had.
    """
    index, coverage = {}, {"datesRequested": len(dates), "datesWithArchive": 0, "teamLinesDerived": 0}
    for date in dates:
        observations = load_observations_for_date(date)
        if not observations:
            continue
        coverage["datesWithArchive"] += 1
        lines, _reasons = team_lines_for_date(observations)
        if lines:
            index[date] = lines
            coverage["teamLinesDerived"] += len(lines)
    return index, coverage


def build_team_form(abbr, games, line_index, as_of_date):
    runs = [g["runsScored"] for g in games]
    margins = None
    if line_index is not None:
        margins = [margin_vs_line(g["runsScored"], (line_index.get(g["date"]) or {}).get(abbr))
                   for g in games]

    form = build_form(
        runs, line_margins=margins, team=abbr, as_of_date=as_of_date,
        source="mlb_stats_api_schedule", games_available=len(games),
    )
    form["gameLog"] = [
        {**g, "teamTotalLine": (line_index.get(g["date"]) or {}).get(abbr) if line_index else None,
         "marginVsTeamTotalLine": margins[i] if margins else None}
        for i, g in enumerate(games)
    ]
    label, reason = hot_label(form)
    form["formLabel"] = {"label": label, "reason": reason, "window": "L7"}
    form["formLines"] = {f"L{w}": format_form_line(form, f"L{w}") for w in FORM_WINDOWS}
    return form


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--as-of", default=None, help="Last date to include (YYYY-MM-DD). Default: yesterday UTC.")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--no-market-lines", action="store_true",
                        help="Skip the archived team-total line pass (runs-only form)")
    parser.add_argument("--out", default=OUTPUT_PATH)
    args = parser.parse_args()

    end = (datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of
           else (datetime.now(tz=timezone.utc).date() - timedelta(days=1)))
    start = end - timedelta(days=max(1, args.lookback_days))
    start_s, end_s = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    schedule_json = mlb_schedule.fetch_schedule_range(start_s, end_s)
    if schedule_json is None:
        print(f"ERROR: MLB schedule fetch failed for {start_s}..{end_s} -- writing nothing", file=sys.stderr)
        return 1

    by_team = parse_team_game_log(schedule_json)
    if not by_team:
        print(f"ERROR: no completed games parsed for {start_s}..{end_s} -- writing nothing "
              f"(never a fabricated empty form)", file=sys.stderr)
        return 1

    dates = sorted({g["date"] for games in by_team.values() for g in games if g["date"]})
    if args.no_market_lines:
        line_index, line_coverage = None, {"status": "SKIPPED_BY_FLAG"}
    else:
        line_index, line_coverage = build_line_index(dates)
        line_coverage["status"] = "LOADED" if line_index else "NO_ARCHIVED_LADDERS_IN_WINDOW"

    payload = {
        "schemaVersion": "1",
        "generatedAt": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "asOfDate": end_s,
        "windowStartDate": start_s,
        "lookbackDays": args.lookback_days,
        "formWindows": list(FORM_WINDOWS),
        "runsSource": "mlb_stats_api_schedule",
        "marketLineSource": {
            "source": "data/edgelab/observations (archived Kalshi team-total ladder)",
            **line_coverage,
        },
        "teams": {
            abbr: build_team_form(abbr, games, line_index, end_s)
            for abbr, games in sorted(by_team.items())
        },
        "note": (
            "Descriptive handicapping context only. No production model weight reads this file; "
            "scripts/enrich_data.py's offense-baseline blend is unchanged. See "
            "docs/RESEARCH_OFFENSIVE_FORM.md for what the out-of-sample evidence does and does not support."
        ),
    }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)

    labels = {}
    for abbr, form in payload["teams"].items():
        labels[form["formLabel"]["label"]] = labels.get(form["formLabel"]["label"], 0) + 1
    print(f"[fetch_team_offense_form] {len(payload['teams'])} teams, {start_s}..{end_s} -> {args.out}")
    print(f"[fetch_team_offense_form] labels: {labels}")
    print(f"[fetch_team_offense_form] market-line coverage: {payload['marketLineSource']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
