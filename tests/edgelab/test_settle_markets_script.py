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


def test_cancelled_bets_are_excluded_from_settlement(tmp_path, monkeypatch):
    """
    Maintainer review regression: a CANCELLED bet (logged in error) must
    never gain a result/netProfitLoss from a settlement run, and must
    never become a Settlement record's representative betId -- it isn't
    a real wager.
    """
    monkeypatch.chdir(tmp_path)
    from lib.edgelab import storage

    date = "2026-08-03"
    ticker = "KXMLBGAME-TEST-DET"
    game_id = "2026-08-03_DET_ATH"

    storage.write_all_records(storage.partition_path("games", date), [{
        "gameId": game_id, "mlbGamePk": 999999, "awayTeam": "DET", "homeTeam": "ATH", "status": "Final",
    }])
    storage.write_all_records(storage.partition_path("markets", date), [{
        "marketTicker": ticker, "gameId": game_id, "marketFamily": "game_result",
        "marketHorizon": "FULL_GAME", "team": "DET", "outcomeLabel": "Win",
    }])

    active_bet = {
        "betId": "active-bet", "marketTicker": ticker, "side": "YES", "stake": 10.0,
        "entryPrice": 0.5, "status": "pending", "recordStatus": "ACTIVE",
    }
    cancelled_bet = {
        "betId": "cancelled-bet", "marketTicker": ticker, "side": "YES", "stake": 999.0,
        "entryPrice": 0.5, "status": "pending", "recordStatus": "CANCELLED",
    }
    storage.write_all_records(storage.singleton_path("bets", "bets.jsonl"), [active_bet, cancelled_bet])

    monkeypatch.setattr(
        settle_markets_script, "fetch_mlb_linescore",
        lambda game_pk: {"teams": {"away": {"runs": 5}, "home": {"runs": 2}}, "innings": []},
    )
    monkeypatch.setattr(sys, "argv", ["settle_markets.py", "--date", date])
    exit_code = settle_markets_script.main()
    assert exit_code == 0

    rows = {r["betId"]: r for r in storage.read_records(storage.singleton_path("bets", "bets.jsonl"))}
    assert rows["active-bet"]["status"] == "settled"
    assert rows["active-bet"]["result"] == "WIN"  # DET (away) won 5-2
    assert rows["cancelled-bet"]["status"] == "pending"  # untouched
    assert rows["cancelled-bet"].get("result") is None

    settlements = list(storage.read_records(storage.partition_path("settlements", date)))
    assert settlements[0]["betId"] == "active-bet"  # never the cancelled bet
