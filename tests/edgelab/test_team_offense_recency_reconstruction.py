import pytest

from lib.edgelab.backtest.team_offense_recency_reconstruction import (
    MIN_PRIOR_GAMES_FOR_BASELINE,
    RECENT_FORM_WINDOWS,
    prior_games_this_season,
    season_to_date_rate,
    recent_form_rate,
    reconstruct_offense_features,
    offense_outcome_for_game,
)


def _game(date, runs_scored, runs_allowed=2, game_number=1, opponent_id=999):
    return {
        "gamePk": hash((date, game_number)) % 10_000_000,
        "date": date,
        "side": "home",
        "doubleHeader": "N" if game_number == 1 else "Y",
        "gameNumber": game_number,
        "runsScored": runs_scored,
        "runsAllowed": runs_allowed,
        "opponentTeamId": opponent_id,
    }


def _season_of_games(n, start=1, runs=4):
    return [_game(f"2023-04-{d:02d}" if d <= 30 else f"2023-05-{d - 30:02d}", runs) for d in range(start, start + n)]


class TestPriorGamesLeakage:
    def test_target_game_excluded_from_its_own_prior_games(self):
        games = _season_of_games(5)
        target = games[2]
        prior = prior_games_this_season(games, target)
        assert target not in prior
        assert all(g["date"] < target["date"] for g in prior)

    def test_future_games_excluded(self):
        games = _season_of_games(10)
        target = games[3]
        prior = prior_games_this_season(games, target)
        assert len(prior) == 3

    def test_doubleheader_game_2_sees_game_1_not_vice_versa(self):
        g1 = _game("2023-06-01", 3, game_number=1)
        g2 = _game("2023-06-01", 5, game_number=2)
        games = [g1, g2]
        prior_for_g2 = prior_games_this_season(games, g2)
        prior_for_g1 = prior_games_this_season(games, g1)
        assert g1 in prior_for_g2
        assert g1 not in prior_for_g1
        assert g2 not in prior_for_g2

    def test_same_day_ambiguous_game_number_excluded_both_ways(self):
        g1 = dict(_game("2023-06-01", 3), gameNumber=None)
        g2 = dict(_game("2023-06-01", 5), gameNumber=None)
        games = [g1, g2]
        assert g1 not in prior_games_this_season(games, g2)
        assert g2 not in prior_games_this_season(games, g1)


class TestSeasonBaseline:
    def test_mean_of_prior_games(self):
        games = [_game("2023-04-01", 2), _game("2023-04-02", 4), _game("2023-04-03", 6)]
        rate = season_to_date_rate(games, "runsScored")
        assert rate == 4.0

    def test_none_when_no_games(self):
        assert season_to_date_rate([], "runsScored") is None

    def test_uses_runs_allowed_field_for_opponent_baseline(self):
        games = [_game("2023-04-01", 2, runs_allowed=1), _game("2023-04-02", 4, runs_allowed=3)]
        assert season_to_date_rate(games, "runsAllowed") == 2.0


class TestRecentFormWindows:
    def test_none_when_fewer_prior_games_than_window(self):
        games = _season_of_games(3)
        assert recent_form_rate(games, 5) is None

    def test_uses_exact_most_recent_n_games(self):
        games = [_game(f"2023-04-{d:02d}", d) for d in range(1, 11)]
        rate = recent_form_rate(games, 5)
        assert rate == sum(range(6, 11)) / 5

    @pytest.mark.parametrize("window", RECENT_FORM_WINDOWS)
    def test_all_preregistered_windows_computable_with_enough_history(self, window):
        games = _season_of_games(25)
        assert recent_form_rate(games, window) is not None


class TestEligibilityAndFeatureReconstruction:
    def test_none_when_insufficient_prior_games(self):
        games = _season_of_games(MIN_PRIOR_GAMES_FOR_BASELINE - 1)
        target = _game("2023-06-01", 5)
        all_games = games + [target]
        features = reconstruct_offense_features(all_games, [], target)
        assert features is None

    def test_first_game_of_season_always_ineligible(self):
        target = _game("2023-04-01", 5)
        features = reconstruct_offense_features([target], [], target)
        assert features is None

    def test_eligible_once_min_prior_games_reached(self):
        games = _season_of_games(MIN_PRIOR_GAMES_FOR_BASELINE)
        target = _game("2023-06-01", 5)
        all_games = games + [target]
        features = reconstruct_offense_features(all_games, [], target)
        assert features is not None
        assert features["priorGamesThisSeason"] == MIN_PRIOR_GAMES_FOR_BASELINE

    def test_all_windows_fillable_once_eligible(self):
        games = _season_of_games(MIN_PRIOR_GAMES_FOR_BASELINE)
        target = _game("2023-06-01", 5)
        all_games = games + [target]
        features = reconstruct_offense_features(all_games, [], target)
        for window in RECENT_FORM_WINDOWS:
            assert features[f"recentFormRate_{window}"] is not None
            assert features[f"recentFormDeviation_{window}"] is not None

    def test_deviation_is_recent_minus_baseline(self):
        games = [_game(f"2023-04-{d:02d}", 2) for d in range(1, 21)] + [_game(f"2023-05-{d:02d}", 8) for d in range(1, 6)]
        target = _game("2023-05-10", 9)
        all_games = games + [target]
        features = reconstruct_offense_features(all_games, [], target)
        assert features["seasonToDateRunsPerGame"] == pytest.approx((2 * 20 + 8 * 5) / 25)
        assert features["recentFormRate_5"] == 8.0
        assert features["recentFormDeviation_5"] == pytest.approx(8.0 - features["seasonToDateRunsPerGame"])

    def test_target_games_own_runs_never_enter_its_own_features(self):
        games = _season_of_games(MIN_PRIOR_GAMES_FOR_BASELINE, runs=2)
        target = _game("2023-06-01", 999)
        all_games = games + [target]
        features = reconstruct_offense_features(all_games, [], target)
        assert features["seasonToDateRunsPerGame"] == 2.0
        assert features["recentFormRate_5"] == 2.0

    def test_opponent_baseline_uses_opponent_runs_allowed(self):
        team_games = _season_of_games(MIN_PRIOR_GAMES_FOR_BASELINE)
        opponent_games = [_game(f"2023-04-{d:02d}", 5, runs_allowed=1) for d in range(1, 21)]
        target = _game("2023-06-01", 5)
        all_games = team_games + [target]
        features = reconstruct_offense_features(all_games, opponent_games, target)
        assert features["opponentSeasonToDateRunsAllowedPerGame"] == 1.0

    def test_deterministic_across_repeated_calls(self):
        games = _season_of_games(30)
        target = _game("2023-06-01", 5)
        all_games = games + [target]
        first = reconstruct_offense_features(all_games, [], target)
        second = reconstruct_offense_features(all_games, [], target)
        assert first == second


class TestOffenseOutcome:
    def test_basic_fields(self):
        outcome = offense_outcome_for_game(_game("2023-06-01", 4))
        assert outcome["runsScored"] == 4
        assert outcome["scored3Plus"] is True
        assert outcome["scored4Plus"] is True
        assert outcome["scored5Plus"] is False
        assert outcome["shutout"] is False

    def test_shutout(self):
        outcome = offense_outcome_for_game(_game("2023-06-01", 0))
        assert outcome["shutout"] is True
        assert outcome["scored3Plus"] is False

    def test_none_when_runs_missing(self):
        g = _game("2023-06-01", 4)
        g["runsScored"] = None
        assert offense_outcome_for_game(g) is None
