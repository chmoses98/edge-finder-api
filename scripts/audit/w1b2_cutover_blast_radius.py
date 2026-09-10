#!/usr/bin/env python3
"""
scripts/audit/w1b2_cutover_blast_radius.py
==========================================
WAVE 1, subwave B2. What does the executable-price cutover actually change?

Runs THE REAL PRODUCTION DECISION PATH twice on identical frozen inputs:

    scripts/merge_odds.py  ->  scripts/build_market_ledger.py

once from the legacy ref and once from the B2 ref, then diffs the resulting
market-ledger rows. There is no shadow qualification engine here and no
re-implemented edge maths -- B1's report used a seam that called production's
functions, and this one goes further by running production's actual scripts, so
the comparison includes every gate, cap and ceiling exactly as production
applies them.

WHY BOTH ARMS RUN IN COPIES
---------------------------
merge_odds.py has no `if __name__ == '__main__'` guard: importing or executing
it writes data/slate.json immediately. Each arm therefore gets its own git
worktree and its own copy of the data tree, and the repository the caller is
sitting in is never used as a working directory.

WHAT IT MEASURES
----------------
Per market family, and split by CAUSE, because "we lost coverage" and "the
price moved" are different facts and only one of them is a defect:

  * price-driven   -- a genuine ask replaced a midpoint and the edge moved
  * identity-driven-- the contract or the side could not be proven
  * freshness-driven-- the quote was too old for production authority

`--decision-at` fixes the instant both arms age quotes against, so the run is
reproducible and the two arms are compared on equal terms. It is a clock
injection and cannot change how any price is derived.
"""

import argparse
import collections
import json
import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))

LEDGER_SCRIPTS = ("scripts/merge_odds.py", "scripts/build_market_ledger.py")


def _prepare(ref, tag, workdir, data_root):
    """A git worktree at `ref` with a private copy of the data tree."""
    tree = os.path.join(workdir, "wt_" + tag)
    subprocess.run(["git", "worktree", "remove", "--force", tree],
                   cwd=ROOT, capture_output=True)
    if os.path.exists(tree):
        shutil.rmtree(tree)
    subprocess.run(["git", "worktree", "add", "--detach", tree, ref],
                   cwd=ROOT, check=True, capture_output=True)
    dst = os.path.join(tree, "data")
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(os.path.join(data_root, "data"), dst, symlinks=True)
    return tree


def _run_production_path(tree, decision_at):
    env = dict(os.environ, W1_B2_DECISION_AT=decision_at, PYTHONPATH=tree)
    results = {}
    for script in LEDGER_SCRIPTS:
        proc = subprocess.run([sys.executable, script], cwd=tree, env=env,
                              capture_output=True, text=True, timeout=1800)
        results[script] = {"returncode": proc.returncode,
                           "stderr_tail": proc.stderr[-2000:]}
        if proc.returncode != 0:
            raise SystemExit("%s failed in %s:\n%s" % (script, tree, proc.stderr[-3000:]))
    return results


def _ledger_rows(tree):
    pipeline = os.path.join(tree, "data", "pipeline")
    if not os.path.isdir(pipeline):
        return []
    dates = sorted(os.listdir(pipeline))
    if not dates:
        return []
    path = os.path.join(pipeline, dates[-1], "recommendations.json")
    if not os.path.exists(path):
        return []
    with open(path) as handle:
        doc = json.load(handle)
    rows = []
    for game in (doc.get("data", {}).get("games") or []):
        game_id = str(game.get("gameId")
                      or "%s@%s" % (game.get("away"), game.get("home")))
        for row in (game.get("marketLedger") or []):
            rows.append(dict(row, _game=game_id))
    return rows


def _actionable(row):
    """Production's own verdict: a row is actionable iff it carries a tier."""
    return bool(row and row.get("confidence"))


def _cause(refusal_reason):
    if not refusal_reason:
        return "price/book"
    if "TOO_OLD" in refusal_reason:
        return "freshness"
    if "SIDE_NOT_PROVEN" in refusal_reason or "CONTRACT_NOT_IDENTIFIED" in refusal_reason:
        return "identity/side"
    return "price/book"


def compare(legacy_rows, b2_rows):
    key = lambda r: (r["_game"], r.get("market"))          # noqa: E731
    legacy = {key(r): r for r in legacy_rows}
    b2 = {key(r): r for r in b2_rows}

    totals = collections.Counter()
    families = collections.defaultdict(collections.Counter)
    causes = collections.Counter()
    refusals = collections.Counter()
    price_moves, edge_moves, verdict_changes = [], [], []

    for k in sorted(set(legacy) | set(b2)):
        lrow, brow = legacy.get(k), b2.get(k)
        family = k[1] or "UNKNOWN"
        totals["candidates"] += 1
        families[family]["candidates"] += 1

        la, ba = _actionable(lrow), _actionable(brow)
        totals["legacyActionable"] += la
        totals["b2Actionable"] += ba
        families[family]["legacyActionable"] += la
        families[family]["b2Actionable"] += ba
        if la and not ba:
            totals["betToPass"] += 1
            families[family]["betToPass"] += 1
            verdict_changes.append({"game": k[0], "market": family,
                                    "direction": "BET->PASS",
                                    "legacyConfidence": (lrow or {}).get("confidence"),
                                    "b2Confidence": (brow or {}).get("confidence"),
                                    "legacyPrice": (lrow or {}).get("executablePriceUsed"),
                                    "b2Price": (brow or {}).get("executablePriceUsed"),
                                    "refusalReason": (brow or {}).get("priceRefusalReason")})
        elif ba and not la:
            totals["passToBet"] += 1
            families[family]["passToBet"] += 1
            verdict_changes.append({"game": k[0], "market": family,
                                    "direction": "PASS->BET"})

        if (lrow or {}).get("confidence") != (brow or {}).get("confidence"):
            totals["confidenceChanged"] += 1
            families[family]["confidenceChanged"] += 1
        if (lrow or {}).get("betUpToPriceNet") != (brow or {}).get("betUpToPriceNet"):
            totals["betUpToChanged"] += 1
            families[family]["betUpToChanged"] += 1

        lp = (lrow or {}).get("executablePriceUsed")
        bp = (brow or {}).get("executablePriceUsed")
        reason = (brow or {}).get("priceRefusalReason")
        if lp is not None and bp is None:
            totals["priceSuppressed"] += 1
            families[family]["priceSuppressed"] += 1
            causes[_cause(reason)] += 1
            refusals["%s :: %s" % (family, reason or "(unrecorded)")] += 1
        if lp is None and bp is not None:
            totals["priceGained"] += 1
            families[family]["priceGained"] += 1
        if lp is not None and bp is not None and abs(float(bp) - float(lp)) > 1e-9:
            price_moves.append({"game": k[0], "market": family,
                                "legacyPriceCents": lp, "b2PriceCents": bp,
                                "deltaCents": round(float(bp) - float(lp), 2),
                                "bookYesBid": (brow or {}).get("bookYesBid"),
                                "bookYesAsk": (brow or {}).get("bookYesAsk"),
                                "bookState": (brow or {}).get("bookState"),
                                "quoteAgeSeconds": (brow or {}).get("quoteAgeSeconds")})
        le = (lrow or {}).get("netExecutableEdge")
        be = (brow or {}).get("netExecutableEdge")
        if le is not None and be is not None and abs(float(be) - float(le)) > 1e-9:
            edge_moves.append({"game": k[0], "market": family,
                               "legacyNetEdge": le, "b2NetEdge": be,
                               "deltaPP": round(float(be) - float(le), 3)})

    price_moves.sort(key=lambda m: -abs(m["deltaCents"]))
    edge_moves.sort(key=lambda m: -abs(m["deltaPP"]))

    basis = collections.Counter(r.get("executablePriceBasis") for r in b2_rows
                                if r.get("executablePriceBasis"))
    book_states = collections.Counter(r.get("bookState") for r in b2_rows
                                      if r.get("bookState"))
    return {
        "totals": dict(totals),
        "suppressionCause": dict(causes),
        "refusalReasons": dict(refusals),
        "familyBreakdown": {f: dict(c) for f, c in families.items()},
        "priceBasisDistribution": dict(basis),
        "bookStateDistribution": dict(book_states),
        "largestPriceMoves": price_moves[:25],
        "largestEdgeMoves": edge_moves[:25],
        "verdictChanges": verdict_changes,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="W1-B2 production cutover blast radius")
    parser.add_argument("--legacy-ref", required=True)
    parser.add_argument("--b2-ref", required=True)
    parser.add_argument("--decision-at", required=True,
                        help="fixed decision instant both arms age quotes against")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--data-root", default=ROOT)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    os.makedirs(args.workdir, exist_ok=True)
    arms = {}
    for tag, ref in (("legacy", args.legacy_ref), ("b2", args.b2_ref)):
        tree = _prepare(ref, tag, args.workdir, args.data_root)
        arms[tag] = {"ref": ref, "tree": tree,
                     "runs": _run_production_path(tree, args.decision_at),
                     "rows": _ledger_rows(tree)}
        print("[%s] ref=%s ledger rows=%d" % (tag, ref, len(arms[tag]["rows"])))

    result = compare(arms["legacy"]["rows"], arms["b2"]["rows"])
    payload = {
        "reportVersion": "w1b2-cutover-blast-radius-1",
        "legacyRef": args.legacy_ref,
        "b2Ref": args.b2_ref,
        "decisionAt": args.decision_at,
        "productionScripts": list(LEDGER_SCRIPTS),
        "modelDrivenRealMoneyAuthority": "OFF",
        "recommendationAuthorityExercised": False,
        **result,
    }

    totals = payload["totals"]
    print("\nW1-B2 CUTOVER BLAST RADIUS (real production path)")
    for label, k in (("candidates", "candidates"),
                     ("legacy actionable", "legacyActionable"),
                     ("B2 actionable", "b2Actionable"),
                     ("BET -> PASS", "betToPass"),
                     ("PASS -> BET", "passToBet"),
                     ("confidence changed", "confidenceChanged"),
                     ("Bet Up To changed", "betUpToChanged"),
                     ("price suppressed", "priceSuppressed"),
                     ("price gained", "priceGained")):
        print("  %-22s %s" % (label, totals.get(k, 0)))
    print("  suppression cause      %s" % (payload["suppressionCause"] or {}))

    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
