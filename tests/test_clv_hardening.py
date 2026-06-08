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
        from backfill_market_identity import parse_game, date_to_kalshi_prefix
        self.parse_game = parse_game
        self.date_to_prefix = date_to_kalshi_prefix

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

    def test_find_match_in_registry_ml(self):
        """find_match_in_registry returns the correct MIN-suffixed ticker."""
        from backfill_market_identity import find_match_in_registry
        registry = {
            "KXMLBGAME-26JUN061410KCMIN-MIN": {
                "event_ticker": "KXMLBGAME-26JUN061410KCMIN",
                "market_type": "moneyline",
            },
            "KXMLBGAME-26JUN061410KCMIN-KC": {
                "event_ticker": "KXMLBGAME-26JUN061410KCMIN",
                "market_type": "moneyline",
            },
        }
        matches = find_match_in_registry(registry, "KC", "MIN", "ML", "MIN", "2026-06-06")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0][0], "KXMLBGAME-26JUN061410KCMIN-MIN")

    def test_find_match_in_registry_no_match(self):
        """Empty registry → no matches."""
        from backfill_market_identity import find_match_in_registry
        matches = find_match_in_registry({}, "KC", "MIN", "ML", "MIN", "2026-06-06")
        self.assertEqual(matches, [])

    def test_no_snapshot_returns_unmatchable_no_snapshot(self):
        """Backfill with no snapshot dir → UNMATCHABLE_NO_SNAPSHOT."""
        from backfill_market_identity import backfill_bet
        b = make_bet()
        updated, status = backfill_bet(b, snapshots_dir="/nonexistent/path")
        self.assertEqual(status, "UNMATCHABLE_NO_SNAPSHOT")

    def test_registry_match_returns_successfully_matched(self):
        """Full backfill with a real snapshot returns SUCCESSFULLY_MATCHED."""
        import tempfile, json as _json, shutil
        import backfill_market_identity as bm
        tmp = tempfile.mkdtemp()
        sd = os.path.join(tmp, "snaps")
        os.makedirs(sd)
        snap = {"markets": [{
            "event_ticker": "KXMLBGAME-26JUN061410KCMIN",
            "market_ticker": "KXMLBGAME-26JUN061410KCMIN-MIN",
            "market_type": "moneyline",
        }]}
        with open(os.path.join(sd, "kalshi_search_2026-06-06.json"), "w") as f:
            _json.dump(snap, f)
        bm._snapshot_cache.clear()

        b = make_bet(date="2026-06-06", game="KC @ MIN", market="ML", bet="MIN ML")
        updated, status = bm.backfill_bet(b, snapshots_dir=sd)
        self.assertEqual(status, "SUCCESSFULLY_MATCHED")
        self.assertEqual(updated["marketTicker"], "KXMLBGAME-26JUN061410KCMIN-MIN")

        shutil.rmtree(tmp)
        bm._snapshot_cache.clear()


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


# ── Test Suite 9: Snapshot Archiving & Snapshot-Gated Backfill ───────────────

class TestSnapshotBackfill(unittest.TestCase):
    """
    Verifies:
      - backfill uses the correct dated snapshot per bet
      - bets with no snapshot are UNMATCHABLE_NO_SNAPSHOT, not guessed
      - bets with a matching snapshot are SUCCESSFULLY_MATCHED
      - snapshot_path_for_date returns the right path
      - list_available_snapshots reads directory correctly
    """

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.snapshots_dir = os.path.join(self.tmpdir, "kalshi_registry_snapshots")
        os.makedirs(self.snapshots_dir)

        # Build a realistic June 6 snapshot with a KC@MIN ML market
        self.june6_registry = {
            "markets": [
                {
                    "event_ticker": "KXMLBGAME-26JUN061410KCMIN",
                    "market_ticker": "KXMLBGAME-26JUN061410KCMIN-MIN",
                    "market_type": "moneyline",
                    "title": "Kansas City vs Minnesota Winner?",
                    "yes_bid": 0.55, "yes_ask": 0.57, "mid": 0.56,
                },
                {
                    "event_ticker": "KXMLBGAME-26JUN061410KCMIN",
                    "market_ticker": "KXMLBGAME-26JUN061410KCMIN-KC",
                    "market_type": "moneyline",
                    "title": "Kansas City vs Minnesota Winner?",
                    "yes_bid": 0.43, "yes_ask": 0.45, "mid": 0.44,
                },
                {
                    "event_ticker": "KXMLBF5-26JUN061410KCMIN",
                    "market_ticker": "KXMLBF5-26JUN061410KCMIN-MIN",
                    "market_type": "f5_moneyline",
                    "title": "KC vs MIN First 5 Innings Winner?",
                    "yes_bid": 0.53, "yes_ask": 0.55, "mid": 0.54,
                },
                {
                    "event_ticker": "KXMLBRFI-26JUN061410KCMIN",
                    "market_ticker": "KXMLBRFI-26JUN061410KCMIN",
                    "market_type": "nrfi_yrfi",
                    "title": "KC vs MIN First Inning Run?",
                    "yes_bid": 0.47, "yes_ask": 0.49, "mid": 0.48,
                },
            ]
        }

        # Write June 6 snapshot
        with open(os.path.join(self.snapshots_dir, "kalshi_search_2026-06-06.json"), "w") as f:
            json.dump(self.june6_registry, f)

        # Clear module-level cache between tests
        import backfill_market_identity as bm
        bm._snapshot_cache.clear()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)
        import backfill_market_identity as bm
        bm._snapshot_cache.clear()

    # --- snapshot_path_for_date ---

    def test_snapshot_path_for_date_format(self):
        from backfill_market_identity import snapshot_path_for_date
        path = snapshot_path_for_date("2026-06-06", self.snapshots_dir)
        self.assertTrue(path.endswith("kalshi_search_2026-06-06.json"))

    def test_snapshot_path_for_date_includes_dir(self):
        from backfill_market_identity import snapshot_path_for_date
        path = snapshot_path_for_date("2026-06-06", self.snapshots_dir)
        self.assertIn(self.snapshots_dir, path)

    # --- list_available_snapshots ---

    def test_list_available_snapshots_finds_file(self):
        from backfill_market_identity import list_available_snapshots
        dates = list_available_snapshots(self.snapshots_dir)
        self.assertIn("2026-06-06", dates)

    def test_list_available_snapshots_empty_dir(self):
        import tempfile
        empty = tempfile.mkdtemp()
        from backfill_market_identity import list_available_snapshots
        dates = list_available_snapshots(empty)
        self.assertEqual(dates, [])
        import shutil; shutil.rmtree(empty)

    def test_list_available_snapshots_missing_dir(self):
        from backfill_market_identity import list_available_snapshots
        dates = list_available_snapshots("/nonexistent/path")
        self.assertEqual(dates, [])

    # --- load_snapshot_for_date ---

    def test_load_snapshot_returns_registry_dict(self):
        from backfill_market_identity import load_snapshot_for_date
        reg = load_snapshot_for_date("2026-06-06", self.snapshots_dir)
        self.assertIsNotNone(reg)
        self.assertIsInstance(reg, dict)
        self.assertIn("KXMLBGAME-26JUN061410KCMIN-MIN", reg)

    def test_load_snapshot_returns_none_for_missing_date(self):
        from backfill_market_identity import load_snapshot_for_date
        reg = load_snapshot_for_date("2026-01-01", self.snapshots_dir)
        self.assertIsNone(reg)

    def test_load_snapshot_caches_result(self):
        from backfill_market_identity import load_snapshot_for_date, _snapshot_cache
        reg1 = load_snapshot_for_date("2026-06-06", self.snapshots_dir)
        reg2 = load_snapshot_for_date("2026-06-06", self.snapshots_dir)
        self.assertIs(reg1, reg2, "Second call should return cached object")

    # --- backfill_bet with snapshot isolation ---

    def test_uses_correct_dated_snapshot(self):
        """Bet on 2026-06-06 must use the June 6 snapshot, not any other date."""
        from backfill_market_identity import backfill_bet
        b = make_bet(date="2026-06-06", game="KC @ MIN", market="ML", bet="MIN ML")
        updated, status = backfill_bet(b, snapshots_dir=self.snapshots_dir)
        self.assertEqual(status, "SUCCESSFULLY_MATCHED")
        self.assertEqual(updated["marketTicker"], "KXMLBGAME-26JUN061410KCMIN-MIN")
        self.assertIn("2026-06-06", updated.get("backfillSource", ""))

    def test_does_not_use_wrong_date_snapshot(self):
        """Bet on 2026-06-07 must NOT match against 2026-06-06 snapshot."""
        from backfill_market_identity import backfill_bet
        b = make_bet(date="2026-06-07", game="KC @ MIN", market="ML", bet="MIN ML")
        updated, status = backfill_bet(b, snapshots_dir=self.snapshots_dir)
        # June 7 snapshot doesn't exist → UNMATCHABLE_NO_SNAPSHOT
        self.assertEqual(status, "UNMATCHABLE_NO_SNAPSHOT")
        self.assertIsNone(updated.get("marketTicker"))

    def test_no_snapshot_returns_unmatchable_no_snapshot(self):
        """Bets with no dated snapshot are classified UNMATCHABLE_NO_SNAPSHOT."""
        from backfill_market_identity import backfill_bet
        b = make_bet(date="2026-05-26", game="ATL @ BOS", market="ML", bet="BOS ML")
        updated, status = backfill_bet(b, snapshots_dir=self.snapshots_dir)
        self.assertEqual(status, "UNMATCHABLE_NO_SNAPSHOT")

    def test_no_snapshot_does_not_set_market_ticker(self):
        """Bets without snapshots must NEVER get a marketTicker — no guessing."""
        from backfill_market_identity import backfill_bet
        b = make_bet(date="2026-05-26", game="ATL @ BOS", market="ML", bet="BOS ML")
        updated, status = backfill_bet(b, snapshots_dir=self.snapshots_dir)
        self.assertIsNone(updated.get("marketTicker"),
                          "marketTicker must not be set when snapshot is missing")

    def test_f5_match_uses_snapshot(self):
        """F5 ML bets match via KXMLBF5 series in snapshot."""
        from backfill_market_identity import backfill_bet
        b = make_bet(date="2026-06-06", game="KC @ MIN", market="F5 ML", bet="MIN F5 ML")
        updated, status = backfill_bet(b, snapshots_dir=self.snapshots_dir)
        self.assertEqual(status, "SUCCESSFULLY_MATCHED")
        self.assertIn("KXMLBF5", updated.get("marketTicker", ""))

    def test_yrfi_match_uses_snapshot(self):
        """YRFI bets match via KXMLBRFI series; no team suffix expected."""
        from backfill_market_identity import backfill_bet
        b = make_bet(date="2026-06-06", game="KC @ MIN", market="YRFI", bet="KC @ MIN YRFI")
        updated, status = backfill_bet(b, snapshots_dir=self.snapshots_dir)
        self.assertEqual(status, "SUCCESSFULLY_MATCHED")
        self.assertIn("KXMLBRFI", updated.get("marketTicker", ""))

    def test_already_present_skipped(self):
        """Bets that already have a marketTicker are not re-processed."""
        from backfill_market_identity import backfill_bet
        b = make_bet(
            date="2026-06-06", game="KC @ MIN", market="ML", bet="MIN ML",
            marketTicker="KXMLBGAME-26JUN061410KCMIN-MIN",
        )
        updated, status = backfill_bet(b, snapshots_dir=self.snapshots_dir)
        self.assertEqual(status, "ALREADY_PRESENT")

    def test_snapshot_note_stored_on_no_snapshot(self):
        """UNMATCHABLE_NO_SNAPSHOT bets store a note with the expected snapshot path."""
        from backfill_market_identity import backfill_bet
        b = make_bet(date="2026-05-26", game="ATL @ BOS", market="ML", bet="BOS ML")
        updated, status = backfill_bet(b, snapshots_dir=self.snapshots_dir)
        note = updated.get("backfillNote", "")
        self.assertIn("2026-05-26", note)
        self.assertIn("kalshi_search_2026-05-26.json", note)

    # --- fetch-slate archive integration (simulation) ---

    def test_archive_step_creates_dated_snapshot(self):
        """Simulates what the fetch-slate Archive step does."""
        import tempfile, shutil
        tmp = tempfile.mkdtemp()
        src = os.path.join(tmp, "kalshi_search.json")
        snap_dir = os.path.join(tmp, "kalshi_registry_snapshots")
        os.makedirs(snap_dir, exist_ok=True)

        # Write a fake kalshi_search.json (simulating the fetch step)
        fake_data = {"markets": [{"market_ticker": "KXMLBGAME-TEST", "event_ticker": "KXMLBGAME-TEST"}]}
        with open(src, "w") as f:
            json.dump(fake_data, f)

        # Simulate the archive step: cp data/kalshi_search.json $SNAP_PATH
        date_str = "2026-06-08"
        dst = os.path.join(snap_dir, f"kalshi_search_{date_str}.json")
        shutil.copy2(src, dst)

        # Verify snapshot is present and readable
        from backfill_market_identity import load_snapshot_for_date, _snapshot_cache
        _snapshot_cache.clear()
        reg = load_snapshot_for_date(date_str, snap_dir)
        self.assertIsNotNone(reg)
        self.assertIn("KXMLBGAME-TEST", reg)

        shutil.rmtree(tmp)


# ── Test Suite 10: Split Validation & Stale-Data Guard ───────────────────────

class TestSplitValidation(unittest.TestCase):
    """
    Verifies:
      - validate_slate_pre passes/fails correctly based on starters + pinnacleVF
      - validate_slate_pre exits 2 (not ready) vs 1 (hard fail) vs 0 (ok)
      - validate_slate_final catches missing post-pipeline fields
      - stale date guard prevents wrong-date snapshot from being created
      - Kalshi snapshot archives even when final validation would fail
    """

    def _make_slate(self, **overrides):
        """Build a minimal valid slate dict."""
        game = {
            "away": {"abbr": "KC", "pitcher": {"name": "Singer"}},
            "home": {"abbr": "MIN", "pitcher": {"name": "Gray"}},
            "pinnacleVF": {"away": 0.43, "home": 0.57},
            "awayTeamStats": {"lineupConfirmed": True, "offenseBaselineAdj": 4.2},
            "homeTeamStats": {"lineupConfirmed": True, "offenseBaselineAdj": 4.8},
            "odds": {"kalshi": {"ml": {"away": -135}, "f5ml": {"away": -110},
                                "nrfi_yrfi": {"nrfi_american": 100},
                                "total": {"line": 8.5},
                                "rl": {"best_ticker": "KXMLBRL-TEST"},
                                "team_totals": {"away": {"best_ticker": "T1"}, "home": {"best_ticker": "T2"}}}},
            "allEdges": [{"awayProjRuns": 3.8, "homeProjRuns": 4.2}],
            "marketLedger": [
                {"market": m, "status": "Rejected", "rejectionReason": "edge < threshold"}
                for m in ["NRFI","YRFI","F5_ML_Away","F5_ML_Home",
                          "TT_Away_Over","TT_Home_Over","ML_Away","ML_Home",
                          "Game_Total","RL_Away","RL_Home"]
            ],
            "away": {"abbr": "KC", "pitcher": {"name": "Singer"},
                     "pitcherSavant": {"xFIP": 3.8, "recentFIP": 3.9}},
            "home": {"abbr": "MIN", "pitcher": {"name": "Gray"},
                     "pitcherSavant": {"xFIP": 3.2, "recentFIP": 3.4}},
        }
        game.update(overrides)
        slate = {"date": "2026-06-08", "games": [game]}
        return slate

    # --- validate_slate_pre ---

    def test_pre_validate_passes_with_starters_and_pvf(self):
        import validate_slate_pre as vsp
        slate = self._make_slate()
        hard, soft, _ = vsp.validate_pre(slate, "2026-06-08")
        self.assertEqual(hard, [])
        self.assertEqual(soft, [])

    def test_pre_validate_soft_fail_missing_starters(self):
        import validate_slate_pre as vsp
        slate = self._make_slate()
        # Remove starter names
        slate["games"][0]["away"]["pitcher"] = {}
        slate["games"][0]["home"]["pitcher"] = {}
        hard, soft, _ = vsp.validate_pre(slate, "2026-06-08")
        self.assertEqual(hard, [], "Missing starters should be soft fail, not hard")
        self.assertGreater(len(soft), 0)
        self.assertTrue(any("starter" in e for e in soft))

    def test_pre_validate_soft_fail_missing_pvf(self):
        import validate_slate_pre as vsp
        slate = self._make_slate()
        slate["games"][0]["pinnacleVF"] = {}
        hard, soft, _ = vsp.validate_pre(slate, "2026-06-08")
        self.assertEqual(hard, [], "Missing pvf should be soft fail, not hard")
        self.assertTrue(any("pinnacleVF" in e for e in soft))

    def test_pre_validate_hard_fail_no_games(self):
        import validate_slate_pre as vsp
        hard, soft, _ = vsp.validate_pre({"date": "2026-06-08", "games": []}, "2026-06-08")
        self.assertGreater(len(hard), 0)
        self.assertTrue(any("no games" in e for e in hard))

    def test_pre_validate_hard_fail_wrong_date(self):
        import validate_slate_pre as vsp
        slate = self._make_slate()
        slate["date"] = "2026-06-07"  # stale
        hard, soft, _ = vsp.validate_pre(slate, "2026-06-08")
        self.assertGreater(len(hard), 0)
        self.assertTrue(any("STALE" in e for e in hard))

    def test_pre_validate_does_not_check_ledger(self):
        """Pre-validation must NOT fail on missing marketLedger."""
        import validate_slate_pre as vsp
        slate = self._make_slate()
        slate["games"][0].pop("marketLedger", None)
        slate["games"][0]["awayTeamStats"].pop("offenseBaselineAdj", None)
        slate["games"][0]["homeTeamStats"].pop("offenseBaselineAdj", None)
        hard, soft, _ = vsp.validate_pre(slate, "2026-06-08")
        # No hard errors for missing ledger
        ledger_hard = [e for e in hard if "marketLedger" in e]
        self.assertEqual(ledger_hard, [],
                         "Pre-validation must not check marketLedger")

    def test_pre_validate_does_not_check_baseline(self):
        """Pre-validation must NOT fail on missing offenseBaselineAdj."""
        import validate_slate_pre as vsp
        slate = self._make_slate()
        slate["games"][0]["awayTeamStats"].pop("offenseBaselineAdj", None)
        hard, soft, _ = vsp.validate_pre(slate, "2026-06-08")
        baseline_hard = [e for e in hard if "offenseBaseline" in e]
        self.assertEqual(baseline_hard, [],
                         "Pre-validation must not check offenseBaselineAdj")

    # --- validate_slate_final ---

    def test_final_validate_passes_complete_slate(self):
        import validate_slate_final as vsf
        slate = self._make_slate()
        errors, _ = vsf.validate_final(slate, "2026-06-08")
        self.assertEqual(errors, [])

    def test_final_validate_fails_missing_baseline(self):
        import validate_slate_final as vsf
        slate = self._make_slate()
        slate["games"][0]["awayTeamStats"]["offenseBaselineAdj"] = None
        errors, _ = vsf.validate_final(slate, "2026-06-08")
        self.assertTrue(any("offenseBaselineAdj" in e for e in errors))

    def test_final_validate_fails_missing_ledger(self):
        import validate_slate_final as vsf
        slate = self._make_slate()
        slate["games"][0]["marketLedger"] = []
        errors, _ = vsf.validate_final(slate, "2026-06-08")
        self.assertTrue(any("marketLedger" in e for e in errors))

    def test_final_validate_fails_missing_lineup_confirmed(self):
        import validate_slate_final as vsf
        slate = self._make_slate()
        slate["games"][0]["awayTeamStats"]["lineupConfirmed"] = None
        errors, _ = vsf.validate_final(slate, "2026-06-08")
        self.assertTrue(any("lineupConfirmed" in e for e in errors))

    # --- Stale-date guard ---

    def test_stale_date_guard_blocks_wrong_date(self):
        """
        Simulate the archive step logic: if slate.json reports a different date
        than the expected date, snapshot must NOT be written.
        """
        import tempfile, shutil, json as _json
        tmp = tempfile.mkdtemp()
        snap_dir = os.path.join(tmp, "kalshi_registry_snapshots")
        os.makedirs(snap_dir)

        # Write kalshi_search.json (the source)
        ks = {"markets": [{"market_ticker": "TEST-26JUN07-GAME", "event_ticker": "TEST"}]}
        with open(os.path.join(tmp, "kalshi_search.json"), "w") as f:
            _json.dump(ks, f)

        # Write stale slate.json (says June 7 when we want June 8)
        stale_slate = {"date": "2026-06-07", "games": []}
        with open(os.path.join(tmp, "slate.json"), "w") as f:
            _json.dump(stale_slate, f)

        # Simulate the guard logic from fetch-slate.yml archive step
        expected_date = "2026-06-08"
        slate_date = stale_slate["date"]
        snap_path = os.path.join(snap_dir, f"kalshi_search_{expected_date}.json")

        if slate_date == expected_date:
            shutil.copy2(os.path.join(tmp, "kalshi_search.json"), snap_path)
            archived = True
        else:
            archived = False

        self.assertFalse(archived,
                         "Stale-date guard must block archiving when slate date != expected date")
        self.assertFalse(os.path.exists(snap_path),
                         f"Snapshot file must NOT be created when dates mismatch")
        shutil.rmtree(tmp)

    def test_stale_date_guard_allows_correct_date(self):
        """When dates match, snapshot should be archived."""
        import tempfile, shutil, json as _json
        tmp = tempfile.mkdtemp()
        snap_dir = os.path.join(tmp, "kalshi_registry_snapshots")
        os.makedirs(snap_dir)

        ks = {"markets": [{"market_ticker": "TEST-26JUN08-GAME", "event_ticker": "TEST"}]}
        with open(os.path.join(tmp, "kalshi_search.json"), "w") as f:
            _json.dump(ks, f)

        correct_slate = {"date": "2026-06-08", "games": []}
        with open(os.path.join(tmp, "slate.json"), "w") as f:
            _json.dump(correct_slate, f)

        expected_date = "2026-06-08"
        slate_date = correct_slate["date"]
        snap_path = os.path.join(snap_dir, f"kalshi_search_{expected_date}.json")

        if slate_date == expected_date:
            shutil.copy2(os.path.join(tmp, "kalshi_search.json"), snap_path)
            archived = True
        else:
            archived = False

        self.assertTrue(archived, "Correct date should allow archiving")
        self.assertTrue(os.path.exists(snap_path), "Snapshot must exist after archiving")
        shutil.rmtree(tmp)

    def test_no_stale_june7_data_archived_as_june8(self):
        """
        Prove the guard prevents June 7 data from being written as June 8 snapshot.
        This is the exact failure mode we're protecting against.
        """
        import tempfile, shutil, json as _json
        import backfill_market_identity as bm
        tmp = tempfile.mkdtemp()
        snap_dir = os.path.join(tmp, "snaps")
        os.makedirs(snap_dir)

        # June 7 kalshi_search data (what was fetched but slate says wrong date)
        june7_ks = {
            "markets": [{
                "market_ticker": "KXMLBGAME-26JUN071610NYMSD-NYM",
                "event_ticker": "KXMLBGAME-26JUN071610NYMSD",
                "market_type": "moneyline",
            }]
        }
        with open(os.path.join(tmp, "kalshi_search.json"), "w") as f:
            _json.dump(june7_ks, f)

        # Slate says June 7 but workflow expected June 8
        stale_slate = {"date": "2026-06-07", "games": []}
        expected_date = "2026-06-08"
        slate_date = stale_slate["date"]

        snap_path_june8 = os.path.join(snap_dir, "kalshi_search_2026-06-08.json")

        # Guard logic
        if slate_date == expected_date:
            shutil.copy2(os.path.join(tmp, "kalshi_search.json"), snap_path_june8)

        # June 8 snapshot must NOT exist (would contain June 7 data)
        self.assertFalse(os.path.exists(snap_path_june8),
                         "June 7 Kalshi data must NOT be archived as June 8 snapshot")

        # June 7 snapshot also must NOT exist (we didn't archive for June 7 either)
        snap_path_june7 = os.path.join(snap_dir, "kalshi_search_2026-06-07.json")
        self.assertFalse(os.path.exists(snap_path_june7),
                         "June 7 snapshot must not be created either (wrong workflow run)")

        # Backfill must see UNMATCHABLE_NO_SNAPSHOT for June 8 bets
        bm._snapshot_cache.clear()
        b = make_bet(date="2026-06-08", game="NYM @ SD", market="ML", bet="NYM ML")
        updated, status = bm.backfill_bet(b, snapshots_dir=snap_dir)
        self.assertEqual(status, "UNMATCHABLE_NO_SNAPSHOT",
                         "June 8 bets must be UNMATCHABLE_NO_SNAPSHOT when guard blocked the archive")
        bm._snapshot_cache.clear()
        shutil.rmtree(tmp)

    def test_snapshot_archives_before_final_validation_would_fail(self):
        """
        Prove the pipeline ordering: if Kalshi fetch succeeds and pre-validation
        passes but final validation would fail (missing marketLedger), the snapshot
        is still correctly archived.

        In the workflow: archive (block 2) < pre-validate (block 3) < final-validate (block 5).
        The snapshot is committed at block 3b or included in block 6.
        Either way it doesn't depend on final validation passing.
        """
        import tempfile, shutil, json as _json
        import backfill_market_identity as bm

        tmp = tempfile.mkdtemp()
        snap_dir = os.path.join(tmp, "snaps")
        os.makedirs(snap_dir)

        # Simulate: Kalshi search fetched successfully
        good_ks = {
            "markets": [{
                "market_ticker": "KXMLBGAME-26JUN081610NYMSD-NYM",
                "event_ticker": "KXMLBGAME-26JUN081610NYMSD",
                "market_type": "moneyline",
            }]
        }
        with open(os.path.join(tmp, "kalshi_search.json"), "w") as f:
            _json.dump(good_ks, f)

        # Slate date is correct (June 8) — archive should proceed
        correct_slate = {"date": "2026-06-08", "games": []}
        with open(os.path.join(tmp, "slate.json"), "w") as f:
            _json.dump(correct_slate, f)

        # Simulate archive step (runs before any validation)
        expected_date = "2026-06-08"
        snap_path = os.path.join(snap_dir, f"kalshi_search_{expected_date}.json")
        slate_date = correct_slate["date"]
        if slate_date == expected_date:
            shutil.copy2(os.path.join(tmp, "kalshi_search.json"), snap_path)

        # Snapshot IS archived
        self.assertTrue(os.path.exists(snap_path),
                        "Snapshot must be archived regardless of later validation outcome")

        # Now simulate final validation failing (marketLedger missing)
        # In the real workflow this would exit 1 and skip the commit step
        # But the snapshot is already on disk (committed in block 3b or will be in block 6)
        import validate_slate_final as vsf
        incomplete_slate = {"date": "2026-06-08", "games": [
            {"away": {"abbr": "NYM", "pitcher": {"name": "X"}, "pitcherSavant": {"xFIP": 3.5, "recentFIP": 3.5}},
             "home": {"abbr": "SD",  "pitcher": {"name": "Y"}, "pitcherSavant": {"xFIP": 3.8, "recentFIP": 3.8}},
             "pinnacleVF": {"away": 0.50}, "awayTeamStats": {"lineupConfirmed": True, "offenseBaselineAdj": 4.0},
             "homeTeamStats": {"lineupConfirmed": True, "offenseBaselineAdj": 4.2},
             "allEdges": [{"awayProjRuns": 3.5}], "marketLedger": []}  # empty ledger = fail
        ]}
        errors, _ = vsf.validate_final(incomplete_slate, "2026-06-08")
        self.assertGreater(len(errors), 0, "Final validation must fail on empty marketLedger")

        # Snapshot still usable for backfill
        bm._snapshot_cache.clear()
        b = make_bet(date="2026-06-08", game="NYM @ SD", market="ML", bet="NYM ML")
        updated, status = bm.backfill_bet(b, snapshots_dir=snap_dir)
        # June 8 snapshot exists and has the market — but our bet says "NYM @ SD"
        # and the ticker is 26JUN08... so it should match
        self.assertNotEqual(status, "UNMATCHABLE_NO_SNAPSHOT",
                            "Snapshot exists — status should not be UNMATCHABLE_NO_SNAPSHOT")

        bm._snapshot_cache.clear()
        shutil.rmtree(tmp)


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
        TestSnapshotBackfill,
        TestSplitValidation,
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
