"""Forward projections (sift.projections.v1) for the most recent archived MLB slate.

    python -m mlb.project [--slate-date YYYY-MM-DD] [--out results/projections_latest.json]

Uses only data dated before the slate: regular-season box lines (to 2026-09-27) plus 2026
postseason PITCHER lines (research_cache, to 2026-10-10). Postseason hitter lines are not in the
repo, so hitter and team-offence form stops at the end of the regular season. Lineups are not
confirmed in the archived slate, so each team's last regular-season starting lineup is assumed and
labelled as such. Every structural parameter (shrinkage, opponent betas, distribution shapes,
stack weights) is the frozen development fit; LightGBM components are refit once on all history.
Authority is always research_only.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PF = os.path.dirname(HERE)
if PF not in sys.path:
    sys.path.insert(0, PF)
warnings.filterwarnings("ignore")

from pf_core import gate as G  # noqa: E402
from mlb import data as D, evaluate as EV  # noqa: E402
from mlb.ladder import build_dist, walkforward_lgbm  # noqa: E402

NEW_BLOCK = 999999
CONFIRMED_LINEUP = "CONFIRMED_POSTED_LINEUP"
PITCH_FAM = {"bf": "batters_faced", "outs": "outs", "k": "pitcher_strikeouts", "bb": "pitcher_walks",
             "h": "pitcher_hits_allowed", "r": "pitcher_runs_allowed"}
HIT_FAM = {"PA": "plate_appearances", "H": "hits", "TB": "total_bases", "HR": "home_runs", "R": "runs",
           "RBI": "rbis", "HRR": "hits_runs_rbis"}


def _latest_slate(date=None):
    sd = os.path.join(D.DATA, "slates")
    dates = sorted(d for d in os.listdir(sd) if os.path.exists(os.path.join(sd, d, "authoritative.json")))
    d = date or dates[-1]
    return d, json.load(open(os.path.join(sd, d, "authoritative.json")))


def _postseason_pitcher_lines(abbr_by_id):
    rows = []
    sch = json.load(open(os.path.join(D.RC, "starter_workload", "2026", "postseason_schedule.json")))
    meta = {int(g["gamePk"]): g for g in sch["games"]}
    for r in D._jsonl_gz(os.path.join(D.RC, "starter_workload", "2026", "postseason_boxscores.jsonl.gz")):
        g = meta.get(int(r["gamePk"]))
        if g is None:
            continue
        for side, key in (("A", "awayPitchers"), ("H", "homePitchers")):
            tid = g["away"]["id"] if side == "A" else g["home"]["id"]
            oid = g["home"]["id"] if side == "A" else g["away"]["id"]
            for x in r.get(key) or []:
                rows.append({"game_pk": int(r["gamePk"]), "side": side, "team_id": tid, "pitcher_id": int(x["playerId"]),
                             "hand": np.nan, "order": x.get("orderIndex"), "outs": x.get("outs"), "bf": x.get("battersFaced"),
                             "pitches": x.get("numberOfPitches"), "k": x.get("strikeOuts"), "bb": x.get("baseOnBalls"),
                             "h": x.get("hits"), "r": x.get("runs"), "hr": np.nan, "er": x.get("earnedRuns"),
                             "name": x.get("name"), "season": 2026, "date": pd.Timestamp(g["officialDate"]),
                             "start_utc": pd.Timestamp(g["gameDate"]), "venue_id": np.nan, "opp_id": oid,
                             "team": abbr_by_id.get(tid), "opp": abbr_by_id.get(oid), "is_home": int(side == "H"),
                             "is_starter": int(x.get("orderIndex") == 0)})
    return pd.DataFrame(rows)


def _confirmed_lineup_rows(tmpl, bat, players, stat_cols):
    """Batting rows for a POSTED lineup (list of {player_id, slot, name, position}). Each row starts from the player's most
    recent box row (bat side, ids) with every outcome statistic blanked; players with no history get a blank row."""
    rows = []
    blank = bat.iloc[-1].copy()
    for c in bat.columns:
        blank[c] = np.nan
    for p in players:
        pid = int(p["player_id"])
        r = (tmpl.loc[pid].copy() if pid in tmpl.index else blank.copy())
        r["player_id"] = pid
        r["slot"] = int(p["slot"])
        r["batting_order"] = int(p["slot"]) * 100
        r["is_starter"] = 1
        if p.get("position"):
            r["position"] = p["position"]
        for c in stat_cols:
            if c in r.index:
                r[c] = np.nan
        rows.append(r)
    return pd.DataFrame(rows).reset_index(drop=True)


BAT_STAT_COLS = ("PA", "AB", "H", "d2", "d3", "HR", "R", "RBI", "BB", "IBB", "HBP", "K", "SF", "SH", "SB", "CS",
                 "TB", "LOB", "GIDP", "HRR", "1B")


def build_inputs(slate, cutoff=None, extra_root=None):
    """`cutoff` (UTC timestamp) keeps only history that started before it (replays and live runs alike: a game that
    started after the cutoff cannot inform a forecast made at the cutoff)."""
    games = D.load_games()
    pl = D.load_pitcher_lines()
    tbat = D.load_team_batting()
    bat = D.load_batting()
    pa = D.load_pa()
    if cutoff is not None:
        cut = pd.Timestamp(cutoff)
        cut = cut.tz_localize("UTC") if cut.tzinfo is None else cut.tz_convert("UTC")
        games, pl, tbat, bat = (x[x["start_utc"] < cut] for x in (games, pl, tbat, bat))
        pa = pa[pa["game_pk"].isin(games["game_pk"])]
    abbr_by_id = pd.concat([games[["away_id", "away_abbr"]].set_axis(["id", "abbr"], axis=1),
                            games[["home_id", "home_abbr"]].set_axis(["id", "abbr"], axis=1)]).drop_duplicates("id", keep="last")
    id_by_abbr = dict(zip(abbr_by_id["abbr"], abbr_by_id["id"]))
    abbr_by_id = dict(zip(abbr_by_id["id"], abbr_by_id["abbr"]))
    hand_map = pl.dropna(subset=["hand"]).groupby("pitcher_id")["hand"].first()
    ps = _postseason_pitcher_lines(abbr_by_id)
    ps["hand"] = ps["pitcher_id"].map(hand_map)
    n_extra = 0
    if extra_root:
        # archived live box scores (results stage) newer than the committed research data; deduped by game_pk
        from mlb.live import history as HIST
        ex = HIST.extra_inputs(extra_root, set(games["game_pk"]) | set(pl["game_pk"]) | set(tbat["game_pk"]) | set(bat["game_pk"]),
                               cutoff if cutoff is not None else pd.Timestamp.now(tz="UTC"), exclude_pks=set(ps["game_pk"]))
        n_extra = ex["n_games"]
        if n_extra:
            hm_new = ex["pl"].dropna(subset=["hand"]).groupby("pitcher_id")["hand"].first()
            hand_map = pd.concat([hand_map, hm_new[~hm_new.index.isin(hand_map.index)]])
            pl = pd.concat([pl, ex["pl"][pl.columns]], ignore_index=True)
            tbat = pd.concat([tbat, ex["tb"][tbat.columns]], ignore_index=True)
            bat = pd.concat([bat, ex["bat"][bat.columns]], ignore_index=True)
            games = pd.concat([games, ex["games"][[c for c in games.columns if c in ex["games"].columns]]], ignore_index=True)
            for x in (pl, tbat, bat):
                x.sort_values(["start_utc", "game_pk"], kind="mergesort", inplace=True)
                x.reset_index(drop=True, inplace=True)
    tmpl = bat.drop_duplicates("player_id", keep="last").set_index("player_id")
    ps = ps[ps["start_utc"] < (pd.Timestamp(cutoff) if cutoff is not None else
                               min(pd.Timestamp(g["startTime"]) for g in slate["games"]))]
    new_pl, new_tb, new_bat, gmeta = [], [], [], []
    names = {}
    for g in slate["games"]:
        pk = int(g["gameId"])
        start = pd.Timestamp(g["startTime"])
        date = pd.Timestamp(g.get("officialDate") or slate["date"])
        season = int(date.year)
        aid, hid = id_by_abbr[g["away"]["abbr"]], id_by_abbr[g["home"]["abbr"]]
        ven = games.loc[(games["home_id"] == hid) & (games["season"] == games["season"].max()), "venue_id"].mode()
        ven = float(ven.iat[0]) if len(ven) else np.nan
        if g.get("venueId") is not None and float(g["venueId"]) in set(games["venue_id"].dropna().astype(float)):
            ven = float(g["venueId"])
        gmeta.append({"game_pk": pk, "start_utc": start, "away": g["away"]["abbr"], "home": g["home"]["abbr"],
                      "away_id": aid, "home_id": hid, "series": g.get("seriesDescription"),
                      "away_sp": g["away"].get("pitcher"), "home_sp": g["home"].get("pitcher"),
                      "away_lineup_posted": bool((g.get("lineups") or {}).get("A")),
                      "home_lineup_posted": bool((g.get("lineups") or {}).get("H"))})
        for side, tid, oid in (("A", aid, hid), ("H", hid, aid)):
            sp = g["away" if side == "A" else "home"].get("pitcher") or {}
            if sp.get("id"):
                pid = int(sp["id"])
                new_pl.append({"game_pk": pk, "side": side, "team_id": tid, "pitcher_id": pid, "hand": hand_map.get(pid, np.nan),
                               "order": 0, "outs": np.nan, "bf": np.nan, "pitches": np.nan, "k": np.nan, "bb": np.nan,
                               "h": np.nan, "r": np.nan, "hr": np.nan, "er": np.nan, "name": sp.get("name"), "season": season,
                               "date": date, "start_utc": start, "venue_id": ven, "opp_id": oid, "team": abbr_by_id[tid],
                               "opp": abbr_by_id[oid], "is_home": int(side == "H"), "is_starter": 1})
            new_tb.append({"game_pk": pk, "side": side, "season": season, "date": date, "start_utc": start, "venue_id": ven,
                           "team_id": tid, "opp_id": oid, "team": abbr_by_id[tid], "opp": abbr_by_id[oid],
                           "is_home": int(side == "H"), "runs": np.nan, "R": np.nan, "PA": np.nan, "future": 1})
            opp_sp = g["home" if side == "A" else "away"].get("pitcher") or {}
            opp_pid = int(opp_sp["id"]) if opp_sp.get("id") else np.nan
            posted = (g.get("lineups") or {}).get(side)
            if posted:
                lu = _confirmed_lineup_rows(tmpl, bat, posted, BAT_STAT_COLS)
                lu = lu.assign(game_pk=pk, side=side, season=season, date=date, start_utc=start, venue_id=ven, team_id=tid,
                               opp_id=oid, team=abbr_by_id[tid], opp=abbr_by_id[oid], is_home=int(side == "H"),
                               opp_starter_id=opp_pid, opp_starter_hand=hand_map.get(opp_pid, np.nan),
                               assumed_lineup_from="CONFIRMED_POSTED_LINEUP", future=1)
                names.update({str(p["player_id"]): p.get("name") for p in posted})
            else:
                last_pk = bat.loc[bat["team_id"] == tid, "game_pk"].iloc[-1]
                lu = bat[(bat["game_pk"] == last_pk) & (bat["team_id"] == tid) & (bat["is_starter"] == 1)].copy()
                for c in BAT_STAT_COLS:
                    if c in lu:
                        lu[c] = np.nan
                lu = lu.assign(game_pk=pk, side=side, season=season, date=date, start_utc=start, venue_id=ven, opp_id=oid,
                               team=abbr_by_id[tid], opp=abbr_by_id[oid], is_home=int(side == "H"), opp_starter_id=opp_pid,
                               opp_starter_hand=hand_map.get(opp_pid, np.nan),
                               assumed_lineup_from=str(lu["date"].iat[0].date()), future=1)
            new_bat.append(lu)
    pl2 = pd.concat([pl, ps[pl.columns], pd.DataFrame(new_pl)[pl.columns]], ignore_index=True)
    pl2 = pl2.sort_values(["start_utc", "game_pk", "side", "order"]).reset_index(drop=True)
    tb2 = pd.concat([tbat, pd.DataFrame(new_tb)], ignore_index=True)
    nb = pd.concat(new_bat, ignore_index=True)
    bat2 = pd.concat([bat, nb[list(bat.columns) + ["future", "assumed_lineup_from"]]], ignore_index=True)
    asof = {"pitchers": str(ps["date"].max().date()) if len(ps) else str(pl["date"].max().date()),
            "hitters": str(bat["date"].max().date()), "teams": str(tbat["date"].max().date())}
    res = {"live": cutoff is not None, "n_extra_games": n_extra, "pl": pl2, "tb": tb2, "bat": bat2, "pa": pa, "games": gmeta, "new_pks": [m["game_pk"] for m in gmeta],
           "asof": asof, "assumed_lineups": nb[["team", "player_id", "slot", "assumed_lineup_from"]], "names": names}
    if cutoff is not None:
        # precise "newest input" timestamps (game start + 3h ~ when its box score existed); always before the cutoff
        def _ts(df, col):
            x = df.loc[df[col].notna(), "start_utc"]
            return (x.max() + pd.Timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ") if len(x) else None
        res["asof_utc"] = {"pitchers": _ts(pl2, "outs"), "hitters": _ts(bat, "PA"), "teams": _ts(tbat, "R")}
        res["asof"] = {"pitchers": str(pl2.loc[pl2["outs"].notna(), "date"].max().date()), "hitters": str(bat["date"].max().date()),
                       "teams": str(tbat["date"].max().date())}
    return res


def _team_vs_hand_k_live(pa, batting, halflife_days: float = 60.0):
    """features.team_vs_hand_k plus ZERO-event rows for the games being forecast.

    The research function only has rows for games present in the per-PA table, so a forecast for a game that has not been
    played falls back to the overall team K factor (opp_k_hand_fac := opp_off_k_fac) while the evaluated model used the
    hand-specific factor. Appending (game, team, date) rows with K = PA = 0 adds nothing to any decayed sum (history is
    unchanged) but gives the forecast game its own 'sums strictly before today', i.e. the same quantity the backtest used."""
    from mlb.features import ew_prior_sums
    team_of = batting[["game_pk", "player_id", "team_id", "date", "season"]].drop_duplicates(["game_pk", "player_id"])
    p = pa.merge(team_of, left_on=["game_pk", "batter_id"], right_on=["game_pk", "player_id"], how="inner")
    p["K"] = p["event"].isin(["strikeout", "strikeout_double_play"]).astype(float)
    p["PA"] = 1.0
    g = p.groupby(["game_pk", "team_id", "date", "pitch_hand"])[["K", "PA"]].sum().reset_index()
    fut = batting.loc[batting["future"].fillna(0).astype(bool), ["game_pk", "team_id", "date"]].drop_duplicates() \
        if "future" in batting else batting.iloc[0:0][["game_pk", "team_id", "date"]]
    out = []
    for h in ("L", "R"):
        gh = g[g["pitch_hand"] == h].copy()
        allg = pd.concat([g[["game_pk", "team_id", "date"]], fut]).drop_duplicates().copy()
        allg = allg.merge(gh[["game_pk", "team_id", "K", "PA"]], on=["game_pk", "team_id"], how="left").fillna({"K": 0, "PA": 0})
        sm = ew_prior_sums(allg, ["team_id"], "date", ["K", "PA"], halflife_days)
        lg = ew_prior_sums(allg, None, "date", ["K", "PA"], halflife_days)
        allg[f"kvs{h}_num"] = sm["K_ew"].to_numpy()
        allg[f"kvs{h}_den"] = sm["PA_ew"].to_numpy()
        allg[f"lgk_vs{h}"] = (lg["K_ew"] / lg["PA_ew"].replace(0, np.nan)).to_numpy()
        out.append(allg[["game_pk", "team_id", f"kvs{h}_num", f"kvs{h}_den", f"lgk_vs{h}"]])
    return out[0].merge(out[1], on=["game_pk", "team_id"])


@contextlib.contextmanager
def patched(inputs):
    from mlb import hitters as H, pitchers as P, teams as T
    saved = {k: getattr(D, k) for k in ("load_pitcher_lines", "load_team_batting", "load_batting", "load_pa", "_cache")}
    saved_wf = (P._wf, H._wf, T._wf, T.ridge_ratings)
    from mlb import features as F
    saved_vh = F.team_vs_hand_k
    if inputs.get("live"):
        F.team_vs_hand_k = _team_vs_hand_k_live
    D.load_pitcher_lines = lambda refresh=False: inputs["pl"]
    D.load_team_batting = lambda refresh=False: inputs["tb"]
    D.load_batting = lambda refresh=False: inputs["bat"]
    D.load_pa = lambda refresh=False: inputs["pa"]
    D._cache = lambda name, builder, refresh=False: builder()

    def wf(df, name, feats, target, objective, refresh=False):
        pred, _ = walkforward_lgbm(df, feats, target, "block", NEW_BLOCK, objective=objective)
        return pred
    P._wf = H._wf = T._wf = wf

    def ridge(tb):
        from pf_core.pit import rolling_ridge_ratings
        g = tb[["week", "team_id", "opp_id", "R", "is_home", "date", "season"]].copy()
        new = g["R"].isna()
        g["blk"] = new.astype(int)
        r = rolling_ridge_ratings(g, "blk", "team_id", "opp_id", "R", home_col="is_home", alpha=20.0, date_col="date",
                                  halflife_days=90, season_col="season", prior_season_decay=0.5)
        r["week"] = int(g.loc[new, "week"].iloc[0])
        return r.drop(columns=["blk"])
    T.ridge_ratings = ridge
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(D, k, v)
        P._wf, H._wf, T._wf, T.ridge_ratings = saved_wf
        F.team_vs_hand_k = saved_vh


def _summ_readiness():
    p = os.path.join(PF, "results", "summary.json")
    if not os.path.exists(p):
        return {}
    s = json.load(open(p))
    return {f["family"]: {"readiness": f["readiness"], "model": f["best_model"], "reason": f["readiness_reason"]}
            for f in s["families"]}


def _dist_summary(d, i, rungs):
    th = {str(k): round(float(d.prob_ge(k)[i]), 4) for k in rungs}
    return {"mean": round(float(d.mean[i]), 3), "median": int(d.median[i]), "p10": int(d.quantile(0.1)[i]),
            "p90": int(d.quantile(0.9)[i]), "thresholds": th}


def _asof_iso(inputs, key, end_of_day=False):
    """Timestamp of the newest input of one dataset. Live runs carry precise timestamps (inputs['asof_utc']); the legacy
    slate run only has dates."""
    if "asof_utc" in inputs:
        return inputs["asof_utc"][key]
    d = pd.Timestamp(inputs["asof"][key])
    return d.strftime("%Y-%m-%dT23:59:59Z") if end_of_day else d.tz_localize("UTC").isoformat()


def _part_dict(p):
    return {"status": p.status, "certainty": p.certainty, "source": p.source, "captured_utc": p.captured_utc,
            "reasons": list(p.reasons)}


def compute(inputs, now, participation_fn=None, emit_pmf=False, slate_info=None):
    """Frozen-model projections for inputs['games'] (sift.projections.v1 dict).

    participation_fn(kind, game_id, entity_id) -> pf_core.availability.Participation | None  (kind: 'pitcher'|'hitter');
    when given, every player row carries participation{} / role 'shadow' and the gate receives it.
    emit_pmf adds '_pmf' ({lo, probs}) and '_flags' to rows for the forecast archive (callers strip '_*' keys before
    publishing)."""
    from mlb import hitters as H, pitchers as P, teams as T
    newpk = set(inputs["new_pks"])
    frozen = json.load(open(os.path.join(PF, "results", "frozen_params_mlb.json")))
    pfin, hfin, tfin = frozen["pitchers"], frozen["hitters"], frozen["teams"]
    ready = _summ_readiness()
    live = participation_fn is not None

    with patched(inputs):
        st = P.build_frame(refresh=True)
        st.loc[st["game_pk"].isin(newpk), "block"] = NEW_BLOCK
        pres = P.run(st)
        st = pres["frame"]
        b = H.build_frame(st, refresh=True)
        b.loc[b["game_pk"].isin(newpk), "block"] = NEW_BLOCK
        hres = H.run(b)
        b = hres["frame"]
        tb = T.build_frame(st, refresh=True)
        tb.loc[tb["game_pk"].isin(newpk), "block"] = NEW_BLOCK
        tres = T.run(tb)
        tb = tres["frame"]
    players, teams_out = [], {}
    gstart = {m["game_pk"]: m["start_utc"] for m in inputs["games"]}

    def _pmf(d, i):
        return {"lo": int(d.lo), "probs": [float(x) for x in d.P[i]]}

    def _live_cols(kind, game_pk, entity_id, extra=None):
        if not live:
            return None, {}
        part = participation_fn(kind, str(game_pk), str(entity_id))
        cols = {"participation": _part_dict(part), "role": "shadow"}
        cols.update(extra or {})
        return part, cols

    # ---- pitchers
    sp_new = st["game_pk"].isin(newpk).to_numpy()
    idx = np.where(sp_new)[0]
    bfw = pfin["winners"]["bf"]
    bf_col = {"D_rc": "bf_Drc"}.get(bfw, f"bf_{bfw}")
    bf_mask = sp_new
    mix = {"n_dist_fn": lambda m: EV.make_family_dist(pfin["dist_params"]["bf"]["D_rc"], st.loc[m, "bf_Drc"].to_numpy(float)),
           "phi_grid": ()}
    bfd = build_dist(pfin["dist_params"]["bf"][bfw], st.loc[sp_new, bf_col].to_numpy(float), mix, bf_mask)
    for c, fam in PITCH_FAM.items():
        w = pfin["winners"][c]
        col = {"D_rc": "bf_Drc"}.get(w, f"{c}_{w}")
        mu = st.loc[sp_new, col].to_numpy(float)
        d = build_dist(pfin["dist_params"][c][w], mu, mix, sp_new)
        for i, r in enumerate(idx):
            row = st.iloc[r]
            hist_bf = st.loc[(st["pitcher_id"] == row["pitcher_id"]) & st["bf"].notna()].tail(10)
            ds = _dist_summary(d, i, EV.RUNGS[fam])
            fac = {"k": "opp_k_hand_fac", "bb": "opp_off_bb_fac", "h": "opp_off_h_fac", "r": "opp_off_r_fac"}.get(c)
            flags = {"opener_flag": int(row["opener_flag"]), "days_rest": None if pd.isna(row["days_rest"]) else int(row["days_rest"]),
                     "season_debut": int(row["season_debut"]), "layoff15": int(row["layoff15"]),
                     "no_start_history": int(row["no_start_history"]), "prior_starts": int(np.nan_to_num(row["bf_n"])),
                     "expected_bf": round(float(bfd.mean[i]), 2)}
            part, lc = _live_cols("pitcher", row["game_pk"], int(row["pitcher_id"]), {"_flags": flags} if emit_pmf else None)
            gi = G.GateInput(sport="MLB", family=fam, entity_id=str(int(row["pitcher_id"])), entity_name=row["name"],
                             team=row["team"], game_id=str(row["game_pk"]), game_start_utc=gstart[row["game_pk"]].isoformat(),
                             mean=ds["mean"], data_asof_utc=_asof_iso(inputs, "pitchers"),
                             expected_opportunity=float(bfd.mean[i]), opportunity_history=hist_bf["bf"].tolist(),
                             stat_history=hist_bf[c].tolist(), opponent_factor=float(row[fac]) if fac else None,
                             support_lo=0, probs=d.P[i].tolist(), thresholds=ds["thresholds"],
                             model_n_obs=int(np.nan_to_num(row["bf_n"])),
                             participation=part.status if part else None,
                             participation_reasons=part.reasons if part else None)
            gate = G.evaluate(gi, now_utc=now.isoformat())
            adj = []
            if row["days_rest"] <= 4:
                adj.append(f"Pitched {int(row['days_rest'])} day(s) before this start: treated as an opener/short outing.")
            if row["opener_flag"]:
                adj.append("Recent usage is mostly relief / very short starts (opener flag).")
            if fac:
                adj.append(f"Opponent factor x{row[fac]:.2f} (point-in-time, shrunk; elasticity fitted on 2023-24).")
            adj.append(("Pitcher lines through %s included as history; regular-season parameters, no postseason leash adjustment." if live else
                        "Postseason pitcher lines through %s included as history; regular-season parameters, no postseason leash adjustment.") % inputs["asof"]["pitchers"])
            prow = {"player_id": str(int(row["pitcher_id"])), "name": row["name"], "team": row["team"], "opponent": row["opp"],
                    "game_id": str(row["game_pk"]), "family": fam, **ds,
                    "opportunity": None if c == "bf" else {"family": "batters_faced", "mean": round(float(bfd.mean[i]), 2),
                                                           "p10": int(bfd.quantile(0.1)[i]), "p90": int(bfd.quantile(0.9)[i])},
                    "opponent_adjustment": {"factor": round(float(row[fac]), 3) if fac else 1.0, "sample_games": None},
                    "adjustments": adj, "readiness": ready.get(fam, {}).get("readiness", "SHADOW VALIDATION"),
                    "model": w, "gate": {"status": gate["status"], "findings": [{k: f[k] for k in ("check", "severity", "what", "category", "action")} for f in gate["findings"]]},
                    "data_asof_utc": _asof_iso(inputs, "pitchers", end_of_day=True), **lc}
            if emit_pmf:
                prow["_pmf"] = _pmf(d, i)
                prow["_mean_exact"] = float(d.mean[i])
            players.append(prow)
    # ---- hitters
    hn = b["game_pk"].isin(newpk).to_numpy()
    hidx = np.where(hn)[0]
    paw = hfin["winners"]["PA"]
    pa_fn = lambda m: EV.make_family_dist(hfin["dist_params"]["PA"]["D"], b.loc[m, "PA_D"].to_numpy(float))  # noqa: E731
    hmix = {"n_dist_fn": pa_fn, "phi_grid": (), "tb_fn": lambda m, mu: H.tb_dist(b, m, mu, pa_fn, K=20)}
    pad = build_dist(hfin["dist_params"]["PA"][paw], b.loc[hn, f"PA_{paw}"].to_numpy(float), hmix, hn)
    names = {}
    hp = os.path.join(D.DATA, "edgelab", "hitter_projection_snapshots")
    if os.path.isdir(hp):
        for f in sorted(os.listdir(hp)):
            for line in open(os.path.join(hp, f)):
                try:
                    x = json.loads(line)
                    names[str(x.get("playerId"))] = x.get("player")
                except Exception:
                    pass
    names.update({k: v for k, v in (inputs.get("names") or {}).items() if v})
    team_R_sum = {}
    for c, fam in HIT_FAM.items():
        w = hfin["winners"][c]
        d = build_dist(hfin["dist_params"][c][w], b.loc[hn, f"{c}_{w}"].to_numpy(float), hmix, hn)
        for i, r in enumerate(hidx):
            row = b.iloc[r]
            hist = b.loc[(b["player_id"] == row["player_id"]) & b["PA"].notna()].tail(10)
            ds = _dist_summary(d, i, EV.RUNGS[fam])
            confirmed = row["assumed_lineup_from"] == CONFIRMED_LINEUP
            if c == "R":
                team_R_sum[(row["game_pk"], row["team"])] = team_R_sum.get((row["game_pk"], row["team"]), 0) + ds["mean"]
            part, lc = _live_cols("hitter", row["game_pk"], int(row["player_id"]),
                                  {"slot": int(row["slot"]), "slot_source": "CONFIRMED_POSTED_LINEUP" if confirmed else "ASSUMED_LAST_LINEUP"})
            gi = G.GateInput(sport="MLB", family=fam, entity_id=str(int(row["player_id"])), entity_name=names.get(str(int(row["player_id"]))),
                             team=row["team"], team_verified=None, game_id=str(row["game_pk"]),
                             game_start_utc=gstart[row["game_pk"]].isoformat(), mean=ds["mean"],
                             data_asof_utc=_asof_iso(inputs, "hitters"),
                             expected_opportunity=float(pad.mean[i]), opportunity_history=hist["PA"].tolist(),
                             stat_history=hist[c].tolist(), support_lo=0, probs=d.P[i].tolist(), thresholds=ds["thresholds"],
                             model_n_obs=int(np.nan_to_num(row["PA_n"])),
                             participation=part.status if part else None,
                             participation_reasons=part.reasons if part else None)
            gate = G.evaluate(gi, now_utc=now.isoformat())
            if not confirmed:
                gate["findings"].append({"check": "lineup", "severity": "REVIEW", "what": f"Lineup not confirmed; assumed from the team's last regular-season lineup ({row['assumed_lineup_from']}), slot {int(row['slot'])}",
                                         "category": "model_assumption", "action": "Re-run after the confirmed lineup is posted.", "inputs": {}})
                if gate["status"] == "PASS":
                    gate["status"] = "REVIEW"
            slot_txt = (f"Batting slot {int(row['slot'])} from the club's POSTED lineup card." if confirmed else
                        f"Batting slot {int(row['slot'])} assumed from {row['assumed_lineup_from']} (lineup unconfirmed).")
            hrow = {"player_id": str(int(row["player_id"])), "name": names.get(str(int(row["player_id"]))), "team": row["team"],
                    "opponent": row["opp"], "game_id": str(row["game_pk"]), "family": fam, **ds,
                    "opportunity": None if c == "PA" else {"family": "plate_appearances", "mean": round(float(pad.mean[i]), 2),
                                                           "p10": int(pad.quantile(0.1)[i]), "p90": int(pad.quantile(0.9)[i])},
                    "opponent_adjustment": {"factor": round(float(row["sp_h_fac"]), 3), "sample_games": None},
                    "adjustments": [slot_txt,
                                    f"Opposing starter hit-rate factor x{row['sp_h_fac']:.2f} over an expected {row['sp_share']*100:.0f}% of PAs.",
                                    ("Hitter form is through %s (the repo/archive history available at run time)." % inputs["asof"]["hitters"]) if live else
                                    "No 2026 postseason hitter lines in the repo: form is through the end of the regular season."],
                    "readiness": ready.get(fam, {}).get("readiness", "SHADOW VALIDATION"), "model": w,
                    "gate": {"status": gate["status"], "findings": [{k: f.get(k) for k in ("check", "severity", "what", "category", "action")} for f in gate["findings"]]},
                    "data_asof_utc": _asof_iso(inputs, "hitters", end_of_day=True), **lc}
            if emit_pmf:
                hrow["_pmf"] = _pmf(d, i)
                hrow["_mean_exact"] = float(d.mean[i])
                hrow["_confirmed_slot"] = bool(confirmed)
            players.append(hrow)
    # ---- teams
    tn = tb["game_pk"].isin(newpk).to_numpy()
    tw = tfin["winner"]
    td = build_dist(tfin["dist_params"][tw], tb.loc[tn, tw].to_numpy(float), None, tn)
    games_out = []
    for m in inputs["games"]:
        tm = []
        dd = {}
        for i, r in enumerate(np.where(tn)[0]):
            row = tb.iloc[r]
            if row["game_pk"] != m["game_pk"]:
                continue
            ds = _dist_summary(td, i, EV.RUNGS["team_runs"])
            dd[row["side"]] = td.P[i]
            share = float(tfin.get("starter_run_share") or 0.93)
            rec = G.team_reconciliation(team_R_sum.get((row["game_pk"], row["team"]), 0.0), ds["mean"] * share, f"{row['team']} runs")
            trow = {"team": row["team"], "family": "team_runs", "mean": ds["mean"], "p10": ds["p10"], "p90": ds["p90"],
                    "thresholds": ds["thresholds"], "model": tw, "readiness": ready.get("team_runs", {}).get("readiness"),
                    "reconciliation": {"status": rec["status"], "ratio": round(float(rec.get("ratio", np.nan)), 3) if rec.get("ratio") else None,
                                       "note": "sum of projected starters' runs vs the starters' historical share of team runs"}}
            if emit_pmf:
                trow["_pmf"] = _pmf(td, i)
                trow["_mean_exact"] = float(td.mean[i])
                trow["_side"] = row["side"]
            tm.append(trow)
        if "A" in dd and "H" in dd:
            Ka = len(dd["A"])
            P_ = np.convolve(dd["A"], dd["H"])
            from pf_core import dists
            gd = dists.DistBatch(0, P_[None, :])
            gs = _dist_summary(gd, 0, EV.RUNGS["game_total"])
            grow = {"team": "TOTAL", "family": "game_total", **gs, "readiness": ready.get("game_total", {}).get("readiness")}
            if emit_pmf:
                grow["_pmf"] = _pmf(gd, 0)
                grow["_mean_exact"] = float(gd.mean[0])
            tm.append(grow)
        games_out.append({"game_id": str(m["game_pk"]), "start_utc": m["start_utc"].isoformat(), "home": m["home"], "away": m["away"],
                          "series": m["series"], "teams": tm})
    fams = {k: v for k, v in ready.items()}
    out = {"schema": "sift.projections.v1", "sport": "MLB", "generated_utc": now.isoformat().replace("+00:00", "Z"),
           "data_asof_utc": _asof_iso(inputs, "pitchers", end_of_day=True) if "asof_utc" in inputs else max(inputs["asof"].values()) + "T23:59:59Z",
           "model": {"id": "pf-mlb", "version": f"pf-1.0.0+{_git_sha()[:10]}", "authority": "research_only"},
           "slate": slate_info,
           "families": fams, "games": games_out, "players": players}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--slate-date", default=None)
    ap.add_argument("--out", default=os.path.join(PF, "results", "projections_latest.json"))
    a = ap.parse_args(argv)
    sdate, slate = _latest_slate(a.slate_date)
    inputs = build_inputs(slate)
    now = pd.Timestamp.now(tz="UTC").floor("s")
    info = {"date": sdate, "label": "2026 postseason slate (most recent archived). Generated after the archived slate "
            "date using ONLY data dated before it; lineups unconfirmed; may already be final — not a live pregame forecast.",
            "inputs_through": inputs["asof"]}
    out = compute(inputs, now, slate_info=info)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1, default=lambda o: None if isinstance(o, float) and not np.isfinite(o) else str(o))
    print(f"wrote {a.out}: {len(out['players'])} player projections, {len(out['games'])} games")
    return out


def _git_sha():
    import subprocess
    try:
        return subprocess.check_output(["git", "-C", PF, "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    main()
