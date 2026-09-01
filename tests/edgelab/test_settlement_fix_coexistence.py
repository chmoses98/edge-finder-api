"""
Coexistence guard for the two settlement fixes that landed separately and
BOTH edit lib/edgelab/settlement.py::settle_market:

  PR #175 -- integer total ladders settle ">= N" (N or more)
  PR #176 -- a period-scoped market grades on periodScores[horizon]

A merge that silently preserved only one of them would be invisible in a
diff review, so every property is asserted explicitly here.
"""

from lib.edgelab.settlement import settle_market

GAME_TOTAL = "game_total"
INNING_TOTAL = "inning_total"
TEAM_TOTAL = "team_total"
WINNING_MARGIN = "winning_margin"


def test_kxmlbtotal_exact_integer_boundary_is_yes():
    """#175: full-game total of exactly N settles YES on rung N."""
    outcome = {"awayRuns": 4, "homeRuns": 5, "gameStatus": "Final"}  # 9
    assert settle_market({"marketFamily": GAME_TOTAL, "marketHorizon": "FULL_GAME",
                          "threshold": 9}, outcome)[:2] == ("SETTLED", "YES")
    assert settle_market({"marketFamily": GAME_TOTAL, "marketHorizon": "FULL_GAME",
                          "threshold": 10}, outcome)[:2] == ("SETTLED", "NO")


def test_kxmlbf5total_exact_f5_boundary_is_yes_on_the_f5_score():
    """#175 + #176 together: the >= rule applied to the F5 period score,
    not to the full-game total."""
    outcome = {"awayRuns": 9, "homeRuns": 8, "gameStatus": "Final",
               "periodScores": {"F5": (1, 1)}}          # F5 total 2, game total 17
    m = {"marketFamily": INNING_TOTAL, "marketHorizon": "F5", "threshold": 2}
    assert settle_market(m, outcome)[:2] == ("SETTLED", "YES")
    m3 = {"marketFamily": INNING_TOTAL, "marketHorizon": "F5", "threshold": 3}
    assert settle_market(m3, outcome)[:2] == ("SETTLED", "NO")


def test_kxmlbf5spread_grades_from_the_f5_period_score():
    """#176: F5 margin, never the full-game margin."""
    outcome = {"awayRuns": 7, "homeRuns": 3, "awayAbbr": "PIT", "homeAbbr": "CIN",
               "gameStatus": "Final", "periodScores": {"F5": (1, 2)}}
    f5 = {"marketFamily": WINNING_MARGIN, "marketHorizon": "F5",
          "team": "PIT", "threshold": 1.5}
    assert settle_market(f5, outcome)[:2] == ("SETTLED", "NO")


def test_missing_f5_period_score_is_unresolved_never_full_game():
    outcome = {"awayRuns": 7, "homeRuns": 3, "awayAbbr": "PIT", "homeAbbr": "CIN",
               "gameStatus": "Final", "periodScores": {}}
    for family, extra in ((WINNING_MARGIN, {"team": "PIT", "threshold": 1.5}),
                          (INNING_TOTAL, {"threshold": 2})):
        market = dict({"marketFamily": family, "marketHorizon": "F5"}, **extra)
        status, result, reason = settle_market(market, outcome)
        assert (status, result) == ("SETTLEMENT_UNRESOLVED", None)
        assert reason == "missing_period_score_F5"


def test_full_game_winning_margin_remains_full_game():
    outcome = {"awayRuns": 7, "homeRuns": 3, "awayAbbr": "PIT", "homeAbbr": "CIN",
               "gameStatus": "Final", "periodScores": {"F5": (1, 2)}}
    full = {"marketFamily": WINNING_MARGIN, "marketHorizon": "FULL_GAME",
            "team": "PIT", "threshold": 1.5}
    assert settle_market(full, outcome)[:2] == ("SETTLED", "YES")


def test_no_full_game_fallback_for_an_explicit_period_horizon():
    """The exact defect: a period market must NOT borrow the full-game score
    when its own period score is absent, even though the full-game score is
    right there in the outcome."""
    outcome = {"awayRuns": 12, "homeRuns": 0, "awayAbbr": "PIT", "homeAbbr": "CIN",
               "gameStatus": "Final"}
    for horizon in ("F3", "F5", "F7"):
        m = {"marketFamily": WINNING_MARGIN, "marketHorizon": horizon,
             "team": "PIT", "threshold": 1.5}
        status, result, _ = settle_market(m, outcome)
        assert (status, result) == ("SETTLEMENT_UNRESOLVED", None), horizon


def test_half_point_families_unaffected_by_the_ge_change():
    outcome = {"awayRuns": 6, "homeRuns": 2, "awayAbbr": "PIT", "homeAbbr": "CIN",
               "gameStatus": "Final"}
    assert settle_market({"marketFamily": TEAM_TOTAL, "team": "PIT",
                          "threshold": 5.5}, outcome)[:2] == ("SETTLED", "YES")
    assert settle_market({"marketFamily": TEAM_TOTAL, "team": "PIT",
                          "threshold": 6.5}, outcome)[:2] == ("SETTLED", "NO")
