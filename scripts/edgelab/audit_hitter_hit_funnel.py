#!/usr/bin/env python3
"""Read-only raw-to-standardized hitter-hit checkpoint funnel; no inferred history."""
import argparse
from collections import Counter
import json
import os
from datetime import datetime, timezone

from scripts.edgelab.run_research_reports import _discover_dates, _load_universe
from lib.edgelab.research_dataset import build_opportunity_rows
from lib.edgelab.market_family_mapping import canonicalize_market_family

def utc_date(value):
    if not isinstance(value, str):
        return "MISSING_OR_INVALID"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None or dt.utcoffset() is None:
            return "MISSING_OR_INVALID"
        return dt.astimezone(timezone.utc).date().isoformat()
    except ValueError:
        return "MISSING_OR_INVALID"

def summarize(observations, opportunity_rows, source_dates):
    raw_by_day = Counter()
    raw_hit_by_day = Counter()
    raw_family = Counter()
    raw_checkpoint = Counter()
    raw_hit_tickers = set()
    for obs in observations:
        day = utc_date(obs.get("capturedAt"))
        raw_by_day[day] += 1
        family = canonicalize_market_family(obs.get("marketFamily"))
        raw_family[str(family)] += 1
        if family == "hitter_hits":
            raw_hit_by_day[day] += 1
            raw_checkpoint[str(obs.get("checkpoint"))] += 1
            if obs.get("marketTicker"):
                raw_hit_tickers.add(obs["marketTicker"])
    standardized = Counter()
    standard_tickers = set()
    for row in opportunity_rows:
        if row.get("canonicalMarketFamily") == "hitter_hits":
            standardized[utc_date(row.get("capturedAt"))] += 1
            if row.get("marketTicker"):
                standard_tickers.add(row["marketTicker"])
    days = sorted(set(raw_by_day) | set(raw_hit_by_day) | set(standardized))
    return {
        "schemaVersion": 1,
        "authority": "READ_ONLY_DIAGNOSTIC_NOT_HISTORICAL_DENOMINATOR_RECOVERY",
        "sourcePartitionDates": source_dates,
        "rawObservationRows": len(observations),
        "rawHitterHitRows": sum(raw_hit_by_day.values()),
        "rawHitterHitUniqueTickers": len(raw_hit_tickers),
        "standardizedHitterHitRows": sum(standardized.values()),
        "standardizedHitterHitUniqueTickers": len(standard_tickers),
        "rawFamilyClassification": dict(sorted(raw_family.items())),
        "rawHitterHitCheckpointLabels": dict(sorted(raw_checkpoint.items())),
        "byCaptureUTCDate": {day: {
            "rawAll": raw_by_day[day],
            "rawHitterHits": raw_hit_by_day[day],
            "standardizedHitterHits": standardized[day],
        } for day in days},
        "rawHitterTickersWithoutStandardizedCheckpoint": len(raw_hit_tickers - standard_tickers),
        "notes": [
            "Raw observations and standardized market/checkpoint rows are different denominators.",
            "Capture UTC date can differ from archive partition date; this does not reconstruct the August 20 report.",
            "No settlement, model evaluation, or historical forecast is inferred."
        ]
    }

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start-date", required=True)
    p.add_argument("--end-date", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    if args.start_date > args.end_date:
        p.error("start-date must not exceed end-date")
    dates = [d for d in _discover_dates() if args.start_date <= d <= args.end_date]
    if not dates:
        p.error("No observation partitions")
    obs, settlements, evals, recommendations, games, bets, _ = _load_universe(dates)
    rows = build_opportunity_rows(obs, settlements=settlements, evaluations=evals,
                                  recommendations=recommendations, games=games, bets=bets)
    report = summarize(obs, rows, dates)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")
    print(json.dumps({k: report[k] for k in (
        "rawObservationRows", "rawHitterHitRows", "standardizedHitterHitRows",
        "rawHitterTickersWithoutStandardizedCheckpoint")}, indent=2))

if __name__ == "__main__":
    main()
