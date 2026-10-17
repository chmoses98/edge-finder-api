"""Data-availability matrix with point-in-time classification and timestamp-integrity checks.

pit_class:
  archived          captured before first pitch and committed with its timestamp
  reconstructable   rebuildable point-in-time from dated raw outcomes (box scores, PA logs)
  retrospective     only a post-game version exists (contaminated if used as a pregame input)
  missing           not in the repository
"""
from __future__ import annotations

import glob
import gzip
import json
import os

import numpy as np
import pandas as pd

from . import data as D
from . import incumbents as INC


def _count_lines(paths) -> int:
    n = 0
    for p in paths:
        with (gzip.open(p, "rt") if p.endswith(".gz") else open(p)) as f:
            n += sum(1 for _ in f)
    return n


def checks() -> dict:
    out = {}
    pl = D.load_pitcher_lines()
    g = D.load_games()
    # 1 feed vs research-cache box agreement on starters (2024-2026 overlap)
    box = []
    for s in (2024, 2025, 2026):
        for r in D._jsonl_gz(os.path.join(D.RC, "starter_workload", str(s), "boxscores.jsonl.gz")):
            for key in ("awayPitchers", "homePitchers"):
                for x in r.get(key) or []:
                    if x.get("orderIndex") == 0:
                        box.append({"game_pk": int(r["gamePk"]), "pitcher_id": int(x["playerId"]),
                                    "bf_box": x.get("battersFaced"), "k_box": x.get("strikeOuts")})
    box = pd.DataFrame(box)
    m = pl[pl["is_starter"] == 1].merge(box, on=["game_pk", "pitcher_id"])
    out["feed_vs_box_starters"] = {"n": int(len(m)), "bf_equal": float((m["bf"] == m["bf_box"]).mean()),
                                   "k_equal": float((m["k"] == m["k_box"]).mean())}
    # 2 probable == actual starter in the post-game feed (retrospective field)
    f = g[g["season"] >= 2024]
    eq = pd.concat([(f["away_probable_id"] == f["away_starter_id"]), (f["home_probable_id"] == f["home_starter_id"])])
    out["feed_probable_equals_starter"] = float(eq.mean())
    # 3 archived pregame lineups vs post-game lineup
    pl26 = D.load_pregame_lineups()
    bat = D.load_batting()
    bat = bat[(bat["season"] == 2026) & (bat["is_starter"] == 1)][["game_pk", "side", "player_id", "slot"]]
    j = pl26.merge(bat, on=["game_pk", "side", "player_id"], how="left", suffixes=("_pre", "_post"))
    out["pregame_lineups_2026"] = {
        "rows": int(len(pl26)), "games": int(pl26["game_pk"].nunique()),
        "dates": [str(pd.to_datetime(pl26["start_utc"]).min().date()), str(pd.to_datetime(pl26["start_utc"]).max().date())],
        "captured_before_start": float((pd.to_datetime(pl26["capture_utc"]) < pd.to_datetime(pl26["start_utc"])).mean()),
        "player_started": float(j["slot_post"].notna().mean()),
        "same_slot": float((j["slot_pre"] == j["slot_post"]).mean()),
        "probable_equals_starter": None,
    }
    # 4 archived team-run projections: created before first pitch?
    ti = INC.load_team_incumbent()
    if len(ti):
        ti = ti.merge(g[["game_pk", "start_utc"]], on="game_pk", how="left")
        out["pipeline_team_projections"] = {
            "rows": int(len(ti)), "games": int(ti["game_pk"].nunique()),
            "dates": [str(ti["file_date"].min()), str(ti["file_date"].max())],
            "created_before_start": float((ti["inc_created"] < ti["start_utc"]).mean()),
        }
    # 5 statcast coverage
    sc = D.load_statcast_pitcher_games()
    out["statcast"] = {"games": int(sc["game_pk"].nunique()), "pitcher_games": int(len(sc)),
                       "dates": [str(sc["date"].min()), str(sc["date"].max())], "pitches": int(sc["sc_pitches"].sum())}
    return out


def matrix(chk: dict | None = None) -> list[dict]:
    chk = chk or checks()
    g = D.load_games()
    pl = D.load_pitcher_lines()
    bat = D.load_batting()
    pa_rows = len(D.load_pa())
    tb = D.load_team_batting()
    hp = sorted(glob.glob(os.path.join(D.DATA, "edgelab", "hitter_projection_snapshots", "*.jsonl")))
    sp = sorted(glob.glob(os.path.join(D.DATA, "shadow_pure", "mlb_pitcher", "projections", "*")))
    obs = sorted(glob.glob(os.path.join(D.DATA, "edgelab", "observations", "*.jsonl.gz")))
    slates = sorted(os.listdir(os.path.join(D.DATA, "slates")))
    pin = sorted(glob.glob(os.path.join(D.RC, "pinnacle_historical", "*", "*.json")))
    ps26 = _count_lines([os.path.join(D.RC, "starter_workload", "2026", "postseason_boxscores.jsonl.gz")])
    rows = [
        {"dataset": "games / schedule (date, start time, venue, teams, final score)", "seasons": "2022-2026 RS",
         "rows": int(len(g)), "pit_class": "reconstructable",
         "notes": "2022-23 from research_cache schedules, 2024-26 from MLB feed; start_utc used as the forecast cutoff."},
        {"dataset": "pitcher box lines per game (BF, outs, pitches, K, BB, H, R; ER; HR)", "seasons": "2022-2026 RS",
         "rows": int(len(pl)), "pit_class": "reconstructable",
         "notes": f"Feed (2024-26) and research-cache box agree on starters: BF {chk['feed_vs_box_starters']['bf_equal']:.4f}, "
                  f"K {chk['feed_vs_box_starters']['k_equal']:.4f} (n={chk['feed_vs_box_starters']['n']}). "
                  "ER missing after 2026-08-26 (box cache ends) -> runs-allowed family uses R. HR only 2024+."},
        {"dataset": "hitter box lines per game (PA..RBI, batting order)", "seasons": "2024-2026 RS",
         "rows": int(len(bat)), "pit_class": "reconstructable",
         "notes": "MLB feed/live, 4 rain-shortened games missing per season. No player-level hitter lines for 2022-2023 "
                  "(team-level only) -> 2024 is a cold-start development season."},
        {"dataset": "per-PA events with batter side / pitcher hand", "seasons": "2024-2026 RS", "rows": int(pa_rows),
         "pit_class": "reconstructable", "notes": "Used for platoon multipliers and opponent K% vs handedness."},
        {"dataset": "team batting lines per game", "seasons": "2022-2026 RS", "rows": int(len(tb)),
         "pit_class": "reconstructable", "notes": "Opponent / park / league context."},
        {"dataset": "pitch-level Statcast (velocity, whiff, CSW)", "seasons": f"2026 {chk['statcast']['dates'][0]}..{chk['statcast']['dates'][1]}",
         "rows": int(chk["statcast"]["pitches"]), "pit_class": "reconstructable",
         "notes": f"{chk['statcast']['games']} games only (~7 weeks); Savant is blocked so no backfill. Too short for a "
                  "multi-season walk-forward; used in one sub-study."},
        {"dataset": "batting order (post-game official lineup)", "seasons": "2024-2026", "rows": int((bat['is_starter'] == 1).sum()),
         "pit_class": "retrospective",
         "notes": "Equals the confirmed lineup except late scratches; models using it are labelled 'known slot'."},
        {"dataset": "archived PREGAME lineups", "seasons": f"2026 {chk['pregame_lineups_2026']['dates'][0]}..{chk['pregame_lineups_2026']['dates'][1]}",
         "rows": chk["pregame_lineups_2026"]["rows"], "pit_class": "archived",
         "notes": f"{chk['pregame_lineups_2026']['games']} games; captured before first pitch "
                  f"{chk['pregame_lineups_2026']['captured_before_start']:.3f}; player started {chk['pregame_lineups_2026']['player_started']:.3f}; "
                  f"same slot as post-game lineup {chk['pregame_lineups_2026']['same_slot']:.3f}."},
        {"dataset": "probable pitchers", "seasons": "2024-2026 (feed field) / 2026-06-15.. (slates)", "rows": int(len(slates)),
         "pit_class": "retrospective",
         "notes": f"Feed 'probable' field was fetched post-game and equals the actual starter {chk['feed_probable_equals_starter']:.4f} "
                  "of the time (it is overwritten, so it cannot measure scratches). Archived pregame probables exist only in "
                  f"data/slates ({len(slates)} slate dates from {slates[0]})."},
        {"dataset": "handedness (bats/throws)", "seasons": "2022-2026", "rows": None, "pit_class": "reconstructable",
         "notes": "Static identity; 2022-23-only pitchers lack throws (24% of 2022 appearances) -> treated as R."},
        {"dataset": "parks (venue id)", "seasons": "2022-2026", "rows": None, "pit_class": "reconstructable",
         "notes": "Park factors are estimated point-in-time from prior games at the venue (both teams), shrunk (EB)."},
        {"dataset": "weather", "seasons": "-", "rows": 0, "pit_class": "missing",
         "notes": "No per-date archive (Milestone-2 PIT audit, unchanged)."},
        {"dataset": "injuries / IL / pitch-count restrictions", "seasons": "-", "rows": 0, "pit_class": "missing",
         "notes": "No source in repo; layoff (>=15 days) is used as a proxy."},
        {"dataset": "bullpen usage", "seasons": "2022-2026", "rows": int((pl['is_starter'] == 0).sum()),
         "pit_class": "reconstructable", "notes": "Relief lines derivable from box scores."},
        {"dataset": "archived pregame TEAM-RUN projections (production)", "seasons": "2026-07-30..",
         "rows": chk.get("pipeline_team_projections", {}).get("rows"), "pit_class": "archived",
         "notes": f"data/pipeline/<date>/projections.json; createdAt before first pitch "
                  f"{chk.get('pipeline_team_projections', {}).get('created_before_start', float('nan')):.3f}."},
        {"dataset": "archived pregame HITTER projections (production snapshots)", "seasons": f"2026 {os.path.basename(hp[0])[:10]}..{os.path.basename(hp[-1])[:10]}",
         "rows": _count_lines(hp), "pit_class": "archived",
         "notes": f"{len(hp)} dates; incumbent also re-run over 2026-08-16..09-27 (hitter_w2/audit, fidelity ~0.014)."},
        {"dataset": "PITCHER projections in the archive", "seasons": "2025-2026 sidecars; 2026-10-12.. shadow",
         "rows": None, "pit_class": "retrospective",
         "notes": "pitcher_workload_joint sidecars are retroactive walk-forward reruns (NOT archived pregame forecasts). "
                  f"Genuinely prospective, hash-chained pitcher projections exist only in data/shadow_pure/mlb_pitcher ({len(sp)} dates, postseason)."},
        {"dataset": "Kalshi market observations", "seasons": f"2026 {os.path.basename(obs[0])[:10]}..{os.path.basename(obs[-1])[:10]}",
         "rows": None, "pit_class": "archived", "notes": "Not used: this initiative measures projection accuracy, not market edge."},
        {"dataset": "sportsbook game lines (Pinnacle sample)", "seasons": "2022-2026", "rows": len(pin), "pit_class": "archived",
         "notes": "Every ~5 days; not used."},
        {"dataset": "2026 postseason box lines", "seasons": "2026 PS", "rows": ps26, "pit_class": "reconstructable",
         "notes": "Pitcher lines only (25 games to 2026-10-10); no postseason hitter lines -> forward hitter projections use regular-season form."},
    ]
    return rows
