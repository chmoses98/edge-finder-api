#!/usr/bin/env python3
"""
tests/test_risk_gate_rule_order.py
=====================================
Phase 7 Part 7: rule-order preservation under adversarial multi-rule
failure. These fixtures deliberately trigger MULTIPLE risk_gate.py rules
at once and assert the exact legacy warning order, first-terminal
rejection reasons, and decision_reason composition survive unchanged --
"do not 'improve' or simplify rule ordering" (mission's own words).
Run against the Phase 6/7-refactored build_risk_portfolio()/
evaluate_candidate_tt_risk() (see scripts/risk_gate.py), which must
reproduce the pre-refactor single-function implementation's behavior
exactly for every case here.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from test_risk_gate_immutable import (
    make_entry, make_tt_entry, make_game, make_slate, NOW,
    _game_with_ml, _game_with_tt,
)


@pytest.fixture
def rg():
    if "risk_gate" in sys.modules:
        del sys.modules["risk_gate"]
    import risk_gate as _rg
    return _rg


class TestAdversarialRuleOrderPreservation:

    def test_daily_cap_and_tt_dominance_both_fire_decision_reason_omits_underfill(self, rg):
        """
        Construct a slate that simultaneously triggers DAILY_RISK_CAP,
        TT_DOMINANCE, and ML_F5_UNDERFILL. Legacy hard_block logic only
        folds CAP/DOMINANCE warnings into decision_reason -- UNDERFILL
        must never leak into decision_reason even though it fired.
        """
        pairs = [_game_with_tt((f'A{i}', f'B{i}'), stake=10.0, edge=4.0) for i in range(4)]
        ml1, e1 = _game_with_ml(('X', 'Y'), stake=0.01, market='ML_Away')
        ml2, e2 = _game_with_ml(('Z', 'W'), stake=0.01, market='ML_Home')
        slate = make_slate([g for g, _ in pairs] + [ml1, ml2])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)

        warnings = report['concentration_warnings']
        assert any(w.startswith('DAILY_RISK_CAP') for w in warnings)
        assert any(w.startswith('TT_DOMINANCE') for w in warnings)
        assert any(w.startswith('ML_F5_UNDERFILL') for w in warnings)
        # Legacy warning evaluation order: daily cap first, then TT rules,
        # then ML/F5 underfill last -- exactly the order the warnings list
        # is appended in, never reordered/sorted/deduplicated.
        kinds = [w.split(':')[0].split(' ')[0] for w in warnings]
        assert kinds.index('DAILY_RISK_CAP') < kinds.index('TT_DOMINANCE')
        assert kinds.index('TT_DOMINANCE') < kinds.index('ML_F5_UNDERFILL')

        assert decision == 'PAPER_ONLY'
        assert 'DAILY_RISK_CAP' in report['decision_reason']
        assert 'TT_DOMINANCE' in report['decision_reason']
        assert 'ML_F5_UNDERFILL' not in report['decision_reason']

    def test_all_five_warnings_fire_simultaneously_exact_order_preserved(self, rg):
        """
        A single adversarial fixture triggering every warning rule
        risk_gate.py has (DAILY_RISK_CAP, TT_CONCENTRATION, TT_STAKE_CAP,
        TT_DOMINANCE, ML_F5_UNDERFILL) in one run: 6 TT bets (exceeds
        TT_MAX_BETS=4, triggers TT_CONCENTRATION), each at high stake
        (triggers TT_STAKE_CAP and TT_DOMINANCE post-downgrade), plus 2
        tiny ML_F5 bets (triggers ML_F5_UNDERFILL) and total stake over
        the daily cap.
        """
        tt_pairs = [_game_with_tt((f'A{i}', f'B{i}'), stake=8.0, edge=10.0 - i) for i in range(6)]
        ml1, e1 = _game_with_ml(('X', 'Y'), stake=0.01, market='ML_Away')
        ml2, e2 = _game_with_ml(('Z', 'W'), stake=0.01, market='ML_Home')
        slate = make_slate([g for g, _ in tt_pairs] + [ml1, ml2])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)

        warnings = report['concentration_warnings']
        legacy_order = ['DAILY_RISK_CAP', 'TT_CONCENTRATION', 'TT_STAKE_CAP', 'TT_DOMINANCE', 'ML_F5_UNDERFILL']
        fired_order = [next(name for name in legacy_order if w.startswith(name)) for w in warnings]
        assert fired_order == legacy_order, (
            f"expected all 5 warnings to fire in exact legacy order, got {fired_order}"
        )

        # Downgrade selection: lowest-edge entries beyond TT_MAX_BETS=4 are
        # cut, in strict edge-descending order -- edges are 10,9,8,7,6,5;
        # the bottom 2 (6.0 and 5.0, indices 4 and 5) must be downgraded.
        kept_edges = sorted(
            [e.get('edge') for _, e in tt_pairs if e['confidenceTier'] == 'HIGH']
        )
        downgraded_edges = sorted(
            [e.get('edge') for _, e in tt_pairs if e['confidenceTier'] == 'PAPER']
        )
        assert kept_edges == [7.0, 8.0, 9.0, 10.0]
        assert downgraded_edges == [5.0, 6.0]

        assert decision == 'PAPER_ONLY'
        assert 'ML_F5_UNDERFILL' not in report['decision_reason']

    def test_tied_edges_downgrade_order_is_stable_not_reordered(self, rg):
        """
        When multiple TT candidates share the exact same edge value,
        Python's sorted() is stable -- ties must resolve in ORIGINAL
        list (game) order, not be re-sorted by any secondary key. This
        locks in that stability as legacy behavior the refactor must not
        alter, since Part 7 explicitly forbids "improving" tie-breaking.
        """
        pairs = [_game_with_tt((f'A{i}', f'B{i}'), stake=5.0, edge=4.0, side='Away') for i in range(5)]
        slate = make_slate([g for g, _ in pairs])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        # All 5 share edge=4.0 -- stable sort keeps original order, so the
        # LAST one in list order (index 4) is the one beyond the top-4 cut.
        tiers = [e['confidenceTier'] for _, e in pairs]
        assert tiers == ['HIGH', 'HIGH', 'HIGH', 'HIGH', 'PAPER']

    def test_tt_pass_then_portfolio_pass_combined_first_terminal_reason_is_tt_pass(self, rg):
        """
        End-to-end (both passes, as main() runs them): a candidate that
        fails the TT safety pass's edge check is downgraded to PAPER
        BEFORE the portfolio pass ever runs -- so it is excluded from
        the family tally entirely, and portfolio-level rules (max bets,
        stake cap) never "see" it. This is the exact two-pass ordering
        main() has always used and must continue to use.
        """
        low_edge_tt = make_tt_entry(tier='HIGH', edge=1.0)  # fails TT edge check
        good_tt = [make_tt_entry(tier='HIGH', edge=4.0, ticker=f'G{i}') for i in range(4)]
        game1 = make_game('A', 'B', [low_edge_tt])
        games_good = [make_game(f'C{i}', f'D{i}', [e]) for i, e in enumerate(good_tt)]
        slate = make_slate([game1] + games_good)

        tt_downgrades = rg.apply_tt_safety(slate, now_ts=NOW)
        assert len(tt_downgrades) == 1
        assert low_edge_tt['confidenceTier'] == 'PAPER'

        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        # Only the 4 good entries reach the portfolio tally -- the TT-pass
        # downgraded entry is excluded before portfolio rules ever run.
        assert report['tt_bets'] == 4
        assert not any('TT_CONCENTRATION' in w for w in report['concentration_warnings'])
