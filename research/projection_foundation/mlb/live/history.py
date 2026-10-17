"""Live box-score history: keeps the model's inputs current without touching the committed research data.

The committed datasets (data/research/hitter_w2/mlb_feed, data/research_cache/*) are produced by research jobs and end at
the last backfill (regular season 2026-09-27, postseason pitcher lines 2026-10-10). Model features (rest days, opener
flags, recent form, opponent/park factors) need yesterday's games, so the results stage archives EVERY final game's box
score (`boxscores/mlb/<officialDate>/...jsonl.gz`, one record per game, write-once) and projections append those games to
the base frames with exactly the base schemas. Nothing is invented: a game appears in history only after the schedule said
Final and its box score was fetched. Per-PA platoon events are not archived, so the hand-specific K factor stops updating
at the last committed per-PA game (the model keeps using the latest value; see LIVE_MLB.md).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

KIND = "boxscores"
SPORT = "mlb"


def record_from_box(game: dict, box: dict) -> dict:
    return {"schema": "sift.boxscore.v1", "game_pk": game["game_pk"], "official_date": game["official_date"],
            "start_utc": game["start_utc"], "game_type": game.get("game_type"), "venue_id": game.get("venue_id"),
            "detailed": game.get("detailed"), "double_header": game.get("double_header"),
            "game_number": game.get("game_number"),
            "away": {k: game["away"].get(k) for k in ("team_id", "abbr")}, "home": {k: game["home"].get(k) for k in ("team_id", "abbr")},
            "pitchers": box["pitchers"], "batters": box["batters"], "runs": {s: box["team"][s].get("runs") for s in ("away", "home")}}


def stored_ids(root: str) -> set[str]:
    base = os.path.join(root, KIND, SPORT)
    out = set()
    if os.path.isdir(base):
        for day in os.listdir(base):
            for fn in os.listdir(os.path.join(base, day)):
                if fn.endswith(".jsonl.gz"):
                    with gzip.open(os.path.join(base, day, fn), "rt") as f:
                        out |= {json.loads(x)["game_pk"] for x in f if x.strip()}
    return out


def write_records(records: list[dict], root: str, now: datetime) -> int:
    by_day: dict[str, list] = {}
    have = stored_ids(root)
    for r in records:
        if r["game_pk"] not in have:
            by_day.setdefault(r["official_date"], []).append(r)
    n = 0
    for day, rows in by_day.items():
        d = os.path.join(root, KIND, SPORT, day)
        os.makedirs(d, exist_ok=True)
        payload = "\n".join(json.dumps(r, sort_keys=True) for r in rows).encode()
        h = hashlib.sha256(payload).hexdigest()[:12]
        path = os.path.join(d, f"{now.strftime('%H%M%S')}Z_{h}.jsonl.gz")
        if not os.path.exists(path):
            with gzip.open(path, "xb") as f:
                f.write(payload)
        n += len(rows)
    return n


def load_records(root: str, since_date: str | None = None) -> list[dict]:
    base = os.path.join(root, KIND, SPORT)
    out, seen = [], set()
    if not os.path.isdir(base):
        return out
    for day in sorted(os.listdir(base)):
        if since_date and day < since_date:
            continue
        for fn in sorted(os.listdir(os.path.join(base, day))):
            if fn.endswith(".jsonl.gz"):
                with gzip.open(os.path.join(base, day, fn), "rt") as f:
                    for x in f:
                        if x.strip():
                            r = json.loads(x)
                            if r["game_pk"] not in seen:
                                seen.add(r["game_pk"])
                                out.append(r)
    return out


def frames(recs: list[dict]) -> dict[str, pd.DataFrame]:
    """Records -> frames with the BASE schemas of data.load_games / load_pitcher_lines / load_batting / load_team_batting."""
    g_rows, p_rows, b_rows, t_rows = [], [], [], []
    for r in recs:
        pk = int(r["game_pk"])
        start = pd.Timestamp(r["start_utc"])
        date = pd.Timestamp(r["official_date"])
        season = int(date.year)
        ids = {"A": r["away"]["team_id"], "H": r["home"]["team_id"]}
        abbr = {"A": r["away"]["abbr"], "H": r["home"]["abbr"]}
        starters = {s: next((p for p in r["pitchers"][n] if p["order"] == 0), None) for s, n in (("A", "away"), ("H", "home"))}
        g_rows.append({"game_pk": pk, "season": season, "date": date, "start_utc": start, "game_type": r.get("game_type"),
                       "double_header": r.get("double_header"), "game_number": r.get("game_number"),
                       "venue_id": r.get("venue_id"), "away_id": ids["A"], "home_id": ids["H"],
                       "away_runs": r["runs"].get("away"), "home_runs": r["runs"].get("home"),
                       "detailed_state": r.get("detailed"), "away_abbr": abbr["A"], "home_abbr": abbr["H"], "source": "pf_live",
                       "away_starter_id": int(starters["A"]["player_id"]) if starters["A"] else None,
                       "away_starter_hand": (starters["A"] or {}).get("hand"),
                       "home_starter_id": int(starters["H"]["player_id"]) if starters["H"] else None,
                       "home_starter_hand": (starters["H"] or {}).get("hand")})
        for s, n in (("A", "away"), ("H", "home")):
            o = "H" if s == "A" else "A"
            common = {"game_pk": pk, "side": s, "season": season, "date": date, "start_utc": start,
                      "venue_id": r.get("venue_id"), "team_id": ids[s], "opp_id": ids[o], "team": abbr[s], "opp": abbr[o],
                      "is_home": int(s == "H")}
            for p in r["pitchers"][n]:
                p_rows.append({**common, "pitcher_id": int(p["player_id"]), "hand": p.get("hand"), "order": p["order"],
                               "outs": p.get("outs"), "bf": p.get("bf"), "pitches": p.get("pitches"), "k": p.get("k"),
                               "bb": p.get("bb"), "h": p.get("h"), "r": p.get("r"), "hr": p.get("hr"), "er": p.get("er"),
                               "name": p.get("name"), "is_starter": int(p["order"] == 0)})
            opp_sp = starters[o]
            tot = {c: 0 for c in ("PA", "AB", "H", "d2", "d3", "HR", "BB", "HBP", "K", "SF", "R")}
            for b in r["batters"][n]:
                for c in tot:
                    tot[c] += int(b.get(c) or 0)
                b_rows.append({**common, "player_id": int(b["player_id"]), "bat_side": b.get("bat_side"),
                               "batting_order": b["slot"] * 100 + (0 if b["is_starter"] else 1), "slot": b["slot"],
                               "is_starter": int(b["is_starter"]), "position": b.get("position"),
                               **{c: b.get(c) for c in ("PA", "AB", "H", "d2", "d3", "HR", "R", "RBI", "BB", "IBB", "HBP", "K",
                                                         "SF", "SH", "SB", "CS", "TB", "LOB", "GIDP", "HRR")},
                               "opp_starter_id": int(opp_sp["player_id"]) if opp_sp else None,
                               "opp_starter_hand": (opp_sp or {}).get("hand"),
                               "1B": int(b.get("H") or 0) - int(b.get("d2") or 0) - int(b.get("d3") or 0) - int(b.get("HR") or 0)})
            t_rows.append({**common, **tot, "runs": float(r["runs"].get("away" if s == "A" else "home") or 0)})
    out = {"games": pd.DataFrame(g_rows), "pl": pd.DataFrame(p_rows), "bat": pd.DataFrame(b_rows), "tb": pd.DataFrame(t_rows)}
    return out


def extra_inputs(root: str, base_game_pks: set[int], cutoff, exclude_pks: set[int] | None = None) -> dict:
    """History frames from archived box scores that are NOT already in the base data and started before `cutoff`."""
    recs = [r for r in load_records(root) if int(r["game_pk"]) not in base_game_pks and
            int(r["game_pk"]) not in (exclude_pks or set()) and pd.Timestamp(r["start_utc"]) < pd.Timestamp(cutoff)]
    fr = frames(recs)
    fr["n_games"] = len(recs)
    return fr
