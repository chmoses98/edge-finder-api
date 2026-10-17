"""End-to-end replay of the live pipeline on REAL recorded data already in this repo (no network).

    cd research/projection_foundation && python -m mlb.live.smoke [--scenario A|B|all] [--out DIR]

Scenario A  2026-09-12 (regular season).  Pregame lineups + probables are the 527-game archive
            data/research/hitter_v2/pregame_lineups_2026.csv.gz (capture timestamps are the real capture times); final
            box scores are the committed MLB feed (data/research/hitter_w2/mlb_feed); incumbent team runs are
            data/pipeline/2026-09-12/projections.json (its createdAt tells which games it was pregame for) and incumbent
            hitters are data/edgelab/hitter_projection_snapshots/2026-09-12.jsonl.
Scenario B  2026-10-15/16 ALCS-NLCS night (postseason): real pregame probables with fetched_at, champion incumbent forecasts and
            final outcomes from data/shadow_pure/mlb_pitcher.

The recorded statsapi documents are re-assembled from those real values in the documented statsapi shape (schedule with
probablePitcher/lineups hydration, boxscore teams.<side>.players...): the shape is NOT proven by this replay, only the
logic around it. The first live Actions run proves the shape (see LIVE_MLB.md).

Each scenario runs the real stage chain (capture -> project -> publish -> results -> settle -> score -> guard) with the real
frozen models, asserts the integrity properties and returns a metrics dict.
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PF = os.path.dirname(os.path.dirname(HERE))
REPO = os.path.dirname(os.path.dirname(PF))
if PF not in sys.path:
    sys.path.insert(0, PF)

from pf_core import availability as av, prospective as pr  # noqa: E402
from mlb import data as D  # noqa: E402

FEED = os.path.join(REPO, "data", "research", "hitter_w2", "mlb_feed", "2026")


def iso(t):
    return pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ") if pd.Timestamp(t).tzinfo else \
        pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- statsapi-shaped documents from real values
def _game_doc(row, now, probables=None, lineups=None, status=None, names=None, scores=None):
    start = pd.Timestamp(row["startUTC"])
    state = status or (("Live", "In Progress") if start <= pd.Timestamp(now) else ("Preview", "Scheduled"))
    d = {"gamePk": int(row["gamePk"]), "gameType": row["gameType"], "gameDate": row["startUTC"], "officialDate": row["officialDate"],
         "status": {"abstractGameState": state[0], "detailedState": state[1]}, "doubleHeader": row["doubleHeader"],
         "gameNumber": int(row["gameNumber"]), "seriesDescription": "Regular Season" if row["gameType"] == "R" else row["gameType"],
         "venue": {"id": int(row["venueId"]), "name": ""},
         "teams": {}}
    for side, k in (("away", "A"), ("home", "H")):
        t = {"team": {"id": int(row[f"{side}Id"]), "abbreviation": row[f"{side}Abbr"], "name": row[f"{side}Abbr"]}}
        pid = (probables or {}).get(k)
        if pid:
            t["probablePitcher"] = {"id": int(pid), "fullName": (names or {}).get(int(pid), f"P{int(pid)}")}
        if scores:
            t["score"] = scores[k]
        d["teams"][side] = t
    if lineups:
        d["lineups"] = {f"{s}Players": [{"id": int(p), "fullName": (names or {}).get(int(p), f"P{int(p)}"),
                                         "primaryPosition": {"abbreviation": pos}} for p, pos in lineups[k]]
                        for s, k in (("away", "A"), ("home", "H")) if lineups.get(k)}
    return d


def box_doc_from_feed(pk, pit, bat):
    """statsapi boxscore shape from the committed feed's per-player rows."""
    teams = {}
    for side, k in (("away", "A"), ("home", "H")):
        players, pitchers = {}, []
        for _, r in pit[(pit.gamePk == pk) & (pit.side == k)].sort_values("order").iterrows():
            pitchers.append(int(r.pitcherId))
            players[f"ID{int(r.pitcherId)}"] = {"person": {"id": int(r.pitcherId), "fullName": f"P{int(r.pitcherId)}",
                                                           "pitchHand": {"code": r.pitchHand}},
                                                "stats": {"pitching": {"outs": int(r.outs), "battersFaced": int(r.BF), "numberOfPitches": int(r.pitches),
                                                                       "strikeOuts": int(r.K), "baseOnBalls": int(r.BB), "hits": int(r.H),
                                                                       "runs": int(r.R), "homeRuns": int(r.HR)}}}
        runs = 0
        for _, r in bat[(bat.gamePk == pk) & (bat.side == k)].iterrows():
            players.setdefault(f"ID{int(r.playerId)}", {"person": {"id": int(r.playerId), "fullName": f"P{int(r.playerId)}"}, "stats": {}})
            players[f"ID{int(r.playerId)}"].update({"battingOrder": str(r.battingOrder), "position": {"abbreviation": r.position}})
            players[f"ID{int(r.playerId)}"]["person"]["batSide"] = {"code": r.batSide}
            players[f"ID{int(r.playerId)}"]["stats"]["batting"] = {
                "plateAppearances": int(r.PA), "atBats": int(r.AB), "hits": int(r.H), "doubles": int(r["2B"]), "triples": int(r["3B"]),
                "homeRuns": int(r.HR), "runs": int(r.R), "rbi": int(r.RBI), "baseOnBalls": int(r.BB), "intentionalWalks": int(r.IBB),
                "hitByPitch": int(r.HBP), "strikeOuts": int(r.K), "sacFlies": int(r.SF), "sacBunts": int(r.SH), "stolenBases": int(r.SB),
                "caughtStealing": int(r.CS), "totalBases": int(r.TB), "leftOnBase": int(r.LOB), "groundIntoDoublePlay": int(r.GIDP)}
            runs += int(r.R)
        teams[side] = {"pitchers": pitchers, "players": players, "teamStats": {"batting": {"runs": runs}}}
    return {"teams": teams}


# --------------------------------------------------------------------------- git helpers (the archive is a git checkout)
def git(archive, *a):
    return subprocess.run(["git", "-C", archive, "-c", "user.email=t@t", "-c", "user.name=t", *a], check=True, capture_output=True, text=True).stdout


def init_archive(path):
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path)
    subprocess.run(["git", "init", "-q", path], check=True)
    git(path, "commit", "-q", "--allow-empty", "-m", "init")


def commit(archive, msg):
    git(archive, "add", "-A")
    try:
        git(archive, "commit", "-q", "-m", msg)
    except subprocess.CalledProcessError:
        pass


def run(archive, recorded, now, stages, extra=()):
    summ = os.path.join(archive, "..", f"summary_{now.replace(':', '')}_{stages.replace(',', '_')}.json")
    cmd = [sys.executable, "-m", "mlb.live.run", "--stage", stages, "--archive", archive, "--recorded", recorded, "--now", now,
           "--repo", REPO, "--no-step-summary", "--summary-out", summ, *extra]
    r = subprocess.run(cmd, cwd=PF, capture_output=True, text=True)
    print(r.stdout[-3500:])
    if r.returncode != 0:
        print(r.stderr[-3000:])
        raise SystemExit(f"stage chain failed: {stages} @ {now}")
    return json.load(open(summ))


def tree_files(archive):
    out = set()
    for root, ds, fs in os.walk(archive):
        ds[:] = [d for d in ds if d != ".git"]
        for f in fs:
            out.add(os.path.relpath(os.path.join(root, f), archive))
    return out


# --------------------------------------------------------------------------- scenario A
def scenario_a(out):
    date = "2026-09-12"
    pre = pd.read_csv(os.path.join(REPO, "data", "research", "hitter_v2", "pregame_lineups_2026.csv.gz"))
    games = pd.read_csv(os.path.join(FEED, "games.csv.gz"))
    games = games[games.officialDate == date]
    pit = pd.read_csv(os.path.join(FEED, "pitching.csv.gz"))
    bat = pd.read_csv(os.path.join(FEED, "batting.csv.gz"))
    names = {}
    pl = D.load_pitcher_lines()
    names.update(dict(zip(pl["pitcher_id"], pl["name"])))
    pre = pre[pre.gamePk.isin(games.gamePk)]

    def schedule_at(now):
        docs = []
        for _, g in games.iterrows():
            cap = pre[(pre.gamePk == g.gamePk) & (pd.to_datetime(pre.captureUTC) <= pd.Timestamp(now))]
            probables, lineups = {}, {}
            if len(cap):
                last = cap[cap.captureUTC == cap.captureUTC.max()]
                for k in ("A", "H"):
                    s = last[last.side == k].sort_values("slot")
                    if len(s):
                        probables[k] = s.ownProbableId.iloc[0] if pd.notna(s.ownProbableId.iloc[0]) else None
                        lineups[k] = list(zip(s.playerId, s.position))
            docs.append(_game_doc(g, now, probables, lineups, names=names))
        return {"dates": [{"date": date, "games": docs}]}

    root = os.path.join(out, "A")
    shutil.rmtree(root, ignore_errors=True)
    archive = os.path.join(root, "archive")
    init_archive(archive)
    metrics = {"scenario": "A", "date": date}
    # two real pregame runs: after the 22:00:53Z capture, after the 23:29:55Z capture
    runs = ["2026-09-12T22:01:30Z", "2026-09-12T23:30:30Z"]
    for i, now in enumerate(runs):
        rec = os.path.join(root, f"rec_{i}")
        os.makedirs(rec)
        json.dump(schedule_at(now), open(os.path.join(rec, f"schedule_{date}.json"), "w"))
        s = run(archive, rec, now, "capture,project,publish,guard")
        metrics[f"run{i}"] = {k: s["stages"][k] for k in ("capture", "project")}
        commit(archive, f"run {now}")
    # integrity: nothing archived at/after start; participation present; gate statuses sensible
    f = pr.load_forecasts(archive, "MLB")
    f["start"] = pd.to_datetime(f["game_start_utc"], utc=True)
    f["arch"] = pd.to_datetime(f["archived_utc"], utc=True)
    f["gen"] = pd.to_datetime(f["generated_utc"], utc=True)
    assert (f["arch"] < f["start"]).all() and (f["gen"] < f["start"]).all(), "late forecast archived"
    ch = f[f.model_role == "challenger"]
    players = ch[~ch.family.isin(["team_runs", "game_total"])]
    assert len(players) and players["participation"].map(lambda p: isinstance(p, dict) and "status" in p).all()
    assert set(ch[ch.family.isin(["hits", "plate_appearances"])]["slot_source"].dropna()) == {"CONFIRMED_POSTED_LINEUP"}
    metrics["archived"] = {"n": len(f), "by_role": f.groupby("model_role").size().to_dict(),
                           "by_family": f.groupby(["model_role", "family"]).size().to_dict().__repr__(),
                           "gate": ch.groupby("gate_status").size().to_dict(),
                           "participation": players["participation"].map(lambda p: p["status"]).value_counts().to_dict(),
                           "games": sorted(f.game_id.unique().tolist())}
    inc = f[f.model_role == "incumbent"]
    metrics["incumbent"] = {"n": len(inc), "families": inc.groupby("family").size().to_dict(),
                            "all_generated_before_start": bool((inc["gen"] < inc["start"]).all())}
    before = tree_files(archive)
    # idempotency: rerun the pregame stages with the same clocks -> no new forecasts / availability files
    for i, now in enumerate(runs):
        run(archive, os.path.join(root, f"rec_{i}"), now, "capture,project,publish,guard")
    after = tree_files(archive)
    new = sorted(x for x in after - before if x.startswith(("forecasts", "outcomes", "settlements", "corrections")))
    assert not new, f"re-run archived new forecasts: {new}"
    metrics["idempotent_pregame_rerun_new_forecast_files"] = 0
    # ---- results: final box scores for all games of the day (statuses Final)
    final_now = "2026-09-13T09:00:00Z"
    rec = os.path.join(root, "rec_final")
    os.makedirs(rec)
    final_docs = []
    for _, g in games.iterrows():
        sc = {"A": int(g.awayRuns), "H": int(g.homeRuns)}
        final_docs.append(_game_doc(g, final_now, status=("Final", "Final"), scores=sc))
        json.dump(box_doc_from_feed(int(g.gamePk), pit, bat), open(os.path.join(rec, f"boxscore_{int(g.gamePk)}.json"), "w"))
    json.dump({"dates": [{"date": date, "games": final_docs}]}, open(os.path.join(rec, f"schedule_{date}.json"), "w"))
    s = run(archive, rec, final_now, "results,settle,score,guard")
    commit(archive, "results")
    metrics["results"] = {k: s["stages"][k] for k in ("results", "settle")}
    st = pr.load_settlements(archive, "MLB")
    metrics["settled"] = {"rows": len(st), "void": int(st["void"].sum()),
                          "by_role_family": st[~st["void"]].groupby(["model_role", "family"]).size().to_dict().__repr__()}
    # real-values check: challenger pitcher K settled actuals equal the committed feed
    k = st[(st.family == "pitcher_strikeouts") & ~st["void"] & (st.model_role == "challenger")]
    truth = pit[pit["order"] == 0].set_index(["gamePk", "pitcherId"])["K"]
    mism = [(r.game_id, r.entity_id) for r in k.itertuples() if truth.get((int(r.game_id), int(r.entity_id)), np.nan) != r.actual]
    assert not mism, f"settled K differs from the committed feed: {mism}"
    metrics["settled_K_equals_feed_rows"] = int(len(k))
    # idempotency of results/settle/score
    before = tree_files(archive)
    s2 = run(archive, rec, final_now, "results,settle,score,guard")
    after = tree_files(archive)
    new = sorted(x for x in after - before if x.startswith(("forecasts", "outcomes", "settlements", "corrections")))
    assert not new, f"re-run wrote new immutable files: {new}"
    assert s2["stages"]["settle"]["settled"] == 0 and s2["stages"]["settle"]["corrections"] == 0
    metrics["idempotent_results_rerun"] = True
    # ---- stat correction: official scorer changes one starter's strikeouts
    kid = k.iloc[0]
    d = json.load(open(os.path.join(rec, f"boxscore_{kid.game_id}.json")))
    side = next(s_ for s_ in ("away", "home") if str(kid.entity_id) in [str(p) for p in d["teams"][s_]["pitchers"]])
    d["teams"][side]["players"][f"ID{kid.entity_id}"]["stats"]["pitching"]["strikeOuts"] += 1
    json.dump(d, open(os.path.join(rec, f"boxscore_{kid.game_id}.json"), "w"))
    before = tree_files(archive)
    s3 = run(archive, rec, "2026-09-14T09:00:00Z", "results,settle,guard")
    n_corr = s3["stages"]["settle"]["corrections"]
    st2 = pr.load_settlements(archive, "MLB")
    assert n_corr == len(k[(k.game_id == kid.game_id) & (k.entity_id == kid.entity_id)]) and n_corr >= 1
    assert float(st2[st2.forecast_id == kid.forecast_id].actual.iloc[0]) == float(kid.actual), "original settlement was modified"
    metrics["correction"] = {"corrections_written": n_corr, "original_untouched": True}
    commit(archive, "correction")
    s4 = run(archive, rec, "2026-09-14T09:00:00Z", "results,settle,guard")
    assert s4["stages"]["settle"]["corrections"] == 0
    # scorecard excerpt
    card = json.load(open(os.path.join(archive, "scorecards", "mlb", "season.json")))
    metrics["scorecard_season"] = {"capture": card["capture"], "settlement": card["settlement"],
                                   "families": [{"family": f_["family"], "models": [(m["model_id"], m["model_role"], m["n"], round(m["mae"], 3)) for m in f_["models"]],
                                                 "paired": f_.get("paired", {}).get("mae_diff")} for f_ in card["families"]],
                                   "segments_participation": card.get("segments", {}).get("participation")}
    return metrics


# --------------------------------------------------------------------------- scenario B (postseason, pitchers)
def scenario_b(out):
    store = os.path.join(REPO, "data", "shadow_pure", "mlb_pitcher")
    sch = json.load(open(os.path.join(D.RC, "starter_workload", "2026", "postseason_schedule.json")))
    teams = {}
    for g in sch["games"]:
        for s in ("away", "home"):
            teams[g[s]["id"]] = g[s].get("abbr") or g[s].get("abbreviation")
    gk = "849810"
    runs = {os.path.basename(f): json.load(open(f)) for f in glob.glob(os.path.join(store, "runs", "*", "*.json"))}
    morning = next(v for k, v in runs.items() if "20261015T165142Z" in k)
    pregame = next(v for k, v in runs.items() if "20261015T231930Z" in k)
    outc = [json.loads(x) for f in glob.glob(os.path.join(store, "outcomes", "*", "*.jsonl")) for x in open(f) if x.strip()]
    prov = [json.loads(x) for f in glob.glob(os.path.join(store, "outcome_provenance", "*", "*.jsonl")) for x in open(f) if x.strip()]
    prov = next(p for p in prov if p["game_id"] == gk)
    proj = [json.loads(x) for f in glob.glob(os.path.join(store, "projections", "*", "*champion.jsonl")) for x in open(f) if x.strip()]
    pg = [p for p in proj if p["game_id"] == gk]
    team_ids = {s: pg[0]["x_team_id"] if pg[0]["x_side"] == s else pg[0]["x_opponent_team_id"] for s in ("away", "home")}
    # use the abbreviations the model knows
    games_tbl = D.load_games()
    abbr = dict(zip(games_tbl.home_id, games_tbl.home_abbr))
    abbr.update(dict(zip(games_tbl.away_id, games_tbl.away_abbr)))

    def doc_at(run, now, final=False):
        g = next(x for x in run["games"] if x["game_id"] == gk)
        t = {}
        for s in ("away", "home"):
            pid = g["probables"][s]["id"]
            t[s] = {"team": {"id": int(team_ids[s]), "abbreviation": abbr.get(int(team_ids[s])), "name": abbr.get(int(team_ids[s]))}}
            if pid:
                t[s]["probablePitcher"] = {"id": int(pid), "fullName": g["probables"][s]["name"]}
        d = {"gamePk": int(gk), "gameType": "L", "gameDate": g["kickoff"], "officialDate": "2026-10-15",
             "status": {"abstractGameState": "Final" if final else "Preview", "detailedState": "Final" if final else "Scheduled"},
             "venue": {"id": 0}, "seriesDescription": "LCS", "doubleHeader": "N", "gameNumber": 1, "teams": t}
        return d
    root = os.path.join(out, "B")
    shutil.rmtree(root, ignore_errors=True)
    archive = os.path.join(root, "archive")
    init_archive(archive)
    metrics = {"scenario": "B", "game": gk}
    rr = [("2026-10-15T16:52:30Z", morning), ("2026-10-15T23:19:50Z", pregame)]
    for i, (now, rn) in enumerate(rr):
        rec = os.path.join(root, f"rec_{i}")
        os.makedirs(rec)
        json.dump({"dates": [{"date": "2026-10-15", "games": [doc_at(rn, now)]}]}, open(os.path.join(rec, "schedule_2026-10-15.json"), "w"))
        s = run(archive, rec, now, "capture,project,publish,guard", ["--horizon-hours", "10"])
        metrics[f"run{i}"] = {k: s["stages"][k] for k in ("capture", "project")}
        commit(archive, f"run {now}")
    f = pr.load_forecasts(archive, "MLB")
    metrics["archived"] = f.groupby(["model_role", "family", "entity_id"]).size().to_dict().__repr__()
    # results from the shadow store's real outcomes (feed/live of the day after)
    rec = os.path.join(root, "rec_final")
    os.makedirs(rec)
    final_now = "2026-10-16T08:00:00Z"
    d = doc_at(pregame, final_now, final=True)
    json.dump({"dates": [{"date": "2026-10-15", "games": [d]}]}, open(os.path.join(rec, "schedule_2026-10-15.json"), "w"))
    box = {"teams": {"away": {"pitchers": [], "players": {}, "teamStats": {"batting": {}}}, "home": {"pitchers": [], "players": {}, "teamStats": {"batting": {}}}}}
    stat = {"pitcher_outs": "outs", "pitcher_batters_faced": "battersFaced", "pitcher_strikeouts": "strikeOuts"}
    for side, pid in prov["actual_starters"].items():
        box["teams"][side]["pitchers"].append(int(pid))
        pit = {stat[o["statistic"]]: o["actual"] for o in outc if o["game_id"] == gk and o["player_id"] == pid}
        box["teams"][side]["players"][f"ID{pid}"] = {"person": {"id": int(pid), "fullName": pid}, "stats": {"pitching": pit}}
    json.dump(box, open(os.path.join(rec, f"boxscore_{gk}.json"), "w"))
    s = run(archive, rec, final_now, "results,settle,score,guard")
    metrics["results"] = {k: s["stages"][k] for k in ("results", "settle")}
    st = pr.load_settlements(archive, "MLB")
    metrics["settled"] = st[["model_role", "family", "entity_id", "mean", "actual", "void"]].to_dict("records")
    card = json.load(open(os.path.join(archive, "scorecards", "mlb", "season.json")))
    metrics["scorecard_participation"] = card["segments"].get("participation")
    metrics["actual_starters"] = prov["actual_starters"]
    return metrics


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="all")
    ap.add_argument("--out", default=os.path.join(PF, ".cache", "smoke"))
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    res = {}
    if a.scenario in ("A", "all"):
        res["A"] = scenario_a(a.out)
    if a.scenario in ("B", "all"):
        res["B"] = scenario_b(a.out)
    p = os.path.join(a.out, "smoke_metrics.json")
    json.dump(res, open(p, "w"), indent=1, default=str)
    print("SMOKE OK; metrics ->", p)
    return res


if __name__ == "__main__":
    main()
