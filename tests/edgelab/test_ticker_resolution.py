#!/usr/bin/env python3
"""
tests/edgelab/test_ticker_resolution.py
===========================================
Timestamp-Optional Manual Imports milestone: resolving a marketTicker
from game + family + threshold + participant/side when a bulk import row
doesn't already know the exact ticker. Ambiguous matches must refuse to
resolve; unmatched rows return NOT_FOUND, never a guess.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.ticker_resolution import AMBIGUOUS, NOT_FOUND, RESOLVED, resolve_ticker

GAMES = [
    {"gameId": "G1", "awayTeam": "SF", "homeTeam": "LAD"},
    {"gameId": "G2", "awayTeam": "NYY", "homeTeam": "BOS"},
]

MARKETS = [
    {"marketTicker": "SF-F5-ML", "gameId": "G1", "marketFamily": "game_result", "marketHorizon": "F5", "team": "SF", "threshold": None},
    {"marketTicker": "LAD-F5-ML", "gameId": "G1", "marketFamily": "game_result", "marketHorizon": "F5", "team": "LAD", "threshold": None},
    {"marketTicker": "SF-TT-OVER-3.5", "gameId": "G1", "marketFamily": "team_total", "marketHorizon": "FULL_GAME", "team": "SF", "threshold": 3.5},
    {"marketTicker": "SF-TT-OVER-4.5", "gameId": "G1", "marketFamily": "team_total", "marketHorizon": "FULL_GAME", "team": "SF", "threshold": 4.5},
    {"marketTicker": "NYY-FULL-ML", "gameId": "G2", "marketFamily": "game_result", "marketHorizon": "FULL_GAME", "team": "NYY", "threshold": None},
]


def test_resolves_unique_match():
    ticker, status, candidates = resolve_ticker(MARKETS, GAMES, away="SF", home="LAD", market_family="game_result", market_horizon="F5", team="SF")
    assert status == RESOLVED
    assert ticker == "SF-F5-ML"
    assert candidates == ["SF-F5-ML"]


def test_ambiguous_when_threshold_not_supplied_for_alt_lines():
    ticker, status, candidates = resolve_ticker(MARKETS, GAMES, away="SF", home="LAD", market_family="team_total", team="SF")
    assert status == AMBIGUOUS
    assert ticker is None
    assert set(candidates) == {"SF-TT-OVER-3.5", "SF-TT-OVER-4.5"}


def test_threshold_disambiguates_alt_lines():
    ticker, status, candidates = resolve_ticker(MARKETS, GAMES, away="SF", home="LAD", market_family="team_total", team="SF", threshold=4.5)
    assert status == RESOLVED
    assert ticker == "SF-TT-OVER-4.5"


def test_not_found_for_nonexistent_game():
    ticker, status, candidates = resolve_ticker(MARKETS, GAMES, away="ZZZ", home="YYY", market_family="game_result")
    assert status == NOT_FOUND
    assert candidates == []


def test_refuses_to_resolve_with_no_discriminating_filters_at_all():
    ticker, status, candidates = resolve_ticker(MARKETS, GAMES)
    assert status == AMBIGUOUS
    assert candidates == []


def test_never_silently_picks_first_candidate():
    """Explicitly documents the never-guess contract: two candidates always AMBIGUOUS, never candidates[0]."""
    ticker, status, candidates = resolve_ticker(MARKETS, GAMES, away="SF", home="LAD", market_family="team_total")
    assert status == AMBIGUOUS
    assert ticker is None
