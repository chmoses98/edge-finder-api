#!/usr/bin/env python3
"""
scripts/generate_wager_research_report.py
=============================================
Generates human- and machine-readable performance reports from the
canonical wager research database (data/research/wagers.jsonl, built by
scripts/build_wager_research_db.py).

Writes:
    data/research/reports/summary.json
    data/research/reports/summary.md
    data/research/reports/daily/<date>.json
    data/research/reports/daily/<date>.md

Every aggregate always reports its own sample size prominently — no
performance claim is presented without the N it is based on. This
module does not modify scripts/build_wager_research_db.py's rows or any
model calibration; it only aggregates what is already there.
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, ROOT_DIR)

RESEARCH_DIR = os.path.join(ROOT_DIR, "data", "research")
WAGERS_PATH = os.path.join(RESEARCH_DIR, "wagers.jsonl")
REPORTS_DIR = os.path.join(RESEARCH_DIR, "reports")

SETTLED = {"WIN", "LOSS", "PUSH", "VOID"}
BINARY_RESULT = {"WIN", "LOSS"}


def load_rows(path=None):
    path = path or WAGERS_PATH
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 3) if values else None


def _sum(values):
    values = [v for v in values if v is not None]
    return round(sum(values), 2) if values else None


def summarize(rows):
    """
    One performance summary block for an arbitrary list of rows. Always
    reports sampleSize (all rows passed in) and settledSampleSize
    (rows with a final W/L/Push/Void result) side by side, since some
    metrics (ROI, record) are only meaningful over settled wagers while
    others (CLV) can include any wager with a captured closing line
    regardless of settlement.
    """
    n = len(rows)
    settled = [r for r in rows if r.get("result") in SETTLED]
    binary = [r for r in settled if r.get("result") in BINARY_RESULT]
    wins = sum(1 for r in binary if r["result"] == "WIN")
    losses = sum(1 for r in binary if r["result"] == "LOSS")
    pushes = sum(1 for r in settled if r["result"] in ("PUSH", "VOID"))

    total_risked = _sum(r.get("stake") for r in settled)
    gross_returned = _sum(r.get("grossReturn") for r in settled)
    net_profit = _sum(r.get("netProfit") for r in settled)
    roi_pct = round(net_profit / total_risked * 100, 3) if (net_profit is not None and total_risked) else None

    ask_clv_vals = [r.get("clvAskPct") for r in rows if r.get("clvAskPct") is not None]
    mid_clv_vals = [r.get("clvMidPct") for r in rows if r.get("clvMidPct") is not None]
    positive_clv = [v for v in mid_clv_vals if v > 0]
    valid_closing = [r for r in rows if r.get("clvCaptureStatus") in ("OK",) or r.get("closingMidPct") is not None]

    return {
        "sampleSize": n,
        "settledSampleSize": len(settled),
        "record": {"wins": wins, "losses": losses, "pushesVoids": pushes},
        "totalRisked": total_risked,
        "grossReturned": gross_returned,
        "netProfit": net_profit,
        "roiPct": roi_pct,
        "avgAskClvPct": _avg(ask_clv_vals),
        "avgMidClvPct": _avg(mid_clv_vals),
        "positiveClvRatePct": round(len(positive_clv) / len(mid_clv_vals) * 100, 2) if mid_clv_vals else None,
        "validClosingCaptureRatePct": round(len(valid_closing) / n * 100, 2) if n else None,
    }


def summarize_paper(rows):
    """
    Spread-correction mission Part 5: performance summary for PAPER
    (trackingType="PAPER") rows only, using hypotheticalStake/
    hypotheticalNetProfit/hypotheticalRoiPct -- NEVER stake/netProfit/
    roiPct (those fields are always null on a paper row by
    construction, see scripts/build_wager_research_db.py's
    build_paper_row()). This keeps hypothetical paper performance
    structurally impossible to blend with real bankroll performance.
    """
    n = len(rows)
    settled = [r for r in rows if r.get("result") in BINARY_RESULT]
    wins = sum(1 for r in settled if r["result"] == "WIN")
    losses = sum(1 for r in settled if r["result"] == "LOSS")

    total_risked = _sum(r.get("hypotheticalStake") for r in settled)
    net_profit = _sum(r.get("hypotheticalNetProfit") for r in settled)
    roi_pct = round(net_profit / total_risked * 100, 3) if (net_profit is not None and total_risked) else None

    ask_clv_vals = [r.get("clvAskPct") for r in rows if r.get("clvAskPct") is not None]
    mid_clv_vals = [r.get("clvMidPct") for r in rows if r.get("clvMidPct") is not None]
    positive_clv = [v for v in mid_clv_vals if v > 0]

    return {
        "sampleSize": n,
        "settledSampleSize": len(settled),
        "record": {"wins": wins, "losses": losses},
        "hypotheticalTotalRisked": total_risked,
        "hypotheticalNetProfit": net_profit,
        "hypotheticalRoiPct": roi_pct,
        "avgAskClvPct": _avg(ask_clv_vals),
        "avgMidClvPct": _avg(mid_clv_vals),
        "positiveClvRatePct": round(len(positive_clv) / len(mid_clv_vals) * 100, 2) if mid_clv_vals else None,
    }


def build_paper_breakdowns(rows):
    return {
        "byPeriod": {k: summarize_paper(v) for k, v in
                     defaultdict(list, group_rows(rows, lambda r: r.get("period") or "unknown")).items()},
        "byFavoriteUnderdog": {k: summarize_paper(v) for k, v in
                               defaultdict(list, group_rows(rows, lambda r: r.get("favoriteOrUnderdog") or "unknown")).items()},
        "byLineType": {k: summarize_paper(v) for k, v in
                       defaultdict(list, group_rows(rows, lambda r: "alternate" if r.get("line") is not None
                                    else "primary_or_no_line")).items()},
    }


def group_rows(rows, key_fn):
    groups = defaultdict(list)
    for r in rows:
        groups[key_fn(r)].append(r)
    return groups


def group_by(rows, key_fn):
    groups = defaultdict(list)
    for r in rows:
        groups[key_fn(r)].append(r)
    return {k: summarize(v) for k, v in groups.items()}


def build_breakdowns(rows):
    return {
        "byMarketFamily": group_by(rows, lambda r: r.get("marketFamily") or "unknown"),
        "byLineType": group_by(rows, lambda r: "alternate" if r.get("line") is not None and r.get("marketFamily")
                                else "primary_or_no_line"),
        "byPeriod": group_by(rows, lambda r: r.get("period") or "unknown"),
        "bySource": group_by(rows, lambda r: r.get("source") or "unknown"),
        "byConfidenceTier": group_by(rows, lambda r: r.get("confidenceTier") or "unknown"),
        "byFavoriteUnderdog": group_by(rows, lambda r: r.get("favoriteOrUnderdog") or "unknown"),
        "byLineupConfirmation": group_by(rows, lambda r: r.get("lineupConfirmationStatus") if r.get(
            "lineupConfirmationStatus") is not None else "unknown"),
        "byModelSupportStatus": group_by(rows, lambda r: r.get("modelSupportStatus") or "unknown"),
    }


def parse_date(d):
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def rows_in_window(rows, days, reference_date=None):
    """
    Last N SETTLED BETTING DAYS (distinct dates with at least one
    settled wager), not just the last N calendar days -- a day with no
    settled action does not count toward the window.
    """
    settled_dates = sorted({r["date"] for r in rows if r.get("result") in SETTLED and r.get("date")}, reverse=True)
    window_dates = set(settled_dates[:days])
    return [r for r in rows if r.get("date") in window_dates]


def season_start_for(date_str):
    """MLB season proxy: everything from March 1 of the wager's own year onward."""
    d = parse_date(date_str)
    if d is None:
        return None
    return f"{d.year}-03-01"


def _split_real_paper(rows):
    """
    Real-money/manual (bankroll-counting) rows vs PAPER rows -- never
    blended. A row with no trackingType (should not happen post
    spread-correction-mission, but tolerated for any legacy/injected
    fixture row) is treated as real, matching the pre-existing
    behavior before trackingType existed.
    """
    real = [r for r in rows if r.get("trackingType") != "PAPER"]
    paper = [r for r in rows if r.get("trackingType") == "PAPER"]
    return real, paper


def build_summary_report(rows):
    real_rows, paper_rows = _split_real_paper(rows)

    all_time = summarize(real_rows)
    last_7 = summarize(rows_in_window(real_rows, 7))
    last_30 = summarize(rows_in_window(real_rows, 30))

    latest_date = max((r["date"] for r in real_rows if r.get("date")), default=None)
    season_cutoff = season_start_for(latest_date) if latest_date else None
    season_rows = [r for r in real_rows if season_cutoff and r.get("date") and r["date"] >= season_cutoff]
    season = summarize(season_rows)

    return {
        "generatedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "allTime": all_time,
        "last7SettledBettingDays": last_7,
        "last30SettledBettingDays": last_30,
        "currentSeason": season,
        "breakdowns": build_breakdowns(real_rows),
        # Spread-correction mission Part 5: paper (Rule-81/not-yet-
        # activated-blocked) spread performance, reported SEPARATELY
        # from real-money performance above -- never blended into
        # allTime/last7/last30/currentSeason, which are real-bankroll
        # only.
        "paperSpreadPerformance": {
            "allTime": summarize_paper(paper_rows),
            "last7SettledBettingDays": summarize_paper(rows_in_window(paper_rows, 7)),
            "breakdowns": build_paper_breakdowns(paper_rows),
        },
    }


def build_daily_report(rows, date_str):
    day_rows = [r for r in rows if r.get("date") == date_str]
    real_rows, paper_rows = _split_real_paper(day_rows)
    return {
        "date": date_str,
        "generatedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": summarize(real_rows),
        "breakdowns": build_breakdowns(real_rows),
        "paperSpreadPerformance": summarize_paper(paper_rows),
    }


def _fmt(v):
    return "n/a" if v is None else v


def render_summary_md(report):
    lines = [
        "# Wager Research Summary", "",
        f"Generated: {report['generatedAt']}", "",
        "## All time",
        f"- Sample size: {report['allTime']['sampleSize']} (settled: {report['allTime']['settledSampleSize']})",
        f"- Record: {report['allTime']['record']}",
        f"- Net profit: {_fmt(report['allTime']['netProfit'])}  ROI: {_fmt(report['allTime']['roiPct'])}%",
        f"- Avg ask CLV: {_fmt(report['allTime']['avgAskClvPct'])}%  Avg mid CLV: {_fmt(report['allTime']['avgMidClvPct'])}%",
        "",
        "## Last 7 settled betting days",
        f"- Sample size: {report['last7SettledBettingDays']['sampleSize']} "
        f"(settled: {report['last7SettledBettingDays']['settledSampleSize']})",
        f"- Net profit: {_fmt(report['last7SettledBettingDays']['netProfit'])}  "
        f"ROI: {_fmt(report['last7SettledBettingDays']['roiPct'])}%",
        "",
        "## Last 30 settled betting days",
        f"- Sample size: {report['last30SettledBettingDays']['sampleSize']} "
        f"(settled: {report['last30SettledBettingDays']['settledSampleSize']})",
        f"- Net profit: {_fmt(report['last30SettledBettingDays']['netProfit'])}  "
        f"ROI: {_fmt(report['last30SettledBettingDays']['roiPct'])}%",
        "",
        "## Current season",
        f"- Sample size: {report['currentSeason']['sampleSize']} "
        f"(settled: {report['currentSeason']['settledSampleSize']})",
        f"- Net profit: {_fmt(report['currentSeason']['netProfit'])}  "
        f"ROI: {_fmt(report['currentSeason']['roiPct'])}%",
        "",
        "## By market family",
        "| Family | N | Record | Net Profit | ROI% | Avg Mid CLV% |",
        "|---|---|---|---|---|---|",
    ]
    for fam, s in sorted(report["breakdowns"]["byMarketFamily"].items()):
        rec = s["record"]
        lines.append(f"| {fam} | {s['sampleSize']} | {rec['wins']}-{rec['losses']}-{rec['pushesVoids']} "
                     f"| {_fmt(s['netProfit'])} | {_fmt(s['roiPct'])} | {_fmt(s['avgMidClvPct'])} |")
    lines.append("")
    lines.append("*Sample sizes are shown for every row above — treat any row with a small N as directional only.*")
    return "\n".join(lines) + "\n"


def render_daily_md(report):
    s = report["summary"]
    lines = [
        f"# Wager Research Daily Report — {report['date']}", "",
        f"Generated: {report['generatedAt']}", "",
        f"- Sample size: {s['sampleSize']} (settled: {s['settledSampleSize']})",
        f"- Record: {s['record']}",
        f"- Net profit: {_fmt(s['netProfit'])}  ROI: {_fmt(s['roiPct'])}%",
        f"- Avg ask CLV: {_fmt(s['avgAskClvPct'])}%  Avg mid CLV: {_fmt(s['avgMidClvPct'])}%",
        f"- Positive CLV rate: {_fmt(s['positiveClvRatePct'])}%",
        f"- Valid closing-capture rate: {_fmt(s['validClosingCaptureRatePct'])}%",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(wagers_path=None, out_dir=None, target_date=None, dry_run=False):
    out_dir = out_dir or REPORTS_DIR
    rows = load_rows(wagers_path)
    summary = build_summary_report(rows)

    daily_date = target_date or max((r["date"] for r in rows if r.get("date")), default=None)
    daily = build_daily_report(rows, daily_date) if daily_date else None

    if not dry_run:
        os.makedirs(out_dir, exist_ok=True)
        os.makedirs(os.path.join(out_dir, "daily"), exist_ok=True)
        with open(os.path.join(out_dir, "summary.json"), "w") as f:
            json.dump(summary, f, indent=2)
        with open(os.path.join(out_dir, "summary.md"), "w") as f:
            f.write(render_summary_md(summary))
        if daily:
            with open(os.path.join(out_dir, "daily", f"{daily_date}.json"), "w") as f:
                json.dump(daily, f, indent=2)
            with open(os.path.join(out_dir, "daily", f"{daily_date}.md"), "w") as f:
                f.write(render_daily_md(daily))

    return {"summary": summary, "daily": daily}


if __name__ == "__main__":
    arg_date = sys.argv[1] if len(sys.argv) > 1 else None
    result = main(target_date=arg_date)
    print(json.dumps({k: v for k, v in result["summary"].items() if k != "breakdowns"}, indent=2))
