#!/usr/bin/env python3
"""
lib/research/market_handler_registry.py
============================================
Model Performance Phase 1 (Market Audit) -- RESEARCH-ONLY dynamic
market-discovery/handler-registry design.

Replaces the CONCEPTUAL dependency on one static REQUIRED_MARKETS list
(scripts/build_market_ledger.py's `REQUIRED_MARKETS` at module scope,
confirmed present via this phase's repository audit -- see
docs/research/PROJECTION_AUDIT.md Part 1) with a registry that:

  1. never silently drops a discovered market, regardless of whether a
     handler exists for its family,
  2. cleanly separates "can this market be classified/evaluated at
     all" (discovery/classification/projection support) from
     "is this market allowed into real-money production" (a SEPARATE,
     later gate this module does not implement or decide),
  3. gives every discovered market exactly one status from a small,
     closed set, so a caller can always distinguish WHY a market has
     no row rather than it simply being absent.

THIS MODULE DOES NOT RUN IN PRODUCTION. Nothing in
scripts/build_market_ledger.py, scripts/risk_gate.py,
scripts/protect_slate.py, scripts/validate_slate_final.py, or
scripts/write_pending_bets.py imports this module as of this phase.
"""
from lib.research.market_taxonomy import classify_market, is_three_way_family
from lib.research.three_way_projection import three_way_result_probs_for_horizon

STATUS_EVALUATED = "Evaluated"
STATUS_UNSUPPORTED_MARKET = "Unsupported Market"
STATUS_MISSING_DATA = "Missing Data"
STATUS_CLASSIFICATION_FAILED = "Classification Failed"
STATUS_SETTLEMENT_RULE_UNRESOLVED = "Settlement Rule Unresolved"
STATUS_EVALUATION_FAILED = "Evaluation Failed"

# Families with settlement rules independently read and documented
# this phase (docs/research/PROJECTION_AUDIT.md) -- everything else
# resolves to STATUS_SETTLEMENT_RULE_UNRESOLVED even if a handler
# function exists, since "we wrote code for it" and "we have read and
# confirmed its actual Kalshi settlement rules" are deliberately kept
# as two separate gates per the mission's explicit instruction not to
# assume settlement conventions.
SETTLEMENT_VERIFIED_FAMILIES = {
    ("game_result", "full_game"),
    ("inning_result", "F5"),
    ("game_total", "full_game"),
    ("team_total", "full_game"),
    ("inning_total", "F5"),
    ("winning_margin", "full_game"),
    ("winning_margin", "F5"),
    ("first_inning_run", "F1"),
}


def _evaluate_game_result_research(market, context):
    away = context.get("awayFullProj")
    home = context.get("homeFullProj")
    if away is None or home is None:
        return None, ["awayFullProj", "homeFullProj"]
    probs = three_way_result_probs_for_horizon(away, home, "full_game")
    return {
        "awayWinProb": probs["awayWinProb"],
        "tieProb": probs["tieProb"],
        "homeWinProb": probs["homeWinProb"],
        "truncationMass": probs["truncationMass"],
    }, []


def _evaluate_inning_result_research(market, context):
    away = context.get("awayFullProj")
    home = context.get("homeFullProj")
    scope = market.get("scope")
    if away is None or home is None:
        return None, ["awayFullProj", "homeFullProj"]
    if scope not in ("F3", "F5", "F7"):
        return None, [f"unsupported inning scope {scope!r}"]
    scale_fn = context.get("scaleFn")
    probs = three_way_result_probs_for_horizon(away, home, scope, scale_fn=scale_fn)
    return {
        "awayWinProb": probs["awayWinProb"],
        "tieProb": probs["tieProb"],
        "homeWinProb": probs["homeWinProb"],
        "truncationMass": probs["truncationMass"],
    }, []


def _unimplemented_handler(_market, _context):
    """Placeholder for a family this phase has classified/taxonomized but not yet built a research projection for."""
    return None, ["handler not yet implemented in Phase 1 -- see docs/research/PROJECTION_UPGRADE_ROADMAP.md"]


# The registry itself. Every value is a (family, scope-or-None) ->
# handler function mapping; scope=None means "handles all scopes of
# this family." Handlers return (result_dict_or_None, missing_data_list).
MARKET_HANDLERS = {
    ("game_result", "full_game"): _evaluate_game_result_research,
    ("inning_result", "F3"): _evaluate_inning_result_research,
    ("inning_result", "F5"): _evaluate_inning_result_research,
    ("inning_result", "F7"): _evaluate_inning_result_research,
    ("game_total", None): _unimplemented_handler,
    ("inning_total", None): _unimplemented_handler,
    ("team_total", None): _unimplemented_handler,
    ("winning_margin", None): _unimplemented_handler,
    ("first_inning_run", None): _unimplemented_handler,
    ("pitcher_strikeouts", None): _unimplemented_handler,
    ("pitcher_outs", None): _unimplemented_handler,
    ("pitcher_hits_allowed", None): _unimplemented_handler,
    ("pitcher_earned_runs", None): _unimplemented_handler,
    ("hitter_hits", None): _unimplemented_handler,
    ("hitter_total_bases", None): _unimplemented_handler,
    ("hitter_home_runs", None): _unimplemented_handler,
}


def _lookup_handler(family, scope):
    if (family, scope) in MARKET_HANDLERS:
        return MARKET_HANDLERS[(family, scope)]
    if (family, None) in MARKET_HANDLERS:
        return MARKET_HANDLERS[(family, None)]
    return None


def evaluate_market_research(market_ticker, event_ticker=None, title=None,
                              subtitle=None, context=None):
    """
    Pure (given a pure/deterministic context and scale_fn, if any).
    Research-only dispatcher: classifies the raw market, looks up a
    handler, and returns EXACTLY one status-tagged research row. Never
    raises for an unrecognized or unsupported market -- every
    discovered market gets a row (the "no silent drop" guarantee).

    Returns a dict:
        {
            "marketTicker": ...,
            "eventTicker": ...,
            "seriesTicker": ...,
            "family": ...,
            "scope": ...,
            "outcome": ...,
            "status": one of the STATUS_* constants above,
            "reasonCodes": [...],
            "result": <handler output dict, or None>,
        }
    """
    context = context or {}
    try:
        classified = classify_market(market_ticker, event_ticker=event_ticker,
                                      title=title, subtitle=subtitle)
    except Exception as e:
        return {
            "marketTicker": market_ticker,
            "eventTicker": event_ticker,
            "seriesTicker": None,
            "family": None,
            "scope": None,
            "outcome": None,
            "status": STATUS_CLASSIFICATION_FAILED,
            "reasonCodes": [f"{type(e).__name__}: {e}"],
            "result": None,
        }

    base = {
        "marketTicker": classified["marketTicker"],
        "eventTicker": classified["eventTicker"],
        "seriesTicker": classified["seriesTicker"],
        "family": classified["family"],
        "scope": classified["scope"],
        "outcome": classified["outcome"],
    }

    if classified["classificationStatus"] != "classified":
        return {**base, "status": STATUS_CLASSIFICATION_FAILED,
                "reasonCodes": ["unrecognized series ticker prefix"], "result": None}

    if (classified["family"], classified["scope"]) not in SETTLEMENT_VERIFIED_FAMILIES:
        return {**base, "status": STATUS_SETTLEMENT_RULE_UNRESOLVED,
                "reasonCodes": [
                    f"settlement rules for family={classified['family']!r} "
                    f"scope={classified['scope']!r} have not been independently "
                    f"verified this phase -- see docs/research/PROJECTION_AUDIT.md"
                ], "result": None}

    handler = _lookup_handler(classified["family"], classified["scope"])
    if handler is None:
        return {**base, "status": STATUS_UNSUPPORTED_MARKET,
                "reasonCodes": [f"no handler registered for family={classified['family']!r}"],
                "result": None}

    try:
        result, missing = handler(classified, context)
    except Exception as e:
        return {**base, "status": STATUS_EVALUATION_FAILED,
                "reasonCodes": [f"{type(e).__name__}: {e}"], "result": None}

    if result is None:
        return {**base, "status": STATUS_MISSING_DATA, "reasonCodes": missing, "result": None}

    return {**base, "status": STATUS_EVALUATED, "reasonCodes": [], "result": result}


def evaluate_market_batch_research(markets, context=None):
    """
    Pure. Evaluates a list of raw market dicts (each with at least
    marketTicker, optionally eventTicker/title/subtitle) and returns a
    list of research rows, ONE PER INPUT MARKET, in the same order --
    the direct proof of the "no discovered market may silently
    disappear" requirement: len(output) == len(input) always.
    """
    context = context or {}
    rows = []
    for m in markets:
        rows.append(evaluate_market_research(
            m.get("market_ticker") or m.get("marketTicker"),
            event_ticker=m.get("event_ticker") or m.get("eventTicker"),
            title=m.get("title"),
            subtitle=m.get("subtitle"),
            context=context,
        ))
    return rows
