#!/usr/bin/env python3
"""
scripts/research/full_universe/run_qualified_calibration.py
===========================================================
PHASES K/L/M: recompute calibration on completeness-qualified evidence,
with uncertainty that respects how correlated the rows actually are.

TWO CORRECTIONS TO #233

1. SOURCE COMPLETENESS (K/L). #233 scored every settled market carrying
   an executable pre-start ask, with no regard for whether the capture
   that produced that ask had retrieved everything it asked for. It had
   not, 9 times in 21 days, and the missingness was SYSTEMATIC: the old
   sequential fetch meant a rate limit truncated whatever series came
   last, so the same hitter prop families absorbed the loss every time.
   That is bias in a known direction, not noise.

   Every row here carries the completeness class of the capture its
   defining quote came from, and the headline arm EXCLUDES rows sourced
   from a PARTIAL capture.

   There is no COMPLETE arm. Zero historical captures can prove
   completeness (see docs/EDGELAB_ARCHIVE_COMPLETENESS.md): the pre-v4
   path discarded live cursors with no failure recorded, so a clean
   legacy snapshot is UNKNOWN_LEGACY, not complete. Saying so is the
   point; inventing a COMPLETE arm out of legacy data would be the
   failure mode this whole exercise exists to prevent.

2. INDEPENDENCE (M). #233 printed Wilson intervals over 31,584 rows and
   flagged its own correlation problem: 145.5 rows per independent game.
   Every hitter prop, team total and game total on one game shares that
   game's outcome, so a Wilson interval assuming 31,584 independent
   trials is far too narrow, and "outside the interval" meant very
   little.

   Uncertainty here is a CLUSTERED BOOTSTRAP: resample whole GAMES with
   replacement, recompute the statistic, and take percentiles. A game is
   the unit that is actually independent. Both intervals are reported so
   the difference is visible rather than asserted.

WHAT IT REFUSES TO DO

* It never fabricates a COMPLETE arm from evidence that cannot prove it.
* It never clamps a degenerate 0/1 price into range.
* It never claims a family is miscalibrated on the naive interval alone.
* It never drops the unqualified arm -- the comparison IS the finding.

Usage:
    python3 scripts/research/full_universe/run_qualified_calibration.py
"""
import argparse
import collections
import datetime
import gzip
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from lib.edgelab.capture_completeness import COMPLETE, PARTIAL, UNKNOWN_LEGACY

SCHEMA_VERSION = "full_universe_qualified_calibration_v1"
AUDIT_ROWS = os.path.join("data", "edgelab", "reports", "market_coverage_audit.jsonl.gz")
LEDGER = os.path.join("data", "edgelab", "reports", "capture_completeness_ledger.json")
OUT_DIR = os.path.join("data", "edgelab", "reports")

# A quote is attributed to the capture whose fetched_at it matches within
# this tolerance. Observation timestamps are the capture's own clock, so the
# match is near-exact; the window only absorbs per-market write skew.
CAPTURE_MATCH_TOLERANCE_SECONDS = 180

UNCLASSIFIABLE = "UNCLASSIFIABLE_SNAPSHOT_PRUNED"

BOOTSTRAP_ITERATIONS = 2000
BOOTSTRAP_SEED = 20260922      # fixed: a research number must be reproducible


def _ts(value):
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def load_capture_classes(ledger_path):
    with open(ledger_path) as fh:
        ledger = json.load(fh)
    stamps = []
    for row in ledger["rows"]:
        when = _ts(row.get("fetchedAt"))
        if when:
            stamps.append((when, row["class"]))
    stamps.sort()
    return stamps


def classify_quote_source(captured_at, stamps, tolerance=CAPTURE_MATCH_TOLERANCE_SECONDS):
    """Which capture produced this quote, and what could that capture prove?

    A quote older than the 21-day snapshot retention window has no capture
    left to classify. That is UNCLASSIFIABLE, which is NOT the same as
    UNKNOWN_LEGACY: one is a snapshot we still hold that carries no evidence,
    the other is a snapshot we no longer hold at all.
    """
    when = _ts(captured_at)
    if when is None or not stamps:
        return UNCLASSIFIABLE
    best, best_delta = None, None
    for stamp, klass in stamps:
        delta = abs((stamp - when).total_seconds())
        if best_delta is None or delta < best_delta:
            best, best_delta = klass, delta
    return best if best_delta is not None and best_delta <= tolerance else UNCLASSIFIABLE


def wilson(successes, n, z=1.96):
    """The NAIVE interval: it assumes n independent trials, which these are
    emphatically not. Reported only for comparison against the clustered one."""
    if not n:
        return (None, None)
    phat = successes / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n) / denom
    return (round(max(0.0, centre - margin), 4), round(min(1.0, centre + margin), 4))


def clustered_bootstrap_gap(rows, iterations=BOOTSTRAP_ITERATIONS, seed=BOOTSTRAP_SEED):
    """
    95% interval on the calibration gap, resampling whole GAMES.

    A game is the unit that is actually independent: every prop, team total
    and game total on it shares one outcome. Resampling rows would assume
    away exactly the correlation that makes the naive interval wrong.

    Per-game aggregates are precomputed so an iteration is a sum over games,
    not over rows -- the whole bootstrap is then cheap enough to run at
    research scale.
    """
    by_game = collections.defaultdict(lambda: {"n": 0, "yes": 0, "price": 0.0})
    for row in rows:
        game = by_game[row["gameId"] or row["ticker"]]
        game["n"] += 1
        game["yes"] += 1 if row["resolvedYes"] else 0
        game["price"] += row["price"]
    games = list(by_game.values())
    if len(games) < 2:
        return {"lo": None, "hi": None, "games": len(games),
                "note": "too few independent games to bootstrap"}

    rng = random.Random(seed)
    k = len(games)
    gaps = []
    for _ in range(iterations):
        n = yes = 0
        price = 0.0
        for _ in range(k):
            game = games[rng.randrange(k)]
            n += game["n"]
            yes += game["yes"]
            price += game["price"]
        if n:
            gaps.append(yes / n - price / n)
    if not gaps:
        return {"lo": None, "hi": None, "games": k}
    gaps.sort()
    # Two-sided bootstrap p-value: how often the resampled gap lands on the
    # other side of zero. Needed because a dozen families are being tested at
    # once, and an interval that excludes zero says nothing on its own once
    # you have looked at twelve of them.
    below = sum(1 for g in gaps if g <= 0)
    above = sum(1 for g in gaps if g >= 0)
    p_value = min(1.0, 2.0 * min(below, above) / len(gaps))
    return {
        "lo": round(gaps[int(0.025 * (len(gaps) - 1))], 4),
        "hi": round(gaps[int(0.975 * (len(gaps) - 1))], 4),
        "games": k,
        "iterations": len(gaps),
        "pValue": round(p_value, 4),
    }


def summarize(rows, *, bootstrap=True):
    n = len(rows)
    if not n:
        return None
    yes = sum(1 for r in rows if r["resolvedYes"])
    mean_price = sum(r["price"] for r in rows) / n
    gap = yes / n - mean_price
    lo, hi = wilson(yes, n)
    eps = 1e-15
    brier = sum((r["price"] - (1.0 if r["resolvedYes"] else 0.0)) ** 2 for r in rows) / n
    logloss = -sum(
        math.log(max(eps, r["price"])) if r["resolvedYes"]
        else math.log(max(eps, 1.0 - r["price"])) for r in rows) / n

    out = {
        "n": n,
        "independentGames": len({r["gameId"] for r in rows if r["gameId"]}),
        "meanExecutablePrice": round(mean_price, 4),
        "observedYesRate": round(yes / n, 4),
        "calibrationGap": round(gap, 4),
        "brier": round(brier, 5),
        "logLoss": round(logloss, 5),
        # The naive view, kept only so the comparison is visible.
        "naiveWilson95": {"lo": lo, "hi": hi,
                          "marketWithinInterval": bool(lo is not None and lo <= mean_price <= hi)},
    }
    if bootstrap:
        boot = clustered_bootstrap_gap(rows)
        out["clusteredBootstrap95GapCI"] = boot
        out["gapSignificantUnderClustering"] = bool(
            boot.get("lo") is not None and (boot["lo"] > 0 or boot["hi"] < 0))
        out["rowsPerIndependentGame"] = (
            round(n / out["independentGames"], 1) if out["independentGames"] else None)
    return out


def load_rows(audit_path, stamps):
    kept = []
    excluded = collections.Counter()
    opener = gzip.open if audit_path.endswith(".gz") else open
    with opener(audit_path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("settlementStatus") != "SETTLED":
                excluded["not_settled"] += 1
                continue
            if r.get("settlementResult") not in ("YES", "NO"):
                excluded["settled_without_yes_no_result"] += 1
                continue
            if r.get("secondsBeforeStart") is None:
                excluded["no_valid_prestart_quote"] += 1
                continue
            price = r.get("lastPrestartYesAsk")
            if price is None:
                excluded["no_executable_yes_ask"] += 1
                continue
            if not (0.0 < price < 1.0):
                # 0 and 1 are not tradable probabilities; they are an absent
                # or already-resolved book. Excluded, never clamped.
                excluded["price_not_strictly_between_0_and_1"] += 1
                continue
            kept.append({
                "ticker": r["ticker"],
                "gameId": r.get("gameId"),
                "gameDate": r.get("gameDate"),
                "marketFamily": r.get("marketFamily") or "UNKNOWN",
                "price": float(price),
                "resolvedYes": r["settlementResult"] == "YES",
                "secondsBeforeStart": r["secondsBeforeStart"],
                "coverageClass": r.get("coverageClass"),
                "sourceCaptureClass": classify_quote_source(r.get("lastPrestartAt"), stamps),
            })
    return kept, excluded


def benjamini_hochberg(p_by_key, alpha=0.05):
    """
    Control the false discovery rate across families.

    A dozen families are tested at once. At 95%% you expect roughly one
    interval to exclude zero by chance alone, so "the CI excludes zero" is
    not a finding until multiplicity is accounted for. BH is used rather than
    Bonferroni because the aim is to keep the proportion of false claims low,
    not to make any single claim near-impossible.
    """
    items = sorted(((k, v) for k, v in p_by_key.items() if v is not None),
                   key=lambda kv: kv[1])
    m = len(items)
    survivors = set()
    for rank, (key, p) in enumerate(items, start=1):
        if p <= alpha * rank / m:
            survivors = {k for k, _ in items[:rank]}      # BH: everything up to the largest passing rank
    return {
        "alpha": alpha,
        "familiesTested": m,
        "survivors": sorted(survivors),
        "thresholds": {k: round(alpha * i / m, 5) for i, (k, _) in enumerate(items, start=1)},
    }


def family_table(rows, minimum_n=200):
    out = {}
    by_family = collections.defaultdict(list)
    for row in rows:
        by_family[row["marketFamily"]].append(row)
    for family, family_rows in sorted(by_family.items()):
        if len(family_rows) < minimum_n:
            continue
        out[family] = summarize(family_rows)

    # Multiplicity control across every family tested in THIS table.
    correction = benjamini_hochberg({
        family: (stat.get("clusteredBootstrap95GapCI") or {}).get("pValue")
        for family, stat in out.items()})
    for family, stat in out.items():
        stat["survivesMultiplicityCorrection"] = family in correction["survivors"]
        stat["finding"] = (
            "CANDIDATE_FOR_HOLDOUT" if stat["survivesMultiplicityCorrection"]
            else "EXPLORATORY" if stat.get("gapSignificantUnderClustering")
            else "NO_EVIDENCE")
    out["_multiplicityCorrection"] = correction
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit-rows", default=AUDIT_ROWS)
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()

    stamps = load_capture_classes(args.ledger)
    rows, excluded = load_rows(args.audit_rows, stamps)

    by_class = collections.Counter(r["sourceCaptureClass"] for r in rows)
    qualified = [r for r in rows if r["sourceCaptureClass"] != PARTIAL]
    partial_only = [r for r in rows if r["sourceCaptureClass"] == PARTIAL]

    report = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.datetime.now(datetime.timezone.utc)
                        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sourceCompleteness": {
            "rowsByCaptureClass": dict(by_class.most_common()),
            "completeArmExists": by_class.get(COMPLETE, 0) > 0,
            "whyNoCompleteArm": (
                "Zero historical captures can prove completeness. The pre-v4 path "
                "discarded live cursors at the page cap with no failure recorded, so a "
                "clean legacy snapshot is UNKNOWN_LEGACY, never COMPLETE. Building a "
                "COMPLETE arm out of legacy data would be precisely the laundering this "
                "exercise exists to prevent."),
        },
        "excludedFromResearch": dict(excluded.most_common()),
        "arms": {
            # The headline. Drops every row whose defining quote came from a
            # capture we KNOW was truncated.
            "qualified_excluding_partial_captures": summarize(qualified),
            # #233's population, reproduced so the comparison is visible.
            "unqualified_all_evidence": summarize(rows),
            # What the excluded evidence looked like on its own.
            "partial_capture_evidence_only": summarize(partial_only),
        },
        "familiesQualified": family_table(qualified),
        "familiesUnqualified": family_table(rows),
        "method": {
            "clustering": (
                "95%% intervals on the calibration gap come from a bootstrap that "
                "resamples whole GAMES with replacement (%d iterations, seed %d). A "
                "game is the unit that is actually independent: every prop, team total "
                "and game total on it shares one outcome."
                % (BOOTSTRAP_ITERATIONS, BOOTSTRAP_SEED)),
            "naiveIntervalIsReportedOnlyForComparison": (
                "Wilson intervals assume n independent trials. At ~145 rows per game "
                "they are far too narrow, and a cell falling outside one is not "
                "evidence of miscalibration."),
            "executablePricing": (
                "Buying YES pays the ASK. A calibration gap is not a profit: it is "
                "before the spread and before Kalshi fees."),
        },
    }

    os.makedirs(args.out_dir, exist_ok=True)
    out = os.path.join(args.out_dir, "full_universe_qualified_calibration.json")
    with open(out, "w") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)

    print(json.dumps({"sourceCompleteness": report["sourceCompleteness"],
                      "arms": report["arms"]}, indent=2, sort_keys=True))
    print("\n-> %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
