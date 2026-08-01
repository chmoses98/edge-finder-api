#!/usr/bin/env python3
"""
scripts/edgelab/run_analytics.py
====================================
CLI entry point: EdgeLab Phase 2 Milestone 1 cross-date analytics
(docs/EDGELAB_PHASE2_DESIGN.md). Opens one disposable, in-memory DuckDB
session over the existing git-committed data/edgelab/ JSONL(.gz)
partitions (lib/edgelab/analytics.py), runs the Milestone 1 query set,
and writes a machine-readable summary + a human-readable report.

Read-only: this script never writes to data/edgelab/<entity>/ and never
touches production betting/settlement logic. No .duckdb file or Parquet
export is created or committed -- see docs/EDGELAB_ANALYTICS.md.

Deliberately does NOT compute or claim statistical significance --
every grouped metric here carries an explicit sampleStatus
(INSUFFICIENT_SAMPLE / DESCRIPTIVE_ONLY) and must never be read as
actionable strategy advice (see lib.edgelab.analytics.MIN_SAMPLE_SIZE).

Usage:
    python3 scripts/edgelab/run_analytics.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids
from lib.edgelab.analytics import (
    AnalyticsDataError,
    bets_by_canonical_family,
    clv_summary_by_canonical_family,
    completeness_metrics,
    open_session,
    roi_by_canonical_family,
    row_counts_by_entity_and_date,
    unmapped_market_family_values,
)

SUMMARY_PATH = os.path.join("data", "edgelab", "analytics", "latest_summary.json")
REPORT_PATH = os.path.join("data", "edgelab", "reports", "phase2_query_foundation.md")


def build_summary(session):
    return {
        "schemaVersion": "1",
        "generatedAt": ids.utc_now_iso(),
        "entityAvailability": session.availability,
        "rowCountsByEntityAndDate": row_counts_by_entity_and_date(session),
        "betsByCanonicalFamily": bets_by_canonical_family(session),
        "roiByCanonicalFamily": roi_by_canonical_family(session),
        "clvSummaryByCanonicalFamily": clv_summary_by_canonical_family(session),
        "unmappedMarketFamilyValues": unmapped_market_family_values(session),
        "completenessMetrics": completeness_metrics(session),
    }


def render_markdown(summary):
    lines = [
        "# EdgeLab Phase 2 Milestone 1 — Query Foundation Report",
        "",
        f"_Generated {summary['generatedAt']}_",
        "",
        "**This is a descriptive-statistics report, not a calibrated model.** Every",
        "grouped metric below carries an explicit sample-size status; a group",
        "marked `INSUFFICIENT_SAMPLE` (fewer than 20 observations) is noise, not",
        "evidence, and must not be read as a recommendation to change strategy.",
        "",
        "## Entity availability",
    ]
    for entity, info in sorted(summary["entityAvailability"].items()):
        status = "available" if info["available"] else "no files yet"
        lines.append(f"- `{entity}`: {status} ({info['fileCount']} file(s))")

    lines += ["", "## Row counts by entity and date"]
    if summary["rowCountsByEntityAndDate"]:
        lines.append("| Entity | Date | Rows |")
        lines.append("|---|---|---|")
        for r in summary["rowCountsByEntityAndDate"]:
            lines.append(f"| {r['entity']} | {r['date']} | {r['rowCount']} |")
    else:
        lines.append("_(no data available yet)_")

    lines += ["", "## Placed bets by canonical market family"]
    if summary["betsByCanonicalFamily"]:
        lines.append("| Canonical family | Count | Sample status |")
        lines.append("|---|---|---|")
        for r in summary["betsByCanonicalFamily"]:
            lines.append(f"| {r['canonicalMarketFamily']} | {r['count']} | {r['sampleStatus']} |")
    else:
        lines.append("_(no placed bets available yet)_")

    lines += ["", "## ROI by canonical market family (settled bets only)"]
    if summary["roiByCanonicalFamily"]:
        lines.append("| Canonical family | n | Total stake | Total P/L | ROI | Sample status |")
        lines.append("|---|---|---|---|---|---|")
        for r in summary["roiByCanonicalFamily"]:
            roi_pct = f"{r['roi'] * 100:.1f}%" if r["roi"] is not None else "n/a"
            lines.append(f"| {r['canonicalMarketFamily']} | {r['n']} | {r['totalStake']} | {r['totalNetProfitLoss']} | {roi_pct} | {r['sampleStatus']} |")
    else:
        lines.append("_(no settled bets available yet)_")

    lines += ["", "## CLV summary by canonical market family"]
    if summary["clvSummaryByCanonicalFamily"]:
        lines.append("| Canonical family | n | Avg CLV (cents) | Positive | Negative | Sample status |")
        lines.append("|---|---|---|---|---|---|")
        for r in summary["clvSummaryByCanonicalFamily"]:
            lines.append(f"| {r['canonicalMarketFamily']} | {r['n']} | {r['avgClv']:.3f} | {r['positiveCount']} | {r['negativeCount']} | {r['sampleStatus']} |")
    else:
        lines.append("_(no CLV data available yet)_")

    lines += ["", "## Unmapped market-family values"]
    if summary["unmappedMarketFamilyValues"]:
        lines.append("**Action needed** — add these to `lib/edgelab/market_family_mapping.py`:")
        lines.append("")
        lines.append("| Raw value | Count |")
        lines.append("|---|---|")
        for r in summary["unmappedMarketFamilyValues"]:
            lines.append(f"| `{r['rawMarketFamily']}` | {r['count']} |")
    else:
        lines.append("None — every observed `marketFamily` spelling is covered by the mapping table.")

    lines += ["", "## Data-population completeness"]
    lines.append("| Entity | Field | Populated | Total | % | Status |")
    lines.append("|---|---|---|---|---|---|")
    for r in summary["completenessMetrics"]:
        pct = f"{r['pct']:.1f}%" if r["pct"] is not None else "n/a"
        lines.append(f"| {r['entity']} | {r['field']} | {r['populated']} | {r['total']} | {pct} | {r['status']} |")

    return "\n".join(lines) + "\n"


def main():
    os.makedirs(os.path.dirname(SUMMARY_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)

    try:
        with open_session() as session:
            summary = build_summary(session)
    except AnalyticsDataError as exc:
        print(f"[run_analytics] ERROR: {exc}", file=sys.stderr)
        return 1

    with open(SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True, default=str)

    with open(REPORT_PATH, "w") as f:
        f.write(render_markdown(summary))

    print(f"[run_analytics] wrote {SUMMARY_PATH} and {REPORT_PATH}")
    print(f"[run_analytics] entities available: {[e for e, i in summary['entityAvailability'].items() if i['available']]}")
    print(f"[run_analytics] unmapped market-family values: {len(summary['unmappedMarketFamilyValues'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
