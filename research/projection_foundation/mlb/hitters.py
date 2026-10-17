"""Hitter families: plate appearances, hits, total bases, home runs, runs, RBIs, H+R+RBI.

Population: every starting batter (isStarter=1) in 2024-2026 regular-season games.
Splits: development = 2024 (distribution/beta/stack fitting uses 2024-05-01.. only, April is a
cold start because no 2023 player-level box lines exist in the repo), validation = 2025,
holdout = 2026.

Lineup slot: the batting order in the feed is the POST-GAME official lineup. Models labelled
"known slot" use it (equivalent to forecasting after the confirmed lineup is posted, which is
when hitter props are normally priced); "C_hist_slot" uses only the player's previous slot (no
lineup information) to quantify what lineup confirmation is worth.
"""
from __future__ import annotations

import hashlib
import os

import numpy as np
import pandas as pd

from pf_core.pit import eb_shrink, estimate_shrinkage_k, lagged_features, sort_chrono
from pf_core import dists, models as M
from . import data as D
from . import features as F
from . import evaluate as EV
from .ladder import apply_glm_offset, fit_glm_offset, fit_stack, run_family, walkforward_lgbm

DEV, VAL, HOLD = (2024,), (2025,), (2026,)
FIT_START = pd.Timestamp("2024-05-01")
STATS = ["PA", "H", "TB", "HR", "R", "RBI", "HRR", "BB", "K", "1B", "d2", "d3"]
FAMILY = {"PA": "plate_appearances", "H": "hits", "TB": "total_bases", "HR": "home_runs", "R": "runs",
          "RBI": "rbis", "HRR": "hits_runs_rbis"}
HL = (10, 40)


def split_of(season):
    s = np.asarray(season)
    return np.where(np.isin(s, DEV), "development", np.where(np.isin(s, VAL), "validation",
                    np.where(np.isin(s, HOLD), "holdout", "warmup")))


def platoon_table(pa: pd.DataFrame, dev_seasons=DEV) -> pd.DataFrame:
    """League per-PA rate multipliers by (bat_side, pitch_hand), fitted on development PAs only."""
    p = pa[pa["season"].isin(dev_seasons)].copy()
    ev = p["event"]
    p["H"] = ev.isin(["single", "double", "triple", "home_run"]).astype(float)
    p["HR"] = (ev == "home_run").astype(float)
    p["TB"] = ev.map({"single": 1, "double": 2, "triple": 3, "home_run": 4}).fillna(0)
    p["K"] = ev.isin(["strikeout", "strikeout_double_play"]).astype(float)
    p["R"] = p["runsOnPlay"].astype(float)  # proxy for run environment
    p["side"] = np.where(p["bat_side"] == "S", np.where(p["pitch_hand"] == "L", "R", "L"), p["bat_side"])
    overall = p[["H", "HR", "TB", "K"]].mean()
    t = p.groupby(["bat_side", "pitch_hand"])[["H", "HR", "TB", "K"]].mean() / overall
    return t.reset_index()


def build_frame(pitch_frame: pd.DataFrame, refresh: bool = False) -> pd.DataFrame:
    def build():
        b = D.load_batting()
        b = b[b["PA"].notna()].copy()
        b["slot"] = b["slot"].where(b["is_starter"] == 1)
        b = sort_chrono(b, "player_id", "start_utc", "game_pk")
        b = lagged_features(b, "player_id", STATS, halflives=HL, season_col="season")
        g = b.groupby("player_id", sort=False)
        b["days_since_game"] = (b["date"] - g["date"].shift(1)).dt.days
        # PIT slot: previous slot as a starter (no lineup information)
        st_slot = b["slot"].copy()
        b["slot_prev"] = st_slot.groupby(b["player_id"]).transform(lambda s: s.ffill().shift(1))
        b["slot_ewm"] = st_slot.groupby(b["player_id"]).transform(lambda s: s.ewm(halflife=5, ignore_na=True).mean().shift(1))
        b["starts_prev10"] = b["is_starter"].groupby(b["player_id"]).transform(lambda s: s.shift(1).rolling(10, min_periods=1).mean())
        b = b[b["is_starter"] == 1].copy()
        # team / park context
        tb = F.team_context(D.load_team_batting(), dev_seasons=(2022, 2023, 2024))
        own = tb[["game_pk", "team_id", "off_obp_fac", "off_r_fac", "off_pa_pg", "off_k_fac", "off_h_fac",
                  "off_hr_fac", "lg_h_rate", "lg_hr_rate", "lg_k_rate", "lg_r_pg", "lg_obp_rate",
                  "park_h_fac", "park_hr_fac", "park_r_fac", "park_k_fac", "park_obp_fac"]]
        b = b.merge(own, on=["game_pk", "team_id"], how="left")
        # opponent run prevention (team runs allowed, PIT) = opponents' offense vs this pitching staff
        ra = tb[["game_pk", "opp_id", "date", "R"]].rename(columns={"opp_id": "def_id"}).copy()
        ra["G"] = 1.0
        ra["R"] = ra["R"].astype(float)
        s = F.ew_prior_sums(ra, ["def_id"], "date", ["R", "G"], 60.0)
        l = F.ew_prior_sums(ra, None, "date", ["R", "G"], 60.0)
        lg = (l["R_ew"] / l["G_ew"].replace(0, np.nan)).fillna(4.5)
        kk = estimate_shrinkage_k(ra.loc[ra["date"] < "2025-01-01", "R"], ra.loc[ra["date"] < "2025-01-01", "def_id"].astype(str))
        _, ra["opp_def_r_fac"] = F.shrunk_rate_factor(s["R_ew"], s["G_ew"], lg, kk)
        b = b.merge(ra[["game_pk", "def_id", "opp_def_r_fac"]].rename(columns={"def_id": "opp_id"}),
                    on=["game_pk", "opp_id"], how="left")
        # opposing starter quality (from the pitcher frame; PIT rates per BF, shrunk)
        pf = pitch_frame[["game_pk", "pitcher_id", "rate_k", "rate_bb", "rate_h", "rate_r", "bf_Drc", "hr_ewm8",
                          "bf_ewm8", "bf_n", "lg_k_pbf", "lg_h_pbf", "lg_r_pbf", "lg_bb_pbf", "opener_flag", "hand"]].copy()
        pf = pf.rename(columns={"pitcher_id": "opp_starter_id"})
        pf.columns = ["game_pk", "opp_starter_id"] + [f"sp_{c}" for c in pf.columns[2:]]
        b["opp_starter_id"] = b["opp_starter_id"].astype("Int64")
        pf["opp_starter_id"] = pf["opp_starter_id"].astype("Int64")
        b = b.merge(pf, on=["game_pk", "opp_starter_id"], how="left")
        b["sp_k_fac"] = b["sp_rate_k"] / b["sp_lg_k_pbf"]
        b["sp_h_fac"] = b["sp_rate_h"] / b["sp_lg_h_pbf"]
        b["sp_r_fac"] = b["sp_rate_r"] / b["sp_lg_r_pbf"]
        b["sp_bb_fac"] = b["sp_rate_bb"] / b["sp_lg_bb_pbf"]
        hr_units = np.nan_to_num(b["sp_bf_ewm8"]) * np.minimum(b["sp_bf_n"].fillna(0), 2.9 * 8)
        hr_raw = b["sp_hr_ewm8"] / b["sp_bf_ewm8"]
        b["sp_hr_fac"] = eb_shrink(hr_raw, hr_units, b["lg_hr_rate"], 600.0) / b["lg_hr_rate"]
        b["sp_share"] = np.clip(b["sp_bf_Drc"].fillna(20.0) / 38.5, 0.1, 0.9)
        # platoon (league, dev-fitted)
        pt = platoon_table(D.load_pa())
        b["opp_hand"] = b["opp_starter_hand"].fillna(b["sp_hand"]).fillna("R")
        b = b.merge(pt.rename(columns={"bat_side": "bat_side", "pitch_hand": "opp_hand", "H": "plat_h",
                                       "HR": "plat_hr", "TB": "plat_tb", "K": "plat_k"}),
                    on=["bat_side", "opp_hand"], how="left")
        for c in ("plat_h", "plat_hr", "plat_tb", "plat_k"):
            b[c] = b[c].fillna(1.0)
        b["platoon_adv"] = (((b["bat_side"] == "L") & (b["opp_hand"] == "R")) |
                            ((b["bat_side"] == "R") & (b["opp_hand"] == "L")) | (b["bat_side"] == "S")).astype(int)
        b["split"] = split_of(b["season"])
        b["block"] = F.month_block(b["date"])
        b["month"] = pd.to_datetime(b["date"]).dt.month
        b["fit_ok"] = (b["date"] >= FIT_START).astype(int)
        return b.reset_index(drop=True)
    return D._cache("hitter_frame", build, refresh)


def _slot_prior(b: pd.DataFrame, slot_col: str) -> pd.Series:
    """PIT league mean PA by (slot, home/away) from strictly earlier dates."""
    tmp = b[["date", "is_home", "PA"]].copy()
    tmp["slot_k"] = b[slot_col].round().clip(1, 9).fillna(5).astype(int)
    return M.group_prior_pit(tmp, ["slot_k", "is_home"], "date", "PA", fallback=4.2)


def _wf(b, name, feats, target, objective, refresh=False):
    dh = str(int(pd.util.hash_pandas_object(b[feats + [target]], index=False).sum()))
    key = hashlib.sha1(("|".join(feats) + f"|{len(b)}|{objective}|" + dh).encode()).hexdigest()[:10]
    p = os.path.join(D.CACHE, f"lgbm_hit_{name}_{key}.parquet")
    if os.path.exists(p) and not refresh:
        s = pd.read_parquet(p)["pred"]
        s.index = b.index
        return s
    pred, _ = walkforward_lgbm(b, feats, target, "block", 202405, objective=objective, refit_every=2,
                               params={"n_estimators": 200, "min_child_samples": 400})
    pd.DataFrame({"pred": pred.to_numpy()}).to_parquet(p)
    return pred


E_FEATURES = ["slot", "slot_prev", "slot_ewm", "is_home", "PA_ewm10", "PA_career", "PA_n", "H_ewm40", "H_ewm10",
              "TB_ewm40", "HR_ewm40", "R_ewm40", "RBI_ewm40", "BB_ewm40", "K_ewm40", "HRR_ewm40",
              "H_prev", "TB_prev", "HR_prev", "rate_H", "rate_TB", "rate_HR", "rate_R", "rate_RBI", "rate_BB",
              "off_obp_fac", "off_r_fac", "off_pa_pg", "park_h_fac", "park_hr_fac", "park_r_fac",
              "sp_k_fac", "sp_h_fac", "sp_r_fac", "sp_hr_fac", "sp_bb_fac", "sp_share", "sp_opener_flag",
              "opp_def_r_fac", "platoon_adv", "plat_h", "plat_hr", "days_since_game", "starts_prev10", "month"]

COVARS = {  # GLM covariates (log factors) for D, scaled by the starter's share where relevant
    "PA": [("own_obp", lambda b: np.log(b["off_obp_fac"])), ("park_obp", lambda b: np.log(b["park_obp_fac"])),
           ("sp_h", lambda b: b["sp_share"] * np.log(b["sp_h_fac"]))],
    "H": [("sp_h", lambda b: b["sp_share"] * np.log(b["sp_h_fac"])), ("park_h", lambda b: np.log(b["park_h_fac"])),
          ("plat", lambda b: b["sp_share"] * np.log(b["plat_h"])), ("sp_k", lambda b: b["sp_share"] * np.log(b["sp_k_fac"]))],
    "TB": [("sp_h", lambda b: b["sp_share"] * np.log(b["sp_h_fac"])), ("sp_hr", lambda b: b["sp_share"] * np.log(b["sp_hr_fac"])),
           ("park_h", lambda b: np.log(b["park_h_fac"])), ("park_hr", lambda b: np.log(b["park_hr_fac"])),
           ("plat", lambda b: b["sp_share"] * np.log(b["plat_tb"]))],
    "HR": [("sp_hr", lambda b: b["sp_share"] * np.log(b["sp_hr_fac"])), ("park_hr", lambda b: np.log(b["park_hr_fac"])),
           ("plat", lambda b: b["sp_share"] * np.log(b["plat_hr"]))],
    "R": [("own_obp", lambda b: np.log(b["off_obp_fac"])), ("sp_r", lambda b: b["sp_share"] * np.log(b["sp_r_fac"])),
          ("park_r", lambda b: np.log(b["park_r_fac"])), ("opp_def", lambda b: np.log(b["opp_def_r_fac"]))],
    "RBI": [("own_obp", lambda b: np.log(b["off_obp_fac"])), ("sp_r", lambda b: b["sp_share"] * np.log(b["sp_r_fac"])),
            ("park_r", lambda b: np.log(b["park_r_fac"])), ("opp_def", lambda b: np.log(b["opp_def_r_fac"]))],
    "HRR": [("own_obp", lambda b: np.log(b["off_obp_fac"])), ("sp_h", lambda b: b["sp_share"] * np.log(b["sp_h_fac"])),
            ("sp_r", lambda b: b["sp_share"] * np.log(b["sp_r_fac"])), ("park_r", lambda b: np.log(b["park_r_fac"])),
            ("opp_def", lambda b: np.log(b["opp_def_r_fac"]))],
}


def run(b: pd.DataFrame) -> dict:
    b = b.copy()
    out = {}
    dev_fit = ((b["split"] == "development") & (b["fit_ok"] == 1)).to_numpy()
    devall = b[b["season"].isin(DEV)]
    grp = devall["player_id"].astype(str)
    ks = {f"{c}_games": estimate_shrinkage_k(devall[c], grp) for c in FAMILY}
    mpa = float(devall["PA"].mean())
    for c in ("H", "TB", "HR", "R", "RBI", "HRR", "BB", "1B", "d2", "d3"):
        ks[f"{c}_pa"] = estimate_shrinkage_k(devall[c] / devall["PA"].clip(lower=1), grp) * mpa
    out["ks"] = ks
    # PIT league per-game means and per-PA rates (strictly earlier dates)
    b["one"] = 1.0
    lcols = ["PA", "H", "TB", "HR", "R", "RBI", "HRR", "BB", "1B", "d2", "d3"]
    ew = F.ew_prior_sums(b, None, "date", lcols + ["one"], 60.0)
    for c in lcols:
        b[f"lg_{c}_pg"] = (ew[f"{c}_ew"] / ew["one_ew"].replace(0, np.nan)).fillna(b[c].mean())
        b[f"lg_{c}_ppa"] = (ew[f"{c}_ew"] / ew["PA_ew"].replace(0, np.nan)).fillna(b[c].sum() / b["PA"].sum())
    for c in ("H", "TB", "HR", "R", "RBI", "HRR", "BB", "1B", "d2", "d3"):
        b[f"rate_{c}"] = M.ratio_rate(b, c, "PA", 40, b[f"lg_{c}_ppa"], ks[f"{c}_pa"])
    # ---------------- PA (opportunity)
    y = b["PA"].to_numpy(float)
    b["PA_A"] = M.est_A(b, "PA", b["lg_PA_pg"], ks["PA_games"])
    b["PA_B"] = M.est_B(b, "PA", b["lg_PA_pg"], ks["PA_games"], 10)
    team_pa = (b["off_pa_pg"] / 38.3).clip(0.85, 1.15)
    b["PA_C"] = _slot_prior(b, "slot") * team_pa
    b["PA_C_hist_slot"] = _slot_prior(b, "slot_prev") * team_pa
    X = np.column_stack([f(b) for _, f in COVARS["PA"]] + [np.ones(len(b))])
    names = [n for n, _ in COVARS["PA"]] + ["icpt"]
    bet = fit_glm_offset(y[dev_fit], b["PA_C"].to_numpy()[dev_fit], X[dev_fit], names)
    b["PA_D"] = apply_glm_offset(b["PA_C"], X, bet, names)
    b["PA_D_hist_slot"] = apply_glm_offset(b["PA_C_hist_slot"], X, bet, names)
    out["PA_betas"] = bet
    feats = [f for f in E_FEATURES if f in b.columns]
    feats_noslot = [f for f in feats if f != "slot"]
    b["PA_E"] = _wf(b, "PA", feats, "PA", "regression")
    b["PA_E_hist_slot"] = _wf(b, "PA_noslot", feats_noslot, "PA", "regression")
    # ---------------- counting stats = PA x per-PA rate
    for c in ("H", "TB", "HR", "R", "RBI", "HRR"):
        yc = b[c].to_numpy(float)
        b[f"{c}_A"] = M.est_A(b, c, b[f"lg_{c}_pg"], ks[f"{c}_games"])
        b[f"{c}_B"] = M.est_B(b, c, b[f"lg_{c}_pg"], ks[f"{c}_games"], 40)
        b[f"{c}_C"] = b["PA_D"] * b[f"rate_{c}"]
        X = np.column_stack([np.nan_to_num(f(b).to_numpy(float)) for _, f in COVARS[c]] + [np.ones(len(b))])
        names = [n for n, _ in COVARS[c]] + ["icpt"]
        bet = fit_glm_offset(yc[dev_fit], b[f"{c}_C"].to_numpy()[dev_fit], X[dev_fit], names)
        b[f"{c}_D"] = apply_glm_offset(b[f"{c}_C"], X, bet, names)
        b[f"{c}_D_hist_slot"] = apply_glm_offset(b["PA_D_hist_slot"] * b[f"rate_{c}"], X, bet, names)
        out[f"{c}_betas"] = bet
        b[f"{c}_E"] = _wf(b, c, feats + ["PA_D"], c, "poisson")
    for c in ("PA", "H", "TB", "HR", "R", "RBI", "HRR"):
        cols = [f"{c}_B", f"{c}_D", f"{c}_E"]
        ok = dev_fit & np.isfinite(b[cols].to_numpy()).all(axis=1)
        w = fit_stack(b.loc[ok, c].to_numpy(float), b.loc[ok, cols].to_numpy(float))
        b[f"{c}_F"] = b[cols].to_numpy(float) @ w
        out[f"{c}_stack_w"] = dict(zip(cols, map(float, w)))
    out["frame"] = b
    return out


def pa_dist_fn(b: pd.DataFrame, mean_col: str, params: dict):
    def fn(mask):
        return EV.make_family_dist(params, b.loc[mask, mean_col].to_numpy(float))
    return fn


def tb_dist(b: pd.DataFrame, mask, mu, pa_fn, K=20) -> dists.DistBatch:
    """Total bases: per-PA multinomial (out/BB, 1B, 2B, 3B, HR) from the player's shrunk component
    rates, scaled so E[TB] equals the model mean, convolved over the PA distribution."""
    nd = pa_fn(mask)
    comp = np.column_stack([b.loc[mask, f"rate_{c}"].to_numpy(float) for c in ("1B", "d2", "d3", "HR")])
    per_pa = comp @ np.array([1, 2, 3, 4])
    scale = np.asarray(mu, float) / np.maximum(nd.mean * per_pa, 1e-6)
    comp = comp * scale[:, None]
    s = comp.sum(axis=1)
    comp = np.where(s[:, None] > 0.9, comp * (0.9 / s)[:, None], comp)
    probs = np.column_stack([1 - comp.sum(axis=1), comp])
    return EV.multinomial_bases_mixture(nd, probs, K=K)


def evaluate_families(res: dict) -> dict:
    b = res["frame"]
    b = b[b["split"] != "warmup"].reset_index(drop=True)
    b["cluster"] = b["game_pk"]
    fit_mask = (b["fit_ok"] == 1).to_numpy()
    fams = {}
    Ks = {"PA": 10, "H": 7, "TB": 20, "HR": 5, "R": 6, "RBI": 10, "HRR": 14}
    preds = {m: b[f"PA_{m}"].to_numpy(float) for m in "ABCDEF"}
    preds["C_hist_slot"] = b["PA_C_hist_slot"].to_numpy(float)
    preds["D_hist_slot"] = b["PA_D_hist_slot"].to_numpy(float)
    preds["E_hist_slot"] = b["PA_E_hist_slot"].to_numpy(float)
    fams["PA"] = run_family(b, FAMILY["PA"], "PA", preds, "split", "cluster",
                            kinds=("poisson", "normal_binned", "empirical_resid"), K=Ks["PA"], fit_mask=fit_mask)
    pa_params = fams["PA"].dist_params["D"]
    pa_fn = pa_dist_fn(b, "PA_D", pa_params)
    mix = {"n_dist_fn": pa_fn, "phi_grid": (0.0, 0.01, 0.03, 0.06),
           "tb_fn": lambda mask, mu: tb_dist(b, mask, mu, pa_fn, K=Ks["TB"])}
    for c in ("H", "TB", "HR", "R", "RBI", "HRR"):
        preds = {m: b[f"{c}_{m}"].to_numpy(float) for m in "ABCDEF"}
        preds["D_hist_slot"] = b[f"{c}_D_hist_slot"].to_numpy(float)
        kinds = ["poisson", "nb", "empirical_resid"]
        if c in ("H", "HR", "R"):
            kinds.append("binom_mix")
        if c == "TB":
            kinds.append("tb_mix")
        fams[c] = run_family(b, FAMILY[c], c, preds, "split", "cluster", kinds=tuple(kinds), K=Ks[c],
                             mix_ctx=mix, fit_mask=fit_mask)
    return {"families": fams, "frame": b, "pa_fn": pa_fn}
