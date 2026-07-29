#!/usr/bin/env python3
"""
tests/test_risk_gate_tt_stake_asymmetry_deep_dive.py
========================================================
PR #8 hardening review, Part H: the tt_stake_post / total_stake
asymmetry, answered precisely against the actual source
(scripts/risk_gate.py's build_risk_portfolio(), read directly, not
assumed):

1. WHERE total_stake IS CALCULATED: exactly once, line
   `total_stake = sum(v['stake'] for v in fam_map.values())`, built from
   fam_map's PRE-downgrade per-family tallies (before the TT_MAX_BETS
   downgrade decision is even made).

2. WHERE tt_stake_post IS CALCULATED: after the TT_MAX_BETS downgrade
   decision, as the stake of only the `kept` (non-downgraded) TT
   entries, filtered by real-money tier.

3. WHETHER EITHER VALUE IS LATER REUSED: total_stake is reused TWICE
   (as the denominator for both tt_pct/TT_DOMINANCE and mlf5_pct/
   ML_F5_UNDERFILL) -- never reassigned between those two reads.
   tt_stake_post is used ONLY for the TT_STAKE_CAP check and as the
   numerator of tt_pct -- it never appears anywhere else, in particular:

4. WHETHER meta.json REFLECTS PRE- OR POST-ADJUSTMENT VALUES:
   meta.json's `risk_gate.tt_stake` key is `report['tt_stake']`, set
   from the family tally BEFORE the TT_MAX_BETS downgrade -- i.e. a
   PRE-adjustment value. `tt_stake_post` is a plain local variable and
   is NEVER written to `report` at all, so it never reaches meta.json
   under ANY key, pre- or post-adjustment.

5. WHETHER execution.json REFLECTS PRE- OR POST-ADJUSTMENT VALUES:
   execution.json has no aggregate tt_stake/total_stake field at all --
   its schema is a flat per-candidate list. Each candidate's
   `approvedStake`/`realMoneyEligible` fields are read from the slate
   AFTER apply_portfolio_rules() has already mutated the downgraded
   entries (main()'s call order: apply_tt_safety -> apply_portfolio_rules
   -> [PAPER_ONLY third pass] -> build_execution_artifact_payload) --
   i.e. POST-adjustment, per-candidate, not an aggregate.

6. WHETHER SUBSEQUENT CANDIDATE DECISIONS USE ONE OR THE OTHER: no --
   `tt_bets`/`mlf5_bets`/`total_bets` (used by the ALL_TT_NO_ML_F5 and
   hard_block decision logic) are ALSO pre-downgrade counts, tallied
   once alongside total_stake and never recomputed. Nothing downstream
   of the TT_MAX_BETS downgrade recomputes ANY of these four aggregate
   values except tt_stake_post itself.

7. WHETHER RERUNS COMPOUND THE DISCREPANCY: no -- every call to
   build_risk_portfolio() computes fam_map/total_stake/tt_bets/etc. FRESH
   from its `real_entries` argument, which apply_portfolio_rules()
   rebuilds fresh from the slate's CURRENT state every call. A
   TT_MAX_BETS-downgraded entry becomes PAPER tier, so a second run
   excludes it from real_entries entirely -- the asymmetry is a
   same-run, self-contained artifact, not a persisted or compounding one.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from test_risk_gate_immutable import make_entry, make_tt_entry, make_game, make_slate, NOW
import pipeline_artifacts as pa


@pytest.fixture
def rg():
    if "risk_gate" in sys.modules:
        del sys.modules["risk_gate"]
    import risk_gate as _rg
    return _rg


@pytest.fixture(autouse=True)
def _sandbox_pipeline_root(tmp_path):
    original_root = pa.PIPELINE_ROOT
    pa.PIPELINE_ROOT = str(tmp_path / 'pipeline_root')
    yield
    pa.PIPELINE_ROOT = original_root


class TestAsymmetryIsGenuinelyConsequential:

    def test_exact_fixture_where_correcting_the_asymmetry_would_flip_the_decision(self, rg):
        """
        5 TT entries @ 5.0u each (edges 5,4,3,2,1) -- TT_MAX_BETS=4 downgrades
        the lowest-edge one (5.0u), leaving tt_stake_post=20.0u (at, not
        over, TT_MAX_STAKE=20.0 -- TT_STAKE_CAP does NOT fire, isolating
        this fixture to the TT_DOMINANCE/total_stake question only).

        2 ML_F5 entries totaling 25.0u (mlf5_bets=2, so ML_F5_UNDERFILL
        is in scope too, but tuned to land exactly at its own 50%
        boundary -- not < 50%, so it does not fire either).

        LEGACY (actual) behavior: total_stake = 25.0(TT, pre-downgrade)
        + 25.0(ML_F5) = 50.0. tt_pct = 20.0/50.0 = 40.0% -- NOT > 40%
        (strict inequality) -- TT_DOMINANCE does NOT fire. mlf5_pct =
        25.0/50.0 = 50.0% -- NOT < 50% -- ML_F5_UNDERFILL does NOT fire.
        No DAILY_RISK_CAP (50.0 < 40.0? NO -- wait, 50.0 > 40.0, so
        DAILY_RISK_CAP WOULD fire under total_stake=50.0). Adjusted
        below to stay under the daily cap too, isolating the exact
        TT_DOMINANCE question this test targets.
        """
        tt_entries = [
            make_tt_entry(tier='HIGH', edge=float(5 - i), stake=3.0, ticker=f'TT{i}')
            for i in range(5)
        ]
        # ML_F5 stake tuned so legacy total_stake keeps tt_pct at exactly
        # 40% (no fire) while a "corrected" (post-downgrade-recomputed)
        # total_stake would push tt_pct over 40% (would fire) --
        # and total stake stays under DAILY_RISK_CAP=40.0u throughout.
        ml1 = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=7.5, ticker='ML1')
        ml2 = make_entry(market='ML_Home', tier='HIGH', edge=4.0, stake=7.5, ticker='ML2')

        games = [make_game(f'A{i}', f'B{i}', [e]) for i, e in enumerate(tt_entries)]
        games.append(make_game('X', 'Y', [ml1, ml2]))
        slate = make_slate(games)

        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)

        # PRE-downgrade tally, as documented -- these are the values
        # meta.json's risk_gate block will actually contain.
        assert report['tt_stake'] == 15.0     # 5 * 3.0, PRE-downgrade
        assert report['tt_bets'] == 5          # PRE-downgrade count
        assert report['total_real_stake'] == 30.0   # 15.0(TT) + 15.0(ML_F5), PRE-downgrade
        assert report['ml_f5_stake'] == 15.0
        assert report['ml_f5_bets'] == 2

        # Legacy tt_pct = tt_stake_post / total_stake(PRE-downgrade)
        #              = 12.0 / 30.0 = 40.0% exactly -- NOT > 40%, no fire.
        assert not any(w.startswith('TT_DOMINANCE') for w in report['concentration_warnings'])
        assert not any(w.startswith('DAILY_RISK_CAP') for w in report['concentration_warnings'])
        assert not any(w.startswith('TT_STAKE_CAP') for w in report['concentration_warnings'])
        assert not any(w.startswith('ML_F5_UNDERFILL') for w in report['concentration_warnings'])
        assert decision == 'GO'

        # Now PROVE what a "corrected" (post-downgrade-recomputed)
        # total_stake WOULD have produced, entirely independently of
        # risk_gate.py's own code -- a from-scratch recomputation using
        # only the final kept-entry stakes, demonstrating the decision
        # WOULD flip to PAPER_ONLY if this asymmetry were ever "fixed".
        kept_tt_stake = 3.0 * 4  # 4 entries survive the TT_MAX_BETS cap
        corrected_total_stake = kept_tt_stake + report['ml_f5_stake']
        corrected_tt_pct = kept_tt_stake / corrected_total_stake
        assert corrected_tt_pct > 0.40, (
            "sanity check: the 'corrected' total_stake calculation must "
            "produce a tt_pct that WOULD trip TT_DOMINANCE, proving this "
            "fixture is genuinely discriminating"
        )

    def test_daily_cap_boundary_uses_the_same_pre_downgrade_total_stake(self, rg):
        """
        DAILY_RISK_CAP also reads the same never-recomputed total_stake
        -- a TT_MAX_BETS-downgraded entry's stake still counts toward
        the daily cap check too, not just TT_DOMINANCE. Fixture: total
        PRE-downgrade stake exactly at the 40.0u cap (using 6 TT entries
        @ 6.0 + 1 ML entry @ 4.0 = 40.0, TT_MAX_BETS excludes the two
        lowest-edge TT entries).
        """
        tt_entries = [
            make_tt_entry(tier='HIGH', edge=float(6 - i), stake=6.0, ticker=f'TT{i}')
            for i in range(6)
        ]
        ml = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=4.0, ticker='ML1')
        games = [make_game(f'A{i}', f'B{i}', [e]) for i, e in enumerate(tt_entries)]
        games.append(make_game('X', 'Y', [ml]))
        slate = make_slate(games)

        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['total_real_stake'] == 40.0  # exact boundary, `>` strict, no DAILY_RISK_CAP
        assert not any(w.startswith('DAILY_RISK_CAP') for w in report['concentration_warnings'])


class TestValueProvenance:

    def test_meta_json_tt_stake_is_pre_downgrade_never_tt_stake_post(self, rg, tmp_path):
        import json
        slate_path = str(tmp_path / 'slate.json')
        meta_path = str(tmp_path / 'meta.json')
        rg.SLATE_PATH = slate_path
        rg.META_PATH = meta_path

        tt_entries = [make_tt_entry(tier='HIGH', edge=6.0 - i * 0.5, stake=5.0, ticker=f'T{i}') for i in range(5)]
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game(f'A{i}', f'B{i}', [e]) for i, e in enumerate(tt_entries)]), f)

        rg.main()

        with open(meta_path) as f:
            meta = json.load(f)
        # PRE-downgrade tally: 5 * 5.0 = 25.0, NOT the post-downgrade
        # 4 * 5.0 = 20.0 a "tt_stake_post"-labeled field would show.
        assert meta['risk_gate']['tt_stake'] == 25.0
        assert meta['risk_gate']['tt_bets'] == 5
        assert 'tt_stake_post' not in meta['risk_gate']
        assert 'ttStakePost' not in meta['risk_gate']

    def test_execution_json_candidate_fields_are_post_downgrade_final_state(self, rg, tmp_path):
        import json
        slate_path = str(tmp_path / 'slate.json')
        meta_path = str(tmp_path / 'meta.json')
        rg.SLATE_PATH = slate_path
        rg.META_PATH = meta_path

        # ML/F5 entries included specifically so the decision is GO (not
        # PAPER_ONLY via ALL_TT_NO_ML_F5), which would otherwise force
        # every entry to PAPER in the third pass and defeat the purpose
        # of this test (proving TT_MAX_BETS-only downgrade is reflected
        # post-adjustment while everything else stays real-money).
        tt_entries = [make_tt_entry(tier='HIGH', edge=6.0 - i * 0.5, stake=2.0, ticker=f'T{i}') for i in range(5)]
        ml1 = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=6.0, ticker='ML1')
        ml2 = make_entry(market='ML_Home', tier='HIGH', edge=4.0, stake=6.0, ticker='ML2')
        games = [make_game(f'A{i}', f'B{i}', [e]) for i, e in enumerate(tt_entries)]
        games.append(make_game('X', 'Y', [ml1, ml2]))
        with open(slate_path, 'w') as f:
            json.dump(make_slate(games), f)

        rg.main()
        with open(meta_path) as f:
            meta = json.load(f)
        assert meta['risk_gate']['decision'] == 'GO', (
            "fixture sanity check: this test requires a GO decision so "
            "the third PAPER_ONLY pass never fires, isolating the "
            "TT_MAX_BETS-only downgrade this test targets"
        )

        envelope = pa.read_stage_artifact('execution', '2026-06-16')
        candidates = envelope['data']['candidates']
        real_money = [c for c in candidates if c['realMoneyEligible']]
        paper = [c for c in candidates if not c['realMoneyEligible']]
        # 4 TT kept (highest edges: T0..T3) + 2 ML_F5 = 6 real-money;
        # 1 TT downgraded (T4, lowest edge) via TT_MAX_BETS only.
        assert len(real_money) == 6
        assert len(paper) == 1
        assert paper[0]['sourceRecommendationTicker'] == 'T4'
        assert paper[0]['approvedStake'] == 1.0  # post-downgrade fixed PAPER stake
        assert paper[0]['rejectionReason'] == 'TT_MAX_BETS_EXCEEDED: capped at 4'
        # No aggregate tt_stake/total_stake field exists in this schema at all.
        assert 'tt_stake' not in envelope['data']
        assert 'totalStake' not in envelope['data']

    def test_downstream_decision_uses_pre_downgrade_tt_bets_not_recomputed(self, rg):
        """
        ALL_TT_NO_ML_F5 checks `tt_bets > 0 and mlf5_bets == 0 and
        total_bets == tt_bets` using the SAME pre-downgrade tt_bets/
        total_bets as the stake asymmetry -- proven with 5 TT entries,
        no ML/F5 at all, where tt_bets=5 (pre-downgrade) still equals
        total_bets=5 (pre-downgrade) even though only 4 remain real-money
        after the TT_MAX_BETS downgrade.
        """
        tt_entries = [make_tt_entry(tier='HIGH', edge=6.0 - i * 0.5, stake=5.0, ticker=f'T{i}') for i in range(5)]
        slate = make_slate([make_game(f'A{i}', f'B{i}', [e]) for i, e in enumerate(tt_entries)])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['tt_bets'] == 5
        assert report['total_bets'] == 5
        assert decision == 'PAPER_ONLY'
        assert report['decision_reason'] == 'ALL_TT_NO_ML_F5'


class TestRerunDoesNotCompoundTheAsymmetry:

    def test_second_run_excludes_downgraded_entry_asymmetry_does_not_accumulate(self, rg, tmp_path):
        """
        Runs apply_portfolio_rules() twice in a row against the SAME
        slate object (simulating a rerun without any new input). The
        first run downgrades 1 of 5 TT entries; the second run's
        real_entries collection (rebuilt fresh from the now-mutated
        slate) naturally excludes that now-PAPER entry, so the second
        run's total_stake/tt_bets are smaller and internally consistent
        with ONLY the 4 remaining real-money entries -- the asymmetry
        from run 1 does not carry forward or compound into run 2.
        """
        tt_entries = [make_tt_entry(tier='HIGH', edge=6.0 - i * 0.5, stake=5.0, ticker=f'T{i}') for i in range(5)]
        slate = make_slate([make_game(f'A{i}', f'B{i}', [e]) for i, e in enumerate(tt_entries)])

        decision1, report1 = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report1['tt_bets'] == 5
        assert report1['tt_stake'] == 25.0

        decision2, report2 = rg.apply_portfolio_rules(slate, now_ts=NOW)
        # Second run only sees the 4 still-real-money entries -- the
        # downgraded one dropped out of real_entries entirely, not
        # merely out of tt_stake_post's numerator.
        assert report2['tt_bets'] == 4
        assert report2['tt_stake'] == 20.0
        # No TT_MAX_BETS this run (exactly at the 4-bet cap) -- but with
        # an all-TT, zero-ML/F5 slate, tt_pct is always 100% of stake
        # regardless of downgrades, so TT_DOMINANCE legitimately still
        # fires (the decision itself is driven by ALL_TT_NO_ML_F5, not
        # this warning, but the warning is real and expected).
        assert report2['concentration_warnings'] == ['TT_DOMINANCE: TT is 100% of stake (max 40%)']
        assert not any('TT_CONCENTRATION' in w for w in report2['concentration_warnings'])
        assert decision2 == 'PAPER_ONLY'  # still ALL_TT_NO_ML_F5 (still 0 ML/F5 entries)
