import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.backtest import starter_workload_reconstruction as recon


def _pitcher_line(player_id, order_index=0, pitches=90, outs=18, runs=2, earned=2, hits=6, walks=2, k=6, bf=27):
    return {
        "playerId": player_id, "orderIndex": order_index, "name": player_id,
        "numberOfPitches": pitches, "outs": outs, "saves": 0, "holds": 0,
        "runs": runs, "earnedRuns": earned, "battersFaced": bf,
        "strikeOuts": k, "baseOnBalls": walks, "hits": hits,
    }


def _game(date, game_pk, away_starter=None, home_starter=None, game_number=1, double_header="N"):
    return {
        "date": date, "gamePk": game_pk, "gameNumber": game_number, "doubleHeader": double_header,
        "awayPitchers": [away_starter] if away_starter else [],
        "homePitchers": [home_starter] if home_starter else [],
    }


SEASON_START = "2024-01-01"


# ── build_pitcher_start_index ────────────────────────────────────────────

class TestBuildPitcherStartIndex:
    def test_indexes_by_player_id_across_both_sides(self):
        games = [
            _game("2024-04-01", 1, home_starter=_pitcher_line("P1")),
            _game("2024-04-06", 2, away_starter=_pitcher_line("P1")),
        ]
        idx = recon.build_pitcher_start_index(games)
        assert len(idx["P1"]) == 2
        assert [s["gamePk"] for s in idx["P1"]] == [1, 2]

    def test_sorted_by_date_then_game_number(self):
        games = [
            _game("2024-04-06", 2, home_starter=_pitcher_line("P1")),
            _game("2024-04-01", 1, home_starter=_pitcher_line("P1")),
        ]
        idx = recon.build_pitcher_start_index(games)
        assert [s["gamePk"] for s in idx["P1"]] == [1, 2]

    def test_handles_a_midseason_trade_via_team_field_not_index_key(self):
        """A pitcher starting for team A then team B is still one
        continuous history under their own playerId."""
        games = [
            _game("2024-04-01", 1, home_starter=_pitcher_line("P1")),  # team A (home side)
            _game("2024-07-01", 2, away_starter=_pitcher_line("P1")),  # team B (away side, post-trade)
        ]
        idx = recon.build_pitcher_start_index(games)
        assert len(idx["P1"]) == 2
        assert idx["P1"][0]["team"] == "home"
        assert idx["P1"][1]["team"] == "away"

    def test_only_orderIndex_zero_counts_as_a_start(self):
        reliever = _pitcher_line("R1", order_index=1)
        games = [_game("2024-04-01", 1, home_starter=None)]
        games[0]["homePitchers"] = [_pitcher_line("SP1", order_index=0), reliever]
        idx = recon.build_pitcher_start_index(games)
        assert "R1" not in idx
        assert "SP1" in idx


# ── eligibility: first start of season excluded ─────────────────────────

class TestEligibility:
    def test_first_start_of_season_returns_none(self):
        target = {"date": "2024-04-01", "gameNumber": 1}
        starts = [{"playerId": "P1", "date": "2024-04-01", "gameNumber": 1, "gamePk": 1,
                   "doubleHeader": "N", "team": "home", "pitcherLine": _pitcher_line("P1")}]
        assert recon.reconstruct_starter_features(starts, target, SEASON_START) is None

    def test_prior_start_from_previous_season_is_excluded_by_season_start_date(self):
        target = {"date": "2024-04-05", "gameNumber": 1}
        starts = [
            {"playerId": "P1", "date": "2023-09-30", "gameNumber": 1, "gamePk": 0,
             "doubleHeader": "N", "team": "home", "pitcherLine": _pitcher_line("P1")},
        ]
        assert recon.reconstruct_starter_features(starts, target, SEASON_START) is None

    def test_one_prior_start_this_season_is_sufficient(self):
        target = {"date": "2024-04-06", "gameNumber": 1}
        starts = [
            {"playerId": "P1", "date": "2024-04-01", "gameNumber": 1, "gamePk": 1,
             "doubleHeader": "N", "team": "home", "pitcherLine": _pitcher_line("P1")},
        ]
        features = recon.reconstruct_starter_features(starts, target, SEASON_START)
        assert features is not None
        assert features["daysSincePreviousStart"] == 5


# ── leakage: target/future starts never contribute ──────────────────────

class TestLeakageGuard:
    def test_target_starts_own_line_excluded(self):
        target = {"date": "2024-04-06", "gameNumber": 1}
        starts = [
            {"playerId": "P1", "date": "2024-04-01", "gameNumber": 1, "gamePk": 1,
             "doubleHeader": "N", "team": "home", "pitcherLine": _pitcher_line("P1", pitches=90)},
            {"playerId": "P1", "date": "2024-04-06", "gameNumber": 1, "gamePk": 2,
             "doubleHeader": "N", "team": "home", "pitcherLine": _pitcher_line("P1", pitches=999)},
        ]
        features = recon.reconstruct_starter_features(starts, target, SEASON_START)
        assert features["previousStartPitches"] == 90

    def test_future_starts_excluded(self):
        target = {"date": "2024-04-06", "gameNumber": 1}
        starts = [
            {"playerId": "P1", "date": "2024-04-01", "gameNumber": 1, "gamePk": 1,
             "doubleHeader": "N", "team": "home", "pitcherLine": _pitcher_line("P1", pitches=90)},
            {"playerId": "P1", "date": "2024-04-11", "gameNumber": 1, "gamePk": 3,
             "doubleHeader": "N", "team": "home", "pitcherLine": _pitcher_line("P1", pitches=999)},
        ]
        features = recon.reconstruct_starter_features(starts, target, SEASON_START)
        assert features["previousStartPitches"] == 90
        assert features["priorSeasonToDateStarts"] == 1

    def test_doubleheader_game_two_sees_game_one_start(self):
        g2 = {"date": "2024-04-06", "gameNumber": 2}
        starts = [
            {"playerId": "P1", "date": "2024-04-01", "gameNumber": 1, "gamePk": 1,
             "doubleHeader": "N", "team": "home", "pitcherLine": _pitcher_line("P1", pitches=90)},
            {"playerId": "P1", "date": "2024-04-06", "gameNumber": 1, "gamePk": 2,
             "doubleHeader": "Y", "team": "home", "pitcherLine": _pitcher_line("P1", pitches=85)},
        ]
        features = recon.reconstruct_starter_features(starts, g2, SEASON_START)
        assert features["previousStartPitches"] == 85

    def test_same_day_ambiguous_game_number_excluded(self):
        g2 = {"date": "2024-04-06", "gameNumber": 2}
        starts = [
            {"playerId": "P1", "date": "2024-04-01", "gameNumber": 1, "gamePk": 1,
             "doubleHeader": "N", "team": "home", "pitcherLine": _pitcher_line("P1", pitches=90)},
            {"playerId": "P1", "date": "2024-04-06", "gameNumber": None, "gamePk": 2,
             "doubleHeader": None, "team": "home", "pitcherLine": _pitcher_line("P1", pitches=999)},
        ]
        features = recon.reconstruct_starter_features(starts, g2, SEASON_START)
        assert features["previousStartPitches"] == 90


# ── rest categories ───────────────────────────────────────────────────────

class TestRestCategories:
    def test_short_rest(self):
        target = {"date": "2024-04-05", "gameNumber": 1}
        starts = [{"playerId": "P1", "date": "2024-04-01", "gameNumber": 1, "gamePk": 1,
                   "doubleHeader": "N", "team": "home", "pitcherLine": _pitcher_line("P1")}]
        f = recon.reconstruct_starter_features(starts, target, SEASON_START)
        assert f["daysSincePreviousStart"] == 4
        assert f["restCategory"] == recon.REST_SHORT

    def test_normal_rest(self):
        target = {"date": "2024-04-06", "gameNumber": 1}
        starts = [{"playerId": "P1", "date": "2024-04-01", "gameNumber": 1, "gamePk": 1,
                   "doubleHeader": "N", "team": "home", "pitcherLine": _pitcher_line("P1")}]
        f = recon.reconstruct_starter_features(starts, target, SEASON_START)
        assert f["daysSincePreviousStart"] == 5
        assert f["restCategory"] == recon.REST_NORMAL

    def test_extended_rest(self):
        target = {"date": "2024-04-07", "gameNumber": 1}
        starts = [{"playerId": "P1", "date": "2024-04-01", "gameNumber": 1, "gamePk": 1,
                   "doubleHeader": "N", "team": "home", "pitcherLine": _pitcher_line("P1")}]
        f = recon.reconstruct_starter_features(starts, target, SEASON_START)
        assert f["daysSincePreviousStart"] == 6
        assert f["restCategory"] == recon.REST_EXTENDED

    def test_unusually_long_rest_flag(self):
        target = {"date": "2024-04-15", "gameNumber": 1}
        starts = [{"playerId": "P1", "date": "2024-04-01", "gameNumber": 1, "gamePk": 1,
                   "doubleHeader": "N", "team": "home", "pitcherLine": _pitcher_line("P1")}]
        f = recon.reconstruct_starter_features(starts, target, SEASON_START)
        assert f["daysSincePreviousStart"] == 14
        assert f["returnFromUnusuallyLongRest"] is True


# ── workload windows ──────────────────────────────────────────────────────

class TestWorkloadWindows:
    def test_rolling_2_and_3_start_windows(self):
        target = {"date": "2024-04-25", "gameNumber": 1}
        starts = [
            {"playerId": "P1", "date": "2024-04-01", "gameNumber": 1, "gamePk": 1, "doubleHeader": "N",
             "team": "home", "pitcherLine": _pitcher_line("P1", pitches=80, outs=15)},
            {"playerId": "P1", "date": "2024-04-10", "gameNumber": 1, "gamePk": 2, "doubleHeader": "N",
             "team": "home", "pitcherLine": _pitcher_line("P1", pitches=90, outs=18)},
            {"playerId": "P1", "date": "2024-04-20", "gameNumber": 1, "gamePk": 3, "doubleHeader": "N",
             "team": "home", "pitcherLine": _pitcher_line("P1", pitches=100, outs=18)},
        ]
        f = recon.reconstruct_starter_features(starts, target, SEASON_START)
        assert f["pitchesOverPrior2Starts"] == 190
        assert f["pitchesOverPrior3Starts"] == 270
        assert f["inningsOverPrior2Starts"] == 12.0
        assert f["priorSeasonToDateStarts"] == 3
        assert f["priorSeasonToDatePitches"] == 270

    def test_high_pitch_count_and_stressful_flags(self):
        target = {"date": "2024-04-10", "gameNumber": 1}
        starts = [{"playerId": "P1", "date": "2024-04-01", "gameNumber": 1, "gamePk": 1, "doubleHeader": "N",
                   "team": "home", "pitcherLine": _pitcher_line("P1", pitches=105, outs=15)}]
        f = recon.reconstruct_starter_features(starts, target, SEASON_START)
        assert f["highPitchCountPreviousStart"] is True
        assert f["previousStartPitchesPerOut"] == 7.0
        assert f["stressfulPreviousStart"] is True

    def test_not_high_pitch_count_when_below_threshold(self):
        target = {"date": "2024-04-10", "gameNumber": 1}
        starts = [{"playerId": "P1", "date": "2024-04-01", "gameNumber": 1, "gamePk": 1, "doubleHeader": "N",
                   "team": "home", "pitcherLine": _pitcher_line("P1", pitches=80, outs=18)}]
        f = recon.reconstruct_starter_features(starts, target, SEASON_START)
        assert f["highPitchCountPreviousStart"] is False
        assert f["stressfulPreviousStart"] is False

    def test_workload_relative_to_own_baseline(self):
        target = {"date": "2024-05-01", "gameNumber": 1}
        starts = [
            {"playerId": "P1", "date": "2024-04-01", "gameNumber": 1, "gamePk": 1, "doubleHeader": "N",
             "team": "home", "pitcherLine": _pitcher_line("P1", pitches=80, outs=18)},
            {"playerId": "P1", "date": "2024-04-10", "gameNumber": 1, "gamePk": 2, "doubleHeader": "N",
             "team": "home", "pitcherLine": _pitcher_line("P1", pitches=120, outs=18)},
        ]
        f = recon.reconstruct_starter_features(starts, target, SEASON_START)
        # own baseline = (80+120)/2 = 100; most recent (120) / 100 = 1.2
        assert f["workloadRelativeToOwnBaseline"] == 1.2

    def test_own_baseline_runs_per_9(self):
        target = {"date": "2024-04-10", "gameNumber": 1}
        starts = [{"playerId": "P1", "date": "2024-04-01", "gameNumber": 1, "gamePk": 1, "doubleHeader": "N",
                   "team": "home", "pitcherLine": _pitcher_line("P1", pitches=90, outs=18, earned=2)}]
        f = recon.reconstruct_starter_features(starts, target, SEASON_START)
        assert f["ownBaselineRunsPer9"] == 3.0  # 2 earned / 18 outs * 27


# ── outcome ────────────────────────────────────────────────────────────────

class TestStarterOutcome:
    def test_basic_outcome_fields(self):
        line = _pitcher_line("P1", pitches=95, outs=18, runs=3, earned=2, hits=7, walks=2, k=8)
        outcome = recon.starter_outcome_for_start(line)
        assert outcome["starterRunsPer9"] == 4.5
        assert outcome["starterEarnedRunsPer9"] == 3.0
        assert outcome["starterInningsPitched"] == 6.0
        assert outcome["whipLike"] == 1.5
        assert outcome["completedFiveInnings"] is True

    def test_did_not_complete_five_innings(self):
        line = _pitcher_line("P1", outs=14)
        outcome = recon.starter_outcome_for_start(line)
        assert outcome["completedFiveInnings"] is False

    def test_zero_outs_returns_none(self):
        line = _pitcher_line("P1", outs=0)
        assert recon.starter_outcome_for_start(line) is None

    def test_missing_runs_returns_none(self):
        line = _pitcher_line("P1")
        line["runs"] = None
        assert recon.starter_outcome_for_start(line) is None

    def test_none_line_returns_none(self):
        assert recon.starter_outcome_for_start(None) is None


def test_deterministic_across_repeated_calls():
    target = {"date": "2024-04-25", "gameNumber": 1}
    starts = [
        {"playerId": "P1", "date": "2024-04-01", "gameNumber": 1, "gamePk": 1, "doubleHeader": "N",
         "team": "home", "pitcherLine": _pitcher_line("P1", pitches=80, outs=15)},
        {"playerId": "P1", "date": "2024-04-10", "gameNumber": 1, "gamePk": 2, "doubleHeader": "N",
         "team": "home", "pitcherLine": _pitcher_line("P1", pitches=90, outs=18)},
    ]
    f1 = recon.reconstruct_starter_features(starts, target, SEASON_START)
    f2 = recon.reconstruct_starter_features(starts, target, SEASON_START)
    assert f1 == f2
