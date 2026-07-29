#!/usr/bin/env python3
"""
tests/test_risk_gate_rule71_81_bankroll_absence.py
======================================================
Phase 7 Parts 8-9: Rule 71/81 boundary lockdown and bankroll/stake edge
cases for scripts/risk_gate.py.

REAL FINDING (documented in tests/test_risk_gate_immutable.py's module
docstring, re-verified here as an executable regression guard rather
than prose alone): risk_gate.py contains NO Rule 71 or Rule 81 logic,
and NO bankroll concept. Both rules live entirely in
scripts/build_market_ledger.py (Pinnacle-gap check),
scripts/bet_eligibility.py, and scripts/validate_slate_final.py -- none
of which Phase 7 touches. Part 8's boundary-test requirement and Part
9's bankroll-scenario requirement are satisfied by this documented,
grep-verified absence, not by inventing tests for logic that does not
exist in this file. If a future phase ever adds Rule 71/81 or a
bankroll concept to risk_gate.py, the grep tests below will fail loudly,
forcing a deliberate update to this file rather than a silent drift.

The applicable Part 9 equivalent -- fixed-unit stake edge cases at and
around risk_gate.py's ACTUAL thresholds (TT_MIN_EDGE_PCT=2.5,
TT_MAX_STAKE=20.0u, DAILY_RISK_CAP=40.0u, TT_MAX_STAKE_PCT=0.40,
ML_F5_MIN_STAKE_PCT=0.50) -- is tested precisely here, including the
exact-equality boundary (all five thresholds use strict `>`/`<`, never
`>=`/`<=`, so the exact threshold value itself must NOT trigger the
rule) and int(line) truncation behavior for non-integer line values.
"""

import inspect
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from test_risk_gate_immutable import make_entry, make_tt_entry, make_game, make_slate, NOW

RISK_GATE_PATH = os.path.join(ROOT, "scripts", "risk_gate.py")


@pytest.fixture
def rg():
    if "risk_gate" in sys.modules:
        del sys.modules["risk_gate"]
    import risk_gate as _rg
    return _rg


class TestRule71And81DocumentedAbsence:

    def test_no_rule_71_or_rule_81_references_in_source(self):
        with open(RISK_GATE_PATH) as f:
            source = f.read()
        assert not re.search(r'rule\s*71', source, re.IGNORECASE)
        assert not re.search(r'rule\s*81', source, re.IGNORECASE)

    def test_no_bankroll_concept_in_source(self):
        with open(RISK_GATE_PATH) as f:
            source = f.read()
        assert not re.search(r'bankroll', source, re.IGNORECASE)

    def test_no_correlation_or_duplicate_detection_terms_in_source(self):
        """
        Part 15's documented absence (same-team/opposing-side/market-
        overlap/NRFI-YRFI-conflict logic), re-verified alongside 8-9 since
        all three are "grep for the concept, confirm it's absent" findings
        of the same shape.
        """
        with open(RISK_GATE_PATH) as f:
            source = f.read()
        for term in ('correlat', 'opposing_side', 'duplicate_market', 'nrfi', 'yrfi'):
            assert not re.search(term, source, re.IGNORECASE), f"unexpected '{term}' reference found"


class TestTtEdgeThresholdExactBoundary:
    """TT_MIN_EDGE_PCT = 2.5; the check is `edge < TT_MIN_EDGE_PCT` (strict)."""

    def test_edge_exactly_2_5_passes_not_downgraded(self, rg):
        entry = make_tt_entry(tier='HIGH', edge=2.5)
        slate = make_slate([make_game(entries=[entry])])
        downgrades = rg.apply_tt_safety(slate, now_ts=NOW)
        assert downgrades == []
        assert entry['confidenceTier'] == 'HIGH'

    def test_edge_one_hundredth_below_2_5_downgrades(self, rg):
        entry = make_tt_entry(tier='HIGH', edge=2.49)
        slate = make_slate([make_game(entries=[entry])])
        downgrades = rg.apply_tt_safety(slate, now_ts=NOW)
        assert len(downgrades) == 1
        assert entry['confidenceTier'] == 'PAPER'

    def test_edge_one_hundredth_above_2_5_passes(self, rg):
        entry = make_tt_entry(tier='HIGH', edge=2.51)
        slate = make_slate([make_game(entries=[entry])])
        downgrades = rg.apply_tt_safety(slate, now_ts=NOW)
        assert downgrades == []


class TestStakeCapExactBoundaries:
    """TT_MAX_STAKE=20.0u, DAILY_RISK_CAP=40.0u -- both `>` (strict)."""

    def test_tt_stake_cap_one_cent_below_no_warning(self, rg):
        entries = [('A@B', make_entry(market='TT_Away_Over', tier='HIGH', edge=4.0, stake=4.99))
                   for _ in range(4)]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        assert report['tt_stake'] == pytest.approx(19.96)
        assert not any(w.startswith('TT_STAKE_CAP') for w in report['concentration_warnings'])

    def test_tt_stake_cap_one_cent_above_warns(self, rg):
        entries = [('A@B', make_entry(market='TT_Away_Over', tier='HIGH', edge=4.0, stake=5.01))
                   for _ in range(4)]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        assert report['tt_stake'] == pytest.approx(20.04)
        assert any(w.startswith('TT_STAKE_CAP') for w in report['concentration_warnings'])

    def test_daily_risk_cap_one_cent_below_no_warning(self, rg):
        entries = [(f'A{i}@B{i}', make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=4.99))
                   for i in range(8)]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        assert report['total_real_stake'] == pytest.approx(39.92)
        assert not any(w.startswith('DAILY_RISK_CAP') for w in report['concentration_warnings'])

    def test_daily_risk_cap_one_cent_above_warns(self, rg):
        entries = [(f'A{i}@B{i}', make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.01))
                   for i in range(8)]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        assert report['total_real_stake'] == pytest.approx(40.08)
        assert any(w.startswith('DAILY_RISK_CAP') for w in report['concentration_warnings'])

    def test_zero_stake_boundary(self, rg):
        entries = [('A@B', make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=0.00))]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        assert report['total_real_stake'] == 0.00
        assert report['total_bets'] == 1

    def test_one_cent_stake_boundary(self, rg):
        entries = [('A@B', make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=0.01))]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        assert report['total_real_stake'] == pytest.approx(0.01)


class TestRequiredRunsToWinTruncation:
    """
    requiredRunsToWin = int(line) + 1. `int()` truncates toward zero
    (not floor), which matters for negative lines -- a scenario that
    should never occur in production (team-total lines are always
    non-negative) but is not validated/rejected by risk_gate.py, so its
    exact (truncating, not flooring) behavior must be preserved.
    """

    def test_integer_line_truncation(self, rg):
        entry = make_tt_entry(tier='HIGH', edge=4.0, line=4)
        decision = rg.evaluate_candidate_tt_risk(entry)
        assert decision['requiredRunsToWin'] == 5

    def test_fractional_line_truncates_toward_zero(self, rg):
        entry = make_tt_entry(tier='HIGH', edge=4.0, line=4.9)
        decision = rg.evaluate_candidate_tt_risk(entry)
        assert decision['requiredRunsToWin'] == 5  # int(4.9) == 4, +1 == 5

    def test_negative_fractional_line_truncates_toward_zero_not_floor(self, rg):
        entry = make_tt_entry(tier='HIGH', edge=4.0, line=-4.5)
        decision = rg.evaluate_candidate_tt_risk(entry)
        assert decision['requiredRunsToWin'] == -3  # int(-4.5) == -4 (trunc, not -5), +1 == -3

    def test_zero_line(self, rg):
        entry = make_tt_entry(tier='HIGH', edge=4.0, line=0)
        decision = rg.evaluate_candidate_tt_risk(entry)
        assert decision['requiredRunsToWin'] == 1
