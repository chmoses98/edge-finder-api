"""Point-in-time feature helpers for MLB (team, park, league and handedness context).

Leakage rule (same as pf_core.pit): a feature attached to a game on date d uses only games
whose officialDate is strictly earlier than d. Player-level lagged features use
pf_core.pit.lagged_features (shift one game within entity); team/park/league context uses
exponentially-decayed sums over strictly earlier *dates* (so the two games of a doubleheader
never see each other).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pf_core.pit import eb_shrink, estimate_shrinkage_k


def ew_prior_sums(df: pd.DataFrame, group_cols: list[str] | None, date_col: str, cols: list[str],
                  halflife_days: float) -> pd.DataFrame:
    """For every row: exponentially decayed sums of `cols` over rows of the same group on strictly
    earlier dates, decayed to the row's date (weight 0.5 ** (age_days / halflife))."""
    gc = list(group_cols or [])
    d = df[gc + [date_col] + cols].copy()
    d["_dn"] = (pd.to_datetime(d[date_col]) - pd.Timestamp("2000-01-01")).dt.days.astype(float)
    if not gc:
        d["_g"] = 0
        gc2 = ["_g"]
    else:
        gc2 = gc
    daily = d.groupby(gc2 + ["_dn"], sort=True)[cols].sum().reset_index()
    lam = np.log(2) / halflife_days
    out_parts = []
    for _, g in daily.groupby(gc2, sort=False):
        t = g["_dn"].to_numpy()
        X = g[cols].to_numpy(float)
        X = np.nan_to_num(X)
        S = np.zeros_like(X)        # decayed sum strictly before t_i, evaluated at t_i
        acc = np.zeros(X.shape[1])
        last_t = None
        for i in range(len(t)):
            if last_t is not None:
                acc = acc * np.exp(-lam * (t[i] - last_t))
            S[i] = acc
            acc = acc + X[i]
            last_t = t[i]
        part = g[gc2 + ["_dn"]].copy()
        part[[f"{c}_ew" for c in cols]] = S
        out_parts.append(part)
    res = pd.concat(out_parts, ignore_index=True)
    m = d[gc2 + ["_dn"]].merge(res, on=gc2 + ["_dn"], how="left")
    m.index = df.index
    return m[[f"{c}_ew" for c in cols]]


def shrunk_rate_factor(num_ew, den_ew, league_rate, k_den):
    """EB-shrunk group rate (in den units) and its factor vs the league rate."""
    num_ew = np.asarray(num_ew, float)
    den_ew = np.asarray(den_ew, float)
    raw = np.where(den_ew > 0, num_ew / np.maximum(den_ew, 1e-9), np.nan)
    shr = eb_shrink(raw, den_ew, np.asarray(league_rate, float), k_den)
    return shr, shr / np.asarray(league_rate, float)


def k_in_units(rates: pd.Series, groups: pd.Series, units_per_obs: float) -> float:
    """pf_core.estimate_shrinkage_k gives k in observations (games); convert to rate units (PA/BF)."""
    return estimate_shrinkage_k(rates, groups) * units_per_obs


def team_context(team_batting: pd.DataFrame, dev_seasons: tuple, halflife_days: float = 60.0) -> pd.DataFrame:
    """Per team-game (batting side) PIT offensive context + league context + park context.

    Returns team_batting with columns:
      off_{x}_rate / off_{x}_fac (shrunk team offensive rate per PA, factor vs league) for x in
      k, bb, h, hr, obp, plus off_r_pg (runs per game) and off_r_fac;
      lg_{x}_rate; park_{x}_fac (venue factor, both teams, long memory).
    Shrinkage strengths are estimated on dev seasons only (method of moments, pf_core).
    """
    tb = team_batting.copy()
    tb["OB"] = tb["H"] + tb["BB"] + tb["HBP"]
    tb["G"] = 1.0
    tb["R_"] = tb["R"].astype(float)
    num = {"k": "K", "bb": "BB", "h": "H", "hr": "HR", "obp": "OB"}
    cols = ["K", "BB", "H", "HR", "OB", "PA", "R_", "G"]
    lg = ew_prior_sums(tb, None, "date", cols, halflife_days)
    tm = ew_prior_sums(tb, ["team_id"], "date", cols, halflife_days)
    pk = ew_prior_sums(tb, ["venue_id"], "date", cols, 365.0)
    dev = tb["season"].isin(dev_seasons)
    for x, c in num.items():
        lgr = lg[f"{c}_ew"] / lg["PA_ew"].replace(0, np.nan)
        lgr = lgr.fillna((tb[c] / tb["PA"]).mean())
        tb[f"lg_{x}_rate"] = lgr
        kk = k_in_units((tb.loc[dev, c] / tb.loc[dev, "PA"]), tb.loc[dev, "team_id"].astype(str) + tb.loc[dev, "season"].astype(str), 38.0)
        tb[f"off_{x}_rate"], tb[f"off_{x}_fac"] = shrunk_rate_factor(tm[f"{c}_ew"], tm["PA_ew"], lgr, kk)
        kp = k_in_units((tb.loc[dev, c] / tb.loc[dev, "PA"]), tb.loc[dev, "venue_id"].astype(str), 38.0)
        _, tb[f"park_{x}_fac"] = shrunk_rate_factor(pk[f"{c}_ew"], pk["PA_ew"], lgr, kp)
    lgr = (lg["R__ew"] / lg["G_ew"].replace(0, np.nan)).fillna(tb["R_"].mean())
    tb["lg_r_pg"] = lgr
    kr = estimate_shrinkage_k(tb.loc[dev, "R_"], tb.loc[dev, "team_id"].astype(str) + tb.loc[dev, "season"].astype(str))
    tb["off_r_pg"], tb["off_r_fac"] = shrunk_rate_factor(tm["R__ew"], tm["G_ew"], lgr, kr)
    kpr = estimate_shrinkage_k(tb.loc[dev, "R_"], tb.loc[dev, "venue_id"].astype(str))
    _, tb["park_r_fac"] = shrunk_rate_factor(pk["R__ew"], pk["G_ew"], lgr, kpr)
    tb["off_pa_pg"] = (tm["PA_ew"] / tm["G_ew"].replace(0, np.nan)).fillna(38.0)
    tb["off_games_ew"] = tm["G_ew"]
    tb.attrs["k"] = {"runs_team_games": kr, "runs_park_games": kpr}
    return tb


def team_vs_hand_k(pa: pd.DataFrame, batting: pd.DataFrame, halflife_days: float = 60.0) -> pd.DataFrame:
    """PIT team strikeout rate vs LHP / RHP (2024+, from the per-PA feed).

    Returns one row per (game_pk, team_id) with opp-hand-specific rates for both hands so the
    caller can pick the hand of the pitcher it is projecting."""
    team_of = batting[["game_pk", "player_id", "team_id", "date", "season"]].drop_duplicates(["game_pk", "player_id"])
    p = pa.merge(team_of, left_on=["game_pk", "batter_id"], right_on=["game_pk", "player_id"], how="inner")
    p["K"] = p["event"].isin(["strikeout", "strikeout_double_play"]).astype(float)
    p["PA"] = 1.0
    g = p.groupby(["game_pk", "team_id", "date", "pitch_hand"])[["K", "PA"]].sum().reset_index()
    out = []
    for h in ("L", "R"):
        gh = g[g["pitch_hand"] == h].copy()
        allg = g[["game_pk", "team_id", "date"]].drop_duplicates().copy()
        allg = allg.merge(gh[["game_pk", "team_id", "K", "PA"]], on=["game_pk", "team_id"], how="left").fillna({"K": 0, "PA": 0})
        s = ew_prior_sums(allg, ["team_id"], "date", ["K", "PA"], halflife_days)
        l = ew_prior_sums(allg, None, "date", ["K", "PA"], halflife_days)
        allg[f"kvs{h}_num"] = s["K_ew"].to_numpy()
        allg[f"kvs{h}_den"] = s["PA_ew"].to_numpy()
        allg[f"lgk_vs{h}"] = (l["K_ew"] / l["PA_ew"].replace(0, np.nan)).to_numpy()
        out.append(allg[["game_pk", "team_id", f"kvs{h}_num", f"kvs{h}_den", f"lgk_vs{h}"]])
    return out[0].merge(out[1], on=["game_pk", "team_id"])


def month_block(dates: pd.Series) -> pd.Series:
    d = pd.to_datetime(dates)
    m = d.dt.month.clip(lower=4, upper=9)  # March folds into April, October into September
    return (d.dt.year * 100 + m).astype(int)
