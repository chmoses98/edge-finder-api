#!/usr/bin/env python3
"""
tests/research/test_hitter_w2_feed_extract.py
=============================================
SIFT W2 hitter lane: offline test of the pure MLB feed extractor used by
.github/workflows/research-hitter-w2-backfill.yml. Synthetic feed only.
"""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_spec = importlib.util.spec_from_file_location(
    "hitter_w2_fetch", os.path.join(ROOT, "scripts", "research", "hitter_w2", "fetch_pbp_backfill.py"))
F = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(F)


def _feed():
    return {
        "gameData": {
            "game": {"pk": 1, "type": "R", "doubleHeader": "N", "gameNumber": 1},
            "datetime": {"officialDate": "2025-05-01", "dateTime": "2025-05-01T23:05:00Z", "dayNight": "night"},
            "status": {"detailedState": "Final"},
            "teams": {"away": {"id": 10, "abbreviation": "AAA"}, "home": {"id": 20, "abbreviation": "HHH"}},
            "venue": {"id": 5},
            "players": {"ID100": {"batSide": {"code": "L"}}, "ID900": {"pitchHand": {"code": "R"}},
                        "ID901": {"pitchHand": {"code": "L"}}},
            "probablePitchers": {"away": {"id": 901}, "home": {"id": 900}},
        },
        "liveData": {
            "linescore": {"innings": [{}] * 9, "teams": {"away": {"runs": 2}, "home": {"runs": 1}}},
            "boxscore": {"teams": {
                "away": {"team": {"id": 10}, "pitchers": [901], "players": {
                    "ID100": {"person": {"id": 100}, "battingOrder": "300", "position": {"abbreviation": "1B"},
                              "stats": {"batting": {"plateAppearances": 4, "atBats": 3, "hits": 2, "doubles": 1,
                                                    "triples": 0, "homeRuns": 1, "rbi": 2, "runs": 1,
                                                    "baseOnBalls": 1, "strikeOuts": 0, "totalBases": 7}}},
                    "ID901": {"person": {"id": 901}, "stats": {"pitching": {"outs": 27, "battersFaced": 30}}}}},
                "home": {"team": {"id": 20}, "pitchers": [900], "players": {
                    "ID900": {"person": {"id": 900}, "stats": {"pitching": {"outs": 24, "battersFaced": 35,
                                                                             "strikeOuts": 7}}}}},
            }},
            "plays": {"allPlays": [
                {"about": {"atBatIndex": 0, "inning": 1, "halfInning": "top", "isComplete": True},
                 "result": {"eventType": "single", "rbi": 0, "awayScore": 0, "homeScore": 0},
                 "matchup": {"batter": {"id": 100}, "batSide": {"code": "L"}, "pitcher": {"id": 900},
                             "pitchHand": {"code": "R"}, "postOnFirst": {"id": 100}},
                 "count": {"outs": 0}, "runners": []},
                {"about": {"atBatIndex": 1, "inning": 1, "halfInning": "top", "isComplete": True},
                 "result": {"eventType": "home_run", "rbi": 2, "awayScore": 2, "homeScore": 0},
                 "matchup": {"batter": {"id": 101}, "pitcher": {"id": 900}},
                 "count": {"outs": 0},
                 "runners": [{"movement": {"end": "score"}, "details": {"runner": {"id": 100}}},
                             {"movement": {"end": "score"}, "details": {"runner": {"id": 101}}}]},
            ]},
        },
    }


def test_extract_game_rows():
    g, bats, pits, pas = F.extract_game(_feed(), 2025, "2025-05-02T00:00:00Z", "abc")
    assert g["gamePk"] == 1 and g["awayStarterId"] == 901 and g["awayStarterHand"] == "L"
    assert g["homeStarterHand"] == "R" and g["innings"] == 9 and g["awayRuns"] == 2
    assert len(bats) == 1
    b = bats[0]
    assert b["slot"] == 3 and b["isStarter"] == 1 and b["batSide"] == "L" and b["RBI"] == 2 and b["TB"] == 7
    assert [p["pitcherId"] for p in pits] == [901, 900] and pits[1]["K"] == 7
    assert pas[0]["postBases"] == 1 and pas[0]["runsOnPlay"] == 0
    assert pas[1]["runsOnPlay"] == 2 and pas[1]["rbi"] == 2 and pas[1]["scorerIds"] == "100|101"


def test_extract_fields_match_headers():
    g, bats, pits, pas = F.extract_game(_feed(), 2025, "t", "h")
    assert set(g) == set(F.GAME_FIELDS)
    assert all(set(r) == set(F.BAT_FIELDS) for r in bats)
    assert all(set(r) == set(F.PIT_FIELDS) for r in pits)
    assert all(set(r) == set(F.PA_FIELDS) for r in pas)
