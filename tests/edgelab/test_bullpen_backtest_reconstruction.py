import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.backtest import bullpen_backtest_reconstruction as recon
from lib.edgelab.bullpen_availability import compute_bullpen_workload_adjustment


def _appearance(player_id, pitches=10, saves=0, holds=0):
    return {"playerId": player_id, "name": f"P{player_id}", "throwsHand": "R",
            "numberOfPitches": pitches, "outsRecorded": 3, "saves": saves, "holds": holds}


def _team_game(date, game_pk, appearances, game_number=1, double_header="N"):
    return {"date": date, "gamePk": game_pk, "gameNumber": game_number,
            "doubleHeader": double_header, "appearances": appearances}


def _boxscore(pitcher_stats, side="home"):
    """pitcher_stats: list of (playerId, numberOfPitches, outs, saves, holds, runs, earnedRuns)."""
    players = {}
    ids = []
    for pid, pitches, outs, saves, holds, runs, er in pitcher_stats:
        ids.append(pid)
        players[f"ID{pid}"] = {
            "person": {"fullName": f"Pitcher {pid}", "pitchHand": {"code": "R"}},
            "stats": {"pitching": {
                "numberOfPitches": pitches, "outs": outs, "saves": saves, "holds": holds,
                "runs": runs, "earnedRuns": er,
            }},
        }
    return {"teams": {side: {"pitchers": ids, "players": players}}}


# ── is_strictly_before / prior_games ────────────────────────────────────

class TestIsStrictlyBefore:
    def test_earlier_date_is_before(self):
        assert recon.is_strictly_before({"date": "2026-08-10"}, {"date": "2026-08-15"}) is True

    def test_same_date_is_not_before(self):
        assert recon.is_strictly_before({"date": "2026-08-15"}, {"date": "2026-08-15"}) is False

    def test_later_date_is_not_before(self):
        assert recon.is_strictly_before({"date": "2026-08-16"}, {"date": "2026-08-15"}) is False

    def test_doubleheader_game_one_precedes_game_two_same_date(self):
        g1 = {"date": "2026-08-15", "gameNumber": 1}
        g2 = {"date": "2026-08-15", "gameNumber": 2}
        assert recon.is_strictly_before(g1, g2) is True
        assert recon.is_strictly_before(g2, g1) is False

    def test_same_date_unknown_game_number_is_never_prior(self):
        g1 = {"date": "2026-08-15", "gameNumber": None}
        g2 = {"date": "2026-08-15", "gameNumber": 2}
        assert recon.is_strictly_before(g1, g2) is False
        g3 = {"date": "2026-08-15", "gameNumber": 1}
        g4 = {"date": "2026-08-15", "gameNumber": None}
        assert recon.is_strictly_before(g3, g4) is False


class TestTargetGameNeverContributesToItsOwnFeatures:
    def test_target_games_own_appearances_are_excluded(self):
        target = {"date": "2026-08-15", "gameNumber": 1}
        all_games = [
            _team_game("2026-08-14", 1, [_appearance("A", pitches=20)]),
            _team_game("2026-08-15", 2, [_appearance("B", pitches=999)]),  # the target game itself
        ]
        features = recon.reconstruct_workload_features(all_games, target)
        assert features["bullpenPitchesPrevDay1"] == 20  # only the 08-14 game
        assert features["productionFormulaInput"]["gamesConsidered"] == 1


class TestFutureGamesNeverEnterFeatures:
    def test_future_games_are_excluded(self):
        target = {"date": "2026-08-15", "gameNumber": 1}
        all_games = [
            _team_game("2026-08-14", 1, [_appearance("A", pitches=20)]),
            _team_game("2026-08-16", 2, [_appearance("B", pitches=999)]),  # future
            _team_game("2026-09-01", 3, [_appearance("C", pitches=999)]),  # far future
        ]
        features = recon.reconstruct_workload_features(all_games, target)
        assert features["bullpenPitchesPrevDay1"] == 20
        assert features["productionFormulaInput"]["gamesConsidered"] == 1
        all_ids = {p["playerId"] for p in features["productionFormulaInput"]["recentPitchCounts"]}
        assert "B" not in all_ids and "C" not in all_ids


class TestDoubleheaderHandling:
    def test_game_two_of_a_doubleheader_sees_game_ones_usage(self):
        g1 = {"date": "2026-08-15", "gameNumber": 1}
        g2 = {"date": "2026-08-15", "gameNumber": 2}
        all_games = [_team_game("2026-08-15", 100, [_appearance("A", pitches=25)], game_number=1)]
        features = recon.reconstruct_workload_features(all_games, g2)
        assert features["productionFormulaInput"]["gamesConsidered"] == 1
        assert features["productionFormulaInput"]["teamPitchCountWindow"] == 25

    def test_game_one_never_sees_game_twos_usage(self):
        g1 = {"date": "2026-08-15", "gameNumber": 1}
        all_games = [_team_game("2026-08-15", 101, [_appearance("B", pitches=999)], game_number=2)]
        features = recon.reconstruct_workload_features(all_games, g1)
        assert features["productionFormulaInput"]["gamesConsidered"] == 0

    def test_same_day_later_game_with_unknown_game_number_is_excluded_for_safety(self):
        target = {"date": "2026-08-15", "gameNumber": 1}
        all_games = [_team_game("2026-08-15", 102, [_appearance("X", pitches=15)], game_number=None)]
        features = recon.reconstruct_workload_features(all_games, target)
        assert features["productionFormulaInput"]["gamesConsidered"] == 0


# ── extract_team_games_from_schedule ────────────────────────────────────

def _schedule(games):
    """games: list of (date, gamePk, status, side, doubleHeader, gameNumber)."""
    dates = []
    for date, game_pk, status, side, dh, gn in games:
        teams = {"away": {"team": {"id": 999}}, "home": {"team": {"id": 999}}}
        teams[side] = {"team": {"id": 111}}
        dates.append({"date": date, "games": [{
            "gamePk": game_pk, "status": {"detailedState": status}, "teams": teams,
            "doubleHeader": dh, "gameNumber": gn,
        }]})
    return {"dates": dates}


class TestExtractTeamGamesFromSchedule:
    def test_only_completed_games_included(self):
        sched = _schedule([
            ("2026-08-14", 1, "Final", "home", "N", 1),
            ("2026-08-15", 2, "In Progress", "home", "N", 1),
        ])
        games = recon.extract_team_games_from_schedule(sched, 111)
        assert [g["gamePk"] for g in games] == [1]

    def test_doubleheader_fields_carried_through(self):
        sched = _schedule([("2026-08-15", 1, "Final", "home", "Y", 1), ("2026-08-15", 2, "Final", "home", "Y", 2)])
        games = recon.extract_team_games_from_schedule(sched, 111)
        assert [g["gameNumber"] for g in games] == [1, 2]
        assert all(g["doubleHeader"] == "Y" for g in games)

    def test_sorted_by_date_then_game_number(self):
        sched = _schedule([
            ("2026-08-15", 2, "Final", "home", "Y", 2),
            ("2026-08-15", 1, "Final", "home", "Y", 1),
            ("2026-08-14", 3, "Final", "home", "N", 1),
        ])
        games = recon.extract_team_games_from_schedule(sched, 111)
        assert [g["gamePk"] for g in games] == [3, 1, 2]


# ── calendar-day feature reconstruction ─────────────────────────────────

class TestCalendarDayFeatures:
    def test_pitches_prev_day_windows(self):
        target = {"date": "2026-08-15", "gameNumber": 1}
        all_games = [
            _team_game("2026-08-14", 1, [_appearance("A", pitches=20)]),
            _team_game("2026-08-13", 2, [_appearance("B", pitches=15)]),
            _team_game("2026-08-12", 3, [_appearance("C", pitches=10)]),
        ]
        f = recon.reconstruct_workload_features(all_games, target)
        assert f["bullpenPitchesPrevDay1"] == 20
        assert f["bullpenPitchesPrevDays2"] == 35
        assert f["bullpenPitchesPrevDays3"] == 45

    def test_back_to_back_reliever_count(self):
        target = {"date": "2026-08-15", "gameNumber": 1}
        all_games = [
            _team_game("2026-08-14", 1, [_appearance("A"), _appearance("B")]),
            _team_game("2026-08-13", 2, [_appearance("A"), _appearance("C")]),
        ]
        f = recon.reconstruct_workload_features(all_games, target)
        assert f["backToBackRelieverCount"] == 1  # only A appears both days

    def test_three_consecutive_day_reliever_present(self):
        target = {"date": "2026-08-15", "gameNumber": 1}
        all_games = [
            _team_game("2026-08-14", 1, [_appearance("A")]),
            _team_game("2026-08-13", 2, [_appearance("A")]),
            _team_game("2026-08-12", 3, [_appearance("A")]),
        ]
        f = recon.reconstruct_workload_features(all_games, target)
        assert f["threeConsecutiveDayRelieverCount"] == 1

    def test_three_consecutive_day_is_none_when_team_did_not_play_three_days_ago(self):
        target = {"date": "2026-08-15", "gameNumber": 1}
        all_games = [
            _team_game("2026-08-14", 1, [_appearance("A")]),
            _team_game("2026-08-13", 2, [_appearance("A")]),
            # no game on 2026-08-12 -- team had an off day
        ]
        f = recon.reconstruct_workload_features(all_games, target)
        assert f["threeConsecutiveDayRelieverCount"] is None

    def test_high_leverage_used_prev_day_and_back_to_back(self):
        target = {"date": "2026-08-15", "gameNumber": 1}
        all_games = [
            _team_game("2026-08-14", 1, [_appearance("Closer", saves=1)]),
            _team_game("2026-08-13", 2, [_appearance("Closer", holds=1)]),
        ]
        f = recon.reconstruct_workload_features(all_games, target)
        assert f["highLeverageUsedPrevDayCount"] == 1
        assert f["highLeverageBackToBackCount"] == 1

    def test_days_since_last_game(self):
        target = {"date": "2026-08-15", "gameNumber": 1}
        all_games = [_team_game("2026-08-11", 1, [_appearance("A")])]
        f = recon.reconstruct_workload_features(all_games, target)
        assert f["daysSinceLastGame"] == 4

    def test_days_since_last_game_none_at_season_start(self):
        target = {"date": "2026-08-15", "gameNumber": 1}
        f = recon.reconstruct_workload_features([], target)
        assert f["daysSinceLastGame"] is None

    def test_double_header_context_carried_from_as_of_game(self):
        target = {"date": "2026-08-15", "gameNumber": 2, "doubleHeader": "Y"}
        f = recon.reconstruct_workload_features([], target)
        assert f["doubleHeader"] == "Y"
        assert f["gameNumber"] == 2


# ── production formula reused exactly ───────────────────────────────────

class TestProductionFormulaReusedExactly:
    def test_current_production_multiplier_calls_the_real_unmodified_function(self):
        assert recon.current_production_multiplier is not compute_bullpen_workload_adjustment
        target = {"date": "2026-08-15", "gameNumber": 1}
        all_games = [_team_game("2026-08-14", 1, [_appearance("A", pitches=40)])]
        f = recon.reconstruct_workload_features(all_games, target)
        direct = compute_bullpen_workload_adjustment(f["productionFormulaInput"])
        via_helper = recon.current_production_multiplier(f)
        assert direct == via_helper

    def test_heavy_usage_threshold_matches_production_constant(self):
        from lib.edgelab.bullpen_availability import HEAVY_USE_PITCH_THRESHOLD
        target = {"date": "2026-08-15", "gameNumber": 1}
        all_games = [_team_game("2026-08-14", 1, [_appearance("A", pitches=HEAVY_USE_PITCH_THRESHOLD)])]
        f = recon.reconstruct_workload_features(all_games, target)
        assert f["heavyUsageRelieverCount"] == 1


# ── extract_pitcher_lines / relief_outcome_for_game (outcome side) ──────

class TestExtractPitcherLines:
    def test_starter_included_at_index_zero(self):
        box = _boxscore([("1", 90, 18, 0, 0, 2, 2), ("2", 15, 3, 1, 0, 0, 0)])
        lines = recon.extract_pitcher_lines(box, "home")
        assert lines[0]["playerId"] == "1" and lines[0]["orderIndex"] == 0
        assert lines[1]["playerId"] == "2" and lines[1]["orderIndex"] == 1

    def test_missing_boxscore_returns_empty(self):
        assert recon.extract_pitcher_lines(None, "home") == []
        assert recon.extract_pitcher_lines({}, "home") == []


class TestReliefOutcomeForGame:
    def test_complete_game_shutout_is_a_real_zero(self):
        lines = recon.extract_pitcher_lines(_boxscore([("1", 100, 27, 0, 0, 0, 0)]), "home")
        outcome = recon.relief_outcome_for_game(lines)
        assert outcome == {
            "reliefRunsAllowed": 0, "reliefEarnedRunsAllowed": 0,
            "bullpenOuts": 0, "bullpenInningsPitched": 0.0,
            "numberOfRelieversUsed": 0, "fullGameTeamRunsAllowed": 0,
        }

    def test_relief_runs_summed_across_relievers_only(self):
        box = _boxscore([
            ("1", 90, 18, 0, 0, 2, 2),   # starter -- excluded from relief totals
            ("2", 15, 3, 0, 0, 1, 1),
            ("3", 10, 3, 1, 0, 0, 0),
            ("4", 12, 3, 0, 0, 2, 1),
        ])
        lines = recon.extract_pitcher_lines(box, "home")
        outcome = recon.relief_outcome_for_game(lines)
        assert outcome["reliefRunsAllowed"] == 3
        assert outcome["reliefEarnedRunsAllowed"] == 2
        assert outcome["numberOfRelieversUsed"] == 3
        assert outcome["bullpenInningsPitched"] == 3.0
        assert outcome["fullGameTeamRunsAllowed"] == 5

    def test_missing_runs_data_on_a_reliever_invalidates_the_whole_outcome(self):
        box = _boxscore([("1", 90, 18, 0, 0, 2, 2), ("2", 15, 3, 0, 0, None, 0)])
        lines = recon.extract_pitcher_lines(box, "home")
        assert recon.relief_outcome_for_game(lines) is None

    def test_empty_pitcher_lines_returns_none(self):
        assert recon.relief_outcome_for_game([]) is None


# ── deterministic reconstruction ─────────────────────────────────────────

def test_reconstruction_is_deterministic_across_repeated_calls():
    target = {"date": "2026-08-15", "gameNumber": 1}
    all_games = [
        _team_game("2026-08-14", 1, [_appearance("A", pitches=30, saves=1), _appearance("B", pitches=10)]),
        _team_game("2026-08-13", 2, [_appearance("A", pitches=20)]),
    ]
    f1 = recon.reconstruct_workload_features(all_games, target)
    f2 = recon.reconstruct_workload_features(all_games, target)
    assert f1 == f2
    m1 = recon.current_production_multiplier(f1)
    m2 = recon.current_production_multiplier(f2)
    assert m1 == m2
