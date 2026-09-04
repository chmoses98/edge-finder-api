#!/usr/bin/env python3
"""
scripts/edgelab/run_calibration_characterization.py
====================================================
Descriptive characterization of production MLB probability calibration on
the point-in-time research dataset (lib/edgelab/research/calibration_dataset.py).
RESEARCH ONLY. Fits nothing that is reused; only measures.

Writes data/edgelab/research_artifacts/calibration_research/characterization.json
and characterization.md.
"""
import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.research import calibration_analysis as ca  # noqa: E402

OUT_JSON = os.path.join(ca.DATASET_DIR, "characterization.json")
OUT_MD = os.path.join(ca.DATASET_DIR, "characterization.md")
SETTLED_MAX_DATE = "2026-08-31"
N_BOOT = 400


def family_block(d, label):
    y = d["outcome"].values
    rows = []
    for fam, g in d.groupby("family"):
        yy = g["outcome"].values
        pt, lo, hi, p = ca.paired_delta_ci(g, "modelP", "marketP")
        a, b = ca.calibration_slope_intercept(g["modelP"].values, yy)
        am, bm = ca.calibration_slope_intercept(g["marketP"].values, yy)
        md = ca.murphy_decomposition(g["modelP"].values, yy)
        mm = ca.murphy_decomposition(g["marketP"].values, yy)
        rows.append({
            "family": fam, "n": len(g), "games": int(g["gameId"].nunique()), "baseRate": float(yy.mean()),
            "meanModelP": float(g["modelP"].mean()), "meanMarketP": float(g["marketP"].mean()),
            "brierModel": ca.brier(g["modelP"].values, yy), "brierMarket": ca.brier(g["marketP"].values, yy),
            "logLossModel": ca.log_loss(g["modelP"].values, yy), "logLossMarket": ca.log_loss(g["marketP"].values, yy),
            "eceModel": ca.ece(g["modelP"].values, yy), "eceMarket": ca.ece(g["marketP"].values, yy),
            "deltaBrier": pt, "deltaLo95": lo, "deltaHi95": hi, "deltaP": p,
            "modelIntercept": a, "modelSlope": b, "marketIntercept": am, "marketSlope": bm,
            "modelReliability": md["reliability"], "modelResolution": md["resolution"],
            "marketReliability": mm["reliability"], "marketResolution": mm["resolution"],
        })
    pt, lo, hi, p = ca.paired_delta_ci(d, "modelP", "marketP")
    a, b = ca.calibration_slope_intercept(d["modelP"].values, y)
    md = ca.murphy_decomposition(d["modelP"].values, y)
    mm = ca.murphy_decomposition(d["marketP"].values, y)
    rows.append({"family": "ALL", "n": len(d), "games": int(d["gameId"].nunique()), "baseRate": float(y.mean()),
                 "meanModelP": float(d["modelP"].mean()), "meanMarketP": float(d["marketP"].mean()),
                 "brierModel": ca.brier(d["modelP"].values, y), "brierMarket": ca.brier(d["marketP"].values, y),
                 "logLossModel": ca.log_loss(d["modelP"].values, y), "logLossMarket": ca.log_loss(d["marketP"].values, y),
                 "eceModel": ca.ece(d["modelP"].values, y), "eceMarket": ca.ece(d["marketP"].values, y),
                 "deltaBrier": pt, "deltaLo95": lo, "deltaHi95": hi, "deltaP": p,
                 "modelIntercept": a, "modelSlope": b,
                 "modelReliability": md["reliability"], "modelResolution": md["resolution"],
                 "marketReliability": mm["reliability"], "marketResolution": mm["resolution"]})
    return {"label": label, "rows": rows}


def split_block(d, col, label, min_n=80):
    rows = []
    for key, g in d.groupby(col, dropna=False):
        if len(g) < min_n:
            continue
        yy = g["outcome"].values
        pt, lo, hi, p = ca.paired_delta_ci(g, "modelP", "marketP")
        a, b = ca.calibration_slope_intercept(g["modelP"].values, yy)
        rows.append({"split": str(key), "n": len(g), "games": int(g["gameId"].nunique()), "baseRate": float(yy.mean()),
                     "meanModelP": float(g["modelP"].mean()), "meanMarketP": float(g["marketP"].mean()),
                     "brierModel": ca.brier(g["modelP"].values, yy), "brierMarket": ca.brier(g["marketP"].values, yy),
                     "deltaBrier": pt, "deltaLo95": lo, "deltaHi95": hi, "modelIntercept": a, "modelSlope": b,
                     "eceModel": ca.ece(g["modelP"].values, yy)})
    return {"label": label, "column": col, "rows": rows}


def add_dimensions(d):
    d = d.copy()
    d["band"] = pd.cut(d["modelP"], [0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0], include_lowest=True).astype(str)
    d["marketBand"] = pd.cut(d["marketP"], [0, .2, .4, .6, .8, 1.0], include_lowest=True).astype(str)
    d["disagreement"] = d["modelP"] - d["marketP"]
    d["absDisagreementBand"] = pd.cut(d["disagreement"].abs(), [0, .025, .05, .10, .20, 1.01], include_lowest=True).astype(str)
    d["disagreementSign"] = np.where(d["disagreement"] > 0.025, "model_above", np.where(d["disagreement"] < -0.025, "model_below", "agree"))
    d["timeBucket"] = pd.cut(d["minutesToStart"], [0, 45, 90, 180, 360, 100000], labels=["0-45m", "45-90m", "90-180m", "3-6h", ">6h"]).astype(str)
    d["favorite"] = np.where(d["marketP"] >= 0.5, "market_fav", "market_dog")
    d["lineupState"] = np.where(d.get("lineupConfirmed", pd.Series(False, index=d.index)).fillna(False).astype(bool), "confirmed", "unconfirmed")
    d["week"] = pd.to_datetime(d["date"]).dt.isocalendar().week.astype(str)
    d["period"] = d["period"].fillna("")
    d["familyPeriod"] = d["family"] + ":" + d["period"]
    if "contractSide" in d:
        d["homeAway"] = d["contractSide"].where(d["contractSide"].isin(["Away", "Home"]), "other")
    return d


def main():
    df = ca.load_rows()
    out = {"settledMaxDate": SETTLED_MAX_DATE, "blocks": {}}
    md = ["# Calibration characterization (point-in-time dataset)\n",
          f"Primary unit: last pregame capture per (ticker, side); settled outcomes through {SETTLED_MAX_DATE}; "
          "market = simultaneous Kalshi bid/ask mid; CIs = 95% game-clustered bootstrap on paired Brier delta (model - market; negative = model better).\n"]

    for engine in ("B", "A"):
        d = ca.primary_rows(df, engine=engine, max_date=SETTLED_MAX_DATE)
        d = add_dimensions(d)
        fb = family_block(d, f"engine_{engine}_by_family")
        out["blocks"][f"engine_{engine}_family"] = fb
        md.append(f"\n## Engine {engine} — by family\n")
        md.append(ca.md_table(fb["rows"], ["family", "n", "games", "baseRate", "meanModelP", "meanMarketP", "brierModel", "brierMarket", "deltaBrier", "deltaLo95", "deltaHi95", "eceModel", "eceMarket", "modelIntercept", "modelSlope", "modelReliability", "modelResolution", "marketResolution"]))
        # reliability tables
        rel = {}
        for fam, g in list(d.groupby("family")) + [("ALL", d)]:
            rel[fam] = {"model": ca.reliability_table(g["modelP"].values, g["outcome"].values),
                        "market": ca.reliability_table(g["marketP"].values, g["outcome"].values)}
        out["blocks"][f"engine_{engine}_reliability"] = rel
        md.append(f"\n### Engine {engine} — reliability (ALL families, model probability bands)\n")
        md.append(ca.md_table(rel["ALL"]["model"], ["lo", "hi", "n", "meanP", "obsRate", "bias"]))
        md.append(f"\n### Engine {engine} — reliability (ALL families, market mid bands)\n")
        md.append(ca.md_table(rel["ALL"]["market"], ["lo", "hi", "n", "meanP", "obsRate", "bias"]))
        for col, label in (("band", "model probability band"), ("marketBand", "market band"), ("absDisagreementBand", "|model-market|"),
                           ("disagreementSign", "disagreement direction"), ("timeBucket", "minutes to first pitch at capture"),
                           ("favorite", "market favorite/underdog"), ("lineupState", "lineup confirmed at capture"),
                           ("week", "ISO week"), ("familyPeriod", "family:period"), ("homeAway", "contract side")):
            if col not in d:
                continue
            sb = split_block(d, col, label)
            out["blocks"][f"engine_{engine}_split_{col}"] = sb
            md.append(f"\n### Engine {engine} — split by {label}\n")
            md.append(ca.md_table(sb["rows"], ["split", "n", "games", "baseRate", "meanModelP", "meanMarketP", "brierModel", "brierMarket", "deltaBrier", "deltaLo95", "deltaHi95", "modelSlope", "eceModel"]))
        # era split for team_total (Engine A carries archived v1.1/v1.2 probabilities)
        if engine == "A":
            tt = d[d["family"] == "team_total"].copy()
            tt["era"] = np.where(tt["era_team_total_v12"], "v1.2 (>=08-21)", "v1.1 (<08-21)")
            sb = split_block(tt, "era", "team_total model era (archived Engine A)")
            out["blocks"]["engine_A_team_total_era"] = sb
            md.append("\n### Engine A — team_total by model era\n")
            md.append(ca.md_table(sb["rows"], ["split", "n", "games", "baseRate", "meanModelP", "meanMarketP", "brierModel", "brierMarket", "deltaBrier", "deltaLo95", "deltaHi95", "modelSlope"]))
            gt = d[d["family"] == "game_total"].copy()
            gt["era"] = np.where(gt["era_total_rung_ge"], "rung>=N (>=09-01)", "rung>N (<09-01)")
            sb = split_block(gt, "era", "game_total ledger era")
            out["blocks"]["engine_A_game_total_era"] = sb

        # closing benchmark on the subset with a pregame closing quote
        dc = ca.primary_rows(df, engine=engine, max_date=SETTLED_MAX_DATE, require_close=True)
        rows = []
        for fam, g in list(dc.groupby("family")) + [("ALL", dc)]:
            yy = g["outcome"].values
            pt, lo, hi, p = ca.paired_delta_ci(g, "modelP", "closeP")
            pt2, lo2, hi2, p2 = ca.paired_delta_ci(g, "marketP", "closeP")
            rows.append({"family": fam, "n": len(g), "games": int(g["gameId"].nunique()),
                         "brierModel": ca.brier(g["modelP"].values, yy), "brierCaptureMid": ca.brier(g["marketP"].values, yy), "brierClose": ca.brier(g["closeP"].values, yy),
                         "deltaModelVsClose": pt, "lo": lo, "hi": hi, "deltaCaptureMidVsClose": pt2, "lo2": lo2, "hi2": hi2})
        out["blocks"][f"engine_{engine}_closing"] = rows
        md.append(f"\n### Engine {engine} — closing-quote benchmark (retrospective only)\n")
        md.append(ca.md_table(rows, ["family", "n", "games", "brierModel", "brierCaptureMid", "brierClose", "deltaModelVsClose", "lo", "hi", "deltaCaptureMidVsClose", "lo2", "hi2"]))

    # time-to-start dynamics using ALL pregame captures (not just the last) for Engine B
    dall = df[(df["engine"] == "B") & df["pregameAtCapture"] & df["outcome"].notna() & df["marketP"].notna()]
    dall = dall[(dall["marketP"] > 0) & (dall["marketP"] < 1) & (dall["date"] <= SETTLED_MAX_DATE)].copy()
    dall["outcome"] = dall["outcome"].astype(int)
    dall = add_dimensions(dall)
    sb = split_block(dall, "timeBucket", "all pregame captures by minutes to start")
    out["blocks"]["engine_B_all_captures_timeBucket"] = sb
    md.append("\n## Engine B — every pregame capture (multiple per ticker), by minutes to first pitch\n")
    md.append(ca.md_table(sb["rows"], ["split", "n", "games", "brierModel", "brierMarket", "deltaBrier", "deltaLo95", "deltaHi95", "modelSlope"]))

    # Engine A vs Engine B on the same ticker/capture (game_result, first_inning_run)
    a = df[(df["engine"] == "A") & df["pregameAtCapture"] & df["outcome"].notna()][["captureId", "ticker", "side", "modelP", "family", "outcome"]]
    b = df[(df["engine"] == "B") & df["pregameAtCapture"]][["captureId", "ticker", "modelP"]].rename(columns={"modelP": "modelP_B"})
    ab = a.merge(b, on=["captureId", "ticker"], how="inner")
    ab = ab[ab["side"] == "YES"]
    rows = []
    for fam, g in ab.groupby("family"):
        yy = g["outcome"].values.astype(int)
        rows.append({"family": fam, "n": len(g), "brierEngineA": ca.brier(g["modelP"].values, yy), "brierEngineB": ca.brier(g["modelP_B"].values, yy),
                     "meanAbsDiff": float((g["modelP"] - g["modelP_B"]).abs().mean())})
    out["blocks"]["engine_A_vs_B_same_contract"] = rows
    md.append("\n## Engine A vs Engine B on identical contracts/captures (YES side)\n")
    md.append(ca.md_table(rows, ["family", "n", "brierEngineA", "brierEngineB", "meanAbsDiff"]))

    ca.write_json(out, OUT_JSON)
    with open(OUT_MD, "w") as f:
        f.write("\n".join(md) + "\n")
    print(f"wrote {OUT_JSON} and {OUT_MD}")


if __name__ == "__main__":
    main()
