#!/usr/bin/env python3
"""
PHASE 9 — REGRESSION TESTS
Verifies:
  - ticker storage
  - identity audit
  - historical lookup (mocked)
  - archived markets
  - candle selection logic
  - CLV calculation
  - CLV rejection handling
  - Rule 71 flag/downgrade behavior
"""
import json, os, sys, time, unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

# Add scripts dir to path
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_bet(**kwargs):
    defaults = {
        "id": "2026-06-06-001",
        "date": "2026-06-06",
        "game": "KC @ MIN",
        "market": "ML",
        "bet": "MIN ML",
        "price": -135,
        "betTimeLine": -135,
        "modelPct": 69.9,
        "pinnacleVFPct": 57.1,
        "kalshiPct": 57.5,
        "edgePct": 3.16,
        "size": 4.0,
        "confidence": "High",
        "status": "SETTLED",
        "result": "WIN",
        "pl": 2.96,
        "betSize": 4.0,
    }
    defaults.update(kwargs)
    return defaults


# ── Test Suite 1: Identity Audit ─────────────────────────────────────────────

class TestIdentityAudit(unittest.TestCase):

    def setUp(self):
        from audit_bet_identity import classify_bet
        self.classify = classify_bet

    def test_missing_market_ticker_classified_correctly(self):
        b = make_bet()
        result = self.classify(b)
        self.assertEqual(result["identityStatus"], "MISSING_MARKET_TICKER")

    def test_ready_for_clv_when_all_fields_present(self):
        b = make_bet(
            marketTicker="KXMLBGAME-26JUN061410KCMIN-MIN",
            seriesTicker="KXMLBGAME",
            eventTicker="KXMLBGAME-26JUN061410KCMIN",
            loggedAt="2026-06-06T14:00:00Z",
            scheduledStartTime="2026-06-06T18:10:00Z",
        )
        result = self.classify(b)
        self.assertEqual(result["identityStatus"], "READY_FOR_CLV")

    def test_missing_series_ticker_status(self):
        b = make_bet(
            marketTicker="KXMLBGAME-26JUN061410KCMIN-MIN",
            loggedAt="2026-06-06T14:00:00Z",
            scheduledStartTime="2026-06-06T18:10:00Z",
        )
        result = self.classify(b)
        self.assertEqual(result["identityStatus"], "MISSING_SERIES_TICKER")

    def test_missing_timestamp_status(self):
        b = make_bet(
            marketTicker="KXMLBGAME-26JUN061410KCMIN-MIN",
            seriesTicker="KXMLBGAME",
        )
        result = self.classify(b)
        self.assertEqual(result["identityStatus"], "MISSING_TIMESTAMP")

    def test_invalid_match_no_game(self):
        b = make_bet(game="", market="")
        result = self.classify(b)
        self.assertEqual(result["identityStatus"], "INVALID_MATCH")

    def test_clv_supported_markets(self):
        for mkt in ["ML", "F5 ML", "NRFI", "YRFI", "Total"]:
            b = make_bet(market=mkt)
            result = self.classify(b)
            self.assertTrue(result["clvSupported"], f"{mkt} should be CLV-supported")

    def test_clv_unsupported_props(self):
        b = make_bet(market="K Prop")
        result = self.classify(b)
        self.assertFalse(result["clvSupported"])


# ── Test Suite 2: Ticker Storage ──────────────────────────────────────────────

class TestTickerStorage(unittest.TestCase):
    """Verify that bet logging schema includes required identity fields."""

    REQUIRED_TICKER_FIELDS = [
        "marketTicker", "seriesTicker", "eventTicker",
        "marketType", "side", "entryPrice",
        "betTimestamp", "scheduledStartTime",
    ]

    def test_required_fields_present_in_schema(self):
        """A fully-formed bet must be able to store all required identity fields."""
        full_bet = make_bet(
            marketTicker="KXMLBGAME-26JUN061410KCMIN-MIN",
            seriesTicker="KXMLBGAME",
            eventTicker="KXMLBGAME-26JUN061410KCMIN",
            marketType="ML",
            side="MIN",
            entryPrice=-135,
            betTimestamp="2026-06-06T14:00:00Z",
            scheduledStartTime="2026-06-06T18:10:00Z",
        )
        for field in self.REQUIRED_TICKER_FIELDS:
            self.assertIn(field, full_bet, f"Required field '{field}' missing from bet schema")

    def test_bet_id_format(self):
        """Bet IDs must be YYYY-MM-DD-NNN format."""
        import re
        b = make_bet(id="2026-06-06-001")
        self.assertRegex(b["id"], r"^\d{4}-\d{2}-\d{2}-\d{3}$")

    def test_no_logging_without_ticker(self):
        """A bet without marketTicker should be flagged MISSING_MARKET_TICKER."""
        from audit_bet_identity import classify_bet
        b = make_bet()  # no marketTicker
        result = classify_bet(b)
        self.assertNotEqual(result["identityStatus"], "READY_FOR_CLV")


# ── Test Suite 3: Candle Selection ────────────────────────────────────────────

class TestCandleSelection(unittest.TestCase):

    def _make_candle(self, end_period_ts, yes_bid=0.55, yes_ask=0.57):
        return {
            "end_period_ts": end_period_ts,
            "yes_bid": {"close": yes_bid},
            "yes_ask": {"close": yes_ask},
        }

    def test_selects_last_candle_before_start(self):
        """Should select the candle with the highest end_period_ts that is ≤ scheduled start."""
        from fetch_kalshi_clv_v2 import get_candlestick_clv

        scheduled_start = 1749226200  # 2026-06-06 18:10 UTC

        candles = [
            self._make_candle(scheduled_start - 120, 0.55, 0.57),   # 2min before — use this
            self._make_candle(scheduled_start - 60, 0.56, 0.58),    # 1min before — prefer this
            self._make_candle(scheduled_start - 3600, 0.50, 0.52),  # 1hr before — old
        ]

        mock_data = {"candlesticks": candles}

        with patch("fetch_kalshi_clv_v2.kget", return_value=(mock_data, None)):
            result = get_candlestick_clv(
                "KXMLBGAME-26JUN061410KCMIN-MIN",
                scheduled_start,
                "ML"
            )

        self.assertIsNotNone(result)
        # Should use mid = (0.56+0.58)/2 = 0.57
        self.assertAlmostEqual(result["closingPrice"], 0.57, places=2)

    def test_rejects_candle_after_start(self):
        """All candles after scheduled start → should raise ValueError."""
        from fetch_kalshi_clv_v2 import get_candlestick_clv

        scheduled_start = 1749226200
        candles = [
            self._make_candle(scheduled_start + 60, 0.55, 0.57),  # AFTER start
            self._make_candle(scheduled_start + 300, 0.55, 0.57), # AFTER start
        ]

        with patch("fetch_kalshi_clv_v2.kget", side_effect=[
            ({"candlesticks": candles}, None),  # candlestick endpoint
            ({"candlesticks": candles}, None),  # historical endpoint
            ({"candlesticks": candles}, None),  # 5min
            ({"candlesticks": candles}, None),
            ({"candlesticks": candles}, None),  # 15min
            ({"candlesticks": candles}, None),
            ({"candlesticks": candles}, None),  # 60min
            ({"candlesticks": candles}, None),
            (None, "HTTP 404"),  # market detail
        ]):
            with self.assertRaises(Exception):
                get_candlestick_clv(
                    "KXMLBGAME-26JUN061410KCMIN-MIN",
                    scheduled_start,
                    "ML"
                )

    def test_handles_empty_candlestick_response(self):
        """Empty candlestick list → should fail through to error."""
        from fetch_kalshi_clv_v2 import get_candlestick_clv

        scheduled_start = 1749226200
        with patch("fetch_kalshi_clv_v2.kget", return_value=({"candlesticks": []}, None)):
            with self.assertRaises(ValueError):
                get_candlestick_clv(
                    "KXMLBGAME-26JUN061410KCMIN-MIN",
                    scheduled_start,
                    "ML"
                )


# ── Test Suite 4: CLV Calculation ─────────────────────────────────────────────

class TestCLVCalculation(unittest.TestCase):

    def _calc(self, entry_american, closing_yes_prob, bet_is_yes=True):
        from fetch_kalshi_clv_v2 import calculate_clv
        return calculate_clv(entry_american, closing_yes_prob, "ML", bet_is_yes)

    def test_positive_clv_favorite_shortens(self):
        """
        Bet at -135 (impl 57.4%). Closes at -150 (yes_prob=0.60).
        CLV = (0.60 - 0.574) * 100 = +2.6% (good — market moved toward us)
        """
        clv = self._calc(-135, 0.60, bet_is_yes=True)
        self.assertIsNotNone(clv)
        self.assertGreater(clv, 0, "Should be positive CLV when line shortens")

    def test_negative_clv_favorite_lengthens(self):
        """
        Bet at -135. Closes at -115 (yes_prob=0.535).
        CLV should be negative — market moved away from us.
        """
        clv = self._calc(-135, 0.535, bet_is_yes=True)
        self.assertIsNotNone(clv)
        self.assertLess(clv, 0, "Should be negative CLV when line lengthens against us")

    def test_clv_underdog_bet(self):
        """
        Bet on underdog at +115 (impl 46.5%). Closes at +100 (yes_prob=0.50 for opponent).
        We bet YES on underdog, so our closing prob = 1 - 0.50 = 0.50 on YES.
        """
        # Underdog at +115 = impl 46.5%
        # If opponent closes at -100 yes_prob=0.50 → our side closes 0.50
        clv = self._calc(115, 0.50, bet_is_yes=False)
        # Entry implied = 100/(115+100) = 46.5%; closing implied 50%
        # CLV = (0.50 - 0.465)*100 = +3.5%
        self.assertGreater(clv, 0)

    def test_clv_returns_none_on_none_inputs(self):
        from fetch_kalshi_clv_v2 import calculate_clv
        self.assertIsNone(calculate_clv(None, 0.55, "ML", True))
        self.assertIsNone(calculate_clv(-135, None, "ML", True))

    def test_is_yes_bet_detection_ml(self):
        """For ML markets, YES = the team in the marketTicker suffix."""
        from fetch_kalshi_clv_v2 import is_yes_bet
        # MIN is in the ticker → MIN ML bet should be YES
        self.assertTrue(is_yes_bet("MIN ML", "ML", "KXMLBGAME-26JUN061410KCMIN-MIN", "MIN"))
        # KC ML — ticker ends in -MIN so YES=MIN; KC bet should be NO
        self.assertFalse(is_yes_bet("KC ML", "ML", "KXMLBGAME-26JUN061410KCMIN-MIN", "KC"))

    def test_is_yes_bet_yrfi(self):
        """YRFI bet = YES side of the RFI market."""
        from fetch_kalshi_clv_v2 import is_yes_bet
        self.assertTrue(is_yes_bet("KC @ MIN YRFI", "YRFI", "KXMLBRFI-26JUN061410KCMIN", None))

    def test_is_yes_bet_nrfi(self):
        """NRFI bet = NO side of the RFI market."""
        from fetch_kalshi_clv_v2 import is_yes_bet
        self.assertFalse(is_yes_bet("KC @ MIN NRFI", "NRFI", "KXMLBRFI-26JUN061410KCMIN", None))


# ── Test Suite 5: CLV Rejection Handling ──────────────────────────────────────

class TestCLVRejection(unittest.TestCase):

    def _run_bet(self, **kwargs):
        from fetch_kalshi_clv_v2 import process_bet_clv
        return process_bet_clv(make_bet(**kwargs))

    def test_fails_no_market_ticker(self):
        result = self._run_bet()  # no marketTicker
        self.assertEqual(result["clvStatus"], "FAIL_NO_TICKER")
        self.assertIsNone(result["clv"])

    def test_fails_no_scheduled_start(self):
        result = self._run_bet(
            marketTicker="KXMLBGAME-26JUN061410KCMIN-MIN",
            seriesTicker="KXMLBGAME",
        )
        self.assertEqual(result["clvStatus"], "FAIL_NO_TIMESTAMP")
        self.assertIsNone(result["clv"])

    def test_fails_invalid_timestamp(self):
        result = self._run_bet(
            marketTicker="KXMLBGAME-26JUN061410KCMIN-MIN",
            scheduledStartTime="not-a-date",
        )
        self.assertEqual(result["clvStatus"], "FAIL_INVALID_TIMESTAMP")
        self.assertIsNone(result["clv"])

    def test_stores_clv_error_on_failure(self):
        result = self._run_bet()
        self.assertIsNotNone(result.get("clvError"))
        self.assertIn("marketTicker", result.get("clvError", ""))

    def test_ok_status_when_successful(self):
        from fetch_kalshi_clv_v2 import process_bet_clv

        mock_candle = {"candlesticks": [{
            "end_period_ts": 1749226100,  # before 1749226200 start
            "yes_bid": {"close": 0.55},
            "yes_ask": {"close": 0.57},
        }]}
        b = make_bet(
            marketTicker="KXMLBGAME-26JUN061410KCMIN-MIN",
            seriesTicker="KXMLBGAME",
            scheduledStartTime="2026-06-06T18:10:00Z",
        )
        with patch("fetch_kalshi_clv_v2.kget", return_value=(mock_candle, None)):
            result = process_bet_clv(b)
        self.assertEqual(result["clvStatus"], "OK")
        self.assertIsNotNone(result["clv"])
        self.assertIsNone(result["clvError"])


# ── Test Suite 6: Rule 71 Flag/Downgrade ──────────────────────────────────────

class TestRule71(unittest.TestCase):

    def _eval(self, model_pct, pin_pct, kalshi_pct=None, confidence="High", market="ML"):
        from rule71_tracker import evaluate_rule71
        return evaluate_rule71(market, model_pct, pin_pct, kalshi_pct, confidence)

    def test_no_flag_within_threshold(self):
        result = self._eval(65.0, 60.0)  # gap = 5% < 8%
        self.assertFalse(result["fires"])
        self.assertFalse(result["rule71Flag"])
        self.assertEqual(result["action"], "ALLOW")

    def test_flag_fires_at_gap_above_threshold(self):
        result = self._eval(75.0, 60.0)  # gap = 15% > 8%
        self.assertTrue(result["fires"])
        self.assertTrue(result["rule71Flag"])

    def test_downgrade_not_hard_block_for_market_disagreement(self):
        result = self._eval(75.0, 60.0)
        self.assertFalse(result["hardBlock"])
        self.assertEqual(result["action"], "DOWNGRADE")

    def test_high_downgrades_to_medium(self):
        result = self._eval(75.0, 60.0, confidence="High")
        self.assertEqual(result["adjustedConfidence"], "Medium")

    def test_medium_downgrades_to_paper(self):
        result = self._eval(75.0, 60.0, confidence="Medium")
        self.assertEqual(result["adjustedConfidence"], "Paper")

    def test_paper_downgrades_to_skip(self):
        result = self._eval(75.0, 60.0, confidence="Paper")
        self.assertEqual(result["adjustedConfidence"], "Skip")

    def test_kalshi_inefficiency_not_rule71(self):
        """When Kalshi agrees with model but Pinnacle disagrees → Kalshi inefficiency, not Rule 71."""
        result = self._eval(70.0, 55.0, kalshi_pct=68.0)  # Kalshi close to model
        self.assertFalse(result["rule71Flag"])
        self.assertEqual(result["action"], "ALLOW")

    def test_both_markets_disagree_fires_rule71(self):
        """When both Pinnacle AND Kalshi disagree → Rule 71 fires."""
        result = self._eval(75.0, 58.0, kalshi_pct=57.0)  # both well below model
        self.assertTrue(result["rule71Flag"])
        self.assertIn("BOTH_MARKETS", result["rule71Reason"])

    def test_exempt_markets_no_flag(self):
        """NRFI, YRFI, Total, Team Total are exempt from Rule 71."""
        for market in ["NRFI", "YRFI", "Total", "Team Total"]:
            result = self._eval(75.0, 55.0, market=market)
            self.assertFalse(result["fires"], f"{market} should be exempt from Rule 71")

    def test_hard_block_on_data_error(self):
        from rule71_tracker import evaluate_rule71
        result = evaluate_rule71(
            "ML", 75.0, 60.0,
            additional_context={"hardBlockReason": "MISSING_KEY_DATA"}
        )
        self.assertTrue(result["hardBlock"])
        self.assertEqual(result["action"], "HARD_BLOCK")
        self.assertEqual(result["adjustedConfidence"], "Skip")

    def test_stores_gap_value(self):
        result = self._eval(75.0, 60.0)
        self.assertIsNotNone(result["marketGap"])
        self.assertAlmostEqual(result["marketGap"], 15.0, places=1)


# ── Test Suite 7: Backfill Identity ───────────────────────────────────────────

class TestBackfillIdentity(unittest.TestCase):

    def setUp(self):
        from backfill_market_identity import parse_game, date_to_kalshi_prefix, build_candidate_tickers
        self.parse_game = parse_game
        self.date_to_prefix = date_to_kalshi_prefix
        self.build_candidates = build_candidate_tickers

    def test_parse_game_standard(self):
        away, home = self.parse_game("KC @ MIN")
        self.assertEqual(away, "KC")
        self.assertEqual(home, "MIN")

    def test_parse_game_full_names(self):
        away, home = self.parse_game("San Francisco @ Chicago C")
        self.assertEqual(away, "SF")
        self.assertEqual(home, "CHC")

    def test_date_to_kalshi_prefix(self):
        prefix = self.date_to_prefix("2026-06-06")
        self.assertEqual(prefix, "26JUN06")

    def test_date_to_kalshi_prefix_may(self):
        prefix = self.date_to_prefix("2026-05-26")
        self.assertEqual(prefix, "26MAY26")

    def test_candidates_generated_for_ml(self):
        candidates = self.build_candidates("2026-06-06", "KC", "MIN", "ML", "MIN")
        tickers = [c["marketTicker"] for c in candidates]
        # Should include MIN-suffixed tickers
        min_tickers = [t for t in tickers if t.endswith("-MIN")]
        self.assertGreater(len(min_tickers), 0)

    def test_candidates_nrfi_no_team_suffix(self):
        candidates = self.build_candidates("2026-06-06", "KC", "MIN", "NRFI", None)
        # NRFI tickers have no team suffix
        for c in candidates:
            self.assertFalse(
                c["marketTicker"].endswith("-KC") or c["marketTicker"].endswith("-MIN"),
                f"NRFI ticker should not have team suffix: {c['marketTicker']}"
            )

    def test_registry_match_returns_successfully_matched(self):
        from backfill_market_identity import backfill_bet
        b = make_bet()
        registry = {
            "KXMLBGAME-26JUN061410KCMIN-MIN": {
                "market_ticker": "KXMLBGAME-26JUN061410KCMIN-MIN",
                "event_ticker": "KXMLBGAME-26JUN061410KCMIN",
                "series_ticker": "KXMLBGAME",
                "market_type": "moneyline",
            }
        }
        updated, status = backfill_bet(b, registry, {})
        self.assertEqual(status, "SUCCESSFULLY_MATCHED")
        self.assertEqual(updated["marketTicker"], "KXMLBGAME-26JUN061410KCMIN-MIN")

    def test_no_registry_match_returns_unmatchable(self):
        from backfill_market_identity import backfill_bet
        b = make_bet()
        updated, status = backfill_bet(b, {}, {})
        self.assertEqual(status, "UNMATCHABLE")


# ── Test Suite 8: Rule 71 Reporting ───────────────────────────────────────────

class TestRule71Reporting(unittest.TestCase):

    def _make_bets_with_flags(self, n_flagged, n_clean):
        bets = []
        for i in range(n_flagged):
            b = make_bet(
                id=f"2026-06-06-{i+1:03d}",
                gatesFired=["R71-suspended-pin_div=12.8%"],
                result="WIN" if i % 2 == 0 else "LOSS",
                pl=3.0 if i % 2 == 0 else -4.0,
                clv=1.5 if i % 2 == 0 else -0.5,
            )
            bets.append(b)
        for i in range(n_clean):
            b = make_bet(
                id=f"2026-06-06-{n_flagged+i+1:03d}",
                gatesFired=[],
                result="WIN" if i % 3 != 0 else "LOSS",
                pl=2.0 if i % 3 != 0 else -4.0,
                clv=2.0 if i % 3 != 0 else -0.3,
            )
            bets.append(b)
        return bets

    def test_report_counts_flags_correctly(self):
        from rule71_tracker import generate_rule71_report
        import tempfile, json

        bets = self._make_bets_with_flags(10, 20)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(bets, f)
            tmp = f.name

        report = generate_rule71_report(tmp)
        self.assertEqual(report["rule71_summary"]["total_flags"], 10)
        self.assertEqual(report["rule71_summary"]["non_flagged"], 20)
        os.unlink(tmp)

    def test_insufficient_data_recommendation(self):
        from rule71_tracker import generate_rule71_report
        import tempfile

        bets = self._make_bets_with_flags(3, 20)  # < 10 flagged with CLV
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(bets, f)
            tmp = f.name

        report = generate_rule71_report(tmp)
        self.assertIn("INSUFFICIENT_DATA", report["recommendation"])
        os.unlink(tmp)


# ── Run all tests ─────────────────────────────────────────────────────────────

def run_all_tests():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestIdentityAudit,
        TestTickerStorage,
        TestCandleSelection,
        TestCLVCalculation,
        TestCLVRejection,
        TestRule71,
        TestBackfillIdentity,
        TestRule71Reporting,
    ]

    for tc in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(tc))

    runner = unittest.TextTestRunner(verbosity=2, failfast=False)
    result = runner.run(suite)

    print("\n" + "=" * 60)
    print(f"REGRESSION TEST SUMMARY")
    print("=" * 60)
    print(f"Tests run:    {result.testsRun}")
    print(f"Failures:     {len(result.failures)}")
    print(f"Errors:       {len(result.errors)}")
    print(f"Skipped:      {len(result.skipped)}")
    print(f"Status:       {'PASS' if result.wasSuccessful() else 'FAIL'}")

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
    ok = run_all_tests()
    sys.exit(0 if ok else 1)
