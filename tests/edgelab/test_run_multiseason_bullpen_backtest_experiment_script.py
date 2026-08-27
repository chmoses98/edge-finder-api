import ast
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

import run_multiseason_bullpen_backtest_experiment as exp  # noqa: E402
import fetch_mlb_multiseason_bullpen_cache as fetcher  # noqa: E402
from lib.edgelab.bullpen_availability import compute_bullpen_workload_adjustment  # noqa: E402
from lib.edgelab.bullpen_usage import summarize_team_bullpen_usage, MLB_TEAM_ID_MAP  # noqa: E402


# ── Synthetic cache builder (no network) ────────────────────────────────

def _pitcher_stat(pid, pitches, outs, saves=0, holds=0, runs=0, er=0):
    return {"playerId": pid, "numberOfPitches": pitches, "outs": outs, "saves": saves, "holds": holds,
            "runs": runs, "earnedRuns": er}


def _boxscore_json(home_pitchers, away_pitchers):
    def side_block(pitchers):
        players = {}
        ids = []
        for p in pitchers:
            ids.append(p["playerId"])
            players[f"ID{p['playerId']}"] = {
                "person": {"fullName": p["playerId"], "pitchHand": {"code": "R"}},
                "stats": {"pitching": {k: v for k, v in p.items() if k != "playerId"}},
            }
        return {"pitchers": ids, "players": players}
    return {"teams": {"home": side_block(home_pitchers), "away": side_block(away_pitchers)}}


def _schedule_json(games_for_team):
    """games_for_team: list of (date, gamePk, side, gameNumber, doubleHeader, opponentId)."""
    dates = {}
    for date, game_pk, side, gn, dh, opp_id in games_for_team:
        teams = {"away": {"team": {"id": opp_id}}, "home": {"team": {"id": opp_id}}}
        teams[side] = {"team": {"id": None}}  # filled by caller via team_id below
        dates.setdefault(date, []).append({
            "gamePk": game_pk, "status": {"detailedState": "Final"}, "teams": teams,
            "doubleHeader": dh, "gameNumber": gn,
        })
    return {"dates": [{"date": d, "games": g} for d, g in sorted(dates.items())]}


def build_synthetic_cache(tmp_path, season, team_a="NYY", team_b="BOS", games=None):
    """
    Builds a tiny two-team synthetic season cache under tmp_path,
    monkeypatch-ready via fetcher.CACHE_ROOT. `games`: list of dicts
    {date, gamePk, teamAPitchers, teamBPitchers} where team_a is always
    home, team_b always away (kept simple -- side assignment isn't the
    point of this fixture).
    """
    team_a_id = MLB_TEAM_ID_MAP[team_a]
    team_b_id = MLB_TEAM_ID_MAP[team_b]

    schedule_games = []
    boxscores = {}
    for g in games:
        teams = {"away": {"team": {"id": team_b_id}}, "home": {"team": {"id": team_a_id}}}
        schedule_games.append((g["date"], teams, g["gamePk"], g.get("gameNumber", 1), g.get("doubleHeader", "N")))
        boxscores[g["gamePk"]] = _boxscore_json(g["teamAPitchers"], g["teamBPitchers"])

    by_date = {}
    for date, teams, game_pk, gn, dh in schedule_games:
        by_date.setdefault(date, []).append({
            "gamePk": game_pk, "status": {"detailedState": "Final"}, "teams": teams,
            "doubleHeader": dh, "gameNumber": gn,
        })
    schedule = {"dates": [{"date": d, "games": gs} for d, gs in sorted(by_date.items())]}

    season_dir = tmp_path / str(season)
    (season_dir / "schedules").mkdir(parents=True)
    for team_abbr in MLB_TEAM_ID_MAP:
        path = season_dir / "schedules" / f"{team_abbr}.json"
        with open(path, "w") as f:
            json.dump(schedule if team_abbr in (team_a, team_b) else {"dates": []}, f)

    boxscore_path = season_dir / "boxscores.jsonl.gz"
    import gzip
    with gzip.open(boxscore_path, "wt") as f:
        for game_pk, box in boxscores.items():
            f.write(json.dumps({
                "gamePk": game_pk,
                "awayPitchers": _extract_lines(box, "away"),
                "homePitchers": _extract_lines(box, "home"),
            }) + "\n")
    return str(tmp_path)


def _extract_lines(box, side):
    from lib.edgelab.backtest.bullpen_backtest_reconstruction import extract_pitcher_lines
    return extract_pitcher_lines(box, side)


# ── preregistration ordering (structural) ───────────────────────────────

RESULT_PRODUCING_CALL_NAMES = {
    "build_team_game_rows", "run_hypothesis_tests", "coverage_report", "classify_magnitude",
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
    source = open(os.path.join(_ROOT, "scripts", "edgelab", "run_multiseason_bullpen_backtest_experiment.py")).read()
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
                f"{result_call!r} is called before register_experiment -- preregistration must happen "
                f"before any real-corpus result is computed or inspected"
            )


# ── holdout isolation (structural) ───────────────────────────────────────

class TestHoldoutIsolation:
    def test_run_hypothesis_tests_never_references_season_group_constants(self):
        """The SAME function must be applied unchanged to development,
        validation, and holdout rows -- proven by showing its own source
        never references which season group it was called with."""
        node = _find_function_node("run_hypothesis_tests")
        names_referenced = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        for forbidden in ("HOLDOUT_SEASONS", "DEV_SEASONS", "VALIDATION_SEASONS", "season"):
            assert forbidden not in names_referenced, (
                f"run_hypothesis_tests references {forbidden!r} -- it must be one fixed specification "
                f"applied identically regardless of which season group's rows it receives"
            )

    def test_main_calls_run_hypothesis_tests_with_the_same_function_for_all_three_groups(self):
        source = open(os.path.join(_ROOT, "scripts", "edgelab", "run_multiseason_bullpen_backtest_experiment.py")).read()
        # exactly one def, so every call site necessarily uses the same function object --
        # this assertion documents that invariant explicitly rather than leaving it implicit.
        assert source.count("def run_hypothesis_tests(") == 1
        assert source.count("run_hypothesis_tests(dev_rows)") == 1
        assert source.count("run_hypothesis_tests(validation_rows)") == 1
        assert source.count("run_hypothesis_tests(holdout_rows)") == 1


# ── production formula reused exactly ───────────────────────────────────

class TestProductionFormulaReusedExactly:
    def test_reconstruction_module_calls_the_real_unmodified_functions(self):
        from lib.edgelab.backtest import bullpen_backtest_reconstruction as recon
        assert recon.compute_bullpen_workload_adjustment is compute_bullpen_workload_adjustment
        assert recon.summarize_team_bullpen_usage is summarize_team_bullpen_usage


# ── production files unchanged ───────────────────────────────────────────

def test_no_production_module_is_reimplemented_in_this_script():
    """This script/its libraries import, never redefine, every production
    function they use."""
    import lib.edgelab.backtest.bullpen_backtest_reconstruction as recon
    source = open(recon.__file__).read()
    assert "def compute_bullpen_workload_adjustment" not in source
    assert "def summarize_team_bullpen_usage" not in source
    assert "def extract_relief_appearances" not in source


# ── end-to-end synthetic-cache integration ───────────────────────────────

class TestEndToEndSyntheticCache:
    def test_build_team_game_rows_produces_expected_rows(self, tmp_path, monkeypatch):
        games = [
            {"date": "2026-04-01", "gamePk": 1001,
             "teamAPitchers": [_pitcher_stat("SP1", 90, 18, runs=2, er=2), _pitcher_stat("R1", 15, 3, runs=1, er=1)],
             "teamBPitchers": [_pitcher_stat("SP2", 85, 18, runs=3, er=3)]},
            {"date": "2026-04-02", "gamePk": 1002,
             "teamAPitchers": [_pitcher_stat("SP3", 88, 17, runs=1, er=1), _pitcher_stat("R1", 40, 6, runs=4, er=4)],
             "teamBPitchers": [_pitcher_stat("SP4", 90, 18, runs=2, er=2)]},
        ]
        cache_root = build_synthetic_cache(tmp_path, 2026, games=games)
        monkeypatch.setattr(fetcher, "CACHE_ROOT", cache_root)
        rows = exp.build_team_game_rows(2026)
        nyy_rows = [r for r in rows if r["team"] == "NYY"]
        # game 1001 is NYY's first game of the season -- zero prior games,
        # so recentUsage.dataAvailable is False and it is correctly
        # EXCLUDED by the eligibility criterion ("at least one prior
        # completed game exists"). Only game 1002 qualifies.
        assert len(nyy_rows) == 1
        game2 = nyy_rows[0]
        assert game2["gamePk"] == 1002
        # R1 pitched 15 pitches on 2026-04-01 -- must show up as game2's prevDay1 feature
        assert game2["bullpenPitchesPrevDay1"] == 15
        assert game2["reliefRunsAllowed"] == 4  # R1's own runs allowed in game 1002

    def test_deterministic_across_repeated_calls(self, tmp_path, monkeypatch):
        games = [
            {"date": "2026-04-01", "gamePk": 2001,
             "teamAPitchers": [_pitcher_stat("SP1", 90, 18, runs=2, er=2)],
             "teamBPitchers": [_pitcher_stat("SP2", 85, 18, runs=1, er=1)]},
            {"date": "2026-04-02", "gamePk": 2002,
             "teamAPitchers": [_pitcher_stat("SP3", 88, 17, runs=1, er=1), _pitcher_stat("R1", 20, 3, runs=1, er=1)],
             "teamBPitchers": [_pitcher_stat("SP4", 90, 18, runs=2, er=2)]},
        ]
        cache_root = build_synthetic_cache(tmp_path, 2026, games=games)
        monkeypatch.setattr(fetcher, "CACHE_ROOT", cache_root)
        rows1 = exp.build_team_game_rows(2026)
        rows2 = exp.build_team_game_rows(2026)
        assert rows1 == rows2

    def test_hypothesis_tests_detect_a_constructed_positive_relationship(self, tmp_path, monkeypatch):
        """Build a season where days with heavy prior-day bullpen usage
        are FOLLOWED by worse relief outcomes, by construction -- H1's
        correlation should come out clearly positive."""
        games = []
        import datetime
        game_pk = 3000
        # Alternate: a light-usage day, then a game following it (good outcome);
        # a heavy-usage day, then a game following it (bad outcome) -- repeated
        # across enough independent games for a stable correlation sign.
        cursor_date = datetime.date(2026, 4, 1)
        for i in range(20):
            heavy = i % 2 == 0
            d1 = cursor_date.strftime("%Y-%m-%d")
            d2 = (cursor_date + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            game_pk += 1
            games.append({
                "date": d1, "gamePk": game_pk,
                "teamAPitchers": [_pitcher_stat("SP", 90, 18, runs=1, er=1),
                                   _pitcher_stat("R1", 60 if heavy else 5, 6, runs=1, er=1)],
                "teamBPitchers": [_pitcher_stat("OSP", 85, 18, runs=1, er=1)],
            })
            game_pk += 1
            games.append({
                "date": d2, "gamePk": game_pk,
                "teamAPitchers": [_pitcher_stat("SP2", 88, 15, runs=1, er=1),
                                   _pitcher_stat("R2", 20, 3, runs=(6 if heavy else 0), er=(6 if heavy else 0))],
                "teamBPitchers": [_pitcher_stat("OSP2", 85, 18, runs=1, er=1)],
            })
            cursor_date += datetime.timedelta(days=2)

        cache_root = build_synthetic_cache(tmp_path, 2026, games=games)
        monkeypatch.setattr(fetcher, "CACHE_ROOT", cache_root)
        rows = exp.build_team_game_rows(2026)
        nyy_rows = [r for r in rows if r["team"] == "NYY" and r["bullpenPitchesPrevDay1"] is not None]
        result = exp.run_hypothesis_tests(nyy_rows)
        assert result is not None
        assert result["h1_workload_vs_outcome"]["spearman"] > 0.5

    def test_coverage_report_flags_shortfall_below_minimum(self):
        rows_by_season = {2022: [], 2023: [], 2024: [], 2025: [], 2026: []}
        coverage = exp.coverage_report(rows_by_season)
        assert coverage["meetsMinimumExpectedSample"] is False
        assert coverage["totalTeamGames"] == 0

    def test_empty_cache_produces_honest_shortfall_result_not_a_crash_or_fabrication(self, tmp_path, monkeypatch):
        """Against an entirely empty cache (this repo's actual current
        state, since this environment has no network access),
        build_team_game_rows/run_hypothesis_tests/classify_magnitude
        must complete and honestly report nothing -- never crash, never
        fabricate a large-sample-looking result."""
        monkeypatch.setattr(fetcher, "CACHE_ROOT", str(tmp_path))
        rows_by_season = {season: exp.build_team_game_rows(season) for season in exp.ALL_SEASONS}
        assert all(rows == [] for rows in rows_by_season.values())
        coverage = exp.coverage_report(rows_by_season)
        assert coverage["meetsMinimumExpectedSample"] is False
        dev_result = exp.run_hypothesis_tests([r for s in exp.DEV_SEASONS for r in rows_by_season[s]])
        assert dev_result is None
        assert exp.classify_magnitude(dev_result) == "UNPROVEN"
