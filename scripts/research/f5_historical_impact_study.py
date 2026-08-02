#!/usr/bin/env python3
"""
scripts/research/f5_historical_impact_study.py
====================================================
F5 Three-Way Pricing Correction milestone (item 8): research-only
comparison of the legacy two-way-renormalized F5 model probability
against the corrected three-way probability, over every available
historical F5 evaluation this repository has preserved enough raw input
to reproduce.

REAL DATA LIMITATION (confirmed, not assumed): `data/pipeline/<date>/`
pipeline-stage artifacts -- the only place F5AwayProj/F5HomeProj (the raw
Poisson run-projection inputs) are preserved -- exist for only 3 dates
(2026-07-30, 2026-07-31, 2026-08-01) at the time this milestone was
built. `bets.json` has 144 historical F5 bets going back to 2026-05-26,
but only carries the FINAL (already-renormalized) modelPct for each,
never the raw run projections -- so the corrected model probability
CANNOT be reproduced for the other ~130+ older bets without fabricating
projection inputs that were never recorded. This script only reports on
the reproducible subset and says so explicitly, per this milestone's
"do not fabricate corrected historical values from incomplete artifacts"
constraint.

A SECOND limitation, also confirmed: the real Kalshi TIE contract's own
American odds were never captured in any of these preserved artifacts
(production discarded them before this milestone's fix). This means the
MARKET-implied side of the correction (which needs the tie price for a
true 3-way vig-free split) cannot be reproduced historically either --
only the MODEL-side correction (which needs only the run projections)
is reproducible. Any edge/tier re-derivation below explicitly reuses the
historical (2-way) kalshiVF as an approximation and is labeled as such.

Research-only: writes a JSON report, never modifies bets.json or any
production file.
"""
import json
import os
import sys
from collections import Counter

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from lib.research.three_way_projection import three_way_result_probs  # noqa: E402

PIPELINE_ROOT = os.path.join(ROOT_DIR, "data", "pipeline")
BETS_PATH = os.path.join(ROOT_DIR, "bets.json")

# The existing production calibration threshold this milestone must not
# alter (scripts/build_market_ledger.py's CAL_MEDIUM) -- used only to
# label findings "clears the existing calibration threshold" vs purely
# descriptive, never to imply a NEW threshold.
CAL_MEDIUM = 0.255
THRESHOLD_HIGH = 3.0
THRESHOLD_MEDIUM = 1.5
THRESHOLD_PAPER = 1.0


def _confidence_tier(edge_pct, f5_amplified=False):
    if edge_pct is None:
        return None
    floor = THRESHOLD_PAPER if not f5_amplified else 1.0
    if edge_pct < floor:
        return None
    if edge_pct >= THRESHOLD_HIGH:
        return "HIGH"
    if edge_pct >= THRESHOLD_MEDIUM:
        return "MEDIUM"
    return "PAPER"


def _legacy_renormalized(p_win, p_tie):
    denom = 1 - p_tie
    return p_win / denom if denom > 0 else p_win


def _available_pipeline_dates():
    if not os.path.isdir(PIPELINE_ROOT):
        return []
    return sorted(
        d for d in os.listdir(PIPELINE_ROOT)
        if os.path.isfile(os.path.join(PIPELINE_ROOT, d, "recommendations.json"))
    )


def _f5_rows_with_projections(date):
    path = os.path.join(PIPELINE_ROOT, date, "recommendations.json")
    with open(path) as f:
        artifact = json.load(f)
    games = (artifact.get("data") or {}).get("games") or artifact.get("games") or []
    rows = []
    for g in games:
        for row in g.get("marketLedger") or []:
            if row.get("market") in ("F5_ML_Away", "F5_ML_Home") and row.get("f5AwayProj") is not None:
                rows.append({
                    "date": date,
                    "market": row["market"],
                    "f5AwayProj": row["f5AwayProj"],
                    "f5HomeProj": row["f5HomeProj"],
                    "legacyModelProb": row.get("modelProb"),
                    "kalshiVF": row.get("kalshiVF"),
                    "status": row.get("status"),
                    "confidenceTier": row.get("confidenceTier"),
                })
    return rows


def build_report():
    dates = _available_pipeline_dates()
    all_rows = []
    for date in dates:
        all_rows.extend(_f5_rows_with_projections(date))

    inflation_pp = []  # percentage points, legacy - corrected, per row
    tie_probs = []
    tier_changes = 0
    would_disappear = 0  # was tier-eligible (non-None), corrected tier is None
    would_newly_appear = 0  # was tier-None, corrected tier is non-None
    detail = []

    for row in all_rows:
        r = three_way_result_probs(row["f5AwayProj"], row["f5HomeProj"], max_runs=20)
        is_away = row["market"] == "F5_ML_Away"
        p_corrected = r["awayWinProb"] if is_away else r["homeWinProb"]
        p_legacy_recomputed = _legacy_renormalized(p_corrected, r["tieProb"])
        tie_probs.append(r["tieProb"] * 100)
        inflation_pp.append((p_legacy_recomputed - p_corrected) * 100)

        # Re-derive edge/tier using the historical (2-way) kalshiVF as an
        # APPROXIMATION -- the true market-side correction needs the real
        # historical tie price, which was never captured (see module
        # docstring). Labeled explicitly in the report, not silently used
        # as if it were the full correction.
        kalshi_vf_pct = row.get("kalshiVF")
        legacy_tier = row.get("confidenceTier")
        corrected_tier = None
        if kalshi_vf_pct is not None:
            raw_edge_pct = (p_corrected * 100) - kalshi_vf_pct
            calibrated_edge_pct = round(raw_edge_pct * CAL_MEDIUM, 3)
            corrected_tier = _confidence_tier(calibrated_edge_pct)

        if legacy_tier != corrected_tier:
            tier_changes += 1
            if legacy_tier is not None and corrected_tier is None:
                would_disappear += 1
            elif legacy_tier is None and corrected_tier is not None:
                would_newly_appear += 1

        detail.append({
            "date": row["date"],
            "market": row["market"],
            "f5AwayProj": row["f5AwayProj"],
            "f5HomeProj": row["f5HomeProj"],
            "correctedModelProbability": round(p_corrected * 100, 3),
            "legacyRenormalizedModelProbability_recomputed": round(p_legacy_recomputed * 100, 3),
            "legacyModelProbability_asRecorded": row["legacyModelProb"],
            "tieProbability": round(r["tieProb"] * 100, 3),
            "inflationPercentagePoints": round((p_legacy_recomputed - p_corrected) * 100, 3),
            "historicalKalshiVF_2way_approximation": kalshi_vf_pct,
            "legacyConfidenceTier_asRecorded": legacy_tier,
            "approximateCorrectedConfidenceTier": corrected_tier,
            "tierChanged": legacy_tier != corrected_tier,
        })

    # bets.json cross-reference: how much of the REAL historical F5 bet
    # history falls inside vs outside the reproducible-projections window.
    with open(BETS_PATH) as f:
        bets = json.load(f)
    f5_bets = [b for b in bets if "F5" in str(b.get("market", ""))]
    f5_bet_dates = sorted({b.get("date") for b in f5_bets if b.get("date")})
    reproducible_dates = set(dates)
    f5_bets_in_reproducible_window = sum(1 for b in f5_bets if b.get("date") in reproducible_dates)
    f5_bets_not_reproducible = len(f5_bets) - f5_bets_in_reproducible_window

    # Settlement/CLV data availability for the reproducible-window bets.
    settled_in_window = [
        b for b in f5_bets
        if b.get("date") in reproducible_dates and (b.get("result") or b.get("status")) in ("WIN", "LOSS", "PUSH")
    ]
    clv_available_in_window = [b for b in settled_in_window if b.get("clv") is not None]

    n = len(detail)
    avg_inflation = sum(inflation_pp) / n if n else None
    avg_tie = sum(tie_probs) / n if n else None

    return {
        "schemaVersion": "1",
        "note": (
            "RESEARCH-ONLY. Descriptive findings unless explicitly labeled as "
            "clearing the existing production calibration threshold "
            "(CAL_MEDIUM=0.255). Does not modify bets.json or any production "
            "file. See module docstring for the two confirmed data-availability "
            "limitations (projection-input window, missing historical tie price)."
        ),
        "reproduciblePipelineDates": dates,
        "numberOfF5MarketsEvaluated": n,
        "averageOldTeamSideProbabilityInflationPercentagePoints": (
            round(avg_inflation, 3) if avg_inflation is not None else None
        ),
        "averageTieProbabilityPercent": round(avg_tie, 3) if avg_tie is not None else None,
        "approximateTierChanges": {
            "totalRowsWithTierChange": tier_changes,
            "wouldDisappear_tierEligibleToNone": would_disappear,
            "wouldNewlyAppear_noneToTierEligible": would_newly_appear,
            "caveat": (
                "Computed using the historical (2-way) kalshiVF as an approximation "
                "for the market side -- the true historical tie price was never "
                "captured, so this is NOT a full both-sides-corrected re-evaluation. "
                "Descriptive only; does not clear the calibration threshold on its own."
            ),
        },
        "placedBetsCrossReference": {
            "totalF5PlacedBets": len(f5_bets),
            "f5BetDateRange": [f5_bet_dates[0], f5_bet_dates[-1]] if f5_bet_dates else None,
            "f5BetsWithinReproducibleProjectionWindow": f5_bets_in_reproducible_window,
            "f5BetsNotReproducible_noPreservedProjectionInputs": f5_bets_not_reproducible,
        },
        "settlementAndClvDataAvailability": {
            "settledF5BetsInReproducibleWindow": len(settled_in_window),
            "ofThoseWithClvCaptured": len(clv_available_in_window),
            "sampleSizeStatus": (
                "INSUFFICIENT for any ROI/CLV comparison -- fewer than 10 settled "
                "bets fall inside the 3-date reproducible-projection window; no "
                "hypothetical ROI/CLV difference is reported (would not be "
                "statistically meaningful, and this milestone's own calibration "
                "convention requires N>=50 per tier before drawing a conclusion)."
                if len(settled_in_window) < 50 else
                "Sufficient sample size for a hypothetical ROI/CLV comparison."
            ),
        },
        "detail": detail,
    }


def main():
    report = build_report()
    print(json.dumps({k: v for k, v in report.items() if k != "detail"}, indent=2))
    out_path = os.path.join(ROOT_DIR, "data", "research", "f5_historical_impact_study.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nFull report (including per-row detail) written to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
