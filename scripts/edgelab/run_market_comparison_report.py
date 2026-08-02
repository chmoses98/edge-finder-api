#!/usr/bin/env python3
"""
scripts/edgelab/run_market_comparison_report.py
====================================================
CLI entry point: EdgeLab Phase 2 Milestone 5 (docs/EDGELAB_MARKET_COMPARISON.md)
RESEARCH-ONLY same-game market comparison report. Opens one disposable,
in-memory DuckDB session over the existing git-committed data/edgelab/
JSONL(.gz) partitions, builds every market's comparison result via
lib.edgelab.market_comparison.build_comparisons(), and writes a
machine-readable summary + a human-readable report -- same convention as
scripts/edgelab/run_calibration.py and run_model_evaluation_report.py.

Read-only: never writes to data/edgelab/<entity>/ and never touches
production betting/recommendation logic. This report does not change,
and is not consulted by, any production recommendation, staking, or bet
selection code path.

Usage:
    python3 scripts/edgelab/run_market_comparison_report.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids
from lib.edgelab.analytics import AnalyticsDataError, open_session
from lib.edgelab.calibration import market_family_calibration
from lib.edgelab.market_comparison import (
    STATUS_ALTERNATIVE_EXPRESSION,
    STATUS_BEST_EXPRESSION,
    STATUS_DISTINCT_THESIS,
    STATUS_DOMINATED_MARKET,
    STATUS_HIGH_TIE_RISK,
    STATUS_INCOMPLETE_COMPARISON,
    STATUS_LOW_DATA_QUALITY,
    STATUS_LOW_LIQUIDITY,
    STATUS_NO_MODEL_SUPPORT,
    STATUS_NOT_COMPARABLE,
    build_comparisons,
    historical_analysis,
)

SUMMARY_PATH = os.path.join("data", "edgelab", "analytics", "latest_market_comparison_report.json")
REPORT_PATH = os.path.join("data", "edgelab", "reports", "phase2_market_comparison.md")

_ALL_STATUSES = (
    STATUS_BEST_EXPRESSION, STATUS_ALTERNATIVE_EXPRESSION, STATUS_DOMINATED_MARKET,
    STATUS_INCOMPLETE_COMPARISON, STATUS_NO_MODEL_SUPPORT, STATUS_LOW_DATA_QUALITY,
    STATUS_LOW_LIQUIDITY, STATUS_HIGH_TIE_RISK, STATUS_DISTINCT_THESIS, STATUS_NOT_COMPARABLE,
)


def _calibration_status_by_family(session):
    return {r["canonicalMarketFamily"]: r["status"] for r in market_family_calibration(session)}


def build_summary(session):
    calibration_status_by_family = _calibration_status_by_family(session)
    comparisons = build_comparisons(session, calibration_status_by_family)
    status_counts = {status: 0 for status in _ALL_STATUSES}
    for c in comparisons:
        status_counts[c["comparisonStatus"]] = status_counts.get(c["comparisonStatus"], 0) + 1
    return {
        "schemaVersion": "1",
        "generatedAt": ids.utc_now_iso(),
        "entityAvailability": session.availability,
        "totalMarketsCompared": len(comparisons),
        "statusCounts": status_counts,
        "comparisons": comparisons,
        "historicalAnalysis": historical_analysis(comparisons),
    }


def render_markdown(summary):
    lines = [
        "# EdgeLab Phase 2 Milestone 5 — Market Comparison Report",
        "",
        f"_Generated {summary['generatedAt']}_",
        "",
        "**RESEARCH ONLY.** This report compares different ways of expressing the",
        "same underlying baseball edge (e.g. full-game ML vs F5 ML vs run line).",
        "It does not change, and is not consulted by, any production recommendation,",
        "staking, or bet-selection code path. See docs/EDGELAB_MARKET_COMPARISON.md.",
        "",
        f"Total markets compared: **{summary['totalMarketsCompared']}**",
        "",
        "## Comparison status counts",
        "",
        "| Status | Count |",
        "|---|---|",
    ]
    for status in _ALL_STATUSES:
        lines.append(f"| {status} | {summary['statusCounts'].get(status, 0)} |")

    ha = summary["historicalAnalysis"]
    lines += [
        "",
        "## Historical analysis",
        "",
        f"- Games with comparable markets: **{ha['gamesWithComparableMarkets']}**",
        f"- Expression clusters (size > 1): **{ha['expressionClusters']}**",
        f"- Placed-bet audit sample size: **{ha['placedBetAuditSampleSize']}** ({ha['placedBetAuditSampleStatus']})",
        f"- Placed bets that were NOT the top-ranked expression in their cluster: **{ha['placedBetNotTopRankedCount']}**",
        "",
        "### Best-expression counts by canonical family",
    ]
    if ha["bestExpressionCountsByFamily"]:
        for family, n in sorted(ha["bestExpressionCountsByFamily"].items()):
            lines.append(f"- {family}: {n}")
    else:
        lines.append("_(none yet)_")

    lines.append("")
    lines.append("### Dominated-market counts by canonical family")
    if ha["dominatedMarketCountsByFamily"]:
        for family, n in sorted(ha["dominatedMarketCountsByFamily"].items()):
            lines.append(f"- {family}: {n}")
    else:
        lines.append("_(none yet)_")

    lines.append("")
    lines.append("### Missing-data blockers (INCOMPLETE_COMPARISON, by missing field set)")
    if ha["missingDataBlockers"]:
        for fields, n in sorted(ha["missingDataBlockers"].items(), key=lambda kv: -kv[1]):
            lines.append(f"- `{fields}`: {n}")
    else:
        lines.append("_(none)_")

    lines += [
        "",
        "### Dominated-market examples",
    ]
    dominated_examples = [c for c in summary["comparisons"] if c["comparisonStatus"] == STATUS_DOMINATED_MARKET][:10]
    if dominated_examples:
        lines.append("| Market | Dominated by | Reasons |")
        lines.append("|---|---|---|")
        for c in dominated_examples:
            lines.append(f"| {c['marketTicker'] or c['selection']} | {c['dominantMarketTicker']} | {', '.join(c['dominationReasons'])} |")
    else:
        lines.append("_(none yet)_")

    lines += [
        "",
        "### Best-expression examples",
    ]
    best_examples = [c for c in summary["comparisons"] if c["comparisonStatus"] == STATUS_BEST_EXPRESSION][:10]
    if best_examples:
        lines.append("| Market | Cluster | Score |")
        lines.append("|---|---|---|")
        for c in best_examples:
            score = f"{c['score']:.3f}" if c["score"] is not None else "n/a"
            lines.append(f"| {c['marketTicker'] or c['selection']} | {c['clusterId']} | {score} |")
    else:
        lines.append("_(none yet)_")

    lines += [
        "",
        "## Known limitations",
        "",
        "- `liquidity` is never populated -- no volume/depth field exists anywhere in this schema.",
        "- `bidAskSpread` is only known for markets with a placed bet's CLV quote, so most",
        "  evaluated-but-never-bet markets show LOW_LIQUIDITY/bidAskSpread as unknown, not zero.",
        "- The comparison score's weights (SCORE_WEIGHTS) are illustrative defaults, not tuned",
        "  or backtested against outcome data.",
        "- Pitcher strikeouts/outs markets are not in the current 11-market production set",
        "  (config/rules.json), so PLAYER_PROP clustering/domination is structurally supported",
        "  but currently exercised only by this module's tests, not by real historical data.",
        "- This report is research-only: it does not change production recommendations,",
        "  staking, or bet selection.",
    ]

    return "\n".join(lines) + "\n"


def main():
    os.makedirs(os.path.dirname(SUMMARY_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)

    try:
        with open_session() as session:
            summary = build_summary(session)
    except AnalyticsDataError as exc:
        print(f"[run_market_comparison_report] ERROR: {exc}", file=sys.stderr)
        return 1

    with open(SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True, default=str)

    with open(REPORT_PATH, "w") as f:
        f.write(render_markdown(summary))

    print(f"[run_market_comparison_report] wrote {SUMMARY_PATH} and {REPORT_PATH}")
    print(f"[run_market_comparison_report] total markets compared: {summary['totalMarketsCompared']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
