#!/usr/bin/env python3
"""
scripts/research/build_hitter_projection_audit.py
======================================================
RESEARCH-ONLY retrospective grading + calibration audit for the archived
prospective hitter projection engine. See lib/research/hitter_projection_audit.py
for the full methodology writeup. Reads only already-archived repository
artifacts (data/pipeline/*/hitter_projection_board.json,
data/edgelab/hitter_projection_snapshots/*.jsonl,
data/edgelab/settlements/*.jsonl, data/pipeline/*/hitter_features.json);
writes only under data/edgelab/hitter_validation/. Never touches
data/slate.json, bets.json, config/rules.json, any production
recommendation/risk-gate/settlement file, or any file outside its own
output directory.

IDEMPOTENT "latest" view: rerunning against unchanged inputs reproduces
byte-identical top-level output (all writes go through
lib.atomic_json.write_json_atomic and every derived record is
recomputed fresh from source each run -- nothing is appended to or
merged with a prior run's own "latest" files, so there is no
accumulation state in THOSE files to diverge).

VERSIONED HISTORY (additive, on by default): because new settlement data
and new checkpoint-scheduler captures arrive between reruns, this script
ALSO archives a full, timestamped copy of every report it produces to
data/edgelab/hitter_validation/history/<UTC-run-timestamp>/ before
overwriting the top-level "latest" files -- mirroring this repository's
own existing latest_*.json + dated-history convention
(data/edgelab/analytics/latest_*.json alongside dated report files
elsewhere in data/edgelab/reports/). A prior run's audit artifacts are
therefore never silently lost when a later run's settlement/counts
change -- every historical capture remains inspectable at its own
timestamped path. Pass --no-history to skip this (e.g. for a rapid
local dry-run loop).

Usage:
    python3 scripts/research/build_hitter_projection_audit.py
    python3 scripts/research/build_hitter_projection_audit.py --pipeline-root data/pipeline
    python3 scripts/research/build_hitter_projection_audit.py --no-history
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.atomic_json import write_json_atomic
from lib.research import hitter_projection_audit as audit

OUTPUT_DIR = os.path.join("data", "edgelab", "hitter_validation")
HISTORY_DIRNAME = "history"


def _clv_key_market_family(row):
    return row.get("marketFamily")


def _clv_key_edge_bucket(row):
    edge = row.get("computedEdge")
    if edge is None:
        return "UNKNOWN"
    abs_edge = abs(edge)
    for lo, hi, label in audit.EDGE_BUCKETS:
        if lo <= abs_edge < hi:
            return label
    return "UNKNOWN"


def _clv_key_probability_bucket(row):
    p = row.get("modelProbability")
    if p is None:
        return "UNKNOWN"
    for lo, hi, label in audit.PROBABILITY_BUCKETS:
        if lo <= p < hi:
            return label
    return "UNKNOWN"


def build_reports(pipeline_root, settlements_root, checkpoint_snapshots_root=None):
    checkpoint_snapshots_root = checkpoint_snapshots_root or audit.CHECKPOINT_SNAPSHOTS_ROOT_DEFAULT
    corpus = audit.build_full_corpus(pipeline_root, settlements_root, checkpoint_snapshots_root)
    graded = corpus["graded"]
    primary = audit.primary_metric_rows(graded)

    reports = {}

    reports["provenance_audit"] = audit.provenance_audit(corpus["allRows"], graded, corpus["boardSummaries"])

    reports["calibration_overall"] = audit.overall_calibration(primary)
    reports["calibration_by_bucket"] = {"buckets": audit.bucket_calibration(primary)}
    reports["calibration_by_market"] = audit.market_family_calibration(primary)
    reports["calibration_by_threshold"] = audit.threshold_calibration(primary)

    reports["roi_overall"] = audit.roi_simulation(primary)
    reports["roi_by_market"] = audit.roi_by_market_family(primary)
    reports["roi_by_probability_bucket"] = audit.roi_by_probability_bucket(primary)
    reports["roi_by_threshold"] = audit.roi_by_threshold(primary)
    reports["roi_by_edge_bucket"] = {"buckets": audit.edge_bucket_analysis(primary)}

    reports["clv_summary"] = {
        "overall": audit.clv_summary(primary),
        "byMarketFamily": audit.clv_by_dimension(primary, _clv_key_market_family),
        "byEdgeBucket": audit.clv_by_dimension(primary, _clv_key_edge_bucket),
        "byProbabilityBucket": audit.clv_by_dimension(primary, _clv_key_probability_bucket),
    }

    reports["monotonicity_violations"] = audit.monotonicity_check(corpus["projectedRows"])

    reports["segmentation"] = audit.segmentation_report(primary)
    reports["snapshot_timing"] = audit.snapshot_timing_report(primary)

    unresolved = [g for g in graded if g["propositionOutcome"] == "UNRESOLVED"]
    uncertain_provenance = [g for g in graded if g["provenanceConfidence"] != "PROSPECTIVE_VERIFIED"]
    reports["unresolved_records"] = {
        "unresolvedCount": len(unresolved),
        "uncertainProvenanceCount": len(uncertain_provenance),
        "unresolvedSample": unresolved[:200],
        "uncertainProvenanceSample": uncertain_provenance[:200],
    }

    reports["graded"] = graded
    reports["primaryMetricRowCount"] = len(primary)
    reports["totalProjectedRowCount"] = len(graded)
    reports["independentEvidence"] = audit.independent_evidence_counts(primary)

    return reports


def _write_report_files(reports, output_dir):
    """Writes every report file into `output_dir` (used for both the top-level 'latest' view and each timestamped history snapshot). Returns the summary dict."""
    os.makedirs(output_dir, exist_ok=True)

    write_json_atomic(reports["provenance_audit"], os.path.join(output_dir, "provenance_audit.json"), indent=2)
    write_json_atomic(reports["calibration_by_bucket"], os.path.join(output_dir, "calibration_by_bucket.json"), indent=2)
    write_json_atomic(reports["calibration_by_market"], os.path.join(output_dir, "calibration_by_market.json"), indent=2)
    write_json_atomic(reports["roi_by_market"], os.path.join(output_dir, "roi_by_market.json"), indent=2)
    write_json_atomic(reports["roi_by_edge_bucket"], os.path.join(output_dir, "roi_by_edge_bucket.json"), indent=2)
    write_json_atomic(reports["clv_summary"], os.path.join(output_dir, "clv_summary.json"), indent=2)
    write_json_atomic(reports["monotonicity_violations"], os.path.join(output_dir, "monotonicity_violations.json"), indent=2)
    write_json_atomic(reports["unresolved_records"], os.path.join(output_dir, "unresolved_records.json"), indent=2)
    write_json_atomic({"thresholds": reports["calibration_by_threshold"]}, os.path.join(output_dir, "calibration_by_threshold.json"), indent=2)
    write_json_atomic({"thresholds": reports["roi_by_threshold"]}, os.path.join(output_dir, "roi_by_threshold.json"), indent=2)
    write_json_atomic({"buckets": reports["roi_by_probability_bucket"]}, os.path.join(output_dir, "roi_by_probability_bucket.json"), indent=2)
    write_json_atomic(reports["segmentation"], os.path.join(output_dir, "segmentation.json"), indent=2)
    write_json_atomic(reports["snapshot_timing"], os.path.join(output_dir, "snapshot_timing.json"), indent=2)

    graded_path = os.path.join(output_dir, "graded_projections.jsonl")
    tmp_path = graded_path + ".tmp"
    with open(tmp_path, "w") as fh:
        for row in reports["graded"]:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    os.replace(tmp_path, graded_path)

    summary = {
        "schemaVersion": audit.SCHEMA_VERSION,
        "generatedAt": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "totalProjectedRowCount": reports["totalProjectedRowCount"],
        "primaryMetricRowCount": reports["primaryMetricRowCount"],
        "independentEvidence": reports["independentEvidence"],
        "calibrationOverall": reports["calibration_overall"],
        "roiOverall": reports["roi_overall"],
        "clvOverall": reports["clv_summary"]["overall"],
        "monotonicityViolationCount": reports["monotonicity_violations"]["violationCount"],
        "snapshotTiming": reports["snapshot_timing"],
        "provenance": reports["provenance_audit"],
    }
    write_json_atomic(summary, os.path.join(output_dir, "summary.json"), indent=2)

    return summary


def write_reports(reports, output_dir=OUTPUT_DIR, archive_history=True):
    summary = _write_report_files(reports, output_dir)

    if archive_history:
        run_stamp = summary["generatedAt"].replace(":", "").rstrip("Z")
        history_dir = os.path.join(output_dir, HISTORY_DIRNAME, run_stamp)
        # A rerun within the same wall-clock second (rare, but real in a
        # tight local loop) must never partially clobber a prior history
        # snapshot -- each is a complete, self-contained copy of that
        # run's full report set, written fresh every time.
        if os.path.isdir(history_dir):
            shutil.rmtree(history_dir)
        _write_report_files(reports, history_dir)

    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipeline-root", default=audit.PIPELINE_ROOT_DEFAULT)
    parser.add_argument("--settlements-root", default=audit.SETTLEMENTS_ROOT_DEFAULT)
    parser.add_argument("--checkpoint-snapshots-root", default=audit.CHECKPOINT_SNAPSHOTS_ROOT_DEFAULT)
    parser.add_argument("--output-dir", default=OUTPUT_DIR)
    parser.add_argument("--no-history", action="store_true", help="Skip archiving a timestamped history snapshot (still writes/overwrites the top-level 'latest' report files).")
    args = parser.parse_args()

    reports = build_reports(args.pipeline_root, args.settlements_root, args.checkpoint_snapshots_root)
    summary = write_reports(reports, args.output_dir, archive_history=not args.no_history)

    print(json.dumps({
        "status": "OK",
        "totalProjectedRowCount": summary["totalProjectedRowCount"],
        "primaryMetricRowCount": summary["primaryMetricRowCount"],
        "independentEvidence": summary["independentEvidence"],
        "calibrationOverall": summary["calibrationOverall"],
        "roiOverall": summary["roiOverall"],
        "monotonicityViolationCount": summary["monotonicityViolationCount"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
