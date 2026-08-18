#!/usr/bin/env python3
"""
scripts/research/build_edge_inversion_diagnostic.py
=========================================================
RESEARCH-ONLY. Builds the edge-inversion diagnostic (see
lib/research/hitter_edge_diagnostic.py) against the current hitter
projection audit corpus and writes
data/edgelab/hitter_validation/edge_inversion_diagnostic.json.
Analysis only -- does not change any model formula, threshold, weight,
prior, or recommendation logic.

Usage:
    python3 scripts/research/build_edge_inversion_diagnostic.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.atomic_json import write_json_atomic
from lib.research import hitter_projection_audit as audit
from lib.research.hitter_edge_diagnostic import build_edge_inversion_diagnostic

OUTPUT_PATH = os.path.join("data", "edgelab", "hitter_validation", "edge_inversion_diagnostic.json")


def main():
    corpus = audit.build_full_corpus()
    primary = audit.primary_metric_rows(corpus["graded"])
    report = build_edge_inversion_diagnostic(primary)
    write_json_atomic(report, OUTPUT_PATH, indent=2)
    print(json.dumps({
        "status": "OK",
        "primaryRowCount": len(primary),
        "largeEdgeCohortN": report["largeEdgeCohort"]["calibration"]["n"],
        "smallEdgeCohortN": report["smallEdgeCohort"]["calibration"]["n"],
        "largeEdgeROI": report["largeEdgeCohort"]["roi"]["roi"],
        "smallEdgeROI": report["smallEdgeCohort"]["roi"]["roi"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
