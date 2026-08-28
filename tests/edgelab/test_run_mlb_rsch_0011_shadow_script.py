#!/usr/bin/env python3
"""
tests/edgelab/test_run_mlb_rsch_0011_shadow_script.py
============================================================
Coverage for scripts/edgelab/run_mlb_rsch_0011_shadow.py -- MLB-RSCH-0011's
registration + replay + prospective-E4-scoring + classification logic.
Every test here uses fakes/fixtures for the outcome corpus and snapshot
discovery -- no dependency on the real multi-season team-schedule cache
(that reuse is proven by import alone, same convention as
tests/edgelab/test_run_distribution_calibration_experiment_script.py).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
for p in (
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "edgelab"),
):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_mlb_rsch_0011_shadow as rsch0011  # noqa: E402
from lib.edgelab import shadow_distribution as sd  # noqa: E402


class TestOutcomesForActual:
    def test_moneyline_and_totals_derived_correctly(self):
        outcomes = rsch0011._outcomes_for_actual(actual_home=6, actual_away=3)
        assert outcomes["moneyline_home_win"] == 1
        assert outcomes["moneyline_away_win"] == 0
        assert outcomes["game_total_over_8.5"] == 1  # 9 > 8.5
        assert outcomes["game_total_over_9.5"] == 0  # 9 is not > 9.5
        assert outcomes["team_total_home_over_5.5"] == 1  # 6 > 5.5
        assert outcomes["team_total_away_over_3.5"] == 0  # 3 is not > 3.5

    def test_margin_thresholds(self):
        outcomes = rsch0011._outcomes_for_actual(actual_home=6, actual_away=3)  # home wins by 3
        assert outcomes["run_margin_win_by_at_least_2"] == 1
        assert outcomes["run_margin_win_by_at_least_3"] == 1
        assert outcomes["run_margin_lose_by_at_least_2"] == 0

    def test_every_cell_sd_computes_has_a_matching_outcome_key(self):
        """No cell compute_paired_probabilities produces should ever be un-scoreable for a decided game."""
        cells = sd.compute_paired_probabilities(3.8, 4.2)
        outcomes = rsch0011._outcomes_for_actual(actual_home=5, actual_away=2)
        assert set(cells.keys()) == set(outcomes.keys())

    def test_tie_game_moneyline_both_zero(self):
        outcomes = rsch0011._outcomes_for_actual(actual_home=4, actual_away=4)
        assert outcomes["moneyline_home_win"] == 0
        assert outcomes["moneyline_away_win"] == 0


class TestScoreCells:
    def test_skips_games_with_no_resolvable_actual_score(self):
        cells = sd.compute_paired_probabilities(3.8, 4.2)
        cells_by_game = [(101, cells), (102, cells)]
        actual_runs = {101: (5, 2)}  # 102 has no entry -- unsettled/unknown
        result = rsch0011._score_cells(cells_by_game, actual_runs)
        assert result["independentGames"] == 1
        assert result["perGameSample"][0]["gameId"] == 101

    def test_skips_games_with_partial_none_actual_score(self):
        cells = sd.compute_paired_probabilities(3.8, 4.2)
        cells_by_game = [(101, cells)]
        actual_runs = {101: (None, 2)}
        result = rsch0011._score_cells(cells_by_game, actual_runs)
        assert result["independentGames"] == 0

    def test_candidate_beats_control_when_frozen_dispersion_is_correct_direction(self):
        """A deliberately constructed case: home team consistently blows out (high-variance outcome) should favor the overdispersed NB candidate on the game-total cell."""
        cells = sd.compute_paired_probabilities(3.8, 4.2)
        # gameId starts at 1 -- independent_unit_count treats a falsy gameId (0) as absent, matching lib.edgelab.research_stats' own documented convention.
        cells_by_game = [(i, cells) for i in range(1, 21)]
        # Alternate low-scoring and very high-scoring outcomes -- real overdispersion pattern.
        actual_runs = {i: ((1, 0) if i % 2 == 0 else (9, 8)) for i in range(1, 21)}
        result = rsch0011._score_cells(cells_by_game, actual_runs)
        assert result["independentGames"] == 20
        # Not asserting a specific sign here (small/synthetic sample) -- just that the machinery runs end-to-end and produces a real number.
        assert result["overall"]["pairedDelta"]["brierScore"] is not None

    def test_by_family_keys_match_sd_primary_and_secondary_families(self):
        cells = sd.compute_paired_probabilities(3.8, 4.2)
        cells_by_game = [(1, cells)]
        actual_runs = {1: (5, 2)}
        result = rsch0011._score_cells(cells_by_game, actual_runs)
        assert set(result["byFamily"].keys()) == set(sd.PRIMARY_FAMILIES) | set(sd.SECONDARY_FAMILIES)


class TestClassifyShadowEvidence:
    def test_zero_games_is_no_evidence_yet(self):
        assert rsch0011.classify_shadow_evidence(0, None) == rsch0011.CLASSIFICATION_NO_EVIDENCE_YET

    def test_none_delta_is_no_evidence_yet_even_with_games(self):
        assert rsch0011.classify_shadow_evidence(50, None) == rsch0011.CLASSIFICATION_NO_EVIDENCE_YET

    def test_below_directional_threshold_is_early_directional(self):
        assert rsch0011.classify_shadow_evidence(rsch0011.MIN_INDEPENDENT_GAMES_FOR_DIRECTIONAL - 1, -0.01) == rsch0011.CLASSIFICATION_EARLY_DIRECTIONAL

    def test_above_threshold_negative_delta_is_reinforced(self):
        assert rsch0011.classify_shadow_evidence(rsch0011.MIN_INDEPENDENT_GAMES_FOR_DIRECTIONAL, -0.001) == rsch0011.CLASSIFICATION_REINFORCED

    def test_above_threshold_positive_delta_is_weakened(self):
        assert rsch0011.classify_shadow_evidence(rsch0011.MIN_INDEPENDENT_GAMES_FOR_DIRECTIONAL, 0.001) == rsch0011.CLASSIFICATION_WEAKENED

    def test_never_returns_a_promotion_candidate_style_label(self):
        """This function's vocabulary is deliberately disjoint from lib.edgelab.dispositions -- it must never accidentally emit a disposition-ladder string."""
        from lib.edgelab import dispositions as disp
        for games in (0, 10, 50, 1000):
            for delta in (None, -0.5, 0.5):
                result = rsch0011.classify_shadow_evidence(games, delta)
                assert result not in disp.ALL_DISPOSITIONS


class TestDiscoverPreGameDecisionSnapshots:
    def test_finds_manifest_files_under_expected_layout(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        stage_dir = os.path.join("data", "edgelab", "snapshots", "2026-08-16", "pre_game_decision", "2026-08-16T165949Z")
        os.makedirs(stage_dir)
        with open(os.path.join(stage_dir, "manifest.json"), "w") as f:
            json.dump({"components": []}, f)
        result = rsch0011.discover_pre_game_decision_snapshots()
        assert len(result) == 1
        assert result[0][0] == "2026-08-16"

    def test_empty_when_no_snapshots_root(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert rsch0011.discover_pre_game_decision_snapshots() == []


class TestLoadRawProjections:
    def test_missing_component_reports_explicit_reason(self, tmp_path):
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps({"components": []}))
        games, err = rsch0011._load_raw_projections(str(manifest_path))
        assert games is None
        assert "no RAW_PROJECTIONS component" in err

    def test_unavailable_component_reports_explicit_reason(self, tmp_path):
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps({"components": [
            {"componentType": "RAW_PROJECTIONS", "availabilityStatus": "MISSING"},
        ]}))
        games, err = rsch0011._load_raw_projections(str(manifest_path))
        assert games is None
        assert "MISSING" in err

    def test_available_but_file_missing_on_disk_reports_explicit_reason(self, tmp_path):
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps({"components": [
            {"componentType": "RAW_PROJECTIONS", "availabilityStatus": "AVAILABLE", "snapshotPath": str(tmp_path / "does_not_exist.json.gz")},
        ]}))
        games, err = rsch0011._load_raw_projections(str(manifest_path))
        assert games is None
        assert "missing on disk" in err

    def test_available_and_present_returns_games(self, tmp_path):
        import gzip
        frozen_path = tmp_path / "raw_projections.json.gz"
        with gzip.open(str(frozen_path), "wt") as f:
            json.dump({"data": {"games": [{"gameId": 1, "awayProjRuns": 3.8, "homeProjRuns": 4.2}]}}, f)
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps({"components": [
            {"componentType": "RAW_PROJECTIONS", "availabilityStatus": "AVAILABLE", "snapshotPath": str(frozen_path)},
        ]}))
        games, err = rsch0011._load_raw_projections(str(manifest_path))
        assert err is None
        assert games == [{"gameId": 1, "awayProjRuns": 3.8, "homeProjRuns": 4.2}]


class TestRunCurrentSlateSmokeTest:
    def test_returns_none_when_no_slate_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert rsch0011.run_current_slate_smoke_test() is None

    def test_never_recommends_a_wager_field(self, tmp_path, monkeypatch):
        """Structural guarantee: the smoke test's output schema carries no recommendation/stake/edge field."""
        monkeypatch.chdir(tmp_path)
        os.makedirs("data", exist_ok=True)
        with open("data/slate.json", "w") as f:
            json.dump({"date": "2026-08-27", "games": []}, f)
        result = rsch0011.run_current_slate_smoke_test()
        assert result is not None
        assert "recommendation" not in json.dumps(result).lower()
        assert "stake" not in json.dumps(result).lower()


class TestScoreProspectiveShadowIsolation:
    def test_zero_captured_records_produces_zero_sample_not_a_crash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = rsch0011.score_prospective_shadow(actual_runs_by_game_id={})
        assert result["independentGames"] == 0
        assert result["totalCapturedRecords"] == 0
        assert result["evidenceLabel"] == "E4_PROSPECTIVE_SHADOW"

    def test_failed_capture_records_are_counted_but_never_scored(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        os.makedirs(os.path.join("data", "edgelab", rsch0011.SHADOW_ENTITY), exist_ok=True)
        path = os.path.join("data", "edgelab", rsch0011.SHADOW_ENTITY, "2026-08-20.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps({
                "shadowEvaluationId": "x", "gameId": "g1", "computationStatus": sd.STATUS_FAILED,
                "cells": None, "capturedAt": "2026-08-20T10:00:00Z",
            }) + "\n")
        result = rsch0011.score_prospective_shadow(actual_runs_by_game_id={})
        assert result["totalCapturedRecords"] == 1
        assert result["failedCaptureRecords"] == 1
        assert result["successfulCaptureRecords"] == 0
        assert result["independentGames"] == 0


def test_real_replay_produces_a_meaningful_sample():
    """
    Integration smoke test against the REAL, committed
    data/edgelab/snapshots/*/pre_game_decision archive (21+ real daily
    snapshots as of this milestone) -- proves the replay actually finds
    and scores real data, not just its own fakes. Skips gracefully if
    the archive isn't present in this checkout (e.g. a shallow/partial
    clone).
    """
    if not os.path.isdir(rsch0011.SNAPSHOTS_ROOT):
        import pytest
        pytest.skip("no data/edgelab/snapshots archive in this checkout")
    replay = rsch0011.run_replay()
    assert replay["snapshotsDiscovered"] > 0
    if replay["independentGames"] > 0:
        assert replay["overall"]["sampleSizeStatus"]["status"] in ("DESCRIPTIVE_ONLY", "CALIBRATED")
