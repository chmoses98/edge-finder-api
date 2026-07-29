#!/usr/bin/env python3
"""
scripts/research/build_inning_result_shadow_comparison.py
===============================================================
Model Performance Phase 2A, Part 11 -- builds
data/research/inning_result_shadow_comparison.json: for F5 Away/Home,
compares legacy conditional probability vs. canonical unconditional
(three-way) probability vs. Kalshi executable price, per-side edge
under each, and whether a real-money recommendation would change if
canonical probabilities were used instead of legacy.

Reads data/research/inning_result_shadow_ledger.json (built by
scripts/research/build_inning_result_shadow_ledger.py) -- this script
performs NO live network call and does not read data/slate.json,
bets.json, or any real production recommendation artifact.

`_confidence_from_edge()` below is a BYTE-FOR-BYTE REPLICA of
scripts/build_market_ledger.py's confidence_from_edge() (same
THRESHOLD_HIGH/MEDIUM/PAPER constants), NOT imported from it, so this
research script has zero runtime coupling to production -- same
honest-replica technique used in
scripts/research/generate_projection_outcome_comparison.py (Model
Performance Phase 1).

This artifact is RESEARCH-ONLY and must never feed production
execution, never overwrite marketLedger, and never influence real
bet sizing, calibration, or eligibility.
"""
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

LEDGER_PATH = os.path.join(ROOT, "data", "research", "inning_result_shadow_ledger.json")
OUTPUT_PATH = os.path.join(ROOT, "data", "research", "inning_result_shadow_comparison.json")

THRESHOLD_HIGH = 3.0
THRESHOLD_MEDIUM = 1.5
THRESHOLD_PAPER = 1.0


def _confidence_from_edge(edge_pct):
    """Byte-for-byte replica of build_market_ledger.py's confidence_from_edge()
    for the non-F5-amplified case (F5 team-leg edges use the standard
    threshold in production -- f5_amplified only applies to a different,
    unrelated code path this comparison does not need to reproduce)."""
    if edge_pct is None:
        return None
    if edge_pct < THRESHOLD_PAPER:
        return None
    if edge_pct >= THRESHOLD_HIGH:
        return "HIGH"
    if edge_pct >= THRESHOLD_MEDIUM:
        return "MEDIUM"
    return "PAPER"


def compare_row(row):
    """Pure. Builds one Part 11 comparison row from one F5 Away/Home
    shadow-ledger row. Returns None if the row lacks the data needed
    for a meaningful comparison (e.g. no projection proxy available)."""
    legacy = row.get("legacyConditionalProb")
    canonical = row.get("canonicalModelProb")
    executable_price = row.get("yesAsk")
    if legacy is None or canonical is None or executable_price is None:
        return None

    difference_pct_points = round((canonical - legacy) * 100, 3)
    legacy_edge = round((legacy - executable_price) * 100, 3)
    canonical_edge = round((canonical - executable_price) * 100, 3)
    legacy_status = _confidence_from_edge(legacy_edge)
    canonical_status = _confidence_from_edge(canonical_edge)

    return {
        "date": row["date"],
        "gameId": row["gameId"],
        "scope": row["scope"],
        "outcome": row["outcome"],
        "ticker": row["ticker"],
        "legacyConditionalProb": legacy,
        "canonicalModelProb": canonical,
        "differencePctPoints": difference_pct_points,
        "executablePrice": executable_price,
        "legacyEdge": legacy_edge,
        "canonicalEdge": canonical_edge,
        "legacyStatus": legacy_status,
        "canonicalShadowStatus": canonical_status,
        "recommendationWouldChange": legacy_status != canonical_status,
    }


def build_comparison():
    with open(LEDGER_PATH) as f:
        ledger = json.load(f)

    rows = []
    for row in ledger.get("rows", []):
        if row.get("scope") != "F5" or row.get("outcome") not in ("Away", "Home"):
            continue
        cmp_row = compare_row(row)
        if cmp_row is not None:
            rows.append(cmp_row)

    changed = [r for r in rows if r["recommendationWouldChange"]]
    diffs = [abs(r["differencePctPoints"]) for r in rows]

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "generatorScript": "scripts/research/build_inning_result_shadow_comparison.py",
        "note": (
            "RESEARCH-ONLY artifact comparing production's legacy F5 "
            "conditional probability against the canonical (tie-retained) "
            "three-way probability for F5 Away/Home only. Never feeds "
            "production execution, never overwrites marketLedger, and "
            "does not itself change any current real-money recommendation "
            "-- see docs/research/INNING_RESULT_MIGRATION.md."
        ),
        "ledgerSource": os.path.relpath(LEDGER_PATH, ROOT),
        "totalComparisons": len(rows),
        "countRecommendationWouldChange": len(changed),
        "averageAbsoluteDifferencePctPoints": round(sum(diffs) / len(diffs), 4) if diffs else None,
        "largestAbsoluteDifferencePctPoints": round(max(diffs), 4) if diffs else None,
        "rows": rows,
    }


def main():
    comparison = build_comparison()
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(comparison, f, indent=2, sort_keys=True)
    print(f"Wrote {comparison['totalComparisons']} F5 Away/Home comparisons to "
          f"{os.path.relpath(OUTPUT_PATH, ROOT)} "
          f"({comparison['countRecommendationWouldChange']} would change recommendation)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
