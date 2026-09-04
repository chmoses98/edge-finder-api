"""
tests/edgelab/test_calibration_research_infrastructure.py
==========================================================
Unit tests for the calibration research infrastructure
(lib/edgelab/research/calibration_{dataset,analysis,candidates}.py and
frozen_calibration_map.py). Pure, offline, no archive reads.
"""
import datetime as dt
import math
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

pd = pytest.importorskip("pandas")

from lib.edgelab.research import calibration_analysis as ca  # noqa: E402
from lib.edgelab.research import calibration_candidates as cc  # noqa: E402
from lib.edgelab.research import calibration_dataset as cd  # noqa: E402
from lib.edgelab.research import frozen_calibration_map as fcm  # noqa: E402


# ---------------------------------------------------------------- dataset

def test_capture_ts_parses_keyed_directory_only():
    ts = cd._capture_ts("2026-08-25T163337Z")
    assert ts == dt.datetime(2026, 8, 25, 16, 33, 37, tzinfo=dt.timezone.utc)
    assert cd._capture_ts("unkeyed") is None
    assert cd._capture_ts("frozen") is None


def test_f5_spread_regrade_uses_verified_linescore_and_side():
    scores = {"824807": (5, 0)}
    c = {"gameId": 824807, "line": 1.5, "subjectId": "PHI", "awayTeam": "PHI", "homeTeam": "BAL"}
    assert cd.f5_spread_outcome(c, scores) == 1
    c["subjectId"] = "BAL"
    assert cd.f5_spread_outcome(c, scores) == 0
    c["gameId"] = 1  # unverified game -> never fabricated
    assert cd.f5_spread_outcome(c, scores) is None


def test_era_flags_follow_documented_boundaries():
    f = cd._era_flags("2026-08-20")
    assert f["era_team_total_v12"] is False and f["era_pitcher_props_modeled"] is True
    f = cd._era_flags("2026-09-01")
    assert f["era_total_rung_ge"] is True and f["era_team_total_v12"] is True


def test_no_side_rows_are_expressed_on_the_no_leg():
    rows = [{"engine": "A", "date": "2026-08-25", "ticker": "KXMLBRFI-X", "side": "NO", "family": "first_inning_run",
             "yesBid": 40.0, "yesAsk": 42.0}]
    outcomes = {"KXMLBRFI-X": {"outcome": 1, "settleDate": "2026-08-25", "source": "settlement_store"}}
    closing = {"KXMLBRFI-X": {"yesBid": 44.0, "yesAsk": 46.0, "capturedAt": "t", "checkpoint": "T_MINUS_5"}}
    cd.attach_outcomes_and_quotes(rows, outcomes, {}, closing, {}, {})
    r = rows[0]
    assert r["outcome"] == 0                      # YES settled -> NO leg lost
    assert r["mid"] == pytest.approx(59.0)        # 100 - 41
    assert r["closeMid"] == pytest.approx(55.0)   # 100 - 45
    assert r["yesBid"] == pytest.approx(58.0) and r["yesAsk"] == pytest.approx(60.0)


def test_engine_a_family_mapping_covers_all_ledger_markets():
    for m in cd.ENGINE_A_MARKETS:
        assert cd._engine_a_family(m) is not None


# --------------------------------------------------------------- analysis

def test_platt_fit_recovers_known_map():
    rng = np.random.default_rng(1)
    x = rng.normal(0, 1.5, 20000)
    p_true = ca.sigmoid(0.3 + 0.6 * x)
    y = (rng.random(20000) < p_true).astype(float)
    a, b = ca.fit_platt_params(x, y)
    assert abs(a - 0.3) < 0.06 and abs(b - 0.6) < 0.06


def test_platt_fit_on_calibrated_data_is_identity_like():
    rng = np.random.default_rng(2)
    p = rng.uniform(0.05, 0.95, 20000)
    y = (rng.random(20000) < p).astype(float)
    a, b = ca.calibration_slope_intercept(p, y)
    assert abs(a) < 0.08 and abs(b - 1.0) < 0.08


def test_beta_calibration_identity_on_calibrated_data():
    rng = np.random.default_rng(3)
    p = rng.uniform(0.05, 0.95, 20000)
    y = (rng.random(20000) < p).astype(float)
    c, a, b = ca.fit_beta_calibration(p, y)
    q = ca.apply_beta_calibration(np.array([0.2, 0.5, 0.8]), c, a, b)
    assert np.allclose(q, [0.2, 0.5, 0.8], atol=0.04)


def test_market_blend_finds_zero_weight_for_pure_noise_model():
    rng = np.random.default_rng(4)
    pk = rng.uniform(0.1, 0.9, 20000)
    y = (rng.random(20000) < pk).astype(float)
    pm = np.clip(pk + rng.normal(0, 0.25, 20000), 0.01, 0.99)  # noisy copy of the market
    c, wm, wk = ca.fit_market_blend(pm, pk, y)
    assert wk > 0.8 and abs(wm) < 0.15


def test_murphy_decomposition_sums_to_brier():
    rng = np.random.default_rng(5)
    p = rng.uniform(0, 1, 5000)
    y = (rng.random(5000) < p).astype(float)
    m = ca.murphy_decomposition(p, y)
    assert abs((m["reliability"] - m["resolution"] + m["uncertainty"]) - m["brier"]) < 2e-3


def test_cluster_bootstrap_ci_contains_point_and_is_wider_with_clusters():
    rng = np.random.default_rng(6)
    clusters = np.repeat(np.arange(50), 40)
    cluster_effect = rng.normal(0, 0.2, 50)[clusters]
    v = cluster_effect + rng.normal(0, 0.05, len(clusters))
    pt, lo, hi, p = ca.fast_cluster_bootstrap_mean(v, clusters, n_boot=500)
    assert lo <= pt <= hi
    # naive iid SE would be ~0.2/sqrt(2000)=0.0045; clustered should be ~0.2/sqrt(50)=0.028
    assert (hi - lo) / 2 > 0.03


def test_paired_delta_ci_sign_convention():
    d = pd.DataFrame({"outcome": [1, 0, 1, 0] * 50, "gameId": np.repeat(np.arange(50), 4)})
    d["good"] = np.where(d["outcome"] == 1, 0.9, 0.1)
    d["bad"] = np.where(d["outcome"] == 1, 0.6, 0.4)
    pt, lo, hi, _ = ca.paired_delta_ci(d, "good", "bad")
    assert pt < 0 and hi < 0


# ------------------------------------------------------------- candidates

def _game(away=4.3, home=4.0):
    return {"ctxAwayProjRuns": away, "ctxHomeProjRuns": home, "ctxTotalProj": away + home,
            "ctxF5AwayProj": away * 5 / 9, "ctxF5HomeProj": home * 5 / 9,
            "ctxF3AwayProj": away * 3 / 9, "ctxF3HomeProj": home * 3 / 9,
            "ctxF7AwayProj": away * 7 / 9, "ctxF7HomeProj": home * 7 / 9}


def test_poisson_replication_matches_production_adapters():
    from lib.kalshi_probability_adapters import adapt_game_result, adapt_total, adapt_team_total, adapt_winning_margin, adapt_f5_result
    g = _game()
    r = {"family": "game_result", "period": "full_game", "contractSide": "Away", "line": None, "subjectId": "A", "awayTeam": "A", "homeTeam": "H"}
    assert cc.poisson_probability(r, g) == pytest.approx(adapt_game_result(4.3, 4.0, "Away")[0], abs=1e-6)
    r = {"family": "game_total", "period": "full_game", "contractSide": "Over", "line": 9, "awayTeam": "A", "homeTeam": "H"}
    assert cc.poisson_probability(r, g) == pytest.approx(adapt_total(8.3, 9, "Over")[0], abs=1e-6)
    r = {"family": "team_total", "period": "full_game", "contractSide": "Over", "line": 3.5, "subjectId": "H", "awayTeam": "A", "homeTeam": "H"}
    assert cc.poisson_probability(r, g) == pytest.approx(adapt_team_total(4.0, 3.5, "Over")[0], abs=1e-6)
    r = {"family": "winning_margin", "period": "full_game", "line": 1.5, "subjectId": "A", "awayTeam": "A", "homeTeam": "H"}
    assert cc.poisson_probability(r, g) == pytest.approx(adapt_winning_margin(4.3, 4.0, 1.5)[0], abs=1e-6)
    r = {"family": "inning_result", "period": "F5", "contractSide": "Tie", "awayTeam": "A", "homeTeam": "H"}
    assert cc.poisson_probability(r, g) == pytest.approx(adapt_f5_result(4.3 * 5 / 9, 4.0 * 5 / 9, "Tie")[0], abs=1e-6)


def test_nb_widens_tails_relative_to_poisson_and_shift_raises_totals():
    g = _game()
    r = {"family": "game_total", "period": "full_game", "contractSide": "Over", "line": 13, "awayTeam": "A", "homeTeam": "H"}
    p0, p1 = cc.poisson_probability(r, g), cc.nb_probability(r, g)
    assert p1 > p0                                   # overdispersion: more mass in the upper tail
    r2 = {"family": "game_total", "period": "full_game", "contractSide": "Over", "line": 4, "awayTeam": "A", "homeTeam": "H"}
    assert cc.nb_probability(r2, g) < cc.poisson_probability(r2, g)   # ... and in the lower tail
    assert cc.nb_probability(r, g, mean_shift=0.3) > p1               # higher means -> higher over prob


def test_candidates_never_fabricate_for_unpriced_families():
    g = _game()
    assert cc.nb_probability({"family": "pitcher_strikeouts", "period": "full_game", "line": 5}, g) is None
    assert cc.nb_probability({"family": "team_total", "period": "full_game", "line": 3.5, "subjectId": "ZZ", "awayTeam": "A", "homeTeam": "H"}, g) is None


# ----------------------------------------------------------- frozen map

def test_frozen_map_apply_is_pure_and_falls_back_to_identity():
    art = {"recipes": {"drop_in": {"global": {"a": 0.0, "b": 1.0}, "families": {"team_total": {"a": 0.1, "b": 0.7}}}}, "quarantine": {"families": ["pitcher_outs"]}}
    p = fcm.apply_calibrated_probability("team_total", 0.3, artifact=art)
    assert p == pytest.approx(1 / (1 + math.exp(-(0.1 + 0.7 * math.log(0.3 / 0.7)))))
    assert fcm.apply_calibrated_probability("game_total", 0.3, artifact=art) == pytest.approx(0.3)
    assert fcm.apply_calibrated_probability("team_total", None, artifact=art) is None
    assert fcm.is_quarantined("pitcher_outs", artifact=art) and not fcm.is_quarantined("team_total", artifact=art)


def test_committed_frozen_artifact_is_inactive_and_monotone():
    if not os.path.exists(fcm.ARTIFACT_PATH):
        pytest.skip("artifact not built")
    art = fcm.load_artifact()
    assert art["productionActive"] is False and art["researchOnly"] is True
    for recipe in ("drop_in", "structural"):
        for fam, pr in art["recipes"][recipe]["families"].items():
            assert pr["b"] > 0, (recipe, fam)   # monotone maps only
            grid = [fcm.apply_calibrated_probability(fam, x, recipe=recipe, artifact=art) for x in (0.05, 0.3, 0.5, 0.7, 0.95)]
            assert grid == sorted(grid)
