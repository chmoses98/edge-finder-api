#!/usr/bin/env python3
"""Migrate canonical wager CLV to the POSITIVE_IS_GOOD_V1 convention.

RECOMPUTES from first principles (side, entryPrice, closingPrice) using
lib.edgelab.clv_convention. It NEVER blanket-negates: a zero-CLV row is
sign-ambiguous, and a row whose recomputation matches neither the stored
value nor its exact negation is STOPPED and classified OTHER_DISCREPANCY
rather than guessed at.

ONLY CLV-related fields may change. stake, entryPrice, closingPrice,
result, settlement, returns, P/L, ticker, betId, importBatchId and
sourceBetKey are asserted byte-identical before the write is accepted.

Dry-run by default; --apply writes through the canonical
lib.edgelab.storage.upsert_records path (atomic + locked). Idempotent:
a second --apply produces zero changes.

  python3 scripts/edgelab/migrate_clv_sign.py            # manifest only
  python3 scripts/edgelab/migrate_clv_sign.py --apply    # migrate + receipt
"""

import argparse
import hashlib
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

from lib.edgelab import clv_convention, storage  # noqa: E402

LEDGER = os.path.join(REPO, "data", "edgelab", "bets", "bets.jsonl")
OUT_DIR = os.path.join(REPO, "data", "edgelab", "analytics")
MANIFEST = os.path.join(OUT_DIR, "clv_sign_migration_manifest.json")
RECEIPT = os.path.join(OUT_DIR, "clv_sign_migration_receipt.json")

TOL = 0.02          # cents; stored values are rounded to 2dp

# Fields the migration is permitted to touch. Everything else must be
# byte-identical afterwards.
MUTABLE = {"clv", "clvConvention", "clvUnit"}

IMMUTABLE_CRITICAL = (
    "betId", "stake", "entryPrice", "closingPrice", "result", "status",
    "side", "marketTicker", "importBatchId", "sourceBetKey",
    "recommendationId", "netProfitLoss", "realizedReturn",
    "confirmedReceiptNetProfitLoss", "settlementStatus",
)

RECOMPUTED = "RECOMPUTED_FROM_SOURCE"
ZERO = "ZERO_UNAMBIGUOUS"
UNRESOLVED = "UNRESOLVED_MISSING_SOURCE_FIELDS"
OTHER = "OTHER_DISCREPANCY"
ALREADY = "ALREADY_CANONICAL"


def sha256_file(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def side_prices(row):
    """Side-relevant entry/closing implied probabilities, or (None, None).

    entryPrice and closingPrice are both stored as the SIDE-RELEVANT
    implied probability the bet actually faced (verified in the sign
    audit: NO rows carry the NO-side price, not the YES price)."""
    e, c = row.get("entryPrice"), row.get("closingPrice")
    try:
        e = float(e) if e is not None else None
        c = float(c) if c is not None else None
    except (TypeError, ValueError):
        return None, None
    if e is None or c is None:
        return None, None
    if not (0.0 < e < 1.0) or not (0.0 < c < 1.0):
        return None, None
    return e, c


def classify(row):
    """-> (classification, recomputed_clv_or_None, reason)"""
    stored = row.get("clv")
    e, c = side_prices(row)
    if e is None or c is None:
        return UNRESOLVED, None, "entryPrice/closingPrice missing or out of range"

    side = (row.get("side") or "YES").upper()
    if side not in (clv_convention.SIDE_YES, clv_convention.SIDE_NO):
        return UNRESOLVED, None, "unrecognized side %r" % row.get("side")

    # Both prices are already side-relevant, so the canonical formula is a
    # direct closing-minus-entry on them.
    recomputed = round(clv_convention.good_clv_from_implied(
        e, c, unit=clv_convention.UNIT_PERCENTAGE_POINTS), 2)

    if stored is None:
        return RECOMPUTED, recomputed, "stored clv was null; recomputed from source"

    stored = float(stored)
    if abs(recomputed) <= TOL and abs(stored) <= TOL:
        return ZERO, 0.0, "entry == closing; canonical CLV is unambiguously zero"
    if abs(stored - recomputed) <= TOL:
        return ALREADY, recomputed, "stored value already matches the canonical sign"
    if abs(stored + recomputed) <= TOL:
        return RECOMPUTED, recomputed, "stored value was the exact negation (legacy convention)"
    return OTHER, recomputed, (
        "recomputed %.2f matches neither stored %.2f nor its negation" % (recomputed, stored))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the migration")
    args = ap.parse_args()

    before_hash = sha256_file(LEDGER)
    rows = [json.loads(l) for l in open(LEDGER) if l.strip()]

    manifest, counts, updates = [], {}, []
    for row in rows:
        cls, recomputed, reason = classify(row)
        counts[cls] = counts.get(cls, 0) + 1
        manifest.append({
            "betId": row.get("betId"),
            "side": row.get("side"),
            "entryPrice": row.get("entryPrice"),
            "closingPrice": row.get("closingPrice"),
            "oldStoredClv": row.get("clv"),
            "recomputedClv": recomputed,
            "classification": cls,
            "reason": reason,
            "source": row.get("source"),
            "importBatchId": row.get("importBatchId"),
        })
        if cls in (RECOMPUTED, ZERO, ALREADY) and recomputed is not None:
            new_row = dict(row)
            new_row["clv"] = recomputed
            new_row["clvConvention"] = clv_convention.CONVENTION_ID
            new_row["clvUnit"] = clv_convention.UNIT_PERCENTAGE_POINTS
            if json.dumps(new_row, sort_keys=True) != json.dumps(row, sort_keys=True):
                # Safety: only permitted fields may differ.
                changed = {k for k in set(new_row) | set(row)
                           if json.dumps(new_row.get(k), sort_keys=True)
                           != json.dumps(row.get(k), sort_keys=True)}
                illegal = changed - MUTABLE
                if illegal:
                    raise SystemExit("REFUSING: migration would change %s on %s"
                                     % (sorted(illegal), row.get("betId")))
                for f in IMMUTABLE_CRITICAL:
                    assert new_row.get(f) == row.get(f), f
                updates.append(new_row)

    # A NO-OP RUN MUST NOT DESTROY THE RECORD OF THE REAL ONE. Re-running
    # this migration (e.g. an idempotence check) legitimately finds nothing
    # to do; rewriting the manifest/receipt then would replace the real
    # before/after hashes with a pair that are trivially equal, erasing the
    # audit trail of the migration that actually happened.
    if not updates and os.path.exists(MANIFEST):
        print("no rows need a write; leaving the existing manifest/receipt intact")
        print("  (already migrated -- this run is a no-op)")
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(MANIFEST, "w") as fh:
        json.dump({"ledger": os.path.relpath(LEDGER, REPO),
                   "beforeSha256": before_hash,
                   "convention": clv_convention.CONVENTION_ID,
                   "toleranceCents": TOL,
                   "counts": counts,
                   "rowsNeedingWrite": len(updates),
                   "rows": manifest}, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("manifest ->", MANIFEST)
    for k in sorted(counts):
        print("  %-34s %d" % (k, counts[k]))
    print("  %-34s %d" % ("rows needing a write", len(updates)))

    if counts.get(OTHER):
        print("STOP: %d OTHER_DISCREPANCY rows; refusing to migrate." % counts[OTHER])
        return 2
    if not args.apply:
        print("(dry run -- pass --apply to write)")
        return 0

    if updates:
        storage.upsert_records(LEDGER, updates, "betId")
    after_hash = sha256_file(LEDGER)

    after_rows = [json.loads(l) for l in open(LEDGER) if l.strip()]
    assert len(after_rows) == len(rows), "row count changed"
    by_id = {r.get("betId"): r for r in rows}
    for r in after_rows:
        old = by_id.get(r.get("betId"))
        assert old is not None, "new betId appeared"
        for f in IMMUTABLE_CRITICAL:
            assert r.get(f) == old.get(f), "immutable field %s changed on %s" % (f, r.get("betId"))

    with open(RECEIPT, "w") as fh:
        json.dump({
            "convention": clv_convention.CONVENTION_ID,
            "unit": clv_convention.UNIT_PERCENTAGE_POINTS,
            "ledger": os.path.relpath(LEDGER, REPO),
            "beforeSha256": before_hash,
            "afterSha256": after_hash,
            "totalRows": len(rows),
            "rowsChanged": len(updates),
            "counts": counts,
            "unchangedZeroRows": counts.get(ZERO, 0),
            "unresolvedNullRows": counts.get(UNRESOLVED, 0),
            "discrepancies": counts.get(OTHER, 0),
            "immutableFieldsVerified": list(IMMUTABLE_CRITICAL),
            "note": ("Recomputed from side/entryPrice/closingPrice via "
                     "lib.edgelab.clv_convention. No value was blanket-negated. "
                     "Only clv/clvConvention/clvUnit may differ."),
        }, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("receipt ->", RECEIPT)
    print("before %s\nafter  %s" % (before_hash[:32], after_hash[:32]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
