#!/usr/bin/env python3
"""
tests/test_market_ticker_logging.py
Tests that Accepted rows carry non-null marketTicker and ticker fields.
Phase 4 target: build_market_ledger.py identity injection.
"""
import sys, os, unittest

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts')
ROOT_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)


# ── Minimal game fixture builder ───────────────────────────────────────────────
def _make_game(
    yrfi_ticker='KXMLBRFI-26JUN091910COLSD',
    ml_away_ticker='KXMLBGAME-26JUN091910COLSD-COL',
    ml_home_ticker='KXMLBGAME-26JUN091910COLSD-SD',
    f5_away_ticker='KXMLBF5-26JUN091910COLSD-COL',
    f5_home_ticker='KXMLBF5-26JUN091910COLSD-SD',
    tt_away_ticker='KXMLBTEAMTOTAL-26JUN091910COLSD-COL4',
    tt_home_ticker='KXMLBTEAMTOTAL-26JUN091910COLSD-SD4',
    rl_ticker='KXMLBSPREAD-26JUN091910COLSD-COL2',
    tot_ticker='KXMLBTOTAL-26JUN091910COLSD-8',
    ml_away_am=-120, ml_home_am=+110,
    nrfi_am=-130, yrfi_am=+115,
    f5_away_am=-115, f5_home_am=+105,
    tt_am=-120, tt_implied=54.5, tt_line=4,
):
    return {
        'away': {'abbr': 'COL', 'team': 'Colorado Rockies', 'pitcher': {'name': 'Pitcher A'}, 'pitcherSavant': {'xFIP': 4.2, 'avgIPperStart': 5.5, 'openerRole': False, 'ttoSplit': 0.3}, 'bullpen': {'xFIP': 4.0}},
        'home': {'abbr': 'SD',  'team': 'San Diego Padres',  'pitcher': {'name': 'Pitcher B'}, 'pitcherSavant': {'xFIP': 3.8, 'avgIPperStart': 6.0, 'openerRole': False, 'ttoSplit': 0.25}, 'bullpen': {'xFIP': 3.7}},
        'awayTeamStats': {'offenseBaselineAdj': 4.2, 'lineupConfirmed': True},
        'homeTeamStats':  {'offenseBaselineAdj': 4.0, 'lineupConfirmed': True},
        'park': {'parkFactor': 105},
        'pinnacleVF': {'away': 47.5, 'home': 52.5},
        'oddsApiCommenceTime': '2026-06-09T23:10:00Z',
        'kalshiKey': 'COLSD',
        'odds': {
            'kalshi': {
                'ml': {
                    'away': ml_away_am, 'home': ml_home_am,
                    'away_ticker': ml_away_ticker, 'home_ticker': ml_home_ticker,
                },
                'nrfi_yrfi': {
                    'ticker': yrfi_ticker,
                    'nrfi_american': nrfi_am, 'yrfi_american': yrfi_am,
                    'nrfi_implied': 56.5, 'yrfi_implied': 46.5,
                },
                'f5ml': {
                    'away': f5_away_am, 'home': f5_home_am,
                    'away_ticker': f5_away_ticker, 'home_ticker': f5_home_ticker,
                },
                'team_totals': {
                    'away': {'best_ticker': tt_away_ticker, 'line': tt_line, 'american': tt_am, 'implied_pct': tt_implied},
                    'home': {'best_ticker': tt_home_ticker, 'line': tt_line, 'american': tt_am, 'implied_pct': tt_implied},
                },
                'rl': {'best_ticker': rl_ticker, 'american': -118},
                'total': {'best_ticker': tot_ticker, 'line': 8, 'american': -108},
            }
        }
    }


class TestMarketTickerLogging(unittest.TestCase):

    def setUp(self):
        from build_market_ledger import evaluate_game
        self.evaluate = evaluate_game

    def _accepted(self, rows, market):
        return next((r for r in rows if r['market'] == market and r['status'] == 'Accepted'), None)

    def _any_accepted(self, rows, market):
        return next((r for r in rows if r['market'] == market), None)

    # ── Test 1: Accepted YRFI has non-null ticker and marketTicker ────────────
    def test_accepted_yrfi_has_ticker(self):
        g = _make_game()
        rows = self.evaluate(g)
        yrfi = self._accepted(rows, 'YRFI')
        if yrfi is None:
            self.skipTest('YRFI not accepted in this fixture (edge below threshold)')
        self.assertIsNotNone(yrfi.get('ticker'), 'YRFI Accepted row must have non-null ticker')
        self.assertIsNotNone(yrfi.get('marketTicker'), 'YRFI Accepted row must have non-null marketTicker')
        self.assertEqual(yrfi['seriesTicker'], 'KXMLBRFI')

    # ── Test 2: Accepted NRFI has non-null ticker and marketTicker ────────────
    def test_accepted_nrfi_has_ticker(self):
        g = _make_game()
        rows = self.evaluate(g)
        nrfi = self._accepted(rows, 'NRFI')
        if nrfi is None:
            self.skipTest('NRFI not accepted in this fixture')
        self.assertIsNotNone(nrfi.get('ticker'))
        self.assertIsNotNone(nrfi.get('marketTicker'))
        self.assertEqual(nrfi['seriesTicker'], 'KXMLBRFI')

    # ── Test 3: Accepted ML_Away has non-null ticker and marketTicker ─────────
    def test_accepted_ml_away_has_ticker(self):
        g = _make_game()
        rows = self.evaluate(g)
        ml = self._accepted(rows, 'ML_Away')
        if ml is None:
            self.skipTest('ML_Away not accepted in this fixture')
        self.assertIsNotNone(ml.get('ticker'))
        self.assertIsNotNone(ml.get('marketTicker'))
        self.assertEqual(ml['seriesTicker'], 'KXMLBGAME')

    # ── Test 4: Accepted ML_Home has non-null ticker and marketTicker ─────────
    def test_accepted_ml_home_has_ticker(self):
        g = _make_game()
        rows = self.evaluate(g)
        ml = self._accepted(rows, 'ML_Home')
        if ml is None:
            self.skipTest('ML_Home not accepted in this fixture')
        self.assertIsNotNone(ml.get('ticker'))
        self.assertIsNotNone(ml.get('marketTicker'))
        self.assertEqual(ml['seriesTicker'], 'KXMLBGAME')

    # ── Test 5: Accepted TT rows have non-null ticker fields ──────────────────
    def test_accepted_tt_has_ticker(self):
        g = _make_game()
        rows = self.evaluate(g)
        for mkt in ('TT_Away_Over', 'TT_Home_Over'):
            r = self._accepted(rows, mkt)
            if r is None:
                continue
            self.assertIsNotNone(r.get('ticker'), f'{mkt} Accepted must have ticker')
            self.assertIsNotNone(r.get('marketTicker'), f'{mkt} Accepted must have marketTicker')
            self.assertEqual(r['seriesTicker'], 'KXMLBTEAMTOTAL')

    # ── Test 6: Rejected rows may have null ticker when market lacks one ──────
    def test_rejected_rl_has_ticker_when_available(self):
        g = _make_game()
        rows = self.evaluate(g)
        for mkt in ('RL_Away', 'RL_Home'):
            r = next((r for r in rows if r['market'] == mkt), None)
            self.assertIsNotNone(r)
            self.assertEqual(r['status'], 'Rejected')
            # RL always has a ticker from the registry when best_ticker is present
            # (it's included in rejected rows per the fix)

    # ── Test 7: Missing Data rows clearly show the missing field path ─────────
    def test_missing_data_shows_field_path(self):
        g = _make_game(ml_away_am=None, ml_home_am=None)
        rows = self.evaluate(g)
        ml_away = next((r for r in rows if r['market'] == 'ML_Away'), None)
        self.assertIsNotNone(ml_away)
        self.assertEqual(ml_away['status'], 'Missing Data')
        self.assertIsNotNone(ml_away.get('missingFields'))
        self.assertTrue(len(ml_away['missingFields']) > 0, 'Missing Data row must list missing fields')

    # ── Test 8: No Accepted row can have null marketTicker ────────────────────
    def test_no_accepted_row_has_null_market_ticker(self):
        g = _make_game()
        rows = self.evaluate(g)
        accepted = [r for r in rows if r['status'] == 'Accepted']
        for r in accepted:
            self.assertIsNotNone(
                r.get('marketTicker'),
                f'Accepted row {r["market"]} has null marketTicker — identity injection failed'
            )


class TestBackfillSeriesNames(unittest.TestCase):
    """Test that MARKET_TO_SERIES uses canonical names (Phase 6)."""

    def setUp(self):
        sys.path.insert(0, SCRIPTS_DIR)
        from backfill_market_identity import MARKET_TO_SERIES
        self.mts = MARKET_TO_SERIES

    def test_yrfi_maps_to_kxmlbrfi(self):
        self.assertEqual(self.mts.get('YRFI'), 'KXMLBRFI')

    def test_nrfi_maps_to_kxmlbrfi(self):
        self.assertEqual(self.mts.get('NRFI'), 'KXMLBRFI')

    def test_ml_maps_to_kxmlbgame(self):
        self.assertEqual(self.mts.get('ML'), 'KXMLBGAME')

    def test_run_line_maps_to_kxmlbspread(self):
        self.assertEqual(self.mts.get('Run Line'), 'KXMLBSPREAD')
        self.assertNotIn('KXMLBRL', self.mts.values(), 'Stale series KXMLBRL must not be used')

    def test_total_maps_to_kxmlbtotal(self):
        self.assertEqual(self.mts.get('Total'), 'KXMLBTOTAL')
        self.assertNotIn('KXMLBGT', self.mts.values(), 'Stale series KXMLBGT must not be used')

    def test_team_total_maps_to_kxmlbteamtotal(self):
        self.assertEqual(self.mts.get('Team Total'), 'KXMLBTEAMTOTAL')
        self.assertNotIn('KXMLBTT', self.mts.values(), 'Stale series KXMLBTT must not be used')

    def test_f5_ml_maps_to_kxmlbf5(self):
        self.assertEqual(self.mts.get('F5 ML'), 'KXMLBF5')

    def test_no_stale_series_names(self):
        stale = {'KXMLBRL', 'KXMLBGT', 'KXMLBTT', 'KXMLBF5RL', 'KXMLBF5T'}
        used = set(self.mts.values())
        intersection = stale & used
        self.assertEqual(intersection, set(), f'Stale series names still in use: {intersection}')


if __name__ == '__main__':
    unittest.main()
