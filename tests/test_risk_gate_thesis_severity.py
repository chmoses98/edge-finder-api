#!/usr/bin/env python3
"""
tests/test_risk_gate_thesis_severity.py
============================================
MLB Model Expression Guardrails milestone: coverage for the severity
labels (DUPLICATE_THESIS / MODERATELY_CORRELATED) this milestone adds
onto scripts/risk_gate.py's EXISTING evaluate_correlation_gate() output
(correlationGroups items and the report's clusters list). Purely
additive provenance -- see risk_gate.py's own RULE_TYPE_SEVERITY
comment. Does not change which entries get downgraded; that behavior
(and its extensive existing coverage) lives in
tests/test_risk_gate_correlation_gate.py, untouched by this milestone.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from test_risk_gate_immutable import make_entry, make_tt_entry, make_game, make_slate, NOW


@pytest.fixture
def rg():
    if "risk_gate" in sys.modules:
        del sys.modules["risk_gate"]
    import risk_gate as _rg
    return _rg


def _decision_for(decisions, market):
    for d in decisions:
        if d['entry'].get('market') == market:
            return d
    raise KeyError(f"no decision for market {market!r}")


class TestDuplicateThesisSeverity:
    """SAME_SIDE_THESIS (ML+F5 same side) and SAME_MARKET_BOTH_SIDES (NRFI+YRFI) are DUPLICATE_THESIS."""

    def test_same_side_thesis_pair_is_duplicate_severity(self, rg):
        ml = make_entry(market='ML_Away', edge=5.0, ticker='ML')
        f5 = make_entry(market='F5_ML_Away', edge=3.0, ticker='F5')
        entries = [('KC@WSH', ml), ('KC@WSH', f5)]
        decisions, _ = rg.evaluate_correlation_gate(entries)
        ml_d = _decision_for(decisions, 'ML_Away')
        f5_d = _decision_for(decisions, 'F5_ML_Away')
        assert any(g['type'] == rg.SAME_SIDE_THESIS and g['severity'] == rg.DUPLICATE_THESIS
                   for g in ml_d['correlationGroups'])
        assert any(g['type'] == rg.SAME_SIDE_THESIS and g['severity'] == rg.DUPLICATE_THESIS
                   for g in f5_d['correlationGroups'])

    def test_nrfi_yrfi_pair_is_duplicate_severity(self, rg):
        nrfi = make_entry(market='NRFI', edge=4.0, ticker='N')
        yrfi = make_entry(market='YRFI', edge=2.0, ticker='Y')
        entries = [('KC@WSH', nrfi), ('KC@WSH', yrfi)]
        decisions, _ = rg.evaluate_correlation_gate(entries)
        nrfi_d = _decision_for(decisions, 'NRFI')
        assert any(g['type'] == rg.SAME_MARKET_BOTH_SIDES and g['severity'] == rg.DUPLICATE_THESIS
                   for g in nrfi_d['correlationGroups'])


class TestModeratelyCorrelatedSeverity:
    """SIDE_TEAM_TOTAL and PITCHER_DEPENDENT are MODERATELY_CORRELATED, never DUPLICATE_THESIS."""

    def test_side_team_total_pair_is_moderate_severity(self, rg):
        ml = make_entry(market='ML_Away', edge=5.0, ticker='ML')
        tt = make_tt_entry(side='Away', edge=3.0, ticker='TT')
        entries = [('KC@WSH', ml), ('KC@WSH', tt)]
        decisions, _ = rg.evaluate_correlation_gate(entries)
        ml_d = _decision_for(decisions, 'ML_Away')
        assert any(g['type'] == rg.SIDE_TEAM_TOTAL and g['severity'] == rg.MODERATELY_CORRELATED
                   for g in ml_d['correlationGroups'])

    def test_pitcher_dependent_pair_is_moderate_severity(self, rg):
        nrfi = make_entry(market='NRFI', edge=4.0, ticker='N')
        f5 = make_entry(market='F5_ML_Away', edge=2.0, ticker='F5')
        entries = [('KC@WSH', nrfi), ('KC@WSH', f5)]
        decisions, _ = rg.evaluate_correlation_gate(entries)
        nrfi_d = _decision_for(decisions, 'NRFI')
        assert any(g['type'] == rg.PITCHER_DEPENDENT and g['severity'] == rg.MODERATELY_CORRELATED
                   for g in nrfi_d['correlationGroups'])


class TestClusterReportSeverity:
    def test_cluster_report_carries_severity(self, rg):
        # NRFI+YRFI (SAME_MARKET_BOTH_SIDES) is not deduped by Step 1 (which
        # only handles ML+F5 same-side pairs), so both stay active into
        # Step 3's cluster report.
        nrfi = make_entry(market='NRFI', edge=4.0, ticker='N')
        yrfi = make_entry(market='YRFI', edge=2.0, ticker='Y')
        entries = [('KC@WSH', nrfi), ('KC@WSH', yrfi)]
        _, report = rg.evaluate_correlation_gate(entries)
        assert len(report['clusters']) == 1
        assert report['clusters'][0]['severity'] == rg.DUPLICATE_THESIS

    def test_mixed_cluster_reports_the_stricter_severity(self, rg):
        """A cluster combining a MODERATE edge and a DUPLICATE edge reports DUPLICATE (the stricter label), never silently drops it."""
        nrfi = make_entry(market='NRFI', edge=4.0, ticker='N')
        yrfi = make_entry(market='YRFI', edge=2.0, ticker='Y')
        f5 = make_entry(market='F5_ML_Away', edge=1.5, ticker='F5')
        # NRFI-YRFI edge is DUPLICATE_THESIS; NRFI-F5_ML_Away edge is
        # MODERATELY_CORRELATED (PITCHER_DEPENDENT) -- both in one
        # connected component via the shared NRFI node.
        entries = [('KC@WSH', nrfi), ('KC@WSH', yrfi), ('KC@WSH', f5)]
        _, report = rg.evaluate_correlation_gate(entries)
        assert len(report['clusters']) == 1
        assert report['clusters'][0]['severity'] == rg.DUPLICATE_THESIS

    def test_report_shape_unchanged_by_severity_addition(self, rg):
        """The exact top-level report shape must stay {'warnings','downgrades','clusters','total_stake_basis'} -- severity is nested, never a new top-level key."""
        ml = make_entry(market='ML_Away', edge=5.0, ticker='ML')
        entries = [('KC@WSH', ml)]
        _, report = rg.evaluate_correlation_gate(entries)
        assert set(report.keys()) == {'warnings', 'downgrades', 'clusters', 'total_stake_basis'}


class TestSeverityNeverChangesDowngradeDecisions:
    """The severity addition is purely descriptive -- it must never change which entries get downgraded."""

    def test_downgrade_outcome_matches_pre_existing_behavior(self, rg):
        ml = make_entry(market='ML_Away', edge=5.0, ticker='ML')
        f5 = make_entry(market='F5_ML_Away', edge=3.0, ticker='F5')
        entries = [('KC@WSH', ml), ('KC@WSH', f5)]
        decisions, _ = rg.evaluate_correlation_gate(entries)
        ml_d = _decision_for(decisions, 'ML_Away')
        f5_d = _decision_for(decisions, 'F5_ML_Away')
        # Higher edge (ML, 5.0) kept; lower edge (F5, 3.0) downgraded --
        # exactly the pre-existing Step 1 outcome.
        assert ml_d['downgrade'] is False
        assert f5_d['downgrade'] is True
