#!/usr/bin/env python3
"""
tests/test_risk_gate_correlation_gate.py
============================================
Portfolio Correlation Gate milestone: coverage for
scripts/risk_gate.py's new evaluate_correlation_gate()/
apply_correlation_gate() pass (CORRELATION_RULES, build_same_game_clusters,
GAME_MAX_REAL_MONEY_BETS, GAME_CLUSTER_MAX_STAKE_PCT).

Every wager this gate ever considers has ALREADY independently cleared
the normal EV/edge threshold (it only ever looks at status='Accepted' +
confidenceTier in ('HIGH','MEDIUM') entries) -- this gate decides which
of several already-qualified bets stay real-money-eligible once
shared/correlated risk within a game is accounted for. It is
downgrade-only: a flagged entry is forced to PAPER (same shape every
other gate in this file already uses -- confidence/confidenceTier,
betSize=1.0, realMoneyBlocked, blockReason, gatesFired), never deleted,
and no other entry's stake is ever changed as a result.
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


# ══════════════════════════════════════════════════════════════════════════════
# One-primary-expression: duplicate F3/F5 side expressions (ML + F5, same side)
# ══════════════════════════════════════════════════════════════════════════════

class TestSameSideThesisDedup:

    def test_ml_and_f5_same_side_lower_edge_downgraded(self, rg):
        ml = make_entry(market='ML_Away', edge=3.0, stake=3.0, ticker='ML')
        f5 = make_entry(market='F5_ML_Away', edge=5.0, stake=3.0, ticker='F5')
        entries = [('A@B', ml), ('A@B', f5)]
        decisions, report = rg.evaluate_correlation_gate(entries)

        ml_d = _decision_for(decisions, 'ML_Away')
        f5_d = _decision_for(decisions, 'F5_ML_Away')
        assert ml_d['downgrade'] is True
        assert 'CORRELATION_DUPLICATE_THESIS' in ml_d['downgradeReason']
        assert f5_d['downgrade'] is False
        assert any(g['type'] == rg.SAME_SIDE_THESIS for g in ml_d['correlationGroups'])
        assert any(g['type'] == rg.SAME_SIDE_THESIS for g in f5_d['correlationGroups'])

    def test_higher_edge_expression_always_kept_regardless_of_market(self, rg):
        # Same pair, edges reversed -- the ML side should now be kept.
        ml = make_entry(market='ML_Home', edge=6.0, stake=2.0, ticker='ML')
        f5 = make_entry(market='F5_ML_Home', edge=2.0, stake=2.0, ticker='F5')
        entries = [('A@B', ml), ('A@B', f5)]
        decisions, report = rg.evaluate_correlation_gate(entries)
        assert _decision_for(decisions, 'ML_Home')['downgrade'] is False
        assert _decision_for(decisions, 'F5_ML_Home')['downgrade'] is True

    def test_opposite_sides_not_deduped(self, rg):
        """ML_Away + F5_ML_Home express DIFFERENT teams' theses -- not a
        duplicate, must not be touched by thesis dedup."""
        ml = make_entry(market='ML_Away', edge=4.0, stake=2.0, ticker='ML')
        f5 = make_entry(market='F5_ML_Home', edge=4.0, stake=2.0, ticker='F5')
        entries = [('A@B', ml), ('A@B', f5)]
        decisions, report = rg.evaluate_correlation_gate(entries)
        assert _decision_for(decisions, 'ML_Away')['downgrade'] is False
        assert _decision_for(decisions, 'F5_ML_Home')['downgrade'] is False

    def test_downgraded_entry_does_not_reduce_the_kept_entrys_stake(self, rg):
        """Requirement: removing a wager must never reallocate bankroll --
        the kept entry's own betSize is never touched by this gate."""
        ml = make_entry(market='ML_Away', edge=3.0, stake=3.0, ticker='ML')
        f5 = make_entry(market='F5_ML_Away', edge=5.0, stake=7.0, ticker='F5')
        entries = [('A@B', ml), ('A@B', f5)]
        rg.evaluate_correlation_gate(entries)
        # evaluate_correlation_gate is pure -- entries themselves untouched.
        assert ml['betSize'] == 3.0
        assert f5['betSize'] == 7.0


# ══════════════════════════════════════════════════════════════════════════════
# NRFI + pitcher-performance correlation (expressed via the pitcher-driven
# F5 ML market -- the only real-money pitcher-quality-driven market in
# marketLedger; see scripts/risk_gate.py's CORRELATION_RULES docstring)
# ══════════════════════════════════════════════════════════════════════════════

class TestPitcherDependentCorrelation:

    def test_nrfi_and_f5_ml_tagged_pitcher_dependent(self, rg):
        nrfi = make_entry(market='NRFI', edge=2.0, stake=1.0, ticker='N')
        f5   = make_entry(market='F5_ML_Away', edge=2.0, stake=1.0, ticker='F5')
        # Large unrelated stake elsewhere so this cluster is well under the
        # 15% target cap -- isolates the TAGGING behavior from the stake cap.
        filler = make_entry(market='ML_Away', edge=4.0, stake=50.0, ticker='FILL')
        entries = [('A@B', nrfi), ('A@B', f5), ('C@D', filler)]
        decisions, report = rg.evaluate_correlation_gate(entries)

        nrfi_d = _decision_for(decisions, 'NRFI')
        f5_d = _decision_for(decisions, 'F5_ML_Away')
        assert nrfi_d['downgrade'] is False
        assert f5_d['downgrade'] is False
        assert any(g['type'] == rg.PITCHER_DEPENDENT and 'F5_ML_Away' in g['withMarkets']
                   for g in nrfi_d['correlationGroups'])
        assert any(g['type'] == rg.PITCHER_DEPENDENT and 'NRFI' in g['withMarkets']
                   for g in f5_d['correlationGroups'])

    def test_yrfi_and_f5_ml_home_also_tagged(self, rg):
        yrfi = make_entry(market='YRFI', edge=2.0, stake=1.0, ticker='Y')
        f5   = make_entry(market='F5_ML_Home', edge=2.0, stake=1.0, ticker='F5')
        filler = make_entry(market='ML_Away', edge=4.0, stake=50.0, ticker='FILL')
        entries = [('A@B', yrfi), ('A@B', f5), ('C@D', filler)]
        decisions, report = rg.evaluate_correlation_gate(entries)
        yrfi_d = _decision_for(decisions, 'YRFI')
        assert any(g['type'] == rg.PITCHER_DEPENDENT for g in yrfi_d['correlationGroups'])

    def test_nrfi_and_yrfi_both_sides_tagged_same_market_both_sides(self, rg):
        nrfi = make_entry(market='NRFI', edge=2.0, stake=1.0, ticker='N')
        yrfi = make_entry(market='YRFI', edge=2.0, stake=1.0, ticker='Y')
        filler = make_entry(market='ML_Away', edge=4.0, stake=50.0, ticker='FILL')
        entries = [('A@B', nrfi), ('A@B', yrfi), ('C@D', filler)]
        decisions, report = rg.evaluate_correlation_gate(entries)
        nrfi_d = _decision_for(decisions, 'NRFI')
        assert any(g['type'] == rg.SAME_MARKET_BOTH_SIDES for g in nrfi_d['correlationGroups'])


# ══════════════════════════════════════════════════════════════════════════════
# Side + team-total correlation
# ══════════════════════════════════════════════════════════════════════════════

class TestSideTeamTotalCorrelation:

    def test_ml_away_and_tt_away_over_tagged_side_team_total(self, rg):
        ml = make_entry(market='ML_Away', edge=2.0, stake=1.0, ticker='ML')
        tt = make_tt_entry(side='Away', edge=2.0, stake=1.0, ticker='TT')
        filler = make_entry(market='ML_Home', edge=4.0, stake=50.0, ticker='FILL')
        entries = [('A@B', ml), ('A@B', tt), ('C@D', filler)]
        decisions, report = rg.evaluate_correlation_gate(entries)
        ml_d = _decision_for(decisions, 'ML_Away')
        tt_d = _decision_for(decisions, 'TT_Away_Over')
        assert any(g['type'] == rg.SIDE_TEAM_TOTAL for g in ml_d['correlationGroups'])
        assert any(g['type'] == rg.SIDE_TEAM_TOTAL for g in tt_d['correlationGroups'])

    def test_away_team_total_not_correlated_with_home_side(self, rg):
        """TT_Away_Over depends on the AWAY offense -- must not be flagged
        as correlated with the HOME side's ML/F5."""
        ml_home = make_entry(market='ML_Home', edge=4.0, stake=1.0, ticker='MLH')
        tt_away = make_tt_entry(side='Away', edge=4.0, stake=1.0, ticker='TTA')
        entries = [('A@B', ml_home), ('A@B', tt_away)]
        decisions, report = rg.evaluate_correlation_gate(entries)
        assert _decision_for(decisions, 'ML_Home')['correlationGroups'] == []
        assert _decision_for(decisions, 'TT_Away_Over')['correlationGroups'] == []


# ══════════════════════════════════════════════════════════════════════════════
# Two bets from one game / genuinely independent bets remaining eligible
# ══════════════════════════════════════════════════════════════════════════════

class TestTwoBetsPerGameBoundary:

    def test_exactly_two_uncorrelated_bets_in_one_game_both_remain_eligible(self, rg):
        ml = make_entry(market='ML_Away', edge=4.0, stake=2.0, ticker='ML')
        tt = make_tt_entry(side='Home', edge=4.0, stake=2.0, ticker='TT')
        entries = [('A@B', ml), ('A@B', tt)]
        decisions, report = rg.evaluate_correlation_gate(entries)
        assert _decision_for(decisions, 'ML_Away')['downgrade'] is False
        assert _decision_for(decisions, 'TT_Home_Over')['downgrade'] is False
        assert report['downgrades'] == []


class TestGenuinelyIndependentBetsRemainEligible:

    def test_ml_away_and_nrfi_are_not_correlated_and_both_stay_eligible(self, rg):
        """ML_Away (full-game win thesis) and NRFI (first-inning scoreless
        thesis) are not connected by any CORRELATION_RULES entry --
        genuinely independent +EV positions must survive untouched."""
        ml = make_entry(market='ML_Away', edge=4.0, stake=2.0, ticker='ML')
        nrfi = make_entry(market='NRFI', edge=4.0, stake=2.0, ticker='N')
        entries = [('A@B', ml), ('A@B', nrfi)]
        decisions, report = rg.evaluate_correlation_gate(entries)
        ml_d = _decision_for(decisions, 'ML_Away')
        nrfi_d = _decision_for(decisions, 'NRFI')
        assert ml_d['downgrade'] is False
        assert nrfi_d['downgrade'] is False
        assert ml_d['correlationGroups'] == []
        assert nrfi_d['correlationGroups'] == []
        assert report['clusters'] == []


# ══════════════════════════════════════════════════════════════════════════════
# Default max 2 actionable (real-money) bets per game
# ══════════════════════════════════════════════════════════════════════════════

class TestGameConcentrationCap:

    def test_three_mutually_uncorrelated_bets_one_game_lowest_edge_downgraded(self, rg):
        """ML_Away/NRFI/TT_Home_Over are pairwise independent per
        CORRELATION_RULES, isolating the raw per-game COUNT cap from any
        cluster-stake or thesis-dedup interaction."""
        ml   = make_entry(market='ML_Away', edge=5.0, stake=2.0, ticker='ML')
        nrfi = make_entry(market='NRFI', edge=4.0, stake=2.0, ticker='N')
        tt   = make_tt_entry(side='Home', edge=3.0, stake=2.0, ticker='TT')
        entries = [('A@B', ml), ('A@B', nrfi), ('A@B', tt)]
        decisions, report = rg.evaluate_correlation_gate(entries)

        assert _decision_for(decisions, 'ML_Away')['downgrade'] is False
        assert _decision_for(decisions, 'NRFI')['downgrade'] is False
        tt_d = _decision_for(decisions, 'TT_Home_Over')
        assert tt_d['downgrade'] is True
        assert 'GAME_CONCENTRATION_CAP' in tt_d['downgradeReason']
        assert any('GAME_CONCENTRATION_CAP' in w for w in report['warnings'])

    def test_default_cap_is_two(self, rg):
        assert rg.GAME_MAX_REAL_MONEY_BETS == 2


# ══════════════════════════════════════════════════════════════════════════════
# Target cap: ~15% of proposed daily allocation in one correlated cluster
# ══════════════════════════════════════════════════════════════════════════════

class TestClusterStakeCap:

    def test_default_cap_is_fifteen_percent(self, rg):
        assert rg.GAME_CLUSTER_MAX_STAKE_PCT == 0.15

    def test_cluster_over_fifteen_percent_trims_lowest_edge_member(self, rg):
        # Cluster (NRFI + F5_ML_Away, PITCHER_DEPENDENT) stake = 4u.
        nrfi = make_entry(market='NRFI', edge=5.0, stake=2.0, ticker='N')
        f5   = make_entry(market='F5_ML_Away', edge=3.0, stake=2.0, ticker='F5')
        # Four unrelated 4u bets in separate games bring total daily
        # allocation to 20u, so the 4u cluster is 20% > the 15% target.
        fillers = [
            (f'G{i}@H{i}', make_entry(market='ML_Away', edge=4.0, stake=4.0, ticker=f'FILL{i}'))
            for i in range(4)
        ]
        entries = [('A@B', nrfi), ('A@B', f5)] + fillers
        decisions, report = rg.evaluate_correlation_gate(entries)

        assert report['total_stake_basis'] == pytest.approx(20.0)
        nrfi_d = _decision_for(decisions, 'NRFI')
        f5_d = _decision_for(decisions, 'F5_ML_Away')
        assert nrfi_d['downgrade'] is False, "higher-edge cluster member must be kept"
        assert f5_d['downgrade'] is True
        assert 'CLUSTER_STAKE_CAP' in f5_d['downgradeReason']
        filler_decisions = [d for d in decisions if d['entry'].get('ticker', '').startswith('FILL')]
        assert len(filler_decisions) == 4
        assert all(d['downgrade'] is False and d['entry']['betSize'] == 4.0 for d in filler_decisions), \
            "unrelated filler bets in other games must be untouched"

    def test_cluster_under_fifteen_percent_untouched(self, rg):
        nrfi = make_entry(market='NRFI', edge=5.0, stake=1.0, ticker='N')
        f5   = make_entry(market='F5_ML_Away', edge=3.0, stake=1.0, ticker='F5')
        fillers = [
            (f'G{i}@H{i}', make_entry(market='ML_Away', edge=4.0, stake=4.0, ticker=f'FILL{i}'))
            for i in range(4)
        ]
        entries = [('A@B', nrfi), ('A@B', f5)] + fillers
        decisions, report = rg.evaluate_correlation_gate(entries)
        # Cluster stake = 2u, total = 18u -> 11% < 15%, no trim.
        assert _decision_for(decisions, 'NRFI')['downgrade'] is False
        assert _decision_for(decisions, 'F5_ML_Away')['downgrade'] is False

    def test_single_position_alone_over_cap_is_kept_not_zeroed(self, rg):
        """Target cap, not a hard rule -- even a lone correlated bet whose
        own stake alone exceeds the cap must never be forced to zero."""
        nrfi = make_entry(market='NRFI', edge=5.0, stake=10.0, ticker='N')
        f5   = make_entry(market='F5_ML_Away', edge=3.0, stake=10.0, ticker='F5')
        entries = [('A@B', nrfi), ('A@B', f5)]  # total_stake = 20u, cluster = 20u = 100%
        decisions, report = rg.evaluate_correlation_gate(entries)
        assert _decision_for(decisions, 'NRFI')['downgrade'] is False


# ══════════════════════════════════════════════════════════════════════════════
# apply_correlation_gate() integration: mutates the slate the same way
# every other gate in this file does
# ══════════════════════════════════════════════════════════════════════════════

class TestApplyCorrelationGateIntegration:

    def test_downgraded_entry_papered_with_block_reason_and_gate_fired(self, rg):
        ml = make_entry(market='ML_Away', edge=3.0, stake=3.0, ticker='ML')
        f5 = make_entry(market='F5_ML_Away', edge=5.0, stake=3.0, ticker='F5')
        slate = make_slate([make_game('A', 'B', [ml, f5])])
        rg.apply_correlation_gate(slate, now_ts=NOW)

        assert ml['confidenceTier'] == 'PAPER'
        assert ml['confidence'] == 'PAPER'
        assert ml['betSize'] == 1.0
        assert ml['realMoneyBlocked'] is True
        assert 'CORRELATION_DUPLICATE_THESIS' in ml['blockReason']
        assert any('CORRELATION_DUPLICATE_THESIS' in g for g in ml['gatesFired'])
        # The kept entry is untouched.
        assert f5['confidenceTier'] == 'HIGH'
        assert f5['betSize'] == 3.0

    def test_correlation_groups_attached_to_every_real_money_entry(self, rg):
        ml = make_entry(market='ML_Away', edge=4.0, stake=2.0, ticker='ML')
        nrfi = make_entry(market='NRFI', edge=4.0, stake=2.0, ticker='N')
        slate = make_slate([make_game('A', 'B', [ml, nrfi])])
        rg.apply_correlation_gate(slate, now_ts=NOW)
        assert ml['correlationGroups'] == []
        assert nrfi['correlationGroups'] == []

    def test_non_real_money_entries_never_touched(self, rg):
        paper = make_entry(market='ML_Away', tier='PAPER', edge=4.0, stake=1.0, ticker='P')
        rejected = make_entry(market='NRFI', status='Rejected', tier='HIGH', edge=4.0, ticker='R')
        slate = make_slate([make_game('A', 'B', [paper, rejected])])
        rg.apply_correlation_gate(slate, now_ts=NOW)
        assert 'correlationGroups' not in paper
        assert 'correlationGroups' not in rejected

    def test_report_shape(self, rg):
        ml = make_entry(market='ML_Away', edge=3.0, stake=3.0, ticker='ML')
        f5 = make_entry(market='F5_ML_Away', edge=5.0, stake=3.0, ticker='F5')
        slate = make_slate([make_game('A', 'B', [ml, f5])])
        report = rg.apply_correlation_gate(slate, now_ts=NOW)
        assert set(report.keys()) == {'warnings', 'downgrades', 'clusters', 'total_stake_basis'}
        assert len(report['downgrades']) == 1
        assert report['downgrades'][0]['market'] == 'ML_Away'
