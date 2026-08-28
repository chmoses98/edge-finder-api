#!/usr/bin/env python3
"""
tests/edgelab/test_run_production_calibration_audit_experiment_script.py
=========================================================
Coverage for scripts/edgelab/run_production_calibration_audit_experiment.py --
MLB-RSCH-0022's walk-forward production-probability calibration audit.
"""
import ast
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

import run_production_calibration_audit_experiment as exp  # noqa: E402

SCRIPT_PATH = os.path.join(_ROOT, "scripts", "edgelab", "run_production_calibration_audit_experiment.py")


def _find_function_node(name):
    tree = ast.parse(open(SCRIPT_PATH).read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found")


def _row(ticker="T1", game="G1", model_p=0.6, market_p=0.5, outcome=1, family="game_result", settle="2026-08-10", created="2026-08-10T10:00:00Z"):
    return {"marketTicker": ticker, "gameId": game, "modelP": model_p, "marketP": market_p,
            "outcome": outcome, "family": family, "settleDate": settle, "createdAt": created,
            "confidence": "HIGH", "dataQuality": "OK", "lineupConfirmationState": "CONFIRMED"}


class TestRegistrationIdempotent:
    def test_register_experiment_is_idempotent_across_reruns(self, tmp_path, monkeypatch):
        import lib.edgelab.experiment_registry as reg
        import lib.edgelab.control_identity as ctrl_id
        monkeypatch.setattr(reg, "EXPERIMENTS_ROOT", str(tmp_path / "experiments"))
        monkeypatch.setattr(ctrl_id, "CONTROL_MODELS_ROOT", str(tmp_path / "control_models"))
        c1, d1 = exp.register_experiment()
        c2, d2 = exp.register_experiment()
        assert d1 == d2 and c1 == c2

    def test_evidence_level_is_e4_per_pit_framework(self, tmp_path, monkeypatch):
        import lib.edgelab.experiment_registry as reg
        import lib.edgelab.control_identity as ctrl_id
        monkeypatch.setattr(reg, "EXPERIMENTS_ROOT", str(tmp_path / "experiments"))
        monkeypatch.setattr(ctrl_id, "CONTROL_MODELS_ROOT", str(tmp_path / "control_models"))
        _, definition = exp.register_experiment()
        assert definition["evidenceLevel"] == "E4_PROSPECTIVE_SHADOW"


class TestNothingIsFit:
    def test_no_fitting_functions_anywhere(self):
        source = open(SCRIPT_PATH).read()
        for forbidden in ("def fit_", "minimize", "gradient", "sklearn", "scipy.optimize"):
            assert forbidden not in source

    def test_disagreement_bands_are_fixed_constants(self):
        assert exp.DISAGREEMENT_BANDS == ((0.00, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 1.01))

    def test_economics_never_selects_or_filters_by_roi(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("disagreement_band_economics"))
        assert "max(" not in source.replace("max(price", "").replace("max_", "")  # no argmax over bands
        assert "sort" not in source.lower()


class TestLastPerTickerPrimaryRow:
    def test_last_evaluation_wins(self):
        evaluated = [
            {"marketTicker": "T1", "marketFamily": "f", "gameId": "G1", "createdAt": "2026-08-10T09:00:00Z", "modelP": 0.4, "marketP": 0.5, "confidence": None, "dataQuality": None, "lineupConfirmationState": None},
            {"marketTicker": "T1", "marketFamily": "f", "gameId": "G1", "createdAt": "2026-08-10T12:00:00Z", "modelP": 0.7, "marketP": 0.5, "confidence": None, "dataQuality": None, "lineupConfirmationState": None},
        ]
        outcomes = {"T1": {"outcome": 1, "settleDate": "2026-08-10", "gameId": "G1", "marketFamily": "fam"}}
        rows = exp.build_audit_rows(evaluated, outcomes, pick="last")
        assert len(rows) == 1 and rows[0]["modelP"] == 0.7

    def test_first_pick_selects_earliest(self):
        evaluated = [
            {"marketTicker": "T1", "marketFamily": "f", "gameId": "G1", "createdAt": "2026-08-10T09:00:00Z", "modelP": 0.4, "marketP": 0.5, "confidence": None, "dataQuality": None, "lineupConfirmationState": None},
            {"marketTicker": "T1", "marketFamily": "f", "gameId": "G1", "createdAt": "2026-08-10T12:00:00Z", "modelP": 0.7, "marketP": 0.5, "confidence": None, "dataQuality": None, "lineupConfirmationState": None},
        ]
        outcomes = {"T1": {"outcome": 0, "settleDate": "2026-08-10", "gameId": "G1", "marketFamily": "fam"}}
        rows = exp.build_audit_rows(evaluated, outcomes, pick="first")
        assert rows[0]["modelP"] == 0.4

    def test_unsettled_tickers_excluded_never_fabricated(self):
        evaluated = [{"marketTicker": "T9", "marketFamily": "f", "gameId": "G1", "createdAt": "x", "modelP": 0.5, "marketP": 0.5, "confidence": None, "dataQuality": None, "lineupConfirmationState": None}]
        rows = exp.build_audit_rows(evaluated, {}, pick="last")
        assert rows == []

    def test_settlement_family_label_preferred(self):
        evaluated = [{"marketTicker": "T1", "marketFamily": "KXMLBTEAMTOTAL", "gameId": "G1", "createdAt": "x", "modelP": 0.5, "marketP": 0.5, "confidence": None, "dataQuality": None, "lineupConfirmationState": None}]
        outcomes = {"T1": {"outcome": 1, "settleDate": "2026-08-10", "gameId": "G1", "marketFamily": "team_total"}}
        rows = exp.build_audit_rows(evaluated, outcomes, pick="last")
        assert rows[0]["family"] == "team_total"


class TestSplit:
    def test_dev_val_split_boundaries(self):
        rows = [_row(ticker="A", settle="2026-08-17"), _row(ticker="B", settle="2026-08-18"), _row(ticker="C", settle="2026-08-28")]
        dev, val = exp.split_rows(rows)
        assert [r["marketTicker"] for r in dev] == ["A"]
        assert sorted(r["marketTicker"] for r in val) == ["B", "C"]


class TestFamilyMetrics:
    def test_paired_delta_negative_when_model_sharper_and_right(self):
        rows = [_row(ticker=f"T{i}", game=f"G{i}", model_p=0.9, market_p=0.6, outcome=1) for i in range(40)]
        m = exp.family_metrics(rows)
        assert m["pairedBrierDelta"] < 0

    def test_paired_delta_positive_when_model_overconfident_and_wrong(self):
        rows = [_row(ticker=f"T{i}", game=f"G{i}", model_p=0.9, market_p=0.6, outcome=0) for i in range(40)]
        m = exp.family_metrics(rows)
        assert m["pairedBrierDelta"] > 0

    def test_pvalue_deterministic(self):
        rows = [_row(ticker=f"T{i}", game=f"G{i % 10}", model_p=0.7, market_p=0.5, outcome=i % 2) for i in range(60)]
        m1 = exp.family_metrics(rows)
        m2 = exp.family_metrics(rows)
        assert m1["pTwoSided"] == m2["pTwoSided"]


class TestBenjaminiHochberg:
    def test_all_tiny_pvalues_significant(self):
        result = exp.benjamini_hochberg({"a": 0.001, "b": 0.002, "c": 0.003}, alpha=0.10)
        assert all(result.values())

    def test_all_large_pvalues_not_significant(self):
        result = exp.benjamini_hochberg({"a": 0.5, "b": 0.8, "c": 0.9}, alpha=0.10)
        assert not any(result.values())

    def test_step_up_property(self):
        # p = .01,.04,.20 at alpha=.10, m=3: thresholds .0333/.0667/.10 -> first two pass
        result = exp.benjamini_hochberg({"a": 0.01, "b": 0.04, "c": 0.20}, alpha=0.10)
        assert result["a"] and result["b"] and not result["c"]

    def test_none_pvalues_ignored(self):
        result = exp.benjamini_hochberg({"a": 0.001, "b": None})
        assert result == {"a": True}


class TestReplicationVerdicts:
    def _split_result(self, fam_delta):
        return {"families": {f: {"pairedBrierDelta": d} for f, d in fam_delta.items()}}

    def test_same_sign_replicates(self):
        v = exp.replication_verdicts(self._split_result({"f": -0.01}), self._split_result({"f": -0.005}))
        assert v["f"] == "REPLICATES_MODEL_BETTER"

    def test_opposite_sign_does_not_replicate(self):
        v = exp.replication_verdicts(self._split_result({"f": -0.01}), self._split_result({"f": 0.005}))
        assert v["f"] == "DOES_NOT_REPLICATE"

    def test_market_better_direction(self):
        v = exp.replication_verdicts(self._split_result({"f": 0.01}), self._split_result({"f": 0.02}))
        assert v["f"] == "REPLICATES_MARKET_BETTER"

    def test_missing_half_flagged(self):
        v = exp.replication_verdicts(self._split_result({"f": -0.01}), {"families": {}})
        assert v["f"] == "INSUFFICIENT_ONE_HALF"


class TestEconomicsDescriptiveOnly:
    def test_every_band_always_reported(self):
        rows = [_row(ticker=f"T{i}", game=f"G{i}", model_p=0.6, market_p=0.5) for i in range(5)]
        econ = exp.disagreement_band_economics(rows)
        assert set(econ.keys()) == {"0.00_0.05", "0.05_0.10", "0.10_0.20", "0.20_1.01"}

    def test_uses_frozen_taker_fee(self):
        source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("disagreement_band_economics"))
        assert "taker_fee(" in source

    def test_win_accounting_correct(self):
        # model says YES (0.7 > 0.5), outcome YES -> win pays 1-price
        rows = [_row(ticker="T1", game="G1", model_p=0.7, market_p=0.5, outcome=1)]
        econ = exp.disagreement_band_economics(rows)
        band = econ["0.10_0.20"]
        assert band["n"] == 1 and band["winRate"] == 1.0
        assert band["grossEvPerContract"] == 0.5


class TestForwardHoldoutNotComputed:
    def test_no_row_after_val_date_max_enters_any_split(self):
        rows = [_row(ticker="A", settle="2026-09-05")]
        dev, val = exp.split_rows(rows)
        assert dev == [] and val == []

    def test_main_never_references_dates_beyond_val_max(self):
        main_source = ast.get_source_segment(open(SCRIPT_PATH).read(), _find_function_node("main"))
        assert "2026-09" not in main_source


class TestNoNetworkCalls:
    def test_script_never_imports_urllib_or_requests(self):
        source = open(SCRIPT_PATH).read()
        assert "urlopen" not in source
        assert "requests." not in source
        assert "http" not in source.lower().replace("hochberg", "")
