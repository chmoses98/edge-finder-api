#!/usr/bin/env python3
"""
tests/test_risk_gate_object_ownership_rerun_livebet.py
==========================================================
Phase 7 Parts 13-16 for scripts/risk_gate.py:

Part 13 -- object ownership: no mutation, no aliasing, no cross-candidate
contamination, newly-owned returned decisions, deterministic reruns,
explicit (not hidden-global) portfolio accumulation.

Part 14 -- rerun/idempotency audit: identical second run is a stable
fixed point; the execution artifact and meta.json's risk_gate key are
fully OVERWRITTEN (not merged/appended/compounded) on rerun; changed
input (price/status/added/removed/reordered recommendations) is
re-evaluated fresh each time with no hidden memory of a prior run.
Documented, not "fixed" into a different (true-idempotent) semantics.

Part 15 -- duplicate/correlation: risk_gate.py has NO same-team/opposing-
side/market-overlap/NRFI-YRFI-conflict logic (grep-verified in Part 8-9's
test file); its only "stateful" concept is the plain TT/ML_F5/OTHER
family tally. A literal duplicate marketLedger entry (same market string
twice in one game -- should never happen upstream, but not rejected here)
is simply tallied twice into the same family bucket, with no special
handling; this is proven explicitly below.

Part 16 -- live-bet/time-gate safety: every game-status/time gate is
exercised with injected deterministic timestamps (the `now_ts` parameter
both apply_tt_safety/apply_portfolio_rules already accept), including
the check_game_status() Signal-2 timestamp fallback (a game still
reporting a pregame-looking status whose scheduled start has already
passed) -- proving the refactor never newly approves a live/already-
started wager.
"""

import copy
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


def _wire(rg, tmp_path):
    slate_path = str(tmp_path / 'slate.json')
    meta_path = str(tmp_path / 'meta.json')
    rg.SLATE_PATH = slate_path
    rg.META_PATH = meta_path
    return slate_path, meta_path


class TestObjectOwnershipIdentity:

    def test_compute_tt_inputs_returns_a_new_dict_not_aliased_to_entry(self, rg):
        entry = make_tt_entry(tier='HIGH', edge=4.0)
        result = rg.compute_tt_inputs(entry)
        result['requiredRunsToWin'] = 999  # mutate the RETURNED dict
        assert 'ttInputs' not in entry  # entry itself was never touched
        result2 = rg.compute_tt_inputs(entry)
        assert result2['requiredRunsToWin'] != 999  # fresh, newly-owned each call

    def test_evaluate_candidate_tt_risk_reasons_list_is_newly_owned(self, rg):
        entry = make_tt_entry(tier='HIGH', edge=1.0)
        d1 = rg.evaluate_candidate_tt_risk(entry)
        d1['reasons'].append('INJECTED')
        d2 = rg.evaluate_candidate_tt_risk(entry)
        assert 'INJECTED' not in d2['reasons']  # d1's mutation didn't leak into a shared list

    def test_no_cross_candidate_contamination_in_tt_pass(self, rg):
        """Evaluating one TT candidate must not affect another candidate's
        independently-computed decision, even when both are in the same
        game's marketLedger."""
        bad = make_tt_entry(side='Away', tier='HIGH', edge=1.0, ticker='BAD')
        good = make_tt_entry(side='Home', tier='HIGH', edge=4.0, ticker='GOOD')
        slate = make_slate([make_game('A', 'B', [bad, good])])
        rg.apply_tt_safety(slate, now_ts=NOW)
        assert bad['confidenceTier'] == 'PAPER'
        assert good['confidenceTier'] == 'HIGH'

    def test_build_risk_portfolio_accumulation_is_explicit_not_hidden_global(self, rg):
        """Two independent calls to build_risk_portfolio() with fresh
        entries must not share any accumulated state -- each call's
        fam_map/report starts from zero, proving the family tally is
        built fresh from the argument each time, never from a
        module-level accumulator."""
        entries_1 = [('A@B', make_entry(market='ML_Away', stake=5.0))]
        entries_2 = [('C@D', make_entry(market='ML_Away', stake=7.0))]
        report_1, _, _ = rg.build_risk_portfolio(entries_1)
        report_2, _, _ = rg.build_risk_portfolio(entries_2)
        assert report_1['total_real_stake'] == 5.0
        assert report_2['total_real_stake'] == 7.0  # not 12.0 -- no shared accumulator

    def test_returned_report_dicts_are_independent_objects_across_calls(self, rg):
        entries = [('A@B', make_entry(market='ML_Away', stake=5.0))]
        report_1, _, _ = rg.build_risk_portfolio(entries)
        report_2, _, _ = rg.build_risk_portfolio(entries)
        assert report_1 == report_2
        assert report_1 is not report_2
        report_1['total_real_stake'] = 999.0
        assert report_2['total_real_stake'] == 5.0


class TestRerunIdempotencyAudit:

    def test_execution_artifact_is_overwritten_not_merged_on_rerun(self, rg, tmp_path):
        slate_path, meta_path = _wire(rg, tmp_path)
        import json
        entry1 = make_entry(market='ML_Away', ticker='FIRST_RUN')
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry1])]), f)
        rg.main()
        first_envelope = pa.read_stage_artifact('execution', '2026-06-16')
        assert len(first_envelope['data']['candidates']) == 1
        assert first_envelope['data']['candidates'][0]['sourceRecommendationTicker'] == 'FIRST_RUN'

        # Second run against a DIFFERENT slate (simulating a same-day
        # rerun after new data arrived) -- the artifact must reflect
        # ONLY the second run's candidates, not both runs' combined.
        entry2a = make_entry(market='ML_Away', ticker='SECOND_RUN_A')
        entry2b = make_entry(market='ML_Home', ticker='SECOND_RUN_B')
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry2a, entry2b])]), f)
        rg.main()
        second_envelope = pa.read_stage_artifact('execution', '2026-06-16')
        tickers = [c['sourceRecommendationTicker'] for c in second_envelope['data']['candidates']]
        assert tickers == ['SECOND_RUN_A', 'SECOND_RUN_B']
        assert 'FIRST_RUN' not in tickers

    def test_meta_json_risk_gate_key_overwritten_not_compounded_on_rerun(self, rg, tmp_path):
        import json
        slate_path, meta_path = _wire(rg, tmp_path)
        entry = make_entry(market='ML_Away')
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)
        rg.main()
        with open(meta_path) as f:
            meta_after_first = json.load(f)
        rg.main()
        with open(meta_path) as f:
            meta_after_second = json.load(f)
        # A single risk_gate key, not a list/history of runs.
        assert isinstance(meta_after_second['risk_gate'], dict)
        assert meta_after_second['risk_gate']['total_bets'] == meta_after_first['risk_gate']['total_bets']

    def test_rerun_after_price_or_status_change_is_freshly_reevaluated(self, rg):
        """
        risk_gate.py has no memory across runs beyond what's baked into
        the slate.json fields themselves -- if betSize/edge/tier/status
        change between runs (as would happen if an upstream script
        re-ran build_market_ledger.py in between), the SAME candidate
        entry is freely re-evaluated with the new values, not "locked in"
        from a prior run's decision.
        """
        entry = make_tt_entry(tier='HIGH', edge=4.0)  # passes first time
        slate = make_slate([make_game('A', 'B', [entry])])
        rg.apply_tt_safety(slate, now_ts=NOW)
        assert entry['confidenceTier'] == 'HIGH'

        # Simulate an upstream re-evaluation lowering the edge below 2.5%.
        entry['edge'] = 1.0
        entry['calibratedEdgeVsExecutable'] = 1.0
        rg.apply_tt_safety(slate, now_ts=NOW)
        assert entry['confidenceTier'] == 'PAPER'

    def test_rerun_with_added_recommendation_evaluates_it_fresh(self, rg):
        e1 = make_tt_entry(tier='HIGH', edge=4.0, ticker='E1')
        slate = make_slate([make_game('A', 'B', [e1])])
        rg.apply_tt_safety(slate, now_ts=NOW)
        first_downgrades = rg.apply_tt_safety(slate, now_ts=NOW)  # 2nd call, no new entries
        assert first_downgrades == []

        e2 = make_tt_entry(tier='HIGH', edge=1.0, ticker='E2')  # newly added
        slate['games'][0]['marketLedger'].append(e2)
        downgrades = rg.apply_tt_safety(slate, now_ts=NOW)
        assert len(downgrades) == 1
        assert downgrades[0]['market'] == 'TT_Away_Over'
        assert e2['confidenceTier'] == 'PAPER'
        assert e1['confidenceTier'] == 'HIGH'  # unaffected by the new sibling entry

    def test_rerun_with_reordered_recommendations_produces_same_final_state(self, rg):
        """Order of candidates within a game's marketLedger must not
        affect each INDIVIDUAL candidate's TT-safety outcome (only the
        portfolio pass's TT_MAX_BETS tie-breaking is order-sensitive, and
        only among ties -- see test_risk_gate_rule_order.py)."""
        e1 = make_tt_entry(side='Away', tier='HIGH', edge=4.0, ticker='E1')
        e2 = make_tt_entry(side='Home', tier='HIGH', edge=1.0, ticker='E2')
        slate_a = make_slate([make_game('A', 'B', [e1, e2])])
        slate_b = make_slate([make_game('A', 'B', [copy.deepcopy(e2), copy.deepcopy(e1)])])
        rg.apply_tt_safety(slate_a, now_ts=NOW)
        rg.apply_tt_safety(slate_b, now_ts=NOW)
        tiers_a = {e['ticker']: e['confidenceTier'] for e in slate_a['games'][0]['marketLedger']}
        tiers_b = {e['ticker']: e['confidenceTier'] for e in slate_b['games'][0]['marketLedger']}
        assert tiers_a == tiers_b == {'E1': 'HIGH', 'E2': 'PAPER'}


class TestDuplicateMarketDocumentedAbsence:

    def test_literal_duplicate_market_entries_tallied_twice_no_special_casing(self, rg):
        """
        Two marketLedger rows sharing the EXACT SAME market string in the
        same game (which should never occur upstream, since
        build_market_ledger.py writes exactly one row per REQUIRED_MARKETS
        entry per game) are simply counted twice into the same family
        bucket -- risk_gate.py has no duplicate-market detection to
        collapse, dedupe, or flag this.
        """
        e1 = make_entry(market='ML_Away', stake=5.0, ticker='DUP1')
        e2 = make_entry(market='ML_Away', stake=5.0, ticker='DUP2')  # same market, different ticker
        entries = [('A@B', e1), ('A@B', e2)]
        report, to_downgrade, decision = rg.build_risk_portfolio(entries)
        assert report['ml_f5_bets'] == 2
        assert report['ml_f5_stake'] == 10.0
        assert report['by_family']['ML_F5']['bets'] == 2


class TestLiveBetTimeGateSafety:

    @pytest.mark.parametrize("status", ["Postponed", "Cancelled", "Suspended", "In Progress", "Final"])
    def test_every_non_pregame_status_blocks_real_money_in_both_passes(self, rg, status):
        entry = make_tt_entry(tier='HIGH', edge=4.0)
        slate = make_slate([make_game('A', 'B', [entry], status=status)])
        tt_downgrades = rg.apply_tt_safety(slate, now_ts=NOW)
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert tt_downgrades == []  # not even reached/enriched
        assert report['total_bets'] == 0  # never counted as real-money real_entries

    def _game_with_scheduled_start(self, entries, scheduled_start):
        """
        make_game()'s `start_time=` parameter sets the key 'startTime',
        which check_game_status() never reads (it looks for
        'scheduledStartTime'/'gameTime'/'firstPitch'/'scheduledStart') --
        confirmed by reading lib/postponed_guard.py directly. That
        mismatch means make_game()'s start_time is inert for shouldSkip
        purposes everywhere else in this test suite (harmlessly, since
        NOW is already chosen before every fixture's default 'startTime'
        value) but must NOT be relied on to exercise Signal 2 here --
        this helper sets the field check_game_status() actually reads,
        without touching the widely-shared make_game() helper itself.
        """
        game = make_game('A', 'B', entries, status='Scheduled')
        game['scheduledStartTime'] = scheduled_start
        return game

    def test_scheduled_status_but_start_time_already_passed_blocked_via_timestamp_fallback(self, rg):
        """
        check_game_status()'s Signal 2: a game still reporting a
        pregame-looking status (e.g. 'Scheduled') whose scheduled start
        time has ALREADY passed relative to now_ts must still be
        skipped -- this is the fallback that prevents a delayed status
        update from ever letting a live game's bet through.
        """
        entry = make_tt_entry(tier='HIGH', edge=4.0)
        game = self._game_with_scheduled_start([entry], '2026-06-16T19:00:00Z')  # 1hr before NOW
        slate = make_slate([game])
        tt_downgrades = rg.apply_tt_safety(slate, now_ts=NOW)
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert tt_downgrades == []
        assert report['total_bets'] == 0

    def test_scheduled_status_with_future_start_time_is_not_blocked(self, rg):
        entry = make_tt_entry(tier='HIGH', edge=4.0)
        game = self._game_with_scheduled_start([entry], '2026-06-16T23:00:00Z')  # 3hr after NOW
        slate = make_slate([game])
        rg.apply_tt_safety(slate, now_ts=NOW)
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert entry['confidenceTier'] == 'HIGH'
        assert report['total_bets'] == 1

    def test_timezone_boundary_utc_z_suffix_respected_exactly(self, rg):
        """now_ts and scheduledStartTime are both ISO-8601 UTC ('Z'
        suffix) -- a start time exactly equal to now_ts must be treated
        as already started (`now > fp` in check_first_pitch_passed uses
        strict `>`, so an EXACT match is NOT yet past -- verified against
        lib/postponed_guard.py's own comparison directly, not assumed)."""
        entry = make_tt_entry(tier='HIGH', edge=4.0)
        game = self._game_with_scheduled_start([entry], NOW)
        slate = make_slate([game])
        rg.apply_tt_safety(slate, now_ts=NOW)
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        # now == scheduled start exactly -- check_first_pitch_passed uses
        # strict `now > fp`, so this is NOT yet blocked by Signal 2.
        assert report['total_bets'] == 1
        assert entry['confidenceTier'] == 'HIGH'

    def test_timezone_boundary_one_second_after_start_is_blocked(self, rg):
        entry = make_tt_entry(tier='HIGH', edge=4.0)
        game = self._game_with_scheduled_start([entry], '2026-06-16T19:59:59Z')
        slate = make_slate([game])
        rg.apply_tt_safety(slate, now_ts=NOW)  # NOW = '2026-06-16T20:00:00Z'
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['total_bets'] == 0

    def test_refactored_functions_never_newly_approve_a_live_game_bet(self, rg):
        """
        End-to-end (both passes, main()'s own order) proof that a live
        ('In Progress') game with an otherwise-perfectly-eligible HIGH
        tier, good-edge TT candidate is NEVER approved for real money by
        either pass, regardless of how attractive the bet looks on paper.
        """
        entry = make_tt_entry(tier='HIGH', edge=10.0, stake=5.0)  # would easily pass every rule
        game = make_game('A', 'B', [entry], status='In Progress')
        slate = make_slate([game])
        rg.apply_tt_safety(slate, now_ts=NOW)
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert entry['confidenceTier'] == 'HIGH'  # untouched -- but never counted as real-money output
        assert report['total_bets'] == 0
        assert report['total_real_stake'] == 0.0
        assert decision == 'GO'  # zero real-money bets, no TT-only/hard-block triggers
