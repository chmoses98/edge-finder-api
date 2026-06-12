#!/usr/bin/env python3
"""
scripts/bet_eligibility.py — Bet Eligibility / CLV / Review Status Classifier
================================================================================
Rule 71 patch: Separates LIVE BET ELIGIBILITY from CLV/REVIEW INTEGRITY.

PRINCIPLE:
  Live bet generation answers: "Can we safely bet this market RIGHT NOW?"
  CLV/review answers:          "After the game, did we capture closing-line data?"

These are independent. Missing CLV data NEVER blocks a live actionable bet.

THREE STATUS FIELDS (added to every market ledger row):

  bet_eligibility_status — Is the live bet safe to place?
    "actionable"                — all identity/price checks pass; real-money eligible
    "paper"                     — passes identity/price but market is paper-only per rules
    "rejected"                  — edge below threshold (evaluated, no qualifying edge)
    "blocked_market_identity"   — no valid Kalshi ticker
    "blocked_no_price"          — ticker exists but no entry price/probability
    "blocked_ambiguous_market"  — multiple tickers match, cannot disambiguate
    "blocked_existing_market_rule" — rule-based hard block (Rule 34, Rule 81, etc.)

  clv_capture_status — Will we be able to calculate closing line value?
    "ready"                 — closing snapshot already captured
    "pending_capture"       — bet logged, closing snapshot not yet taken (pre-game)
    "unavailable"           — closing snapshot was missed or Kalshi history unavailable
    "missing_close_snapshot" — specifically identified that snapshot step was missed
    "not_applicable_yet"    — game has not started; CLV not applicable yet

  review_integrity_status — Can we fully review this bet post-game?
    "full_review_ready"         — CLV captured, result settles via normal pipeline
    "settlement_only"           — result settles but CLV unavailable; ROI tracked, no CLV
    "needs_close_backfill"      — CLV pending; will be filled after game
    "cannot_review_market_identity" — identity fields missing; CLV and settlement both uncertain

BLOCKING RULES (bet_eligibility_status):
  HARD BLOCK (blocked_*):
    - No Kalshi ticker for this game/market/side
    - No entry price / no yes_price / no numeric probability
    - Ambiguous ticker (multiple candidates, cannot resolve)
    - Market stale / expired / wrong date
    - Unsupported market type
    - Corrupted market identity
    - Rule-based hard blocks unrelated to CLV (Rule 34, Rule 81, etc.)

  NOT A BLOCK:
    - CLV unavailable or pending
    - Closing snapshot missing
    - Historical CLV not backfilled
    - review_integrity_status is anything other than "full_review_ready"
    - clv_capture_status is anything
"""


# ── bet_eligibility_status values ─────────────────────────────────────────────

BET_ELIGIBLE      = "actionable"
BET_PAPER         = "paper"
BET_REJECTED      = "rejected"
BET_BLOCK_IDENTITY = "blocked_market_identity"
BET_BLOCK_PRICE   = "blocked_no_price"
BET_BLOCK_AMBIGUOUS = "blocked_ambiguous_market"
BET_BLOCK_RULE    = "blocked_existing_market_rule"

# ── clv_capture_status values ─────────────────────────────────────────────────

CLV_READY         = "ready"
CLV_PENDING       = "pending_capture"
CLV_UNAVAILABLE   = "unavailable"
CLV_MISSING_SNAP  = "missing_close_snapshot"
CLV_NOT_YET       = "not_applicable_yet"

# ── review_integrity_status values ────────────────────────────────────────────

REVIEW_FULL       = "full_review_ready"
REVIEW_SETTLE_ONLY = "settlement_only"
REVIEW_BACKFILL   = "needs_close_backfill"
REVIEW_NO_IDENTITY = "cannot_review_market_identity"


def classify_bet_eligibility(
    market_ticker,        # str | None — Kalshi market ticker
    entry_price,          # float | None — current yes_price or American odds (numeric)
    ledger_status,        # str — 'Accepted'|'Rejected'|'Missing Data'|'Evaluation Failed'
    rule_block_reason,    # str | None — rule-based hard block reason (e.g. Rule 34, Rule 81)
    is_paper_only,        # bool — True if market is paper-only per rules (Game Total, RL)
    ambiguous_ticker,     # bool — True if multiple tickers match and cannot disambiguate
    missing_fields=None,  # list[str] | None — missing identity/price fields
    clv_snapshot_captured=None,  # bool | None — True=captured, False=missed, None=not yet
    market_ticker_valid=None,    # bool | None — explicit ticker validity override
):
    """
    Classify the three independent status fields for a market ledger row.

    Returns dict with keys:
        bet_eligibility_status, clv_capture_status, review_integrity_status, eligibility_reason
    """
    missing_fields = missing_fields or []

    # ── 1. Determine bet_eligibility_status ───────────────────────────────────

    # Rule-based hard block (Rule 34 NRFI, Rule 81 RL, etc.) — unrelated to CLV
    if rule_block_reason:
        return _make_result(
            BET_BLOCK_RULE,
            _clv_from_ticker(market_ticker, clv_snapshot_captured),
            REVIEW_NO_IDENTITY if not market_ticker else REVIEW_SETTLE_ONLY,
            f"Rule block: {rule_block_reason}",
        )

    # No Kalshi ticker — cannot place a real-money bet
    ticker_missing = (
        not market_ticker
        or market_ticker_valid is False
        or any("ticker" in f.lower() for f in missing_fields)
    )
    if ticker_missing:
        return _make_result(
            BET_BLOCK_IDENTITY,
            CLV_UNAVAILABLE,
            REVIEW_NO_IDENTITY,
            "No valid Kalshi ticker for this market/game/side",
        )

    # Ambiguous ticker — multiple candidates, cannot safely pick one
    if ambiguous_ticker:
        return _make_result(
            BET_BLOCK_AMBIGUOUS,
            CLV_UNAVAILABLE,
            REVIEW_NO_IDENTITY,
            "Ambiguous ticker: multiple Kalshi markets match this game/market/side",
        )

    # No entry price — ticker exists but no current Kalshi price
    price_missing = (
        entry_price is None
        or any("price" in f.lower() or "line" in f.lower() for f in missing_fields)
    )
    if price_missing or ledger_status == "Missing Data":
        return _make_result(
            BET_BLOCK_PRICE,
            CLV_UNAVAILABLE,
            REVIEW_NO_IDENTITY,
            "No current Kalshi entry price for this market",
        )

    # Evaluation failed — cannot determine edge
    if ledger_status == "Evaluation Failed":
        return _make_result(
            BET_BLOCK_RULE,
            CLV_NOT_YET,
            REVIEW_SETTLE_ONLY,
            "Market evaluation failed — cannot determine edge",
        )

    # ── 2. At this point ticker + price are valid. Classify by ledger result ──

    # Paper-only market (Game Total Rule 71 suspension, RL Rule 81, etc.)
    if is_paper_only:
        clv_status = _clv_from_ticker(market_ticker, clv_snapshot_captured)
        review_status = _review_from_clv(clv_status, market_ticker)
        return _make_result(
            BET_PAPER,
            clv_status,
            review_status,
            "Paper-only market per current model rules (Rule 71/81)",
        )

    # Edge below threshold — rejected but market identity is clean
    if ledger_status == "Rejected":
        clv_status = _clv_from_ticker(market_ticker, clv_snapshot_captured)
        review_status = _review_from_clv(clv_status, market_ticker)
        return _make_result(
            BET_REJECTED,
            clv_status,
            review_status,
            "Edge below threshold — no qualifying bet",
        )

    # Accepted — actionable real-money bet
    clv_status = _clv_from_ticker(market_ticker, clv_snapshot_captured)
    review_status = _review_from_clv(clv_status, market_ticker)
    return _make_result(
        BET_ELIGIBLE,
        clv_status,
        review_status,
        "Valid ticker, valid price, edge meets threshold — bet is actionable",
    )


def _clv_from_ticker(market_ticker, clv_snapshot_captured):
    """Derive clv_capture_status from ticker presence and snapshot state."""
    if not market_ticker:
        return CLV_UNAVAILABLE
    if clv_snapshot_captured is True:
        return CLV_READY
    if clv_snapshot_captured is False:
        return CLV_MISSING_SNAP
    # None = not yet attempted (pre-game / live)
    return CLV_NOT_YET


def _review_from_clv(clv_status, market_ticker):
    """Derive review_integrity_status from CLV capture status."""
    if not market_ticker:
        return REVIEW_NO_IDENTITY
    if clv_status == CLV_READY:
        return REVIEW_FULL
    if clv_status in (CLV_NOT_YET, CLV_PENDING):
        return REVIEW_BACKFILL
    # CLV unavailable or missing snapshot — settlement still possible
    return REVIEW_SETTLE_ONLY


def _make_result(bet_elig, clv_cap, review_int, reason):
    return {
        "bet_eligibility_status":  bet_elig,
        "clv_capture_status":      clv_cap,
        "review_integrity_status": review_int,
        "eligibility_reason":      reason,
    }


# ── Convenience: patch a make_row dict in-place ───────────────────────────────

def apply_eligibility(row, clv_snapshot_captured=None):
    """
    Given a market ledger row (from build_market_ledger.make_row),
    compute and attach the three status fields.

    Designed to be called AFTER the row is built, so it never interferes
    with existing edge/confidence/price logic.

    CRITICAL: This function NEVER changes 'status', 'edge', 'confidence',
    'betSize', or any existing field. It only ADDS the three new fields.
    """
    market        = row.get("market", "")
    ticker        = row.get("marketTicker") or row.get("ticker")
    price         = row.get("kalshiPrice")
    ledger_status = row.get("status", "")
    gates_fired   = row.get("gatesFired", [])
    missing_fields = row.get("missingFields") or []
    rejection_reason = row.get("rejectionReason") or ""

    # Detect paper-only markets (suspended by rule, not CLV data)
    PAPER_ONLY_MARKETS = {"Game_Total", "RL_Away", "RL_Home"}
    is_paper_only = market in PAPER_ONLY_MARKETS

    # Detect rule-based hard blocks from gatesFired and rejectionReason
    # These are existing market rules — NOT CLV data
    rule_block_texts = [
        "Rule 34", "Rule 81", "Rule 83", "Rule 76",
        "suspended", "unsupported market",
    ]
    rule_block_reason = None
    for text in rule_block_texts:
        for gate in gates_fired:
            if text.lower() in str(gate).lower():
                rule_block_reason = str(gate)
                break
        if not rule_block_reason and text.lower() in rejection_reason.lower():
            rule_block_reason = rejection_reason
            break

    # Paper-only markets with a price are still "paper" eligibility (not "blocked_rule")
    if is_paper_only and price is not None:
        rule_block_reason = None  # don't block — classify as paper below

    result = classify_bet_eligibility(
        market_ticker=ticker,
        entry_price=price,
        ledger_status=ledger_status,
        rule_block_reason=rule_block_reason,
        is_paper_only=is_paper_only,
        ambiguous_ticker=False,  # build_market_ledger resolves ambiguity before building rows
        missing_fields=missing_fields,
        clv_snapshot_captured=clv_snapshot_captured,
    )

    row["bet_eligibility_status"]  = result["bet_eligibility_status"]
    row["clv_capture_status"]      = result["clv_capture_status"]
    row["review_integrity_status"] = result["review_integrity_status"]
    row["eligibility_reason"]      = result["eligibility_reason"]

    return row
