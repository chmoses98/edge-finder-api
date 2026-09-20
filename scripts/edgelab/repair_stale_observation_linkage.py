#!/usr/bin/env python3
"""
scripts/edgelab/repair_stale_observation_linkage.py
===================================================
Re-settle a stored `marketObservationLinkage` that a fresh derivation no
longer agrees with, through the canonical correction path.

WHY THIS EXISTS -- THE 2026-09-19 ROUTER CONFLICT
-------------------------------------------------
`marketObservationLinkage` is not reported by anybody. It is DERIVED at
import time by `lib.edgelab.observation_linkage.link_bet_to_observation`,
whose rule is "the latest VALID PREGAME observation for this exact
ticker". The market corpus keeps gaining pregame captures right up to
first pitch, so that rule's answer legitimately moves while a game is
still pending.

`kalshi-bet-router` re-imports its whole open batch on every delivery
run. One row -- a 2026-09-19 pitcher-strikeouts wager recorded at
23:09:05Z -- had linked to the 21:24:19.751Z capture, the latest valid
pregame observation at the moment it was written. A further pregame
capture then landed at 23:26:44.691Z, seventeen minutes later and still
before that game's 00:10Z first pitch. Every subsequent re-import
re-derived the newer linkage, `marketObservationLinkage` is in NEITHER
`_ALWAYS_PRESERVE_FIELDS` nor `_PRESERVE_IF_NOT_SUPPLIED_FIELDS`, and
`_content_fingerprint` compares it -- so the row came back CONFLICT
forever and the delivery workflow went red on every run.

NEITHER VALUE IS WRONG. Both are correct evaluations of one
deterministic rule at two different times. What makes this repairable
rather than a judgement call is that the input is now FROZEN: once a
game starts, `isValidPregameObservation` is False for every later
capture, so the set of candidates can never grow again and the rule has
exactly one final answer.

WHAT THIS DOES NOT DO
---------------------
It does not hand-edit the ledger, and it changes nothing but the
linkage. The candidate it writes is the STORED ROW with one field
replaced, handed to `lib.edgelab.bets.write_placed_bet` with
`on_conflict="overwrite"` -- the canonical correction mechanism, which
inherits the settlement/CLV lifecycle from the stored row, keeps its
`createdAt`, stamps `recordStatus="CORRECTED"` and returns a receipt.
Starting from the stored row is the point: no field can be dropped by a
payload this script failed to reconstruct.

It REFUSES a row whose game has not started, because that row's linkage
can still legitimately move again and re-settling it would be noise. It
also refuses if applying the correction would change any field other
than the linkage itself (plus the `recordStatus`/`updatedAt` the
correction path stamps), which is the guard against this script becoming
a general-purpose ledger editor.

Usage:
    python3 scripts/edgelab/repair_stale_observation_linkage.py --dry-run
    python3 scripts/edgelab/repair_stale_observation_linkage.py --apply
    python3 scripts/edgelab/repair_stale_observation_linkage.py --apply \
        --bet-id 9e2cc436a80e1f64f1cb34c46b48a5f02b922c19

Exit codes: 0 nothing to do, or the repair applied and verified; 1 a
candidate was refused (reason printed); 2 the write itself failed.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage
from lib.edgelab.bets import write_placed_bet
from lib.edgelab.observation_linkage import link_bet_to_observation

#: The correction path stamps these itself. Anything else changing means
#: the candidate was not "the stored row with one field replaced", and
#: this script stops rather than writing it.
CORRECTION_STAMPED_FIELDS = frozenset({"marketObservationLinkage", "recordStatus", "updatedAt"})

#: Wall-clock ISO8601 fields on an observation are the only evidence of
#: whether the pregame window has closed for a ticker.
PREGAME_FLAG = "isValidPregameObservation"

#: SCOPE. Only a batch something RE-IMPORTS can produce this conflict, and
#: only the router re-imports. Run repo-wide, the drift check also matches
#: 100+ historical manual rows whose observation partitions have since been
#: compacted away -- for those the derivation differs because the EVIDENCE
#: is gone, not because a later pregame capture arrived, and re-settling
#: them would rewrite old rows to UNLINKED for no operational reason. This
#: script is not that migration.
DEFAULT_IMPORT_BATCH_IDS = ("kalshi-router-v1",)


def load_rows(path=None):
    return list(storage.read_records(path or storage.singleton_path("bets", "bets.jsonl")))


def rederive(row, storage_module=None):
    return link_bet_to_observation(
        row.get("marketTicker"), row.get("gameDate"),
        side=(row.get("side") or "YES"),
        scheduled_start=row.get("scheduledStart"),
        storage_module=storage_module,
    )


def observations_for(row, storage_module=None):
    from lib.edgelab.observation_linkage import load_observations_for_ticker
    dates = [row.get("gameDate")]
    if row.get("gameDate"):
        from datetime import datetime, timedelta
        try:
            dates.append((datetime.strptime(row["gameDate"], "%Y-%m-%d")
                          + timedelta(days=1)).strftime("%Y-%m-%d"))
        except ValueError:
            pass
    return load_observations_for_ticker(
        row.get("marketTicker"), dates, storage_module=storage_module)


def refusal_reason(row, storage_module=None):
    """
    Why this drifted row must NOT be re-settled, or None if it may be.

    Two distinct ways the input is not frozen, and they are not the same
    thing -- reporting them identically is what made the first repo-wide
    dry run unreadable:

      * the corpus still holds NO capture for this ticker at all, so the
        derivation moved because the evidence was COMPACTED AWAY, not
        because a later pregame capture arrived; and
      * every capture it does hold is still a valid pregame one, so that
        game has not started and the linkage may legitimately move again.
    """
    observations = observations_for(row, storage_module=storage_module)
    if not observations:
        return ("no observation for this ticker survives in the corpus, so this drift is "
                "compaction, not a later pregame capture")
    if not any(o.get(PREGAME_FLAG) is False for o in observations):
        return ("that game has not started, so a later pregame capture may still appear "
                "and move this linkage again for a correct reason")
    return None


def changed_fields(before, after):
    return sorted(f for f in set(before) | set(after) if before.get(f) != after.get(f))


def find_candidates(rows, bet_ids=None, import_batch_ids=DEFAULT_IMPORT_BATCH_IDS,
                    storage_module=None):
    """[(row, fresh_linkage, refusal_reason_or_None)] for every drifted row."""
    out = []
    for row in rows:
        if bet_ids and row.get("betId") not in bet_ids:
            continue
        if import_batch_ids and row.get("importBatchId") not in import_batch_ids:
            continue
        fresh = rederive(row, storage_module=storage_module)
        if fresh == row.get("marketObservationLinkage"):
            continue
        out.append((row, fresh, refusal_reason(row, storage_module=storage_module)))
    return out


def describe(row, fresh):
    """Identity and linkage provenance only -- never this row's economics."""
    stored = row.get("marketObservationLinkage") or {}
    return "\n".join([
        f"  betId        {row.get('betId')}",
        f"  sourceBetKey {row.get('sourceBetKey')}",
        f"  ticker       {row.get('marketTicker')}  gameDate {row.get('gameDate')}",
        f"  recordedAt   {row.get('recordedAt')}",
        f"  stored   -> observedAt {stored.get('observedAt')} "
        f"({stored.get('linkageStatus')}/{stored.get('linkageMethod')})",
        f"  rederived-> observedAt {fresh.get('observedAt')} "
        f"({fresh.get('linkageStatus')}/{fresh.get('linkageMethod')})",
    ])


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--bet-id", action="append", dest="bet_ids", default=None,
                        help="restrict to these betIds (repeatable)")
    parser.add_argument("--import-batch-id", action="append", dest="import_batch_ids",
                        default=None,
                        help=f"batches to consider (repeatable; default "
                             f"{', '.join(DEFAULT_IMPORT_BATCH_IDS)}). Only a batch "
                             f"something re-imports can produce this conflict.")
    parser.add_argument("--receipts-out", default=None)
    args = parser.parse_args(argv)

    rows = load_rows()
    candidates = find_candidates(
        rows,
        bet_ids=set(args.bet_ids) if args.bet_ids else None,
        import_batch_ids=tuple(args.import_batch_ids) if args.import_batch_ids
        else DEFAULT_IMPORT_BATCH_IDS,
    )
    if not candidates:
        print("[repair_stale_observation_linkage] no row's linkage re-derives differently; "
              "nothing to do")
        return 0

    refused = [c for c in candidates if c[2]]
    actionable = [c for c in candidates if not c[2]]
    print(f"[repair_stale_observation_linkage] {len(candidates)} drifted row(s): "
          f"{len(actionable)} actionable, {len(refused)} refused")
    for row, fresh, reason in refused:
        print(f"\nREFUSED -- {reason}")
        print(describe(row, fresh))

    receipts = []
    status = 0
    for row, fresh, _ in actionable:
        print()
        print(describe(row, fresh))
        candidate = dict(row)
        candidate["marketObservationLinkage"] = fresh

        if args.dry_run:
            print("  DRY RUN: not written")
            continue

        receipt = write_placed_bet(candidate, on_conflict="overwrite")
        receipts.append(receipt)
        if not receipt.get("success"):
            print(f"  REFUSED by the canonical write path: "
                  f"{receipt.get('duplicateStatus')} {receipt.get('errors')}")
            status = 2
            continue

        after = next((r for r in load_rows() if r.get("betId") == row.get("betId")), None)
        touched = set(changed_fields(row, after or {}))
        if not touched <= CORRECTION_STAMPED_FIELDS:
            # Cannot be undone from here, and must never pass silently.
            print(f"  ERROR: the correction changed fields beyond the linkage: "
                  f"{sorted(touched - CORRECTION_STAMPED_FIELDS)}")
            status = 2
            continue
        print(f"  {receipt.get('duplicateStatus')}: changed {sorted(touched)}")

    if args.receipts_out and receipts:
        with open(args.receipts_out, "w") as handle:
            json.dump(receipts, handle, indent=2, sort_keys=True)

    if refused and status == 0:
        status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
