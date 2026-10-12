"""Network-free tests for the W2 MLB pitcher PURE shadow collector
(scripts/research/mlb_pitcher_shadow.py) and its write-once store."""
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPEC = importlib.util.spec_from_file_location("mlb_pitcher_shadow", os.path.join(ROOT, "scripts", "research", "mlb_pitcher_shadow.py"))
SH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SH)
from lib.research.shadow_pure_store import ShadowStore, WriteOnceError  # noqa: E402

PID = "678022"  # a 2026 starter with box-score history in the committed research cache


def _game(kickoff, gtype="D", date="2026-09-30", lineups=(False, False), pid=PID, status=("Preview", "Scheduled")):
    return {"pk": "999001", "game_type": gtype, "official_date": date, "kickoff": kickoff,
            "abstract": status[0], "detailed": status[1], "schedule_fetched_at": "2026-09-30T15:00:00Z",
            "away": {"team_id": 133, "probable_id": pid, "probable_name": "Test Starter", "lineup_posted": lineups[0]},
            "home": {"team_id": 117, "probable_id": None, "probable_name": None, "lineup_posted": lineups[1]}}


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


# ----------------------------------------------------------------------------- windows

def test_window_morning_then_pregame_then_closed():
    k = "2026-09-30T23:08:00Z"
    g = _game(k)
    assert SH.window_for(g, _t("2026-09-30T14:00:00Z"), set())[0] == "morning"
    assert SH.window_for(g, _t("2026-09-30T14:00:00Z"), {"morning"})[0] is None
    # T-75m without lineups: wait; T-40m without lineups: capture anyway
    assert SH.window_for(g, _t(k) - timedelta(minutes=75), {"morning"}) == (None, "pregame_waiting_for_lineups")
    assert SH.window_for(g, _t(k) - timedelta(minutes=40), {"morning"})[0] == "pregame"
    g2 = _game(k, lineups=(True, True))
    assert SH.window_for(g2, _t(k) - timedelta(minutes=75), set()) == ("pregame", "lineups_posted")
    assert SH.window_for(g2, _t(k) - timedelta(minutes=10), set())[0] is None
    assert SH.window_for(g2, _t(k), set()) == (None, "at_or_after_kickoff")
    assert SH.window_for(g2, _t(k) + timedelta(minutes=5), set()) == (None, "at_or_after_kickoff")
    assert SH.window_for(_game(k, status=("Live", "In Progress")), _t(k) - timedelta(minutes=50), set())[0] is None


def test_parse_schedule_probables_and_lineups():
    doc = {"dates": [{"date": "2026-10-11", "games": [{
        "gamePk": 5, "gameType": "L", "officialDate": "2026-10-11", "gameDate": "2026-10-11T23:38:00Z",
        "status": {"abstractGameState": "Preview", "detailedState": "Scheduled"},
        "teams": {"away": {"team": {"id": 147}, "probablePitcher": {"id": 1, "fullName": "A B"}},
                  "home": {"team": {"id": 141}}},
        "lineups": {"awayPlayers": [{"id": 9}]}}]}]}
    g = SH.parse_schedule(doc, "2026-10-11T12:00:00Z")[0]
    assert g["pk"] == "5" and g["away"]["probable_id"] == "1" and g["home"]["probable_id"] is None
    assert g["away"]["lineup_posted"] and not g["home"]["lineup_posted"]


# ----------------------------------------------------------------------------- rows

@pytest.fixture(scope="module")
def arms():
    return SH.Arms(os.path.join(ROOT, "data"))


def test_postseason_rows_validate_and_are_labelled_blocked(arms):
    gate = SH.load_gate()
    g = _game("2026-09-30T23:08:00Z")
    as_of = "2026-09-30T22:00:00Z"
    proj, why = arms.project(g, "away", as_of)
    assert why is None
    rows = SH.build_rows(arms, g, "away", "pregame", as_of, proj, "run")
    assert len(rows) == 9  # 3 arms x outs/BF/K, ladder inside each row
    for arm in SH.ARMS:
        assert gate.validate_file_summary([r for r in rows if r["x_arm"] == arm], require_v1=True)["n"] == 3
    assert {r["x_validation_status"] for r in rows} == {"BLOCKED_NOT_VALIDATED_POSTSEASON"}
    for r in rows:
        assert r["source_max_observed_at"] < r["as_of"] < r["kickoff"]
        assert all(x["observed_at"] <= r["as_of"] for x in r["feature_lineage"])
        assert r["projection_mode"] == "PURE_INDEPENDENT" and r["conditional_on_playing"] is True
    ch = {r["statistic"]: r for r in rows if r["x_arm"] == "challenger"}
    assert ch["pitcher_batters_faced"]["projection"]["mean"] >= ch["pitcher_outs"]["projection"]["mean"]


def test_regular_season_rows_labelled_and_stale_history_refused(arms):
    g = _game("2026-09-28T23:08:00Z", gtype="R", date="2026-09-28")
    proj, why = arms.project(g, "away", "2026-09-28T22:00:00Z")
    assert why is None
    rows = SH.build_rows(arms, g, "away", "morning", "2026-09-28T22:00:00Z", proj, "run")
    lab = {r["x_arm"]: r["x_validation_status"] for r in rows}
    assert lab["challenger"] == "RESEARCH_SHADOW_ACCEPTED_CHALLENGER_REGULAR_SEASON"
    stale = _game("2027-04-10T23:08:00Z", gtype="R", date="2027-04-10")
    assert arms.project(stale, "away", "2027-04-10T22:00:00Z") == (None, "history_stale_regular_season")
    assert arms.project(_game("2026-09-30T23:08:00Z", pid=None), "away", "2026-09-30T22:00:00Z")[1] == "no_probable_starter"


def test_rows_never_read_market_files(arms, tmp_path):
    """Same sports inputs, edgelab market/resolution tree absent -> identical rows."""
    g = _game("2026-09-30T23:08:00Z")
    as_of = "2026-09-30T22:00:00Z"
    base = SH.build_rows(arms, g, "away", "pregame", as_of, arms.project(g, "away", as_of)[0], "r")
    data = tmp_path / "data"
    data.mkdir()
    for sub in ("research_cache", "statcast_raw"):
        os.symlink(os.path.join(ROOT, "data", sub), data / sub)
    other = SH.Arms(str(data))
    again = SH.build_rows(other, g, "away", "pregame", as_of, other.project(g, "away", as_of)[0], "r")
    strip = lambda rs: [{k: v for k, v in r.items() if not k.startswith("x_run")} for r in rs]  # noqa: E731
    assert json.dumps(strip(base), sort_keys=True) == json.dumps(strip(again), sort_keys=True)


# ----------------------------------------------------------------------------- market family / cohort

def test_market_snapshot_listing_time_and_cohort(tmp_path):
    root = tmp_path / "data"
    (root / "edgelab" / "markets").mkdir(parents=True)
    (root / "edgelab" / "observations").mkdir(parents=True)
    ev = "KXMLBKS-26SEP301908ATHHOU"
    mk = {"marketFamily": "pitcher_strikeouts", "eventTicker": ev, "marketTicker": ev + "-ATHTSTARTER1-5",
          "gameId": "999001", "player": "Test Starter", "team": "ATH", "threshold": 5, "comparisonOperator": "AT_LEAST",
          "provenance": {"capturedAt": "2026-09-30T13:00:00.000Z"}}
    late = dict(mk, marketTicker=ev + "-ATHTSTARTER1-7", threshold=7, provenance={"capturedAt": "2026-09-30T22:30:00Z"})
    ob = dict(mk, yesBid=0.4, yesAsk=0.45, lastPrice=0.44, capturedAt="2026-09-30T20:00:00Z",
              provenance={"capturedAt": "2026-09-30T20:00:00Z"})
    (root / "edgelab" / "markets" / "2026-09-30.jsonl").write_text(json.dumps(mk) + "\n" + json.dumps(late) + "\n")
    (root / "edgelab" / "observations" / "2026-09-30.jsonl").write_text(json.dumps(ob) + "\n")
    g = _game("2026-09-30T23:08:00Z")
    rows = SH.market_snapshot(str(root), [g], "2026-09-30T22:00:00Z", {("999001", 133): (PID, "Test Starter")})
    assert len(rows) == 1  # the 7+ contract was first seen after as_of
    r = rows[0]
    assert r["player_id"] == PID and r["first_listed_at"] == "2026-09-30T13:00:00Z" and r["quote"]["yes_ask"] == 0.45
    proj = [{"game_id": "999001", "player_id": PID, "statistic": s, "as_of": a}
            for s in ("pitcher_strikeouts", "pitcher_outs") for a in ("2026-09-30T12:00:00Z", "2026-09-30T22:00:00Z")]
    assert SH.cohort_keys(rows, proj) == {("999001", PID, "pitcher_strikeouts", "2026-09-30T22:00:00Z")}


# ----------------------------------------------------------------------------- outcomes

def test_outcome_rows_conditional_on_starting():
    def p(pid, outs, bf, k):
        return {"person": {"fullName": pid}, "stats": {"pitching": {"outs": outs, "battersFaced": bf, "strikeOuts": k}}}
    feed = {"gameData": {"status": {"detailedState": "Final"}, "game": {"type": "L"}},
            "liveData": {"plays": {"allPlays": [{"about": {"endTime": "2026-10-01T03:00:00Z"}}]},
                         "boxscore": {"teams": {"away": {"pitchers": [11, 12], "players": {"ID11": p("11", 16, 22, 5), "ID12": p("12", 3, 4, 1)}},
                                                "home": {"pitchers": [21], "players": {"ID21": p("21", 27, 31, 9)}}}}}}
    rows, prov = SH.outcome_rows_from_feed("7", feed, {"11", "12", "99"}, "2026-10-01T09:00:00Z")
    by = {(r["player_id"], r["statistic"]): r for r in rows}
    assert by[("11", "pitcher_outs")] == {"sport": "MLB", "game_id": "7", "player_id": "11", "statistic": "pitcher_outs",
                                         "actual": 16, "played": True, "source": "mlb_statsapi_v1.1_feed_live",
                                         "observed_at": "2026-10-01T09:00:00Z"}
    assert by[("12", "pitcher_strikeouts")]["played"] is False  # relief, not a start
    assert by[("99", "pitcher_outs")]["played"] is False  # scratched
    assert prov["unprojected_actual_starters"] == ["21"] and prov["game_end"] == "2026-10-01T03:00:00Z"
    SH.load_gate().outcomes_index(rows)


# ----------------------------------------------------------------------------- store

def test_store_write_once_and_hash_chain(tmp_path):
    st = ShadowStore(str(tmp_path / "s"))
    st.write_once("projections/2026-10-01/a.jsonl", [{"x": 1}])
    st.write_once("runs/2026-10-01/a.json", {"y": 2})
    with pytest.raises(WriteOnceError):
        st.write_once("projections/2026-10-01/a.jsonl", [{"x": 2}])
    assert st.verify()["ok"]
    with open(os.path.join(st.root, "runs/2026-10-01/a.json"), "a") as fh:
        fh.write(" ")
    v = st.verify()
    assert not v["ok"] and any("content changed" in p for p in v["problems"])
    (tmp_path / "s" / "stray.json").write_text("{}")
    assert any("unmanifested" in p for p in st.verify()["problems"])


def test_force_window_refuses_committed_store():
    with pytest.raises(SystemExit):
        SH.main(["capture", "--force-window", "pregame"])


def test_existing_windows_reads_per_arm_files(tmp_path):
    st = ShadowStore(str(tmp_path / "s"))
    for arm in SH.ARMS:
        st.write_once(f"projections/2026-10-11/20261011T120000Z_morning__{arm}.jsonl", [{"game_id": "5", "x": arm}])
    assert SH.existing_windows(st, "2026-10-11")["5"] == {"morning"}


def test_capture_end_to_end_with_offline_schedule(tmp_path, monkeypatch):
    doc = {"dates": [{"date": "2026-10-11", "games": [{
        "gamePk": 849809, "gameType": "L", "officialDate": "2026-10-11", "gameDate": "2026-10-12T00:00:00Z",
        "status": {"abstractGameState": "Preview", "detailedState": "Scheduled"},
        "teams": {"away": {"team": {"id": 133}, "probablePitcher": {"id": int(PID), "fullName": "Test Starter"}},
                  "home": {"team": {"id": 117}}}}]}]}
    clock = [datetime(2026, 10, 11, 20, 0, 0, tzinfo=timezone.utc)]

    def now():
        clock[0] += timedelta(seconds=1)
        return clock[0]
    monkeypatch.setattr(SH, "fetch_json", lambda url, timeout=30: doc)
    monkeypatch.setattr(SH, "utcnow", now)
    monkeypatch.setattr(SH.time, "sleep", lambda s: None)
    store = str(tmp_path / "s")
    assert SH.main(["--store", store, "capture", "--date", "2026-10-11", "--force-window", "pregame"]) == 0
    st = ShadowStore(store)
    files = st.files()
    assert sorted(f.rsplit("__", 1)[-1] for f in files if f.startswith("projections/")) == \
        ["baseline.jsonl", "challenger.jsonl", "champion.jsonl"]
    assert any(f.startswith("market/") for f in files) and any(f.startswith("runs/") for f in files)
    assert st.verify()["ok"]
    gate = SH.load_gate()
    for f in files:
        if f.startswith("projections/"):
            rows = [json.loads(x) for x in open(os.path.join(store, f))]
            assert gate.validate_file_summary(rows, require_v1=True)["n"] == 3
            assert {r["x_validation_status"] for r in rows} == {"BLOCKED_NOT_VALIDATED_POSTSEASON"}


def test_only_the_first_capture_of_a_window_is_scored(tmp_path):
    """2026-10-12: a second run re-captured the same morning window; both stay in the store, one is scored."""
    st = ShadowStore(str(tmp_path / "store"))
    for stamp_, rid in (("20261012T122519Z", "r1"), ("20261012T122604Z", "r2")):
        st.write_once(f"projections/2026-10-12/{stamp_}_morning__champion.jsonl",
                      [{"game_id": "g", "player_id": "p", "statistic": "pitcher_outs", "x_arm": "champion",
                        "x_capture_window": "morning", "x_run_id": rid, "mean": 1.0}])
    st.write_once("projections/2026-10-12/20261012T193000Z_pregame__champion.jsonl",
                  [{"game_id": "g", "player_id": "p", "statistic": "pitcher_outs", "x_arm": "champion",
                    "x_capture_window": "pregame", "x_run_id": "r3", "mean": 1.0}])
    rows = SH.all_projections(st)
    assert sorted(r["x_run_id"] for r in rows) == ["r1", "r3"]

