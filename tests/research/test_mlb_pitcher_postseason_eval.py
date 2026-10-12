"""Tests for the W2 sports-only postseason evaluation harness
(scripts/research/mlb_pitcher_postseason_eval.py)."""
import gzip
import importlib.util
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPEC = importlib.util.spec_from_file_location("mlb_pitcher_postseason_eval", os.path.join(ROOT, "scripts", "research", "mlb_pitcher_postseason_eval.py"))
E = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E)
from lib.research import pitcher_workload_joint as J  # noqa: E402

FEAT = {"n_hist": 8, "season_starts": 8, "days_rest": 5, "w_bf": 23.0, "w_outs": 16.5, "w_pitches": 90.0,
        "last_pitches": 92, "last_bf": 24, "max_pitches_last3": 98, "pitches_per_bf": (720, 184),
        "k_rate_hist_bf": 184, "k_rate_hist_k": 44, "outs_per_bf_hist_bf": 184, "outs_per_bf_hist_outs": 132,
        "bullpen_pitches_3d": 60, "bullpen_pitches_1d": 20, "opp_k_rate": 0.22, "opp_nonout_rate": 0.31, "opp_pa": 3000}


def _params():
    with open(E.W.PARAMS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def test_regime_zero_is_exactly_the_frozen_projection():
    p = _params()
    assert json.dumps(E.project_with_regime(FEAT, p, 0.0), sort_keys=True) == json.dumps(J.project(FEAT, p), sort_keys=True)


def test_regime_shift_moves_outs_and_restores_module():
    p = _params()
    base = J.project(FEAT, p)
    lo = E.project_with_regime(FEAT, p, -1.5)
    assert lo["expected_outs"] < base["expected_outs"] and lo["expected_batters_faced"] < base["expected_batters_faced"]
    assert abs(sum(lo["outs_pmf"]) - 1) < 1e-9
    assert json.dumps(J.project(FEAT, p), sort_keys=True) == json.dumps(base, sort_keys=True)  # wrapper removed


def test_load_postseason_starts_reads_only_the_postseason_cache(tmp_path):
    d = tmp_path / "research_cache" / "starter_workload" / "2026"
    d.mkdir(parents=True)
    rec = {"gamePk": 1, "season": 2026, "officialDate": "2026-10-04", "scheduledStart": "2026-10-04T20:08:00Z",
           "gameEnd": "2026-10-04T23:30:00Z", "gameType": "D", "seriesDescription": "Division Series",
           "fetched_at": "2026-10-11T02:30:00Z", "source_url": "u",
           "awayTeam": {"id": 147}, "homeTeam": {"id": 141},
           "awayPitchers": [{"playerId": "11", "orderIndex": 0, "outs": 15, "battersFaced": 21, "strikeOuts": 6,
                             "numberOfPitches": 88, "inningsPitched": "5.0"}],
           "homePitchers": [{"playerId": "21", "orderIndex": 1, "outs": 3}]}
    with gzip.open(d / "postseason_boxscores.jsonl.gz", "wt") as fh:
        fh.write(json.dumps(rec) + "\n")
    rows = E.load_postseason_starts(str(tmp_path), ("2026",))
    assert len(rows) == 1  # home side has no orderIndex-0 starter line -> skipped
    r = rows[0]
    assert (r["pid"], r["team"], r["opp"], r["outs"], r["bf"], r["k"], r["game_type"]) == ("11", "NYY", "TOR", 15, 21, 6, "D")


def test_decision_rule_is_strict():
    def stat(mae_ch, mae_b, cov, d_ch, d_b):
        ci = lambda lo, hi: {"point": (lo + hi) / 2, "ci95": [lo, hi]}  # noqa: E731
        deltas = {}
        for arm in ("challenger", "challenger_regime"):
            deltas[f"{arm}_minus_champion"] = {m: ci(*d_ch) for m in ("delta_mae", "delta_threshold_brier", "delta_log_loss")}
            deltas[f"{arm}_minus_baseline"] = {m: ci(*d_b) for m in ("delta_mae", "delta_threshold_brier", "delta_log_loss")}
        ov = {"challenger": {"mae": mae_ch, "p10_p90_coverage": cov}, "challenger_regime": {"mae": mae_ch, "p10_p90_coverage": cov},
              "baseline": {"mae": mae_b}}
        return {"n": 60, "overall": ov, "deltas": deltas}
    good = {"stats": {s: stat(2.0, 2.5, 0.85, (-0.3, -0.1), (-0.5, -0.2)) for s in E.STATS}}
    assert E.decide(good)["challenger"]["verdict"] == "POSTSEASON_RESEARCH_PASS"
    unclear = {"stats": {s: stat(2.0, 2.5, 0.85, (-0.3, 0.1), (-0.5, -0.2)) for s in E.STATS}}
    assert E.decide(unclear)["challenger"]["verdict"] == "INCONCLUSIVE"
    worse = {"stats": dict(good["stats"], pitcher_batters_faced=stat(2.0, 2.5, 0.85, (0.1, 0.3), (-0.5, -0.2)))}
    d = E.decide(worse)["challenger"]
    assert d["verdict"] == "REJECTED" and d["postseason_status"] == "BLOCKED"
