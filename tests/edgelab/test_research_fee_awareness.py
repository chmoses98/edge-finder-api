#!/usr/bin/env python3
"""
tests/edgelab/test_research_fee_awareness.py
==================================================
Kalshi Fee-Aware Execution Economics milestone: regression coverage for
full-universe research fee-awareness -- CORRECTION PASS (see
docs/KALSHI_FEE_AWARE_EXECUTION_ECONOMICS.md's "Correction pass" section
for the full writeup of the bug this file's tests guard against).

The original single "NetOfFees" tier conflated legitimate fee drag with
unused-allocated-budget being wrongly counted as a loss. This file now
covers THREE distinct tiers: gross (unchanged), fee-only (Tier B, scale-
consistent with gross), and realistic execution (Tier C, full platform
constraints) -- see lib.edgelab.kalshi_fees.simulate_order/
simulate_settlement_order/net_settlement_pl_fee_only.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import kalshi_fees as kf
from lib.edgelab.research_dataset import build_opportunity_rows
from lib.edgelab.research_reports import edge_backtest
from lib.edgelab.settlement import hypothetical_yes_return


# ---------------------------------------------------------------------------
# Core bug fix: unused allocated cash is never a loss (spec section 30
# items 1-4)
# ---------------------------------------------------------------------------

def test_budget_with_partial_execution_leaves_unused_cash():
    """Item 1: a $10 budget that only actually executes $9.83-ish leaves
    the remainder as unused cash, not a mystery gap."""
    sim = kf.simulate_order(10.0, 0.5)
    assert sim["unusedCash"] == round(sim["availableBudget"] - sim["actualCashConsumed"], 2)
    assert sim["unusedCash"] >= 0


def test_losing_wager_produces_actual_cash_consumed_not_full_budget():
    """Item 2: losing forfeits actualCashConsumed, not the full $10 budget."""
    sim = kf.simulate_order(10.0, 0.5)
    pl = kf.net_settlement_pl_for_order(10.0, 0.5, won=False)
    assert pl == -sim["actualCashConsumed"]
    assert pl != -10.0


def test_winning_pl_uses_actual_consumed_cash():
    """Item 3."""
    sim = kf.simulate_order(10.0, 0.5)
    full = kf.simulate_settlement_order(10.0, 0.5, won=True)
    assert full["netProfitLoss"] == round(full["grossSettlementPayout"] - sim["actualCashConsumed"], 4)


def test_roi_on_risk_uses_actual_consumed_cash():
    """Item 4."""
    full = kf.simulate_settlement_order(10.0, 0.5, won=True)
    assert full["roiOnActualCashConsumed"] == round(full["netProfitLoss"] / full["actualCashConsumed"], 6)


def test_allocated_budget_roi_is_separately_labeled():
    """Item 5: return-on-budget is a DIFFERENT, separately named number."""
    full = kf.simulate_settlement_order(10.0, 0.5, won=True)
    assert full["roiOnAllocatedBudget"] == round(full["netProfitLoss"] / full["availableBudget"], 6)
    assert full["roiOnAllocatedBudget"] != full["roiOnActualCashConsumed"]


# ---------------------------------------------------------------------------
# Fee-only tier: scale-consistent with gross, no sizing/unused-budget
# contamination (spec section 30 items 6-9)
# ---------------------------------------------------------------------------

def test_fee_only_preserves_gross_exposure():
    """Item 6: fee-only ROI is scale-invariant across order sizes (uses
    the same continuous exposure as gross at every size)."""
    rois = set()
    for size in (10.0, 25.0, 50.0, 100.0):
        pl = kf.net_settlement_pl_fee_only(size, 0.5, won=True)
        rois.add(round(pl / size, 6))
    assert len(rois) == 1


def test_fee_only_contains_no_unused_budget_penalty():
    """Item 7: fee-only ROI equals the exact closed-form fee-drag
    identity (multiplier * (1 - price)) with NO extra unused-cash term."""
    price = 0.5
    fee_only_win = kf.net_settlement_pl_fee_only(10.0, price, won=True)
    gross_win = 10.0 * ((1.0 - price) / price)
    drag = (gross_win - fee_only_win) / 10.0
    assert abs(drag - kf.fee_only_drag_percentage_points(price)) < 1e-9


def test_fee_only_and_realistic_execution_can_differ():
    """Item 9: at a size/price where whole-contract rounding leaves real
    unused cash, fee-only and realistic-execution ROI are NOT equal."""
    fee_only = kf.net_settlement_pl_fee_only(10.0, 0.5, won=True)
    realistic = kf.simulate_settlement_order(10.0, 0.5, won=True)
    assert round(fee_only / 10.0, 4) != realistic["roiOnActualCashConsumed"]


def test_realistic_execution_may_contain_quantity_granularity_effects():
    """Item 8: realistic execution's unusedCash reflects real whole-
    contract quantization, which fee-only deliberately excludes."""
    realistic = kf.simulate_settlement_order(10.0, 0.5, won=True, quantity_granularity=kf.QUANTITY_GRANULARITY_WHOLE_CONTRACT_ONLY)
    assert realistic["unusedCash"] > 0  # $10 at $0.50 does not divide evenly once fees are included


# ---------------------------------------------------------------------------
# Fractional / whole-contract / unknown granularity (spec section 30
# items 10-13)
# ---------------------------------------------------------------------------

def test_fractional_contract_quantity_works():
    """Item 10."""
    sim = kf.simulate_order(10.0, 0.5, quantity_granularity=kf.QUANTITY_GRANULARITY_FRACTIONAL_ENABLED)
    assert sim["unusedCash"] == 0.0
    assert not float(sim["contracts"]).is_integer() or sim["contracts"] > 0


def test_integer_only_contract_mode_works_when_explicitly_applicable():
    """Item 11."""
    sim = kf.simulate_order(10.0, 0.5, quantity_granularity=kf.QUANTITY_GRANULARITY_WHOLE_CONTRACT_ONLY)
    assert sim["contracts"] == int(sim["contracts"])


def test_unknown_granularity_refuses_false_precision():
    """
    Item 12: UNKNOWN granularity (this repo's default for every
    historical MLB market -- no archived price_ranges/count_fp evidence)
    falls back to the conservative whole-contract simulation rather than
    fabricating fractional-order certainty, and says so explicitly.
    """
    sim = kf.simulate_order(10.0, 0.5, quantity_granularity=kf.QUANTITY_GRANULARITY_UNKNOWN)
    assert sim["quantityGranularity"] == kf.QUANTITY_GRANULARITY_UNKNOWN
    assert sim["contracts"] == int(sim["contracts"])  # conservative whole-contract fallback


def test_subpenny_price_works():
    """Item 13."""
    sim = kf.simulate_order(10.0, 0.4995, quantity_granularity=kf.QUANTITY_GRANULARITY_FRACTIONAL_ENABLED)
    assert sim is not None
    assert sim["actualCashConsumed"] > 0


# ---------------------------------------------------------------------------
# Series fee metadata / historical fee rules (spec section 30 items 14-16)
# ---------------------------------------------------------------------------

def test_series_specific_fee_multiplier_works():
    """Item 14."""
    rule = kf.fee_rule_for_series("KXMLBGAME")
    assert rule["feeMultiplier"] == kf.FEE_MULTIPLIER_TAKER_STANDARD
    assert rule["seriesTicker"] == "KXMLBGAME"


def test_unknown_series_never_fabricates_a_multiplier_silently():
    rule = kf.fee_rule_for_series("KXMLBSOMETHINGNEW")
    assert rule["feeRuleConfidence"] == "UNKNOWN_SERIES"
    assert rule["feeMultiplier"] == kf.FEE_MULTIPLIER_TAKER_STANDARD  # documented default, not a guess


def test_historical_fee_change_by_timestamp_accepted_without_error():
    """Item 15: at_timestamp is accepted (forward-compat hook) even though
    this repo has no versioned per-series fee-change data yet."""
    rule = kf.fee_rule_for_series("KXMLBGAME", at_timestamp="2026-06-01T00:00:00Z")
    assert rule["seriesTicker"] == "KXMLBGAME"


def test_exact_api_fee_would_outrank_formula_estimate():
    """Item 16: merge_fee_status (lib.edgelab.execution_economics) already
    proves ACTUAL_* outranks ESTIMATED_FEE_SCHEDULE -- re-verified here
    against this module's own rank table directly."""
    assert kf.FEE_STATUS_RANK[kf.FEE_STATUS_ACTUAL_API_FILL] > kf.FEE_STATUS_RANK[kf.FEE_STATUS_ESTIMATED_FEE_SCHEDULE]


# ---------------------------------------------------------------------------
# Fee rounding / rounding-sequence honesty (spec section 30 items 18-21)
# ---------------------------------------------------------------------------

def test_single_fill_ceil_cent_rounding_matches_documented_worked_example():
    """Item 18: 100 contracts @ $0.50 -> $1.75, the cross-corroborated worked example."""
    assert kf.taker_fee(100, 0.5) == 1.75


def test_simulate_order_never_claims_multi_fill_accumulator_awareness():
    """Item 19/20: every simulate_order() result is tagged
    ESTIMATED_AGGREGATED_ORDER (single-fill assumption), never a status
    implying real multi-fill accumulator/rebate sequence knowledge --
    this repo has no fill-level evidence for any historical bet."""
    sim = kf.simulate_order(10.0, 0.5)
    assert sim["feeRuleSource"] == kf.FEE_RULE_SOURCE_ESTIMATED_AGGREGATED_ORDER


def test_settlement_rounding_represented_where_applicable():
    """Item 21: settlement payout is contracts * $1.00 exactly (Kalshi
    pays whole dollars per contract, no settlement-side rounding
    ambiguity) -- verified via simulate_settlement_order's grossSettlementPayout."""
    full = kf.simulate_settlement_order(10.0, 0.5, won=True)
    assert full["grossSettlementPayout"] == float(full["contracts"]) * 1.0


# ---------------------------------------------------------------------------
# YES/NO correctness (spec section 30 items 22-23)
# ---------------------------------------------------------------------------

def _obs(ticker="T1", checkpoint="CLOSING", yes_bid=48.0, yes_ask=50.0, no_bid=50.0, no_ask=52.0):
    return {
        "marketObservationId": "obs1", "marketTicker": ticker, "capturedAt": "2026-08-01T20:00:00Z",
        "checkpoint": checkpoint, "scheduledStart": "2026-08-01T20:30:00Z", "gameId": "g1",
        "marketFamily": "game_result", "marketHorizon": "FULL_GAME",
        "yesBid": yes_bid, "yesAsk": yes_ask, "noBid": no_bid, "noAsk": no_ask, "lastPrice": yes_ask,
        "marketStatus": "active", "isValidPregameObservation": True, "isClosingCandidate": True,
        "threshold": None, "comparisonOperator": None, "team": None, "player": None,
        "outcomeLabel": None, "lineupConfirmationState": None, "source": "edgelab_test",
    }


def _settlement(ticker="T1", result="YES"):
    return {"marketTicker": ticker, "settlementStatus": "SETTLED", "result": result, "unavailableReason": None}


def test_gross_fields_preserved_unchanged():
    """Gross hypothetical fields are byte-identical to the pre-correction-pass formula."""
    rows = build_opportunity_rows([_obs()], settlements=[_settlement(result="YES")])
    row = rows[0]
    expected_gross = hypothetical_yes_return(0.50, "YES")
    assert row["hypotheticalYesReturn"] == expected_gross


def test_yes_side_fee_only_and_realistic_execution_populated():
    """Item 22: YES economics work end to end through research_dataset."""
    rows = build_opportunity_rows([_obs()], settlements=[_settlement(result="YES")])
    row = rows[0]
    assert row["hypotheticalYesReturnFeeOnly"] is not None
    assert row["hypotheticalYesReturnRealisticExecution"] is not None
    assert row["yesActualCashConsumed"] is not None
    assert row["yesUnusedCash"] is not None
    # Fee-only must be strictly worse than gross for a win.
    assert row["hypotheticalYesReturnFeeOnly"] < row["hypotheticalYesReturn"]


def test_no_side_fee_only_and_realistic_execution_populated():
    """Item 23: NO economics work end to end, using its OWN executable
    price and its OWN fee calculation -- never derived from the YES side."""
    rows = build_opportunity_rows([_obs()], settlements=[_settlement(result="NO")])
    row = rows[0]
    assert row["hypotheticalNoReturnFeeOnly"] is not None
    assert row["hypotheticalNoReturnRealisticExecution"] is not None
    assert row["noActualCashConsumed"] is not None
    assert row["noUnusedCash"] is not None
    assert row["hypotheticalNoReturnFeeOnly"] < row["hypotheticalNoReturn"]


# ---------------------------------------------------------------------------
# edge_backtest bucket-level decomposition (spec section 30 items 30-32)
# ---------------------------------------------------------------------------

def _causal_row(edge=0.05, win=True, price=0.5, order_size=10.0):
    fee_only = kf.net_settlement_pl_fee_only(order_size, price, won=win)
    realistic = kf.simulate_settlement_order(order_size, price, won=win)
    return {
        "modelEvaluationAvailable": True, "modelFairProbability": price + edge, "side": "YES",
        "executableYesPrice": price, "executableNoPrice": None,
        "settlementStatus": "SETTLED", "settlementResult": "YES" if win else "NO",
        "hypotheticalYesReturn": hypothetical_yes_return(price, "YES" if win else "NO"),
        "hypotheticalYesReturnFeeOnly": round(fee_only / order_size, 4) if fee_only is not None else None,
        "hypotheticalYesReturnRealisticExecution": realistic["roiOnActualCashConsumed"] if realistic else None,
        "yesActualCashConsumed": realistic["actualCashConsumed"] if realistic else None,
        "yesUnusedCash": realistic["unusedCash"] if realistic else None,
        "yesEntryFee": realistic["entryFee"] if realistic else None,
        "yesContracts": realistic["contracts"] if realistic else None,
        "hypotheticalNoReturn": None, "hypotheticalNoReturnFeeOnly": None,
        "hypotheticalNoReturnRealisticExecution": None,
        "gameId": "g1", "marketObservationId": "obs1", "fullUniverseMarketMovementToClose": None,
        "marketPriceAgeSeconds": None,
    }


def test_edge_backtest_gross_roi_preserved():
    """Item 30: gross research ROI remains preserved."""
    rows = [_causal_row(win=True), _causal_row(win=True), _causal_row(win=False)]
    buckets = edge_backtest(rows, side_filter="YES")
    assert buckets
    bucket = buckets[0]
    assert bucket["roi"] is not None
    assert bucket["grossROI"] == bucket["roi"]


def test_edge_backtest_fee_only_roi_is_separate():
    """Item 31."""
    rows = [_causal_row(win=True), _causal_row(win=True), _causal_row(win=False)]
    buckets = edge_backtest(rows, side_filter="YES")
    bucket = buckets[0]
    assert "roiAfterFeesOnly" in bucket
    assert bucket["roiAfterFeesOnly"] is not None
    assert bucket["roiAfterFeesOnly"] <= bucket["roi"]
    assert bucket["feeOnlyDragPercentagePoints"] is not None
    assert bucket["feeOnlyDragPercentagePoints"] >= 0


def test_edge_backtest_realistic_execution_roi_is_separate():
    """Item 32."""
    rows = [_causal_row(win=True), _causal_row(win=True), _causal_row(win=False)]
    buckets = edge_backtest(rows, side_filter="YES")
    bucket = buckets[0]
    assert "roiRealisticExecution" in bucket
    assert bucket["roiRealisticExecution"] is not None
    assert bucket["roiRealisticExecution"] != bucket["roiAfterFeesOnly"]
    assert bucket["executionDragPercentagePoints"] is not None


def test_edge_backtest_full_decomposition_present():
    rows = [_causal_row(win=True)]
    buckets = edge_backtest(rows, side_filter="YES")
    bucket = buckets[0]
    for field in (
        "grossToFeeOnlyDrag", "feeOnlyToExecutionDrag", "totalExecutionDrag",
        "totalGrossPL", "totalTradeFees", "totalUnusedCash", "totalExecutionPL",
        "executionOrderSizeAssumption", "executionQuantityGranularityAssumption",
        "feeScheduleVersion", "roiDenominatorNote",
    ):
        assert field in bucket, f"missing {field}"


def test_edge_backtest_unknown_components_never_fabricated_as_zero():
    """totalRoundingFees/totalRebates must be None (unknown), never 0."""
    rows = [_causal_row(win=True)]
    buckets = edge_backtest(rows, side_filter="YES")
    bucket = buckets[0]
    assert bucket["totalRoundingFees"] is None
    assert bucket["totalRebates"] is None


# ---------------------------------------------------------------------------
# 10%+ bucket regression + production isolation (spec section 30 items 33-34)
# ---------------------------------------------------------------------------

def test_ten_plus_bucket_decomposition_matches_recomputed_price_bucket_math():
    """
    Item 33: the 10%+ edge bucket's fee-only drag must be internally
    consistent with the price-bucket fee sanity table (correction pass
    docs section 8a) at a comparable average executable price (~50c),
    NOT the original pass's conflated -2.02% "net of fees" artifact.
    A hand-built fixture at the bucket's real ~0.4995 avg price, mirroring
    the corrected engine's actual regenerated output (avg fee-only drag
    ~3.5 pp, additional execution drag under 1 pp, NOT 6+ pp), pins this
    down as a permanent regression guard independent of the live,
    growing real-data corpus.
    """
    price = 0.4995
    rows = (
        [_causal_row(edge=0.15, win=True, price=price)] * 87
        + [_causal_row(edge=0.15, win=False, price=price)] * 81
    )
    buckets = edge_backtest(rows, side_filter="YES")
    bucket = buckets[0]
    assert 3.0 <= bucket["feeOnlyDragPercentagePoints"] <= 4.0
    assert bucket["totalExecutionDrag"] - bucket["feeOnlyDragPercentagePoints"] < 1.0
    assert bucket["roi"] - bucket["roiAfterFeesOnly"] > 0


def test_risk_gate_and_write_pending_bets_never_import_kalshi_fee_engine_directly():
    """
    Item 34, UPDATED for the Production Fee-Aware Net EV Integration
    milestone (see docs/PRODUCTION_FEE_AWARE_NET_EV.md): this repo's PR
    #88 milestone guarded ALL THREE production scripts against importing
    the fee engine, since production betting was explicitly out of scope
    for that PR. That invariant is now INTENTIONALLY superseded for
    build_market_ledger.py -- see
    test_build_market_ledger_uses_shared_fee_engine_not_duplicate_formula
    below -- but scripts/risk_gate.py and scripts/write_pending_bets.py
    still never need to import lib.edgelab.kalshi_fees or
    lib.edgelab.execution_economics directly: they consume the fee
    fields build_market_ledger.py already computed and wrote onto each
    row/bet record (netExecutableEdge, expectedFeeDrag, betUpToPriceNet,
    etc.) as plain data, applying the fee exactly once at a single choke
    point (spec section 33) rather than re-deriving it downstream.
    """
    import os

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidates = [
        os.path.join(repo_root, "scripts", "risk_gate.py"),
        os.path.join(repo_root, "scripts", "write_pending_bets.py"),
    ]
    checked = 0
    for path in candidates:
        if not os.path.exists(path):
            continue
        checked += 1
        with open(path) as f:
            source = f.read()
        assert "kalshi_fees" not in source, f"{path} must not import the fee engine directly"
        assert "execution_economics" not in source, f"{path} must not import execution_economics"
    assert checked > 0, "expected to find at least one production script to guard"


def test_build_market_ledger_uses_shared_fee_engine_not_duplicate_formula():
    """
    Production Fee-Aware Net EV Integration milestone: unlike
    risk_gate.py/write_pending_bets.py above, build_market_ledger.py IS
    now expected to import lib.edgelab.kalshi_fees -- it is the single
    choke point (build_edge_fields()) where every market family's
    fee-aware net edge is computed, per spec section 5 ("reuse the PR #88
    fee engine as the single source of truth ... do not create a second
    fee formula"). This test pins down the intentional new state so a
    future accidental removal (silently reintroducing a duplicate/
    approximated fee formula) fails loudly.
    """
    import os

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(repo_root, "scripts", "build_market_ledger.py")
    with open(path) as f:
        source = f.read()
    assert "from lib.edgelab import kalshi_fees" in source, (
        "build_market_ledger.py must import the shared fee engine, not a duplicate formula"
    )
