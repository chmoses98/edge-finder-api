#!/usr/bin/env python3
"""
tests/research/test_pitcher_workload_joint_leash.py
===================================================
Research variant mlb_pitcher_workload_joint_leash_v1 (lib/research/pitcher_workload_joint_leash.py)
of the frozen workload challenger. Synthetic features + the committed frozen params only.
"""
import inspect
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.research import pitcher_workload_joint as J  # noqa: E402
from lib.research import pitcher_workload_joint_leash as L  # noqa: E402

PARAMS = os.path.join(ROOT, "data", "research", "pitcher_workload_joint", "params.json")
VARIANT = os.path.join(ROOT, "data", "research", "pitcher_long_leash", "variant_params.json")
FORBIDDEN = ("market", "odds", "implied", "kalshi", "pinnacle", "price", "settle", "quote", "wager", "consensus", "closing")


def _feat(w_pitches=88.0, w_outs=16.0):
    return {"n_hist": 12, "season_starts": 10, "days_rest": 5, "w_bf": 23.0, "w_outs": w_outs, "w_pitches": w_pitches,
            "last_pitches": w_pitches, "last_bf": 23, "max_pitches_last3": w_pitches + 3, "pitches_per_bf": (1000, 260),
            "k_rate_hist_bf": 260, "k_rate_hist_k": 60, "outs_per_bf_hist_bf": 260, "outs_per_bf_hist_outs": 180,
            "bullpen_pitches_3d": 120, "bullpen_pitches_1d": 40, "opp_k_rate": 0.22, "opp_nonout_rate": 0.31, "opp_pa": 2000}


def _load(p):
    with open(p) as fh:
        return json.load(fh)


def test_frozen_modes_reproduce_the_frozen_challenger_exactly():
    params = _load(PARAMS)
    for wp in (70.0, 88.0, 101.0):
        a, b = J.project(_feat(wp), params), L.project(_feat(wp), params)
        for k in ("outs_pmf", "bf_pmf", "k_pmf"):
            assert a[k] == b[k]


def test_preregistered_variant_changes_only_bf_given_outs():
    v = _load(VARIANT)
    assert v["nonout_mode"] == "leash_gamma_by_outs" and v["kernel_mode"] == "frozen"
    assert v["variant_fit"]["dev_seasons"] == ["2022", "2023", "2024"]
    assert v["variant_fit"]["base_params_sha256"] == "b36ad6280d69edb593878fdfe371905014eef9fa33cc49398aebe398b035e70c"
    base = _load(PARAMS)
    for wp in (70.0, 101.0):
        a, b = J.project(_feat(wp), base), L.project(_feat(wp), v)
        assert a["outs_pmf"] == b["outs_pmf"]          # duration stage untouched
        assert abs(sum(b["bf_pmf"]) - 1) < 1e-9 and abs(sum(b["k_pmf"]) - 1) < 1e-9


def test_longer_leash_raises_non_outs_at_fixed_outs():
    v = _load(VARIANT)
    p = {**J.DEFAULT_PARAMS, **L.VARIANT_DEFAULTS, **v}
    short, long_ = L.leash_dev(_feat(72.0), p), L.leash_dev(_feat(100.0), p)
    assert short < 0 < long_
    for o in (9, 15, 18):
        assert L.nonout_mean(o, 0.41, long_, p) > L.nonout_mean(o, 0.41, short, p)


def test_variant_reads_only_the_frozen_sports_feature_keys():
    assert L.FEATURE_KEYS == J.FEATURE_KEYS
    src = inspect.getsource(L).lower()
    code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    body = code.split('"""', 2)[-1]                     # skip the module docstring
    assert not any(w in body for w in FORBIDDEN)
    assert "open(" not in body and "import os" not in body


def test_decision_rule_is_mechanical_and_strict():
    sys.path.insert(0, os.path.join(ROOT, "scripts", "research"))
    import mlb_pitcher_long_leash as LL

    def ci(p, lo, hi):
        return {"point": p, "ci95": [lo, hi]}

    def deltas(lo, hi):
        return {m: ci((lo + hi) / 2, lo, hi) for m in ("delta_mae", "delta_threshold_brier", "delta_log_loss")}

    def summ(mae, cov=0.87, n=900):
        return {"mae": mae, "p10_p90_coverage": cov, "n": n}

    def stat(good=True):
        tiers = {t: {"variant_minus_challenger": deltas(-0.1, -0.01) if good else deltas(0.01, 0.1),
                     "variant_minus_champion": deltas(-0.1, -0.01),
                     "summary": {"variant": summ(2.5)},
                     "bias": {"variant": ci(-0.1, -0.2, 0.0), "challenger": ci(-0.6, -0.8, -0.4)}}
                 for t in ("short_lt80", "mid_80_95", "long_ge95")}
        return {"overall": {"variant": summ(2.6), "baseline": summ(2.9)}, "variant_minus_challenger": deltas(-0.05, -0.01),
                "variant_minus_champion": deltas(-0.05, -0.01), "by_pitch_leash": tiers}

    ok = {"stats": {s: stat() for s in LL.STATS}}
    assert LL.verdict(ok)["verdict"] == "ACCEPTED_RESEARCH_VARIANT"
    bad = {"stats": {s: stat() for s in LL.STATS}}
    bad["stats"]["pitcher_strikeouts"]["by_pitch_leash"]["short_lt80"]["variant_minus_challenger"] = deltas(0.01, 0.1)
    v = LL.verdict(bad)
    assert v["verdict"] == "REJECTED" and v["checks"]["4_no_tier_regression_vs_challenger"] is False
