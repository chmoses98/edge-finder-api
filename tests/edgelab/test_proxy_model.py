import pytest

from lib.edgelab.backtest.proxy_model import (
    team_baseline,
    expected_runs,
    game_ml_proxy_probability,
    game_total_proxy_probability,
    fit_home_field_adjustment,
    p_team_wins,
    p_over_total,
    poisson_pmf,
)
import build_market_ledger as bml


def _game(date, runs_scored, runs_allowed=4):
    return {"date": date, "runsScored": runs_scored, "runsAllowed": runs_allowed}


class TestReuseNotReimplementation:
    def test_p_team_wins_is_the_real_production_function(self):
        assert p_team_wins is bml.p_team_wins

    def test_p_over_total_is_the_real_production_function(self):
        assert p_over_total is bml.p_over_total

    def test_poisson_pmf_is_the_real_production_function(self):
        assert poisson_pmf is bml.poisson_pmf


class TestTeamBaseline:
    def test_none_when_insufficient_prior_games(self):
        games = [_game(f"2023-04-{d:02d}", 4) for d in range(1, 10)]
        target = _game("2023-05-01", 5)
        result = team_baseline(games + [target], target)
        assert result is None

    def test_returns_offense_and_run_prevention_once_eligible(self):
        games = [_game(f"2023-04-{d:02d}", 4, runs_allowed=3) for d in range(1, 21)]
        target = _game("2023-05-01", 5)
        result = team_baseline(games + [target], target)
        assert result["offenseRunsPerGame"] == pytest.approx(4.0)
        assert result["runPreventionRunsAllowedPerGame"] == pytest.approx(3.0)


class TestExpectedRuns:
    def test_averages_own_offense_with_opponent_run_prevention(self):
        home_b = {"offenseRunsPerGame": 5.0, "runPreventionRunsAllowedPerGame": 4.0}
        away_b = {"offenseRunsPerGame": 4.0, "runPreventionRunsAllowedPerGame": 3.0}
        eh, ea = expected_runs(home_b, away_b, home_field_adjustment=0.0)
        assert eh == pytest.approx((5.0 + 3.0) / 2)
        assert ea == pytest.approx((4.0 + 4.0) / 2)

    def test_home_field_adjustment_applies_to_home_only(self):
        home_b = {"offenseRunsPerGame": 4.0, "runPreventionRunsAllowedPerGame": 4.0}
        away_b = {"offenseRunsPerGame": 4.0, "runPreventionRunsAllowedPerGame": 4.0}
        eh_adj, ea_adj = expected_runs(home_b, away_b, home_field_adjustment=0.2)
        eh_plain, ea_plain = expected_runs(home_b, away_b, home_field_adjustment=0.0)
        assert eh_adj == pytest.approx(eh_plain + 0.2)
        assert ea_adj == pytest.approx(ea_plain)

    def test_none_on_missing_baseline(self):
        assert expected_runs(None, {"offenseRunsPerGame": 4.0, "runPreventionRunsAllowedPerGame": 4.0}) == (None, None)


class TestGameMlProxyProbability:
    def test_stronger_home_team_favored(self):
        p_home, p_push = game_ml_proxy_probability(5.5, 3.5)
        assert p_home > 0.5

    def test_symmetric_teams_split_evenly_after_excluding_ties(self):
        """Poisson allows literal ties (a real MLB game never ends tied,
        but p_team_wins -- reused unchanged from production -- reports
        that push probability separately); for symmetric teams, home
        and away win probability must be exactly equal, each (1-push)/2."""
        p_home, p_push = game_ml_proxy_probability(4.5, 4.5)
        p_away, _ = game_ml_proxy_probability(4.5, 4.5)  # symmetric call, same args either "side"
        assert p_home == pytest.approx((1 - p_push) / 2, abs=1e-6)

    def test_none_on_missing_input(self):
        assert game_ml_proxy_probability(None, 4.0) == (None, None)


class TestGameTotalProxyProbability:
    def test_higher_expected_runs_higher_over_probability(self):
        low = game_total_proxy_probability(3.0, 3.0, 8.5)
        high = game_total_proxy_probability(5.5, 5.5, 8.5)
        assert high > low

    def test_none_on_missing_line(self):
        assert game_total_proxy_probability(4.0, 4.0, None) is None

    def test_none_on_missing_runs(self):
        assert game_total_proxy_probability(None, 4.0, 8.5) is None


class TestFitHomeFieldAdjustment:
    def test_zero_with_no_usable_rows(self):
        assert fit_home_field_adjustment([]) == 0.0

    def test_recovers_a_known_systematic_home_bias(self):
        """If home teams consistently outscore the naive expectation by
        a fixed amount, the closed-form fit should recover that amount."""
        rows = []
        home_b = {"offenseRunsPerGame": 4.0, "runPreventionRunsAllowedPerGame": 4.0}
        away_b = {"offenseRunsPerGame": 4.0, "runPreventionRunsAllowedPerGame": 4.0}
        for _ in range(20):
            rows.append({
                "homeBaseline": home_b, "awayBaseline": away_b,
                "actualHomeRuns": 4.3, "actualAwayRuns": 4.0,  # +0.3 systematic home bias
            })
        adjustment = fit_home_field_adjustment(rows)
        assert adjustment == pytest.approx(0.3, abs=1e-6)

    def test_skips_rows_with_missing_data(self):
        rows = [{"homeBaseline": None, "awayBaseline": None, "actualHomeRuns": 4, "actualAwayRuns": 3}]
        assert fit_home_field_adjustment(rows) == 0.0

    def test_never_called_twice_in_orchestration_is_enforced_elsewhere(self):
        # Structural note: this function itself is stateless/pure and can
        # be called repeatedly -- the "fit once, freeze" discipline is
        # enforced by the orchestration script calling it exactly once on
        # development rows, proven in that script's own test file.
        rows = [{"homeBaseline": {"offenseRunsPerGame": 4, "runPreventionRunsAllowedPerGame": 4},
                  "awayBaseline": {"offenseRunsPerGame": 4, "runPreventionRunsAllowedPerGame": 4},
                  "actualHomeRuns": 4, "actualAwayRuns": 4}]
        assert fit_home_field_adjustment(rows) == fit_home_field_adjustment(rows)
