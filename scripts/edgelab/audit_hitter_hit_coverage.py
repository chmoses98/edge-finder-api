#!/usr/bin/env python3
"""Read-only exact-ticker reconciliation for archived MLB hitter-hit markets.

Counts both standardized market/checkpoint rows and independent tickers.
A recorded SETTLED result is an EdgeLab result, NOT independently
confirmed Kalshi exchange settlement. No forecast is invented or backfilled.

Usage:
  python3 scripts/edgelab/audit_hitter_hit_coverage.py \
      --start-date 2026-08-01 --end-date 2026-08-20 \
      --out artifacts/hitter_hit_coverage.json
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.research_dataset import build_opportunity_rows
from scripts.edgelab.run_research_reports import _discover_dates, _load_universe

FAMILY = "hitter_hits"
TERMINAL_RESULT = {"YES", "NO"}


def reconcile_hitter_rows(opportunity_rows, dates=None, generated_at=None):
    """Returns a complete observed-market manifest; never claims independent result truth.

    Each source row is an (exact ticker, standardized market checkpoint) observation,
    not a unique bet or a unique contract. An archived outcome is repeated across
    its checkpoint rows; count unique tickers separately.
    """
    hits = [r for r in opportunity_rows if r.get("canonicalMarketFamily") == FAMILY]
    by_ticker = defaultdict(list)
    for r in hits:
        ticker = r.get("marketTicker")
        if not isinstance(ticker, str) or not ticker.strip():
            raise ValueError("Hitter-hit checkpoint missing exact marketTicker")
        by_ticker[ticker].append(r)

    status_rows, status_tickers = Counter(), Counter()
    result_tickers = Counter()
    missing = []
    model_rows = model_tickers = 0
    for ticker, rows in sorted(by_ticker.items()):
        statuses = {r.get("settlementStatus") or "NOT_SETTLED" for r in rows}
        results = {r.get("settlementResult") for r in rows}
        if len(statuses) != 1 or len(results) != 1:
            raise ValueError("Contradictory per-ticker archived settlement: " + ticker)
        status = next(iter(statuses))
        result = next(iter(results))
        if status == "SETTLED" and result not in TERMINAL_RESULT:
            raise ValueError("Recorded SETTLED ticker missing YES/NO: " + ticker)
        if status != "SETTLED" and result is not None:
            raise ValueError("Non-settled ticker has asserted result: " + ticker)
        status_tickers[status] += 1
        status_rows[status] += len(rows)
        if status == "SETTLED":
            result_tickers[result] += 1
        linked = sum(bool(r.get("modelEvaluationAvailable")) for r in rows)
        model_rows += linked
        model_tickers += bool(linked)

        if status != "SETTLED":
            reasons = sorted({str(r.get("settlementUnavailableReason"))
                              for r in rows if r.get("settlementUnavailableReason")})
            game_ids = sorted({str(r.get("gameId")) for r in rows if r.get("gameId") is not None})
            missing.append({
                "marketTicker": ticker,
                "gameIds": game_ids,
                "checkpointRows": len(rows),
                "settlementStatus": status,
                "unavailableReasons": reasons,
                "modelLinkedCheckpointRows": linked,
                "observedAtFirst": min((r["capturedAt"] for r in rows if r.get("capturedAt")), default=None),
                "observedAtLast": max((r["capturedAt"] for r in rows if r.get("capturedAt")), default=None),
            })

    n = len(hits)
    if sum(status_rows.values()) != n or sum(status_tickers.values()) != len(by_ticker):
        raise AssertionError("Hitter-hit denominator drift")
    return {
        "schemaVersion": 1,
        "authority": "READ_ONLY_RESEARCH_DIAGNOSTIC_NO_KALSHI_SETTLEMENT_VERIFICATION",
        "family": FAMILY,
        "generatedAt": generated_at or datetime.now(timezone.utc).isoformat(),
        "sourceObservationDates": dates or [],
        "source": "canonical EdgeLab observations + settlements + ModelEvaluation partitions",
        "checkpointRows": n,
        "uniqueMarketTickers": len(by_ticker),
        "recordedSettledCheckpointRows": status_rows["SETTLED"],
        "recordedSettledTickers": status_tickers["SETTLED"],
        "modelLinkedCheckpointRows": model_rows,
        "modelLinkedTickers": model_tickers,
        "statusByCheckpointRow": dict(sorted(status_rows.items())),
        "statusByUniqueTicker": dict(sorted(status_tickers.items())),
        "recordedResultByUniqueTicker": dict(sorted(result_tickers.items())),
        "notRecordedAsSettledTickers": missing,
        "notes": [
            "NOT_SETTLED may mean no archived settlement row, including games not final at the report cutoff.",
            "SETTLEMENT_UNRESOLVED means a settlement attempt could not verify the outcome.",
            "VOID is a classified terminal disposition, not a YES/NO winner; it is included in the non-SETTLED manifest.",
            "A YES/NO derived from MLB box score is not independently verified against the official Kalshi contract result.",
            "ModelEvaluation means a historical nonfuture ticker/checkpoint join, not that the model won or a bet existed.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.end_date < args.start_date:
        parser.error("end-date must not precede start-date")

    dates = [d for d in _discover_dates() if args.start_date <= d <= args.end_date]
    if not dates:
        parser.error("No archived observation partitions found for requested dates")
    observations, settlements, evaluations, recommendations, games, bets, _ = _load_universe(dates)
    rows = build_opportunity_rows(
        observations, settlements=settlements, evaluations=evaluations,
        recommendations=recommendations, bets=bets, games=games,
    )
    report = reconcile_hitter_rows(rows, dates=dates)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, sort_keys=True, indent=2)
        f.write("\n")
    print(json.dumps({k: report[k] for k in (
        "checkpointRows", "uniqueMarketTickers", "recordedSettledCheckpointRows",
        "recordedSettledTickers", "modelLinkedCheckpointRows", "modelLinkedTickers",
        "statusByCheckpointRow", "statusByUniqueTicker")}, indent=2))
    print("Exact non-SETTLED ticker manifest written:", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
