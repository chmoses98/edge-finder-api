#!/usr/bin/env python3
"""
tests/research/test_hitter_prospective_snapshot.py
========================================================
Coverage for lib/research/hitter_prospective_snapshot.py -- the
checkpoint-scheduling orchestrator for the hitter projection engine.
Every test injects fake build_board_main_fn/write_filtered_slate_fn/
lineup_fetch_fn -- no real network access, no real Kalshi snapshot, no
real Monte Carlo simulation. Mirrors tests/edgelab/test_prospective_snapshot.py's
own structure/conventions for the game-level system this module reuses
pieces of.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage
from lib.research import hitter_prospective_snapshot as hps


def _game(game_id="822780", start_time="2026-08-10T23:00:00Z", away_abbr="BOS", home_abbr="NYY",
          lineup_confirmed=False, **overrides):
    g = {
        "gameId": game_id,
        "startTime": start_time,
        "away": {"abbr": away_abbr},
        "home": {"abbr": home_abbr},
        "awayTeamStats": {"lineupConfirmedOfficial": lineup_confirmed},
        "homeTeamStats": {"lineupConfirmedOfficial": lineup_confirmed},
    }
    g.update(overrides)
    return g


def _fake_build_board_main(rows_by_matchup=None):
    """A fake standing in for scripts.build_hitter_projection_board.main -- reads the filtered slate this call was given and returns exactly one canned row per game in it, so tests can assert cost containment (only DUE games' rows ever appear)."""
    def _fn(*, date_str, slate_path, weather_path, savant_team_path, kalshi_search_path, n_sims, research_run_id, dry_run, emit_rows):
        import json
        with open(slate_path) as f:
            slate = json.load(f)
        rows = []
        for g in slate["games"]:
            matchup = f"{g['away']['abbr']} @ {g['home']['abbr']}"
            rows.append({
                "marketTicker": f"KXMLBHIT-{matchup.replace(' ', '')}-X-1",
                "marketFamily": "hitter_hits", "threshold": 1, "matchup": matchup,
                "modelProbability": 0.4, "executableKalshiPrice": 0.35,
                "projectionStatus": "PROJECTED",
            })
        return {"date": date_str, "totalRows": len(rows), "rows": rows, "hitterSummaries": []}
    return _fn


def _fake_write_filtered_slate(calls_log=None, tmp_dir=None):
    """Writes a REAL temp JSON file (never a fake path string) -- the fake build_board_main_fn above genuinely reads it back, exactly like the real scripts.build_hitter_projection_board.main() would read a real slate_path."""
    import json
    import tempfile
    base_dir = tmp_dir or tempfile.mkdtemp()

    def _fn(date, run_id, checkpoint, games):
        if calls_log is not None:
            calls_log.append({"checkpoint": checkpoint, "gameIds": [g.get("gameId") for g in games]})
        path = os.path.join(base_dir, f"{date}_{run_id}_{checkpoint}.json")
        with open(path, "w") as f:
            json.dump({"date": date, "games": games}, f)
        return path
    return _fn


# ── Checkpoint scheduling ──────────────────────────────────────────────────

class TestDetermineDueHitterCheckpoint:
    def test_t_minus_90_due_when_no_prior_checkpoint(self):
        game = _game(start_time="2026-08-10T23:00:00Z")
        checkpoint, minutes = hps.determine_due_hitter_checkpoint(game, now="2026-08-10T21:30:00Z", already_captured=set())
        assert checkpoint == "T_MINUS_90"
        assert abs(minutes - 90.0) < 1e-6

    def test_already_captured_checkpoint_not_due_again(self):
        game = _game(start_time="2026-08-10T23:00:00Z")
        checkpoint, _ = hps.determine_due_hitter_checkpoint(game, now="2026-08-10T21:30:00Z", already_captured={"T_MINUS_90"})
        assert checkpoint is None

    def test_lineup_confirmation_due_when_just_confirmed(self):
        game = _game(start_time="2026-08-10T23:00:00Z", lineup_confirmed=True)
        checkpoint, _ = hps.determine_due_hitter_checkpoint(game, now="2026-08-10T22:10:00Z", already_captured=set())
        assert checkpoint == "LINEUP_CONFIRMATION"

    def test_lineup_confirmation_not_due_twice(self):
        game = _game(start_time="2026-08-10T23:00:00Z", lineup_confirmed=True)
        checkpoint, _ = hps.determine_due_hitter_checkpoint(game, now="2026-08-10T22:10:00Z", already_captured={"LINEUP_CONFIRMATION"})
        assert checkpoint != "LINEUP_CONFIRMATION"

    def test_closing_window_due(self):
        game = _game(start_time="2026-08-10T23:00:00Z")
        checkpoint, minutes = hps.determine_due_hitter_checkpoint(game, now="2026-08-10T22:50:00Z", already_captured=set())
        assert checkpoint == hps.HITTER_CLOSING_WINDOW
        assert 0 < minutes <= hps.HITTER_CLOSING_WINDOW_MINUTES

    def test_no_checkpoint_due_between_targets(self):
        game = _game(start_time="2026-08-10T23:00:00Z")
        checkpoint, _ = hps.determine_due_hitter_checkpoint(game, now="2026-08-10T22:30:00Z", already_captured={"T_MINUS_90", "T_MINUS_60", "T_MINUS_30"})
        assert checkpoint is None

    def test_lineup_confirmation_takes_priority_over_time_target(self):
        """A game confirming lineups right at a T_MINUS_60 moment must be captured as LINEUP_CONFIRMATION, not T_MINUS_60 -- matching the game-level system's own priority order."""
        game = _game(start_time="2026-08-10T23:00:00Z", lineup_confirmed=True)
        checkpoint, _ = hps.determine_due_hitter_checkpoint(game, now="2026-08-10T22:00:00Z", already_captured=set())
        assert checkpoint == "LINEUP_CONFIRMATION"

    def test_never_reuses_game_level_closing_window_constant(self):
        """This module's closing-window label must be visibly distinct from the game-level system's MODEL_CLOSING_WINDOW -- two different models, two different names (see module docstring)."""
        from lib.edgelab import prospective_snapshot as game_ps
        assert hps.HITTER_CLOSING_WINDOW != game_ps.MODEL_CLOSING_WINDOW


class TestAlreadyCapturedHitterCheckpoints:
    def test_filters_by_game_id_and_requires_checkpoint(self):
        rows = [
            {"gameId": "822780", "checkpoint": "T_MINUS_90"},
            {"gameId": "822780", "checkpoint": "T_MINUS_60"},
            {"gameId": "999999", "checkpoint": "T_MINUS_90"},
            {"gameId": "822780", "checkpoint": None},
        ]
        assert hps.already_captured_hitter_checkpoints(rows, "822780") == {"T_MINUS_90", "T_MINUS_60"}
        assert hps.already_captured_hitter_checkpoints(rows, "000000") == set()


# ── Cycle orchestration ─────────────────────────────────────────────────────

class TestRunHitterProspectiveSnapshotCycle:
    def test_no_op_when_nothing_due(self):
        game = _game(start_time="2026-08-10T23:00:00Z")
        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [{"gameId": "822780", "checkpoint": "T_MINUS_90"},
                                     {"gameId": "822780", "checkpoint": "T_MINUS_60"},
                                     {"gameId": "822780", "checkpoint": "T_MINUS_30"}],
            now="2026-08-10T22:30:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        assert rows == []
        assert all(e["action"] == "SKIPPED" for e in run_log)
        assert run_log[0]["reason"] == hps.SKIPPED_NO_CHECKPOINT_DUE

    def test_started_game_excluded_never_reaches_board_build(self):
        game = _game(start_time="2026-08-10T10:00:00Z")
        calls = []
        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [], now="2026-08-10T10:05:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(calls),
        )
        assert rows == []
        assert calls == []
        assert run_log[0]["action"] == "SKIPPED"
        assert run_log[0]["reason"] == "STARTED"

    def test_due_game_produces_tagged_rows(self):
        game = _game(start_time="2026-08-10T23:00:00Z")
        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
            run_id="TEST_RUN_1",
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["checkpoint"] == "T_MINUS_90"
        assert row["gameId"] == "822780"
        assert row["researchRunId"] == "TEST_RUN_1"
        assert row["hitterProjectionSnapshotId"]
        assert "engineCommitSha" in row
        assert "snapshotGeneratedAt" in row
        evaluated = [e for e in run_log if e["action"] == "EVALUATED"]
        assert len(evaluated) == 1
        assert evaluated[0]["checkpoint"] == "T_MINUS_90"

    def test_only_due_games_are_written_to_the_filtered_slate(self):
        """Cost containment: a game with nothing due this cycle must never appear in the filtered slate passed to the (expensive) board build, even though it's in the full `games` list."""
        due_game = _game(game_id="111", start_time="2026-08-10T23:00:00Z", away_abbr="BOS", home_abbr="NYY")
        not_due_game = _game(game_id="222", start_time="2026-08-10T23:00:00Z", away_abbr="SEA", home_abbr="LAA",
                              **{"awayTeamStats": {"lineupConfirmedOfficial": False}, "homeTeamStats": {"lineupConfirmedOfficial": False}})
        calls = []
        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [due_game, not_due_game],
            existing_snapshot_rows=[{"gameId": "222", "checkpoint": "T_MINUS_90"}],
            now="2026-08-10T21:30:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(calls),
        )
        assert len(calls) == 1
        assert calls[0]["gameIds"] == ["111"]
        assert len(rows) == 1
        assert rows[0]["gameId"] == "111"

    def test_multiple_checkpoints_batched_separately(self):
        """Two games due at DIFFERENT checkpoints this cycle must produce two separate filtered-slate/board-build calls, one per checkpoint."""
        t90_game = _game(game_id="111", start_time="2026-08-10T23:00:00Z", away_abbr="BOS", home_abbr="NYY")
        confirmed_game = _game(game_id="222", start_time="2026-08-10T23:30:00Z", away_abbr="SEA", home_abbr="LAA", lineup_confirmed=True)
        calls = []
        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [t90_game, confirmed_game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(calls),
        )
        checkpoints_called = {c["checkpoint"] for c in calls}
        assert checkpoints_called == {"T_MINUS_90", "LINEUP_CONFIRMATION"}
        assert len(rows) == 2

    def test_one_checkpoint_groups_failure_does_not_erase_another(self):
        t90_game = _game(game_id="111", start_time="2026-08-10T23:00:00Z", away_abbr="BOS", home_abbr="NYY")
        confirmed_game = _game(game_id="222", start_time="2026-08-10T23:30:00Z", away_abbr="SEA", home_abbr="LAA", lineup_confirmed=True)

        def _flaky_build(*, date_str, slate_path, weather_path, savant_team_path, kalshi_search_path, n_sims, research_run_id, dry_run, emit_rows):
            import json
            with open(slate_path) as f:
                slate = json.load(f)
            if any(g["gameId"] == "222" for g in slate["games"]):
                raise RuntimeError("simulated hitter engine failure")
            return _fake_build_board_main()(
                date_str=date_str, slate_path=slate_path, weather_path=weather_path,
                savant_team_path=savant_team_path, kalshi_search_path=kalshi_search_path,
                n_sims=n_sims, research_run_id=research_run_id, dry_run=dry_run, emit_rows=emit_rows,
            )

        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [t90_game, confirmed_game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_flaky_build, write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        assert len(rows) == 1
        assert rows[0]["gameId"] == "111"
        failed_entries = [e for e in run_log if e["action"] == "SKIPPED" and e.get("checkpoint") == "LINEUP_CONFIRMATION"]
        assert len(failed_entries) == 1
        assert "hitter board build raised" in failed_entries[0]["reason"]

    def test_lineup_checkpoint_evaluates_refreshed_game_other_checkpoints_do_not(self):
        game = _game(start_time="2026-08-10T23:00:00Z", lineup_confirmed=False)

        def _lineup_fetch(game_pk, away_abbr, home_abbr, batter_woba_map, team_woba_map):
            return {"confirmed": True}

        import lib.research.hitter_prospective_snapshot as hps_mod
        original_refresh = hps_mod.refresh_lineup_fields

        def _fake_refresh(game, *, lineup_fetch_fn, batter_woba_map, team_woba_map):
            import copy
            g = copy.deepcopy(game)
            g["awayTeamStats"]["lineupConfirmedOfficial"] = True
            g["homeTeamStats"]["lineupConfirmedOfficial"] = True
            return g, None

        hps_mod.refresh_lineup_fields = _fake_refresh
        try:
            calls = []
            rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
                "2026-08-10", [game], [], now="2026-08-10T21:30:00Z",
                lineup_fetch_fn=_lineup_fetch,
                build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(calls),
            )
        finally:
            hps_mod.refresh_lineup_fields = original_refresh

        assert len(rows) == 1
        assert rows[0]["checkpoint"] == "LINEUP_CONFIRMATION"
        assert run_log[0]["lineupNewlyConfirmed"] is True


class TestWriteFilteredHitterSlate:
    def test_writes_only_given_games_never_touches_canonical_board(self, tmp_path):
        game = _game()
        path = hps.write_filtered_hitter_slate("2026-08-10", "RUN1", "T_MINUS_90", [game], output_root=str(tmp_path))
        assert os.path.exists(path)
        assert "hitter_projection_board.json" not in path
        import json
        with open(path) as f:
            doc = json.load(f)
        assert doc["games"] == [game]
        assert doc["date"] == "2026-08-10"


class TestModuleSafety:
    def test_module_never_imports_bet_bankroll_or_recommendation_writers(self):
        """AST-based import scan (this repo's own established pattern, e.g.
        tests/test_hitter_phase5_orchestration.py's TestNoTraditionalPipelineDependency)
        -- checks actual `import`/`from ... import` statements only, never
        raw substring matches against the file's own prose docstrings
        (which legitimately name these modules when explaining what this
        module does NOT do)."""
        import ast
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                             "lib", "research", "hitter_prospective_snapshot.py")
        with open(path) as f:
            tree = ast.parse(f.read())
        imported_names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.append(node.module)
        forbidden = ("write_pending_bets", "risk_gate", "bankroll", "log_bet", "protect_slate")
        for name in forbidden:
            assert not any(name in imported for imported in imported_names), f"module imports forbidden concept: {name}"

    def test_never_calls_board_main_without_dry_run_true(self):
        """A checkpoint-scoped call must never overwrite the canonical data/pipeline/<date>/hitter_projection_board.json artifact."""
        game = _game(start_time="2026-08-10T23:00:00Z")
        captured_kwargs = {}

        def _capturing_build(**kwargs):
            captured_kwargs.update(kwargs)
            return {"rows": [], "hitterSummaries": []}

        hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_capturing_build, write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        assert captured_kwargs["dry_run"] is True
        assert captured_kwargs["emit_rows"] is True
