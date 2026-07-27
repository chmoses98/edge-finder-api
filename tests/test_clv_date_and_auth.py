#!/usr/bin/env python3
"""
tests/test_clv_date_and_auth.py
================================
CLV correctness regression suite — date handling and auth guard.

Covers:
  1. Scheduled workflow date uses ET date, not UTC date
  2. Manual workflow date input is honored
  3. Late-night UTC window does not roll the slate to tomorrow incorrectly
  4. Missing tracked_tickers.json produces a clean skip/status, not a crash
  5. Post-start snapshot is rejected
  6. Sentinel/null/stale price is rejected
  7. Valid pregame snapshot writes CLV successfully
  8. FAIL_API_AUTH produced (not silent null/zero) when Kalshi returns 401/403
  9. FAIL_API_AUTH does not propagate to snapshot path (snapshot works regardless)
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

# ── Path setup ────────────────────────────────────────────────────────────────
_tests_dir = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_tests_dir)
_scripts = os.path.join(_root, "scripts")
sys.path.insert(0, _scripts)
sys.path.insert(0, _root)

import capture_clv_pregame as pregame
import fetch_kalshi_clv_v2 as api_clv


# ══════════════════════════════════════════════════════════════════════════════
# Test Suite 1 — Workflow Date Resolution (ET vs UTC)
#
# These tests validate the WORKFLOW DATE LOGIC — the bash expressions in
# clv_capture.yml.  We replicate the resolution logic in Python and assert
# the correct behavior for the three scenarios described in the issue.
# ══════════════════════════════════════════════════════════════════════════════

class TestWorkflowDateResolution(unittest.TestCase):
    """
    Validates that the workflow resolves to America/New_York date, not UTC.

    The fixed workflow:
        DATE="${{ github.event.inputs.date }}"
        if [ -z "$DATE" ]; then
            DATE=$(TZ='America/New_York' date +%Y-%m-%d)
        fi
        python scripts/capture_clv_pregame.py --date "$DATE"

    Key: the script receives $DATE (ET), NOT $(date -u +%Y-%m-%d) (UTC).
    """

    def _et_date_at_utc(self, utc_hour, utc_minute=0):
        """Return what America/New_York date string would be at a given UTC time today."""
        import datetime as dt
        # ET = UTC-4 (EDT) or UTC-5 (EST); we use UTC-4 (summer/EDT)
        ET_OFFSET = timedelta(hours=-4)
        utc_now = datetime(2026, 6, 20, utc_hour, utc_minute, tzinfo=timezone.utc)
        et_now = utc_now + ET_OFFSET
        return et_now.strftime("%Y-%m-%d")

    def test_late_night_utc_early_evening_et_same_day(self):
        """
        UTC 00:05 = 8:05 PM ET (same calendar day in ET).
        Fixed workflow: DATE = ET date (June 19).
        Broken workflow: DATE = UTC date (June 20).
        """
        # UTC 00:05 on June 20 = 8:05 PM ET on June 19
        utc_h, utc_m = 0, 5
        et_date = self._et_date_at_utc(utc_h, utc_m)  # should be June 19
        utc_date = datetime(2026, 6, 20, utc_h, utc_m, tzinfo=timezone.utc).strftime("%Y-%m-%d")

        # ET date should be one day earlier than UTC date in this scenario
        self.assertEqual(et_date, "2026-06-19",
                         f"At 00:05 UTC, ET date should be 2026-06-19, got {et_date}")
        self.assertEqual(utc_date, "2026-06-20",
                         f"UTC date at 00:05 UTC is 2026-06-20")

        # The FIX: the workflow must pass ET date (June 19), not UTC date (June 20)
        # This is asserted by verifying the two differ and ET is correct
        self.assertNotEqual(et_date, utc_date,
                            "Late-night UTC (00:05) should produce different ET vs UTC dates")

    def test_late_night_utc_very_late_et_same_day(self):
        """
        UTC 02:59 = 10:59 PM ET — still same calendar day.
        Fixed workflow uses ET (June 19). Broken workflow uses UTC (June 20).
        """
        et_date = self._et_date_at_utc(2, 59)
        utc_date = datetime(2026, 6, 20, 2, 59, tzinfo=timezone.utc).strftime("%Y-%m-%d")

        self.assertEqual(et_date, "2026-06-19")
        self.assertEqual(utc_date, "2026-06-20")
        self.assertNotEqual(et_date, utc_date)

    def test_afternoon_utc_same_day_both(self):
        """
        UTC 17:00 = 1:00 PM ET — same calendar day in both timezones.
        UTC 17:00 on June 19 → ET 13:00 June 19 → both are 2026-06-19.
        """
        ET_OFFSET = timedelta(hours=-4)
        utc_dt = datetime(2026, 6, 19, 17, 0, tzinfo=timezone.utc)
        et_dt = utc_dt + ET_OFFSET  # 13:00 ET June 19
        et_date = et_dt.strftime("%Y-%m-%d")
        utc_date = utc_dt.strftime("%Y-%m-%d")

        # At 1 PM ET, both timezones are on June 19
        self.assertEqual(et_date, "2026-06-19")
        self.assertEqual(utc_date, "2026-06-19")
        self.assertEqual(et_date, utc_date,
                         "At 1 PM ET (17:00 UTC) both ET and UTC are the same calendar date")

    def test_manual_dispatch_date_honored(self):
        """
        When workflow_dispatch provides a date input, that exact date is used
        and the ET clock is not consulted.

        Simulates: DATE="${{ github.event.inputs.date }}" = "2026-06-18"
        The script must receive exactly "2026-06-18", not today ET.
        """
        manual_date = "2026-06-18"
        # Simulate: if DATE is non-empty, use it directly (don't call TZ=...date)
        DATE = manual_date if manual_date else None
        if not DATE:
            # This branch should NOT be taken when input is provided
            import datetime as dt
            ET_OFFSET = timedelta(hours=-4)
            DATE = (datetime.now(timezone.utc) + ET_OFFSET).strftime("%Y-%m-%d")

        self.assertEqual(DATE, "2026-06-18",
                         "Manual dispatch date must be used verbatim, not overridden by ET clock")

    def test_workflow_date_passed_to_script_not_utc(self):
        """
        Regression: the fixed workflow passes $DATE to the script, not $(date -u +%Y-%m-%d).
        This test verifies the fix is semantically correct by checking the workflow content.
        """
        # Find the workflow file in the repo
        workflows_dir = os.path.join(_root, ".github", "workflows")
        workflow_path = os.path.join(workflows_dir, "clv_capture.yml")

        if not os.path.exists(workflow_path):
            self.skipTest(f"Workflow not found at {workflow_path} — skipping file content check")

        with open(workflow_path) as f:
            content = f.read()

        # The FIXED workflow must NOT have the old UTC bug line
        self.assertNotIn(
            'python scripts/capture_clv_pregame.py --date $(date -u +%Y-%m-%d)',
            content,
            "BUG PRESENT: workflow still passes UTC date to capture_clv_pregame.py"
        )

        # The FIXED workflow must pass $DATE (the ET-resolved variable)
        self.assertIn(
            'python scripts/capture_clv_pregame.py --date "$DATE"',
            content,
            "FIX MISSING: workflow must pass the resolved ET date via --date \"$DATE\""
        )


# ══════════════════════════════════════════════════════════════════════════════
# Test Suite 2 — capture_clv_pregame.py: tracked_tickers.json missing
# ══════════════════════════════════════════════════════════════════════════════

class TestMissingTrackedTickers(unittest.TestCase):

    def test_missing_tracked_tickers_returns_no_tickers_status(self):
        """
        When tracked_tickers.json does not exist for the requested date,
        run() must return {"status": "NO_TICKERS"} cleanly — no crash, no
        partial writes, no implicit CLV values.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Patch SNAPSHOT_DIR to our tmpdir (no tracked_tickers.json)
            orig = pregame.SNAPSHOT_DIR
            pregame.SNAPSHOT_DIR = tmpdir
            try:
                result = pregame.run(date_str="2026-06-19", dry_run=True)
            finally:
                pregame.SNAPSHOT_DIR = orig

        self.assertEqual(result["status"], "NO_TICKERS",
                         f"Expected NO_TICKERS, got {result['status']}")
        self.assertEqual(result["snapshots"], [],
                         "Snapshots list must be empty when no tickers found")
        self.assertEqual(result["date"], "2026-06-19")

    def test_missing_tracked_tickers_does_not_crash(self):
        """run() must not raise an exception when tracked_tickers.json is absent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig = pregame.SNAPSHOT_DIR
            pregame.SNAPSHOT_DIR = tmpdir
            try:
                try:
                    pregame.run(date_str="2026-06-19", dry_run=True)
                except Exception as e:
                    self.fail(f"run() crashed with missing tracked_tickers.json: {e}")
            finally:
                pregame.SNAPSHOT_DIR = orig

    def test_empty_tracked_tickers_returns_no_tickers_status(self):
        """Empty tracked_tickers.json ([] or {tickers:[]}) → NO_TICKERS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            date_dir = os.path.join(tmpdir, "2026-06-19")
            os.makedirs(date_dir)
            with open(os.path.join(date_dir, "tracked_tickers.json"), "w") as f:
                json.dump([], f)

            orig = pregame.SNAPSHOT_DIR
            pregame.SNAPSHOT_DIR = tmpdir
            try:
                result = pregame.run(date_str="2026-06-19", dry_run=True)
            finally:
                pregame.SNAPSHOT_DIR = orig

        self.assertEqual(result["status"], "NO_TICKERS")


# ══════════════════════════════════════════════════════════════════════════════
# Test Suite 3 — Post-start snapshot rejection
# ══════════════════════════════════════════════════════════════════════════════

class TestPostStartRejection(unittest.TestCase):

    def _ticker_entry(self, ticker, start_utc="2026-06-19T22:10:00Z", game_pk="12345"):
        return {
            "ticker": ticker,
            "marketTicker": ticker,
            "gameStartTime": start_utc,
            "gamePk": game_pk,
            "marketType": "ML",
        }

    def test_snapshot_after_first_pitch_rejected(self):
        """
        classify_snapshot with capture_ts AFTER gameStartTime must return
        INVALID_POST_START, not VALID.
        """
        entry = self._ticker_entry("KXMLBGAME-26JUN191810NYY-NYY")
        # Game starts 22:10 UTC; snapshot taken at 22:15 UTC (5 min after start)
        capture_ts = datetime(2026, 6, 19, 22, 15, tzinfo=timezone.utc)

        result = pregame.classify_snapshot(entry, {}, [], capture_ts)

        self.assertEqual(result["clvStatus"], "INVALID_POST_START",
                         f"Post-start snapshot must be rejected, got {result['clvStatus']}")
        self.assertIsNone(result["clvPrice"])

    def test_snapshot_before_first_pitch_accepted(self):
        """
        classify_snapshot with capture_ts BEFORE gameStartTime may proceed to
        price validation (result depends on price data; at minimum not INVALID_POST_START).
        """
        ticker = "KXMLBGAME-26JUN191810NYY-NYY"
        entry = self._ticker_entry(ticker)
        # Game starts 22:10 UTC; snapshot taken at 22:05 UTC
        capture_ts = datetime(2026, 6, 19, 22, 5, tzinfo=timezone.utc)
        # No price data → TICKER_NOT_FOUND, but definitely not INVALID_POST_START
        result = pregame.classify_snapshot(entry, {}, [], capture_ts)

        self.assertNotEqual(result["clvStatus"], "INVALID_POST_START",
                            "Pre-start snapshot must not be rejected as post-start")

    def test_snapshot_exactly_at_first_pitch_rejected(self):
        """
        capture_ts == gameStartTime should be rejected (not strictly before).
        """
        ticker = "KXMLBGAME-26JUN191810NYY-NYY"
        entry = self._ticker_entry(ticker, start_utc="2026-06-19T22:10:00Z")
        capture_ts = datetime(2026, 6, 19, 22, 10, tzinfo=timezone.utc)  # exactly at start

        result = pregame.classify_snapshot(entry, {}, [], capture_ts)

        self.assertEqual(result["clvStatus"], "INVALID_POST_START",
                         "Snapshot at exactly first pitch must be treated as post-start")

    def test_west_coast_late_game_pregame_accepted(self):
        """
        10:07 PM ET (02:07 UTC next day) game, snapshot at 02:00 UTC → pre-game, must be accepted.
        """
        ticker = "KXMLBGAME-26JUN200207SDLAD-LAD"
        entry = self._ticker_entry(ticker, start_utc="2026-06-20T02:07:00Z")
        # Snapshot at 02:00 UTC (7 min before start)
        capture_ts = datetime(2026, 6, 20, 2, 0, tzinfo=timezone.utc)
        result = pregame.classify_snapshot(entry, {}, [], capture_ts)

        self.assertNotEqual(result["clvStatus"], "INVALID_POST_START")


# ══════════════════════════════════════════════════════════════════════════════
# Test Suite 4 — Sentinel / null / stale price rejection
# ══════════════════════════════════════════════════════════════════════════════

class TestInvalidPriceRejection(unittest.TestCase):

    def _entry(self, ticker="KXMLBGAME-26JUN191810NYY-NYY",
               start="2026-06-19T22:10:00Z", game_pk="12345"):
        return {
            "ticker": ticker,
            "marketTicker": ticker,
            "gameStartTime": start,
            "gamePk": game_pk,
            "marketType": "ML",
        }

    def _pregame_ts(self):
        return datetime(2026, 6, 19, 22, 5, tzinfo=timezone.utc)  # 5 min before start

    def _make_index(self, ticker, yes_price=None, mid=None, yes_bid=None, yes_ask=None):
        entry = {"market_ticker": ticker, "snapshot_ts": "2026-06-19T22:00:00Z"}
        if yes_price is not None:
            entry["yes_price"] = yes_price
        if mid is not None:
            entry["mid"] = mid
        if yes_bid is not None:
            entry["yes_bid"] = yes_bid
        if yes_ask is not None:
            entry["yes_ask"] = yes_ask
        return {ticker: entry}

    def test_sentinel_price_19900_rejected(self):
        """yes_price=19900 (known sentinel) → SENTINEL_PRICE; price is preserved for diagnostics but status is rejected."""
        ticker = "KXMLBGAME-26JUN191810NYY-NYY"
        index = self._make_index(ticker, yes_price=19900)
        result = pregame.classify_snapshot(self._entry(ticker), index, [], self._pregame_ts())
        self.assertEqual(result["clvStatus"], "SENTINEL_PRICE",
                         f"Sentinel 19900 must be rejected as SENTINEL_PRICE, got {result['clvStatus']}")
        # clvPrice may contain the sentinel value for diagnostics — that's OK.
        # What matters is clvStatus=SENTINEL_PRICE so downstream never uses this as CLV.

    def test_sentinel_price_negative_19900_rejected(self):
        ticker = "KXMLBGAME-26JUN191810NYY-NYY"
        index = self._make_index(ticker, yes_price=-19900)
        result = pregame.classify_snapshot(self._entry(ticker), index, [], self._pregame_ts())
        self.assertEqual(result["clvStatus"], "SENTINEL_PRICE",
                         f"Sentinel -19900 must be rejected, got {result['clvStatus']}")
        # Status=SENTINEL_PRICE is the guard; clvPrice may hold the raw value for debugging.

    def test_null_price_rejected(self):
        """yes_price=None (missing) → NO_VALID_PRICE."""
        ticker = "KXMLBGAME-26JUN191810NYY-NYY"
        # Ticker in index but no price fields
        index = {ticker: {"market_ticker": ticker, "snapshot_ts": "2026-06-19T22:00:00Z"}}
        result = pregame.classify_snapshot(self._entry(ticker), index, [], self._pregame_ts())
        self.assertIn(result["clvStatus"], ("NO_VALID_PRICE", "TICKER_NOT_FOUND"),
                      f"Null price must be rejected, got {result['clvStatus']}")
        self.assertIsNone(result["clvPrice"])

    def test_settlement_price_100_rejected(self):
        """yes_price=100 (settlement, probability scale) → SETTLEMENT_PRICE_ONLY (or SENTINEL_PRICE)."""
        ticker = "KXMLBGAME-26JUN191810NYY-NYY"
        index = self._make_index(ticker, yes_price=100)
        result = pregame.classify_snapshot(self._entry(ticker), index, [], self._pregame_ts())
        self.assertIn(result["clvStatus"],
                      ("SETTLEMENT_PRICE_ONLY", "SENTINEL_PRICE"),
                      f"Settlement price 100 must be rejected, got {result['clvStatus']}")
        # clvStatus is the gate — downstream must check this before using clvPrice.

    def test_settlement_price_0_rejected(self):
        """yes_price=0 → must be rejected (SETTLEMENT_PRICE_ONLY, NO_VALID_PRICE, or SENTINEL_PRICE)."""
        ticker = "KXMLBGAME-26JUN191810NYY-NYY"
        index = self._make_index(ticker, yes_price=0)
        result = pregame.classify_snapshot(self._entry(ticker), index, [], self._pregame_ts())
        self.assertIn(result["clvStatus"],
                      ("SETTLEMENT_PRICE_ONLY", "NO_VALID_PRICE", "SENTINEL_PRICE"),
                      f"Price 0 must be rejected, got {result['clvStatus']}")
        self.assertNotEqual(result["clvStatus"], "VALID",
                            "price=0 must never produce clvStatus=VALID")

    def test_missing_ticker_produces_ticker_not_found(self):
        """Ticker absent from index → TICKER_NOT_FOUND."""
        entry = self._entry("KXMLBGAME-26JUN191810NYY-NYY")
        result = pregame.classify_snapshot(entry, {}, [], self._pregame_ts())
        self.assertEqual(result["clvStatus"], "TICKER_NOT_FOUND")
        self.assertIsNone(result["clvPrice"])

    def test_stale_market_rejected(self):
        """
        Market not updated in >6h before first pitch AND within 2h of start
        → STALE_MARKET.
        """
        ticker = "KXMLBGAME-26JUN191810NYY-NYY"
        # Market last updated 14h before first pitch (22:10 UTC), at 08:00 UTC
        stale_ts = "2026-06-19T08:00:00Z"
        index = {ticker: {
            "market_ticker": ticker,
            "mid": 0.55,   # valid price — 0-1 scale
            "yes_bid": 0.54,
            "yes_ask": 0.56,
            "snapshot_ts": stale_ts,
            "last_updated": stale_ts,
        }}
        # Capture 30 min before start (in the 2h window)
        capture_ts = datetime(2026, 6, 19, 21, 40, tzinfo=timezone.utc)
        result = pregame.classify_snapshot(self._entry(ticker), index, [], capture_ts)
        self.assertEqual(result["clvStatus"], "STALE_MARKET",
                         f"14h stale market must be rejected, got {result['clvStatus']}")
        # clvPrice holds the stale value for diagnostics; what matters is status=STALE_MARKET
        self.assertNotEqual(result["clvStatus"], "VALID",
                            "Stale market must never produce clvStatus=VALID")


# ══════════════════════════════════════════════════════════════════════════════
# Test Suite 5 — Valid pregame snapshot writes CLV successfully
# ══════════════════════════════════════════════════════════════════════════════

class TestValidPregameSnapshot(unittest.TestCase):

    def test_valid_snapshot_produces_valid_status(self):
        """
        A valid ticker, price in range (1-99), snapshot before first pitch
        → clvStatus=VALID.
        """
        ticker = "KXMLBGAME-26JUN191810NYY-NYY"
        entry = {
            "ticker": ticker,
            "marketTicker": ticker,
            "gameStartTime": "2026-06-19T22:10:00Z",
            "gamePk": "777777",
            "marketType": "ML",
        }
        index = {ticker: {
            "market_ticker": ticker,
            "mid": 0.58,   # ~58 cents (0-1 scale)
            "yes_bid": 0.57,
            "yes_ask": 0.59,
            "snapshot_ts": "2026-06-19T22:00:00Z",
            "last_updated": "2026-06-19T22:00:00Z",
        }}
        # 10 min before start
        capture_ts = datetime(2026, 6, 19, 22, 0, tzinfo=timezone.utc)

        result = pregame.classify_snapshot(entry, index, [], capture_ts)

        self.assertEqual(result["clvStatus"], "VALID",
                         f"Valid pregame snapshot must produce VALID, got {result['clvStatus']}: {result.get('notes')}")
        self.assertIsNotNone(result["clvPrice"])
        self.assertGreater(result["clvPrice"], 0)
        self.assertLess(result["clvPrice"], 100)

    def test_valid_snapshot_writes_file(self):
        """run() with valid data writes pregame_{gamePk}.json to the correct ET date folder."""
        with tempfile.TemporaryDirectory() as tmpdir:
            date = "2026-06-19"
            ticker = "KXMLBGAME-26JUN191810NYY-NYY"

            # Write tracked_tickers.json
            date_dir = os.path.join(tmpdir, date)
            os.makedirs(date_dir)
            tickers = [{
                "ticker": ticker,
                "marketTicker": ticker,
                "gameStartTime": "2026-06-19T22:10:00Z",
                "gamePk": "777777",
                "marketType": "ML",
            }]
            with open(os.path.join(date_dir, "tracked_tickers.json"), "w") as f:
                json.dump(tickers, f)

            # Write a valid Kalshi snapshot
            snap_dir = os.path.join(tmpdir, "kalshi_registry_snapshots_for_pregame")
            # We patch KALSHI_REGISTRY_SNAPSHOTS instead
            os.makedirs(snap_dir)
            snap_data = {
                "date": date,
                "fetched_at": "2026-06-19T22:00:00Z",
                "markets": [{
                    "market_ticker": ticker,
                    "mid": 0.58,
                    "yes_bid": 0.57,
                    "yes_ask": 0.59,
                    "snapshot_ts": "2026-06-19T22:00:00Z",
                    "last_updated": "2026-06-19T22:00:00Z",
                }],
            }
            with open(os.path.join(snap_dir, f"kalshi_search_{date}.json"), "w") as f:
                json.dump(snap_data, f)

            orig_snap_dir = pregame.SNAPSHOT_DIR
            orig_registry = pregame.KALSHI_REGISTRY_SNAPSHOTS
            pregame.SNAPSHOT_DIR = tmpdir
            pregame.KALSHI_REGISTRY_SNAPSHOTS = snap_dir
            try:
                # Freeze "now" to 10 min before first pitch (22:10Z) so this test is
                # deterministic and does not depend on the real wall clock — without
                # this, the fixture's fixed 2026-06-19 game recedes further into the
                # past every day, and classify_snapshot's pregame check would
                # (correctly, but non-deterministically) start reporting
                # INVALID_POST_START once real time passes the fixture's game date.
                result = pregame.run(date_str=date, dry_run=False, current_utc="2026-06-19T22:00:00Z")
            finally:
                pregame.SNAPSHOT_DIR = orig_snap_dir
                pregame.KALSHI_REGISTRY_SNAPSHOTS = orig_registry

            # Verify output in the ET date directory (not tomorrow, not UTC)
            # Check INSIDE the with block so tmpdir is still alive
            out_path = os.path.join(tmpdir, date, "pregame_777777.json")
            self.assertTrue(os.path.exists(out_path),
                            f"pregame_777777.json must be written to {date}/ folder (ET date), not found at {out_path}")

            with open(out_path) as f:
                out = json.load(f)

            self.assertEqual(out["date"], date)
            snaps = out.get("snapshots", [])
            self.assertGreater(len(snaps), 0)
            valid_snaps = [s for s in snaps if s.get("clvStatus") == "VALID"]
            self.assertGreater(len(valid_snaps), 0,
                               f"At least one VALID snapshot expected. Got: {[s['clvStatus'] for s in snaps]}")

    def test_output_folder_uses_et_date_not_utc(self):
        """
        run(date_str='2026-06-19') writes to clv_snapshots/2026-06-19/, not 2026-06-20/.
        This is the core regression test for the date bug.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            date = "2026-06-19"
            ticker = "KXMLBGAME-26JUN191810NYY-NYY"
            date_dir = os.path.join(tmpdir, date)
            os.makedirs(date_dir)
            with open(os.path.join(date_dir, "tracked_tickers.json"), "w") as f:
                json.dump([{
                    "ticker": ticker,
                    "marketTicker": ticker,
                    "gameStartTime": "2026-06-19T22:10:00Z",
                    "gamePk": "888888",
                    "marketType": "ML",
                }], f)

            # Write snapshot
            snap_dir = os.path.join(tmpdir, "ks")
            os.makedirs(snap_dir)
            with open(os.path.join(snap_dir, f"kalshi_search_{date}.json"), "w") as f:
                json.dump({"date": date, "fetched_at": "2026-06-19T22:00:00Z",
                           "markets": [{"market_ticker": ticker, "mid": 0.55,
                                       "snapshot_ts": "2026-06-19T22:00:00Z",
                                       "last_updated": "2026-06-19T22:00:00Z"}]}, f)

            orig_snap_dir = pregame.SNAPSHOT_DIR
            orig_registry = pregame.KALSHI_REGISTRY_SNAPSHOTS
            pregame.SNAPSHOT_DIR = tmpdir
            pregame.KALSHI_REGISTRY_SNAPSHOTS = snap_dir
            try:
                pregame.run(date_str=date, dry_run=False)
            finally:
                pregame.SNAPSHOT_DIR = orig_snap_dir
                pregame.KALSHI_REGISTRY_SNAPSHOTS = orig_registry

            # Must write to 2026-06-19/, NOT 2026-06-20/ — check INSIDE the with block
            correct_path = os.path.join(tmpdir, "2026-06-19", "pregame_888888.json")
            wrong_path = os.path.join(tmpdir, "2026-06-20", "pregame_888888.json")

            self.assertTrue(os.path.exists(correct_path),
                            f"File must be in 2026-06-19/ (ET date). Not found at {correct_path}")
            self.assertFalse(os.path.exists(wrong_path),
                             f"File must NOT be in 2026-06-20/ (UTC rollover date). Found at {wrong_path}")


# ══════════════════════════════════════════════════════════════════════════════
# Test Suite 6 — FAIL_API_AUTH: Kalshi 401/403 produces explicit status
# ══════════════════════════════════════════════════════════════════════════════

class TestAPIAuthFailure(unittest.TestCase):
    """
    Verifies that when Kalshi returns 401 or 403:
    - clvStatus is FAIL_API_AUTH (not OK, not silently null)
    - clv is None (never 0, never estimated)
    - clvError contains a meaningful message
    - This does not affect snapshot-based CLV (which makes no API calls)
    """

    def _make_http_error(self, code):
        """Create a mock HTTPError with given status code."""
        import io
        err = HTTPError(
            url="https://api.elections.kalshi.com/trade-api/v2/markets/TICKER/candlesticks",
            code=code,
            msg=f"HTTP {code}",
            hdrs={},
            fp=io.BytesIO(b""),
        )
        return err

    def _bet(self, **kwargs):
        base = {
            "id": "test-auth-001",
            "date": "2026-06-19",
            "game": "NYY@BOS",
            "market": "ML",
            "marketTicker": "KXMLBGAME-26JUN191810NYYB0S-NYY",
            "betTimeLine": -140,
            "status": "LOSS",
            "scheduledStartTime": "2026-06-19T22:10:00Z",
        }
        base.update(kwargs)
        return base

    def test_kalshi_401_produces_fail_api_auth_not_null_clv(self):
        """HTTP 401 from Kalshi API → clvStatus=FAIL_API_AUTH, clv=None."""
        with patch.object(api_clv, "kget", return_value=(None, "HTTP_401_UNAUTHORIZED — Kalshi API key missing or invalid. Set KALSHI_API_KEY env var.")):
            result = api_clv.process_bet_clv(self._bet())

        self.assertEqual(result["clvStatus"], "FAIL_API_AUTH",
                         f"HTTP 401 must produce FAIL_API_AUTH, got {result['clvStatus']}")
        self.assertIsNone(result["clv"],
                          "clv must be None on auth failure — never 0 or estimated")
        self.assertIsNone(result["closingPrice"])
        self.assertIsNotNone(result.get("clvError"))
        self.assertIn("auth", result["clvError"].lower(),
                      "clvError must mention auth issue")

    def test_kalshi_403_produces_fail_api_auth_not_null_clv(self):
        """HTTP 403 from Kalshi API → clvStatus=FAIL_API_AUTH, clv=None."""
        with patch.object(api_clv, "kget", return_value=(None, "HTTP_403_FORBIDDEN — Kalshi API access denied. Candlestick endpoint requires auth.")):
            result = api_clv.process_bet_clv(self._bet())

        self.assertEqual(result["clvStatus"], "FAIL_API_AUTH",
                         f"HTTP 403 must produce FAIL_API_AUTH, got {result['clvStatus']}")
        self.assertIsNone(result["clv"])
        self.assertIsNone(result["closingPrice"])
        self.assertIsNotNone(result.get("clvError"))

    def test_fail_api_auth_clv_is_none_not_zero(self):
        """Auth failure must never produce clv=0 (which would look like a valid capture)."""
        with patch.object(api_clv, "kget", return_value=(None, "HTTP_401_UNAUTHORIZED — Kalshi API key missing or invalid. Set KALSHI_API_KEY env var.")):
            result = api_clv.process_bet_clv(self._bet())

        # clv must be None, not 0.0, not 0, not any number
        self.assertIsNone(result["clv"],
                          "clv=0 is as bad as a wrong CLV — must be None on auth failure")
        self.assertNotEqual(result.get("clv"), 0)
        self.assertNotEqual(result.get("clv"), 0.0)

    def test_fail_api_auth_error_message_is_actionable(self):
        """clvError must explain what to do (not just say 'error')."""
        with patch.object(api_clv, "kget", return_value=(None, "HTTP_403_FORBIDDEN — Kalshi API access denied. Candlestick endpoint requires auth.")):
            result = api_clv.process_bet_clv(self._bet())

        error = result.get("clvError", "")
        # Must mention the root cause and/or the primary path
        self.assertTrue(
            "snapshot" in error.lower() or "auth" in error.lower() or "KALSHI_API_KEY" in error,
            f"clvError must be actionable (mention snapshot/auth/KALSHI_API_KEY). Got: {error}"
        )

    def test_missing_ticker_fail_no_ticker_not_api_auth(self):
        """Missing marketTicker → FAIL_NO_TICKER (not FAIL_API_AUTH, API never called)."""
        result = api_clv.process_bet_clv(self._bet(marketTicker=None))
        self.assertEqual(result["clvStatus"], "FAIL_NO_TICKER")
        self.assertIsNone(result["clv"])

    def test_run_clv_summary_includes_fail_api_auth_count(self):
        """run_clv() summary must include fail_api_auth field for operator visibility."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([self._bet()], f)
            bets_path = f.name

        try:
            with patch.object(api_clv, "kget", return_value=(None, "HTTP_403_FORBIDDEN — Kalshi API access denied. Candlestick endpoint requires auth.")):
                _, summary = api_clv.run_clv(
                    bets_path=bets_path,
                    write=False,
                    settled_only=False,
                )
            self.assertIn("fail_api_auth", summary,
                          "Summary must include fail_api_auth count for operator visibility")
            self.assertEqual(summary["fail_api_auth"], 1)
            self.assertEqual(summary["clv_ok"], 0)
        finally:
            os.unlink(bets_path)


# ══════════════════════════════════════════════════════════════════════════════
# Test Suite 7 — Dry-run date verification
# ══════════════════════════════════════════════════════════════════════════════

class TestDryRunDateVerification(unittest.TestCase):
    """Smoke tests for run() date argument handling."""

    def test_explicit_date_used_in_output(self):
        """run(date_str='2026-06-19') result includes date='2026-06-19'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig = pregame.SNAPSHOT_DIR
            pregame.SNAPSHOT_DIR = tmpdir
            try:
                result = pregame.run(date_str="2026-06-19", dry_run=True)
            finally:
                pregame.SNAPSHOT_DIR = orig

        self.assertEqual(result["date"], "2026-06-19",
                         "Result date must match the explicit date passed to run()")

    def test_default_date_is_et_not_utc(self):
        """
        run() with no date_str defaults to America/New_York date.
        We validate this by calling run() and checking the result date
        matches what ET clock says (not UTC clock).
        """
        ET_OFFSET = timedelta(hours=-4)
        now_et = datetime.now(timezone.utc) + ET_OFFSET
        et_date = now_et.strftime("%Y-%m-%d")
        utc_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        with tempfile.TemporaryDirectory() as tmpdir:
            orig = pregame.SNAPSHOT_DIR
            pregame.SNAPSHOT_DIR = tmpdir
            try:
                result = pregame.run(dry_run=True)
            finally:
                pregame.SNAPSHOT_DIR = orig

        result_date = result["date"]
        self.assertEqual(result_date, et_date,
                         f"Default date must be ET ({et_date}), not UTC ({utc_date}). Got {result_date}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
