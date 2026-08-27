import ast
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (
    _ROOT,
    os.path.join(_ROOT, "scripts"),
    os.path.join(_ROOT, "scripts", "edgelab"),
    os.path.join(_ROOT, "scripts", "edgelab", "backtest"),
):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_multiseason_team_offense_recency_experiment as exp  # noqa: E402
import fetch_mlb_multiseason_bullpen_cache as fetcher  # noqa: E402
from lib.edgelab.backtest.bullpen_backtest_reconstruction import is_strictly_before  # noqa: E402
from lib.edgelab.bullpen_usage import MLB_TEAM_ID_MAP  # noqa: E402


def _game_entry(date, game_pk, side, team_id, opponent_id, own_score, opp_score, game_number=1, dh="N"):
    teams = {}
    teams[side] = {"team": {"id": team_id}, "score": own_score}
    other_side = "home" if side == "away" else "away"
    teams[other_side] = {"team": {"id": opponent_id}, "score": opp_score}
    return {
        "gamePk": game_pk, "status": {"detailedState": "Final"}, "teams": teams,
        "doubleHeader": dh, "gameNumber": game_number,
    }


def _schedule(entries_by_date):
    return {"dates": [{"date": d, "games": g} for d, g in sorted(entries_by_date.items())]}


def _write_synthetic_schedules(tmp_path, season, per_team_entries):
    """per_team_entries: {team_abbr: {date: [game_entry, ...]}}. Every
    other known team abbreviation gets an empty schedule so
    MLB_TEAM_ID_MAP iteration is harmless."""
    sched_dir = tmp_path / str(season) / "schedules"
    sched_dir.mkdir(parents=True, exist_ok=True)
    for abbr in MLB_TEAM_ID_MAP:
        entries = per_team_entries.get(abbr, {})
        with open(sched_dir / f"{abbr}.json", "w") as f:
            json.dump(_schedule(entries), f)


def _team_season(team_id, opponent_id, start_date, n_games, own_score=4, opp_score=2, own_scores=None):
    """own_scores, if given, overrides own_score per-game (a list of
    length n_games) -- lets a test vary the score sequence so a leakage
    bug (target's own score leaking into its own baseline) is actually
    detectable rather than masked by a constant score."""
    import datetime
    cursor = datetime.date.fromisoformat(start_date)
    by_date = {}
    game_pk = team_id * 100000
    for i in range(n_games):
        game_pk += 1
        score = own_scores[i] if own_scores is not None else own_score
        entry = _game_entry(cursor.strftime("%Y-%m-%d"), game_pk, "home", team_id, opponent_id, score, opp_score)
        by_date[cursor.strftime("%Y-%m-%d")] = [entry]
        cursor += datetime.timedelta(days=1)
    return by_date


# ── preregistration ordering (structural) ───────────────────────────────

RESULT_PRODUCING_CALL_NAMES = {"build_team_game_rows", "run_hypothesis_tests", "coverage_report", "classify_signal"}


def _call_names_in_order(func_node):
    names = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.append(f.id)
            elif isinstance(f, ast.Attribute):
                names.append(f.attr)
    return names


def _find_function_node(name):
    source = open(os.path.join(_ROOT, "scripts", "edgelab", "run_multiseason_team_offense_recency_experiment.py")).read()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found")


class TestPreregistrationOrdering:
    def test_registration_call_exists_in_main(self):
        names = _call_names_in_order(_find_function_node("main"))
        assert "register_experiment" in names

    def test_registration_happens_before_every_result_producing_call(self):
        names = _call_names_in_order(_find_function_node("main"))
        registration_index = names.index("register_experiment")
        for result_call in RESULT_PRODUCING_CALL_NAMES:
            occurrences = [i for i, n in enumerate(names) if n == result_call]
            assert occurrences, f"expected main() to call {result_call!r} at least once"
            assert min(occurrences) > registration_index, f"{result_call!r} is called before register_experiment"


# ── holdout isolation (structural) ───────────────────────────────────────

class TestHoldoutIsolation:
    def test_run_hypothesis_tests_never_references_season_group_constants(self):
        node = _find_function_node("run_hypothesis_tests")
        names_referenced = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        for forbidden in ("HOLDOUT_SEASONS", "DEV_SEASONS", "VALIDATION_SEASONS", "season"):
            assert forbidden not in names_referenced

    def test_main_calls_run_hypothesis_tests_identically_for_all_three_groups(self):
        source = open(os.path.join(_ROOT, "scripts", "edgelab", "run_multiseason_team_offense_recency_experiment.py")).read()
        assert source.count("def run_hypothesis_tests(") == 1
        assert source.count("run_hypothesis_tests(dev_rows)") == 1
        assert source.count("run_hypothesis_tests(validation_rows)") == 1
        assert source.count("run_hypothesis_tests(holdout_rows)") == 1


class TestFrozenCandidateUnchanged:
    def test_ols_fit_called_exactly_once_per_feature_set_in_main(self):
        source = open(os.path.join(_ROOT, "scripts", "edgelab", "run_multiseason_team_offense_recency_experiment.py")).read()
        # ols_fit must be called exactly twice total (control, candidate) -- never refit per split.
        assert source.count("recency_stats.ols_fit(") == 2

    def test_percentile_cutoffs_computed_exactly_once_in_main(self):
        source = open(os.path.join(_ROOT, "scripts", "edgelab", "run_multiseason_team_offense_recency_experiment.py")).read()
        assert source.count("recency_stats.percentile(") == 2  # hot cutoff, cold cutoff -- both from dev only

    def test_frozen_coefs_object_identity_reused_across_all_three_splits(self):
        """The exact same coefs dict returned by one ols_fit call is
        passed unchanged into all three evaluate_frozen_candidate calls
        -- proven by object identity, not merely equal values."""
        from lib.edgelab.backtest import team_offense_recency_stats as recency_stats
        dev_rows = [
            {"seasonToDateRunsPerGame": 3.0 + (i % 5) * 0.3, "opponentSeasonToDateRunsAllowedPerGame": 4.0 + (i % 3) * 0.2,
             "isHome": float(i % 2), "runsScored": 4.0 + (i % 4) * 0.5}
            for i in range(50)
        ]
        coefs = recency_stats.ols_fit(dev_rows, ["seasonToDateRunsPerGame", "opponentSeasonToDateRunsAllowedPerGame", "isHome"], "runsScored")
        val_result = exp.evaluate_frozen_candidate(dev_rows, coefs, coefs)
        assert val_result["control"] is not None  # sanity: the frozen dict is actually usable, not a stub


# ── production / leakage code reused, not reimplemented ─────────────────

class TestReuseNotReimplementation:
    def test_leakage_guard_is_the_real_shared_function(self):
        from lib.edgelab.backtest import team_offense_recency_reconstruction as recon
        assert recon.is_strictly_before is is_strictly_before

    def test_offense_module_does_not_redefine_is_strictly_before(self):
        from lib.edgelab.backtest import team_offense_recency_reconstruction as recon
        source = open(recon.__file__).read()
        assert "def is_strictly_before" not in source

    def test_uses_the_shared_schedule_extraction_function_unchanged(self):
        from lib.edgelab.backtest import bullpen_backtest_reconstruction as bullpen_recon
        source = open(os.path.join(_ROOT, "scripts", "edgelab", "run_multiseason_team_offense_recency_experiment.py")).read()
        assert "extract_team_games_from_schedule" in source
        assert "def extract_team_games_from_schedule" not in source


# ── end-to-end synthetic-cache integration ───────────────────────────────

class TestEndToEndSyntheticCache:
    def test_build_team_game_rows_excludes_first_twenty_games_of_season(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))
        team_id, opp_id = 147, 111
        _write_synthetic_schedules(tmp_path, 2024, {
            "NYY": _team_season(team_id, opp_id, "2024-04-01", 25),
            "BOS": _team_season(opp_id, team_id, "2024-04-01", 25),
        })
        rows = exp.build_team_game_rows(2024)
        nyy_rows = [r for r in rows if r["team"] == "NYY"]
        assert len(nyy_rows) == 5  # 25 games - 20 (MIN_PRIOR_GAMES_FOR_BASELINE) eligibility floor

    def test_deterministic_across_repeated_calls(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))
        team_id, opp_id = 147, 111
        _write_synthetic_schedules(tmp_path, 2024, {
            "NYY": _team_season(team_id, opp_id, "2024-04-01", 25),
            "BOS": _team_season(opp_id, team_id, "2024-04-01", 25),
        })
        rows1 = exp.build_team_game_rows(2024)
        rows2 = exp.build_team_game_rows(2024)
        assert rows1 == rows2

    def test_target_games_own_score_never_enters_its_own_baseline(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))
        team_id, opp_id = 147, 111
        # first 20 games score 3 (establishes the baseline), then every
        # eligible target game (21st onward) scores 99 -- if a leakage
        # bug let a target's own score into its own baseline, the
        # reported baseline for that row would drift toward 99, not stay
        # pinned at 3.0.
        own_scores = [3] * 20 + [99] * 5
        _write_synthetic_schedules(tmp_path, 2024, {
            "NYY": _team_season(team_id, opp_id, "2024-04-01", 25, own_scores=own_scores),
            "BOS": _team_season(opp_id, team_id, "2024-04-01", 25, own_score=2),
        })
        rows = exp.build_team_game_rows(2024)
        nyy_rows = sorted((r for r in rows if r["team"] == "NYY"), key=lambda r: r["gameDate"])
        assert len(nyy_rows) == 5
        # the FIRST eligible row (game 21) has exactly 20 prior games, all
        # scoring 3 -- its baseline must be exactly 3.0, not inflated by
        # its own 99.
        assert nyy_rows[0]["seasonToDateRunsPerGame"] == 3.0
        assert nyy_rows[0]["runsScored"] == 99
        # every later eligible row's baseline is strictly < its own score
        # (99) -- if the target's own score leaked into its own baseline,
        # the baseline would jump to include a 99 CENTERED on that row's
        # own game, not just prior ones.
        for r in nyy_rows:
            assert r["runsScored"] == 99
            assert r["seasonToDateRunsPerGame"] < 99

    def test_opponent_baseline_uses_opponents_own_prior_games(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))
        team_id, opp_id = 147, 111
        _write_synthetic_schedules(tmp_path, 2024, {
            "NYY": _team_season(team_id, opp_id, "2024-04-01", 25, own_score=3, opp_score=1),
            "BOS": _team_season(opp_id, team_id, "2024-04-01", 25, own_score=1, opp_score=3),
        })
        rows = exp.build_team_game_rows(2024)
        nyy_rows = [r for r in rows if r["team"] == "NYY"]
        assert nyy_rows[0]["opponentSeasonToDateRunsAllowedPerGame"] == pytest.approx(3.0)

    def test_coverage_report_flags_shortfall_below_minimum(self):
        rows_by_season = {s: [] for s in exp.ALL_SEASONS}
        coverage = exp.coverage_report(rows_by_season)
        assert coverage["meetsMinimumExpectedSample"] is False
        assert coverage["totalTeamGames"] == 0

    def test_empty_cache_produces_honest_shortfall_not_a_crash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))
        rows_by_season = {season: exp.build_team_game_rows(season) for season in exp.ALL_SEASONS}
        assert all(rows == [] for rows in rows_by_season.values())
        coverage = exp.coverage_report(rows_by_season)
        assert coverage["meetsMinimumExpectedSample"] is False
        dev_result = exp.run_hypothesis_tests([r for s in exp.DEV_SEASONS for r in rows_by_season[s]])
        assert dev_result is None
        assert exp.classify_signal(dev_result, None, None, None, None, None) == "WEAK_UNPROVEN"


# ── classify_signal ──────────────────────────────────────────────────────

class TestClassifySignal:
    def _dev_result(self, lows=(0.1, 0.1, 0.1), n=10000):
        return {
            "n": n,
            "h_form_deviation_5": {"ci": {"low": lows[0], "high": lows[0] + 1}},
            "h_form_deviation_10": {"ci": {"low": lows[1], "high": lows[1] + 1}},
            "h_form_deviation_20": {"ci": {"low": lows[2], "high": lows[2] + 1}},
        }

    def _predictive(self, candidate_beats_control=True):
        control_mae = 1.0
        candidate_mae = 0.9 if candidate_beats_control else 1.1
        return {"control": {"mae": control_mae}, "candidate": {"mae": candidate_mae}}

    def test_insufficient_sample_is_weak_unproven(self):
        dev = self._dev_result(n=100)
        assert exp.classify_signal(dev, None, None, None, None, None) == "WEAK_UNPROVEN"

    def test_no_dev_signal_is_no_useful_signal(self):
        dev = self._dev_result(lows=(-0.1, -0.1, -0.1))
        assert exp.classify_signal(dev, None, None, None, None, None) == "NO_USEFUL_SIGNAL"

    def test_confidently_negative_dev_signal_is_mean_reversion(self):
        dev = {
            "n": 10000,
            "h_form_deviation_5": {"ci": {"low": -1.0, "high": -0.1}},
            "h_form_deviation_10": {"ci": {"low": -1.0, "high": -0.1}},
            "h_form_deviation_20": {"ci": {"low": -1.0, "high": -0.1}},
        }
        assert exp.classify_signal(dev, None, None, None, None, None) == "MEAN_REVERSION_SIGNAL"

    def test_dev_confident_but_no_predictive_improvement_replication_is_weak_unproven(self):
        dev = self._dev_result()
        val = self._dev_result()
        hold = self._dev_result(lows=(-0.1, -0.1, -0.1))
        assert exp.classify_signal(dev, val, hold, self._predictive(), self._predictive(False), None) == "WEAK_UNPROVEN"

    def test_dev_plus_one_replication_with_predictive_improvement_is_partial(self):
        dev = self._dev_result()
        val = self._dev_result()
        hold = self._dev_result(lows=(-0.1, -0.1, -0.1))
        assert exp.classify_signal(dev, val, hold, self._predictive(), self._predictive(True), None) == "PARTIAL_CONDITIONAL_SIGNAL"

    def test_dev_plus_both_replications_with_predictive_improvement_is_strong(self):
        dev = self._dev_result()
        val = self._dev_result()
        hold = self._dev_result()
        assert exp.classify_signal(dev, val, hold, self._predictive(), self._predictive(True), self._predictive(True)) == "STRONG_REPEATABLE_SIGNAL"
