#!/usr/bin/env python3
"""
tests/edgelab/test_settlement.py
====================================
Coverage for lib/edgelab/settlement.py: win/loss/void, three-way F3/F5/F7
tie handling, unimplemented player props, and unbet-market hypothetical
returns.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.settlement import (
    derive_bet_result,
    hypothetical_yes_return,
    realized_return_for_bet,
    settle_bets_for_ticker,
    settle_market,
)

GAME_RESULT = "game_result"
INNING_RESULT = "inning_result"
GAME_TOTAL = "game_total"
INNING_TOTAL = "inning_total"
TEAM_TOTAL = "team_total"
WINNING_MARGIN = "winning_margin"
FIRST_INNING_RUN = "first_inning_run"


def test_moneyline_away_ticker_wins():
    market = {"marketFamily": GAME_RESULT, "team": "PIT", "outcomeLabel": "Win"}
    outcome = {"awayRuns": 5, "homeRuns": 3, "awayAbbr": "PIT", "homeAbbr": "CIN", "gameStatus": "Final"}
    status, result, reason = settle_market(market, outcome)
    assert (status, result, reason) == ("SETTLED", "YES", None)


def test_moneyline_home_ticker_loses_when_away_wins():
    market = {"marketFamily": GAME_RESULT, "team": "CIN", "outcomeLabel": "Win"}
    outcome = {"awayRuns": 5, "homeRuns": 3, "awayAbbr": "PIT", "homeAbbr": "CIN", "gameStatus": "Final"}
    status, result, reason = settle_market(market, outcome)
    assert (status, result, reason) == ("SETTLED", "NO", None)


def test_void_on_postponed_game():
    market = {"marketFamily": GAME_RESULT, "team": "PIT", "outcomeLabel": "Win"}
    outcome = {"gameStatus": "Postponed"}
    status, result, reason = settle_market(market, outcome)
    assert (status, result, reason) == ("VOID", None, None)


def test_missing_final_score_is_unresolved_not_guessed():
    market = {"marketFamily": GAME_RESULT, "team": "PIT", "outcomeLabel": "Win"}
    outcome = {"awayRuns": None, "homeRuns": None, "gameStatus": "In Progress"}
    status, result, reason = settle_market(market, outcome)
    assert status == "SETTLEMENT_UNRESOLVED"
    assert reason == "missing_final_score"
    assert result is None


def test_f5_tie_ticker_settles_yes_on_actual_tie():
    market = {"marketFamily": INNING_RESULT, "marketHorizon": "F5", "outcomeLabel": "Tie"}
    outcome = {"periodScores": {"F5": (2, 2)}, "completedInnings": 5, "gameStatus": "Final"}
    status, result, reason = settle_market(market, outcome)
    assert (status, result, reason) == ("SETTLED", "YES", None)


def test_f5_team_ticker_settles_no_when_actually_tied():
    market = {"marketFamily": INNING_RESULT, "marketHorizon": "F5", "team": "PIT", "outcomeLabel": "Win"}
    outcome = {"periodScores": {"F5": (2, 2)}, "awayAbbr": "PIT", "homeAbbr": "CIN", "completedInnings": 5, "gameStatus": "Final"}
    status, result, reason = settle_market(market, outcome)
    assert (status, result, reason) == ("SETTLED", "NO", None)


def test_f3_and_f7_three_way_settle_same_as_f5_once_confirmed():
    # lib.research.market_taxonomy.HORIZON_MARKET_STATUS currently marks
    # F3/F5/F7 all CONFIRMED_THREE_WAY (see that module for the evidence);
    # settle_inning_result() dispatches on that single source of truth, so
    # F3/F7 settle exactly like F5 today, with no per-horizon special case
    # in this module. If a future Kalshi change ever retracts F3/F7's
    # confirmation, HORIZON_MARKET_STATUS flips back to
    # SETTLEMENT_UNRESOLVED/"structure_unverified" with no change needed here.
    for horizon in ("F3", "F5", "F7"):
        market = {"marketFamily": INNING_RESULT, "marketHorizon": horizon, "team": "PIT", "outcomeLabel": "Win"}
        outcome = {"periodScores": {horizon: (3, 1)}, "awayAbbr": "PIT", "homeAbbr": "CIN", "completedInnings": 9, "gameStatus": "Final"}
        status, result, reason = settle_market(market, outcome)
        assert (status, result, reason) == ("SETTLED", "YES", None)


def test_player_props_are_explicitly_unimplemented_not_fabricated():
    for family in ("pitcher_strikeouts", "hitter_hits", "hitter_total_bases", "hitter_rbis"):
        market = {"marketFamily": family, "threshold": 5.5}
        outcome = {"gameStatus": "Final"}
        status, result, reason = settle_market(market, outcome)
        assert status == "SETTLEMENT_UNRESOLVED"
        assert reason == "player_prop_settlement_not_implemented"
        assert result is None


def test_team_total_and_winning_margin_and_game_total():
    outcome = {"awayRuns": 6, "homeRuns": 2, "awayAbbr": "PIT", "homeAbbr": "CIN", "gameStatus": "Final"}
    assert settle_market({"marketFamily": TEAM_TOTAL, "team": "PIT", "threshold": 3.5}, outcome)[:2] == ("SETTLED", "YES")
    assert settle_market({"marketFamily": TEAM_TOTAL, "team": "CIN", "threshold": 3.5}, outcome)[:2] == ("SETTLED", "NO")
    assert settle_market({"marketFamily": WINNING_MARGIN, "team": "PIT", "threshold": 2.5}, outcome)[:2] == ("SETTLED", "YES")
    assert settle_market({"marketFamily": GAME_TOTAL, "threshold": 7.5}, outcome)[:2] == ("SETTLED", "YES")
    assert settle_market({"marketFamily": GAME_TOTAL, "threshold": 8.5}, outcome)[:2] == ("SETTLED", "NO")


def test_first_inning_run_yes_no():
    assert settle_market({"marketFamily": FIRST_INNING_RUN}, {"firstInningRuns": (1, 0)})[:2] == ("SETTLED", "YES")
    assert settle_market({"marketFamily": FIRST_INNING_RUN}, {"firstInningRuns": (0, 0)})[:2] == ("SETTLED", "NO")
    status, result, reason = settle_market({"marketFamily": FIRST_INNING_RUN}, {"firstInningRuns": None})
    assert status == "SETTLEMENT_UNRESOLVED"


def test_derive_bet_result_win_loss():
    assert derive_bet_result("YES", "YES") == "WIN"
    assert derive_bet_result("NO", "YES") == "LOSS"
    assert derive_bet_result("YES", "NO") == "LOSS"
    assert derive_bet_result(None, "YES") is None  # not yet settled -- never guessed


def test_realized_return_win_loss_void():
    assert realized_return_for_bet(10.0, 0.5, "WIN") == 10.0  # 10 * (1/0.5 - 1) = 10
    assert realized_return_for_bet(10.0, 0.5, "LOSS") == -10.0
    assert realized_return_for_bet(10.0, 0.5, "PUSH") == 0.0
    assert realized_return_for_bet(10.0, 0.5, "VOID") == 0.0
    assert realized_return_for_bet(10.0, None, "WIN") is None  # never fabricate without an entry price


def test_settle_bets_for_ticker_settles_every_bet_not_just_the_first():
    """
    A ticker can carry multiple bet tranches (see
    tests/edgelab/test_bets.py::test_multiple_bets_on_one_market_get_distinct_ids).
    Every one of them must be settled -- a prior version of
    scripts/edgelab/settle_markets.py only ever settled matching_bets[0],
    silently leaving every additional tranche pending forever.
    """
    bets = [
        {"betId": "b1", "side": "YES", "stake": 10.0, "entryPrice": 0.5},
        {"betId": "b2", "side": "YES", "stake": 5.0, "entryPrice": 0.4},
        {"betId": "b3", "side": "NO", "stake": 3.0, "entryPrice": 0.6},
    ]
    updated = settle_bets_for_ticker(bets, "SETTLED", "YES")
    assert len(updated) == 3
    by_id = {b["betId"]: b for b in updated}
    assert by_id["b1"]["result"] == "WIN"
    assert by_id["b1"]["status"] == "settled"
    assert by_id["b1"]["netProfitLoss"] == 10.0
    assert by_id["b2"]["result"] == "WIN"
    assert by_id["b2"]["netProfitLoss"] == 7.5
    assert by_id["b3"]["result"] == "LOSS"  # bought NO, market settled YES
    assert by_id["b3"]["netProfitLoss"] == -3.0


def test_settle_bets_for_ticker_leaves_bets_untouched_when_not_settled():
    bets = [{"betId": "b1", "side": "YES", "stake": 10.0, "entryPrice": 0.5}]
    assert settle_bets_for_ticker(bets, "VOID", None) == []
    assert settle_bets_for_ticker(bets, "SETTLEMENT_UNRESOLVED", None) == []


def test_settle_bets_for_ticker_does_not_mutate_input():
    original = {"betId": "b1", "side": "YES", "stake": 10.0, "entryPrice": 0.5, "status": "pending"}
    bets = [original]
    settle_bets_for_ticker(bets, "SETTLED", "YES")
    assert original["status"] == "pending"  # input list/dicts must not be mutated in place


def test_hypothetical_return_for_unbet_market_uses_price_not_just_win_rate():
    # A cheap YES win pays much more than an expensive YES win -- must be price-dependent.
    cheap_win = hypothetical_yes_return(0.20, "YES")
    expensive_win = hypothetical_yes_return(0.80, "YES")
    assert cheap_win == 4.0     # (1-0.2)/0.2
    assert expensive_win == 0.25  # (1-0.8)/0.8
    assert cheap_win > expensive_win
    assert hypothetical_yes_return(0.5, "NO") == -1.0
    assert hypothetical_yes_return(0.5, None) == 0.0
    assert hypothetical_yes_return(None, "YES") is None
