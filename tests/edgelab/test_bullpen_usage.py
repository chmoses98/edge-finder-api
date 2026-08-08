#!/usr/bin/env python3
"""
tests/edgelab/test_bullpen_usage.py
========================================
Coverage for lib/edgelab/bullpen_usage.py -- the pure parsing core of
the "recent bullpen usage" context improvement (previous-day usage,
back-to-back appearances, recent pitch counts, high-leverage save/hold
workload, handedness mix, and explicit missing-data behavior).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.bullpen_usage import (
    MLB_TEAM_ID_MAP,
    extract_completed_games_for_team,
    extract_relief_appearances,
    summarize_team_bullpen_usage,
)


def _schedule(games):
    """games: list of (date, gamePk, status, away_id, home_id)."""
    by_date = {}
    for date, game_pk, status, away_id, home_id in games:
        by_date.setdefault(date, []).append({
            "gamePk": game_pk, "status": {"detailedState": status},
            "teams": {
                "away": {"team": {"id": away_id}},
                "home": {"team": {"id": home_id}},
            },
        })
    return {"dates": [{"date": d, "games": g} for d, g in by_date.items()]}


def _boxscore(side_pitchers, other_side="home"):
    """side_pitchers: {"away": {"pitchers": [...], "players": {...}}}."""
    teams = dict(side_pitchers)
    for missing in ("away", "home"):
        teams.setdefault(missing, {"pitchers": [], "players": {}})
    return {"teams": teams}


def _player(name, hand=None, pitches=None, outs=None, saves=None, holds=None):
    person = {"fullName": name}
    if hand is not None:
        person["pitchHand"] = {"code": hand}
    pitching = {}
    if pitches is not None:
        pitching["numberOfPitches"] = pitches
    if outs is not None:
        pitching["outs"] = outs
    if saves is not None:
        pitching["saves"] = saves
    if holds is not None:
        pitching["holds"] = holds
    return {"person": person, "stats": {"pitching": pitching}}


class TestExtractCompletedGamesForTeam:

    def test_only_completed_statuses_included(self):
        schedule = _schedule([
            ("2026-08-06", 1, "Final", 147, 111),
            ("2026-08-07", 2, "In Progress", 147, 111),
            ("2026-08-05", 3, "Postponed", 147, 111),
        ])
        games = extract_completed_games_for_team(schedule, 147)
        assert [g["gamePk"] for g in games] == [1]

    def test_side_identifies_which_half_is_this_team(self):
        schedule = _schedule([("2026-08-06", 1, "Final", 147, 111)])
        away_games = extract_completed_games_for_team(schedule, 147)
        home_games = extract_completed_games_for_team(schedule, 111)
        assert away_games[0]["side"] == "away"
        assert home_games[0]["side"] == "home"

    def test_team_not_in_game_is_excluded(self):
        schedule = _schedule([("2026-08-06", 1, "Final", 147, 111)])
        assert extract_completed_games_for_team(schedule, 999) == []

    def test_sorted_oldest_first(self):
        schedule = _schedule([
            ("2026-08-07", 2, "Final", 147, 111),
            ("2026-08-05", 1, "Final", 147, 111),
        ])
        games = extract_completed_games_for_team(schedule, 147)
        assert [g["date"] for g in games] == ["2026-08-05", "2026-08-07"]

    def test_missing_schedule_returns_empty(self):
        assert extract_completed_games_for_team(None, 147) == []
        assert extract_completed_games_for_team({}, 147) == []


class TestExtractReliefAppearances:

    def test_starter_excluded_relievers_included(self):
        boxscore = _boxscore({"away": {
            "pitchers": [100, 200, 300],
            "players": {
                "ID100": _player("Starter", pitches=95),
                "ID200": _player("Setup", hand="R", pitches=18, outs=3, holds=1),
                "ID300": _player("Closer", hand="L", pitches=12, outs=3, saves=1),
            },
        }})
        appearances = extract_relief_appearances(boxscore, "away")
        names = [a["name"] for a in appearances]
        assert names == ["Setup", "Closer"]
        assert appearances[0]["throwsHand"] == "R"
        assert appearances[1]["saves"] == 1

    def test_complete_game_by_starter_has_no_relief_appearances(self):
        boxscore = _boxscore({"away": {
            "pitchers": [100],
            "players": {"ID100": _player("Starter", pitches=110)},
        }})
        assert extract_relief_appearances(boxscore, "away") == []

    def test_malformed_pitch_count_never_truncated_becomes_none(self):
        """Reuses lib.edgelab.player_stats.parse_nonnegative_int -- a
        non-integral or negative value is rejected, never silently
        truncated/coerced."""
        boxscore = _boxscore({"away": {
            "pitchers": [100, 200],
            "players": {
                "ID100": _player("Starter", pitches=90),
                "ID200": {"person": {"fullName": "Reliever"},
                          "stats": {"pitching": {"numberOfPitches": -5}}},
            },
        }})
        appearances = extract_relief_appearances(boxscore, "away")
        assert appearances[0]["numberOfPitches"] is None

    def test_unknown_hand_code_never_guessed(self):
        boxscore = _boxscore({"away": {
            "pitchers": [100, 200],
            "players": {
                "ID100": _player("Starter", pitches=90),
                "ID200": _player("Reliever", hand="S", pitches=10),  # switch/invalid code
            },
        }})
        appearances = extract_relief_appearances(boxscore, "away")
        assert appearances[0]["throwsHand"] is None

    def test_missing_boxscore_returns_empty(self):
        assert extract_relief_appearances(None, "away") == []
        assert extract_relief_appearances({}, "away") == []

    def test_missing_player_entry_still_returns_row_with_nulls(self):
        """A pitcher ID present in the pitchers[] array but absent from
        players{} (a genuinely malformed/partial response) never crashes
        -- fields fall back to None, the row is never dropped."""
        boxscore = _boxscore({"away": {"pitchers": [100, 200], "players": {}}})
        appearances = extract_relief_appearances(boxscore, "away")
        assert len(appearances) == 1
        assert appearances[0]["playerId"] == "200"
        assert appearances[0]["name"] is None
        assert appearances[0]["numberOfPitches"] is None


class TestSummarizeTeamBullpenUsage:

    def test_no_games_is_explicitly_unavailable(self):
        summary = summarize_team_bullpen_usage([])
        assert summary["dataAvailable"] is False
        assert summary["unavailableReason"] == "no_completed_games_in_window"
        assert summary["recentPitchCounts"] == []
        assert summary["backToBackRelievers"] == []

    def test_single_game_with_no_relievers_used_is_available_not_unavailable(self):
        """A complete-game shutout (zero relief appearances) is REAL
        signal (bullpen got a full day off), not a data-unavailable
        state -- must never be conflated with 'no completed games'."""
        summary = summarize_team_bullpen_usage([{"date": "2026-08-07", "appearances": []}])
        assert summary["dataAvailable"] is True
        assert summary["gamesConsidered"] == 1
        assert summary["relieversUsedLastGame"] == []
        assert summary["teamPitchCountLastGame"] == 0

    def test_back_to_back_reliever_detected_across_two_games(self):
        setup = {"playerId": "200", "name": "Setup", "throwsHand": "R",
                 "numberOfPitches": 15, "outsRecorded": 3, "saves": None, "holds": 1}
        games = [
            {"date": "2026-08-06", "appearances": [dict(setup, numberOfPitches=20)]},
            {"date": "2026-08-07", "appearances": [setup]},
        ]
        summary = summarize_team_bullpen_usage(games)
        assert summary["backToBackRelievers"] == [{"playerId": "200", "name": "Setup"}]

    def test_not_back_to_back_when_only_one_of_two_games_used(self):
        setup = {"playerId": "200", "name": "Setup", "throwsHand": "R",
                 "numberOfPitches": 15, "outsRecorded": 3, "saves": None, "holds": 1}
        games = [
            {"date": "2026-08-06", "appearances": []},
            {"date": "2026-08-07", "appearances": [setup]},
        ]
        summary = summarize_team_bullpen_usage(games)
        assert summary["backToBackRelievers"] == []

    def test_recent_pitch_counts_aggregate_across_window(self):
        closer = {"playerId": "300", "name": "Closer", "throwsHand": "L",
                  "numberOfPitches": 12, "outsRecorded": 3, "saves": 1, "holds": None}
        games = [
            {"date": "2026-08-05", "appearances": [dict(closer, numberOfPitches=10)]},
            {"date": "2026-08-07", "appearances": [closer]},
        ]
        summary = summarize_team_bullpen_usage(games)
        entry = summary["recentPitchCounts"][0]
        assert entry["playerId"] == "300"
        assert entry["totalPitches"] == 22
        assert entry["appearances"] == 2

    def test_high_leverage_usage_only_includes_save_or_hold_recorders(self):
        mop_up = {"playerId": "400", "name": "MopUp", "throwsHand": "R",
                  "numberOfPitches": 30, "outsRecorded": 6, "saves": None, "holds": None}
        closer = {"playerId": "300", "name": "Closer", "throwsHand": "L",
                  "numberOfPitches": 12, "outsRecorded": 3, "saves": 1, "holds": None}
        summary = summarize_team_bullpen_usage([{"date": "2026-08-07", "appearances": [mop_up, closer]}])
        hl_ids = {e["playerId"] for e in summary["highLeverageRecentUsage"]}
        assert hl_ids == {"300"}
        assert summary["highLeverageRecentUsage"][0]["saves"] == 1

    def test_handedness_mix_counts_each_reliever_once(self):
        lefty = {"playerId": "300", "name": "Closer", "throwsHand": "L",
                 "numberOfPitches": 12, "outsRecorded": 3, "saves": 1, "holds": None}
        righty = {"playerId": "200", "name": "Setup", "throwsHand": "R",
                  "numberOfPitches": 15, "outsRecorded": 3, "saves": None, "holds": 1}
        unknown_hand = {"playerId": "500", "name": "Mystery", "throwsHand": None,
                         "numberOfPitches": 10, "outsRecorded": 2, "saves": None, "holds": None}
        games = [
            {"date": "2026-08-06", "appearances": [lefty, righty]},
            {"date": "2026-08-07", "appearances": [lefty, unknown_hand]},  # lefty appears twice
        ]
        summary = summarize_team_bullpen_usage(games)
        assert summary["handednessMix"] == {"L": 1, "R": 1, "unknown": 1}

    def test_team_pitch_count_last_game_vs_window_differ(self):
        r1 = {"playerId": "1", "name": "A", "throwsHand": "R", "numberOfPitches": 10,
              "outsRecorded": 3, "saves": None, "holds": None}
        r2 = {"playerId": "2", "name": "B", "throwsHand": "L", "numberOfPitches": 20,
              "outsRecorded": 3, "saves": None, "holds": None}
        games = [
            {"date": "2026-08-06", "appearances": [r1]},
            {"date": "2026-08-07", "appearances": [r2]},
        ]
        summary = summarize_team_bullpen_usage(games)
        assert summary["teamPitchCountLastGame"] == 20
        assert summary["teamPitchCountWindow"] == 30

    def test_as_of_date_is_most_recent_game(self):
        games = [
            {"date": "2026-08-05", "appearances": []},
            {"date": "2026-08-07", "appearances": []},
            {"date": "2026-08-06", "appearances": []},
        ]
        summary = summarize_team_bullpen_usage(games)
        assert summary["asOfDate"] == "2026-08-07"


class TestModuleLevelConstants:

    def test_team_id_map_covers_all_30_teams(self):
        assert len(MLB_TEAM_ID_MAP) == 30

    def test_no_duplicate_team_ids(self):
        assert len(set(MLB_TEAM_ID_MAP.values())) == 30
