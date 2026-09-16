#!/usr/bin/env python3
"""
scripts/edgelab/migrate_exact_cash_basis_2026_09.py
===================================================
HISTORICAL ACCOUNTING CORRECTION -- EXACT EXECUTION CASH BASIS.

A one-off, narrowly scoped migration harness. It is NOT a settlement
authority and it creates no sporting truth. Every outcome it uses was
already canonically established and committed to
data/edgelab/settlements/<date>.jsonl; this harness only recomputes the
realized-P/L CASH BASIS of wagers that were already settled, now that
PR #217 made exact `actualCashConsumed` reachable from the canonical
settlement path.

WHY A PURPOSE-BUILT HARNESS RATHER THAN AN EXISTING SCRIPT.

  * scripts/edgelab/settle_markets.py is the canonical settlement entry
    point, but its job is to FETCH each game's outcome from the MLB Stats
    API. The outcomes are already on disk, and re-running it would also
    regenerate recommendations and model evaluations for these dates --
    restamping modelCommitSha on thousands of historical rows. That is
    model provenance this correction is explicitly forbidden to touch.
  * scripts/edgelab/reconcile_settled_bets_from_archive.py is the right
    ARCHITECTURAL precedent -- offline, settlement-index driven, plan then
    apply -- but its behaviour is deliberately different: it acts only on
    PENDING bets and refuses player props under its archive-specific
    PLAYER_PROP_ISSUE_43 rule. This migration concerns ALREADY-SETTLED
    wagers whose outcome truth is already canonical. That reconciler is
    NOT modified to make this migration fit; its refusals stay exactly as
    they are.

So this harness reuses the canonical chain itself and adds nothing to it:

    lib.edgelab.settlement.settle_bets_for_ticker
      -> lib.edgelab.settlement.bet_needs_settlement_update
      -> lib.edgelab.storage.upsert_records(..., "betId")

There is no second P/L formula anywhere in this file.

PLAYER PROPS. Four of the approved wagers are pitcher_strikeouts. They are
in scope ONLY because the repository's own authoritative postgame
implementation already settled them and that definitive Settlement record
is committed. This harness reads that existing record; it never derives a
player-prop outcome, and it would refuse any wager lacking definitive
committed settlement truth.

SAFETY. The approved set of 45 betIds is derived from the audited criteria
and then FROZEN. The apply phase refuses to write if the plan deviates
from it in any way -- see assert_plan.

Usage:
    python3 scripts/edgelab/migrate_exact_cash_basis_2026_09.py            # plan only
    python3 scripts/edgelab/migrate_exact_cash_basis_2026_09.py --apply    # plan, assert, write
"""
import argparse
import collections
import glob
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage
from lib.edgelab.settlement import bet_needs_settlement_update, settle_bets_for_ticker

# --------------------------------------------------------------------------
# THE APPROVED SCOPE. Both are hard limits, not defaults.
# --------------------------------------------------------------------------

APPROVED_DATES = ("2026-09-11", "2026-09-12", "2026-09-13", "2026-09-14", "2026-09-15")
APPROVED_ROW_COUNT = 45
APPROVED_TOTAL_DELTA = 17.3162
DELTA_TOLERANCE = 0.01

#: The audited criteria that define the approved population. A wager must
#: satisfy EVERY one of these to be eligible; nothing else may be written.
def is_approved_candidate(bet):
    return (
        bet.get("status") == "settled"
        and bet.get("gameDate") in APPROVED_DATES
        and bet.get("trackingType") == "REAL"
        and bet.get("economicsSource") == "EXACT_API_EXECUTION"
        and bet.get("actualCashConsumed") is not None
        and bet.get("contracts") is not None
    )


IDENTITY_FIELDS = (
    "betId", "sourceBetKey", "marketTicker", "side", "contracts", "stake",
    "entryPrice", "gameDate", "importBatchId",
)
#: Execution economics are INPUTS to this correction, never outputs.
EXECUTION_ECONOMICS_FIELDS = (
    "actualCashConsumed", "contractCost", "averageFillPrice", "totalFees",
    "entryFees", "exitFees", "economicsSource", "economicsConfidence",
    "feeSource", "feeStatus", "executionStatus",
)
#: Model / recommendation provenance must survive untouched.
PROVENANCE_FIELDS = (
    "recommendationId", "modelEvaluationId", "modelSupported",
    "modelFairProbability", "productionRunId", "snapshotId", "replayRunId",
)

SETTLEMENTS_GLOB = os.path.join("data", "edgelab", "settlements", "*.jsonl*")


def load_settlement_index():
    """Every committed settlement record, grouped by exact ticker.

    Grouped rather than last-write-wins so a ticker carrying disagreeing
    records is DETECTED and refused instead of one record silently winning
    (the same reasoning as the archive reconciler's own index).
    """
    index = collections.defaultdict(list)
    for path in sorted(glob.glob(SETTLEMENTS_GLOB)):
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                ticker = record.get("marketTicker")
                if ticker:
                    index[ticker].append((os.path.basename(path), record))
    return index


def definite_settlement(entries):
    """One unambiguous committed SETTLED record for this ticker, or a reason."""
    if not entries:
        return None, None, "NO_SETTLEMENT_RECORD"
    records = [r for _src, r in entries]
    if len({r.get("settlementStatus") for r in records}) > 1:
        return None, None, "SETTLEMENT_RECORDS_CONFLICT"
    if len({r.get("result") for r in records}) > 1:
        return None, None, "SETTLEMENT_RECORDS_CONFLICT"
    source, record = entries[0]
    if record.get("settlementStatus") != "SETTLED":
        return None, None, "SETTLEMENT_NOT_DEFINITE"
    if record.get("result") not in ("YES", "NO"):
        return None, None, "SETTLEMENT_NOT_DEFINITE"
    return record, source, None


def build_plan(bets, settlement_index, *, now):
    """Read-only. Returns (rows, updates, refusals).

    `rows` is the machine-readable before/after evidence; `updates` are the
    canonical computed records that the apply phase would upsert.
    """
    rows, updates, refusals = [], [], []
    for bet in bets:
        if not is_approved_candidate(bet):
            continue
        ticker = bet.get("marketTicker")
        record, source, reason = definite_settlement(settlement_index.get(ticker, []))
        if record is None:
            refusals.append({"betId": bet.get("betId"), "marketTicker": ticker, "reason": reason})
            continue

        # THE CANONICAL CHAIN. No second formula, and the settlement's own
        # gameId is passed so the contradiction guard still applies.
        computed = settle_bets_for_ticker(
            [bet], record["settlementStatus"], record["result"],
            now=now, game_id=record.get("gameId"),
        )
        if not computed:
            refusals.append({"betId": bet.get("betId"), "marketTicker": ticker,
                             "reason": "SETTLEMENT_PRODUCED_NO_RESULT"})
            continue
        computed_bet = computed[0]
        if computed_bet.get("settlementRefusalReason") is not None:
            refusals.append({"betId": bet.get("betId"), "marketTicker": ticker,
                             "reason": computed_bet["settlementRefusalReason"]})
            continue
        if not bet_needs_settlement_update(bet, computed_bet):
            continue

        rows.append({
            "betId": bet.get("betId"),
            "gameDate": bet.get("gameDate"),
            "marketTicker": ticker,
            "marketFamily": bet.get("marketFamily"),
            "side": bet.get("side"),
            "contracts": bet.get("contracts"),
            "actualCashConsumed": bet.get("actualCashConsumed"),
            "existingResult": bet.get("result"),
            "proposedResult": computed_bet.get("result"),
            "existingNetProfitLoss": bet.get("netProfitLoss"),
            "proposedNetProfitLoss": computed_bet.get("netProfitLoss"),
            "delta": round((computed_bet.get("netProfitLoss") or 0.0) - (bet.get("netProfitLoss") or 0.0), 4),
            "settlementId": record.get("settlementId"),
            "settlementSource": record.get("settlementSource"),
            "settlementResult": record.get("result"),
            "settlementRecordFile": source,
        })
        computed_bet["updatedAt"] = now
        updates.append((bet, computed_bet))
    return rows, updates, refusals


def is_already_applied(rows, refusals, frozen_ids):
    """True when this correction has already been applied and there is nothing to do.

    THIS IS A SUCCESS, NOT A DEVIATION, and the two must not be confused. The
    migration is idempotent by construction: bet_needs_settlement_update is
    what decides a row needs writing, so once the corrected cash basis is
    stored, a re-run proposes nothing. The required second-pass proof is
    exactly this state -- zero proposed updates against an intact frozen set.

    An EMPTY plan is only benign when the approved population is still all
    present and every one of them produced definitive settlement truth. Zero
    rows because the wagers vanished, or because their settlement records
    stopped resolving, is a real failure and falls through to assert_plan.
    """
    return not rows and not refusals and len(frozen_ids) == APPROVED_ROW_COUNT


def assert_plan(rows, updates, refusals, frozen_ids):
    """Every condition the migration was approved under. Any failure refuses."""
    problems = []
    proposed_ids = [r["betId"] for r in rows]

    if refusals:
        problems.append(f"{len(refusals)} approved wager(s) lack definitive committed settlement truth: {refusals}")
    if len(frozen_ids) < APPROVED_ROW_COUNT:
        problems.append(f"frozen set has {len(frozen_ids)} ids, fewer than the approved {APPROVED_ROW_COUNT}")
    if len(proposed_ids) != APPROVED_ROW_COUNT:
        problems.append(f"{len(proposed_ids)} rows would change, approved exactly {APPROVED_ROW_COUNT}")
    if len(set(proposed_ids)) != len(proposed_ids):
        problems.append("duplicate betIds in the plan")
    outside = sorted(set(proposed_ids) - set(frozen_ids))
    if outside:
        problems.append(f"{len(outside)} proposed update(s) outside the frozen set: {outside}")

    total = round(sum(r["delta"] for r in rows), 4)
    if abs(total - APPROVED_TOTAL_DELTA) > DELTA_TOLERANCE:
        problems.append(f"total delta {total:+} differs from audited {APPROVED_TOTAL_DELTA:+}")

    for r in rows:
        if r["gameDate"] not in APPROVED_DATES:
            problems.append(f"{r['betId']}: gameDate {r['gameDate']} outside approved dates")
        if r["existingResult"] != r["proposedResult"]:
            problems.append(f"{r['betId']}: result would change {r['existingResult']} -> {r['proposedResult']}")

    for original, computed in updates:
        for field in IDENTITY_FIELDS:
            if original.get(field) != computed.get(field):
                problems.append(f"{original.get('betId')}: identity field {field} would change")
        for field in EXECUTION_ECONOMICS_FIELDS:
            if original.get(field) != computed.get(field):
                problems.append(f"{original.get('betId')}: execution economics {field} would change")
        for field in PROVENANCE_FIELDS:
            if original.get(field) != computed.get(field):
                problems.append(f"{original.get('betId')}: provenance {field} would change")
        touched = {f for f in set(original) | set(computed) if original.get(f) != computed.get(f)}
        unexpected = touched - {"netProfitLoss", "returnAmount", "updatedAt"}
        if unexpected:
            problems.append(f"{original.get('betId')}: unexpected field(s) would change: {sorted(unexpected)}")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write the correction (default: plan only)")
    parser.add_argument("--plan-out", default=None, help="Write the machine-readable plan here")
    args = parser.parse_args()

    bets_path = storage.singleton_path("bets", "bets.jsonl")
    bets = list(storage.read_records(bets_path))
    settlement_index = load_settlement_index()
    now = ids.utc_now_iso()

    # FREEZE the approved set from the audited criteria, before planning.
    frozen_ids = sorted(b["betId"] for b in bets if is_approved_candidate(b) and b.get("betId"))

    rows, updates, refusals = build_plan(bets, settlement_index, now=now)
    rows.sort(key=lambda r: (r["gameDate"], r["betId"]))

    by_date = collections.defaultdict(lambda: {"rows": 0, "delta": 0.0})
    for r in rows:
        by_date[r["gameDate"]]["rows"] += 1
        by_date[r["gameDate"]]["delta"] = round(by_date[r["gameDate"]]["delta"] + r["delta"], 4)

    plan = {
        "label": "HISTORICAL ACCOUNTING CORRECTION -- EXACT EXECUTION CASH BASIS",
        "notModelPerformance": (
            "This is an accounting correction to the realized-P/L cash basis. The wagers, "
            "prices, sides, contract counts and sporting outcomes are unchanged. It is NOT "
            "improved betting performance, improved model performance, new edge, or a new "
            "betting result."
        ),
        "generatedAt": now,
        "approvedDates": list(APPROVED_DATES),
        "approvedRowCount": APPROVED_ROW_COUNT,
        "frozenCandidateCount": len(frozen_ids),
        "rowsChanging": len(rows),
        "totalDelta": round(sum(r["delta"] for r in rows), 4),
        "perDate": {d: by_date[d] for d in sorted(by_date)},
        "refusals": refusals,
        "rows": rows,
    }

    already_applied = is_already_applied(rows, refusals, frozen_ids)
    problems = [] if already_applied else assert_plan(rows, updates, refusals, frozen_ids)
    plan["alreadyApplied"] = already_applied
    plan["assertionsPassed"] = not problems
    plan["problems"] = problems

    if args.plan_out:
        with open(args.plan_out, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, indent=2, sort_keys=True)

    print(f"[migrate] frozen approved candidates: {len(frozen_ids)}")
    print(f"[migrate] rows that would change:     {len(rows)}")
    print(f"[migrate] total delta:                {plan['totalDelta']:+}")
    for d in sorted(by_date):
        print(f"[migrate]   {d}  n={by_date[d]['rows']:2}  {by_date[d]['delta']:+.4f}")
    if refusals:
        print(f"[migrate] REFUSALS: {refusals}")

    if problems:
        print("[migrate] ASSERTIONS FAILED -- NOT APPLYING:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    if already_applied:
        print("[migrate] ALREADY APPLIED -- 0 rows to change; the ledger already carries "
              "the exact-cash-basis correction. Nothing written (idempotent no-op).")
        return 0

    print("[migrate] all assertions passed")
    if not args.apply:
        print("[migrate] plan only; nothing written")
        return 0

    updated, inserted = storage.upsert_records(bets_path, [c for _o, c in updates], "betId")
    print(f"[migrate] upsert: updated={updated} inserted={inserted}")
    if inserted != 0:
        print("[migrate] FATAL: the migration inserted rows; it may only update.", file=sys.stderr)
        return 1
    if updated != APPROVED_ROW_COUNT:
        print(f"[migrate] FATAL: updated {updated}, approved {APPROVED_ROW_COUNT}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
