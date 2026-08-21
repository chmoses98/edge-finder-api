#!/usr/bin/env python3
"""
scripts/edgelab/run_research_reports.py
============================================
EdgeLab Research Trustworthiness milestone: CLI entry point that builds
the canonical (marketTicker x checkpoint) opportunity dataset
(lib.edgelab.research_dataset) over the full committed historical
corpus and runs every report in lib.edgelab.research_reports against
it, writing machine-readable JSON under data/edgelab/analytics/ (the
existing "latest_<report>.json" convention -- see
scripts/edgelab/run_calibration.py et al.) plus one concise
human-readable summary under data/edgelab/reports/.

READ-ONLY: this script never writes to data/edgelab/observations/,
model_evaluations/, settlements/, recommendations/, games/, or bets/ --
only to data/edgelab/analytics/ and data/edgelab/reports/, exactly like
every other run_*.py report script in this directory. Nothing here
feeds back into production betting/recommendation logic.

Usage:
    python3 scripts/edgelab/run_research_reports.py
    python3 scripts/edgelab/run_research_reports.py --start-date 2026-08-01 --end-date 2026-08-13
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage
from lib.edgelab.research_dataset import build_opportunity_rows
from lib.edgelab.research_reports import (
    checkpoint_research,
    edge_backtest,
    ladder_research,
    market_calibration,
    market_family_research,
    market_price_staleness_report,
    model_calibration,
    render_summary_markdown,
    research_data_quality,
    snapshot_coverage_report,
    strategy_validation,
)

ANALYTICS_DIR = os.path.join("data", "edgelab", "analytics")
REPORTS_DIR = os.path.join("data", "edgelab", "reports")

SCHEMA_VERSION = "1"


def _discover_dates():
    """Every date with a committed observations partition -- the source of truth for 'what dates does this corpus actually cover', not a hardcoded range."""
    paths = glob.glob(storage.partition_path("observations", "*", compressed=True)) + glob.glob(storage.partition_path("observations", "*", compressed=False))
    dates = sorted({os.path.basename(p).split(".")[0] for p in paths})
    return dates


def _load_universe(dates):
    observations, settlements, evaluations, recommendations, games, research_runs = [], [], [], [], [], []
    for date in dates:
        observations.extend(storage.read_records(storage.partition_path("observations", date, compressed=True)))
        observations.extend(storage.read_records(storage.partition_path("observations", date, compressed=False)))
        settlements.extend(storage.read_partition("settlements", date))
        evaluations.extend(storage.read_partition("model_evaluations", date))
        recommendations.extend(storage.read_partition("recommendations", date))
        games.extend(storage.read_records(storage.partition_path("games", date)))
        research_runs.extend(storage.read_records(storage.partition_path("research_runs", date)))
    bets = list(storage.read_records(storage.singleton_path("bets", "bets.jsonl")))
    return observations, settlements, evaluations, recommendations, games, bets, research_runs


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True, default=str)
        f.write("\n")


def build_reports(rows, observations, settlements, evaluations, games=None, research_runs=None):
    generated_at = ids.utc_now_iso()
    data_quality = research_data_quality(rows, observations=observations, settlements=settlements, evaluations=evaluations)
    m_cal = market_calibration(rows)
    mod_cal = model_calibration(rows)
    eb = edge_backtest(rows)
    fam = market_family_research(rows)
    ckpt = checkpoint_research(rows)
    ladders = ladder_research(rows)
    strategy = strategy_validation(rows)
    snapshot_coverage = snapshot_coverage_report(rows, evaluations, games=games, research_runs=research_runs)
    price_staleness = market_price_staleness_report(rows)

    def _wrap(payload):
        return {"schemaVersion": SCHEMA_VERSION, "generatedAt": generated_at, "rowCount": len(rows), "report": payload}

    return {
        "research_data_quality": _wrap(data_quality),
        "market_calibration": _wrap(m_cal),
        "model_calibration": _wrap(mod_cal),
        "edge_backtest": _wrap(eb),
        "market_family_research": _wrap(fam),
        "checkpoint_research": _wrap(ckpt),
        "ladder_research": _wrap(ladders),
        "strategy_validation": _wrap(strategy),
        "snapshot_coverage": _wrap(snapshot_coverage),
        "market_price_staleness": _wrap(price_staleness),
    }, data_quality, m_cal, mod_cal, eb, strategy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default=None, help="Restrict to dates >= this (default: every committed date)")
    parser.add_argument("--end-date", default=None, help="Restrict to dates <= this (default: every committed date)")
    args = parser.parse_args()

    dates = _discover_dates()
    if args.start_date:
        dates = [d for d in dates if d >= args.start_date]
    if args.end_date:
        dates = [d for d in dates if d <= args.end_date]

    if not dates:
        print("No observation dates found for the requested range -- nothing to do.", file=sys.stderr)
        return 1

    observations, settlements, evaluations, recommendations, games, bets, research_runs = _load_universe(dates)
    rows = build_opportunity_rows(
        observations, settlements=settlements, evaluations=evaluations,
        recommendations=recommendations, bets=bets, games=games,
    )

    reports, data_quality, m_cal, mod_cal, eb, strategy = build_reports(
        rows, observations, settlements, evaluations, games=games, research_runs=research_runs,
    )

    for name, payload in reports.items():
        _write_json(os.path.join(ANALYTICS_DIR, f"latest_research_{name}.json"), payload)

    summary_md = render_summary_markdown(data_quality, m_cal, mod_cal, eb, strategy)
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(os.path.join(REPORTS_DIR, "research_trustworthiness_summary.md"), "w") as f:
        f.write(summary_md + "\n")

    print(f"Dates analyzed: {dates[0]} to {dates[-1]} ({len(dates)} dates)")
    print(f"Opportunity rows: {len(rows)}")
    print(f"Unique games: {data_quality['uniqueGames']}")
    print(f"Unique market tickers: {data_quality['uniqueMarketTickers']}")
    print(f"Reports written to {ANALYTICS_DIR}/latest_research_*.json")
    print(f"Summary written to {REPORTS_DIR}/research_trustworthiness_summary.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
