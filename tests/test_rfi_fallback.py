#!/usr/bin/env python3
"""
tests/test_rfi_fallback.py — RFI Fallback Patch Tests
======================================================
Tests the merge_odds.py RFI fallback that reads NRFI/YRFI prices from
kalshi_search.json when build_kalshi_registry.py failed to populate
the registry 'rfi' block.

Run from repo root:
  PYTHONPATH=scripts python tests/test_rfi_fallback.py
"""
import sys, os, json, unittest, tempfile, shutil

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts')
sys.path.insert(0, SCRIPTS_DIR)

# ── Import the helpers directly from merge_odds
# We need to load them without executing the top-level script side-effects.
# Do this by importing just the two helper functions via exec into a namespace.

def _load_merge_helpers():
    """Load _american_from_mid and _build_rfi_from_ks_market without running the script."""
    src_path = os.path.join(SCRIPTS_DIR, 'merge_odds.py')
    with open(src_path) as f:
        src = f.read()

    ns = {}
    # Execute only the helper function defs (not the top-level I/O)
    # Extract the two functions by finding their def blocks
    lines = src.split('\n')
    collecting = False
    fn_lines = []
    for line in lines:
        if line.startswith('def _american_from_mid') or line.startswith('def _build_rfi_from_ks_market'):
            collecting = True
            fn_lines.append(line)
        elif collecting:
            if line and not line[0].isspace() and not line.startswith('#'):
                # New top-level statement — stop
                collecting = False
            else:
                fn_lines.append(line)
    exec('\n'.join(fn_lines), ns)
    return ns['_american_from_mid'], ns['_build_rfi_from_ks_market']


_american_from_mid, _build_rfi_from_ks_market = _load_merge_helpers()


# ── Sample data builders ──────────────────────────────────────────────────────

def make_ks_rfi_market(team_key="MIAPIT", yrfi_mid=0.485, yes_bid=0.47, yes_ask=0.49):
    """Build a kalshi_search.json RFI market entry (YES=YRFI side)."""
    yrfi_implied = round(yrfi_mid * 100, 2)
    yrfi_am = _american_from_mid(yrfi_mid)
    return {
        "event_ticker": f"KXMLBRFI-26JUN121840{team_key}",
        "market_ticker": f"KXMLBRFI-26JUN121840{team_key}",
        "title": f"Test game First Inning Run?",
        "market_type": "nrfi_yrfi",
        "status": "active",
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "mid": yrfi_mid,
        "implied_pct": yrfi_implied,
        "american_odds": yrfi_am,
        "last_price": yrfi_mid,
    }

def make_registry_rfi(team_key="MIAPIT", yrfi_am=-135, nrfi_am=115):
    """Build a registry 'rfi' block (primary path)."""
    return {
        "ticker": f"KXMLBRFI-26JUN121840{team_key}",
        "yes_is_yrfi": True,
        "prices": {
            "yrfi": {"american": yrfi_am, "implied_pct": 57.0, "side": "YES"},
            "nrfi": {"american": nrfi_am, "implied_pct": 43.0, "side": "NO"},
        },
    }


# ── Suite 1: _american_from_mid helper ───────────────────────────────────────

class TestAmericanFromMid(unittest.TestCase):

    def test_favorite_gives_negative(self):
        """Mid > 0.5 → favorite → negative American odds."""
        result = _american_from_mid(0.60)
        self.assertIsNotNone(result)
        self.assertLess(result, 0)

    def test_underdog_gives_positive(self):
        """Mid < 0.5 → underdog → positive American odds."""
        result = _american_from_mid(0.40)
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)

    def test_even_money(self):
        """Mid = 0.5 → ±100."""
        result = _american_from_mid(0.50)
        self.assertEqual(result, -100)  # convention: exactly 0.5 → -100

    def test_none_input(self):
        self.assertIsNone(_american_from_mid(None))

    def test_zero_input(self):
        self.assertIsNone(_american_from_mid(0))

    def test_one_input(self):
        self.assertIsNone(_american_from_mid(1.0))

    def test_round_trip_nrfi(self):
        """YRFI mid=0.485 → NRFI mid=0.515 → NRFI is slight favorite (negative American)."""
        nrfi_mid = round(1.0 - 0.485, 4)  # 0.515 > 0.5 → favorite
        result = _american_from_mid(nrfi_mid)
        self.assertLess(result, 0)  # NRFI slight favorite → negative American odds


# ── Suite 2: _build_rfi_from_ks_market helper ────────────────────────────────

class TestBuildRFIFromKSMarket(unittest.TestCase):

    def test_produces_expected_fields(self):
        """Output must have all fields build_market_ledger reads."""
        m = make_ks_rfi_market(yrfi_mid=0.485)
        result = _build_rfi_from_ks_market(m)
        self.assertIsNotNone(result)
        for field in ('ticker', 'yrfi_american', 'nrfi_american',
                      'yrfi_implied', 'nrfi_implied', 'source'):
            self.assertIn(field, result, f"Missing field: {field}")

    def test_source_is_fallback(self):
        m = make_ks_rfi_market()
        result = _build_rfi_from_ks_market(m)
        self.assertEqual(result['source'], 'kalshi_search_fallback')

    def test_nrfi_is_complement(self):
        """NRFI implied_pct ≈ 100 - YRFI implied_pct."""
        m = make_ks_rfi_market(yrfi_mid=0.485)
        result = _build_rfi_from_ks_market(m)
        total = result['yrfi_implied'] + result['nrfi_implied']
        self.assertAlmostEqual(total, 100.0, places=1)

    def test_nrfi_american_sign(self):
        """If YRFI is underdog (mid<0.5), NRFI is favorite (negative American)."""
        m = make_ks_rfi_market(yrfi_mid=0.485)  # YRFI slight underdog
        result = _build_rfi_from_ks_market(m)
        # NRFI mid = 0.515 > 0.5 → negative American
        self.assertLess(result['nrfi_american'], 0)

    def test_ticker_preserved(self):
        m = make_ks_rfi_market(team_key="DETCLE")
        result = _build_rfi_from_ks_market(m)
        self.assertIn("DETCLE", result['ticker'])

    def test_returns_none_when_mid_missing(self):
        """No mid price → can't compute NRFI → return None."""
        m = make_ks_rfi_market()
        m.pop('mid')
        result = _build_rfi_from_ks_market(m)
        self.assertIsNone(result)

    def test_nrfi_bid_ask_are_complements(self):
        """NRFI bid = 1 - YRFI ask, NRFI ask = 1 - YRFI bid."""
        m = make_ks_rfi_market(yes_bid=0.47, yes_ask=0.49)
        result = _build_rfi_from_ks_market(m)
        self.assertAlmostEqual(result['nrfi_bid'], 1.0 - 0.49, places=4)
        self.assertAlmostEqual(result['nrfi_ask'], 1.0 - 0.47, places=4)


# ── Suite 3: RFI index building logic (integration) ──────────────────────────

class TestRFIIndexBuilding(unittest.TestCase):
    """
    Tests the _rfi_by_key index-building logic in merge_odds.py
    by running it against controlled kalshi_search.json content.
    """

    def _build_index(self, markets):
        """Replicate the index-building logic from merge_odds.py."""
        rfi_by_key = {}
        for m in markets:
            if m.get('market_type') != 'nrfi_yrfi':
                continue
            et = m.get('event_ticker', '')
            parts = et.split('-')
            if len(parts) < 2:
                continue
            date_team = parts[1] if len(parts) == 2 else '-'.join(parts[1:])
            team_key = date_team[11:] if len(date_team) > 11 else ''
            if not team_key:
                continue
            if team_key in rfi_by_key:
                rfi_by_key[team_key] = '__AMBIGUOUS__'
            else:
                rfi_by_key[team_key] = m
        return rfi_by_key

    def test_single_rfi_market_indexed(self):
        markets = [make_ks_rfi_market("MIAPIT")]
        idx = self._build_index(markets)
        self.assertIn("MIAPIT", idx)
        self.assertNotEqual(idx["MIAPIT"], "__AMBIGUOUS__")

    def test_two_rfi_markets_different_games(self):
        markets = [make_ks_rfi_market("MIAPIT"), make_ks_rfi_market("DETCLE")]
        idx = self._build_index(markets)
        self.assertIn("MIAPIT", idx)
        self.assertIn("DETCLE", idx)
        self.assertNotEqual(idx["MIAPIT"], "__AMBIGUOUS__")
        self.assertNotEqual(idx["DETCLE"], "__AMBIGUOUS__")

    def test_ambiguous_same_key_marked_ambiguous(self):
        """Two markets with same team key → AMBIGUOUS, not guessed."""
        m1 = make_ks_rfi_market("MIAPIT")
        m2 = dict(m1)
        m2['market_ticker'] = m2['event_ticker'] + "-DUP"
        markets = [m1, m2]
        idx = self._build_index(markets)
        self.assertEqual(idx.get("MIAPIT"), "__AMBIGUOUS__",
                         "Duplicate key must be marked AMBIGUOUS, not guessed")

    def test_non_rfi_market_not_indexed(self):
        """Non-nrfi_yrfi markets must be ignored."""
        ml_market = {
            "event_ticker": "KXMLBGAME-26JUN121840MIAPIT-PIT",
            "market_ticker": "KXMLBGAME-26JUN121840MIAPIT-PIT",
            "market_type": "moneyline",
        }
        markets = [ml_market]
        idx = self._build_index(markets)
        self.assertEqual(len(idx), 0, "ML market must not appear in RFI index")

    def test_missing_game_key_not_in_index(self):
        idx = self._build_index([make_ks_rfi_market("MIAPIT")])
        self.assertNotIn("CHCSF", idx)


# ── Suite 4: Full merge_odds.py integration (subprocess-based) ───────────────

class TestMergeOddsRFIIntegration(unittest.TestCase):
    """
    Runs merge_odds.py end-to-end in a temp directory with controlled data,
    verifying that nrfi_yrfi is populated correctly.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data_dir = os.path.join(self.tmp, 'data')
        os.makedirs(self.data_dir)
        self._orig = os.getcwd()
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self._orig)
        shutil.rmtree(self.tmp)

    def _write(self, filename, data):
        with open(os.path.join(self.data_dir, filename), 'w') as f:
            json.dump(data, f)

    def _read_slate(self):
        with open(os.path.join(self.data_dir, 'slate.json')) as f:
            return json.load(f)

    def _run_merge(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, os.path.join(self._orig, SCRIPTS_DIR, 'merge_odds.py')],
            capture_output=True, text=True, cwd=self.tmp
        )
        return result.returncode, result.stdout, result.stderr

    def _make_slate(self):
        return {
            "date": "2026-06-12",
            "games": [{
                "away": {"team": "Miami Marlins", "abbr": "MIA"},
                "home": {"team": "Pittsburgh Pirates", "abbr": "PIT"},
            }]
        }

    def _make_odds(self):
        return {
            "games": [{
                "awayTeam": "Miami Marlins",
                "homeTeam": "Pittsburgh Pirates",
                "awayAbbr": "MIA",
                "homeAbbr": "PIT",
                "commenceTime": "2026-06-12T22:40:00Z",
                "pinnacleVF": {"away": 43.8, "home": 56.2},
                "books": {},
            }]
        }

    def _make_registry_no_rfi(self):
        """Registry entry without 'rfi' block — simulates today's bug."""
        return {
            "registry": {
                "MIAPIT": {
                    "kalshi_key": "MIAPIT",
                    "game_time_et": "6:40 PM",
                    "markets": {
                        "moneyline": {
                            "prices": {
                                "away": {"american": 135, "mid": 0.425},
                                "home": {"american": -135, "mid": 0.575},
                            }
                        }
                    }
                }
            }
        }

    def _make_registry_with_rfi(self):
        """Registry entry WITH 'rfi' block — normal case."""
        reg = self._make_registry_no_rfi()
        reg["registry"]["MIAPIT"]["markets"]["rfi"] = make_registry_rfi("MIAPIT")
        return reg

    def _make_kalshi_search_with_rfi(self):
        return {
            "date": "2026-06-12",
            "markets": [make_ks_rfi_market("MIAPIT", yrfi_mid=0.485)],
            "results": [],
        }

    def _make_kalshi_search_no_rfi(self):
        return {"date": "2026-06-12", "markets": [], "results": []}

    # ── Test 1: Registry has RFI → uses registry, NOT fallback ───────────────

    def test_registry_rfi_present_uses_registry(self):
        """When registry has 'rfi', merge must use registry prices, not fallback."""
        self._write('slate.json', self._make_slate())
        self._write('odds.json', self._make_odds())
        self._write('kalshi_market_registry.json', self._make_registry_with_rfi())
        self._write('kalshi_search.json', self._make_kalshi_search_with_rfi())

        code, out, err = self._run_merge()
        self.assertEqual(code, 0, f"merge_odds.py crashed\nSTDOUT:{out}\nSTDERR:{err}")

        slate = self._read_slate()
        nrfi_yrfi = slate['games'][0]['odds']['kalshi'].get('nrfi_yrfi', {})
        self.assertIsNotNone(nrfi_yrfi.get('ticker'), "ticker must be present")
        self.assertEqual(nrfi_yrfi.get('source'), 'kalshi_registry',
                         "Registry RFI must come from registry, not fallback")
        self.assertEqual(nrfi_yrfi.get('yrfi_american'), -135,
                         "Registry YRFI american must be used (-135)")

    # ── Test 2: Registry missing RFI + kalshi_search has it → fallback fires ─

    def test_registry_no_rfi_fallback_populates(self):
        """When registry has no 'rfi' but kalshi_search has prices → nrfi_yrfi populated."""
        self._write('slate.json', self._make_slate())
        self._write('odds.json', self._make_odds())
        self._write('kalshi_market_registry.json', self._make_registry_no_rfi())
        self._write('kalshi_search.json', self._make_kalshi_search_with_rfi())

        code, out, err = self._run_merge()
        self.assertEqual(code, 0, f"merge_odds.py crashed\nSTDOUT:{out}\nSTDERR:{err}")

        slate = self._read_slate()
        nrfi_yrfi = slate['games'][0]['odds']['kalshi'].get('nrfi_yrfi', {})
        self.assertIsNotNone(nrfi_yrfi.get('nrfi_american'),
                             "nrfi_american must be populated by fallback")
        self.assertIsNotNone(nrfi_yrfi.get('yrfi_american'),
                             "yrfi_american must be populated by fallback")
        self.assertEqual(nrfi_yrfi.get('source'), 'kalshi_search_fallback')

    # ── Test 3: Fallback produces prices build_market_ledger can consume ─────

    def test_fallback_prices_consumable_by_build_market_ledger(self):
        """nrfi_american and yrfi_american must be numeric (not None) after fallback."""
        self._write('slate.json', self._make_slate())
        self._write('odds.json', self._make_odds())
        self._write('kalshi_market_registry.json', self._make_registry_no_rfi())
        self._write('kalshi_search.json', self._make_kalshi_search_with_rfi())

        self._run_merge()
        slate = self._read_slate()
        nrfi_yrfi = slate['games'][0]['odds']['kalshi'].get('nrfi_yrfi', {})

        nrfi_am = nrfi_yrfi.get('nrfi_american')
        yrfi_am = nrfi_yrfi.get('yrfi_american')
        self.assertIsNotNone(nrfi_am, "nrfi_american must not be None")
        self.assertIsNotNone(yrfi_am, "yrfi_american must not be None")
        self.assertIsInstance(nrfi_am, (int, float))
        self.assertIsInstance(yrfi_am, (int, float))
        # Implied probs must be present too
        self.assertIsNotNone(nrfi_yrfi.get('nrfi_implied'))
        self.assertIsNotNone(nrfi_yrfi.get('yrfi_implied'))

    # ── Test 4: Ambiguous RFI match → Missing Data (no crash, no guess) ──────

    def test_ambiguous_rfi_does_not_guess(self):
        """Two RFI markets for same game → no nrfi_yrfi populated, no crash."""
        m1 = make_ks_rfi_market("MIAPIT")
        m2 = dict(m1)
        m2['market_ticker'] = m2['event_ticker'] + "-DUP"
        ks = {"date": "2026-06-12", "markets": [m1, m2], "results": []}

        self._write('slate.json', self._make_slate())
        self._write('odds.json', self._make_odds())
        self._write('kalshi_market_registry.json', self._make_registry_no_rfi())
        self._write('kalshi_search.json', ks)

        code, out, err = self._run_merge()
        self.assertEqual(code, 0, f"Must not crash on ambiguous match\nSTDOUT:{out}\nSTDERR:{err}")
        slate = self._read_slate()
        nrfi_yrfi = slate['games'][0]['odds']['kalshi'].get('nrfi_yrfi')
        self.assertIsNone(nrfi_yrfi,
                          "Ambiguous RFI must NOT populate nrfi_yrfi — no guessing")

    # ── Test 5: No match → Missing Data, no crash ────────────────────────────

    def test_no_rfi_match_leaves_missing_data(self):
        """Registry missing rfi + kalshi_search has no RFI → nrfi_yrfi absent, no crash."""
        self._write('slate.json', self._make_slate())
        self._write('odds.json', self._make_odds())
        self._write('kalshi_market_registry.json', self._make_registry_no_rfi())
        self._write('kalshi_search.json', self._make_kalshi_search_no_rfi())

        code, out, err = self._run_merge()
        self.assertEqual(code, 0, f"Must not crash when no RFI match\nSTDOUT:{out}\nSTDERR:{err}")
        slate = self._read_slate()
        nrfi_yrfi = slate['games'][0]['odds']['kalshi'].get('nrfi_yrfi')
        self.assertIsNone(nrfi_yrfi,
                          "No match must leave nrfi_yrfi absent (Missing Data in ledger)")

    # ── Test 6: Non-RFI markets unaffected ───────────────────────────────────

    def test_non_rfi_markets_unaffected(self):
        """ML prices in registry must not be changed by RFI fallback logic."""
        reg = self._make_registry_no_rfi()
        # ML prices are in the registry
        ml_away_am = reg["registry"]["MIAPIT"]["markets"]["moneyline"]["prices"]["away"]["american"]

        ks = {"date": "2026-06-12",
              "markets": [make_ks_rfi_market("MIAPIT")], "results": []}

        self._write('slate.json', self._make_slate())
        self._write('odds.json', self._make_odds())
        self._write('kalshi_market_registry.json', reg)
        self._write('kalshi_search.json', ks)

        self._run_merge()
        slate = self._read_slate()
        ml = slate['games'][0]['odds']['kalshi'].get('ml', {})
        self.assertEqual(ml.get('away'), ml_away_am,
                         "ML price must be unchanged by RFI fallback")


# ── Run all tests ─────────────────────────────────────────────────────────────

def run_all():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    test_classes = [
        TestAmericanFromMid,
        TestBuildRFIFromKSMarket,
        TestRFIIndexBuilding,
        TestMergeOddsRFIIntegration,
    ]
    for tc in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(tc))

    runner = unittest.TextTestRunner(verbosity=2, failfast=False)
    result = runner.run(suite)

    print("\n" + "=" * 60)
    print("RFI FALLBACK TEST SUMMARY")
    print("=" * 60)
    print(f"Tests run:  {result.testsRun}")
    print(f"Failures:   {len(result.failures)}")
    print(f"Errors:     {len(result.errors)}")
    print(f"Status:     {'PASS' if result.wasSuccessful() else 'FAIL'}")

    if result.failures:
        print("\nFAILURES:")
        for test, tb in result.failures:
            print(f"  {test}: {tb.splitlines()[-1]}")
    if result.errors:
        print("\nERRORS:")
        for test, tb in result.errors:
            print(f"  {test}: {tb.splitlines()[-1]}")

    return result.wasSuccessful()


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
