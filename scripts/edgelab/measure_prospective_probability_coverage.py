#!/usr/bin/env python3
"""
scripts/edgelab/measure_prospective_probability_coverage.py
================================================================
Universal ModelEvaluation Persistence mission: read-only coverage
report. For one or more dates, measures:

  - active archived contracts: distinct marketTicker seen in that
    date's MarketObservation partition (data/edgelab/observations/).
  - legitimately modelable contracts: the subset of those tickers this
    repository's own discovery pipeline (scripts/discover_kalshi_mlb_markets.py,
    data/kalshi/discovery/<date>.json) classified modelSupportStatus
    SUPPORTED, unioned with tickers covered by a pipeline-derived (11-
    REQUIRED_MARKETS) ModelEvaluation row carrying a non-null
    modelFairProbability -- i.e. every ticker for which SOME already-
    verified projection method exists, regardless of which code path
    computed it.
  - contracts receiving ModelEvaluation.modelFairProbability: the count
    of that date's ModelEvaluation rows with a non-null
    modelFairProbability, deduplicated by marketTicker (the persistence
    layer's own upsert/append keying may write more than one row per
    ticker across checkpoints -- this counts distinct TICKERS covered,
    not raw row count).
  - coverage % overall and by canonical market family.
  - explicit reasons for the remaining gap (families never modeled at
    all -- e.g. hitter props -- vs. families modeled but not yet
    persisted -- the actual target of this mission).

Never writes anything. Pure read + report.

Usage:
    python3 scripts/edgelab/measure_prospective_probability_coverage.py --date 2026-08-20
    python3 scripts/edgelab/measure_prospective_probability_coverage.py --date 2026-08-20 --json
"""
import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage
from lib.edgelab.kalshi_discovery_bridge import load_discovery_lookup
from lib.edgelab.market_family_mapping import canonicalize_market_family
from lib.edgelab.model_evaluation import extend_full_universe_evaluations
from lib.edgelab.recommendations import load_model_covered_series

DISCOVERY_DIR = os.path.join("data", "kalshi", "discovery")


def _load_discovery(date):
    path = os.path.join(DISCOVERY_DIR, f"{date}.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        doc = json.load(f)
    return {c["ticker"]: c for c in doc.get("contracts", []) if c.get("ticker")}


def _load_observations(date):
    obs = list(storage.read_records(storage.partition_path("observations", date, compressed=True)))
    obs += list(storage.read_records(storage.partition_path("observations", date, compressed=False)))
    return obs


def _load_evaluations(date):
    return list(storage.read_records(storage.partition_path("model_evaluations", date)))


def _simulate_after_evaluations(date, observations, evaluations):
    """
    In-memory only -- never writes any file. Runs the REAL (post-mission)
    extend_full_universe_evaluations() with a real discovery_lookup
    against the already-committed observations/evaluations for `date`,
    and returns evaluations + the new extension rows it would produce.
    This is exactly what scripts/edgelab/build_recommendations.py's live
    call site now does; the only difference is this never persists the
    result, so measuring "after" coverage never mutates committed data.
    """
    covered_tickers = {e["marketTicker"] for e in evaluations if e.get("marketTicker")}
    discovery_lookup = load_discovery_lookup(date)
    model_covered_series = load_model_covered_series()
    extension = extend_full_universe_evaluations(
        covered_tickers, observations, date, model_covered_series, discovery_lookup=discovery_lookup,
    )
    return evaluations + extension


def measure_date(date, simulate_after=False):
    observations = _load_observations(date)
    evaluations = _load_evaluations(date)
    discovery = _load_discovery(date)
    if simulate_after:
        evaluations = _simulate_after_evaluations(date, observations, evaluations)

    active_by_ticker = {}
    for o in observations:
        ticker = o.get("marketTicker")
        if not ticker:
            continue
        family = canonicalize_market_family(o.get("marketFamily")) or o.get("marketFamily") or "UNKNOWN"
        active_by_ticker[ticker] = family

    # A ticker is "legitimately modelable" if EITHER the discovery
    # pipeline priced it (modelSupportStatus SUPPORTED) OR a
    # ModelEvaluation row for it carries a real probability already
    # (covers the 11-REQUIRED_MARKETS pipeline/prospective-snapshot
    # path, which discovery does not re-derive).
    modelable = set()
    for ticker, c in discovery.items():
        if c.get("modelSupportStatus") == "SUPPORTED":
            modelable.add(ticker)

    covered_by_ticker = {}
    for e in evaluations:
        ticker = e.get("marketTicker")
        if not ticker:
            continue
        if e.get("modelFairProbability") is not None:
            covered_by_ticker[ticker] = True
            modelable.add(ticker)

    active_tickers = set(active_by_ticker)
    modelable_tickers = modelable & (active_tickers | set(covered_by_ticker))
    covered_tickers = set(covered_by_ticker) & active_tickers

    by_family = defaultdict(lambda: {"active": 0, "modelable": 0, "covered": 0})
    for ticker, family in active_by_ticker.items():
        by_family[family]["active"] += 1
        if ticker in modelable_tickers:
            by_family[family]["modelable"] += 1
        if ticker in covered_tickers:
            by_family[family]["covered"] += 1

    gap_reasons = defaultdict(int)
    for ticker in active_tickers - covered_tickers:
        family = active_by_ticker[ticker]
        c = discovery.get(ticker)
        if ticker in modelable_tickers:
            gap_reasons[f"{family}: modelable but not yet persisted"] += 1
        elif c is not None and c.get("modelSupportStatus") == "UNSUPPORTED":
            gap_reasons[f"{family}: {c.get('unsupportedReason') or 'unsupported'}"] += 1
        elif c is not None and c.get("modelSupportStatus") == "MISSING_DATA":
            gap_reasons[f"{family}: missing data ({c.get('unsupportedReason') or 'unspecified'})"] += 1
        elif c is None:
            gap_reasons[f"{family}: not present in discovery output for this date"] += 1
        else:
            gap_reasons[f"{family}: unclassified/other"] += 1

    def pct(n, d):
        return round(100.0 * n / d, 2) if d else None

    # Cross-check, ticker-identity-agnostic: how many ModelEvaluation
    # rows this date carry a real probability at all, regardless of
    # whether marketTicker joins cleanly to an observation/discovery
    # row. A pre-existing, out-of-scope gap (rejected_row() for
    # ML_Away/ML_Home/F5_ML_Away/F5_ML_Home does not always thread the
    # real Kalshi ticker through -- see lib.edgelab.model_evaluation.
    # classify_evaluation_status's own docstring) means some of these
    # rows fall back to a synthetic "gameId:marketName" ticker, which
    # can never join to a discovery/observation row by ticker. This
    # does NOT mean no probability was persisted -- it means this
    # report's ticker-joined coverage numbers understate coverage for
    # families affected by that separate, pre-existing gap. Reported
    # here so the by-family numbers below are never misread as "not
    # persisted" when they were, just under a non-joinable ticker.
    evaluated_with_prob_by_selection = defaultdict(int)
    for e in evaluations:
        if e.get("modelFairProbability") is not None:
            evaluated_with_prob_by_selection[e.get("selection") or e.get("marketFamily") or "UNKNOWN"] += 1

    result = {
        "date": date,
        "activeContracts": len(active_tickers),
        "modelableContracts": len(modelable_tickers),
        "coveredContracts": len(covered_tickers),
        "coveragePctOfActive": pct(len(covered_tickers), len(active_tickers)),
        "coveragePctOfModelable": pct(len(covered_tickers), len(modelable_tickers)),
        "evaluationRowsWithProbabilityBySelectionTickerIdentityAgnostic": dict(
            sorted(evaluated_with_prob_by_selection.items(), key=lambda kv: -kv[1])
        ),
        "byFamily": {
            fam: {
                **v,
                "coveragePctOfActive": pct(v["covered"], v["active"]),
                "coveragePctOfModelable": pct(v["covered"], v["modelable"]),
            }
            for fam, v in sorted(by_family.items(), key=lambda kv: -kv[1]["active"])
        },
        "remainingGapReasons": dict(sorted(gap_reasons.items(), key=lambda kv: -kv[1])),
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", action="append", required=True, help="Date(s) YYYY-MM-DD, repeatable")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--simulate-after", action="store_true",
                         help="In-memory only (never writes a file): report coverage as if "
                              "extend_full_universe_evaluations() had already run with a real "
                              "discovery_lookup for this date, on top of whatever ModelEvaluation "
                              "rows already exist. For a before/after comparison against the same "
                              "committed data without needing to actually run the pipeline twice.")
    args = parser.parse_args()

    results = [measure_date(d, simulate_after=args.simulate_after) for d in args.date]
    if args.json:
        print(json.dumps(results, indent=2))
        return 0

    for r in results:
        print(f"=== {r['date']} ===")
        print(f"Active contracts:     {r['activeContracts']}")
        print(f"Modelable contracts:  {r['modelableContracts']}")
        print(f"Covered contracts:    {r['coveredContracts']}")
        print(f"Coverage % of active:    {r['coveragePctOfActive']}")
        print(f"Coverage % of modelable: {r['coveragePctOfModelable']}")
        print("By family:")
        for fam, v in r["byFamily"].items():
            print(f"  {fam:20s} active={v['active']:5d} modelable={v['modelable']:5d} covered={v['covered']:5d} "
                  f"cov%active={v['coveragePctOfActive']} cov%modelable={v['coveragePctOfModelable']}")
        print("Remaining gap reasons:")
        for reason, n in r["remainingGapReasons"].items():
            print(f"  {n:5d}  {reason}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
