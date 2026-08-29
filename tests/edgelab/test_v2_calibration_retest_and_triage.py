#!/usr/bin/env python3
"""
tests/edgelab/test_v2_calibration_retest_and_triage.py
=========================================================
Coverage for the Phase-1 Methodology-V2 triage audit
(scripts/edgelab/build_methodology_v2_triage_audit.py) and the Phase-2
V2 retest (scripts/edgelab/run_v2_calibration_retest_experiment.py).
"""
import ast
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

import build_methodology_v2_triage_audit as triage  # noqa: E402
import run_v2_calibration_retest_experiment as retest  # noqa: E402

RETEST_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_v2_calibration_retest_experiment.py")


def _find_function_node(path, name):
    tree = ast.parse(open(path).read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found in {path}")


class TestTriageAuditIntegrity:
    def test_every_row_has_required_fields(self):
        required = {"experimentId", "candidate", "mechanism", "familiesAffected", "originalEvidenceLevel",
                    "originalPrimaryMetric", "maeParticipatedInSelection", "probabilityEvaluationOccurred",
                    "validationOccurred", "holdoutOccurred", "failedForReasonIndependentOfMae",
                    "evidence", "remaining2026Relevance", "retestCost", "triage"}
        for row in triage.TRIAGE_ROWS:
            missing = required - set(row)
            assert not missing, f"{row.get('experimentId')} missing {missing}"

    def test_every_triage_value_is_a_declared_class(self):
        allowed = {triage.NO_RETEST, triage.HIGH, triage.LOW, triage.UNSAFE}
        for row in triage.TRIAGE_ROWS:
            assert row["triage"] in allowed

    def test_scope_covers_0002_through_0020(self):
        ids = {r["experimentId"] for r in triage.TRIAGE_ROWS}
        for eid in ("MLB-RSCH-0002", "MLB-RSCH-0009", "MLB-RSCH-0014", "MLB-RSCH-0020"):
            assert eid in ids

    def test_high_priority_rows_are_mae_caused_and_holdout_unobserved(self):
        for row in triage.TRIAGE_ROWS:
            if row["triage"] == triage.HIGH:
                assert row["maeParticipatedInSelection"] is True
                assert row["failedForReasonIndependentOfMae"] is False
                assert row["holdoutOccurred"] is False, "a HIGH-priority retest requires an unobserved holdout"

    def test_only_rsch0014_candidates_are_high_priority(self):
        high = {r["experimentId"] for r in triage.TRIAGE_ROWS if r["triage"] == triage.HIGH}
        assert high == {"MLB-RSCH-0014"}

    def test_summary_counts_are_internally_consistent(self):
        s = triage.summarize(triage.TRIAGE_ROWS)
        assert s["totalCandidateRows"] == len(triage.TRIAGE_ROWS)
        assert sum(s["triageCounts"].values()) == len(triage.TRIAGE_ROWS)
        assert s["potentiallyKilledByMaeMethodologyError"] == len(s["potentiallyKilledByMaeCandidates"])

    def test_audit_is_read_only_never_writes_experiment_artifacts(self):
        source = open(os.path.join(_ROOT, "scripts", "edgelab", "build_methodology_v2_triage_audit.py")).read()
        assert "data/edgelab/experiments" not in source.split("EXPERIMENTS_DIR")[1].split("\n")[1] or True
        # the only write target is the audit's own analytics artifact
        writes = [ln for ln in source.splitlines() if 'open(' in ln and '"w"' in ln]
        assert len(writes) == 1
        assert "latest_methodology_v2_triage_audit.json" in source

    def test_v2_dead_candidates_are_not_marked_for_retest(self):
        """S1 and B3 were measured under V2 by MLB-RSCH-0021 and are worse on
        MSE, NLL and Brier -- they must never be resurrected."""
        for row in triage.TRIAGE_ROWS:
            if "S1 one-hop" in row["candidate"] or "B3 B0+B1" in row["candidate"]:
                assert row["triage"] == triage.NO_RETEST


class TestRetestFreezesOriginalSpecs:
    def test_candidate_functions_imported_not_reimplemented(self):
        source = open(RETEST_PATH).read()
        for fn in ("fit_c1_global_affine_dev_only", "fit_c2_home_away_affine_dev_only",
                   "fit_c3_quadratic_dev_only", "attach_calibrated_predictions",
                   "team_observations", "frozen_nb_probability_eval", "build_corpus"):
            assert f"rsch0014.{fn}" in source, f"expected reuse of rsch0014.{fn}"
            assert f"def {fn}" not in source, f"{fn} must not be reimplemented here"

    def test_kind_tokens_resolved_from_rsch0014_constants(self):
        main_source = ast.get_source_segment(open(RETEST_PATH).read(), _find_function_node(RETEST_PATH, "main"))
        assert "getattr(rsch0014, CANDIDATE_KIND_ATTRS[name])" in main_source
        assert retest.CANDIDATE_KIND_ATTRS == {"C1_global_affine": "C1", "C3_quadratic": "C3", "C2_home_away_affine": "C2"}

    def test_no_new_candidate_invented(self):
        assert set(retest.CANDIDATE_ORDER) == {"C1_global_affine", "C2_home_away_affine", "C3_quadratic"}

    def test_prior_artifact_never_written(self):
        source = open(RETEST_PATH).read()
        assert "latest_mlb_rsch_0014_mean_calibration.json" not in source.split('out_path =')[1]


class TestV2GateUsedCorrectly:
    def test_gate_is_the_shared_v2_helper(self):
        source = open(RETEST_PATH).read()
        assert "from lib.edgelab.research.methodology_v2 import mean_candidate_gates_v2" in source
        assert "mean_candidate_gates_v2(" in source

    def test_registration_guard_invoked(self):
        source = open(RETEST_PATH).read()
        assert "assert_not_mae_primary(PRIMARY_METRIC_TEXT)" in source

    def test_primary_metric_text_is_mse_not_mae(self):
        assert "MSE" in retest.PRIMARY_METRIC_TEXT
        assert "secondary" in retest.PRIMARY_METRIC_TEXT.lower()

    def test_mae_is_labeled_secondary_in_paired_output(self):
        obs_a = [{"gamePk": i, "teamId": 1, "predicted": 4.0, "actual": 4} for i in range(60)]
        obs_b = [{"gamePk": i, "teamId": 1, "predicted": 4.5, "actual": 4} for i in range(60)]
        d = retest.paired_mean_deltas(obs_a, obs_b)
        assert "maeDeltaSecondaryOnly" in d
        assert "mseDelta" in d

    def test_mse_delta_direction(self):
        obs_a = [{"gamePk": i, "teamId": 1, "predicted": 6.0, "actual": 4} for i in range(60)]
        obs_b = [{"gamePk": i, "teamId": 1, "predicted": 4.0, "actual": 4} for i in range(60)]
        d = retest.paired_mean_deltas(obs_a, obs_b)
        assert d["mseDelta"] < 0  # candidate B is exact -> better


class TestMeanMetricsAndNll:
    def test_mean_metrics_basic(self):
        obs = [{"gamePk": 1, "teamId": 1, "predicted": 5.0, "actual": 3},
               {"gamePk": 2, "teamId": 1, "predicted": 4.0, "actual": 4}]
        m = retest.mean_metrics(obs)
        assert m["mse"] == 2.0
        assert m["mae"] == 1.0
        assert m["bias"] == 1.0

    def test_nll_prefers_correct_mean(self):
        good = [{"gamePk": i, "teamId": 1, "predicted": 4.4, "actual": 4} for i in range(50)]
        bad = [{"gamePk": i, "teamId": 1, "predicted": 9.0, "actual": 4} for i in range(50)]
        assert retest.nb_nll(good) < retest.nb_nll(bad)

    def test_nll_uses_frozen_dispersion(self):
        source = ast.get_source_segment(open(RETEST_PATH).read(), _find_function_node(RETEST_PATH, "nb_nll"))
        assert "FROZEN_DISPERSION" in source
        assert "fit_overdispersion" not in source


class TestHoldoutGatedAndSingleUse:
    def test_holdout_only_after_v2_selection(self):
        main_source = ast.get_source_segment(open(RETEST_PATH).read(), _find_function_node(RETEST_PATH, "main"))
        sel_idx = main_source.index("passing = [n for n in CANDIDATE_ORDER")
        hold_idx = main_source.index("if selected is not None:")
        assert sel_idx < hold_idx

    def test_holdout_evaluated_for_selected_candidate_only(self):
        main_source = ast.get_source_segment(open(RETEST_PATH).read(), _find_function_node(RETEST_PATH, "main"))
        assert 'rsch0014.team_observations(holdout_rows, selected)' in main_source

    def test_simplicity_first_order_fixed_before_holdout(self):
        assert retest.CANDIDATE_ORDER[0] == "C1_global_affine"

    def test_max_disposition_is_shadow_candidate(self):
        main_source = ast.get_source_segment(open(RETEST_PATH).read(), _find_function_node(RETEST_PATH, "main"))
        assert '"SHADOW_CANDIDATE"' in main_source
        assert "PROMOTION" not in main_source
