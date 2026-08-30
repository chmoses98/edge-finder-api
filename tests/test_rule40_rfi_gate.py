#!/usr/bin/env python3
"""
tests/test_rule40_rfi_gate.py
==============================
Regression tests for Rule 40: four-factor composite required for YRFI/NRFI.

When first-inning xERA data (Factor 1) is missing from both pitchers'
firstInningSplit, the maximum allowed ledger status for YRFI and NRFI is PAPER.
The ledger must enforce this automatically — not via manual explanation text.

Tests:
  1. YRFI with MEDIUM-level edge but missing Rule 40 data → status=Accepted, conf=PAPER
  2. NRFI with MEDIUM-level edge but missing Rule 40 data → status=Accepted, conf=PAPER
  3. Both YRFI and NRFI get a gatesFired entry containing 'Rule 40'
  4. The gate message says 'paper cap applied'
  5. With complete Rule 40 data, YRFI/NRFI can be Accepted at MEDIUM (not forced to PAPER)
  6. Rule 40 paper cap is independent of the Rule 52 lineup gate
  7. Rule 40 applies when only one pitcher's 1st-inning xERA is missing
  8. YRFI with complete data and MEDIUM edge is NOT downgraded by Rule 40
"""

import sys
import os
import unittest

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts')
sys.path.insert(0, SCRIPTS_DIR)

from build_market_ledger import evaluate_game


# ── Fixture builders ────────────────────────────────────────────────────────────

def _make_game(
    away_fi_xera=None,       # None = missing (triggers Rule 40)
    home_fi_xera=None,       # None = missing (triggers Rule 40)
    away_lineup=True,
    home_lineup=True,
    yrfi_implied=40.0,       # Low implied → high model edge for YRFI (model will compute ~65%)
    nrfi_implied=60.0,       # Complement
    total_line=7,            # Below 8.0 so Rule 34 doesn't block NRFI
    away_xfip=5.0,           # Weak starter → high scoring, YRFI likely
    home_xfip=5.0,
):
    """
    Build a minimal game dict for evaluate_game().

    With xFIP 5.0 for both starters and yrfi_implied=40%, the model should
    compute a YRFI probability well above 40%, giving a qualifying MEDIUM edge.

    firstInningSplit content controls Rule 40:
    - away_fi_xera=None, home_fi_xera=None  → Rule 40 fires (both missing)
    - away_fi_xera=5.0, home_fi_xera=4.5   → Rule 40 clear (data present)
    """
    away_fi = {}
    if away_fi_xera is not None:
        away_fi = {'firstInningXERA': away_fi_xera, 'gamesUsed': 8, 'appearances': 8}

    home_fi = {}
    if home_fi_xera is not None:
        home_fi = {'firstInningXERA': home_fi_xera, 'gamesUsed': 8, 'appearances': 8}

    return {
        'gameId': 888888,
        'away': {
            'abbr': 'AAA', 'team': 'Away Team',
            'pitcher': {'name': 'Away SP'},
            'pitcherSavant': {
                'xFIP': away_xfip, 'seasonFIP': away_xfip, 'recentFIP': away_xfip,
                'avgIPperStart': 5.0, 'openerRole': False,
                'ttoSplit': 0.2, 'ttoAvailable': True,
                'tto1': {'fip': away_xfip, 'gamesUsed': 5},
                'tto3': {'fip': away_xfip, 'gamesUsed': 3},
                'firstInningSplit': away_fi,
            },
            'bullpen': {'xFIP': 4.5, 'hlGrade': 'AVERAGE', 'hlAvailable': True, 'hlXFIP': 4.5},
        },
        'home': {
            'abbr': 'HHH', 'team': 'Home Team',
            'pitcher': {'name': 'Home SP'},
            'pitcherSavant': {
                'xFIP': home_xfip, 'seasonFIP': home_xfip, 'recentFIP': home_xfip,
                'avgIPperStart': 5.5, 'openerRole': False,
                'ttoSplit': 0.2, 'ttoAvailable': True,
                'tto1': {'fip': home_xfip, 'gamesUsed': 5},
                'tto3': {'fip': home_xfip, 'gamesUsed': 3},
                'firstInningSplit': home_fi,
            },
            'bullpen': {'xFIP': 4.2, 'hlGrade': 'AVERAGE', 'hlAvailable': True, 'hlXFIP': 4.2},
        },
        'awayTeamStats': {
            'offenseBaselineAdj': 5.0,
            'lineupConfirmed': away_lineup,
            'lineupConfirmedOfficial': away_lineup,
            'lineupPosted': away_lineup,
            'lineupStatus': 'confirmed' if away_lineup else 'missing',
            'lineupSource': 'mlb_stats_api',
            'lineupBattersExpected': 9,
            'lineupBattersFound': 9 if away_lineup else 0,
            'lineupBattersResolved': 9 if away_lineup else 0,
            'lineupAdjAvailable': away_lineup,
            'lineupAdjApplied': away_lineup,
            'lineupDataQuality': 'official' if away_lineup else 'none',
            'lineupStatusReason': '' if away_lineup else 'not_posted',
            'lineupAdj': 0.05 if away_lineup else None,
        },
        'homeTeamStats': {
            'offenseBaselineAdj': 5.0,
            'lineupConfirmed': home_lineup,
            'lineupConfirmedOfficial': home_lineup,
            'lineupPosted': home_lineup,
            'lineupStatus': 'confirmed' if home_lineup else 'missing',
            'lineupSource': 'mlb_stats_api',
            'lineupBattersExpected': 9,
            'lineupBattersFound': 9 if home_lineup else 0,
            'lineupBattersResolved': 9 if home_lineup else 0,
            'lineupAdjAvailable': home_lineup,
            'lineupAdjApplied': home_lineup,
            'lineupDataQuality': 'official' if home_lineup else 'none',
            'lineupStatusReason': '' if home_lineup else 'not_posted',
            'lineupAdj': 0.02 if home_lineup else None,
        },
        'park': {'parkFactor': 100},
        'pinnacleVF': {'away': 50.0, 'home': 50.0},
        'oddsApiCommenceTime': '2026-06-11T23:05:00Z',
        'kalshiKey': 'AAAHH',
        'kalshiGameTime': '1905',
        'odds': {
            'kalshi': {
                'ml': {
                    'away': -110, 'home': -110,
                    'away_ticker': 'KXMLBGAME-26JUN111905AAAHH-AAA',
                    'home_ticker': 'KXMLBGAME-26JUN111905AAAHH-HHH',
                    'source': 'kalshi_registry',
                },
                'nrfi_yrfi': {
                    'ticker':        'KXMLBRFI-26JUN111905AAAHH',
                    'nrfi_american': int(nrfi_implied / (100 - nrfi_implied) * -100)
                                     if nrfi_implied >= 50
                                     else int((100 - nrfi_implied) / nrfi_implied * 100),
                    'yrfi_american': int(yrfi_implied / (100 - yrfi_implied) * -100)
                                     if yrfi_implied >= 50
                                     else int((100 - yrfi_implied) / yrfi_implied * 100),
                    'nrfi_implied':  nrfi_implied,
                    'yrfi_implied':  yrfi_implied,
                    'source': 'kalshi_registry',
                },
                'f5ml': {
                    'away': -110, 'home': +100,
                    'away_ticker': 'KXMLBF5-26JUN111905AAAHH-AAA',
                    'home_ticker': 'KXMLBF5-26JUN111905AAAHH-HHH',
                    'source': 'kalshi_registry',
                },
                'team_totals': {
                    'away': {'best_ticker': 'KXMLBTEAMTOTAL-26JUN111905AAAHH-AAA5',
                             'line': 5, 'american': +120, 'implied_pct': 45.0},
                    'home': {'best_ticker': 'KXMLBTEAMTOTAL-26JUN111905AAAHH-HHH4',
                             'line': 4, 'american': +115, 'implied_pct': 46.5},
                },
                'rl': {
                    'best_ticker': 'KXMLBSPREAD-26JUN111905AAAHH-HHH2',
                    'american': +133, 'implied_pct': 43.0, 'team': 'HHH',
                },
                'total': {
                    'best_ticker': f'KXMLBTOTAL-26JUN111905AAAHH-{total_line}',
                    'line': total_line, 'american': -105,
                },
            },
        },
    }


def _row(ledger, market):
    for r in ledger:
        if r['market'] == market:
            return r
    raise KeyError(f'Market {market!r} not found in ledger')


# ── Tests ───────────────────────────────────────────────────────────────────────

class TestRule40MissingData(unittest.TestCase):
    """Rule 40 paper cap fires when firstInningSplit data is absent."""

    def setUp(self):
        # Both pitchers missing 1st-inning xERA, both lineups confirmed,
        # low total (7) so Rule 34 doesn't block NRFI, low YRFI implied (40%)
        # so model has a clear YRFI edge (model ~65% vs market 40%)
        self.game   = _make_game(away_fi_xera=None, home_fi_xera=None,
                                  yrfi_implied=40.0, nrfi_implied=60.0, total_line=7)
        self.ledger = evaluate_game(self.game)

    def test_rule40_still_fires_and_caps_under_the_kxmlbrfi_suspension(self):
        """Rule 40's own behaviour, re-expressed under the MLB-RSCH-0032 suspension.

        This test previously asserted YRFI stayed Accepted with confidence
        PAPER, because Rule 40 caps a tier rather than blocking a row. That
        remains true OF RULE 40 -- but the whole KXMLBRFI family is now
        suspended from real-money qualification, which supersedes any tier a
        rule would otherwise assign, so the row status is Rejected and the
        confidence is None regardless of what Rule 40 caps to.

        Rule 40's invariants are still asserted here: the gate fires, and the
        edge is still computed and logged rather than skipped. Only the
        family-level qualification changed."""
        row = _row(self.ledger, 'YRFI')
        self.assertEqual(row['status'], 'Rejected',
                         "KXMLBRFI is suspended, so YRFI cannot be Accepted")
        self.assertIsNone(row['confidence'],
                          f"suspended row must carry no confidence tier, got {row['confidence']}")
        self.assertIsNone(row.get('betSize'),
                          "a suspended row must never carry a real-money bet size")
        # Rule 40 itself still fires and still computes the edge.
        self.assertIn('Rule 40', ' '.join(row.get('gatesFired') or []),
                      f"Rule 40 must still fire, got: {row.get('gatesFired')}")
        self.assertIsNotNone(row.get('edge'), "Edge should still be computed and logged")
        self.assertIsNotNone(row.get('modelProb'), "modelProb must still be computed")

    def test_nrfi_is_paper_not_medium_when_rule40_missing(self):
        """NRFI with qualifying edge but missing Rule 40 data must be PAPER, not MEDIUM/HIGH."""
        row = _row(self.ledger, 'NRFI')
        # NRFI may be rejected due to low edge or Rule 34, but if accepted it must be PAPER.
        # With total_line=7 (below 8), Rule 34 does not fire.
        # With NRFI implied=60%, model NRFI will be ~35%, so edge is negative → Rejected.
        # We primarily test YRFI here; see test_nrfi_paper_with_positive_nrfi_edge for NRFI.
        # This test just confirms Rule 40 gate is in gatesFired when data missing.
        gates_all = ' '.join(row.get('gatesFired') or [])
        # Rule 40 should appear in gatesFired for NRFI too (set alongside YRFI)
        self.assertIn('Rule 40', gates_all,
                      f"Expected 'Rule 40' in NRFI gatesFired, got: {row.get('gatesFired')}")

    def test_yrfi_gatesFired_contains_rule40(self):
        """Rule 40 gate message must appear in YRFI gatesFired list."""
        row = _row(self.ledger, 'YRFI')
        gates_str = ' '.join(row.get('gatesFired') or [])
        self.assertIn('Rule 40', gates_str,
                      f"Expected 'Rule 40' in gatesFired, got: {row.get('gatesFired')}")

    def test_yrfi_gate_message_says_paper_cap(self):
        """Rule 40 gate message must say 'paper cap applied'."""
        row = _row(self.ledger, 'YRFI')
        gates_str = ' '.join(row.get('gatesFired') or []).lower()
        self.assertIn('paper cap', gates_str,
                      f"Expected 'paper cap' in gate message, got: {row.get('gatesFired')}")

    def test_yrfi_notes_reflect_missing_data(self):
        """YRFI notes must mention missing Factor 1 data."""
        row = _row(self.ledger, 'YRFI')
        notes = (row.get('notes') or '').lower()
        self.assertIn('missing', notes,
                      f"Expected missing-data note in YRFI notes, got: {row.get('notes')}")


class TestRule40NrfiPositiveEdge(unittest.TestCase):
    """NRFI with qualifying edge (low total, good starters) still gets PAPER when Rule 40 missing."""

    def setUp(self):
        # Give NRFI a positive edge: strong starters (xFIP 3.0), low total (6), NRFI implied 40%
        # Model NRFI will be higher than market → positive NRFI edge
        self.game   = _make_game(away_fi_xera=None, home_fi_xera=None,
                                  away_xfip=3.0, home_xfip=3.0,
                                  yrfi_implied=60.0, nrfi_implied=40.0,
                                  total_line=6)
        self.ledger = evaluate_game(self.game)

    def test_nrfi_is_paper_not_medium_when_rule40_missing(self):
        """NRFI with positive MEDIUM-level edge but missing Rule 40 must be PAPER."""
        row = _row(self.ledger, 'NRFI')
        # Rule 34 does not fire (total_line=6 < 8)
        # With xFIP 3.0 both sides and NRFI implied 40%, model NRFI should be >40%
        # → positive edge → confidence would be MEDIUM without Rule 40 → must be PAPER with it
        gates_str = ' '.join(row.get('gatesFired') or [])
        if row['status'] == 'Accepted':
            self.assertEqual(row['confidence'], 'PAPER',
                             f"NRFI with Rule 40 missing must be PAPER, got {row['confidence']}")
            self.assertIn('Rule 40', gates_str)
        # If rejected for other reason (edge below threshold), that's fine too —
        # just confirm Rule 40 gate is recorded
        self.assertIn('Rule 40', gates_str,
                      f"Rule 40 gate must appear even if NRFI rejected for other reasons: {row}")


class TestRule40WithCompleteData(unittest.TestCase):
    """With complete 1st-inning xERA data, YRFI/NRFI can be Accepted at MEDIUM."""

    def setUp(self):
        # Both pitchers have 1st-inning xERA data, both lineups confirmed
        # YRFI implied 40% → model ~65% → qualifying MEDIUM edge
        self.game   = _make_game(away_fi_xera=5.5, home_fi_xera=5.5,
                                  yrfi_implied=40.0, nrfi_implied=60.0, total_line=7)
        self.ledger = evaluate_game(self.game)

    def test_yrfi_can_be_medium_with_complete_rule40_data(self):
        """YRFI with complete Rule 40 data and MEDIUM edge must NOT be forced to PAPER by Rule 40."""
        row = _row(self.ledger, 'YRFI')
        gates_str = ' '.join(row.get('gatesFired') or [])
        self.assertNotIn('Rule 40', gates_str,
                         f"Rule 40 should NOT fire when data is present; gates: {row.get('gatesFired')}")
        if row['status'] == 'Accepted':
            self.assertNotEqual(row.get('confidence'), 'PAPER',
                                "YRFI should be MEDIUM (not PAPER) when Rule 40 data is complete and edge qualifies")

    def test_nrfi_not_blocked_by_rule40_with_complete_data(self):
        """NRFI with complete Rule 40 data must not have Rule 40 in gatesFired."""
        row = _row(self.ledger, 'NRFI')
        gates_str = ' '.join(row.get('gatesFired') or [])
        self.assertNotIn('Rule 40', gates_str,
                         f"Rule 40 should NOT fire when data is present; gates: {row.get('gatesFired')}")


class TestRule40IndependentOfRule52(unittest.TestCase):
    """Rule 40 paper cap fires independently of the Rule 52 lineup gate."""

    def test_rule40_fires_even_with_confirmed_lineups(self):
        """Rule 40 must fire when 1st-inning data is missing, regardless of lineup status."""
        game   = _make_game(away_fi_xera=None, home_fi_xera=None,
                             away_lineup=True, home_lineup=True,  # lineups confirmed
                             yrfi_implied=40.0, nrfi_implied=60.0, total_line=7)
        ledger = evaluate_game(game)
        row = _row(ledger, 'YRFI')
        gates_str = ' '.join(row.get('gatesFired') or [])
        self.assertIn('Rule 40', gates_str,
                      "Rule 40 must fire even when both lineups are confirmed")
        self.assertNotIn('Rule 52', gates_str,
                         "Rule 52 must NOT fire when both lineups are confirmed")

    def test_rule52_fires_independently_of_rule40(self):
        """Rule 52 must fire when lineup unconfirmed, even if Rule 40 data is present."""
        game   = _make_game(away_fi_xera=5.0, home_fi_xera=4.5,  # data present
                             away_lineup=False, home_lineup=True,  # away unconfirmed
                             yrfi_implied=40.0, nrfi_implied=60.0, total_line=7)
        ledger = evaluate_game(game)
        row = _row(ledger, 'YRFI')
        gates_str = ' '.join(row.get('gatesFired') or [])
        self.assertIn('Rule 52', gates_str,
                      "Rule 52 must fire when away lineup unconfirmed")
        self.assertNotIn('Rule 40', gates_str,
                         "Rule 40 must NOT fire when 1st-inning data IS present")


class TestRule40OneMissingPitcher(unittest.TestCase):
    """Rule 40 fires when even ONE pitcher's 1st-inning xERA is missing."""

    def test_rule40_fires_when_only_away_pitcher_missing(self):
        """Rule 40 must fire when only the away pitcher's 1st-inning xERA is absent."""
        game   = _make_game(away_fi_xera=None, home_fi_xera=4.5,  # only away missing
                             yrfi_implied=40.0, nrfi_implied=60.0, total_line=7)
        ledger = evaluate_game(game)
        row = _row(ledger, 'YRFI')
        gates_str = ' '.join(row.get('gatesFired') or [])
        self.assertIn('Rule 40', gates_str)
        if row['status'] == 'Accepted':
            self.assertEqual(row['confidence'], 'PAPER')

    def test_rule40_fires_when_only_home_pitcher_missing(self):
        """Rule 40 must fire when only the home pitcher's 1st-inning xERA is absent."""
        game   = _make_game(away_fi_xera=5.0, home_fi_xera=None,  # only home missing
                             yrfi_implied=40.0, nrfi_implied=60.0, total_line=7)
        ledger = evaluate_game(game)
        row = _row(ledger, 'YRFI')
        gates_str = ' '.join(row.get('gatesFired') or [])
        self.assertIn('Rule 40', gates_str)
        if row['status'] == 'Accepted':
            self.assertEqual(row['confidence'], 'PAPER')


if __name__ == '__main__':
    unittest.main(verbosity=2)
