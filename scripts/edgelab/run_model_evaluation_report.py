#!/usr/bin/env python3
"""
scripts/edgelab/run_model_evaluation_report.py
====================================================
CLI entry point: EdgeLab Phase 2 Milestone 3 (docs/EDGELAB_MODEL_EVALUATION.md)
data-population report -- how completely the ModelEvaluation ledger
covers what the model actually evaluated, and how well it links to
Recommendation/PlacedBet/Settlement. Opens one disposable, in-memory
DuckDB session over the existing git-committed data/edgelab/ JSONL(.gz)
partitions and writes a machine-readable summary + a human-readable
report -- same convention as scripts/edgelab/run_analytics.py and
run_calibration.py.

Read-only: never writes to data/edgelab/<entity>/ and never touches
production betting/recommendation logic.

Usage:
    python3 scripts/edgelab/run_model_evaluation_report.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids
from lib.edgelab.analytics import AnalyticsDataError, open_session
from lib.edgelab.model_evaluation import (
    population_by_canonical_family,
    population_by_date,
    population_by_model_version_and_source,
    population_by_recommendation_status,
    population_report,
    unresolved_metadata_report,
)

SUMMARY_PATH = os.path.join("data", "edgelab", "analytics", "latest_model_evaluation_report.json")
REPORT_PATH = os.path.join("data", "edgelab", "reports", "phase2_model_evaluation.md")


def build_summary(session):
    return {
        "schemaVersion": "1",
        "generatedAt": ids.utc_now_iso(),
        "entityAvailability": session.availability,
        "populationReport": population_report(session),
        "populationByCanonicalFamily": population_by_canonical_family(session),
        "populationByModelVersionAndSource": population_by_model_version_and_source(session),
        "populationByDate": population_by_date(session),
        "populationByRecommendationStatus": population_by_recommendation_status(session),
        "unresolvedMetadataReport": unresolved_metadata_report(session),
    }


def _fmt_pct_block(block):
    if block is None:
        return "n/a (entity unavailable)"
    pct = block["pct"]
    return f"{block['count']} ({pct:.1f}%)" if pct is not None else f"{block['count']} (n/a)"


def render_markdown(summary):
    lines = [
        "# EdgeLab Phase 2 Milestone 3/4 — Model Evaluation Report",
        "",
        f"_Generated {summary['generatedAt']}_",
        "",
        "**This report measures data completeness and linkage only.** It does not",
        "evaluate model accuracy (see docs/EDGELAB_CALIBRATION.md for that) and makes",
        "no betting recommendations.",
        "",
        "## Population coverage",
    ]
    pr = summary["populationReport"]
    if pr is None:
        lines.append("_(no ModelEvaluation files yet)_")
    else:
        lines.append(f"Total ModelEvaluation records: **{pr['total']}**")
        lines.append("")
        lines.append("| Field | Count (%) |")
        lines.append("|---|---|")
        lines.append(f"| modelFairProbability | {_fmt_pct_block(pr['modelFairProbability'])} |")
        lines.append(f"| estimatedEdge | {_fmt_pct_block(pr['estimatedEdge'])} |")
        lines.append(f"| confidence | {_fmt_pct_block(pr['confidence'])} |")
        lines.append(f"| thesisTags | {_fmt_pct_block(pr['thesisTags'])} |")
        lines.append(f"| linked to Recommendation | {_fmt_pct_block(pr['linkedToRecommendation'])} |")
        lines.append(f"| linked to PlacedBet | {_fmt_pct_block(pr['linkedToPlacedBet'])} |")
        lines.append(f"| linked to Settlement | {_fmt_pct_block(pr['linkedToSettlement'])} |")

    lines += ["", "## Breakdown by canonical market family"]
    rows = summary["populationByCanonicalFamily"]
    if rows:
        lines.append("| Canonical family | n | % w/ prob | % w/ edge | % w/ confidence | % w/ tags |")
        lines.append("|---|---|---|---|---|---|")
        for r in rows:
            def _p(v):
                return f"{v:.1f}%" if v is not None else "n/a"
            lines.append(f"| {r['canonicalMarketFamily']} | {r['n']} | {_p(r['pctModelFairProbability'])} | {_p(r['pctEstimatedEdge'])} | {_p(r['pctConfidence'])} | {_p(r['pctThesisTags'])} |")
    else:
        lines.append("_(no ModelEvaluation records available yet)_")

    lines += ["", "## Breakdown by model version / source"]
    rows = summary["populationByModelVersionAndSource"]
    if rows:
        lines.append("| Model version | Model source | n |")
        lines.append("|---|---|---|")
        for r in rows:
            lines.append(f"| {r['modelVersion']} | {r['modelSource']} | {r['n']} |")
    else:
        lines.append("_(no ModelEvaluation records available yet)_")

    def _p(v):
        return f"{v:.1f}%" if v is not None else "n/a"

    lines += ["", "## Breakdown by date"]
    rows = summary["populationByDate"]
    if rows:
        lines.append("| Date | n | % w/ prob | % w/ edge | % w/ confidence | % w/ tags | % w/ correlation groups |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in rows:
            lines.append(f"| {r['date']} | {r['n']} | {_p(r['pctModelFairProbability'])} | {_p(r['pctEstimatedEdge'])} | {_p(r['pctConfidence'])} | {_p(r['pctThesisTags'])} | {_p(r['pctCorrelationGroups'])} |")
    else:
        lines.append("_(no ModelEvaluation records available yet)_")

    lines += ["", "## Breakdown by recommendation status"]
    rows = summary["populationByRecommendationStatus"]
    if rows:
        lines.append("| Recommendation status | n | % w/ prob | % w/ confidence | % w/ tags |")
        lines.append("|---|---|---|---|---|")
        for r in rows:
            lines.append(f"| {r['recommendationStatus']} | {r['n']} | {_p(r['pctModelFairProbability'])} | {_p(r['pctConfidence'])} | {_p(r['pctThesisTags'])} |")
    else:
        lines.append("_(no linked Recommendation data available yet)_")

    lines += ["", "## Unresolved / conflicting metadata"]
    umr = summary["unresolvedMetadataReport"]
    if umr is None:
        lines.append("_(no ModelEvaluation records available yet)_")
    else:
        lines.append(f"Of **{umr['totalEvaluated']}** fully `EVALUATED` records:")
        lines.append(f"- **{umr['evaluatedMissingConfidence']}** are missing `confidence` entirely.")
        lines.append(f"- **{umr['evaluatedMissingLineupEvidence']}** have no lineup evidence at all (`lineupConfirmationState=UNKNOWN`).")
        lines.append("")
        lines.append("A non-zero count here is a genuine data gap in the upstream pipeline artifact for that")
        lines.append("specific row, not a query defect -- see docs/EDGELAB_EVALUATION_METADATA.md.")

    return "\n".join(lines) + "\n"


def main():
    os.makedirs(os.path.dirname(SUMMARY_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)

    try:
        with open_session() as session:
            summary = build_summary(session)
    except AnalyticsDataError as exc:
        print(f"[run_model_evaluation_report] ERROR: {exc}", file=sys.stderr)
        return 1

    with open(SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True, default=str)

    with open(REPORT_PATH, "w") as f:
        f.write(render_markdown(summary))

    print(f"[run_model_evaluation_report] wrote {SUMMARY_PATH} and {REPORT_PATH}")
    total = summary["populationReport"]["total"] if summary["populationReport"] else 0
    print(f"[run_model_evaluation_report] total model evaluations: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
