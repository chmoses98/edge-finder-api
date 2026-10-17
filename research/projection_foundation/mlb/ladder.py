"""Generic A..F ladder evaluation: distribution fitting on development, scoring on validation
and holdout, game-clustered paired comparisons vs baseline (A) and vs incumbent."""
from __future__ import annotations

import numpy as np
import pandas as pd

from pf_core import dists, models
from . import evaluate as EV

SPLITS = ("development", "validation", "holdout")


def fit_stack(y_dev, P_dev: np.ndarray) -> np.ndarray:
    return models.fit_stack_weights(y_dev, P_dev)


class FamilyResult:
    def __init__(self, family):
        self.family = family
        self.rows = []          # summary-schema model rows
        self.dist_params = {}   # model -> params
        self.dist_choice = {}   # model -> {kind: dev crps}
        self.dists = {}         # (model, split) -> DistBatch
        self.extra = {}


def build_dist(kind_params: dict, mu: np.ndarray, mix_ctx: dict | None, mask: np.ndarray) -> dists.DistBatch:
    k = kind_params["kind"]
    if k == "tb_mix":
        return mix_ctx["tb_fn"](mask, mu)
    if k == "binom_mix":
        nd = mix_ctx["n_dist_fn"](mask)
        n_mean = nd.mean
        p = np.clip(mu / np.maximum(n_mean, 1e-6), 1e-6, 0.999)
        return EV.binomial_mixture(nd, p, kind_params.get("phi"), K=kind_params["K"])
    return EV.make_family_dist(kind_params, mu)


def fit_model_dist(y, mu, kinds, K, lo, mix_ctx, dev_mask) -> tuple[dict, dict]:
    """Choose the distribution family for one model's mean by development CRPS."""
    cands = {}
    scores = {}
    for kind in kinds:
        if kind == "binom_mix":
            if mix_ctx is None:
                continue
            best = None
            for phi in mix_ctx.get("phi_grid", (0.0, 0.01, 0.03)):
                prm = {"kind": "binom_mix", "phi": phi, "K": K}
                d = build_dist(prm, mu[dev_mask], mix_ctx, dev_mask)
                c = float(np.mean(d.crps(y[dev_mask])))
                if best is None or c < best[0]:
                    best = (c, prm)
            scores[f"binom_mix(phi={best[1]['phi']})"] = best[0]
            cands["binom_mix"] = best
            continue
        if kind == "tb_mix":
            if mix_ctx is None or "tb_fn" not in mix_ctx:
                continue
            prm = {"kind": "tb_mix", "K": K}
        else:
            prm = EV.fit_family_dist(kind, y[dev_mask], mu[dev_mask], K=K, lo=lo)
        d = build_dist(prm, mu[dev_mask], mix_ctx, dev_mask)
        c = float(np.mean(d.crps(y[dev_mask])))
        scores[kind] = c
        cands[kind] = (c, prm)
    best_kind = min(cands, key=lambda k: cands[k][0])
    return cands[best_kind][1], scores


def run_family(df: pd.DataFrame, family: str, y_col: str, preds: dict, split_col: str, cluster_col: str,
               kinds=("poisson", "nb", "nb_binned", "empirical_resid"), K: int = 30, lo: int = 0,
               mix_ctx: dict | None = None, baseline: str = "A", fixed_kinds: dict | None = None,
               fit_mask: np.ndarray | None = None) -> FamilyResult:
    """preds: model name -> np.ndarray of means aligned with df (NaN allowed where a model has no
    prediction; rows scored for a split are those where every model has a prediction)."""
    fr = FamilyResult(family)
    y = df[y_col].to_numpy(float)
    split = df[split_col].to_numpy()
    cl = df[cluster_col].to_numpy()
    allp = np.column_stack([np.asarray(v, float) for v in preds.values()])
    ok = np.isfinite(allp).all(axis=1) & np.isfinite(y)
    dev = (split == "development") & ok
    if fit_mask is not None:
        dev &= fit_mask
    for m, mu in preds.items():
        mu = np.clip(np.asarray(mu, float), 1e-3, None)
        kk = (fixed_kinds or {}).get(m, kinds)
        prm, sc = fit_model_dist(y, np.nan_to_num(mu, nan=1.0), kk, K, lo, mix_ctx, dev)
        fr.dist_params[m] = prm
        fr.dist_choice[m] = sc
    losses = {}
    for sp in SPLITS:
        mask = (split == sp) & ok
        if mask.sum() == 0:
            continue
        for m, mu in preds.items():
            mu_s = np.clip(np.asarray(mu, float)[mask], 1e-3, None)
            d = build_dist(fr.dist_params[m], mu_s, mix_ctx, mask)
            fr.dists[(m, sp)] = d
            sc = EV.score(y[mask], d, cl[mask], family)
            sc.update({"model": m, "split": sp, "dist": fr.dist_params[m]["kind"]})
            losses[(m, sp)] = (d.crps(y[mask]), np.abs(d.mean - y[mask]))
            fr.rows.append(sc)
        if baseline in preds:
            for r in fr.rows:
                if r["split"] != sp:
                    continue
                if r["model"] == baseline:
                    r["vs_baseline"] = None
                    continue
                a = losses[(r["model"], sp)][0]
                b = losses[(baseline, sp)][0]
                r["vs_baseline"] = EV.paired(a, b, cl[mask])
                r["vs_baseline_mae"] = EV.paired(losses[(r["model"], sp)][1], losses[(baseline, sp)][1], cl[mask])
    fr.extra["masks"] = {sp: ((split == sp) & ok) for sp in SPLITS}
    return fr


def walkforward_lgbm(df, features, target, block_col, first_pred_block, objective="poisson", refit_every=1,
                     params=None, train_mask=None):
    """pf_core.models.lgbm_walkforward, but predictions start at first_pred_block (all earlier blocks
    are training only). Returns a Series aligned to df.index."""
    import lightgbm as lgb
    p = dict(objective=objective, learning_rate=0.05, n_estimators=250, num_leaves=15,
             min_child_samples=200, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
             reg_lambda=5.0, verbose=-1, n_jobs=2)
    if params:
        p.update(params)
    blocks = sorted(df[block_col].unique())
    pred = pd.Series(np.nan, index=df.index)
    model = None
    i_fit = 0
    tm = np.ones(len(df), bool) if train_mask is None else np.asarray(train_mask, bool)
    for b in blocks:
        if b < first_pred_block:
            continue
        if model is None or i_fit % refit_every == 0:
            tr = (df[block_col] < b).to_numpy() & tm & df[target].notna().to_numpy()
            model = lgb.LGBMRegressor(**p)
            model.fit(df.loc[tr, features], df.loc[tr, target])
        i_fit += 1
        te = (df[block_col] == b).to_numpy()
        pred.loc[te] = model.predict(df.loc[te, features])
    return pred, model


def fit_glm_offset(y, base_mu, X: np.ndarray, names: list[str], l2: float = 1.0) -> dict:
    """Poisson GLM log(mu) = log(base) + X @ beta (no intercept => preserves C's level unless an
    'icpt' column is passed). Small ridge for stability. Fitted on development only."""
    from scipy.optimize import minimize
    y = np.asarray(y, float)
    lb = np.log(np.maximum(np.asarray(base_mu, float), 1e-6))
    X = np.nan_to_num(np.asarray(X, float))

    def f(b):
        eta = lb + X @ b
        mu = np.exp(eta)
        return float(np.sum(mu - y * eta) + l2 * np.sum(b ** 2)), X.T @ (mu - y) + 2 * l2 * b

    r = minimize(f, np.zeros(X.shape[1]), jac=True, method="L-BFGS-B")
    return dict(zip(names, map(float, r.x)))


def apply_glm_offset(base_mu, X, beta: dict, names):
    b = np.array([beta[n] for n in names])
    return np.asarray(base_mu, float) * np.exp(np.nan_to_num(np.asarray(X, float)) @ b)


def summary_model_rows(fr: FamilyResult, incumbent_cmp: dict | None = None, verdicts: dict | None = None) -> list[dict]:
    out = []
    for r in fr.rows:
        if r["split"] == "development":
            continue
        row = {k: r[k] for k in ("model", "split", "n_obs", "n_clusters", "mae", "rmse", "bias", "crps",
                                  "log_score", "cover_50", "cover_80", "cover_90", "pit_var_ratio")}
        row["dist"] = r["dist"]
        row["mean_threshold_brier"] = r.get("mean_threshold_brier")
        vb = r.get("vs_baseline")
        row["vs_baseline"] = None if vb is None else {"mean_diff": vb["mean_diff"], "ci95": vb["ci95"], "metric": "crps"}
        row["vs_incumbent"] = (incumbent_cmp or {}).get((r["model"], r["split"]))
        row["verdict"] = (verdicts or {}).get(r["model"], "")
        out.append(row)
    return out
