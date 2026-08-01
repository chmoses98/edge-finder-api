#!/usr/bin/env python3
"""
scripts/edgelab/run_calibration.py
=======================================
CLI entry point: EdgeLab Phase 2 Milestone 2 calibration engine
(docs/EDGELAB_CALIBRATION.md). Opens one disposable, in-memory DuckDB
session over the existing git-committed data/edgelab/ JSONL(.gz)
partitions (lib/edgelab/analytics.py), runs every calibration query in
lib/edgelab/calibration.py, and writes a machine-readable summary + a
human-readable report.

Read-only, descriptive statistics only: this script never writes to
data/edgelab/<entity>/, never touches production betting/recommendation/
settlement logic, and never recommends a strategy change. Every bucket
carries an explicit status (INSUFFICIENT_SAMPLE n<20 / DESCRIPTIVE_ONLY
20<=n<100 / CALIBRATED n>=100 -- see lib.edgelab.calibration) that must
never be read as betting advice.

Usage:
    python3 scripts/edgelab/run_calibration.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import calibration as cal
from lib.edgelab import ids
from lib.edgelab.analytics import AnalyticsDataError, open_session

SUMMARY_PATH = os.path.join("data", "edgelab", "analytics", "latest_calibration.json")
REPORT_PATH = os.path.join("data", "edgelab", "reports", "phase2_calibration.md")


def build_summary(session):
    return {
        "schemaVersion": "1",
        "generatedAt": ids.utc_now_iso(),
        "entityAvailability": session.availability,
        "edgeBucketCalibration": cal.edge_bucket_calibration(session),
        "confidenceCalibration": cal.confidence_calibration(session),
        "marketFamilyReport": cal.market_family_report(session),
        "thesisTagCalibration": cal.thesis_tag_calibration(session),
        "thesisTagCooccurrence": cal.thesis_tag_cooccurrence(session),
        "clvBucketCalibration": cal.clv_bucket_calibration(session),
        "clvSignStudy": cal.clv_sign_study(session),
        "timingBucketCalibration": cal.timing_bucket_calibration(session),
        "recommendationPathAnalysis": cal.recommendation_path_calibration(session),
        "modelVersionSourceCalibration": cal.model_version_source_calibration(session),
        "dataQualityCalibration": cal.data_quality_calibration(session),
        "correlationGroupCalibration": cal.correlation_group_calibration(session),
        "dailyTrend": cal.daily_trend_report(session),
        "weeklyTrend": cal.weekly_trend_report(session),
        "monthlyTrend": cal.monthly_trend_report(session),
        "seasonToDate": cal.season_to_date_report(session),
    }


def _fmt_pct(value):
    return f"{value * 100:.1f}%" if value is not None else "n/a"


def _fmt_num(value, digits=3):
    return f"{value:.{digits}f}" if value is not None else "n/a"


def _calibration_table(rows, bucket_field, bucket_header):
    lines = [f"| {bucket_header} | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r[bucket_field]} | {r['n']} | {_fmt_pct(r['actualWinRate'])} | {_fmt_pct(r['expectedWinRate'])} | "
            f"{_fmt_num(r['calibrationError'], 4)} | {_fmt_pct(r['roi'])} | {_fmt_num(r['avgClv'])} | {r['status']} |"
        )
    return lines


def render_markdown(summary):
    lines = [
        "# EdgeLab Phase 2 Milestone 2 — Calibration Report",
        "",
        f"_Generated {summary['generatedAt']}_",
        "",
        "**This report measures historical model performance only. It makes no",
        "betting recommendations and does not influence production recommendation",
        "or staking logic in any way.** Every bucket below carries an explicit",
        "sample-size status: `INSUFFICIENT_SAMPLE` (n<20) is noise, not evidence;",
        "`DESCRIPTIVE_ONLY` (20<=n<100) is a real number that is not yet a",
        "calibrated statistical claim; `CALIBRATED` (n>=100) means enough volume",
        "exists for the reliability numbers to be a meaningful summary -- still",
        "not, by itself, a signal to change strategy.",
        "",
        "## Edge bucket calibration",
    ]
    rows = summary["edgeBucketCalibration"]
    lines += _calibration_table(rows, "edgeBucket", "Edge bucket") if rows else ["_(no decided bets yet)_"]

    lines += ["", "## Confidence calibration"]
    rows = summary["confidenceCalibration"]
    lines += _calibration_table(rows, "confidence", "Confidence") if rows else ["_(no decided bets yet)_"]

    lines += ["", "## Market-family report"]
    rows = summary["marketFamilyReport"]
    if rows:
        lines.append("| Canonical family | Bets | Win % | ROI | Avg CLV | Avg edge | Avg confidence (1-3) | Calibration error | Status |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            lines.append(
                f"| {r['canonicalMarketFamily']} | {r['bets']} | {_fmt_pct(r['actualWinRate'])} | {_fmt_pct(r['roi'])} | "
                f"{_fmt_num(r['avgClv'])} | {_fmt_num(r['avgEdge'])} | {_fmt_num(r['avgConfidenceScore'])} | "
                f"{_fmt_num(r['calibrationError'], 4)} | {r['status']} |"
            )
    else:
        lines.append("_(no decided bets yet)_")

    lines += ["", "## Thesis-tag calibration"]
    rows = summary["thesisTagCalibration"]
    lines += _calibration_table(rows, "thesisTag", "Thesis tag") if rows else [
        "_(no tagged decided bets yet -- thesisTags coverage is 0% in the real",
        "ledger today; this is a known, honest gap, see docs/EDGELAB_CALIBRATION.md)_",
    ]

    lines += ["", "## Thesis-tag co-occurrence"]
    rows = summary["thesisTagCooccurrence"]
    if rows:
        lines.append("| Tag A | Tag B | Co-occurrence count |")
        lines.append("|---|---|---|")
        for r in rows:
            lines.append(f"| {r['tagA']} | {r['tagB']} | {r['coOccurrenceCount']} |")
    else:
        lines.append("_(no co-tagged bets yet)_")

    lines += ["", "## CLV bucket calibration"]
    rows = summary["clvBucketCalibration"]
    lines += _calibration_table(rows, "clvBucket", "CLV bucket") if rows else ["_(no decided bets yet)_"]

    lines += ["", "## CLV sign study (positive / neutral / negative)"]
    rows = summary["clvSignStudy"]
    lines += _calibration_table(rows, "clvSign", "CLV sign") if rows else ["_(no decided bets yet)_"]

    lines += ["", "## Timing-bucket calibration"]
    rows = summary["timingBucketCalibration"]
    lines += _calibration_table(rows, "timingBucket", "Timing bucket") if rows else ["_(no decided bets yet)_"]

    lines += ["", "## Recommendation-path analysis"]
    rows = summary["recommendationPathAnalysis"]
    if rows:
        lines.append("| Path | n | Win rate | ROI | Avg CLV | Avg model prob | Avg market prob | Avg edge | Status |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            lines.append(
                f"| {r['recommendationPath']} | {r['n']} | {_fmt_pct(r['actualWinRate'])} | {_fmt_pct(r['roi'])} | "
                f"{_fmt_num(r['avgClv'])} | {_fmt_num(r.get('avgModelFairProbability'))} | "
                f"{_fmt_num(r.get('avgMarketImpliedProbability'))} | {_fmt_num(r.get('avgEstimatedEdge'))} | {r['status']} |"
            )
        lines.append("")
        lines.append("_`RECOMMENDED_NOT_BET`/`PASSED` rows have no win rate/ROI/CLV: no bet was ever placed on")
        lines.append("them, so there is no real stake or outcome to measure -- only what the model/market")
        lines.append("recorded at decision time. See docs/EDGELAB_CALIBRATION.md._")
    else:
        lines.append("_(no recommendation or bet data available yet)_")

    lines += ["", "## Model version/source calibration"]
    rows = summary["modelVersionSourceCalibration"]
    if rows:
        lines.append("| Model version | Model source | n | Win rate | ROI | Avg CLV | Status |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in rows:
            lines.append(f"| {r['modelVersion']} | {r['modelSource']} | {r['n']} | {_fmt_pct(r['actualWinRate'])} | {_fmt_pct(r['roi'])} | {_fmt_num(r['avgClv'])} | {r['status']} |")
    else:
        lines.append("_(no decided bets linked to a ModelEvaluation yet)_")

    lines += ["", "## Data-quality calibration"]
    rows = summary["dataQualityCalibration"]
    lines += _calibration_table(rows, "dataQuality", "Data quality") if rows else ["_(no decided bets linked to a ModelEvaluation yet)_"]

    lines += ["", "## Correlation-group calibration"]
    rows = summary["correlationGroupCalibration"]
    lines += _calibration_table(rows, "correlationGroup", "Correlation group") if rows else ["_(no decided bets with a correlation group yet)_"]

    for title, key in (("Daily trend", "dailyTrend"), ("Weekly trend", "weeklyTrend"),
                        ("Monthly trend", "monthlyTrend"), ("Season-to-date", "seasonToDate")):
        lines += ["", f"## {title}"]
        rows = summary[key]
        if rows:
            lines.append("| Period | n | Win rate | ROI | Avg CLV | Status |")
            lines.append("|---|---|---|---|---|---|")
            for r in rows:
                lines.append(f"| {r['period']} | {r['n']} | {_fmt_pct(r['actualWinRate'])} | {_fmt_pct(r['roi'])} | {_fmt_num(r['avgClv'])} | {r['status']} |")
        else:
            lines.append("_(no decided bets yet)_")

    return "\n".join(lines) + "\n"


def main():
    os.makedirs(os.path.dirname(SUMMARY_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)

    try:
        with open_session() as session:
            summary = build_summary(session)
    except AnalyticsDataError as exc:
        print(f"[run_calibration] ERROR: {exc}", file=sys.stderr)
        return 1

    with open(SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True, default=str)

    with open(REPORT_PATH, "w") as f:
        f.write(render_markdown(summary))

    print(f"[run_calibration] wrote {SUMMARY_PATH} and {REPORT_PATH}")
    print(f"[run_calibration] season-to-date decided bets: {summary['seasonToDate'][0]['n'] if summary['seasonToDate'] else 0}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
