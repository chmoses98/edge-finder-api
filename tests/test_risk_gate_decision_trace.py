#!/usr/bin/env python3
"""
tests/test_risk_gate_decision_trace.py
=========================================
Verifies tests/risk_gate_trace.py's test-only decision trace mechanism
(Phase 7 Part 5) against the untouched scripts/risk_gate.py: proves the
trace call is production-output-identical to an untraced direct call, and
proves the trace correctly reconstructs rule sequence, pass/fail, first
terminal rejection reason, stake before/after, and family exposure
before/after -- including an adversarial fixture where TT_EVIDENCE and
TT_EDGE fail simultaneously (setting up Phase 7 Part 7's rule-order
preservation proof, which will diff this same trace shape against the
post-refactor implementation).
"""

import copy
import json
import os
import sys

import pytest

from risk_gate_trace import build_decision_trace
from test_risk_gate_immutable import (
    make_entry, make_tt_entry, make_game, make_slate, NOW,
    _game_with_ml, _game_with_tt,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))


@pytest.fixture
def rg():
    if "risk_gate" in sys.modules:
        del sys.modules["risk_gate"]
    import risk_gate as _rg
    return _rg


class TestDecisionTraceProductionEquivalence:

    def test_trace_call_returns_identical_decision_and_report_to_direct_call(self, rg):
        tt_game, tt_entry = _game_with_tt(('A', 'B'), stake=4.0, edge=1.0)
        ml_game, ml_entry = _game_with_ml(('C', 'D'), stake=5.0, market='ML_Away')
        slate_traced = make_slate([copy.deepcopy(tt_game), copy.deepcopy(ml_game)])
        slate_direct = copy.deepcopy(slate_traced)

        _, decision_traced, report_traced, trace = build_decision_trace(rg, slate_traced, now_ts=NOW)
        rg.apply_tt_safety(slate_direct, now_ts=NOW)
        decision_direct, report_direct = rg.apply_portfolio_rules(slate_direct, now_ts=NOW)

        assert decision_traced == decision_direct
        assert report_traced == report_direct
        assert slate_traced == slate_direct

    def test_trace_does_not_leak_live_entry_references(self, rg):
        tt_game, tt_entry = _game_with_tt(('A', 'B'), stake=4.0, edge=4.0)
        slate = make_slate([tt_game])
        _, _, _, trace = build_decision_trace(rg, slate, now_ts=NOW)
        for c in trace['candidates']:
            assert 'entry' not in c


class TestDecisionTraceRuleSequence:

    def test_passing_tt_candidate_all_rules_pass_no_terminal_reason(self, rg):
        tt_game, tt_entry = _game_with_tt(('A', 'B'), stake=4.0, edge=4.0)
        slate = make_slate([tt_game])
        _, _, _, trace = build_decision_trace(rg, slate, now_ts=NOW)
        c = trace['candidates'][0]
        assert c['rules_evaluated'] == ['TT_EVIDENCE', 'TT_EDGE']
        assert c['pass_fail'] == {'TT_EVIDENCE': True, 'TT_EDGE': True}
        assert c['first_terminal_reason'] is None
        assert c['final_classification'] == 'HIGH'
        assert c['execution_included'] is True

    def test_edge_only_failure_first_terminal_reason_is_edge(self, rg):
        tt_game, tt_entry = _game_with_tt(('A', 'B'), stake=4.0, edge=1.0)
        slate = make_slate([tt_game])
        _, _, _, trace = build_decision_trace(rg, slate, now_ts=NOW)
        c = trace['candidates'][0]
        assert c['pass_fail'] == {'TT_EVIDENCE': True, 'TT_EDGE': False}
        assert c['first_terminal_reason'] == 'TT_EDGE'
        assert c['final_classification'] == 'PAPER'
        assert c['execution_included'] is False
        assert c['stake_before'] == 4.0
        assert c['stake_after'] == 1.0  # forced to PAPER's fixed 1.0u

    def test_adversarial_both_evidence_and_edge_fail_first_terminal_is_evidence(self, rg):
        """
        Adversarial multi-rule-failure fixture (Phase 7 Part 7 setup): when
        both TT_EVIDENCE and TT_EDGE fail simultaneously, risk_gate.py's own
        evaluation order (evidence check first, edge check second) must
        remain the trace's reconstructed first_terminal_reason -- proving
        the trace reflects the LEGACY order, not an alphabetical or
        severity-based reordering.
        """
        tt_entry = make_tt_entry(tier='HIGH', edge=1.0)
        tt_entry['awayProjRuns'] = None  # also fails evidence
        game = make_game('A', 'B', [tt_entry])
        slate = make_slate([game])
        _, _, _, trace = build_decision_trace(rg, slate, now_ts=NOW)
        c = trace['candidates'][0]
        assert c['pass_fail'] == {'TT_EVIDENCE': False, 'TT_EDGE': False}
        assert c['first_terminal_reason'] == 'TT_EVIDENCE'

    def test_non_tt_candidate_has_no_tt_rules_evaluated(self, rg):
        ml_game, ml_entry = _game_with_ml(('A', 'B'), stake=5.0, market='ML_Away')
        slate = make_slate([ml_game])
        _, _, _, trace = build_decision_trace(rg, slate, now_ts=NOW)
        c = trace['candidates'][0]
        assert c['rules_evaluated'] == []
        assert c['first_terminal_reason'] is None
        assert c['execution_included'] is True

    def test_rejected_status_tt_candidate_not_evaluated_but_still_enriched(self, rg):
        tt_entry = make_tt_entry(status='Rejected', tier='HIGH', edge=1.0)
        slate = make_slate([make_game('A', 'B', [tt_entry])])
        _, _, _, trace = build_decision_trace(rg, slate, now_ts=NOW)
        c = trace['candidates'][0]
        assert c['pass_fail'] == {'TT_EVIDENCE': True, 'TT_EDGE': True}
        assert c['execution_included'] is False


class TestDecisionTraceFamilyExposure:

    def test_tt_max_bets_family_exposure_shows_pre_post_asymmetry(self, rg):
        # All 5 edges stay above TT_MIN_EDGE_PCT=2.5 so apply_tt_safety
        # itself downgrades none of them -- the TT_MAX_BETS cap (max 4) is
        # enforced entirely by apply_portfolio_rules, in isolation.
        pairs = [_game_with_tt((f'A{i}', f'B{i}'), stake=5.0, edge=6.0 - i * 0.5) for i in range(5)]
        slate = make_slate([g for g, _ in pairs])
        _, decision, report, trace = build_decision_trace(rg, slate, now_ts=NOW)
        tt_exposure = next(f for f in trace['family_exposure'] if f['family'] == 'TT')
        # Pre-downgrade tally: all 5 counted (report['tt_bets']/tt_stake).
        assert tt_exposure['bets_before'] == 5
        assert tt_exposure['stake_before'] == 25.0
        # Post-downgrade: only the top 4 by edge remain real-money.
        assert tt_exposure['bets_after'] == 4
        assert tt_exposure['stake_after'] == 20.0

    def test_family_exposure_stable_when_no_downgrades_fire(self, rg):
        pairs = [_game_with_tt((f'A{i}', f'B{i}'), stake=5.0, edge=4.0) for i in range(4)]
        ml_game, ml_entry = _game_with_ml(('X', 'Y'), stake=5.0, market='ML_Away')
        slate = make_slate([g for g, _ in pairs] + [ml_game])
        _, decision, report, trace = build_decision_trace(rg, slate, now_ts=NOW)
        tt_exposure = next(f for f in trace['family_exposure'] if f['family'] == 'TT')
        ml_exposure = next(f for f in trace['family_exposure'] if f['family'] == 'ML_F5')
        assert tt_exposure['bets_before'] == tt_exposure['bets_after'] == 4
        assert tt_exposure['stake_before'] == tt_exposure['stake_after'] == 20.0
        assert ml_exposure['bets_before'] == ml_exposure['bets_after'] == 1
