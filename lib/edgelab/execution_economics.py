"""
lib/edgelab/execution_economics.py
======================================
Kalshi Fee-Aware Execution Economics milestone: sits on top of the pure
formula engine in lib.edgelab.kalshi_fees and implements the two things
that engine deliberately doesn't know about --

  1. The STAKE-EVIDENCE PRIORITY LADDER (spec section 6/9): what canonical
     stake to record given whatever mix of user-confirmed/exact/receipt/
     screenshot evidence is available for one wager. A Kalshi share-card
     "Initial cost" NEVER becomes stake automatically -- see
     determine_canonical_stake.

  2. FEE-AWARE REALIZED P/L (spec section 13): replacing
     lib.edgelab.settlement.realized_return_for_bet's WIN-case assumption
     that stake/entryPrice exactly equals a (fractional, fee-free)
     contract count. realized_return_for_bet itself is left UNCHANGED
     (it's still exactly correct for LOSS/PUSH/VOID, and is kept for any
     caller that still wants the simple, non-fee-aware historical
     formula) -- lib.edgelab.settlement.settle_bets_for_ticker now calls
     realized_pl_for_bet from THIS module instead, which is fee-aware and
     execution-status-aware.
"""
from lib.edgelab import kalshi_fees as kf

# ---------------------------------------------------------------------------
# Stake-evidence priority ladder
# ---------------------------------------------------------------------------

STAKE_EVIDENCE_USER_CONFIRMED = "USER_CONFIRMED"
STAKE_EVIDENCE_EXACT_API_EXECUTION = "EXACT_API_EXECUTION"
STAKE_EVIDENCE_EXACT_RECEIPT = "EXACT_RECEIPT"
STAKE_EVIDENCE_FEE_AWARE_INFERRED = "FEE_AWARE_WHOLE_DOLLAR_INFERRED"
STAKE_EVIDENCE_LEGACY_ASSUMED_EXACT = "LEGACY_ASSUMED_EXACT"
STAKE_EVIDENCE_AMBIGUOUS = "AMBIGUOUS_UNRESOLVED"

_CONFIDENCE_BY_SOURCE = {
    STAKE_EVIDENCE_USER_CONFIRMED: "HIGH",
    STAKE_EVIDENCE_EXACT_API_EXECUTION: "HIGH",
    STAKE_EVIDENCE_EXACT_RECEIPT: "HIGH",
    STAKE_EVIDENCE_FEE_AWARE_INFERRED: "MEDIUM",
    STAKE_EVIDENCE_AMBIGUOUS: "LOW",
    STAKE_EVIDENCE_LEGACY_ASSUMED_EXACT: "UNKNOWN",
}


def confidence_for_economics_source(source):
    """Pure. Coarse confidence rating for a given economicsSource value -- see placed_bet.schema.json's economicsConfidence field."""
    return _CONFIDENCE_BY_SOURCE.get(source, "UNKNOWN")


def determine_canonical_stake(
    *, user_confirmed_stake=None, exact_api_stake=None, exact_receipt_stake=None,
    share_card_initial_cost=None, price=None, whole_dollar_candidates=None, tolerance=0.01,
):
    """
    Pure. Implements the stake-evidence priority ladder (spec section 6):
      1. user_confirmed_stake -- authoritative; a screenshot supplied
         LATER never overwrites this (see docs/KALSHI_FEE_AWARE_EXECUTION_ECONOMICS.md).
      2. exact_api_stake -- real Kalshi order/fill data identifying total cash debited.
      3. exact_receipt_stake -- a receipt that explicitly gives fee-inclusive cash outlay.
      4. Fee-aware whole-dollar reconstruction (lib.edgelab.kalshi_fees.
         reconstruct_whole_dollar_stake) from share_card_initial_cost +
         price, ONLY when it finds exactly one whole-dollar candidate
         within `tolerance` -- never nearest-dollar rounding (spec
         section 7).
      5. Otherwise: AMBIGUOUS -- stake is None, never guessed.

    Each priority level is checked only when every level above it was
    NOT supplied (None) -- an explicit but numerically-zero-adjacent
    value (e.g. a real $0 receipt on a void) is still "supplied" as long
    as it is not None.

    Returns {"stake": float|None, "source": <STAKE_EVIDENCE_*>,
             "confidence": str, "candidates": [float, ...], "detail": {...}}.
    `detail` carries reconstruct_whole_dollar_stake's own return value
    when the whole-dollar path was attempted, empty dict otherwise.
    """
    if user_confirmed_stake is not None:
        return {
            "stake": round(user_confirmed_stake, 2), "source": STAKE_EVIDENCE_USER_CONFIRMED,
            "confidence": "HIGH", "candidates": [round(user_confirmed_stake, 2)], "detail": {},
        }
    if exact_api_stake is not None:
        return {
            "stake": round(exact_api_stake, 2), "source": STAKE_EVIDENCE_EXACT_API_EXECUTION,
            "confidence": "HIGH", "candidates": [round(exact_api_stake, 2)], "detail": {},
        }
    if exact_receipt_stake is not None:
        return {
            "stake": round(exact_receipt_stake, 2), "source": STAKE_EVIDENCE_EXACT_RECEIPT,
            "confidence": "HIGH", "candidates": [round(exact_receipt_stake, 2)], "detail": {},
        }
    if share_card_initial_cost is not None and price is not None:
        result = kf.reconstruct_whole_dollar_stake(
            share_card_initial_cost, price, candidates=whole_dollar_candidates, tolerance=tolerance,
        )
        if result["status"] == "UNIQUE_MATCH":
            return {
                "stake": result["stake"], "source": STAKE_EVIDENCE_FEE_AWARE_INFERRED,
                "confidence": "MEDIUM", "candidates": result["candidates"], "detail": result,
            }
        return {
            "stake": None, "source": STAKE_EVIDENCE_AMBIGUOUS, "confidence": "LOW",
            "candidates": result["candidates"], "detail": result,
        }
    return {"stake": None, "source": STAKE_EVIDENCE_AMBIGUOUS, "confidence": "LOW", "candidates": [], "detail": {}}


# ---------------------------------------------------------------------------
# Fee-status merge (spec section 17): ACTUAL > RECONSTRUCTED_EXACT >
# ESTIMATED > UNKNOWN, never let a lower tier silently overwrite a higher one.
# ---------------------------------------------------------------------------

def merge_fee_status(existing_status, existing_fee, new_status, new_fee):
    """
    Pure. Compares two (feeStatus, fee) pairs by kalshi_fees.FEE_STATUS_RANK
    and returns whichever pair ranks higher -- ties (e.g. a fresh
    ESTIMATED_FEE_SCHEDULE recompute replacing an older one of the same
    tier) prefer the NEW pair, so a corrected/rerun estimate can still
    update in place; a strictly lower-ranked new status never overwrites
    a strictly higher-ranked existing one.
    """
    existing_rank = kf.FEE_STATUS_RANK.get(existing_status, -1)
    new_rank = kf.FEE_STATUS_RANK.get(new_status, -1)
    if new_rank >= existing_rank:
        return new_status, new_fee
    return existing_status, existing_fee


# ---------------------------------------------------------------------------
# Fee-aware realized P/L (spec section 13)
# ---------------------------------------------------------------------------

EXECUTION_STATUS_HELD_TO_SETTLEMENT = "HELD_TO_SETTLEMENT"
EXECUTION_STATUS_SOLD_EARLY = "SOLD_EARLY"
EXECUTION_STATUS_PARTIAL_CLOSE = "PARTIAL_CLOSE"
EXECUTION_STATUS_VOID_REFUND = "VOID_REFUND"
EXECUTION_STATUS_UNKNOWN = "UNKNOWN"


def estimate_contracts_for_stake(stake, price):
    """
    Pure. Best-effort contract count when no exact `contracts` evidence
    exists: the same fee-aware "spend $stake" order-entry simulation
    used everywhere else in this milestone
    (lib.edgelab.kalshi_fees.max_contracts_for_cash) -- always an
    IMPROVEMENT over the old stake/price (fractional, fee-free) shortcut,
    never claimed as exact. Returns None for invalid inputs.
    """
    if stake is None or price is None or not (0 < price < 1):
        return None
    return kf.max_contracts_for_cash(stake, price)


def realized_pl_for_bet(
    *, execution_status, stake, bet_result, entry_price=None, contracts=None,
    exit_sale_proceeds=None, actual_cash_consumed=None, quantity_granularity=None,
):
    """
    Pure. Fee-aware net P/L, execution-status-aware (spec section 13),
    AND -- correction pass, spec section 12 -- actualCashConsumed-aware:
    the function lib.edgelab.settlement.settle_bets_for_ticker calls
    instead of the older realized_return_for_bet (kept unchanged
    elsewhere for any caller that still wants the simple, non-fee-aware
    formula).

    ============================================================
    CORRECTION PASS: `stake` is the user's INTENDED/ALLOCATED cash
    commitment (unchanged meaning -- "I put $10 on it" remains
    authoritative). It is NOT automatically the same as
    actualCashConsumed, the amount the platform actually debited for the
    executed position -- a whole-contract order frequently can't deploy
    every last cent of an allocated budget (see
    lib.edgelab.kalshi_fees.simulate_order's unusedCash). The ORIGINAL
    version of this function computed `grossSettlementPayout - stake`
    for a WIN and `-stake` for a LOSS -- both silently assumed the full
    stake was genuinely at risk, contaminating net P/L with unused-
    budget effects exactly like the research-simulation bug this same
    correction pass fixes in lib.edgelab.kalshi_fees.
    ============================================================

    execution_status is None/UNKNOWN treated as HELD_TO_SETTLEMENT for
    backward compatibility.

    Evidence precedence for the cash basis used in the HELD_TO_SETTLEMENT
    branch:
      1. `actual_cash_consumed` AND `contracts` both supplied (exact
         evidence, e.g. a real receipt/fill) -- used directly, no
         simulation.
      2. Otherwise, `stake` is treated as an ALLOCATED BUDGET and run
         through lib.edgelab.kalshi_fees.simulate_settlement_order (the
         same fee-aware order-entry simulation used everywhere else in
         this milestone) to derive a best-effort actualCashConsumed and
         contracts -- an honest estimate, never claimed exact, but
         always closer to reality than assuming every cent of `stake`
         was genuinely deployed.

    SOLD_EARLY / PARTIAL_CLOSE: net = exit_sale_proceeds - basis, where
      basis is actual_cash_consumed if known, else `stake` (the best
      available estimate of what was originally deployed into the
      closed position) -- NEVER the win/loss settlement formula (spec
      section 13's explicit requirement).

    VOID_REFUND: net = 0.0 (whatever was deployed is fully returned).

    UNKNOWN (only reachable if a caller explicitly passes it rather than
      None): returns None -- an unknown cash shape is never guessed.

    Returns None whenever a required input for the applicable branch is
    missing.
    """
    status = execution_status or EXECUTION_STATUS_HELD_TO_SETTLEMENT
    if stake is None:
        return None

    if status in (EXECUTION_STATUS_SOLD_EARLY, EXECUTION_STATUS_PARTIAL_CLOSE):
        if exit_sale_proceeds is None:
            return None
        basis = actual_cash_consumed if actual_cash_consumed is not None else stake
        return round(exit_sale_proceeds - basis, 4)

    if status == EXECUTION_STATUS_VOID_REFUND:
        return 0.0

    if status == EXECUTION_STATUS_UNKNOWN:
        return None

    # HELD_TO_SETTLEMENT (default)
    if bet_result is None:
        return None
    if bet_result in ("PUSH", "VOID"):
        return 0.0
    if bet_result not in ("WIN", "LOSS"):
        return None

    if actual_cash_consumed is not None and contracts is not None:
        # Exact evidence for both cash-consumed and contract count.
        payout = float(contracts) if bet_result == "WIN" else 0.0
        return round(payout - actual_cash_consumed, 4)

    # No exact evidence -- `stake` is the allocated budget, simulated
    # through the same fee-aware order-entry engine used everywhere else
    # (never assumes the full stake was genuinely deployed).
    granularity = quantity_granularity or kf.QUANTITY_GRANULARITY_UNKNOWN
    sim = kf.simulate_settlement_order(stake, entry_price, bet_result == "WIN", quantity_granularity=granularity)
    if sim is None:
        return None
    return sim["netProfitLoss"]
