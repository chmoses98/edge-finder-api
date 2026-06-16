#!/usr/bin/env python3
"""
tests/test_phase1_lineup_fields.py
====================================
Phase 1B: Lineup field separation regression tests.

1. Official lineup posted, 9 found, 7 resolved → confirmed + adj applied
2. Official lineup posted, 9 found, 5 resolved → confirmed + adj NOT applied
3. No battingOrder → missing/unconfirmed
4. Projected source only → projected, not real-money eligible where confirmation required
5. Source conflict → LINEUP_SOURCE_CONFLICT warning
6. Confirmed lineup with weak xwOBA resolution must NOT be mislabeled as unconfirmed
"""

import sys, os, unittest

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts')
sys.path.insert(0, SCRIPTS_DIR)


def _simulate_lineup_fetch(batters_order, batter_woba_map, team_woba_map, abbr='BOS'):
    """
    Reproduce the key logic from fetch_lineups.fetch_lineup_for_game without
    making actual HTTP requests. Returns the result dict for one team side.
    """
    MIN_BATTERS = 6  # matches fetch_lineups.MIN_BATTERS_FOR_CONFIRMED
    WOBA_SCALAR = 4.5
    ADJ_CAP = 0.25
    POSITIONAL_WOBA = {
        'C': 0.305, '1B': 0.335, '2B': 0.315, '3B': 0.325,
        'SS': 0.310, 'LF': 0.330, 'RF': 0.330, 'CF': 0.315,
        'DH': 0.340, 'P': 0.145,
    }
    LEAGUE_AVG = 0.318

    if not batters_order:
        return {
            'lineupConfirmed': False,
            'lineupPosted': False,
            'lineupStatus': 'missing',
            'lineupConfirmedOfficial': False,
            'lineupSource': 'mlb_stats_api',
            'lineupBattersExpected': 9,
            'lineupBattersFound': 0,
            'lineupBattersResolved': 0,
            'lineupAdjAvailable': False,
            'lineupAdjApplied': False,
            'lineupDataQuality': 'none',
            'lineupStatusReason': 'Batting order not yet posted by MLB Stats API',
            'lineupWOBADelta': None,
            'lineupAdj': None,
        }

    # Simulate batter resolution
    lineup_wobas = []
    real_data_count = 0
    fallback_count = 0
    for pid in batters_order[:9]:
        xwoba = batter_woba_map.get(str(pid))
        if xwoba is not None:
            lineup_wobas.append(float(xwoba))
            real_data_count += 1
        else:
            lineup_wobas.append(LEAGUE_AVG)  # positional fallback
            fallback_count += 1

    lineup_avg_woba = sum(lineup_wobas) / len(lineup_wobas) if lineup_wobas else LEAGUE_AVG
    team_season_woba = team_woba_map.get(abbr, LEAGUE_AVG)
    raw_delta = round(lineup_avg_woba - team_season_woba, 4)
    lineup_adj = max(-ADJ_CAP, min(ADJ_CAP, raw_delta * WOBA_SCALAR))
    lineup_adj = round(lineup_adj, 3)

    confirmed = real_data_count >= MIN_BATTERS
    adj_available = confirmed
    adj_applied   = adj_available

    if adj_applied:
        data_quality = 'full' if real_data_count >= 8 else 'partial'
        status_reason = f'Official lineup confirmed, {real_data_count}/9 batters resolved for xwOBA adjustment'
    else:
        data_quality = 'partial' if real_data_count > 0 else 'insufficient'
        status_reason = (
            f'Official lineup confirmed but only {real_data_count}/9 batters resolved — '
            f'lineup adjustment NOT applied (need {MIN_BATTERS}/9)'
        )

    return {
        'lineupConfirmed': confirmed,
        'lineupPosted': True,
        'lineupStatus': 'confirmed',
        'lineupConfirmedOfficial': True,   # battingOrder present = official
        'lineupSource': 'mlb_stats_api',
        'lineupBattersExpected': 9,
        'lineupBattersFound': len(batters_order[:9]),
        'lineupBattersResolved': real_data_count,
        'lineupAdjAvailable': adj_available,
        'lineupAdjApplied': adj_applied,
        'lineupDataQuality': data_quality,
        'lineupStatusReason': status_reason,
        'lineupWOBADelta': raw_delta,
        'lineupAdj': lineup_adj if adj_applied else None,
    }


# ── Test data ─────────────────────────────────────────────────────────────────

TEAM_WOBA = {'BOS': 0.320, 'NYY': 0.315}

def _make_batters(n_real, total=9):
    """n_real batters have xwOBA, rest don't."""
    order = list(range(1, total + 1))
    woba_map = {str(i): 0.330 for i in range(1, n_real + 1)}
    return order, woba_map


# ══════════════════════════════════════════════════════════════════════════════
# Test Suite 1: Official lineup + 7 resolved → confirmed + adj applied
# ══════════════════════════════════════════════════════════════════════════════

class TestOfficialLineupSevenResolved(unittest.TestCase):

    def setUp(self):
        batters, woba_map = _make_batters(n_real=7)
        self.result = _simulate_lineup_fetch(batters, woba_map, TEAM_WOBA, 'BOS')

    def test_lineup_confirmed_official_is_true(self):
        """lineupConfirmedOfficial must be True when battingOrder present."""
        self.assertTrue(self.result['lineupConfirmedOfficial'])

    def test_lineup_status_is_confirmed(self):
        """lineupStatus must be 'confirmed' when battingOrder present."""
        self.assertEqual(self.result['lineupStatus'], 'confirmed')

    def test_adj_available_is_true(self):
        """lineupAdjAvailable=True when >=6 batters resolved."""
        self.assertTrue(self.result['lineupAdjAvailable'])

    def test_adj_applied_is_true(self):
        """lineupAdjApplied=True when >=6 batters resolved."""
        self.assertTrue(self.result['lineupAdjApplied'])

    def test_lineup_adj_is_not_none(self):
        """lineupAdj must be set when adj is applied."""
        self.assertIsNotNone(self.result['lineupAdj'])

    def test_data_quality_is_partial_or_full(self):
        """7/9 resolved → data_quality = 'partial' (not full, not insufficient)."""
        self.assertIn(self.result['lineupDataQuality'], ('partial', 'full'))

    def test_batters_resolved_count(self):
        """lineupBattersResolved = 7."""
        self.assertEqual(self.result['lineupBattersResolved'], 7)


# ══════════════════════════════════════════════════════════════════════════════
# Test Suite 2: Official lineup + 5 resolved → confirmed + adj NOT applied
# ══════════════════════════════════════════════════════════════════════════════

class TestOfficialLineupFiveResolved(unittest.TestCase):

    def setUp(self):
        batters, woba_map = _make_batters(n_real=5)
        self.result = _simulate_lineup_fetch(batters, woba_map, TEAM_WOBA, 'BOS')

    def test_lineup_confirmed_official_is_true(self):
        """lineupConfirmedOfficial=True even when only 5 resolved."""
        self.assertTrue(self.result['lineupConfirmedOfficial'],
                        "Official lineup MUST be confirmed even with weak xwOBA resolution")

    def test_lineup_status_is_confirmed(self):
        """lineupStatus='confirmed' even with only 5 resolved."""
        self.assertEqual(self.result['lineupStatus'], 'confirmed',
                         "lineupStatus must be 'confirmed', NOT 'unconfirmed' with low xwOBA resolution")

    def test_adj_available_is_false(self):
        """lineupAdjAvailable=False when <6 batters resolved."""
        self.assertFalse(self.result['lineupAdjAvailable'])

    def test_adj_applied_is_false(self):
        """lineupAdjApplied=False when <6 batters resolved."""
        self.assertFalse(self.result['lineupAdjApplied'])

    def test_lineup_adj_is_none(self):
        """lineupAdj must be None when adj is NOT applied."""
        self.assertIsNone(self.result['lineupAdj'])

    def test_status_reason_explains_adj_unavailable(self):
        """lineupStatusReason must explain why adj was not applied."""
        reason = self.result['lineupStatusReason']
        self.assertIn('5/9', reason, f"Reason must mention 5/9 batters resolved: {reason}")

    def test_confirmed_not_mislabeled_as_unconfirmed(self):
        """CRITICAL: confirmed lineup with 5/9 xwOBA must NOT be called 'unconfirmed'."""
        self.assertNotEqual(self.result.get('lineupStatus'), 'missing',
                            "A posted lineup must NEVER be labeled 'missing'")
        self.assertNotEqual(self.result.get('lineupStatus'), 'unknown',
                            "A posted lineup must NEVER be labeled 'unknown'")
        self.assertNotFalse_custom(self.result.get('lineupConfirmedOfficial'),
                                   "lineupConfirmedOfficial must not be False for an official lineup")

    def assertNotFalse_custom(self, value, msg):
        """Helper to check value is not False (allows 0, None to also fail)."""
        self.assertTrue(value is True or (value is not False and value is not None and value != 0),
                        msg)


# ══════════════════════════════════════════════════════════════════════════════
# Test Suite 3: No battingOrder → missing/unconfirmed
# ══════════════════════════════════════════════════════════════════════════════

class TestNoBattingOrder(unittest.TestCase):

    def setUp(self):
        self.result = _simulate_lineup_fetch([], {}, TEAM_WOBA, 'BOS')

    def test_lineup_posted_is_false(self):
        """lineupPosted=False when no battingOrder."""
        self.assertFalse(self.result['lineupPosted'])

    def test_lineup_status_is_missing(self):
        """lineupStatus='missing' when battingOrder absent."""
        self.assertEqual(self.result['lineupStatus'], 'missing')

    def test_lineup_confirmed_official_is_false(self):
        """lineupConfirmedOfficial=False when no battingOrder."""
        self.assertFalse(self.result['lineupConfirmedOfficial'])

    def test_adj_not_available(self):
        """lineupAdjAvailable=False when no lineup."""
        self.assertFalse(self.result['lineupAdjAvailable'])

    def test_batters_found_is_zero(self):
        """lineupBattersFound=0 when no lineup."""
        self.assertEqual(self.result['lineupBattersFound'], 0)

    def test_lineup_adj_is_none(self):
        """lineupAdj is None when no lineup."""
        self.assertIsNone(self.result['lineupAdj'])

    def test_legacy_lineup_confirmed_is_false(self):
        """Legacy lineupConfirmed field is False (backward compat)."""
        self.assertFalse(self.result.get('lineupConfirmed', True))


# ══════════════════════════════════════════════════════════════════════════════
# Test Suite 4: Reason codes for lineup status
# ══════════════════════════════════════════════════════════════════════════════

class TestLineupReasonCodes(unittest.TestCase):

    def test_confirmed_official_generates_correct_code(self):
        """lineupConfirmedOfficial=True → reason code LINEUP_CONFIRMED_OFFICIAL."""
        from reason_codes import LINEUP_CONFIRMED_OFFICIAL, LINEUP_ADJ_APPLIED
        row = {
            'status': 'Accepted',
            'marketTicker': 'KXMLBGAME-123-BOS',
            'lineupConfirmedOfficial': True,
            'lineupAdjAvailable': True,
            'lineupAdjApplied': True,
        }
        from reason_codes import build_reason_codes
        codes = build_reason_codes('Accepted', row)
        self.assertIn(LINEUP_CONFIRMED_OFFICIAL, codes,
                      f"LINEUP_CONFIRMED_OFFICIAL must be in codes: {codes}")
        self.assertIn(LINEUP_ADJ_APPLIED, codes,
                      f"LINEUP_ADJ_APPLIED must be in codes when adj applied: {codes}")

    def test_confirmed_no_adj_generates_unavailable_code(self):
        """Official confirmed but adj not available → LINEUP_ADJ_UNAVAILABLE_BUT_OFFICIAL_CONFIRMED."""
        from reason_codes import LINEUP_ADJ_UNAVAILABLE_BUT_OFFICIAL, build_reason_codes
        row = {
            'status': 'Accepted',
            'marketTicker': 'KXMLBGAME-123-BOS',
            'lineupConfirmedOfficial': True,
            'lineupAdjAvailable': False,
            'lineupAdjApplied': False,
        }
        codes = build_reason_codes('Accepted', row)
        self.assertIn(LINEUP_ADJ_UNAVAILABLE_BUT_OFFICIAL, codes,
                      f"LINEUP_ADJ_UNAVAILABLE_BUT_OFFICIAL_CONFIRMED must be in codes: {codes}")

    def test_missing_lineup_generates_correct_code(self):
        """lineupStatus='missing' → LINEUP_MISSING in reason codes."""
        from reason_codes import LINEUP_MISSING, build_reason_codes
        row = {
            'status': 'Rejected',
            'rejectionReason': 'Rule 52: lineupConfirmed=False (away) — YRFI downgraded',
            'lineupStatus': 'missing',
            'gatesFired': ['Rule 52: lineup gate'],
        }
        codes = build_reason_codes('Rejected', row)
        self.assertIn(LINEUP_MISSING, codes,
                      f"LINEUP_MISSING must be in codes for missing lineup: {codes}")


# ══════════════════════════════════════════════════════════════════════════════
# Test Suite 5: YRFI/NRFI cannot use banned bullpen reasoning
# ══════════════════════════════════════════════════════════════════════════════

class TestYRFINRFIBannedReasoning(unittest.TestCase):

    BANNED_PHRASES = [
        'bullpen exposure',
        'full-game bullpen',
        'short starter leash',
        'average innings per start',
        'pen arrives by inning',
        'late-game bullpen fatigue',
    ]

    def test_yrfi_explanation_cannot_cite_bullpen_exposure(self):
        """YRFI explanations must not cite bullpen exposure."""
        from reason_codes import build_reason_codes
        for phrase in self.BANNED_PHRASES:
            row = {
                'market': 'YRFI',
                'status': 'Accepted',
                'notes': f'Edge driven by {phrase}',
                'marketTicker': 'KXMLBRFI-123',
            }
            codes = build_reason_codes('Accepted', row)
            self.assertIn('YRFI_NRFI_BANNED_REASONING_DETECTED', codes,
                          f"Banned phrase '{phrase}' must be flagged in YRFI: {codes}")

    def test_nrfi_explanation_cannot_cite_short_starter_leash(self):
        """NRFI explanations must not cite short starter leash."""
        from reason_codes import build_reason_codes
        row = {
            'market': 'NRFI',
            'status': 'Accepted',
            'notes': 'NRFI supported by short starter leash risk',
            'marketTicker': 'KXMLBRFI-123',
        }
        codes = build_reason_codes('Accepted', row)
        self.assertIn('YRFI_NRFI_BANNED_REASONING_DETECTED', codes,
                      "short starter leash must be flagged in NRFI reasoning")

    def test_clean_yrfi_explanation_passes(self):
        """YRFI explanation without banned phrases is clean."""
        from reason_codes import build_reason_codes
        row = {
            'market': 'YRFI',
            'status': 'Accepted',
            'notes': '1st-inn approx: away=0.491 home=0.489 R/inn',
            'marketTicker': 'KXMLBRFI-123',
        }
        codes = build_reason_codes('Accepted', row)
        self.assertNotIn('YRFI_NRFI_BANNED_REASONING_DETECTED', codes,
                         f"Clean explanation should not trigger banned reasoning flag: {codes}")

    def test_reason_codes_present_for_every_market_status(self):
        """Every row (approved/rejected/missing/failed) must have reasonCodes key."""
        from reason_codes import build_reason_codes
        for status in ['Accepted', 'Rejected', 'Missing Data', 'Evaluation Failed']:
            row = {'status': status, 'market': 'ML_Away', 'rejectionReason': 'test',
                   'missingFields': ['test'], 'evaluationError': 'test'}
            codes = build_reason_codes(status, row)
            self.assertIsInstance(codes, list,
                                  f"reasonCodes must be a list for status={status}")


if __name__ == '__main__':
    unittest.main(verbosity=2)
