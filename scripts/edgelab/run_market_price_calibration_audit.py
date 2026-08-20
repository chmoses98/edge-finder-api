#!/usr/bin/env python3
"""
scripts/edgelab/run_market_price_calibration_audit.py
============================================================
Retrospective MARKET-price calibration audit: is Kalshi's own executable
YES price, on its own, a calibrated probability of the contract settling
YES -- across the complete archived MLB market universe, independent of
whether our model ever had a probability for that contract and
independent of whether we ever bet it?

This is deliberately NOT a model-validation report (see
data/edgelab/reports/retrospective_validation_audit.md for that). It
reuses lib.edgelab.research_dataset's existing canonical
(marketTicker x researchCheckpoint) opportunity-row corpus and
lib.edgelab.research_reports' existing market_calibration /
market_family_research / research_stats helpers -- no new data-loading
or fee logic, only new aggregation cuts:

  1. A ONE-ROW-PER-CONTRACT "closing" dataset (isClosingQuote==True),
     used for every headline/family/price-bucket/date-partition table,
     so multiple snapshots of the same contract are never counted as
     independent outcomes (spec: "avoid treating multiple snapshots of
     the same contract as independent outcomes"). market_calibration()
     itself pools ALL researchCheckpoint rows per contract and is kept
     only as a supplementary, already-committed cross-check
     (data/edgelab/analytics/latest_research_market_calibration.json).
  2. researchCheckpoint-segmented calibration (FIRST_DAILY/T-90/60/30/
     CLOSING), each checkpoint already exactly one row per contract by
     the row schema's own construction -- this is the "snapshot timing"
     cut and needs no extra dedup.
  3. F3/F5/F7 outcome-label-preserving segmentation: inning_result rows
     are grouped by (marketHorizon, outcomeLabel) so a genuine "Win"
     contract is never averaged together with a genuine "Tie" contract
     just because both canonicalize to inning_result.
  4. A YES/NO orientation mirror: the same contract's NO side (its own
     executableNoPrice, not 1-YES) calibrated against the NO hit rate,
     to see whether the two sides of the same market are equally
     mispriced.
  5. Fee-aware simulated YES/NO ROI, taken verbatim from research_dataset's
     already-computed per-row hypothetical{Yes,No}Return[FeeOnly|
     RealisticExecution] fields (lib.edgelab.kalshi_fees under the hood)
     -- averaged exactly the way edge_backtest() already does
     (sum(returns)/len(returns)), never recomputed.
  6. DEV/VALIDATION/HOLDOUT date-partition stability of #1-#3, via
     lib.edgelab.research_splits.chronological_split (unchanged).

READ-ONLY: never writes to data/edgelab/observations/, settlements/,
model_evaluations/, recommendations/, games/, or bets/. Writes only to
data/edgelab/analytics/latest_market_price_calibration_audit.json and
data/edgelab/reports/market_price_calibration_audit.md.

Usage:
    python3 scripts/edgelab/run_market_price_calibration_audit.py
"""
import glob
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage
from lib.edgelab.research_dataset import build_opportunity_rows
from lib.edgelab.research_reports import market_calibration, market_family_research, _price_bucket_pct
from lib.edgelab.research_splits import DEVELOPMENT, HOLDOUT, VALIDATION, chronological_split, label_rows_with_split
from lib.edgelab.research_stats import brier_and_log_loss_summary, game_clustered_bootstrap_ci, independent_unit_count, sample_size_status, win_rate_value_fn

ANALYTICS_DIR = os.path.join("data", "edgelab", "analytics")
REPORTS_DIR = os.path.join("data", "edgelab", "reports")
SCHEMA_VERSION = "1"

FINE_BUCKET_WIDTH = 5
COARSE_BUCKET_WIDTH = 10
COARSE_BUCKET_THRESHOLD_N = 500  # below this settled-n, use 10c buckets instead of 5c


# ── Loading (mirrors scripts/edgelab/run_research_reports.py's _load_universe) ──

def _discover_dates():
    paths = glob.glob(storage.partition_path("observations", "*", compressed=True)) + glob.glob(storage.partition_path("observations", "*", compressed=False))
    return sorted({os.path.basename(p).split(".")[0] for p in paths})


def _load_universe(dates):
    observations, settlements, evaluations, recommendations, games = [], [], [], [], []
    for date in dates:
        observations.extend(storage.read_records(storage.partition_path("observations", date, compressed=True)))
        observations.extend(storage.read_records(storage.partition_path("observations", date, compressed=False)))
        settlements.extend(storage.read_records(storage.partition_path("settlements", date)))
        evaluations.extend(storage.read_records(storage.partition_path("model_evaluations", date)))
        recommendations.extend(storage.read_records(storage.partition_path("recommendations", date)))
        games.extend(storage.read_records(storage.partition_path("games", date)))
    bets = list(storage.read_records(storage.singleton_path("bets", "bets.jsonl")))
    return observations, settlements, evaluations, recommendations, games, bets


# ── Core dataset construction ────────────────────────────────────────────

def _settled_priced(rows):
    return [
        r for r in rows
        if r.get("settlementStatus") == "SETTLED"
        and r.get("settlementResult") in ("YES", "NO")
        and r.get("executableYesPrice") is not None
    ]


def closing_contract_rows(rows):
    """
    One row per contract: the settled+priced CLOSING-quote observation.
    This is the primary dataset for every headline/family/bucket/
    partition table in this report.

    MEASUREMENT-BUG WORKAROUND (documented in the report's "Measurement
    bugs found" section, NOT fixed in lib/edgelab/research_dataset.py
    itself -- scoped entirely to this audit's own dataset construction):
    for contracts where research_dataset never resolved a scheduledStart
    (minutesToStart is None on every row for that ticker -- ~46% of all
    isClosingQuote rows, concentrated in but not limited to hitter/
    pitcher-prop families), isClosingQuote can point at a snapshot
    captured well after first pitch (verified against raw observations:
    e.g. KXMLBHRR-26AUG141910SDCLE-CLESKWAN38-5's "closing" quote was
    captured 23:53Z the same day with yesBid=0/yesAsk=97 -- a one-sided,
    degenerate quote, not a genuine pregame market price) rather than a
    genuine last-pregame price. Restricting to minutesToStart is not
    None collapses the >=90c-price-bucket calibration error from -0.35..
    -0.48 (implausible for any functioning market) to -0.03..-0.06
    (an ordinary, plausible favorite-longshot-bias magnitude) and moves
    overall calibration error from -0.086 to -0.020 at n=35,907 -- see
    the report for the full before/after comparison. A real fix belongs
    in research_dataset.py's checkpoint/isClosingQuote classification
    (gate it on a resolved, non-negative minutesToStart) and is flagged
    as a follow-up; it is out of scope to change here since that
    function is shared by every other EdgeLab report.
    """
    return [r for r in _settled_priced(rows) if r.get("isClosingQuote") and r.get("minutesToStart") is not None]


def _bucket_width_for_n(n):
    return FINE_BUCKET_WIDTH if n >= COARSE_BUCKET_THRESHOLD_N else COARSE_BUCKET_WIDTH


# ── Coverage ──────────────────────────────────────────────────────────────

def coverage_summary(rows, closing_rows):
    all_tickers = {r["marketTicker"] for r in rows}
    settled = _settled_priced(rows)
    settled_tickers = {r["marketTicker"] for r in settled}
    closing_tickers = {r["marketTicker"] for r in closing_rows}
    all_closing = [r for r in settled if r.get("isClosingQuote")]
    excluded_no_start = [r for r in all_closing if r.get("minutesToStart") is None]
    return {
        "uniqueContractsObserved": len(all_tickers),
        "uniqueContractsSettled": len(settled_tickers),
        "uniqueContractsWithClosingQuote": len(closing_tickers),
        "settlementCoveragePct": round(len(settled_tickers) / len(all_tickers), 4) if all_tickers else None,
        "closingQuoteCoverageOfSettledPct": round(len(closing_tickers) / len(settled_tickers), 4) if settled_tickers else None,
        "totalOpportunityRows": len(rows),
        "settledPricedOpportunityRows": len(settled),
        "excludedForUnresolvedScheduledStart": {
            "n": len(excluded_no_start),
            "pctOfAllClosingQuoteRows": round(len(excluded_no_start) / len(all_closing), 4) if all_closing else None,
            "note": (
                "Settled+priced isClosingQuote rows dropped from closing_contract_rows() because "
                "minutesToStart was None (scheduledStart never resolved for that contract) -- see "
                "closing_contract_rows()'s docstring and this report's 'Measurement bugs found' section."
            ),
        },
        "note": (
            "uniqueContracts* counts distinct marketTicker values. settledPricedOpportunityRows counts "
            "(ticker x researchCheckpoint) snapshot rows, NOT independent contracts -- a single contract can "
            "appear at FIRST_DAILY, T-90/60/30, and CLOSING. Every calibration/price-bucket/family table below "
            "uses uniqueContractsWithClosingQuote (one row per contract) unless explicitly labeled 'by snapshot timing'."
        ),
    }


def measurement_bug_evidence(rows):
    """
    Reproducible before/after evidence for the isClosingQuote/
    minutesToStart measurement bug documented in closing_contract_rows().
    Computed independently of the corrected dataset so the comparison is
    self-contained and regenerable from source, not a one-off finding.
    """
    settled = _settled_priced(rows)
    all_closing = [r for r in settled if r.get("isClosingQuote")]
    uncorrected = all_closing
    corrected = [r for r in all_closing if r.get("minutesToStart") is not None]

    def _summary(bucket_rows):
        n = len(bucket_rows)
        if not n:
            return None
        avg_implied = sum(r["executableYesPrice"] for r in bucket_rows) / n
        actual_rate = sum(1 for r in bucket_rows if r["settlementResult"] == "YES") / n
        hi = [r for r in bucket_rows if r["executableYesPrice"] >= 0.90]
        hi_summary = None
        if hi:
            hi_avg = sum(r["executableYesPrice"] for r in hi) / len(hi)
            hi_hit = sum(1 for r in hi if r["settlementResult"] == "YES") / len(hi)
            hi_summary = {"n": len(hi), "avgImpliedProbability": round(hi_avg, 4), "actualHitRate": round(hi_hit, 4)}
        return {
            "n": n,
            "avgImpliedProbability": round(avg_implied, 4),
            "actualHitRate": round(actual_rate, 4),
            "calibrationError": round(actual_rate - avg_implied, 4),
            "priceAtOrAbove90Cents": hi_summary,
        }

    return {
        "beforeFix_allClosingQuoteRows": _summary(uncorrected),
        "afterFix_minutesToStartResolvedOnly": _summary(corrected),
        "exampleContract": {
            "marketTicker": "KXMLBHRR-26AUG141910SDCLE-CLESKWAN38-5",
            "issue": (
                "isClosingQuote snapshot captured 2026-08-14T23:53:18.874Z (yesBid=0.0, yesAsk=97.0, "
                "no two-sided noBid/noAsk quote present) for a game whose FIRST_DAILY snapshot the same "
                "morning priced this AT_LEAST-5-combined-hits+runs+RBI contract at yesAsk=8.0. scheduledStart "
                "never resolved for this ticker (minutesToStart is None on every row), so the pipeline's "
                "isClosingQuote selection had no pregame-validity signal to gate on and picked a late, "
                "one-sided, non-executable snapshot instead. Settlement itself was independently correct "
                "(MLB Stats API: Kwan recorded 1 hit + 1 run + 0 RBI = 2, correctly settled NO against "
                "threshold=5) -- the bug is in price/snapshot selection, not settlement."
            ),
        },
    }


# ── Generic calibration bucket helper (mirrors research_reports._stat_block's arithmetic) ──

def _calibration_row(key, bucket_rows, price_field="executableYesPrice", result_positive="YES"):
    n = len(bucket_rows)
    avg_implied = sum(r[price_field] for r in bucket_rows) / n
    actual_rate = sum(1 for r in bucket_rows if r["settlementResult"] == result_positive) / n
    pairs = [(r[price_field], 1 if r["settlementResult"] == result_positive else 0) for r in bucket_rows]
    brier, _ = brier_and_log_loss_summary(pairs)
    independent_games = independent_unit_count(bucket_rows, key="gameId")
    ci_lo, ci_hi, ci_method = game_clustered_bootstrap_ci(
        bucket_rows, win_rate_value_fn(lambda r: r["settlementResult"] == result_positive)
    )

    def _mean(field):
        vals = [r[field] for r in bucket_rows if r.get(field) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    yes_field_prefix = "hypotheticalYes" if price_field == "executableYesPrice" else "hypotheticalNo"
    return {
        "bucket": key,
        "n": n,
        "independentGames": independent_games,
        "avgImpliedProbability": round(avg_implied, 4),
        "actualHitRate": round(actual_rate, 4),
        "calibrationError": round(actual_rate - avg_implied, 4),
        "brierScore": brier,
        "confidenceInterval": {"low": ci_lo, "high": ci_hi, "method": ci_method, "level": 0.90},
        "sampleSize": sample_size_status(n, independent_games),
        "feeAwareSimulatedROI": {
            "grossROI": _mean(f"{yes_field_prefix}Return"),
            "roiAfterFeesOnly": _mean(f"{yes_field_prefix}ReturnFeeOnly"),
            "roiRealisticExecution": _mean(f"{yes_field_prefix}ReturnRealisticExecution"),
        },
    }


def _grouped(rows, key_fn):
    groups = defaultdict(list)
    for r in rows:
        k = key_fn(r)
        if k is not None:
            groups[k].append(r)
    return groups


def overall_and_price_bucket_calibration(closing_rows, side="YES"):
    price_field = "executableYesPrice" if side == "YES" else "executableNoPrice"
    result_positive = "YES" if side == "YES" else "NO"
    eligible = [r for r in closing_rows if r.get(price_field) is not None]
    overall = _calibration_row("ALL", eligible, price_field, result_positive) if eligible else None

    width = _bucket_width_for_n(len(eligible))
    buckets = _grouped(eligible, lambda r: _price_bucket_pct(r[price_field], width=width))
    by_bucket = sorted(
        (_calibration_row(k, v, price_field, result_positive) for k, v in buckets.items()),
        key=lambda r: str(r["bucket"]),
    )
    return {"overall": overall, "bucketWidthUsed": width, "byPriceBucket": by_bucket}


def family_calibration(closing_rows):
    groups = _grouped(closing_rows, lambda r: r.get("canonicalMarketFamily"))
    out = []
    for fam, fam_rows in groups.items():
        row = _calibration_row(fam, fam_rows)
        row["marketHorizons"] = sorted({r.get("marketHorizon") for r in fam_rows if r.get("marketHorizon")})
        out.append(row)
    return sorted(out, key=lambda r: -r["n"])


def inning_result_outcome_label_calibration(closing_rows):
    """
    Preserves exact tie-treatment semantics for F3/F5/F7 -- a genuine
    'Win' ticker (settles YES iff that team wins the period outright) is
    never pooled with a genuine 'Tie' ticker (settles YES iff the period
    ends tied) just because both canonicalize to inning_result.
    """
    inning = [r for r in closing_rows if r.get("canonicalMarketFamily") == "inning_result"]
    groups = _grouped(inning, lambda r: (r.get("marketHorizon"), r.get("outcomeLabel")))
    out = []
    for (horizon, label), grp in groups.items():
        row = _calibration_row(f"{horizon}:{label}", grp)
        row["marketHorizon"] = horizon
        row["outcomeLabel"] = label
        out.append(row)
    return sorted(out, key=lambda r: (r["marketHorizon"] or "", r["outcomeLabel"] or ""))


def threshold_family_calibration(closing_rows):
    """Reuses market_family_research verbatim on the closing-only dedup dataset, so its (family, horizon, threshold, comparisonOperator) grouping never mixes snapshots of the same contract."""
    return market_family_research(closing_rows)


def checkpoint_timing_calibration(rows):
    """
    Snapshot-timing cut: EACH researchCheckpoint bucket is already one row
    per contract by the row schema's own construction (canonical
    marketTicker x researchCheckpoint), so no extra dedup is needed here
    -- this is intentionally NOT run on closing_rows (that would collapse
    to only the CLOSING checkpoint).

    Applies the SAME minutesToStart-resolved gate as closing_contract_rows()
    (see its docstring) -- the isClosingQuote measurement bug is not
    specific to isClosingQuote itself, it is specific to any checkpoint
    whose snapshot-selection logic cannot verify pregame timing when
    scheduledStart never resolved. CLOSING is worst-affected (its
    "last observed price" selection has no ceiling on how late that
    price was captured) but FIRST_DAILY is not immune either.
    """
    settled = [r for r in _settled_priced(rows) if r.get("minutesToStart") is not None]
    groups = _grouped(settled, lambda r: r.get("researchCheckpoint"))
    return sorted((_calibration_row(k, v) for k, v in groups.items()), key=lambda r: -r["n"])


def date_partition_stability(closing_rows):
    dates = [r["gameDate"] for r in closing_rows if r.get("gameDate")]
    split_map = chronological_split(dates)
    labeled = label_rows_with_split(closing_rows, split_map)

    out = {"totalDates": split_map["totalDates"], "maturity": split_map["maturity"], "ratiosUsed": split_map["ratiosUsed"], "partitions": {}}
    for label in (DEVELOPMENT, VALIDATION, HOLDOUT):
        part_rows = [r for r in labeled if r.get("researchSplit") == label]
        overall = _calibration_row("ALL", part_rows) if part_rows else None
        fam_groups = _grouped(part_rows, lambda r: r.get("canonicalMarketFamily"))
        by_family = sorted((_calibration_row(k, v) for k, v in fam_groups.items()), key=lambda r: -r["n"])
        out["partitions"][label] = {
            "dateCount": len(split_map[label]),
            "dates": split_map[label],
            "overall": overall,
            "byFamilyTopN": by_family[:6],
        }
    return out


def main():
    dates = _discover_dates()
    if not dates:
        print("No observation dates found -- nothing to do.", file=sys.stderr)
        return 1

    observations, settlements, evaluations, recommendations, games, bets = _load_universe(dates)
    rows = build_opportunity_rows(observations, settlements=settlements, evaluations=evaluations, recommendations=recommendations, bets=bets, games=games)
    closing_rows = closing_contract_rows(rows)

    generated_at = ids.utc_now_iso()
    report = {
        "coverage": coverage_summary(rows, closing_rows),
        "measurementBugEvidence": measurement_bug_evidence(rows),
        "yesOrientationCalibration": overall_and_price_bucket_calibration(closing_rows, side="YES"),
        "noOrientationCalibration": overall_and_price_bucket_calibration(closing_rows, side="NO"),
        "byCanonicalMarketFamily": family_calibration(closing_rows),
        "inningResultByHorizonAndOutcomeLabel": inning_result_outcome_label_calibration(closing_rows),
        "byFamilyHorizonThreshold": threshold_family_calibration(closing_rows),
        "bySnapshotTiming": checkpoint_timing_calibration(rows),
        "datePartitionStability": date_partition_stability(closing_rows),
    }
    payload = {"schemaVersion": SCHEMA_VERSION, "generatedAt": generated_at, "datesAnalyzed": dates, "report": report}

    os.makedirs(ANALYTICS_DIR, exist_ok=True)
    out_path = os.path.join(ANALYTICS_DIR, "latest_market_price_calibration_audit.json")
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True, default=str)
        f.write("\n")

    print(f"Dates analyzed: {dates[0]} to {dates[-1]} ({len(dates)} dates)")
    print(f"Opportunity rows: {len(rows)}; closing-quote contract rows: {len(closing_rows)}")
    print(f"Written: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
