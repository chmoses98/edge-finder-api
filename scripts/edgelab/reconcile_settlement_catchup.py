#!/usr/bin/env python3
"""
scripts/edgelab/reconcile_settlement_catchup.py
===================================================
Destination-side settlement catch-up for the canonical EdgeLab wager
ledger.

THE PROBLEM
-----------
A wager can land in data/edgelab/bets/bets.jsonl at any time -- the
Kalshi bet router imports through the same canonical write path every
other entry surface uses, and it can do so hours AFTER "EdgeLab
Postgame Settlement" already ran for that wager's date. That bet then
sits ungraded until a human notices and manually dispatches a
settlement rerun.

THE FIX
-------
This is not a second settlement system. It is a scheduler in front of
the existing one:

    1. figure out which wager dates actually need another look
       (lib.edgelab.settlement_reconciliation -- pure, no I/O)
    2. for each of them, run the CANONICAL pipeline, unchanged:
         scripts/edgelab/ingest_market_observations.py  (Market/Game rows
             for the date, from already-archived Kalshi snapshots -- zero
             Kalshi API calls; without it a ticker nobody had archived
             yet has nothing to settle against)
         scripts/edgelab/repair_game_identity.py        (resolve mlbGamePk)
         scripts/edgelab/settle_markets.py's settle_date()  (THE settler)
         scripts/edgelab/generate_daily_report.py       (only when
             settlement actually changed canonical state)

settle_date() is imported and called directly -- exactly as
scripts/edgelab/backfill_player_prop_settlement.py already does. There
is deliberately no second grading implementation anywhere in this file.

WHAT IT WILL NEVER DO
---------------------
It never decides an outcome. Every grade comes from settle_date(),
which records SETTLEMENT_UNRESOLVED with an explicit reason whenever
the game is not final, the linescore/boxscore fetch fails, the gamePk
is unresolved, or the market family has no settlement support at all.
A market family the canonical settlement system does not support stays
unresolved here too -- this script adds no family-specific knowledge of
its own, so it cannot invent support that does not exist.

It also never records a recommendation as a placed wager: it only ever
reads rows that are ALREADY in the canonical ledger because a
user-confirmed entry surface put them there.

IDEMPOTENCE
-----------
Every step it calls is idempotent (upsert by settlementId/betId), and a
second run over an unchanged world reports zero meaningful changes,
regenerates no report, and therefore produces no commit. The `--verify-
idempotent` flag proves this in-process by running the same date set
twice and asserting the second pass changed nothing.

Usage:
    # everything the recent window still owes (the scheduled sweep)
    python3 scripts/edgelab/reconcile_settlement_catchup.py --lookback-days 5

    # exactly what a push to the canonical ledger changed
    python3 scripts/edgelab/reconcile_settlement_catchup.py --changed-from <sha>

    # recovery / debugging
    python3 scripts/edgelab/reconcile_settlement_catchup.py --date 2026-09-15
    python3 scripts/edgelab/reconcile_settlement_catchup.py --start-date 2026-09-10 --end-date 2026-09-15
    python3 scripts/edgelab/reconcile_settlement_catchup.py --lookback-days 3 --dry-run
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import settlement_reconciliation as recon
from lib.edgelab import storage
from scripts.edgelab.settle_markets import settle_date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RECEIPT_DIR = os.path.join("data", "edgelab", "operational_health")
RECEIPT_PATH = os.path.join(RECEIPT_DIR, "settlement_reconciliation_receipt.json")
STATUS_PATH = os.path.join(RECEIPT_DIR, "settlement_reconciliation_status.json")


def _run(script_rel_path, args, label):
    """
    Run one canonical CLI as a subprocess. Failures are recorded as a
    warning on this date's result, never raised: a date whose optional
    ingest/identity-repair step failed must still get its settlement
    attempt (which will simply leave what it cannot prove unresolved),
    and one bad date must never abort the remaining dates.
    """
    # Deliberately NOT `cwd=ROOT`: every canonical script this calls --
    # and settle_date() itself -- resolves data/edgelab/... RELATIVE to the
    # process working directory. Pinning subprocesses to the repository
    # root while the in-process settler followed os.getcwd() would make
    # the two halves of one reconciliation operate on two different data
    # trees (harmless in production, where the workflow runs from the
    # root; corrupting in any sandboxed/test invocation). One working
    # directory, whatever the caller chose.
    cmd = [sys.executable, os.path.join(ROOT, script_rel_path)] + list(args)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except Exception as exc:  # noqa: BLE001 -- a broken step is data, not a crash
        return {"step": label, "ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        return {"step": label, "ok": False, "reason": f"exit {proc.returncode}: {' | '.join(tail)}"}
    return {"step": label, "ok": True, "reason": None}


def read_ledger_rows():
    return list(storage.read_records(recon.BETS_LEDGER_PATH))


def git_show(rev, path):
    """
    The file's content at `rev`, or None when that revision has no such
    file (a commit predating the ledger, a bad SHA, a shallow clone that
    does not contain it). None is a legitimate answer -- analyze_ledger_
    change treats "no before content" as "every current row is new",
    which is the correct fail-safe direction: reconcile more, never less.
    """
    try:
        proc = subprocess.run(
            ["git", "show", f"{rev}:{path}"], cwd=ROOT,
            capture_output=True, text=True, timeout=120,  # git needs the repo, not the data cwd
        )
    except Exception:  # noqa: BLE001
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def reconcile_date(date, *, dry_run=False, skip_ingest=False, skip_report=False):
    """
    Run the canonical pipeline for ONE date. Returns a per-date result
    dict; never raises.
    """
    steps = []
    if not dry_run and not skip_ingest:
        # Canonical ingest: reads data/kalshi_registry_snapshots/*.json
        # already committed by the capture workflows. Makes ZERO Kalshi
        # API calls (see that script's docstring) and is idempotent by
        # marketObservationId. Needed because a bet can name a ticker
        # whose Market row was never ingested for this date -- settlement
        # iterates the markets partition, so an un-ingested ticker is a
        # bet that can never be found, no matter how final the game is.
        steps.append(_run("scripts/edgelab/ingest_market_observations.py", ["--date", date], "ingest_market_observations"))
        steps.append(_run("scripts/edgelab/repair_game_identity.py", ["--date", date], "repair_game_identity"))

    pending_before = recon.pending_wager_counts(read_ledger_rows(), [date]).get(date, 0)

    error = None
    summary = None
    try:
        summary = settle_date(date, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 -- one bad date must not abort the rest
        error = f"{type(exc).__name__}: {exc}"

    changed = recon.settlement_changed_canonical_state(summary)

    report_regenerated = False
    if changed and not dry_run and not skip_report:
        result = _run("scripts/edgelab/generate_daily_report.py", ["--date", date], "generate_daily_report")
        steps.append(result)
        report_regenerated = result["ok"]

    pending_after = recon.pending_wager_counts(read_ledger_rows(), [date]).get(date, 0)

    return {
        "date": date,
        "error": error,
        "steps": steps,
        "counts": (summary or {}).get("counts") or {},
        "byFamily": (summary or {}).get("byFamily") or {},
        "unresolvedReasonsByFamily": (summary or {}).get("unresolvedReasonsByFamily") or {},
        "settlementWarnings": (summary or {}).get("warnings") or [],
        "changedCanonicalState": changed,
        "reportRegenerated": report_regenerated,
        "pendingWagersBefore": pending_before,
        "pendingWagersAfter": pending_after,
    }


def resolve_dates(args):
    """
    Decide which dates this run processes, and record how it decided.
    Returns (dates, ledger_analysis, sweep_window, refusal_reason).
    """
    explicit = []
    if args.date:
        explicit.extend(args.date)
    if args.start_date and args.end_date:
        explicit.extend(recon.date_range(args.start_date, args.end_date))
    elif bool(args.start_date) != bool(args.end_date):
        return [], None, None, "--start-date and --end-date must be given together"

    ledger_analysis = None
    ledger_dates = []
    if args.changed_from and not explicit:
        before = git_show(args.changed_from, recon.BETS_LEDGER_PATH)
        after_rows_text = None
        if os.path.exists(recon.BETS_LEDGER_PATH):
            with open(recon.BETS_LEDGER_PATH, encoding="utf-8") as f:
                after_rows_text = f.read()
        ledger_analysis = recon.analyze_ledger_change(before, after_rows_text)
        ledger_analysis["baseRevision"] = args.changed_from
        ledger_analysis["baseRevisionResolved"] = before is not None
        if before is None:
            # A base revision we cannot resolve (a branch's first push,
            # a shallow clone, a bad SHA) would make analyze_ledger_change
            # report EVERY row in the ledger as brand new -- i.e. every
            # date the system has ever wagered on. That is not a
            # reconciliation, it is a full historical replay, and it would
            # trip select_dates' ceiling and abort the run. Drop the diff
            # signal entirely in that case and let the lookback sweep
            # below (which is bounded and bet-driven) decide -- the sweep
            # is precisely the backstop that exists for "the push signal
            # was unusable".
            ledger_analysis["unusableBaseRevisionFallback"] = "LOOKBACK_SWEEP_ONLY"
            ledger_dates = []
        else:
            ledger_dates = ledger_analysis["affectedDates"]

    sweep_window = None
    sweep_dates = []
    if not explicit and args.lookback_days:
        start, end = recon.lookback_window(args.lookback_days)
        sweep_window = {"startDate": start, "endDate": end, "lookbackDays": args.lookback_days}
        sweep_dates = recon.pending_wager_dates(read_ledger_rows(), start, end)

    dates, refusal = recon.select_dates(
        explicit=explicit, ledger_change_dates=ledger_dates, sweep_dates=sweep_dates,
        max_dates=args.max_dates,
    )
    return dates, ledger_analysis, sweep_window, refusal


def write_receipt(receipt, receipt_out):
    os.makedirs(os.path.dirname(receipt_out) or ".", exist_ok=True)
    with open(receipt_out, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2, sort_keys=True)

    # Rolling status file: the small, stable thing a human (or a fresh
    # ChatGPT session asking "is yesterday finished?") can read without
    # parsing a full receipt.
    #
    # A dry run deliberately does NOT touch it. A dry run computes what
    # WOULD happen; letting it overwrite the record of what actually did
    # happen would make the one file people trust for "is yesterday
    # finished?" describe a run that wrote nothing. The receipt above is
    # still written, which is the whole point of a dry run.
    if receipt.get("dryRun"):
        return

    status = {
        "schemaVersion": "1",
        "lastRunAt": receipt["completedAt"],
        "lastRunTrigger": receipt["trigger"],
        "lastRunDryRun": receipt["dryRun"],
        "lastRunCounts": receipt["counts"],
        "lastRunDates": receipt["datesConsidered"],
        "lastRunRefusalReason": receipt["refusalReason"],
        "stillPendingByDate": {
            d["date"]: d.get("pendingWagersAfter", 0) for d in receipt["perDate"]
            if d.get("pendingWagersAfter")
        },
    }
    os.makedirs(os.path.dirname(STATUS_PATH) or ".", exist_ok=True)
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, sort_keys=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", action="append", default=None, help="Explicit date YYYY-MM-DD (repeatable)")
    parser.add_argument("--start-date", default=None, help="First date of an inclusive explicit range")
    parser.add_argument("--end-date", default=None, help="Last date of an inclusive explicit range")
    parser.add_argument("--changed-from", default=None,
                        help="Git revision to diff the canonical bet ledger against (push-triggered path)")
    parser.add_argument("--lookback-days", type=int, default=recon.DEFAULT_LOOKBACK_DAYS,
                        help=f"Sweep window for still-pending wagers (default {recon.DEFAULT_LOOKBACK_DAYS}; 0 disables)")
    parser.add_argument("--max-dates", type=int, default=recon.MAX_DATES_PER_RUN)
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute everything; write only the receipt -- never settlements, "
                             "the bet ledger, a daily report, or the rolling status file")
    parser.add_argument("--skip-ingest", action="store_true",
                        help="Skip the market-observation ingest/identity-repair steps (settlement only)")
    parser.add_argument("--skip-report", action="store_true", help="Never regenerate daily reports")
    parser.add_argument("--verify-idempotent", action="store_true",
                        help="Run the same date set a second time and fail if anything changed on the second pass")
    parser.add_argument("--receipt-out", default=RECEIPT_PATH)
    parser.add_argument("--trigger", default=os.environ.get("RECONCILE_TRIGGER", "manual"))
    args = parser.parse_args()

    started_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dates, ledger_analysis, sweep_window, refusal = resolve_dates(args)

    per_date = []
    for date in dates:
        per_date.append(reconcile_date(
            date, dry_run=args.dry_run, skip_ingest=args.skip_ingest, skip_report=args.skip_report,
        ))

    completed_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    receipt = recon.build_receipt(
        dates=dates, per_date=per_date, trigger=args.trigger, dry_run=args.dry_run,
        started_at=started_at, completed_at=completed_at, ledger_analysis=ledger_analysis,
        sweep_window=sweep_window, refusal_reason=refusal,
        github_run_id=os.environ.get("GITHUB_RUN_ID"),
    )
    write_receipt(receipt, args.receipt_out)

    print(f"[reconcile_settlement_catchup] trigger={args.trigger} dates={dates or '[]'} "
          f"changed={receipt['counts']['datesChanged']} betsSettled={receipt['counts']['betsSettled']} "
          f"reportsRegenerated={receipt['counts']['reportsRegenerated']} "
          f"stillPending={receipt['counts']['wagersStillPending']}")
    if refusal:
        print(f"[reconcile_settlement_catchup] REFUSED: {refusal}", file=sys.stderr)
        return 1
    for row in per_date:
        if row["error"]:
            print(f"  {row['date']}: ERROR {row['error']}", file=sys.stderr)
        for step in row["steps"]:
            if not step["ok"]:
                print(f"  {row['date']}: step {step['step']} failed -- {step['reason']}", file=sys.stderr)

    if args.verify_idempotent and not args.dry_run and dates:
        second = [reconcile_date(d, dry_run=False, skip_ingest=True, skip_report=True) for d in dates]
        offenders = [r["date"] for r in second if r["changedCanonicalState"]]
        if offenders:
            print(f"[reconcile_settlement_catchup] NOT IDEMPOTENT: second pass changed {offenders}", file=sys.stderr)
            return 1
        print("[reconcile_settlement_catchup] idempotence verified: second pass changed nothing")

    if any(r["error"] for r in per_date):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
