#!/usr/bin/env python3
"""
tests/test_closing_line_settlement.py
========================================
Coverage for the settlement-side fixes in scripts/capture_closing_lines.py
(MODE='settle') and scripts/clv_from_snapshot.py:

  - official_closing_snapshot is preferred over any other snapshot.
  - Otherwise, the closest snapshot at or before scheduled first pitch wins.
  - A late (post-first-pitch) snapshot is never used as a valid closing
    line; when only late snapshots exist, closingLine stays null and
    clvCaptureStatus/clvStatus becomes LATE_ONLY with a clear reason.
  - Exact ticker matching for ladder markets (spread/total/team total/F5
    spread/F5 total) — never the registry's generic best_line.
  - closingAskPct / closingMidPct / clvAskPct / clvMidPct are computed
    correctly, with positive CLV meaning the contract got MORE expensive
    after entry.
  - Doubleheader isolation: a bet resolves to the correct game entry via
    its ticker's event suffix, not team-name string matching alone.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
CAPTURE_CLOSING_LINES = os.path.join(SCRIPTS_DIR, "capture_closing_lines.py")

sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)


def run_settle(tmp_path, date_str="2026-07-30"):
    result = subprocess.run(
        [sys.executable, CAPTURE_CLOSING_LINES, "settle", date_str],
        cwd=str(tmp_path), capture_output=True, text=True,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return result


def write_registry(tmp_path, registry):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    with open(data_dir / "kalshi_market_registry.json", "w") as f:
        json.dump({"registry": registry}, f)


def write_bets(tmp_path, bets):
    with open(tmp_path / "bets.json", "w") as f:
        json.dump(bets, f)


def read_bets(tmp_path):
    with open(tmp_path / "bets.json") as f:
        return json.load(f)


def base_entry(**overrides):
    entry = {
        "date": "2026-07-30", "time_str": "2140",
        "event_ticker_suffix": "26JUL302140BOSATH",
        "away": "BOS", "home": "ATH",
        "markets": {
            "moneyline": {
                "away_ticker": "KXMLBGAME-26JUL302140BOSATH-BOS",
                "home_ticker": "KXMLBGAME-26JUL302140BOSATH-ATH",
            },
            "spread": {
                "lines": [
                    {"ticker": "KXMLBSPREAD-26JUL302140BOSATH-BOS2", "team": "BOS", "run_number": 2, "win_by_over": 1.5},
                    {"ticker": "KXMLBSPREAD-26JUL302140BOSATH-BOS3", "team": "BOS", "run_number": 3, "win_by_over": 2.5},
                ],
            },
            "team_total_away": {
                "lines": [
                    {"ticker": "KXMLBTEAMTOTAL-26JUL302140BOSATH-BOS4", "team": "BOS", "over_n": 4},
                ],
            },
            "rfi": {"ticker": "KXMLBRFI-26JUL302140BOSATH"},
        },
        "closing_snapshots": [],
    }
    entry.update(overrides)
    return entry


def official_snapshot(prices_by_ticker, snapshot_ts="2026-07-31T01:35:00Z"):
    return {
        "snapshot_ts": snapshot_ts,
        "capture_timing": "PRE_START",
        "prices": {"by_ticker": prices_by_ticker},
    }


class TestOfficialSnapshotPreferred:

    def test_official_snapshot_used_over_older_closing_snapshots(self, tmp_path):
        entry = base_entry()
        entry["official_closing_snapshot"] = official_snapshot({
            "KXMLBGAME-26JUL302140BOSATH-BOS": {"ticker": "KXMLBGAME-26JUL302140BOSATH-BOS", "mid": 0.55, "yes_ask": 0.56, "yes_bid": 0.54},
        })
        # An older, DIFFERENT-priced snapshot also exists — must be ignored.
        entry["closing_snapshots"] = [{
            "snapshot_ts": "2026-07-31T00:00:00Z",
            "prices": {"by_ticker": {
                "KXMLBGAME-26JUL302140BOSATH-BOS": {"ticker": "KXMLBGAME-26JUL302140BOSATH-BOS", "mid": 0.40, "yes_ask": 0.41},
            }},
        }]
        write_registry(tmp_path, {"BOSATH": entry})
        write_bets(tmp_path, [{
            "id": "b1", "date": "2026-07-30", "game": "BOS @ ATH", "market": "ML", "betSide": "AWAY",
            "betTimeLine": -120, "status": "pending",
            "marketTicker": "KXMLBGAME-26JUL302140BOSATH-BOS",
            "eventTicker": "KXMLBGAME-26JUL302140BOSATH",
        }])
        run_settle(tmp_path)
        bet = read_bets(tmp_path)[0]
        assert bet["closingLineSource"] == "official_closing_snapshot"
        assert bet["closingMidPct"] == 55.0


class TestClosestPreStartSelection:

    def test_closest_pre_start_snapshot_chosen_not_earliest(self, tmp_path):
        entry = base_entry()
        entry["closing_snapshots"] = [
            {"snapshot_ts": "2026-07-31T00:00:00Z", "prices": {"by_ticker": {
                "KXMLBGAME-26JUL302140BOSATH-BOS": {"ticker": "KXMLBGAME-26JUL302140BOSATH-BOS", "mid": 0.40, "yes_ask": 0.41},
            }}},
            {"snapshot_ts": "2026-07-31T01:37:00Z", "prices": {"by_ticker": {
                "KXMLBGAME-26JUL302140BOSATH-BOS": {"ticker": "KXMLBGAME-26JUL302140BOSATH-BOS", "mid": 0.55, "yes_ask": 0.56},
            }}},
        ]
        write_registry(tmp_path, {"BOSATH": entry})
        write_bets(tmp_path, [{
            "id": "b1", "date": "2026-07-30", "game": "BOS @ ATH", "market": "ML", "betSide": "AWAY",
            "betTimeLine": -120, "status": "pending",
            "marketTicker": "KXMLBGAME-26JUL302140BOSATH-BOS",
            "scheduledStartTime": "2026-07-31T01:40:00Z",
        }])
        run_settle(tmp_path)
        bet = read_bets(tmp_path)[0]
        assert bet["closingLineSource"] == "closest_pre_start_snapshot"
        assert bet["closingMidPct"] == 55.0  # the 01:37 snapshot, not the 00:00 one


class TestLateOnlyNeverOfficial:

    def test_late_only_leaves_closing_line_null_with_reason(self, tmp_path):
        entry = base_entry()
        entry["closing_snapshots"] = [{
            "snapshot_ts": "2026-07-31T01:42:00Z",  # AFTER first pitch (01:40)
            "capture_timing": "LATE",
            "prices": {"by_ticker": {
                "KXMLBGAME-26JUL302140BOSATH-BOS": {"ticker": "KXMLBGAME-26JUL302140BOSATH-BOS", "mid": 0.60, "yes_ask": 0.61},
            }},
        }]
        write_registry(tmp_path, {"BOSATH": entry})
        write_bets(tmp_path, [{
            "id": "b1", "date": "2026-07-30", "game": "BOS @ ATH", "market": "ML", "betSide": "AWAY",
            "betTimeLine": -120, "status": "pending",
            "marketTicker": "KXMLBGAME-26JUL302140BOSATH-BOS",
            "scheduledStartTime": "2026-07-31T01:40:00Z",
        }])
        run_settle(tmp_path)
        bet = read_bets(tmp_path)[0]
        assert bet["closingLine"] is None
        assert bet["clvCaptureStatus"] == "LATE_ONLY"
        assert bet.get("closingLineUnavailableReason")
        assert "LATE" in bet["closingLineUnavailableReason"] or "late" in bet["closingLineUnavailableReason"]


class TestExactTickerMatchingLadderMarkets:

    def test_spread_matches_exact_ticker_not_best_line(self, tmp_path):
        entry = base_entry()
        entry["official_closing_snapshot"] = official_snapshot({
            "KXMLBSPREAD-26JUL302140BOSATH-BOS2": {"ticker": "KXMLBSPREAD-26JUL302140BOSATH-BOS2", "mid": 0.53, "yes_ask": 0.54},
            "KXMLBSPREAD-26JUL302140BOSATH-BOS3": {"ticker": "KXMLBSPREAD-26JUL302140BOSATH-BOS3", "mid": 0.41, "yes_ask": 0.42},
        })
        write_registry(tmp_path, {"BOSATH": entry})
        # Bet is on the BOS3 (win by >2.5) contract specifically.
        write_bets(tmp_path, [{
            "id": "b1", "date": "2026-07-30", "game": "BOS @ ATH", "market": "Run Line", "betSide": "BOS",
            "line": 3, "betTimeLine": 135, "status": "pending",
            "marketTicker": "KXMLBSPREAD-26JUL302140BOSATH-BOS3",
        }])
        run_settle(tmp_path)
        bet = read_bets(tmp_path)[0]
        assert bet["closingTicker"] == "KXMLBSPREAD-26JUL302140BOSATH-BOS3"
        assert bet["closingMidPct"] == 41.0   # BOS3's price, not BOS2's (53.0)

    def test_team_total_falls_back_to_exact_side_and_line_without_ticker(self, tmp_path):
        entry = base_entry()
        entry["official_closing_snapshot"] = {
            "snapshot_ts": "2026-07-31T01:35:00Z",
            "capture_timing": "PRE_START",
            "prices": {
                "team_total_away": {"lines": [
                    {"ticker": "KXMLBTEAMTOTAL-26JUL302140BOSATH-BOS4", "team": "BOS", "over_n": 4, "mid": 0.50, "yes_ask": 0.51},
                ]},
                "by_ticker": {},
            },
        }
        write_registry(tmp_path, {"BOSATH": entry})
        # No marketTicker stored (legacy/manual bet) — must match by exact
        # side + exact line, never the registry's generic best_line.
        write_bets(tmp_path, [{
            "id": "b1", "date": "2026-07-30", "game": "BOS @ ATH", "market": "Team Total", "betSide": "BOS",
            "line": 4, "betTimeLine": 102, "status": "pending",
        }])
        run_settle(tmp_path)
        bet = read_bets(tmp_path)[0]
        assert bet["closingMidPct"] == 50.0


class TestClvAskAndMidFields:

    def test_positive_clv_means_contract_got_more_expensive(self, tmp_path):
        entry = base_entry()
        entry["official_closing_snapshot"] = official_snapshot({
            "KXMLBGAME-26JUL302140BOSATH-BOS": {"ticker": "KXMLBGAME-26JUL302140BOSATH-BOS", "mid": 0.60, "yes_ask": 0.61, "yes_bid": 0.59},
        })
        write_registry(tmp_path, {"BOSATH": entry})
        # Entry price -120 American == 54.55% implied — cheaper than the
        # 60%/61% closing mid/ask, so CLV should be positive (we got in
        # before the market moved toward "more expensive").
        write_bets(tmp_path, [{
            "id": "b1", "date": "2026-07-30", "game": "BOS @ ATH", "market": "ML", "betSide": "AWAY",
            "betTimeLine": -120, "status": "pending",
            "marketTicker": "KXMLBGAME-26JUL302140BOSATH-BOS",
        }])
        run_settle(tmp_path)
        bet = read_bets(tmp_path)[0]
        assert bet["closingAskPct"] == 61.0
        assert bet["closingMidPct"] == 60.0
        entry_pct = 120 / 220 * 100  # -120 American -> implied %
        assert bet["clvMidPct"] == pytest.approx(60.0 - entry_pct, abs=0.01)
        assert bet["clvAskPct"] == pytest.approx(61.0 - entry_pct, abs=0.01)
        assert bet["clvMidPct"] > 0
        assert bet["clvAskPct"] > 0


class TestDoubleheaderIsolationAtSettlement:

    def test_bet_resolves_to_correct_game_via_ticker_suffix_not_team_name(self, tmp_path):
        g1 = base_entry(time_str="1600", event_ticker_suffix="26JUL301600BOSNYY", away="BOS", home="NYY")
        g1["markets"] = {"moneyline": {
            "away_ticker": "KXMLBGAME-26JUL301600BOSNYY-BOS", "home_ticker": "KXMLBGAME-26JUL301600BOSNYY-NYY",
        }}
        g1["official_closing_snapshot"] = official_snapshot({
            "KXMLBGAME-26JUL301600BOSNYY-BOS": {"ticker": "KXMLBGAME-26JUL301600BOSNYY-BOS", "mid": 0.30, "yes_ask": 0.31},
        })

        g2 = base_entry(time_str="1930", event_ticker_suffix="26JUL301930BOSNYY", away="BOS", home="NYY")
        g2["markets"] = {"moneyline": {
            "away_ticker": "KXMLBGAME-26JUL301930BOSNYY-BOS", "home_ticker": "KXMLBGAME-26JUL301930BOSNYY-NYY",
        }}
        g2["official_closing_snapshot"] = official_snapshot({
            "KXMLBGAME-26JUL301930BOSNYY-BOS": {"ticker": "KXMLBGAME-26JUL301930BOSNYY-BOS", "mid": 0.70, "yes_ask": 0.71},
        })

        write_registry(tmp_path, {"BOSNYY_G1": g1, "BOSNYY_G2": g2})
        # Both bets have an identical `game` string ("BOS @ NYY") — only the
        # ticker/eventTicker can disambiguate which game each belongs to.
        write_bets(tmp_path, [
            {"id": "b1", "date": "2026-07-30", "game": "BOS @ NYY", "market": "ML", "betSide": "AWAY",
             "betTimeLine": -120, "status": "pending",
             "marketTicker": "KXMLBGAME-26JUL301600BOSNYY-BOS",
             "eventTicker": "KXMLBGAME-26JUL301600BOSNYY"},
            {"id": "b2", "date": "2026-07-30", "game": "BOS @ NYY", "market": "ML", "betSide": "AWAY",
             "betTimeLine": -120, "status": "pending",
             "marketTicker": "KXMLBGAME-26JUL301930BOSNYY-BOS",
             "eventTicker": "KXMLBGAME-26JUL301930BOSNYY"},
        ])
        run_settle(tmp_path)
        bets = {b["id"]: b for b in read_bets(tmp_path)}
        assert bets["b1"]["closingMidPct"] == 30.0
        assert bets["b2"]["closingMidPct"] == 70.0
