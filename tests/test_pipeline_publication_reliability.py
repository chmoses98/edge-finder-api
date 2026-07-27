#!/usr/bin/env python3
"""
tests/test_pipeline_publication_reliability.py
================================================
Regression tests for the 2026-07-25 / 2026-07-26 incident:

  data/fetch_status.json reported a successful fetch (status=OK, requestedDate
  == actualDate == the current slate date) while data/meta.json remained
  stale from an earlier successful run days prior.

Root cause (confirmed from GitHub Actions run logs for runs 30175591673,
30175861494, 30211684499):

  scripts/write_pending_bets.py correctly refuses to log a real-money bet
  for a game whose status is live ("In Progress") or final ("Final") —
  required by the "never recommend a live or completed game" rule.

  scripts/validate_bet_logging.py, however, computed its "expected
  real-money bets" list directly from marketLedger's Accepted/HIGH|MEDIUM
  rows with NO awareness of game status. It therefore expected a bet that
  write_pending_bets.py had correctly and deliberately skipped, treated the
  gap as a hard failure, and exited 1 — killing the GitHub Actions job
  before it reached the "Write meta and commit" step. fetch_status.json had
  already been committed unconditionally (if: always()) minutes earlier,
  so the run *looked* successful in fetch_status.json while meta.json/
  slate.json/bets.json for that date were never published.

  scripts/risk_gate.py had the same gap in its portfolio-composition tally:
  it counted stake from live/final games that would never actually be
  logged, distorting its GO/PAPER_ONLY decision.

These tests reproduce the exact July 25/26 scenario and assert both scripts
now treat a correctly-excluded live/final game as expected — a live game's
unlogged bet must NOT be treated as a missing bet.
"""

import json
import os
import sys
import importlib

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'lib'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import validate_bet_logging   # noqa: E402
import risk_gate               # noqa: E402


def _ledger_entry(market, status='Accepted', tier='MEDIUM', ticker=None, edge=3.0):
    return {
        'market': market,
        'status': status,
        'confidence': tier,
        'confidenceTier': tier,
        'edge': edge,
        'calibratedEdgeVsExecutable': edge,
        'betSize': 4.0,
        'ticker': ticker or f'KXMLBGAME-26JUL26TESTGAME-{market}',
        'kalshiPrice': -110,
    }


def _game(away, home, status, ledger_entries, scheduled_start='2026-07-26T17:05:00Z'):
    return {
        'away': {'abbr': away},
        'home': {'abbr': home},
        'status': status,
        'scheduledStartTime': scheduled_start,
        'marketLedger': ledger_entries,
    }


def _write_slate_and_bets(tmp_path, slate, bets):
    slate_path = tmp_path / 'slate.json'
    bets_path = tmp_path / 'bets.json'
    slate_path.write_text(json.dumps(slate))
    bets_path.write_text(json.dumps(bets))
    return str(slate_path), str(bets_path)


class TestValidateBetLoggingLiveGameExclusion:
    """Reproduces the exact July 25/26 GATE FAIL false positive."""

    def test_live_game_unlogged_bet_does_not_fail_gate(self, monkeypatch, tmp_path):
        """
        A game that is 'In Progress' (like CLE@TB on 2026-07-26) has an
        Accepted MEDIUM ledger row that write_pending_bets.py correctly
        never wrote to bets.json. validate_bet_logging.py must NOT treat
        this as a missing bet — the gate must PASS.
        """
        date = '2026-07-26'
        live_game = _game(
            'CLE', 'TB', 'In Progress',
            [_ledger_entry('TT_Away_Over', ticker='KXMLBTEAMTOTAL-26JUL26CLETB-CLE2')],
        )
        pregame_game = _game(
            'NYY', 'PHI', 'Pre-Game',
            [_ledger_entry('ML_Away', ticker='KXMLBGAME-26JUL26NYYPHI-NYY')],
            scheduled_start='2099-01-01T19:05:00Z',
        )
        slate = {'date': date, 'games': [live_game, pregame_game]}
        # Only the pregame bet was logged — exactly what write_pending_bets.py does.
        bets = [{
            'date': date, 'game': 'NYY@PHI', 'market': 'ML_Away',
            'ticker': 'KXMLBGAME-26JUL26NYYPHI-NYY',
        }]

        slate_path, bets_path = _write_slate_and_bets(tmp_path, slate, bets)
        monkeypatch.setattr(validate_bet_logging, 'SLATE_PATH', slate_path)
        monkeypatch.setattr(validate_bet_logging, 'BETS_PATH', bets_path)

        # main() falls through (returns 0) on success; raises SystemExit(1) on failure.
        result = validate_bet_logging.main()
        assert result == 0, "Live game's correctly-unlogged bet must not fail the gate"

    def test_final_game_unlogged_bet_does_not_fail_gate(self, monkeypatch, tmp_path):
        """Same scenario but with a 'Final' game (like KC@DET on 2026-07-25)."""
        date = '2026-07-25'
        final_game = _game(
            'KC', 'DET', 'Final',
            [_ledger_entry('ML_Home', ticker='KXMLBGAME-26JUL25KCDET-DET')],
        )
        slate = {'date': date, 'games': [final_game]}
        bets = []

        slate_path, bets_path = _write_slate_and_bets(tmp_path, slate, bets)
        monkeypatch.setattr(validate_bet_logging, 'SLATE_PATH', slate_path)
        monkeypatch.setattr(validate_bet_logging, 'BETS_PATH', bets_path)

        result = validate_bet_logging.main()
        assert result == 0, "Final game's correctly-unlogged bet must not fail the gate"

    def test_genuinely_missing_pregame_bet_still_fails_gate(self, monkeypatch, tmp_path):
        """
        A pregame game with an Accepted MEDIUM row that is NOT in bets.json
        for a reason other than live/final status is a real pipeline bug —
        the gate must still fail. This guards against over-correcting the
        fix into a gate that never fails.
        """
        date = '2026-07-26'
        pregame_game = _game(
            'NYY', 'PHI', 'Pre-Game',
            [_ledger_entry('ML_Away', ticker='KXMLBGAME-26JUL26NYYPHI-NYY')],
            scheduled_start='2099-01-01T19:05:00Z',
        )
        slate = {'date': date, 'games': [pregame_game]}
        bets = []   # bet was never written — genuine bug

        slate_path, bets_path = _write_slate_and_bets(tmp_path, slate, bets)
        monkeypatch.setattr(validate_bet_logging, 'SLATE_PATH', slate_path)
        monkeypatch.setattr(validate_bet_logging, 'BETS_PATH', bets_path)

        with pytest.raises(SystemExit) as exc_info:
            validate_bet_logging.main()
        assert exc_info.value.code == 1

    def test_excluded_from_slate_game_does_not_fail_gate(self, monkeypatch, tmp_path):
        """Quarantined games (excludedFromSlate) must also not count as expected."""
        date = '2026-07-26'
        quarantined_game = _game(
            'AZ', 'WSH', 'Pre-Game',
            [_ledger_entry('ML_Away', ticker='KXMLBGAME-26JUL26AZWSH-AZ')],
            scheduled_start='2099-01-01T19:05:00Z',
        )
        quarantined_game['excludedFromSlate'] = True
        slate = {'date': date, 'games': [quarantined_game]}
        bets = []

        slate_path, bets_path = _write_slate_and_bets(tmp_path, slate, bets)
        monkeypatch.setattr(validate_bet_logging, 'SLATE_PATH', slate_path)
        monkeypatch.setattr(validate_bet_logging, 'BETS_PATH', bets_path)

        result = validate_bet_logging.main()
        assert result == 0


class TestRiskGateLiveGameExclusion:
    """risk_gate.py must not count live/final game stake in portfolio composition."""

    def test_portfolio_rules_exclude_live_game_stake(self):
        live_game = _game(
            'CLE', 'TB', 'In Progress',
            [_ledger_entry('TT_Away_Over', edge=4.0)],
        )
        pregame_game = _game(
            'NYY', 'PHI', 'Pre-Game',
            [_ledger_entry('ML_Away', edge=2.0)],
            scheduled_start='2099-01-01T19:05:00Z',
        )
        slate = {'date': '2026-07-26', 'games': [live_game, pregame_game]}

        decision, report = risk_gate.apply_portfolio_rules(slate, now_ts='2026-07-26T18:00:00Z')

        assert report['total_bets'] == 1, (
            f"Live game's Accepted bet must be excluded from portfolio composition, "
            f"got {report['total_bets']} bets: {report['by_family']}"
        )
        assert report['total_real_stake'] == 4.0

    def test_tt_safety_pass_skips_live_game(self):
        live_game = _game(
            'CLE', 'TB', 'In Progress',
            [_ledger_entry('TT_Away_Over', edge=0.5)],   # would trigger TT edge downgrade
        )
        slate = {'date': '2026-07-26', 'games': [live_game]}

        downgrades = risk_gate.apply_tt_safety(slate, now_ts='2026-07-26T18:00:00Z')

        assert downgrades == [], (
            "TT safety pass must not process entries from live/final games at all"
        )
