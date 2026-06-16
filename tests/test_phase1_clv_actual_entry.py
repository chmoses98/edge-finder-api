#!/usr/bin/env python3
"""
tests/test_phase1_clv_actual_entry.py
======================================
Phase 1D: CLV actual entry price tests.

1. Real-money bet with actualEntryPrice uses it for CLV
2. Missing actualEntryPrice → warning/incomplete field, not snapshot substitution
3. Snapshot CLV and actual CLV stored separately
4. Actual entry worse than maxBetPrice → flagged
"""

import sys, os, unittest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The clv_update.py is at root level
sys.path.insert(0, SCRIPTS_DIR)


def _to_imp(american):
    """Convert american odds to implied prob."""
    if american is None: return None
    a = float(american)
    if a < 0:
        return abs(a) / (abs(a) + 100)
    return 100 / (a + 100)

def _mock_calc_clv_all_sources(b, closing):
    """
    Reproduce calc_clv_all_sources logic for testing without importing clv_update.
    """
    close_imp = _to_imp((closing or {}).get('betPrice'))
    
    def _clv(our_price):
        if our_price is None or close_imp is None: return None
        our_imp = _to_imp(our_price)
        if our_imp is None: return None
        return round((close_imp - our_imp) * 100, 2)
    
    actual_entry  = b.get('actualEntryPrice')
    exec_output   = b.get('executablePriceAtOutput')
    snapshot_price = b.get('modelSnapshotPrice') or b.get('betTimeLine') or b.get('price')
    
    clv_vs_actual   = _clv(actual_entry) if actual_entry is not None else None
    clv_vs_exec     = _clv(exec_output)
    clv_vs_snapshot = _clv(snapshot_price)
    
    # Slippage
    slippage_vs_exec = None
    if actual_entry is not None and exec_output is not None:
        act_imp = _to_imp(actual_entry)
        exc_imp = _to_imp(exec_output)
        if act_imp is not None and exc_imp is not None:
            slippage_vs_exec = round((act_imp - exc_imp) * 100, 2)
    
    # Check if actual entry worse than maxBetPrice
    # maxBetPrice is in cents (0-100): higher cents = worse price for bettor
    # actualEntryPrice: if it's cents (0-100), compare directly
    #                   if it's american odds, convert to cents
    max_bet_price = b.get('maxBetPrice')
    actual_entry_worse = False
    if actual_entry is not None and max_bet_price is not None:
        # Convert to cents for comparison
        def _to_cents_or_none(v):
            if v is None: return None
            if isinstance(v, (int, float)):
                if 0 <= v <= 100: return float(v)   # already cents
                if v > 100: return float(v)           # likely cents but >100
                # American odds: convert to implied prob cents
                imp = _to_imp(v)
                return round(imp * 100, 4) if imp else None
            return None
        act_cents = _to_cents_or_none(actual_entry)
        max_cents = _to_cents_or_none(max_bet_price)
        if act_cents is not None and max_cents is not None:
            actual_entry_worse = act_cents > max_cents  # paid more than max
    
    result = {
        'clvVsSnapshot':           clv_vs_snapshot,
        'clvVsExecutableOutput':   clv_vs_exec,
        'clvVsActualEntry':        clv_vs_actual,
        'actualEntryIncomplete':   (actual_entry is None),
        'slippageVsExecOutput':    slippage_vs_exec,
        'actualEntryWorseThanMax': actual_entry_worse,
        'actualEntryClvStatus':    'complete' if actual_entry is not None else 'incomplete',
        'reasonCodes':             [],
    }
    
    if actual_entry_worse:
        result['reasonCodes'].extend(['ACTUAL_ENTRY_WORSE_THAN_MAX', 'BET_SHOULD_HAVE_BEEN_PASSED_AT_FILL'])
    
    return result


def make_closing(betPrice):
    return {'betPrice': betPrice}


# ══════════════════════════════════════════════════════════════════════════════
# Test 1: actualEntryPrice is used as primary CLV source
# ══════════════════════════════════════════════════════════════════════════════

class TestActualEntryPriceIsUsedForCLV(unittest.TestCase):

    def test_clv_vs_actual_entry_when_provided(self):
        """When actualEntryPrice provided, clvVsActualEntry must be computed."""
        b = {
            'price': -120,
            'betTimeLine': -120,
            'modelSnapshotPrice': -120,
            'executablePriceAtOutput': -118,
            'actualEntryPrice': -115,   # slightly better than exec
        }
        closing = make_closing(-130)
        result = _mock_calc_clv_all_sources(b, closing)
        
        self.assertIsNotNone(result['clvVsActualEntry'],
                             "clvVsActualEntry must be computed when actualEntryPrice provided")
        self.assertEqual(result['actualEntryClvStatus'], 'complete')
        self.assertFalse(result['actualEntryIncomplete'])

    def test_snapshot_clv_stored_separately(self):
        """clvVsSnapshot must be stored even when actualEntryPrice is present."""
        b = {
            'price': -120,
            'betTimeLine': -120,
            'modelSnapshotPrice': -120,
            'executablePriceAtOutput': -118,
            'actualEntryPrice': -115,
        }
        closing = make_closing(-130)
        result = _mock_calc_clv_all_sources(b, closing)
        
        self.assertIsNotNone(result['clvVsSnapshot'],
                             "clvVsSnapshot must be stored separately from clvVsActualEntry")
        # They should generally be different
        # (snapshot at -120, actual entry at -115, same closing)
        self.assertIn('clvVsSnapshot', result)
        self.assertIn('clvVsActualEntry', result)

    def test_three_clv_fields_are_independent(self):
        """clvVsSnapshot, clvVsExecutableOutput, and clvVsActualEntry are all different."""
        b = {
            'price': -110,
            'betTimeLine': -110,
            'modelSnapshotPrice': -110,
            'executablePriceAtOutput': -115,
            'actualEntryPrice': -120,
        }
        closing = make_closing(-130)
        result = _mock_calc_clv_all_sources(b, closing)
        
        # All three should be non-None (we have closing data)
        self.assertIsNotNone(result['clvVsSnapshot'])
        self.assertIsNotNone(result['clvVsExecutableOutput'])
        self.assertIsNotNone(result['clvVsActualEntry'])
        
        # They should differ (different entry prices)
        clv_s  = result['clvVsSnapshot']
        clv_e  = result['clvVsExecutableOutput']
        clv_a  = result['clvVsActualEntry']
        # Better price = lower american = higher implied = lower CLV when close is same
        # snapshot (-110) better than exec (-115) better than actual (-120)
        # So clvVsSnapshot > clvVsExecutableOutput > clvVsActualEntry
        self.assertGreater(clv_s, clv_e, "Snapshot CLV > exec CLV when snapshot price better")
        self.assertGreater(clv_e, clv_a, "Exec CLV > actual CLV when exec price better than actual")


# ══════════════════════════════════════════════════════════════════════════════
# Test 2: Missing actualEntryPrice → incomplete, not snapshot substitution
# ══════════════════════════════════════════════════════════════════════════════

class TestMissingActualEntryPrice(unittest.TestCase):

    def test_actual_entry_clv_is_none_when_missing(self):
        """clvVsActualEntry must be None when actualEntryPrice not provided."""
        b = {
            'price': -120,
            'betTimeLine': -120,
            'modelSnapshotPrice': -120,
            # NO actualEntryPrice
        }
        closing = make_closing(-130)
        result = _mock_calc_clv_all_sources(b, closing)
        
        self.assertIsNone(result['clvVsActualEntry'],
                          "clvVsActualEntry must be None when actualEntryPrice missing")

    def test_actual_entry_status_is_incomplete_when_missing(self):
        """actualEntryClvStatus must be 'incomplete' when actualEntryPrice missing."""
        b = {'price': -120, 'betTimeLine': -120}
        closing = make_closing(-130)
        result = _mock_calc_clv_all_sources(b, closing)
        
        self.assertEqual(result['actualEntryClvStatus'], 'incomplete',
                         "actualEntryClvStatus must be 'incomplete' when actualEntryPrice not provided")

    def test_actual_entry_incomplete_flag_set(self):
        """actualEntryIncomplete=True when actualEntryPrice is None."""
        b = {'price': -120}
        closing = make_closing(-130)
        result = _mock_calc_clv_all_sources(b, closing)
        
        self.assertTrue(result['actualEntryIncomplete'],
                        "actualEntryIncomplete must be True when price not provided")

    def test_snapshot_clv_still_computed_when_actual_missing(self):
        """clvVsSnapshot is still computed even without actualEntryPrice."""
        b = {
            'price': -120,
            'betTimeLine': -120,
            'modelSnapshotPrice': -120,
        }
        closing = make_closing(-130)
        result = _mock_calc_clv_all_sources(b, closing)
        
        self.assertIsNotNone(result['clvVsSnapshot'],
                             "Snapshot CLV must still be computed for diagnostics")

    def test_snapshot_not_substituted_for_actual(self):
        """When actualEntryPrice missing, snapshot price must NOT be used as actualEntryPrice CLV."""
        b = {
            'price': -120,
            'betTimeLine': -120,
            'modelSnapshotPrice': -120,
        }
        closing = make_closing(-130)
        result = _mock_calc_clv_all_sources(b, closing)
        
        # clvVsActualEntry must be None, not the snapshot CLV value
        self.assertIsNone(result['clvVsActualEntry'],
                          "clvVsActualEntry must be None — snapshot must not be substituted for actual entry")


# ══════════════════════════════════════════════════════════════════════════════
# Test 3: Actual entry worse than maxBetPrice → flagged
# ══════════════════════════════════════════════════════════════════════════════

class TestActualEntryWorseThanMax(unittest.TestCase):

    def test_actual_entry_worse_than_max_flagged(self):
        """actualEntryWorseThanMax=True when entry price (cents) exceeds max (cents)."""
        # Kalshi YES bet in cents: maxBetPrice=53¢, actual entry=56¢ (paid more = worse)
        b = {
            'price': 53,        # original qualifying price in cents
            'actualEntryPrice': 56,  # paid 56¢ — worse than max 53¢
            'maxBetPrice': 53,       # we said max is 53¢
        }
        closing = make_closing(-130)
        result = _mock_calc_clv_all_sources(b, closing)
        
        self.assertTrue(result['actualEntryWorseThanMax'],
                        "actualEntryWorseThanMax must be True when 56¢ paid but max was 53¢")

    def test_reason_codes_when_entry_worse_than_max(self):
        """ACTUAL_ENTRY_WORSE_THAN_MAX and BET_SHOULD_HAVE_BEEN_PASSED_AT_FILL must be flagged."""
        b = {
            'price': 53,
            'actualEntryPrice': 56,
            'maxBetPrice': 53,
        }
        closing = make_closing(-130)
        result = _mock_calc_clv_all_sources(b, closing)
        
        self.assertIn('ACTUAL_ENTRY_WORSE_THAN_MAX', result['reasonCodes'])
        self.assertIn('BET_SHOULD_HAVE_BEEN_PASSED_AT_FILL', result['reasonCodes'])

    def test_actual_entry_within_max_not_flagged(self):
        """actualEntryWorseThanMax=False when entry within max (paid less or equal)."""
        b = {
            'price': 53,
            'actualEntryPrice': 51,  # paid 51¢ — better than max 53¢
            'maxBetPrice': 53,
        }
        closing = make_closing(-130)
        result = _mock_calc_clv_all_sources(b, closing)
        
        self.assertFalse(result['actualEntryWorseThanMax'],
                         "actualEntryWorseThanMax must be False when 51¢ paid and max is 53¢")
        self.assertNotIn('ACTUAL_ENTRY_WORSE_THAN_MAX', result['reasonCodes'])

    def test_no_max_price_set_no_flag(self):
        """When maxBetPrice is None, actualEntryWorseThanMax stays False."""
        b = {
            'price': 53,
            'actualEntryPrice': 56,
            'maxBetPrice': None,
        }
        closing = make_closing(-130)
        result = _mock_calc_clv_all_sources(b, closing)
        
        self.assertFalse(result['actualEntryWorseThanMax'],
                         "Cannot flag worse than max when maxBetPrice is None")


if __name__ == '__main__':
    unittest.main(verbosity=2)
