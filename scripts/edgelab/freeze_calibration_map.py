#!/usr/bin/env python3
"""
scripts/edgelab/freeze_calibration_map.py
==========================================
Fits the all-data parameters for the two calibration recipes validated by
scripts/edgelab/run_calibration_walkforward.py and writes the frozen,
reviewable artifact data/edgelab/analytics/frozen_calibration_map_v1.json.
RESEARCH ONLY: the artifact is `productionActive: false` and nothing in
production reads it.

Deterministic; safe to rerun (rewrites the same artifact from the same
inputs).
"""
import datetime as _dt
import json
import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.research import calibration_analysis as ca  # noqa: E402
from lib.edgelab.research import calibration_candidates as cc  # noqa: E402
from lib.edgelab.research.frozen_calibration_map import ARTIFACT_PATH  # noqa: E402
from scripts.edgelab.run_calibration_walkforward import fit_global_platt, fit_family_platt, select_mean_shift, RUN_FAMILIES, HIER_L2, MIN_FAMILY_ROWS  # noqa: E402

SETTLED_MAX_DATE = "2026-08-31"
QUARANTINE = ["pitcher_strikeouts", "pitcher_outs", "first_inning_run"]


def main():
    df = ca.load_rows()
    games = pd.read_json(ca.GAMES_PATH, lines=True)
    gidx = {(r.captureId, int(r.gameId)): r._asdict() for r in games.itertuples(index=False)}
    d = ca.primary_rows(df, engine="B", max_date=SETTLED_MAX_DATE)
    for s_ in cc.MEAN_SHIFT_GRID:
        d[f"nbP_shift{s_}"] = [cc.nb_probability(r, gidx.get((r["captureId"], int(r["gameId"]))), mean_shift=s_) for r in d.to_dict("records")]

    # drop-in recipe: family Platt on production's Poisson probability
    t = d.copy()
    t["baseP"] = t["modelP"]
    g_drop = fit_global_platt(t)
    f_drop = fit_family_platt(t, g_drop)

    # structural recipe: NB (frozen dispersion) + selected mean shift + family Platt
    shift = select_mean_shift(d)
    t2 = d.copy()
    t2["baseP"] = t2[f"nbP_shift{shift}"].where(t2[f"nbP_shift{shift}"].notna(), t2["modelP"])
    g_str = fit_global_platt(t2)
    f_str = fit_family_platt(t2, g_str)

    fam_counts = d.groupby("family").agg(n=("outcome", "size"), games=("gameId", "nunique")).to_dict("index")
    art = {
        "artifactId": "frozen_calibration_map_v1",
        "createdAt": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "productionActive": False,
        "researchOnly": True,
        "fitWindow": {"slateDates": [str(d["date"].min()), str(d["date"].max())], "rows": int(len(d)), "games": int(d["gameId"].nunique()),
                      "unit": "last pregame capture per (ticker, side), Engine B, settled outcomes"},
        "validation": "walk-forward + two frozen pseudo-holdouts; see data/edgelab/research_artifacts/calibration_research/walkforward.md",
        "hierarchicalShrinkL2": HIER_L2, "minFamilyRows": MIN_FAMILY_ROWS,
        "recipes": {
            "drop_in": {"appliesTo": "production Poisson probability (lib.kalshi_probability_adapters / scripts.build_market_ledger modelProb)",
                        "form": "p' = sigmoid(a + b*logit(p))", "global": g_drop, "families": f_drop},
            "structural": {"appliesTo": "probability re-priced with negative-binomial dispersion 0.281513 (MLB-RSCH-0010) and mean shift below",
                           "nbDispersion": cc.FROZEN_NB_DISPERSION, "meanShiftRunsPerTeam": shift, "runFamilies": list(RUN_FAMILIES),
                           "form": "p' = sigmoid(a + b*logit(p_nb))", "global": g_str, "families": f_str},
        },
        "quarantine": {"families": QUARANTINE,
                       "reason": "calibrated probability no better than a walk-forward base rate by family/line (pitcher_strikeouts worse than base rate; first_inning_run resolution ~0)"},
        "familySample": fam_counts,
    }
    os.makedirs(os.path.dirname(ARTIFACT_PATH), exist_ok=True)
    with open(ARTIFACT_PATH, "w") as f:
        json.dump(art, f, indent=2, sort_keys=True, default=float)
    print(f"wrote {ARTIFACT_PATH}; mean shift={shift}; drop-in global={g_drop}")


if __name__ == "__main__":
    main()
