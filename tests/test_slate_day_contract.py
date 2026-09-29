#!/usr/bin/env python3
"""
tests/test_slate_day_contract.py
================================
The off-day contract (lib/edgelab/slate_day_contract.py) and the post-fetch
gate's use of it.

REAL FAILURE SHAPE. 2026-09-28 was an MLB off-day between the regular season
and the Wild Card round. data/slate.json was {"date": "2026-09-28", "games": [],
"scheduleSource": "none", "kalshiMarketsFound": 0}; every Kalshi registry
snapshot that day held 0 markets; and post_fetch_gate.py recorded
FAILED_STALE_DATE "slate.json has no games" -- exactly what it records when the
schedule fetch is dead. Nothing could tell the two apart.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from lib.edgelab import slate_day_contract as C  # noqa: E402
import post_fetch_gate as G  # noqa: E402

GATE_PATH = os.path.join(ROOT, "scripts", "post_fetch_gate.py")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "kalshi_snapshots")

# The committed 2026-09-28 slate, verbatim in shape.
SEP28_SLATE = {"date": "2026-09-28", "kalshiDate": "26SEP28", "scheduleSource": "none",
               "games": [], "kalshiMarketsFound": 0}
# MLB Stats API answer for a date with no games: an empty `dates` list.
NO_GAMES_RESPONSE = {"copyright": "x", "totalItems": 0, "totalEvents": 0, "totalGames": 0,
                     "totalGamesInProgress": 0, "dates": []}


def _game(pk, state="Scheduled", game_type="R"):
    return {"gamePk": pk, "gameType": game_type, "status": {"detailedState": state}}


def schedule_response(date, *games):
    return {"totalGames": len(games), "dates": [{"date": date, "games": list(games)}]}


def evidence(date, response):
    return C.schedule_evidence(date, response, fetched_at="2026-09-29T01:23:00Z")


def game_day_slate(date="2026-09-27", n=2):
    return {"date": date, "games": [{"gameId": i} for i in range(n)]}


# ── schedule evidence ────────────────────────────────────────────────────────

def test_a_genuine_no_games_answer_is_no_games_scheduled():
    ev = evidence("2026-09-28", NO_GAMES_RESPONSE)
    assert ev["status"] == C.NO_GAMES_SCHEDULED
    assert ev["scheduledGames"] == 0 and ev["playableGames"] == 0


def test_a_postseason_date_is_a_game_day():
    """gameType=R alone would call a Wild Card date an off-day."""
    ev = evidence("2026-09-29", schedule_response("2026-09-29", _game(1, game_type="F")))
    assert ev["status"] == C.GAMES_SCHEDULED


def test_a_date_whose_every_game_is_postponed_has_nothing_to_play():
    ev = evidence("2026-07-01", schedule_response("2026-07-01", _game(1, "Postponed"),
                                                   _game(2, "Cancelled")))
    assert ev["status"] == C.NO_GAMES_SCHEDULED
    assert ev["scheduledGames"] == 2 and ev["playableGames"] == 0


@pytest.mark.parametrize("response", [None, {}, {"dates": None}, "not json", {"totalGames": 0}])
def test_a_failed_or_malformed_schedule_is_unknown_never_an_off_day(response):
    assert evidence("2026-09-28", response)["status"] == C.SCHEDULE_UNKNOWN


def test_games_for_another_date_in_the_response_do_not_count():
    ev = evidence("2026-09-28", schedule_response("2026-09-27", _game(1)))
    assert ev["status"] == C.NO_GAMES_SCHEDULED


# ── CASE A: games scheduled ──────────────────────────────────────────────────

def test_1_normal_game_day_with_games_passes():
    ev = evidence("2026-09-27", schedule_response("2026-09-27", _game(1), _game(2)))
    day = C.classify_slate_day(game_day_slate(), ev)
    assert day["verdict"] == C.GAME_DAY_COLLECTED and day["ok"]


def test_2_normal_game_day_with_nothing_collected_fails():
    ev = evidence("2026-09-27", schedule_response("2026-09-27", _game(1), _game(2)))
    day = C.classify_slate_day({"date": "2026-09-27", "games": []}, ev)
    assert day["verdict"] == C.GAME_DAY_COLLECTION_FAILED and not day["ok"]


def test_2b_normal_game_day_with_markets_missing_fails():
    ev = evidence("2026-09-26", schedule_response("2026-09-26", _game(1)))
    empty = {"date": "2026-09-26", "markets": []}
    assert C.classify_snapshot_day(empty, ev)["verdict"] == C.GAME_DAY_COLLECTION_FAILED
    with open(os.path.join(FIXTURES, "kalshi_search_2026-09-26_game_day.json")) as f:
        real = json.load(f)
    assert C.classify_snapshot_day(real, ev)["verdict"] == C.GAME_DAY_COLLECTED


# ── CASE B: no games scheduled ───────────────────────────────────────────────

def test_3_off_day_with_zero_games_and_zero_markets_passes():
    ev = evidence("2026-09-28", NO_GAMES_RESPONSE)
    assert C.classify_slate_day(SEP28_SLATE, ev)["verdict"] == C.OFF_DAY
    with open(os.path.join(FIXTURES, "kalshi_search_2026-09-28_off_day.json")) as f:
        snapshot = json.load(f)
    day = C.classify_snapshot_day(snapshot, ev)
    assert day["verdict"] == C.OFF_DAY and day["ok"]


@pytest.mark.parametrize("artifact, verdict", [
    ({"date": "2026-09-28"}, C.MALFORMED_ARTIFACT),                  # no games list
    ({"date": "2026-09-28", "games": None}, C.MALFORMED_ARTIFACT),
    ({"games": []}, C.MALFORMED_ARTIFACT),                            # no date
    ({"date": "2026-09-27", "games": []}, C.EVIDENCE_DATE_MISMATCH),  # stale slate
    ({"date": "2026-09-28", "games": [{"gameId": 1}]}, C.OFF_DAY_INCONSISTENT),
])
def test_4_off_day_with_malformed_stale_or_contradictory_artifacts_fails(artifact, verdict):
    day = C.classify_slate_day(artifact, evidence("2026-09-28", NO_GAMES_RESPONSE))
    assert day["verdict"] == verdict and not day["ok"]


def test_4b_a_stale_empty_snapshot_on_an_off_day_fails():
    stale = {"date": "2026-09-27", "markets": []}
    day = C.classify_snapshot_day(stale, evidence("2026-09-28", NO_GAMES_RESPONSE))
    assert day["verdict"] == C.EVIDENCE_DATE_MISMATCH and not day["ok"]


# ── CASE C: schedule unknown ─────────────────────────────────────────────────

@pytest.mark.parametrize("ev", [None, {}, evidence("2026-09-28", None)])
def test_5_unknown_schedule_is_never_silently_an_off_day(ev):
    day = C.classify_slate_day(SEP28_SLATE, ev)
    assert day["verdict"] == C.SCHEDULE_UNKNOWN and not day["ok"]


# ── the gate ─────────────────────────────────────────────────────────────────

def test_6_the_september_28_shape_resolves_to_no_games_scheduled():
    status, reason, ev, day = G.resolve_empty_slate(SEP28_SLATE, "2026-09-28", NO_GAMES_RESPONSE)
    assert status == "NO_GAMES_SCHEDULED"
    assert day["verdict"] == C.OFF_DAY
    assert ev["status"] == C.NO_GAMES_SCHEDULED and ev["date"] == "2026-09-28"


def test_an_empty_slate_on_a_scheduled_day_is_still_a_collection_failure():
    status, reason, _, day = G.resolve_empty_slate(
        SEP28_SLATE, "2026-09-28", schedule_response("2026-09-28", _game(1)))
    assert status == "FAILED_STALE_DATE"
    assert day["verdict"] == C.GAME_DAY_COLLECTION_FAILED
    assert "1 playable game" in reason


def test_an_empty_slate_with_no_schedule_evidence_fails_closed():
    status, reason, _, day = G.resolve_empty_slate(SEP28_SLATE, "2026-09-28", None)
    assert status == "FAILED_STALE_DATE"
    assert day["verdict"] == C.SCHEDULE_UNKNOWN


class TestGateEndToEnd:
    """main(), as the workflow runs it, with the schedule supplied by file."""

    def _run(self, tmp_path, slate, response, date="2026-09-28"):
        data = tmp_path / "data"
        data.mkdir()
        (data / "slate.json").write_text(json.dumps(slate))
        env = dict(os.environ)
        if response == "offline":
            env[G.SCHEDULE_EVIDENCE_ENV] = "offline"
        else:
            (tmp_path / "schedule.json").write_text(json.dumps(response))
            env[G.SCHEDULE_EVIDENCE_ENV] = str(tmp_path / "schedule.json")
        result = subprocess.run([sys.executable, GATE_PATH, date], cwd=tmp_path,
                                env=env, capture_output=True, text=True)
        status = json.loads((data / "fetch_status.json").read_text())
        return result, status

    def test_7_off_day_is_recorded_as_one_and_still_halts_downstream(self, tmp_path):
        """Fails against the pre-contract gate, which wrote FAILED_STALE_DATE."""
        result, status = self._run(tmp_path, SEP28_SLATE, NO_GAMES_RESPONSE)
        assert status["status"] == "NO_GAMES_SCHEDULED"
        assert status["scheduleEvidence"]["status"] == C.NO_GAMES_SCHEDULED
        assert "NO GAMES SCHEDULED" in result.stderr
        # Still not "OK": stale_date_guard.py and validate_current_slate_date.py
        # refuse anything else, and no downstream stage runs on an empty slate.
        assert result.returncode == 1

    def test_a_scheduled_day_with_an_empty_slate_is_still_failed_stale_date(self, tmp_path):
        result, status = self._run(tmp_path, SEP28_SLATE,
                                   schedule_response("2026-09-28", _game(1), _game(2)))
        assert status["status"] == "FAILED_STALE_DATE"
        assert status["actualDate"] == "no-games"
        assert status["scheduleEvidence"]["playableGames"] == 2
        assert "STALE SLATE ABORT" in result.stderr and result.returncode == 1

    def test_an_unreachable_schedule_is_failed_stale_date(self, tmp_path):
        result, status = self._run(tmp_path, SEP28_SLATE, "offline")
        assert status["status"] == "FAILED_STALE_DATE"
        assert status["scheduleEvidence"]["status"] == C.SCHEDULE_UNKNOWN
        assert result.returncode == 1

    def test_a_non_empty_slate_never_consults_the_schedule(self, tmp_path):
        """The contract only answers what an EMPTY slate means; a real slate's
        status file is unchanged (no scheduleEvidence key)."""
        slate = {"date": "2026-09-28", "games": [{
            "away": {"abbr": "NYY", "pitcher": {"name": "A", "id": "1"},
                     "pitcherSavant": {"xFIP": 3.8, "seasonFIP": 3.9}},
            "home": {"abbr": "BOS", "pitcher": {"name": "B", "id": "2"},
                     "pitcherSavant": {"xFIP": 4.0, "seasonFIP": 4.1}},
            "awayTeamStats": {"lineupConfirmed": True, "last7RpG": 4.2, "last15RpG": 4.3,
                              "runsPerGame": 4.1},
            "homeTeamStats": {"lineupConfirmed": True, "last7RpG": 4.4, "last15RpG": 4.2,
                              "runsPerGame": 4.3},
            "startTime": "2026-09-28T23:05:00Z"}]}
        _, status = self._run(tmp_path, slate, NO_GAMES_RESPONSE)
        assert "scheduleEvidence" not in status


def test_the_suite_never_reaches_the_live_schedule():
    """conftest.py sets the seam to offline for every test and subprocess."""
    assert os.environ.get(G.SCHEDULE_EVIDENCE_ENV)
