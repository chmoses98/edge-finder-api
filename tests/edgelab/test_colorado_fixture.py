#!/usr/bin/env python3
"""
tests/edgelab/test_colorado_fixture.py
==========================================
Kalshi Fee-Aware Execution Economics milestone: the "Colorado $10"
acceptance-case regression fixture (spec section 0/14) --

  User-confirmed cash stake: $10.00
  Kalshi share card: Market "Colorado team total over 4.5 runs",
    YES @ approximately 41%, Initial cost $9.80, Paid out $23.42,
    state CLOSED POSITION.

This is the canonical proof that the bug described in the spec is fixed:
a share card's "Initial cost" must NEVER silently become canonical
stake, even when it's the only other cash-shaped number nearby.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import execution_economics as ee
from lib.edgelab import schema
from lib.edgelab.bets import build_manual_bet_record

COLORADO_SHARE_CARD = {
    "shareCardInitialCost": 9.80,
    "shareCardPaidOut": 23.42,
    "shareCardDisplayedProbability": 0.41,
    "shareCardPositionState": "CLOSED_POSITION",
    "capturedNote": "nightly postmortem screenshot",
}


def test_canonical_stake_is_ten_dollars_not_the_share_card_initial_cost():
    """The user said $10 -- that is authoritative, full stop, regardless
    of what the share card's Initial cost says."""
    result = ee.determine_canonical_stake(
        user_confirmed_stake=10.00,
        share_card_initial_cost=COLORADO_SHARE_CARD["shareCardInitialCost"],
        price=COLORADO_SHARE_CARD["shareCardDisplayedProbability"],
    )
    assert result["stake"] == 10.00
    assert result["stake"] != 9.80
    assert result["source"] == ee.STAKE_EVIDENCE_USER_CONFIRMED
    assert result["confidence"] == "HIGH"


def test_placed_bet_record_preserves_raw_share_card_facts_unmodified():
    """shareCardEvidence carries the raw display values EXACTLY as seen --
    never interpreted, never overwritten by any derived field."""
    record = build_manual_bet_record(
        "KXMLBTEAMTOTAL-COL-4.5", "Colorado team total over 4.5 runs",
        10.00, 0.41, None,
        side="YES", import_batch_id="colorado-fixture", source_bet_key="bet-01",
        share_card_evidence=COLORADO_SHARE_CARD,
    )
    assert schema.validate_record("placed_bet", record) == []
    assert record["stake"] == 10.00
    evidence = record["shareCardEvidence"]
    assert evidence["shareCardInitialCost"] == 9.80
    assert evidence["shareCardPaidOut"] == 23.42
    assert evidence["shareCardDisplayedProbability"] == 0.41
    assert evidence["shareCardPositionState"] == "CLOSED_POSITION"


def test_initial_cost_never_leaks_into_stake_field_on_the_written_record():
    record = build_manual_bet_record(
        "KXMLBTEAMTOTAL-COL-4.5", "Colorado team total over 4.5 runs",
        10.00, 0.41, None,
        side="YES", import_batch_id="colorado-fixture", source_bet_key="bet-01",
        share_card_evidence=COLORADO_SHARE_CARD,
    )
    assert record["stake"] == 10.00
    assert record["stake"] != record["shareCardEvidence"]["shareCardInitialCost"]


def test_fee_is_not_auto_set_to_the_twenty_cent_difference():
    """The $0.20 gap between $10.00 stake and $9.80 Initial cost is NOT
    automatically assumed to be a transaction fee -- entryFees stays
    unknown absent verified fee evidence."""
    record = build_manual_bet_record(
        "KXMLBTEAMTOTAL-COL-4.5", "Colorado team total over 4.5 runs",
        10.00, 0.41, None,
        side="YES", import_batch_id="colorado-fixture", source_bet_key="bet-01",
        share_card_evidence=COLORADO_SHARE_CARD,
    )
    assert record["entryFees"] is None
    assert record["entryFees"] != 0.20
    assert record["feeStatus"] is None


def test_actual_cash_consumed_is_not_auto_inferred_from_stake_or_initial_cost():
    """
    CORRECTION PASS (spec section 13): the missing $0.20 between the
    $10.00 stake and the $9.80 Initial cost could reflect unused stake
    budget, fees, execution rounding, fractional/whole-contract
    mechanics, or a mixture -- actualCashConsumed/unusedAllocatedCash
    must NEVER be silently set to stake/0.20 respectively without
    verified evidence.
    """
    record = build_manual_bet_record(
        "KXMLBTEAMTOTAL-COL-4.5", "Colorado team total over 4.5 runs",
        10.00, 0.41, None,
        side="YES", import_batch_id="colorado-fixture", source_bet_key="bet-01",
        share_card_evidence=COLORADO_SHARE_CARD,
    )
    assert record["actualCashConsumed"] is None
    assert record["actualCashConsumed"] != 10.00
    assert record["unusedAllocatedCash"] is None
    assert record["unusedAllocatedCash"] != 0.20


def test_paid_out_is_not_auto_classified_as_settlement_payout():
    """CLOSED_POSITION alone does not prove $23.42 is a final $1/contract
    settlement payout -- it could be early-sale proceeds. Neither
    executionStatus nor exitSaleProceeds/grossSettlementPayout may be
    silently populated from shareCardPaidOut."""
    record = build_manual_bet_record(
        "KXMLBTEAMTOTAL-COL-4.5", "Colorado team total over 4.5 runs",
        10.00, 0.41, None,
        side="YES", import_batch_id="colorado-fixture", source_bet_key="bet-01",
        share_card_evidence=COLORADO_SHARE_CARD,
    )
    assert record["executionStatus"] is None
    assert record["exitSaleProceeds"] is None
    assert record["grossSettlementPayout"] is None
    assert record["netProfitLoss"] is None


def test_once_semantics_are_verified_net_pl_and_roi_compute_correctly():
    """
    Spec section 14's explicit closing statement: IF authoritative
    evidence later confirms $23.42 is total cash returned (this position
    was sold, not settled) and $10.00 is total cash committed, THEN
    netProfitLoss = +13.42 and realizedROI = 1.342 -- but only once that
    verification has actually happened (modeled here by explicitly
    supplying executionStatus=SOLD_EARLY + exit_sale_proceeds, exactly
    as a verified reconciliation step would).
    """
    net_pl = ee.realized_pl_for_bet(
        execution_status=ee.EXECUTION_STATUS_SOLD_EARLY,
        stake=10.00, bet_result="WIN", entry_price=0.41,
        exit_sale_proceeds=COLORADO_SHARE_CARD["shareCardPaidOut"],
    )
    assert net_pl == 13.42
    realized_roi = round(net_pl / 10.00, 4)
    assert realized_roi == 1.342


def test_verified_record_still_preserves_the_original_raw_evidence():
    """Even after the derived fields are populated, the raw shareCardEvidence
    block must remain byte-identical -- interpretation never overwrites evidence."""
    record = build_manual_bet_record(
        "KXMLBTEAMTOTAL-COL-4.5", "Colorado team total over 4.5 runs",
        10.00, 0.41, None,
        side="YES", import_batch_id="colorado-fixture", source_bet_key="bet-01",
        share_card_evidence=COLORADO_SHARE_CARD,
        execution_economics={
            "executionStatus": "SOLD_EARLY",
            "exitSaleProceeds": 23.42,
            "economicsSource": "USER_CONFIRMED",
            "economicsConfidence": "HIGH",
        },
    )
    assert schema.validate_record("placed_bet", record) == []
    assert record["executionStatus"] == "SOLD_EARLY"
    assert record["exitSaleProceeds"] == 23.42
    # Raw evidence untouched by the now-populated derived fields.
    assert record["shareCardEvidence"]["shareCardPaidOut"] == 23.42
    assert record["shareCardEvidence"]["shareCardInitialCost"] == 9.80


def test_execution_economics_rejects_unknown_field_typo():
    """A caller-programming-error typo in the execution_economics dict must
    fail loudly, never silently vanish."""
    import pytest
    with pytest.raises(ValueError):
        build_manual_bet_record(
            "KXMLBTEAMTOTAL-COL-4.5", "Colorado team total over 4.5 runs",
            10.00, 0.41, None,
            import_batch_id="colorado-fixture", source_bet_key="bet-01",
            execution_economics={"exitSaleProcedes": 23.42},  # typo
        )
