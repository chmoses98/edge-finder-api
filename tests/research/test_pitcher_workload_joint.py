#!/usr/bin/env python3
"""
tests/research/test_pitcher_workload_joint.py
=============================================
W1-D sports-only joint pitcher workload challenger (lib/research/pitcher_workload_joint.py,
lib/research/pitcher_workload_sources.py). Synthetic fixtures only (tmp_path); the
full-data runtime mutation test is scripts/research/mlb_pitcher_workload_mutation.py.
"""
import gzip
import inspect
import json
import os
import random
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.research import pitcher_workload_joint as J  # noqa: E402
from lib.research import pitcher_workload_sources as S  # noqa: E402
from lib.research import mlb_player_props as MP  # noqa: E402
from lib.research import pitcher_prop_projection as PP  # noqa: E402

FORBIDDEN = ("market", "odds", "implied", "kalshi", "pinnacle", "price", "settle", "observation",
             "quote", "wager", "bet", "consensus", "closing")


# ---------------------------------------------------------------- synthetic sports tree

def _write_tree(root, n_days=24, seed=3):
    """Two clubs, alternating starters, one game per day (gamePk 1000+day)."""
    rng = random.Random(seed)
    sched_dir = os.path.join(root, "research_cache", "bullpen_backtest", "2026", "schedules")
    box_dir = os.path.join(root, "research_cache", "starter_workload", "2026")
    os.makedirs(sched_dir)
    os.makedirs(box_dir)
    dates, box = [], []
    for d in range(1, n_days + 1):
        pk = 1000 + d
        day = f"2026-08-{d:02d}"
        dates.append({"date": day, "games": [{
            "gamePk": pk, "gameType": "R", "officialDate": day, "gameDate": f"{day}T23:05:00Z", "gameNumber": 1,
            "status": {"detailedState": "Final"},
            "teams": {"away": {"team": {"id": 147}}, "home": {"team": {"id": 111}}}}]})

        def line(pid, outs):
            bf = outs + rng.randint(4, 9)
            return [{"playerId": pid, "orderIndex": 0, "outs": outs, "battersFaced": bf,
                     "strikeOuts": rng.randint(2, min(10, bf)), "numberOfPitches": 4 * bf, "baseOnBalls": 1},
                    {"playerId": "900", "orderIndex": 1, "outs": 27 - outs, "battersFaced": 30 - bf % 5,
                     "strikeOuts": 3, "numberOfPitches": 40, "baseOnBalls": 0}]
        box.append({"gamePk": pk, "awayPitchers": line(str(100 + d % 5), rng.randint(12, 20)),
                    "homePitchers": line(str(200 + d % 5), rng.randint(12, 20))})
    with open(os.path.join(sched_dir, "NYY.json"), "w") as fh:
        json.dump({"dates": dates}, fh)
    with gzip.open(os.path.join(box_dir, "boxscores.jsonl.gz"), "wt") as fh:
        for b in box:
            fh.write(json.dumps(b) + "\n")


def _write_settlements(root, actual):
    sd = os.path.join(root, "edgelab", "settlements")
    os.makedirs(sd, exist_ok=True)
    with gzip.open(os.path.join(sd, "2026-10-01.jsonl.gz"), "wt") as fh:
        fh.write(json.dumps({"marketFamily": "pitcher_outs", "result": "YES",
                             "marketTicker": "KXMLBOUTS-26SEP301900NYYBOS-NYYP1-15",
                             "settlementEvidence": {"actualValue": actual, "gamePk": "777",
                                                    "candidates": [{"playerId": 101, "side": "away"}]}}) + "\n")


def _project_all(root, params=None):
    games = J.link_mirrors(S.load_pitching_games(root))
    idx = J.AsOfIndex(games)
    out = []
    for g in games:
        st = g["starter"]
        feat, obs = J.features(idx, st["pid"], g["team"], g["opp"], g["date"], g["start_utc"])
        if feat["n_hist"] >= 3:
            pr = J.project(feat, params)
            out.append((g["pk"], st["pid"], round(pr["expected_outs"], 9), round(pr["expected_strikeouts"], 9),
                        round(pr["expected_batters_faced"], 9)))
    return out


# ---------------------------------------------------------------- tests

def test_feature_names_and_source_globs_are_sports_only():
    for k in J.FEATURE_KEYS + J.DESIGN_NAMES:
        assert not any(f in k.lower() for f in FORBIDDEN), k
    for g in S.SOURCE_GLOBS:
        assert g.split(os.sep)[0] in ("research_cache", "statcast_raw"), g
        assert not any(f in g.lower() for f in FORBIDDEN), g
    src = inspect.getsource(S) + inspect.getsource(J)
    for bad in ("edgelab", "settlements", "observations", "kalshi_", "odds.json"):
        assert f'"{bad}' not in src and f"'{bad}" not in src


def test_thresholds_monotone_and_families_coherent():
    feat = {k: None for k in J.FEATURE_KEYS}
    feat.update({"n_hist": 8, "season_starts": 8, "days_rest": 5, "w_bf": 23.0, "w_outs": 17.0, "w_pitches": 92.0,
                 "last_pitches": 95, "last_bf": 24, "max_pitches_last3": 99, "pitches_per_bf": (1500, 390),
                 "k_rate_hist_bf": 200, "k_rate_hist_k": 50, "outs_per_bf_hist_bf": 200, "outs_per_bf_hist_outs": 140,
                 "bullpen_pitches_3d": 120, "bullpen_pitches_1d": 40, "opp_k_rate": 0.23, "opp_nonout_rate": 0.30,
                 "opp_pa": 3000})
    pr = J.project(feat)
    for pmf in (pr["outs_pmf"], pr["bf_pmf"], pr["k_pmf"]):
        assert abs(sum(pmf) - 1) < 1e-9 and min(pmf) >= 0
        t = J.tail(pmf)
        assert all(t[i] >= t[i + 1] - 1e-12 for i in range(len(t) - 1))
        assert J.quantile(pmf, 0.1) <= J.quantile(pmf, 0.5) <= J.quantile(pmf, 0.9)
    assert pr["expected_batters_faced"] > pr["expected_outs"] > pr["expected_strikeouts"]
    # short leash (low pitch history, short rest) lowers outs, BF and K together
    short = dict(feat, w_pitches=60.0, w_outs=11.0, last_pitches=55, max_pitches_last3=62, days_rest=3)
    ps = J.project(short)
    assert ps["expected_outs"] < pr["expected_outs"]
    assert ps["expected_batters_faced"] < pr["expected_batters_faced"]
    assert ps["expected_strikeouts"] < pr["expected_strikeouts"]


def test_features_are_strictly_as_of(tmp_path):
    root = str(tmp_path)
    _write_tree(root)
    games = J.link_mirrors(S.load_pitching_games(root))
    idx = J.AsOfIndex(games)
    g = [x for x in games if x["date"] == "2026-08-20" and x["side"] == "away"][0]
    feat, obs = J.features(idx, g["starter"]["pid"], g["team"], g["opp"], g["date"], "2026-08-20T22:05:00Z")
    used = idx.prior_starts(g["starter"]["pid"], g["date"])
    assert all(u["date"] < "2026-08-20" for u in used) and feat["n_hist"] == len(used[-12:])
    assert obs <= "2026-08-20T22:05:00Z"
    # mutating the projected game itself or any later game changes nothing
    before = J.features(idx, g["starter"]["pid"], g["team"], g["opp"], g["date"], "2026-08-20T22:05:00Z")
    for x in games:
        if x["date"] >= "2026-08-20":
            x["starter"]["outs"] = 27
            x["relief_pitches"] = 999
    after = J.features(J.AsOfIndex(games), g["starter"]["pid"], g["team"], g["opp"], g["date"], "2026-08-20T22:05:00Z")
    assert before == after
    # a game dated earlier but completed after the cutoff (suspended / resumed) is excluded
    late = used[-1]
    late["observed_at"] = "2026-08-25T04:00:00Z"
    f2, o2 = J.features(J.AsOfIndex(games), g["starter"]["pid"], g["team"], g["opp"], g["date"], "2026-08-20T22:05:00Z")
    assert f2["n_hist"] == feat["n_hist"] - 1 and o2 < "2026-08-25"


def test_runtime_market_mutation_synthetic(tmp_path):
    """Challenger rerun is bit-identical with the settlement file present, removed or
    randomized; the champion's settlement-reading postseason path (negative control)
    changes under the same mutation."""
    root = str(tmp_path / "data")
    _write_tree(root)
    _write_settlements(root, actual=18)
    base = _project_all(root)
    assert base
    os.remove(os.path.join(root, "edgelab", "settlements", "2026-10-01.jsonl.gz"))
    removed = _project_all(root)
    _write_settlements(root, actual=3)
    randomized = _project_all(root)
    assert base == removed == randomized

    def control(actual):
        rows = [{"marketFamily": "pitcher_outs", "marketTicker": "KXMLBOUTS-26SEP301900NYYBOS-NYYP1-15",
                 "settlementEvidence": {"actualValue": actual, "candidates": [{"playerId": 101}]}}] if actual else []
        hist = [{"pid": "101", "date": f"2026-09-{d:02d}", "outs": 18, "bf": 25, "k": 6} for d in (5, 11, 17, 23)]
        ctx = MP.PitcherContext({}, hist, [], MP.postseason_starts_from_settlements(rows))
        return PP.project(ctx.history("101", "2026-10-03"), {}, postseason=True)["expected_outs"]

    assert control(18) != control(None) != control(3)


def test_fit_on_synthetic_rows_is_finite(tmp_path):
    root = str(tmp_path)
    _write_tree(root, n_days=30)
    games = J.link_mirrors(S.load_pitching_games(root))
    idx = J.AsOfIndex(games)
    rows = []
    for g in games:
        f, _ = J.features(idx, g["starter"]["pid"], g["team"], g["opp"], g["date"], g["start_utc"])
        if f["n_hist"] >= 1:
            rows.append({"feat": f, "bf": g["starter"]["bf"], "outs": g["starter"]["outs"], "k": g["starter"]["k"]})
    params = J.fit(rows, league=J.league_constants(games, {"2026"}))
    assert len(params["mean_coef"]["beta"]) == len(J.DESIGN_NAMES)
    pr = J.project(rows[-1]["feat"], params)
    assert 0 < pr["expected_outs"] < 27 and abs(sum(pr["outs_pmf"]) - 1) < 1e-9


@pytest.mark.parametrize("bad", ["market_center", "kalshi_quotes"])
def test_project_ignores_unknown_feature_keys(bad):
    feat = {k: None for k in J.FEATURE_KEYS}
    feat.update({"n_hist": 5, "season_starts": 5, "days_rest": 5, "w_bf": 22.0, "w_outs": 16.0, "w_pitches": 88.0,
                 "last_pitches": 90, "last_bf": 22, "max_pitches_last3": 95, "pitches_per_bf": (800, 210),
                 "k_rate_hist_bf": 110, "k_rate_hist_k": 25, "outs_per_bf_hist_bf": 110, "outs_per_bf_hist_outs": 78,
                 "bullpen_pitches_3d": 0, "bullpen_pitches_1d": 0, "opp_k_rate": None, "opp_nonout_rate": None,
                 "opp_pa": 0})
    a = J.project(feat)
    b = J.project(dict(feat, **{bad: 0.99}))
    assert a["outs_pmf"] == b["outs_pmf"] and a["k_pmf"] == b["k_pmf"]
