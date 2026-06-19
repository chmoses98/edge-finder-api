#!/usr/bin/env python3
"""
tests/test_live_game_gate.py
=============================
Regression tests for the pregame-only hard-block gate.

Covers:
  1. In-progress game with confirmed lineups → zero REAL_MONEY_OFFICIAL bets
  2. In-progress game with positive edge → still blocked
  3. Final/completed game → blocked
  4. Pregame scheduled game with confirmed lineups → can pass
  5. Explicit LIVE_BET mode → may analyze live markets, labeled LIVE not pregame
  6. June 18 BAL@SEA regression → BAL TT Away Over cannot be official pregame real-money
     when game status is In Progress

All tests target the three gate layers independently:
  - lib/postponed_guard.check_game_status()
  - scripts/bet_eligibility.classify_bet_eligibility()
  - scripts/write_pending_bets logic (via check_game_status integration)
  - scripts/validate_slate_final.generate_execution_slip()
"""

import json
import os
import sys
import pytest

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'lib'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

from postponed_guard import (
    check_game_status,
    is_live_game_blocked,
    check_first_pitch_passed,
    IN_PLAY_STATUSES,
    FINAL_STATUSES,
)
from bet_eligibility import (
    classify_bet_eligibility,
    BET_BLOCK_LIVE,
    BET_ELIGIBLE,
    BET_PAPER,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_game(status, away='BAL', home='SEA', lineup_confirmed=True,
              scheduled_start=None, game_id='12345'):
    """Build a minimal game dict matching slate.json shape."""
    return {
        'gameId': game_id,
        'status': status,
        'away': {'abbr': away},
        'home': {'abbr': home},
        'scheduledStartTime': scheduled_start or '2026-06-18T16:10:00Z',
        'awayTeamStats': {'lineupConfirmed': lineup_confirmed},
        'homeTeamStats': {'lineupConfirmed': lineup_confirmed},
        'marketLedger': [
            {
                'market': 'TT_Away_Over',
                'status': 'Accepted',
                'confidence': 'HIGH',
                'confidenceTier': 'HIGH',
                'edge': 3.81,
                'calibratedEdgeVsExecutable': 3.81,
                'rawEdgeVsExecutable': 14.93,
                'modelProb': 64.93,
                'kalshiPrice': -100,
                'executablePriceUsed': 50.0,
                'betSize': 5.0,
                'marketTicker': f'KXMLBTEAMTOTAL-26JUN181610{away}{home}-{away}2',
                'reasonCodes': ['MARKET_REAL_MONEY_ELIGIBLE', 'LINEUP_CONFIRMED_OFFICIAL'],
                'gatesFired': [],
                'rejectionReason': None,
            },
            {
                'market': 'ML_Away',
                'status': 'Accepted',
                'confidence': 'MEDIUM',
                'confidenceTier': 'MEDIUM',
                'edge': 1.47,
                'calibratedEdgeVsExecutable': 1.47,
                'rawEdgeVsExecutable': 5.76,
                'modelProb': 54.3,
                'kalshiPrice': -106,
                'executablePriceUsed': 48.54,
                'betSize': 3.0,
                'marketTicker': f'KXMLBGAME-26JUN181840{away}{home}-{away}',
                'reasonCodes': ['MARKET_REAL_MONEY_ELIGIBLE', 'LINEUP_CONFIRMED_OFFICIAL'],
                'gatesFired': [],
                'rejectionReason': None,
            },
        ],
    }


def _slip_real_money_for_game(games, game_label):
    """Run generate_execution_slip and return real_money entries for one game."""
    # Import here to pick up the patched version
    from validate_slate_final import generate_execution_slip
    _, slip_dict = generate_execution_slip(games, '2026-06-18')
    return [e for e in slip_dict['realMoney'] if e['game'] == game_label]


def _slip_blocked_for_game(games, game_label):
    """Return rejectedBlocked entries for one game."""
    from validate_slate_final import generate_execution_slip
    _, slip_dict = generate_execution_slip(games, '2026-06-18')
    return [e for e in slip_dict['rejectedBlocked'] if e['game'] == game_label]


def _slip_live_blocked_games(games):
    """Return liveGameBlockedGames list from the slip."""
    from validate_slate_final import generate_execution_slip
    _, slip_dict = generate_execution_slip(games, '2026-06-18')
    return slip_dict.get('liveGameBlockedGames', [])


# ─────────────────────────────────────────────────────────────────────────────
# 1. In-progress game with confirmed lineups → zero REAL_MONEY_OFFICIAL bets
# ─────────────────────────────────────────────────────────────────────────────

class TestInProgressGameBlocked:

    def test_check_game_status_in_progress_should_skip(self):
        """check_game_status must return shouldSkip=True for In Progress."""
        game = make_game('In Progress')
        result = check_game_status(game)
        assert result['shouldSkip'] is True, (
            f"In Progress game must set shouldSkip=True, got: {result}"
        )

    def test_check_game_status_in_progress_live_blocked(self):
        """liveGameBlocked must be True, not voidExisting (game was not postponed)."""
        game = make_game('In Progress')
        result = check_game_status(game)
        assert result['liveGameBlocked'] is True
        assert result['voidExisting'] is False  # game played; don't void, just block late entry

    def test_check_game_status_in_progress_skip_reason(self):
        """skipReason must be LIVE_GAME_BLOCKED."""
        game = make_game('In Progress')
        result = check_game_status(game)
        assert result['skipReason'] == 'LIVE_GAME_BLOCKED'

    def test_execution_slip_in_progress_zero_real_money(self):
        """generate_execution_slip must produce 0 real-money entries for In Progress game."""
        game = make_game('In Progress')
        real_money = _slip_real_money_for_game([game], 'BAL@SEA')
        assert len(real_money) == 0, (
            f"Expected 0 real-money entries for In Progress game, got {len(real_money)}: "
            f"{real_money}"
        )

    def test_execution_slip_in_progress_game_in_live_blocked_list(self):
        """In Progress game must appear in liveGameBlockedGames."""
        game = make_game('In Progress')
        blocked = _slip_live_blocked_games([game])
        assert 'BAL@SEA' in blocked

    def test_write_pending_bets_skips_in_progress(self):
        """
        Simulate the write_pending_bets game-loop guard for an In Progress game.
        The gate must fire and the game must be skipped (not iterated into marketLedger).
        """
        game = make_game('In Progress')
        gs_result = check_game_status(game)
        # The condition that write_pending_bets.py checks:
        gate_fires = (
            gs_result.get('shouldSkip') and (
                gs_result.get('liveGameBlocked') or
                gs_result.get('skipReason') in ('LIVE_GAME_BLOCKED', 'PREGAME_ONLY_STARTED_GAME')
            )
        )
        assert gate_fires is True, (
            "write_pending_bets pregame gate must fire for In Progress game"
        )

    def test_all_in_play_statuses_blocked(self):
        """Every status in IN_PLAY_STATUSES must trigger the live-game block."""
        for status in IN_PLAY_STATUSES:
            game = make_game(status)
            result = check_game_status(game)
            assert result['liveGameBlocked'] is True, (
                f"Status {status!r} must set liveGameBlocked=True"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 2. In-progress game with positive edge is still blocked
# ─────────────────────────────────────────────────────────────────────────────

class TestPositiveEdgeStillBlocked:

    def test_classify_bet_eligibility_live_blocked_overrides_positive_edge(self):
        """
        Even with a valid ticker, valid price, and positive edge,
        live_game_blocked=True must return BET_BLOCK_LIVE.
        """
        result = classify_bet_eligibility(
            market_ticker='KXMLBTEAMTOTAL-26JUN181610BALSEA-BAL2',
            entry_price=50.0,
            ledger_status='Accepted',
            rule_block_reason=None,
            is_paper_only=False,
            ambiguous_ticker=False,
            live_game_blocked=True,   # ← the gate
            live_bet_mode=False,
        )
        assert result['bet_eligibility_status'] == BET_BLOCK_LIVE, (
            f"Expected BET_BLOCK_LIVE, got {result['bet_eligibility_status']!r}"
        )

    def test_live_block_fires_before_rule_check(self):
        """live_game_blocked fires before rule_block_reason check."""
        result = classify_bet_eligibility(
            market_ticker='KXMLBTEAMTOTAL-26JUN181610BALSEA-BAL2',
            entry_price=50.0,
            ledger_status='Accepted',
            rule_block_reason='Rule 34: NRFI blocked',  # would also block, but live fires first
            is_paper_only=False,
            ambiguous_ticker=False,
            live_game_blocked=True,
            live_bet_mode=False,
        )
        assert result['bet_eligibility_status'] == BET_BLOCK_LIVE

    def test_eligibility_reason_mentions_pregame_only(self):
        """eligibility_reason must reference pregame-only mode clearly."""
        result = classify_bet_eligibility(
            market_ticker='KXMLBTEAMTOTAL-26JUN181610BALSEA-BAL2',
            entry_price=50.0,
            ledger_status='Accepted',
            rule_block_reason=None,
            is_paper_only=False,
            ambiguous_ticker=False,
            live_game_blocked=True,
            live_bet_mode=False,
        )
        reason = result['eligibility_reason'].lower()
        assert 'pregame' in reason or 'live_game_blocked' in reason.upper() or 'live' in reason, (
            f"eligibility_reason should reference pregame-only or live block: {result['eligibility_reason']!r}"
        )

    def test_execution_slip_in_progress_positive_edge_blocked(self):
        """
        Game with In Progress status and high edge must produce 0 real-money entries.
        The edge is positive (3.81%) but the gate fires first.
        """
        game = make_game('In Progress')
        # Verify ledger has a positive-edge Accepted entry
        assert game['marketLedger'][0]['edge'] == 3.81
        assert game['marketLedger'][0]['status'] == 'Accepted'
        # But slip must show 0 real-money
        real_money = _slip_real_money_for_game([game], 'BAL@SEA')
        assert len(real_money) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. Final/completed game is blocked
# ─────────────────────────────────────────────────────────────────────────────

class TestFinalGameBlocked:

    def test_all_final_statuses_blocked(self):
        """Every status in FINAL_STATUSES must set shouldSkip=True and liveGameBlocked=True."""
        for status in FINAL_STATUSES:
            game = make_game(status)
            result = check_game_status(game)
            assert result['shouldSkip'] is True, (
                f"Final status {status!r} must set shouldSkip=True"
            )
            assert result['liveGameBlocked'] is True, (
                f"Final status {status!r} must set liveGameBlocked=True"
            )

    def test_final_skip_reason_pregame_only(self):
        """Final game skipReason must be PREGAME_ONLY_STARTED_GAME."""
        result = check_game_status(make_game('Final'))
        assert result['skipReason'] == 'PREGAME_ONLY_STARTED_GAME'

    def test_final_game_zero_real_money_slip(self):
        """Final game must produce 0 real-money entries in execution slip."""
        game = make_game('Final')
        real_money = _slip_real_money_for_game([game], 'BAL@SEA')
        assert len(real_money) == 0

    def test_final_game_in_live_blocked_games(self):
        """Final game must appear in liveGameBlockedGames."""
        game = make_game('Final')
        blocked = _slip_live_blocked_games([game])
        assert 'BAL@SEA' in blocked

    def test_completed_early_blocked(self):
        """Completed Early (rain) must also be blocked."""
        result = check_game_status(make_game('Completed Early'))
        assert result['liveGameBlocked'] is True

    def test_game_over_blocked(self):
        """Game Over status must be blocked."""
        result = check_game_status(make_game('Game Over'))
        assert result['liveGameBlocked'] is True


# ─────────────────────────────────────────────────────────────────────────────
# 4. Pregame scheduled game passes through
# ─────────────────────────────────────────────────────────────────────────────

class TestPregameGamePassesThrough:

    @pytest.mark.parametrize('status', ['Scheduled', 'Pre-Game', 'Warmup', 'Pregame'])
    def test_pregame_status_not_blocked(self, status):
        """Pregame statuses must not trigger the live-game block."""
        game = make_game(status)
        result = check_game_status(game)
        assert result['shouldSkip'] is False, (
            f"Pregame status {status!r} must NOT set shouldSkip=True"
        )
        assert result.get('liveGameBlocked', False) is False

    def test_pregame_game_appears_in_real_money_slip(self):
        """
        Pregame game with confirmed lineups and accepted HIGH bet must appear
        in real-money section of execution slip.
        """
        game = make_game('Pre-Game', away='NYM', home='PHI', game_id='99001')
        real_money = _slip_real_money_for_game([game], 'NYM@PHI')
        assert len(real_money) > 0, (
            "Pregame game with Accepted HIGH bets must have real-money entries in slip"
        )

    def test_pregame_game_not_in_live_blocked_list(self):
        """Pregame game must not appear in liveGameBlockedGames."""
        game = make_game('Pre-Game', away='NYM', home='PHI', game_id='99001')
        blocked = _slip_live_blocked_games([game])
        assert 'NYM@PHI' not in blocked

    def test_classify_bet_eligibility_pregame_actionable(self):
        """classify_bet_eligibility with live_game_blocked=False must return actionable."""
        result = classify_bet_eligibility(
            market_ticker='KXMLBGAME-26JUN181840NYMPHI-NYM',
            entry_price=48.54,
            ledger_status='Accepted',
            rule_block_reason=None,
            is_paper_only=False,
            ambiguous_ticker=False,
            live_game_blocked=False,
            live_bet_mode=False,
        )
        assert result['bet_eligibility_status'] == BET_ELIGIBLE

    def test_unknown_status_treated_as_pregame(self):
        """
        A game with no status field (None or '') should be treated as pregame
        (safe default: assume not started rather than block valid early-session runs).
        """
        game = make_game('')
        result = check_game_status(game)
        assert result.get('liveGameBlocked', False) is False


# ─────────────────────────────────────────────────────────────────────────────
# 5. LIVE_BET mode may analyze live markets but must not classify as pregame
# ─────────────────────────────────────────────────────────────────────────────

class TestLiveBetMode:

    def test_live_bet_mode_bypasses_live_game_block(self):
        """
        When live_bet_mode=True, live_game_blocked=True must NOT return BET_BLOCK_LIVE.
        The bet may be analyzed but must be labeled differently (not pregame real-money).
        """
        result = classify_bet_eligibility(
            market_ticker='KXMLBTEAMTOTAL-26JUN181610BALSEA-BAL2',
            entry_price=50.0,
            ledger_status='Accepted',
            rule_block_reason=None,
            is_paper_only=False,
            ambiguous_ticker=False,
            live_game_blocked=True,
            live_bet_mode=True,   # ← explicit live mode
        )
        # Should NOT be BET_BLOCK_LIVE — live mode bypasses the gate
        assert result['bet_eligibility_status'] != BET_BLOCK_LIVE, (
            "LIVE_BET mode must bypass the live-game block gate"
        )

    def test_live_bet_mode_false_still_blocked(self):
        """live_bet_mode=False (default) must still block a live game."""
        result = classify_bet_eligibility(
            market_ticker='KXMLBTEAMTOTAL-26JUN181610BALSEA-BAL2',
            entry_price=50.0,
            ledger_status='Accepted',
            rule_block_reason=None,
            is_paper_only=False,
            ambiguous_ticker=False,
            live_game_blocked=True,
            live_bet_mode=False,
        )
        assert result['bet_eligibility_status'] == BET_BLOCK_LIVE

    def test_live_bet_mode_default_is_false(self):
        """Default behavior (no live_bet_mode arg) must block a live game."""
        result = classify_bet_eligibility(
            market_ticker='KXMLBTEAMTOTAL-26JUN181610BALSEA-BAL2',
            entry_price=50.0,
            ledger_status='Accepted',
            rule_block_reason=None,
            is_paper_only=False,
            ambiguous_ticker=False,
            live_game_blocked=True,
            # live_bet_mode omitted — must default to False
        )
        assert result['bet_eligibility_status'] == BET_BLOCK_LIVE


# ─────────────────────────────────────────────────────────────────────────────
# 6. June 18 BAL@SEA regression test
# ─────────────────────────────────────────────────────────────────────────────

class TestJune18BalSeaRegression:
    """
    Regression: June 18, 2026 — BAL@SEA first pitch 16:10 UTC.
    Execution slip generated at 21:16 UTC (5h+ after first pitch).
    Slate showed game status = "In Progress".
    BAL TT Away Over should have been blocked as PREGAME_ONLY_STARTED_GAME / LIVE_GAME_BLOCKED.
    """

    BALSEA_TICKER = 'KXMLBTEAMTOTAL-26JUN181610BALSEA-BAL2'

    def _june18_bal_sea_game(self, status='In Progress'):
        """Build the June 18 BAL@SEA game dict as it appeared in slate.json."""
        return {
            'gameId': 'june18_balsea',
            'status': status,
            'away': {'abbr': 'BAL'},
            'home': {'abbr': 'SEA'},
            'scheduledStartTime': '2026-06-18T16:10:00Z',
            'awayTeamStats': {'lineupConfirmed': True, 'lineupStatus': 'confirmed'},
            'homeTeamStats': {'lineupConfirmed': True},
            'marketLedger': [
                {
                    'market': 'TT_Away_Over',
                    'status': 'Accepted',
                    'confidence': 'HIGH',
                    'confidenceTier': 'HIGH',
                    'edge': 3.81,
                    'calibratedEdgeVsExecutable': 3.81,
                    'rawEdgeVsExecutable': 14.93,
                    'modelProb': 64.93,
                    'kalshiPrice': -100,
                    'executablePriceUsed': 50.0,
                    'betSize': 5.0,
                    'marketTicker': self.BALSEA_TICKER,
                    'reasonCodes': [
                        'MARKET_REAL_MONEY_ELIGIBLE',
                        'EXECUTABLE_EDGE_ABOVE_THRESHOLD',
                        'RAW_EDGE_STRONG',
                        'LINEUP_CONFIRMED_OFFICIAL',
                    ],
                    'gatesFired': [],
                    'rejectionReason': None,
                },
            ],
        }

    def test_june18_balsea_in_progress_status_blocked(self):
        """
        check_game_status must block BAL@SEA when status = 'In Progress'.
        This is the exact condition on June 18.
        """
        game = self._june18_bal_sea_game(status='In Progress')
        result = check_game_status(game)
        assert result['shouldSkip'] is True
        assert result['liveGameBlocked'] is True
        assert result['skipReason'] in ('LIVE_GAME_BLOCKED', 'PREGAME_ONLY_STARTED_GAME')

    def test_june18_bal_tt_not_in_real_money_when_in_progress(self):
        """
        generate_execution_slip must produce 0 real-money entries for BAL@SEA
        when the game is In Progress — regardless of edge or lineup confirmation.
        This is the exact failure mode from June 18.
        """
        game = self._june18_bal_sea_game(status='In Progress')
        real_money = _slip_real_money_for_game([game], 'BAL@SEA')
        assert len(real_money) == 0, (
            f"BAL TT Away Over must not appear in real-money slip for In Progress game. "
            f"Got {len(real_money)} entries: {real_money}"
        )

    def test_june18_bal_tt_appears_in_rejected_blocked(self):
        """
        When BAL@SEA is In Progress, all its ledger rows must appear in rejectedBlocked
        with a block reason of LIVE_GAME_BLOCKED or PREGAME_ONLY_STARTED_GAME.
        """
        game = self._june18_bal_sea_game(status='In Progress')
        blocked = _slip_blocked_for_game([game], 'BAL@SEA')
        assert len(blocked) > 0, (
            "BAL@SEA markets must appear in rejectedBlocked when game is In Progress"
        )
        for entry in blocked:
            reason = entry.get('rejectionReason', '')
            gates = entry.get('gatesFired', [])
            assert ('LIVE_GAME_BLOCKED' in reason or 'PREGAME_ONLY_STARTED_GAME' in reason
                    or any('LIVE' in g for g in gates)), (
                f"BAL@SEA rejectedBlocked entry must reference live-game block: {entry}"
            )

    def test_june18_balsea_in_live_blocked_games_list(self):
        """BAL@SEA must appear in liveGameBlockedGames for In Progress game."""
        game = self._june18_bal_sea_game(status='In Progress')
        blocked = _slip_live_blocked_games([game])
        assert 'BAL@SEA' in blocked

    def test_june18_first_pitch_passed_at_slip_time(self):
        """
        check_first_pitch_passed must return True when called with the slip generation
        timestamp (21:16 UTC) for a 16:10 UTC first pitch.
        """
        result = check_first_pitch_passed(
            scheduled_start_utc='2026-06-18T16:10:00Z',
            current_utc='2026-06-18T21:16:33Z',  # exact slip generation time
        )
        assert result is True, (
            "First pitch at 16:10 UTC must be considered passed at 21:16 UTC"
        )

    def test_june18_other_games_not_blocked(self):
        """
        NYM@PHI (Pre-Game) and STL@KC (Pre-Game) must NOT be blocked.
        Only BAL@SEA must be in liveGameBlockedGames.
        """
        bal_sea = self._june18_bal_sea_game(status='In Progress')
        nym_phi = make_game('Pre-Game', away='NYM', home='PHI', game_id='99001')
        stl_kc  = make_game('Pre-Game', away='STL', home='KC',  game_id='99002')

        games = [bal_sea, nym_phi, stl_kc]
        blocked = _slip_live_blocked_games(games)
        real_money_nym = _slip_real_money_for_game(games, 'NYM@PHI')
        real_money_stl = _slip_real_money_for_game(games, 'STL@KC')

        assert 'BAL@SEA' in blocked, "BAL@SEA must be in live-blocked list"
        assert 'NYM@PHI' not in blocked, "NYM@PHI (Pre-Game) must NOT be blocked"
        assert 'STL@KC' not in blocked, "STL@KC (Pre-Game) must NOT be blocked"
        assert len(real_money_nym) > 0, "NYM@PHI must have real-money entries"
        assert len(real_money_stl) > 0, "STL@KC must have real-money entries"

    def test_june18_classify_bet_eligibility_live_blocked(self):
        """
        classify_bet_eligibility with live_game_blocked=True for the BAL TT ticker
        must return BET_BLOCK_LIVE.
        """
        result = classify_bet_eligibility(
            market_ticker=self.BALSEA_TICKER,
            entry_price=50.0,
            ledger_status='Accepted',
            rule_block_reason=None,
            is_paper_only=False,
            ambiguous_ticker=False,
            live_game_blocked=True,
            live_bet_mode=False,
        )
        assert result['bet_eligibility_status'] == BET_BLOCK_LIVE, (
            f"BAL TT Away Over must be BET_BLOCK_LIVE when game is In Progress. "
            f"Got: {result['bet_eligibility_status']!r}"
        )

    def test_june18_write_pending_bets_gate_fires(self):
        """
        The write_pending_bets pregame gate must fire for BAL@SEA In Progress game,
        preventing any bets from being written to bets.json.
        """
        game = self._june18_bal_sea_game(status='In Progress')
        gs_result = check_game_status(game)
        gate_fires = (
            gs_result.get('shouldSkip') and (
                gs_result.get('liveGameBlocked') or
                gs_result.get('skipReason') in ('LIVE_GAME_BLOCKED', 'PREGAME_ONLY_STARTED_GAME')
            )
        )
        assert gate_fires is True, (
            f"write_pending_bets gate must fire for BAL@SEA In Progress. result={gs_result}"
        )
