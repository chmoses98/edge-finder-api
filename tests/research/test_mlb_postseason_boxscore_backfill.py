"""Network-free tests for scripts/research/mlb_postseason_boxscore_backfill.py and its
PR-branch-only workflow (W2 Phase 3 data repair, research only)."""
import fnmatch
import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPEC = importlib.util.spec_from_file_location(
    "mlb_postseason_boxscore_backfill", os.path.join(ROOT, "scripts", "research", "mlb_postseason_boxscore_backfill.py"))
B = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B)
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "research-mlb-postseason-boxscore-backfill.yml")


def _box(pitchers_away, pitchers_home):
    def side(ps):
        return {"pitchers": [p[0] for p in ps],
                "players": {f"ID{p[0]}": {"person": {"fullName": f"P{p[0]}", "pitchHand": {"code": "R"}},
                                          "stats": {"pitching": {"outs": p[1], "battersFaced": p[2], "strikeOuts": p[3],
                                                                 "numberOfPitches": p[4], "inningsPitched": p[5]}}}
                            for p in ps}}
    return {"teams": {"away": side(pitchers_away), "home": side(pitchers_home)}}


def _feed(state="Final", gtype="D", end="2026-10-05T03:10:00.000Z"):
    return {"gameData": {"status": {"detailedState": state}, "game": {"type": gtype},
                         "datetime": {"dateTime": "2026-10-05T00:08:00Z", "officialDate": "2026-10-04"},
                         "gameInfo": {"firstPitch": "2026-10-05T00:09:30.000Z"},
                         "teams": {"away": {"id": 147, "abbreviation": "NYY"}, "home": {"id": 141, "abbreviation": "TOR"}}},
            "liveData": {"plays": {"allPlays": [{"about": {"startTime": "2026-10-05T00:09:30Z", "endTime": "2026-10-05T00:12:00Z"}},
                                                {"about": {"startTime": "2026-10-05T03:05:00Z", "endTime": end}}]},
                         "boxscore": _box([(1, 17, 23, 6, 91, "5.2"), (2, 10, 13, 3, 40, "3.1")],
                                          [(3, 15, 22, 4, 88, "5.0"), (4, 12, 14, 2, 45, "4.0")])}}


SCHED = {"gamePk": 777, "seriesDescription": "Division Series", "seriesGameNumber": 2, "gamesInSeries": 5,
         "officialDate": "2026-10-04", "gameDate": "2026-10-05T00:08:00Z"}
META = {"url": "u", "fetched_at": "2026-10-05T09:00:00Z", "payload_sha256": "0" * 64}


def test_parse_schedule_keeps_only_postseason_types():
    doc = {"dates": [{"date": "2026-10-04", "games": [
        {"gamePk": 1, "gameType": "D", "status": {"detailedState": "Final"}, "teams": {}},
        {"gamePk": 2, "gameType": "R", "status": {"detailedState": "Final"}, "teams": {}},
        {"gamePk": 3, "gameType": "W", "status": {"detailedState": "Scheduled"}, "teams": {}}]}]}
    rows = B.parse_schedule(doc)
    assert [r["gamePk"] for r in rows] == [1, 3]
    assert rows[0]["officialDate"] == "2026-10-04"


def test_build_record_final_game_has_lines_times_and_provenance():
    feed = _feed()
    rec, why = B.build_record(2026, SCHED, feed, META, feed["liveData"]["boxscore"], dict(META, url="b"))
    assert why is None
    st = rec["awayPitchers"][0]
    assert (st["playerId"], st["orderIndex"], st["outs"], st["battersFaced"], st["strikeOuts"], st["numberOfPitches"],
            st["inningsPitched"]) == ("1", 0, 17, 23, 6, 91, "5.2")
    assert rec["gameType"] == "D" and rec["gameEnd"] == "2026-10-05T03:10:00Z"
    assert rec["firstPitch"] == "2026-10-05T00:09:30Z" and rec["scheduledStart"] == "2026-10-05T00:08:00Z"
    assert rec["fetched_at"] == META["fetched_at"] and rec["source_url"] == "u"
    assert rec["boxscoreEndpointAgreement"] is True and len(rec["sources"]) == 2


def test_build_record_flags_endpoint_disagreement():
    feed = _feed()
    other = _box([(1, 18, 23, 6, 91, "6.0"), (2, 10, 13, 3, 40, "3.1")],
                 [(3, 15, 22, 4, 88, "5.0"), (4, 12, 14, 2, 45, "4.0")])
    rec, _ = B.build_record(2026, SCHED, feed, META, other, META)
    assert rec["boxscoreEndpointAgreement"] is False
    assert {d["field"] for d in rec["boxscoreEndpointDisagreements"]} == {"outs", "inningsPitched"}


@pytest.mark.parametrize("feed,meta,reason", [
    (_feed(state="In Progress"), META, "not_final"),
    (_feed(gtype="R"), META, "not_postseason"),
    (_feed(), dict(META, fetched_at="2026-10-05T03:00:00Z"), "fetched_before_game_end"),
])
def test_build_record_refuses_partial_or_wrong_games(feed, meta, reason):
    rec, why = B.build_record(2026, SCHED, feed, meta, None, None)
    assert rec is None and why.startswith(reason)


def test_postseason_files_are_outside_the_frozen_challenger_source_globs():
    from lib.research import pitcher_workload_sources as S
    for season in (2022, 2026):
        for p in (B.schedule_path(season), B.boxscore_path(season)):
            rel = os.path.relpath(p, os.path.join(ROOT, "data"))
            assert not any(fnmatch.fnmatch(rel, g) for g in S.SOURCE_GLOBS), rel


def test_workflow_is_pr_branch_only_and_guarded():
    yaml = pytest.importorskip("yaml")
    with open(WORKFLOW) as fh:
        wf = yaml.safe_load(fh)
    on = wf.get(True) or wf.get("on")
    assert set(on) == {"pull_request", "workflow_dispatch"}  # never a schedule
    steps = wf["jobs"]["backfill"]["steps"]
    assert "protected branch" in steps[0]["name"].lower()
    assert any("checkout" in (s.get("name") or "").lower() for s in steps[1:2])
    body = open(WORKFLOW).read()
    assert "git_data_commit.py" in body and "settlements" not in body.split("jobs:")[1]
