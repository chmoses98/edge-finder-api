#!/usr/bin/env python3
"""
scripts/audit_settlement_backlog.py
===================================
REMEDIATION WAVE 0, section D. Classify every non-terminal row in the root
bets.json ledger into the five dispositions the Wave 0 brief requires, using
ONLY canonical machinery and evidence already committed to this repository.

    SETTLEMENT_AVAILABLE  -- safely reproducible from canonical sources once
                             ground truth is fetched: the row carries enough
                             identity (date, both teams, a market family that
                             production actually settles automatically) for
                             the canonical settler to resolve it. This is a
                             CAPABILITY claim, never a claim that a result is
                             known -- no outcome is inferred anywhere here.
    EXPECTED_UNRESOLVED   -- correctly unresolved forever: no automated
                             settlement path exists for the family, or the row
                             predates the settlement workflow entirely.
    PIPELINE_BACKLOG      -- should have settled and did not, because a
                             specific, independently confirmed workflow
                             failure ate it.
    IDENTITY_BLOCKED      -- cannot be settled without first resolving an
                             identity ambiguity (unparseable teams, missing
                             date, or a doubleheader leg whose ticker is known
                             to be cross-assigned -- audit CR-3, owned by
                             Wave 1).
    OTHER                 -- anything else, always with an explicit reason
                             string. Never a silent bucket.

WHAT THIS TOOL WILL NOT DO
--------------------------
It never marks a bet settled. It never infers a result from a recommendation,
a sibling contract, a model probability, or a related market. It never
fabricates a price or a CLV. It never writes bets.json. Its only output is a
read-only classification artifact.

That restraint is not incidental: the whole reason the Wave 0 brief demands a
classification pass before any repair is that 156 rows sat unsettled for three
months, and the tempting fix -- marking them resolved to make a counter go to
zero -- would destroy the only honest record of what the system actually knew.

RELATIONSHIP TO lib/bet_backlog_classifier.py
---------------------------------------------
That module is the canonical, already-tested evidence engine and is used here
unchanged as the first pass; this tool maps its eight evidence categories onto
the five dispositions above and refines its single coarse bucket
(`requires_manual_review`, 84 of 156 rows) using additional canonical
evidence -- the CR-2 outage window, doubleheader identity collisions, and
market-family settleability. No classification threshold or constant from that
module is redefined here.

OUTPUT
------
data/edgelab/operational_health/settlement_backlog_classification.json

`acknowledgedClassifications` in that artifact names the dispositions that
scripts/ci/production_health_gate.py's PROD-7 assertion excludes from the
"unexplained backlog" count, so a permanent, understood residue cannot hold
the health gate red forever while a genuine new backlog hides behind it.

USAGE
-----
    python3 scripts/audit_settlement_backlog.py                # print summary
    python3 scripts/audit_settlement_backlog.py --write-artifact
    python3 scripts/audit_settlement_backlog.py --json
"""

import argparse
import json
import os
import sys
from datetime import date as _date, datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(_HERE)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from lib import bet_backlog_classifier as C  # noqa: E402
from lib.atomic_json import write_json_atomic  # noqa: E402

# ── Dispositions ─────────────────────────────────────────────────────────────
SETTLEMENT_AVAILABLE = "SETTLEMENT_AVAILABLE"
EXPECTED_UNRESOLVED = "EXPECTED_UNRESOLVED"
PIPELINE_BACKLOG = "PIPELINE_BACKLOG"
IDENTITY_BLOCKED = "IDENTITY_BLOCKED"
OTHER = "OTHER"

# Dispositions the health gate treats as legitimately, permanently unresolved.
# PIPELINE_BACKLOG and SETTLEMENT_AVAILABLE are deliberately NOT acknowledged:
# both describe rows that SHOULD settle, so both must keep the gate red until
# they actually do.
ACKNOWLEDGED_CLASSIFICATIONS = [EXPECTED_UNRESOLVED, IDENTITY_BLOCKED]

# The CR-2 outage: clv-update.yml failed on every scheduled run whose
# "yesterday ET" target fell in this window, and edgelab-postgame.yml was
# skipped on each. Read off the GitHub Actions run history for runs 89-94
# during the Wave 0 investigation, and stated as a literal cited constant --
# never a live API call -- so this classification stays reproducible offline
# and deterministic, matching lib/bet_backlog_classifier.py's own convention
# for KNOWN_FAILED_CLV_UPDATE_DATES.
CR2_OUTAGE_DATES = frozenset({
    "2026-09-01",  # target of the 2026-09-02 run (#89, failure)
    "2026-09-02",  # target of the 2026-09-03 run (#90, failure)
    "2026-09-03",  # target of the 2026-09-04 run (#91, failure)
    "2026-09-04",  # target of the 2026-09-05 run (#92, failure)
    "2026-09-05",  # target of the 2026-09-06 run (#93, failure)
    "2026-09-06",  # target of the 2026-09-07 run (#94, failure)
})

# Doubleheader dates whose two legs were assigned identical Kalshi tickers
# (audit CR-3). A bet on one of these carries a ticker that may belong to the
# other physical game, so its settlement is identity-blocked until Wave 1
# repairs slate-level doubleheader identity. Settling one of these today would
# risk recording the WRONG game's outcome against a real wager -- exactly the
# failure mode this Wave is meant to prevent, not create.
CR3_DOUBLEHEADER_COLLISION_DATES = frozenset({
    "2026-06-17",  # SFATL: both legs -> KXMLBGAME-26JUN171915SFATL-*
    "2026-07-11",  # MILPIT: both legs -> KXMLBGAME-26JUL111605MILPIT-*
    "2026-07-07",  # MILSTL: both legs -> same team-total ticker
})


def _disposition_for(bet, category, today):
    """
    Pure. Map one canonical evidence category (plus the row itself) onto a
    (disposition, reason) pair. Every branch returns an explicit reason.
    """
    bet_date = str(bet.get("date") or "")[:10]

    # Identity ambiguity dominates every other consideration: if we cannot say
    # WHICH physical game a wager refers to, no amount of ground truth makes it
    # safe to settle.
    if bet_date in CR3_DOUBLEHEADER_COLLISION_DATES:
        return (IDENTITY_BLOCKED,
                "audit CR-3: %s is a doubleheader date whose two legs were assigned "
                "identical Kalshi tickers; the physical game this row refers to cannot "
                "be established from the ledger alone. Owned by Wave 1." % bet_date)

    if category == C.CATEGORY_MALFORMED_RECORD:
        return (IDENTITY_BLOCKED,
                "record has no usable date, or `game` does not parse into two team "
                "abbreviations, so no canonical game identity can be resolved")

    if category == C.CATEGORY_UNSUPPORTED_MARKET_FAMILY:
        return (EXPECTED_UNRESOLVED,
                "clv_update.py's determine_result() permanently routes this family "
                "(NRFI/YRFI) to manual settlement -- production has no automated "
                "settlement path for it at all")

    if category == C.CATEGORY_MISSING_SOURCE_DATA:
        return (EXPECTED_UNRESOLVED,
                "bet predates clv-update.yml (created %s); no automated settlement "
                "run has ever existed that could have resolved it"
                % C.CLV_WORKFLOW_CREATED)

    if category == C.CATEGORY_DUPLICATE:
        return (OTHER,
                "byte-for-byte content duplicate of another ledger row (every field "
                "except `id`); settling it would double-count a single wager")

    if category == C.CATEGORY_LEGITIMATELY_PENDING:
        return (OTHER,
                "too recent for a settlement pass to have run yet (within the "
                "%d-day legitimate-pending window) -- not a backlog row"
                % C.LEGITIMATE_PENDING_WINDOW_DAYS)

    if category == C.CATEGORY_PIPELINE_FAILURE:
        return (PIPELINE_BACKLOG,
                "date matches an independently confirmed failed clv-update.yml run "
                "(lib/bet_backlog_classifier.KNOWN_FAILED_CLV_UPDATE_DATES)")

    if category == C.CATEGORY_SETTLEABLE_FROM_EVIDENCE:
        return (SETTLEMENT_AVAILABLE,
                "a committed local artifact already resolves this row; canonical "
                "settlement can be reproduced offline")

    # C.CATEGORY_REQUIRES_MANUAL_REVIEW -- the coarse bucket, refined here.
    if bet_date in CR2_OUTAGE_DATES:
        return (PIPELINE_BACKLOG,
                "audit CR-2: %s fell inside the 2026-09-02..09-07 outage window in "
                "which clv-update.yml failed on every scheduled run and its commit "
                "step never executed" % bet_date)

    away, home = C.parse_game_teams(bet.get("game"))
    if not away or not home:
        return (IDENTITY_BLOCKED,
                "`game` field does not resolve to two team abbreviations, so the "
                "canonical settler cannot map this row to an MLB game")

    family = C.canonical_market_family(bet.get("market"))
    if not family:
        return (OTHER,
                "market %r does not map to any canonical family known to the "
                "settlement engine" % bet.get("market"))

    return (SETTLEMENT_AVAILABLE,
            "row carries a resolvable date, both team abbreviations and the "
            "automatically-settled family %r; canonical settlement machinery can "
            "resolve it once final-score ground truth is fetched (requires the MLB "
            "Stats API -- see the backfill workflow)" % family)


def classify_backlog(bets, today=None):
    """
    Pure. Returns (rows, counts). `rows` is one record per non-terminal bet,
    each carrying both the canonical evidence category and the Wave 0
    disposition, so the mapping is always auditable rather than opaque.
    """
    # lib.bet_backlog_classifier.classify_bet takes `today` as an ISO
    # YYYY-MM-DD STRING, not a date object -- normalize here so callers may
    # pass either and the canonical module's contract is honored exactly.
    if today is None:
        today = _date.today().isoformat()
    elif isinstance(today, (_date, datetime)):
        today = today.strftime("%Y-%m-%d")

    non_terminal = [b for b in bets if C.is_non_terminal(b)]
    duplicate_ids = C.find_duplicates(non_terminal)

    rows = []
    for bet in non_terminal:
        category = C.classify_bet(bet, today, duplicate_ids=duplicate_ids)
        disposition, reason = _disposition_for(bet, category, today)
        rows.append({
            "betId": bet.get("id"),
            "date": str(bet.get("date") or "")[:10],
            "game": bet.get("game"),
            "market": bet.get("market"),
            "ticker": bet.get("ticker"),
            "hasUserConfirmedEconomics": bool(
                bet.get("actualEntryPrice") is not None or bet.get("stake") is not None
            ),
            "evidenceCategory": category,
            "classification": disposition,
            "reason": reason,
        })

    counts = {}
    for row in rows:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    return rows, counts


def main(argv=None):
    parser = argparse.ArgumentParser(description="Classify the settlement backlog")
    parser.add_argument("--bets-path", default=os.path.join(ROOT_DIR, "bets.json"))
    parser.add_argument("--write-artifact", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    with open(args.bets_path) as f:
        bets = json.load(f)

    rows, counts = classify_backlog(bets)

    payload = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generatedBy": "scripts/audit_settlement_backlog.py",
        "wave": "REMEDIATION_WAVE_0",
        "ledger": os.path.relpath(args.bets_path, ROOT_DIR),
        "totalLedgerRows": len(bets),
        "nonTerminalRows": len(rows),
        "counts": counts,
        "acknowledgedClassifications": ACKNOWLEDGED_CLASSIFICATIONS,
        "note": (
            "Read-only classification. No bet was settled, no result inferred, no "
            "price or CLV fabricated, and bets.json was not modified. "
            "SETTLEMENT_AVAILABLE is a capability claim about the canonical settler, "
            "not a claim that any outcome is known."
        ),
        "rows": rows,
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("SETTLEMENT BACKLOG CLASSIFICATION (read-only)")
        print("=" * 78)
        print("ledger rows: %d | non-terminal: %d" % (len(bets), len(rows)))
        print()
        for disposition in (SETTLEMENT_AVAILABLE, PIPELINE_BACKLOG,
                            EXPECTED_UNRESOLVED, IDENTITY_BLOCKED, OTHER):
            n = counts.get(disposition, 0)
            ack = " (acknowledged by health gate)" if disposition in ACKNOWLEDGED_CLASSIFICATIONS else ""
            print("  %-22s %4d%s" % (disposition, n, ack))
        print()
        print("by disposition x evidence category:")
        pairs = {}
        for row in rows:
            key = (row["classification"], row["evidenceCategory"])
            pairs[key] = pairs.get(key, 0) + 1
        for (disp, cat), n in sorted(pairs.items()):
            print("  %-22s %-28s %4d" % (disp, cat, n))

    if args.write_artifact:
        out_dir = os.path.join(ROOT_DIR, "data", "edgelab", "operational_health")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "settlement_backlog_classification.json")
        write_json_atomic(payload, out_path, indent=2)
        print("\nartifact: %s" % os.path.relpath(out_path, ROOT_DIR))

    return 0


if __name__ == "__main__":
    sys.exit(main())
