#!/usr/bin/env python3
"""
scripts/edgelab/record_bet_from_workflow.py
================================================
Backing script for the "Record Placed Bet" GitHub Actions form
(.github/workflows/record-placed-bet.yml). Reads its 10 workflow_dispatch
inputs from environment variables, builds a PlacedBet record, and writes
it through the ONE canonical write path (lib.edgelab.bets.write_placed_bet
-- the same function scripts/edgelab/log_bet.py uses), so a bet recorded
from the GitHub form and a bet recorded from a chat session are
indistinguishable in the ledger except for entryMethod.

This script NEVER calls a Kalshi (or any) order-placement API -- it has
no such capability at all; it only appends a row describing a bet the
user has told it they ALREADY placed elsewhere.

Environment variables (all optional except GAME_DATE/MARKET_TICKER/
SELECTION/STAKE/ENTRY_PRICE/PLACED_AT):
    GAME_DATE, MARKET_TICKER, SELECTION, SIDE, STAKE, ENTRY_PRICE,
    PLACED_AT, RECOMMENDATION_ID, NOTES, ADVANCED_JSON

ADVANCED_JSON (a JSON object string) may set any of: gameId, matchup,
eventTicker, seriesTicker, marketFamily, marketHorizon, threshold,
contracts, scheduledStart, entryOdds, source, entryMethod,
productionRunId, snapshotId, manualFairProbability, modelFairProbability,
executablePriceAtEntry, betUpToPriceAtEntry, confidence, dataQuality,
correlationGroups, trackingType, thesisTags, onConflict. Deliberately NOT
settable here: modelSupported/modelEvaluationId -- this is a
manual-entry surface, and modelSupported=True requires a real
modelEvaluationId (enforced by build_manual_bet_record). modelEvaluationId
is only ever set two ways: later, by
scripts/edgelab/build_recommendations.py's link_bets_to_recommendations()
backfill (ticker-matched, async), or immediately, when RECOMMENDATION_ID
is supplied AND resolves against a real row in that date's Recommendation
ledger (see lib.edgelab.bets.resolve_recommendation_context) -- an
UNVERIFIED caller-asserted modelEvaluationId is never accepted directly
through advanced_json either way.

Prospective Canonical Wager-Context Capture milestone: whenever
RECOMMENDATION_ID resolves against a real Recommendation row for
GAME_DATE, modelFairProbability/confidence/executablePriceAtEntry/
betUpToPriceAtEntry/estimatedEdgeAtEntry (and modelEvaluationId, above)
are auto-snapshotted from it -- never fabricated, only what that ledger
row actually recorded. Any of those fields ALSO given explicitly through
ADVANCED_JSON always wins over the resolved value. This script also now
computes marketObservationLinkage (previously only import_bet_batch.py
did) via lib.edgelab.observation_linkage.link_bet_to_observation, the
same archived-corpus lookup, for every bet regardless of whether a
recommendation was cited.

Writes:
  - stdout: the JSON receipt
  - $RECEIPT_PATH (default receipt.json): same, for the workflow's
    upload-artifact step
  - $GITHUB_STEP_SUMMARY (if set): a human-readable summary
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import tags as tags_mod
from lib.edgelab.bets import build_manual_bet_record, resolve_recommendation_context, write_placed_bet
from lib.edgelab.observation_linkage import link_bet_to_observation


def _env(name, default=None):
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v


def _prefer(explicit, resolved):
    """An explicitly-supplied (non-null) value always wins over a
    resolved/auto-populated one -- 0 is a valid explicit value here (a
    genuine, if unusual, 0.0 fair-probability/edge estimate), so this
    checks `is not None` rather than plain truthiness."""
    return explicit if explicit is not None else resolved


def _float_env(name):
    v = _env(name)
    return float(v) if v is not None else None


def main():
    game_date = _env("GAME_DATE")
    market_ticker = _env("MARKET_TICKER")
    selection = _env("SELECTION")
    side = _env("SIDE", "YES")
    stake = _float_env("STAKE")
    entry_price = _float_env("ENTRY_PRICE")
    placed_at = _env("PLACED_AT")
    recommendation_id = _env("RECOMMENDATION_ID")
    notes = _env("NOTES")
    advanced_raw = _env("ADVANCED_JSON", "{}")

    missing = [
        name for name, v in (
            ("GAME_DATE", game_date), ("MARKET_TICKER", market_ticker), ("SELECTION", selection),
            ("STAKE", stake), ("ENTRY_PRICE", entry_price), ("PLACED_AT", placed_at),
        ) if v is None
    ]
    if missing:
        print(f"[record_bet_from_workflow] missing required input(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    try:
        advanced = json.loads(advanced_raw) if advanced_raw else {}
    except json.JSONDecodeError as e:
        print(f"[record_bet_from_workflow] ADVANCED_JSON is not valid JSON: {e}", file=sys.stderr)
        return 1

    thesis_tags = advanced.get("thesisTags") or []
    if thesis_tags:
        try:
            tags_mod.validate_tags(thesis_tags)
        except Exception as e:
            print(f"[record_bet_from_workflow] {e}", file=sys.stderr)
            return 1

    entry_method = advanced.get("entryMethod") or (
        "PRODUCTION_RECOMMENDATION_CONFIRMED" if recommendation_id else "MANUAL_GITHUB_FORM"
    )
    source = advanced.get("source") or ("MODEL" if recommendation_id else "MANUAL")
    on_conflict = advanced.get("onConflict", "reject")

    # Prospective Canonical Wager-Context Capture milestone: verify
    # recommendation_id against that date's real Recommendation ledger and
    # snapshot its decision-time context -- never fabricated, only what a
    # real, matching ledger row actually recorded. An explicit ADVANCED_JSON
    # value always wins over the resolved one (see _prefer). None of this
    # touches manualFairProbability, which has no Recommendation-side
    # equivalent and is only ever whatever advanced_json explicitly supplies.
    context = resolve_recommendation_context(recommendation_id, game_date) or {}
    model_evaluation_id = context.get("modelEvaluationId")

    model_fair_probability = _prefer(advanced.get("modelFairProbability"), context.get("modelFairProbability"))
    estimated_edge = context.get("estimatedEdgeAtEntry")
    if estimated_edge is None and advanced.get("modelFairProbability") is not None:
        # No recommendation context resolved (or it had no estimatedEdge on
        # record) but the caller supplied modelFairProbability directly --
        # preserve this script's original fallback derivation exactly, on
        # the same 0-100-vs-0-1 convention advanced_json callers already
        # rely on for this specific path.
        estimated_edge = round(advanced["modelFairProbability"] - entry_price * 100, 4)

    linkage = link_bet_to_observation(
        market_ticker, game_date, side=side, scheduled_start=advanced.get("scheduledStart"),
    )

    record = build_manual_bet_record(
        market_ticker, selection, stake, entry_price, placed_at,
        game_id=advanced.get("gameId"), game_date=game_date, matchup=advanced.get("matchup"),
        event_ticker=advanced.get("eventTicker"), series_ticker=advanced.get("seriesTicker"),
        market_family=advanced.get("marketFamily"), market_horizon=advanced.get("marketHorizon"),
        side=side, threshold=advanced.get("threshold"), contracts=advanced.get("contracts"),
        scheduled_start=advanced.get("scheduledStart"), entry_odds=advanced.get("entryOdds"),
        source=source, entry_method=entry_method,
        recommendation_id=recommendation_id, production_run_id=advanced.get("productionRunId"),
        snapshot_id=advanced.get("snapshotId"),
        model_evaluation_id=model_evaluation_id, model_supported=True if model_evaluation_id else None,
        manual_fair_probability=advanced.get("manualFairProbability"),
        model_fair_probability=model_fair_probability, estimated_edge_at_entry=estimated_edge,
        executable_price_at_entry=_prefer(advanced.get("executablePriceAtEntry"), context.get("executablePriceAtEntry")),
        bet_up_to_price_at_entry=_prefer(advanced.get("betUpToPriceAtEntry"), context.get("betUpToPriceAtEntry")),
        confidence=_prefer(advanced.get("confidence"), context.get("confidence")), data_quality=advanced.get("dataQuality"),
        correlation_groups=advanced.get("correlationGroups"), tracking_type=advanced.get("trackingType"),
        thesis_tags=thesis_tags, rationale=notes, market_observation_linkage=linkage,
    )

    receipt = write_placed_bet(record, on_conflict=on_conflict)

    receipt_path = _env("RECEIPT_PATH", "receipt.json")
    with open(receipt_path, "w") as f:
        json.dump(receipt, f, indent=2, sort_keys=True)
    print(json.dumps(receipt, indent=2, sort_keys=True))

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write("## Record Placed Bet\n\n")
            if receipt["success"]:
                f.write(f"**Saved** (`{receipt['duplicateStatus']}`)\n\n")
            else:
                f.write(f"**NOT saved** (`{receipt['duplicateStatus']}`)\n\n")
            f.write(f"- betId: `{receipt['betId']}`\n")
            f.write(f"- market: `{receipt['market']['marketTicker']}` — {receipt['market']['selection']} ({receipt['market']['side']})\n")
            f.write(f"- stake: ${receipt['stake']}\n")
            f.write(f"- entry price: {receipt['entryPrice']}\n")
            f.write(f"- potential gross return: {receipt['potentialGrossReturn']}\n")
            f.write(f"- linkage: {receipt['linkageStatus']} ({', '.join(receipt['linkedEntities']) or 'none'})\n")
            if receipt["errors"]:
                f.write(f"- errors: {receipt['errors']}\n")
            if receipt["conflictingFields"]:
                f.write(f"- conflicting fields: {receipt['conflictingFields']}\n")
            if receipt["nearDuplicateWarnings"]:
                f.write(f"- near-duplicate warning: {receipt['nearDuplicateWarnings']}\n")

    if not receipt["success"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
