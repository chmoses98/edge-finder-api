#!/usr/bin/env python3
"""
scripts/edgelab/run_market_intelligence_report.py
========================================================
CLI entry point: EdgeLab Phase 2 Milestone 6 Market Intelligence Engine
(docs/EDGELAB_MARKET_INTELLIGENCE.md). Opens one disposable, in-memory
DuckDB session over the existing git-committed data/edgelab/ JSONL(.gz)
partitions, builds lib.edgelab.market_comparison's comparisons ONCE and
threads them through every lib.edgelab.market_intelligence function
(avoiding redundant recomputation), and writes a machine-readable
summary + a human-readable report -- same convention as
run_calibration.py and run_market_comparison_report.py.

Read-only: never writes to data/edgelab/<entity>/ and never touches
production betting/recommendation logic. strategy_experiments()'s output
is a labeled hypothetical simulation, not a real recorded outcome --
see lib/edgelab/market_intelligence.py's SIMULATION_LABEL.

Usage:
    python3 scripts/edgelab/run_market_intelligence_report.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import calibration as cal
from lib.edgelab import ids
from lib.edgelab.analytics import AnalyticsDataError, open_session
from lib.edgelab.market_comparison import build_comparisons
from lib.edgelab.market_intelligence import (
    edge_stability,
    expression_performance_profiles,
    market_health_scores,
    opportunity_cost_analysis,
    pass_analysis,
    strategy_experiments,
)

SUMMARY_PATH = os.path.join("data", "edgelab", "analytics", "latest_market_intelligence_report.json")
REPORT_PATH = os.path.join("data", "edgelab", "reports", "phase2_market_intelligence.md")


def build_summary(session):
    comparisons = build_comparisons(session) if session.is_available("model_evaluations") else []
    return {
        "schemaVersion": "1",
        "generatedAt": ids.utc_now_iso(),
        "entityAvailability": session.availability,
        "expressionPerformanceProfiles": expression_performance_profiles(session, comparisons),
        "opportunityCostAnalysis": opportunity_cost_analysis(session, comparisons),
        "passAnalysis": pass_analysis(session, comparisons),
        "strategyExperiments": strategy_experiments(session, comparisons),
        "edgeStability": edge_stability(session),
        "marketHealthScores": market_health_scores(session, comparisons),
        "dailyTrend": cal.daily_trend_report(session),
        "weeklyTrend": cal.weekly_trend_report(session),
        "seasonToDate": cal.season_to_date_report(session),
    }


def _fmt_pct(value):
    return f"{value * 100:.1f}%" if value is not None else "n/a"


def _fmt_num(value, decimals=3):
    return f"{value:.{decimals}f}" if value is not None else "n/a"


def render_markdown(summary):
    lines = [
        "# EdgeLab Phase 2 Milestone 6 — Market Intelligence Report",
        "",
        f"_Generated {summary['generatedAt']}_",
        "",
        "**RESEARCH ONLY.** This report measures how historical expressions of the",
        "model's edge performed. It does not change, and is not consulted by, any",
        "production recommendation, staking, or bet-selection code path. Every",
        "`strategyExperiments` result is a labeled hypothetical simulation of the real",
        "settled bet ledger, never a real recorded outcome or a recommendation to",
        "change strategy. See docs/EDGELAB_MARKET_INTELLIGENCE.md.",
        "",
        "## Expression performance profiles",
        "",
        "| Family | n | Win rate | ROI | Avg CLV | Rec. freq | Pass freq | Best-expr freq | Dominated freq |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for p in summary["expressionPerformanceProfiles"]:
        lines.append(
            f"| {p['canonicalMarketFamily']} | {p['n']} | {_fmt_pct(p['winRate'])} | {_fmt_pct(p['roi'])} | "
            f"{_fmt_num(p['avgClv'])} | {_fmt_pct(p['recommendationFrequency'])} | {_fmt_pct(p['passFrequency'])} | "
            f"{_fmt_pct(p['bestExpressionFrequency'])} | {_fmt_pct(p['dominatedFrequency'])} |"
        )

    occ = summary["opportunityCostAnalysis"]
    lines += [
        "",
        "## Opportunity cost analysis",
        "",
        f"Sample size: **{occ['sampleSize']}** ({occ['sampleStatus']}) — "
        f"{occ['opportunityCostCaseCount']} case(s), frequency {_fmt_pct(occ['opportunityCostFrequency'])}",
        "",
    ]
    if occ["cases"]:
        lines.append("| Bet market | Better expression | Lost edge | Lost CLV | Lost ROI | Dominated |")
        lines.append("|---|---|---|---|---|---|")
        for c in occ["cases"][:20]:
            lines.append(
                f"| {c['betMarketTicker']} | {c['betterExpressionMarketTicker']} | {_fmt_num(c['lostEstimatedEdge'])} | "
                f"{_fmt_num(c['lostClv'])} | {_fmt_num(c['lostRoi'])} | {c['dominatedByBestExpression']} |"
            )
    else:
        lines.append("_(no cases yet)_")

    lines += [
        "",
        "## Pass analysis",
        "",
        "_No hypothetical win/loss or return is computed for never-bet markets -- Recommendation/ModelEvaluation",
        "never record which side (YES/NO) was implicitly favored, so a settlement outcome can't be honestly",
        "attributed. Only settlement STATUS coverage (did the market resolve at all) is reported._",
        "",
        "| Category | n | Status | Settlement status counts |",
        "|---|---|---|---|",
    ]
    for p in summary["passAnalysis"]:
        lines.append(f"| {p['category']} | {p['n']} | {p['status']} | {p['settlementStatusCounts']} |")

    se = summary["strategyExperiments"]
    lines += ["", "## Strategy experiments (SIMULATION -- not real recorded outcomes)", ""]
    if se["baseline"] is None:
        lines.append("_(no bet data available)_")
    else:
        b = se["baseline"]
        lines.append(f"**Baseline** (real, unmodified settled bets): n={b['n']} ({b['status']}), winRate={_fmt_pct(b['winRate'])}, ROI={_fmt_pct(b['roi'])}")
        lines.append("")
        lines.append("| Experiment | n | Status | Win rate | ROI | Delta ROI vs baseline |")
        lines.append("|---|---|---|---|---|---|")
        for e in se["experiments"]:
            lines.append(f"| {e['name']} | {e['n']} | {e['status']} | {_fmt_pct(e['winRate'])} | {_fmt_pct(e['roi'])} | {_fmt_pct(e['deltaRoiVsBaseline'])} |")

    lines += ["", "## Edge stability", "", "| Edge bucket | n | Status | Stable | Volatile | False edge | Unknown |", "|---|---|---|---|---|---|---|"]
    for e in summary["edgeStability"]:
        lines.append(f"| {e['edgeBucket']} | {e['n']} | {e['status']} | {e['stableCount']} | {e['volatileCount']} | {e['falseEdgeCount']} | {e['unknownCount']} |")

    lines += ["", "## Market health scores", "", "| Family | Health score | Sample n | Status |", "|---|---|---|---|"]
    for h in summary["marketHealthScores"]:
        lines.append(f"| {h['canonicalMarketFamily']} | {_fmt_num(h['healthScore'])} | {h['sampleSize']} | {h['sampleStatus']} |")

    lines += [
        "",
        "## Historical trend (daily / weekly / season)",
        "",
        f"- Daily trend points: {len(summary['dailyTrend'])}",
        f"- Weekly trend points: {len(summary['weeklyTrend'])}",
        f"- Season-to-date: {summary['seasonToDate'][0] if summary['seasonToDate'] else 'n/a'}",
    ]

    return "\n".join(lines) + "\n"


def main():
    os.makedirs(os.path.dirname(SUMMARY_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)

    try:
        with open_session() as session:
            summary = build_summary(session)
    except AnalyticsDataError as exc:
        print(f"[run_market_intelligence_report] ERROR: {exc}", file=sys.stderr)
        return 1

    with open(SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True, default=str)

    with open(REPORT_PATH, "w") as f:
        f.write(render_markdown(summary))

    print(f"[run_market_intelligence_report] wrote {SUMMARY_PATH} and {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
