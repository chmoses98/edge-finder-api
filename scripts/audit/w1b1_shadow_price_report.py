#!/usr/bin/env python3
"""
scripts/audit/w1b1_shadow_price_report.py
=========================================
WAVE 1, subwave B1. Read-only shadow report: what would the decision layer see
if it priced against the real book instead of a midpoint?

CHANGES NOTHING. It never writes bets.json, never writes a recommendation, and
never touches qualification. Its single output is one JSON artifact under
data/edgelab/operational_health/, which is the Wave 0 convention for audit
output and is already excluded from the repository's data-scope guards.

WHAT IT ANSWERS
---------------
  * COVERAGE, per market family: can the decision-time join actually recover a
    book for each contract, and is that book executable? A family with archived
    evidence that the join cannot reach is a B1 defect, not a data gap.
  * BLAST RADIUS: for every contract, legacy midpoint price vs canonical
    executable price, and how the implied probability moves. This is the
    evidence the B2 cutover has to be argued from.

It deliberately does NOT compute actionable/qualification verdicts. Those depend
on model probability, calibration and thresholds, none of which B1 may touch;
reporting a "shadow actionable count" would require re-implementing the
qualification rule outside the engine that owns it, which is exactly the
duplicate-authority mistake CR-6 is about. Instead it reports the PRICE change,
from which the B2 owner can derive the verdict change inside the real engine.
"""

import argparse
import collections
import gzip
import json
import os
import statistics
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.edgelab import canonical_price as cp          # noqa: E402
from lib.edgelab import observation_join as oj         # noqa: E402
from lib.atomic_json import write_json_atomic          # noqa: E402

OUT_DIR = os.path.join(ROOT, "data", "edgelab", "operational_health")
OUT_NAME = "w1b1_shadow_price_report.json"


def _read_jsonl(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _partition(directory, date):
    for ext in (".jsonl", ".jsonl.gz"):
        path = os.path.join(directory, date + ext)
        if os.path.exists(path):
            return path
    return None


def family_of(row):
    return (row.get("marketFamily") or row.get("family")
            or (row.get("seriesTicker") or "UNKNOWN"))


def side_of(row):
    """
    Which side of the contract a decision buys. The production evaluation record
    does not carry an explicit side field (0% coverage, measured), so YES is
    used and recorded as an assumption rather than silently presumed: every
    Kalshi market row in this corpus is a YES-purchase on its own ticker.
    """
    declared = (row.get("side") or "").upper()
    if declared in (cp.SIDE_YES, cp.SIDE_NO):
        return declared, "declared"
    return cp.SIDE_YES, "assumed_yes_no_side_field_in_record"


def legacy_price_cents(row):
    """
    The price the legacy path would have used, in cents, or None.

    marketFairProbability / marketProbVF are vig-free MIDPOINT-derived numbers.
    Reading them here is how the report measures the gap; nothing consumes them
    as an executable price.
    """
    for key in ("executablePriceUsed", "modelSnapshotPrice", "executablePriceAtOutput"):
        value = row.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    # marketImpliedProbability is the vig-free MIDPOINT-derived probability the
    # decision layer actually used -- measured coverage: 100% of evaluation rows,
    # while executablePriceUsed is 0%. It is the legacy "price" in practice.
    for key in ("marketFairProbability", "marketProbVF", "kalshiVF",
                "marketImpliedProbability"):
        value = row.get(key)
        if value is not None:
            try:
                v = float(value)
                return v if v > 1.0 else v * 100.0
            except (TypeError, ValueError):
                pass
    return None


# Rows whose createdAt is a genuine decision instant. The postgame settlement
# pass re-writes evaluation rows the FOLLOWING day, so their createdAt is a
# bookkeeping timestamp, not a moment anyone decided anything -- joining those
# to "the latest quote before createdAt" produces a technically-correct but
# meaningless 11-hour-old quote. Measured on 2026-09-08: median age 39,702s
# across all rows. Real-time decisions are the prospective capture.
REAL_TIME_DECISION_SOURCES = ("prospective_snapshot",)


def is_real_time_decision(row):
    return (row.get("artifactSource") or "") in REAL_TIME_DECISION_SOURCES


def analyse(dates, root=None, stale_after=oj.STALE_AFTER_SECONDS,
            real_time_only=False):
    root = root or ROOT
    evaluations_dir = os.path.join(root, "data", "edgelab", "model_evaluations")
    index = oj.load_observations(dates=dates, root=root)

    per_family = collections.defaultdict(lambda: {
        "total": 0, "matchedObservation": 0, "genuineYesAsk": 0,
        "genuineNoAsk": 0, "derivedNoAskFromYesBid": 0, "noExecutablePrice": 0,
        "staleQuote": 0, "ambiguousJoin": 0, "legacyPriceAvailable": 0,
        "bothPricesAvailable": 0, "bookState": collections.Counter(),
        "tickerResolution": collections.Counter(),
        "artifactSource": collections.Counter(),
        "priceBasis": collections.Counter(), "refusalReason": collections.Counter(),
        "joinRefusal": collections.Counter(),
        "_ages": [], "_deltas": [],
    })
    biggest = []

    for date in dates:
        path = _partition(evaluations_dir, date)
        if not path:
            continue
        for row in _read_jsonl(path):
            if real_time_only and not is_real_time_decision(row):
                continue
            family = family_of(row)
            bucket = per_family[family]
            bucket["total"] += 1
            bucket["artifactSource"][row.get("artifactSource") or "unknown"] += 1

            decided_at = row.get("createdAt") or row.get("capturedAt")
            side, _side_source = side_of(row)

            # The decision record may carry a synthetic "<gamePk>:<family>" key
            # rather than a Kalshi ticker; resolve it, or refuse.
            ticker, resolve_method, resolve_refusal = oj.resolve_market_ticker(row, index)
            bucket["tickerResolution"][resolve_method] += 1
            if ticker is None:
                bucket["joinRefusal"][resolve_method] += 1
                bucket["noExecutablePrice"] += 1
                continue

            price, join = oj.price_for_decision(ticker, side, decided_at, index,
                                                stale_after_seconds=stale_after)
            if join.get("tieBroken"):
                bucket["ambiguousJoin"] += 1
            if not join["matched"]:
                bucket["joinRefusal"][join["joinMethod"]] += 1
                bucket["noExecutablePrice"] += 1
                continue

            bucket["matchedObservation"] += 1
            if join["stale"]:
                bucket["staleQuote"] += 1
            if join["quoteAgeSeconds"] is not None:
                bucket["_ages"].append(join["quoteAgeSeconds"])

            bucket["bookState"][price["book"]["bookState"]] += 1
            basis = price["priceBasis"]
            if basis:
                bucket["priceBasis"][basis] += 1
            if basis == cp.BASIS_YES_ASK:
                bucket["genuineYesAsk"] += 1
            elif basis == cp.BASIS_NO_ASK:
                bucket["genuineNoAsk"] += 1
            elif basis == cp.BASIS_DERIVED_NO_ASK_FROM_YES_BID:
                bucket["derivedNoAskFromYesBid"] += 1
            else:
                bucket["noExecutablePrice"] += 1
                bucket["refusalReason"][price["refusalReason"]] += 1

            legacy = legacy_price_cents(row)
            if legacy is not None:
                bucket["legacyPriceAvailable"] += 1
            canonical = price["executablePrice"]
            if legacy is not None and canonical is not None:
                bucket["bothPricesAvailable"] += 1
                delta = round(canonical - legacy, 4)
                bucket["_deltas"].append(delta)
                biggest.append({
                    "marketTicker": ticker, "marketFamily": family,
                    "decisionAt": decided_at, "side": side,
                    "legacyPriceCents": round(legacy, 4),
                    "canonicalExecutablePriceCents": canonical,
                    "deltaCents": delta, "priceBasis": basis,
                    "bookState": price["book"]["bookState"],
                    "yesBid": price["book"]["yesBid"],
                    "yesAsk": price["book"]["yesAsk"],
                    "spreadCents": price["book"]["spreadCents"],
                    "observationId": price["observationId"],
                    "quoteAgeSeconds": price["quoteAgeSeconds"],
                })

    families = {}
    for name, bucket in sorted(per_family.items()):
        ages, deltas = bucket.pop("_ages"), bucket.pop("_deltas")
        entry = dict(bucket)
        entry["bookState"] = dict(bucket["bookState"])
        entry["tickerResolution"] = dict(bucket["tickerResolution"])
        entry["artifactSource"] = dict(bucket["artifactSource"])
        entry["priceBasis"] = dict(bucket["priceBasis"])
        entry["refusalReason"] = dict(bucket["refusalReason"])
        entry["joinRefusal"] = dict(bucket["joinRefusal"])
        entry["quoteAgeSeconds"] = {
            "median": round(statistics.median(ages), 1) if ages else None,
            "p95": (round(sorted(ages)[max(0, int(len(ages) * 0.95) - 1)], 1)
                    if ages else None),
            "max": round(max(ages), 1) if ages else None,
        }
        entry["priceDeltaCents"] = {
            "n": len(deltas),
            "median": round(statistics.median(deltas), 3) if deltas else None,
            "mean": round(statistics.fmean(deltas), 3) if deltas else None,
            "min": round(min(deltas), 3) if deltas else None,
            "max": round(max(deltas), 3) if deltas else None,
            "canonicalMoreExpensive": sum(1 for d in deltas if d > 0),
            "canonicalCheaper": sum(1 for d in deltas if d < 0),
            "identical": sum(1 for d in deltas if d == 0),
        }
        families[name] = entry

    biggest.sort(key=lambda r: abs(r["deltaCents"]), reverse=True)
    totals = collections.Counter()
    for entry in families.values():
        for key in ("total", "matchedObservation", "genuineYesAsk", "genuineNoAsk",
                    "derivedNoAskFromYesBid", "noExecutablePrice", "staleQuote",
                    "ambiguousJoin", "legacyPriceAvailable", "bothPricesAvailable"):
            totals[key] += entry[key]

    return {
        "schemaVersion": "1",
        "wave": "W1_B1",
        "mode": "SHADOW_READ_ONLY",
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generatedBy": "scripts/audit/w1b1_shadow_price_report.py",
        "dates": list(dates),
        "realTimeDecisionsOnly": real_time_only,
        "observationSource": "data/edgelab/observations",
        "staleAfterSeconds": stale_after,
        "authorityNote": (
            "Shadow only. No production qualification, confidence, Bet Up To, "
            "stake or recommendation eligibility is computed or changed here."
        ),
        "summary": dict(totals),
        "families": families,
        "largestPriceDiscrepancies": biggest[:40],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="W1-B1 shadow executable-price report")
    ap.add_argument("--dates", nargs="+", required=True,
                    help="model_evaluations partitions to analyse (YYYY-MM-DD)")
    ap.add_argument("--out-dir", dest="out_dir", default=OUT_DIR)
    ap.add_argument("--real-time-only", dest="real_time_only", action="store_true",
                    help="analyse only genuine real-time decisions (prospective capture)")
    ap.add_argument("--write-artifact", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    report = analyse(args.dates, real_time_only=args.real_time_only)

    if args.write_artifact:
        os.makedirs(args.out_dir, exist_ok=True)
        write_json_atomic(report, os.path.join(args.out_dir, OUT_NAME))

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return 0

    s = report["summary"]
    print("W1-B1 SHADOW EXECUTABLE-PRICE REPORT  (%s)" % report["mode"])
    print("=" * 78)
    print("dates: %s" % ", ".join(report["dates"]))
    print("contracts evaluated      : %d" % s.get("total", 0))
    print("  observation matched    : %d" % s.get("matchedObservation", 0))
    print("  genuine YES ask        : %d" % s.get("genuineYesAsk", 0))
    print("  genuine NO ask         : %d" % s.get("genuineNoAsk", 0))
    print("  derived NO from YES bid: %d" % s.get("derivedNoAskFromYesBid", 0))
    print("  NO executable price    : %d" % s.get("noExecutablePrice", 0))
    print("  stale quote            : %d" % s.get("staleQuote", 0))
    print("  ambiguous join         : %d" % s.get("ambiguousJoin", 0))
    print("  legacy price available : %d" % s.get("legacyPriceAvailable", 0))
    print("  both prices comparable : %d" % s.get("bothPricesAvailable", 0))
    print()
    print("%-26s %6s %8s %8s %8s %9s %9s" % (
        "family", "total", "matched", "execPx", "noExec", "medAge", "medDelta"))
    for name, e in sorted(report["families"].items(),
                          key=lambda kv: -kv[1]["total"]):
        exec_n = e["genuineYesAsk"] + e["genuineNoAsk"] + e["derivedNoAskFromYesBid"]
        print("%-26s %6d %8d %8d %8d %9s %9s" % (
            name[:26], e["total"], e["matchedObservation"], exec_n,
            e["noExecutablePrice"],
            e["quoteAgeSeconds"]["median"], e["priceDeltaCents"]["median"]))
    if report["largestPriceDiscrepancies"]:
        print("\nlargest legacy-vs-canonical price gaps (cents):")
        for r in report["largestPriceDiscrepancies"][:10]:
            print("  %+8.2f  %-34s legacy %6.2f -> canonical %6.2f  [%s, %s]" % (
                r["deltaCents"], (r["marketTicker"] or "?")[:34],
                r["legacyPriceCents"], r["canonicalExecutablePriceCents"],
                r["priceBasis"], r["bookState"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
