#!/usr/bin/env python3
"""
tests/test_stale_date_guard.py
================================
Tests covering all 27 stale-date guard scenarios.

Scenarios:
 1. stale meta.json date aborts
 2. stale slate.json date aborts
 3. stale pitchers.json date aborts
 4. game startTime previous-day ET aborts
 5. missing requested date argument aborts
 6. invalid requested date argument aborts
 7. missing meta date field aborts
 8. valid same-date slate passes
 9. failed fetch_status.json blocks model run
10. stale fetch cannot fall back to yesterday's slate
11. model output cannot be generated from stale slate (DATA_QUALITY_GATE)
12. real-money bets cannot be written from stale slate
13. paper bets cannot be written from stale slate (stale = no paper either)
14. BET_LOG.md cannot be updated with stale-date bets
15. CLV cannot be calculated against mismatched slate date
16. Kalshi ticker date/matchup mismatch blocks bet creation
17. active but wrong-date Kalshi markets are rejected
18. post-fetch gate fails when requested date and slate date differ
19. workflow command passes requested date into post-fetch gate
20. DATA_QUALITY_GATE blocks real-money when ticker validation fails
21. DATA_QUALITY_GATE blocks real-money when pitcher mismatch exists
22. DATA_QUALITY_GATE blocks real-money when required data missing
23. DATA_QUALITY_GATE allows real-money when all required data is valid
24. paper tracking still works separately after guard changes
25. real-money tracking still excludes paper after guard changes
26. snapshot CLV still works for valid same-date slate
27. snapshot CLV rejects wrong-date snapshots
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

# Add scripts dir to path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
sys.path.insert(0, SCRIPTS_DIR)
sys.path.insert(0, REPO_ROOT)

from stale_date_guard import (
    check_date_or_abort,
    validate_kalshi_ticker_date,
)
from data_quality_gate import (
    classify_bet,
    classify_all_bets,
    check_ticker_date,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

REQ_DATE = "2026-06-13"
PREV_DATE = "2026-06-12"


def _make_meta(date=REQ_DATE, fetched_at=None):
    m = {"date": date, "status": "ok", "oddsSource": "the-odds-api-via-vercel"}
    if fetched_at:
        m["fetchedAt"] = fetched_at
    else:
        m["fetchedAt"] = f"{date}T18:00:00Z"
    return m


def _make_slate(date=REQ_DATE, games=None):
    if games is None:
        games = [_make_game()]
    return {"date": date, "games": games}


def _make_game(away="NYY", home="BOS", start_date=None):
    start = start_date or REQ_DATE
    return {
        "gameId": 12345,
        "status": "Pre-Game",
        "startTime": f"{start}T19:05:00Z",
        "away": {"team": "New York Yankees", "abbr": away, "pitcher": {"name": "Gerrit Cole"}},
        "home": {"team": "Boston Red Sox", "abbr": home, "pitcher": {"name": "Brayan Bello"}},
    }


def _make_pitchers(date=REQ_DATE):
    return {"date": date, "games": []}


def _make_fetch_status(status="OK", requested=REQ_DATE, actual=REQ_DATE):
    return {
        "status": status,
        "requestedDate": requested,
        "actualDate": actual,
        "fetchedAt": f"{requested}T18:00:00Z",
        "source": "fetch-slate/post_fetch_gate"
    }


class TempDataDir:
    """Context manager: writes data files to a temp dir and patches ROOT_DIR."""
    def __init__(self, meta=None, slate=None, pitchers=None, fetch_status=None):
        self.meta = meta
        self.slate = slate
        self.pitchers = pitchers
        self.fetch_status = fetch_status
        self.tmpdir = None
        self.patcher = None

    def __enter__(self):
        self.tmpdir = tempfile.mkdtemp()
        data_dir = os.path.join(self.tmpdir, "data")
        os.makedirs(data_dir)

        if self.meta is not None:
            with open(os.path.join(data_dir, "meta.json"), "w") as f:
                json.dump(self.meta, f)
        if self.slate is not None:
            with open(os.path.join(data_dir, "slate.json"), "w") as f:
                json.dump(self.slate, f)
        if self.pitchers is not None:
            with open(os.path.join(data_dir, "pitchers.json"), "w") as f:
                json.dump(self.pitchers, f)
        if self.fetch_status is not None:
            with open(os.path.join(data_dir, "fetch_status.json"), "w") as f:
                json.dump(self.fetch_status, f)

        import stale_date_guard as sdg
        self.patcher = patch.object(sdg, "ROOT_DIR", self.tmpdir)
        self.patcher.start()
        return self.tmpdir

    def __exit__(self, *args):
        if self.patcher:
            self.patcher.stop()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestStaleDateGuard(unittest.TestCase):

    # Scenario 1: stale meta.json date aborts
    def test_01_stale_meta_date_aborts(self):
        with TempDataDir(
            meta=_make_meta(date=PREV_DATE, fetched_at=f"{PREV_DATE}T18:00:00Z"),
            slate=_make_slate(date=REQ_DATE),
            fetch_status=_make_fetch_status(status="OK", requested=REQ_DATE, actual=REQ_DATE),
        ):
            with self.assertRaises(SystemExit) as cm:
                check_date_or_abort(REQ_DATE)
            self.assertNotEqual(cm.exception.code, 0,
                "Stale meta.json date must cause nonzero exit")

    # Scenario 2: stale slate.json date aborts
    def test_02_stale_slate_date_aborts(self):
        with TempDataDir(
            meta=_make_meta(date=REQ_DATE),
            slate=_make_slate(date=PREV_DATE),
            fetch_status=_make_fetch_status(status="OK"),
        ):
            with self.assertRaises(SystemExit) as cm:
                check_date_or_abort(REQ_DATE)
            self.assertNotEqual(cm.exception.code, 0,
                "Stale slate.json date must cause nonzero exit")

    # Scenario 3: stale pitchers.json date aborts
    def test_03_stale_pitchers_date_aborts(self):
        with TempDataDir(
            meta=_make_meta(date=REQ_DATE),
            slate=_make_slate(date=REQ_DATE),
            pitchers=_make_pitchers(date=PREV_DATE),
            fetch_status=_make_fetch_status(status="OK"),
        ):
            with self.assertRaises(SystemExit) as cm:
                check_date_or_abort(REQ_DATE)
            self.assertNotEqual(cm.exception.code, 0,
                "Stale pitchers.json date must cause nonzero exit")

    # Scenario 4: game startTime previous-day ET aborts
    def test_04_game_starttime_prev_day_et_aborts(self):
        # June 12 game at 22:40 UTC = 18:40 ET on June 12 (previous day)
        prev_day_game = _make_game(start_date=PREV_DATE)
        prev_day_game["startTime"] = f"{PREV_DATE}T22:40:00Z"  # June 12 18:40 ET
        with TempDataDir(
            meta=_make_meta(date=REQ_DATE),
            slate={"date": REQ_DATE, "games": [prev_day_game]},
            fetch_status=_make_fetch_status(status="OK"),
        ):
            with self.assertRaises(SystemExit) as cm:
                check_date_or_abort(REQ_DATE)
            self.assertNotEqual(cm.exception.code, 0,
                "Previous-day startTime in ET must cause nonzero exit")

    # Scenario 5: missing requested date argument aborts
    def test_05_missing_date_argument_aborts(self):
        with TempDataDir(
            meta=_make_meta(date=REQ_DATE),
            slate=_make_slate(date=REQ_DATE),
        ):
            with self.assertRaises(SystemExit) as cm:
                check_date_or_abort(None)
            self.assertNotEqual(cm.exception.code, 0,
                "Missing date argument must cause nonzero exit")

    # Scenario 6: invalid requested date argument aborts
    def test_06_invalid_date_argument_aborts(self):
        with TempDataDir(
            meta=_make_meta(date=REQ_DATE),
            slate=_make_slate(date=REQ_DATE),
        ):
            with self.assertRaises(SystemExit) as cm:
                check_date_or_abort("not-a-date")
            self.assertNotEqual(cm.exception.code, 0,
                "Invalid date argument must cause nonzero exit")

    # Scenario 7: missing meta date field aborts
    def test_07_missing_meta_date_field_aborts(self):
        meta_no_date = {"status": "ok"}  # no 'date' key
        with TempDataDir(
            meta=meta_no_date,
            slate=_make_slate(date=REQ_DATE),
            fetch_status=_make_fetch_status(status="OK"),
        ):
            with self.assertRaises(SystemExit) as cm:
                check_date_or_abort(REQ_DATE)
            self.assertNotEqual(cm.exception.code, 0,
                "Missing meta date field must cause nonzero exit")

    # Scenario 8: valid same-date slate passes
    def test_08_valid_same_date_passes(self):
        # Game at 23:05 UTC on req date = 19:05 ET on req date
        with TempDataDir(
            meta=_make_meta(date=REQ_DATE),
            slate=_make_slate(date=REQ_DATE),
            pitchers=_make_pitchers(date=REQ_DATE),
            fetch_status=_make_fetch_status(status="OK"),
        ):
            result = check_date_or_abort(REQ_DATE)
            self.assertTrue(result, "Valid same-date slate must return True")

    # Scenario 9: failed fetch_status.json blocks model run
    def test_09_failed_fetch_status_blocks(self):
        with TempDataDir(
            meta=_make_meta(date=REQ_DATE),
            slate=_make_slate(date=REQ_DATE),
            fetch_status=_make_fetch_status(
                status="FAILED_STALE_DATE",
                requested=REQ_DATE,
                actual=PREV_DATE
            ),
        ):
            with self.assertRaises(SystemExit) as cm:
                check_date_or_abort(REQ_DATE)
            self.assertNotEqual(cm.exception.code, 0,
                "FAILED_STALE_DATE fetch_status must block model run")

    # Scenario 10: stale fetch cannot fall back to yesterday's slate
    def test_10_no_fallback_to_previous_slate(self):
        # Even if meta says today but slate says yesterday — must fail
        with TempDataDir(
            meta=_make_meta(date=REQ_DATE),
            slate=_make_slate(date=PREV_DATE),  # yesterday's slate
            fetch_status=_make_fetch_status(status="OK"),
        ):
            with self.assertRaises(SystemExit) as cm:
                check_date_or_abort(REQ_DATE)
            self.assertNotEqual(cm.exception.code, 0,
                "Cannot fall back to yesterday's slate")

    # Scenario 11: model output cannot be generated from stale slate (DATA_QUALITY_GATE)
    def test_11_model_output_blocked_by_stale_slate(self):
        c = {
            "market": "ML", "betSide": "AWAY",
            "awayAbbr": "NYY", "homeAbbr": "BOS",
            "ticker": "KXMLBGAME-26JUN131905NYYBOS-NYY",
            "price": -120,
            "sourceTimestamp": f"{REQ_DATE}T18:00:00Z",
            "lineupConfirmed": True,
        }
        result = classify_bet(c, REQ_DATE, slate_date=PREV_DATE)
        self.assertEqual(result["dataQualityStatus"], "REJECT_STALE_DATE")
        self.assertFalse(result["realMoneyEligible"])
        self.assertFalse(result["paperEligible"],
            "Stale slate must block model output including paper")

    # Scenario 12: real-money bets cannot be written from stale slate
    def test_12_real_money_blocked_by_stale_slate(self):
        result = classify_bet(
            {"market": "ML", "price": -120},
            REQ_DATE,
            slate_date=PREV_DATE
        )
        self.assertFalse(result["realMoneyEligible"])
        self.assertEqual(result["dataQualityStatus"], "REJECT_STALE_DATE")

    # Scenario 13: paper bets also blocked by stale slate
    def test_13_paper_bets_blocked_by_stale_slate(self):
        result = classify_bet(
            {"market": "ML", "price": -120},
            REQ_DATE,
            slate_date=PREV_DATE
        )
        self.assertFalse(result["paperEligible"],
            "Even paper bets must be blocked when slate is stale")

    # Scenario 14: BET_LOG.md cannot be updated with stale-date bets
    # (guard test: if stale date, all bets REJECT_STALE_DATE)
    def test_14_bet_log_not_updated_with_stale_bets(self):
        candidates = [
            {"market": "ML", "price": -120, "ticker": "KXMLBGAME-26JUN121905NYYBOS-NYY"},
            {"market": "F5 ML", "price": 105, "ticker": "KXMLBF5-26JUN121905NYYBOS-NYY"},
        ]
        results = classify_all_bets(candidates, REQ_DATE, slate_date=PREV_DATE)
        for r in results:
            self.assertEqual(r["dataQualityStatus"], "REJECT_STALE_DATE",
                "All bets from stale slate must be REJECT_STALE_DATE")
            self.assertFalse(r["paperEligible"])

    # Scenario 15: CLV cannot be calculated against mismatched slate date
    def test_15_clv_blocked_by_mismatched_slate(self):
        # CLV scripts call check_date_or_abort; simulate the same gate
        result = classify_bet(
            {"market": "ML", "price": 110, "ticker": "KXMLBGAME-26JUN121905NYYBOS-NYY"},
            REQ_DATE,
            slate_date=PREV_DATE
        )
        self.assertEqual(result["dataQualityStatus"], "REJECT_STALE_DATE")

    # Scenario 16: Kalshi ticker date/matchup mismatch blocks bet creation
    def test_16_ticker_date_mismatch_blocks_bet(self):
        result = classify_bet(
            {
                "market": "ML", "betSide": "AWAY",
                "awayAbbr": "NYY", "homeAbbr": "BOS",
                "ticker": "KXMLBGAME-26JUN121905NYYBOS-NYY",  # June 12 ticker
                "price": -120,
                "sourceTimestamp": f"{REQ_DATE}T18:00:00Z",
            },
            REQ_DATE,
            slate_date=REQ_DATE
        )
        self.assertEqual(result["dataQualityStatus"], "REJECT_TICKER_MISMATCH",
            "June 12 ticker used for June 13 must be REJECT_TICKER_MISMATCH")
        self.assertFalse(result["realMoneyEligible"])

    # Scenario 17: active but wrong-date Kalshi markets are rejected
    def test_17_wrong_date_kalshi_market_rejected(self):
        wrong_date_ticker = "KXMLBGAME-26JUN121905NYYBOS-NYY"
        self.assertFalse(
            check_ticker_date(wrong_date_ticker, REQ_DATE),
            "Wrong-date Kalshi ticker must not match requested date"
        )
        self.assertTrue(
            check_ticker_date("KXMLBGAME-26JUN131905NYYBOS-NYY", REQ_DATE),
            "Correct-date Kalshi ticker must match requested date"
        )

    # Scenario 18: post-fetch gate fails when requested date and slate date differ
    def test_18_post_fetch_gate_rejects_date_mismatch(self):
        # test the validate_pre (same pattern used in post_fetch_gate logic)
        from validate_slate_pre import validate_pre
        slate = _make_slate(date=PREV_DATE)
        hard, soft, warns = validate_pre(slate, REQ_DATE)
        self.assertTrue(len(hard) > 0, "Hard error expected for date mismatch")
        self.assertTrue(any("STALE" in e.upper() for e in hard),
            "Error must mention STALE for date mismatch")

    # Scenario 19: workflow command passes requested date into post-fetch gate
    # (integration-level test — verifies the argument pattern in the workflow)
    def test_19_workflow_passes_date_to_post_fetch_gate(self):
        wf_path = os.path.join(REPO_ROOT, ".github", "workflows", "fetch-slate.yml")
        with open(wf_path) as f:
            content = f.read()
        self.assertIn(
            'python3 scripts/post_fetch_gate.py "${{ env.DATE }}"',
            content,
            "Workflow must pass $DATE to post_fetch_gate.py"
        )

    # Scenario 20: DATA_QUALITY_GATE blocks real-money when ticker validation fails
    def test_20_dqg_blocks_real_money_on_ticker_fail(self):
        result = classify_bet(
            {
                "market": "ML", "price": -120,
                "awayAbbr": "NYY", "homeAbbr": "BOS",
                "ticker": "KXMLBGAME-26JUN121905NYYBOS-NYY",  # wrong date
                "sourceTimestamp": f"{REQ_DATE}T18:00:00Z",
            },
            REQ_DATE,
            slate_date=REQ_DATE
        )
        self.assertFalse(result["realMoneyEligible"])
        self.assertEqual(result["dataQualityStatus"], "REJECT_TICKER_MISMATCH")

    # Scenario 21: DATA_QUALITY_GATE blocks real-money when pitcher mismatch exists
    def test_21_dqg_blocks_real_money_on_pitcher_mismatch(self):
        result = classify_bet(
            {
                "market": "F5 ML",
                "price": -120,
                "awayAbbr": "NYY", "homeAbbr": "BOS",
                "ticker": "KXMLBF5-26JUN131905NYYBOS-NYY",
                "sourceTimestamp": f"{REQ_DATE}T18:00:00Z",
                "awayPitcher": None,   # Pitcher not confirmed
                "homePitcher": "Brayan Bello",
                "hasPitcherSavant": False,
            },
            REQ_DATE,
            slate_date=REQ_DATE
        )
        self.assertFalse(result["realMoneyEligible"])
        self.assertEqual(result["dataQualityStatus"], "REJECT_PITCHER_MISMATCH")

    # Scenario 22: DATA_QUALITY_GATE blocks real-money when required data missing
    def test_22_dqg_blocks_real_money_on_missing_data(self):
        result = classify_bet(
            {
                "market": "ML",
                # Missing: ticker, price, sourceTimestamp, awayAbbr, homeAbbr
            },
            REQ_DATE,
            slate_date=REQ_DATE
        )
        self.assertFalse(result["realMoneyEligible"])
        self.assertIn(
            result["dataQualityStatus"],
            ("REJECT_DATA_MISSING", "REJECT_ODDS_MISSING"),
        )

    # Scenario 23: DATA_QUALITY_GATE allows real-money when all required data is valid
    def test_23_dqg_allows_real_money_when_all_valid(self):
        result = classify_bet(
            {
                "market": "ML", "betSide": "AWAY",
                "awayAbbr": "NYY", "homeAbbr": "BOS",
                "ticker": "KXMLBGAME-26JUN131905NYYBOS-NYY",
                "price": -120,
                "sourceTimestamp": f"{REQ_DATE}T18:00:00Z",
                "lineupConfirmed": True,
                "awayPitcher": "Gerrit Cole",
                "homePitcher": "Brayan Bello",
                "hasBullpenData": True,
            },
            REQ_DATE,
            slate_date=REQ_DATE
        )
        self.assertEqual(result["dataQualityStatus"], "OK_REAL_ELIGIBLE")
        self.assertTrue(result["realMoneyEligible"])
        self.assertTrue(result["paperEligible"])

    # Scenario 24: paper tracking still works separately after guard changes
    def test_24_paper_tracking_works_when_data_incomplete(self):
        # Lineup not confirmed → paper only
        result = classify_bet(
            {
                "market": "ML", "betSide": "AWAY",
                "awayAbbr": "NYY", "homeAbbr": "BOS",
                "ticker": "KXMLBGAME-26JUN131905NYYBOS-NYY",
                "price": -120,
                "sourceTimestamp": f"{REQ_DATE}T18:00:00Z",
                "lineupConfirmed": False,  # Not confirmed yet
                "awayPitcher": "Gerrit Cole",
                "homePitcher": "Brayan Bello",
            },
            REQ_DATE,
            slate_date=REQ_DATE
        )
        self.assertTrue(result["paperEligible"],
            "Paper tracking must work when data is incomplete but not stale")
        self.assertFalse(result["realMoneyEligible"],
            "Real-money must be blocked when lineup not confirmed")

    # Scenario 25: real-money tracking excludes paper after guard changes
    def test_25_real_money_excludes_paper_bets(self):
        paper_bet = {
            "market": "ML", "betSide": "AWAY",
            "awayAbbr": "NYY", "homeAbbr": "BOS",
            "ticker": "KXMLBGAME-26JUN131905NYYBOS-NYY",
            "price": -120,
            "sourceTimestamp": f"{REQ_DATE}T18:00:00Z",
            "lineupConfirmed": False,  # paper only
        }
        result = classify_bet(paper_bet, REQ_DATE, slate_date=REQ_DATE)
        self.assertFalse(result["realMoneyEligible"],
            "Paper-only bet must not be real-money eligible")

    # Scenario 26: snapshot CLV still works for valid same-date slate
    def test_26_snapshot_clv_works_for_valid_slate(self):
        # Verify that kalshi ticker with correct date passes validation
        valid_ticker = "KXMLBGAME-26JUN131905NYYBOS-NYY"
        self.assertTrue(
            check_ticker_date(valid_ticker, REQ_DATE),
            "Valid same-date ticker must pass date check"
        )
        self.assertTrue(
            validate_kalshi_ticker_date(valid_ticker, REQ_DATE),
            "Valid ticker must pass stale_date_guard validation too"
        )

    # Scenario 27: snapshot CLV rejects wrong-date snapshots
    def test_27_snapshot_clv_rejects_wrong_date(self):
        wrong_date_ticker = "KXMLBGAME-26JUN121905NYYBOS-NYY"
        self.assertFalse(
            check_ticker_date(wrong_date_ticker, REQ_DATE),
            "Wrong-date snapshot ticker must fail date check"
        )
        self.assertFalse(
            validate_kalshi_ticker_date(wrong_date_ticker, REQ_DATE),
            "Wrong-date ticker must fail stale_date_guard validation"
        )


class TestPostFetchGate(unittest.TestCase):
    """Additional tests focused on the post_fetch_gate date logic."""

    def test_post_fetch_gate_date_check_logic(self):
        """The post_fetch_gate must hard-fail on date mismatch."""
        # The gate now uses the validate_slate_pre date logic.
        # We test via validate_pre as proxy:
        from validate_slate_pre import validate_pre
        # Slate says June 12, requested June 13 → hard fail
        slate = _make_slate(date=PREV_DATE)
        hard, soft, warns = validate_pre(slate, REQ_DATE)
        self.assertTrue(len(hard) > 0)
        self.assertTrue(any("STALE" in e.upper() or PREV_DATE in e for e in hard))


class TestKalshiIdentityHardening(unittest.TestCase):
    """Tests for Kalshi ticker date/identity validation."""

    def test_correct_date_ticker_accepted(self):
        self.assertTrue(check_ticker_date("KXMLBGAME-26JUN131905NYYBOS-NYY", REQ_DATE))

    def test_prev_day_ticker_rejected(self):
        self.assertFalse(check_ticker_date("KXMLBGAME-26JUN121905NYYBOS-NYY", REQ_DATE))

    def test_no_ticker_rejected(self):
        self.assertFalse(check_ticker_date(None, REQ_DATE))

    def test_empty_ticker_rejected(self):
        self.assertFalse(check_ticker_date("", REQ_DATE))

    def test_no_date_rejected(self):
        self.assertFalse(check_ticker_date("KXMLBGAME-26JUN131905NYYBOS-NYY", None))

    def test_f5_ticker_correct_date(self):
        self.assertTrue(check_ticker_date("KXMLBF5-26JUN131905NYYBOS-BOS", REQ_DATE))

    def test_rfi_ticker_correct_date(self):
        self.assertTrue(check_ticker_date("KXMLBRFI-26JUN131905NYYBOS", REQ_DATE))


class TestDataQualityGate(unittest.TestCase):
    """Additional DATA_QUALITY_GATE tests."""

    def test_nrfi_without_bullpen_data_blocked(self):
        result = classify_bet(
            {
                "market": "NRFI", "price": 110,
                "awayAbbr": "NYY", "homeAbbr": "BOS",
                "ticker": "KXMLBRFI-26JUN131905NYYBOS",
                "sourceTimestamp": f"{REQ_DATE}T18:00:00Z",
                "hasBullpenData": False,
            },
            REQ_DATE,
            slate_date=REQ_DATE
        )
        self.assertEqual(result["dataQualityStatus"], "REJECT_BULLPEN_DATA_MISSING")
        self.assertFalse(result["realMoneyEligible"])

    def test_stale_fetch_status_blocks_everything(self):
        result = classify_bet(
            {"market": "ML", "price": -120},
            REQ_DATE,
            fetch_status="FAILED_STALE_DATE"
        )
        self.assertEqual(result["dataQualityStatus"], "REJECT_STALE_DATE")
        self.assertFalse(result["realMoneyEligible"])
        self.assertFalse(result["paperEligible"])

    def test_classify_all_bets_stale_blocks_all(self):
        candidates = [
            {"market": "ML", "price": -120, "ticker": "KXMLBGAME-26JUN131905NYYBOS-NYY"},
            {"market": "F5 ML", "price": 105, "ticker": "KXMLBF5-26JUN131905NYYBOS-BOS"},
        ]
        results = classify_all_bets(candidates, REQ_DATE, slate_date=PREV_DATE)
        for r in results:
            self.assertEqual(r["dataQualityStatus"], "REJECT_STALE_DATE")
            self.assertFalse(r["paperEligible"])


if __name__ == "__main__":
    # Run tests
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestStaleDateGuard))
    suite.addTests(loader.loadTestsFromTestCase(TestPostFetchGate))
    suite.addTests(loader.loadTestsFromTestCase(TestKalshiIdentityHardening))
    suite.addTests(loader.loadTestsFromTestCase(TestDataQualityGate))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
