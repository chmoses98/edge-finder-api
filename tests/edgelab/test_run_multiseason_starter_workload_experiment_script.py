import ast
import gzip
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (
    _ROOT,
    os.path.join(_ROOT, "scripts"),
    os.path.join(_ROOT, "scripts", "edgelab"),
    os.path.join(_ROOT, "scripts", "edgelab", "backtest"),
):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_multiseason_starter_workload_experiment as exp  # noqa: E402
import fetch_mlb_starter_workload_cache as fetcher  # noqa: E402
from lib.edgelab.backtest.bullpen_backtest_reconstruction import is_strictly_before  # noqa: E402


def _pitcher_line(player_id, pitches=90, outs=18, runs=2, earned=2, hits=6, walks=2, k=6, bf=27):
    return {"playerId": player_id, "orderIndex": 0, "name": player_id, "throwsHand": "R",
            "numberOfPitches": pitches, "outs": outs, "saves": 0, "holds": 0,
            "runs": runs, "earnedRuns": earned, "battersFaced": bf,
            "strikeOuts": k, "baseOnBalls": walks, "hits": hits}


def _reliever_line(player_id):
    d = _pitcher_line(player_id)
    d["orderIndex"] = 1
    return d


def _schedule_for_team(team_id, games):
    """games: list of (date, gamePk, side, gameNumber, doubleHeader)."""
    by_date = {}
    for date, game_pk, side, gn, dh in games:
        teams = {"away": {"team": {"id": 999}}, "home": {"team": {"id": 999}}}
        teams[side] = {"team": {"id": team_id}}
        by_date.setdefault(date, []).append({
            "gamePk": game_pk, "status": {"detailedState": "Final"}, "teams": teams,
            "doubleHeader": dh, "gameNumber": gn,
        })
    return {"dates": [{"date": d, "games": g} for d, g in sorted(by_date.items())]}


def _write_synthetic_cache(tmp_path, season, team_abbr, team_id, games, boxscores):
    """games: schedule entries as above. boxscores: {gamePk: {"awayPitchers":[...], "homePitchers":[...]}}."""
    sched_dir = tmp_path / "bullpen_backtest" / str(season) / "schedules"
    sched_dir.mkdir(parents=True, exist_ok=True)
    with open(sched_dir / f"{team_abbr}.json", "w") as f:
        json.dump(_schedule_for_team(team_id, games), f)
    # every other team gets an empty schedule so MLB_TEAM_ID_MAP iteration is harmless
    from lib.edgelab.bullpen_usage import MLB_TEAM_ID_MAP
    for abbr in MLB_TEAM_ID_MAP:
        if abbr == team_abbr:
            continue
        with open(sched_dir / f"{abbr}.json", "w") as f:
            json.dump({"dates": []}, f)

    starter_dir = tmp_path / "starter_workload" / str(season)
    starter_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(starter_dir / "boxscores.jsonl.gz", "wt") as f:
        for game_pk, box in boxscores.items():
            f.write(json.dumps({"gamePk": game_pk, **box}) + "\n")


# ── preregistration ordering (structural) ───────────────────────────────

RESULT_PRODUCING_CALL_NAMES = {
    "build_pitcher_start_rows", "run_hypothesis_tests", "coverage_report", "classify_signal",
}


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
    source = open(os.path.join(_ROOT, "scripts", "edgelab", "run_multiseason_starter_workload_experiment.py")).read()
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
            assert min(occurrences) > registration_index, (
                f"{result_call!r} is called before register_experiment"
            )


# ── holdout isolation (structural) ───────────────────────────────────────

class TestHoldoutIsolation:
    def test_run_hypothesis_tests_never_references_season_group_constants(self):
        node = _find_function_node("run_hypothesis_tests")
        names_referenced = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        for forbidden in ("HOLDOUT_SEASONS", "DEV_SEASONS", "VALIDATION_SEASONS", "season"):
            assert forbidden not in names_referenced

    def test_main_calls_run_hypothesis_tests_identically_for_all_three_groups(self):
        source = open(os.path.join(_ROOT, "scripts", "edgelab", "run_multiseason_starter_workload_experiment.py")).read()
        assert source.count("def run_hypothesis_tests(") == 1
        assert source.count("run_hypothesis_tests(dev_rows)") == 1
        assert source.count("run_hypothesis_tests(validation_rows)") == 1
        assert source.count("run_hypothesis_tests(holdout_rows)") == 1


# ── production / leakage code reused, not reimplemented ─────────────────

class TestReuseNotReimplementation:
    def test_leakage_guard_is_the_real_shared_function(self):
        from lib.edgelab.backtest import starter_workload_reconstruction as recon
        assert recon.is_strictly_before is is_strictly_before

    def test_starter_module_does_not_redefine_is_strictly_before(self):
        from lib.edgelab.backtest import starter_workload_reconstruction as recon
        source = open(recon.__file__).read()
        assert "def is_strictly_before" not in source


# ── end-to-end synthetic-cache integration ───────────────────────────────

class TestEndToEndSyntheticCache:
    def test_build_pitcher_start_rows_excludes_first_start_of_season(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "BULLPEN_CACHE_ROOT", str(tmp_path / "bullpen_backtest"))
        monkeypatch.setattr(fetcher, "STARTER_CACHE_ROOT", str(tmp_path / "starter_workload"))
        team_id = 147  # NYY
        games = [
            ("2024-04-01", 1, "home", 1, "N"),
            ("2024-04-06", 2, "home", 1, "N"),
        ]
        boxscores = {
            1: {"awayPitchers": [], "homePitchers": [_pitcher_line("P1", pitches=90)]},
            2: {"awayPitchers": [], "homePitchers": [_pitcher_line("P1", pitches=95)]},
        }
        _write_synthetic_cache(tmp_path, 2024, "NYY", team_id, games, boxscores)
        rows = exp.build_pitcher_start_rows(2024)
        assert len(rows) == 1
        assert rows[0]["gamePk"] == 2
        assert rows[0]["daysSincePreviousStart"] == 5

    def test_deterministic_across_repeated_calls(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "BULLPEN_CACHE_ROOT", str(tmp_path / "bullpen_backtest"))
        monkeypatch.setattr(fetcher, "STARTER_CACHE_ROOT", str(tmp_path / "starter_workload"))
        team_id = 147
        games = [("2024-04-01", 1, "home", 1, "N"), ("2024-04-06", 2, "home", 1, "N")]
        boxscores = {
            1: {"awayPitchers": [], "homePitchers": [_pitcher_line("P1", pitches=90)]},
            2: {"awayPitchers": [], "homePitchers": [_pitcher_line("P1", pitches=95)]},
        }
        _write_synthetic_cache(tmp_path, 2024, "NYY", team_id, games, boxscores)
        rows1 = exp.build_pitcher_start_rows(2024)
        rows2 = exp.build_pitcher_start_rows(2024)
        assert rows1 == rows2

    def test_hypothesis_tests_detect_a_constructed_short_rest_effect(self, tmp_path, monkeypatch):
        """Build a season where short-rest starts are, by construction,
        followed by worse outcomes -- H1 should come out positive."""
        monkeypatch.setattr(fetcher, "BULLPEN_CACHE_ROOT", str(tmp_path / "bullpen_backtest"))
        monkeypatch.setattr(fetcher, "STARTER_CACHE_ROOT", str(tmp_path / "starter_workload"))
        team_id = 147
        games = []
        boxscores = {}
        game_pk = 1000
        import datetime
        cursor = datetime.date(2024, 4, 1)
        for i in range(20):
            player_id = f"P{i}"
            # first start (establishes a baseline) -- normal rest gap doesn't matter, excluded as first start
            game_pk += 1
            games.append((cursor.strftime("%Y-%m-%d"), game_pk, "home", 1, "N"))
            boxscores[game_pk] = {"awayPitchers": [], "homePitchers": [_pitcher_line(player_id, earned=2, outs=18)]}
            first_date = cursor
            # second start: short rest (3 days) for even i, normal rest (5 days) for odd i
            gap = 3 if i % 2 == 0 else 5
            second_date = first_date + datetime.timedelta(days=gap)
            game_pk += 1
            games.append((second_date.strftime("%Y-%m-%d"), game_pk, "home", 1, "N"))
            # short-rest starts get a worse outcome, by construction
            earned = 6 if i % 2 == 0 else 1
            boxscores[game_pk] = {"awayPitchers": [], "homePitchers": [_pitcher_line(player_id, earned=earned, outs=18)]}
            cursor = second_date + datetime.timedelta(days=10)

        _write_synthetic_cache(tmp_path, 2024, "NYY", team_id, games, boxscores)
        rows = exp.build_pitcher_start_rows(2024)
        result = exp.run_hypothesis_tests(rows)
        assert result is not None
        assert result["h1_short_rest"]["meanDifference"] > 0
        assert result["h1_short_rest"]["ci"]["low"] > 0

    def test_coverage_report_flags_shortfall_below_minimum(self):
        rows_by_season = {s: [] for s in exp.ALL_SEASONS}
        coverage = exp.coverage_report(rows_by_season)
        assert coverage["meetsMinimumExpectedSample"] is False
        assert coverage["totalPitcherStarts"] == 0

    def test_empty_cache_produces_honest_shortfall_not_a_crash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fetcher, "BULLPEN_CACHE_ROOT", str(tmp_path / "bullpen_backtest"))
        monkeypatch.setattr(fetcher, "STARTER_CACHE_ROOT", str(tmp_path / "starter_workload"))
        rows_by_season = {season: exp.build_pitcher_start_rows(season) for season in exp.ALL_SEASONS}
        assert all(rows == [] for rows in rows_by_season.values())
        coverage = exp.coverage_report(rows_by_season)
        assert coverage["meetsMinimumExpectedSample"] is False
        dev_result = exp.run_hypothesis_tests([r for s in exp.DEV_SEASONS for r in rows_by_season[s]])
        assert dev_result is None
        assert exp.classify_signal(dev_result, None, None) == "WEAK_UNPROVEN"


# ── classify_signal ──────────────────────────────────────────────────────

class TestClassifySignal:
    def _dev_result(self, low1=0.1, low2=0.1, n=3000):
        return {"n": n, "h1_short_rest": {"ci": {"low": low1}},
                "h2_previous_start_high_pitch_count": {"ci": {"low": low2}}}

    def test_insufficient_sample_is_weak_unproven(self):
        assert exp.classify_signal(self._dev_result(n=100), None, None) == "WEAK_UNPROVEN"

    def test_no_dev_signal_is_no_useful_signal(self):
        dev = self._dev_result(low1=-0.1, low2=-0.1)
        assert exp.classify_signal(dev, None, None) == "NO_USEFUL_SIGNAL"

    def test_dev_only_confident_is_weak_unproven(self):
        dev = self._dev_result(low1=0.1, low2=-0.1)
        val = {"h1_short_rest": {"ci": {"low": -0.1}}, "h2_previous_start_high_pitch_count": {"ci": {"low": -0.1}}}
        hold = {"h1_short_rest": {"ci": {"low": -0.1}}, "h2_previous_start_high_pitch_count": {"ci": {"low": -0.1}}}
        assert exp.classify_signal(dev, val, hold) == "WEAK_UNPROVEN"

    def test_dev_plus_one_replication_is_partial(self):
        dev = self._dev_result(low1=0.1, low2=-0.1)
        val = {"h1_short_rest": {"ci": {"low": 0.05}}, "h2_previous_start_high_pitch_count": {"ci": {"low": -0.1}}}
        hold = {"h1_short_rest": {"ci": {"low": -0.1}}, "h2_previous_start_high_pitch_count": {"ci": {"low": -0.1}}}
        assert exp.classify_signal(dev, val, hold) == "PARTIAL_CONDITIONAL_SIGNAL"

    def test_dev_plus_both_replications_is_strong(self):
        dev = self._dev_result(low1=0.1, low2=-0.1)
        val = {"h1_short_rest": {"ci": {"low": 0.05}}, "h2_previous_start_high_pitch_count": {"ci": {"low": -0.1}}}
        hold = {"h1_short_rest": {"ci": {"low": 0.02}}, "h2_previous_start_high_pitch_count": {"ci": {"low": -0.1}}}
        assert exp.classify_signal(dev, val, hold) == "STRONG_REPEATABLE_SIGNAL"
