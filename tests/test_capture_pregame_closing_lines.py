#!/usr/bin/env python3
"""
tests/test_capture_pregame_closing_lines.py
==============================================
Coverage for scripts/capture_pregame_closing_lines.py:
  - America/New_York -> UTC conversion, including DST transitions.
  - Only games inside [-12min, +5min] of first pitch are fetched.
  - No-op (no writes) when nothing is in-window and no status changed.
  - capture_timing PRE_START vs LATE classification.
  - official_closing_snapshot is only ever chosen from PRE_START snapshots
    with prices, closest to first pitch — never a LATE one.
  - closing_capture_status transitions: CAPTURED_PRE_START / LATE_ONLY /
    MISSED / NO_PRICES.
  - NRFI is correctly derived (inverted) from the single RFI YES contract.
  - 20-snapshot retention cap.
  - Doubleheader isolation: two registry entries with the same team pair
    but different scheduled times must never contaminate each other's
    snapshots/timestamps.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)


@pytest.fixture
def m():
    for name in list(sys.modules):
        if name == "capture_pregame_closing_lines":
            del sys.modules[name]
    import capture_pregame_closing_lines as mod
    return mod


def make_registry_entry(date="2026-07-30", time_str="2140", away="BOS", home="ATH",
                         markets=None, closing_snapshots=None):
    return {
        "kalshi_key": f"{away}{home}",
        "date": date,
        "time_str": time_str,
        "event_ticker_suffix": f"26JUL30{time_str}{away}{home}",
        "away": away,
        "home": home,
        "markets": markets or {
            "moneyline": {
                "away_ticker": f"KXMLBGAME-X{time_str}-{away}",
                "home_ticker": f"KXMLBGAME-X{time_str}-{home}",
            },
        },
        "closing_snapshots": closing_snapshots or [],
    }


def write_registry(path, entries):
    with open(path, "w") as f:
        json.dump({"registry": entries}, f)


def fake_fetcher_factory(price_by_ticker=None, default_mid=0.5):
    price_by_ticker = price_by_ticker or {}

    def fetcher(ticker):
        if ticker in price_by_ticker:
            bid, ask = price_by_ticker[ticker]
        else:
            bid, ask = default_mid - 0.01, default_mid + 0.01
        return {"yes_bid": bid, "yes_ask": ask, "last_price": (bid + ask) / 2,
                "volume": 100, "status": "active"}
    return fetcher


class TestEtToUtcConversion:

    def test_summer_edt_offset(self, m):
        # 9:40 PM ET in July is EDT (UTC-4) -> 01:40 UTC next day.
        dt = m.parse_scheduled_start_utc("2026-07-30", "2140")
        assert dt == datetime(2026, 7, 31, 1, 40, tzinfo=timezone.utc)

    def test_winter_est_offset(self, m):
        # 8:10 PM ET in January is EST (UTC-5) -> 01:10 UTC next day.
        dt = m.parse_scheduled_start_utc("2026-01-30", "2010")
        assert dt == datetime(2026, 1, 31, 1, 10, tzinfo=timezone.utc)

    def test_dst_spring_forward_boundary(self, m):
        # 2026-03-08 02:00 ET is the DST spring-forward instant (US).
        # A game time just after the transition (e.g. 7:00 PM ET, which is
        # already EDT) must produce a UTC offset of -4, not -5.
        dt = m.parse_scheduled_start_utc("2026-03-08", "1900")
        assert dt == datetime(2026, 3, 8, 23, 0, tzinfo=timezone.utc)

    def test_dst_fall_back_boundary(self, m):
        # 2026-11-01 is the fall-back Sunday (US). A game the day before
        # (Oct 31, still EDT, UTC-4) and a game a few days after (EST,
        # UTC-5) at the same wall-clock time must land on a UTC hour one
        # later for the EST game, since EST is one hour further behind UTC.
        before = m.parse_scheduled_start_utc("2026-10-31", "1900")  # EDT, UTC-4
        after = m.parse_scheduled_start_utc("2026-11-04", "1900")   # EST, UTC-5
        assert before == datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc)
        assert after == datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc)

    def test_unparseable_returns_none(self, m):
        assert m.parse_scheduled_start_utc("", "") is None
        assert m.parse_scheduled_start_utc("2026-07-30", "") is None
        assert m.parse_scheduled_start_utc(None, "2010") is None


class TestCaptureWindow:

    def test_in_window_just_before_first_pitch_is_fetched(self, m, tmp_path):
        reg_path = str(tmp_path / "registry.json")
        write_registry(reg_path, {"BOSATH": make_registry_entry()})
        now = datetime(2026, 7, 31, 1, 30, tzinfo=timezone.utc)  # 10 min before 01:40
        result = m.run(date_str="2026-07-30", now_utc=now, registry_path=reg_path,
                        fetcher=fake_fetcher_factory(), clv_dir=str(tmp_path / "clv"))
        assert result["games_in_window"] == 1
        assert result["games"][0]["fetched_this_run"] is True

    def test_outside_window_before_is_noop(self, m, tmp_path):
        reg_path = str(tmp_path / "registry.json")
        write_registry(reg_path, {"BOSATH": make_registry_entry()})
        now = datetime(2026, 7, 30, 22, 0, tzinfo=timezone.utc)  # hours before first pitch
        result = m.run(date_str="2026-07-30", now_utc=now, registry_path=reg_path,
                        fetcher=fake_fetcher_factory(), clv_dir=str(tmp_path / "clv"))
        assert result["games_in_window"] == 0
        assert result["registry_changed"] is False
        # No-op: no log file written, registry untouched.
        assert not os.path.exists(str(tmp_path / "clv"))
        with open(reg_path) as f:
            reg = json.load(f)
        assert reg["registry"]["BOSATH"]["closing_snapshots"] == []

    def test_outside_window_after_is_noop_for_fetch(self, m, tmp_path):
        reg_path = str(tmp_path / "registry.json")
        write_registry(reg_path, {"BOSATH": make_registry_entry()})
        now = datetime(2026, 7, 31, 3, 0, tzinfo=timezone.utc)  # well after first pitch + 5min
        result = m.run(date_str="2026-07-30", now_utc=now, registry_path=reg_path,
                        fetcher=fake_fetcher_factory(), clv_dir=str(tmp_path / "clv"))
        assert result["games_in_window"] == 0
        game = result["games"][0]
        assert game["fetched_this_run"] is False
        # Window closed with zero snapshots ever captured -> MISSED.
        assert game["closing_capture_status"] == "MISSED"

    def test_no_op_run_does_not_write_registry_or_log(self, m, tmp_path):
        reg_path = str(tmp_path / "registry.json")
        write_registry(reg_path, {"BOSATH": make_registry_entry()})
        clv_dir = str(tmp_path / "clv")
        before_mtime = os.path.getmtime(reg_path)
        now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)  # way before window
        m.run(date_str="2026-07-30", now_utc=now, registry_path=reg_path,
              fetcher=fake_fetcher_factory(), clv_dir=clv_dir)
        assert os.path.getmtime(reg_path) == before_mtime
        assert not os.path.exists(clv_dir)


class TestSnapshotClassificationAndOfficialLine:

    def test_pre_start_snapshot_captured_and_promoted_official(self, m, tmp_path):
        reg_path = str(tmp_path / "registry.json")
        write_registry(reg_path, {"BOSATH": make_registry_entry()})
        now = datetime(2026, 7, 31, 1, 35, tzinfo=timezone.utc)  # 5 min before first pitch
        m.run(date_str="2026-07-30", now_utc=now, registry_path=reg_path,
              fetcher=fake_fetcher_factory(), clv_dir=str(tmp_path / "clv"))
        with open(reg_path) as f:
            reg = json.load(f)
        entry = reg["registry"]["BOSATH"]
        assert entry["closing_capture_status"] == "CAPTURED_PRE_START"
        assert entry["official_closing_snapshot"]["capture_timing"] == "PRE_START"

    def test_late_snapshot_never_becomes_official(self, m, tmp_path):
        reg_path = str(tmp_path / "registry.json")
        write_registry(reg_path, {"BOSATH": make_registry_entry()})
        # 2 min AFTER first pitch (01:40), still inside the +5min tail window.
        now = datetime(2026, 7, 31, 1, 42, tzinfo=timezone.utc)
        m.run(date_str="2026-07-30", now_utc=now, registry_path=reg_path,
              fetcher=fake_fetcher_factory(), clv_dir=str(tmp_path / "clv"))
        with open(reg_path) as f:
            reg = json.load(f)
        entry = reg["registry"]["BOSATH"]
        assert entry["closing_snapshots"][-1]["capture_timing"] == "LATE"
        assert entry["closing_capture_status"] == "LATE_ONLY"
        assert "official_closing_snapshot" not in entry

    def test_closest_pre_start_snapshot_wins_as_official(self, m, tmp_path):
        reg_path = str(tmp_path / "registry.json")
        write_registry(reg_path, {"BOSATH": make_registry_entry()})
        fetcher = fake_fetcher_factory()
        # First run: 11 min before first pitch.
        m.run(date_str="2026-07-30", now_utc=datetime(2026, 7, 31, 1, 29, tzinfo=timezone.utc),
              registry_path=reg_path, fetcher=fetcher, clv_dir=str(tmp_path / "clv"))
        # Second run: 3 min before first pitch (closer).
        m.run(date_str="2026-07-30", now_utc=datetime(2026, 7, 31, 1, 37, tzinfo=timezone.utc),
              registry_path=reg_path, fetcher=fetcher, clv_dir=str(tmp_path / "clv"))
        with open(reg_path) as f:
            reg = json.load(f)
        entry = reg["registry"]["BOSATH"]
        assert len(entry["closing_snapshots"]) == 2
        official = entry["official_closing_snapshot"]
        # Closest to first pitch = the -3min snapshot, not the -11min one.
        assert official["minutes_to_start"] == pytest.approx(3.0, abs=0.01)

    def test_snapshot_retention_capped_at_20(self, m, tmp_path):
        reg_path = str(tmp_path / "registry.json")
        write_registry(reg_path, {"BOSATH": make_registry_entry()})
        fetcher = fake_fetcher_factory()
        # Simulate 25 in-window runs (every ~30s across the 17-min window
        # would exceed 20; we just call run() 25 times directly).
        base = datetime(2026, 7, 31, 1, 28, tzinfo=timezone.utc)
        for i in range(25):
            now = base + timedelta(seconds=i * 30)
            if now > datetime(2026, 7, 31, 1, 45, tzinfo=timezone.utc):
                break
            m.run(date_str="2026-07-30", now_utc=now, registry_path=reg_path,
                  fetcher=fetcher, clv_dir=str(tmp_path / "clv"))
        with open(reg_path) as f:
            reg = json.load(f)
        assert len(reg["registry"]["BOSATH"]["closing_snapshots"]) <= 20


class TestNrfiInversion:

    def test_nrfi_derived_from_yrfi_yes_contract(self, m, tmp_path):
        reg_path = str(tmp_path / "registry.json")
        markets = {"rfi": {"ticker": "KXMLBRFI-X"}}
        write_registry(reg_path, {"BOSATH": make_registry_entry(markets=markets)})

        def fetcher(ticker):
            assert ticker == "KXMLBRFI-X"
            return {"yes_bid": 0.40, "yes_ask": 0.42, "last_price": 0.41, "volume": 5, "status": "active"}

        now = datetime(2026, 7, 31, 1, 35, tzinfo=timezone.utc)
        m.run(date_str="2026-07-30", now_utc=now, registry_path=reg_path,
              fetcher=fetcher, clv_dir=str(tmp_path / "clv"))
        with open(reg_path) as f:
            reg = json.load(f)
        snap = reg["registry"]["BOSATH"]["official_closing_snapshot"]
        yrfi = snap["prices"]["rfi"]["yrfi"]
        nrfi = snap["prices"]["rfi"]["nrfi"]
        assert yrfi["mid"] == pytest.approx(0.41, abs=0.001)
        # NRFI is the inverse of YRFI: bid/ask flip and swap.
        assert nrfi["yes_bid"] == pytest.approx(1 - yrfi["yes_ask"], abs=0.001)
        assert nrfi["yes_ask"] == pytest.approx(1 - yrfi["yes_bid"], abs=0.001)
        assert nrfi["mid"] == pytest.approx(1 - yrfi["mid"], abs=0.001)
        assert nrfi["side"] == "NO"
        assert yrfi["side"] == "YES"


class TestDoubleheaderIsolation:

    def test_two_entries_same_teams_different_times_isolated(self, m, tmp_path):
        """
        A doubleheader: two games between the same two teams on the same
        date, at different scheduled times, stored under distinct registry
        keys (as they must be to coexist at all). Capturing one must never
        write into, or borrow the schedule of, the other.
        """
        reg_path = str(tmp_path / "registry.json")
        g1 = make_registry_entry(time_str="1600", away="BOS", home="NYY")
        g1["kalshi_key"] = "BOSNYY_G1"
        g2 = make_registry_entry(time_str="1930", away="BOS", home="NYY")
        g2["kalshi_key"] = "BOSNYY_G2"
        write_registry(reg_path, {"BOSNYY_G1": g1, "BOSNYY_G2": g2})

        # Only game 1's window is active right now (16:00 ET game).
        now = m.parse_scheduled_start_utc("2026-07-30", "1600") - timedelta(minutes=5)
        result = m.run(date_str="2026-07-30", now_utc=now, registry_path=reg_path,
                        fetcher=fake_fetcher_factory(), clv_dir=str(tmp_path / "clv"))

        by_key = {g["kalshi_key"]: g for g in result["games"]}
        assert by_key["BOSNYY_G1"]["fetched_this_run"] is True
        assert by_key["BOSNYY_G2"]["fetched_this_run"] is False
        assert by_key["BOSNYY_G1"]["scheduled_start_ts"] != by_key["BOSNYY_G2"]["scheduled_start_ts"]

        with open(reg_path) as f:
            reg = json.load(f)
        assert len(reg["registry"]["BOSNYY_G1"]["closing_snapshots"]) == 1
        assert len(reg["registry"]["BOSNYY_G2"]["closing_snapshots"]) == 0
