#!/usr/bin/env python3
"""
scripts/edgelab/reconcile_settled_bets_from_archive.py
======================================================
ACCOUNTING ONLY. Settles confirmed wagers that are still `pending` in the
canonical ledger even though EdgeLab's settlement archive ALREADY holds a
definite `SETTLED` record for their exact market ticker.

This is a bookkeeping gap, not a modelling question: the outcome was
observed, graded and archived; only the ledger's own status was never
brought forward.

WHY THIS EXISTS SEPARATELY FROM settle_markets.py
-------------------------------------------------
scripts/edgelab/settle_markets.py is the canonical settlement driver and
remains so. But it re-derives every market's outcome from a live MLB Stats
API fetch. Where that host is unreachable, a run does not merely fail to
help -- its own dry-run reports that it would rewrite thousands of already
-correct settlement records as SETTLEMENT_UNRESOLVED. Re-fetching is the
wrong tool for a gap whose outcomes are already archived and correct.

So this script performs NO fetch and writes NO settlement record. It reads
the archive, and for each still-pending bet whose ticker has exactly one
definite SETTLED record, it runs the SAME canonical functions
settle_markets.py runs on the bet side:

    lib.edgelab.settlement.settle_bets_for_ticker      (grades the bet)
    lib.edgelab.settlement.bet_needs_settlement_update (decides persistence)
    lib.edgelab.storage.upsert_records(..., "betId")   (writes it)

No ledger JSON is hand-edited. No ticker is fuzzy-matched. Nothing is
guessed.

REFUSALS, ENFORCED IN CODE
--------------------------
  * exact ticker equality only -- no normalisation, no prefix match
  * a ticker with more than one settlement record, or with conflicting
    results across records, is SKIPPED as ambiguous
  * settlementStatus must be exactly "SETTLED" and result must be YES/NO;
    VOID and SETTLEMENT_UNRESOLVED are left pending
  * a bet that is not `pending` is never touched
  * player-prop families (GitHub issue #43) are never settled here
  * a bet missing side or stake is skipped rather than defaulted

Usage:
    python3 scripts/edgelab/reconcile_settled_bets_from_archive.py --dry-run
    python3 scripts/edgelab/reconcile_settled_bets_from_archive.py --apply
"""
import argparse
import collections
import glob
import gzip
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab import storage
from lib.edgelab.settlement import bet_needs_settlement_update, settle_bets_for_ticker

SETTLEMENTS_GLOB = os.path.join(_ROOT, "data", "edgelab", "settlements", "*")

# GitHub issue #43 -- these have no settlement implementation at all and
# must remain pending regardless of what any archive appears to say.
PLAYER_PROP_FAMILIES = {
    "pitcher_strikeouts", "pitcher_outs", "hitter_hits", "hitter_total_bases",
    "hitter_hits_runs_rbis", "hitter_rbis", "hitter_stolen_bases",
}
PLAYER_PROP_TICKER_PREFIXES = ("KXMLBKS-", "KXMLBOUTS-")

SKIP_REASONS = (
    "NOT_PENDING",
    "NO_CANONICAL_TICKER",
    "PLAYER_PROP_ISSUE_43",
    "NO_SETTLEMENT_RECORD",
    "SETTLEMENT_NOT_DEFINITE",
    "AMBIGUOUS_SETTLEMENT_RECORDS",
    "MISSING_BET_FIELDS",
)


def load_settlement_index():
    """Every settlement record, grouped by exact ticker.

    Grouped rather than last-write-wins so that a ticker carrying
    disagreeing records can be DETECTED and skipped, instead of one record
    silently winning.
    """
    index = collections.defaultdict(list)
    for path in sorted(glob.glob(SETTLEMENTS_GLOB)):
        opener = gzip.open if path.endswith(".gz") else open
        try:
            with opener(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    ticker = record.get("marketTicker")
                    if ticker:
                        index[ticker].append(record)
        except (OSError, json.JSONDecodeError):
            continue
    return index


def _is_player_prop(bet):
    family = str(bet.get("marketFamily") or "")
    ticker = str(bet.get("marketTicker") or "")
    return (family in PLAYER_PROP_FAMILIES
            or "hitter" in family
            or "pitcher" in family
            or ticker.startswith(PLAYER_PROP_TICKER_PREFIXES))


def _definite_settlement(records):
    """One unambiguous SETTLED record, or None with a reason."""
    if not records:
        return None, "NO_SETTLEMENT_RECORD"
    statuses = {r.get("settlementStatus") for r in records}
    results = {r.get("result") for r in records}
    if len(results) > 1 or len(statuses) > 1:
        return None, "AMBIGUOUS_SETTLEMENT_RECORDS"
    record = records[0]
    if record.get("settlementStatus") != "SETTLED":
        return None, "SETTLEMENT_NOT_DEFINITE"
    if record.get("result") not in ("YES", "NO"):
        return None, "SETTLEMENT_NOT_DEFINITE"
    return record, None


def plan(bets, settlement_index):
    """Decide, without writing anything, exactly which bets may settle."""
    updates, skipped, receipts = [], collections.Counter(), []
    for bet in bets:
        if bet.get("status") != "pending":
            skipped["NOT_PENDING"] += 1
            continue
        ticker = bet.get("marketTicker")
        if not ticker or not str(ticker).strip() or str(bet.get("marketFamily")) == "N/A":
            skipped["NO_CANONICAL_TICKER"] += 1
            continue
        if _is_player_prop(bet):
            skipped["PLAYER_PROP_ISSUE_43"] += 1
            continue
        record, reason = _definite_settlement(settlement_index.get(ticker, []))
        if record is None:
            skipped[reason] += 1
            continue
        if bet.get("side") not in ("YES", "NO") or bet.get("stake") is None:
            skipped["MISSING_BET_FIELDS"] += 1
            continue

        computed = settle_bets_for_ticker([bet], record["settlementStatus"], record["result"])
        if not computed:
            skipped["SETTLEMENT_NOT_DEFINITE"] += 1
            continue
        computed_bet = computed[0]
        if not bet_needs_settlement_update(bet, computed_bet):
            skipped["NOT_PENDING"] += 1
            continue
        updates.append(computed_bet)
        receipts.append({
            "betId": bet.get("betId"),
            "marketTicker": ticker,
            "gameDate": bet.get("gameDate"),
            "marketFamily": bet.get("marketFamily"),
            "side": bet.get("side"),
            "settlementResult": record["result"],
            "settlementId": record.get("settlementId"),
            "betResult": computed_bet.get("result"),
            "stake": bet.get("stake"),
            "entryPrice": bet.get("entryPrice"),
            "netProfitLoss": computed_bet.get("netProfitLoss"),
        })
    return updates, skipped, receipts


def summarise(receipts):
    wins = [r for r in receipts if r["betResult"] == "WIN"]
    losses = [r for r in receipts if r["betResult"] == "LOSS"]
    pl = [r["netProfitLoss"] for r in receipts if r["netProfitLoss"] is not None]
    return {
        "betsSettled": len(receipts),
        "wins": len(wins),
        "losses": len(losses),
        "other": len(receipts) - len(wins) - len(losses),
        "netProfitLoss": round(sum(pl), 4) if pl else None,
        "totalStake": round(sum(r["stake"] for r in receipts if r["stake"] is not None), 4),
        "byDate": dict(collections.Counter(r["gameDate"] for r in receipts)),
        "byFamily": dict(collections.Counter(r["marketFamily"] for r in receipts)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true",
                       help="compute and print the plan; write nothing")
    group.add_argument("--apply", action="store_true",
                       help="persist the plan via the canonical upsert path")
    parser.add_argument("--receipts", default=None,
                        help="optional path to write the settlement receipts as JSON")
    args = parser.parse_args()

    bets_path = storage.singleton_path("bets", "bets.jsonl")
    bets = list(storage.read_records(bets_path))
    settlement_index = load_settlement_index()

    updates, skipped, receipts = plan(bets, settlement_index)
    summary = summarise(receipts)

    print("[reconcile] ledger=%d settlement_tickers=%d" % (len(bets), len(settlement_index)))
    print("[reconcile] eligible_for_settlement=%d" % len(updates))
    for reason in SKIP_REASONS:
        if skipped.get(reason):
            print("[reconcile]   skipped %-28s %d" % (reason, skipped[reason]))
    print("[reconcile] summary=%s" % json.dumps(summary, sort_keys=True))

    if args.receipts:
        with open(args.receipts, "w", encoding="utf-8") as fh:
            json.dump({"summary": summary, "receipts": receipts}, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print("[reconcile] receipts written to %s" % args.receipts)

    if args.dry_run:
        print("[reconcile] DRY RUN -- nothing written")
        return 0

    updated, inserted = storage.upsert_records(bets_path, updates, "betId")
    print("[reconcile] APPLIED updated=%d inserted=%d" % (updated, inserted))
    if inserted:
        raise SystemExit("[reconcile] FATAL: reconciliation inserted %d NEW bets; it must only "
                         "update existing ones" % inserted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
