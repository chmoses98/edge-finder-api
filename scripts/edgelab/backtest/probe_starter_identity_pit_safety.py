#!/usr/bin/env python3
"""
scripts/edgelab/backtest/probe_starter_identity_pit_safety.py
====================================================================
MLB-RSCH-0009 Phase A: resolves whether historical starting-pitcher
IDENTITY can be established point-in-time-safe (i.e. as it was known
BEFORE first pitch), at scale, for 2022-2026.

WHY THIS PROBE EXISTS
----------------------
The starter workload boxscore cache (MLB-RSCH-0004,
data/research_cache/starter_workload/<season>/boxscores.jsonl.gz) only
ever recorded the CONFIRMED, postgame starter (orderIndex==0 in a
boxscore response) -- never a pregame "probable pitcher" capture. That
is fine for building a pitcher's OWN prior-start history (used AFTER
the fact to construct starter N+1's features), but it is NOT
automatically usable as "today's starter" for a component ablation --
using the boxscore-confirmed starter as a same-game predictive input
would silently assume the eventual/actual starter was known before the
game, which is false whenever a scratch/injury/rotation change occurred.

The mission's own instruction is explicit: "Be extremely strict here.
If historical pregame starter identity cannot be established PIT-safe
at scale, classify this component as unavailable rather than leaking
final-game information."

METHOD
------
MLB Stats API's schedule endpoint supports a `hydrate=probablePitcher`
parameter (the SAME hydrate api/slate.js's live pathway already uses,
scripts/edgelab/../slate.js:330) -- when queried for a PAST date, this
either (a) genuinely returns whatever probable/announced starter was
on record for that date (a real historical fact, independent of the
final result), or (b) has been silently overwritten to just reflect the
final/actual starter once the game completed (in which case it carries
NO independent pregame information and must not be trusted).

To tell these apart WITHOUT assuming either answer: sample games
spread across all five seasons, fetch each sampled date's schedule with
`hydrate=probablePitcher`, and compare the returned probable pitcher's
playerId against the SAME game's already-cached boxscore-CONFIRMED
starter (starter_workload cache, read-only, no re-fetch). A genuine
pregame record should show a real, nonzero, plausible mismatch rate
(the well-documented real-world rate of pregame-announced starters who
were later scratched/swapped is on the rough order of a few percent to
low double digits over a season) -- NOT exactly 0% (which would be the
signature of an endpoint that just echoes the final result) and not an
implausibly large fraction either (which would suggest a probe/matching
bug, not real signal).

COST: MLB Stats API is free/uncredited (unlike the Odds API) -- this
probe's cost is call-count/runtime, not a budget concern. Each
`schedule?date=...&hydrate=probablePitcher` call returns EVERY game
league-wide for that one date, so ~30 dates (6/season x 5 seasons)
covers hundreds of games at ~30 total calls.

Prints a JSON summary to stdout, and separately persists it to
data/research_cache/sharp_market_probe/starter_identity_probe_result.json
(reusing the SAME research-sharp-market-probe.yml workflow's
commit_paths input -- no new workflow needed) for MLB-RSCH-0009's
run script to read the PIT-safety verdict from.
"""
import gzip
import json
import os
import sys
import time
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.storage import read_records

MLB_STATS_API = "https://statsapi.mlb.com/api/v1"
STARTER_CACHE_ROOT = os.path.join(_ROOT, "data", "research_cache", "starter_workload")
OUTPUT_PATH = os.path.join(_ROOT, "data", "research_cache", "sharp_market_probe", "starter_identity_probe_result.json")

# Preregistered: 6 dates spread across each regular season (Apr-Sep),
# 2022-2026. 2026 dates capped to what's actually elapsed. Fixed BEFORE
# any result is inspected -- never chosen post-hoc to favor an outcome.
PROBE_DATES = {
    2022: ["2022-04-15", "2022-05-15", "2022-06-15", "2022-07-15", "2022-08-15", "2022-09-15"],
    2023: ["2023-04-15", "2023-05-15", "2023-06-15", "2023-07-15", "2023-08-15", "2023-09-15"],
    2024: ["2024-04-15", "2024-05-15", "2024-06-15", "2024-07-15", "2024-08-15", "2024-09-15"],
    2025: ["2025-04-15", "2025-05-15", "2025-06-15", "2025-07-15", "2025-08-15", "2025-09-15"],
    2026: ["2026-04-15", "2026-05-15", "2026-06-15", "2026-07-15"],
}

# A genuine pregame-announced-starter record should show a real,
# nonzero mismatch rate somewhere in this plausible band (real-world
# MLB scratch/rotation-change rates). Below this floor is the
# signature of an endpoint that has been overwritten to just echo the
# final result (no independent information); above the ceiling
# suggests a matching bug, not real signal. Preregistered before any
# real result is computed.
PLAUSIBLE_MISMATCH_RATE_FLOOR = 0.01
PLAUSIBLE_MISMATCH_RATE_CEILING = 0.30

RATE_LIMIT_SECONDS = 0.3


def _fetch_json(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "edge-finder-edgelab/1.0",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _mlb_date(iso_date):
    yr, mo, dy = iso_date.split("-")
    return f"{mo}/{dy}/{yr}"


def fetch_probable_pitchers_for_date(iso_date, timeout=15):
    """Network adapter. One call returns every league game on this date
    with its (possibly-None) probablePitcher per side. Same endpoint
    api/slate.js's live schedule fetch already uses, with the same
    hydrate parameter, applied here to a PAST date."""
    url = f"{MLB_STATS_API}/schedule?sportId=1&date={_mlb_date(iso_date)}&hydrate=probablePitcher&gameType=R"
    return _fetch_json(url, timeout)


def extract_probable_pitchers(schedule_json):
    """Pure. {gamePk: {"home": playerId|None, "away": playerId|None}}."""
    out = {}
    if not schedule_json:
        return out
    for day in schedule_json.get("dates") or []:
        for g in day.get("games") or []:
            game_pk = g.get("gamePk")
            if game_pk is None:
                continue
            teams = g.get("teams") or {}
            entry = {}
            for side in ("home", "away"):
                pp = (teams.get(side) or {}).get("probablePitcher") or {}
                entry[side] = str(pp["id"]) if pp.get("id") is not None else None
            out[game_pk] = entry
    return out


def load_confirmed_starters(season):
    """Pure/local, no network. {gamePk: {"home": playerId|None, "away": playerId|None}}
    from the already-cached starter_workload boxscore cache."""
    path = os.path.join(STARTER_CACHE_ROOT, str(season), "boxscores.jsonl.gz")
    out = {}
    for row in read_records(path):
        game_pk = row.get("gamePk")
        if game_pk is None:
            continue
        entry = {}
        for side in ("home", "away"):
            lines = row.get(f"{side}Pitchers") or []
            starter = next((p for p in lines if p.get("orderIndex") == 0), None)
            entry[side] = starter.get("playerId") if starter else None
        out[game_pk] = entry
    return out


def compare_date(iso_date, season, confirmed_starters):
    schedule_json = fetch_probable_pitchers_for_date(iso_date)
    probables = extract_probable_pitchers(schedule_json)
    rows = []
    for game_pk, probable_sides in probables.items():
        confirmed_sides = confirmed_starters.get(game_pk)
        if confirmed_sides is None:
            continue  # game not in our cache (e.g. postseason) -- skip, never guess
        for side in ("home", "away"):
            probable_id = probable_sides.get(side)
            confirmed_id = confirmed_sides.get(side)
            if probable_id is None or confirmed_id is None:
                continue
            rows.append({
                "gamePk": game_pk, "side": side, "date": iso_date,
                "probablePitcherId": probable_id, "confirmedStarterId": confirmed_id,
                "match": probable_id == confirmed_id,
            })
    return rows


def main():
    all_rows = []
    dates_probed = 0
    for season, dates in PROBE_DATES.items():
        confirmed_starters = load_confirmed_starters(season)
        for iso_date in dates:
            rows = compare_date(iso_date, season, confirmed_starters)
            all_rows.extend(rows)
            dates_probed += 1
            time.sleep(RATE_LIMIT_SECONDS)

    total = len(all_rows)
    mismatches = sum(1 for r in all_rows if not r["match"])
    mismatch_rate = round(mismatches / total, 4) if total else None

    pit_safe_at_scale = (
        total >= 50
        and mismatch_rate is not None
        and PLAUSIBLE_MISMATCH_RATE_FLOOR <= mismatch_rate <= PLAUSIBLE_MISMATCH_RATE_CEILING
    )

    result = {
        "generatedAt": "PROBE_RUNTIME",
        "datesProbed": dates_probed,
        "comparableRows": total,
        "mismatches": mismatches,
        "mismatchRate": mismatch_rate,
        "plausibleMismatchRateBand": [PLAUSIBLE_MISMATCH_RATE_FLOOR, PLAUSIBLE_MISMATCH_RATE_CEILING],
        "pitSafeAtScale": pit_safe_at_scale,
        "verdict": (
            "STARTER_IDENTITY_PIT_SAFE_AT_SCALE" if pit_safe_at_scale
            else "STARTER_IDENTITY_NOT_PIT_SAFE_AT_SCALE"
        ),
        "sampleMismatches": [r for r in all_rows if not r["match"]][:10],
    }
    print(json.dumps(result, indent=2))

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(result, f, indent=2)
    return result


if __name__ == "__main__":
    main()
