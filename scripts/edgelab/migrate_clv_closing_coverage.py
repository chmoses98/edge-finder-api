#!/usr/bin/env python3
"""
scripts/edgelab/migrate_clv_closing_coverage.py
===============================================
Backfills closing-quote COVERAGE onto historical canonical bet rows, and
recomputes CLV from the archived quote with a declared price unit.

Two independent defects put historical CLV out of use (see
docs/EDGELAB_CLV_PRICE_UNIT_AUDIT.md and
docs/EDGELAB_CLOSING_QUOTE_POLICY.md):

  1. SCALE. The consumer divided every ClvQuote price by 100, which was
     true only of archived rows up to 2026-09-10. From 2026-09-11 the
     snapshots declare `*_dollars` fields and quotes are 0-1
     probabilities, so a 0.66 NO ask became a 0.0066 closing probability.

  2. COVERAGE. 57 of 94 recent rows were scored against a quote captured
     a median 863 minutes -- 14.4 hours -- before first pitch, and
     reported as closing-line value.

This migration repairs (1) where the evidence is unambiguous and LABELS
(2) honestly. It does not manufacture closing-line evidence that was
never captured: a row whose only pre-start quote is 14 hours old is
marked PRE_CLOSE and drops out of headline CLV. That is the point.

EVIDENCE RULES -- every one of these refuses rather than guesses:

  * The archived ClvQuote named by the row's own clvQuoteId must exist.
  * Its price unit must be DECLARED, never inferred from magnitude. The
    unit comes from the quote's own priceUnit when present, otherwise
    from the `price_source_fields` map in the snapshot its provenance
    names. A snapshot that declares nothing is a refusal.
  * scheduledStart must be resolvable for the market.
  * The quote must be strictly pre-start.
  * The executable price for the wager's OWN side must exist (never a
    bid for an ask, never a midpoint, never the opposite side).
  * entryPrice must be present.

NEVER:
  * selects a different quote than the one already recorded (so no
    hindsight can enter through re-selection);
  * consults any result, outcome or final score;
  * infers a unit from how big a number looks;
  * writes a row it refused.

Idempotent: a second run recomputes identical values and reports
unchanged. Dry-run by default.

Usage:
    python3 scripts/edgelab/migrate_clv_closing_coverage.py            # audit
    python3 scripts/edgelab/migrate_clv_closing_coverage.py --apply    # migrate + receipt
"""
import argparse
import collections
import glob
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import checkpoints, clv_convention, storage

OUT_DIR = os.path.join("data", "edgelab", "reports")
RECEIPT_NAME = "clv_closing_coverage_migration_receipt.json"

REFUSAL_QUOTE_MISSING = "ARCHIVED_QUOTE_NOT_FOUND"
REFUSAL_UNIT_UNDECLARED = "PRICE_UNIT_NOT_DECLARED_IN_ARCHIVE"
REFUSAL_NO_START = "SCHEDULED_START_UNRESOLVED"
REFUSAL_POST_START = "ARCHIVED_QUOTE_IS_POST_START"
REFUSAL_NO_EXECUTABLE = "NO_EXECUTABLE_PRICE_FOR_SIDE"
REFUSAL_NO_ENTRY_PRICE = "ENTRY_PRICE_MISSING"


def _read_any(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def load_archived_quotes():
    """{clvQuoteId: quote} across every archived clv_quotes partition."""
    out = {}
    for path in glob.glob(os.path.join("data", "edgelab", "clv_quotes", "*.jsonl*")):
        for row in _read_any(path):
            out[row["clvQuoteId"]] = row
    return out


def load_scheduled_starts():
    """{marketTicker: scheduledStart} from archived observations."""
    out = {}
    for path in glob.glob(os.path.join("data", "edgelab", "observations", "*.jsonl*")):
        for row in _read_any(path):
            start = row.get("scheduledStart")
            ticker = row.get("marketTicker")
            if start and ticker and ticker not in out:
                out[ticker] = start
    return out


def declared_unit_from_snapshot(source_file, ticker, _cache={}):
    """
    The unit DECLARED by the snapshot this quote came from, via Kalshi's
    own `price_source_fields` map. Returns None when the snapshot is
    missing or declares nothing -- which is a refusal, never a default.
    """
    if not source_file or not os.path.exists(source_file):
        return None
    if source_file not in _cache:
        try:
            with open(source_file) as fh:
                payload = json.load(fh)
        except Exception:
            _cache[source_file] = {}
        else:
            # The snapshot keys its markets by `market_ticker` (older rows may
            # use `ticker`); both are indexed so neither shape silently misses.
            _cache[source_file] = {
                (m.get("market_ticker") or m.get("ticker")): m
                for m in (payload.get("markets") or []) if isinstance(m, dict)
            }
    market = (_cache[source_file] or {}).get(ticker)
    if not market:
        return None

    # Two independent DECLARATIONS, in order of directness. Neither reads the
    # value itself -- a 1-cent quote and a $1.00 quote are both "1", which is
    # exactly the ambiguity that produced the 100x defect.
    #
    # 1. Kalshi's own `unit` field on the market.
    unit = str(market.get("unit") or "").lower()
    if unit == "dollars":
        return clv_convention.UNIT_PROBABILITY
    if unit == "cents":
        return clv_convention.UNIT_CENTS

    # 2. The `price_source_fields` map, which names the API field each price
    #    was sourced from; the `_dollars` suffix is Kalshi's fixed-point form.
    fields = market.get("price_source_fields") or {}
    sourced = str(fields.get("yes_bid") or fields.get("yes_ask") or "")
    if sourced.endswith("_dollars"):
        return clv_convention.UNIT_PROBABILITY
    if sourced:
        return clv_convention.UNIT_CENTS
    return None


def plan_row(bet, quotes, starts):
    """
    Pure-ish. Returns (action, detail) for one bet.
    action is "REPAIR", "UNCHANGED" or "REFUSED".
    """
    quote = quotes.get(bet.get("clvQuoteId"))
    if quote is None:
        return "REFUSED", {"reason": REFUSAL_QUOTE_MISSING}
    if bet.get("entryPrice") is None:
        return "REFUSED", {"reason": REFUSAL_NO_ENTRY_PRICE}

    unit = quote.get("priceUnit") or declared_unit_from_snapshot(
        (quote.get("provenance") or {}).get("sourceFile"), quote.get("marketTicker"))
    if unit not in (clv_convention.UNIT_CENTS, clv_convention.UNIT_PROBABILITY):
        return "REFUSED", {"reason": REFUSAL_UNIT_UNDECLARED}

    start = quote.get("scheduledStart") or starts.get(bet.get("marketTicker"))
    if not start:
        return "REFUSED", {"reason": REFUSAL_NO_START}

    seconds = checkpoints.seconds_before_start(quote.get("capturedAt"), start)
    if seconds is None or seconds < 0:
        return "REFUSED", {"reason": REFUSAL_POST_START, "secondsBeforeStart": seconds}

    side = bet.get("side") or "YES"
    raw = clv_convention.executable_price(quote, side, unit)
    if raw is None:
        return "REFUSED", {"reason": REFUSAL_NO_EXECUTABLE}

    closing = round(clv_convention.convert(raw, unit, clv_convention.UNIT_PROBABILITY), 4)
    new_clv = round((closing - bet["entryPrice"]) * 100, 2)
    coverage = checkpoints.classify_closing_coverage(seconds)

    detail = {
        "betId": bet["betId"], "gameDate": bet.get("gameDate"),
        "marketTicker": bet.get("marketTicker"), "side": side,
        "clvQuoteId": bet.get("clvQuoteId"),
        "declaredPriceUnit": unit,
        "closingCapturedAt": quote.get("capturedAt"),
        "scheduledStart": start,
        "secondsBeforeStart": seconds,
        "checkpoint": quote.get("checkpoint"),
        "before": {"closingPrice": bet.get("closingPrice"), "clv": bet.get("clv"),
                   "closingCoverageClass": bet.get("closingCoverageClass")},
        "after": {"closingPrice": closing, "clv": new_clv, "closingCoverageClass": coverage},
    }
    same = (bet.get("closingPrice") == closing and bet.get("clv") == new_clv
            and bet.get("closingCoverageClass") == coverage)
    return ("UNCHANGED" if same else "REPAIR"), detail


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the migration (default: audit only)")
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()

    bets_path = storage.singleton_path("bets", "bets.jsonl")
    bets = list(storage.read_records(bets_path))
    quotes, starts = load_archived_quotes(), load_scheduled_starts()

    candidates = [b for b in bets if b.get("clvQuoteId") or b.get("clv") is not None]
    repairs, unchanged, refusals = [], [], []
    for bet in candidates:
        action, detail = plan_row(bet, quotes, starts)
        if action == "REPAIR":
            repairs.append(detail)
        elif action == "UNCHANGED":
            unchanged.append(detail)
        else:
            refusals.append(dict(detail, betId=bet["betId"], gameDate=bet.get("gameDate")))

    cov = collections.Counter(d["after"]["closingCoverageClass"] for d in repairs + unchanged)
    reasons = collections.Counter(d["reason"] for d in refusals)
    before = [d["before"]["clv"] for d in repairs if d["before"]["clv"] is not None]
    after = [d["after"]["clv"] for d in repairs]
    true_close_after = [d["after"]["clv"] for d in (repairs + unchanged)
                        if d["after"]["closingCoverageClass"] == checkpoints.COVERAGE_TRUE_CLOSE]

    summary = {
        "rowsExamined": len(candidates),
        "rowsRepaired": len(repairs),
        "rowsUnchanged": len(unchanged),
        "rowsRefused": len(refusals),
        "refusalReasons": dict(reasons),
        "coverageClassAfter": dict(cov),
        "avgClvBeforeRepairedRows": round(sum(before) / len(before), 2) if before else None,
        "avgClvAfterRepairedRows": round(sum(after) / len(after), 2) if after else None,
        "trueCloseRowCount": len(true_close_after),
        "avgClvTrueCloseOnly": round(sum(true_close_after) / len(true_close_after), 2) if true_close_after else None,
    }

    print(json.dumps(summary, indent=2, sort_keys=True))
    if not args.apply:
        print("\n(dry run -- pass --apply to write)")
        return 0

    by_id = {d["betId"]: d for d in repairs}
    updates = []
    for bet in bets:
        d = by_id.get(bet.get("betId"))
        if not d:
            continue
        row = dict(bet)
        row["closingPrice"] = d["after"]["closingPrice"]
        row["clv"] = d["after"]["clv"]
        row["closingCoverageClass"] = d["after"]["closingCoverageClass"]
        row["closingSecondsBeforeStart"] = d["secondsBeforeStart"]
        row["closingCheckpoint"] = d["checkpoint"]
        row["clvUnit"] = "PERCENTAGE_POINTS"
        row["clvConvention"] = clv_convention.CONVENTION_ID
        updates.append(row)

    if updates:
        storage.upsert_records(bets_path, updates, "betId")

    os.makedirs(args.out_dir, exist_ok=True)
    receipt_path = os.path.join(args.out_dir, RECEIPT_NAME)
    with open(receipt_path, "w") as fh:
        json.dump({"summary": summary, "repaired": repairs, "refused": refusals},
                  fh, indent=2, sort_keys=True)
    print("\nwrote %d rows; receipt -> %s" % (len(updates), receipt_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
