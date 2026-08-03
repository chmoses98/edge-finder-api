#!/usr/bin/env python3
"""
scripts/edgelab/reconcile_2026_06_18_legacy_entry_prices.py
================================================================
One-time, narrowly-scoped reconciliation for exactly two bets
(data/bets.json indices 88/89, both from 2026-06-18) that a prior review
pass flagged AMBIGUOUS_REQUIRES_REVIEW: their raw entryPrice (48.54,
51.46) is a non-integer value in the (1, 100) range, which
lib.edgelab.bets._classify_price_value deliberately refuses to guess
(see that function's docstring) since such a value could be American
odds with a lost sign/digit, genuine decimal odds, or malformed input.

A follow-up cross-artifact review resolved the ambiguity conclusively
for these two specific bets. This script does NOT change either bet's
entryPrice or entryOdds -- both already read 0.4854/0.5146 (refuses to
run if they don't, see below) -- it only records, on each bet's own
`reconciliation` field, the evidence that resolved the ambiguity, so a
future reader never re-flags these two rows as unverified guesses.
Preserves every other field on both rows exactly (settlement state, CLV
state, recommendation/model links, notes, stake, ticker, timestamps,
provenance) -- touches nothing else in the ledger.

Evidence for betId 12cd9391595975ffb29b4839497c5773043676cf
(data/bets.json index 88, ticker KXMLBGAME-26JUN181840NYMPHI-NYM):
  - rawEdgePct (5.76) in the raw record equals modelProb (54.3) minus
    entryPrice (48.54) exactly -- both on the same 0-100 scale, an
    internal arithmetic identity, not a range guess.
  - data/execution_slip_2026-06-18.json (a wholly separate production
    pipeline artifact, generated 2026-06-18T21:16:33Z -- 14 minutes
    before this bet was logged) independently records, for this exact
    ticker: execPrice "48.54¢" (explicitly cent-labeled), modelProb
    "54.3%", rawEdge "+5.76%", betSize 3.0 -- an exact match on every
    field.

Evidence for betId b4bac80dca6ffd6c306994a0e9b447646db937d5
(data/bets.json index 89, ticker KXMLBGAME-26JUN181940STLKC-KC):
  - rawEdgePct (5.57) equals modelProb (57.03) minus entryPrice (51.46)
    exactly.
  - The same execution-slip file independently records, for this exact
    ticker: execPrice "51.46¢", modelProb "57.03%", rawEdge "+5.57%",
    betSize 3.0.

This script is intentionally hardcoded to these two betIds -- it is NOT
a generalized inference rule and must never be generalized into an
automatic classification rule inside _classify_price_value: cross-
referencing a separate execution-slip artifact and checking a specific
arithmetic identity is per-row investigative work, not something safe to
apply blindly to arbitrary future ambiguous rows (see
docs/CANONICAL_BET_LEDGER.md's normalization-rules section).

Usage:
    python3 scripts/edgelab/reconcile_2026_06_18_legacy_entry_prices.py [--dry-run]

Idempotent: rerunning against an already-reconciled ledger is a true
no-op (byte-identical file, no write attempted at all).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import schema, storage

# Fixed at the time this reconciliation was performed -- never regenerated
# on a rerun, so a repeat run produces byte-identical output.
_RECONCILED_AT = "2026-08-03T14:30:00Z"

_EXPECTED_ENTRY_PRICE = {
    "12cd9391595975ffb29b4839497c5773043676cf": 0.4854,
    "b4bac80dca6ffd6c306994a0e9b447646db937d5": 0.5146,
}

_RECONCILIATIONS = {
    "12cd9391595975ffb29b4839497c5773043676cf": {
        "classification": "SAFE_MANUAL_FIX",
        "originalSourceFile": "data/bets.json",
        "originalSourceIndex": "88",
        "originalRawValue": 48.54,
        "corroboratingArtifactPath": "data/execution_slip_2026-06-18.json",
        "corroboratingArithmetic": (
            "rawEdgePct (5.76) == modelProb (54.3) - entryPrice (48.54); "
            "execution slip independently records execPrice=48.54¢, modelProb=54.3%, "
            "rawEdge=+5.76%, betSize=3.0 for ticker KXMLBGAME-26JUN181840NYMPHI-NYM"
        ),
        "reconciledAt": _RECONCILED_AT,
        "reconciliationMethod": "MANUAL_CROSS_ARTIFACT_REVIEW",
        "generalizedInferenceRuleUsed": False,
    },
    "b4bac80dca6ffd6c306994a0e9b447646db937d5": {
        "classification": "SAFE_MANUAL_FIX",
        "originalSourceFile": "data/bets.json",
        "originalSourceIndex": "89",
        "originalRawValue": 51.46,
        "corroboratingArtifactPath": "data/execution_slip_2026-06-18.json",
        "corroboratingArithmetic": (
            "rawEdgePct (5.57) == modelProb (57.03) - entryPrice (51.46); "
            "execution slip independently records execPrice=51.46¢, modelProb=57.03%, "
            "rawEdge=+5.57%, betSize=3.0 for ticker KXMLBGAME-26JUN181940STLKC-KC"
        ),
        "reconciledAt": _RECONCILED_AT,
        "reconciliationMethod": "MANUAL_CROSS_ARTIFACT_REVIEW",
        "generalizedInferenceRuleUsed": False,
    },
}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = storage.singleton_path("bets", "bets.jsonl")

    with storage.locked(path):
        rows = list(storage.read_records(path))
        index_by_id = {r["betId"]: i for i, r in enumerate(rows) if r.get("betId")}

        touched = []
        for bet_id, reconciliation in _RECONCILIATIONS.items():
            idx = index_by_id.get(bet_id)
            if idx is None:
                print(f"[reconcile] ERROR: betId {bet_id} not found in the ledger -- aborting, no changes made", file=sys.stderr)
                return 1

            row = rows[idx]
            expected_price = _EXPECTED_ENTRY_PRICE[bet_id]
            if row.get("entryPrice") != expected_price:
                print(
                    f"[reconcile] ERROR: betId {bet_id} entryPrice is {row.get('entryPrice')!r}, "
                    f"expected {expected_price!r} -- refusing to touch (this script never changes "
                    f"entryPrice/entryOdds, only annotates already-correct rows)",
                    file=sys.stderr,
                )
                return 1

            if row.get("reconciliation") == reconciliation:
                continue  # true no-op: already reconciled with this exact evidence

            new_row = dict(row)
            new_row["reconciliation"] = reconciliation
            errors = schema.validate_record("placed_bet", new_row)
            if errors:
                print(f"[reconcile] ERROR: betId {bet_id} would fail schema validation: {errors}", file=sys.stderr)
                return 1
            touched.append((idx, new_row))

        if not touched:
            print("[reconcile] no-op: both bets already carry this exact reconciliation metadata")
            return 0

        if args.dry_run:
            print(f"[reconcile] DRY RUN: would update {len(touched)} row(s): {[rows[i]['betId'] for i, _ in touched]}")
            return 0

        for idx, new_row in touched:
            rows[idx] = new_row
        storage.write_all_records(path, rows)
        print(f"[reconcile] updated {len(touched)} row(s): {[r['betId'] for _, r in touched]}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
