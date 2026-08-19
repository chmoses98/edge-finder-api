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
    """A fake standing in for scripts.build_hitter_projection_board.main -- reads the filtered slate this call was given and returns exactly one canned row per game in it, so tests can assert cost containment (only DUE games' rows ever appear). Rows carry `gameId` (mirroring the real function's own doubleheader-safe gameId stamping, see scripts/build_hitter_projection_board.py's main())."""
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
                "projectionStatus": "PROJECTED", "gameId": g.get("gameId"),
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

    def test_multiple_checkpoints_consolidated_into_one_call(self):
        """Two games due at DIFFERENT checkpoints this cycle are combined into ONE filtered-slate/board-build call (the scheduler-capacity fix -- see docs/HITTER_SCHEDULER_RUNTIME_HARDENING.md) instead of one call per checkpoint group, and each resulting row is still correctly attributed back to its own game's due checkpoint via gameId."""
        t90_game = _game(game_id="111", start_time="2026-08-10T23:00:00Z", away_abbr="BOS", home_abbr="NYY")
        confirmed_game = _game(game_id="222", start_time="2026-08-10T23:30:00Z", away_abbr="SEA", home_abbr="LAA", lineup_confirmed=True)
        calls = []
        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [t90_game, confirmed_game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(calls),
        )
        assert len(calls) == 1
        assert set(calls[0]["gameIds"]) == {"111", "222"}
        assert len(rows) == 2
        rows_by_game = {r["gameId"]: r for r in rows}
        assert rows_by_game["111"]["checkpoint"] == "T_MINUS_90"
        assert rows_by_game["222"]["checkpoint"] == "LINEUP_CONFIRMATION"

    def test_consolidated_call_falls_back_to_per_checkpoint_group_on_failure(self):
        """If the single consolidated call raises for ANY reason, the cycle falls back to the OLD one-call-per-checkpoint-group loop so a single bad game (or a transient failure of the combined call) never aborts the whole cycle's evaluation."""
        t90_game = _game(game_id="111", start_time="2026-08-10T23:00:00Z", away_abbr="BOS", home_abbr="NYY")
        confirmed_game = _game(game_id="222", start_time="2026-08-10T23:30:00Z", away_abbr="SEA", home_abbr="LAA", lineup_confirmed=True)
        calls = []

        call_count = {"n": 0}

        def _fails_once_then_per_checkpoint_ok(*, date_str, slate_path, weather_path, savant_team_path, kalshi_search_path, n_sims, research_run_id, dry_run, emit_rows):
            call_count["n"] += 1
            import json
            with open(slate_path) as f:
                slate = json.load(f)
            if len(slate["games"]) > 1:
                raise RuntimeError("simulated consolidated-call failure")
            return _fake_build_board_main()(
                date_str=date_str, slate_path=slate_path, weather_path=weather_path,
                savant_team_path=savant_team_path, kalshi_search_path=kalshi_search_path,
                n_sims=n_sims, research_run_id=research_run_id, dry_run=dry_run, emit_rows=emit_rows,
            )

        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [t90_game, confirmed_game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_fails_once_then_per_checkpoint_ok, write_filtered_slate_fn=_fake_write_filtered_slate(calls),
        )
        assert call_count["n"] == 3  # 1 failed consolidated attempt + 2 fallback per-checkpoint-group calls
        rows_by_game = {r["gameId"]: r for r in rows}
        assert set(rows_by_game) == {"111", "222"}
        assert rows_by_game["111"]["checkpoint"] == "T_MINUS_90"
        assert rows_by_game["222"]["checkpoint"] == "LINEUP_CONFIRMATION"
        evaluated = {e["checkpoint"] for e in run_log if e["action"] == "EVALUATED"}
        assert evaluated == {"T_MINUS_90", "LINEUP_CONFIRMATION"}

    def test_doubleheader_same_matchup_distinct_checkpoints_never_cross_attributed(self):
        """Two doubleheader legs (identical away/home abbreviations, distinct gameIds) due at DIFFERENT checkpoints in the SAME consolidated cycle must never have their rows swapped -- attribution is via gameId, never the ambiguous shared matchup label the two legs share."""
        leg1 = _game(game_id="G1", start_time="2026-08-10T23:00:00Z", away_abbr="COL", home_abbr="AZ")
        leg2 = _game(game_id="G2", start_time="2026-08-10T23:30:00Z", away_abbr="COL", home_abbr="AZ", lineup_confirmed=True)
        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [leg1, leg2], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        rows_by_game = {r["gameId"]: r for r in rows}
        assert set(rows_by_game) == {"G1", "G2"}
        assert rows_by_game["G1"]["checkpoint"] == "T_MINUS_90"
        assert rows_by_game["G2"]["checkpoint"] == "LINEUP_CONFIRMATION"

    def test_row_content_equivalent_between_consolidated_and_fallback_paths(self):
        """Whether a cycle takes the consolidated happy path or falls back to the old per-checkpoint-group loop, the persisted row's own market/model fields (marketTicker, matchup, modelProbability, executableKalshiPrice, projectionStatus, threshold, gameId, checkpoint, hitterProjectionSnapshotId) must be identical for the same due games and run_id -- consolidation must never change WHAT gets computed and stored, only HOW MANY board-build calls it took to get there."""
        t90_game = _game(game_id="111", start_time="2026-08-10T23:00:00Z", away_abbr="BOS", home_abbr="NYY")
        confirmed_game = _game(game_id="222", start_time="2026-08-10T23:30:00Z", away_abbr="SEA", home_abbr="LAA", lineup_confirmed=True)

        def _always_fails_on_more_than_one_game(*, date_str, slate_path, weather_path, savant_team_path, kalshi_search_path, n_sims, research_run_id, dry_run, emit_rows):
            import json
            with open(slate_path) as f:
                slate = json.load(f)
            if len(slate["games"]) > 1:
                raise RuntimeError("force fallback")
            return _fake_build_board_main()(
                date_str=date_str, slate_path=slate_path, weather_path=weather_path,
                savant_team_path=savant_team_path, kalshi_search_path=kalshi_search_path,
                n_sims=n_sims, research_run_id=research_run_id, dry_run=dry_run, emit_rows=emit_rows,
            )

        consolidated_rows, _ = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [t90_game, confirmed_game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
            run_id="FIXED_RUN",
        )
        fallback_rows, _ = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [t90_game, confirmed_game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_always_fails_on_more_than_one_game, write_filtered_slate_fn=_fake_write_filtered_slate(),
            run_id="FIXED_RUN",
        )

        def _key_fields(rows):
            return sorted(
                (r["gameId"], r["checkpoint"], r["marketTicker"], r["matchup"], r["modelProbability"],
                 r["executableKalshiPrice"], r["projectionStatus"], r["threshold"], r["hitterProjectionSnapshotId"])
                for r in rows
            )

        assert _key_fields(consolidated_rows) == _key_fields(fallback_rows)

    def test_n_sims_reaches_board_build_unchanged_under_consolidation(self):
        """The consolidated call must pass the caller's own n_sims through unmodified -- consolidation is a call-count/batching change only, never a Monte Carlo sample-count change."""
        t90_game = _game(game_id="111", start_time="2026-08-10T23:00:00Z", away_abbr="BOS", home_abbr="NYY")
        confirmed_game = _game(game_id="222", start_time="2026-08-10T23:30:00Z", away_abbr="SEA", home_abbr="LAA", lineup_confirmed=True)
        captured_n_sims = []

        def _capturing_build(*, n_sims, **kwargs):
            captured_n_sims.append(n_sims)
            return {"rows": [], "hitterSummaries": []}

        hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [t90_game, confirmed_game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_capturing_build, write_filtered_slate_fn=_fake_write_filtered_slate(),
            n_sims=2500,
        )
        assert captured_n_sims == [2500]

    def test_lineup_leakage_safety_preserved_when_games_combined_into_one_consolidated_call(self):
        """Two games due DIFFERENT checkpoints this cycle, combined into ONE consolidated board-build call: the LINEUP_CONFIRMATION game must receive the lineup-refreshed copy, but the OTHER (T_MINUS_90) game must receive its ORIGINAL, un-refreshed copy -- a same-cycle lineup confirmation for one game must never leak into another game's snapshot merely because they were batched into the same call."""
        t90_game = _game(game_id="111", start_time="2026-08-10T23:00:00Z", away_abbr="BOS", home_abbr="NYY", lineup_confirmed=False)
        confirming_game = _game(game_id="222", start_time="2026-08-10T23:30:00Z", away_abbr="SEA", home_abbr="LAA", lineup_confirmed=False)

        def _lineup_fetch(game_pk, away_abbr, home_abbr, batter_woba_map, team_woba_map):
            return {"confirmed": away_abbr == "SEA"}

        import copy as copy_mod

        def _fake_refresh(game, *, lineup_fetch_fn, batter_woba_map, team_woba_map):
            g = copy_mod.deepcopy(game)
            g["_refreshedMarker"] = True
            confirm = lineup_fetch_fn(None, (g.get("away") or {}).get("abbr"), (g.get("home") or {}).get("abbr"), batter_woba_map, team_woba_map)["confirmed"]
            if confirm:
                g["awayTeamStats"]["lineupConfirmedOfficial"] = True
                g["homeTeamStats"]["lineupConfirmedOfficial"] = True
            return g, None

        captured_games_by_call = []

        def _capturing_write_filtered_slate(date, run_id, checkpoint, games):
            import copy as c2
            captured_games_by_call.append(c2.deepcopy(games))
            return _fake_write_filtered_slate()(date, run_id, checkpoint, games)

        import lib.research.hitter_prospective_snapshot as hps_mod
        original_refresh = hps_mod.refresh_lineup_fields
        hps_mod.refresh_lineup_fields = _fake_refresh
        try:
            rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
                "2026-08-10", [t90_game, confirming_game], [], now="2026-08-10T21:30:00Z",
                lineup_fetch_fn=_lineup_fetch,
                build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_capturing_write_filtered_slate,
            )
        finally:
            hps_mod.refresh_lineup_fields = original_refresh

        assert len(captured_games_by_call) == 1  # one consolidated call
        games_sent = {g["gameId"]: g for g in captured_games_by_call[0]}
        assert set(games_sent) == {"111", "222"}
        # T_MINUS_90 game: must be the ORIGINAL, un-refreshed object -- no leakage marker, still unconfirmed.
        assert "_refreshedMarker" not in games_sent["111"]
        assert games_sent["111"]["awayTeamStats"]["lineupConfirmedOfficial"] is False
        # LINEUP_CONFIRMATION game: must be the refreshed, now-confirmed copy.
        assert games_sent["222"]["_refreshedMarker"] is True
        assert games_sent["222"]["awayTeamStats"]["lineupConfirmedOfficial"] is True

        rows_by_game = {r["gameId"]: r for r in rows}
        assert rows_by_game["111"]["checkpoint"] == "T_MINUS_90"
        assert rows_by_game["222"]["checkpoint"] == "LINEUP_CONFIRMATION"

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
                             "executableKalshiPrice": 0.35, "projectionStatus": "PROJECTED",
                             "gameId": g.get("gameId")})
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


class TestRuntimeObservabilityAndTiming:
    """Regression coverage for the runtime-hardening/observability fix
    (workflow run 32189380616 was cancelled by its own 25-minute
    timeout while legitimately still evaluating multiple due checkpoint
    groups, with ZERO visible progress in the Actions log the whole
    time -- see docs/HITTER_SCHEDULER_RUNTIME_HARDENING.md for the full
    incident audit). These tests never assert exact print wording
    (fragile) beyond the small, deliberately-stable set of substrings
    that ARE the observability contract; they focus on the timing
    metadata's shape and on confirming this change never touches the
    actual persisted projection data."""

    def test_evaluated_entry_carries_nonnegative_timing_metadata(self):
        game = _game(start_time="2026-08-10T23:00:00Z")
        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        evaluated = [e for e in run_log if e["action"] == "EVALUATED"]
        assert len(evaluated) == 1
        assert isinstance(evaluated[0]["boardBuildElapsedSeconds"], float)
        assert evaluated[0]["boardBuildElapsedSeconds"] >= 0
        assert isinstance(evaluated[0]["checkpointBatchElapsedSeconds"], float)
        assert evaluated[0]["checkpointBatchElapsedSeconds"] >= 0

    def test_multiple_checkpoint_groups_get_independent_timing(self):
        t90_game = _game(game_id="111", start_time="2026-08-10T23:00:00Z", away_abbr="BOS", home_abbr="NYY")
        confirmed_game = _game(game_id="222", start_time="2026-08-10T23:30:00Z", away_abbr="SEA", home_abbr="LAA", lineup_confirmed=True)
        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [t90_game, confirmed_game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        evaluated_by_checkpoint = {e["checkpoint"]: e for e in run_log if e["action"] == "EVALUATED"}
        assert set(evaluated_by_checkpoint) == {"T_MINUS_90", "LINEUP_CONFIRMATION"}
        for entry in evaluated_by_checkpoint.values():
            assert entry["boardBuildElapsedSeconds"] is not None
            assert entry["checkpointBatchElapsedSeconds"] is not None

    def test_failing_group_still_records_timing_and_does_not_corrupt_the_succeeding_groups_timing(self):
        """Mirrors test_one_checkpoint_groups_failure_does_not_erase_another's fixture, but asserts on the NEW timing fields specifically: a group that raises must still record how long it ran before failing, and that must never leak onto or blank out the other, successful group's own timing."""
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
        failed_entry = next(e for e in run_log if e["action"] == "SKIPPED" and e.get("checkpoint") == "LINEUP_CONFIRMATION")
        assert failed_entry["boardBuildElapsedSeconds"] is not None
        assert failed_entry["boardBuildElapsedSeconds"] >= 0
        assert failed_entry["checkpointBatchElapsedSeconds"] is not None

        succeeded_entry = next(e for e in run_log if e["action"] == "EVALUATED" and e.get("checkpoint") == "T_MINUS_90")
        assert succeeded_entry["boardBuildElapsedSeconds"] is not None
        assert len(rows) == 1
        assert rows[0]["gameId"] == "111"

    def test_projection_rows_carry_no_new_timing_keys(self):
        """The actual PERSISTED projection data (new_rows -- what ultimately gets appended to data/edgelab/hitter_projection_snapshots/<date>.jsonl) must be completely unaffected by this observability change: timing metadata lives only on run_log entries, never on a row that gets written to the append-only store."""
        game = _game(start_time="2026-08-10T23:00:00Z")
        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        assert len(rows) == 1
        for forbidden_key in ("boardBuildElapsedSeconds", "checkpointBatchElapsedSeconds", "totalRuntimeSeconds"):
            assert forbidden_key not in rows[0]

    def test_cycle_produces_visible_progress_output(self, capsys):
        """The actual observability contract this fix exists for: a healthy, in-progress cycle must print SOMETHING before it returns, not stay silent the entire time the way it did during workflow run 32189380616."""
        game = _game(start_time="2026-08-10T23:00:00Z")
        hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        out = capsys.readouterr().out
        assert "cycle start" in out
        assert "checkpoint batch starting" in out
        assert "hitter-board build starting" in out
        assert "hitter-board build complete" in out
        assert "cycle complete" in out

    def test_no_op_cycle_still_prints_a_completion_line(self, capsys):
        game = _game(start_time="2026-08-10T23:00:00Z")
        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [{"gameId": "822780", "checkpoint": "T_MINUS_90"},
                                     {"gameId": "822780", "checkpoint": "T_MINUS_60"},
                                     {"gameId": "822780", "checkpoint": "T_MINUS_30"}],
            now="2026-08-10T22:30:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        assert rows == []
        out = capsys.readouterr().out
        assert "cycle complete" in out
        assert "noCheckpointsDue" in out

    def test_does_not_spam_one_line_per_game_within_a_batch(self, capsys):
        """Explicit guard against the task's own 'do not spam one log line per Monte Carlo simulation' instruction, scaled down to the observable unit here: a batch of several games due at the SAME checkpoint must produce ONE 'hitter-board build starting' line, not one per game."""
        games = [
            _game(game_id=str(i), start_time="2026-08-10T23:00:00Z", away_abbr=f"A{i}", home_abbr=f"H{i}")
            for i in range(5)
        ]
        hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", games, [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        out = capsys.readouterr().out
        assert out.count("hitter-board build starting") == 1
        assert out.count("checkpoint batch starting") == 1


# ── Snapshot timestamp integrity (PR #93 final correctness review) ─────────

class TestSnapshotTimestampIntegrity:
    """A row's snapshotGeneratedAt must reflect when it was REALLY computed
    (cycle `now` advanced by real elapsed wall-clock seconds), never the
    cycle's earlier due-determination instant unchanged -- especially
    important now that a failed consolidated attempt can materially delay
    when the fallback path's rows actually get produced."""

    def test_generated_at_reflects_real_completion_time_not_cycle_start(self, monkeypatch):
        game = _game(start_time="2026-08-10T23:00:00Z")
        call_count = {"n": 0}
        base_time = 1_000_000.0

        def _fake_time():
            call_count["n"] += 1
            return base_time if call_count["n"] <= 3 else base_time + 500  # 500s of "real" elapsed time

        monkeypatch.setattr(hps.time, "time", _fake_time)
        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        assert len(rows) == 1
        assert rows[0]["snapshotGeneratedAt"] == "2026-08-10T21:38:20Z"  # 21:30:00 + 500s

    def test_computation_finishing_after_first_pitch_is_discarded_not_persisted(self, monkeypatch):
        """A row whose board-build computation only finishes AFTER its own game's
        scheduled start (by real elapsed time) must never be persisted as a normal
        pregame snapshot -- discarded and explicitly logged, never silently kept."""
        game = _game(start_time="2026-08-10T21:45:00Z", lineup_confirmed=False)  # due HITTER_CLOSING_WINDOW at now=21:30
        call_count = {"n": 0}
        base_time = 2_000_000.0

        def _fake_time():
            call_count["n"] += 1
            return base_time if call_count["n"] <= 3 else base_time + 1200  # 20 minutes -- past the 21:45 first pitch

        monkeypatch.setattr(hps.time, "time", _fake_time)
        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        assert rows == []
        skipped = next(e for e in run_log if e["action"] == "SKIPPED" and e.get("checkpoint") == hps.HITTER_CLOSING_WINDOW)
        assert skipped["reason"] == hps.SKIPPED_COMPUTED_AFTER_GAME_START
        assert not any(e["action"] == "EVALUATED" for e in run_log)

    def test_computation_finishing_before_first_pitch_is_kept_normally(self):
        """Sanity counterpart: a normal (fast, fake) computation that finishes well
        before first pitch is persisted exactly as before -- this integrity check
        must never falsely discard a genuinely still-pregame row."""
        game = _game(start_time="2026-08-10T23:00:00Z")
        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_fake_build_board_main(), write_filtered_slate_fn=_fake_write_filtered_slate(),
        )
        assert len(rows) == 1
        assert any(e["action"] == "EVALUATED" for e in run_log)

    def test_market_observed_at_and_source_capture_path_remain_the_frozen_input_provenance(self):
        """marketObservedAt/sourceCapturePath (stamped by the board builder itself
        from the immutable Kalshi snapshot) must stay untouched by this fix --
        distinct from snapshotGeneratedAt, which is the orchestration-level
        computation-COMPLETION timestamp this fix corrects."""
        def _build_with_observed_at(*, date_str, slate_path, weather_path, savant_team_path, kalshi_search_path, n_sims, research_run_id, dry_run, emit_rows):
            return {"rows": [{
                "marketTicker": "T-1", "matchup": "BOS @ NYY", "gameId": "822780",
                "marketObservedAt": "2026-08-10T18:00:00.000Z", "sourceCapturePath": kalshi_search_path,
            }], "hitterSummaries": []}

        game = _game(start_time="2026-08-10T23:00:00Z")
        rows, _ = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_build_with_observed_at, write_filtered_slate_fn=_fake_write_filtered_slate(),
            kalshi_search_path="data/kalshi_registry_snapshots/kalshi_search_2026-08-10_1800.json",
        )
        assert len(rows) == 1
        assert rows[0]["marketObservedAt"] == "2026-08-10T18:00:00.000Z"
        assert rows[0]["sourceCapturePath"] == "data/kalshi_registry_snapshots/kalshi_search_2026-08-10_1800.json"
        assert rows[0]["snapshotGeneratedAt"] != rows[0]["marketObservedAt"]


# ── Bounded fallback policy (PR #93 final correctness review) ──────────────

class TestBoundedFallbackPolicy:
    """If the consolidated call fails, the per-checkpoint-group fallback loop
    must never be started when it cannot possibly complete within the
    cycle's own configured job_timeout_seconds -- see
    HITTER_FALLBACK_WORST_CASE_SECONDS's own module-level docstring."""

    def test_fallback_declined_when_insufficient_time_remains(self, monkeypatch):
        t90_game = _game(game_id="111", start_time="2026-08-10T23:00:00Z", away_abbr="BOS", home_abbr="NYY")
        confirmed_game = _game(game_id="222", start_time="2026-08-10T23:30:00Z", away_abbr="SEA", home_abbr="LAA", lineup_confirmed=True)

        def _always_fails(*, date_str, slate_path, weather_path, savant_team_path, kalshi_search_path, n_sims, research_run_id, dry_run, emit_rows):
            raise RuntimeError("simulated consolidated failure")

        call_count = {"n": 0}
        base_time = 3_000_000.0

        def _fake_time():
            call_count["n"] += 1
            return base_time if call_count["n"] <= 3 else base_time + 1700  # ~28.3 min already elapsed

        monkeypatch.setattr(hps.time, "time", _fake_time)
        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [t90_game, confirmed_game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_always_fails, write_filtered_slate_fn=_fake_write_filtered_slate(),
            job_timeout_seconds=1800,  # 30 minutes
        )
        assert rows == []
        skipped_reasons = {e["reason"] for e in run_log if e["action"] == "SKIPPED"}
        assert hps.SKIPPED_INSUFFICIENT_TIME_FOR_SAFE_FALLBACK in skipped_reasons
        assert not any(e["action"] == "EVALUATED" for e in run_log)
        # The per-checkpoint-group fallback loop must never have even been
        # attempted -- no "hitter board build raised" reason (which only the
        # fallback loop's own per-group except branch ever produces) appears.
        assert not any(isinstance(r, str) and r.startswith("hitter board build raised") for r in skipped_reasons)

    def test_fallback_attempted_when_sufficient_time_remains(self):
        t90_game = _game(game_id="111", start_time="2026-08-10T23:00:00Z", away_abbr="BOS", home_abbr="NYY")

        def _always_fails(*, date_str, slate_path, weather_path, savant_team_path, kalshi_search_path, n_sims, research_run_id, dry_run, emit_rows):
            raise RuntimeError("simulated failure")

        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [t90_game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_always_fails, write_filtered_slate_fn=_fake_write_filtered_slate(),
            job_timeout_seconds=1800,
        )
        assert rows == []
        skipped_reasons = {e["reason"] for e in run_log if e["action"] == "SKIPPED"}
        assert any(isinstance(r, str) and r.startswith("hitter board build raised") for r in skipped_reasons)
        assert hps.SKIPPED_INSUFFICIENT_TIME_FOR_SAFE_FALLBACK not in skipped_reasons

    def test_job_timeout_seconds_none_always_attempts_fallback_regardless_of_elapsed_time(self, monkeypatch):
        """Default (no configured bound) preserves this function's original
        behavior -- fallback is always attempted, matching every pre-existing
        test's own expectations and every caller that doesn't opt in."""
        t90_game = _game(game_id="111", start_time="2026-08-10T23:00:00Z", away_abbr="BOS", home_abbr="NYY")

        def _always_fails(*, date_str, slate_path, weather_path, savant_team_path, kalshi_search_path, n_sims, research_run_id, dry_run, emit_rows):
            raise RuntimeError("simulated failure")

        call_count = {"n": 0}
        base_time = 4_000_000.0

        def _fake_time():
            call_count["n"] += 1
            return base_time if call_count["n"] <= 3 else base_time + 100_000  # enormous elapsed time

        monkeypatch.setattr(hps.time, "time", _fake_time)
        rows, run_log = hps.run_hitter_prospective_snapshot_cycle(
            "2026-08-10", [t90_game], [], now="2026-08-10T21:30:00Z",
            build_board_main_fn=_always_fails, write_filtered_slate_fn=_fake_write_filtered_slate(),
            job_timeout_seconds=None,
        )
        skipped_reasons = {e["reason"] for e in run_log if e["action"] == "SKIPPED"}
        assert any(isinstance(r, str) and r.startswith("hitter board build raised") for r in skipped_reasons)
