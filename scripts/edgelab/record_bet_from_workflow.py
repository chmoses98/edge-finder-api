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
confidence, dataQuality, correlationGroups, trackingType, thesisTags,
onConflict. Deliberately NOT settable here: modelSupported/
modelEvaluationId -- this is a manual-entry surface, and modelSupported
=True requires a real modelEvaluationId (enforced by
build_manual_bet_record); that link is only ever established later by
scripts/edgelab/build_recommendations.py's link_bets_to_recommendations()
backfill, never claimed at entry time.

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
from lib.edgelab.bets import build_manual_bet_record, write_placed_bet


def _env(name, default=None):
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v


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

    estimated_edge = None
    model_fair_probability = advanced.get("modelFairProbability")
    if model_fair_probability is not None:
        estimated_edge = round(model_fair_probability - entry_price * 100, 4)

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
        manual_fair_probability=advanced.get("manualFairProbability"),
        model_fair_probability=model_fair_probability, estimated_edge_at_entry=estimated_edge,
        confidence=advanced.get("confidence"), data_quality=advanced.get("dataQuality"),
        correlation_groups=advanced.get("correlationGroups"), tracking_type=advanced.get("trackingType"),
        thesis_tags=thesis_tags, rationale=notes,
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
