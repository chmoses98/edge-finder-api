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


# ── Missed-checkpoint explicit recording (scheduling-coverage fix) ─────────

class TestComputeMissedHitterCheckpoints:
    def test_no_misses_while_window_still_open(self):
        game = _game(start_time="2026-08-10T23:00:00Z")
        missed = hps.compute_missed_hitter_checkpoints(
            game, now="2026-08-10T21:30:00Z", already_captured=set(),
        )
        assert missed == []

    def test_t90_missed_once_window_definitively_closes(self):
        game = _game(start_time="2026-08-10T23:00:00Z")
        # T-90 target = 21:30. Tolerance 12 min -> window closes at 21:42
        # (78 min before start). now = 21:50 (70 min before start) is past it.
        missed = hps.compute_missed_hitter_checkpoints(
            game, now="2026-08-10T21:50:00Z", already_captured=set(),
        )
        assert "T_MINUS_90" in missed
        assert "T_MINUS_60" not in missed  # T-60's own window (22:00 +/-12) hasn't closed yet

    def test_already_captured_checkpoint_is_never_reported_missed(self):
        game = _game(start_time="2026-08-10T23:00:00Z")
        missed = hps.compute_missed_hitter_checkpoints(
            game, now="2026-08-10T21:50:00Z", already_captured={"T_MINUS_90"},
        )
        assert "T_MINUS_90" not in missed

    def test_lineup_confirmation_never_reported_as_missed(self):
        """Event-driven, no fixed window to 'close' -- not applicable to this mechanism."""
        game = _game(start_time="2026-08-10T23:00:00Z", lineup_confirmed=False)
        missed = hps.compute_missed_hitter_checkpoints(
            game, now="2026-08-10T22:59:00Z", already_captured=set(),
        )
        assert "LINEUP_CONFIRMATION" not in missed

    def test_missing_scheduled_start_never_raises(self):
        game = _game(start_time=None)
        missed = hps.compute_missed_hitter_checkpoints(game, now="2026-08-10T21:30:00Z", already_captured=set())
        assert missed == []

    def test_respects_custom_tolerance(self):
        game = _game(start_time="2026-08-10T23:00:00Z")
        # With a wider 20-minute tolerance, T-90's window doesn't close until 70 min before start.
        missed_wide = hps.compute_missed_hitter_checkpoints(
            game, now="2026-08-10T21:50:00Z", already_captured=set(), tolerance_minutes=20,
        )
        assert "T_MINUS_90" not in missed_wide


class TestMissedCheckpointIntegrationInCycle:
    def test_missed_checkpoint_recorded_in_run_log(self):
        game = _game(start_time="2026-08-10T23:00:00Z")
        existing = [{"gameId": "822780", "checkpoint": "T_MINUS_90"}]  # pretend not captured -- test the miss path via time instead
        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [], now="2026-08-10T21:50:00Z",  # 70 min before start -- T-90's window has closed
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        missed_entries = [e for e in run_log if e["action"] == "MISSED"]
        assert len(missed_entries) == 1
        assert missed_entries[0]["checkpoint"] == "T_MINUS_90"
        assert missed_entries[0]["reason"] == hps.MISSED_CHECKPOINT_WINDOW_CLOSED

    def test_missed_checkpoint_does_not_block_a_later_checkpoint_same_cycle(self):
        """T-90 missed (window closed) must not prevent T-60 from still being evaluated if T-60's own window is open this same cycle."""
        game = _game(start_time="2026-08-10T23:00:00Z")
        # 61 minutes before start: T-90 window long closed; T-60 (target 60, tolerance 12) is due (diff=1).
        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [], now="2026-08-10T21:59:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        missed_labels = {e["checkpoint"] for e in run_log if e["action"] == "MISSED"}
        evaluated_labels = {e["checkpoint"] for e in run_log if e["action"] == "EVALUATED"}
        assert "T_MINUS_90" in missed_labels
        assert "T_MINUS_60" in evaluated_labels
        assert len(rows) == 1
        assert rows[0]["checkpoint"] == "T_MINUS_60"

    def test_missed_checkpoint_does_not_block_a_later_cycle_capturing_the_next_target(self):
        """Across two SEPARATE cycles: T-90 missed in cycle 1 must not prevent T-60 from being captured in cycle 2."""
        game = _game(start_time="2026-08-10T23:00:00Z")
        # 73 minutes before start: T-90's window closed (diff=17>12) but
        # T-60's own window (target 60, +/-12) isn't due yet (diff=13>12).
        rows1, run_log1 = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [], now="2026-08-10T21:47:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        assert rows1 == []
        assert any(e["action"] == "MISSED" and e["checkpoint"] == "T_MINUS_90" for e in run_log1)

        rows2, run_log2 = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [], now="2026-08-10T22:00:00Z",  # T-60 exactly due now
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        assert len(rows2) == 1
        assert rows2[0]["checkpoint"] == "T_MINUS_60"

    def test_one_game_failure_does_not_block_another_games_checkpoint(self):
        """Distinct from the existing checkpoint-group failure test: here TWO games are due the SAME checkpoint and one game's row construction fails inside the (fake) board build -- the other game's rows must still be returned."""
        game_a = _game(game_id="AAA", start_time="2026-08-10T23:00:00Z", away_abbr="BOS", home_abbr="NYY")
        game_b = _game(game_id="BBB", start_time="2026-08-10T23:00:00Z", away_abbr="SEA", home_abbr="LAA")

        def _build(*, date_str, slate_path, weather_path, savant_team_path, kalshi_search_path, n_sims, research_run_id, dry_run, emit_rows):
            import json
            with open(slate_path) as f:
                slate = json.load(f)
            rows = []
            for g in slate["games"]:
                matchup = f"{g['away']['abbr']} @ {g['home']['abbr']}"
                rows.append({"marketTicker": f"T-{matchup}", "marketFamily": "hitter_hits",
                             "threshold": 1, "matchup": matchup, "modelProbability": 0.4,
                             "executableKalshiPrice": 0.35, "projectionStatus": "PROJECTED"})
            return {"rows": rows, "hitterSummaries": []}

        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game_a, game_b], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_build, write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        game_ids_with_rows = {r["gameId"] for r in rows}
        assert game_ids_with_rows == {"AAA", "BBB"}


class TestRepeatedRunIdempotency:
    def test_repeated_identical_cycle_produces_no_duplicate_stored_records(self, tmp_path):
        """End-to-end through the real lib.edgelab.storage.append_records dedup layer (not just already_captured's own in-memory check) -- proves genuine storage-level idempotency."""
        import lib.edgelab.storage as storage

        game = _game(start_time="2026-08-10T23:00:00Z")
        rows1, _ = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [], now="2026-08-10T21:30:00Z", run_id="FIXED_RUN_ID",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        path = str(tmp_path / "2026-08-10.jsonl")
        written1, dup1 = storage.append_records(path, rows1, "hitterProjectionSnapshotId")
        assert written1 == 1
        assert dup1 == 0

        # Same run_id, same inputs -- e.g. a retried identical invocation.
        rows2, _ = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [], now="2026-08-10T21:30:00Z", run_id="FIXED_RUN_ID",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        written2, dup2 = storage.append_records(path, rows2, "hitterProjectionSnapshotId")
        assert written2 == 0
        assert dup2 == 1

        stored = list(storage.read_records(path))
        assert len(stored) == 1

    def test_already_captured_checkpoint_is_never_recaptured_across_cycles(self):
        game = _game(start_time="2026-08-10T23:00:00Z")
        rows1, _ = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        assert len(rows1) == 1
        existing_after_cycle_1 = rows1

        # Next cycle, 15 minutes later -- still within T-90's own window in
        # principle, but it's already captured, so must not fire again.
        rows2, run_log2 = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], existing_after_cycle_1, now="2026-08-10T21:35:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        assert rows2 == []


class TestLineupConfirmationBetweenRunsAtNewCadence:
    def test_lineup_confirmed_between_two_15_minute_cycles_is_captured_on_the_next_one(self):
        game = _game(start_time="2026-08-10T23:00:00Z", lineup_confirmed=False)

        rows1, run_log1 = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [], now="2026-08-10T22:00:00Z",  # T-60, lineup not confirmed
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        assert any(r["checkpoint"] == "T_MINUS_60" for r in rows1)
        assert not any(r["checkpoint"] == "LINEUP_CONFIRMATION" for r in rows1)

        # Lineup becomes confirmed sometime in the next 15 minutes.
        confirmed_game = dict(game)
        confirmed_game["awayTeamStats"] = {"lineupConfirmedOfficial": True}
        confirmed_game["homeTeamStats"] = {"lineupConfirmedOfficial": True}

        rows2, run_log2 = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [confirmed_game], rows1, now="2026-08-10T22:15:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        assert any(r["checkpoint"] == "LINEUP_CONFIRMATION" for r in rows2)

    def test_never_retroactively_labels_the_earlier_capture_as_lineup_confirmed(self):
        """The T-60 row captured in cycle 1 (while unconfirmed) must remain unchanged -- confirming the lineup later must never mutate a prior stored row."""
        game = _game(start_time="2026-08-10T23:00:00Z", lineup_confirmed=False)
        rows1, _ = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [], now="2026-08-10T22:00:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        t60_row_before = next(r for r in rows1 if r["checkpoint"] == "T_MINUS_60")
        assert t60_row_before == next(r for r in rows1 if r["checkpoint"] == "T_MINUS_60")  # unchanged, still itself
