#!/usr/bin/env python3
"""
tests/research/test_inning_result_report.py
================================================
Model Performance Phase 2A Part 15 -- tests for
lib/research/inning_result_report.py.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.research.inning_result_report import (
    format_f5_result_report,
    format_unresolved_horizon_report,
    LEGACY_WARNING,
)

AWAY = {"canonicalModelProb": 0.421, "yesAsk": 0.39, "executableYesEdge": 0.031, "legacyConditionalProb": 0.522}
TIE = {"canonicalModelProb": 0.194, "yesAsk": 0.17, "executableYesEdge": 0.024, "legacyConditionalProb": None}
HOME = {"canonicalModelProb": 0.385, "yesAsk": 0.42, "executableYesEdge": -0.035, "legacyConditionalProb": 0.478}


class TestF5ResultReport:

    def test_contains_all_three_model_probs(self):
        report = format_f5_result_report(AWAY, TIE, HOME)
        assert "Away model: 42.1%" in report
        assert "Tie model: 19.4%" in report
        assert "Home model: 38.5%" in report

    def test_contains_legacy_warning_verbatim(self):
        report = format_f5_result_report(AWAY, TIE, HOME)
        assert LEGACY_WARNING in report

    def test_legacy_section_only_has_away_home_no_tie(self):
        report = format_f5_result_report(AWAY, TIE, HOME)
        legacy_section = report.split("Legacy F5 no-tie conditional:")[1]
        assert "Away: 52.2%" in legacy_section
        assert "Home: 47.8%" in legacy_section

    def test_missing_row_renders_as_na_not_omitted(self):
        report = format_f5_result_report(None, TIE, HOME)
        assert "Away model: n/a" in report

    def test_deterministic(self):
        r1 = format_f5_result_report(AWAY, TIE, HOME)
        r2 = format_f5_result_report(AWAY, TIE, HOME)
        assert r1 == r2


class TestUnresolvedHorizonReport:

    def test_f3_report_never_shows_a_probability(self):
        report = format_unresolved_horizon_report("F3")
        assert "%" not in report
        assert "¢" not in report

    def test_f3_report_states_existence_confirmed_structure_unresolved(self):
        report = format_unresolved_horizon_report("F3")
        assert "Market existence: confirmed" in report
        assert "Contract structure: unresolved" in report
        assert "Model binding: disabled" in report

    def test_f7_report_uses_f7_label(self):
        report = format_unresolved_horizon_report("F7")
        assert report.startswith("F7 result:")
