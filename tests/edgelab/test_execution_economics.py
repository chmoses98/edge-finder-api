#!/usr/bin/env python3
"""
tests/edgelab/test_execution_economics.py
==============================================
Kalshi Fee-Aware Execution Economics milestone: regression coverage for
lib/edgelab/execution_economics.py -- the stake-evidence priority
ladder, fee-status merge rules, and execution-status-aware realized P/L.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import execution_economics as ee
from lib.edgelab import kalshi_fees as kf


# ---------------------------------------------------------------------------
# Stake-evidence priority ladder (spec section 6) -- items 1, 2, 26 of the
# 35-item regression list.
# ---------------------------------------------------------------------------

def test_share_card_initial_cost_never_automatically_becomes_stake():
    """Item 1: with ONLY screenshot evidence and no unique whole-dollar
    reconstruction, stake must be None, never the displayed Initial cost."""
    result = ee.determine_canonical_stake(share_card_initial_cost=9.80, price=0.41)
    assert result["stake"] != 9.80
    assert result["stake"] is None
    assert result["source"] == ee.STAKE_EVIDENCE_AMBIGUOUS


def test_explicit_user_stake_remains_even_when_initial_cost_present():
    """Item 2 / the Colorado acceptance case: a user-confirmed $10 stake
    must win even though shareCardInitialCost=$9.80 is also supplied."""
    result = ee.determine_canonical_stake(
        user_confirmed_stake=10.00, share_card_initial_cost=9.80, price=0.41,
    )
    assert result["stake"] == 10.00
    assert result["source"] == ee.STAKE_EVIDENCE_USER_CONFIRMED
    assert result["confidence"] == "HIGH"


def test_user_confirmed_stake_outranks_screenshot_inference():
    """Item 26."""
    result = ee.determine_canonical_stake(
        user_confirmed_stake=12.00, share_card_initial_cost=9.80, price=0.41,
    )
    assert result["source"] == ee.STAKE_EVIDENCE_USER_CONFIRMED
    assert result["stake"] == 12.00


def test_exact_api_execution_outranks_fee_estimation():
    """Item 27."""
    result = ee.determine_canonical_stake(
        exact_api_stake=9.84, share_card_initial_cost=9.80, price=0.41,
    )
    assert result["source"] == ee.STAKE_EVIDENCE_EXACT_API_EXECUTION
    assert result["stake"] == 9.84


def test_exact_receipt_outranks_screenshot_inference():
    result = ee.determine_canonical_stake(
        exact_receipt_stake=10.00, share_card_initial_cost=9.80, price=0.41,
    )
    assert result["source"] == ee.STAKE_EVIDENCE_EXACT_RECEIPT
    assert result["stake"] == 10.00


def test_screenshot_only_unique_match_infers_stake():
    """Priority 4: a screenshot whose Initial cost uniquely reconstructs
    to exactly one whole dollar candidate is safely inferred."""
    price = 0.5
    contracts = kf.max_contracts_for_cash(25.0, price)
    _, _, cost = kf.cost_for_contracts(contracts, price)
    result = ee.determine_canonical_stake(share_card_initial_cost=cost, price=price)
    assert result["source"] == ee.STAKE_EVIDENCE_FEE_AWARE_INFERRED
    assert result["stake"] == 25.0
    assert result["confidence"] == "MEDIUM"


def test_screenshot_only_ambiguous_case_asks_for_confirmation():
    """Item 28: no reconstruction possible -> ambiguous, never guessed."""
    result = ee.determine_canonical_stake(share_card_initial_cost=9.84, price=0.55)
    assert result["source"] == ee.STAKE_EVIDENCE_AMBIGUOUS
    assert result["stake"] is None


def test_no_evidence_at_all_is_ambiguous():
    result = ee.determine_canonical_stake()
    assert result["stake"] is None
    assert result["source"] == ee.STAKE_EVIDENCE_AMBIGUOUS


def test_confidence_for_economics_source_mapping():
    assert ee.confidence_for_economics_source(ee.STAKE_EVIDENCE_USER_CONFIRMED) == "HIGH"
    assert ee.confidence_for_economics_source(ee.STAKE_EVIDENCE_FEE_AWARE_INFERRED) == "MEDIUM"
    assert ee.confidence_for_economics_source(ee.STAKE_EVIDENCE_AMBIGUOUS) == "LOW"
    assert ee.confidence_for_economics_source(ee.STAKE_EVIDENCE_LEGACY_ASSUMED_EXACT) == "UNKNOWN"
    assert ee.confidence_for_economics_source("nonsense") == "UNKNOWN"


# ---------------------------------------------------------------------------
# Fee-status merge (spec section 17) -- items 4, 5, 6, 7.
# ---------------------------------------------------------------------------

def test_exact_actual_fee_overrides_estimated_fee():
    """Item 4."""
    status, fee = ee.merge_fee_status(
        kf.FEE_STATUS_ESTIMATED_FEE_SCHEDULE, 0.35,
        kf.FEE_STATUS_ACTUAL_RECEIPT, 0.30,
    )
    assert status == kf.FEE_STATUS_ACTUAL_RECEIPT
    assert fee == 0.30


def test_estimated_fee_cannot_masquerade_as_actual():
    """Item 5: an existing ACTUAL fee is never demoted by a new ESTIMATED one."""
    status, fee = ee.merge_fee_status(
        kf.FEE_STATUS_ACTUAL_API_FILL, 0.30,
        kf.FEE_STATUS_ESTIMATED_FEE_SCHEDULE, 0.35,
    )
    assert status == kf.FEE_STATUS_ACTUAL_API_FILL
    assert fee == 0.30


def test_unknown_fee_remains_unknown_until_real_evidence_arrives():
    """Item 6."""
    status, fee = ee.merge_fee_status(kf.FEE_STATUS_UNKNOWN, None, kf.FEE_STATUS_UNKNOWN, None)
    assert status == kf.FEE_STATUS_UNKNOWN
    assert fee is None


def test_reconstructed_exact_outranks_estimated_fee_schedule():
    status, fee = ee.merge_fee_status(
        kf.FEE_STATUS_ESTIMATED_FEE_SCHEDULE, 0.35,
        kf.FEE_STATUS_RECONSTRUCTED_EXACT, 0.33,
    )
    assert status == kf.FEE_STATUS_RECONSTRUCTED_EXACT
    assert fee == 0.33


def test_same_tier_replacement_takes_the_new_value():
    """A fresh estimate at the same tier as an older one is allowed to update in place."""
    status, fee = ee.merge_fee_status(
        kf.FEE_STATUS_ESTIMATED_FEE_SCHEDULE, 0.30,
        kf.FEE_STATUS_ESTIMATED_FEE_SCHEDULE, 0.35,
    )
    assert status == kf.FEE_STATUS_ESTIMATED_FEE_SCHEDULE
    assert fee == 0.35


# ---------------------------------------------------------------------------
# Execution-status-aware realized P/L (spec section 13) -- items 16-21.
# ---------------------------------------------------------------------------

def test_stake_represents_allocated_budget_loss_uses_actual_cash_consumed():
    """
    CORRECTION PASS: a LOSS forfeits the ACTUAL cash consumed for the
    executed position, not necessarily the full allocated stake --
    whole-contract sizing can leave a real remainder that was never
    genuinely deployed/at risk (see docs/KALSHI_FEE_AWARE_EXECUTION_ECONOMICS.md's
    "Correction pass" section). This replaces the ORIGINAL (buggy)
    version of this test, which asserted -10.0 exactly.
    """
    sim = kf.simulate_order(10.0, 0.5)
    pl = ee.realized_pl_for_bet(execution_status=None, stake=10.0, bet_result="LOSS", entry_price=0.5)
    assert pl == -sim["actualCashConsumed"]
    assert pl != -10.0


def test_exact_actual_cash_consumed_evidence_is_used_directly_when_known():
    """When actual_cash_consumed/contracts ARE known exactly (real receipt/
    fill evidence), they're used directly -- no simulation, no ambiguity."""
    pl = ee.realized_pl_for_bet(
        execution_status=None, stake=10.0, bet_result="LOSS", entry_price=0.5,
        actual_cash_consumed=9.99, contracts=19,
    )
    assert pl == -9.99


def test_contract_cost_is_distinct_from_stake_via_estimate_helper():
    """Item 17: contracts * price (contractCost) is strictly less than
    stake once a fee is included -- they are never the same number."""
    stake, price = 10.0, 0.5
    contracts = ee.estimate_contracts_for_stake(stake, price)
    contract_cost = round(contracts * price, 2)
    assert contract_cost < stake


def test_winning_settlement_pl_uses_actual_cash_consumed_not_full_stake():
    """
    Item 18, CORRECTED: net P/L on a WIN is grossSettlementPayout minus
    ACTUAL cash consumed, not minus the full allocated stake.
    """
    pl = ee.realized_pl_for_bet(execution_status=None, stake=10.0, bet_result="WIN", entry_price=0.5)
    sim = kf.simulate_order(10.0, 0.5)
    contracts = sim["contracts"]
    assert pl == round(contracts * 1.0 - sim["actualCashConsumed"], 4)
    assert pl < round(10.0 * (1.0 / 0.5 - 1.0), 4)  # strictly less than the old fee-free formula


def test_losing_pl_loses_actual_cash_consumed_not_full_stake():
    """Item 19, CORRECTED: unused allocated cash is never part of the loss."""
    sim = kf.simulate_order(7.5, 0.3)
    pl = ee.realized_pl_for_bet(execution_status=None, stake=7.5, bet_result="LOSS", entry_price=0.3)
    assert pl == -sim["actualCashConsumed"]
    assert pl != -7.5


def test_early_closed_position_is_not_treated_as_final_settlement():
    """Item 20: SOLD_EARLY must use exitSaleProceeds, never the win/loss formula,
    even when bet_result says WIN."""
    pl_sold = ee.realized_pl_for_bet(
        execution_status=ee.EXECUTION_STATUS_SOLD_EARLY, stake=10.0, bet_result="WIN",
        entry_price=0.41, exit_sale_proceeds=23.42,
    )
    assert pl_sold == 13.42
    # Confirm this really did bypass the settlement formula (which would give a different number).
    settlement_style = ee.realized_pl_for_bet(execution_status=None, stake=10.0, bet_result="WIN", entry_price=0.41)
    assert pl_sold != settlement_style


def test_partial_close_also_uses_exit_sale_proceeds_not_settlement_formula():
    pl = ee.realized_pl_for_bet(
        execution_status=ee.EXECUTION_STATUS_PARTIAL_CLOSE, stake=20.0, bet_result="WIN",
        entry_price=0.5, exit_sale_proceeds=15.0,
    )
    assert pl == -5.0


def test_sold_early_without_proceeds_evidence_returns_none_never_fabricated():
    pl = ee.realized_pl_for_bet(
        execution_status=ee.EXECUTION_STATUS_SOLD_EARLY, stake=10.0, bet_result="WIN", entry_price=0.41,
    )
    assert pl is None


def test_void_refund_returns_full_stake_zero_net():
    """Item 21."""
    pl = ee.realized_pl_for_bet(execution_status=ee.EXECUTION_STATUS_VOID_REFUND, stake=15.0, bet_result=None)
    assert pl == 0.0


def test_push_and_void_bet_results_unaffected_by_fee_awareness():
    for result in ("PUSH", "VOID"):
        pl = ee.realized_pl_for_bet(execution_status=None, stake=10.0, bet_result=result, entry_price=0.5)
        assert pl == 0.0


def test_unknown_execution_status_never_fabricates_a_number():
    pl = ee.realized_pl_for_bet(execution_status=ee.EXECUTION_STATUS_UNKNOWN, stake=10.0, bet_result="WIN", entry_price=0.5)
    assert pl is None


def test_none_execution_status_defaults_to_held_to_settlement_for_backward_compat():
    """A bet with no executionStatus recorded (every bet settled before
    this milestone) must still compute a number -- the safe backward-
    compatible default, not a new UNKNOWN gap."""
    pl = ee.realized_pl_for_bet(execution_status=None, stake=10.0, bet_result="WIN", entry_price=0.5)
    assert pl is not None


def test_missing_stake_never_fabricates_a_number():
    assert ee.realized_pl_for_bet(execution_status=None, stake=None, bet_result="WIN", entry_price=0.5) is None


def test_estimate_contracts_for_stake_matches_kalshi_fees_engine():
    assert ee.estimate_contracts_for_stake(10.0, 0.5) == kf.max_contracts_for_cash(10.0, 0.5)


def test_estimate_contracts_for_stake_none_for_invalid_price():
    assert ee.estimate_contracts_for_stake(10.0, None) is None
    assert ee.estimate_contracts_for_stake(10.0, 1.5) is None
