#!/usr/bin/env python3
"""
Postseason / date-window regression tests for
scripts/edgelab/run_hitter_prospective_snapshots.py.

Fixtures are postseason-shaped: statsapi gameType D/L/W games, a 10 PM ET
(02:00Z next UTC day) West Coast first pitch, and Kalshi registry captures
named by the US-Eastern slate date with a UTC HHMM suffix (exactly how
data/kalshi_registry_snapshots/kalshi_search_2026-10-06_0034.json is named
while its fetched_at is 2026-10-07T00:34Z). No network.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import mlb_schedule  # noqa: E402
from lib.research.hitter_prospective_snapshot import run_hitter_prospective_snapshot_cycle  # noqa: E402
from scripts.edgelab import run_hitter_prospective_snapshots as runner  # noqa: E402

DATE = "2026-10-07"


def _capture(tmp_path, name, fetched_at):
    (tmp_path / name).write_text(json.dumps({"date": DATE, "fetched_at": fetched_at, "markets": []}))


def _postseason_schedule_json():
    """statsapi schedule-by-date shape for a Division Series day (gameType D)."""
    def g(pk, away_id, home_id, start, state):
        return {"gamePk": pk, "gameType": "D", "gameDate": start, "gameNumber": 1,
                "status": {"detailedState": state},
                "teams": {"away": {"team": {"id": away_id}}, "home": {"team": {"id": home_id}}},
                "venue": {"name": "x"}}
    return {"dates": [{"date": DATE, "games": [
        g(849822, 119, 144, "2026-10-07T22:00:00Z", "Pre-Game"),      # LAD @ ATL
        g(849827, 158, 135, "2026-10-08T02:00:00Z", "Postponed"),     # MIL @ SD
    ]}]}


def _ps_game(game_id, start, away, home, confirmed):
    lineup = [{"order": i + 1, "playerId": str(600000 + i), "name": f"P{i}", "position": "C" if i == 8 else "1B"}
              for i in range(9)] if confirmed else []
    ts = {"lineupConfirmedOfficial": confirmed, "confirmedLineup": lineup,
          "lineupStatus": "confirmed" if confirmed else "missing"}
    return {"gameId": game_id, "status": "Pre-Game", "startTime": start, "gameType": "D",
            "away": {"abbr": away, "team": away}, "home": {"abbr": home, "team": home},
            "awayTeamStats": dict(ts), "homeTeamStats": dict(ts), "park": {}}


class TestLatestCaptureAcrossUtcMidnight:
    def test_post_midnight_utc_capture_is_the_latest(self, tmp_path):
        _capture(tmp_path, f"kalshi_search_{DATE}_1953.json", "2026-10-07T19:53:35.000Z")
        _capture(tmp_path, f"kalshi_search_{DATE}_2134.json", "2026-10-07T21:34:00.000Z")
        _capture(tmp_path, f"kalshi_search_{DATE}_0034.json", "2026-10-08T00:34:51.000Z")
        got = runner.latest_dated_kalshi_snapshot(DATE, snapshot_dir=str(tmp_path))
        assert got.endswith("_0034.json"), "name order would wrongly pick _2134"

    def test_capture_after_now_is_never_used(self, tmp_path):
        _capture(tmp_path, f"kalshi_search_{DATE}_2134.json", "2026-10-07T21:34:00.000Z")
        _capture(tmp_path, f"kalshi_search_{DATE}_0034.json", "2026-10-08T00:34:51.000Z")
        got = runner.latest_dated_kalshi_snapshot(DATE, snapshot_dir=str(tmp_path), now="2026-10-08T00:10:00Z")
        assert got.endswith("_2134.json")
        assert runner.latest_dated_kalshi_snapshot(DATE, snapshot_dir=str(tmp_path), now="2026-10-07T12:00:00Z") is None

    def test_early_morning_capture_of_same_slate_date_sorts_first(self, tmp_path):
        _capture(tmp_path, f"kalshi_search_{DATE}_0414.json", "2026-10-07T04:14:00.000Z")
        _capture(tmp_path, f"kalshi_search_{DATE}_1046.json", "2026-10-07T10:46:00.000Z")
        assert runner.latest_dated_kalshi_snapshot(DATE, snapshot_dir=str(tmp_path)).endswith("_1046.json")


class TestDefaultSlateDate:
    def test_post_midnight_utc_cycle_stays_on_the_eastern_slate_date(self):
        # 01:30Z Oct 8 == 9:30 PM ET Oct 7: a 10 PM ET Division Series game is still pregame.
        assert runner.default_slate_date("2026-10-08T01:30:00Z") == "2026-10-07"

    def test_afternoon_cycle(self):
        assert runner.default_slate_date("2026-10-07T20:00:00Z") == "2026-10-07"

    def test_standard_time_world_series(self):
        # Nov 4 04:30Z == Nov 3 11:30 PM EST (after DST ends Nov 1, 2026).
        assert runner.default_slate_date("2026-11-04T04:30:00Z") == "2026-11-03"
        assert runner.default_slate_date("2026-11-04T05:30:00Z") == "2026-11-04"


class TestLiveStatusPostseason:
    def test_default_fetch_covers_postseason_game_types(self, monkeypatch):
        calls = []

        def all_types(date, timeout=15):
            calls.append(date)
            return _postseason_schedule_json()

        def regular_only(date, timeout=15):
            raise AssertionError("regular-season-only schedule fetch must not be used")

        monkeypatch.setattr(mlb_schedule, "fetch_schedule_all_game_types", all_types)
        monkeypatch.setattr(mlb_schedule, "fetch_schedule", regular_only)
        status = runner._live_status_by_team_pair(DATE)
        assert calls == [DATE]
        assert status[("LAD", "ATL")] == "Pre-Game"
        assert status[("MIL", "SD")] == "Postponed"

    def test_fetch_failure_is_never_a_hard_block(self):
        assert runner._live_status_by_team_pair(DATE, fetch_schedule_fn=lambda d: None) == {}


class TestLoadPregameContextFile:
    def test_keeps_postseason_games_including_cross_midnight_start(self, tmp_path):
        doc = {"date": DATE, "games": [
            _ps_game(849822, "2026-10-07T22:00:00Z", "LAD", "ATL", True),
            _ps_game(849827, "2026-10-08T02:00:00Z", "MIL", "SD", False),   # 10 PM ET, still Oct 7
            _ps_game(849900, "2026-10-08T22:00:00Z", "TB", "NYY", False),   # tomorrow's game
        ]}
        path = tmp_path / "authoritative.json"
        path.write_text(json.dumps(doc))
        ctx = runner.load_pregame_context_file(str(path), DATE)
        assert [g["gameId"] for g in ctx["games"]] == [849822, 849827]

    def test_rejects_non_slate_document(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"rows": []}))
        try:
            runner.load_pregame_context_file(str(path), DATE)
        except ValueError:
            return
        raise AssertionError("expected ValueError")


class TestPostseasonCycleProducesSnapshots:
    """End to end through the real cycle with a fake board builder: a
    postseason (gameType D) slate is evaluated exactly like a regular-season
    one, and a 10 PM ET game's T-90 checkpoint is reached at 00:30Z."""

    @staticmethod
    def _fake_build(**kwargs):
        with open(kwargs["slate_path"]) as fh:
            slate = json.load(fh)
        rows = [{"marketTicker": f"KXMLBHIT-26OCT07-{g['away']['abbr']}{g['home']['abbr']}-X-1",
                 "marketFamily": "hitter_hits", "threshold": 1, "modelProbability": 0.6,
                 "executableKalshiPrice": 0.55, "projectionStatus": "PROJECTED", "gameId": g["gameId"]}
                for g in slate["games"]]
        return {"date": kwargs["date_str"], "totalRows": len(rows), "rows": rows, "hitterSummaries": []}

    @staticmethod
    def _fake_write(date, run_id, checkpoint, games, market_resolution_games=None, _dir=[None]):
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, f"{checkpoint}.json")
        with open(p, "w") as fh:
            json.dump({"date": date, "games": games}, fh)
        return p

    def test_confirmed_postseason_lineup_is_captured(self):
        games = [_ps_game(849822, "2026-10-07T22:00:00Z", "LAD", "ATL", True)]
        rows, log = run_hitter_prospective_snapshot_cycle(
            DATE, games, [], now="2026-10-07T20:20:00Z",
            build_board_main_fn=self._fake_build, write_filtered_slate_fn=self._fake_write,
            kalshi_search_path="unused.json", run_id="RUN_TEST")
        assert [r["checkpoint"] for r in rows] == ["LINEUP_CONFIRMATION"]
        assert rows[0]["gameId"] == 849822

    def test_ten_pm_et_game_reaches_t_minus_90_after_utc_midnight(self):
        now = "2026-10-08T00:30:00Z"
        date = runner.default_slate_date(now)
        assert date == DATE
        games = [_ps_game(849827, "2026-10-08T02:00:00Z", "MIL", "SD", False)]
        rows, log = run_hitter_prospective_snapshot_cycle(
            date, games, [], now=now,
            build_board_main_fn=self._fake_build, write_filtered_slate_fn=self._fake_write,
            kalshi_search_path="unused.json", run_id="RUN_TEST")
        assert [r["checkpoint"] for r in rows] == ["T_MINUS_90"]
