#!/usr/bin/env python3
"""
scripts/edgelab/build_recommendations.py
=============================================
CLI entry point: build the EdgeLab decision-layer ledger for one date
from data/pipeline/<date>/recommendations.json + execution.json (the
11-market model config's decisions) plus full-universe extension rows
for every other market EdgeLab observed that day. Since Phase 2
Milestone 3 (docs/EDGELAB_MODEL_EVALUATION.md), also builds the parallel
ModelEvaluation ledger from the exact same source rows, and backfills
any already-logged PlacedBet's recommendationId/modelEvaluationId once a
matching evaluation exists for its ticker.

MLB Model Expression Guardrails milestone -- best-expression wiring:
ModelEvaluations are now built and written to disk BEFORE Recommendations
(previously the reverse). This is a pure reordering, not a new data
dependency: eval_pipeline_records/eval_extension_records have never
depended on anything build_recommendations_from_pipeline() produces --
both read directly from data/pipeline/<date>/recommendations.json, the
SAME already-complete, whole-day artifact scripts/build_market_ledger.py
finished writing before this script ever runs. Writing ModelEvaluations
first means lib.edgelab.market_comparison's clustering (which reads
ModelEvaluation back via a DuckDB session) sees the day's COMPLETE
candidate market set -- every game, every market -- never a partial one,
so which candidate happens to be evaluated first can never change the
comparison result. See lib.edgelab.market_comparison.build_comparisons()
and comparison_markets_lookup()/comparison_annotations_lookup().

The comparison step is wrapped so its own failure can never block
Recommendation-building (this repo's pipeline runs this script every
day): any exception degrades to an empty lookup -- comparisonMarkets/
comparisonStatus/dominantMarketTicker/dominationReasons stay null/[],
exactly as they were before this milestone -- with a warning recorded on
the run, never a crash.

Usage:
    python3 scripts/edgelab/build_recommendations.py [--date YYYY-MM-DD]
"""
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage
from lib.edgelab.analytics import open_session
from lib.edgelab.bets import link_bets_to_recommendations
from lib.edgelab.market_comparison import (
    build_comparisons,
    comparison_annotations_lookup,
    comparison_markets_lookup,
)
from lib.edgelab.hitter_board_bridge import load_hitter_board_lookup
from lib.edgelab.kalshi_discovery_bridge import load_discovery_lookup
from lib.edgelab.model_evaluation import build_model_evaluations_from_pipeline, extend_full_universe_evaluations
from lib.edgelab.recommendations import (
    build_recommendations_from_pipeline,
    extend_with_full_universe,
    load_model_covered_series,
)


def build_comparison_lookups():
    """
    Impure shell: opens a fresh DuckDB session over the current
    data/edgelab/ archive (including the ModelEvaluations this run just
    wrote) and runs lib.edgelab.market_comparison's clustering/domination
    engine over it. Returns (comparison_lookup, comparison_annotations,
    rows_evaluated, warnings) -- an empty-lookup, non-raising degradation
    on any failure, so a comparison-engine problem never blocks
    Recommendation-building.
    """
    try:
        with open_session() as session:
            comparisons = build_comparisons(session)
            return (
                comparison_markets_lookup(comparisons),
                comparison_annotations_lookup(comparisons),
                len(comparisons),
                [],
            )
    except Exception as e:
        return {}, {}, 0, [f"best-expression comparison unavailable: {type(e).__name__}: {e}"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    date = args.date or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    run_id = ids.new_run_id("RECOMMENDATION_SYNC", github_run_id=os.environ.get("GITHUB_RUN_ID"))
    started_at = ids.utc_now_iso()

    bets_path = storage.singleton_path("bets", "bets.jsonl")
    bets = list(storage.read_records(bets_path))
    placed_bet_tickers = {}
    for row in bets:
        if row.get("marketTicker"):
            placed_bet_tickers.setdefault(row["marketTicker"], row["betId"])

    observations = list(storage.read_records(storage.partition_path("observations", date, compressed=True)))
    model_covered_series = load_model_covered_series()

    # ── ModelEvaluations first (see module docstring for why) ───────────
    eval_pipeline_records, eval_warnings = build_model_evaluations_from_pipeline(date, run_id, observations)
    # Corpus Storage Growth mission: resolved (not the bare plain path) --
    # append_records/upsert_records below already merge with whatever
    # this path currently holds, gzip or not, so this stays correct even
    # for a (structurally unlikely) already-compacted `date`.
    evaluations_path = storage.resolve_partition_path("model_evaluations", date)
    eval_written, eval_skipped = storage.append_records(evaluations_path, eval_pipeline_records, "modelEvaluationId")

    eval_covered_tickers = {r["marketTicker"] for r in eval_pipeline_records if r.get("marketTicker")}
    # Universal ModelEvaluation Persistence mission: cross-reference
    # scripts/discover_kalshi_mlb_markets.py's own already-computed,
    # already-tested per-contract fair probabilities (real-money-
    # eligibility-independent) for every family that pipeline discovers
    # but the 11-REQUIRED_MARKETS pipeline never runs against (F3/F5/F7
    # winner and totals, spread/winning_margin, pitcher strikeouts/outs).
    # Empty for a date discovery hasn't run for -- degrades to the
    # pre-existing NOT_EVALUATED/NO_MODEL_SUPPORT-only behavior exactly.
    # Hitter Prop Methodology Repair mission: the four hitter-prop
    # families whose live pricing methodology this mission fixed
    # (hitter_hits/hitter_total_bases/hitter_rbis/hitter_hits_runs_rbis
    # -- see lib/edgelab/hitter_board_bridge.py's own docstring) are
    # priced by a SEPARATE artifact/pipeline
    # (scripts/build_hitter_projection_board.py) than
    # scripts/discover_kalshi_mlb_markets.py's non-hitter families, but
    # both bridges emit the SAME ticker-keyed contract shape, so they
    # merge into one discovery_lookup with no changes to
    # extend_full_universe_evaluations()/_discovery_extension_fields()
    # at all. hitter_stolen_bases never appears in the hitter board's
    # PROJECTED rows (no method exists for it), so it is structurally
    # impossible for this merge to persist a fabricated probability for
    # it -- see hitter_board_bridge.py's own docstring.
    discovery_lookup = {**load_discovery_lookup(date), **load_hitter_board_lookup(date)}
    eval_extension_records = extend_full_universe_evaluations(
        eval_covered_tickers, observations, date, model_covered_series, discovery_lookup=discovery_lookup,
    )
    eval_ext_updated, eval_ext_inserted = storage.upsert_records(evaluations_path, eval_extension_records, "modelEvaluationId")

    # ── Best-expression comparison over the now-complete candidate set ──
    comparison_lookup, comparison_annotations, comparison_rows_evaluated, comparison_warnings = build_comparison_lookups()

    # ── Recommendations, annotated with the comparison result ───────────
    pipeline_records, warnings = build_recommendations_from_pipeline(
        date, run_id, placed_bet_tickers, observations,
        comparison_lookup=comparison_lookup, comparison_annotations=comparison_annotations,
    )
    pipeline_path = storage.resolve_partition_path("recommendations", date)
    written, skipped = storage.append_records(pipeline_path, pipeline_records, "recommendationId")

    covered_tickers = {r["marketTicker"] for r in pipeline_records if r.get("marketTicker")}
    extension_records = extend_with_full_universe(covered_tickers, observations, model_covered_series, date, placed_bet_tickers)
    ext_updated, ext_inserted = storage.upsert_records(pipeline_path, extension_records, "recommendationId")

    bet_updates = link_bets_to_recommendations(bets, pipeline_records + extension_records)
    if bet_updates:
        storage.upsert_records(bets_path, bet_updates, "betId")

    all_warnings = warnings + eval_warnings + comparison_warnings
    run_record = {
        "schemaVersion": "1",
        "runId": run_id,
        "runType": "RECOMMENDATION_SYNC",
        "startedAt": started_at,
        "completedAt": ids.utc_now_iso(),
        "status": "success" if not all_warnings else "partial",
        "sourceWorkflow": os.environ.get("GITHUB_WORKFLOW"),
        "githubRunId": os.environ.get("GITHUB_RUN_ID"),
        "inputFiles": [
            os.path.join("data", "pipeline", date, "recommendations.json"),
            os.path.join("data", "pipeline", date, "execution.json"),
            storage.partition_path("observations", date, compressed=True),
        ],
        "outputFiles": [pipeline_path, evaluations_path, bets_path],
        "counts": {
            "pipelineRowsWritten": written,
            "pipelineRowsSkippedDuplicate": skipped,
            "extensionRowsInserted": ext_inserted,
            "extensionRowsUpdated": ext_updated,
            "modelEvaluationsWritten": eval_written,
            "modelEvaluationsSkippedDuplicate": eval_skipped,
            "modelEvaluationExtensionRowsInserted": eval_ext_inserted,
            "modelEvaluationExtensionRowsUpdated": eval_ext_updated,
            "betsLinked": len(bet_updates),
            "observationsConsidered": len(observations),
            "comparisonRowsEvaluated": comparison_rows_evaluated,
        },
        "errors": [],
        "warnings": all_warnings,
        "createdAt": started_at,
        "provenance": {
            "sourceSystem": "edgelab_cli",
            "sourceFile": __file__,
            "sourceKey": date,
            "capturedAt": started_at,
            "ingestedAt": started_at,
        },
    }
    storage.append_records(storage.partition_path("research_runs", date), [run_record], "runId")

    print(
        f"[build_recommendations] date={date} pipeline_rows={written} "
        f"skipped_dup={skipped} extension_inserted={ext_inserted} extension_updated={ext_updated} "
        f"model_evaluations_written={eval_written} eval_extension_inserted={eval_ext_inserted} "
        f"comparison_rows_evaluated={comparison_rows_evaluated} "
        f"bets_linked={len(bet_updates)} warnings={all_warnings}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
