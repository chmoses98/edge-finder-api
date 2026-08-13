#!/usr/bin/env python3
"""
tests/edgelab/test_reconcile_execution_economics.py
=========================================================
Kalshi Fee-Aware Execution Economics milestone: regression coverage for
scripts/edgelab/reconcile_execution_economics.py -- classification
taxonomy, safe-vs-ambiguous gating, betId/provenance preservation, and
idempotency.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import kalshi_fees as kf
from scripts.edgelab.reconcile_execution_economics import (
    CLASS_ALREADY_CORRECT, CLASS_AMBIGUOUS, CLASS_INSUFFICIENT, CLASS_SAFE_INFERENCE, CLASS_SOURCE_ERROR,
    apply_safe_corrections, build_reconciliation_report, classify_bet,
)


def _bet(**overrides):
    base = {
        "betId": "b1", "stake": 10.0, "entryPrice": 0.5, "trackingType": "REAL",
        "recordStatus": "ACTIVE", "entryMethod": "IMPORTED_RECEIPT",
        "executionEconomicsReconciliation": None,
    }
    base.update(overrides)
    return base


def test_whole_dollar_stake_is_already_correct():
    classification, _ = classify_bet(_bet(stake=10.0), repeated_stake_counts={})
    assert classification == CLASS_ALREADY_CORRECT


def test_repeated_nonwhole_stake_across_many_prices_is_already_correct():
    """Real corpus finding: $4.50 repeated 47x across wildly different
    entryPrice values is corroborating evidence of an intentional
    historical stake convention, not a screenshot artifact -- never
    flagged for correction."""
    classification, detail = classify_bet(
        _bet(stake=4.5, entryPrice=0.39), repeated_stake_counts={4.5: 5},
    )
    assert classification == CLASS_ALREADY_CORRECT
    assert "repeats identically" in detail["reason"]


def test_unique_cents_stake_with_no_reconstruction_match_is_ambiguous():
    classification, detail = classify_bet(
        _bet(stake=9.84, entryPrice=0.55), repeated_stake_counts={},
    )
    assert classification == CLASS_AMBIGUOUS
    assert detail["likelyWholeDollarCandidates"] == [9.0, 10.0]


def test_unique_cents_stake_with_reconstruction_match_is_safe():
    price = 0.5
    contracts = kf.max_contracts_for_cash(15.0, price)
    _, _, cost = kf.cost_for_contracts(contracts, price)
    classification, detail = classify_bet(
        _bet(stake=cost, entryPrice=price), repeated_stake_counts={},
    )
    assert classification == CLASS_SAFE_INFERENCE
    assert detail["reconstruction"]["stake"] == 15.0


def test_missing_entry_price_is_insufficient_evidence():
    classification, _ = classify_bet(
        _bet(stake=9.84, entryPrice=None), repeated_stake_counts={},
    )
    assert classification == CLASS_INSUFFICIENT


def test_missing_stake_is_insufficient_evidence():
    classification, _ = classify_bet(_bet(stake=None), repeated_stake_counts={})
    assert classification == CLASS_INSUFFICIENT


def test_nonpositive_stake_is_source_data_error():
    classification, _ = classify_bet(_bet(stake=0.0), repeated_stake_counts={})
    assert classification == CLASS_SOURCE_ERROR


def test_already_reconciled_bet_stays_already_correct_on_rerun():
    """A bet that already carries executionEconomicsReconciliation is
    never re-flagged, regardless of its current stake value."""
    bet = _bet(stake=9.84, executionEconomicsReconciliation={"classification": CLASS_SAFE_INFERENCE})
    classification, _ = classify_bet(bet, repeated_stake_counts={})
    assert classification == CLASS_ALREADY_CORRECT


def test_paper_and_probe_bets_excluded_from_report():
    """Item: PAPER/REAL_PROBE bets are excluded from the reconciliation entirely."""
    bets = [
        _bet(betId="paper1", trackingType="PAPER", stake=9.84, entryPrice=0.55),
        _bet(betId="probe1", trackingType="REAL_PROBE", stake=9.84, entryPrice=0.55),
        _bet(betId="real1", trackingType="REAL", stake=10.0, entryPrice=0.5),
    ]
    report = build_reconciliation_report(bets)
    assert report["realWagersAudited"] == 1
    ids_seen = {r["betId"] for r in report["questionableRows"]}
    assert "paper1" not in ids_seen
    assert "probe1" not in ids_seen


def test_cancelled_bets_excluded_from_report():
    bets = [_bet(betId="cancelled1", recordStatus="CANCELLED", stake=9.84, entryPrice=0.55)]
    report = build_reconciliation_report(bets)
    assert report["realWagersAudited"] == 0


# ---------------------------------------------------------------------------
# apply_safe_corrections -- betId preservation, idempotency, provenance (spec
# section 26 items 13, 14, 15)
# ---------------------------------------------------------------------------

def _fake_storage(tmp_path, bets):
    path = os.path.join(tmp_path, "bets.jsonl")
    with open(path, "w") as f:
        for b in bets:
            f.write(json.dumps(b) + "\n")
    return path


def test_apply_preserves_bet_id_and_unrelated_linkage_fields(tmp_path):
    """Item 13: correcting stake must never change betId, importBatchId,
    sourceBetKey, recommendationId, or modelEvaluationId."""
    price = 0.5
    contracts = kf.max_contracts_for_cash(15.0, price)
    _, _, cost = kf.cost_for_contracts(contracts, price)
    bet = _bet(
        betId="stable-id-123", stake=cost, entryPrice=price,
        importBatchId="batch-1", sourceBetKey="bet-01",
        recommendationId="rec-1", modelEvaluationId="eval-1",
    )
    path = _fake_storage(str(tmp_path), [bet])
    report = build_reconciliation_report([bet])

    from lib.edgelab import storage
    applied, updated = apply_safe_corrections(report, path=path)
    assert applied == 1
    assert updated[0]["betId"] == "stable-id-123"
    assert updated[0]["importBatchId"] == "batch-1"
    assert updated[0]["sourceBetKey"] == "bet-01"
    assert updated[0]["recommendationId"] == "rec-1"
    assert updated[0]["modelEvaluationId"] == "eval-1"
    assert updated[0]["stake"] == 15.0


def test_apply_is_idempotent_across_two_full_runs(tmp_path):
    """Item 14: applying corrections twice must produce no second mutation."""
    price = 0.5
    contracts = kf.max_contracts_for_cash(15.0, price)
    _, _, cost = kf.cost_for_contracts(contracts, price)
    bet = _bet(betId="idempotent-1", stake=cost, entryPrice=price)
    path = _fake_storage(str(tmp_path), [bet])
    report1 = build_reconciliation_report([bet])
    applied1, _ = apply_safe_corrections(report1, path=path)
    assert applied1 == 1

    from lib.edgelab import storage
    reloaded = list(storage.read_records(path))
    report2 = build_reconciliation_report(reloaded)
    applied2, updated2 = apply_safe_corrections(report2, path=path)
    assert applied2 == 0
    assert updated2 == []


def test_apply_writes_full_provenance_object(tmp_path):
    """Item 15: previous value, corrected value, reason, evidence source,
    method, and timestamp are all preserved on the corrected row."""
    price = 0.5
    contracts = kf.max_contracts_for_cash(15.0, price)
    _, _, cost = kf.cost_for_contracts(contracts, price)
    bet = _bet(betId="prov-1", stake=cost, entryPrice=price)
    path = _fake_storage(str(tmp_path), [bet])
    report = build_reconciliation_report([bet])
    _, updated = apply_safe_corrections(report, path=path, now="2026-08-13T00:00:00Z")

    prov = updated[0]["executionEconomicsReconciliation"]
    assert prov["previousStake"] == cost
    assert prov["correctedStake"] == 15.0
    assert prov["classification"] == CLASS_SAFE_INFERENCE
    assert prov["exactOrInferred"] == "INFERRED"
    assert prov["reconciledAt"] == "2026-08-13T00:00:00Z"
    assert prov["correctionReason"]
    assert prov["evidenceSource"]
    assert prov["correctionMethod"]


def test_apply_never_touches_ambiguous_rows(tmp_path):
    bet = _bet(betId="ambig-1", stake=9.84, entryPrice=0.55)
    path = _fake_storage(str(tmp_path), [bet])
    report = build_reconciliation_report([bet])
    applied, updated = apply_safe_corrections(report, path=path)
    assert applied == 0
    from lib.edgelab import storage
    reloaded = list(storage.read_records(path))
    assert reloaded[0]["stake"] == 9.84
    assert reloaded[0]["executionEconomicsReconciliation"] is None
