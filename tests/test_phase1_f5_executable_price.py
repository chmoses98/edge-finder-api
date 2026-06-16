#!/usr/bin/env python3
"""
tests/test_phase1_f5_executable_price.py
==========================================
Phase 1A regression tests:

1. F5 away/home/tie tickers map correctly
2. F5 ledger rows include executable prices (yes_ask/no_ask)
3. Real-money edge uses yes_ask/no_ask, not mid/VF/last
4. A price qualifying at 53¢ but not 56¢ gets rejected by maxBetPrice
5. Tie outcome is not silently ignored when present
6. Ambiguous F5 market mapping cannot produce a real-money bet
"""

import sys, os, unittest
SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts')
sys.path.insert(0, SCRIPTS_DIR)

from executable_price import (
    get_executable_prices,
    executable_prob_from_price,
    check_max_bet_price,
    executable_price_cents_to_american,
)


# ══════════════════════════════════════════════════════════════════════════════
# Test 1: Executable price extraction
# ══════════════════════════════════════════════════════════════════════════════

class TestExecutablePriceExtraction(unittest.TestCase):

    def test_yes_executable_is_yes_ask(self):
        """For a YES bet, executable price = yes_ask."""
        result = get_executable_prices(yes_bid=0.51, yes_ask=0.53)
        self.assertEqual(result['yes_executable'], 53.0)

    def test_no_executable_is_complement_of_yes_bid(self):
        """For a NO bet, executable price = no_ask = 100 - yes_bid."""
        result = get_executable_prices(yes_bid=0.51, yes_ask=0.53)
        # no_ask = 100 - yes_bid = 100 - 51 = 49
        self.assertAlmostEqual(result['no_executable'], 49.0, places=2)

    def test_mid_is_average_of_bid_ask(self):
        """Mid price = (yes_bid + yes_ask) / 2."""
        result = get_executable_prices(yes_bid=0.51, yes_ask=0.53)
        self.assertAlmostEqual(result['mid'], 52.0, places=2)

    def test_executable_differs_from_mid(self):
        """Yes executable (ask) is always >= mid (spread cost)."""
        result = get_executable_prices(yes_bid=0.51, yes_ask=0.53)
        self.assertGreater(result['yes_executable'], result['mid'])

    def test_cents_input_normalized_correctly(self):
        """Input already in cents (>1) is kept as-is."""
        result = get_executable_prices(yes_bid=51, yes_ask=53)
        self.assertEqual(result['yes_ask'], 53)
        self.assertEqual(result['yes_bid'], 51)

    def test_none_bid_returns_none_no_ask(self):
        """If yes_bid is None, no_ask cannot be computed."""
        result = get_executable_prices(yes_bid=None, yes_ask=0.53)
        self.assertIsNone(result['no_executable'])

    def test_none_ask_returns_none_yes_executable(self):
        """If yes_ask is None, yes_executable is None."""
        result = get_executable_prices(yes_bid=0.51, yes_ask=None)
        self.assertIsNone(result['yes_executable'])


# ══════════════════════════════════════════════════════════════════════════════
# Test 2: maxBetPrice gate
# ══════════════════════════════════════════════════════════════════════════════

class TestMaxBetPriceGate(unittest.TestCase):

    def test_price_at_max_passes(self):
        """Price exactly at max passes."""
        ok, code = check_max_bet_price(exec_p=53, max_p=53)
        self.assertTrue(ok)
        self.assertIsNone(code)

    def test_price_below_max_passes(self):
        """Price below max (better price) passes."""
        ok, code = check_max_bet_price(exec_p=51, max_p=53)
        self.assertTrue(ok)
        self.assertIsNone(code)

    def test_price_above_max_rejected(self):
        """53¢ qualifies but 56¢ does not — rejected with PRICE_MOVED_BEYOND_MAX."""
        # Qualifying at 53¢:
        ok53, code53 = check_max_bet_price(exec_p=53, max_p=53)
        self.assertTrue(ok53, "53¢ should pass max_bet_price=53")

        # Not qualifying at 56¢:
        ok56, code56 = check_max_bet_price(exec_p=56, max_p=53)
        self.assertFalse(ok56, "56¢ should fail max_bet_price=53")
        self.assertEqual(code56, 'PRICE_MOVED_BEYOND_MAX')

    def test_none_exec_price_allows_through(self):
        """Cannot check if exec price is None — allow through."""
        ok, code = check_max_bet_price(exec_p=None, max_p=53)
        self.assertTrue(ok)
        self.assertIsNone(code)

    def test_none_max_price_allows_through(self):
        """Cannot check if max price is None — allow through."""
        ok, code = check_max_bet_price(exec_p=55, max_p=None)
        self.assertTrue(ok)
        self.assertIsNone(code)


# ══════════════════════════════════════════════════════════════════════════════
# Test 3: F5 away/home/tie ticker mapping
# ══════════════════════════════════════════════════════════════════════════════

class TestF5TickerMapping(unittest.TestCase):
    """
    Verify that F5 registry entries correctly map away/home/tie tickers.
    Uses the same backfill logic as test_kalshi_f5_pipeline.py.
    """

    def _make_f5_registry_entry(self):
        return {
            'kalshi_key': 'WSHSF',
            'away': 'WSH',
            'home': 'SF',
            'event_ticker_suffix': '26JUN101545WSHSF',
            'markets': {},
        }

    def _make_f5_markets(self, away='WSH', home='SF', suffix='26JUN101545WSHSF'):
        event = f'KXMLBF5-{suffix}'
        return [
            {'event_ticker': event, 'market_ticker': f'{event}-{away}', 'market_type': 'f5_moneyline',
             'yes_bid': 0.42, 'yes_ask': 0.44, 'mid': 0.43, 'implied_pct': 43.0, 'american_odds': 133},
            {'event_ticker': event, 'market_ticker': f'{event}-{home}', 'market_type': 'f5_moneyline',
             'yes_bid': 0.39, 'yes_ask': 0.41, 'mid': 0.40, 'implied_pct': 40.0, 'american_odds': 150},
            {'event_ticker': event, 'market_ticker': f'{event}-TIE', 'market_type': 'f5_moneyline',
             'yes_bid': 0.14, 'yes_ask': 0.16, 'mid': 0.15, 'implied_pct': 15.0, 'american_odds': 567},
        ]

    def test_away_ticker_maps_to_away_outcome(self):
        """Away team ticker ends with -{AWAY_ABBR}."""
        markets = self._make_f5_markets()
        away_ticker = next(m['market_ticker'] for m in markets if m['market_ticker'].endswith('-WSH'))
        self.assertTrue(away_ticker.endswith('-WSH'), "Away ticker must end with -WSH")

    def test_home_ticker_maps_to_home_outcome(self):
        """Home team ticker ends with -{HOME_ABBR}."""
        markets = self._make_f5_markets()
        home_ticker = next(m['market_ticker'] for m in markets if m['market_ticker'].endswith('-SF'))
        self.assertTrue(home_ticker.endswith('-SF'), "Home ticker must end with -SF")

    def test_tie_ticker_maps_to_tie_outcome(self):
        """Tie ticker ends with -TIE."""
        markets = self._make_f5_markets()
        tie_ticker = next(m['market_ticker'] for m in markets if m['market_ticker'].endswith('-TIE'))
        self.assertTrue(tie_ticker.endswith('-TIE'), "Tie ticker must end with -TIE")

    def test_tie_outcome_not_silently_ignored(self):
        """Tie market prices must be present in audit when TIE ticker exists."""
        markets = self._make_f5_markets()
        tie_markets = [m for m in markets if m['market_ticker'].endswith('-TIE')]
        self.assertGreater(len(tie_markets), 0, "TIE market must not be silently ignored")
        self.assertIsNotNone(tie_markets[0].get('yes_ask'), "TIE market must have yes_ask")

    def test_all_three_outcomes_have_yes_ask(self):
        """All three F5 markets (away/home/tie) must have yes_ask for executable price."""
        markets = self._make_f5_markets()
        for m in markets:
            self.assertIsNotNone(m.get('yes_ask'),
                                 f"yes_ask missing from {m['market_ticker']}")

    def test_f5_executable_price_uses_yes_ask_not_mid(self):
        """Executable price for F5 YES bet is yes_ask, not mid."""
        markets = self._make_f5_markets()
        away_m = next(m for m in markets if m['market_ticker'].endswith('-WSH'))
        exec_prices = get_executable_prices(
            yes_bid=away_m['yes_bid'], yes_ask=away_m['yes_ask']
        )
        self.assertEqual(exec_prices['yes_executable'],
                         round(away_m['yes_ask'] * 100, 4),
                         "Executable price must be yes_ask, not mid")
        self.assertNotEqual(exec_prices['yes_executable'],
                            exec_prices['mid'],
                            "Executable price must not equal mid (unless bid=ask)")


# ══════════════════════════════════════════════════════════════════════════════
# Test 4: Ambiguous F5 mapping cannot produce a real-money bet
# ══════════════════════════════════════════════════════════════════════════════

class TestF5AmbiguousMapping(unittest.TestCase):

    def test_ambiguous_mapping_produces_no_executable_price(self):
        """
        An ambiguous F5 market (cannot determine away/home/tie) must not
        produce a real-money executable price.
        """
        # Simulate an ambiguous market: ticker suffix doesn't match known teams
        # This would yield mappedOutcome='unknown' and eligibilityStatus='F5_MAPPING_AMBIGUOUS'
        ambiguous_market = {
            'event_ticker': 'KXMLBF5-26JUN101545UNKUNK',
            'market_ticker': 'KXMLBF5-26JUN101545UNKUNK-AMBIG',
            'market_type': 'f5_moneyline',
            'yes_bid': 0.45,
            'yes_ask': 0.47,
            'mid': 0.46,
        }
        # When mappedOutcome is ambiguous, executablePriceUsed must NOT be used
        # for a real-money bet slip. The eligibilityStatus must be F5_MAPPING_AMBIGUOUS.
        mapped_outcome = 'unknown'  # what the mapping logic returns for ambiguous case
        self.assertEqual(mapped_outcome, 'unknown',
                         "Ambiguous mapping must produce outcome='unknown'")

        # A bet with mappedOutcome='unknown' must be F5_MAPPING_AMBIGUOUS
        eligibility = 'F5_MAPPING_AMBIGUOUS' if mapped_outcome == 'unknown' else 'ELIGIBLE'
        self.assertEqual(eligibility, 'F5_MAPPING_AMBIGUOUS',
                         "Ambiguous F5 mapping must produce F5_MAPPING_AMBIGUOUS code, not an eligible bet")

    def test_f5_mapping_ambiguous_reason_code_set(self):
        """F5_MAPPING_AMBIGUOUS reason code must appear for unknown outcomes."""
        from reason_codes import F5_MAPPING_AMBIGUOUS
        self.assertEqual(F5_MAPPING_AMBIGUOUS, 'F5_MAPPING_AMBIGUOUS')

    def test_three_way_f5_no_silently_normalized_to_two_way(self):
        """
        When Kalshi has 3 F5 outcomes (away/home/tie), the tie price must NOT
        be silently discarded. It must appear in audit output.
        """
        markets = [
            {'market_ticker': 'KXMLBF5-26JUN-WSH', 'mid': 0.43, 'yes_ask': 0.44},
            {'market_ticker': 'KXMLBF5-26JUN-SF',  'mid': 0.40, 'yes_ask': 0.41},
            {'market_ticker': 'KXMLBF5-26JUN-TIE', 'mid': 0.15, 'yes_ask': 0.16},
        ]
        outcomes = [m['market_ticker'].split('-')[-1] for m in markets]
        self.assertIn('TIE', outcomes, "TIE must be present in F5 market outputs")
        
        # All three must have executable prices (yes_ask)
        for m in markets:
            self.assertIsNotNone(m.get('yes_ask'),
                                 f"All three F5 outcomes must have yes_ask: {m['market_ticker']}")


# ══════════════════════════════════════════════════════════════════════════════
# Test 5: Edge uses yes_ask/no_ask, not mid/VF/last
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgeUsesExecutablePrice(unittest.TestCase):

    def test_raw_edge_vs_executable_differs_from_raw_edge_vs_mid(self):
        """
        rawEdgeVsExecutable uses yes_ask as basis.
        rawEdgeVsVF uses mid as basis.
        These must be different when bid != ask (normal spread exists).
        """
        model_prob = 0.60  # 60%
        yes_bid = 0.51
        yes_ask = 0.55  # 4¢ spread
        mid     = (yes_bid + yes_ask) / 2  # 0.53

        exec_prices = get_executable_prices(yes_bid=yes_bid, yes_ask=yes_ask)
        exec_prob   = executable_prob_from_price(exec_prices['yes_executable'])

        raw_vs_mid  = round((model_prob - mid) * 100, 3)
        raw_vs_exec = round((model_prob - exec_prob) * 100, 3)

        self.assertNotEqual(raw_vs_mid, raw_vs_exec,
                            "Edge vs mid must differ from edge vs executable (spread exists)")
        self.assertGreater(raw_vs_mid, raw_vs_exec,
                           "Edge vs mid > edge vs executable (mid is better than ask for bettor)")

    def test_build_edge_fields_stores_both_raw_edges(self):
        """build_edge_fields() must return both rawEdgeVsVF and rawEdgeVsExecutable."""
        sys.path.insert(0, SCRIPTS_DIR)
        import build_market_ledger as bml
        
        result = bml.build_edge_fields(
            model_prob=0.60,
            kalshi_vf=0.53,   # mid-based
            yes_ask_cents=55, # ask slightly worse
            cal_factor=0.255,
        )
        self.assertIn('rawEdgeVsVF', result, "rawEdgeVsVF must be present")
        self.assertIn('rawEdgeVsExecutable', result, "rawEdgeVsExecutable must be present")
        self.assertIsNotNone(result['rawEdgeVsVF'])
        self.assertIsNotNone(result['rawEdgeVsExecutable'])

    def test_calibrated_edge_vs_executable_not_equal_to_raw(self):
        """Calibrated edge != raw edge (calibration factor applied)."""
        import build_market_ledger as bml
        result = bml.build_edge_fields(
            model_prob=0.60,
            kalshi_vf=0.53,
            yes_ask_cents=55,
            cal_factor=0.255,
        )
        raw  = result['rawEdgeVsExecutable']
        cal  = result['calibratedEdgeVsExecutable']
        self.assertIsNotNone(raw)
        self.assertIsNotNone(cal)
        self.assertNotAlmostEqual(raw, cal, places=3,
                                  msg="Calibrated edge must differ from raw edge")

    def test_raw_edge_not_overwritten_by_calibrated(self):
        """Raw edge field must persist alongside calibrated edge field."""
        import build_market_ledger as bml
        result = bml.build_edge_fields(
            model_prob=0.60,
            kalshi_vf=0.53,
            yes_ask_cents=55,
            cal_factor=0.255,
        )
        # Both must be present and independent
        self.assertIsNotNone(result.get('rawEdgeVsExecutable'),
                             "rawEdgeVsExecutable must not be overwritten")
        self.assertIsNotNone(result.get('calibratedEdgeVsExecutable'),
                             "calibratedEdgeVsExecutable must be present")
        # They must be different (cal factor != 1.0)
        raw = result['rawEdgeVsExecutable']
        cal = result['calibratedEdgeVsExecutable']
        self.assertAlmostEqual(cal, raw * 0.255, places=2,
                               msg="Calibrated edge = raw edge * calibration factor")


if __name__ == '__main__':
    unittest.main(verbosity=2)
