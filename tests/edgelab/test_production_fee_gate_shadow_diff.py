#!/usr/bin/env python3
"""
tests/edgelab/test_production_fee_gate_shadow_diff.py
===========================================================
Production Fee-Aware Net EV Integration milestone: coverage for
scripts/edgelab/production_fee_gate_shadow_diff.py's classification and
breakdown logic (spec sections 24-26).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "edgelab"))

from production_fee_gate_shadow_diff import classify_opportunity, _price_bucket, _edge_bucket, build_validation_report


def _opp(model_prob, price, yes_bid=None, yes_ask=None, side="YES", win=True, game_id="g1", checkpoint="CLOSING"):
    return {
        "opportunityModelProbability": model_prob,
        "opportunityPrice": price,
        "opportunitySide": side,
        "yesBid": yes_bid if yes_bid is not None else (price - 0.01 if price is not None else None),
        "yesAsk": yes_ask if yes_ask is not None else price,
        "canonicalMarketFamily": "team_total",
        "researchCheckpoint": checkpoint,
        "gameId": game_id,
        "gameDate": "2026-08-01",
        "opportunityReturn": 0.5 if win else -1.0,
        "opportunityReturnFeeOnly": 0.4 if win else -1.1,
        "opportunityReturnRealisticExecution": 0.35 if win else -1.0,
        "opportunityWin": win,
    }


def test_retained_when_both_old_and_new_qualify_same_tier():
    c = classify_opportunity(_opp(0.68, 0.50))
    assert c is not None
    assert c["oldQualifies"] is True
    assert c["newQualifies"] is True
    assert c["classification"] in ("RETAINED", "TIER_DOWNGRADED")


def test_rejected_by_fees_marginal_candidate():
    """Same fixture used in the marginal-edge acceptance test."""
    c = classify_opportunity(_opp(0.555, 0.51))
    assert c["oldQualifies"] is True
    assert c["newQualifies"] is False
    assert c["classification"] == "REJECTED_BY_FEES"


def test_unchanged_unqualified_when_neither_side_qualifies():
    c = classify_opportunity(_opp(0.505, 0.50))
    assert c["oldQualifies"] is False
    assert c["newQualifies"] is False
    assert c["classification"] == "UNCHANGED_UNQUALIFIED"


def test_bet_up_to_reduction_is_nonnegative():
    c = classify_opportunity(_opp(0.68, 0.50))
    assert c["betUpToReduction"] is not None
    assert c["betUpToReduction"] >= 0


def test_none_for_invalid_price():
    assert classify_opportunity(_opp(0.60, None)) is None
    assert classify_opportunity(_opp(0.60, 1.5)) is None


def test_price_bucket_boundaries():
    assert _price_bucket(0.05) == "0-10c"
    assert _price_bucket(0.50) == "50-60c"
    assert _price_bucket(1.0) == "90-100c"


def test_edge_bucket_boundaries():
    assert _edge_bucket(-1.0) == "<0"
    assert _edge_bucket(1.0) == "0-2"
    assert _edge_bucket(11.0) == "10+"


def test_build_validation_report_counts_are_internally_consistent():
    rows = [
        {
            "modelEvaluationAvailable": True, "modelFairProbability": 0.68, "side": "YES",
            "executableYesPrice": 0.50, "executableNoPrice": None,
            "settlementStatus": "SETTLED", "settlementResult": "YES",
            "hypotheticalYesReturn": 0.5, "hypotheticalYesReturnFeeOnly": 0.4,
            "hypotheticalYesReturnRealisticExecution": 0.35,
            "hypotheticalNoReturn": None, "hypotheticalNoReturnFeeOnly": None,
            "hypotheticalNoReturnRealisticExecution": None,
            "yesBid": 0.49, "yesAsk": 0.50, "gameId": "g1", "gameDate": "2026-08-01",
            "canonicalMarketFamily": "team_total", "researchCheckpoint": "CLOSING",
            "marketObservationId": "obs1", "fullUniverseMarketMovementToClose": None,
            "marketPriceAgeSeconds": None,
        },
        {
            "modelEvaluationAvailable": True, "modelFairProbability": 0.555, "side": "YES",
            "executableYesPrice": 0.51, "executableNoPrice": None,
            "settlementStatus": "SETTLED", "settlementResult": "NO",
            "hypotheticalYesReturn": -1.0, "hypotheticalYesReturnFeeOnly": -1.1,
            "hypotheticalYesReturnRealisticExecution": -1.0,
            "hypotheticalNoReturn": None, "hypotheticalNoReturnFeeOnly": None,
            "hypotheticalNoReturnRealisticExecution": None,
            "yesBid": 0.50, "yesAsk": 0.51, "gameId": "g2", "gameDate": "2026-08-02",
            "canonicalMarketFamily": "team_total", "researchCheckpoint": "CLOSING",
            "marketObservationId": "obs2", "fullUniverseMarketMovementToClose": None,
            "marketPriceAgeSeconds": None,
        },
    ]
    report, classified = build_validation_report(rows, ["2026-08-01", "2026-08-02"])
    assert report["causalOpportunitiesAudited"] == len(classified)
    assert report["oldQualifierCount"] == sum(1 for c in classified if c["oldQualifies"])
    assert report["newQualifierCount"] == sum(1 for c in classified if c["newQualifies"])
    assert (
        report["retainedCount"] + report["rejectedByFeesCount"]
        + report["tierDowngradedCount"] + report["unchangedUnqualifiedCount"]
        == report["causalOpportunitiesAudited"]
    )
    assert report["sideChangedCount"] == 0
    assert report["label"].startswith("DESCRIPTIVE / IN-SAMPLE")
    assert report["chronologicalSplit"]["maturity"] == "FRAMEWORK_ONLY_INSUFFICIENT_DATES"
