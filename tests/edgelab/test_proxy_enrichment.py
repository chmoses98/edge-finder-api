#!/usr/bin/env python3
"""tests/edgelab/test_proxy_enrichment.py"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.backtest.proxy_enrichment import (
    fit_league_average_runs_per_game,
    stabilized_offense_rate,
    OFFENSE_SHRINKAGE_K,
    team_relief_er9_games,
    bullpen_quality_baseline,
    fit_league_average_bullpen_er9,
    stabilized_bullpen_rate,
    blend_run_prevention_with_bullpen_quality,
    BULLPEN_BLEND_WEIGHT,
    extract_team_games_with_venue,
    fit_park_factors,
    season_run_environment,
    fit_reference_season_run_environment,
    park_and_environment_multiplier,
    apply_runs_multiplier,
    PARK_MIN_DEV_GAMES,
)


def _game(date, game_number=1, **kw):
    d = {"date": date, "gameNumber": game_number, "gamePk": kw.pop("gamePk", hash(date) % 100000)}
    d.update(kw)
    return d


class TestOffenseStabilization:
    def test_league_average_is_mean_of_all_runs_scored(self):
        team_a = [_game("2023-04-01", runsScored=4), _game("2023-04-02", runsScored=6)]
        team_b = [_game("2023-04-01", runsScored=2)]
        avg = fit_league_average_runs_per_game([team_a, team_b])
        assert avg == 4.0

    def test_empty_input_returns_none(self):
        assert fit_league_average_runs_per_game([]) is None
        assert fit_league_average_runs_per_game([[]]) is None

    def test_shrinkage_pulls_small_sample_toward_league_average(self):
        # n=1 prior game, raw rate far from league average -> heavily shrunk
        shrunk = stabilized_offense_rate(raw_rate=10.0, prior_game_count=1, league_avg=4.0, k=OFFENSE_SHRINKAGE_K)
        assert 4.0 < shrunk < 10.0
        assert shrunk < 5.0  # k=30 >> n=1, so mostly pulled to league avg

    def test_shrinkage_barely_moves_large_sample(self):
        shrunk = stabilized_offense_rate(raw_rate=5.0, prior_game_count=140, league_avg=4.0, k=OFFENSE_SHRINKAGE_K)
        assert 4.8 < shrunk < 5.0

    def test_missing_inputs_pass_through_raw_rate_unchanged(self):
        assert stabilized_offense_rate(None, 20, 4.0) is None
        assert stabilized_offense_rate(5.0, 20, None) == 5.0
        assert stabilized_offense_rate(5.0, None, 4.0) == 5.0


class TestBullpenQuality:
    def test_relief_er9_uses_bullpen_outs_and_earned_runs(self):
        team_games = [_game("2023-04-01", gamePk=1), _game("2023-04-02", gamePk=2)]
        outcomes = {
            1: {"bullpenOuts": 9, "reliefEarnedRunsAllowed": 3},   # 3 ER over 3 IP = 9.0 ER/9
            2: {"bullpenOuts": 0, "reliefEarnedRunsAllowed": 0},   # complete game -- undefined, not zero
        }
        out = team_relief_er9_games(team_games, outcomes)
        assert out[0]["reliefEarnedRunsPer9"] == 9.0
        assert out[1]["reliefEarnedRunsPer9"] is None

    def test_missing_outcome_for_game_is_none(self):
        team_games = [_game("2023-04-01", gamePk=1)]
        out = team_relief_er9_games(team_games, {})
        assert out[0]["reliefEarnedRunsPer9"] is None

    def test_baseline_requires_min_prior_games_with_defined_rate(self):
        games = [{**_game(f"2023-04-{i:02d}", gamePk=i), "reliefEarnedRunsPer9": 4.0} for i in range(1, 20)]
        as_of = _game("2023-05-01", gamePk=999)
        assert bullpen_quality_baseline(games, as_of, min_prior_games=20) is None  # only 19 prior

    def test_baseline_excludes_undefined_rate_games_from_eligibility_count(self):
        games = (
            [{**_game(f"2023-04-{i:02d}", gamePk=i), "reliefEarnedRunsPer9": 4.0} for i in range(1, 21)]
            + [{**_game(f"2023-05-{i:02d}", gamePk=100 + i), "reliefEarnedRunsPer9": None} for i in range(1, 21)]
        )
        as_of = _game("2023-06-01", gamePk=999)
        result = bullpen_quality_baseline(games, as_of, min_prior_games=20)
        assert result is not None
        assert result["priorGamesWithBullpenData"] == 20  # the None-rate games don't count
        assert result["bullpenEarnedRunsPer9"] == 4.0

    def test_league_average_bullpen_er9_ignores_none_values(self):
        team_a = [{"reliefEarnedRunsPer9": 4.0}, {"reliefEarnedRunsPer9": None}]
        team_b = [{"reliefEarnedRunsPer9": 6.0}]
        assert fit_league_average_bullpen_er9([team_a, team_b]) == 5.0

    def test_bullpen_shrinkage_shape_matches_offense(self):
        shrunk = stabilized_bullpen_rate(raw_rate=8.0, prior_game_count=1, league_avg=4.0)
        assert 4.0 < shrunk < 8.0

    def test_blend_returns_base_unchanged_when_bullpen_unavailable(self):
        assert blend_run_prevention_with_bullpen_quality(4.5, None) == 4.5

    def test_blend_returns_none_when_base_missing(self):
        assert blend_run_prevention_with_bullpen_quality(None, 4.0) is None

    def test_blend_weight_is_fixed_and_symmetric_at_default(self):
        blended = blend_run_prevention_with_bullpen_quality(4.0, 6.0, weight=BULLPEN_BLEND_WEIGHT)
        assert blended == 5.0  # equal-weight midpoint


class TestParkAndEnvironment:
    def test_extract_team_games_with_venue_carries_venue_fields(self):
        schedule = {"dates": [{"date": "2023-04-01", "games": [{
            "gamePk": 1, "gameNumber": 1, "doubleHeader": "N",
            "status": {"detailedState": "Final"},
            "teams": {
                "home": {"team": {"id": 119}, "score": 5},
                "away": {"team": {"id": 121}, "score": 3},
            },
            "venue": {"id": 22, "name": "Dodger Stadium"},
        }]}]}
        games = extract_team_games_with_venue(schedule, team_id=119)
        assert games[0]["venueId"] == 22
        assert games[0]["venueName"] == "Dodger Stadium"
        assert games[0]["runsScored"] == 5
        assert games[0]["runsAllowed"] == 3

    def test_park_factors_omit_undersampled_venues(self):
        games = [{"venueId": 1, "runsScored": 5, "runsAllowed": 5}] * 10  # far below PARK_MIN_DEV_GAMES
        factors = fit_park_factors(games, min_dev_games=PARK_MIN_DEV_GAMES)
        assert factors == {}

    def test_park_factors_index_100_for_league_average_venue(self):
        # Three venues at league average -> that average IS the league
        # average, so their own index is exactly 100.
        venue_a = [{"venueId": 1, "runsScored": 4, "runsAllowed": 4}] * 150
        venue_b = [{"venueId": 2, "runsScored": 4, "runsAllowed": 4}] * 150
        factors = fit_park_factors(venue_a + venue_b, min_dev_games=100)
        assert factors[1]["parkRunIndex"] == 100.0
        assert factors[2]["parkRunIndex"] == 100.0

    def test_park_factors_reflect_relative_scoring_level(self):
        low_venue = [{"venueId": 1, "runsScored": 4, "runsAllowed": 4}] * 150
        high_venue = [{"venueId": 2, "runsScored": 6, "runsAllowed": 6}] * 150
        factors = fit_park_factors(low_venue + high_venue, min_dev_games=100)
        assert factors[1]["parkRunIndex"] < 100.0
        assert factors[2]["parkRunIndex"] > 100.0

    def test_season_run_environment_excludes_same_date_and_future_games(self):
        team = [
            {"side": "home", "date": "2023-04-01", "runsScored": 4, "runsAllowed": 4},
            {"side": "home", "date": "2023-04-05", "runsScored": 10, "runsAllowed": 10},  # same date as as_of -- excluded
            {"side": "home", "date": "2023-04-10", "runsScored": 20, "runsAllowed": 20},  # future -- excluded
        ]
        env = season_run_environment([team], as_of_date="2023-04-05")
        assert env == 8.0  # only the 2023-04-01 game (4+4=8), never the 04-05 or 04-10 games

    def test_season_run_environment_none_when_no_qualifying_games(self):
        assert season_run_environment([[]], as_of_date="2023-04-01") is None

    def test_reference_environment_is_mean_of_all_development_totals(self):
        games = [{"runsScored": 4, "runsAllowed": 4}, {"runsScored": 6, "runsAllowed": 6}]
        assert fit_reference_season_run_environment(games) == 10.0

    def test_multiplier_is_1_when_venue_unmeasured(self):
        m = park_and_environment_multiplier(999, {}, 8.5, 8.0)
        assert m == 1.0

    def test_multiplier_combines_park_and_season_index(self):
        factors = {1: {"parkRunIndex": 110.0}}
        m = park_and_environment_multiplier(1, factors, season_env_runs_per_game=8.8, reference_env_runs_per_game=8.0)
        # park index 110/100 * season index (8.8/8.0*100)/100 = 1.10 * 1.10 = 1.21
        assert abs(m - 1.21) < 1e-6

    def test_apply_runs_multiplier_scales_both_sides_equally(self):
        h, a = apply_runs_multiplier(4.0, 3.0, 1.1)
        assert h == 4.4 and a == 3.3

    def test_apply_runs_multiplier_none_passthrough(self):
        assert apply_runs_multiplier(None, 3.0, 1.1) == (None, None)
