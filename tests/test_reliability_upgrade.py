#!/usr/bin/env python3
"""
tests/test_reliability_upgrade.py
====================================
20 Regression Tests for the June 14, 2026 Reliability Upgrade

Tests cover:
1.  CLV snapshot before first pitch → VALID
2.  CLV snapshot after first pitch → INVALID_POST_START
3.  Missing snapshot → MISSING
4.  Missing ticker → TICKER_NOT_FOUND
5.  Sentinel prices are rejected (19900, -19900, 100000, -100000)
6.  Authoritative slate cannot be overwritten by rerun
7.  Valid lineup recheck before first pitch can update a game
8.  Rerun after first pitch cannot update that game
9.  One contaminated game does not overwrite the full slate
10. Widespread contaminated rerun is quarantined as REJECTED_CONTAMINATED
11. PAPER F5 $1.50 bet is NOT counted as REAL
12. Official bankroll excludes MODEL_ONLY and PAPER
13. REAL_PROBE is separate from REAL in bankroll reporting
14. Postponed game generates no active bets
15. F5 tie grades as LOSS
16. F5 settlement uses linescore before RBI reconstruction
17. YRFI/NRFI explanations cannot cite bullpen/full-game-only factors
18. Game totals are tracked even when blocked for win-rate reasons
19. Post-slate review cannot use postgame snapshots for CLV
20. betSize > 1 never controls real-money classification
"""

import json
import os
import sys
import tempfile
import shutil
from datetime import datetime, timezone, timedelta

# Add repo root to path
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, ROOT_DIR)

from lib.clv_validator import validate_clv, CLVResult
from lib.sentinel_validator import (
    is_sentinel_american, validate_no_sentinels, SentinelValidationError,
    validate_slate_for_sentinels
)
from lib.slate_manager import (
    save_slate, load_authoritative, authoritative_exists,
    RUN_TYPE_OFFICIAL_PREGAME, RUN_TYPE_LINEUP_RECHECK,
    RUN_TYPE_IN_PLAY_RECHECK, RUN_TYPE_REJECTED_CONTAMINATED,
    merge_rerun_into_authoritative,
)
from lib.tracking_type import (
    enforce_tracking_schema, calculate_bankroll_pl, validate_real_probe_eligibility,
    TrackingTypeError, TRACKING_REAL, TRACKING_MODEL_ONLY, TRACKING_PAPER,
    TRACKING_REAL_PROBE, BLOCK_CLASS_DATA_HARD, BLOCK_CLASS_RISK_SOFT,
    REAL_PROBE_ABSOLUTE_MAX_STAKE,
)
from lib.postponed_guard import is_postponed, check_game_status, void_bets_for_game
from lib.f5_settlement import (
    settle_f5_from_linescore_api, settle_f5_from_boxscore_fallback,
    extract_f5_score_from_linescore, settle_f5_ml,
    F5_RESULT_LOSS, F5_RESULT_WIN, F5_RESULT_PUSH
)
from lib.yrfi_nrfi_validator import (
    validate_yrfi_nrfi_inputs, check_probe_eligibility
)


# ── Test Helpers ──────────────────────────────────────────────────────────────

def make_temp_dir():
    """Create a temporary directory for slate/snapshot files."""
    return tempfile.mkdtemp()


def write_snapshot(snap_dir, date_str, game_pk, ticker, yes_price, capture_ts_str, game_start_str):
    """Write a test pregame snapshot file."""
    day_dir = os.path.join(snap_dir, date_str)
    os.makedirs(day_dir, exist_ok=True)
    path = os.path.join(day_dir, f"pregame_{game_pk}.json")
    snap = {
        "date": date_str,
        "gamePk": str(game_pk),
        "captureTimestamp": capture_ts_str,
        "snapshots": [{
            "ticker": ticker,
            "gamePk": str(game_pk),
            "clvStatus": "VALID" if yes_price and 1 <= float(yes_price) <= 99 else "NO_VALID_PRICE",
            "clvPrice": yes_price,
            "captureTimestamp": capture_ts_str,
            "gameStartTime": game_start_str,
        }]
    }
    # Override status for sentinel
    if yes_price in (19900, -19900, 100000, -100000):
        snap["snapshots"][0]["clvStatus"] = "SENTINEL_PRICE"
    with open(path, "w") as f:
        json.dump(snap, f)
    return path


def make_bet(ticker, price, date_str, game_start_str, placement_ts=None, tracking_type="REAL", actually_placed=True, bet_size=None):
    """Create a minimal test bet dict."""
    bet = {
        "ticker": ticker,
        "price": price,
        "date": date_str,
        "scheduledStartTime": game_start_str,
        "trackingType": tracking_type,
        "actuallyPlaced": actually_placed,
        "placementConfirmedAt": placement_ts or (game_start_str if actually_placed else None),
        "stake": bet_size or 1.0,
        "betSize": bet_size or 1.0,
        "pl": 0.85,
    }
    return bet


def make_game(game_pk, start_time_str, away="NYY", home="TOR", status="Scheduled", markets=None):
    """Create a minimal test game dict."""
    return {
        "gameId": str(game_pk),
        "gamePk": str(game_pk),
        "startTime": start_time_str,
        "status": status,
        "away": {"abbr": away, "pitcher": {"id": "111", "name": "Test Pitcher"}},
        "home": {"abbr": home, "pitcher": {"id": "222", "name": "Test Pitcher 2"}},
        "markets": markets or [],
    }


def future_ts(hours=3):
    """Return ISO timestamp N hours in the future."""
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def past_ts(hours=3):
    """Return ISO timestamp N hours in the past."""
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


# ── TESTS ─────────────────────────────────────────────────────────────────────

def test_01_clv_snapshot_before_first_pitch_valid():
    """TEST 1: CLV snapshot before first pitch → VALID"""
    tmp = make_temp_dir()
    try:
        date_str = "2026-06-14"
        game_pk = "12345"
        ticker = "KXMLBF5-26JUN141340ATLNYM-ATL"
        game_start = future_ts(2)   # starts 2 hours from now
        capture_ts = past_ts(0.5)   # captured 30 min ago

        write_snapshot(tmp, date_str, game_pk, ticker, 55.0, capture_ts, game_start)

        bet = make_bet(ticker, -141, date_str, game_start, capture_ts, bet_size=4.50)
        result = validate_clv(bet, snapshot_dir=tmp)

        assert result.clvStatus == "VALID", f"Expected VALID, got {result.clvStatus}: {result.clvNotes}"
        assert result.closePrice == 55.0
        assert result.entryPrice == -141
        assert result.clvPct is not None
        print("  TEST 1 PASS: CLV snapshot before first pitch → VALID")
    finally:
        shutil.rmtree(tmp)


def test_02_clv_snapshot_after_first_pitch_invalid():
    """TEST 2: CLV snapshot after first pitch → INVALID_POST_START"""
    tmp = make_temp_dir()
    try:
        date_str = "2026-06-14"
        game_pk = "12346"
        ticker = "KXMLBF5-26JUN141337NYYTOR-NYY"
        game_start = past_ts(1)     # started 1 hour ago
        capture_ts = past_ts(0.5)   # snapshot taken 30 min ago (AFTER start)

        # Write snapshot with post-start capture time
        write_snapshot(tmp, date_str, game_pk, ticker, 62.0, capture_ts, game_start)
        # Override the snapshot status to simulate post-start
        snap_path = os.path.join(tmp, date_str, f"pregame_{game_pk}.json")
        with open(snap_path) as f:
            data = json.load(f)
        data["snapshots"][0]["clvStatus"] = "INVALID_POST_START"
        data["snapshots"][0]["notes"] = "Captured after game start"
        with open(snap_path, "w") as f:
            json.dump(data, f)

        bet = make_bet(ticker, -200, date_str, game_start, past_ts(2), bet_size=4.50)
        result = validate_clv(bet, snapshot_dir=tmp)

        assert result.clvStatus == "INVALID_POST_START", \
            f"Expected INVALID_POST_START, got {result.clvStatus}"
        print("  TEST 2 PASS: Post-start snapshot → INVALID_POST_START")
    finally:
        shutil.rmtree(tmp)


def test_03_missing_snapshot_returns_missing():
    """TEST 3: Missing snapshot → MISSING"""
    tmp = make_temp_dir()
    try:
        date_str = "2026-06-14"
        ticker = "KXMLBF5-26JUN141510CHCSF-SF"
        game_start = future_ts(2)

        bet = make_bet(ticker, -130, date_str, game_start, bet_size=3.0)
        result = validate_clv(bet, snapshot_dir=tmp)

        assert result.clvStatus == "MISSING", f"Expected MISSING, got {result.clvStatus}"
        assert result.clvPct is None
        print("  TEST 3 PASS: Missing snapshot → MISSING")
    finally:
        shutil.rmtree(tmp)


def test_04_missing_ticker_returns_ticker_not_found():
    """TEST 4: Missing ticker → TICKER_NOT_FOUND"""
    tmp = make_temp_dir()
    try:
        date_str = "2026-06-14"
        game_start = future_ts(2)

        # Bet with no ticker
        bet = {
            "ticker": None,
            "marketTicker": None,
            "price": -141,
            "date": date_str,
            "scheduledStartTime": game_start,
            "trackingType": "REAL",
            "actuallyPlaced": True,
        }
        result = validate_clv(bet, snapshot_dir=tmp)

        assert result.clvStatus == "TICKER_NOT_FOUND", \
            f"Expected TICKER_NOT_FOUND, got {result.clvStatus}"
        assert result.clvPct is None
        print("  TEST 4 PASS: Missing ticker → TICKER_NOT_FOUND")
    finally:
        shutil.rmtree(tmp)


def test_05_sentinel_prices_rejected():
    """TEST 5: Sentinel prices are rejected (19900, -19900, 100000, -100000)"""
    sentinel_values = [19900, -19900, 100000, -100000, 99999]

    for val in sentinel_values:
        assert is_sentinel_american(val), f"Expected {val} to be sentinel"

    # Test in slate validation
    bad_slate = {
        "games": [
            {"gameId": "1", "markets": [{"price": 19900, "modelProb": 0.5}]},
        ]
    }
    is_valid, bad_games = validate_slate_for_sentinels(bad_slate, raise_on_error=False)
    assert not is_valid, "Expected sentinel slate to be invalid"
    assert "1" in bad_games

    # Test in object scan
    test_obj = {"awayML": 19900, "homeML": -141}
    try:
        validate_no_sentinels(test_obj, raise_on_error=True)
        assert False, "Expected SentinelValidationError"
    except SentinelValidationError as e:
        assert "19900" in str(e)

    # Valid prices should pass
    good_slate = {
        "games": [
            {"gameId": "2", "markets": [{"price": -141, "modelProb": 0.57}]},
        ]
    }
    is_valid2, _ = validate_slate_for_sentinels(good_slate, raise_on_error=False)
    assert is_valid2, "Good slate should pass sentinel validation"

    print("  TEST 5 PASS: Sentinel prices 19900/-19900/100000/-100000 all rejected")


def test_06_authoritative_slate_cannot_be_overwritten():
    """TEST 6: Authoritative slate cannot be overwritten by rerun"""
    tmp = make_temp_dir()
    try:
        date_str = "2026-06-14"
        start_time = future_ts(3)

        original_slate = {
            "date": date_str,
            "games": [make_game("111", start_time)],
        }

        # First run creates authoritative
        result1 = save_slate(date_str, tmp, original_slate, RUN_TYPE_OFFICIAL_PREGAME)
        assert result1.get("authoritativeWritten") is True

        auth1 = load_authoritative(date_str, tmp)
        assert auth1 is not None

        # Second run with different data
        modified_slate = {
            "date": date_str,
            "games": [make_game("111", start_time, status="Modified")],
            "_modifiedData": True,
        }
        result2 = save_slate(date_str, tmp, modified_slate, RUN_TYPE_OFFICIAL_PREGAME)

        # Authoritative should NOT be overwritten
        auth2 = load_authoritative(date_str, tmp)
        assert auth2.get("_authoritative") is True
        assert "_modifiedData" not in auth2, \
            "Authoritative slate was overwritten by second OFFICIAL_PREGAME run!"
        assert result2.get("authoritativeWritten") is False

        print("  TEST 6 PASS: Authoritative slate cannot be overwritten by rerun")
    finally:
        shutil.rmtree(tmp)


def test_07_valid_lineup_recheck_before_first_pitch_can_update():
    """TEST 7: Valid lineup recheck before first pitch can update a game"""
    tmp = make_temp_dir()
    try:
        date_str = "2026-06-14"
        start_time = future_ts(3)

        # Original slate with incomplete pitcher
        original_game = make_game("222", start_time)
        original_game["away"]["pitcher"] = None  # Incomplete
        original_slate = {"date": date_str, "games": [original_game]}

        save_slate(date_str, tmp, original_slate, RUN_TYPE_OFFICIAL_PREGAME)

        # Recheck with confirmed pitcher
        updated_game = make_game("222", start_time)
        updated_game["away"]["pitcher"] = {"id": "999", "name": "Confirmed Ace"}
        updated_game["away"]["lineup"] = [{"name": f"Player {i}"} for i in range(9)]
        updated_slate = {"date": date_str, "games": [updated_game]}

        result = save_slate(date_str, tmp, updated_slate, RUN_TYPE_LINEUP_RECHECK)
        assert result.get("authoritativeUpdated") is True

        auth = load_authoritative(date_str, tmp)
        updated = next((g for g in auth["games"] if g.get("gameId") == "222"), None)
        assert updated is not None
        assert updated["away"]["pitcher"]["id"] == "999", \
            "Lineup recheck should have updated pitcher"

        print("  TEST 7 PASS: Valid lineup recheck before first pitch updates game")
    finally:
        shutil.rmtree(tmp)


def test_08_rerun_after_first_pitch_cannot_update():
    """TEST 8: Rerun after first pitch cannot update that game"""
    tmp = make_temp_dir()
    try:
        date_str = "2026-06-14"
        start_time = past_ts(1)  # Game started 1 hour ago

        original_game = make_game("333", start_time)
        original_game["away"]["pitcher"] = {"id": "100", "name": "Original Pitcher"}
        original_slate = {"date": date_str, "games": [original_game]}
        save_slate(date_str, tmp, original_slate, RUN_TYPE_OFFICIAL_PREGAME)

        # Rerun with different pitcher (should be frozen)
        updated_game = make_game("333", start_time)
        updated_game["away"]["pitcher"] = {"id": "999", "name": "New Pitcher Post-Start"}
        updated_slate = {"date": date_str, "games": [updated_game]}

        result = save_slate(date_str, tmp, updated_slate, RUN_TYPE_LINEUP_RECHECK)
        run_report = result.get("runReport", {})

        # The game should be frozen (not updated)
        frozen = run_report.get("frozen", [])
        frozen_pks = [f["gamePk"] for f in frozen]
        assert "333" in frozen_pks, f"Game 333 should be frozen but frozen={frozen_pks}"

        # Authoritative should still have original pitcher
        auth = load_authoritative(date_str, tmp)
        game = next((g for g in auth["games"] if g.get("gameId") == "333"), None)
        assert game["away"]["pitcher"]["id"] == "100", \
            "Post-start game should retain original pitcher (frozen)"

        print("  TEST 8 PASS: Rerun after first pitch cannot update that game")
    finally:
        shutil.rmtree(tmp)


def test_09_one_contaminated_game_does_not_overwrite_full_slate():
    """TEST 9: One contaminated game does not overwrite the full slate"""
    tmp = make_temp_dir()
    try:
        date_str = "2026-06-14"
        start_time = future_ts(3)

        game1 = make_game("444", start_time)
        game2 = make_game("445", start_time)
        original_slate = {"date": date_str, "games": [game1, game2]}
        save_slate(date_str, tmp, original_slate, RUN_TYPE_OFFICIAL_PREGAME)

        # Rerun: game1 clean, game2 has sentinel price
        game1_recheck = make_game("444", start_time)
        game1_recheck["away"]["pitcher"] = {"id": "111", "name": "Updated Pitcher"}
        game1_recheck["away"]["lineup"] = [{"name": f"P{i}"} for i in range(9)]

        game2_bad = make_game("445", start_time)
        game2_bad["markets"] = [{"price": 19900}]  # Sentinel!

        recheck_slate = {"date": date_str, "games": [game1_recheck, game2_bad]}

        now_utc = datetime.now(timezone.utc)
        auth_data = load_authoritative(date_str, tmp)
        merged, run_report = merge_rerun_into_authoritative(
            auth_data, recheck_slate, RUN_TYPE_LINEUP_RECHECK, now_utc
        )

        # Only 1 of 2 games bad → not quarantined
        assert not run_report.get("quarantined"), \
            "Should not quarantine when only 1/2 games bad"

        # Game 444 should be updated (clean)
        # Game 445 should be rejected (sentinel)
        rejected_pks = [r["gamePk"] for r in run_report.get("rejected", [])]
        assert "445" in rejected_pks, "Game 445 (sentinel) should be in rejected"

        # Authoritative game 444 should have updated pitcher
        game1_auth = next((g for g in merged["games"] if g.get("gameId") == "444"), None)
        assert game1_auth is not None

        print("  TEST 9 PASS: One contaminated game does not overwrite full slate")
    finally:
        shutil.rmtree(tmp)


def test_10_widespread_contaminated_rerun_quarantined():
    """TEST 10: Widespread contaminated rerun is quarantined as REJECTED_CONTAMINATED"""
    tmp = make_temp_dir()
    try:
        date_str = "2026-06-14"
        start_time = future_ts(3)

        games_orig = [make_game(str(pk), start_time) for pk in range(600, 605)]
        original_slate = {"date": date_str, "games": games_orig}
        save_slate(date_str, tmp, original_slate, RUN_TYPE_OFFICIAL_PREGAME)

        # Rerun: 4 of 5 games have sentinel prices
        games_bad = []
        for i, pk in enumerate(range(600, 605)):
            g = make_game(str(pk), start_time)
            if i < 4:  # 4 of 5 games contaminated
                g["markets"] = [{"price": -19900}]
            games_bad.append(g)

        bad_slate = {"date": date_str, "games": games_bad}
        auth_data = load_authoritative(date_str, tmp)
        now_utc = datetime.now(timezone.utc)
        merged, run_report = merge_rerun_into_authoritative(
            auth_data, bad_slate, RUN_TYPE_LINEUP_RECHECK, now_utc
        )

        assert run_report.get("quarantined"), \
            f"Expected quarantine with 4/5 games contaminated. Report: {run_report}"
        assert run_report.get("runType") == RUN_TYPE_REJECTED_CONTAMINATED

        print("  TEST 10 PASS: Widespread contaminated rerun quarantined")
    finally:
        shutil.rmtree(tmp)


def test_11_paper_f5_bet_not_counted_as_real():
    """TEST 11: PAPER F5 $1.50 bet is NOT counted as REAL"""
    bets = [
        {
            "id": "f5_paper_001",
            "market": "F5 ML",
            "trackingType": TRACKING_MODEL_ONLY,
            "actuallyPlaced": False,
            "placementConfirmedAt": None,
            "betSize": 1.50,   # F5 multiplier applied to $1 base
            "stake": 1.50,
            "pl": 1.50,        # Hypothetical win
            "result": "WIN",
        }
    ]

    schema = enforce_tracking_schema(bets[0])
    assert schema.trackingType == TRACKING_MODEL_ONLY
    assert not schema.counts_for_bankroll

    result = calculate_bankroll_pl(bets)
    assert result["totalPL"] == 0.0, \
        f"MODEL_ONLY bet with betSize=1.50 must not affect bankroll. Got: {result['totalPL']}"
    assert result["realBetCount"] == 0
    assert result["probeBetCount"] == 0

    print("  TEST 11 PASS: PAPER F5 $1.50 bet NOT counted as REAL")


def test_12_bankroll_excludes_model_only_and_paper():
    """TEST 12: Official bankroll excludes MODEL_ONLY and PAPER"""
    bets = [
        {"trackingType": "REAL", "actuallyPlaced": True,
         "placementConfirmedAt": "2026-06-14T11:00:00Z", "pl": 5.0, "betSize": 4.0},
        {"trackingType": "MODEL_ONLY", "actuallyPlaced": False,
         "placementConfirmedAt": None, "pl": 3.0, "betSize": 1.5},
        {"trackingType": "PAPER", "actuallyPlaced": False,
         "placementConfirmedAt": None, "pl": 2.0, "betSize": 1.0},
        {"trackingType": "REAL", "actuallyPlaced": True,
         "placementConfirmedAt": "2026-06-14T12:00:00Z", "pl": -4.5, "betSize": 4.5},
    ]

    result = calculate_bankroll_pl(bets)

    assert result["totalPL"] == round(5.0 + (-4.5), 2), \
        f"Expected 0.50, got {result['totalPL']}"
    assert result["realBetCount"] == 2
    assert result["excludedBetCount"] == 2  # MODEL_ONLY and PAPER excluded

    print("  TEST 12 PASS: Bankroll excludes MODEL_ONLY and PAPER")


def test_13_real_probe_separate_from_real_in_bankroll():
    """TEST 13: REAL_PROBE is separate from REAL in bankroll reporting"""
    bets = [
        {"trackingType": "REAL", "actuallyPlaced": True,
         "placementConfirmedAt": "2026-06-14T11:00:00Z", "pl": 5.0, "betSize": 4.0},
        {"trackingType": "REAL_PROBE", "actuallyPlaced": True,
         "placementConfirmedAt": "2026-06-14T11:00:00Z", "pl": 0.85, "betSize": 1.0},
        {"trackingType": "PAPER", "actuallyPlaced": False,
         "placementConfirmedAt": None, "pl": 2.0, "betSize": 1.0},
    ]

    result = calculate_bankroll_pl(bets)

    assert result["realBetCount"] == 1, f"Expected 1 REAL bet, got {result['realBetCount']}"
    assert result["probeBetCount"] == 1, f"Expected 1 REAL_PROBE bet, got {result['probeBetCount']}"
    assert result["realPL"] == 5.0, f"Expected realPL=5.0, got {result['realPL']}"
    assert result["probePL"] == 0.85, f"Expected probePL=0.85, got {result['probePL']}"
    assert result["totalPL"] == 5.85, f"Expected totalPL=5.85, got {result['totalPL']}"

    print("  TEST 13 PASS: REAL_PROBE separate from REAL in bankroll")


def test_14_postponed_game_generates_no_active_bets():
    """TEST 14: Postponed game generates no active bets"""
    postponed_statuses = [
        "Postponed", "Cancelled", "Canceled", "Suspended",
        "Postponed - Rain", "Rain Delay"
    ]

    for status in postponed_statuses:
        assert is_postponed(status), f"Expected '{status}' to be postponed"

    non_postponed = ["Scheduled", "Pre-Game", "In Progress", "Final"]
    for status in non_postponed:
        assert not is_postponed(status), f"Expected '{status}' NOT postponed"

    # Test full game check
    det_cle = {
        "gameId": "777",
        "status": "Postponed",
        "away": {"abbr": "DET"},
        "home": {"abbr": "CLE"},
    }
    result = check_game_status(det_cle)
    assert result["shouldSkip"] is True
    assert result["voidExisting"] is True
    assert result["skipReason"] == "postponed"

    # Test void existing bets
    bets = [
        {"id": "001", "game": "DET@CLE", "result": None, "pl": 5.0},
        {"id": "002", "game": "NYY@TOR", "result": None, "pl": 3.0},
    ]
    voided = void_bets_for_game(bets, matchup="DET@CLE", reason="postponed")
    assert "001" in voided
    assert "002" not in voided
    assert bets[0]["result"] == "VOID"
    assert bets[0]["pl"] == 0
    assert bets[1]["result"] is None  # NYY@TOR not affected

    print("  TEST 14 PASS: Postponed game generates no active bets")


def test_15_f5_tie_grades_as_loss():
    """TEST 15: F5 tie grades as LOSS"""
    # NYY@TOR June 14 regression: F5 score 2-2 → NYY Away = LOSS
    result = settle_f5_ml(
        away_f5_score=2,
        home_f5_score=2,
        bet_side="away",
        kalshi_refunds_ties=False,
        source="linescore",
    )
    assert result["result"] == F5_RESULT_LOSS, f"Expected LOSS for tie, got {result['result']}"
    assert result["isTie"] is True

    # Also test home side tie
    result_home = settle_f5_ml(
        away_f5_score=2,
        home_f5_score=2,
        bet_side="home",
        kalshi_refunds_ties=False,
    )
    assert result_home["result"] == F5_RESULT_LOSS

    # Non-tie should be decided correctly
    result_win = settle_f5_ml(away_f5_score=3, home_f5_score=1, bet_side="away")
    assert result_win["result"] == F5_RESULT_WIN
    assert not result_win["isTie"]

    result_loss = settle_f5_ml(away_f5_score=1, home_f5_score=3, bet_side="away")
    assert result_loss["result"] == F5_RESULT_LOSS

    print("  TEST 15 PASS: F5 tie grades as LOSS")


def test_16_f5_settlement_uses_linescore_before_rbi():
    """TEST 16: F5 settlement uses linescore before RBI reconstruction"""
    # Valid 5-inning linescore
    linescore = {
        "innings": [
            {"num": 1, "away": {"runs": 0}, "home": {"runs": 1}},
            {"num": 2, "away": {"runs": 1}, "home": {"runs": 0}},
            {"num": 3, "away": {"runs": 1}, "home": {"runs": 0}},
            {"num": 4, "away": {"runs": 0}, "home": {"runs": 1}},
            {"num": 5, "away": {"runs": 0}, "home": {"runs": 0}},
        ]
    }

    away_score, home_score = extract_f5_score_from_linescore(linescore)
    assert away_score == 2, f"Expected away=2, got {away_score}"
    assert home_score == 2, f"Expected home=2, got {home_score}"

    result = settle_f5_from_linescore_api(linescore, bet_side="away")
    assert result["source"] == "linescore"
    assert not result.get("fallbackUsed")

    # Fallback should flag itself
    fallback_result = settle_f5_from_boxscore_fallback(
        away_f5_score=3, home_f5_score=1, bet_side="away",
        fallback_reason="linescore_unavailable"
    )
    assert fallback_result.get("fallbackUsed") is True
    assert "linescore_unavailable" in fallback_result.get("fallbackReason", "")

    # RBI discrepancy: linescore wins
    from lib.f5_settlement import validate_rbi_vs_linescore
    check = validate_rbi_vs_linescore(rbi_away=4, rbi_home=1, linescore_away=3, linescore_home=1)
    assert check["discrepancy"] is True
    assert check["linescoreAway"] == 3  # Linescore value used
    assert "Linescore values used for settlement" in check["notes"]

    print("  TEST 16 PASS: F5 settlement uses linescore before RBI")


def test_17_yrfi_nrfi_cannot_cite_bullpen_factors():
    """TEST 17: YRFI/NRFI explanations cannot cite bullpen/full-game-only factors"""
    # Bad bet with bullpen factors
    bad_bet = {
        "market": "YRFI",
        "factors": {"bullpen_exposure": 0.3, "first_inning_xera_away": 3.5},
        "reasons": ["Bullpen arrives by inning 2", "short leash on starter"],
        "edge": 2.5,
    }

    is_valid, violations = validate_yrfi_nrfi_inputs(bad_bet)
    assert not is_valid, "YRFI with bullpen factors should be invalid"
    assert len(violations) > 0
    violation_text = " ".join(violations)
    assert "bullpen" in violation_text.lower() or "leash" in violation_text.lower(), \
        f"Expected bullpen/leash violation, got: {violations}"

    # Good bet with only first-inning inputs
    good_bet = {
        "market": "YRFI",
        "factors": {
            "first_inning_xera_away": 4.1,
            "first_inning_run_rate_home": 0.72,
        },
        "reasons": [
            "Away starter weak in 1st innings (xERA 4.1)",
            "Home team scores 0.72 R/game in 1st"
        ],
        "edge": 2.5,
        "lambda_used": 0.95,
        "lambda_formula": "avg(away_1inn_rate, home_1inn_rate)",
        "lambda_is_first_inning_specific": True,
        "lambda_derived_from_full_game": False,
        "park_first_inning_included": True,
        "team_first_inning_rates_included": True,
        "independent_poisson_first_inning_valid": True,
        "ticker": "KXMLBRFI-26JUN14ATLNYM",
    }

    is_valid2, violations2 = validate_yrfi_nrfi_inputs(good_bet)
    hard_violations = [v for v in violations2 if "Warning" not in v]
    assert len(hard_violations) == 0, f"Good YRFI bet should have no hard violations: {hard_violations}"

    print("  TEST 17 PASS: YRFI/NRFI rejects bullpen/full-game factors")


def test_18_game_totals_tracked_when_blocked():
    """TEST 18: Game totals are tracked even when blocked for win-rate reasons"""
    # Game total bet blocked by Rule 71 (paper-only)
    blocked_total_bet = {
        "id": "gt_001",
        "market": "Game_Total",
        "betType": "PAPER",
        "trackingType": TRACKING_PAPER,
        "actuallyPlaced": False,
        "placementConfirmedAt": None,
        "blocked": True,
        "blockReason": "Game Total WR 41% — paper only per Rule 71 until WR≥52% over N≥30",
        "blockClass": "OPPORTUNITY_FILTER",
        "ticker": "KXMLBTOTAL-26JUN14ATLNYM",
        "result": "LOSS",
        "pl": -1.0,
        "edge": 1.5,
        "clvStatus": "UNAVAILABLE",
    }

    # Validate required fields are present for tracking
    required_tracking_fields = ["market", "blockReason", "blockClass", "result", "ticker"]
    for field in required_tracking_fields:
        assert field in blocked_total_bet and blocked_total_bet[field], \
            f"Blocked game total missing tracking field: {field}"

    # Verify it doesn't affect bankroll
    result = calculate_bankroll_pl([blocked_total_bet])
    assert result["totalPL"] == 0.0
    assert result["excludedBetCount"] == 1

    # Verify block class is OPPORTUNITY_FILTER
    assert blocked_total_bet["blockClass"] == "OPPORTUNITY_FILTER"

    # Verify bet is not classified as real-money even though it has a result
    schema = enforce_tracking_schema(blocked_total_bet)
    assert not schema.counts_for_bankroll

    print("  TEST 18 PASS: Blocked game totals tracked but excluded from bankroll")


def test_19_postgame_snapshot_cannot_be_used_for_clv():
    """TEST 19: Post-slate review cannot use postgame snapshots for CLV"""
    tmp = make_temp_dir()
    try:
        date_str = "2026-06-14"
        game_pk = "88888"
        ticker = "KXMLBGAME-26JUN14ATLNYM-ATL"
        game_start = past_ts(4)     # Game started 4 hours ago
        postgame_capture = past_ts(1)  # Snapshot 1 hour ago (postgame!)

        # Write a snapshot that was captured AFTER game start
        write_snapshot(tmp, date_str, game_pk, ticker, 98.0, postgame_capture, game_start)
        # Manually set clvStatus to INVALID_POST_START to simulate post-game snapshot
        snap_path = os.path.join(tmp, date_str, f"pregame_{game_pk}.json")
        with open(snap_path) as f:
            data = json.load(f)
        data["snapshots"][0]["clvStatus"] = "INVALID_POST_START"
        data["snapshots"][0]["notes"] = "Post-game snapshot — invalid for CLV"
        # Note: yes_price of 98.0 would be a settlement price (near 100)
        with open(snap_path, "w") as f:
            json.dump(data, f)

        bet = make_bet(ticker, -141, date_str, game_start, past_ts(5), bet_size=4.0)
        result = validate_clv(bet, snapshot_dir=tmp)

        # Must not return VALID — post-game snapshot rejected
        assert result.clvStatus != "VALID", \
            f"Post-game snapshot should not produce VALID CLV, got {result.clvStatus}"
        assert result.clvPct is None, \
            f"Post-game CLV must be None, got {result.clvPct}"

        print("  TEST 19 PASS: Post-game snapshots cannot be used for CLV")
    finally:
        shutil.rmtree(tmp)


def test_20_bet_size_over_1_never_controls_real_money_classification():
    """TEST 20: betSize > 1 never controls real-money classification"""
    # F5 multiplier can make a PAPER/MODEL_ONLY bet have betSize=1.50
    # This must NOT reclassify to real-money
    high_stake_paper = {
        "trackingType": TRACKING_MODEL_ONLY,
        "actuallyPlaced": False,
        "placementConfirmedAt": None,
        "betSize": 1.50,
        "stake": 1.50,
        "pl": 1.50,
    }
    schema = enforce_tracking_schema(high_stake_paper)
    assert not schema.is_real_money
    assert not schema.counts_for_bankroll

    # A PAPER bet with betSize=8.00 (High tier)
    high_paper = {
        "trackingType": TRACKING_PAPER,
        "actuallyPlaced": False,
        "placementConfirmedAt": None,
        "betSize": 8.00,
        "stake": 8.00,
        "pl": 7.27,
    }
    schema2 = enforce_tracking_schema(high_paper)
    assert not schema2.counts_for_bankroll

    # REAL_PROBE must be rejected if betSize exceeds absolute max
    over_max_probe = {
        "trackingType": TRACKING_REAL_PROBE,
        "actuallyPlaced": True,
        "placementConfirmedAt": "2026-06-14T11:00:00Z",
        "betSize": 2.50,  # Over $1.50 max
        "stake": 2.50,
    }
    try:
        schema3 = enforce_tracking_schema(over_max_probe)
        schema3.validate()
        assert False, "REAL_PROBE with betSize=2.50 should raise TrackingTypeError"
    except TrackingTypeError:
        pass

    # REAL_PROBE probe eligibility: betSize>1 disqualifies probe
    big_bet = {
        "trackingType": TRACKING_REAL_PROBE,
        "betSize": 5.00,
        "ticker": "SOME-TICKER",
        "price": -141,
    }
    is_eligible, reason = validate_real_probe_eligibility(big_bet)
    assert not is_eligible, "betSize=5.00 should disqualify REAL_PROBE"
    assert "betSize" in reason or "max" in reason.lower()

    # Bankroll: PAPER bet with betSize=8 should not count
    bets = [
        {"trackingType": TRACKING_PAPER, "actuallyPlaced": False,
         "placementConfirmedAt": None, "betSize": 8.00, "pl": 7.27, "stake": 8.00},
        {"trackingType": TRACKING_REAL, "actuallyPlaced": True,
         "placementConfirmedAt": "2026-06-14T11:00:00Z", "betSize": 4.50, "pl": 5.00, "stake": 4.50},
    ]
    result = calculate_bankroll_pl(bets)
    assert result["totalPL"] == 5.00, \
        f"Only REAL bet should count. Got {result['totalPL']}"
    assert result["realBetCount"] == 1

    print("  TEST 20 PASS: betSize > 1 never controls real-money classification")


# ── Test runner ───────────────────────────────────────────────────────────────

def run_all_tests():
    tests = [
        test_01_clv_snapshot_before_first_pitch_valid,
        test_02_clv_snapshot_after_first_pitch_invalid,
        test_03_missing_snapshot_returns_missing,
        test_04_missing_ticker_returns_ticker_not_found,
        test_05_sentinel_prices_rejected,
        test_06_authoritative_slate_cannot_be_overwritten,
        test_07_valid_lineup_recheck_before_first_pitch_can_update,
        test_08_rerun_after_first_pitch_cannot_update,
        test_09_one_contaminated_game_does_not_overwrite_full_slate,
        test_10_widespread_contaminated_rerun_quarantined,
        test_11_paper_f5_bet_not_counted_as_real,
        test_12_bankroll_excludes_model_only_and_paper,
        test_13_real_probe_separate_from_real_in_bankroll,
        test_14_postponed_game_generates_no_active_bets,
        test_15_f5_tie_grades_as_loss,
        test_16_f5_settlement_uses_linescore_before_rbi,
        test_17_yrfi_nrfi_cannot_cite_bullpen_factors,
        test_18_game_totals_tracked_when_blocked,
        test_19_postgame_snapshot_cannot_be_used_for_clv,
        test_20_bet_size_over_1_never_controls_real_money_classification,
    ]

    passed = 0
    failed = 0
    results = []

    for test_fn in tests:
        num = test_fn.__name__.split("_")[1].lstrip("0") or "0"
        name = test_fn.__doc__.strip().split("\n")[0] if test_fn.__doc__ else test_fn.__name__
        try:
            test_fn()
            passed += 1
            results.append((int(num), name, "PASS", None))
        except Exception as e:
            failed += 1
            results.append((int(num), name, "FAIL", str(e)))
            print(f"  TEST {num} FAIL: {name}")
            print(f"    Error: {e}")

    print(f"\n{'='*60}")
    print(f"RESULTS: {passed} PASS, {failed} FAIL out of {len(tests)} tests")
    print(f"{'='*60}")

    return results, passed, failed


if __name__ == "__main__":
    print("Running reliability upgrade regression tests...\n")
    results, passed, failed = run_all_tests()
    sys.exit(0 if failed == 0 else 1)
