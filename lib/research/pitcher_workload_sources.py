#!/usr/bin/env python3
"""
lib/research/pitcher_workload_sources.py
========================================
SPORTS-SOURCE-ONLY loaders for the W1-D pitcher workload challenger
(lib/research/pitcher_workload_joint.py). RESEARCH ONLY.

Reads exactly three committed sports datasets and nothing else:

  data/research_cache/starter_workload/<yr>/boxscores.jsonl.gz
      MLB StatsAPI box-score pitching lines (every pitcher of every game:
      outs, battersFaced, strikeOuts, numberOfPitches, baseOnBalls, hits,
      runs, orderIndex). Regular season 2022 -> 2026-08-26.
  data/research_cache/bullpen_backtest/<yr>/schedules/*.json
      MLB StatsAPI schedule: gamePk -> officialDate, scheduled first pitch
      (gameDate, UTC), gameType, teams, doubleheader number, status.
  data/statcast_raw/games/<gamePk>.jsonl
      Statcast pitch log (2026-08-11 -> 2026-09-27). Used to rebuild
      pitcher lines (outs, BF, K, pitches) for regular-season games after
      the last box score in the research cache.

It never opens data/edgelab/settlements, data/edgelab/observations,
data/kalshi*, odds files, app/ exports or any other market artifact: the
only path arguments are the three sub-trees above (see SOURCE_GLOBS, which
tests/research/test_pitcher_workload_joint.py asserts). The data root is a
parameter so the runtime market-mutation test can point it at a copy of
the tree whose market files are deleted or randomized.

Timestamps: the research cache stores no fetch time. A box-score line is
treated as observable FINAL_LAG_HOURS after that game's scheduled first
pitch (a conservative proxy for "game final"); every projection only uses
games on dates strictly before the projected game's officialDate, so the
proxy is always earlier than the projection's as-of.
"""

from __future__ import annotations

import glob
import gzip
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

#: The only data sub-trees this module may read (relative to data_root).
SOURCE_GLOBS = (
    os.path.join("research_cache", "starter_workload", "*", "boxscores.jsonl.gz"),
    os.path.join("research_cache", "bullpen_backtest", "*", "schedules", "*.json"),
    os.path.join("statcast_raw", "games", "*.jsonl"),
)
FINAL_LAG_HOURS = 5

# MLB StatsAPI team ids -> abbreviations (stable numeric ids; mirrors
# lib/edgelab/mlb_schedule.TEAM_ID_TO_ABBR, duplicated so this loader has no
# import-time dependency on production modules).
_TEAM = {108: "LAA", 109: "AZ", 110: "BAL", 111: "BOS", 112: "CHC", 113: "CIN", 114: "CLE", 115: "COL",
         116: "DET", 117: "HOU", 118: "KC", 119: "LAD", 120: "WSH", 121: "NYM", 133: "ATH", 134: "PIT",
         135: "SD", 136: "SEA", 137: "SF", 138: "STL", 139: "TB", 140: "TEX", 141: "TOR", 142: "MIN",
         143: "PHI", 144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL"}


def _jsonl(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_schedule(data_root):
    """gamePk -> {date, start_utc, game_type, away, home, game_number, status}."""
    info = {}
    for f in sorted(glob.glob(os.path.join(data_root, SOURCE_GLOBS[1]))):
        with open(f, encoding="utf-8") as fh:
            doc = json.load(fh)
        for d in doc.get("dates", []):
            for g in d.get("games", []):
                t = g.get("teams") or {}
                st = (g.get("status") or {}).get("detailedState")
                info[g["gamePk"]] = {
                    "date": g.get("officialDate") or d["date"],
                    "start_utc": g.get("gameDate"),
                    "game_type": g.get("gameType"),
                    "away": _TEAM.get(((t.get("away") or {}).get("team") or {}).get("id")),
                    "home": _TEAM.get(((t.get("home") or {}).get("team") or {}).get("id")),
                    "game_number": g.get("gameNumber") or 1,
                    "status": st,
                }
    return info


def _observed_at(start_utc):
    t = datetime.fromisoformat(start_utc.replace("Z", "+00:00")).astimezone(timezone.utc)
    return (t + timedelta(hours=FINAL_LAG_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")


_OUT_EVENTS = {"field_out": 1, "strikeout": 1, "force_out": 1, "sac_fly": 1, "sac_bunt": 1, "fielders_choice_out": 1,
               "grounded_into_double_play": 2, "double_play": 2, "strikeout_double_play": 2,
               "sac_fly_double_play": 2, "triple_play": 3}


def statcast_lines(rows):
    """Per-pitcher lines from one game's Statcast pitches.

    Returns {"home_sp": pid, "away_sp": pid, "lines": {pid: {outs, bf, k, pitches, bb}},
    "side": {pid: "home"|"away"}}. Outs use the outs-state transition between
    consecutive PAs of a half inning (same convention as the champion's
    scripts/research/mlb_pitcher_prop_calibration._pa_outs_from_pitches, which
    matched the box score on all 70 overlap starts); pitches = pitch rows."""
    pas, pitches = {}, defaultdict(int)
    for r in rows:
        i = r.get("atBatIndex")
        if i is None:
            continue
        pitches[str(r.get("pitcherId"))] += 1
        cur = pas.get(i)
        if cur is None or (r.get("pitchNumber") or 0) > (cur.get("pitchNumber") or 0):
            pas[i] = r
    seq = [pas[i] for i in sorted(pas)]
    halves, half, prev = [], -1, None
    for pa in seq:
        if prev is None or pa.get("inning") != prev.get("inning") or (pa.get("outsWhenUp") or 0) < (prev.get("outsWhenUp") or 0):
            half += 1
        halves.append(half)
        prev = pa
    lines = defaultdict(lambda: {"outs": 0, "bf": 0, "k": 0, "bb": 0, "pitches": 0})
    side, first = {}, {}
    for idx, pa in enumerate(seq):
        h = halves[idx]
        pid = str(pa.get("pitcherId"))
        if h in (0, 1) and h not in first:
            first[h] = pid
        # top halves (even) -> home team pitching
        side.setdefault(pid, "home" if h % 2 == 0 else "away")
        before = pa.get("outsWhenUp") or 0
        if idx + 1 < len(seq) and halves[idx + 1] == h:
            after = seq[idx + 1].get("outsWhenUp") or 0
        elif idx + 1 == len(seq):
            after = min(3, before + _OUT_EVENTS.get(pa.get("events"), 0))
        else:
            after = 3
        ln = lines[pid]
        ln["bf"] += 1
        ln["outs"] += max(0, after - before)
        if pa.get("events") in ("strikeout", "strikeout_double_play"):
            ln["k"] += 1
        if pa.get("events") in ("walk", "intent_walk"):
            ln["bb"] += 1
    for pid in lines:
        lines[pid]["pitches"] = pitches.get(pid, 0)
    return {"home_sp": first.get(0), "away_sp": first.get(1), "lines": dict(lines), "side": side}


def load_pitching_games(data_root, schedule=None):
    """All regular-season team pitching games 2022 -> 2026-09-27 from sports sources.

    Returns a list of team-game dicts sorted by (date, start_utc, gamePk):
      {pk, date, start_utc, observed_at, season, team, opp, side, game_type, source,
       starter: {pid, outs, bf, k, pitches, bb, h, r} | None,
       relief_pitches: int, relief_outs: int, batting_pa: int (opp batters faced),
       batting_k: int}
    `batting_*` describe the OPPONENT's offense in this game (PA/K against this
    team's pitchers)."""
    schedule = schedule if schedule is not None else load_schedule(data_root)
    out, seen = [], set()
    for f in sorted(glob.glob(os.path.join(data_root, SOURCE_GLOBS[0]))):
        for r in _jsonl(f):
            g = schedule.get(r["gamePk"])
            if not g or g["game_type"] != "R" or not g.get("start_utc"):
                continue
            seen.add(r["gamePk"])
            for side, opp_side in (("away", "home"), ("home", "away")):
                ps = r.get(f"{side}Pitchers") or []
                if not ps:
                    continue
                st = ps[0]
                starter = None
                if st.get("orderIndex") == 0 and st.get("outs") is not None:
                    starter = {"pid": str(st["playerId"]), "outs": st["outs"], "bf": st.get("battersFaced"),
                               "k": st.get("strikeOuts"), "pitches": st.get("numberOfPitches"),
                               "bb": st.get("baseOnBalls"), "h": st.get("hits"), "r": st.get("runs")}
                out.append({"pk": r["gamePk"], "date": g["date"], "start_utc": g["start_utc"],
                            "observed_at": _observed_at(g["start_utc"]), "season": g["date"][:4],
                            "team": g[side], "opp": g[opp_side], "side": side, "game_type": "R",
                            "source": "statsapi_boxscore_research_cache", "starter": starter,
                            "relief_pitches": sum(p.get("numberOfPitches") or 0 for p in ps[1:]),
                            "relief_outs": sum(p.get("outs") or 0 for p in ps[1:]),
                            "n_relievers": len(ps) - 1,
                            "batting_pa": sum(p.get("battersFaced") or 0 for p in ps),
                            "batting_k": sum(p.get("strikeOuts") or 0 for p in ps)})
    last_box = max((x["date"] for x in out if x["season"] == "2026"), default="0000")
    for f in sorted(glob.glob(os.path.join(data_root, SOURCE_GLOBS[2]))):
        pk = int(os.path.basename(f).split(".")[0])
        g = schedule.get(pk)
        if pk in seen or not g or g["game_type"] != "R" or g["date"] <= last_box or not g.get("start_utc"):
            continue
        rows = list(_jsonl(f))
        if not rows:
            continue
        sc = statcast_lines(rows)
        for side, opp_side in (("away", "home"), ("home", "away")):
            sp = sc[f"{side}_sp"]
            pids = [p for p, s in sc["side"].items() if s == side]
            if not sp or sp not in sc["lines"]:
                continue
            ln = sc["lines"][sp]
            starter = {"pid": sp, "outs": ln["outs"], "bf": ln["bf"], "k": ln["k"], "pitches": ln["pitches"],
                       "bb": ln["bb"], "h": None, "r": None}
            rel = [sc["lines"][p] for p in pids if p != sp]
            out.append({"pk": pk, "date": g["date"], "start_utc": g["start_utc"],
                        "observed_at": _observed_at(g["start_utc"]), "season": g["date"][:4],
                        "team": g[side], "opp": g[opp_side], "side": side, "game_type": "R",
                        "source": "statcast_pitch_log", "starter": starter,
                        "relief_pitches": sum(x["pitches"] for x in rel),
                        "relief_outs": sum(x["outs"] for x in rel), "n_relievers": len(rel),
                        "batting_pa": sum(sc["lines"][p]["bf"] for p in pids),
                        "batting_k": sum(sc["lines"][p]["k"] for p in pids)})
    out.sort(key=lambda x: (x["date"], x["start_utc"], x["pk"], x["side"]))
    return out
