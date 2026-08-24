#!/usr/bin/env python3
"""
scripts/edgelab/standalone_full_universe_evaluation.py
==========================================================
Phase 2 (Full-Universe MLB Kalshi Probability Persistence), item 7:
a standalone Kalshi price check today archives raw market observations
into the shared EdgeLab corpus (scripts/edgelab/ingest_market_observations.py,
--source-system standalone_price_check) but computes NO model
probabilities. This script closes that gap: given the SAME raw Kalshi
registry snapshot the standalone workflow already captured (the exact
archived universe it captured -- never a broader/narrower substitute),
it runs the existing, unchanged discover()/adapt_contract() engine
(scripts.discover_kalshi_mlb_markets, lib.kalshi_probability_adapters)
and persists one ModelEvaluation row per contract, tagged
sourceCaptureType=PROSPECTIVE_STANDALONE.

Deliberately reuses, never duplicates:
  - scripts.discover_kalshi_mlb_markets.discover() for parsing/
    classification/pricing (the identical engine the scheduled discovery
    workflow uses -- no second parser, classifier, or adapter).
  - lib.edgelab.model_evaluation.extend_full_universe_evaluations() for
    row shaping/status classification/schema fields (the identical
    function RECOMMENDATION_SYNC's scheduled path calls) --
    covered_tickers is always the empty set here: a standalone run has
    no separate "pipeline" pass that could already cover a ticker.

No recommendation filtering: every classified contract in this capture
gets a row, unfiltered by edge thresholds, real-money eligibility, or
whether it would ever be recommended -- unfiltered research capture,
exactly like the existing MarketObservation archive step this runs
alongside.

Idempotency / checkpoint preservation (item 11): each invocation's runId
is content-derived from the exact snapshot file processed
(lib.edgelab.ids.new_run_id + build_run_content_signature, the same
scheme scripts/edgelab/ingest_market_observations.py already uses for
the identical safety reason). extend_full_universe_evaluations() itself
keys its returned rows' modelEvaluationId by (date, marketTicker) --
correct for its ORIGINAL caller (RECOMMENDATION_SYNC's once-daily
scheduled pass, where "one current row per market per day" is the
right identity) but wrong here, where the whole point is preserving
MULTIPLE same-day checkpoints. This script therefore re-keys each
returned row's modelEvaluationId to sha1(runId, marketTicker) (the SAME
lib.edgelab.ids.build_model_evaluation_id helper, just with runId in
place of date) before writing, and appends (never upserts) via
lib.edgelab.storage.append_records, which skips any id already on
disk. An accidental identical rerun in the SAME GitHub Actions run/
attempt (same snapshot content -> same content_signature -> same
runId, per lib.edgelab.ids.new_run_id's own doc) is therefore a pure
no-op; two genuinely distinct standalone captures at different times
(different snapshot -> different content_signature -> different
runId) are each preserved as their own distinct rows -- multiple time
checkpoints are never collapsed into one. Because standalone rows are
keyed by runId (never by bare date), they can never collide with, and
never overwrite, the scheduled/pipeline writer's own (date,
marketTicker)-keyed or (pipelineRunId, marketTicker)-keyed rows for
the same date -- the two ID spaces never intersect.

Never touches recommendations.json, risk_gate, write_pending_bets, or
any real-money pipeline file -- read/discover/price/persist-to-
model-evaluations only, matching this repo's existing safety boundary
for every discovery/research script (see
scripts/discover_kalshi_mlb_markets.py's own module docstring).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage  # noqa: E402
from lib.edgelab.model_evaluation import extend_full_universe_evaluations  # noqa: E402
from lib.edgelab.probability_status import SOURCE_CAPTURE_TYPE_PROSPECTIVE_STANDALONE  # noqa: E402
from scripts.discover_kalshi_mlb_markets import discover  # noqa: E402

DEFAULT_SLATE_PATH = os.path.join("data", "slate.json")


def load_json(path):
    with open(path) as f:
        return json.load(f)


def contracts_to_observations(contracts, run_id, snapshot_path):
    """
    Reshapes discover()'s own contract dicts into the minimal
    observation-shaped dicts extend_full_universe_evaluations() expects
    (marketTicker/runId/gameId/eventTicker/seriesTicker/marketFamily/
    threshold/provenance) -- copies existing fields verbatim, computes
    nothing new. A contract with no resolved ticker is skipped (nothing
    to key a ModelEvaluation row on -- mirrors discover()'s own
    parse-error/unclassified contracts, which never carry a usable
    ticker either).
    """
    now = ids.utc_now_iso()
    observations = []
    for c in contracts:
        ticker = c.get("ticker")
        if not ticker:
            continue
        observations.append({
            "marketTicker": ticker,
            "runId": run_id,
            "gameId": c.get("gameId"),
            "eventTicker": c.get("eventTicker"),
            "seriesTicker": c.get("seriesTicker"),
            "marketFamily": c.get("marketFamily"),
            "threshold": c.get("line"),
            "provenance": {
                "sourceSystem": "standalone_price_check",
                "sourceFile": snapshot_path,
                "sourceKey": ticker,
                "capturedAt": c.get("currentMarketObservedAt"),
                "ingestedAt": now,
            },
        })
    return observations


def run(date_str, snapshot_path, slate_path=None, out_path=None, dry_run=False):
    """
    Core: given an already-resolved snapshot_path, returns a result
    summary dict. Writes to data/edgelab/model_evaluations/<date>.jsonl
    via lib.edgelab.storage.append_records unless dry_run=True.
    """
    slate_path = slate_path or DEFAULT_SLATE_PATH
    search_doc = load_json(snapshot_path)
    try:
        slate_doc = load_json(slate_path)
    except (FileNotFoundError, json.JSONDecodeError):
        slate_doc = {"games": []}

    date_str = date_str or search_doc.get("date")
    contracts, discovery_summary = discover(date_str, search_doc, slate_doc)

    content_signature = ids.build_run_content_signature("standalone_full_universe_evaluation", snapshot_path)
    run_id = ids.new_run_id(
        "STANDALONE_FULL_UNIVERSE_EVALUATION",
        github_run_id=os.environ.get("GITHUB_RUN_ID"),
        github_run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT"),
        content_signature=content_signature,
    )

    observations = contracts_to_observations(contracts, run_id, snapshot_path)
    discovery_lookup = {c["ticker"]: c for c in contracts if c.get("ticker")}

    rows = extend_full_universe_evaluations(
        covered_tickers=set(),
        observations=observations,
        date=date_str,
        discovery_lookup=discovery_lookup,
        source_capture_type=SOURCE_CAPTURE_TYPE_PROSPECTIVE_STANDALONE,
    )
    # Re-key by (runId, marketTicker) -- see module docstring's
    # "Idempotency / checkpoint preservation" section for why this
    # script cannot use extend_full_universe_evaluations()'s own
    # (date, marketTicker) identity unchanged.
    for row in rows:
        row["modelEvaluationId"] = ids.build_model_evaluation_id(run_id, row["marketTicker"])

    written, skipped = 0, 0
    if not dry_run and rows:
        out_path = out_path or storage.partition_path("model_evaluations", date_str)
        written, skipped = storage.append_records(out_path, rows, "modelEvaluationId")

    return {
        "date": date_str,
        "runId": run_id,
        "snapshotPath": snapshot_path,
        "discoverySummary": discovery_summary,
        "rowsBuilt": len(rows),
        "rowsWritten": written,
        "rowsSkipped": skipped,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="UTC date YYYY-MM-DD; defaults to the snapshot's own 'date' field")
    parser.add_argument("--snapshot-path", required=True, help="Path to the raw Kalshi search/registry snapshot this standalone run captured")
    parser.add_argument("--slate-path", default=None, help="Path to slate.json for game matching (default data/slate.json)")
    parser.add_argument("--dry-run", action="store_true", help="Compute and print rows without writing")
    args = parser.parse_args(argv)

    if not os.path.exists(args.snapshot_path):
        print(f"[standalone_full_universe_evaluation] No snapshot file at {args.snapshot_path} — nothing to evaluate")
        print(json.dumps({"status": "NO_SNAPSHOT_FILE", "snapshotPath": args.snapshot_path}, indent=2))
        return 0

    result = run(args.date, args.snapshot_path, slate_path=args.slate_path, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
