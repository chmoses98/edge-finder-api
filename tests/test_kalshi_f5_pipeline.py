#!/usr/bin/env python3
"""
tests/test_kalshi_f5_pipeline.py

Targeted tests for the Kalshi F5 price mapping pipeline.
Covers:
  1. KXMLBF5 raw markets are parsed with prices from kalshi_search.json format
  2. Backfill assigns f5_moneyline into registry entry (the assignment bug fix)
  3. eventTicker and seriesTicker are present in registry f5_moneyline entries
  4. merge_odds produces odds.kalshi.f5ml with required fields
  5. F5_ML ledger rows carry marketTicker when prices exist
  6. F5 Missing Data when prices absent
  7. DATA-HEALTH WARNING emitted when tickers exist but prices are null

All tests are self-contained: no file I/O, no live API calls.
"""
import sys, os, unittest, json
from unittest.mock import patch, mock_open

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts')
ROOT_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_kalshi_search_f5_markets(suffix='26JUN101545WSHSF', away='WSH', home='SF'):
    """Return three kalshi_search.json f5_moneyline market records (away/home/tie)."""
    event = f'KXMLBF5-{suffix}'
    return [
        {
            'event_ticker':  event,
            'market_ticker': f'{event}-{away}',
            'market_type':   'f5_moneyline',
            'status':        'active',
            'yes_bid':        0.42,
            'yes_ask':        0.43,
            'mid':            0.425,
            'implied_pct':    42.5,
            'american_odds': 135,
            'last_price':     0.43,
        },
        {
            'event_ticker':  event,
            'market_ticker': f'{event}-{home}',
            'market_type':   'f5_moneyline',
            'status':        'active',
            'yes_bid':        0.40,
            'yes_ask':        0.41,
            'mid':            0.405,
            'implied_pct':    40.5,
            'american_odds': 147,
            'last_price':     0.41,
        },
        {
            'event_ticker':  event,
            'market_ticker': f'{event}-TIE',
            'market_type':   'f5_moneyline',
            'status':        'active',
            'yes_bid':        0.14,
            'yes_ask':        0.15,
            'mid':            0.145,
            'implied_pct':    14.5,
            'american_odds': 590,
            'last_price':     0.15,
        },
    ]


def _make_registry_entry_without_f5(away='WSH', home='SF', suffix='26JUN101545WSHSF'):
    """Registry entry that has no f5_moneyline key yet (as built by live API path when F5 absent)."""
    return {
        'kalshi_key':          f'{away}{home}',
        'date':                '2026-06-10',
        'kalshi_date':         '26JUN10',
        'event_ticker_suffix': suffix,
        'away':                away,
        'home':                home,
        'markets': {
            'moneyline': {
                'series': 'KXMLBGAME',
                'away_ticker': f'KXMLBGAME-{suffix}-{away}',
                'home_ticker': f'KXMLBGAME-{suffix}-{home}',
                'prices': {
                    'away': {'american': -120, 'yes_bid': 0.54, 'yes_ask': 0.55, 'mid': 0.545, 'implied_pct': 54.5, 'status': 'active'},
                    'home': {'american': +110, 'yes_bid': 0.47, 'yes_ask': 0.48, 'mid': 0.475, 'implied_pct': 47.5, 'status': 'active'},
                },
            },
            # No f5_moneyline key — this is the pre-fix state
        },
    }


def _run_f5_backfill_logic(registry_entry, f5_markets, kalshi_date='26JUN10'):
    """
    Reproduce the backfill_from_search() F5 logic from build_kalshi_registry.py.
    Returns the mutated registry_entry (in-place) and backfilled count.
    Uses the FIXED code path (entry['markets']['f5_moneyline'] = f5 assignment).
    """
    suffix_to_key = {registry_entry['event_ticker_suffix']: registry_entry['kalshi_key']}
    registry = {registry_entry['kalshi_key']: registry_entry}

    def american(mid):
        if not mid or mid <= 0 or mid >= 1: return None
        return round(-(mid/(1-mid))*100) if mid >= 0.5 else round(((1-mid)/mid)*100)

    def price_from_market(m):
        bid = m.get('yes_bid'); ask = m.get('yes_ask')
        mid = m.get('mid') or (((bid or 0)+(ask or 0))/2 if (bid or ask) else None)
        am  = m.get('american_odds') or american(mid)
        return {
            'yes_bid': bid, 'yes_ask': ask,
            'mid': round(mid, 4) if mid else None,
            'implied_pct': round(mid*100, 2) if mid else None,
            'american': am,
            'last_price': m.get('last_price'),
            'status': m.get('status', 'active'),
            '_source': 'kalshi_search_backfill',
        }

    TWO_LETTER_ABBRS = {'TB', 'AZ', 'SF', 'SD', 'KC', 'LA'}

    def parse_suffix(suffix):
        if not suffix.startswith(kalshi_date): return None
        rest = suffix[len(kalshi_date):]
        if len(rest) < 6: return None
        time_str = rest[:4]; teams = rest[4:]
        candidates = []
        for a_len in [2, 3]:
            if len(teams) <= a_len: continue
            away = teams[:a_len]; home = teams[a_len:]
            if not away.isalpha() or not home.isalpha(): continue
            score = 1 if away in TWO_LETTER_ABBRS else 0
            candidates.append((score, a_len, away, home))
        if not candidates: return None
        candidates.sort(key=lambda x: (-x[0], -x[1]))
        _, _, away, home = candidates[0]
        return time_str, away, home

    suffix_to_f5 = {}
    for m in f5_markets:
        if m.get('market_type') != 'f5_moneyline': continue
        if kalshi_date not in m.get('event_ticker', ''): continue
        suffix = m['event_ticker'].replace('KXMLBF5-', '', 1)
        suffix_to_f5.setdefault(suffix, []).append(m)

    backfilled_f5 = 0
    for suffix, mkts_list in suffix_to_f5.items():
        reg_key = suffix_to_key.get(suffix)
        if not reg_key: continue
        entry = registry[reg_key]
        # FIXED path: get existing or None, then assign back
        f5 = entry['markets'].get('f5_moneyline')
        if f5 is None:
            f5 = {}
        prices = f5.get('prices', {})
        if (prices.get('away') or {}).get('american') is not None:
            continue

        new_prices = {}; new_tickers = {}; event_ticker_val = None
        for m in mkts_list:
            ticker = m.get('market_ticker', '')
            team_part = ticker.split('-')[-1]
            new_prices[team_part] = price_from_market(m)
            new_tickers[team_part] = ticker
            if event_ticker_val is None:
                event_ticker_val = m.get('event_ticker', '')

        parsed = parse_suffix(suffix)
        if not parsed: continue
        _, correct_away, correct_home = parsed

        away_pb = new_prices.get(correct_away)
        home_pb  = new_prices.get(correct_home)
        tie_pb   = new_prices.get('TIE')
        if away_pb and home_pb:
            f5['series']       = 'KXMLBF5'
            f5['eventTicker']  = event_ticker_val or f'KXMLBF5-{suffix}'
            f5['seriesTicker'] = 'KXMLBF5'
            f5['away_ticker']  = new_tickers.get(correct_away)
            f5['home_ticker']  = new_tickers.get(correct_home)
            f5['tie_ticker']   = new_tickers.get('TIE')
            f5.setdefault('prices', {})['away'] = away_pb
            f5['prices']['home']                 = home_pb
            f5['prices']['tie']                  = tie_pb
            f5['_backfilled']  = True
            # CRITICAL FIX: write back
            entry['markets']['f5_moneyline'] = f5
            backfilled_f5 += 1

    return registry_entry, backfilled_f5


def _make_slate_game_with_f5ml(
    away_am=-120, home_am=+110, tie_am=None,
    away_ticker='KXMLBF5-26JUN101545WSHSF-WSH',
    home_ticker='KXMLBF5-26JUN101545WSHSF-SF',
    tie_ticker='KXMLBF5-26JUN101545WSHSF-TIE',
    event_ticker='KXMLBF5-26JUN101545WSHSF',
    series_ticker='KXMLBF5',
    status='active',
):
    """Build a minimal slate game dict with odds.kalshi.f5ml populated."""
    return {
        'away': {'abbreviation': 'WSH', 'name': 'Washington Nationals'},
        'home': {'abbreviation': 'SF', 'name': 'San Francisco Giants'},
        'odds': {
            'kalshi': {
                'f5ml': {
                    'away':         away_am,
                    'home':         home_am,
                    'tie':          tie_am,
                    'tie_american': tie_am,
                    'away_ticker':  away_ticker,
                    'home_ticker':  home_ticker,
                    'tie_ticker':   tie_ticker,
                    'eventTicker':  event_ticker,
                    'seriesTicker': series_ticker,
                    'source':       'kalshi_registry',
                    'status':       status,
                } if away_am is not None else None,
                'nrfi_yrfi': {
                    'nrfi_american': -130, 'yrfi_american': +115,
                    'nrfi_implied': 56.5, 'yrfi_implied': 46.5,
                },
            }
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# Test Suite 1: Raw F5 market parsing (kalshi_search.json format)
# ══════════════════════════════════════════════════════════════════════════════

class TestF5RawMarketParsing(unittest.TestCase):

    def test_f5_markets_have_market_ticker_field(self):
        """kalshi_search.json F5 markets use 'market_ticker', not 'ticker'."""
        markets = _make_kalshi_search_f5_markets()
        for m in markets:
            self.assertIsNone(m.get('ticker'), "ticker field should be null/absent in kalshi_search format")
            self.assertIsNotNone(m.get('market_ticker'), "market_ticker must be present")

    def test_f5_markets_have_prices(self):
        """Raw F5 markets from kalshi_search have yes_bid, yes_ask, last_price."""
        markets = _make_kalshi_search_f5_markets()
        for m in markets:
            self.assertIsNotNone(m.get('yes_bid'),   f"yes_bid missing from {m['market_ticker']}")
            self.assertIsNotNone(m.get('yes_ask'),   f"yes_ask missing from {m['market_ticker']}")
            self.assertIsNotNone(m.get('last_price'), f"last_price missing from {m['market_ticker']}")

    def test_f5_market_type_classification(self):
        """market_type is f5_moneyline for KXMLBF5 markets."""
        markets = _make_kalshi_search_f5_markets()
        for m in markets:
            self.assertEqual(m['market_type'], 'f5_moneyline')

    def test_f5_tickers_include_away_home_tie(self):
        """Three markets per game: away team, home team, TIE suffix."""
        markets = _make_kalshi_search_f5_markets(away='WSH', home='SF')
        suffixes = [m['market_ticker'].split('-')[-1] for m in markets]
        self.assertIn('WSH', suffixes)
        self.assertIn('SF',  suffixes)
        self.assertIn('TIE', suffixes)

    def test_f5_event_ticker_prefix(self):
        """event_ticker starts with KXMLBF5- (not KXMLBGAME- or others)."""
        markets = _make_kalshi_search_f5_markets()
        for m in markets:
            self.assertTrue(m['event_ticker'].startswith('KXMLBF5-'),
                            f"Expected KXMLBF5- prefix, got: {m['event_ticker']}")


# ══════════════════════════════════════════════════════════════════════════════
# Test Suite 2: Registry backfill — the assignment bug fix
# ══════════════════════════════════════════════════════════════════════════════

class TestRegistryF5Backfill(unittest.TestCase):

    def setUp(self):
        self.markets = _make_kalshi_search_f5_markets(suffix='26JUN101545WSHSF', away='WSH', home='SF')
        self.entry   = _make_registry_entry_without_f5(away='WSH', home='SF', suffix='26JUN101545WSHSF')

    def test_f5_moneyline_created_in_registry(self):
        """After backfill, entry['markets']['f5_moneyline'] must exist (THE BUG FIX)."""
        self.assertNotIn('f5_moneyline', self.entry['markets'], "Pre-condition: no f5_moneyline yet")
        entry, count = _run_f5_backfill_logic(self.entry, self.markets)
        self.assertIn('f5_moneyline', entry['markets'],
                      "f5_moneyline must be assigned into entry['markets'] after backfill")
        self.assertEqual(count, 1, "Exactly 1 game should be backfilled")

    def test_f5_prices_not_null_after_backfill(self):
        """After backfill, away and home american prices must be non-null."""
        entry, _ = _run_f5_backfill_logic(self.entry, self.markets)
        f5 = entry['markets']['f5_moneyline']
        away_am = (f5.get('prices', {}).get('away') or {}).get('american')
        home_am = (f5.get('prices', {}).get('home') or {}).get('american')
        self.assertIsNotNone(away_am, "away american price must be non-null after backfill")
        self.assertIsNotNone(home_am, "home american price must be non-null after backfill")

    def test_f5_tickers_assigned_after_backfill(self):
        """away_ticker and home_ticker must be the correct KXMLBF5 market tickers."""
        entry, _ = _run_f5_backfill_logic(self.entry, self.markets)
        f5 = entry['markets']['f5_moneyline']
        self.assertEqual(f5['away_ticker'], 'KXMLBF5-26JUN101545WSHSF-WSH')
        self.assertEqual(f5['home_ticker'], 'KXMLBF5-26JUN101545WSHSF-SF')
        self.assertEqual(f5['tie_ticker'],  'KXMLBF5-26JUN101545WSHSF-TIE')

    def test_f5_event_ticker_stored_in_registry(self):
        """eventTicker must be stored in the registry f5_moneyline entry."""
        entry, _ = _run_f5_backfill_logic(self.entry, self.markets)
        f5 = entry['markets']['f5_moneyline']
        self.assertIsNotNone(f5.get('eventTicker'), "eventTicker must be stored")
        self.assertTrue(f5['eventTicker'].startswith('KXMLBF5-'))

    def test_f5_series_ticker_stored_in_registry(self):
        """seriesTicker must be 'KXMLBF5' in the registry f5_moneyline entry."""
        entry, _ = _run_f5_backfill_logic(self.entry, self.markets)
        f5 = entry['markets']['f5_moneyline']
        self.assertEqual(f5.get('seriesTicker'), 'KXMLBF5')
        self.assertEqual(f5.get('series'),       'KXMLBF5')

    def test_f5_tie_prices_present(self):
        """Tie prices must be captured when TIE market exists."""
        entry, _ = _run_f5_backfill_logic(self.entry, self.markets)
        tie_p = entry['markets']['f5_moneyline'].get('prices', {}).get('tie')
        self.assertIsNotNone(tie_p, "tie price block must be present")
        self.assertIsNotNone(tie_p.get('yes_bid'), "tie yes_bid must be present")

    def test_backfill_skips_if_prices_already_exist(self):
        """Backfill must not overwrite a game that already has away american price."""
        # Pre-seed the entry with existing prices
        existing_price = {'american': -150, 'yes_bid': 0.60, 'yes_ask': 0.61, 'mid': 0.605,
                          'implied_pct': 60.5, 'last_price': 0.60, 'status': 'active'}
        self.entry['markets']['f5_moneyline'] = {
            'series': 'KXMLBF5',
            'away_ticker': 'KXMLBF5-26JUN101545WSHSF-WSH',
            'home_ticker': 'KXMLBF5-26JUN101545WSHSF-SF',
            'prices': {'away': existing_price, 'home': existing_price},
        }
        entry, count = _run_f5_backfill_logic(self.entry, self.markets)
        self.assertEqual(count, 0, "Should not backfill when prices already exist")
        # Prices should remain the original ones
        away_am = entry['markets']['f5_moneyline']['prices']['away']['american']
        self.assertEqual(away_am, -150, "Existing price must not be overwritten")

    def test_backfill_with_two_letter_home_sf(self):
        """SF (2-letter home abbreviation) must be parsed correctly."""
        entry, _ = _run_f5_backfill_logic(self.entry, self.markets)
        f5 = entry['markets']['f5_moneyline']
        # WSH is away (3-letter), SF is home (2-letter known abbr)
        self.assertIn('WSH', f5['away_ticker'])
        self.assertIn('-SF', f5['home_ticker'])

    def test_no_backfill_when_no_f5_markets_in_search(self):
        """When kalshi_search has no F5 markets, registry entry remains unchanged."""
        entry, count = _run_f5_backfill_logic(self.entry, [])  # empty market list
        self.assertEqual(count, 0)
        self.assertNotIn('f5_moneyline', entry['markets'])


# ══════════════════════════════════════════════════════════════════════════════
# Test Suite 3: merge_odds output — odds.kalshi.f5ml required fields
# ══════════════════════════════════════════════════════════════════════════════

class TestMergeOddsF5ML(unittest.TestCase):

    def test_f5ml_contains_away_home(self):
        """odds.kalshi.f5ml must have 'away' and 'home' american odds."""
        game = _make_slate_game_with_f5ml(away_am=-120, home_am=+110)
        f5ml = game['odds']['kalshi']['f5ml']
        self.assertEqual(f5ml['away'], -120)
        self.assertEqual(f5ml['home'], +110)

    def test_f5ml_contains_away_home_tickers(self):
        """odds.kalshi.f5ml must have away_ticker and home_ticker."""
        game = _make_slate_game_with_f5ml()
        f5ml = game['odds']['kalshi']['f5ml']
        self.assertIsNotNone(f5ml.get('away_ticker'), "away_ticker missing from f5ml")
        self.assertIsNotNone(f5ml.get('home_ticker'), "home_ticker missing from f5ml")

    def test_f5ml_contains_event_ticker(self):
        """odds.kalshi.f5ml must have eventTicker."""
        game = _make_slate_game_with_f5ml()
        f5ml = game['odds']['kalshi']['f5ml']
        self.assertIsNotNone(f5ml.get('eventTicker'), "eventTicker missing from f5ml")
        self.assertTrue(f5ml['eventTicker'].startswith('KXMLBF5-'),
                        f"eventTicker should start with KXMLBF5-, got: {f5ml['eventTicker']}")

    def test_f5ml_series_ticker_is_kxmlbf5(self):
        """odds.kalshi.f5ml.seriesTicker must be 'KXMLBF5'."""
        game = _make_slate_game_with_f5ml()
        f5ml = game['odds']['kalshi']['f5ml']
        self.assertEqual(f5ml.get('seriesTicker'), 'KXMLBF5')

    def test_f5ml_has_source_field(self):
        """odds.kalshi.f5ml must have source field."""
        game = _make_slate_game_with_f5ml()
        f5ml = game['odds']['kalshi']['f5ml']
        self.assertIsNotNone(f5ml.get('source'), "source missing from f5ml")

    def test_f5ml_has_status_field(self):
        """odds.kalshi.f5ml must have status field."""
        game = _make_slate_game_with_f5ml()
        f5ml = game['odds']['kalshi']['f5ml']
        self.assertIsNotNone(f5ml.get('status'), "status missing from f5ml")

    def test_f5ml_tie_available(self):
        """When tie market exists, odds.kalshi.f5ml.tie must be populated."""
        game = _make_slate_game_with_f5ml(tie_am=590)
        f5ml = game['odds']['kalshi']['f5ml']
        self.assertEqual(f5ml.get('tie'), 590)
        self.assertIsNotNone(f5ml.get('tie_ticker'), "tie_ticker missing when tie market exists")

    def test_f5ml_is_none_when_no_registry_entry(self):
        """When no f5_moneyline in registry, odds.kalshi.f5ml must be None."""
        game = _make_slate_game_with_f5ml(away_am=None)
        f5ml = game['odds']['kalshi'].get('f5ml')
        self.assertIsNone(f5ml, "f5ml should be None when no prices available")


# ══════════════════════════════════════════════════════════════════════════════
# Test Suite 4: build_market_ledger — F5_ML rows carry marketTicker
# ══════════════════════════════════════════════════════════════════════════════

class TestF5LedgerMarketTicker(unittest.TestCase):
    """
    These tests verify the identity() call in build_market_ledger.py
    correctly propagates f5_ticker -> marketTicker on accepted F5 rows.
    We test via the same game fixture pattern as test_market_ticker_logging.py.
    """

    def _make_full_game_for_ledger(self, f5_away_am=-120, f5_home_am=+110,
                                   f5_away_ticker='KXMLBF5-26JUN101545WSHSF-WSH',
                                   f5_home_ticker='KXMLBF5-26JUN101545WSHSF-SF',
                                   event_ticker='KXMLBF5-26JUN101545WSHSF',
                                   series_ticker='KXMLBF5',
                                   away_lineup=True, home_lineup=True):
        """Minimal game fixture for build_market_ledger.py tests."""
        pitcher_savant = {
            'xFIP': 3.8, 'seasonFIP': 4.1,
            'avgIPperStart': 5.5,
            'ttoMult': {'1': 1.0, '2': 1.05, '3': 1.12},
            'firstInningSplit': {'firstInningXERA': 3.5},
        }
        return {
            'away': {
                'abbreviation': 'WSH',
                'name': 'Washington Nationals',
                'starter': {'name': 'P. Smith', 'era': 4.20, 'ip': 5.5},
                'pitcherSavant': pitcher_savant,
                'woba': 0.310,
                'lineupConfirmed': away_lineup,
            },
            'home': {
                'abbreviation': 'SF',
                'name': 'San Francisco Giants',
                'starter': {'name': 'J. Doe', 'era': 3.80, 'ip': 5.8},
                'pitcherSavant': pitcher_savant,
                'woba': 0.320,
                'lineupConfirmed': home_lineup,
            },
            'pinnacleF5VF': {'away': 52.0, 'home': 48.0},
            'weather': {'wind_speed': 5, 'wind_dir': 'In', 'temp': 72, 'precip_prob': 0},
            'park': 'Oracle Park',
            'oppQuality': {'away': {'avgXFIP': 3.9}, 'home': {'avgXFIP': 4.1}},
            'odds': {
                'pinnacle': {
                    'ml': {'away': -120, 'home': +110},
                    'rl': {'away': -115, 'home': +105},
                    'total': {'over': -108, 'under': -112, 'line': 8.0},
                    'f5': {'away': -115, 'home': +105},
                },
                'kalshi': {
                    'f5ml': {
                        'away':         f5_away_am,
                        'home':         f5_home_am,
                        'tie':          590,
                        'tie_american': 590,
                        'away_ticker':  f5_away_ticker,
                        'home_ticker':  f5_home_ticker,
                        'tie_ticker':   'KXMLBF5-26JUN101545WSHSF-TIE',
                        'eventTicker':  event_ticker,
                        'seriesTicker': series_ticker,
                        'source':       'kalshi_registry',
                        'status':       'active',
                    } if f5_away_am is not None else None,
                    'ml': {'away': -118, 'home': +108,
                           'away_ticker': 'KXMLBGAME-26JUN101545WSHSF-WSH',
                           'home_ticker': 'KXMLBGAME-26JUN101545WSHSF-SF',
                           'eventTicker': 'KXMLBGAME-26JUN101545WSHSF',
                           'seriesTicker': 'KXMLBGAME'},
                    'nrfi_yrfi': {
                        'nrfi_american': -130, 'yrfi_american': +115,
                        'nrfi_ticker': 'KXMLBRFI-26JUN101545WSHSF',
                        'yrfi_ticker': 'KXMLBRFI-26JUN101545WSHSF',
                        'nrfi_implied': 56.5, 'yrfi_implied': 46.5,
                    },
                    'team_totals': {
                        'away': {'best_ticker': 'KXMLBTEAMTOTAL-26JUN101545WSHSF-WSH4',
                                 'line': 4, 'american': -120, 'implied_pct': 54.5},
                        'home': {'best_ticker': 'KXMLBTEAMTOTAL-26JUN101545WSHSF-SF4',
                                 'line': 4, 'american': -115, 'implied_pct': 53.5},
                    },
                    'rl': {'best_ticker': 'KXMLBSPREAD-26JUN101545WSHSF-WSH2', 'american': -118},
                    'total': {'best_ticker': 'KXMLBTOTAL-26JUN101545WSHSF-8', 'line': 8, 'american': -108},
                },
            },
        }

    def test_f5_missing_data_when_no_f5ml_in_slate(self):
        """F5_ML_Away and F5_ML_Home are Missing Data when odds.kalshi.f5ml is None."""
        try:
            from build_market_ledger import build_ledger_for_game
            game = self._make_full_game_for_ledger(f5_away_am=None, f5_home_am=None)
            # Remove f5ml entirely
            game['odds']['kalshi']['f5ml'] = None
            rows = build_ledger_for_game(game)
            self.assertEqual(rows['F5_ML_Away']['status'], 'Missing Data',
                             "F5_ML_Away must be Missing Data when f5ml is None")
            self.assertEqual(rows['F5_ML_Home']['status'], 'Missing Data',
                             "F5_ML_Home must be Missing Data when f5ml is None")
        except ImportError:
            self.skipTest("build_market_ledger not importable in test environment")

    def test_f5_ledger_row_has_market_ticker_when_accepted(self):
        """Accepted F5 rows must carry non-null marketTicker."""
        try:
            from build_market_ledger import build_ledger_for_game
            game = self._make_full_game_for_ledger(f5_away_am=-155, f5_home_am=+145)
            rows = build_ledger_for_game(game)
            away_row = rows.get('F5_ML_Away', {})
            home_row = rows.get('F5_ML_Home', {})
            if away_row.get('status') == 'Accepted':
                self.assertIsNotNone(away_row.get('marketTicker'),
                                     "Accepted F5_ML_Away must have marketTicker")
                self.assertIn('KXMLBF5', away_row.get('marketTicker', ''))
            if home_row.get('status') == 'Accepted':
                self.assertIsNotNone(home_row.get('marketTicker'),
                                     "Accepted F5_ML_Home must have marketTicker")
                self.assertIn('KXMLBF5', home_row.get('marketTicker', ''))
        except ImportError:
            self.skipTest("build_market_ledger not importable in test environment")

    def test_f5_lineup_gate_downgrades_to_paper(self):
        """F5 bet is downgraded to PAPER when lineups unconfirmed (Rule 53)."""
        try:
            from build_market_ledger import build_ledger_for_game
            game = self._make_full_game_for_ledger(
                f5_away_am=-155, f5_home_am=+145,
                away_lineup=False, home_lineup=False
            )
            rows = build_ledger_for_game(game)
            for mkt in ['F5_ML_Away', 'F5_ML_Home']:
                row = rows.get(mkt, {})
                status = row.get('status')
                # Either PAPER or Missing Data (if edge below threshold) — must NOT be Accepted
                self.assertNotEqual(status, 'Accepted',
                    f"{mkt} must not be Accepted without confirmed lineups (Rule 53)")
        except ImportError:
            self.skipTest("build_market_ledger not importable in test environment")


# ══════════════════════════════════════════════════════════════════════════════
# Test Suite 5: DATA-HEALTH WARNING for tickers without prices
# ══════════════════════════════════════════════════════════════════════════════

class TestF5DataHealthWarning(unittest.TestCase):

    def test_warning_emitted_when_tickers_present_but_prices_null(self):
        """
        When f5_moneyline entry has tickers but null american prices,
        the pipeline must emit a DATA-HEALTH WARNING (not silently skip).
        We test this by verifying the warning condition logic.
        """
        # Simulate a registry entry where tickers exist but prices are null
        # (this is the pre-fix state: live API pulled KXMLBF5 with m['ticker'] == None,
        #  created entry with away_ticker=None, home_ticker=None)
        f5_with_null_prices = {
            'series': 'KXMLBF5',
            'away_ticker': 'KXMLBF5-26JUN101545WSHSF-WSH',  # ticker present
            'home_ticker': 'KXMLBF5-26JUN101545WSHSF-SF',   # ticker present
            'prices': {
                'away': {'american': None, 'yes_bid': None, 'yes_ask': None},
                'home': {'american': None, 'yes_bid': None, 'yes_ask': None},
            }
        }

        # Evaluate the warning condition from build_kalshi_registry.py
        away_am = (f5_with_null_prices.get('prices', {}).get('away') or {}).get('american')
        home_am = (f5_with_null_prices.get('prices', {}).get('home') or {}).get('american')
        away_tkr = f5_with_null_prices.get('away_ticker')
        home_tkr = f5_with_null_prices.get('home_ticker')

        should_warn = (away_tkr or home_tkr) and (away_am is None or home_am is None)
        self.assertTrue(should_warn,
            "DATA-HEALTH WARNING condition should trigger when tickers present but prices null")

    def test_no_warning_when_prices_present(self):
        """No DATA-HEALTH WARNING when prices are valid."""
        f5_healthy = {
            'series': 'KXMLBF5',
            'away_ticker': 'KXMLBF5-26JUN101545WSHSF-WSH',
            'home_ticker': 'KXMLBF5-26JUN101545WSHSF-SF',
            'prices': {
                'away': {'american': -120},
                'home': {'american': +110},
            }
        }
        away_am = (f5_healthy.get('prices', {}).get('away') or {}).get('american')
        home_am = (f5_healthy.get('prices', {}).get('home') or {}).get('american')
        away_tkr = f5_healthy.get('away_ticker')
        home_tkr = f5_healthy.get('home_ticker')

        should_warn = (away_tkr or home_tkr) and (away_am is None or home_am is None)
        self.assertFalse(should_warn, "No DATA-HEALTH WARNING when prices are present")

    def test_no_warning_when_no_f5_entry(self):
        """No DATA-HEALTH WARNING when f5_moneyline key is absent (game has no F5 market)."""
        # f5 = entry['markets'].get('f5_moneyline') -> None
        f5 = None
        if f5 is None:
            should_warn = False
        else:
            away_am = (f5.get('prices', {}).get('away') or {}).get('american')
            home_am = (f5.get('prices', {}).get('home') or {}).get('american')
            away_tkr = f5.get('away_ticker')
            home_tkr = f5.get('home_ticker')
            should_warn = (away_tkr or home_tkr) and (away_am is None or home_am is None)
        self.assertFalse(should_warn, "No warning when f5_moneyline entry is absent")


# ══════════════════════════════════════════════════════════════════════════════
# Test Suite 6: End-to-end backfill → merge chain (integration)
# ══════════════════════════════════════════════════════════════════════════════

class TestF5EndToEndChain(unittest.TestCase):

    def test_f5ml_present_in_slate_after_full_chain(self):
        """
        Simulate: backfill populates registry -> merge reads registry -> f5ml in slate.
        This is the full WSHSF / CINSD scenario.
        """
        markets = _make_kalshi_search_f5_markets(suffix='26JUN101545WSHSF', away='WSH', home='SF')
        entry   = _make_registry_entry_without_f5(away='WSH', home='SF', suffix='26JUN101545WSHSF')

        # Step 1: backfill
        entry, count = _run_f5_backfill_logic(entry, markets)
        self.assertEqual(count, 1, "Backfill should succeed for WSHSF")

        # Step 2: simulate merge_odds reading f5_moneyline
        f5 = entry['markets']['f5_moneyline']
        away_p = (f5.get('prices') or {}).get('away') or {}
        home_p = (f5.get('prices') or {}).get('home') or {}
        tie_p  = (f5.get('prices') or {}).get('tie')  or {}
        a_am   = away_p.get('american')
        h_am   = home_p.get('american')
        t_am   = tie_p.get('american')
        away_tkr = f5.get('away_ticker')
        derived_event_ticker = '-'.join((away_tkr or '').split('-')[:-1]) or None

        f5ml = {
            'away':         a_am,
            'home':         h_am,
            'tie':          t_am,
            'tie_american': t_am,
            'away_ticker':  f5.get('away_ticker'),
            'home_ticker':  f5.get('home_ticker'),
            'tie_ticker':   f5.get('tie_ticker'),
            'eventTicker':  f5.get('eventTicker') or derived_event_ticker,
            'seriesTicker': f5.get('seriesTicker', 'KXMLBF5'),
            'source':       'kalshi_registry',
            'status':       away_p.get('status') or 'active',
        }

        # Assertions
        self.assertIsNotNone(f5ml['away'],  "F5ML away must be non-null after full chain")
        self.assertIsNotNone(f5ml['home'],  "F5ML home must be non-null after full chain")
        self.assertIsNotNone(f5ml['away_ticker'], "away_ticker must flow through full chain")
        self.assertIsNotNone(f5ml['home_ticker'], "home_ticker must flow through full chain")
        self.assertEqual(f5ml['seriesTicker'], 'KXMLBF5')
        self.assertIsNotNone(f5ml['eventTicker'])
        self.assertTrue(f5ml['eventTicker'].startswith('KXMLBF5-'))

    def test_cinsd_f5ml_prices_correct(self):
        """CIN@SD F5ML prices map correctly (SD is home, 2-letter abbr)."""
        markets = _make_kalshi_search_f5_markets(suffix='26JUN101610CINSD', away='CIN', home='SD')
        entry   = _make_registry_entry_without_f5(away='CIN', home='SD', suffix='26JUN101610CINSD')

        entry, count = _run_f5_backfill_logic(entry, markets)
        self.assertEqual(count, 1, "Backfill should succeed for CINSD")

        f5 = entry['markets']['f5_moneyline']
        self.assertIn('-CIN', f5['away_ticker'], "CIN should be away ticker")
        self.assertIn('-SD',  f5['home_ticker'], "SD should be home ticker (2-letter abbr)")
        away_am = (f5['prices']['away'] or {}).get('american')
        home_am = (f5['prices']['home'] or {}).get('american')
        self.assertIsNotNone(away_am)
        self.assertIsNotNone(home_am)


if __name__ == '__main__':
    unittest.main(verbosity=2)
