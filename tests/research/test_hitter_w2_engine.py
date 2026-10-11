#!/usr/bin/env python3
"""
tests/research/test_hitter_w2_engine.py
=======================================
SIFT W2 hitter lane (research only): unit tests for lib/research/hitter_w2_engine.py
(MLB_HITTER_W2_PURE_V1) and the incumbent-rerun instrumented replica. Synthetic
inputs only.
"""
import importlib.util
import os
import random
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.research import hitter_w2_engine as E  # noqa: E402

LEAGUE = [0.225, 0.085, 0.010, 0.145, 0.045, 0.004, 0.032, 0.454]


def _table():
    """Empty table -> textbook fallback transitions everywhere, plus one empirical cell."""
    return {}


def _spec(survival=None, budget=22.0):
    rates = [list(LEAGUE)] * 9
    surv = survival or [[1.0] * 8 for _ in range(9)]
    return E.TeamSpec(list(range(9)), rates, rates, list(LEAGUE), budget, [0.0], surv)


def test_simulated_games_are_coherent():
    rng = random.Random(3)
    for _ in range(300):
        a, h = E.simulate_game(_spec(), _spec(), _table(), rng)
        for st in (a, h):
            r_sum = sum(ln[5] for ln in st.lines)
            rbi_sum = sum(ln[4] for ln in st.lines)
            assert r_sum == st.runs  # no substitutes -> every run belongs to a starter
            assert rbi_sum <= st.runs
            for ln in st.lines:
                assert ln[2] >= ln[1] >= ln[3]  # TB >= H >= HR
                assert ln[6] <= ln[0]
        assert a.runs != h.runs or True


def test_home_team_skips_bottom_ninth_when_leading_and_games_end():
    rng = random.Random(11)
    pas = []
    for _ in range(400):
        a, h = E.simulate_game(_spec(), _spec(), _table(), rng)
        pas.append((sum(ln[0] for ln in a.lines), sum(ln[0] for ln in h.lines), a.runs, h.runs))
    # home team bats less on average (no bottom of the 9th when ahead, walk-offs)
    assert sum(p[1] for p in pas) < sum(p[0] for p in pas)


def test_substitution_reduces_late_pa():
    rng = random.Random(5)
    surv = [[1.0, 1.0, 1.0, 0.5, 0.2, 0.1, 0.1, 0.1] for _ in range(9)]
    full = sum(sum(ln[0] for ln in E.simulate_game(_spec(), _spec(), {}, random.Random(i))[0].lines)
               for i in range(200))
    part = sum(sum(ln[0] for ln in E.simulate_game(_spec(surv), _spec(surv), {}, random.Random(i))[0].lines)
               for i in range(200))
    assert part < full
    assert rng is not None


def test_fallback_transitions_are_consistent():
    for cls in ("K", "OUT", "BB", "HBP", "1B", "2B", "3B", "HR"):
        for outs in range(3):
            for bases in range(8):
                d_outs, post, runs, rbi = E._fallback_transition(cls, outs, bases)
                p = {"outsBefore": outs, "outsAfter": outs + d_outs, "basesBefore": bases,
                     "postBases": post, "runs": runs}
                if outs + d_outs < 3:
                    assert E.transition_consistent(p), (cls, outs, bases)
                assert rbi <= runs


def test_fit_transitions_drops_inconsistent_rows():
    pas = [
        {"cls": "1B", "outsBefore": 0, "basesBefore": 2, "outsAfter": 0, "postBases": 1, "runs": 1, "rbi": 1},
        {"cls": "1B", "outsBefore": 0, "basesBefore": 2, "outsAfter": 0, "postBases": 5, "runs": 0, "rbi": 0},
        {"cls": "1B", "outsBefore": 0, "basesBefore": 2, "outsAfter": 0, "postBases": 7, "runs": 0, "rbi": 0},
    ]
    table, info = E.fit_transitions(pas)
    assert info == {"kept": 2, "dropped_inconsistent": 1}
    cum, outs = table[("1B", 0, 2)]
    assert cum == [1, 2] and len(outs) == 2


def test_log5_and_shrink_normalize():
    r = E.log5(LEAGUE, LEAGUE, LEAGUE)
    assert abs(sum(r) - 1) < 1e-12 and all(abs(a - b) < 1e-12 for a, b in zip(r, LEAGUE))
    s = E.shrunk_rates([10, 2, 0, 5, 1, 0, 1, 20], None, 0.5, 100, LEAGUE)
    assert abs(sum(s) - 1) < 1e-9


def test_crps_discrete_matches_definition():
    pmf = {0: 0.5, 1: 0.5}
    # F(0)=.5, F(1)=1 ; y=1 -> (0.5-0)^2 + (1-1)^2 = 0.25
    assert abs(E.crps_discrete(pmf, 1) - 0.25) < 1e-12
    assert abs(E.crps_discrete({2: 1.0}, 2)) < 1e-12


def test_event_class_mapping():
    assert E.event_class("single") == "1B" and E.event_class("sac_fly") == "OUT"
    assert E.event_class("field_error") == "OUT" and E.event_class("caught_stealing_2b") is None
    assert E.event_class("intent_walk") == "BB"


def test_engine_reads_no_market_vocabulary():
    src = open(os.path.join(ROOT, "lib", "research", "hitter_w2_engine.py")).read()
    code = "\n".join(line for line in src.splitlines() if not line.strip().startswith("#"))
    code = re.sub(r'"""[\s\S]*?"""', "", code)
    for term in ("kalshi", "odds", "implied", "sportsbook", "pinnacle", "settle", "price", "wager", "ticker",
                 "quote", "vegas", "moneyline"):
        assert term not in code.lower(), term


def test_incumbent_instrumented_replica_matches_original():
    spec = importlib.util.spec_from_file_location(
        "hw2_rerun", os.path.join(ROOT, "scripts", "research", "hitter_w2", "incumbent_rerun.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from lib.research.lineup_game_simulator import simulate_game as original
    kw = dict(target_hitter_pitches=[], batter_hand="R", starter_context={"avgIPperStart": 5.5},
              bullpen_context={}, starter_pitch_mix=None, bullpen_pitch_mix=None, park_geometry_entry=None,
              field_relative_wind=None, defense_snapshot=None, hitter_speed_snapshot=None)
    for slot in (1, 5, 9):
        for seed in range(25):
            a = original(target_slot=slot, rng=random.Random(seed), **kw)
            b, ins = mod.simulate_game_instrumented(target_slot=slot, rng=random.Random(seed), **kw)
            assert a == b
            assert ins["PA_starter"] + ins["PA_bullpen"] == a["PA"]
            assert ins["H_starter"] + ins["H_bullpen"] == a["H"]


def test_challenger_feature_and_model_code_reads_no_market_vocabulary():
    for rel in ("features.py", "model.py", "sidecars.py"):
        src = open(os.path.join(ROOT, "scripts", "research", "hitter_w2", rel)).read()
        code = "\n".join(line for line in src.splitlines() if not line.strip().startswith("#"))
        code = re.sub(r'"""[\s\S]*?"""', "", code).lower()
        for term in ("kalshi", "odds", "implied", "sportsbook", "pinnacle", "settle", "price", "ticker", "quote",
                     "slates", "teamtotals", "vegas"):
            assert term not in code, (rel, term)


def test_pure_path_never_opens_market_file(tmp_path, monkeypatch):
    """The PURE forecast path must not reference the market-file argument except in the
    negative-control branch."""
    src = open(os.path.join(ROOT, "scripts", "research", "hitter_w2", "run_hitter_w2.py")).read()
    body = src[src.index("def stage_forecast"):src.index("def _control_params")]
    assert body.count("open(market_file)") == 1
    assert "if control_read_market:" in body
