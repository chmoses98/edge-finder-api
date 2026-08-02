#!/usr/bin/env python3
"""
scripts/remediate_bet_backlog.py
=====================================
Production Reliability and Settlement Recovery milestone: classifies
every non-terminal (pending/open) record in bets.json into one of eight
categories, using ONLY evidence already present in this repository (no
live network access -- this environment has none, and even where it
exists elsewhere, a settlement result must never be guessed), and
produces a machine-readable remediation PLAN. Dry-run by default; never
mutates bets.json unless invoked with --execute AND the plan contains at
least one change classified as safe to apply automatically (see
SAFE_TO_AUTO_APPLY below) -- see docs/INCIDENT_2026-07-31_CLV_COMMIT_FAILURE.md
and docs/POSTMORTEM_PRODUCTION_RELIABILITY_2026.md for the investigation
this tool's categories are drawn from.

Classification categories (see classify_bet()):
  legitimately_pending    -- too recent to expect settlement yet
  settleable_from_evidence -- a real, already-committed local artifact
                              (Settlement/score data) resolves this bet;
                              NEVER populated by guessing, only by a real
                              match. In practice this repo has no local
                              post-game score archive today (pregame
                              slate snapshots only), so this category is
                              usually empty -- see the "why 0" note in
                              build_plan()'s report.
  missing_source_data     -- placed before the settlement workflow
                              (clv-update.yml, created 2026-06-10) existed
  malformed_record        -- no `date`, or `game` unparseable into two
                              team abbreviations
  duplicate               -- a byte-for-byte content duplicate of another
                              record (comparing every field except `id`)
  unsupported_market_family -- NRFI/YRFI: clv_update.py's own
                              determine_result() permanently routes these
                              to manual settlement (no automated path
                              exists in production at all, confirmed at
                              clv_update.py's `if canonical_mkt in ('NRFI',
                              'YRFI'): nrfi_yrfi_manual.append(...)`)
  pipeline_failure        -- date matches a clv-update.yml run that is
                              independently confirmed (via the GitHub
                              Actions run history investigated for this
                              milestone) to have failed
  requires_manual_review  -- well-formed, old enough, a supported market
                              family, no known automated-pipeline-failure
                              match -- genuinely unclear why still pending
                              without live MLB/Kalshi API access to
                              investigate further; never guessed at.

This module performs NO financial-outcome mutation (no WIN/LOSS/pl is
ever written) -- see docs/POSTMORTEM_PRODUCTION_RELIABILITY_2026.md's
"why zero bets were auto-remediated" section for why that bar was not
met in this run, and lib/bet_backlog_classifier.py for the reusable,
tested classification functions this script's CLI wraps.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from atomic_json import write_json_atomic
from bet_backlog_classifier import build_plan

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BETS_PATH = os.path.join(ROOT, "bets.json")
BACKUP_DIR = os.path.join(ROOT, "data", "backups")
PLAN_PATH = os.path.join(ROOT, "data", "bet_backlog_remediation_plan.json")


def _load_bets(path):
    with open(path) as f:
        return json.load(f)


def _write_backup(bets, now_iso):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = now_iso.replace(":", "").replace("-", "")
    backup_path = os.path.join(BACKUP_DIR, f"bets_json_backup_{ts}.json")
    write_json_atomic(bets, backup_path, indent=2)
    return backup_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bets-path", default=BETS_PATH, help="Path to bets.json (default: repo root)")
    parser.add_argument("--date-from", default=None, help="Only classify/remediate bets with date >= this (YYYY-MM-DD)")
    parser.add_argument("--date-to", default=None, help="Only classify/remediate bets with date <= this (YYYY-MM-DD)")
    parser.add_argument("--execute", action="store_true", help="Apply the plan's auto-safe changes (default: dry-run, no mutation)")
    parser.add_argument("--plan-out", default=PLAN_PATH, help="Where to write the machine-readable plan JSON")
    args = parser.parse_args()

    bets = _load_bets(args.bets_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    today = now_iso[:10]

    plan = build_plan(bets, today=today, date_from=args.date_from, date_to=args.date_to)
    plan["generatedAt"] = now_iso
    plan["betsPath"] = args.bets_path
    plan["mode"] = "EXECUTE" if args.execute else "DRY_RUN"

    os.makedirs(os.path.dirname(args.plan_out), exist_ok=True)
    write_json_atomic(plan, args.plan_out, indent=2)

    print(f"[remediate_bet_backlog] mode={plan['mode']}")
    print(f"[remediate_bet_backlog] bets considered: {plan['totalConsidered']}")
    print("[remediate_bet_backlog] classification counts:")
    for category, n in sorted(plan["classificationCounts"].items()):
        print(f"    {category}: {n}")
    print(f"[remediate_bet_backlog] auto-safe changes proposed: {len(plan['autoSafeChanges'])}")
    print(f"[remediate_bet_backlog] plan written to {args.plan_out}")

    if not args.execute:
        print("[remediate_bet_backlog] DRY RUN -- bets.json was not modified. Re-run with --execute to apply autoSafeChanges.")
        return 0

    if not plan["autoSafeChanges"]:
        print("[remediate_bet_backlog] --execute passed but there is nothing safe to auto-apply -- bets.json was not modified.")
        return 0

    backup_path = _write_backup(bets, now_iso)
    print(f"[remediate_bet_backlog] Backup written to {backup_path}")

    by_id = {b.get("id"): b for b in bets if b.get("id")}
    applied = 0
    for change in plan["autoSafeChanges"]:
        bet = by_id.get(change["id"])
        if bet is None:
            continue
        if bet.get(change["field"]) != change["before"]:
            # The record changed since the plan was built -- never apply a
            # stale change blindly.
            print(f"    SKIPPED {change['id']}: {change['field']} no longer equals the planned 'before' value")
            continue
        bet[change["field"]] = change["after"]
        applied += 1

    write_json_atomic(bets, args.bets_path, indent=2)
    print(f"[remediate_bet_backlog] Applied {applied} of {len(plan['autoSafeChanges'])} planned changes. bets.json updated, backup preserved at {backup_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
