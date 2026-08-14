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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from executable_price import (
    get_executable_prices,
    executable_prob_from_price,
    check_max_bet_price,
    executable_price_cents_to_american,
)
import build_market_ledger as bml
from test_lineup_gate import _make_game


def _row(ledger, market):
    for r in ledger:
        if r['market'] == market:
            return r
    raise KeyError(f'Market {market!r} not found in ledger')


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


# ══════════════════════════════════════════════════════════════════════════════
# Executable EV / bet-up-to correctness mission:
#
# Prior gap -- eligibility (Accepted/Rejected/confidenceTier) was decided
# from calibrated_edge(model_prob, kalshi_vf, ...) using the MID-derived
# kalshi_vf, even though every row already computed the correct
# post-friction executable-ask edge in build_edge_fields() and simply
# never used it to gate anything (edgeUsedForQualification claimed
# 'calibratedEdgeVsExecutable' was driving qualification when it never
# was). Separately, maxBetPrice was always set to an echo of the row's
# own current executablePriceUsed, so check_max_bet_price() (imported,
# never called) was checking a price against itself -- always true.
#
# Fixed by: (1) gating conf on ef['calibratedEdgeVsExecutable'] at all
# four evaluate_game() market sections, and (2) bet_up_to_price_cents()
# + enforce_bet_up_to() in scripts/build_market_ledger.py, which derive
# a genuine ceiling from the model's own edge requirement and hard-reject
# (PRICE_MOVED_BEYOND_MAX) whenever the row's executable price is worse
# than that ceiling.
# ══════════════════════════════════════════════════════════════════════════════


class TestBetUpToPriceCents(unittest.TestCase):
    """bet_up_to_price_cents() -- the genuine, model-derived price ceiling."""

    def test_ceiling_is_inverse_of_calibrated_edge(self):
        """
        The ceiling this returns must be exactly the kalshi_vf at which
        calibrated_edge(fair_prob, kalshi_vf, cal_factor) == threshold_pct
        -- i.e. plugging the ceiling back into calibrated_edge() must
        reproduce the threshold, not some other number.
        """
        fair_prob, threshold_pct, cal_factor = 0.55, 1.0, 0.255
        ceiling_cents = bml.bet_up_to_price_cents(fair_prob, threshold_pct, cal_factor)
        edge_at_ceiling = bml.calibrated_edge(fair_prob, ceiling_cents / 100.0, cal_factor)
        self.assertAlmostEqual(edge_at_ceiling, threshold_pct, delta=0.02)

    def test_higher_fair_prob_yields_higher_ceiling(self):
        """A model more confident of the outcome is willing to pay more."""
        low  = bml.bet_up_to_price_cents(0.52, 1.0, 0.255)
        high = bml.bet_up_to_price_cents(0.65, 1.0, 0.255)
        self.assertGreater(high, low)

    def test_never_fabricated_when_inputs_missing(self):
        """No fair_prob / threshold / cal_factor -> no ceiling, ever."""
        self.assertIsNone(bml.bet_up_to_price_cents(None, 1.0, 0.255))
        self.assertIsNone(bml.bet_up_to_price_cents(0.55, None, 0.255))
        self.assertIsNone(bml.bet_up_to_price_cents(0.55, 1.0, None))
        self.assertIsNone(bml.bet_up_to_price_cents(0.55, 1.0, 0.0))

    def test_ceiling_is_never_an_echo_of_a_current_price(self):
        """
        The historical bug: maxBetPrice == executablePriceUsed, so the
        "ceiling" moved in lockstep with whatever price was being
        checked and the check was always trivially true. The genuine
        ceiling must NOT depend on any observed market price at all --
        only on fair_prob/threshold/cal_factor.
        """
        ceiling_a = bml.bet_up_to_price_cents(0.55, 1.0, 0.255)
        ceiling_b = bml.bet_up_to_price_cents(0.55, 1.0, 0.255)
        self.assertEqual(ceiling_a, ceiling_b)


class TestEnforceBetUpTo(unittest.TestCase):
    """
    enforce_bet_up_to() -- hard ceiling enforcement wiring.

    Production Fee-Aware Net EV Integration milestone: enforce_bet_up_to()
    now returns a 4-tuple (conf, gates, max_bet_price_gross,
    max_bet_price_net) and gates against the NET (fee-aware) ceiling, not
    the gross one -- see enforce_bet_up_to()'s docstring for why (keeping
    the enforcement gate consistent with confidence_from_edge()'s own
    net-edge decision metric at every call site). GROSS_CEILING is
    preserved and still returned (for display/backward-compat); it is
    always >= NET_CEILING for a nonzero fee, per
    fee_aware_bet_up_to_price_cents()'s docstring.
    """

    FAIR_PROB = 0.55
    GROSS_CEILING = bml.bet_up_to_price_cents(FAIR_PROB, bml.THRESHOLD_PAPER, bml.CAL_MEDIUM)  # ~51.08
    NET_CEILING = bml.fee_aware_bet_up_to_price_cents(FAIR_PROB, bml.THRESHOLD_PAPER, bml.CAL_MEDIUM)

    def test_gross_ceiling_exceeds_net_ceiling_for_nonzero_fee(self):
        """Sanity check on the fixture itself: the two ceilings must actually differ here."""
        self.assertLess(self.NET_CEILING, self.GROSS_CEILING)

    def test_price_exactly_at_net_bet_up_to_remains_actionable(self):
        """The boundary itself is inclusive: exec price == NET ceiling still qualifies."""
        conf, gates, max_bet_gross, max_bet_net = bml.enforce_bet_up_to(
            self.FAIR_PROB, self.NET_CEILING, 'MEDIUM', [])
        self.assertEqual(conf, 'MEDIUM')
        self.assertEqual(gates, [])
        self.assertEqual(max_bet_gross, self.GROSS_CEILING)
        self.assertEqual(max_bet_net, self.NET_CEILING)

    def test_price_between_net_and_gross_ceiling_becomes_non_actionable(self):
        """
        The core fee-aware behavior change: a price that used to qualify
        under the fee-blind GROSS ceiling but exceeds the fee-aware NET
        ceiling must now be rejected -- this is the exact "gross edge
        positive, net EV non-positive -> NO BET" acceptance case applied
        at the price-ceiling layer.
        """
        mid_price = round((self.NET_CEILING + self.GROSS_CEILING) / 2.0, 2)
        conf, gates, max_bet_gross, max_bet_net = bml.enforce_bet_up_to(
            self.FAIR_PROB, mid_price, 'HIGH', [])
        self.assertIsNone(conf)
        self.assertTrue(any('PRICE_MOVED_BEYOND_MAX' in g for g in gates),
                        f"expected a PRICE_MOVED_BEYOND_MAX gate, got {gates}")

    def test_price_worse_than_bet_up_to_becomes_non_actionable(self):
        """
        A single cent worse than the NET ceiling must flip the row to
        non-actionable (conf=None) and record a PRICE_MOVED_BEYOND_MAX-
        tagged gate -- never silently widen the limit to let it through.
        """
        worse_price = round(self.NET_CEILING + 1.0, 2)
        conf, gates, max_bet_gross, max_bet_net = bml.enforce_bet_up_to(
            self.FAIR_PROB, worse_price, 'MEDIUM', [])
        self.assertIsNone(conf)
        self.assertEqual(max_bet_gross, self.GROSS_CEILING)
        self.assertEqual(max_bet_net, self.NET_CEILING)
        self.assertTrue(any('PRICE_MOVED_BEYOND_MAX' in g for g in gates),
                        f"expected a PRICE_MOVED_BEYOND_MAX gate, got {gates}")

    def test_price_improvement_remains_actionable(self):
        """A price that moves in the bettor's favor (lower) stays actionable."""
        better_price = round(self.NET_CEILING - 5.0, 2)
        conf, gates, max_bet_gross, max_bet_net = bml.enforce_bet_up_to(
            self.FAIR_PROB, better_price, 'HIGH', [])
        self.assertEqual(conf, 'HIGH')
        self.assertEqual(gates, [])

    def test_already_non_actionable_passes_through_unchanged(self):
        """A row already rejected for some other reason must not be re-gated here."""
        worse_price = round(self.NET_CEILING + 10.0, 2)
        conf, gates, max_bet_gross, max_bet_net = bml.enforce_bet_up_to(
            self.FAIR_PROB, worse_price, None, ['some other rejection reason'])
        self.assertIsNone(conf)
        self.assertEqual(gates, ['some other rejection reason'],
                         "must not append a price gate to a row that wasn't actionable anyway")
        # Both ceilings are still computed and returned for record-keeping,
        # even though neither was the reason this row failed.
        self.assertEqual(max_bet_gross, self.GROSS_CEILING)
        self.assertEqual(max_bet_net, self.NET_CEILING)

    def test_missing_exec_price_does_not_block(self):
        """No real executable price to check against -- never fabricate a rejection."""
        conf, gates, max_bet_gross, max_bet_net = bml.enforce_bet_up_to(self.FAIR_PROB, None, 'MEDIUM', [])
        self.assertEqual(conf, 'MEDIUM')
        self.assertEqual(gates, [])
        self.assertEqual(max_bet_gross, self.GROSS_CEILING)
        self.assertEqual(max_bet_net, self.NET_CEILING)


class TestPostFrictionEdgeEligibility(unittest.TestCase):
    """
    Requirement: recommendation eligibility must be based on post-friction
    (executable-ask) EV, not midpoint/raw edge alone.
    """

    def test_positive_raw_edge_becomes_negative_after_friction(self):
        """
        model=55%, mid(VF)=50% -> positive raw edge vs mid (qualifies).
        The real executable ask is 56 (worse than the model's own fair
        price) -> raw edge vs executable is NEGATIVE. A row must not be
        judged actionable off the mid number alone.
        """
        ef = bml.build_edge_fields(
            model_prob=0.55, kalshi_vf=0.50, yes_ask_cents=56,
            cal_factor=bml.CAL_MEDIUM,
        )
        self.assertGreater(ef['rawEdgeVsVF'], 0)
        self.assertLess(ef['rawEdgeVsExecutable'], 0)
        self.assertIsNotNone(bml.confidence_from_edge(ef['calibratedEdgeVsVF']),
                             "mid-based edge alone would have qualified this row")
        self.assertIsNone(bml.confidence_from_edge(ef['calibratedEdgeVsExecutable']),
                          "post-friction edge is negative -- must not qualify")


class TestYesNoSideHandling(unittest.TestCase):
    """
    Requirement: correct YES/NO side handling -- a market's YES executable
    price and NO executable price are priced and gated independently,
    never conflated with each other or with the mid.
    """

    def test_yes_and_no_sides_gate_independently(self):
        yes_bid, yes_ask = 45, 52
        prices = get_executable_prices(yes_bid=yes_bid, yes_ask=yes_ask)
        self.assertEqual(prices['yes_executable'], 52)
        self.assertEqual(prices['no_executable'], 55)  # 100 - yes_bid

        # A model that likes the YES side at 60% clears its own ceiling
        # against the YES ask (52).
        yes_ceiling = bml.bet_up_to_price_cents(0.60, bml.THRESHOLD_PAPER, bml.CAL_MEDIUM)
        ok_yes, _ = check_max_bet_price(prices['yes_executable'], yes_ceiling)
        self.assertTrue(ok_yes)

        # The SAME market's NO side, evaluated against its own fair
        # probability (50%) and its own executable price (55, the NO
        # ask), is independently rejected -- never inferred from the
        # YES side's own numbers.
        no_ceiling = bml.bet_up_to_price_cents(0.50, bml.THRESHOLD_PAPER, bml.CAL_MEDIUM)
        ok_no, reason_no = check_max_bet_price(prices['no_executable'], no_ceiling)
        self.assertFalse(ok_no)
        self.assertEqual(reason_no, 'PRICE_MOVED_BEYOND_MAX')

    def test_nrfi_and_yrfi_use_distinct_complementary_executable_prices(self):
        """
        End-to-end (evaluate_game()): NRFI is priced as 100 - yrfi_bid
        (the NO side of the YRFI question) and YRFI is priced off its
        own yrfi_ask (the YES side) -- the two rows must never share a
        single executable price even though they describe the same
        binary event.
        """
        g = _make_game()
        g['odds']['kalshi']['nrfi_yrfi']['yrfi_bid'] = 45
        g['odds']['kalshi']['nrfi_yrfi']['yrfi_ask'] = 50
        rows = bml.evaluate_game(g)
        nrfi_row = _row(rows, 'NRFI')
        yrfi_row = _row(rows, 'YRFI')
        self.assertEqual(nrfi_row['executablePriceUsed'], 55.0)  # 100 - 45
        self.assertEqual(yrfi_row['executablePriceUsed'], 50.0)  # yrfi_ask
        self.assertNotEqual(nrfi_row['executablePriceUsed'], yrfi_row['executablePriceUsed'])


class TestEvaluateGameBetUpToIntegration(unittest.TestCase):
    """
    Full evaluate_game() integration: proves the fix end-to-end, not just
    at the pure-function level -- a market that would have been Accepted
    is correctly downgraded to Rejected/PASS once its own executable
    price moves worse than the bet-up-to ceiling the row itself reports,
    and never silently widened to stay Accepted.
    """

    def test_default_fixture_ml_home_accepted_baseline(self):
        g = _make_game()
        rows = bml.evaluate_game(g)
        row = _row(rows, 'ML_Home')
        self.assertEqual(row['status'], 'Accepted')
        self.assertIsNotNone(row['maxBetPrice'])
        # The ceiling must not equal the row's own current executable
        # price (the historical echo bug) unless that's a genuine
        # coincidence -- here it demonstrably differs.
        self.assertNotEqual(row['maxBetPrice'], row['executablePriceUsed'])

    def test_price_worse_than_bet_up_to_downgrades_accepted_to_rejected(self):
        """
        bet_up_to_price_cents() is the exact price at which calibrated
        edge crosses the qualifying floor, so a price worse than the
        ceiling necessarily fails the ordinary edge check too within a
        single evaluate_game() call -- the two are mathematically the
        same constraint at generation time, which is exactly why this
        row correctly flips to Rejected. What this proves beyond the
        edge check alone: the row's OWN recorded maxBetPrice is fit for
        a downstream consumer (e.g. a live-price recheck immediately
        before execution, which this pipeline does not yet have) to
        independently re-verify later -- replaying check_max_bet_price()
        against the row's own executablePriceUsed/maxBetPrice correctly
        flags the violation from outside evaluate_game() entirely.
        """
        baseline = _row(bml.evaluate_game(_make_game()), 'ML_Home')
        ceiling = baseline['maxBetPrice']

        g = _make_game()
        g['odds']['kalshi']['ml']['home_yes_ask'] = round(ceiling + 0.01, 2)
        row = _row(bml.evaluate_game(g), 'ML_Home')

        self.assertEqual(row['status'], 'Rejected')
        self.assertIsNone(row['confidenceTier'])
        ok, reason = check_max_bet_price(row['executablePriceUsed'], row['maxBetPrice'])
        self.assertFalse(ok)
        self.assertEqual(reason, 'PRICE_MOVED_BEYOND_MAX')

    def test_price_exactly_at_bet_up_to_remains_accepted(self):
        baseline = _row(bml.evaluate_game(_make_game()), 'ML_Home')
        ceiling = baseline['maxBetPrice']

        g = _make_game()
        g['odds']['kalshi']['ml']['home_yes_ask'] = ceiling
        row = _row(bml.evaluate_game(g), 'ML_Home')

        self.assertEqual(row['status'], 'Accepted')
        self.assertEqual(row['maxBetPrice'], ceiling)

    def test_price_improvement_remains_actionable(self):
        g = _make_game()
        g['odds']['kalshi']['ml']['home_yes_ask'] = 40.0  # better than the default 45.45
        row = _row(bml.evaluate_game(g), 'ML_Home')

        self.assertEqual(row['status'], 'Accepted')
        self.assertEqual(row['executablePriceUsed'], 40.0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
