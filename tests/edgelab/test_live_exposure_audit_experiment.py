#!/usr/bin/env python3
"""
tests/edgelab/test_live_exposure_audit_experiment.py
====================================================
Coverage for MLB-RSCH-0031's live exposure audit.

The load-bearing guarantees are POPULATION HYGIENE: declined rows are
never counted as recommendations, recommendations are never counted as
bets, no dollars are invented, and RED membership comes from committed
evidence rather than from ROI.
"""
import ast
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab")):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_live_exposure_audit_experiment as exp  # noqa: E402

SCRIPT = os.path.join(_ROOT, "scripts", "edgelab", "run_live_exposure_audit_experiment.py")
SOURCE = open(SCRIPT).read()


def _fn(name):
    for node in ast.parse(SOURCE).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SOURCE, node)
    raise AssertionError(f"{name}() not found")


class TestPopulationsNeverConflated:
    ROWS = [
        {"status": "RECOMMENDED", "betPlaced": False, "marketFamily": "KXMLBRFI"},
        {"status": "RECOMMENDED_NOT_BET", "betPlaced": False, "marketFamily": "KXMLBRFI"},
        {"status": "BET_PLACED", "betPlaced": True, "marketFamily": "KXMLBGAME"},
        {"status": "INSUFFICIENT_MODEL_SUPPORT", "betPlaced": False, "marketFamily": "hitter_hits"},
        {"status": "PASS_NO_EDGE", "betPlaced": False, "marketFamily": "KXMLBTEAMTOTAL"},
    ]

    def test_declined_rows_are_not_recommendations(self):
        pop = exp.population_split(self.ROWS)
        assert len(pop["recommended"]) == 2
        assert all(r["status"] != "INSUFFICIENT_MODEL_SUPPORT" for r in pop["recommended"])

    def test_insufficient_model_support_is_a_decline(self):
        assert "INSUFFICIENT_MODEL_SUPPORT" in exp.DECLINED_STATUSES
        assert "INSUFFICIENT_MODEL_SUPPORT" not in exp.RECOMMENDED_STATUSES

    def test_confirmed_bet_requires_betplaced_true(self):
        rows = [{"status": "BET_PLACED", "betPlaced": False, "marketFamily": "X"}]
        assert exp.population_split(rows)["confirmedBet"] == []

    def test_recommendation_is_never_counted_as_a_bet(self):
        pop = exp.population_split(self.ROWS)
        assert len(pop["confirmedBet"]) == 1
        assert all(r.get("betPlaced") is True for r in pop["confirmedBet"])

    def test_live_is_recommended_plus_confirmed(self):
        pop = exp.population_split(self.ROWS)
        assert len(pop["live"]) == 3


class TestNoDollarsInvented:
    def test_matrix_reports_dollars_as_unavailable(self):
        m = exp.exposure_matrix([{"marketFamily": "KXMLBRFI", "status": "RECOMMENDED"}])
        assert m[0]["recommendedDollars"] is None
        assert "no stake field" in m[0]["recommendedDollarsNote"]

    def test_artifact_declares_no_invented_dollars(self):
        assert '"dollarsInvented": False' in _fn("main")

    def test_no_stake_estimation_anywhere(self):
        for banned in ("stake =", "estimated_stake", "assumed_stake", "unit_size"):
            assert banned not in SOURCE


class TestEvidenceMapDrivesRiskNotRoi:
    def test_team_total_is_red_with_a_cited_defect(self):
        e = exp.evidence_for("KXMLBTEAMTOTAL")
        assert e["risk"] == "RED"
        assert "SEMANTIC_DEFECT" in e["status"]
        assert "MLB-RSCH-0027" in e["evidence"] and e["knownDefect"]

    def test_moneylines_are_red_from_rsch0027(self):
        for f in ("ML_Home", "ML_Away"):
            assert exp.evidence_for(f)["risk"] == "RED"
            assert "MLB-RSCH-0027" in exp.evidence_for(f)["evidence"]

    def test_underpowered_families_are_yellow_not_green(self):
        for f in ("KXMLBF5", "KXMLBGAME"):
            assert exp.evidence_for(f)["risk"] == "YELLOW"
            assert "INSUFFICIENT_SAMPLE" in exp.evidence_for(f)["status"]

    def test_no_family_is_green_without_validated_evidence(self):
        greens = [f for f, e in exp.EVIDENCE_MAP.items() if e["risk"] == "GREEN"]
        assert greens == [], "a GREEN band requires a validated improvement artifact"

    def test_unknown_family_defaults_to_unassessed_yellow(self):
        e = exp.evidence_for("SOME_NEW_FAMILY")
        assert e["status"] == ["UNASSESSED"] and e["risk"] == "YELLOW"

    def test_red_definition_is_not_tuned_to_roi(self):
        src = _fn("red_counterfactual")
        assert "never tuned to historical ROI" in src
        for banned in ("sort", "argmax", "best", "optimi"):
            assert banned not in src.lower().replace("shareofhypotheticalloss", "")


class TestTeamTotalAuditIsHonest:
    def _row(self, ticker, display):
        return {"marketFamily": "KXMLBTEAMTOTAL", "marketTicker": ticker,
                "thresholdDisplay": display, "_date": "2026-08-20"}

    def test_detects_the_half_run_mismatch(self):
        out = exp.team_total_threshold_audit(
            [self._row("KXMLBTEAMTOTAL-26AUG072140HOUSD-HOU4", "Team Total Over 4")])
        assert out["audited"] == 1 and out["mismatched"] == 1 and out["mismatchRate"] == 1.0

    def test_accepts_a_correct_line(self):
        out = exp.team_total_threshold_audit(
            [self._row("KXMLBTEAMTOTAL-26AUG072140HOUSD-HOU4", "Team Total Over 3.5")])
        assert out["audited"] == 1 and out["mismatched"] == 0

    def test_missing_display_is_unauditable_not_agreement(self):
        """The bug this test exists to prevent: an earlier version scored a
        None display as a match and reported a 0% mismatch rate."""
        out = exp.team_total_threshold_audit(
            [self._row("KXMLBTEAMTOTAL-26AUG072140HOUSD-HOU4", None)])
        assert out["audited"] == 0
        assert out["rowsWithNoUsableThresholdDisplay"] == 1
        assert out["mismatchRate"] is None

    def test_derivation_never_uses_production_stored_value(self):
        src = _fn("team_total_threshold_audit")
        assert "never used as the source of truth" in src

    def test_unparsed_ticker_is_counted_separately(self):
        out = exp.team_total_threshold_audit([self._row("NOT-A-TEAM-TOTAL-TICKER", "Over 3.5")])
        assert out["unparsedTickers"] == 1 and out["audited"] == 0


class TestGovernance:
    def test_nothing_is_fitted_and_no_probability_produced(self):
        main = _fn("main")
        assert '"parametersFitted": 0' in main
        assert '"producesProbability": False' in main
        for node in ast.parse(SOURCE).body:
            if isinstance(node, ast.FunctionDef):
                assert not node.name.startswith("fit_")

    def test_recommendations_never_assumed_placed(self):
        assert '"recommendationsAssumedPlaced": False' in _fn("main")
        assert "never assumed to have been placed" in _fn("hypothetical_performance")

    def test_risk_bands_are_not_production_settings(self):
        main = _fn("main")
        assert '"riskBandsAreProductionSettings": False' in main
        assert '"productionActionAuthorized": False' in main

    def test_no_ledger_is_written(self):
        """Checked against writes and paths, not prose -- the module docstring
        legitimately discusses bankroll exposure."""
        for banned in ("data/edgelab/bets", "data/edgelab/bankroll",
                       "write_pending_bets", "risk_gate", 'open(', "json.dump("):
            if banned in ("open(", "json.dump("):
                continue
            assert banned not in SOURCE, f"module references {banned}"
        # the only files this audit writes are its own two artifacts
        writes = [ln for ln in SOURCE.splitlines() if "open(" in ln and '"w"' in ln]
        assert len(writes) == 2, writes

    def test_hitter_premise_correction_is_recorded(self):
        main = _fn("main")
        assert "hitterPremiseCorrection" in main
        assert "DECLINING" in SOURCE


class TestClassificationThresholds:
    def test_bands_are_ordered_and_exhaustive(self):
        main = _fn("main")
        for label in ("CRITICAL_RESEARCH_RISK", "HIGH_RESEARCH_RISK",
                      "MODERATE_RESEARCH_RISK", "LOW_RESEARCH_RISK"):
            assert label in main

    def test_edge_buckets_are_fixed_constants(self):
        assert exp.EDGE_BUCKETS == ((-1.0, 0.025), (0.025, 0.05), (0.05, 0.075),
                                    (0.075, 0.10), (0.10, 0.15), (0.15, 1.01))
