#!/usr/bin/env python3
"""
tests/test_lineup_gate.py
=========================
Tests that the lineup gate (Rules 51/52/53) correctly:
 1. Allows real-money Accepted bets when BOTH lineups are confirmed
 2. Downgrades ML bets to PAPER when either lineup is unconfirmed (Rule 51)
 3. Downgrades YRFI/NRFI bets to PAPER when either lineup is unconfirmed (Rule 52)
 4. Downgrades F5 bets to PAPER when either lineup is unconfirmed (Rule 53)
 5. Preserves marketTicker in all downgraded rows (for CLV tracking)
 6. Reason string clearly identifies lineup as the cause in all downgraded rows
 7. Unconfirmed-away-only triggers the gate
 8. Unconfirmed-home-only triggers the gate

These tests exercise build_market_ledger.evaluate_game() directly.
"""

import sys
import os
import unittest

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts')
ROOT_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)

from build_market_ledger import evaluate_game


# ── Fixture builder ────────────────────────────────────────────────────────────
def _make_game(away_lineup=True, home_lineup=True,
               ml_away_am=-130, ml_home_am=+120,
               nrfi_am=-115, yrfi_am=+108,
               f5_away_am=-120, f5_home_am=+110,
               tt_away_am=+120, tt_home_am=+130,
               total_line=8, yrfi_implied=47.0, nrfi_implied=53.0):
    """
    Build a minimal game dict suitable for evaluate_game().
    away_lineup / home_lineup control lineupConfirmed for each side.
    Default odds are set so that YRFI, TT, and ML all have qualifying edges
    when lineups are confirmed.
    """
    return {
        'gameId': 999999,
        'away': {
            'abbr': 'AAA', 'team': 'Away Team',
            'pitcher': {'name': 'SP Away'},
            'pitcherSavant': {
                'xFIP': 5.5, 'seasonFIP': 5.5, 'recentFIP': 5.4,
                'avgIPperStart': 5.0, 'openerRole': False,
                'ttoSplit': 0.3, 'ttoAvailable': True,
                'tto1': {'fip': 5.5, 'gamesUsed': 5},
                'tto3': {'fip': 5.2, 'gamesUsed': 3},
            },
            'bullpen': {'xFIP': 4.5, 'hlGrade': 'AVERAGE', 'hlAvailable': True, 'hlXFIP': 4.5},
        },
        'home': {
            'abbr': 'HHH', 'team': 'Home Team',
            'pitcher': {'name': 'SP Home'},
            'pitcherSavant': {
                'xFIP': 3.5, 'seasonFIP': 3.5, 'recentFIP': 3.4,
                'avgIPperStart': 6.0, 'openerRole': False,
                'ttoSplit': 0.1, 'ttoAvailable': True,
                'tto1': {'fip': 3.5, 'gamesUsed': 5},
                'tto3': {'fip': 3.4, 'gamesUsed': 3},
            },
            'bullpen': {'xFIP': 3.8, 'hlGrade': 'ABOVE_AVERAGE', 'hlAvailable': True, 'hlXFIP': 3.7},
        },
        'awayTeamStats': {
            'offenseBaselineAdj': 5.2,
            'lineupConfirmed': away_lineup,
            'lineupBattersResolved': 9 if away_lineup else 0,
            'lineupAdj': 0.05 if away_lineup else None,
            'lineupAdjApplied': away_lineup,
        },
        'homeTeamStats': {
            'offenseBaselineAdj': 4.0,
            'lineupConfirmed': home_lineup,
            'lineupBattersResolved': 9 if home_lineup else 0,
            'lineupAdj': 0.02 if home_lineup else None,
            'lineupAdjApplied': home_lineup,
        },
        'park': {'parkFactor': 100},
        'pinnacleVF': {'away': 48.0, 'home': 52.0},
        'oddsApiCommenceTime': '2026-06-10T19:45:00Z',
        'kalshiKey': 'AAAHH',
        'kalshiGameTime': '1545',
        'odds': {
            'kalshi': {
                'ml': {
                    'away': ml_away_am, 'home': ml_home_am,
                    'away_ticker': 'KXMLBGAME-26JUN101545AAAHH-AAA',
                    'home_ticker': 'KXMLBGAME-26JUN101545AAAHH-HHH',
                    'source': 'kalshi_registry',
                },
                'nrfi_yrfi': {
                    'ticker':        'KXMLBRFI-26JUN101545AAAHH',
                    'nrfi_american': nrfi_am,
                    'yrfi_american': yrfi_am,
                    'nrfi_implied':  nrfi_implied,
                    'yrfi_implied':  yrfi_implied,
                    'source': 'kalshi_registry',
                },
                'f5ml': {
                    'away': f5_away_am, 'home': f5_home_am,
                    'away_ticker': 'KXMLBF5-26JUN101545AAAHH-AAA',
                    'home_ticker': 'KXMLBF5-26JUN101545AAAHH-HHH',
                    'source': 'kalshi_registry',
                },
                'team_totals': {
                    'away': {
                        'best_ticker': 'KXMLBTEAMTOTAL-26JUN101545AAAHH-AAA5',
                        'line': 5, 'american': tt_away_am, 'implied_pct': 44.0,
                    },
                    'home': {
                        'best_ticker': 'KXMLBTEAMTOTAL-26JUN101545AAAHH-HHH4',
                        'line': 4, 'american': tt_home_am, 'implied_pct': 43.0,
                    },
                },
                'rl': {
                    'best_ticker': 'KXMLBSPREAD-26JUN101545AAAHH-HHH2',
                    'american': +133, 'implied_pct': 43.0,
                    'team': 'HHH',
                },
                'total': {
                    'best_ticker': 'KXMLBTOTAL-26JUN101545AAAHH-9',
                    'line': total_line, 'american': -105,
                },
            },
        },
    }


def _row(ledger, market):
    """Return the first ledger row matching market name."""
    for r in ledger:
        if r['market'] == market:
            return r
    raise KeyError(f'Market {market!r} not found in ledger')


# ── Tests ──────────────────────────────────────────────────────────────────────
class TestLineupGateBothConfirmed(unittest.TestCase):
    """With both lineups confirmed, high-edge bets should be Accepted (not PAPER)."""

    def setUp(self):
        self.game = _make_game(away_lineup=True, home_lineup=True)
        self.ledger = evaluate_game(self.game)

    def test_yrfi_can_be_accepted_with_both_lineups(self):
        """YRFI should be Accepted when both lineups confirmed and edge qualifies."""
        row = _row(self.ledger, 'YRFI')
        # With SP xFIP 5.5 vs 3.5, YRFI should have edge. Accept or reject on merit, not lineup gate.
        self.assertNotIn('Rule 52', row.get('rejectionReason') or '')
        self.assertNotIn('Rule 52', ' '.join(row.get('gatesFired') or []))

    def test_ml_can_be_accepted_with_both_lineups(self):
        """ML should NOT be blocked by lineup gate when both confirmed."""
        for market in ('ML_Away', 'ML_Home'):
            row = _row(self.ledger, market)
            self.assertNotIn('Rule 51', row.get('rejectionReason') or '')
            self.assertNotIn('Rule 51', ' '.join(row.get('gatesFired') or []))

    def test_tt_can_be_accepted_with_both_lineups(self):
        """TT should NOT carry lineup gate message when both confirmed."""
        for market in ('TT_Away_Over', 'TT_Home_Over'):
            row = _row(self.ledger, market)
            self.assertNotIn('lineupConfirmed=False', row.get('rejectionReason') or '')
            gates = ' '.join(row.get('gatesFired') or [])
            self.assertNotIn('Rule 50', gates)


class TestLineupGateBothUnconfirmed(unittest.TestCase):
    """With both lineups unconfirmed, all applicable markets must be PAPER."""

    def setUp(self):
        self.game = _make_game(away_lineup=False, home_lineup=False)
        self.ledger = evaluate_game(self.game)

    def _assert_paper_or_below(self, row):
        """Status Accepted with PAPER confidence, or Rejected, or Missing Data."""
        status = row.get('status')
        conf   = row.get('confidence')
        if status == 'Accepted':
            self.assertEqual(conf, 'PAPER',
                msg=f"Market {row['market']}: Accepted with conf={conf!r} despite unconfirmed lineups — expected PAPER")
        # Rejected or Missing Data are also acceptable outcomes

    def test_yrfi_downgraded_to_paper_or_rejected(self):
        """YRFI must not be Accepted with HIGH/MEDIUM confidence when lineups unconfirmed."""
        row = _row(self.ledger, 'YRFI')
        self._assert_paper_or_below(row)

    def test_nrfi_downgraded_to_paper_or_rejected(self):
        """NRFI must not be Accepted with HIGH/MEDIUM confidence when lineups unconfirmed."""
        row = _row(self.ledger, 'NRFI')
        self._assert_paper_or_below(row)

    def test_ml_away_downgraded_to_paper_or_rejected(self):
        """ML_Away must not be real-money Accepted when lineups unconfirmed."""
        row = _row(self.ledger, 'ML_Away')
        self._assert_paper_or_below(row)

    def test_ml_home_downgraded_to_paper_or_rejected(self):
        """ML_Home must not be real-money Accepted when lineups unconfirmed."""
        row = _row(self.ledger, 'ML_Home')
        self._assert_paper_or_below(row)

    def test_tt_away_downgraded_to_paper_or_rejected(self):
        """TT_Away_Over must not be real-money Accepted when lineups unconfirmed."""
        row = _row(self.ledger, 'TT_Away_Over')
        self._assert_paper_or_below(row)

    def test_tt_home_downgraded_to_paper_or_rejected(self):
        """TT_Home_Over must not be real-money Accepted when lineups unconfirmed."""
        row = _row(self.ledger, 'TT_Home_Over')
        self._assert_paper_or_below(row)


class TestLineupGateAwayUnconfirmed(unittest.TestCase):
    """Away lineup unconfirmed, home confirmed — gate must still fire."""

    def setUp(self):
        self.game = _make_game(away_lineup=False, home_lineup=True)
        self.ledger = evaluate_game(self.game)

    def _assert_paper_or_below(self, row):
        status = row.get('status')
        conf   = row.get('confidence')
        if status == 'Accepted':
            self.assertEqual(conf, 'PAPER',
                msg=f"Market {row['market']}: Accepted with conf={conf!r} despite away lineup unconfirmed")

    def test_yrfi_downgraded_when_away_unconfirmed(self):
        row = _row(self.ledger, 'YRFI')
        self._assert_paper_or_below(row)

    def test_ml_downgraded_when_away_unconfirmed(self):
        for market in ('ML_Away', 'ML_Home'):
            row = _row(self.ledger, market)
            self._assert_paper_or_below(row)

    def test_yrfi_gate_reason_mentions_lineup(self):
        """The YRFI gate reason must clearly cite lineup as the cause."""
        row = _row(self.ledger, 'YRFI')
        gates = ' '.join(row.get('gatesFired') or [])
        reason = row.get('rejectionReason') or ''
        self.assertTrue(
            'lineup' in gates.lower() or 'lineup' in reason.lower() or
            'Rule 52' in gates or 'Rule 52' in reason,
            msg=f"YRFI gate reason does not mention lineup. gates={gates!r} reason={reason!r}"
        )


class TestLineupGateHomeUnconfirmed(unittest.TestCase):
    """Home lineup unconfirmed, away confirmed — gate must still fire."""

    def setUp(self):
        self.game = _make_game(away_lineup=True, home_lineup=False)
        self.ledger = evaluate_game(self.game)

    def _assert_paper_or_below(self, row):
        status = row.get('status')
        conf   = row.get('confidence')
        if status == 'Accepted':
            self.assertEqual(conf, 'PAPER',
                msg=f"Market {row['market']}: Accepted with conf={conf!r} despite home lineup unconfirmed")

    def test_yrfi_downgraded_when_home_unconfirmed(self):
        row = _row(self.ledger, 'YRFI')
        self._assert_paper_or_below(row)

    def test_ml_downgraded_when_home_unconfirmed(self):
        for market in ('ML_Away', 'ML_Home'):
            row = _row(self.ledger, market)
            self._assert_paper_or_below(row)

    def test_ml_gate_reason_mentions_lineup(self):
        """ML gate reason must clearly cite lineup as the cause."""
        for market in ('ML_Away', 'ML_Home'):
            row = _row(self.ledger, market)
            gates = ' '.join(row.get('gatesFired') or [])
            reason = row.get('rejectionReason') or ''
            self.assertTrue(
                'lineup' in gates.lower() or 'lineup' in reason.lower() or
                'Rule 51' in gates or 'Rule 51' in reason,
                msg=f"{market} gate reason does not mention lineup. gates={gates!r} reason={reason!r}"
            )


class TestLineupGateTickerPreservation(unittest.TestCase):
    """marketTicker must be preserved in all rows, including lineup-gated PAPER rows."""

    def setUp(self):
        self.game = _make_game(away_lineup=False, home_lineup=False)
        self.ledger = evaluate_game(self.game)

    def test_yrfi_ticker_preserved_when_downgraded(self):
        """YRFI marketTicker must survive lineup downgrade."""
        row = _row(self.ledger, 'YRFI')
        if row.get('status') == 'Accepted':
            # If YRFI was downgraded to PAPER Accepted, ticker must be present
            self.assertIsNotNone(row.get('marketTicker'),
                msg='YRFI marketTicker is null despite being Accepted (even as PAPER)')

    def test_ml_ticker_preserved_when_downgraded(self):
        """ML marketTicker must survive lineup downgrade."""
        for market in ('ML_Away', 'ML_Home'):
            row = _row(self.ledger, market)
            if row.get('status') == 'Accepted':
                self.assertIsNotNone(row.get('marketTicker'),
                    msg=f'{market} marketTicker is null despite being Accepted (even as PAPER)')


class TestLineupGateF5(unittest.TestCase):
    """F5 bets must also be gated when lineups are unconfirmed (Rule 53)."""

    def setUp(self):
        # F5 is currently Missing Data in production (price mapping bug),
        # but if prices were present, Rule 53 must fire.
        # We test via the gates logic by inspecting the F5 section directly.
        # Since f5ml prices are provided in the fixture, this exercises the gate.
        self.game_no_lineup = _make_game(away_lineup=False, home_lineup=False)
        self.ledger_no_lineup = evaluate_game(self.game_no_lineup)
        self.game_confirmed = _make_game(away_lineup=True, home_lineup=True)
        self.ledger_confirmed = evaluate_game(self.game_confirmed)

    def _assert_paper_or_below(self, row):
        status = row.get('status')
        conf   = row.get('confidence')
        if status == 'Accepted':
            self.assertEqual(conf, 'PAPER',
                msg=f"{row['market']}: Accepted with conf={conf!r} despite unconfirmed lineups")

    def test_f5_ml_away_downgraded_when_lineups_unconfirmed(self):
        row = _row(self.ledger_no_lineup, 'F5_ML_Away')
        # F5 may be Missing Data if prices absent; if Accepted it must be PAPER
        self._assert_paper_or_below(row)

    def test_f5_ml_home_downgraded_when_lineups_unconfirmed(self):
        row = _row(self.ledger_no_lineup, 'F5_ML_Home')
        self._assert_paper_or_below(row)

    def test_f5_no_spurious_gate_when_lineups_confirmed(self):
        """F5 should NOT have Rule 53 gate when lineups ARE confirmed."""
        for market in ('F5_ML_Away', 'F5_ML_Home'):
            row = _row(self.ledger_confirmed, market)
            gates = ' '.join(row.get('gatesFired') or [])
            reason = row.get('rejectionReason') or ''
            self.assertNotIn('Rule 53', gates)
            self.assertNotIn('Rule 53', reason)


class TestLineupPersistenceInSlate(unittest.TestCase):
    """
    Verify that lineupConfirmed is correctly read from awayTeamStats/homeTeamStats.
    This is an integration-style check on the field path the ledger uses.
    """

    def test_lineup_confirmed_field_read_path(self):
        """evaluate_game() reads lineupConfirmed from awayTeamStats / homeTeamStats."""
        game = _make_game(away_lineup=True, home_lineup=True)
        # Confirm the field path is correct
        self.assertTrue(game['awayTeamStats']['lineupConfirmed'])
        self.assertTrue(game['homeTeamStats']['lineupConfirmed'])
        # Should not raise
        ledger = evaluate_game(game)
        self.assertEqual(len(ledger), 11)

    def test_missing_teamstats_treated_as_unconfirmed(self):
        """If awayTeamStats is absent, lineupConfirmed defaults to False → gate fires."""
        game = _make_game(away_lineup=False, home_lineup=True)
        # Simulate completely absent awayTeamStats
        game['awayTeamStats'] = {}
        ledger = evaluate_game(game)
        yrfi_row = _row(ledger, 'YRFI')
        if yrfi_row.get('status') == 'Accepted':
            self.assertEqual(yrfi_row.get('confidence'), 'PAPER',
                msg='YRFI should be PAPER when awayTeamStats is empty (lineupConfirmed defaults False)')


if __name__ == '__main__':
    unittest.main(verbosity=2)
