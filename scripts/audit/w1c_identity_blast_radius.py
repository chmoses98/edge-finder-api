#!/usr/bin/env python3
"""
scripts/audit/w1c_identity_blast_radius.py
==========================================
WAVE 1, subwave C. What does canonical market identity actually change?

Runs THE REAL PRODUCTION DECISION PATH twice on identical frozen inputs:

    scripts/merge_odds.py  ->  scripts/build_market_ledger.py

once from the pre-W1-C ref and once from the W1-C ref, then diffs the
resulting market-ledger rows. Same construction as
`w1b2_cutover_blast_radius.py` and for the same reason: a shadow engine that
re-implements the gates would be measuring the shadow engine.

WHAT THIS ONE MEASURES THAT B2's DID NOT
----------------------------------------
B2 asked "did the price change". W1-C asks "did we change our mind about WHICH
CONTRACT this is", so every difference is attributed to one of three causes and
they are reported separately because they carry completely different meanings:

  identity  the contract, its family/horizon/side, or the physical game it
            belongs to changed -- or could no longer be proven at all. This is
            what W1-C is FOR, and a refusal here is a success, not a loss.

  price     identity is proven and unchanged in both arms, but the executable
            price moved. W1-C touches no pricing code, so anything landing
            here is a finding to explain, not an expected result.

  model     `modelProb` moved. W1-C touches no model, calibration, threshold,
            staking or bankroll code, so this bucket MUST be empty. It exists
            precisely so that claim is measured rather than asserted.

It also checks, in BOTH arms, the invariant that produced CR-3: a Kalshi
contract belongs to exactly one physical game. The before/after counts of
tickers claimed by two or more gamePks are the direct measurement of whether
the defect is gone.

`--decision-at` fixes the instant both arms age quotes against, so the run is
reproducible and the arms are compared on equal terms. It is a clock injection
and cannot change how any price or identity is derived.

READ-ONLY: both arms run in private git worktrees over private copies of the
data tree. The repository the caller sits in is never used as a working
directory, and nothing is written back to it.
"""

import argparse
import collections
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.edgelab import market_identity as mi                      # noqa: E402
from scripts.audit.w1b2_cutover_blast_radius import (              # noqa: E402
    LEDGER_SCRIPTS, _ledger_rows, _prepare, _run_production_path,
)

# The identity fields whose disagreement between arms means "we changed our
# mind about which contract this row is". `line` is deliberately absent: it is
# a display copy of `threshold`, and counting both would double-count one fact.
IDENTITY_FIELDS = (
    "marketTicker", "seriesTicker", "physicalGameKey", "marketFamily",
    "marketHorizon", "selection", "direction", "threshold", "contractSide",
)


def _actionable(row):
    """Production's own verdict: a row is actionable iff it carries a tier."""
    return bool(row and row.get("confidence"))


def _identity_proven(row):
    return (row or {}).get("identityStatus") == mi.IDENTITY_PROVEN


def _identity_view(row):
    return {f: (row or {}).get(f) for f in IDENTITY_FIELDS}


def _exclusivity(rows):
    """Tickers claimed by more than one physical game, in one arm's ledger.

    Falls back to the arm's own game key when `physicalGameKey` is absent,
    which is the case for the pre-W1-C arm -- it has no such field, and using
    the row's game is exactly the claim being tested.
    """
    pairs = []
    for row in rows:
        ticker = row.get("marketTicker") or row.get("ticker")
        game = row.get("physicalGameKey") or row.get("_game")
        if ticker and game:
            pairs.append((ticker, game))
    return mi.assert_ticker_exclusivity(pairs)


def compare(before_rows, after_rows):
    key = lambda r: (r["_game"], r.get("market"))                   # noqa: E731
    before = {key(r): r for r in before_rows}
    after = {key(r): r for r in after_rows}

    totals = collections.Counter()
    families = collections.defaultdict(collections.Counter)
    causes = collections.Counter()
    refusal_reasons = collections.Counter()
    identity_changes, model_moves, price_moves = [], [], []
    unproven_actionable = []

    for k in sorted(set(before) | set(after)):
        brow, arow = before.get(k), after.get(k)
        family = k[1] or "UNKNOWN"
        totals["candidates"] += 1
        families[family]["candidates"] += 1

        ba, aa = _actionable(brow), _actionable(arow)
        totals["beforeActionable"] += ba
        totals["afterActionable"] += aa
        families[family]["beforeActionable"] += ba
        families[family]["afterActionable"] += aa

        # ── cause attribution ────────────────────────────────────────────
        bid_view, aid_view = _identity_view(brow), _identity_view(arow)
        identity_moved = bid_view != aid_view
        status = (arow or {}).get("identityStatus")

        bp = (brow or {}).get("executablePriceUsed")
        ap = (arow or {}).get("executablePriceUsed")
        price_moved = (bp != ap)

        bm = (brow or {}).get("modelProb")
        am = (arow or {}).get("modelProb")
        model_moved = (bm != am)

        if model_moved:
            totals["modelMoved"] += 1
            model_moves.append({"game": k[0], "market": family,
                                "beforeModelProb": bm, "afterModelProb": am})
        if identity_moved:
            totals["identityMoved"] += 1
            families[family]["identityMoved"] += 1
            identity_changes.append({
                "game": k[0], "market": family,
                "before": bid_view, "after": aid_view,
                "identityStatus": status,
                "identityMissing": (arow or {}).get("identityMissing"),
            })
        if price_moved:
            totals["priceMoved"] += 1
            price_moves.append({"game": k[0], "market": family,
                                "beforePriceCents": bp, "afterPriceCents": ap,
                                "identityStatus": status})

        # ── verdict changes, attributed ──────────────────────────────────
        if ba and not aa:
            totals["betToPass"] += 1
            families[family]["betToPass"] += 1
            if not _identity_proven(arow):
                cause = "identity"
                refusal_reasons["%s :: %s" % (family, status or "(absent)")] += 1
            elif model_moved:
                cause = "model"
            elif price_moved:
                cause = "price"
            else:
                cause = "unattributed"
            causes[cause] += 1
        elif aa and not ba:
            totals["passToBet"] += 1
            families[family]["passToBet"] += 1
            causes["gained:%s" % ("identity" if identity_moved else "price")] += 1

        # ── the guarantee, checked rather than asserted ──────────────────
        if aa and not _identity_proven(arow):
            unproven_actionable.append({
                "game": k[0], "market": family,
                "identityStatus": status,
                "confidence": (arow or {}).get("confidence"),
            })

    after_status = collections.Counter(
        r.get("identityStatus") or "(absent)" for r in after_rows)
    before_status = collections.Counter(
        r.get("identityStatus") or "(absent)" for r in before_rows)

    before_violations = _exclusivity(before_rows)
    after_violations = _exclusivity(after_rows)

    return {
        "totals": dict(totals),
        "verdictChangeCause": dict(causes),
        "identityRefusalReasons": dict(refusal_reasons),
        "identityStatusBefore": dict(before_status),
        "identityStatusAfter": dict(after_status),
        "familyBreakdown": {f: dict(c) for f, c in families.items()},
        "tickerExclusivity": {
            "beforeViolationCount": len(before_violations),
            "afterViolationCount": len(after_violations),
            "beforeViolations": before_violations[:25],
            "afterViolations": after_violations[:25],
        },
        # The three headline guarantees, each as a boolean backed by the list
        # that would falsify it.
        "guarantees": {
            "noModelChange": not model_moves,
            "noUnprovenIdentityActionable": not unproven_actionable,
            "tickerExclusivityHolds": not after_violations,
        },
        "modelMoves": model_moves[:25],
        "unprovenActionableRows": unproven_actionable[:25],
        "identityChanges": identity_changes[:50],
        "priceMoves": price_moves[:25],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="W1-C canonical identity production blast radius")
    parser.add_argument("--before-ref", required=True,
                        help="pre-W1-C ref (normally origin/main)")
    parser.add_argument("--after-ref", required=True, help="W1-C ref")
    parser.add_argument("--decision-at", required=True,
                        help="fixed decision instant both arms age quotes against")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--data-root", default=ROOT)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    os.makedirs(args.workdir, exist_ok=True)
    arms = {}
    for tag, ref in (("before", args.before_ref), ("after", args.after_ref)):
        tree = _prepare(ref, tag, args.workdir, args.data_root)
        arms[tag] = {"ref": ref, "tree": tree,
                     "runs": _run_production_path(tree, args.decision_at),
                     "rows": _ledger_rows(tree)}
        print("[%s] ref=%s ledger rows=%d" % (tag, ref, len(arms[tag]["rows"])))

    result = compare(arms["before"]["rows"], arms["after"]["rows"])
    payload = {
        "reportVersion": "w1c-identity-blast-radius-1",
        "beforeRef": args.before_ref,
        "afterRef": args.after_ref,
        "decisionAt": args.decision_at,
        "productionScripts": list(LEDGER_SCRIPTS),
        "modelDrivenRealMoneyAuthority": "OFF",
        "recommendationAuthorityExercised": False,
        **result,
    }

    totals = payload["totals"]
    print("\nW1-C IDENTITY BLAST RADIUS (real production path)")
    for label, field in (("candidates", "candidates"),
                         ("before actionable", "beforeActionable"),
                         ("after actionable", "afterActionable"),
                         ("BET -> PASS", "betToPass"),
                         ("PASS -> BET", "passToBet"),
                         ("identity fields moved", "identityMoved"),
                         ("executable price moved", "priceMoved"),
                         ("model prob moved", "modelMoved")):
        print("  %-24s %s" % (label, totals.get(field, 0)))
    print("  cause of BET->PASS      %s" % (payload["verdictChangeCause"] or "{}"))
    excl = payload["tickerExclusivity"]
    print("  ticker exclusivity      %d violation(s) before -> %d after"
          % (excl["beforeViolationCount"], excl["afterViolationCount"]))
    for name, ok in payload["guarantees"].items():
        print("  %-24s %s" % (name, "HOLDS" if ok else "VIOLATED"))

    if args.out:
        with open(args.out, "w") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        print("\nwrote %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
