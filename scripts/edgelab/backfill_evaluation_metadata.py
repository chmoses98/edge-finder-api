#!/usr/bin/env python3
"""
scripts/edgelab/backfill_evaluation_metadata.py
=====================================================
ONE-TIME Milestone 4 migration (docs/EDGELAB_EVALUATION_METADATA.md):
refreshes already-committed data/edgelab/model_evaluations/<date>.jsonl
files written by the Milestone 3 version of
lib.edgelab.model_evaluation (which didn't yet compute modelCommitSha/
modelConfigVersion/probabilityAdapter/confidenceSource/thesisTags/
tagEvidence/correlationGroups) so they carry the fuller Milestone 4
field set.

NOT part of the ongoing production path: scripts/edgelab/build_recommendations.py
still uses storage.append_records for pipeline-derived rows (a
deliberate no-op on rerun -- see lib/edgelab/recommendations.py's
module docstring), because once a ModelEvaluation is recorded against a
specific, unchanged source artifact snapshot it is meant to be
immutable historical fact. This script exists only because the
COMPUTATION ITSELF changed underneath already-recorded rows (a one-time
code upgrade, not a new pipeline run) -- it uses storage.upsert_records
to replace each existing row in place, by the exact same
modelEvaluationId, from the exact same still-unchanged source artifact.
No new information is invented: every backfilled field is recomputed by
the same lib.edgelab.model_evaluation functions the normal pipeline path
already uses, over the same already-committed data/pipeline/<date>/recommendations.json
artifact.

Reports records inspected/updated and population rates before and
after, per Milestone 4 scope item 9.

Usage:
    python3 scripts/edgelab/backfill_evaluation_metadata.py --date YYYY-MM-DD [--date YYYY-MM-DD ...]
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage
from lib.edgelab.model_evaluation import build_model_evaluations_from_pipeline
from lib.pipeline_artifacts import stage_artifact_exists

# NOTE: this backfill only recomputes pipeline-derived rows (source=
# "pipeline_recommendations"). No committed model_evaluations file
# currently contains a market_universe_extension row (data/edgelab/observations/
# has no committed files yet, so extend_full_universe_evaluations has
# never produced anything real) -- confirmed by inspection before writing
# this script, not assumed. If that changes, this script's population
# rates will visibly under-count and this comment should be revisited.

_TRACKED_FIELDS = (
    "modelCommitSha", "modelConfigVersion", "probabilityAdapter", "confidenceSource",
    "thesisTags", "correlationGroups", "dataQualityReasons", "pipelineRunId", "artifactSource",
)


def _population_rates(records):
    total = len(records)
    if not total:
        return {f: 0.0 for f in _TRACKED_FIELDS}
    rates = {}
    for field in _TRACKED_FIELDS:
        populated = sum(1 for r in records if r.get(field))  # non-empty list or non-null string, both truthy
        rates[field] = round(100.0 * populated / total, 2)
    return rates


def discover_dates():
    """Every date with an existing data/edgelab/model_evaluations/<date>.jsonl file."""
    pattern = os.path.join("data", "edgelab", "model_evaluations", "*.jsonl")
    return sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(pattern))


def backfill_date(date):
    path = storage.partition_path("model_evaluations", date)
    existing = list(storage.read_records(path))
    if not existing:
        return {"date": date, "recordsInspected": 0, "recordsUpdated": 0, "skippedReason": "no existing file"}

    before_rates = _population_rates(existing)
    existing_by_id = {r["modelEvaluationId"]: r for r in existing}

    if not stage_artifact_exists("recommendations", date):
        return {
            "date": date, "recordsInspected": len(existing), "recordsUpdated": 0,
            "skippedReason": "source data/pipeline/<date>/recommendations.json no longer available -- cannot recompute without fabricating",
            "beforeRates": before_rates,
        }

    # Recompute fresh from the same source artifact. run_id is regenerated
    # (this backfill IS a new EdgeLab run), but modelEvaluationId is
    # unchanged (same source_run_key + market_key), so upsert_records
    # replaces each existing row in place rather than duplicating it.
    run_id = ids.new_run_id("EVALUATION_METADATA_BACKFILL", github_run_id=os.environ.get("GITHUB_RUN_ID"))
    observations = list(storage.read_records(storage.partition_path("observations", date, compressed=True)))
    fresh_pipeline_records, warnings = build_model_evaluations_from_pipeline(date, run_id, observations)

    conflicts = []
    updated_records = []
    for fresh in fresh_pipeline_records:
        old = existing_by_id.get(fresh["modelEvaluationId"])
        if old is None:
            conflicts.append(f"new modelEvaluationId {fresh['modelEvaluationId']} not present in existing file (unexpected -- source artifact should be unchanged)")
            continue
        # createdAt is preserved from the original record -- this is a
        # metadata refresh, not a new evaluation event; provenance.capturedAt
        # (the source artifact's own meta.createdAt) is unchanged too since
        # build_model_evaluations_from_pipeline recomputes it from the same
        # artifact. Only provenance.ingestedAt (already set to "now" by the
        # fresh computation) legitimately reflects this backfill run.
        fresh["createdAt"] = old["createdAt"]
        # Immutable core facts (modelFairProbability, estimatedEdge,
        # evaluationStatus, etc.) must be byte-identical to the original
        # since the source artifact hasn't changed -- flag, never
        # silently overwrite, if they somehow differ.
        for field in ("evaluationStatus", "modelFairProbability", "estimatedEdge", "marketTicker"):
            if old.get(field) != fresh.get(field):
                conflicts.append(f"{fresh['modelEvaluationId']}: {field} changed from {old.get(field)!r} to {fresh.get(field)!r} -- not overwriting this record")
                break
        else:
            updated_records.append(fresh)

    updated, inserted = storage.upsert_records(path, updated_records, "modelEvaluationId")
    after_records = list(storage.read_records(path))
    after_rates = _population_rates(after_records)

    return {
        "date": date,
        "recordsInspected": len(existing),
        "recordsUpdated": updated,
        "recordsUnchanged": len(existing) - updated,
        "beforeRates": before_rates,
        "afterRates": after_rates,
        "conflicts": conflicts,
        "warnings": warnings,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", action="append", default=None, help="Date(s) to backfill (YYYY-MM-DD). Repeatable. Defaults to every date with an existing model_evaluations file.")
    args = parser.parse_args()
    dates = args.date or discover_dates()

    results = [backfill_date(d) for d in dates]
    print(json.dumps(results, indent=2))
    total_updated = sum(r.get("recordsUpdated", 0) for r in results)
    total_inspected = sum(r.get("recordsInspected", 0) for r in results)
    print(f"[backfill_evaluation_metadata] dates={len(dates)} inspected={total_inspected} updated={total_updated}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
