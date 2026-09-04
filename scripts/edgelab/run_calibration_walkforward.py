#!/usr/bin/env python3
"""
scripts/edgelab/run_calibration_walkforward.py
===============================================
Walk-forward (rolling-origin, by slate date) comparison of probability
calibration candidates on the point-in-time research dataset. RESEARCH
ONLY -- nothing here touches production.

For every test date D in chronological order, every parametric candidate
is fit ONLY on rows from dates < D (expanding window, with a minimum
history), then scored on date D. Out-of-sample predictions are pooled
across all test dates and compared with game-clustered bootstrap CIs.
Structural candidates (frozen NB dispersion) and market baselines have
no fitted parameters and are scored on the same rows.

Also reports a strict frozen pseudo-holdout: fit on dates <= 2026-08-24
(the MLB-RSCH-0024 training end), score 2026-08-25 .. 2026-08-31.

Writes data/edgelab/research_artifacts/calibration_research/walkforward.{json,md}
"""
import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.research import calibration_analysis as ca  # noqa: E402
from lib.edgelab.research import calibration_candidates as cc  # noqa: E402

OUT_JSON = os.path.join(ca.DATASET_DIR, "walkforward.json")
OUT_MD = os.path.join(ca.DATASET_DIR, "walkforward.md")
SETTLED_MAX_DATE = "2026-08-31"
MIN_TRAIN_DATES = 6
MIN_FAMILY_ROWS = 150          # family-specific parameters need at least this many training rows
HIER_L2 = 25.0                 # ridge strength pulling family Platt params toward the global fit
HOLDOUT_TRAIN_END = "2026-08-24"
RUN_FAMILIES = ("game_result", "inning_result", "game_total", "inning_total", "team_total", "winning_margin", "first_inning_run")


# ------------------------------------------------------------ candidates

def fit_global_platt(tr):
    a, b = ca.fit_platt_params(ca.logit(tr["baseP"].values), tr["outcome"].values)
    return {"a": a, "b": b}


def fit_family_platt(tr, global_params, l2=HIER_L2):
    """Per-family logit-affine map, ridge-shrunk toward the global map (partial pooling)."""
    out = {}
    for fam, g in tr.groupby("family"):
        if len(g) < MIN_FAMILY_ROWS:
            out[fam] = dict(global_params)
            continue
        x = ca.logit(g["baseP"].values)
        # re-centre so the penalty pulls toward the GLOBAL (a, b), not toward identity
        # (fit_platt_params penalises deviation from a=0,b=1): fit on x' = a_g + b_g*x.
        xg = global_params["a"] + global_params["b"] * x
        a, b = ca.fit_platt_params(xg, g["outcome"].values, l2=l2)
        out[fam] = {"a": a + b * global_params["a"], "b": b * global_params["b"]}
    return out


def apply_family_platt(te, params, fallback):
    p = np.empty(len(te))
    for i, (fam, q) in enumerate(zip(te["family"].values, te["baseP"].values)):
        pr = params.get(fam, fallback)
        p[i] = ca.apply_platt(q, pr["a"], pr["b"])
    return p


def fit_family_beta(tr):
    out = {}
    for fam, g in tr.groupby("family"):
        if len(g) < MIN_FAMILY_ROWS:
            continue
        out[fam] = ca.fit_beta_calibration(g["baseP"].values, g["outcome"].values)
    return out


def apply_family_beta(te, params, global_platt):
    p = np.empty(len(te))
    for i, (fam, q) in enumerate(zip(te["family"].values, te["baseP"].values)):
        if fam in params:
            p[i] = ca.apply_beta_calibration(q, *params[fam])
        else:
            p[i] = ca.apply_platt(q, global_platt["a"], global_platt["b"])
    return p


def fit_family_isotonic(tr):
    out = {}
    for fam, g in tr.groupby("family"):
        if len(g) < MIN_FAMILY_ROWS:
            continue
        out[fam] = ca.fit_isotonic(g["baseP"].values, g["outcome"].values)
    return out


def apply_family_isotonic(te, models, global_platt):
    p = np.empty(len(te))
    for i, (fam, q) in enumerate(zip(te["family"].values, te["baseP"].values)):
        if fam in models:
            p[i] = float(models[fam].predict([min(max(q, ca.EPS), 1 - ca.EPS)])[0])
        else:
            p[i] = ca.apply_platt(q, global_platt["a"], global_platt["b"])
    return p


def fit_global_blend(tr, base_col="baseP"):
    c, wm, wk = ca.fit_market_blend(tr[base_col].values, tr["marketP"].values, tr["outcome"].values, l2=1.0)
    return {"c": c, "wm": wm, "wk": wk}


def fit_family_blend(tr, base_col="baseP"):
    out = {}
    for fam, g in tr.groupby("family"):
        if len(g) < MIN_FAMILY_ROWS:
            continue
        c, wm, wk = ca.fit_market_blend(g[base_col].values, g["marketP"].values, g["outcome"].values, l2=1.0)
        out[fam] = {"c": c, "wm": wm, "wk": wk}
    return out


def apply_blend(te, params, fallback, base_col="baseP"):
    p = np.empty(len(te))
    for i, (fam, q, k) in enumerate(zip(te["family"].values, te[base_col].values, te["marketP"].values)):
        pr = params.get(fam, fallback) if isinstance(params, dict) and "c" not in params else params
        p[i] = ca.apply_market_blend(q, k, pr["c"], pr["wm"], pr["wk"])
    return p


def fit_market_shrink(tr):
    """Kalshi-only baseline: one logit shrink of the market mid toward its base rate (MLB-RSCH-0026 form)."""
    base = float(tr["outcome"].mean())
    x = ca.logit(tr["marketP"].values) - ca.logit(np.full(len(tr), base))
    a, b = ca.fit_platt_params(x, tr["outcome"].values)
    return {"base": base, "a": a, "b": b}


def apply_market_shrink(te, pr):
    x = ca.logit(te["marketP"].values) - ca.logit(np.full(len(te), pr["base"]))
    return ca.sigmoid(pr["a"] + pr["b"] * x)


def fit_climatology(tr):
    """Walk-forward base rates keyed (family, period, line, side) with back-off to (family, period, line), (family, period), (family)."""
    keys = {}
    def _key(r, level):
        if level == 0:
            return (r["family"], r["period"], r["line"], r["contractSide"])
        if level == 1:
            return (r["family"], r["period"], r["line"])
        if level == 2:
            return (r["family"], r["period"])
        return (r["family"],)
    for level in range(4):
        g = tr.groupby(tr.apply(lambda r: _key(r, level), axis=1))["outcome"].agg(["sum", "count"])
        keys[level] = {k: (float(v["sum"]), float(v["count"])) for k, v in g.iterrows()}
    return keys


def apply_climatology(te, keys, min_n=30, prior_n=10):
    p = np.empty(len(te))
    for i, r in enumerate(te.to_dict("records")):
        est = None
        for level in range(4):
            k = (r["family"], r["period"], r["line"], r["contractSide"]) if level == 0 else \
                (r["family"], r["period"], r["line"]) if level == 1 else \
                (r["family"], r["period"]) if level == 2 else (r["family"],)
            s_c = keys[level].get(k)
            if s_c and s_c[1] >= min_n:
                # shrink toward the next coarser level for stability
                coarse = keys[min(level + 1, 3)].get(k[:len(k) - 1] if level < 3 else k)
                prior = (coarse[0] / coarse[1]) if coarse and coarse[1] > 0 else 0.5
                est = (s_c[0] + prior_n * prior) / (s_c[1] + prior_n)
                break
        p[i] = 0.5 if est is None else est
    return np.clip(p, ca.EPS, 1 - ca.EPS)


CANDIDATE_SPECS = [
    ("B0_climatology", "modelP", "climatology"),
    # name, base column (what the map is applied to), fit fn, apply fn
    ("C1_global_platt", "modelP", "global_platt"),
    ("C2_family_platt_hier", "modelP", "family_platt"),
    ("C3_family_beta", "modelP", "family_beta"),
    ("C4_family_isotonic", "modelP", "family_iso"),
    ("C5_global_model_market_blend", "modelP", "global_blend"),
    ("C6_family_model_market_blend", "modelP", "family_blend"),
    ("C7_nb_structural", "nbP", "none"),
    ("C8_nb_plus_family_platt", "nbP", "family_platt"),
    ("C9_nb_plus_family_blend", "nbP", "family_blend"),
    ("C10_nb_mean_shift", "nbP", "nb_shift"),
    ("C11_nb_mean_shift_family_platt", "nbP", "nb_shift_family_platt"),
    ("M1_market_shrink", "marketP", "market_shrink"),
]


def select_mean_shift(tr):
    """Pick the NB mean shift (runs/team) minimising training log loss on run-based rows."""
    best, best_ll = 0.0, None
    for s in cc.MEAN_SHIFT_GRID:
        col = f"nbP_shift{s}"
        m = tr[col].notna() & tr["family"].isin(RUN_FAMILIES)
        if m.sum() == 0:
            continue
        ll = ca.log_loss(tr.loc[m, col].values, tr.loc[m, "outcome"].values)
        if best_ll is None or ll < best_ll:
            best, best_ll = s, ll
    return best


def _shifted_base(frame, s):
    col = f"nbP_shift{s}"
    return frame[col].where(frame[col].notna(), frame["modelP"]).values


def fit_and_apply(spec, tr, te):
    name, base_col, kind = spec
    tr = tr[tr[base_col].notna()].copy()
    te = te[te[base_col].notna()].copy()
    if len(te) == 0:
        return te.index, np.array([])
    tr["baseP"] = tr[base_col]
    te["baseP"] = te[base_col]
    if kind == "none":
        return te.index, te["baseP"].values
    if kind == "climatology":
        return te.index, apply_climatology(te, fit_climatology(tr))
    gp = fit_global_platt(tr)
    if kind == "global_platt":
        return te.index, ca.apply_platt(te["baseP"].values, gp["a"], gp["b"])
    if kind == "family_platt":
        fp = fit_family_platt(tr, gp)
        return te.index, apply_family_platt(te, fp, gp)
    if kind == "family_beta":
        return te.index, apply_family_beta(te, fit_family_beta(tr), gp)
    if kind == "family_iso":
        return te.index, apply_family_isotonic(te, fit_family_isotonic(tr), gp)
    if kind == "global_blend":
        return te.index, apply_blend(te, fit_global_blend(tr), None)
    if kind == "family_blend":
        gb = fit_global_blend(tr)
        return te.index, apply_blend(te, fit_family_blend(tr), gb)
    if kind == "market_shrink":
        return te.index, apply_market_shrink(te, fit_market_shrink(tr))
    if kind in ("nb_shift", "nb_shift_family_platt"):
        sft = select_mean_shift(tr)
        tr["baseP"] = _shifted_base(tr, sft)
        te["baseP"] = _shifted_base(te, sft)
        if kind == "nb_shift":
            return te.index, te["baseP"].values
        gp = fit_global_platt(tr)
        fp = fit_family_platt(tr, gp)
        return te.index, apply_family_platt(te, fp, gp)
    raise ValueError(kind)


# ---------------------------------------------------------------- scoring

def score_block(d, cols, label):
    """Paired Brier / log-loss vs raw model and vs market for each candidate column, with game-clustered CIs."""
    rows = []
    y = d["outcome"].values
    for c in cols:
        m = d[c].notna()
        g = d[m]
        yy = g["outcome"].values
        dm = ca.paired_delta_ci(g, c, "modelP")
        dk = ca.paired_delta_ci(g, c, "marketP")
        a, b = ca.calibration_slope_intercept(g[c].values, yy)
        rows.append({"candidate": c, "n": int(m.sum()), "games": int(g["gameId"].nunique()),
                     "brier": ca.brier(g[c].values, yy), "logLoss": ca.log_loss(g[c].values, yy), "ece": ca.ece(g[c].values, yy),
                     "brierModelSameRows": ca.brier(g["modelP"].values, yy), "brierMarketSameRows": ca.brier(g["marketP"].values, yy),
                     "dVsModel": dm[0], "dVsModelLo": dm[1], "dVsModelHi": dm[2],
                     "dVsMarket": dk[0], "dVsMarketLo": dk[1], "dVsMarketHi": dk[2],
                     "intercept": a, "slope": b})
    return {"label": label, "rows": rows}


def per_family_block(d, cols):
    out = {}
    for fam, g in d.groupby("family"):
        rows = []
        yy = g["outcome"].values
        for c in cols:
            m = g[c].notna()
            gg = g[m]
            if len(gg) < 50:
                continue
            y2 = gg["outcome"].values
            dm = ca.paired_delta_ci(gg, c, "modelP")
            dk = ca.paired_delta_ci(gg, c, "marketP")
            rows.append({"candidate": c, "n": len(gg), "games": int(gg["gameId"].nunique()), "brier": ca.brier(gg[c].values, y2),
                         "brierModel": ca.brier(gg["modelP"].values, y2), "brierMarket": ca.brier(gg["marketP"].values, y2),
                         "dVsModel": dm[0], "dVsModelLo": dm[1], "dVsModelHi": dm[2], "dVsMarket": dk[0], "dVsMarketLo": dk[1], "dVsMarketHi": dk[2],
                         "ece": ca.ece(gg[c].values, y2)})
        out[fam] = rows
    return out


def per_date_wins(d, cols):
    """Fraction of test dates on which each candidate beats raw model / market on Brier."""
    out = {}
    for c in cols:
        wins_m = wins_k = tot = 0
        for _, g in d.groupby("date"):
            gg = g[g[c].notna()]
            if len(gg) < 20:
                continue
            yy = gg["outcome"].values
            tot += 1
            wins_m += ca.brier(gg[c].values, yy) < ca.brier(gg["modelP"].values, yy)
            wins_k += ca.brier(gg[c].values, yy) < ca.brier(gg["marketP"].values, yy)
        out[c] = {"dates": tot, "beatsModel": wins_m, "beatsMarket": wins_k}
    return out


def run_walkforward(d, specs, min_train_dates=MIN_TRAIN_DATES):
    dates = sorted(d["date"].unique())
    preds = {s[0]: pd.Series(np.nan, index=d.index) for s in specs}
    tested = []
    for i, dt in enumerate(dates):
        if i < min_train_dates:
            continue
        tr = d[d["date"] < dt]
        te = d[d["date"] == dt]
        if len(tr) < 500 or len(te) == 0:
            continue
        tested.append(dt)
        for spec in specs:
            idx, p = fit_and_apply(spec, tr, te)
            preds[spec[0]].loc[idx] = p
    out = d.copy()
    for k, v in preds.items():
        out[k] = v
    out = out[out["date"].isin(tested)]
    return out, tested


def run_frozen_holdout(d, specs, train_end=HOLDOUT_TRAIN_END):
    tr = d[d["date"] <= train_end]
    te = d[d["date"] > train_end].copy()
    for spec in specs:
        idx, p = fit_and_apply(spec, tr, te)
        te[spec[0]] = np.nan
        te.loc[idx, spec[0]] = p
    return te, {s[0]: _describe_fit(s, tr) for s in specs}


def _describe_fit(spec, tr):
    name, base_col, kind = spec
    t = tr[tr[base_col].notna()].copy()
    t["baseP"] = t[base_col]
    if kind in ("none", "climatology"):
        return {"parameters": "frozen (no fit)" if kind == "none" else "walk-forward base rates"}
    gp = fit_global_platt(t)
    if kind == "global_platt":
        return gp
    if kind == "family_platt":
        return {"global": gp, "family": fit_family_platt(t, gp)}
    if kind == "family_beta":
        return {"family": {k: list(v) for k, v in fit_family_beta(t).items()}}
    if kind == "global_blend":
        return fit_global_blend(t)
    if kind == "family_blend":
        return {"global": fit_global_blend(t), "family": fit_family_blend(t)}
    if kind == "market_shrink":
        return fit_market_shrink(t)
    if kind in ("nb_shift", "nb_shift_family_platt"):
        sft = select_mean_shift(t)
        t["baseP"] = _shifted_base(t, sft)
        gp = fit_global_platt(t)
        return {"meanShift": sft, "global": gp, "family": fit_family_platt(t, gp) if kind == "nb_shift_family_platt" else None}
    return {}


def main(engine="B"):
    global OUT_JSON, OUT_MD, CANDIDATE_SPECS
    df = ca.load_rows()
    games = pd.read_json(ca.GAMES_PATH, lines=True)
    gidx = {(r.captureId, int(r.gameId)): r._asdict() for r in games.itertuples(index=False)}
    d = ca.primary_rows(df, engine=engine, max_date=SETTLED_MAX_DATE)
    suffix = "" if engine == "B" else f"_engine{engine}"
    OUT_JSON = os.path.join(ca.DATASET_DIR, f"walkforward{suffix}.json")
    OUT_MD = os.path.join(ca.DATASET_DIR, f"walkforward{suffix}.md")
    if engine == "B":
        d["nbP"] = [cc.nb_probability(r, gidx.get((r["captureId"], int(r["gameId"])))) for r in d.to_dict("records")]
        # NB candidates fall back to the raw model where the family has no run-distribution analogue (pitcher props)
        d["nbP"] = d["nbP"].where(d["nbP"].notna(), d["modelP"])
        for s_ in cc.MEAN_SHIFT_GRID:
            d[f"nbP_shift{s_}"] = [cc.nb_probability(r, gidx.get((r["captureId"], int(r["gameId"]))), mean_shift=s_) for r in d.to_dict("records")]
    else:
        # Engine A rows are production's archived 11-market ledger probabilities; no NB re-pricing here
        d["nbP"] = d["modelP"]
        d["period"] = d.get("period", "")
        d["contractSide"] = d.get("contractSide", None)
        CANDIDATE_SPECS = [s for s in CANDIDATE_SPECS if not s[0].startswith(("C7", "C8", "C9", "C10", "C11"))]
    out = {"settledMaxDate": SETTLED_MAX_DATE, "minTrainDates": MIN_TRAIN_DATES, "minFamilyRows": MIN_FAMILY_ROWS, "hierL2": HIER_L2,
           "nRows": len(d), "nGames": int(d["gameId"].nunique()), "dates": sorted(d["date"].unique().tolist())}
    md = ["# Walk-forward calibration comparison (Engine B primary rows)\n",
          f"Rows={len(d)} games={d['gameId'].nunique()} dates={d['date'].nunique()}; expanding-window fits on dates < D, scored on D; "
          "CIs = 95% game-clustered bootstrap. dVsModel / dVsMarket = paired Brier delta vs raw production model / vs simultaneous Kalshi mid (negative = candidate better).\n"]

    cols = [s[0] for s in CANDIDATE_SPECS]
    wf, tested = run_walkforward(d, CANDIDATE_SPECS)
    out["walkforward"] = {"testDates": tested, "nRows": len(wf), "nGames": int(wf["gameId"].nunique())}
    blk = score_block(wf, ["modelP", "marketP", "nbP"] + cols, "walkforward_pooled")
    out["walkforward"]["pooled"] = blk
    md.append(f"\n## Pooled out-of-sample ({len(tested)} test dates, {len(wf)} rows, {wf['gameId'].nunique()} games)\n")
    md.append(ca.md_table(blk["rows"], ["candidate", "n", "games", "brier", "logLoss", "ece", "dVsModel", "dVsModelLo", "dVsModelHi", "dVsMarket", "dVsMarketLo", "dVsMarketHi", "intercept", "slope"]))
    pdw = per_date_wins(wf, cols)
    out["walkforward"]["perDateWins"] = pdw
    md.append("\n### Per-date win counts (Brier)\n")
    md.append(ca.md_table([{"candidate": k, **v} for k, v in pdw.items()], ["candidate", "dates", "beatsModel", "beatsMarket"]))
    fam = per_family_block(wf, ["nbP"] + cols)
    out["walkforward"]["perFamily"] = fam
    for f, rows in fam.items():
        md.append(f"\n### Walk-forward by family: {f}\n")
        md.append(ca.md_table(rows, ["candidate", "n", "games", "brier", "brierModel", "brierMarket", "dVsModel", "dVsModelLo", "dVsModelHi", "dVsMarket", "dVsMarketLo", "dVsMarketHi", "ece"]))

    ho, fits = run_frozen_holdout(d, CANDIDATE_SPECS)
    out["frozenHoldout"] = {"trainEnd": HOLDOUT_TRAIN_END, "nRows": len(ho), "nGames": int(ho["gameId"].nunique()), "fits": fits}
    blk = score_block(ho, ["modelP", "marketP", "nbP"] + cols, "frozen_holdout")
    out["frozenHoldout"]["pooled"] = blk
    md.append(f"\n## Frozen pseudo-holdout: fit on dates <= {HOLDOUT_TRAIN_END}, score {len(ho)} rows / {ho['gameId'].nunique()} games after\n")
    md.append(ca.md_table(blk["rows"], ["candidate", "n", "games", "brier", "logLoss", "ece", "dVsModel", "dVsModelLo", "dVsModelHi", "dVsMarket", "dVsMarketLo", "dVsMarketHi", "intercept", "slope"]))
    fam = per_family_block(ho, ["nbP"] + cols)
    out["frozenHoldout"]["perFamily"] = fam
    for f, rows in fam.items():
        md.append(f"\n### Frozen holdout by family: {f}\n")
        md.append(ca.md_table(rows, ["candidate", "n", "games", "brier", "brierModel", "brierMarket", "dVsModel", "dVsModelLo", "dVsModelHi", "dVsMarket", "dVsMarketLo", "dVsMarketHi", "ece"]))
    ho2, fits2 = run_frozen_holdout(d, CANDIDATE_SPECS, train_end="2026-08-28")
    out["frozenHoldout2"] = {"trainEnd": "2026-08-28", "nRows": len(ho2), "nGames": int(ho2["gameId"].nunique()), "fits": fits2}
    blk = score_block(ho2, ["modelP", "marketP", "nbP"] + cols, "frozen_holdout_0828")
    out["frozenHoldout2"]["pooled"] = blk
    md.append(f"\n## Frozen pseudo-holdout #2 (the 10-day review window): fit on dates <= 2026-08-28, score {len(ho2)} rows / {ho2['gameId'].nunique()} games (2026-08-29..31)\n")
    md.append(ca.md_table(blk["rows"], ["candidate", "n", "games", "brier", "logLoss", "ece", "dVsModel", "dVsModelLo", "dVsModelHi", "dVsMarket", "dVsMarketLo", "dVsMarketHi", "intercept", "slope"]))
    out["frozenHoldout2"]["perFamily"] = per_family_block(ho2, ["nbP"] + cols)
    md.append("\n### Frozen-holdout fitted parameters (fit on training window only)\n")
    md.append("```\n" + __import__("json").dumps(fits, indent=1, default=str)[:6000] + "\n```")

    # persist the out-of-sample predictions for downstream economics
    keep = ["date", "captureId", "gameId", "ticker", "side", "family", "period", "contractSide", "line", "modelP", "marketP", "askP", "bidP", "closeP", "nbP", "outcome", "minutesToStart"] + cols
    keep = [k for k in keep if k in wf.columns]
    wf[keep].to_json(os.path.join(ca.DATASET_DIR, f"walkforward_predictions{suffix}.jsonl.gz"), orient="records", lines=True)
    ho[keep].to_json(os.path.join(ca.DATASET_DIR, f"frozen_holdout_predictions{suffix}.jsonl.gz"), orient="records", lines=True)
    ca.write_json(out, OUT_JSON)
    with open(OUT_MD, "w") as f:
        f.write("\n".join(md) + "\n")
    print(f"wrote {OUT_JSON}, {OUT_MD}")


if __name__ == "__main__":
    main(engine=(sys.argv[1] if len(sys.argv) > 1 else "B"))
