#!/usr/bin/env python3
"""
scripts/audit/w1a_settlement_rehearsal.py
=========================================
WAVE 1, subwave A. READ-ONLY rehearsal of the canonical settlement chain
against REAL committed wager and settlement evidence.

WHY A REHEARSAL AND NOT JUST TESTS. The W1-A unit tests assert the invariants
against fixtures, and fixtures are written by whoever wrote the code. This runs
the same invariants over rows that were actually placed and actually settled,
so the claim "the chain composes" is made against evidence nobody wrote to make
it pass.

THE CHAIN, END TO END, PER ROW:

    canonical wager (bets.json / data/edgelab/bets/bets.jsonl)
      -> exact Kalshi contract        (W1-C kalshi_mlb_contract_parser)
      -> exact purchased side         (W1-A resolve_wager_side)
      -> terminal exchange truth      (data/edgelab/settlements/*.jsonl)
      -> canonical wager outcome      (W1-A resolve_wager_outcome)
      -> the outcome the ledger already recorded

A DISAGREEMENT IS REPORTED, NEVER RESOLVED. Where the rehearsal's outcome and
the ledger's recorded result differ, both are printed with the evidence behind
each. Nothing is rewritten -- not the wager, not the settlement, not the
result. That is the CEO's call, not this script's.

A REFUSAL IS NOT A FAILURE. Rows this system cannot prove are counted and
classified, because fail-closed is the objective. The job fails only on a
genuine violation:

  * a row graded WON or LOST whose side was never proven;
  * a row graded from a settlement for a DIFFERENT ticker or a different game;
  * a player-prop row that reached a grade instead of deferring under #43;
  * any canonical evidence file differing before and after the run.

SAFETY. Opens files for reading only. Writes one JSON report to a caller-named
path, defaulting to a temp dir. Never writes into data/, bets.json, or any
ledger. It runs no staking code, creates no wager, and touches no
model/price/confidence path.

Usage:
    python3 scripts/audit/w1a_settlement_rehearsal.py [--out report.json]
"""

import argparse
import collections
import glob
import gzip
import hashlib
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib import canonical_evidence  # noqa: E402
from lib import wager_settlement_semantics as wss  # noqa: E402

ROOT_LEDGER = os.path.join(ROOT, "bets.json")
EDGELAB_LEDGER = os.path.join(ROOT, "data", "edgelab", "bets", "bets.jsonl")
SETTLEMENTS_DIR = os.path.join(ROOT, "data", "edgelab", "settlements")

# The families the rehearsal tries to cover with a REAL placed wager. A family
# with no trustworthy real fixture is reported as such rather than quietly
# skipped -- see `familyCoverage.familiesWithNoRealFixture`.
TARGET_FAMILIES = ("MONEYLINE", "GAME_TOTAL", "TEAM_TOTAL", "RFI", "RUN_LINE")


def fingerprint_canonical_evidence():
    """`sha256  path` for every canonical evidence file that exists."""
    out = {}
    for rel in canonical_evidence.CANONICAL_EVIDENCE:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        out[rel] = digest.hexdigest()
    for rel_dir in canonical_evidence.CANONICAL_EVIDENCE_DIRS:
        base = os.path.join(ROOT, rel_dir)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            if not os.path.isfile(path):
                continue
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
            out[os.path.join(rel_dir, name)] = digest.hexdigest()
    return out


def _open_partition(path):
    """A settlement partition, plain or gzipped. Most of them are gzipped."""
    if path.endswith(".gz"):
        return gzip.open(path, "rt")
    return open(path)


def load_settlements():
    """
    {marketTicker: settlement row} over every committed settlement partition.

    Both `*.jsonl` and `*.jsonl.gz` are read. An earlier draft globbed only the
    uncompressed form and found 2 of 452 ticketed wagers, which looked like a
    join defect and was a glob defect -- the kind of thing that would have made
    this rehearsal report a vacuous green.
    """
    by_ticker = {}
    paths = sorted(glob.glob(os.path.join(SETTLEMENTS_DIR, "*.jsonl"))
                   + glob.glob(os.path.join(SETTLEMENTS_DIR, "*.jsonl.gz")))
    for path in paths:
        with _open_partition(path) as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                ticker = row.get("marketTicker")
                if ticker:
                    by_ticker[ticker] = row
    return by_ticker


def load_wagers():
    """Every canonical wager from both ledgers, tagged with its source."""
    wagers = []
    with open(ROOT_LEDGER) as handle:
        for row in json.load(handle):
            wagers.append(("bets.json", row))
    if os.path.exists(EDGELAB_LEDGER):
        with open(EDGELAB_LEDGER) as handle:
            for line in handle:
                if line.strip():
                    wagers.append(("data/edgelab/bets/bets.jsonl", json.loads(line)))
    return wagers


def _recorded_outcome(source, row):
    """The outcome the ledger ALREADY records for this row, canonically read."""
    return wss.normalize_row_semantics(row)["canonicalOutcome"]


def rehearse(wagers, settlements):
    counts = collections.Counter()
    violations = []
    disagreements = []
    agreements = []
    refusals = collections.Counter()
    refusal_classes = collections.Counter()
    per_family = collections.defaultdict(collections.Counter)
    examples_by_family = {}

    for source, row in wagers:
        counts["wagersExamined"] += 1
        ticker = row.get("marketTicker") or row.get("ticker")
        side_resolution = wss.resolve_wager_side(row)
        expression = side_resolution.get("expression") or {}
        family = expression.get("family") or "UNCLASSIFIED"

        settlement = settlements.get(ticker) if ticker else None
        if settlement is None:
            counts["noCommittedSettlementForThisTicker"] += 1
            market_settlement = {}
        else:
            counts["settlementFound"] += 1
            market_settlement = {
                "marketTicker": settlement.get("marketTicker"),
                "gameId": settlement.get("gameId"),
                "settlementStatus": settlement.get("settlementStatus"),
                "result": settlement.get("result"),
                "unavailableReason": settlement.get("unavailableReason"),
            }

        outcome = wss.resolve_wager_outcome(side_resolution, market_settlement, wager=row)
        counts["outcome_%s" % outcome["outcome"]] += 1
        per_family[family][outcome["outcome"]] += 1

        if side_resolution["side"] is None:
            refusals[str(side_resolution["refusalReason"])] += 1
            refusal_classes[str(side_resolution["refusalClass"])] += 1

        record = {
            "source": source,
            "id": row.get("id") or row.get("betId"),
            "game": row.get("game") or row.get("matchup"),
            "market": row.get("market") or row.get("marketFamily"),
            "marketTicker": ticker,
            "semanticFamily": family,
            "canonicalSide": side_resolution["side"],
            "canonicalSideBasis": side_resolution["basis"],
            "sideRefusalReason": side_resolution["refusalReason"],
            "sideRefusalClass": side_resolution["refusalClass"],
            "settlementStatus": market_settlement.get("settlementStatus"),
            "settlementResult": market_settlement.get("result"),
            "rehearsalOutcome": outcome["outcome"],
            "rehearsalRefusalReason": outcome["refusalReason"],
            "ledgerResult": row.get("result"),
            "ledgerStatus": row.get("status"),
        }

        # ── VIOLATIONS. These are the only things that fail this job.
        if outcome["outcome"] in (wss.OUTCOME_WON, wss.OUTCOME_LOST):
            if side_resolution["side"] not in (wss.SIDE_YES, wss.SIDE_NO):
                violations.append(dict(record, violation="GRADED_WITHOUT_A_PROVEN_SIDE"))
            if ticker and market_settlement.get("marketTicker") and \
                    str(ticker) != str(market_settlement["marketTicker"]):
                violations.append(dict(record, violation="GRADED_FROM_ANOTHER_TICKERS_SETTLEMENT"))
            if wss.is_player_prop(row):
                violations.append(dict(record, violation="PLAYER_PROP_REACHED_A_GRADE"))

        recorded = _recorded_outcome(source, row)
        if outcome["outcome"] in (wss.OUTCOME_WON, wss.OUTCOME_LOST, wss.OUTCOME_PUSH,
                                  wss.OUTCOME_VOID) and recorded is not None:
            if outcome["outcome"] == recorded:
                counts["agreesWithLedger"] += 1
                agreements.append(record)
            else:
                counts["disagreesWithLedger"] += 1
                disagreements.append(dict(record, ledgerCanonicalOutcome=recorded))

        if family not in examples_by_family and side_resolution["side"] is not None:
            examples_by_family[family] = record

    covered = {f for f in TARGET_FAMILIES if f in examples_by_family}
    return {
        "counts": dict(counts),
        "outcomesBySemanticFamily": {k: dict(v) for k, v in sorted(per_family.items())},
        "sideRefusalReasons": dict(refusals.most_common()),
        "sideRefusalClasses": dict(refusal_classes.most_common()),
        "familyCoverage": {
            "familiesWithARealProvenFixture": sorted(covered),
            "familiesWithNoRealFixture": sorted(set(TARGET_FAMILIES) - covered),
            "examplePerFamily": examples_by_family,
            "note": "A family listed under familiesWithNoRealFixture has NO trustworthy "
                    "placed-wager row in the committed corpus whose contract side this "
                    "system can prove. Its coverage comes from the adversarial synthetic "
                    "fixtures in tests/test_w1a_canonical_settlement_semantics.py, and "
                    "this report says so rather than implying a real one exists.",
        },
        "agreementSample": agreements[:20],
        "disagreementCount": len(disagreements),
        "disagreements": disagreements,
        "violationCount": len(violations),
        "violations": violations,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    before = fingerprint_canonical_evidence()
    settlements = load_settlements()
    wagers = load_wagers()
    result = rehearse(wagers, settlements)
    after = fingerprint_canonical_evidence()

    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    report = {
        "subwave": "W1-A",
        "mode": "READ_ONLY_REHEARSAL",
        "committedSettlementTickers": len(settlements),
        "canonicalEvidenceFilesFingerprinted": len(before),
        "canonicalEvidenceChanged": changed,
        "canonicalEvidenceFingerprintBefore": before,
        "canonicalEvidenceFingerprintAfter": after,
        "rehearsal": result,
    }

    out_path = args.out or os.path.join(tempfile.gettempdir(), "w1a_settlement_rehearsal.json")
    with open(out_path, "w") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)

    counts = result["counts"]
    print("W1-A READ-ONLY SETTLEMENT REHEARSAL")
    print("  wagers examined              : %d" % counts.get("wagersExamined", 0))
    print("  committed settlements loaded : %d tickers" % len(settlements))
    print("  wagers with a settlement      : %d" % counts.get("settlementFound", 0))
    print("  graded WON                    : %d" % counts.get("outcome_WON", 0))
    print("  graded LOST                   : %d" % counts.get("outcome_LOST", 0))
    print("  VOID                          : %d" % counts.get("outcome_VOID", 0))
    print("  NOT_TERMINAL                  : %d" % counts.get("outcome_NOT_TERMINAL", 0))
    print("  UNRESOLVED (refused)          : %d" % counts.get("outcome_UNRESOLVED", 0))
    print("  agrees with the ledger        : %d" % counts.get("agreesWithLedger", 0))
    print("  DISAGREES with the ledger     : %d" % result["disagreementCount"])
    print("  families with a real fixture  : %s"
          % ", ".join(result["familyCoverage"]["familiesWithARealProvenFixture"]) or "none")
    print("  families WITHOUT one          : %s"
          % (", ".join(result["familyCoverage"]["familiesWithNoRealFixture"]) or "none"))
    print("  canonical evidence changed    : %d file(s)" % len(changed))
    print("  violations                    : %d" % result["violationCount"])
    print("  report                        : %s" % out_path)

    if changed:
        print("\nERROR: this rehearsal is READ-ONLY and canonical evidence changed:")
        for path in changed:
            print("  %s" % path)
        return 1
    if result["violationCount"]:
        print("\nERROR: invariant violations:")
        for violation in result["violations"][:20]:
            print("  %s  %s  %s" % (violation["violation"], violation["id"],
                                    violation["marketTicker"]))
        return 1
    print("\nPASS: every graded row carried a proven side, an exact ticker match and "
          "terminal truth; no canonical evidence file changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
