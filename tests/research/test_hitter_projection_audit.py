#!/usr/bin/env python3
"""
tests/research/test_hitter_projection_audit.py
===================================================
Deterministic unit tests for lib/research/hitter_projection_audit.py and
scripts/research/build_hitter_projection_audit.py, using synthetic
fixtures written to a tmp_path -- never depends on real repo data being
present in any particular state, so these tests are stable regardless of
how many real hitter_projection_board.json files exist on disk at test
time.
"""
import json
import os

import pytest

from lib.research import hitter_projection_audit as audit
from scripts.research.build_hitter_projection_audit import build_reports, write_reports


def _write_board(pipeline_root, date, rows, summary_extra=None):
    """pipeline_root: the exact directory that will contain <date>/hitter_projection_board.json (i.e. the caller's own data/pipeline equivalent -- NOT a parent of it)."""
    board_dir = os.path.join(str(pipeline_root), date)
    os.makedirs(board_dir, exist_ok=True)
    summary = {
        "date": date, "generatedAt": f"{date}T18:00:00Z",
        "rowsByProjectionStatus": {},
        "totalRows": len(rows),
    }
    if summary_extra:
        summary.update(summary_extra)
    doc = {
        "meta": {"createdAt": f"{date}T18:00:00Z", "stage": "hitter_projection_board"},
        "data": {"rows": rows, "summary": summary, "hitterSummaries": []},
    }
    path = os.path.join(board_dir, "hitter_projection_board.json")
    with open(path, "w") as fh:
        json.dump(doc, fh)
    return path


def _write_settlement(settlements_root, date, rows):
    """settlements_root: the exact directory that will contain <date>.jsonl."""
    os.makedirs(str(settlements_root), exist_ok=True)
    path = os.path.join(str(settlements_root), f"{date}.jsonl")
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _projected_row(ticker, family, threshold, player="Test Hitter", player_id="123",
                    model_prob=0.30, entry_price=0.28, gen_at="2026-08-13T18:00:05Z",
                    obs_at="2026-08-13T18:00:00.000Z",
                    snapshot_path="data/kalshi_registry_snapshots/kalshi_search_2026-08-13_180000_standalone.json"):
    return {
        "marketTicker": ticker, "marketFamily": family, "threshold": threshold,
        "player": player, "playerId": player_id, "matchup": "AAA @ BBB",
        "naturalLanguageMarket": f"{player}: {threshold}+ stat?",
        "modelProbability": model_prob, "executableKalshiPrice": entry_price,
        "rawProbabilityEdge": round(model_prob - entry_price, 4) if model_prob is not None and entry_price is not None else None,
        "monteCarloStderr": 0.01, "distributionUsed": "hits",
        "projectionStatus": "PROJECTED", "projectionStatusReason": None,
        "pricingStatus": "PRICED",
        "researchRunId": "HITTER_PROJECTION_STANDALONE_TEST",
        "sourceCapturePath": snapshot_path,
        "marketObservedAt": obs_at, "projectionGeneratedAt": gen_at,
    }


def _settlement_row(ticker, family, outcome, checkpoints=None):
    return {
        "marketTicker": ticker, "marketFamily": family, "outcome": outcome,
        "settlementStatus": "SETTLED",
        "hypotheticalReturnsByCheckpoint": checkpoints or [],
        "settlementEvidence": {
            "resolutionStatus": "RESOLVED", "actualValue": 1 if outcome == "YES" else 0,
            "gameStatus": "Final",
        },
    }


# ---------------------------------------------------------------------------
# Discovery / loading
# ---------------------------------------------------------------------------

class TestDiscoveryAndLoading:
    def test_discover_projection_boards_finds_only_date_named_dirs(self, tmp_path):
        pipeline_root = tmp_path / "pipeline"
        (pipeline_root / "2026-08-13").mkdir(parents=True)
        (pipeline_root / "2026-08-13" / "hitter_projection_board.json").write_text("{}")
        (pipeline_root / "not-a-date").mkdir(parents=True)
        (pipeline_root / "not-a-date" / "hitter_projection_board.json").write_text("{}")

        found = audit.discover_projection_boards(str(pipeline_root))
        assert found == [("2026-08-13", str(pipeline_root / "2026-08-13" / "hitter_projection_board.json"))]

    def test_load_board_tags_rows_with_source_date_and_path(self, tmp_path):
        row = _projected_row("T1-1", "hitter_hits", 1)
        path = _write_board(tmp_path / "pipeline", "2026-08-13", [row])
        rows, summary = audit.load_board("2026-08-13", path)
        assert rows[0]["sourceDate"] == "2026-08-13"
        assert rows[0]["sourceBoardPath"] == path
        assert summary["totalRows"] == 1

    def test_load_board_missing_file_returns_empty_never_raises(self, tmp_path):
        rows, summary = audit.load_board("2026-01-01", str(tmp_path / "nope.json"))
        assert rows == []
        assert summary is None

    def test_load_settlement_index_missing_file_returns_empty(self, tmp_path):
        index = audit.load_settlement_index("2026-08-13", str(tmp_path / "settlements"))
        assert index == {}

    def test_load_settlement_index_filters_to_hitter_families_only(self, tmp_path):
        _write_settlement(tmp_path / "settlements", "2026-08-13", [
            _settlement_row("T1-1", "hitter_hits", "YES"),
            {"marketTicker": "GAME-1", "marketFamily": "game_result", "outcome": "YES", "settlementStatus": "SETTLED"},
        ])
        index = audit.load_settlement_index("2026-08-13", str(tmp_path / "settlements"))
        assert list(index.keys()) == ["T1-1"]


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

class TestProvenanceConfidence:
    def test_verified_when_generated_shortly_after_observation_and_file_exists(self, tmp_path):
        snap_dir = tmp_path / "data" / "kalshi_registry_snapshots"
        snap_dir.mkdir(parents=True)
        snap_name = "kalshi_search_2026-08-13_180000_standalone.json"
        (snap_dir / snap_name).write_text("{}")
        row = _projected_row("T1-1", "hitter_hits", 1, snapshot_path=f"data/kalshi_registry_snapshots/{snap_name}")
        confidence, reason = audit.classify_provenance_confidence(row, repo_root=str(tmp_path))
        assert confidence == "PROSPECTIVE_VERIFIED"
        assert reason is None

    def test_uncertain_when_snapshot_file_missing_from_disk(self, tmp_path):
        row = _projected_row("T1-1", "hitter_hits", 1)
        confidence, reason = audit.classify_provenance_confidence(row, repo_root=str(tmp_path))
        assert confidence == "PROVENANCE_UNCERTAIN"
        assert reason == "SNAPSHOT_FILE_NOT_FOUND_ON_DISK"

    def test_uncertain_when_generated_before_its_own_market_observation(self, tmp_path):
        snap_dir = tmp_path / "data" / "kalshi_registry_snapshots"
        snap_dir.mkdir(parents=True)
        snap_name = "kalshi_search_2026-08-13_180000_standalone.json"
        (snap_dir / snap_name).write_text("{}")
        row = _projected_row(
            "T1-1", "hitter_hits", 1,
            gen_at="2026-08-13T17:00:00Z", obs_at="2026-08-13T18:00:00.000Z",
            snapshot_path=f"data/kalshi_registry_snapshots/{snap_name}",
        )
        confidence, reason = audit.classify_provenance_confidence(row, repo_root=str(tmp_path))
        assert confidence == "PROVENANCE_UNCERTAIN"
        assert reason.startswith("GENERATED_AT_PRECEDES_MARKET_OBSERVATION")

    def test_wide_gap_within_a_long_run_still_verified_when_lag_budget_supplied(self, tmp_path):
        """A big board can legitimately take 20+ minutes to simulate every hitter -- a late-in-run row must not be misclassified just because it lands long after the shared snapshot's capture instant."""
        snap_dir = tmp_path / "data" / "kalshi_registry_snapshots"
        snap_dir.mkdir(parents=True)
        snap_name = "kalshi_search_2026-08-13_180000_standalone.json"
        (snap_dir / snap_name).write_text("{}")
        row = _projected_row(
            "T1-1", "hitter_hits", 1,
            gen_at="2026-08-13T18:19:00Z", obs_at="2026-08-13T18:00:00.000Z",
            snapshot_path=f"data/kalshi_registry_snapshots/{snap_name}",
        )
        confidence, _ = audit.classify_provenance_confidence(row, repo_root=str(tmp_path), max_generation_lag_seconds=1200)
        assert confidence == "PROSPECTIVE_VERIFIED"

    def test_missing_projection_generated_at(self):
        row = _projected_row("T1-1", "hitter_hits", 1)
        row["projectionGeneratedAt"] = None
        confidence, reason = audit.classify_provenance_confidence(row)
        assert confidence == "PROVENANCE_UNCERTAIN"
        assert reason == "MISSING_PROJECTION_GENERATED_AT"


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------

class TestGrading:
    def test_no_settlement_found_is_unresolved(self):
        row = _projected_row("T1-1", "hitter_hits", 1)
        grade = audit.grade_row(row, None)
        assert grade["propositionOutcome"] == "UNRESOLVED"
        assert grade["unresolvedReason"] == "NO_SETTLEMENT_RECORD_FOUND"

    def test_settled_yes_outcome(self):
        row = _projected_row("T1-1", "hitter_hits", 1)
        settlement = _settlement_row("T1-1", "hitter_hits", "YES")
        grade = audit.grade_row(row, settlement)
        assert grade["propositionOutcome"] == "YES"

    def test_unsettled_status_is_unresolved_not_a_guess(self):
        row = _projected_row("T1-1", "hitter_hits", 1)
        settlement = dict(_settlement_row("T1-1", "hitter_hits", "YES"), settlementStatus="PENDING")
        settlement["outcome"] = None
        grade = audit.grade_row(row, settlement)
        assert grade["propositionOutcome"] == "UNRESOLVED"

    def test_never_invents_a_void_the_settlement_layer_did_not_produce(self):
        row = _projected_row("T1-1", "hitter_hits", 1)
        settlement = dict(_settlement_row("T1-1", "hitter_hits", "YES"))
        settlement["settlementStatus"] = "UNRESOLVED"
        settlement["outcome"] = None
        settlement["settlementEvidence"]["resolutionStatus"] = "SETTLEMENT_UNRESOLVED"
        settlement["settlementEvidence"]["resolutionReason"] = "player_participation_unverified"
        grade = audit.grade_row(row, settlement)
        assert grade["propositionOutcome"] == "UNRESOLVED"
        assert grade["unresolvedReason"] == "player_participation_unverified"


class TestBuildGradedRow:
    def _verified_row(self, tmp_path, **kwargs):
        snap_dir = tmp_path / "data" / "kalshi_registry_snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        snap_name = "kalshi_search_2026-08-13_180000_standalone.json"
        (snap_dir / snap_name).write_text("{}")
        defaults = dict(snapshot_path=f"data/kalshi_registry_snapshots/{snap_name}")
        defaults.update(kwargs)
        return _projected_row("T1-1", "hitter_hits", 1, **defaults)

    def test_edge_positive_picks_yes_side(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        row = self._verified_row(tmp_path, model_prob=0.40, entry_price=0.30)
        row["sourceDate"] = "2026-08-13"
        settlement = _settlement_row("T1-1", "hitter_hits", "YES")
        graded = audit.build_graded_row(row, settlement)
        assert graded["simulatedBetSide"] == "YES"
        assert graded["simulatedBetEntryPrice"] == 0.30
        assert graded["simulatedBetWon"] is True
        assert graded["simulatedBetNetPL"] is not None
        assert graded["simulatedBetNetPL"] > 0  # won a positive-EV-priced YES bet

    def test_edge_negative_picks_no_side(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        row = self._verified_row(tmp_path, model_prob=0.20, entry_price=0.30)
        row["sourceDate"] = "2026-08-13"
        settlement = _settlement_row("T1-1", "hitter_hits", "NO")
        graded = audit.build_graded_row(row, settlement)
        assert graded["simulatedBetSide"] == "NO"
        assert graded["simulatedBetEntryPrice"] == pytest.approx(0.70)
        assert graded["simulatedBetWon"] is True

    def test_clv_uses_nearest_time_distance_checkpoint_never_first_daily(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        row = self._verified_row(tmp_path, model_prob=0.40, entry_price=0.30)
        row["sourceDate"] = "2026-08-13"
        settlement = _settlement_row("T1-1", "hitter_hits", "YES", checkpoints=[
            {"checkpoint": "FIRST_DAILY", "yesPrice": 0.60},
            {"checkpoint": "T_MINUS_30", "yesPrice": 0.28},
            {"checkpoint": "T_MINUS_5", "yesPrice": 0.25},
        ])
        graded = audit.build_graded_row(row, settlement)
        assert graded["closingCheckpointUsedForCLV"] == "T_MINUS_5"
        # entry 0.30 - closing 0.25 = +5 cents CLV (entered cheaper than close)
        assert graded["clvCents"] == pytest.approx(5.0)

    def test_clv_unavailable_when_only_first_daily_checkpoint_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        row = self._verified_row(tmp_path, model_prob=0.40, entry_price=0.30)
        row["sourceDate"] = "2026-08-13"
        settlement = _settlement_row("T1-1", "hitter_hits", "YES", checkpoints=[
            {"checkpoint": "FIRST_DAILY", "yesPrice": 0.60},
        ])
        graded = audit.build_graded_row(row, settlement)
        assert graded["closingCheckpointUsedForCLV"] is None
        assert graded["clvCents"] is None


# ---------------------------------------------------------------------------
# Calibration math
# ---------------------------------------------------------------------------

class TestCalibrationMath:
    def _rows(self, probs_and_outcomes):
        return [{"modelProbability": p, "propositionOutcome": o} for p, o in probs_and_outcomes]

    def test_overall_calibration_perfect_calibration_zero_error(self):
        rows = self._rows([(0.5, "YES"), (0.5, "NO")] * 30)
        result = audit.overall_calibration(rows)
        assert result["n"] == 60
        assert result["avgPredictedProbability"] == 0.5
        assert result["actualWinRate"] == 0.5
        assert result["calibrationError"] == 0.0
        assert result["status"] == "DESCRIPTIVE_ONLY"  # n=60: 20 <= n < 100

    def test_overall_calibration_empty_input(self):
        result = audit.overall_calibration([])
        assert result["n"] == 0
        assert result["status"] == "INSUFFICIENT_SAMPLE"
        assert result["brierScore"] is None

    def test_sample_size_status_tiers(self):
        assert audit.overall_calibration(self._rows([(0.5, "YES")] * 5))["status"] == "INSUFFICIENT_SAMPLE"
        assert audit.overall_calibration(self._rows([(0.5, "YES")] * 50))["status"] == "DESCRIPTIVE_ONLY"
        assert audit.overall_calibration(self._rows([(0.5, "YES")] * 150))["status"] == "CALIBRATED"

    def test_bucket_calibration_assigns_rows_to_correct_bucket(self):
        rows = self._rows([(0.10, "NO"), (0.80, "YES")])
        buckets = audit.bucket_calibration(rows)
        low_bucket = next(b for b in buckets if b["bucket"] == "<35%")
        high_bucket = next(b for b in buckets if b["bucket"] == "75%+")
        assert low_bucket["n"] == 1
        assert high_bucket["n"] == 1

    def test_overconfident_model_shows_negative_calibration_error(self):
        # Model always says 90%, but only wins half the time.
        rows = self._rows([(0.9, "YES"), (0.9, "NO")] * 30)
        result = audit.overall_calibration(rows)
        assert result["calibrationError"] < 0


class TestMonotonicity:
    def test_no_violation_for_a_valid_non_increasing_ladder(self):
        rows = [
            dict(_projected_row("SER-EVT-PLR-1", "hitter_hits", 1, model_prob=0.60), sourceDate="2026-08-13"),
            dict(_projected_row("SER-EVT-PLR-2", "hitter_hits", 2, model_prob=0.30), sourceDate="2026-08-13"),
            dict(_projected_row("SER-EVT-PLR-3", "hitter_hits", 3, model_prob=0.10), sourceDate="2026-08-13"),
        ]
        result = audit.monotonicity_check(rows)
        assert result["violationCount"] == 0
        assert result["totalLaddersChecked"] == 1

    def test_detects_a_real_violation(self):
        rows = [
            dict(_projected_row("SER-EVT-PLR-1", "hitter_hits", 1, model_prob=0.30), sourceDate="2026-08-13"),
            dict(_projected_row("SER-EVT-PLR-2", "hitter_hits", 2, model_prob=0.45), sourceDate="2026-08-13"),
        ]
        result = audit.monotonicity_check(rows)
        assert result["violationCount"] == 1
        assert result["violations"][0]["violationDelta"] == pytest.approx(0.15)

    def test_detects_flat_ladder(self):
        rows = [
            dict(_projected_row("SER-EVT-PLR-1", "hitter_hits", 1, model_prob=0.30), sourceDate="2026-08-13"),
            dict(_projected_row("SER-EVT-PLR-2", "hitter_hits", 2, model_prob=0.30), sourceDate="2026-08-13"),
        ]
        result = audit.monotonicity_check(rows)
        assert result["flatLadderCount"] == 1

    def test_different_hitters_never_grouped_into_same_ladder(self):
        rows = [
            dict(_projected_row("SER-EVT-PLRA-1", "hitter_hits", 1, model_prob=0.10, player="A"), sourceDate="2026-08-13"),
            dict(_projected_row("SER-EVT-PLRB-1", "hitter_hits", 1, model_prob=0.90, player="B"), sourceDate="2026-08-13"),
        ]
        result = audit.monotonicity_check(rows)
        assert result["totalLaddersChecked"] == 0  # each hitter has only 1 rung -- no ladder to check


# ---------------------------------------------------------------------------
# Full corpus / end-to-end orchestration
# ---------------------------------------------------------------------------

class TestFullCorpusEndToEnd:
    def _build_repo(self, tmp_path):
        snap_dir = tmp_path / "data" / "kalshi_registry_snapshots"
        snap_dir.mkdir(parents=True)
        snap_name = "kalshi_search_2026-08-13_180000_standalone.json"
        (snap_dir / snap_name).write_text("{}")
        snapshot_path = f"data/kalshi_registry_snapshots/{snap_name}"

        projected = _projected_row("SER-EVT-PLR-1", "hitter_hits", 1, model_prob=0.40, entry_price=0.30,
                                    snapshot_path=snapshot_path)
        unconfirmed = dict(_projected_row("SER-EVT-PLR-2", "hitter_hits", 1, snapshot_path=snapshot_path),
                            projectionStatus="LINEUP_UNCONFIRMED", modelProbability=None)
        pipeline_root = tmp_path / "data" / "pipeline"
        board_path = _write_board(str(pipeline_root), "2026-08-13", [projected, unconfirmed],
                                   summary_extra={"rowsByProjectionStatus": {"PROJECTED": 1, "LINEUP_UNCONFIRMED": 1}})
        settlements_root = tmp_path / "data" / "settlements"
        _write_settlement(settlements_root, "2026-08-13", [_settlement_row("SER-EVT-PLR-1", "hitter_hits", "YES")])
        return str(pipeline_root), str(settlements_root)

    def test_build_full_corpus_only_grades_projected_rows(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pipeline_root, settlements_root = self._build_repo(tmp_path)
        corpus = audit.build_full_corpus(pipeline_root, settlements_root)
        assert len(corpus["allRows"]) == 2
        assert len(corpus["projectedRows"]) == 1
        assert len(corpus["graded"]) == 1
        assert corpus["graded"][0]["propositionOutcome"] == "YES"

    def test_idempotent_rerun_produces_identical_output(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pipeline_root, settlements_root = self._build_repo(tmp_path)
        reports1 = build_reports(pipeline_root, settlements_root)
        reports2 = build_reports(pipeline_root, settlements_root)
        assert reports1["graded"] == reports2["graded"]
        assert reports1["calibration_overall"] == reports2["calibration_overall"]

    def test_write_reports_creates_every_expected_artifact(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pipeline_root, settlements_root = self._build_repo(tmp_path)
        reports = build_reports(pipeline_root, settlements_root)
        output_dir = tmp_path / "output"
        write_reports(reports, str(output_dir))
        expected = [
            "summary.json", "calibration_by_bucket.json", "calibration_by_market.json",
            "roi_by_market.json", "roi_by_edge_bucket.json", "clv_summary.json",
            "monotonicity_violations.json", "unresolved_records.json",
            "provenance_audit.json", "graded_projections.jsonl",
        ]
        for name in expected:
            assert (output_dir / name).exists(), f"missing {name}"

    def test_write_reports_is_atomic_and_rerunnable(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        pipeline_root, settlements_root = self._build_repo(tmp_path)
        reports = build_reports(pipeline_root, settlements_root)
        output_dir = tmp_path / "output"
        write_reports(reports, str(output_dir))
        write_reports(reports, str(output_dir))  # rerun must not raise or corrupt
        with open(output_dir / "summary.json") as fh:
            summary = json.load(fh)
        assert summary["totalProjectedRowCount"] == 1


class TestProvenanceAudit:
    def test_reports_non_projected_reason_counts(self):
        all_rows = [
            {"projectionStatus": "PROJECTED", "marketFamily": "hitter_hits", "sourceDate": "2026-08-13"},
            {"projectionStatus": "GAME_STARTED", "marketFamily": "hitter_hits", "sourceDate": "2026-08-13"},
            {"projectionStatus": "LINEUP_UNCONFIRMED", "marketFamily": "hitter_rbis", "sourceDate": "2026-08-13"},
        ]
        graded = [{"provenanceConfidence": "PROSPECTIVE_VERIFIED", "provenanceReason": None,
                    "propositionOutcome": "YES", "unresolvedReason": None,
                    "sourceCapturePath": None, "marketObservedAt": None, "sourceDate": "2026-08-13"}]
        report = audit.provenance_audit(all_rows, graded, {"2026-08-13": {"rowsByProjectionStatus": {"PROJECTED": 1}}})
        assert report["rowsByProjectionStatus"]["GAME_STARTED"] == 1
        assert report["totalRowsAllStatuses"] == 3
        assert report["datesWithZeroProjectedRows"] == []
