#!/usr/bin/env python3
"""
tests/edgelab/test_settle_markets_script.py
================================================
Coverage for scripts/edgelab/settle_markets.py's linescore-to-game-outcome
parsing (the one piece of settlement wiring that isn't already covered
by tests/edgelab/test_settlement.py's pure settle_market() tests).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "settle_markets_script",
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", "settle_markets.py"),
)
settle_markets_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(settle_markets_script)


def _linescore(away_runs, home_runs, innings):
    return {
        "teams": {"away": {"runs": away_runs}, "home": {"runs": home_runs}},
        "innings": innings,
    }


def test_build_game_outcome_extracts_full_and_period_scores():
    innings = [
        {"num": 1, "away": {"runs": 1}, "home": {"runs": 0}},
        {"num": 2, "away": {"runs": 0}, "home": {"runs": 2}},
        {"num": 3, "away": {"runs": 0}, "home": {"runs": 0}},
        {"num": 4, "away": {"runs": 1}, "home": {"runs": 0}},
        {"num": 5, "away": {"runs": 0}, "home": {"runs": 1}},
        {"num": 6, "away": {"runs": 1}, "home": {"runs": 0}},
        {"num": 7, "away": {"runs": 0}, "home": {"runs": 0}},
    ]
    linescore = _linescore(3, 3, innings)
    outcome = settle_markets_script.build_game_outcome_from_linescore(linescore, "Final")
    assert outcome["awayRuns"] == 3
    assert outcome["homeRuns"] == 3
    assert outcome["completedInnings"] == 7
    assert outcome["firstInningRuns"] == (1, 0)
    assert outcome["periodScores"]["F5"] == (2, 3)
    assert outcome["periodScores"]["F3"] == (1, 2)
    assert outcome["periodScores"]["F7"] == (3, 3)


def test_build_game_outcome_returns_none_for_missing_linescore():
    assert settle_markets_script.build_game_outcome_from_linescore(None, "Final") is None
