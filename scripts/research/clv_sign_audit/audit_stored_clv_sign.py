#!/usr/bin/env python3
"""READ-ONLY audit of the sign convention actually stored in the canonical
bet ledger's `clv` field.

Recomputes side-aware GOOD CLV -- positive means entered CHEAPER than the
close -- from each bet's own raw fields:

    good_clv_cents = (closing side-relevant implied - entry side-relevant
                      implied) * 100

and compares it against the stored `clv`, classifying every row as an
exact MATCH, an exact NEGATION, or something else.

Never mutates the ledger. Never multiplies anything by -1 in place.
"""

import json
import os
import sys
from collections import Counter, defaultdict

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
LEDGER = os.path.join(REPO, "data", "edgelab", "bets", "bets.jsonl")
OUT = os.path.join(REPO, "data", "edgelab", "research_artifacts",
                   "clv_sign_audit", "stored_clv_sign_audit.json")
TOL = 0.51  # cents; stored values are rounded to 2dp, some to whole cents


def side_relevant(entry_price, closing_price, side):
    """Both prices are already the SIDE-RELEVANT implied probability this
    bet paid / would pay at the close (0-1). Returns (entry, closing) in
    cents, or None when either is unusable."""
    if entry_price is None or closing_price is None:
        return None
    try:
        e, c = float(entry_price), float(closing_price)
    except (TypeError, ValueError):
        return None
    if not (0.0 < e < 1.0) or not (0.0 < c < 1.0):
        return None
    return e * 100.0, c * 100.0


def main():
    rows = [json.loads(l) for l in open(LEDGER) if l.strip()]
    counts = Counter()
    by_source = defaultdict(Counter)
    by_month = defaultdict(Counter)
    by_batch = defaultdict(Counter)
    examples = defaultdict(list)

    for r in rows:
        stored = r.get("clv")
        pair = side_relevant(r.get("entryPrice"), r.get("closingPrice"), r.get("side"))
        src = r.get("source") or "unknown"
        month = (r.get("gameDate") or r.get("placedAt") or "unknown")[:7]
        batch = r.get("importBatchId") or "none"

        if stored is None:
            counts["stored_clv_null"] += 1
            continue
        if pair is None:
            counts["not_reconstructable"] += 1
            by_source[src]["not_reconstructable"] += 1
            continue

        entry_c, closing_c = pair
        good = closing_c - entry_c          # positive = entered cheaper
        inverted = entry_c - closing_c
        s = float(stored)

        # AMBIGUITY FIRST. When entry == closing, good == inverted == 0, so
        # such a row matches BOTH conventions and is evidence for neither.
        # Classifying it as either one would fabricate a signal.
        if abs(good - inverted) <= TOL:
            k = "sign_ambiguous_entry_equals_closing"
        elif abs(s - good) <= TOL:
            k = "matches_POSITIVE_IS_GOOD"
        elif abs(s - inverted) <= TOL:
            k = "matches_INVERTED_entry_minus_closing"
        else:
            k = "other_discrepancy"
            if len(examples["other"]) < 15:
                examples["other"].append({
                    "betId": (r.get("betId") or "")[:12], "side": r.get("side"),
                    "entryPrice": r.get("entryPrice"), "closingPrice": r.get("closingPrice"),
                    "storedClv": s, "goodClv": round(good, 2),
                    "invertedClv": round(inverted, 2), "source": src,
                })
        counts[k] += 1
        by_source[src][k] += 1
        by_month[month][k] += 1
        by_batch[batch][k] += 1

    # zero-CLV rows are sign-ambiguous; report how many are decisive
    decisive = counts["matches_POSITIVE_IS_GOOD"] + counts["matches_INVERTED_entry_minus_closing"]
    doc = {
        "audit": "stored_clv_sign",
        "readOnly": True,
        "ledger": os.path.relpath(LEDGER, REPO),
        "totalBets": len(rows),
        "toleranceCents": TOL,
        "convention": "good_clv = (closing_side_implied - entry_side_implied) * 100",
        "counts": dict(counts),
        "decisiveRows": decisive,
        "bySource": {k: dict(v) for k, v in by_source.items()},
        "byMonth": {k: dict(v) for k, v in sorted(by_month.items())},
        "byImportBatch": {k: dict(v) for k, v in by_batch.items()},
        "otherDiscrepancyExamples": examples["other"],
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("wrote", OUT)
    for k, v in sorted(counts.items()):
        print("  %-42s %d" % (k, v))
    print("\nby source:")
    for k, v in sorted(by_source.items()):
        print("  %-28s %s" % (k[:28], dict(v)))
    print("\nby month:")
    for k, v in sorted(by_month.items()):
        print("  %-10s %s" % (k, dict(v)))


if __name__ == "__main__":
    sys.exit(main())
