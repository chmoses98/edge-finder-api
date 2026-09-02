#!/usr/bin/env python3
"""
tests/edgelab/test_kalshi_multiplier_evidence.py
=====================================================
Kalshi UI Payout-Multiplier addendum (2026-09, see docs/
KALSHI_FEE_AWARE_EXECUTION_ECONOMICS.md section 13): Kalshi's redesigned
app now shows a payout multiplier (e.g. "1.97x") in place of a cents
price for some flows. shareCardEvidence.shareCardDisplayedMultiplier
preserves that raw fact -- additive, backward compatible, never used to
silently derive/overwrite entryPrice or any fee/contract-count field.

Confirmed real example (2026-09-02 manual postmortem): SD F5 YES, stake
$60, displayed executed multiplier 1.97x, no cents price/probability
reported.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import schema
from lib.edgelab.bets import build_manual_bet_record

SD_F5_MULTIPLIER_SHARE_CARD = {
    "shareCardDisplayedMultiplier": 1.97,
    "shareCardInitialCost": None,
    "shareCardPaidOut": 0.0,
    "shareCardDisplayedProbability": None,
    "shareCardPositionState": "SETTLED",
    "capturedNote": "2026-09-02 manual postmortem: Kalshi new-UI payout-multiplier display only, no cents price reported",
}


def _build(**overrides):
    kwargs = dict(
        market_ticker="KXMLBF5-26SEP021240SDCIN-SD", selection="SD F5 YES",
        stake=60.0, entry_price=round(1 / 1.97, 4), entry_timestamp=None,
        side="YES", import_batch_id="manual-postmortem-20260902-partial-v1",
        source_bet_key="manual-20260902-sdcin-sd-f5-001",
        share_card_evidence=SD_F5_MULTIPLIER_SHARE_CARD,
        data_quality="MULTIPLIER_DERIVED_ESTIMATE_UNVERIFIED",
        rationale="entryPrice is a gross 1/1.97 approximation from the displayed multiplier only -- "
                   "no cents price or displayed probability was reported; NOT a verified execution price.",
    )
    kwargs.update(overrides)
    return build_manual_bet_record(
        kwargs.pop("market_ticker"), kwargs.pop("selection"), kwargs.pop("stake"),
        kwargs.pop("entry_price"), kwargs.pop("entry_timestamp"), **kwargs,
    )


def test_record_with_multiplier_evidence_validates():
    record = _build()
    assert schema.validate_record("placed_bet", record) == []


def test_raw_multiplier_is_preserved_verbatim():
    record = _build()
    assert record["shareCardEvidence"]["shareCardDisplayedMultiplier"] == 1.97


def test_multiplier_never_silently_becomes_entry_price():
    """entryPrice must never equal the raw multiplier itself (1.97 is not
    a valid 0-1 price) -- only an explicitly-computed, explicitly-flagged
    approximation may occupy entryPrice, and the multiplier's own field
    is untouched by that derivation."""
    record = _build()
    assert record["entryPrice"] != 1.97
    assert 0 < record["entryPrice"] < 1
    assert record["shareCardEvidence"]["shareCardDisplayedMultiplier"] == 1.97


def test_derived_entry_price_is_flagged_non_authoritative():
    record = _build()
    assert record["dataQuality"] == "MULTIPLIER_DERIVED_ESTIMATE_UNVERIFIED"
    assert "1/1.97" in record["rationale"] or "approximation" in record["rationale"].lower()


def test_multiplier_never_populates_fee_or_contract_fields():
    """A multiplier alone must never seed contractCost/entryFees/feeStatus/
    contracts -- those all stay null until independently verified."""
    record = _build()
    assert record["contractCost"] is None
    assert record["entryFees"] is None
    assert record["feeStatus"] is None
    assert record["contracts"] is None
    assert record["economicsSource"] is None


def test_null_multiplier_is_backward_compatible_with_existing_rows():
    """A pre-existing shareCardEvidence block with no multiplier key at
    all (the pre-2026-09 shape) must still validate -- additive schema
    change, nothing is retroactively required."""
    record = _build(share_card_evidence={
        "shareCardInitialCost": 9.80,
        "shareCardPaidOut": 23.42,
        "shareCardDisplayedProbability": 0.41,
        "shareCardPositionState": "CLOSED_POSITION",
        "capturedNote": "pre-multiplier-era screenshot",
    })
    assert schema.validate_record("placed_bet", record) == []
    assert record["shareCardEvidence"].get("shareCardDisplayedMultiplier") is None
