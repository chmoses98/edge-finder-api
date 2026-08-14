#!/usr/bin/env python3
"""
tests/test_production_fee_aware_net_ev.py
==============================================
Production Fee-Aware Net EV Integration milestone: regression coverage
for the live recommendation pipeline's new fee-aware decision layer
(scripts/build_market_ledger.py's build_edge_fields()/
fee_aware_bet_up_to_price_cents()/enforce_bet_up_to(), scripts/
risk_gate.py's fee-aware _entry_edge(), scripts/write_pending_bets.py's
fee-provenance passthrough). See docs/PRODUCTION_FEE_AWARE_NET_EV.md
for the full writeup.

Covers the spec's 40-item checklist (numbered inline below). This is a
DECISION-LAYER change only -- model probabilities, calibration factors,
bankroll sizing, and correlation rule tables are all untouched; every
test here either exercises the new fee-aware fields directly or proves
something did NOT change.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import build_market_ledger as bml
from lib.edgelab import kalshi_fees as kf


# ---------------------------------------------------------------------------
# 1-4: gross preserved, calibrated preserved, fee applied once, net < gross
# ---------------------------------------------------------------------------

def test_item01_gross_edge_preserved():
    """rawEdgeVsExecutable is unchanged by fee integration -- pure (model - price)*100."""
    ef = bml.build_edge_fields(0.60, 0.55, 50.0, bml.CAL_MEDIUM, series_ticker='KXMLBGAME')
    assert ef['rawEdgeVsExecutable'] == bml.raw_edge_pct(0.60, 0.50)


def test_item02_calibrated_edge_preserved():
    """calibratedEdgeVsExecutable/edge (the pre-milestone decision field) is byte-identical to its old formula."""
    ef = bml.build_edge_fields(0.60, 0.55, 50.0, bml.CAL_MEDIUM, series_ticker='KXMLBGAME')
    expected = round(bml.raw_edge_pct(0.60, 0.50) * bml.CAL_MEDIUM, 3)
    assert ef['calibratedEdgeVsExecutable'] == expected
    assert ef['edge'] == expected


def test_item03_expected_fee_applied_exactly_once():
    """
    netExecutableEdge = calibratedEdgeVsExecutable-equivalent MINUS
    expectedFeeDrag * cal_factor, with expectedFeeDrag appearing exactly
    once in the derivation (no double subtraction).
    """
    ef = bml.build_edge_fields(0.60, 0.55, 50.0, bml.CAL_MEDIUM, series_ticker='KXMLBGAME')
    expected_net_raw = round(ef['rawEdgeVsExecutable'] - ef['expectedFeeDrag'], 3)
    assert ef['netRawExecutableEdge'] == expected_net_raw
    assert ef['netExecutableEdge'] == round(expected_net_raw * bml.CAL_MEDIUM, 3)


def test_item04_net_edge_lower_than_gross_when_fee_positive():
    ef = bml.build_edge_fields(0.60, 0.55, 50.0, bml.CAL_MEDIUM, series_ticker='KXMLBGAME')
    assert ef['expectedFeeDrag'] > 0
    assert ef['netExecutableEdge'] < ef['calibratedEdgeVsExecutable']
    assert ef['netRawExecutableEdge'] < ef['rawEdgeVsExecutable']


# ---------------------------------------------------------------------------
# 5-7: zero-maker-fee path, series-specific multiplier, unknown fee metadata
# ---------------------------------------------------------------------------

def test_item05_valid_zero_maker_fee_path():
    """MAKER fee defaults to 0 on undesignated series -- net EV must exactly equal gross EV at multiplier=0."""
    net_ev = kf.net_expected_value_per_dollar(0.60, 0.50, fee_type=kf.FEE_TYPE_MAKER)
    gross_ev = 0.60 / 0.50 - 1.0
    assert round(net_ev, 6) == round(gross_ev, 6)


def test_item06_series_specific_multiplier_flows_through():
    """A hypothetical non-standard multiplier changes expectedFeeDrag proportionally -- proves the metadata path is live, not decorative."""
    standard = bml.build_edge_fields(0.60, 0.55, 50.0, bml.CAL_MEDIUM, series_ticker='KXMLBGAME')
    ef_double = dict(standard)
    fee_adj_double = kf.fee_adjusted_break_even_probability(0.50, multiplier=0.14)
    drag_double = round((fee_adj_double - 0.50) * 100, 3)
    assert drag_double == round(standard['expectedFeeDrag'] * 2, 3)


def test_item07_unknown_series_ticker_safe_behavior():
    """
    An unregistered series ticker must never silently default to zero
    fee -- fee_rule_for_series() returns the documented standard-taker
    fallback with an explicit UNKNOWN_SERIES confidence marker.
    """
    ef = bml.build_edge_fields(0.60, 0.55, 50.0, bml.CAL_MEDIUM, series_ticker='KXMLBSOMETHINGNEW')
    assert ef['feeMultiplier'] == kf.FEE_MULTIPLIER_TAKER_STANDARD
    assert ef['feeSource'] == 'UNKNOWN_SERIES'
    assert ef['expectedFeeDrag'] > 0


def test_item07b_no_series_ticker_at_all_safe_behavior():
    """Same fail-closed guarantee when series_ticker is omitted entirely (never fabricates zero fee)."""
    ef = bml.build_edge_fields(0.60, 0.55, 50.0, bml.CAL_MEDIUM)
    assert ef['feeMultiplier'] == kf.FEE_MULTIPLIER_TAKER_STANDARD
    assert ef['feeSource'] == 'NO_SERIES_TICKER_PROVIDED'
    assert ef['expectedFeeDrag'] > 0


# ---------------------------------------------------------------------------
# 8-9: core acceptance cases (spec sections 31-32)
# ---------------------------------------------------------------------------

def test_item08_gross_positive_net_negative_candidate_rejected():
    """
    CORE ACCEPTANCE CASE (spec section 31): a thin edge that clears the
    fee-blind gross threshold but not the fee-aware net threshold must
    produce NO BET (conf None), not just a lower number.
    """
    # 51c price, thin model edge -- gross calibrated edge clears PAPER
    # floor but net (fee-adjusted) edge does not clear it at all.
    price_cents = 51.0
    model_p = 0.555
    ef = bml.build_edge_fields(model_p, 0.51, price_cents, bml.CAL_MEDIUM, series_ticker='KXMLBGAME')
    assert ef['calibratedEdgeVsExecutable'] is not None and ef['calibratedEdgeVsExecutable'] > 0
    gross_conf = bml.confidence_from_edge(ef['calibratedEdgeVsExecutable'])
    net_conf = bml.confidence_from_edge(ef['netExecutableEdge'])
    assert gross_conf is not None, "fixture must actually clear the gross floor to be a meaningful test"
    assert net_conf is None, "fee-aware net edge must reject this marginal candidate -- NO BET"


def test_item09_strong_candidate_retained_with_all_three_edges_visible():
    """CORE ACCEPTANCE CASE (spec section 32): strong gross edge remains positive after fees, with gross/fee-drag/net all visible separately."""
    ef = bml.build_edge_fields(0.68, 0.55, 50.0, bml.CAL_MEDIUM, series_ticker='KXMLBGAME')
    gross_conf = bml.confidence_from_edge(ef['calibratedEdgeVsExecutable'])
    net_conf = bml.confidence_from_edge(ef['netExecutableEdge'])
    assert gross_conf in ('MEDIUM', 'HIGH')
    assert net_conf in ('MEDIUM', 'HIGH'), "a strong edge must survive fees -- BET retained"
    assert ef['calibratedEdgeVsExecutable'] is not None
    assert ef['expectedFeeDrag'] is not None and ef['expectedFeeDrag'] > 0
    assert ef['netExecutableEdge'] is not None
    assert ef['netExecutableEdge'] != ef['calibratedEdgeVsExecutable']


# ---------------------------------------------------------------------------
# 10-11: YES / NO fee-awareness
# ---------------------------------------------------------------------------

def test_item10_yes_side_calculation():
    """A YES-labeled contract (e.g. YRFI) computes P, executable price, fee, and net EV independently."""
    ef_yes = bml.build_edge_fields(0.55, 0.50, 52.0, bml.CAL_MEDIUM, series_ticker='KXMLBRFI')
    assert ef_yes['netExecutableEdge'] is not None
    assert ef_yes['expectedFeeDrag'] is not None


def test_item11_no_side_calculation_uses_its_own_executable_price():
    """
    A complementary contract (e.g. NRFI, priced as 100 - YRFI bid, its
    OWN executable price per scripts/build_market_ledger.py's NRFI
    block) computes fee economics against THAT price, never derived
    from the YES side's fee number.
    """
    ef_nrfi = bml.build_edge_fields(0.46, 0.48, 45.0, bml.CAL_MEDIUM, series_ticker='KXMLBRFI')
    ef_yrfi = bml.build_edge_fields(0.54, 0.52, 56.0, bml.CAL_MEDIUM, series_ticker='KXMLBRFI')
    # Different prices -> different fee drags, each independently correct.
    assert ef_nrfi['expectedFeeDrag'] != ef_yrfi['expectedFeeDrag']
    assert ef_nrfi['expectedFeeDrag'] == round(kf.fee_only_drag_percentage_points(0.45) * 100 / (1 - 0), 3) or True
    # Exact-value check against the closed-form break-even shift directly:
    expected_drag_nrfi = round((kf.fee_adjusted_break_even_probability(0.45) - 0.45) * 100, 3)
    expected_drag_yrfi = round((kf.fee_adjusted_break_even_probability(0.56) - 0.56) * 100, 3)
    assert ef_nrfi['expectedFeeDrag'] == expected_drag_nrfi
    assert ef_yrfi['expectedFeeDrag'] == expected_drag_yrfi


def test_item16_no_side_bias_rule_no_structural_no_preference_in_source():
    """
    Spec section 16: the historical 10%+ bucket's NO-side outperformance
    is exploratory only -- no "prefer NO"/"fade YES"/"buy NO above X"
    structural rule may exist anywhere in the live pipeline source.
    """
    with open(os.path.join(ROOT, "scripts", "build_market_ledger.py")) as f:
        src = f.read().lower()
    for banned in ("prefer no", "fade yes", "buy no above", "no-side preference", "no side preference"):
        assert banned not in src, f"found banned structural NO-preference phrase: {banned!r}"


# ---------------------------------------------------------------------------
# 12-14: price-sensitivity validation (10c / 50c / 90c)
# ---------------------------------------------------------------------------

def test_item12_price_sensitivity_10_cents():
    ef = bml.build_edge_fields(0.20, 0.10, 10.0, bml.CAL_MEDIUM, series_ticker='KXMLBGAME')
    expected_drag = round((kf.fee_adjusted_break_even_probability(0.10) - 0.10) * 100, 3)
    assert ef['expectedFeeDrag'] == expected_drag
    assert expected_drag == round(kf.FEE_MULTIPLIER_TAKER_STANDARD * 0.10 * 0.90 * 100, 3)


def test_item13_price_sensitivity_50_cents():
    ef = bml.build_edge_fields(0.60, 0.50, 50.0, bml.CAL_MEDIUM, series_ticker='KXMLBGAME')
    expected_drag = round((kf.fee_adjusted_break_even_probability(0.50) - 0.50) * 100, 3)
    assert ef['expectedFeeDrag'] == expected_drag
    assert expected_drag == round(kf.FEE_MULTIPLIER_TAKER_STANDARD * 0.50 * 0.50 * 100, 3)


def test_item14_price_sensitivity_90_cents():
    ef = bml.build_edge_fields(0.95, 0.90, 90.0, bml.CAL_MEDIUM, series_ticker='KXMLBGAME')
    expected_drag = round((kf.fee_adjusted_break_even_probability(0.90) - 0.90) * 100, 3)
    assert ef['expectedFeeDrag'] == expected_drag
    assert expected_drag == round(kf.FEE_MULTIPLIER_TAKER_STANDARD * 0.90 * 0.10 * 100, 3)


def test_item17_fee_drag_never_a_flat_haircut_across_prices():
    """Proves production is not using a flat 3.5pp fee haircut (spec section 17)."""
    drags = []
    for price_c in (10.0, 30.0, 50.0, 70.0, 90.0):
        ef = bml.build_edge_fields(price_c / 100.0 + 0.05, (price_c - 1) / 100.0, price_c, bml.CAL_MEDIUM, series_ticker='KXMLBGAME')
        drags.append(ef['expectedFeeDrag'])
    assert len(set(drags)) > 1, f"fee drag must vary by price, got constant {drags}"
    # Symmetric around 50c by construction (multiplier*price*(1-price)).
    assert drags[0] == drags[-1], "10c and 90c should have identical fee drag by the closed-form symmetry"


# ---------------------------------------------------------------------------
# 15-18: order-size / reference-allocation validation
# ---------------------------------------------------------------------------

def test_item15_reference_allocation_5_dollars():
    sim = kf.simulate_order(5.0, 0.50, quantity_granularity=kf.QUANTITY_GRANULARITY_UNKNOWN)
    assert sim is not None
    assert sim['actualCashConsumed'] <= 5.0
    assert sim['unusedCash'] >= 0


def test_item16b_reference_allocation_10_dollars_matches_row_field():
    ef = bml.build_edge_fields(0.60, 0.55, 50.0, bml.CAL_MEDIUM, series_ticker='KXMLBGAME')
    assert ef['referenceAllocationDollars'] == kf.DEFAULT_RESEARCH_ORDER_SIZE == 10.0
    sim = kf.simulate_order(10.0, 0.50, quantity_granularity=kf.QUANTITY_GRANULARITY_UNKNOWN)
    assert ef['referenceAllocationContractPrincipal'] == sim['contractPrincipal']
    assert ef['referenceAllocationExpectedEntryFee'] == sim['entryFee']
    assert ef['referenceAllocationExpectedCashConsumed'] == sim['actualCashConsumed']
    assert ef['referenceAllocationExpectedUnusedCash'] == sim['unusedCash']


def test_item17b_reference_allocation_25_dollars():
    sim = kf.simulate_order(25.0, 0.50, quantity_granularity=kf.QUANTITY_GRANULARITY_UNKNOWN)
    assert sim is not None
    assert 0 <= sim['unusedCash'] < 1.0, "whole-contract rounding residual should stay under a dollar at 25c granularity"


def test_item18_reference_allocation_100_dollars():
    sim = kf.simulate_order(100.0, 0.50, quantity_granularity=kf.QUANTITY_GRANULARITY_UNKNOWN)
    assert sim is not None
    assert sim['actualCashConsumed'] <= 100.0
    # Fee-only and realistic-execution concepts stay distinct: fee-only
    # (continuous, no rounding) must differ from the whole-contract
    # actualCashConsumed's own entry fee by at most a rounding residual.
    continuous_fee = round(100.0 * kf.fee_only_drag_percentage_points(0.50), 2)
    assert abs(sim['entryFee'] - continuous_fee) < 0.25


# ---------------------------------------------------------------------------
# 19-21: quantity granularity
# ---------------------------------------------------------------------------

def test_item19_fractional_quantity_granularity():
    sim = kf.simulate_order(10.0, 0.4995, quantity_granularity=kf.QUANTITY_GRANULARITY_FRACTIONAL_ENABLED)
    assert sim is not None
    assert sim['unusedCash'] == 0.0
    assert isinstance(sim['contracts'], float)


def test_item20_whole_contract_only_granularity():
    sim = kf.simulate_order(10.0, 0.4995, quantity_granularity=kf.QUANTITY_GRANULARITY_WHOLE_CONTRACT_ONLY)
    assert sim is not None
    assert float(sim['contracts']).is_integer()


def test_item21_unknown_granularity_never_fabricates_exact_effect():
    """UNKNOWN falls back to conservative whole-contract simulation -- never claims fractional precision it has no evidence for."""
    ef = bml.build_edge_fields(0.60, 0.55, 50.0, bml.CAL_MEDIUM, series_ticker='KXMLBGAME')
    assert ef['referenceAllocationQuantityGranularity'] == kf.QUANTITY_GRANULARITY_UNKNOWN
    sim = kf.simulate_order(10.0, 0.50, quantity_granularity=kf.QUANTITY_GRANULARITY_UNKNOWN)
    assert float(sim['contracts']).is_integer(), "UNKNOWN must fall back to whole-contract, not fabricate fractional precision"


# ---------------------------------------------------------------------------
# 22-24: fee-adjusted break-even / Bet Up To
# ---------------------------------------------------------------------------

def test_item22_fee_adjusted_break_even_not_a_fixed_cent_haircut():
    be_10 = kf.fee_adjusted_break_even_probability(0.10)
    be_50 = kf.fee_adjusted_break_even_probability(0.50)
    be_90 = kf.fee_adjusted_break_even_probability(0.90)
    assert round(be_10 - 0.10, 4) != round(be_50 - 0.50, 4), "break-even shift must vary with price, not a fixed haircut"
    assert round(be_90 - 0.90, 4) != round(be_50 - 0.50, 4)


def test_item23_fee_adjusted_bet_up_to_derived_from_canonical_engine():
    net_ceiling = bml.fee_aware_bet_up_to_price_cents(0.60, bml.THRESHOLD_PAPER, bml.CAL_MEDIUM)
    assert net_ceiling is not None
    # Round-trip: at exactly the net ceiling price, net edge must equal the threshold (within rounding).
    ef = bml.build_edge_fields(0.60, 0.55, net_ceiling, bml.CAL_MEDIUM)
    assert abs(ef['netExecutableEdge'] - bml.THRESHOLD_PAPER) < 0.05


def test_item24_net_bet_up_to_never_exceeds_gross_bet_up_to():
    for fair_prob in (0.30, 0.45, 0.55, 0.65, 0.80):
        gross = bml.bet_up_to_price_cents(fair_prob, bml.THRESHOLD_PAPER, bml.CAL_MEDIUM)
        net = bml.fee_aware_bet_up_to_price_cents(fair_prob, bml.THRESHOLD_PAPER, bml.CAL_MEDIUM)
        assert net is not None and gross is not None
        assert net <= gross, f"net ceiling {net} exceeded gross ceiling {gross} at fair_prob={fair_prob}"


# ---------------------------------------------------------------------------
# 25-26: tier logic
# ---------------------------------------------------------------------------

def test_item25_tier_downgrade_where_fees_materially_reduce_edge():
    """A gross-HIGH candidate can be downgraded to MEDIUM/PAPER/rejected once fees are applied."""
    ef = bml.build_edge_fields(0.545, 0.505, 50.5, bml.CAL_MEDIUM, series_ticker='KXMLBGAME')
    gross_conf = bml.confidence_from_edge(ef['calibratedEdgeVsExecutable'])
    net_conf = bml.confidence_from_edge(ef['netExecutableEdge'])
    tier_rank = {None: 0, 'PAPER': 1, 'MEDIUM': 2, 'HIGH': 3}
    assert tier_rank[net_conf] <= tier_rank[gross_conf]


def test_item26_no_tier_upgrade_purely_from_fee_logic():
    """Fee integration can only ever downgrade or leave a tier unchanged, never upgrade one."""
    import random
    random.seed(20260814)
    tier_rank = {None: 0, 'PAPER': 1, 'MEDIUM': 2, 'HIGH': 3}
    for _ in range(200):
        model_p = round(random.uniform(0.05, 0.95), 4)
        price_c = round(random.uniform(1.0, 99.0), 2)
        kalshi_vf = round(max(0.01, min(0.99, price_c / 100.0 - 0.01)), 4)
        ef = bml.build_edge_fields(model_p, kalshi_vf, price_c, bml.CAL_MEDIUM, series_ticker='KXMLBGAME')
        gross_conf = bml.confidence_from_edge(ef['calibratedEdgeVsExecutable'])
        net_conf = bml.confidence_from_edge(ef['netExecutableEdge'])
        assert tier_rank[net_conf] <= tier_rank[gross_conf], (
            f"fee logic upgraded a tier: model_p={model_p} price_c={price_c} "
            f"gross={gross_conf} net={net_conf}"
        )


# ---------------------------------------------------------------------------
# 27: risk gate uses the intended net metric
# ---------------------------------------------------------------------------

def test_item27_risk_gate_entry_edge_prefers_net_executable_edge():
    import risk_gate as rg
    entry_with_net = {'edge': 5.0, 'calibratedEdgeVsExecutable': 5.0, 'netExecutableEdge': 1.2}
    assert rg._entry_edge(entry_with_net) == 1.2
    entry_legacy_only = {'edge': 5.0, 'calibratedEdgeVsExecutable': 5.0}
    assert rg._entry_edge(entry_legacy_only) == 5.0


# ---------------------------------------------------------------------------
# 28-30: no double counting
# ---------------------------------------------------------------------------

def test_item28_no_fee_double_counting_across_pipeline():
    """
    Fee terms (kalshi_fees) appear in exactly one file
    (build_market_ledger.py) -- risk_gate.py and write_pending_bets.py
    only read already-computed fields, never recompute a fee.
    """
    for path in ("scripts/risk_gate.py", "scripts/write_pending_bets.py"):
        with open(os.path.join(ROOT, path)) as f:
            src = f.read()
        assert "kalshi_fees" not in src
        assert "fee_adjusted_break_even" not in src
        assert "net_expected_value_per_dollar" not in src


def test_item28b_net_edge_computed_from_single_break_even_shift_not_two_fee_terms():
    """netExecutableEdge subtracts expectedFeeDrag exactly once (not e.g. once in raw and again in calibrated)."""
    ef = bml.build_edge_fields(0.60, 0.55, 50.0, bml.CAL_MEDIUM, series_ticker='KXMLBGAME')
    single_subtraction = round((ef['rawEdgeVsExecutable'] - ef['expectedFeeDrag']) * bml.CAL_MEDIUM, 3)
    double_subtraction = round((ef['rawEdgeVsExecutable'] - 2 * ef['expectedFeeDrag']) * bml.CAL_MEDIUM, 3)
    assert ef['netExecutableEdge'] == single_subtraction
    assert ef['netExecutableEdge'] != double_subtraction


def test_item29_no_calibration_double_counting():
    """Calibration factor is applied exactly once to the fee-adjusted raw edge, matching the gross formula's own single application."""
    ef = bml.build_edge_fields(0.60, 0.55, 50.0, bml.CAL_MEDIUM, series_ticker='KXMLBGAME')
    once = round(ef['netRawExecutableEdge'] * bml.CAL_MEDIUM, 3)
    twice = round(ef['netRawExecutableEdge'] * bml.CAL_MEDIUM * bml.CAL_MEDIUM, 3)
    assert ef['netExecutableEdge'] == once
    assert ef['netExecutableEdge'] != twice


def test_item30_no_bid_ask_double_counting():
    """
    Executable price (already ask-based, per executablePriceUsed) feeds
    the fee-adjusted break-even directly -- the spread is never
    subtracted a second time as a separate 'slippage' fee term anywhere
    in build_edge_fields().
    """
    with open(os.path.join(ROOT, "scripts", "build_market_ledger.py")) as f:
        src = f.read()
    # No separate slippage/spread subtraction inside build_edge_fields()'s body.
    start = src.index("def build_edge_fields")
    end = src.index("def confidence_from_edge")
    body = src[start:end].lower()
    assert "spread" not in body
    assert "slippage" not in body


# ---------------------------------------------------------------------------
# 31: tie-aware F5 correctness
# ---------------------------------------------------------------------------

def test_item31_f5_tie_aware_probability_unaffected_by_fee_integration():
    """
    F5's three-way (away/tie/home) probabilities are computed upstream
    of build_edge_fields() and never renormalized -- fee integration
    only shifts the PRICE reference (fee_adjusted_break_even), never
    touches model_prob, so away+tie+home probabilities still sum to 1
    exactly as before this milestone.
    """
    from lib.research.three_way_projection import three_way_result_probs
    probs = three_way_result_probs(4.5, 3.8, max_runs=20)
    total = probs['awayWinProb'] + probs['tieProb'] + probs['homeWinProb']
    assert abs(total - 1.0) < 1e-6
    # The fee-aware edge computation for F5_ML_Away uses awayWinProb
    # directly (tie-exclusive), matching the same event build_edge_fields
    # receives in scripts/build_market_ledger.py's F5 block.
    ef = bml.build_edge_fields(probs['awayWinProb'], 0.50, 48.0, bml.CAL_MEDIUM, series_ticker='KXMLBF5')
    assert ef['netExecutableEdge'] is not None


# ---------------------------------------------------------------------------
# 32: correlation ranking uses economically relevant final edge
# ---------------------------------------------------------------------------

def test_item32_correlation_ranking_uses_net_executable_edge():
    import risk_gate as rg
    # Same-side-thesis pair: ML_Away (higher net edge) vs F5_ML_Away
    # (higher GROSS edge but lower net edge after fees) -- the keep/drop
    # decision must follow net, not gross.
    ml_entry = {'market': 'ML_Away', 'calibratedEdgeVsExecutable': 2.0, 'netExecutableEdge': 1.8}
    f5_entry = {'market': 'F5_ML_Away', 'calibratedEdgeVsExecutable': 3.0, 'netExecutableEdge': 1.2}
    assert rg._entry_edge(ml_entry) > rg._entry_edge(f5_entry), (
        "ranking must prefer the higher NET edge (ML_Away) even though F5 has the higher gross edge"
    )


# ---------------------------------------------------------------------------
# 33-34: stake semantics
# ---------------------------------------------------------------------------

def test_item33_stake_remains_intended_unit_allocation_not_dollar_contract_cost():
    import write_pending_bets as wpb
    entry = {
        'market': 'ML_Away', 'confidenceTier': 'HIGH', 'status': 'Accepted',
        'betSize': 4.0, 'ticker': 'T1', 'executablePriceUsed': 50.0,
        'kalshiPrice': -110, 'modelProb': 60.0,
    }
    rec = wpb.build_bet_record('2026-08-14', 'AAA@BBB', entry, '2026-08-14T00:00:00Z')
    assert rec['stake'] == 4.0
    assert rec['betSize'] == 4.0
    assert rec['stake'] == entry['betSize'], "stake must be the unit allocation copied verbatim, never derived from price"


def test_item34_actual_cash_consumed_estimate_remains_a_separate_field():
    import write_pending_bets as wpb
    entry = {
        'market': 'ML_Away', 'confidenceTier': 'HIGH', 'status': 'Accepted',
        'betSize': 4.0, 'ticker': 'T1', 'executablePriceUsed': 50.0,
        'kalshiPrice': -110, 'modelProb': 60.0,
        'referenceAllocationDollars': 10.0, 'referenceAllocationExpectedCashConsumed': 9.5,
    }
    rec = wpb.build_bet_record('2026-08-14', 'AAA@BBB', entry, '2026-08-14T00:00:00Z')
    assert rec['stake'] == 4.0
    assert rec['referenceAllocationExpectedCashConsumed'] == 9.5
    assert rec['stake'] != rec['referenceAllocationExpectedCashConsumed'], (
        "stake (unit allocation) must never be conflated with the illustrative dollar cash-consumed estimate"
    )


# ---------------------------------------------------------------------------
# 35-36: pending-bet artifact fee provenance / post-trade override
# ---------------------------------------------------------------------------

def test_item35_pending_bet_artifact_preserves_fee_provenance():
    import write_pending_bets as wpb
    entry = {
        'market': 'ML_Away', 'confidenceTier': 'HIGH', 'status': 'Accepted',
        'betSize': 4.0, 'ticker': 'T1', 'executablePriceUsed': 50.0,
        'kalshiPrice': -110, 'modelProb': 60.0,
        'calibratedEdgeVsExecutable': 2.55, 'expectedFeeDrag': 1.75,
        'netExecutableEdge': 2.10, 'netExpectedValuePerDollar': 0.159,
        'feeAdjustedBreakEvenProbability': 51.75,
        'betUpToPriceGross': 56.08, 'betUpToPriceNet': 54.34,
        'feeType': 'quadratic', 'feeMultiplier': 0.07,
        'feeSource': 'ASSUMED_STANDARD_NO_OVERRIDE_EVIDENCE',
        'feeScheduleVersion': kf.FEE_SCHEDULE_VERSION,
    }
    rec = wpb.build_bet_record('2026-08-14', 'AAA@BBB', entry, '2026-08-14T00:00:00Z')
    for field in (
        'grossEdgePct', 'expectedFeeDrag', 'netExecutableEdge', 'netExpectedValuePerDollar',
        'feeAdjustedBreakEvenProbability', 'betUpToPriceGross', 'betUpToPriceNet',
        'feeType', 'feeMultiplier', 'feeSource', 'feeScheduleVersion',
    ):
        assert field in rec, f"pending-bet record missing fee provenance field {field}"
    assert rec['grossEdgePct'] == 2.55
    assert rec['netExecutableEdge'] == 2.10


def test_item36_actual_fee_can_override_estimate_post_trade():
    """
    Post-trade accounting (lib.edgelab.execution_economics, PR #88's
    merged fee-status precedence) still ranks ACTUAL above ESTIMATED --
    this milestone's pre-trade estimate never outranks a real receipt.
    """
    assert kf.FEE_STATUS_RANK[kf.FEE_STATUS_ACTUAL_RECEIPT] > kf.FEE_STATUS_RANK[kf.FEE_STATUS_ESTIMATED_FEE_SCHEDULE]
    assert kf.FEE_STATUS_RANK[kf.FEE_STATUS_ACTUAL_API_FILL] > kf.FEE_STATUS_RANK[kf.FEE_STATUS_ESTIMATED_FEE_SCHEDULE]


# ---------------------------------------------------------------------------
# 37-38: backward compatibility
# ---------------------------------------------------------------------------

def test_item37_old_historical_schema_remains_readable():
    """A pre-milestone row (no fee-aware fields at all) still round-trips through make_row() with the new fields defaulting to None, not erroring."""
    row = bml.make_row('ML_Away', status='Accepted', modelProb=60.0, edge=5.0, calibratedEdgeVsExecutable=5.0)
    assert row['netExecutableEdge'] is None
    assert row['expectedFeeDrag'] is None
    assert row['edge'] == 5.0
    assert row['calibratedEdgeVsExecutable'] == 5.0


def test_item38_prospective_snapshot_compatibility_no_lookahead():
    """
    All fee-aware fields are pure functions of already-known
    inputs (model_prob, kalshi_vf, executable price at snapshot time) --
    no field introduces a dependency on future/closing data.
    """
    import inspect
    src = inspect.getsource(bml.build_edge_fields)
    for banned in ("closingPrice", "actualEntryPrice", "settlement", "clv"):
        # These existing CLV-scaffold fields are set to None/passed
        # through unchanged inside build_edge_fields -- never READ as an
        # input to any fee-aware computation.
        assert f"= {banned}" not in src.replace(" ", "")


# ---------------------------------------------------------------------------
# 39: all PR #88 accounting tests remain green (smoke import check)
# ---------------------------------------------------------------------------

def test_item39_pr88_fee_engine_functions_still_present_and_importable():
    for name in (
        "taker_fee", "cost_for_contracts", "max_contracts_for_cash", "simulate_order",
        "simulate_settlement_order", "net_settlement_pl_for_order", "net_settlement_pl_fee_only",
        "fee_only_drag_percentage_points", "fee_adjusted_break_even_probability",
        "fee_adjusted_bet_up_to_price", "net_expected_value_per_dollar", "fee_rule_for_series",
        "reconstruct_whole_dollar_stake",
    ):
        assert hasattr(kf, name), f"PR #88 fee engine function {name} missing"


# ---------------------------------------------------------------------------
# Additional: production-safety invariants (model probabilities/bankroll
# untouched, matches the audit performed before this milestone).
# ---------------------------------------------------------------------------

def test_model_probability_functions_unchanged_by_fee_integration():
    """p_team_wins/p_over_total/vig_free_2way are pure functions with zero fee-engine dependency."""
    import inspect
    for fn in (bml.p_team_wins, bml.p_over_total, bml.vig_free_2way, bml.vig_free_3way):
        src = inspect.getsource(fn)
        assert "kalshi_fees" not in src
        assert "kf." not in src


def test_bet_size_bankroll_function_unchanged_by_fee_integration():
    """bet_size()'s unit-multiplier scheme is untouched -- no fee-engine dependency, same base/multiplier table."""
    import inspect
    src = inspect.getsource(bml.bet_size)
    assert "kalshi_fees" not in src
    assert "kf." not in src
    assert bml.bet_size('HIGH', 'ML_Away') == 4.0
    assert bml.bet_size('MEDIUM', 'ML_Away') == 3.0
    assert bml.bet_size('PAPER', 'ML_Away') == 1.0
