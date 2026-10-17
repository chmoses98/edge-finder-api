"""MLB Projection Foundation walk-forward driver.

    python -m mlb.run_walkforward --stage all        (from research/projection_foundation/)
    stages: inventory | pitchers | hitters | teams | assemble | all

Every stage writes .cache/stage_<name>.json (gitignored); `assemble` turns them into
results/summary.json (sift.projection_foundation.sport_summary.v1), results/experiments/*.json
and results/tables_mlb.md. Selection rule (fixed before holdout scoring): the frozen winner is the
model with the lowest VALIDATION CRPS; if that is a black-box model (E/F) and its validation CRPS
advantage over the best transparent model (A-D, D_rc) has a game-clustered 95% CI that includes
zero, the transparent model is frozen instead. Holdout is then reported for the winner, the
baseline (A) and the incumbent only.
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import subprocess
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

from pf_core import VERSION as PF_VERSION, dists, gate as G, metrics, registry  # noqa: E402
from mlb import data as D, evaluate as EV, incumbents as INC  # noqa: E402

RESULTS = os.path.join(PF, "results")
EXPDIR = os.path.join(RESULTS, "experiments")
TRANSPARENT = ("A", "B", "C", "D", "D_rc", "C_hist_slot", "D_hist_slot")


def _sha():
    try:
        return subprocess.check_output(["git", "-C", PF, "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def _save_stage(name, obj):
    with open(os.path.join(D.CACHE, f"stage_{name}.json"), "w") as f:
        json.dump(obj, f, indent=1, default=_jd)


def _load_stage(name):
    p = os.path.join(D.CACHE, f"stage_{name}.json")
    return json.load(open(p)) if os.path.exists(p) else None


def _update_frozen(section, obj):
    """results/frozen_params_mlb.json: winners + frozen dev-fitted distribution parameters, read by
    mlb/project.py (so forward projection needs no local pickles)."""
    p = os.path.join(RESULTS, "frozen_params_mlb.json")
    cur = json.load(open(p)) if os.path.exists(p) else {}
    cur[section] = json.loads(json.dumps(obj, default=_jd))
    os.makedirs(RESULTS, exist_ok=True)
    with open(p, "w") as f:
        json.dump(cur, f, indent=1, sort_keys=True)


def _jd(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    return str(o)


def _r(x, n=4):
    try:
        return None if x is None or not np.isfinite(x) else round(float(x), n)
    except TypeError:
        return x


# --------------------------------------------------------------------------- selection
def select_winner(fr, df, y_col, candidates=None):
    m_val = fr.extra["masks"]["validation"]
    y = df.loc[m_val, y_col].to_numpy(float)
    cl = df.loc[m_val, "game_pk"].to_numpy()
    rows = [r for r in fr.rows if r["split"] == "validation" and (candidates is None or r["model"] in candidates)]
    best = min(rows, key=lambda r: r["crps"])["model"]
    note = f"lowest validation CRPS: {best}"
    if best not in TRANSPARENT:
        trans = [r for r in rows if r["model"] in TRANSPARENT and r["model"] not in ("C_hist_slot", "D_hist_slot")]
        bt = min(trans, key=lambda r: r["crps"])["model"]
        diff = EV.paired(fr.dists[(best, "validation")].crps(y), fr.dists[(bt, "validation")].crps(y), cl)
        if diff["ci95"][1] >= 0:
            note += f"; advantage over transparent {bt} not significant ({diff['mean_diff']:.4f} {diff['ci95']}) -> {bt}"
            best = bt
        else:
            note += f"; beats best transparent {bt} by {diff['mean_diff']:.4f} CRPS {np.round(diff['ci95'], 4).tolist()}"
    return best, note


def verdicts_for(fr, winner, baseline="A"):
    out = {}
    val = {r["model"]: r for r in fr.rows if r["split"] == "validation"}
    for m, r in val.items():
        if m == winner:
            out[m] = "best"
        elif m == baseline:
            out[m] = "baseline"
        else:
            vb = r.get("vs_baseline")
            w = val[winner]
            if vb and vb["ci95"][1] < 0 and r["crps"] - w["crps"] < 0.01 * w["crps"]:
                out[m] = "competitive"
            else:
                out[m] = "rejected"
    return out


def family_rows(fr, verdicts, inc_val=None):
    rows = []
    for r in fr.rows:
        if r["split"] != "validation":
            continue
        row = {k: _r(r[k]) if isinstance(r[k], float) else r[k] for k in
               ("model", "split", "n_obs", "n_clusters", "mae", "rmse", "bias", "crps", "log_score", "cover_50",
                "cover_80", "cover_90", "pit_var_ratio")}
        row["dist"] = r["dist"]
        row["mean_threshold_brier"] = _r(r.get("mean_threshold_brier"))
        vb = r.get("vs_baseline")
        row["vs_baseline"] = None if not vb else {"mean_diff": _r(vb["mean_diff"], 5), "ci95": [_r(x, 5) for x in vb["ci95"]], "metric": "crps"}
        row["vs_incumbent"] = (inc_val or {}).get(r["model"])
        row["verdict"] = verdicts.get(r["model"], "")
        rows.append(row)
    return rows


def holdout_block(fr, winner, df, y_col, inc_hold=None):
    r = next(x for x in fr.rows if x["split"] == "holdout" and x["model"] == winner)
    vb = r.get("vs_baseline")
    return {"model": winner, "n_obs": r["n_obs"], "n_clusters": r["n_clusters"], "mae": _r(r["mae"]),
            "rmse": _r(r["rmse"]), "crps": _r(r["crps"]), "bias": _r(r["bias"]), "cover_80": _r(r["cover_80"]),
            "cover_90": _r(r["cover_90"]), "pit_var_ratio": _r(r["pit_var_ratio"]),
            "mean_threshold_brier": _r(r.get("mean_threshold_brier")), "thresholds": r.get("thresholds"),
            "vs_baseline": None if not vb else {"mean_diff": _r(vb["mean_diff"], 5), "ci95": [_r(x, 5) for x in vb["ci95"]], "metric": "crps"},
            "vs_baseline_mae": None if not r.get("vs_baseline_mae") else {"mean_diff": _r(r["vs_baseline_mae"]["mean_diff"], 5), "ci95": [_r(x, 5) for x in r["vs_baseline_mae"]["ci95"]]},
            "vs_incumbent": inc_hold}


def rows_of(fr, df, model, split):
    """Positions (within df) of the rows scored for (model, split) and its DistBatch."""
    m = fr.extra["masks"][split]
    return np.where(m)[0], fr.dists[(model, split)]


def calib_rows(fr, df, y_col, model, split, family):
    pos, d = rows_of(fr, df, model, split)
    y = df[y_col].to_numpy(float)[pos]
    out = []
    for k in EV.RUNGS.get(family, []):
        p = d.prob_ge(k)
        o = (y >= k).astype(float)
        if 0 < o.mean() < 1:
            out.append({"family": family, "split": split, "rung": f"{k}+", "mean_p": _r(p.mean()), "hit_rate": _r(o.mean()),
                        "brier": _r(np.mean((p - o) ** 2)), "ece": _r(metrics.ece(p, o))})
    return out


def segment_errors(df, mask, y_col, pred_col, seg_cols):
    out = []
    sub = df[mask]
    tot = float((sub[pred_col] - sub[y_col]).abs().sum())
    for c in seg_cols:
        for k, g in sub.groupby(c, observed=True):
            if len(g) < 30:
                continue
            e = g[pred_col] - g[y_col]
            out.append({"segment": f"{c}={k}", "n": int(len(g)), "share_rows": _r(len(g) / len(sub)),
                        "share_abs_error": _r(float(e.abs().sum()) / tot), "mae": _r(e.abs().mean()), "bias": _r(e.mean()),
                        "mean_actual": _r(g[y_col].mean()), "mean_pred": _r(g[pred_col].mean())})
    return out


# --------------------------------------------------------------------------- gate
def _full(df, pos, dist):
    a = np.full(len(df), np.nan)
    a[pos] = dist.mean
    return a


def _history(df, entity, col, n=10):
    g = df.groupby(entity, sort=False)[col]
    return np.column_stack([g.shift(k).to_numpy(float) for k in range(n, 0, -1)])


def run_gate(df, pos, dist, family, entity, opp_col, stat_col, opp_hist_col, opp_mean, model_n_col,
             opp_factor_col=None, team_col="team", sample=None, seed=0):
    """Gate every projection in pos (row positions of df) using only pre-game information."""
    rng = np.random.default_rng(seed)
    sel = np.arange(len(pos))
    if sample and len(sel) > sample:
        sel = np.sort(rng.choice(sel, sample, replace=False))
    oh = _history(df, entity, opp_hist_col)
    sh = _history(df, entity, stat_col)
    res = []
    rungs = EV.RUNGS.get(family, [])
    P = dist.P
    means = dist.mean
    for i in sel:
        r = pos[i]
        row = df.iloc[r]
        start = pd.Timestamp(row["start_utc"])
        asof = (pd.Timestamp(row["date"]).tz_localize("UTC") - pd.Timedelta(hours=6))
        th = {str(k): float(1 - P[i, :k].sum()) for k in rungs}
        gi = G.GateInput(sport="MLB", family=family, entity_id=str(row[entity]), entity_name=None,
                         team=str(row.get(team_col)), game_id=str(row["game_pk"]),
                         game_start_utc=start.isoformat(), mean=float(means[i]),
                         data_asof_utc=asof.isoformat(),
                         expected_opportunity=float(opp_mean[r]) if opp_mean is not None and np.isfinite(opp_mean[r]) else None,
                         opportunity_history=[x for x in oh[r] if np.isfinite(x)],
                         stat_history=[x for x in sh[r] if np.isfinite(x)],
                         opponent_factor=float(row[opp_factor_col]) if opp_factor_col and np.isfinite(row[opp_factor_col]) else None,
                         opponent_sample_games=None, support_lo=0, probs=P[i].tolist(), thresholds=th,
                         model_n_obs=int(row[model_n_col]) if np.isfinite(row[model_n_col]) else 0)
        out = G.evaluate(gi, now_utc=(start - pd.Timedelta(hours=1)).isoformat())
        res.append((r, out, None))
    return sel, res


def gate_summary(df, pos, dist, y_col, sel, res):
    y = df[y_col].to_numpy(float)[pos[sel]]
    mu = dist.mean[sel]
    sub = dists.DistBatch(dist.lo, dist.P[sel])
    cr = sub.crps(y)
    st = np.array([o["status"] for _, o, _ in res])
    by = {}
    for s in ("PASS", "REVIEW", "BLOCK"):
        m = st == s
        if m.any():
            by[s] = {"n": int(m.sum()), "share": _r(m.mean()), "mae": _r(np.mean(np.abs(mu[m] - y[m]))),
                     "crps": _r(np.mean(cr[m])), "bias": _r(np.mean(mu[m] - y[m]))}
    return G.summarize([o for _, o, _ in res]), by


# --------------------------------------------------------------------------- experiment records
def write_records(family, fr, winner, df, y_col, train_period, eval_periods, features, hyper, provenance, limitations):
    os.makedirs(EXPDIR, exist_ok=True)
    for r in fr.rows:
        if r["split"] == "development":
            continue
        if r["split"] == "holdout" and r["model"] not in (winner, "A"):
            continue
        rec = registry.ExperimentRecord(
            experiment_id=f"MLB-PF-{family}-{r['model']}-{r['split']}", sport="MLB", family=family, model=r["model"],
            model_version=f"pf-mlb-1.0.0", question=f"How accurate is ladder model {r['model']} for {family}?",
            hypothesis="Each ladder rung adds information over the shrunk-average baseline.",
            split=r["split"], train_period=train_period, eval_period=eval_periods[r["split"]], features=features,
            hyperparameters=hyper.get(r["model"], {}), data_provenance=provenance, n_obs=r["n_obs"],
            n_independent=r["n_obs"], n_clusters=r["n_clusters"],
            point={k: r[k] for k in ("mae", "rmse", "bias", "mean_actual", "mean_pred", "corr")},
            distribution={k: r[k] for k in ("crps", "log_score", "cover_50", "cover_80", "cover_90", "pit_var_ratio",
                                            "pit_deciles", "thresholds", "dist")},
            vs_baseline=r.get("vs_baseline") or {}, limitations=limitations,
            verdict="frozen winner" if r["model"] == winner else "")
        registry.write_record(rec, EXPDIR)


# =========================================================================== stages
def stage_inventory():
    from mlb import inventory as I
    chk = I.checks()
    rows = I.matrix(chk)
    _save_stage("inventory", {"checks": chk, "rows": rows})
    return rows


def _pitch_incumbent(fams, st):
    """Champion (production pitcher engine, retroactive walk-forward rerun) and W1-D challenger,
    matched rows, families bf/outs/k, validation (2025) and holdout (2026)."""
    out = {}
    side = {arm: INC.load_pitcher_sidecar(arm) for arm in ("champion", "challenger")}
    for c in ("bf", "outs", "k"):
        fr = fams[c]
        out[c] = {}
        for sp in ("validation", "holdout"):
            for model in fr.extra["winner_and_base"]:
                pos, d = rows_of(fr, st, model, sp)
                sub = st.iloc[pos][["game_pk", "pitcher_id", c]].reset_index(drop=True)
                sub["i"] = np.arange(len(sub))
                for arm, sc in side.items():
                    j = sub.merge(sc[sc["stat"] == c], on=["game_pk", "pitcher_id"], how="inner")
                    if not len(j):
                        continue
                    rungs = sorted(set.intersection(*[set(t) for t in j["inc_th"]]))
                    y = j[c].to_numpy(float)
                    ii = j["i"].to_numpy()
                    pm = EV.dist_prob_ge_matrix(dists.DistBatch(d.lo, d.P[ii]), rungs)
                    pi = INC.thresholds_to_pge(j["inc_th"].tolist(), rungs)
                    rps_m = EV.rung_rps(pm, y, rungs)
                    rps_i = EV.rung_rps(pi, y, rungs)
                    mae_m = np.abs(d.mean[ii] - y)
                    mae_i = np.abs(j["inc_mean"].to_numpy() - y)
                    kr = [k for k in EV.RUNGS[ {"bf": "batters_faced", "outs": "outs", "k": "pitcher_strikeouts"}[c]] if k in rungs]
                    bm = np.mean([(pm[:, rungs.index(k)] - (y >= k)) ** 2 for k in kr], axis=0)
                    bi = np.mean([(pi[:, rungs.index(k)] - (y >= k)) ** 2 for k in kr], axis=0)
                    cl = j["game_pk"].to_numpy()
                    out[c][f"{model}|{sp}|{arm}"] = {
                        "n_obs": int(len(j)), "n_clusters": int(pd.Series(cl).nunique()), "rungs": rungs,
                        "model_mae": _r(mae_m.mean()), "inc_mae": _r(mae_i.mean()),
                        "model_bias": _r((d.mean[ii] - y).mean()), "inc_bias": _r((j["inc_mean"] - y).mean()),
                        "model_rung_rps": _r(rps_m.mean()), "inc_rung_rps": _r(rps_i.mean()),
                        "model_brier_kalshi_rungs": _r(bm.mean()), "inc_brier_kalshi_rungs": _r(bi.mean()),
                        "kalshi_rungs": kr,
                        "rps_diff": EV.paired(rps_m, rps_i, cl), "mae_diff": EV.paired(mae_m, mae_i, cl),
                        "brier_diff": EV.paired(bm, bi, cl)}
    return out


def _statcast_substudy(st):
    """Does pitch-level quality (prior whiff%, CSW%, fastball velo change) improve strikeouts beyond
    the frozen D model? Only 2026-08-11..09-27 exists, so: features from prior Statcast starts,
    fit on starts 08-25..09-08, test on 09-09..09-27."""
    sc = D.load_statcast_pitcher_games()
    sc = sc.sort_values(["pitcher_id", "date"])
    g = sc.groupby("pitcher_id")
    for c in ("sc_swstr", "sc_pitches", "sc_csw"):
        sc[f"{c}_cum"] = g[c].cumsum() - sc[c]
    sc["sc_n_prior"] = g.cumcount()
    sc["velo_prior"] = g["sc_fb_velo"].transform(lambda s: s.expanding().mean().shift(1))
    sc["velo_last"] = g["sc_fb_velo"].shift(1)
    sc["whiff"] = sc["sc_swstr_cum"] / sc["sc_pitches_cum"].replace(0, np.nan)
    sc["csw"] = sc["sc_csw_cum"] / sc["sc_pitches_cum"].replace(0, np.nan)
    sc["velo_delta"] = sc["velo_last"] - sc["velo_prior"]
    j = st.merge(sc[["game_pk", "pitcher_id", "whiff", "csw", "velo_delta", "sc_n_prior"]], on=["game_pk", "pitcher_id"])
    j = j[(j["sc_n_prior"] >= 2) & j["whiff"].notna()].copy()
    j["date"] = pd.to_datetime(j["date"])
    lg_w = j["whiff"].mean()
    j["x_whiff"] = np.log(j["whiff"] / lg_w) - np.log(j["rate_k"] / j["lg_k_pbf"])  # whiff info beyond K% already used
    j["x_velo"] = j["velo_delta"].fillna(0).clip(-3, 3)
    fit = j[(j["date"] >= "2026-08-25") & (j["date"] <= "2026-09-08")]
    test = j[j["date"] > "2026-09-08"]
    from mlb.ladder import apply_glm_offset, fit_glm_offset
    names = ["x_whiff", "x_velo"]
    b = fit_glm_offset(fit["k"], fit["k_D"], fit[names].to_numpy(), names, l2=1.0)
    test = test.copy()
    test["k_SC"] = apply_glm_offset(test["k_D"], test[names].to_numpy(), b, names)
    ll = lambda y, mu: -(y * np.log(mu) - mu)  # noqa: E731  Poisson deviance proxy (up to constants)
    d_mae = EV.paired(np.abs(test["k_SC"] - test["k"]), np.abs(test["k_D"] - test["k"]), test["game_pk"])
    d_ll = EV.paired(ll(test["k"], test["k_SC"]), ll(test["k"], test["k_D"]), test["game_pk"])
    return {"n_fit": int(len(fit)), "n_test": int(len(test)), "betas": b, "test_mae_D": _r(np.abs(test["k_D"] - test["k"]).mean()),
            "test_mae_D_plus_statcast": _r(np.abs(test["k_SC"] - test["k"]).mean()), "mae_diff": d_mae, "poisson_nll_diff": d_ll}


def stage_pitchers():
    from mlb import pitchers as P
    t0 = time.time()
    res = P.run()
    ev = P.evaluate_families(res)
    st, fams = ev["frame"], ev["families"]
    out = {"params": {k: v for k, v in res.items() if k != "frame"}, "families": {}, "seconds": None}
    for c, fr in fams.items():
        cands = None
        win, note = select_winner(fr, st, c, cands)
        fr.extra["winner"] = win
        fr.extra["winner_and_base"] = [win, "A"] + (["D_rc"] if c == "bf" and win != "D_rc" else []) + (["D"] if win != "D" else [])
        out["families"][c] = {"winner": win, "selection": note, "dist_choice": fr.dist_choice, "verdicts": verdicts_for(fr, win)}
    inc = _pitch_incumbent(fams, st)
    out["incumbent"] = inc
    # segment error analysis (validation, frozen winner and transparent D/D_rc)
    st["early_exit_le12bf"] = (st["bf"] <= 12).astype(int)
    st["rest_bucket"] = pd.cut(st["days_rest"].fillna(99), [-1, 2, 3, 4, 6, 8, 14, 1000], labels=["<=2", "3", "4", "5-6", "7-8", "9-14", "15+"])
    st["month"] = pd.to_datetime(st["date"]).dt.month
    segs = {}
    for c in ("bf", "k"):
        fr = fams[c]
        for model in ("D", "D_rc", fr.extra["winner"]) if c == "bf" else ("D", fr.extra["winner"]):
            m = fr.extra["masks"]["validation"]
            st[f"_p_{c}_{model}"] = np.nan
            st.loc[m, f"_p_{c}_{model}"] = fr.dists[(model, "validation")].mean
            segs[f"{c}|{model}"] = segment_errors(st, m, c, f"_p_{c}_{model}",
                                                 ["opener_flag", "season_debut", "layoff15", "no_start_history",
                                                  "early_exit_le12bf", "rest_bucket", "month", "hand_L"])
    out["segments"] = segs
    out["statcast"] = _statcast_substudy(st[st["season"] == 2026])
    # calibration at Kalshi rungs (holdout, winners)
    cal = []
    for c, fr in fams.items():
        cal += calib_rows(fr, st, c, fr.extra["winner"], "holdout", fr.family)
    out["calibration"] = cal
    # gate (holdout, frozen winners)
    gates = {}
    for c, fr in fams.items():
        win = fr.extra["winner"]
        pos, d = rows_of(fr, st, win, "holdout")
        opp_mean = _full(st, *rows_of(fams["bf"], st, fams["bf"].extra["winner"], "holdout"))
        sel, res_g = run_gate(st, pos, d, fr.family, "pitcher_id", None, c, "bf", opp_mean, "bf_n",
                              opp_factor_col={"k": "opp_k_hand_fac", "bb": "opp_off_bb_fac", "h": "opp_off_h_fac",
                                              "r": "opp_off_r_fac"}.get(c), team_col="team")
        summ, by = gate_summary(st, pos, d, c, sel, res_g)
        gates[c] = {"summary": summ, "error_by_status": by}
    out["gate"] = gates
    out["n_starts"] = {sp: int((st["split"] == sp).sum()) for sp in ("development", "validation", "holdout")}
    fam_summ = {}
    for c, fr in fams.items():
        win = fr.extra["winner"]
        incv = {}
        inch = None
        if c in inc:
            for key, v in inc[c].items():
                model, sp, arm = key.split("|")
                if arm != "champion":
                    continue
                cmp = {"mean_diff": _r(v["rps_diff"]["mean_diff"], 5), "ci95": [_r(x, 5) for x in v["rps_diff"]["ci95"]],
                       "metric": f"rung RPS over incumbent rungs {v['rungs'][0]}..{v['rungs'][-1]} (matched n={v['n_obs']})",
                       "mae_diff": _r(v["mae_diff"]["mean_diff"], 4), "mae_ci95": [_r(x, 4) for x in v["mae_diff"]["ci95"]]}
                if sp == "validation":
                    incv[model] = cmp
                elif model == win:
                    inch = cmp
        fam_summ[c] = {"rows": family_rows(fr, out["families"][c]["verdicts"], incv),
                       "holdout": holdout_block(fr, win, st, c, inch)}
        write_records(fr.family, fr, win, st, c, "2022-2024 (history 2022; fitting 2023-2024)",
                      {"validation": "2025 RS", "holdout": "2026 RS"},
                      ["see mlb/pitchers.py E_FEATURES / ladder definitions"],
                      {"A": {"k_games": res["ks"].get(f"{c}_games")}, "B": {"halflife": 8}, "D": res.get(f"{c}_betas", {}),
                       "F": res.get(f"{c}_stack_w", {})},
                      ["data/research_cache/starter_workload", "data/research/hitter_w2/mlb_feed"],
                      ["Starter identity is the actual starter (probable == starter 99.9% in the post-game feed; scratches unmeasurable)."])
    out["summary_families"] = fam_summ
    out["seconds"] = time.time() - t0
    _save_stage("pitchers", out)
    _update_frozen("pitchers", {"winners": {c: fr.extra["winner"] for c, fr in fams.items()},
                                "dist_params": {c: {m: fr.dist_params[m] for m in {fr.extra["winner"], "D_rc"} if m in fr.dist_params}
                                                for c, fr in fams.items()}})
    with open(os.path.join(D.CACHE, "pitch_final.pkl"), "wb") as f:
        pickle.dump({"frame": st, "winners": {c: fr.extra["winner"] for c, fr in fams.items()},
                     "dist_params": {c: fr.dist_params for c, fr in fams.items()}, "params": out["params"]}, f)
    return out


def _hit_incumbent(fams, b):
    inc = INC.load_hitter_incumbent()
    out = {}
    for c in ("PA", "H", "TB", "HR", "R", "RBI"):
        fr = fams[c]
        win = fr.extra["winner"]
        pos, d = rows_of(fr, b, win, "holdout")
        sub = b.iloc[pos][["game_pk", "player_id", c, "date"]].reset_index(drop=True)
        sub["i"] = np.arange(len(sub))
        j = sub.merge(inc, on=["game_pk", "player_id"], how="inner")
        if not len(j):
            continue
        y = j[c].to_numpy(float)
        ii = j["i"].to_numpy()
        K = d.P.shape[1]
        di = INC.pmf_dicts_to_dist(j[f"inc_{c}_pmf"].tolist(), K)
        dm = dists.DistBatch(d.lo, d.P[ii])
        cl = j["game_pk"].to_numpy()
        fam = fr.family
        rungs = EV.RUNGS[fam]
        bm = np.mean([(dm.prob_ge(k) - (y >= k)) ** 2 for k in rungs], axis=0)
        bi = np.mean([(di.prob_ge(k) - (y >= k)) ** 2 for k in rungs], axis=0)
        out[c] = {"model": win, "n_obs": int(len(j)), "n_clusters": int(pd.Series(cl).nunique()),
                  "dates": [str(j["date"].min().date()), str(j["date"].max().date())],
                  "model_crps": _r(dm.crps(y).mean()), "inc_crps": _r(di.crps(y).mean()),
                  "model_mae": _r(np.abs(dm.mean - y).mean()), "inc_mae": _r(np.abs(di.mean - y).mean()),
                  "model_bias": _r((dm.mean - y).mean()), "inc_bias": _r((di.mean - y).mean()),
                  "model_brier": _r(bm.mean()), "inc_brier": _r(bi.mean()),
                  "model_cover80": _r(metrics.dist_metrics(y, dm)["cover_80"]), "inc_cover80": _r(metrics.dist_metrics(y, di)["cover_80"]),
                  "crps_diff": EV.paired(dm.crps(y), di.crps(y), cl), "mae_diff": EV.paired(np.abs(dm.mean - y), np.abs(di.mean - y), cl),
                  "brier_diff": EV.paired(bm, bi, cl)}
    return out


def stage_hitters():
    from mlb import hitters as H
    t0 = time.time()
    pf = pickle.load(open(os.path.join(D.CACHE, "pitch_final.pkl"), "rb"))["frame"]
    b = H.build_frame(pf)
    res = H.run(b)
    ev = H.evaluate_families(res)
    b, fams = ev["frame"], ev["families"]
    out = {"params": {k: v for k, v in res.items() if k != "frame"}, "families": {}}
    for c, fr in fams.items():
        cands = [m for m in ("A", "B", "C", "D", "E", "F")]
        win, note = select_winner(fr, b, c, cands)
        fr.extra["winner"] = win
        out["families"][c] = {"winner": win, "selection": note, "dist_choice": fr.dist_choice, "verdicts": verdicts_for(fr, win)}
    # lineup-slot knowledge: known slot vs PIT previous slot (validation + holdout)
    slot = {}
    for c in ("PA", "H", "TB", "R", "RBI", "HRR"):
        fr = fams[c]
        for sp in ("validation", "holdout"):
            m = fr.extra["masks"][sp]
            y = b.loc[m, c].to_numpy(float)
            cl = b.loc[m, "game_pk"].to_numpy()
            dk, dh = fr.dists[("D", sp)], fr.dists[("D_hist_slot", sp)]
            slot[f"{c}|{sp}"] = {"crps_known": _r(dk.crps(y).mean()), "crps_hist": _r(dh.crps(y).mean()),
                                 "mae_known": _r(np.abs(dk.mean - y).mean()), "mae_hist": _r(np.abs(dh.mean - y).mean()),
                                 "diff_hist_minus_known": EV.paired(dh.crps(y), dk.crps(y), cl),
                                 "share_slot_changed": _r((b.loc[m, "slot"] != b.loc[m, "slot_prev"]).mean())}
    out["slot_knowledge"] = slot
    out["incumbent"] = _hit_incumbent(fams, b)
    # archived-pregame-lineup subset (2026): does the retrospective slot overstate accuracy?
    pre = D.load_pregame_lineups()[["game_pk", "player_id", "slot"]].rename(columns={"slot": "slot_pre"})
    b2 = b.merge(pre.drop_duplicates(["game_pk", "player_id"]), on=["game_pk", "player_id"], how="left")
    out["pregame_lineup_subset"] = {"rows_in_holdout_with_archived_lineup": int(b2.loc[b2["split"] == "holdout", "slot_pre"].notna().sum()),
                                    "same_slot_rate": _r((b2["slot_pre"] == b2["slot"])[b2["slot_pre"].notna()].mean())}
    # segments (validation, winner)
    b["slot_changed"] = (b["slot"] != b["slot_prev"]).astype(int)
    b["rest_days"] = pd.cut(b["days_since_game"].fillna(99), [-1, 1, 2, 4, 10, 1000], labels=["1", "2", "3-4", "5-10", "11+"])
    b["early_season"] = (b["PA_std_n"].fillna(0) < 10).astype(int)
    b["slot_grp"] = pd.cut(b["slot"], [0, 2, 5, 9], labels=["1-2", "3-5", "6-9"])
    segs = {}
    for c in ("PA", "H", "TB", "HRR"):
        fr = fams[c]
        win = fr.extra["winner"]
        m = fr.extra["masks"]["validation"]
        b[f"_p_{c}"] = np.nan
        b.loc[m, f"_p_{c}"] = fr.dists[(win, "validation")].mean
        segs[f"{c}|{win}"] = segment_errors(b, m, c, f"_p_{c}", ["slot_changed", "rest_days", "early_season", "slot_grp",
                                                                 "platoon_adv", "is_home", "sp_opener_flag", "month"])
    out["segments"] = segs
    out["calibration"] = []
    for c, fr in fams.items():
        out["calibration"] += calib_rows(fr, b, c, fr.extra["winner"], "holdout", fr.family)
    # gate on a holdout sample (per family 8,000 projections)
    gates = {}
    pa_win = fams["PA"].extra["winner"]
    pa_mean = _full(b, *rows_of(fams["PA"], b, pa_win, "holdout"))
    for c, fr in fams.items():
        win = fr.extra["winner"]
        pos, d = rows_of(fr, b, win, "holdout")
        sel, res_g = run_gate(b, pos, d, fr.family, "player_id", None, c, "PA", pa_mean, "PA_n",
                              opp_factor_col="sp_h_fac" if c in ("H", "TB") else None, team_col="team", sample=8000)
        summ, by = gate_summary(b, pos, d, c, sel, res_g)
        gates[c] = {"summary": summ, "error_by_status": by, "sampled": int(len(sel))}
    out["gate"] = gates
    fam_summ = {}
    for c, fr in fams.items():
        win = fr.extra["winner"]
        ic = out["incumbent"].get(c)
        inch = None if not ic else {"mean_diff": _r(ic["crps_diff"]["mean_diff"], 5), "ci95": [_r(x, 5) for x in ic["crps_diff"]["ci95"]],
                                    "metric": f"CRPS vs production hitter engine rerun, matched n={ic['n_obs']} ({ic['dates'][0]}..{ic['dates'][1]})",
                                    "mae_diff": _r(ic["mae_diff"]["mean_diff"], 4), "mae_ci95": [_r(x, 4) for x in ic["mae_diff"]["ci95"]]}
        fam_summ[c] = {"rows": family_rows(fr, out["families"][c]["verdicts"]), "holdout": holdout_block(fr, win, b, c, inch)}
        write_records(fr.family, fr, win, b, c, "2024 (fit from 2024-05-01)", {"validation": "2025 RS", "holdout": "2026 RS"},
                      ["see mlb/hitters.py E_FEATURES / COVARS"],
                      {"D": res.get(f"{c}_betas", {}), "F": res.get(f"{c}_stack_w", {})},
                      ["data/research/hitter_w2/mlb_feed (batting, pa)", "pitcher frame (opposing starter)"],
                      ["Batting order is the post-game official lineup (retrospective; pregame lineups archived only for 527 games)."])
    out["summary_families"] = fam_summ
    out["seconds"] = time.time() - t0
    _save_stage("hitters", out)
    _update_frozen("hitters", {"winners": {c: fr.extra["winner"] for c, fr in fams.items()},
                               "dist_params": {c: {m: fr.dist_params[m] for m in {fr.extra["winner"], "D"}} for c, fr in fams.items()}})
    with open(os.path.join(D.CACHE, "hit_final.pkl"), "wb") as f:
        keep = [c for c in b.columns if not c.startswith("_p_")]
        pickle.dump({"frame": b[keep], "winners": {c: fr.extra["winner"] for c, fr in fams.items()},
                     "dist_params": {c: fr.dist_params for c, fr in fams.items()}, "params": out["params"],
                     "hold_R_mean": fams["R"].dists[(fams["R"].extra["winner"], "holdout")].mean,
                     "hold_R_pos": rows_of(fams["R"], b, fams["R"].extra["winner"], "holdout")[0]}, f)
    return out


def stage_teams():
    from mlb import teams as T
    t0 = time.time()
    pf = pickle.load(open(os.path.join(D.CACHE, "pitch_final.pkl"), "rb"))["frame"]
    tb = T.build_frame(pf)
    res = T.run(tb)
    ev = T.evaluate_families(res)
    tb, fr = ev["frame"], ev["families"]["team_runs"]
    win, note = select_winner(fr, tb, "R")
    fr.extra["winner"] = win
    out = {"params": {k: v for k, v in res.items() if k != "frame"}, "winner": win, "selection": note,
           "dist_choice": fr.dist_choice, "verdicts": verdicts_for(fr, win)}
    # opponent adjustment: C (raw offence x defence) vs B, D vs C, ridge vs raw
    adj = {}
    for sp in ("validation", "holdout"):
        m = fr.extra["masks"][sp]
        y = tb.loc[m, "R"].to_numpy(float)
        cl = tb.loc[m, "game_pk"].to_numpy()
        for a, bb in (("C", "B"), ("D", "C"), ("D_ridge", "D")):
            adj[f"{a}_vs_{bb}|{sp}"] = {"crps": EV.paired(fr.dists[(a, sp)].crps(y), fr.dists[(bb, sp)].crps(y), cl),
                                       "mae": EV.paired(np.abs(fr.dists[(a, sp)].mean - y), np.abs(fr.dists[(bb, sp)].mean - y), cl)}
    out["opponent_adjustment"] = adj
    # NB vs empirical vs Poisson for the winner
    out["dist_compare"] = fr.dist_choice.get(win)
    # incumbent: archived production projections created before first pitch (2026)
    inc = INC.load_team_incumbent()
    g = D.load_games()[["game_pk", "start_utc"]]
    inc = inc.merge(g, on="game_pk")
    inc = inc[inc["inc_created"] < inc["start_utc"]].drop_duplicates(["game_pk", "side"], keep="last")
    pos, d = rows_of(fr, tb, win, "holdout")
    sub = tb.iloc[pos][["game_pk", "side", "R"]].reset_index(drop=True)
    sub["i"] = np.arange(len(sub))
    j = sub.merge(inc, on=["game_pk", "side"])
    if len(j):
        y = j["R"].to_numpy(float)
        ii = j["i"].to_numpy()
        dm = dists.DistBatch(d.lo, d.P[ii])
        di = dists.negbin(j["inc_mean"].to_numpy(), 0.281513, d.P.shape[1])
        cl = j["game_pk"].to_numpy()
        out["incumbent"] = {"n_obs": int(len(j)), "n_clusters": int(pd.Series(cl).nunique()),
                            "model_crps": _r(dm.crps(y).mean()), "inc_crps_nb0.2815": _r(di.crps(y).mean()),
                            "model_mae": _r(np.abs(dm.mean - y).mean()), "inc_mae": _r(np.abs(di.mean - y).mean()),
                            "model_bias": _r((dm.mean - y).mean()), "inc_bias": _r((j["inc_mean"] - y).mean()),
                            "crps_diff": EV.paired(dm.crps(y), di.crps(y), cl), "mae_diff": EV.paired(np.abs(dm.mean - y), np.abs(di.mean - y), cl)}
    # game total
    gt = {}
    for sp in ("validation", "holdout"):
        g2 = T.game_total(tb[fr.extra["masks"][sp]], win, fr.dist_params[win])
        P = np.vstack(g2["dist"].to_numpy())
        dt = dists.DistBatch(0, P)
        y = g2["y"].to_numpy(float)
        sc = EV.score(y, dt, g2["game_pk"], "game_total")
        # direct NB on the total (alpha fitted on dev totals) as the comparator
        gd = T.game_total(tb[fr.extra["masks"]["development"]], win, fr.dist_params[win])
        a = dists.fit_nb_alpha(gd["y"], gd["mu"])
        dn = dists.negbin(g2["mu"].to_numpy(), a, P.shape[1])
        gt[sp] = {"convolution": {k: _r(v) if isinstance(v, float) else v for k, v in sc.items() if k not in ("pit_deciles",)},
                  "direct_nb_alpha": _r(a), "direct_nb_crps": _r(dn.crps(y).mean()),
                  "conv_minus_direct": EV.paired(dt.crps(y), dn.crps(y), g2["game_pk"])}
    out["game_total"] = gt
    # coherence with hitter projections: sum of starters' projected runs vs team runs projection (holdout)
    hp = os.path.join(D.CACHE, "hit_final.pkl")
    if os.path.exists(hp):
        h = pickle.load(open(hp, "rb"))
        hb = h["frame"].iloc[h["hold_R_pos"]][["game_pk", "side", "R"]].copy()
        hb["R_mu"] = h["hold_R_mean"]
        hs = hb.groupby(["game_pk", "side"]).agg(starters_R_mu=("R_mu", "sum"), starters_R=("R", "sum")).reset_index()
        tm = tb.iloc[pos][["game_pk", "side", "R"]].copy()
        tm["team_mu"] = d.mean
        c = tm.merge(hs, on=["game_pk", "side"])
        share = float(c["starters_R"].sum() / c["R"].sum())
        recon = [G.team_reconciliation(r.starters_R_mu, r.team_mu * share, "runs")["status"] for r in c.itertuples()]
        out["coherence"] = {"team_games": int(len(c)), "actual_starter_share_of_runs": _r(share),
                            "mean_sum_starters_R_mu": _r(c["starters_R_mu"].mean()), "mean_team_mu": _r(c["team_mu"].mean()),
                            "ratio_mean": _r((c["starters_R_mu"] / (c["team_mu"] * share)).mean()),
                            "corr_sum_vs_team": _r(np.corrcoef(c["starters_R_mu"], c["team_mu"])[0, 1]),
                            "reconciliation_status": pd.Series(recon).value_counts().to_dict(),
                            "mae_team_model": _r((c["team_mu"] - c["R"]).abs().mean()),
                            "mae_sum_of_hitters_scaled": _r((c["starters_R_mu"] / share - c["R"]).abs().mean())}
    out["calibration"] = calib_rows(fr, tb, "R", win, "holdout", "team_runs")
    tb["early_season"] = (tb["R_std_n"].fillna(0) < 10).astype(int)
    m = fr.extra["masks"]["validation"]
    tb["_p"] = np.nan
    tb.loc[m, "_p"] = fr.dists[(win, "validation")].mean
    out["segments"] = segment_errors(tb, m, "R", "_p", ["early_season", "is_home", "sp_opener_flag", "month"])
    # gate
    pos_h, d_h = rows_of(fr, tb, win, "holdout")
    sel, res_g = run_gate(tb, pos_h, d_h, "team_runs", "team_id", None, "R", "R", None, "R_n", opp_factor_col="opp_def_r_fac", team_col="team")
    summ, by = gate_summary(tb, pos_h, d_h, "R", sel, res_g)
    out["gate"] = {"summary": summ, "error_by_status": by}
    inch = None
    if out.get("incumbent"):
        ic = out["incumbent"]
        inch = {"mean_diff": _r(ic["crps_diff"]["mean_diff"], 5), "ci95": [_r(x, 5) for x in ic["crps_diff"]["ci95"]],
                "metric": f"CRPS vs archived production projection (NB alpha 0.2815), matched n={ic['n_obs']}",
                "mae_diff": _r(ic["mae_diff"]["mean_diff"], 4), "mae_ci95": [_r(x, 4) for x in ic["mae_diff"]["ci95"]]}
    out["summary_family"] = {"rows": family_rows(fr, out["verdicts"]), "holdout": holdout_block(fr, win, tb, "R", inch)}
    write_records("team_runs", fr, win, tb, "R", "2022-2024 (history 2022; fitting 2023-2024)",
                  {"validation": "2025 RS", "holdout": "2026 RS"}, ["see mlb/teams.py"], {"D": res.get("D_betas", {})},
                  ["data/research_cache/batting_backtest", "data/research/hitter_w2/mlb_feed"], [])
    out["seconds"] = time.time() - t0
    _save_stage("teams", out)
    _update_frozen("teams", {"winner": win, "dist_params": {win: fr.dist_params[win]}, "starter_run_share": (out.get("coherence") or {}).get("actual_starter_share_of_runs")})
    with open(os.path.join(D.CACHE, "team_final.pkl"), "wb") as f:
        pickle.dump({"winner": win, "dist_params": fr.dist_params, "params": out["params"]}, f)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all")
    a = ap.parse_args(argv)
    stages = ["inventory", "pitchers", "hitters", "teams", "assemble"] if a.stage == "all" else a.stage.split(",")
    for s in stages:
        t = time.time()
        if s == "assemble":
            from mlb import assemble
            assemble.main()
        else:
            globals()[f"stage_{s}"]()
        print(f"stage {s}: {time.time() - t:.0f}s", flush=True)


if __name__ == "__main__":
    main()
