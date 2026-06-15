#!/usr/bin/env python3
"""
scripts/generate_performance_report.py
========================================
Rolling Performance Report Generator

Output by: market type, trackingType, block class, rule number, tier, edge bucket
Saves to reports/performance_YYYY-MM-DD.json and .md
"""

import json
import os
import sys
from datetime import datetime, timezone
from collections import defaultdict

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, ROOT_DIR)

from lib.promotion_engine import (
    group_bets_by_market, calculate_clv_stats, calculate_win_rate,
    calculate_roi, run_promotion_analysis, MARKET_TYPES
)


def load_bets(path=None):
    """Load bets from bets.json."""
    path = path or os.path.join(ROOT_DIR, "bets.json")
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    return data.get("bets", [])


def group_by_tracking_type(bets):
    """Group bets by trackingType."""
    groups = defaultdict(list)
    for bet in bets:
        tt = bet.get("trackingType") or "unknown"
        groups[tt].append(bet)
    return dict(groups)


def group_by_edge_bucket(bets):
    """Group bets into edge buckets."""
    buckets = defaultdict(list)
    for bet in bets:
        edge = bet.get("edgePct") or bet.get("edge") or 0
        try:
            e = float(edge)
        except (TypeError, ValueError):
            e = 0
        if e >= 3.0:
            buckets["high_3plus"].append(bet)
        elif e >= 1.5:
            buckets["medium_1.5_3"].append(bet)
        elif e >= 1.0:
            buckets["low_1_1.5"].append(bet)
        else:
            buckets["below_threshold"].append(bet)
    return dict(buckets)


def group_by_confidence(bets):
    """Group bets by confidence tier."""
    groups = defaultdict(list)
    for bet in bets:
        conf = bet.get("confidence") or bet.get("betType") or "unknown"
        groups[str(conf).upper()].append(bet)
    return dict(groups)


def build_market_section(market_type, bets):
    """Build performance stats for one market type."""
    settled = [b for b in bets if b.get("result") in ("WIN", "LOSS")]
    paper = [b for b in bets if b.get("trackingType") in ("PAPER", "MODEL_ONLY")]
    real = [b for b in bets if b.get("trackingType") == "REAL" and b.get("actuallyPlaced") is True]
    probe = [b for b in bets if b.get("trackingType") == "REAL_PROBE" and b.get("actuallyPlaced") is True]

    return {
        "marketType": market_type,
        "totalBets": len(bets),
        "settledBets": len(settled),
        "overall": {
            **calculate_win_rate(settled),
            **calculate_clv_stats(settled),
            **calculate_roi(settled),
        },
        "byTrackingType": {
            "PAPER_MODEL_ONLY": {
                **calculate_win_rate([b for b in paper if b.get("result") in ("WIN","LOSS")]),
                **calculate_clv_stats([b for b in paper if b.get("result") in ("WIN","LOSS")]),
            },
            "REAL": {
                **calculate_win_rate([b for b in real if b.get("result") in ("WIN","LOSS")]),
                **calculate_clv_stats([b for b in real if b.get("result") in ("WIN","LOSS")]),
            },
            "REAL_PROBE": {
                **calculate_win_rate([b for b in probe if b.get("result") in ("WIN","LOSS")]),
                **calculate_clv_stats([b for b in probe if b.get("result") in ("WIN","LOSS")]),
            },
        },
        "byEdgeBucket": {
            bucket: {
                **calculate_win_rate([b for b in bucket_bets if b.get("result") in ("WIN","LOSS")]),
                **calculate_clv_stats([b for b in bucket_bets if b.get("result") in ("WIN","LOSS")]),
            }
            for bucket, bucket_bets in group_by_edge_bucket(bets).items()
        },
        "byConfidence": {
            conf: {
                **calculate_win_rate([b for b in conf_bets if b.get("result") in ("WIN","LOSS")]),
            }
            for conf, conf_bets in group_by_confidence(bets).items()
        },
    }


def generate_report(bets, date_str=None):
    """Generate full performance report."""
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    grouped = group_by_market(bets)

    market_sections = {}
    for market_type in MARKET_TYPES:
        market_bets = grouped.get(market_type, [])
        if market_bets:
            market_sections[market_type] = build_market_section(market_type, market_bets)

    # Overall stats
    all_real = [b for b in bets if b.get("trackingType") == "REAL" and b.get("actuallyPlaced") is True]
    all_probe = [b for b in bets if b.get("trackingType") == "REAL_PROBE" and b.get("actuallyPlaced") is True]
    all_settled_real = [b for b in all_real if b.get("result") in ("WIN","LOSS")]
    all_settled_probe = [b for b in all_probe if b.get("result") in ("WIN","LOSS")]

    # Promotion analysis
    promotion = run_promotion_analysis(bets)

    report = {
        "reportDate": date_str,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "totalBets": len(bets),
            "realBets": len(all_real),
            "probeBets": len(all_probe),
            "realSettled": len(all_settled_real),
            "probeSettled": len(all_settled_probe),
            "realStats": {**calculate_win_rate(all_settled_real), **calculate_clv_stats(all_settled_real), **calculate_roi(all_real)},
            "probeStats": {**calculate_win_rate(all_settled_probe), **calculate_clv_stats(all_settled_probe), **calculate_roi(all_probe)},
        },
        "byMarket": market_sections,
        "promotionAnalysis": promotion["decisions"],
    }

    return report


def group_by_market(bets):
    """Group bets by normalized market type."""
    from lib.promotion_engine import group_bets_by_market
    return group_bets_by_market(bets)


def report_to_markdown(report):
    """Convert report dict to markdown string."""
    lines = []
    d = report["reportDate"]
    g = report["generatedAt"]
    lines.append(f"# Performance Report — {d}")
    lines.append(f"*Generated: {g}*\n")

    s = report["summary"]
    lines.append("## Summary")
    lines.append(f"- Total bets: {s['totalBets']}")
    lines.append(f"- Real bets: {s['realBets']} ({s['realSettled']} settled)")
    lines.append(f"- Probe bets: {s['probeBets']} ({s['probeSettled']} settled)")

    rs = s.get("realStats", {})
    if rs.get("winRate") is not None:
        lines.append(f"- Real WR: {rs['winRate']}%")
    if rs.get("avgCLV") is not None:
        lines.append(f"- Real avg CLV: {rs['avgCLV']:.2f}%")
    if rs.get("roi") is not None:
        lines.append(f"- Real ROI: {rs['roi']:.2f}%")

    lines.append("\n## By Market\n")
    lines.append("| Market | N | Settled | WR% | Avg CLV | ROI |")
    lines.append("|--------|---|---------|-----|---------|-----|")

    for market, mdata in report.get("byMarket", {}).items():
        overall = mdata.get("overall", {})
        wr = overall.get("winRate", "—")
        clv = overall.get("avgCLV")
        roi = overall.get("roi")
        n = mdata.get("totalBets", 0)
        settled = mdata.get("settledBets", 0)
        lines.append(f"| {market} | {n} | {settled} | {wr} | {f'{clv:.2f}%' if clv else '—'} | {f'{roi:.2f}%' if roi else '—'} |")

    lines.append("\n## Promotion/Demotion Recommendations\n")
    for market, decision in report.get("promotionAnalysis", {}).items():
        action = decision.get("action", "MAINTAIN")
        reason = decision.get("reason", "")
        curr = decision.get("currentTier", "PAPER")
        rec = decision.get("recommendedTier", curr)
        emoji = {"PROMOTE": "↑", "DEMOTE": "↓", "MAINTAIN": "=", "INSUFFICIENT_SAMPLE": "?"}
        lines.append(f"- **{market}** {emoji.get(action,'?')} {action}: {curr} → {rec}")
        lines.append(f"  - {reason}")

    return "\n".join(lines)


def save_report(report, date_str, root_dir=None):
    """Save report to reports/ directory."""
    root_dir = root_dir or ROOT_DIR
    reports_dir = os.path.join(root_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    json_path = os.path.join(reports_dir, f"performance_{date_str}.json")
    md_path = os.path.join(reports_dir, f"performance_{date_str}.md")

    with open(json_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"[generate_performance_report] Written: {json_path}")

    md = report_to_markdown(report)
    with open(md_path, "w") as f:
        f.write(md)
    print(f"[generate_performance_report] Written: {md_path}")

    return json_path, md_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD (default: today)")
    parser.add_argument("--bets", help="Path to bets.json")
    args = parser.parse_args()

    date_str = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bets = load_bets(args.bets)
    report = generate_report(bets, date_str)
    json_path, md_path = save_report(report, date_str)
    print(f"Report saved: {json_path}")
