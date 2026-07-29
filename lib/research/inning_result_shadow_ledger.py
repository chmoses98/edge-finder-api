#!/usr/bin/env python3
"""
lib/research/inning_result_shadow_ledger.py
================================================
Model Performance Phase 2A -- RESEARCH-ONLY shadow market ledger for
F3/F5/F7 inning-result contracts (Part 9).

Every row is built by build_shadow_ledger_row(), a pure function: no
file I/O, no network, no clock reads (the caller supplies
snapshot_timestamp explicitly), deterministic given deterministic
inputs. This module is never imported by any production script
(scripts/build_market_ledger.py, scripts/risk_gate.py,
scripts/write_pending_bets.py, scripts/protect_slate.py,
scripts/validate_slate_final.py).

Eligibility (Part 10) is derived solely from the market's verified
structure status:
  - STRUCTURE_THREE_WAY (F5 today) -> paper-eligible, real-money
    ineligible, activationStatus="PAPER_ONLY".
  - STRUCTURE_UNVERIFIED (F3/F7 today) -> research-eligible only, NOT
    paper-eligible, activationStatus="UNRESOLVED" -- no synthetic
    model edge is ever attached to an unresolved-structure row.

Executable-edge convention (mission-mandated): YES-buy edge always
uses the YES ASK (never midpoint/last); NO-buy edge always uses the
derived NO ASK (= 1 - YES_BID, since this repository's own snapshot
format has no independently observed NO-side pricing -- see
docs/research/KALSHI_MARKET_TAXONOMY.md's "Documented gaps" section).
"""
from lib.research.market_taxonomy import (
    classify_inning_result_market,
    STRUCTURE_THREE_WAY,
    STRUCTURE_UNVERIFIED,
)
from lib.research.three_way_projection import (
    canonical_inning_result_probs,
    legacy_conditional_probs,
)

ACTIVATION_PAPER_ONLY = "PAPER_ONLY"
ACTIVATION_UNRESOLVED = "UNRESOLVED"
ACTIVATION_REASON_INSUFFICIENT_CALIBRATION = "INSUFFICIENT_HISTORICAL_CALIBRATION"
ACTIVATION_REASON_STRUCTURE_UNVERIFIED = "MARKET_STRUCTURE_OR_SETTLEMENT_UNVERIFIED"

STATUS_STRUCTURE_UNRESOLVED = "Structure Unresolved"


def _eligibility_for_structure(structure):
    if structure == STRUCTURE_THREE_WAY:
        return {
            "researchEligible": True,
            "paperEligible": True,
            "realMoneyEligible": False,
            "activationStatus": ACTIVATION_PAPER_ONLY,
            "activationReason": ACTIVATION_REASON_INSUFFICIENT_CALIBRATION,
        }
    return {
        "researchEligible": True,
        "paperEligible": False,
        "realMoneyEligible": False,
        "activationStatus": ACTIVATION_UNRESOLVED,
        "activationReason": ACTIVATION_REASON_STRUCTURE_UNVERIFIED,
    }


def build_shadow_ledger_row(date, game_id, matchup, market, context):
    """
    Pure. Builds one Part 9 shadow-ledger row, or returns None if
    `market` does not classify as an inning-result market at all
    (game_result/totals/spreads/etc are out of scope for this ledger).

    Args:
        date: "YYYY-MM-DD" string.
        game_id: stable game identifier.
        matchup: human-readable "AWAY@HOME" string.
        market: dict with market_ticker, event_ticker, title, subtitle,
            yes_bid, yes_ask, last_price, volume (any may be None/absent).
        context: dict with away_team, home_team (abbreviations, for
            outcome resolution), away_full_proj, home_full_proj
            (full-game run projections, optional), scale_fn (optional,
            injectable horizon-scaling function), snapshot_timestamp.
    """
    classified = classify_inning_result_market(
        market.get("market_ticker"),
        event_ticker=market.get("event_ticker"),
        title=market.get("title"),
        subtitle=market.get("subtitle"),
        away_team=context.get("away_team"),
        home_team=context.get("home_team"),
    )
    if classified is None:
        return None

    scope = classified["scope"]
    structure = classified["structure"]
    outcome = classified["outcome"]

    yes_bid = market.get("yes_bid")
    yes_ask = market.get("yes_ask")
    no_bid = round(1.0 - yes_ask, 4) if yes_ask is not None else None
    no_ask = round(1.0 - yes_bid, 4) if yes_bid is not None else None
    midpoint = round((yes_bid + yes_ask) / 2.0, 4) if (yes_bid is not None and yes_ask is not None) else None
    spread = round(yes_ask - yes_bid, 4) if (yes_bid is not None and yes_ask is not None) else None

    canonical_prob = None
    legacy_prob = None
    away_proj = context.get("away_full_proj")
    home_proj = context.get("home_full_proj")

    if structure == STRUCTURE_THREE_WAY and outcome in ("Away", "Tie", "Home") \
            and away_proj is not None and home_proj is not None:
        canonical = canonical_inning_result_probs(
            away_proj, home_proj, scope, scale_fn=context.get("scale_fn")
        )
        canonical_prob = {"Away": canonical["awayLeadProb"],
                           "Home": canonical["homeLeadProb"],
                           "Tie": canonical["tieProb"]}[outcome]
        if scope == "F5" and outcome in ("Away", "Home"):
            legacy = legacy_conditional_probs(canonical)
            legacy_prob = (legacy["awayLeadGivenNoTieProb"] if outcome == "Away"
                           else legacy["homeLeadGivenNoTieProb"])

    executable_yes_edge = None
    executable_no_edge = None
    if canonical_prob is not None:
        if yes_ask is not None:
            executable_yes_edge = round(canonical_prob - yes_ask, 4)
        if no_ask is not None:
            executable_no_edge = round((1.0 - canonical_prob) - no_ask, 4)

    row = {
        "date": date,
        "gameId": game_id,
        "matchup": matchup,
        "scope": scope,
        "outcome": outcome,
        "marketStructure": structure,
        "ticker": classified["ticker"],
        "yesBid": yes_bid,
        "yesAsk": yes_ask,
        "noBid": no_bid,
        "noAsk": no_ask,
        "midpoint": midpoint,
        "spread": spread,
        "volume": market.get("volume"),
        "canonicalModelProb": canonical_prob,
        "legacyConditionalProb": legacy_prob,
        "executableYesEdge": executable_yes_edge,
        "executableNoEdge": executable_no_edge,
        "settlementStatus": classified["settlementStatus"],
        "structureStatus": "VERIFIED" if structure == STRUCTURE_THREE_WAY else "UNVERIFIED",
        "reasonCodes": [],
        "snapshotTimestamp": context.get("snapshot_timestamp"),
    }
    row.update(_eligibility_for_structure(structure))

    if structure == STRUCTURE_UNVERIFIED:
        row["reasonCodes"] = [ACTIVATION_REASON_STRUCTURE_UNVERIFIED]
        row["status"] = STATUS_STRUCTURE_UNRESOLVED
        # No synthetic model edge on an unresolved-structure row, even if
        # a projection happened to be supplied in context.
        row["canonicalModelProb"] = None
        row["legacyConditionalProb"] = None
        row["executableYesEdge"] = None
        row["executableNoEdge"] = None
    else:
        row["status"] = "Evaluated" if canonical_prob is not None else "Missing Data"
        if canonical_prob is None:
            row["reasonCodes"] = ["awayFullProj/homeFullProj not supplied"]

    return row


def build_shadow_ledger(date, game_id, matchup, markets, context):
    """
    Pure. Builds one shadow-ledger row per market in `markets` that
    classifies as an inning-result market (via build_shadow_ledger_row);
    non-inning-result markets are simply omitted from THIS ledger (they
    belong to other ledgers/handlers -- this is not the "no silent
    drop" guarantee, which applies to the dynamic market-retention
    layer in lib/research/market_handler_registry.py, not this
    inning-result-specific view).
    """
    rows = []
    for m in markets:
        row = build_shadow_ledger_row(date, game_id, matchup, m, context)
        if row is not None:
            rows.append(row)
    return rows
