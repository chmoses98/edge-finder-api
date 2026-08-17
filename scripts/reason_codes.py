#!/usr/bin/env python3
"""
scripts/reason_codes.py
========================
Phase 1F: Structured reason code definitions.

Every market ledger row and execution slip entry must carry a `reasonCodes` list.
Codes are strings. Multiple codes allowed per row.

Approval codes (bet can proceed):
  STARTER_CONFIRMED
  LINEUP_CONFIRMED_OFFICIAL
  LINEUP_ADJ_APPLIED
  LINEUP_ADJ_UNAVAILABLE_BUT_OFFICIAL_CONFIRMED
  EXECUTABLE_EDGE_ABOVE_THRESHOLD
  RAW_EDGE_STRONG
  F5_MARKET_MAPPED_CONFIDENTLY
  PRICE_WITHIN_MAX
  MARKET_REAL_MONEY_ELIGIBLE

Rejection/pass codes (bet blocked or passed):
  PRICE_MOVED_BEYOND_MAX
  LINEUP_PROJECTED_ONLY
  LINEUP_MISSING
  LINEUP_SOURCE_CONFLICT
  LINEUP_ADJ_UNAVAILABLE
  F5_TIE_MARKET_UNMAPPED
  F5_MAPPING_AMBIGUOUS
  EXECUTABLE_EDGE_BELOW_THRESHOLD
  STALE_PRICE_SNAPSHOT
  STALE_SLATE_DATE
  MARKET_SUSPENDED
  TICKER_MISSING
  ACTUAL_ENTRY_WORSE_THAN_MAX
  BET_SHOULD_HAVE_BEEN_PASSED_AT_FILL
"""

# ── Approval codes ─────────────────────────────────────────────────────────────
STARTER_CONFIRMED                      = 'STARTER_CONFIRMED'
LINEUP_CONFIRMED_OFFICIAL              = 'LINEUP_CONFIRMED_OFFICIAL'
LINEUP_ADJ_APPLIED                     = 'LINEUP_ADJ_APPLIED'
LINEUP_ADJ_UNAVAILABLE_BUT_OFFICIAL    = 'LINEUP_ADJ_UNAVAILABLE_BUT_OFFICIAL_CONFIRMED'
EXECUTABLE_EDGE_ABOVE_THRESHOLD        = 'EXECUTABLE_EDGE_ABOVE_THRESHOLD'
RAW_EDGE_STRONG                        = 'RAW_EDGE_STRONG'
F5_MARKET_MAPPED_CONFIDENTLY           = 'F5_MARKET_MAPPED_CONFIDENTLY'
PRICE_WITHIN_MAX                       = 'PRICE_WITHIN_MAX'
MARKET_REAL_MONEY_ELIGIBLE             = 'MARKET_REAL_MONEY_ELIGIBLE'

# ── Rejection/pass codes ───────────────────────────────────────────────────────
PRICE_MOVED_BEYOND_MAX                 = 'PRICE_MOVED_BEYOND_MAX'
LINEUP_PROJECTED_ONLY                  = 'LINEUP_PROJECTED_ONLY'
LINEUP_MISSING                         = 'LINEUP_MISSING'
LINEUP_SOURCE_CONFLICT                 = 'LINEUP_SOURCE_CONFLICT'
LINEUP_ADJ_UNAVAILABLE                 = 'LINEUP_ADJ_UNAVAILABLE'
F5_TIE_MARKET_UNMAPPED                 = 'F5_TIE_MARKET_UNMAPPED'
F5_MAPPING_AMBIGUOUS                   = 'F5_MAPPING_AMBIGUOUS'
EXECUTABLE_EDGE_BELOW_THRESHOLD        = 'EXECUTABLE_EDGE_BELOW_THRESHOLD'
STALE_PRICE_SNAPSHOT                   = 'STALE_PRICE_SNAPSHOT'
STALE_SLATE_DATE                       = 'STALE_SLATE_DATE'
MARKET_SUSPENDED                       = 'MARKET_SUSPENDED'
TICKER_MISSING                         = 'TICKER_MISSING'
ACTUAL_ENTRY_WORSE_THAN_MAX            = 'ACTUAL_ENTRY_WORSE_THAN_MAX'
BET_SHOULD_HAVE_BEEN_PASSED_AT_FILL    = 'BET_SHOULD_HAVE_BEEN_PASSED_AT_FILL'

ALL_APPROVAL_CODES = {
    STARTER_CONFIRMED,
    LINEUP_CONFIRMED_OFFICIAL,
    LINEUP_ADJ_APPLIED,
    LINEUP_ADJ_UNAVAILABLE_BUT_OFFICIAL,
    EXECUTABLE_EDGE_ABOVE_THRESHOLD,
    RAW_EDGE_STRONG,
    F5_MARKET_MAPPED_CONFIDENTLY,
    PRICE_WITHIN_MAX,
    MARKET_REAL_MONEY_ELIGIBLE,
}

ALL_REJECTION_CODES = {
    PRICE_MOVED_BEYOND_MAX,
    LINEUP_PROJECTED_ONLY,
    LINEUP_MISSING,
    LINEUP_SOURCE_CONFLICT,
    LINEUP_ADJ_UNAVAILABLE,
    F5_TIE_MARKET_UNMAPPED,
    F5_MAPPING_AMBIGUOUS,
    EXECUTABLE_EDGE_BELOW_THRESHOLD,
    STALE_PRICE_SNAPSHOT,
    STALE_SLATE_DATE,
    MARKET_SUSPENDED,
    TICKER_MISSING,
    ACTUAL_ENTRY_WORSE_THAN_MAX,
    BET_SHOULD_HAVE_BEEN_PASSED_AT_FILL,
}

# ── Informational/provenance codes ─────────────────────────────────────────────
# Evidence-quality tags. These never gate accept/reject on their own (the
# confidence-tier cap already happened upstream in build_market_ledger.py's
# cap_tier_for_first_inning_evidence_quality) -- they exist so the evidence
# hierarchy is machine-readable on every row, not just embedded in free-text
# notes/gatesFired. See lib.research.first_inning_context for the source
# vocabulary (FIRST_INNING_NATIVE/PARTIAL/GENERIC_FALLBACK/INSUFFICIENT_DATA).
FIRST_INNING_NATIVE_EVIDENCE           = 'FIRST_INNING_NATIVE_EVIDENCE'
FIRST_INNING_PARTIAL_EVIDENCE          = 'FIRST_INNING_PARTIAL_EVIDENCE'
FIRST_INNING_GENERIC_FALLBACK          = 'FIRST_INNING_GENERIC_FALLBACK'
FIRST_INNING_INSUFFICIENT_DATA         = 'FIRST_INNING_INSUFFICIENT_DATA'

ALL_INFORMATIONAL_CODES = {
    FIRST_INNING_NATIVE_EVIDENCE,
    FIRST_INNING_PARTIAL_EVIDENCE,
    FIRST_INNING_GENERIC_FALLBACK,
    FIRST_INNING_INSUFFICIENT_DATA,
}

ALL_CODES = ALL_APPROVAL_CODES | ALL_REJECTION_CODES | ALL_INFORMATIONAL_CODES


def build_reason_codes(row_status, row_data):
    """
    Build a reasonCodes list for a market ledger row based on its status and data.

    Args:
        row_status: 'Accepted' | 'Rejected' | 'Missing Data' | 'Evaluation Failed'
        row_data: dict with row fields

    Returns:
        list of reason code strings
    """
    codes = []

    if row_status == 'Accepted':
        if row_data.get('marketTicker'):
            codes.append(MARKET_REAL_MONEY_ELIGIBLE)
        else:
            codes.append(TICKER_MISSING)

        cal_edge = row_data.get('calibratedEdgeVsExecutable') or row_data.get('edge')
        if cal_edge is not None and cal_edge > 0:
            codes.append(EXECUTABLE_EDGE_ABOVE_THRESHOLD)

        raw_edge = row_data.get('rawEdgeVsExecutable')
        if raw_edge is not None and raw_edge > 3.0:
            codes.append(RAW_EDGE_STRONG)

        lineup_official = row_data.get('lineupConfirmedOfficial')
        if lineup_official:
            codes.append(LINEUP_CONFIRMED_OFFICIAL)
            if row_data.get('lineupAdjApplied'):
                codes.append(LINEUP_ADJ_APPLIED)
            elif not row_data.get('lineupAdjAvailable'):
                codes.append(LINEUP_ADJ_UNAVAILABLE_BUT_OFFICIAL)

        if row_data.get('maxBetPrice') is not None:
            exec_price = row_data.get('executablePriceUsed')
            max_price  = row_data.get('maxBetPrice')
            if exec_price is not None and max_price is not None:
                if exec_price <= max_price:
                    codes.append(PRICE_WITHIN_MAX)

    elif row_status == 'Rejected':
        reason = row_data.get('rejectionReason', '') or ''
        gates  = row_data.get('gatesFired', []) or []

        # Map common rejection patterns to reason codes
        if 'PRICE_MOVED_BEYOND_MAX' in reason or any('PRICE_MOVED_BEYOND_MAX' in g for g in gates):
            codes.append(PRICE_MOVED_BEYOND_MAX)
        if 'lineupConfirmed=False' in reason or any('lineup' in g.lower() for g in gates):
            # Determine whether projected or missing
            lineup_status = row_data.get('lineupStatus', '')
            if lineup_status == 'projected':
                codes.append(LINEUP_PROJECTED_ONLY)
            elif lineup_status == 'missing':
                codes.append(LINEUP_MISSING)
            else:
                codes.append(LINEUP_PROJECTED_ONLY)  # conservative default
        if 'suspended' in reason.lower() or 'Rule 81' in reason or 'Rule 71 market suspension' in reason:
            codes.append(MARKET_SUSPENDED)
        if 'edge' in reason.lower() and 'below' in reason.lower():
            codes.append(EXECUTABLE_EDGE_BELOW_THRESHOLD)
        if 'F5_MAPPING_AMBIGUOUS' in reason or 'F5_MAPPING_AMBIGUOUS' in str(gates):
            codes.append(F5_MAPPING_AMBIGUOUS)
        if 'F5_TIE_MARKET_UNMAPPED' in reason or 'F5_TIE_MARKET_UNMAPPED' in str(gates):
            codes.append(F5_TIE_MARKET_UNMAPPED)

    elif row_status == 'Missing Data':
        missing = row_data.get('missingFields', []) or []
        if any('ticker' in f.lower() for f in missing):
            codes.append(TICKER_MISSING)

    # YRFI/NRFI explanation guard: check for banned reasoning patterns
    market = row_data.get('market', '')
    notes  = row_data.get('notes', '') or ''
    explanation = row_data.get('explanation', '') or ''
    combined_text = (notes + ' ' + explanation).lower()

    if market in ('YRFI', 'NRFI'):
        banned_phrases = [
            'bullpen exposure',
            'full-game bullpen',
            'short starter leash',
            'average innings per start',
            'pen arrives by inning',
            'late-game bullpen fatigue',
            'arrives in inning',
        ]
        for phrase in banned_phrases:
            if phrase in combined_text:
                codes.append('YRFI_NRFI_BANNED_REASONING_DETECTED')
                break

        fi_ctx = row_data.get('firstInningContext') or {}
        evidence_quality_code = {
            'FIRST_INNING_NATIVE': FIRST_INNING_NATIVE_EVIDENCE,
            'FIRST_INNING_PARTIAL': FIRST_INNING_PARTIAL_EVIDENCE,
            'GENERIC_FALLBACK': FIRST_INNING_GENERIC_FALLBACK,
            'INSUFFICIENT_DATA': FIRST_INNING_INSUFFICIENT_DATA,
        }.get(fi_ctx.get('evidenceQuality'))
        if evidence_quality_code:
            codes.append(evidence_quality_code)

    return codes
