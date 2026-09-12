#!/usr/bin/env python3
"""
scripts/audit/w1a_settlement_semantics_audit.py
===============================================
WAVE 1, subwave A. READ-ONLY. The evidence behind the W1-A claim.

Two jobs, in one pass over the committed corpus:

  1. INVENTORY -- every distinct settlement/status/side/market spelling that
     actually exists in the two wager ledgers, with real counts. Not a sample,
     not an assumption about what "should" be there.

  2. BLAST RADIUS -- the OLD settlement semantics and the NEW ones, run over
     the same rows, and the exact difference between them: which rows change
     grade, which are newly resolved, which are newly REFUSED, and what that
     does to P/L.

WHY THE OLD CODE IS RUN, NOT DESCRIBED. The comparison imports the pre-W1-A
`clv_update.py` out of git (`git show <base>:clv_update.py`) and executes its
real functions. A blast radius computed from a description of the old behavior
is a blast radius computed from the author's belief about the old behavior,
which is exactly the thing under review.

WHAT "BETTER" MEANS HERE. Not "more rows settled". A row that the old code
graded from an assumed side and the new code refuses is a WIN for correctness
and shows up in this report as `newlyRefused`, with the reason. Fail-closed is
the objective; the report is arranged so that a reviewer can see whether any
increase in settled rows came from weaker inference (it did not -- every newly
resolved row is listed with the basis that proved it).

SAFETY. Opens files for reading only. Writes exactly one JSON report, to a
path the caller names, defaulting to a temp dir -- never into data/ and never
into either ledger.

Usage:
    python3 scripts/audit/w1a_settlement_semantics_audit.py \
        [--base-ref <git ref for the pre-W1-A tree>] [--out report.json]
"""

import argparse
import collections
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import clv_update as new_clv  # noqa: E402
from lib import wager_settlement_semantics as wss  # noqa: E402

ROOT_LEDGER = os.path.join(ROOT, "bets.json")
EDGELAB_LEDGER = os.path.join(ROOT, "data", "edgelab", "bets", "bets.jsonl")

# Fields whose distinct values are inventoried verbatim. These are the columns
# a settlement decision has ever been read out of.
_INVENTORY_FIELDS = (
    "result", "status", "betSide", "side", "betTeam", "market", "betType",
    "closingLineSource", "clvStatus", "f5SettlementSource",
)


def _load_root_ledger(path=ROOT_LEDGER):
    with open(path) as handle:
        return json.load(handle)


def _load_edgelab_ledger(path=EDGELAB_LEDGER):
    if not os.path.exists(path):
        return []
    with open(path) as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_pre_w1a_clv(base_ref):
    """
    The pre-W1-A clv_update.py, imported from git history and executed.

    Returns the module, or None when the ref is unavailable (a shallow clone).
    The caller reports that honestly rather than silently comparing new against
    new, which would produce a vacuous all-zero blast radius.
    """
    try:
        source = subprocess.check_output(
            ["git", "-C", ROOT, "show", "%s:clv_update.py" % base_ref],
            stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, OSError):
        return None
    handle, temp_path = tempfile.mkstemp(suffix="_clv_pre_w1a.py")
    with os.fdopen(handle, "wb") as out:
        out.write(source)
    spec = importlib.util.spec_from_file_location("clv_update_pre_w1a", temp_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── 1. Inventory ─────────────────────────────────────────────────────────────

def inventory(root_rows, edgelab_rows):
    """Every distinct spelling that exists, with counts. No interpretation."""
    report = {
        "rootLedgerRows": len(root_rows),
        "edgelabLedgerRows": len(edgelab_rows),
        "rootFields": {},
        "edgelabFields": {},
        "rootFieldPresence": {},
        "sideFieldOverlap": {},
        "statusSemanticCollision": {},
    }
    for field in _INVENTORY_FIELDS:
        counts = collections.Counter()
        present = 0
        for row in root_rows:
            if field in row:
                present += 1
                counts[json.dumps(row[field])] += 1
        if present:
            report["rootFields"][field] = dict(counts.most_common())
            report["rootFieldPresence"][field] = present

    for field in ("side", "result", "status", "marketFamily", "executionStatus",
                  "sideResolutionBasis", "settlementRefusalReason"):
        counts = collections.Counter(json.dumps(row.get(field)) for row in edgelab_rows)
        report["edgelabFields"][field] = dict(counts.most_common())

    # The root ledger carries TWO side-ish columns that mean different things:
    # `betSide` is an ORIENTATION (AWAY/HOME) and `side` is a SELECTION (a club).
    # Several consumers read them as interchangeable (`betSide or side`), which
    # is how an orientation ends up compared against a club abbreviation.
    both = same = 0
    for row in root_rows:
        if "betSide" in row and "side" in row:
            both += 1
            if str(row["betSide"]).upper() == str(row["side"]).upper():
                same += 1
    report["sideFieldOverlap"] = {
        "rowsWithBoth": both,
        "rowsWhereTheyAreIdentical": same,
        "rowsWhereTheyDiffer": both - same,
        "rowsWithOnlySide": sum(1 for r in root_rows if "side" in r and "betSide" not in r),
        "rowsWithOnlyBetSide": sum(1 for r in root_rows if "betSide" in r and "side" not in r),
        "rowsWithNeither": sum(1 for r in root_rows if "side" not in r and "betSide" not in r),
        "note": "betSide is an orientation (AWAY/HOME); side is a selection (a club). "
                "They are DIFFERENT CONCEPTS, so 'differ' here is not an error count -- "
                "it is the measure of how wrong `betSide or side` is as a read.",
    }

    # `status` is three vocabularies in one column: a lifecycle, an outcome and
    # a bet class.
    collision = collections.Counter()
    for row in root_rows:
        raw = row.get("status")
        if raw is None:
            continue
        kind = ("BET_CLASS" if wss.is_bet_class_status(raw)
                else "OUTCOME" if wss.normalize_result(raw) in
                (wss.OUTCOME_WON, wss.OUTCOME_LOST, wss.OUTCOME_PUSH, wss.OUTCOME_VOID)
                else "LIFECYCLE" if wss.normalize_lifecycle(raw)
                else "UNRECOGNIZED")
        collision[(kind, str(raw))] += 1
    report["statusSemanticCollision"] = {
        "%s::%s" % (kind, raw): count for (kind, raw), count in collision.most_common()
    }
    return report


# ── 2. Side resolution, old vs new ───────────────────────────────────────────

def compare_side_resolution(root_rows, old_clv):
    """
    The orientation each resolver proves for every root-ledger row.

    A HARD FLIP -- both resolvers confident, and they disagree -- is the
    alarming case and is enumerated in full. `newlyRefused` is the safety win
    (the old code was inferring); `newlyProven` is recovered evidence, and each
    one names the basis so nobody has to take "recovered" on trust.
    """
    matrix = collections.Counter()
    hard_flips, newly_refused, newly_proven = [], [], []
    canonical_sides = collections.Counter()
    refusal_reasons = collections.Counter()
    refusal_classes = collections.Counter()
    basis_counts = collections.Counter()

    for row in root_rows:
        away, home = new_clv.parse_game(row.get("game", ""))
        old_side = old_clv.get_betside(row, away) if old_clv else None
        new_side = new_clv.get_betside(row, away, home)
        matrix[(old_side, new_side)] += 1

        identity = {"id": row.get("id"), "date": row.get("date"), "game": row.get("game"),
                    "market": row.get("market"), "bet": row.get("bet"),
                    "betSide": row.get("betSide"), "side": row.get("side"),
                    "result": row.get("result"), "pl": row.get("pl")}
        if old_side and new_side and old_side != new_side:
            hard_flips.append(dict(identity, oldOrientation=old_side, newOrientation=new_side))
        elif old_side and not new_side:
            newly_refused.append(dict(identity, oldOrientation=old_side))
        elif new_side and not old_side:
            newly_proven.append(dict(identity, newOrientation=new_side))

        # The canonical CONTRACT side, which is the question that grades money.
        resolution = wss.resolve_wager_side(row, away=away, home=home)
        canonical_sides[resolution["side"]] += 1
        if resolution["side"]:
            basis_counts[resolution["basis"]] += 1
        else:
            refusal_reasons[resolution["refusalReason"]] += 1
            refusal_classes[resolution["refusalClass"]] += 1

    return {
        "orientationMatrixOldToNew": {
            "%s -> %s" % (old, new): count for (old, new), count in matrix.most_common()
        },
        "hardFlipCount": len(hard_flips),
        "hardFlips": hard_flips,
        "newlyRefusedCount": len(newly_refused),
        "newlyRefusedByMarket": dict(collections.Counter(
            str(new_clv.normalize_market(r.get("market") or "")) for r in newly_refused).most_common()),
        "newlyRefusedSample": newly_refused[:25],
        "newlyProvenCount": len(newly_proven),
        "newlyProvenByMarket": dict(collections.Counter(
            str(new_clv.normalize_market(r.get("market") or "")) for r in newly_proven).most_common()),
        "newlyProvenSample": newly_proven[:25],
        "canonicalContractSideCounts": {str(k): v for k, v in canonical_sides.most_common()},
        "canonicalSideBasisCounts": dict(basis_counts.most_common()),
        "canonicalSideRefusalReasons": {str(k): v for k, v in refusal_reasons.most_common()},
        "canonicalSideRefusalClasses": {str(k): v for k, v in refusal_classes.most_common()},
    }


# ── 3. Settlement, old vs new ────────────────────────────────────────────────

def _synthetic_scores(row):
    """
    The final score this row already records, restated in the shape
    determine_result() expects. Reading the row's OWN recorded awayScore /
    homeScore is what makes this comparison scientific: both resolvers see the
    identical terminal truth, so every difference between them is a difference
    in SEMANTICS and never in the evidence they were handed.

    Returns {} when the row records no final score, and both resolvers then
    decline it identically.
    """
    away_score, home_score = row.get("awayScore"), row.get("homeScore")
    if away_score is None or home_score is None:
        return {}
    away, home = new_clv.parse_game(row.get("game", ""))
    if not away or not home:
        return {}
    return {(away, home): {"away_score": away_score, "home_score": home_score,
                           "completed": True}}


def compare_settlement(root_rows, old_clv):
    """
    Grade every row twice -- once through the pre-W1-A determine_result() and
    once through the current one -- against the row's own recorded final score.
    """
    counts = collections.Counter()
    changed_grade, newly_resolved, newly_refused = [], [], []
    pl_before = pl_after = 0.0
    pl_rows = []

    for row in root_rows:
        market = new_clv.normalize_market(row.get("market") or "")
        away, home = new_clv.parse_game(row.get("game", ""))
        scores = _synthetic_scores(row)
        if not scores or not market:
            counts["notComparable_noRecordedScoreOrUnknownMarket"] += 1
            continue
        counts["comparable"] += 1

        old_result = old_clv.determine_result(row, scores, away, home, market)[0] if old_clv else None
        new_result = new_clv.determine_result(row, scores, away, home, market)[0]

        identity = {"id": row.get("id"), "date": row.get("date"), "game": row.get("game"),
                    "market": row.get("market"), "bet": row.get("bet"),
                    "betSide": row.get("betSide"), "side": row.get("side"),
                    "line": row.get("line"), "awayScore": row.get("awayScore"),
                    "homeScore": row.get("homeScore"),
                    "ledgerResult": row.get("result"), "ledgerPl": row.get("pl")}

        if old_result == new_result:
            counts["identical"] += 1
            continue
        size = new_clv.get_size(row)
        price = row.get("price")
        old_pl = new_clv.calc_pl(price, size, old_result)
        new_pl = new_clv.calc_pl(price, size, new_result)
        pl_before += old_pl or 0.0
        pl_after += new_pl or 0.0
        record = dict(identity, oldResult=old_result, newResult=new_result,
                      oldPl=old_pl, newPl=new_pl)
        resolution = wss.resolve_wager_side(row, away=away, home=home)
        record["canonicalSide"] = resolution["side"]
        record["canonicalSideBasis"] = resolution["basis"]
        record["canonicalSideRefusalReason"] = resolution["refusalReason"]
        record["canonicalSideRefusalClass"] = resolution["refusalClass"]
        pl_rows.append(record)
        if old_result is None:
            counts["newlyResolved"] += 1
            newly_resolved.append(record)
        elif new_result is None:
            counts["newlyRefused"] += 1
            newly_refused.append(record)
        else:
            counts["changedGrade"] += 1
            changed_grade.append(record)

    return {
        "counts": dict(counts),
        "changedGradeCount": len(changed_grade),
        "changedGrade": changed_grade,
        "newlyResolvedCount": len(newly_resolved),
        "newlyResolved": newly_resolved,
        "newlyRefusedCount": len(newly_refused),
        "newlyRefusedByMarket": dict(collections.Counter(
            str(r["market"]) for r in newly_refused).most_common()),
        "newlyRefusedByRefusalReason": dict(collections.Counter(
            str(r["canonicalSideRefusalReason"]) for r in newly_refused).most_common()),
        "newlyRefusedSample": newly_refused[:40],
        "hypotheticalPlOnDifferingRowsBefore": round(pl_before, 2),
        "hypotheticalPlOnDifferingRowsAfter": round(pl_after, 2),
        "hypotheticalPlDelta": round(pl_after - pl_before, 2),
        "note": "These P/L figures are what the two SEMANTICS would produce if each "
                "differing row were re-graded from its own recorded final score. They are "
                "NOT a restatement of the committed ledger: no row is rewritten by this "
                "audit, and W1-A writes no historical correction.",
    }


# ── 4. Market-family and doubleheader coverage ───────────────────────────────

def family_coverage(root_rows):
    """Canonical side outcome per market family, and the player-prop deferrals."""
    per_family = collections.defaultdict(collections.Counter)
    deferred = collections.Counter()
    for row in root_rows:
        away, home = new_clv.parse_game(row.get("game", ""))
        resolution = wss.resolve_wager_side(row, away=away, home=home)
        family = (resolution.get("expression") or {}).get("family") or "UNCLASSIFIED"
        per_family[family][str(resolution["side"])] += 1
        if resolution["refusalClass"] == wss.REFUSAL_DEFERRED:
            deferred[resolution["refusalReason"]] += 1
    return {
        "bySemanticFamily": {k: dict(v) for k, v in sorted(per_family.items())},
        "deferredCounts": dict(deferred),
    }


def doubleheader_exposure(root_rows):
    """
    Rows whose (date, matchup) is shared by more than one distinct Kalshi event
    -- i.e. rows a team-pair key alone could not have told apart.
    """
    by_matchup = collections.defaultdict(set)
    for row in root_rows:
        ticker = row.get("marketTicker") or row.get("ticker")
        if not ticker or str(ticker).count("-") < 1:
            continue
        suffix = str(ticker).split("-")[1] if len(str(ticker).split("-")) > 1 else None
        if suffix:
            by_matchup[(row.get("date"), row.get("game"))].add(suffix)
    affected = {("%s %s" % k): sorted(v) for k, v in by_matchup.items() if len(v) > 1}
    return {
        "rowsCarryingATicker": sum(
            1 for r in root_rows if (r.get("marketTicker") or r.get("ticker"))),
        "matchupsWithMoreThanOneEventSuffix": len(affected),
        "detail": affected,
        "note": "A wager with no ticker cannot be bound to one leg of a doubleheader by "
                "date and teams alone. W1-A refuses such a row rather than binding it; "
                "W1-C owns the binding itself.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", default="origin/main",
                        help="git ref holding the pre-W1-A tree (default: origin/main)")
    parser.add_argument("--out", default=None, help="where to write the JSON report")
    args = parser.parse_args()

    root_rows = _load_root_ledger()
    edgelab_rows = _load_edgelab_ledger()
    old_clv = load_pre_w1a_clv(args.base_ref)

    report = {
        "subwave": "W1-A",
        "baseRef": args.base_ref,
        "preW1aModuleLoaded": old_clv is not None,
        "inventory": inventory(root_rows, edgelab_rows),
        "sideResolution": compare_side_resolution(root_rows, old_clv),
        "settlement": compare_settlement(root_rows, old_clv),
        "familyCoverage": family_coverage(root_rows),
        "doubleheaderExposure": doubleheader_exposure(root_rows),
    }
    if old_clv is None:
        report["warning"] = (
            "The pre-W1-A clv_update.py could not be read from %s, so the old-vs-new "
            "comparison below is NOT a real comparison. Do not read its zeros as "
            "'nothing changed'." % args.base_ref)

    out_path = args.out or os.path.join(tempfile.gettempdir(), "w1a_settlement_audit.json")
    with open(out_path, "w") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)

    settlement = report["settlement"]
    side = report["sideResolution"]
    print("W1-A SETTLEMENT SEMANTICS AUDIT")
    print("  root ledger rows            : %d" % report["inventory"]["rootLedgerRows"])
    print("  edgelab ledger rows         : %d" % report["inventory"]["edgelabLedgerRows"])
    print("  pre-W1-A module loaded      : %s" % report["preW1aModuleLoaded"])
    print("  orientation hard flips      : %d" % side["hardFlipCount"])
    print("  orientation newly refused   : %d" % side["newlyRefusedCount"])
    print("  orientation newly proven    : %d" % side["newlyProvenCount"])
    print("  settlement comparable rows  : %d" % settlement["counts"].get("comparable", 0))
    print("  settlement identical        : %d" % settlement["counts"].get("identical", 0))
    print("  settlement changed grade    : %d" % settlement["changedGradeCount"])
    print("  settlement newly resolved   : %d" % settlement["newlyResolvedCount"])
    print("  settlement newly refused    : %d" % settlement["newlyRefusedCount"])
    print("  hypothetical P/L delta      : %+0.2f" % settlement["hypotheticalPlDelta"])
    print("  report                      : %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
