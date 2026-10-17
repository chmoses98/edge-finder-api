"""Team families: team runs (per team-game) and game total.

Splits: development = 2023-2024 (2022 history only), validation = 2025, holdout = 2026.
Builds on MLB-RSCH-0010 (NB dispersion ~0.28 selected over Poisson), RSCH-0015 (opponent strength
improves MAE but not NB scores), RSCH-0005 (recent form is noise) and RSCH-0033 (incumbent
projection bias -0.33, calibration slope 1.03).
"""
from __future__ import annotations

import hashlib
import os

import numpy as np
import pandas as pd

from pf_core import dists, models as M
from pf_core.pit import estimate_shrinkage_k, lagged_features, rolling_ridge_ratings, sort_chrono
from . import data as D
from . import evaluate as EV
from . import features as F
from .ladder import apply_glm_offset, fit_glm_offset, fit_stack, run_family, walkforward_lgbm

DEV, VAL, HOLD = (2023, 2024), (2025,), (2026,)


def split_of(season):
    s = np.asarray(season)
    return np.where(np.isin(s, DEV), "development", np.where(np.isin(s, VAL), "validation",
                    np.where(np.isin(s, HOLD), "holdout", "warmup")))


def build_frame(pitch_frame: pd.DataFrame, refresh: bool = False) -> pd.DataFrame:
    def build():
        tb = F.team_context(D.load_team_batting(), dev_seasons=(2022, 2023, 2024))
        fut = tb["future"].fillna(0).astype(bool) if "future" in tb else pd.Series(False, index=tb.index)
        tb = tb[tb["R"].notna() | fut].copy()
        tb["R"] = tb["R"].astype(float)
        tb = sort_chrono(tb, "team_id", "start_utc", "game_pk")
        tb = lagged_features(tb, "team_id", ["R"], halflives=(10, 40), season_col="season")
        # opponent run prevention (PIT)
        ra = tb[["game_pk", "opp_id", "date", "R"]].rename(columns={"opp_id": "def_id"}).copy()
        ra["G"] = 1.0
        s = F.ew_prior_sums(ra, ["def_id"], "date", ["R", "G"], 60.0)
        l = F.ew_prior_sums(ra, None, "date", ["R", "G"], 60.0)
        lg = (l["R_ew"] / l["G_ew"].replace(0, np.nan)).fillna(4.5)
        dv = ra["date"] < "2025-01-01"
        kk = estimate_shrinkage_k(ra.loc[dv, "R"], ra.loc[dv, "def_id"].astype(str) + ra.loc[dv, "date"].dt.year.astype(str))
        _, ra["opp_def_r_fac"] = F.shrunk_rate_factor(s["R_ew"], s["G_ew"], lg, kk)
        tb = tb.merge(ra[["game_pk", "def_id", "opp_def_r_fac"]].rename(columns={"def_id": "opp_id"}),
                      on=["game_pk", "opp_id"], how="left")
        # opposing starter (the pitcher who started for the defensive team)
        sp = pitch_frame[["game_pk", "team_id", "pitcher_id", "rate_r", "rate_k", "rate_h", "lg_r_pbf", "bf_Drc",
                          "opener_flag", "no_start_history"]].rename(columns={"team_id": "opp_id"})
        sp = sp.drop_duplicates(["game_pk", "opp_id"])
        sp.columns = ["game_pk", "opp_id"] + [f"sp_{c}" for c in sp.columns[2:]]
        tb = tb.merge(sp, on=["game_pk", "opp_id"], how="left")
        tb["sp_r_fac"] = (tb["sp_rate_r"] / tb["sp_lg_r_pbf"]).fillna(1.0)
        tb["sp_share"] = np.clip(tb["sp_bf_Drc"].fillna(20.0) / 38.5, 0.1, 0.9)
        tb["split"] = split_of(tb["season"])
        tb["block"] = F.month_block(tb["date"])
        tb["week"] = (pd.to_datetime(tb["date"]).dt.isocalendar().week.astype(int) + tb["season"] * 100)
        tb["month"] = pd.to_datetime(tb["date"]).dt.month
        return tb.reset_index(drop=True)
    return D._cache("team_frame", build, refresh)


def ridge_ratings(tb: pd.DataFrame) -> pd.DataFrame:
    """Schedule-adjusted offence/defence run ratings refitted before every week (pf_core)."""
    p = os.path.join(D.CACHE, "team_ridge.parquet")
    if os.path.exists(p):
        return pd.read_parquet(p)
    g = tb[["week", "team_id", "opp_id", "R", "is_home", "date", "season"]].copy()
    r = rolling_ridge_ratings(g, "week", "team_id", "opp_id", "R", home_col="is_home", alpha=20.0,
                              date_col="date", halflife_days=90, season_col="season", prior_season_decay=0.5)
    r.to_parquet(p)
    return r


def _wf(tb, name, feats, target, objective):
    dh = str(int(pd.util.hash_pandas_object(tb[feats + [target]], index=False).sum()))
    key = hashlib.sha1(("|".join(feats) + f"|{len(tb)}|{objective}|" + dh).encode()).hexdigest()[:10]
    p = os.path.join(D.CACHE, f"lgbm_team_{name}_{key}.parquet")
    if os.path.exists(p):
        s = pd.read_parquet(p)["pred"]
        s.index = tb.index
        return s
    pred, _ = walkforward_lgbm(tb, feats, target, "block", 202304, objective=objective, refit_every=2,
                               params={"min_child_samples": 300, "n_estimators": 200})
    pd.DataFrame({"pred": pred.to_numpy()}).to_parquet(p)
    return pred


def run(tb: pd.DataFrame) -> dict:
    tb = tb.copy()
    out = {}
    dev = (tb["split"] == "development").to_numpy()
    d0 = tb[tb["season"].isin((2022,) + DEV)]
    kR = estimate_shrinkage_k(d0["R"], d0["team_id"].astype(str) + d0["season"].astype(str))
    out["k_runs_games"] = kR
    y = tb["R"].to_numpy(float)
    tb["A"] = M.est_A(tb, "R", tb["lg_r_pg"], kR)
    tb["B"] = M.est_B(tb, "R", tb["lg_r_pg"], kR, 40)
    tb["C"] = tb["lg_r_pg"] * tb["off_r_fac"] * tb["opp_def_r_fac"]
    names = ["sp_r", "park_r", "home", "icpt"]
    X = np.column_stack([tb["sp_share"] * np.log(tb["sp_r_fac"]), np.log(tb["park_r_fac"]), tb["is_home"],
                         np.ones(len(tb))])
    bet = fit_glm_offset(y[dev], tb["C"].to_numpy()[dev], X[dev], names)
    tb["D"] = apply_glm_offset(tb["C"], X, bet, names)
    out["D_betas"] = bet
    # schedule-adjusted alternative (ridge, pf_core.rolling_ridge_ratings)
    rr = ridge_ratings(tb)
    off = rr[["week", "team", "off_rating", "intercept", "home_adv"]].rename(columns={"team": "team_id"})
    de = rr[["week", "team", "def_rating"]].rename(columns={"team": "opp_id"})
    tb = tb.merge(off, on=["week", "team_id"], how="left").merge(de, on=["week", "opp_id"], how="left")
    tb["C_ridge"] = (tb["intercept"] + tb["off_rating"] + tb["def_rating"] + tb["home_adv"] * tb["is_home"]).clip(lower=1.5)
    X2 = np.column_stack([tb["sp_share"] * np.log(tb["sp_r_fac"]), np.log(tb["park_r_fac"]), np.ones(len(tb))])
    ok = dev & tb["C_ridge"].notna().to_numpy()
    bet2 = fit_glm_offset(y[ok], tb["C_ridge"].to_numpy()[ok], X2[ok], ["sp_r", "park_r", "icpt"])
    tb["D_ridge"] = apply_glm_offset(tb["C_ridge"].fillna(tb["C"]), X2, bet2, ["sp_r", "park_r", "icpt"])
    out["D_ridge_betas"] = bet2
    feats = ["off_r_fac", "off_obp_fac", "off_k_fac", "off_hr_fac", "opp_def_r_fac", "sp_r_fac", "sp_rate_k",
             "sp_rate_h", "sp_share", "sp_opener_flag", "sp_no_start_history", "park_r_fac", "park_hr_fac",
             "is_home", "R_ewm10", "R_ewm40", "R_std", "R_prev", "lg_r_pg", "month"]
    tb["E"] = _wf(tb, "R", feats, "R", "poisson")
    cols = ["B", "D", "D_ridge", "E"]
    okd = dev & np.isfinite(tb[cols].to_numpy()).all(axis=1)
    w = fit_stack(y[okd], tb.loc[okd, cols].to_numpy(float))
    tb["F"] = tb[cols].to_numpy(float) @ w
    out["stack_w"] = dict(zip(cols, map(float, w)))
    out["frame"] = tb
    return out


def evaluate_families(res: dict) -> dict:
    tb = res["frame"]
    tb = tb[tb["split"] != "warmup"].reset_index(drop=True)
    tb["cluster"] = tb["game_pk"]
    preds = {m: tb[m].to_numpy(float) for m in ("A", "B", "C", "D", "D_ridge", "E", "F")}
    fr = run_family(tb, "team_runs", "R", preds, "split", "cluster",
                    kinds=("poisson", "nb", "nb_binned", "empirical_resid"), K=30)
    return {"families": {"team_runs": fr}, "frame": tb}


def game_total(tb: pd.DataFrame, model: str, params: dict, K: int = 30) -> pd.DataFrame:
    """Game total = convolution of the two team-run distributions (independence; RSCH-0010 found
    the run correlation small, lambda_c ~ 0.13)."""
    g = tb.pivot_table(index=["game_pk", "split", "season"], columns="side", values=[model, "R"]).dropna()
    g.columns = [f"{a}_{b}" for a, b in g.columns]
    g = g.reset_index()
    da = EV.make_family_dist(params, g[f"{model}_A"].to_numpy())
    dh = EV.make_family_dist(params, g[f"{model}_H"].to_numpy())
    Ka, Kh = da.P.shape[1], dh.P.shape[1]
    P = np.zeros((len(g), Ka + Kh - 1))
    for j in range(Kh):
        P[:, j:j + Ka] += dh.P[:, j:j + 1] * da.P
    g["dist"] = list(P)
    g["y"] = g["R_A"] + g["R_H"]
    g["mu"] = g[f"{model}_A"] + g[f"{model}_H"]
    return g
