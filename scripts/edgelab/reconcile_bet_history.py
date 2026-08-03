#!/usr/bin/env python3
"""
scripts/edgelab/reconcile_bet_history.py
=============================================
Historical reconciliation REPORT (Canonical Placed-Bet Ledger milestone,
requirement 15). Read-only -- never writes bets.json, data/bets.json, or
data/edgelab/bets/bets.jsonl; use scripts/edgelab/ingest_existing_bets.py
(optionally with --dry-run first) for the actual backfill write. This
script exists purely to answer "what does the historical bet data look
like" before/after a backfill: unique bet count, exact duplicates,
probable duplicates needing a human look, missing-field counts,
legitimate tranches, model-linked vs manual-only, settled/unsettled.

Usage:
    python3 scripts/edgelab/reconcile_bet_history.py [--root-bets bets.json] [--session-bets data/bets.json] [--out report.json]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage
from lib.edgelab.bets import (
    _find_near_duplicates,
    from_legacy_root_bets_record,
    from_legacy_session_bets_record,
)


def _load(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root-bets", default="bets.json")
    parser.add_argument("--session-bets", default=os.path.join("data", "bets.json"))
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    raw_root = _load(args.root_bets)
    raw_session = _load(args.session_bets)

    normalized = []
    missing_ticker = 0
    for i, raw in enumerate(raw_root):
        rec = from_legacy_root_bets_record(raw, i, source_file=args.root_bets)
        if not rec["marketTicker"]:
            missing_ticker += 1
            continue
        normalized.append(rec)
    for i, raw in enumerate(raw_session):
        rec = from_legacy_session_bets_record(raw, i, source_file=args.session_bets)
        if not rec["marketTicker"]:
            missing_ticker += 1
            continue
        normalized.append(rec)

    by_bet_id = {}
    for rec in normalized:
        by_bet_id.setdefault(rec["betId"], []).append(rec)
    exact_duplicate_groups = {bid: recs for bid, recs in by_bet_id.items() if len(recs) > 1}
    exact_duplicate_extra_rows = sum(len(recs) - 1 for recs in exact_duplicate_groups.values())
    unique_normalized = [recs[0] for recs in by_bet_id.values()]

    missing_entry_price = sum(1 for rec in unique_normalized if rec.get("entryPrice") is None)
    missing_stake = sum(1 for rec in unique_normalized if rec.get("stake") is None)

    probable_duplicates = []
    for rec in unique_normalized:
        near = _find_near_duplicates(rec, unique_normalized, window_seconds=300)
        if near:
            probable_duplicates.append({
                "betId": rec["betId"], "marketTicker": rec["marketTicker"],
                "entryTimestamp": rec["entryTimestamp"], "nearMatches": near,
            })

    canonical_path = storage.singleton_path("bets", "bets.jsonl")
    canonical_bets = list(storage.read_records(canonical_path))
    tranche_groups = {}
    for b in canonical_bets:
        key = (b.get("marketTicker"), b.get("side"))
        tranche_groups.setdefault(key, []).append(b)
    multi_tranche_groups = {k: v for k, v in tranche_groups.items() if len(v) > 1}
    multi_tranche_extra_bets = sum(len(v) - 1 for v in multi_tranche_groups.values())

    report = {
        "generatedAt": ids.utc_now_iso(),
        "sources": {"rootBets": args.root_bets, "sessionBets": args.session_bets, "canonicalLedger": canonical_path},
        "rawRowCounts": {"root": len(raw_root), "session": len(raw_session)},
        "totalUniqueHistoricalBets": len(unique_normalized),
        "exactDuplicates": {
            "groupCount": len(exact_duplicate_groups),
            "extraRows": exact_duplicate_extra_rows,
            "examples": [
                {"betId": bid, "count": len(recs), "sourceFiles": [r["provenance"]["sourceFile"] for r in recs]}
                for bid, recs in list(exact_duplicate_groups.items())[:10]
            ],
        },
        "probableDuplicatesRequiringReview": {
            "count": len(probable_duplicates),
            "examples": probable_duplicates[:10],
        },
        "missingTicker": missing_ticker,
        "missingEntryPrice": missing_entry_price,
        "missingStake": missing_stake,
        "multipleLegitimateTranches": {
            "groupCount": len(multi_tranche_groups),
            "extraBets": multi_tranche_extra_bets,
        },
        "canonicalLedgerCounts": {
            "total": len(canonical_bets),
            "modelLinked": sum(1 for b in canonical_bets if b.get("modelEvaluationId")),
            "manualOnly": sum(1 for b in canonical_bets if b.get("source") == "MANUAL" and not b.get("modelEvaluationId")),
            "settled": sum(1 for b in canonical_bets if b.get("status") == "settled"),
            "unsettled": sum(1 for b in canonical_bets if b.get("status") == "pending"),
            "void": sum(1 for b in canonical_bets if b.get("status") == "void"),
        },
    }

    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
