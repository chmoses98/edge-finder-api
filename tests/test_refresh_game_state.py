"""scripts/refresh_game_state.py: the game-state document is the schedule feed's own words, never
invented; a failed or empty fetch leaves the previous document in place."""
import importlib.util
import json
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SPEC = importlib.util.spec_from_file_location("refresh_game_state", os.path.join(ROOT, "scripts", "refresh_game_state.py"))
rgs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rgs)

SCHEDULE = {"dates": [{"date": "2026-10-08", "games": [
    {"gamePk": 849832, "gameDate": "2026-10-09T00:00:00Z", "gameType": "F", "gameNumber": 1,
     "status": {"abstractGameState": "Final", "detailedState": "Final", "codedGameState": "F"}},
    {"gamePk": 849833, "gameDate": "2026-10-09T02:00:00Z", "gameType": "F", "gameNumber": 1,
     "status": {"abstractGameState": "Live", "detailedState": "In Progress", "codedGameState": "I"}},
    {"gameDate": "2026-10-09T03:00:00Z", "status": {"abstractGameState": "Preview"}},  # no gamePk: skipped
]}]}


def test_document_is_the_feeds_own_words():
    doc = rgs.game_state_from_schedule(SCHEDULE, date="2026-10-08", as_of_iso="2026-10-09T03:30:00Z")
    assert doc["source"] == "statsapi.mlb.com/api/v1/schedule"
    assert doc["as_of"] == "2026-10-09T03:30:00Z"
    assert set(doc["games"]) == {"849832", "849833"}
    assert doc["games"]["849832"]["abstract_game_state"] == "Final"
    assert doc["games"]["849833"]["detailed_state"] == "In Progress"
    assert doc["games"]["849833"]["start_time_utc"] == "2026-10-09T02:00:00Z"


def test_failed_or_empty_fetch_writes_nothing(tmp_path, monkeypatch):
    root = tmp_path / "data"
    prev = root / "slates" / "2026-10-08" / "game_state.json"
    prev.parent.mkdir(parents=True)
    prev.write_text('{"games": {"1": {"abstract_game_state": "Live"}}}')
    monkeypatch.setattr(rgs.mlb_schedule, "fetch_schedule_all_game_types", lambda date, timeout=15: None)
    assert rgs.main(["--date", "2026-10-08", "--data-root", str(root), "--now", "2026-10-09T03:30:00Z"]) == 0
    assert json.loads(prev.read_text())["games"]["1"]["abstract_game_state"] == "Live"
    monkeypatch.setattr(rgs.mlb_schedule, "fetch_schedule_all_game_types", lambda date, timeout=15: {"dates": []})
    assert rgs.main(["--date", "2026-10-08", "--data-root", str(root), "--now", "2026-10-09T03:30:00Z"]) == 0
    assert json.loads(prev.read_text())["games"]["1"]["abstract_game_state"] == "Live"


def test_successful_fetch_writes_the_document(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setattr(rgs.mlb_schedule, "fetch_schedule_all_game_types", lambda date, timeout=15: SCHEDULE)
    assert rgs.main(["--date", "2026-10-08", "--data-root", str(root), "--now", "2026-10-09T03:30:00Z"]) == 0
    doc = json.loads((root / "slates" / "2026-10-08" / "game_state.json").read_text())
    assert doc["date"] == "2026-10-08" and doc["games"]["849832"]["abstract_game_state"] == "Final"
