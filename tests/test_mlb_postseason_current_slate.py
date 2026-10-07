#!/usr/bin/env python3
"""
tests/test_mlb_postseason_current_slate.py
==========================================
Regression tests for the 2026-10-07 MLB postseason P0: app/latest published 809
markets, 0 events and 0 model prices with four playoff games on Kalshi.

Root causes covered here:
  * no slate for the day (Fetch Slate Data is cron-only and GitHub fired it late /
    not at all) -> the Kalshi-discovered games carry no MLB gamePk, so every market
    was exported without an event. The export now says so explicitly, and
    scripts/ci/pipeline_watchdog.py decides when the slate chain must be re-armed.
  * regular-season-only (gameType=R) schedule calls that hid every October game from
    the standalone pregame context, the Statcast catch-up and bullpen recent usage.
  * a slate status snapshot ("Pre-Game", "Warmup") outliving first pitch, which kept a
    started playoff game exportable as a pregame candidate.
All tests write only under tmp_path.
"""
import copy
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "contract")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


base = _load("_app_contract_fixture", "tests/test_app_contract_v1.py")
app_export = base.app_export
watchdog = _load("pipeline_watchdog", "scripts/ci/pipeline_watchdog.py")

DATE = base.DATE                    # 2026-10-01; first pitch 2026-10-02T00:00:00Z
BEFORE_FIRST_PITCH = "2026-10-01T23:00:00Z"
AFTER_FIRST_PITCH = "2026-10-02T00:20:00Z"

# A Division Series game exactly as /api/slate now passes it through (gameType "D").
POSTSEASON_GAME_FIELDS = {"gameType": "D", "seriesDescription": "Division Series", "seriesGameNumber": 3,
                          "gamesInSeries": 5, "status": "Warmup"}


def _postseason_root(tmp_path, **overrides):
    root = base.make_data_root(tmp_path / "data")
    slate = copy.deepcopy(base.SLATE)
    slate["games"][0].update(POSTSEASON_GAME_FIELDS)
    slate["games"][0].update(overrides)
    base._write_json(os.path.join(root, "slates", DATE, "authoritative.json"), slate)
    base._write_json(os.path.join(root, "slate.json"), slate)
    # the shared fixture settles the game (MLB "Final" evidence); these scenarios are pregame/live
    base._write_jsonl(os.path.join(root, "edgelab", "settlements", f"{DATE}.jsonl"), [])
    return root


def _export(root, out, now):
    assert base.run_export(root, out, now=now, extra=["--date", DATE]) == 0
    return {name: base._load(out, f"{name}.json") for name in
            ("manifest", "events", "board", "model_prices", "recommendations", "markets")}


# ---------------------------------------------------------------- postseason flows through

def test_postseason_game_survives_ingestion_and_exports_events_board_and_prices(tmp_path):
    docs = _export(_postseason_root(tmp_path), tmp_path / "out", BEFORE_FIRST_PITCH)
    events = docs["events"]["items"]
    assert len(events) == 1
    ev = events[0]
    assert ev["status"] == "SCHEDULED"            # "Warmup" is pregame
    assert ev["extensions"]["game_type"] == "D"    # postseason, never assumed regular season
    assert ev["extensions"]["series_description"] == "Division Series"
    assert ev["extensions"]["status_basis"] is None
    assert docs["board"]["count"] == 1
    assert docs["manifest"]["counts"]["model_prices"] > 0
    assert not any("no model price" in w for w in docs["manifest"]["warnings"])
    # explorer-facing event detail exists for the game
    assert os.path.exists(tmp_path / "out" / "event_detail" / f"{ev['event_id']}.json")


def test_pregame_recommendation_stays_actionable_before_first_pitch(tmp_path):
    docs = _export(_postseason_root(tmp_path), tmp_path / "out", BEFORE_FIRST_PITCH)
    recs = {r["market_id"]: r for r in docs["recommendations"]["items"]}
    placed = recs["mkt_kalshi_KXMLBTEAMTOTAL-26OCT011400PHIATL-ATL4"]
    assert placed["status"] == "RECOMMENDED" and placed["reason_not_playable"] is None


# ---------------------------------------------------------------- started-game gating

def test_started_game_is_live_and_never_a_pregame_candidate(tmp_path):
    docs = _export(_postseason_root(tmp_path), tmp_path / "out", AFTER_FIRST_PITCH)
    ev = docs["events"]["items"][0]
    assert ev["status"] == "LIVE"
    assert ev["extensions"]["status_basis"] == "FIRST_PITCH_PASSED"
    assert ev["extensions"]["slate_status_raw"] == "Warmup"
    for r in docs["recommendations"]["items"]:
        assert r["status"] not in ("RECOMMENDED", "RESEARCH_CANDIDATE", "WATCH"), r
    placed = {r["market_id"]: r for r in docs["recommendations"]["items"]}["mkt_kalshi_KXMLBTEAMTOTAL-26OCT011400PHIATL-ATL4"]
    assert placed["status"] == "EXPIRED"
    assert placed["reason_not_playable"].startswith("GAME_STARTED")
    assert placed["extensions"]["native_status"] == "BET_PLACED"
    # board rows carry the live status, so no consumer can read it as pregame
    assert docs["board"]["items"][0]["status"] == "LIVE"


def test_postponed_game_fails_safe(tmp_path):
    docs = _export(_postseason_root(tmp_path, status="Postponed"), tmp_path / "out", BEFORE_FIRST_PITCH)
    assert docs["events"]["items"][0]["status"] == "POSTPONED"
    placed = {r["market_id"]: r for r in docs["recommendations"]["items"]}["mkt_kalshi_KXMLBTEAMTOTAL-26OCT011400PHIATL-ATL4"]
    assert placed["status"] == "NOT_PLAYABLE"
    assert placed["reason_not_playable"].startswith("EVENT_POSTPONED")


def test_final_status_is_never_overridden_by_the_time_gate():
    assert app_export._time_gate("FINAL", "2026-10-02T00:00:00Z", "SCHEDULED", AFTER_FIRST_PITCH) == ("FINAL", None)
    assert app_export._time_gate("POSTPONED", "2026-10-02T00:00:00Z", "SCHEDULED", AFTER_FIRST_PITCH) == ("POSTPONED", None)
    assert app_export._time_gate("SCHEDULED", "2026-10-02T00:00:00Z", "PLACEHOLDER", AFTER_FIRST_PITCH) == ("SCHEDULED", None)


# ---------------------------------------------------------------- missing slate (the 10-07 shape)

def test_missing_slate_preserves_market_inventory_and_says_why(tmp_path):
    root = base.make_data_root(tmp_path / "data")
    os.remove(os.path.join(root, "slates", DATE, "authoritative.json"))
    os.remove(os.path.join(root, "slate.json"))
    # Kalshi-discovered rows before schedule reconciliation: no mlbGamePk (2026-10-07 shape)
    games = [dict(g, mlbGamePk=None, supersededBy=None, gameId="2026-10-01_PHI_ATL_1400") for g in base.GAMES]
    base._write_jsonl(os.path.join(root, "edgelab", "games", f"{DATE}.jsonl"), games)
    docs = _export(root, tmp_path / "out", BEFORE_FIRST_PITCH)
    assert docs["manifest"]["counts"]["events"] == 0
    assert docs["manifest"]["counts"]["markets"] >= len(base.MARKETS)   # complete inventory kept
    assert any("no published slate for 2026-10-01" in w and "1 Kalshi-discovered game" in w
               for w in docs["manifest"]["warnings"])


# ---------------------------------------------------------------- regular-season-only filters

@pytest.mark.parametrize("rel,attr", [
    ("scripts/fetch_standalone_pregame_context.py", "MLB_SCHEDULE_URL"),
    ("scripts/statcast_completed_game_catchup.py", "MLB_SCHEDULE_URL"),
])
def test_schedule_fetchers_include_postseason_game_types(rel, attr):
    mod = _load(os.path.basename(rel)[:-3], rel)
    url = getattr(mod, attr)
    assert "gameType=R," in url
    for code in ("F", "D", "L", "W"):
        assert code in url.split("gameType=")[1].split("&")[0].split(",")


def test_bullpen_recent_usage_includes_postseason_games(monkeypatch):
    from lib.edgelab import bullpen_usage
    seen = {}
    monkeypatch.setattr(bullpen_usage, "_fetch_json", lambda url, timeout: seen.setdefault("url", url) and None)
    bullpen_usage.fetch_team_recent_schedule(144, "2026-10-04", "2026-10-06")
    assert "gameType=R,F,D,L,W" in seen["url"]


def test_slate_api_passes_game_type_through():
    with open(os.path.join(ROOT, "api", "slate.js"), encoding="utf-8") as fh:
        src = fh.read()
    for field in ("gameType:", "seriesDescription:", "seriesGameNumber:", "gamesInSeries:"):
        assert field in src


# ---------------------------------------------------------------- pipeline watchdog

NOW_1945 = datetime(2026, 10, 7, 19, 45, tzinfo=timezone.utc)


def _state(**kw):
    s = {"today": "2026-10-07", "kalshi_game_count": 4, "market_count": 663, "slate_exists": False,
         "latest_observation_at": datetime(2026, 10, 7, 10, 46, tzinfo=timezone.utc),
         "latest_model_evaluation_at": None}
    s.update(kw)
    return s


def test_watchdog_rearms_the_missing_postseason_slate_unattended():
    out = watchdog.decide(_state(), {}, NOW_1945)
    slate = [d for d in out["dispatch"] if d["workflow"] == "fetch-slate.yml"]
    assert slate and slate[0]["inputs"] == {"date": "2026-10-07", "unattended": "true"}
    assert any(d["workflow"] == "capture-snapshots-scheduled.yml" for d in out["dispatch"])


def test_watchdog_never_stacks_on_an_in_flight_or_recent_run():
    runs = {"fetch-slate.yml": [{"status": "in_progress"}],
            "capture-snapshots-scheduled.yml": [{"status": "completed", "createdAt": "2026-10-07T19:30:00Z"}]}
    out = watchdog.decide(_state(), runs, NOW_1945)
    assert out["dispatch"] == []


def test_watchdog_waits_for_starters_and_ignores_days_without_games():
    early = datetime(2026, 10, 7, 11, 0, tzinfo=timezone.utc)   # 07:00 ET
    assert not any(d["workflow"] == "fetch-slate.yml" for d in watchdog.decide(_state(), {}, early)["dispatch"])
    assert watchdog.decide(_state(kalshi_game_count=0, market_count=0), {}, NOW_1945)["dispatch"] == []


def test_watchdog_refreshes_stale_model_once_the_slate_exists():
    out = watchdog.decide(_state(slate_exists=True,
                                 latest_observation_at=datetime(2026, 10, 7, 19, 40, tzinfo=timezone.utc)), {}, NOW_1945)
    assert [d["workflow"] for d in out["dispatch"]] == ["model-snapshot-scheduler.yml"]


def test_watchdog_reads_the_real_shape_of_a_day_without_slate(tmp_path):
    root = tmp_path / "data"
    base._write_jsonl(str(root / "edgelab" / "games" / "2026-10-07.jsonl"),
                      [{"awayTeam": "LAD", "homeTeam": "ATL", "mlbGamePk": None},
                       {"awayTeam": "TB", "homeTeam": "NYY", "mlbGamePk": None}])
    base._write_jsonl(str(root / "edgelab" / "markets" / "2026-10-07.jsonl"), [{"marketTicker": "X"}] * 3)
    state = watchdog.read_state(str(root), "2026-10-07")
    assert state["kalshi_game_count"] == 2 and state["market_count"] == 3 and state["slate_exists"] is False
    assert json.loads(json.dumps(watchdog.decide(state, {}, NOW_1945), default=str))["dispatch"][0]["workflow"] == "fetch-slate.yml"
