"""Tests for the MLB Projection Foundation modules (leakage, distribution coherence, selection,
output contracts). Fast: synthetic data only, plus schema checks of committed results."""
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

HERE = os.path.dirname(__file__)
PF = os.path.join(HERE, "..")
sys.path.insert(0, PF)

from pf_core import dists  # noqa: E402
from mlb import evaluate as EV, features as F  # noqa: E402
from mlb.ladder import FamilyResult, apply_glm_offset, fit_glm_offset  # noqa: E402


def test_ew_prior_sums_excludes_same_and_future_dates():
    df = pd.DataFrame({"g": ["a", "a", "a", "a"],
                       "date": pd.to_datetime(["2024-04-01", "2024-04-01", "2024-04-02", "2024-04-10"]),
                       "x": [1.0, 2.0, 4.0, 8.0]})
    s = F.ew_prior_sums(df, ["g"], "date", ["x"], halflife_days=1e9)["x_ew"].to_numpy()
    # doubleheader rows (same date) see nothing; the next day sees both; later rows never see the future
    assert np.allclose(s, [0.0, 0.0, 3.0, 7.0])


def test_ew_prior_sums_decay():
    df = pd.DataFrame({"date": pd.to_datetime(["2024-04-01", "2024-04-03"]), "x": [1.0, 0.0]})
    s = F.ew_prior_sums(df, None, "date", ["x"], halflife_days=2.0)["x_ew"].to_numpy()
    assert abs(s[1] - 0.5) < 1e-9


def test_binomial_mixture_mean_and_coherence():
    n = dists.DistBatch(0, np.array([[0, 0, 0, 0.2, 0.5, 0.3, 0, 0]]))  # PA in {3,4,5}
    d = EV.binomial_mixture(n, np.array([0.25]))
    assert abs(d.P.sum() - 1) < 1e-9
    assert abs(d.mean[0] - n.mean[0] * 0.25) < 1e-6
    bb = EV.binomial_mixture(n, np.array([0.25]), phi=0.05)
    assert abs(bb.mean[0] - n.mean[0] * 0.25) < 1e-6
    assert bb.var[0] > d.var[0]  # beta-binomial is wider
    ks = [d.prob_ge(k)[0] for k in range(6)]
    assert all(ks[i] >= ks[i + 1] for i in range(5))


def test_total_bases_convolution():
    n = dists.DistBatch(0, np.array([[0, 1.0]]))  # exactly one PA
    probs = np.array([[0.7, 0.15, 0.05, 0.02, 0.08]])
    d = EV.multinomial_bases_mixture(n, probs, K=10)
    assert np.allclose(d.P[0, :5], probs[0])
    two = EV.multinomial_bases_mixture(dists.DistBatch(0, np.array([[0, 0, 1.0]])), probs, K=10)
    assert abs(two.mean[0] - 2 * (probs[0] @ np.arange(5))) < 1e-9


def test_glm_offset_recovers_elasticity():
    rng = np.random.default_rng(3)
    base = rng.uniform(2, 8, 20000)
    lf = rng.normal(0, 0.2, 20000)
    y = rng.poisson(base * np.exp(0.9 * lf))
    b = fit_glm_offset(y, base, lf[:, None], ["opp"], l2=0.0)
    assert abs(b["opp"] - 0.9) < 0.08
    mu = apply_glm_offset(base, lf[:, None], b, ["opp"])
    assert abs(mu.mean() - y.mean()) < 0.1


def test_selection_rule_prefers_transparent_when_not_significant():
    from mlb.run_walkforward import select_winner
    rng = np.random.default_rng(0)
    n = 2000
    df = pd.DataFrame({"y": rng.poisson(5, n), "game_pk": np.repeat(np.arange(n // 2), 2)})
    fr = FamilyResult("x")
    m = np.ones(n, bool)
    fr.extra["masks"] = {"validation": m}
    dD = dists.poisson(np.full(n, 5.0))
    dE = dists.poisson(np.full(n, 5.0001))
    fr.dists[("D", "validation")] = dD
    fr.dists[("E", "validation")] = dE
    fr.rows = [{"split": "validation", "model": "D", "crps": float(dD.crps(df.y).mean()) + 1e-9},
               {"split": "validation", "model": "E", "crps": float(dD.crps(df.y).mean())}]
    win, note = select_winner(fr, df, "y")
    assert win == "D", note


def _load(path):
    p = os.path.join(PF, "results", path)
    if not os.path.exists(p):
        pytest.skip(f"{path} not generated yet")
    return json.load(open(p))


def test_summary_contract():
    s = _load("summary.json")
    assert s["schema"] == "sift.projection_foundation.sport_summary.v1" and s["sport"] == "MLB"
    for k in ("generated_utc", "code_sha", "pf_core_version", "splits", "inventory", "families", "experiments",
              "error_investigations", "opponent_adjustment", "opportunity", "calibration", "failed_experiments",
              "gate", "priorities"):
        assert k in s, k
    for f in s["families"]:
        assert f["readiness"] in ("READY", "SHADOW VALIDATION", "NEEDS IMPROVEMENT", "INSUFFICIENT DATA")
        assert f["best_model"] and f["holdout"]["model"] == f["best_model"]
        for m in f["models"]:
            assert m["split"] in ("development", "validation", "holdout")
            assert m["verdict"] in ("best", "competitive", "rejected", "baseline", "incumbent")
    for e in s["experiments"]:
        assert e["decision"] in ("adopt", "reject", "investigate", "insufficient evidence")
    for p in s["inventory"]:
        assert p["pit_class"] in ("archived", "reconstructable", "retrospective", "missing")


def test_projections_contract():
    s = _load("projections_latest.json")
    assert s["schema"] == "sift.projections.v1" and s["model"]["authority"] == "research_only"
    assert s["players"], "no player projections"
    for p in s["players"]:
        th = [p["thresholds"][k] for k in sorted(p["thresholds"], key=float)]
        assert all(th[i] >= th[i + 1] - 1e-9 for i in range(len(th) - 1))
        assert p["gate"]["status"] in ("PASS", "REVIEW", "BLOCK")
        assert p["p10"] <= p["median"] <= p["p90"]
