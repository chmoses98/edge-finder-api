"""MLB raw-data loaders for the Projection Foundation (read-only over committed repo data).

Sources (all committed in this repo; nothing is fetched):
  * data/research/hitter_w2/mlb_feed/<2024..2026>/{games,batting,pitching,pa}.csv.gz
      MLB Stats API feed/live per regular-season game (fetched 2026-10-11). Per-player box lines,
      batting order, per-PA events with batter side / pitcher hand. Batting order is the
      POST-GAME official lineup (retrospective with respect to a pregame forecast).
  * data/research_cache/starter_workload/<2022..2026>/boxscores.jsonl.gz
      Per-pitcher box lines (BF, K, BB, H, R, ER, outs, pitches) 2022-2026 (2026 to 08-26).
  * data/research_cache/batting_backtest/<season>/boxscores.jsonl.gz   team batting lines
  * data/research_cache/bullpen_backtest/<season>/schedules/*.json     schedule (date, start, venue)
  * data/research/hitter_v2/pregame_lineups_2026.csv.gz                archived PREGAME lineups (partial 2026)
  * data/statcast_raw/games/*.jsonl                                    pitch-level 2026-08-11..09-27

Everything is cached as parquet under research/projection_foundation/.cache (gitignored).
"""
from __future__ import annotations

import glob
import gzip
import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PF_ROOT = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(PF_ROOT))
DATA = os.path.join(REPO, "data")
CACHE = os.path.join(PF_ROOT, ".cache")
FEED = os.path.join(DATA, "research", "hitter_w2", "mlb_feed")
RC = os.path.join(DATA, "research_cache")
SEASONS_FEED = (2024, 2025, 2026)
SEASONS_BOX = (2022, 2023, 2024, 2025, 2026)


def _cache(name: str, builder, refresh: bool = False) -> pd.DataFrame:
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, f"{name}.parquet")
    if os.path.exists(p) and not refresh:
        return pd.read_parquet(p)
    df = builder()
    df.to_parquet(p, index=False)
    return df


def _jsonl_gz(path: str):
    with gzip.open(path, "rt") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


# --------------------------------------------------------------------------- schedule / games
def _schedule_rows(season: int) -> list[dict]:
    rows = {}
    for f in glob.glob(os.path.join(RC, "bullpen_backtest", str(season), "schedules", "*.json")):
        d = json.load(open(f))
        for dt in d.get("dates", []):
            for g in dt.get("games", []):
                if g.get("gameType") != "R":
                    continue
                st = g.get("status", {}).get("detailedState", "")
                if g.get("status", {}).get("abstractGameState") != "Final":
                    continue
                pk = int(g["gamePk"])
                # a postponed game appears under its original date with a different status; keep the
                # row whose officialDate is latest (the date it was actually completed)
                prev = rows.get(pk)
                row = {
                    "game_pk": pk, "season": season, "date": g.get("officialDate"),
                    "start_utc": g.get("gameDate"), "game_type": "R",
                    "double_header": g.get("doubleHeader"), "game_number": g.get("gameNumber"),
                    "venue_id": (g.get("venue") or {}).get("id"),
                    "away_id": g["teams"]["away"]["team"]["id"], "home_id": g["teams"]["home"]["team"]["id"],
                    "away_runs": g["teams"]["away"].get("score"), "home_runs": g["teams"]["home"].get("score"),
                    "detailed_state": st, "day_night": g.get("dayNight"),
                    "innings": g.get("scheduledInnings"),
                }
                if prev is None or str(row["date"]) > str(prev["date"]):
                    rows[pk] = row
    return list(rows.values())


def load_games(refresh: bool = False) -> pd.DataFrame:
    def build():
        parts = []
        for s in SEASONS_FEED:
            g = pd.read_csv(os.path.join(FEED, str(s), "games.csv.gz"))
            g = g.rename(columns={"gamePk": "game_pk", "officialDate": "date", "startUTC": "start_utc",
                                  "gameType": "game_type", "doubleHeader": "double_header",
                                  "gameNumber": "game_number", "venueId": "venue_id", "awayId": "away_id",
                                  "homeId": "home_id", "awayAbbr": "away_abbr", "homeAbbr": "home_abbr",
                                  "awayRuns": "away_runs", "homeRuns": "home_runs",
                                  "detailedState": "detailed_state", "dayNight": "day_night",
                                  "awayStarterId": "away_starter_id", "homeStarterId": "home_starter_id",
                                  "awayStarterHand": "away_starter_hand", "homeStarterHand": "home_starter_hand",
                                  "awayProbableId": "away_probable_id", "homeProbableId": "home_probable_id"})
            g["source"] = "mlb_feed"
            parts.append(g)
        feed = pd.concat(parts, ignore_index=True)
        abbr = pd.concat([feed[["away_id", "away_abbr"]].set_axis(["id", "abbr"], axis=1),
                          feed[["home_id", "home_abbr"]].set_axis(["id", "abbr"], axis=1)])
        abbr = abbr.drop_duplicates("id", keep="last").set_index("id")["abbr"]
        old = []
        for s in (2022, 2023):
            sch = pd.DataFrame(_schedule_rows(s))
            sch["away_abbr"] = sch["away_id"].map(abbr)
            sch["home_abbr"] = sch["home_id"].map(abbr)
            sch["source"] = "schedule"
            old.append(sch)
        games = pd.concat(old + [feed], ignore_index=True)
        games = games[games["game_type"] == "R"].copy()
        games["date"] = pd.to_datetime(games["date"])
        games["start_utc"] = pd.to_datetime(games["start_utc"], utc=True)
        for c in ("away_starter_id", "home_starter_id", "away_probable_id", "home_probable_id"):
            games[c] = games[c].astype("Int64")
        return games.drop(columns=[c for c in ("fetchedAt", "payloadSha1") if c in games]).reset_index(drop=True)
    return _cache("games", build, refresh)


# --------------------------------------------------------------------------- pitcher lines
def load_pitcher_lines(refresh: bool = False) -> pd.DataFrame:
    """One row per pitcher appearance (regular season), 2022-2026."""
    def build():
        games = load_games()
        box = []
        for s in SEASONS_BOX:
            p = os.path.join(RC, "starter_workload", str(s), "boxscores.jsonl.gz")
            for g in _jsonl_gz(p):
                for side, key in (("A", "awayPitchers"), ("H", "homePitchers")):
                    for x in g.get(key) or []:
                        box.append({"game_pk": int(g["gamePk"]), "side": side, "pitcher_id": int(x["playerId"]),
                                    "order": x.get("orderIndex"), "outs": x.get("outs"), "bf": x.get("battersFaced"),
                                    "pitches": x.get("numberOfPitches"), "k": x.get("strikeOuts"),
                                    "bb": x.get("baseOnBalls"), "h": x.get("hits"), "r": x.get("runs"),
                                    "er": x.get("earnedRuns"), "name": x.get("name")})
        box = pd.DataFrame(box)
        feed = []
        for s in SEASONS_FEED:
            f = pd.read_csv(os.path.join(FEED, str(s), "pitching.csv.gz"))
            feed.append(f)
        feed = pd.concat(feed, ignore_index=True).rename(columns={
            "gamePk": "game_pk", "pitcherId": "pitcher_id", "pitchHand": "hand", "BF": "bf", "K": "k",
            "BB": "bb", "H": "h", "R": "r", "HR": "hr"})
        feed = feed.drop(columns=["season", "teamId"])
        # merge: feed is authoritative for 2024-2026 (complete); ER comes from the box cache
        m = feed.merge(box[["game_pk", "pitcher_id", "er", "name"]], on=["game_pk", "pitcher_id"], how="left")
        old = box[~box["game_pk"].isin(feed["game_pk"])].copy()
        old["hr"] = np.nan
        old["hand"] = np.nan
        allp = pd.concat([m, old[m.columns]], ignore_index=True)
        # static handedness map from every feed appearance (handedness does not change)
        hand_map = feed.dropna(subset=["hand"]).groupby("pitcher_id")["hand"].agg(lambda s: s.mode().iat[0])
        allp["hand"] = allp["hand"].fillna(allp["pitcher_id"].map(hand_map))
        allp = allp.merge(games[["game_pk", "season", "date", "start_utc", "away_id", "home_id", "venue_id",
                                 "away_abbr", "home_abbr"]], on="game_pk", how="inner")
        allp["team_id"] = np.where(allp["side"] == "A", allp["away_id"], allp["home_id"])
        allp["opp_id"] = np.where(allp["side"] == "A", allp["home_id"], allp["away_id"])
        allp["team"] = np.where(allp["side"] == "A", allp["away_abbr"], allp["home_abbr"])
        allp["opp"] = np.where(allp["side"] == "A", allp["home_abbr"], allp["away_abbr"])
        allp["is_home"] = (allp["side"] == "H").astype(int)
        allp["is_starter"] = (allp["order"] == 0).astype(int)
        return allp.drop(columns=["away_id", "home_id", "away_abbr", "home_abbr"]).sort_values(
            ["start_utc", "game_pk", "side", "order"]).reset_index(drop=True)
    return _cache("pitcher_lines", build, refresh)


# --------------------------------------------------------------------------- batting
def load_batting(refresh: bool = False) -> pd.DataFrame:
    """One row per batter appearance 2024-2026 (starters and substitutes)."""
    def build():
        games = load_games()
        parts = [pd.read_csv(os.path.join(FEED, str(s), "batting.csv.gz")) for s in SEASONS_FEED]
        b = pd.concat(parts, ignore_index=True).rename(columns={
            "gamePk": "game_pk", "playerId": "player_id", "batSide": "bat_side", "battingOrder": "batting_order",
            "isStarter": "is_starter", "teamId": "team_id", "2B": "d2", "3B": "d3"})
        b = b.drop(columns=["season"])
        b = b.merge(games[["game_pk", "season", "date", "start_utc", "away_id", "home_id", "venue_id",
                           "away_abbr", "home_abbr", "away_starter_id", "home_starter_id",
                           "away_starter_hand", "home_starter_hand"]], on="game_pk", how="inner")
        a = b["side"] == "A"
        b["opp_id"] = np.where(a, b["home_id"], b["away_id"])
        b["team"] = np.where(a, b["away_abbr"], b["home_abbr"])
        b["opp"] = np.where(a, b["home_abbr"], b["away_abbr"])
        b["is_home"] = (~a).astype(int)
        b["opp_starter_id"] = np.where(a, b["home_starter_id"], b["away_starter_id"])
        b["opp_starter_hand"] = np.where(a, b["home_starter_hand"], b["away_starter_hand"])
        b["HRR"] = b["H"] + b["R"] + b["RBI"]
        b["1B"] = b["H"] - b["d2"] - b["d3"] - b["HR"]
        return b.drop(columns=["away_id", "home_id", "away_abbr", "home_abbr", "away_starter_id",
                               "home_starter_id", "away_starter_hand", "home_starter_hand"]).sort_values(
            ["start_utc", "game_pk", "side", "batting_order"]).reset_index(drop=True)
    return _cache("batting", build, refresh)


def load_pa(refresh: bool = False) -> pd.DataFrame:
    def build():
        parts = [pd.read_csv(os.path.join(FEED, str(s), "pa.csv.gz"),
                             usecols=["season", "gamePk", "atBatIndex", "inning", "half", "batterId", "batSide",
                                      "pitcherId", "pitchHand", "eventType", "rbi", "runsOnPlay"])
                 for s in SEASONS_FEED]
        pa = pd.concat(parts, ignore_index=True).rename(columns={
            "gamePk": "game_pk", "atBatIndex": "ab_index", "batterId": "batter_id", "batSide": "bat_side",
            "pitcherId": "pitcher_id", "pitchHand": "pitch_hand", "eventType": "event"})
        return pa
    return _cache("pa", build, refresh)


def load_team_batting(refresh: bool = False) -> pd.DataFrame:
    """Team-game batting lines 2022-2026 (team-level, all batters)."""
    def build():
        games = load_games()
        rows = []
        for s in SEASONS_BOX:
            for g in _jsonl_gz(os.path.join(RC, "batting_backtest", str(s), "boxscores.jsonl.gz")):
                for side, key in (("A", "awayBatting"), ("H", "homeBatting")):
                    x = g.get(key) or {}
                    rows.append({"game_pk": int(g["gamePk"]), "side": side, "PA": x.get("plateAppearances"),
                                 "AB": x.get("atBats"), "H": x.get("hits"), "d2": x.get("doubles"),
                                 "d3": x.get("triples"), "HR": x.get("homeRuns"), "BB": x.get("baseOnBalls"),
                                 "HBP": x.get("hitByPitch"), "K": x.get("strikeOuts"), "SF": x.get("sacFlies"),
                                 "R": x.get("runs")})
        box = pd.DataFrame(rows)
        bat = load_batting()
        agg = bat.groupby(["game_pk", "side"])[["PA", "AB", "H", "d2", "d3", "HR", "BB", "HBP", "K", "SF", "R"]].sum().reset_index()
        box = box[~box["game_pk"].isin(agg["game_pk"])]
        tb = pd.concat([agg, box], ignore_index=True)
        tb = tb.merge(games[["game_pk", "season", "date", "start_utc", "away_id", "home_id", "venue_id",
                             "away_abbr", "home_abbr", "away_runs", "home_runs"]], on="game_pk", how="inner")
        a = tb["side"] == "A"
        tb["team_id"] = np.where(a, tb["away_id"], tb["home_id"])
        tb["opp_id"] = np.where(a, tb["home_id"], tb["away_id"])
        tb["team"] = np.where(a, tb["away_abbr"], tb["home_abbr"])
        tb["opp"] = np.where(a, tb["home_abbr"], tb["away_abbr"])
        tb["is_home"] = (~a).astype(int)
        tb["runs"] = np.where(a, tb["away_runs"], tb["home_runs"]).astype(float)
        return tb.drop(columns=["away_id", "home_id", "away_abbr", "home_abbr", "away_runs", "home_runs"]).sort_values(
            ["start_utc", "game_pk", "side"]).reset_index(drop=True)
    return _cache("team_batting", build, refresh)


def load_pregame_lineups() -> pd.DataFrame:
    p = os.path.join(DATA, "research", "hitter_v2", "pregame_lineups_2026.csv.gz")
    d = pd.read_csv(p)
    return d.rename(columns={"gamePk": "game_pk", "playerId": "player_id", "captureUTC": "capture_utc",
                             "startUTC": "start_utc"})


# --------------------------------------------------------------------------- statcast (pitch level)
SWSTR = {"swinging_strike", "swinging_strike_blocked", "foul_tip", "missed_bunt"}
SWINGS = SWSTR | {"foul", "hit_into_play", "foul_bunt", "foul_pitchout", "hit_into_play_score",
                  "hit_into_play_no_out"}


def load_statcast_pitcher_games(refresh: bool = False) -> pd.DataFrame:
    """Per pitcher-game pitch-quality aggregates from data/statcast_raw (2026-08-11..09-27 only)."""
    def build():
        rows = []
        for f in sorted(glob.glob(os.path.join(DATA, "statcast_raw", "games", "*.jsonl"))):
            recs = [json.loads(l) for l in open(f) if l.strip()]
            if not recs:
                continue
            df = pd.DataFrame(recs)
            df["swstr"] = df["description"].isin(SWSTR)
            df["swing"] = df["description"].isin(SWINGS)
            df["csw"] = df["swstr"] | (df["description"] == "called_strike")
            df["is_ff"] = df["pitchType"].isin(["FF", "SI"])
            for pid, g in df.groupby("pitcherId"):
                ff = g.loc[g["is_ff"], "releaseSpeed"]
                rows.append({"game_pk": int(g["gamePk"].iat[0]), "pitcher_id": int(pid),
                             "date": g["gameDate"].iat[0], "sc_pitches": len(g),
                             "sc_swstr": int(g["swstr"].sum()), "sc_swings": int(g["swing"].sum()),
                             "sc_csw": int(g["csw"].sum()), "sc_fb_velo": float(ff.mean()) if len(ff) else np.nan,
                             "sc_hand": g["pitcherHand"].iat[0]})
        return pd.DataFrame(rows)
    return _cache("statcast_pitcher_games", build, refresh)
