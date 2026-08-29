#!/usr/bin/env python3
"""
tests/edgelab/test_frozen_forward_scorer.py
=========================================================
Immutability, determinism and isolation tests for the frozen FORWARD
confirmation engine (lib/edgelab/research/frozen_forward_scorer.py and
scripts/edgelab/run_frozen_forward_scorer.py).

The load-bearing guarantees: nothing is ever refit, frozen artifacts are
never mutated, no new segment can be invented after forward data arrive,
every scored row settles strictly after the cutoff, and rerunning on
identical data is byte-stable.
"""
import ast
import hashlib
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

from lib.edgelab.research import frozen_forward_scorer as ffs
import run_frozen_forward_scorer as runner  # noqa: E402

LIB_PATH = os.path.join(_ROOT, "lib", "edgelab", "research", "frozen_forward_scorer.py")
SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_frozen_forward_scorer.py")


def _row(ticker="T1", game="G1", family="game_total", settle="2026-09-02",
         model_p=0.60, market_fair=0.50, outcome=1, quality="full", lineup="CONFIRMED"):
    return {"marketTicker": ticker, "gameId": game, "family": family, "settleDate": settle,
            "outcome": outcome, "modelP": model_p, "marketFair": market_fair,
            "executableAsk": market_fair + 0.005, "yesBid": (market_fair - 0.005) * 100,
            "dataQuality": quality, "lineupConfirmationState": lineup}


class TestNoRefittingIsPossible:
    def test_library_exposes_no_fitting_function(self):
        tree = ast.parse(open(LIB_PATH).read())
        names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
        for n in names:
            assert not n.startswith("fit_"), f"{n} looks like a fitting function"
        source = open(LIB_PATH).read()
        for forbidden in ("minimize", "golden", "newton", "gradient", "sklearn", "scipy.optimize"):
            assert forbidden not in source.lower()

    def test_runner_never_imports_a_fitting_experiment(self):
        source = open(SCRIPT_PATH).read()
        for forbidden in ("fit_alpha", "fit_beta", "fit_logit_affine", "fit_c1_", "fit_c2_", "fit_c3_"):
            assert forbidden not in source

    def test_alpha_is_read_from_artifact_not_computed(self):
        source = open(SCRIPT_PATH).read()
        assert 'alpha = frozen_0024["alpha"]' in source
        assert '"alphaRefit": False' in source

    def test_beta_is_read_from_artifact_not_computed(self):
        source = open(SCRIPT_PATH).read()
        assert 'beta, base = frozen_0026["beta"], frozen_0026["base"]' in source
        assert '"betaRefit": False' in source


class TestFrozenArtifactsNeverMutated:
    def test_loader_opens_read_only(self):
        import inspect
        src = inspect.getsource(ffs.load_frozen_artifact)
        assert '"w"' not in src and "'w'" not in src

    def test_runner_never_writes_to_a_frozen_artifact_path(self):
        source = open(SCRIPT_PATH).read()
        write_targets = [ln for ln in source.splitlines() if 'open(' in ln and '"w"' in ln]
        for ln in write_targets:
            assert "FROZEN_0024" not in ln and "FROZEN_0026" not in ln

    def test_frozen_artifact_hash_unchanged_by_a_scoring_run(self):
        paths = [runner.FROZEN_0024, runner.FROZEN_0026]
        before = {p: hashlib.sha256(open(p, "rb").read()).hexdigest() for p in paths if os.path.exists(p)}
        runner.main()
        after = {p: hashlib.sha256(open(p, "rb").read()).hexdigest() for p in paths if os.path.exists(p)}
        assert before == after, "a scoring run must never modify a frozen artifact"

    def test_training_end_dates_unchanged(self):
        for path, expected in ((runner.FROZEN_0024, "2026-08-24"), (runner.FROZEN_0026, "2026-08-24")):
            if os.path.exists(path):
                assert json.load(open(path))["trainingEndDate"] == expected


class TestFixedSegmentsCannotDrift:
    def test_price_bands_are_the_frozen_quintiles(self):
        assert ffs.PRICE_BANDS == ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0))

    def test_disagreement_bands_unchanged(self):
        assert ffs.DISAGREEMENT_BANDS == ((0.00, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 1.01))

    def test_quality_categories_unchanged(self):
        assert runner._input_quality_key(_row(quality="full", lineup=None)) == "HIGH_QUALITY_INPUT"
        assert runner._input_quality_key(_row(quality=None, lineup="CONFIRMED")) == "HIGH_QUALITY_INPUT"
        assert runner._input_quality_key(_row(quality=None, lineup=None)) == "LOWER_OR_UNKNOWN_INPUT"

    def test_direction_categories_unchanged(self):
        assert runner._direction_key(_row(model_p=0.7, market_fair=0.5)) == "MODEL_ABOVE_MARKET"
        assert runner._direction_key(_row(model_p=0.3, market_fair=0.5)) == "MODEL_BELOW_MARKET"

    def test_price_band_assignment_is_exact(self):
        assert runner._price_band_key(_row(market_fair=0.05)) == "0.0_0.2"
        assert runner._price_band_key(_row(market_fair=0.65)) == "0.6_0.8"


class TestForwardWindowPurity:
    def test_cutoff_constant(self):
        assert ffs.FORWARD_START_DATE == "2026-08-28"

    def test_settlement_loader_skips_dates_at_or_before_cutoff(self):
        import inspect
        src = inspect.getsource(runner.load_forward_settled_outcomes)
        assert "if settle_date <= ffs.FORWARD_START_DATE:" in src
        assert "continue" in src

    def test_every_built_row_settles_after_the_cutoff(self):
        rows, _ = runner.build_forward_rows()
        for r in rows:
            assert r["settleDate"] > ffs.FORWARD_START_DATE

    def test_only_valid_pregame_observations_used(self):
        import inspect
        src = inspect.getsource(runner.load_pregame_fair_prices)
        assert "isValidPregameObservation" in src
        assert "gameStartedAtCapture" in src

    def test_no_post_settlement_field_enters_scoring(self):
        """Scoring inputs are modelP / marketFair / executableAsk / yesBid --
        all pregame. `outcome` is the target, never a predictor."""
        import inspect
        for fn in (ffs.apply_frozen_residual, ffs.apply_frozen_shrink):
            src = inspect.getsource(fn)
            assert "outcome" not in src


class TestCheckpointsAndStatus:
    def test_checkpoint_thresholds(self):
        assert ffs.classify_checkpoint(0, 0)["checkpoint"] == "CHECKPOINT_0"
        assert ffs.classify_checkpoint(249, 30)["checkpoint"] == "CHECKPOINT_0"
        assert ffs.classify_checkpoint(250, 20)["checkpoint"] == "CHECKPOINT_1"
        assert ffs.classify_checkpoint(500, 40)["checkpoint"] == "CHECKPOINT_2"
        assert ffs.classify_checkpoint(1000, 60)["checkpoint"] == "CHECKPOINT_3"
        assert ffs.classify_checkpoint(2000, 100)["checkpoint"] == "CHECKPOINT_4"

    def test_rows_without_games_stay_at_checkpoint_0(self):
        assert ffs.classify_checkpoint(5000, 5)["checkpoint"] == "CHECKPOINT_0"

    def test_production_approved_is_not_in_the_vocabulary(self):
        source = open(LIB_PATH).read()
        assignments = [ln for ln in source.splitlines() if "=" in ln and "PRODUCTION_APPROVED" in ln and not ln.strip().startswith("#")]
        assert assignments == []

    def test_below_checkpoint_1_is_always_insufficient(self):
        status, _ = ffs.decide_status("CHECKPOINT_0", {"brierDelta": -0.01, "logLossDelta": -0.01},
                                      {"majorityFavourable": True, "datesFavourable": 5, "datesTotal": 5},
                                      {"groups": {}})
        assert status == ffs.INSUFFICIENT

    def test_confirmation_requires_checkpoint_3(self):
        paired = {"brierDelta": -0.01, "logLossDelta": -0.01}
        direction = {"majorityFavourable": True, "datesFavourable": 5, "datesTotal": 5}
        fams = {"groups": {"a": {"status": "SCORED", "brierDelta": -0.01},
                           "b": {"status": "SCORED", "brierDelta": -0.005}}}
        assert ffs.decide_status("CHECKPOINT_2", paired, direction, fams)[0] == ffs.INTERMEDIATE_UNCONFIRMED
        assert ffs.decide_status("CHECKPOINT_3", paired, direction, fams)[0] == ffs.SUPPORTS

    def test_worse_on_both_metrics_contradicts(self):
        status, _ = ffs.decide_status("CHECKPOINT_3", {"brierDelta": 0.01, "logLossDelta": 0.02},
                                      {"majorityFavourable": False, "datesFavourable": 0, "datesTotal": 4},
                                      {"groups": {}})
        assert status == ffs.CONTRADICTS

    def test_single_family_concentration_is_mixed_not_supports(self):
        fams = {"groups": {"a": {"status": "SCORED", "brierDelta": -0.02},
                           "b": {"status": "SCORED", "brierDelta": 0.01},
                           "c": {"status": "SCORED", "brierDelta": 0.005}}}
        status, _ = ffs.decide_status("CHECKPOINT_3", {"brierDelta": -0.01, "logLossDelta": -0.01},
                                      {"majorityFavourable": True, "datesFavourable": 3, "datesTotal": 4}, fams)
        assert status == ffs.MIXED

    def test_minority_date_support_does_not_confirm(self):
        fams = {"groups": {"a": {"status": "SCORED", "brierDelta": -0.01},
                           "b": {"status": "SCORED", "brierDelta": -0.01}}}
        status, _ = ffs.decide_status("CHECKPOINT_3", {"brierDelta": -0.01, "logLossDelta": -0.01},
                                      {"majorityFavourable": False, "datesFavourable": 1, "datesTotal": 4}, fams)
        assert status == ffs.INTERMEDIATE_UNCONFIRMED


class TestFrozenFormsAreExact:
    def test_alpha_zero_returns_the_market(self):
        assert abs(ffs.apply_frozen_residual(0.9, 0.5, 0.0) - 0.5) < 1e-9

    def test_alpha_one_returns_the_model(self):
        assert abs(ffs.apply_frozen_residual(0.9, 0.5, 1.0) - 0.9) < 1e-9

    def test_beta_one_returns_the_market(self):
        assert abs(ffs.apply_frozen_shrink(0.75, 1.0, 0.43) - 0.75) < 1e-9

    def test_beta_below_one_shrinks_toward_base(self):
        assert ffs.apply_frozen_shrink(0.90, 0.5, 0.43) < 0.90
        assert ffs.apply_frozen_shrink(0.10, 0.5, 0.43) > 0.10


class TestScoringMath:
    def test_paired_delta_direction(self):
        rows = [_row(ticker=f"T{i}", game=f"G{i}", model_p=0.9, market_fair=0.6, outcome=1) for i in range(30)]
        d = ffs.paired_delta(rows, lambda r: r["modelP"], lambda r: r["marketFair"])
        assert d["brierDelta"] < 0 and d["logLossDelta"] < 0

    def test_segment_minimum_sample_enforced(self):
        rows = [_row(ticker=f"T{i}", game=f"G{i}") for i in range(5)]
        seg = ffs.segment_scores(rows, lambda r: r["modelP"], lambda r: r["marketFair"], lambda r: r["family"], "family")
        assert seg["groups"]["game_total"]["status"] == "BELOW_MINIMUM_SAMPLE"

    def test_per_date_direction_counts_correctly(self):
        rows = ([_row(ticker=f"A{i}", game=f"GA{i}", settle="2026-09-01", model_p=0.9, outcome=1) for i in range(10)]
                + [_row(ticker=f"B{i}", game=f"GB{i}", settle="2026-09-02", model_p=0.1, outcome=1) for i in range(10)])
        d = ffs.per_date_direction(rows, lambda r: r["modelP"], lambda r: r["marketFair"])
        assert d["datesTotal"] == 2 and d["datesFavourable"] == 1
        assert d["majorityFavourable"] is False

    def test_benjamini_hochberg_step_up(self):
        assert ffs.benjamini_hochberg({"a": 0.01, "b": 0.04, "c": 0.20}, alpha=0.10) == {"a": True, "b": True, "c": False}


class TestDeterminismAndIsolation:
    def test_rerun_on_identical_data_is_byte_stable(self):
        runner.main()
        first = open(runner.OUT_JSON, "rb").read()
        runner.main()
        second = open(runner.OUT_JSON, "rb").read()
        assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()

    def test_scorer_reports_governance_flags(self):
        report = runner.main()
        g = report["governance"]
        assert g["refitPerformed"] is False
        assert g["frozenArtifactsMutated"] is False
        assert g["productionChanged"] is False
        assert g["statusVocabularyExcludesProductionApproved"] is True

    def test_empty_forward_window_reports_health_only(self):
        report = runner.main()
        if report["coverage"]["rows"] == 0:
            assert report["status"] == ffs.INSUFFICIENT
            assert report["healthOnly"] is True

    def test_never_imported_by_production_code(self):
        import subprocess
        result = subprocess.run(
            ["grep", "-rl", "frozen_forward_scorer", _ROOT, "--include=*.py",
             "--exclude-dir=.git", "--exclude-dir=__pycache__"],
            capture_output=True, text=True, timeout=30)
        allowed = (os.path.join("lib", "edgelab", "research"), os.path.join("tests", "edgelab"),
                   os.path.join("scripts", "edgelab"))
        offenders = [ln for ln in result.stdout.splitlines() if ln.strip() and not any(a in ln for a in allowed)]
        assert offenders == [], f"forward scorer referenced outside research scope: {offenders}"

    def test_runner_exits_zero_even_on_internal_error(self):
        source = open(SCRIPT_PATH).read()
        assert "sys.exit(0)" in source
        assert "except Exception as exc:" in source
