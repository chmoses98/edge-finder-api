"""Starting-pitcher families: batters faced, outs, strikeouts, walks, hits allowed, runs allowed.

Population: every regular-season starter (orderIndex 0) 2022-2026, openers included and flagged.
Splits: development = 2023-2024 starts (2022 is history/warm-up only), validation = 2025,
holdout = 2026 regular season. Every parameter (shrinkage k, opponent betas, distribution shape,
stack weights) is fitted on development only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pf_core.pit import eb_shrink, estimate_shrinkage_k, lagged_features, sort_chrono
from pf_core import models as M
from . import data as D
from . import features as F
from . import evaluate as EV
from .ladder import (apply_glm_offset, fit_glm_offset, fit_stack, run_family, walkforward_lgbm)

DEV_SEASONS = (2023, 2024)
VAL_SEASONS = (2025,)
HOLD_SEASONS = (2026,)
STATS = ["bf", "outs", "pitches", "k", "bb", "h", "r", "hr"]
FAMILY = {"bf": "batters_faced", "outs": "outs", "k": "pitcher_strikeouts", "bb": "pitcher_walks",
          "h": "pitcher_hits_allowed", "r": "pitcher_runs_allowed"}


def split_of(season):
    s = np.asarray(season)
    return np.where(np.isin(s, DEV_SEASONS), "development",
                    np.where(np.isin(s, VAL_SEASONS), "validation",
                             np.where(np.isin(s, HOLD_SEASONS), "holdout", "warmup")))


def build_frame(refresh: bool = False) -> pd.DataFrame:
    def build():
        pl = D.load_pitcher_lines()
        tbat = D.load_team_batting()
        tb = F.team_context(tbat, dev_seasons=(2022, 2023, 2024))
        apps = pl.sort_values(["pitcher_id", "start_utc", "game_pk"], kind="mergesort").reset_index(drop=True)
        apps["relief"] = 1 - apps["is_starter"]
        g = apps.groupby("pitcher_id", sort=False)
        apps["days_rest"] = (apps["date"] - g["date"].shift(1)).dt.days
        apps["relief_share10"] = g["relief"].transform(lambda s: s.shift(1).rolling(10, min_periods=1).mean())
        apps["app_n"] = g.cumcount()
        for c in ("k", "bf", "bb", "h", "r", "outs", "pitches"):
            apps[f"app_{c}_cum"] = g[c].cumsum() - apps[c]
        # recent relief workload (pitches thrown in relief in the prior 3 days) — opener/bulk signal
        st = apps[apps["is_starter"] == 1].copy()
        st = sort_chrono(st, "pitcher_id", "start_utc")
        st = lagged_features(st, "pitcher_id", STATS, halflives=(3, 8), season_col="season")
        gs = st.groupby("pitcher_id", sort=False)
        st["last_pitches"] = gs["pitches"].shift(1)
        st["last_bf"] = gs["bf"].shift(1)
        st["max_pitches_last3"] = gs["pitches"].transform(lambda s: s.shift(1).rolling(3, min_periods=1).max())
        st["days_since_start"] = (st["date"] - gs["date"].shift(1)).dt.days
        st["season_start_no"] = st["bf_std_n"].fillna(0)
        st["season_debut"] = (st["season_start_no"] == 0).astype(int)
        st["layoff15"] = ((st["days_rest"] >= 15) & (st["season_debut"] == 0)).astype(int)
        st["opener_flag"] = (((st["bf_n"] >= 2) & (st["bf_career"] < 12)) |
                             ((st["relief_share10"] >= 0.7) & (st["app_n"] >= 3))).astype(int)
        st["no_start_history"] = (st["bf_n"].fillna(0) == 0).astype(int)
        # opponent (batting team) context, schedule-raw, PIT
        oc = ["off_k_fac", "off_bb_fac", "off_h_fac", "off_hr_fac", "off_obp_fac", "off_r_fac", "off_k_rate",
              "lg_k_rate", "lg_bb_rate", "lg_h_rate", "lg_obp_rate", "lg_r_pg", "off_pa_pg"]
        opp = tb[["game_pk", "team_id"] + oc].rename(columns={"team_id": "opp_id"})
        opp = opp.rename(columns={c: f"opp_{c}" for c in oc})
        st = st.merge(opp, on=["game_pk", "opp_id"], how="left")
        park = tb.drop_duplicates("game_pk")[["game_pk", "park_k_fac", "park_bb_fac", "park_h_fac", "park_hr_fac",
                                               "park_r_fac", "park_obp_fac"]]
        st = st.merge(park, on="game_pk", how="left")
        # opponent K% vs this pitcher's hand (2024+; per-PA feed)
        vh = F.team_vs_hand_k(D.load_pa(), D.load_batting()).rename(columns={"team_id": "opp_id"})
        st = st.merge(vh, on=["game_pk", "opp_id"], how="left")
        hand = st["hand"].fillna("R")
        num = np.where(hand == "L", st["kvsL_num"], st["kvsR_num"])
        den = np.where(hand == "L", st["kvsL_den"], st["kvsR_den"])
        lgh = np.where(hand == "L", st["lgk_vsL"], st["lgk_vsR"])
        prior = st["opp_off_k_rate"] * lgh / st["opp_lg_k_rate"]
        kk = 38.0 * 25  # ~25 team-games of evidence; same order as the overall team K k (dev MoM)
        raw = np.where(den > 0, num / np.maximum(den, 1e-9), np.nan)
        shr = eb_shrink(raw, np.nan_to_num(den), prior, kk)
        st["opp_k_hand_fac"] = np.where(np.isfinite(shr), shr / lgh, st["opp_off_k_fac"])
        st["opp_k_hand_fac"] = st["opp_k_hand_fac"].fillna(st["opp_off_k_fac"])
        st["hand_L"] = (hand == "L").astype(int)
        st["split"] = split_of(st["season"])
        st["block"] = F.month_block(st["date"])
        return st
    return D._cache("pitcher_frame", build, refresh)


# --------------------------------------------------------------------------- league PIT priors
def league_priors(st: pd.DataFrame) -> pd.DataFrame:
    st = st.copy()
    st["one"] = 1.0
    cols = ["bf", "outs", "pitches", "k", "bb", "h", "r"]
    ew = F.ew_prior_sums(st.fillna({c: 0 for c in cols}), None, "date", cols + ["one"], 60.0)
    for c in cols:
        st[f"lg_{c}_ps"] = (ew[f"{c}_ew"] / ew["one_ew"].replace(0, np.nan)).fillna(st[c].mean())
    for c in ("k", "bb", "h", "r", "outs", "pitches"):
        st[f"lg_{c}_pbf"] = (ew[f"{c}_ew"] / ew["bf_ew"].replace(0, np.nan)).fillna(st[c].sum() / st["bf"].sum())
    return st


def shrinkage_ks(st: pd.DataFrame) -> dict:
    dev = st[st["season"].isin((2022,) + DEV_SEASONS)]
    grp = dev["pitcher_id"].astype(str) + "_" + dev["season"].astype(str)
    ks = {}
    for c in ("bf", "outs", "pitches", "k", "bb", "h", "r"):
        ks[f"{c}_games"] = estimate_shrinkage_k(dev[c], grp)
    mbf = float(dev["bf"].mean())
    for c in ("k", "bb", "h", "r", "outs", "pitches"):
        ks[f"{c}_pbf_bf"] = estimate_shrinkage_k(dev[c] / dev["bf"].clip(lower=1), grp) * mbf
    return ks


E_FEATURES = ["bf_ewm3", "bf_ewm8", "bf_career", "bf_std", "bf_prev", "outs_ewm8", "pitches_ewm3", "pitches_ewm8",
              "pitches_career", "last_pitches", "last_bf", "max_pitches_last3", "days_rest", "days_since_start",
              "season_start_no", "relief_share10", "app_n", "bf_n", "k_ewm8", "k_career", "bb_ewm8", "h_ewm8",
              "r_ewm8", "hr_ewm8", "rate_k", "rate_bb", "rate_h", "rate_r", "rate_outs", "rate_ppbf",
              "app_k_rate", "opp_off_k_fac", "opp_k_hand_fac", "opp_off_bb_fac", "opp_off_h_fac",
              "opp_off_obp_fac", "opp_off_r_fac", "opp_off_hr_fac", "park_k_fac", "park_h_fac", "park_r_fac",
              "park_hr_fac", "hand_L", "is_home", "season_debut", "layoff15", "opener_flag", "month"]


def _wf(st, name, feats, target, objective, refresh=False):
    """Walk-forward LightGBM with a parquet cache keyed by (name, features, rows)."""
    import hashlib
    import os
    dh = str(int(pd.util.hash_pandas_object(st[feats + [target]], index=False).sum()))
    key = hashlib.sha1(("|".join(feats) + f"|{len(st)}|{objective}|" + dh).encode()).hexdigest()[:10]
    p = os.path.join(D.CACHE, f"lgbm_pitch_{name}_{key}.parquet")
    if os.path.exists(p) and not refresh:
        s = pd.read_parquet(p)["pred"]
        s.index = st.index
        return s
    pred, _ = walkforward_lgbm(st, feats, target, "block", 202304, objective=objective, refit_every=2)
    pd.DataFrame({"pred": pred.to_numpy()}).to_parquet(p)
    return pred


def run(st: pd.DataFrame | None = None, verbose: bool = True) -> dict:
    st = build_frame() if st is None else st
    st = league_priors(st)
    ks = shrinkage_ks(st)
    st["month"] = pd.to_datetime(st["date"]).dt.month
    # --- per-BF rates (EB in BF units) used by C/D/E
    for c in ("k", "bb", "h", "r", "outs", "pitches"):
        st[f"rate_{c if c != 'pitches' else 'ppbf'}"] = M.ratio_rate(st, c, "bf", 8, st[f"lg_{c}_pbf"], ks[f"{c}_pbf_bf"])
    st["app_k_rate"] = eb_shrink(st["app_k_cum"] / st["app_bf_cum"].replace(0, np.nan), st["app_bf_cum"],
                                 st["lg_k_pbf"], ks["k_pbf_bf"])
    out = {"ks": ks, "families": {}}
    # ---------------- opportunity models (BF, outs)
    bfA = M.est_A(st, "bf", st["lg_bf_ps"], ks["bf_games"])
    bfB = {h: M.est_B(st, "bf", st["lg_bf_ps"], ks["bf_games"], h) for h in (3, 8)}
    pitB = M.est_B(st, "pitches", st["lg_pitches_ps"], ks["pitches_games"], 3)
    bfC = pitB / st["rate_ppbf"]
    dev = (st["split"] == "development").to_numpy()
    y_bf = st["bf"].to_numpy(float)
    hB = min((3, 8), key=lambda h: np.mean(np.abs(bfB[h][dev] - y_bf[dev])))
    st["bf_A"], st["bf_B"], st["bf_C"] = bfA, bfB[hB], bfC
    # D: C x opponent on-base factor (more traffic -> more BF per inning, but earlier hook) + park
    Xbf = np.column_stack([np.log(st["opp_off_obp_fac"]), np.log(st["park_r_fac"]), np.ones(len(st))])
    bD = fit_glm_offset(y_bf[dev], st["bf_C"].to_numpy()[dev], Xbf[dev], ["opp_obp", "park_r", "icpt"])
    st["bf_D"] = apply_glm_offset(st["bf_C"], Xbf, bD, ["opp_obp", "park_r", "icpt"])
    out["bf_betas"] = bD
    out["bf_halflife"] = hB
    # D_rc: root-cause corrected structural model (role / ramp-up / layoff terms fitted on dev)
    st["lp_ratio"] = np.log((st["last_pitches"].fillna(st["pitches_ewm8"]) + 5) / (st["pitches_ewm8"].fillna(85) + 5))
    dr = st["days_rest"].fillna(99)
    rc_names = ["opp_obp", "park_r", "icpt", "opener", "debut", "no_hist", "relief_share", "layoff", "lp_ratio",
                "rest_le2", "rest_3", "rest_4", "rest_9_14"]
    Xrc = np.column_stack([np.log(st["opp_off_obp_fac"]), np.log(st["park_r_fac"]), np.ones(len(st)),
                           st["opener_flag"], st["season_debut"], st["no_start_history"],
                           st["relief_share10"].fillna(0), st["layoff15"], st["lp_ratio"].fillna(0),
                           (dr <= 2).astype(float), (dr == 3).astype(float), (dr == 4).astype(float),
                           ((dr >= 9) & (dr <= 14)).astype(float)])
    bR = fit_glm_offset(y_bf[dev], st["bf_C"].to_numpy()[dev], Xrc[dev], rc_names, l2=0.1)
    st["bf_Drc"] = apply_glm_offset(st["bf_C"], Xrc, bR, rc_names)
    out["bf_rc_betas"] = bR
    # E (walk-forward LightGBM; first predictions 2023-04, trained on all earlier months)
    E_feats = [f for f in E_FEATURES if f in st.columns]
    st["bf_E"] = _wf(st, "bf", E_feats, "bf", "regression")
    # outs
    outsA = M.est_A(st, "outs", st["lg_outs_ps"], ks["outs_games"])
    outsB = M.est_B(st, "outs", st["lg_outs_ps"], ks["outs_games"], hB)
    st["outs_A"], st["outs_B"] = outsA, outsB
    y_out = st["outs"].to_numpy(float)
    st["outs_C"] = st["bf_Drc"] * st["rate_outs"]
    Xo = np.column_stack([np.log(st["opp_off_obp_fac"]), np.ones(len(st))])
    bO = fit_glm_offset(y_out[dev], st["outs_C"].to_numpy()[dev], Xo[dev], ["opp_obp", "icpt"])
    st["outs_D"] = apply_glm_offset(st["outs_C"], Xo, bO, ["opp_obp", "icpt"])
    st["outs_E"] = _wf(st, "outs", E_feats, "outs", "regression")
    out["outs_betas"] = bO
    # ---------------- count families given BF
    opp_cols = {"k": ("opp_k_hand_fac", "park_k_fac"), "bb": ("opp_off_bb_fac", "park_bb_fac"),
                "h": ("opp_off_h_fac", "park_h_fac"), "r": ("opp_off_r_fac", "park_r_fac")}
    for c in ("k", "bb", "h", "r"):
        st[f"{c}_A"] = M.est_A(st, c, st[f"lg_{c}_ps"], ks[f"{c}_games"])
        st[f"{c}_B"] = M.est_B(st, c, st[f"lg_{c}_ps"], ks[f"{c}_games"], 8)
        st[f"{c}_C"] = st["bf_Drc"] * st[f"rate_{c}"]
        oc, pc = opp_cols[c]
        X = np.column_stack([np.log(st[oc].fillna(1.0)), np.log(st[pc].fillna(1.0)), np.ones(len(st))])
        y = st[c].to_numpy(float)
        b = fit_glm_offset(y[dev], st[f"{c}_C"].to_numpy()[dev], X[dev], ["opp", "park", "icpt"])
        st[f"{c}_D"] = apply_glm_offset(st[f"{c}_C"], X, b, ["opp", "park", "icpt"])
        out[f"{c}_betas"] = b
        st[f"{c}_E"] = _wf(st, c, E_feats + ["bf_Drc"], c, "poisson")
    # ---------------- F: non-negative stack fitted on development predictions
    for c in ("bf", "outs", "k", "bb", "h", "r"):
        cols = [f"{c}_B", f"{c}_D", f"{c}_E"] + (["bf_Drc"] if c == "bf" else [])
        okd = dev & np.isfinite(st[cols].to_numpy()).all(axis=1)
        w = fit_stack(st.loc[okd, c].to_numpy(float), st.loc[okd, cols].to_numpy(float))
        st[f"{c}_F"] = st[cols].to_numpy(float) @ w
        out[f"{c}_stack_w"] = dict(zip(cols, map(float, w)))
    out["frame"] = st
    return out


def bf_dist_ctx(st: pd.DataFrame, mean_col: str, params: dict):
    """Callable used by the binomial mixture: BF distribution for rows in a boolean mask."""
    from .evaluate import make_family_dist

    def fn(mask):
        return make_family_dist(params, st.loc[mask, mean_col].to_numpy(float))
    return fn


def evaluate_families(res: dict) -> dict:
    st = res["frame"]
    st = st[st["split"] != "warmup"].reset_index(drop=True)
    st["cluster"] = st["game_pk"]
    fams = {}
    K = {"bf": 45, "outs": 28, "k": 20, "bb": 12, "h": 16, "r": 14}
    for c in ("bf", "outs"):
        preds = {m: st[f"{c}_{m}"].to_numpy(float) for m in "ABCDEF"}
        if c == "bf":
            preds = {"A": preds["A"], "B": preds["B"], "C": preds["C"], "D": preds["D"],
                     "D_rc": st["bf_Drc"].to_numpy(float), "E": preds["E"], "F": preds["F"]}
        fams[c] = run_family(st, FAMILY[c], c, preds, "split", "cluster",
                             kinds=("poisson", "nb", "normal_binned", "empirical_resid"), K=K[c])
    # BF distribution of the frozen opportunity model (D) feeds the binomial mixtures
    bf_params = fams["bf"].dist_params["D_rc"]
    mix = {"n_dist_fn": bf_dist_ctx(st, "bf_Drc", bf_params), "phi_grid": (0.0, 0.005, 0.01, 0.02, 0.04)}
    for c in ("k", "bb", "h", "r"):
        preds = {m: st[f"{c}_{m}"].to_numpy(float) for m in "ABCDEF"}
        fams[c] = run_family(st, FAMILY[c], c, preds, "split", "cluster",
                             kinds=("poisson", "nb", "nb_binned", "empirical_resid", "binom_mix"), K=K[c], mix_ctx=mix)
    return {"families": fams, "frame": st}
