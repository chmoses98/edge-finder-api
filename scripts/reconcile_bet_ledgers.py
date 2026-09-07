#!/usr/bin/env python3
"""
scripts/reconcile_bet_ledgers.py
================================
REMEDIATION WAVE 0, section E. MEASUREMENT ONLY.

The institutional audit reported two ledgers of different sizes -- root
`bets.json` at 556 rows and canonical `data/edgelab/bets/bets.jsonl` at 385 --
with the difference unexplained by any artifact in the repository. This tool
produces the deterministic explanation, one category per row.

IT DOES NOT FIX THE TWO-LEDGER ARCHITECTURE, and deliberately so. Wave 0's job
is to restore operational truth; collapsing two ledgers into one is a
migration with its own blast radius and belongs to a later, separately
authorized mission. What this tool guarantees is that after it runs, nobody
has to wonder what the 171-row difference is made of.

THE RULE THAT SHAPES EVERYTHING HERE
------------------------------------
A recommendation is NEVER assumed to have been placed. Only user-confirmed
wagers are placed bets.

Root `bets.json` mixes both populations: rows written automatically by
scripts/write_pending_bets.py (a recommendation the pipeline logged, with
`createdBy: write_pending_bets.py` and no confirmed execution economics) sit
alongside rows imported from a real execution receipt. The canonical ledger
contains only the latter, by design. So a root row with no canonical
counterpart is USUALLY CORRECT -- it is a recommendation that was never
placed -- and treating the count difference as "missing data to import" would
manufacture wagers that never happened.

This tool therefore never proposes an import. It classifies, and it counts.

CATEGORIES
----------
    REPRESENTED_CANONICALLY   root row has a canonical counterpart
    RECOMMENDATION_NOT_PLACED root row is a pipeline-written recommendation
                              with no confirmed execution economics -- correctly
                              absent from the canonical ledger
    LEGACY_ONLY               root row predates the canonical ledger's earliest
                              record; no canonical row could exist
    OUT_OF_CANONICAL_WINDOW   root row postdates the canonical ledger's latest
                              record -- i.e. the settlement/ingest outage window
    CANONICAL_ONLY            canonical row with no root counterpart
    SYNTHETIC_OR_MANUAL       manually imported / synthetic construction
    IDENTITY_UNRESOLVED       insufficient identity to match either way
    LIFECYCLE_MISMATCH        matched, but terminal state disagrees between
                              the two ledgers
    OTHER                     always with an explicit reason

USAGE
-----
    python3 scripts/reconcile_bet_ledgers.py
    python3 scripts/reconcile_bet_ledgers.py --write-artifact
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(_HERE)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from lib.atomic_json import write_json_atomic  # noqa: E402

REPRESENTED_CANONICALLY = "REPRESENTED_CANONICALLY"
RECOMMENDATION_NOT_PLACED = "RECOMMENDATION_NOT_PLACED"
LEGACY_ONLY = "LEGACY_ONLY"
OUT_OF_CANONICAL_WINDOW = "OUT_OF_CANONICAL_WINDOW"
CANONICAL_ONLY = "CANONICAL_ONLY"
SYNTHETIC_OR_MANUAL = "SYNTHETIC_OR_MANUAL"
IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"
LIFECYCLE_MISMATCH = "LIFECYCLE_MISMATCH"
OTHER = "OTHER"

_TERMINAL = {"WIN", "LOSS", "PUSH", "VOID"}

# Canonical family aliases, so "F5 ML" and "F5_ML_Away" match the same market.
_MARKET_NORMALIZE = [
    (re.compile(r"^f5[ _]?ml([ _]?(away|home))?$", re.I), "F5_ML"),
    (re.compile(r"^ml([ _]?(away|home))?$", re.I), "ML"),
    (re.compile(r"^(tt|team[ _]?total)([ _]?(away|home))?([ _]?over)?$", re.I), "TEAM_TOTAL"),
    (re.compile(r"^(y|n)rfi$", re.I), "RFI"),
    (re.compile(r"^run[ _]?line$|^rl([ _]?(away|home))?$", re.I), "RUN_LINE"),
    (re.compile(r"^game[ _]?total$|^total$", re.I), "GAME_TOTAL"),
]


# The two ledgers speak different market vocabularies: root bets.json uses
# human labels ("F5 ML", "Total", "YRFI") while the canonical ledger uses the
# Kalshi series ticker ("KXMLBF5", "KXMLBTOTAL", "KXMLBRFI"). Reconciling them
# requires translating both into one neutral family space -- matching on the
# raw strings silently produces a near-empty intersection and an entirely
# fictitious reconciliation.
_SERIES_TO_FAMILY = {
    "KXMLBGAME": "ML",
    "KXMLBF5": "F5_ML",
    "KXMLBF3": "F3_ML",
    "KXMLBF7": "F7_ML",
    "KXMLBTEAMTOTAL": "TEAM_TOTAL",
    "KXMLBTOTAL": "GAME_TOTAL",
    "KXMLBRFI": "RFI",
    "KXMLBSPREAD": "RUN_LINE",
    "KXMLBF5SPREAD": "F5_SPREAD",
    "KXMLBF5TOTAL": "F5_TOTAL",
}


def _norm_market(value):
    if not value:
        return None
    text = str(value).strip()
    upper = text.upper()
    if upper in _SERIES_TO_FAMILY:
        return _SERIES_TO_FAMILY[upper]
    for pattern, canonical in _MARKET_NORMALIZE:
        if pattern.match(text):
            return canonical
    return upper.replace(" ", "_")


# Only '@' or a WHITESPACE-DELIMITED 'vs'/'at' separates two teams. The word
# forms must be delimited: an unanchored /at/i matches the "AT" inside "ATL",
# which turned 'SF@ATL' into {'SF', 'L'} and silently destroyed the match rate.
_MATCHUP_SPLIT_RE = re.compile(r"\s*@\s*|\s+(?:vs\.?|at)\s+", re.I)


def _norm_teams(value):
    """'KC @ MIN' / 'COL@WSH' -> frozenset({'KC','MIN'}); None when unparseable."""
    if not value:
        return None
    parts = _MATCHUP_SPLIT_RE.split(str(value).strip())
    teams = [p.strip().upper() for p in parts if p.strip()]
    if len(teams) != 2:
        return None
    # A team abbreviation is 2-4 letters. Anything else means the split found
    # something that was not a matchup, and guessing is worse than refusing.
    if not all(re.fullmatch(r"[A-Z]{2,4}", t) for t in teams):
        return None
    return frozenset(teams)


def _root_key(bet):
    date = str(bet.get("date") or "")[:10]
    teams = _norm_teams(bet.get("game"))
    market = _norm_market(bet.get("market"))
    if not date or not teams or not market:
        return None
    return (date, teams, market)


def _canonical_key(row):
    date = str(row.get("gameDate") or "")[:10]
    teams = _norm_teams(row.get("matchup"))
    market = _norm_market(row.get("marketFamily") or row.get("market"))
    if not date or not teams or not market:
        return None
    return (date, teams, market)


def _is_pipeline_recommendation(bet):
    """
    A row the pipeline wrote as a recommendation, never confirmed as executed.
    write_pending_bets.py stamps createdBy and writes status 'pending' with no
    execution economics; a confirmed wager carries an actual entry price or a
    confirmed receipt.
    """
    if bet.get("createdBy") == "write_pending_bets.py":
        confirmed = (bet.get("actualEntryPrice") is not None
                     or bet.get("confirmedReceiptAt") is not None)
        return not confirmed
    return False


def reconcile(root_bets, canonical_rows):
    """Pure. Returns (root_rows, canonical_rows_out, counts)."""
    canonical_by_key = {}
    for row in canonical_rows:
        key = _canonical_key(row)
        if key:
            canonical_by_key.setdefault(key, []).append(row)

    canonical_dates = [str(r.get("gameDate") or "")[:10] for r in canonical_rows]
    canonical_dates = [d for d in canonical_dates if d]
    earliest = min(canonical_dates) if canonical_dates else None
    latest = max(canonical_dates) if canonical_dates else None

    matched_canonical = set()
    out_root = []

    for bet in root_bets:
        key = _root_key(bet)
        date = str(bet.get("date") or "")[:10]
        entry = {
            "ledger": "root",
            "betId": bet.get("id"),
            "date": date,
            "game": bet.get("game"),
            "market": bet.get("market"),
            "result": bet.get("result"),
        }

        if key is None:
            # Be specific about WHY, because the three causes have different
            # remedies and only one of them is a defect in this tool's inputs.
            if not date:
                reason = "row carries no usable date"
            elif not _norm_market(bet.get("market")):
                reason = "row carries no market label"
            elif _norm_teams(bet.get("game")) is None:
                reason = (
                    "matchup %r is written with team names/nicknames rather than the "
                    "abbreviations both ledgers key on. Deliberately NOT guessed: "
                    "nickname forms in this ledger include ambiguous ones ('Sox' is "
                    "either Boston or Chicago), and a wrong expansion would silently "
                    "match a wager to the wrong game. Resolving these needs a curated "
                    "franchise alias table, which is a Wave 1 identity task."
                    % bet.get("game")
                )
            else:
                reason = "identity could not be normalized into a comparable key"
            entry.update(category=IDENTITY_UNRESOLVED, reason=reason)
            out_root.append(entry)
            continue

        candidates = canonical_by_key.get(key) or []
        if candidates:
            counterpart = candidates[0]
            matched_canonical.add(id(counterpart))
            root_terminal = bet.get("result") in _TERMINAL
            canon_terminal = counterpart.get("result") in _TERMINAL
            if root_terminal != canon_terminal:
                entry.update(
                    category=LIFECYCLE_MISMATCH,
                    reason="matched canonically but terminal state disagrees "
                           "(root result=%r, canonical result=%r)"
                           % (bet.get("result"), counterpart.get("result")),
                    canonicalBetId=counterpart.get("betId"))
            else:
                entry.update(category=REPRESENTED_CANONICALLY,
                             reason="matched on (date, teams, market family)",
                             canonicalBetId=counterpart.get("betId"))
            out_root.append(entry)
            continue

        if _is_pipeline_recommendation(bet):
            entry.update(category=RECOMMENDATION_NOT_PLACED,
                         reason="written by scripts/write_pending_bets.py with no "
                                "confirmed execution economics -- a recommendation, not a "
                                "placed wager; correctly absent from the canonical ledger")
        elif earliest and date and date < earliest:
            entry.update(category=LEGACY_ONLY,
                         reason="predates the canonical ledger's earliest record (%s); "
                                "no canonical counterpart can exist" % earliest)
        elif latest and date and date > latest:
            entry.update(category=OUT_OF_CANONICAL_WINDOW,
                         reason="postdates the canonical ledger's latest record (%s) -- "
                                "inside the settlement/ingest outage window; a "
                                "counterpart is expected once the corpus is restored"
                                % latest)
        else:
            entry.update(category=OTHER,
                         reason="inside the canonical ledger's date range, carries "
                                "execution-shaped fields, yet no canonical counterpart "
                                "matched -- needs human review before any import")
        out_root.append(entry)

    out_canonical = []
    for row in canonical_rows:
        if id(row) in matched_canonical:
            continue
        entry = {
            "ledger": "canonical",
            "betId": row.get("betId"),
            "date": str(row.get("gameDate") or "")[:10],
            "matchup": row.get("matchup"),
            "marketFamily": row.get("marketFamily"),
            "result": row.get("result"),
            "entryMethod": row.get("entryMethod"),
        }
        if row.get("importBatchId"):
            entry.update(category=SYNTHETIC_OR_MANUAL,
                         reason="manually imported batch %r -- a user-confirmed wager "
                                "recorded directly into the canonical ledger, which the "
                                "root ledger never carried" % row.get("importBatchId"))
        else:
            entry.update(category=CANONICAL_ONLY,
                         reason="canonical row with no root counterpart on "
                                "(date, teams, market family)")
        out_canonical.append(entry)

    counts = {}
    for entry in out_root + out_canonical:
        counts[entry["category"]] = counts.get(entry["category"], 0) + 1

    return out_root, out_canonical, counts, {"earliest": earliest, "latest": latest}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Reconcile the two bet ledgers")
    parser.add_argument("--root-bets", default=os.path.join(ROOT_DIR, "bets.json"))
    parser.add_argument("--canonical-bets",
                        default=os.path.join(ROOT_DIR, "data", "edgelab", "bets", "bets.jsonl"))
    parser.add_argument("--write-artifact", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    with open(args.root_bets) as f:
        root_bets = json.load(f)
    canonical_rows = []
    if os.path.exists(args.canonical_bets):
        with open(args.canonical_bets) as f:
            for line in f:
                line = line.strip()
                if line:
                    canonical_rows.append(json.loads(line))

    out_root, out_canonical, counts, window = reconcile(root_bets, canonical_rows)

    payload = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generatedBy": "scripts/reconcile_bet_ledgers.py",
        "wave": "REMEDIATION_WAVE_0",
        "mode": "MEASUREMENT_ONLY",
        "rootLedgerRows": len(root_bets),
        "canonicalLedgerRows": len(canonical_rows),
        "difference": len(root_bets) - len(canonical_rows),
        "canonicalWindow": window,
        "counts": counts,
        "note": (
            "Measurement only. No bet was imported, created, modified or deleted in "
            "either ledger. A recommendation is never assumed to have been placed: "
            "RECOMMENDATION_NOT_PLACED rows are correctly absent from the canonical "
            "ledger and must not be imported to make the counts match."
        ),
        "rows": out_root + out_canonical,
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("BET LEDGER RECONCILIATION (measurement only)")
        print("=" * 78)
        print("root bets.json          : %d rows" % len(root_bets))
        print("canonical bets.jsonl    : %d rows" % len(canonical_rows))
        print("difference              : %d" % (len(root_bets) - len(canonical_rows)))
        print("canonical window        : %s .. %s" % (window["earliest"], window["latest"]))
        print()
        for category in (REPRESENTED_CANONICALLY, RECOMMENDATION_NOT_PLACED, LEGACY_ONLY,
                         OUT_OF_CANONICAL_WINDOW, CANONICAL_ONLY, SYNTHETIC_OR_MANUAL,
                         IDENTITY_UNRESOLVED, LIFECYCLE_MISMATCH, OTHER):
            if category in counts:
                print("  %-26s %4d" % (category, counts[category]))
        total = sum(counts.values())
        print()
        print("  %-26s %4d  (= %d root + %d unmatched canonical)"
              % ("TOTAL CLASSIFIED", total, len(out_root), len(out_canonical)))
        assert total == len(out_root) + len(out_canonical)

    if args.write_artifact:
        out_dir = os.path.join(ROOT_DIR, "data", "edgelab", "operational_health")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "bet_ledger_reconciliation.json")
        write_json_atomic(payload, out_path, indent=2)
        print("\nartifact: %s" % os.path.relpath(out_path, ROOT_DIR))

    return 0


if __name__ == "__main__":
    sys.exit(main())
