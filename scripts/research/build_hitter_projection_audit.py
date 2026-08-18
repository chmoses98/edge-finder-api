#!/usr/bin/env python3
"""
scripts/research/build_hitter_projection_audit.py
======================================================
RESEARCH-ONLY retrospective grading + calibration audit for the archived
prospective hitter projection engine. See lib/research/hitter_projection_audit.py
for the full methodology writeup. Reads only already-archived repository
artifacts (data/pipeline/*/hitter_projection_board.json,
data/edgelab/settlements/*.jsonl, data/pipeline/*/hitter_features.json);
writes only under data/edgelab/hitter_validation/. Never touches
data/slate.json, bets.json, config/rules.json, any production
recommendation/risk-gate/settlement file, or any file outside its own
output directory. Idempotent: rerunning against unchanged inputs
produces byte-identical output (all writes go through
lib.atomic_json.write_json_atomic and every derived record is
recomputed fresh from source each run -- nothing is appended to or
merged with a prior run's output, so there is no accumulation state to
diverge).

Usage:
    python3 scripts/research/build_hitter_projection_audit.py
    python3 scripts/research/build_hitter_projection_audit.py --pipeline-root data/pipeline
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.atomic_json import write_json_atomic
from lib.research import hitter_projection_audit as audit

OUTPUT_DIR = os.path.join("data", "edgelab", "hitter_validation")


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


def build_reports(pipeline_root, settlements_root):
    corpus = audit.build_full_corpus(pipeline_root, settlements_root)
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

    return reports


def write_reports(reports, output_dir=OUTPUT_DIR):
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

    graded_path = os.path.join(output_dir, "graded_projections.jsonl")
    tmp_path = graded_path + ".tmp"
    with open(tmp_path, "w") as fh:
        for row in reports["graded"]:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    os.replace(tmp_path, graded_path)

    summary = {
        "schemaVersion": audit.SCHEMA_VERSION,
        "totalProjectedRowCount": reports["totalProjectedRowCount"],
        "primaryMetricRowCount": reports["primaryMetricRowCount"],
        "calibrationOverall": reports["calibration_overall"],
        "roiOverall": reports["roi_overall"],
        "clvOverall": reports["clv_summary"]["overall"],
        "monotonicityViolationCount": reports["monotonicity_violations"]["violationCount"],
        "provenance": reports["provenance_audit"],
    }
    write_json_atomic(summary, os.path.join(output_dir, "summary.json"), indent=2)

    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipeline-root", default=audit.PIPELINE_ROOT_DEFAULT)
    parser.add_argument("--settlements-root", default=audit.SETTLEMENTS_ROOT_DEFAULT)
    parser.add_argument("--output-dir", default=OUTPUT_DIR)
    args = parser.parse_args()

    reports = build_reports(args.pipeline_root, args.settlements_root)
    summary = write_reports(reports, args.output_dir)

    print(json.dumps({
        "status": "OK",
        "totalProjectedRowCount": summary["totalProjectedRowCount"],
        "primaryMetricRowCount": summary["primaryMetricRowCount"],
        "calibrationOverall": summary["calibrationOverall"],
        "roiOverall": summary["roiOverall"],
        "monotonicityViolationCount": summary["monotonicityViolationCount"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
