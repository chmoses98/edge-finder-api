"""
lib/edgelab/settlement_reconciliation.py
============================================
Destination-side settlement catch-up: the PURE decision layer for
"which wager dates does the canonical ledger still owe settlement for?"

WHY THIS EXISTS
---------------
A wager can reach the canonical EdgeLab ledger
(data/edgelab/bets/bets.jsonl) at ANY time -- including hours after
"EdgeLab Postgame Settlement" already ran for that wager's game date.
The Kalshi bet router imports through the same canonical write path
every other entry surface uses (lib.edgelab.bets.write_placed_bet, via
scripts/edgelab/import_bet_batch.py), so a late import is a perfectly
ordinary, valid ledger append -- it just misses the one nightly
settlement pass that would have graded it.

Before this module, the only repair was a human noticing and manually
dispatching a settlement rerun for the right date.

The fix is deliberately DESTINATION-SIDE and does not depend on the
router (or any other import surface) remembering to fire a special
"now settle this" event:

  * the canonical ledger landing on main IS the trigger (a push event
    scoped to that one file), and
  * a scheduled sweep over a recent lookback window catches everything
    the push path could ever miss -- most importantly the completely
    normal case of a bet imported BEFORE its game ends, which cannot
    settle at import time no matter who signals what.

WHAT THIS MODULE DOES NOT DO
----------------------------
It never settles anything and never decides an outcome. It only ever
answers "which dates are worth re-running the canonical settlement path
for". The settlement itself stays exactly where it already lives --
scripts/edgelab/settle_markets.py's settle_date(), reused unchanged by
scripts/edgelab/reconcile_settlement_catchup.py the same way
scripts/edgelab/backfill_player_prop_settlement.py already reuses it.
That function is the ONLY code in this repository that grades a market,
and it leaves anything it cannot prove SETTLEMENT_UNRESOLVED rather
than guessing. Nothing here can weaken that.

RECURSION SAFETY
----------------
Settlement writes back onto the same bets.jsonl this module watches
(status/result/netProfitLoss/... -- see SETTLEMENT_MAINTAINED_FIELDS).
A naive "the ledger changed, reconcile" rule would therefore chase its
own tail. Two independent brakes:

  1. DATA-LEVEL (this module): a row whose ONLY differing fields are
     ones settlement itself maintains is an ECHO of a previous
     reconciliation, not a new wager -- see
     classify_ledger_row_change(). Echo-only dates never enter the
     affected set.
  2. WORKFLOW-LEVEL: the reconcile workflow skips a push whose head
     commit carries this module's own commit-message marker
     (RECONCILE_COMMIT_MARKER), and only commits when something
     actually changed.

Either brake alone terminates; both are kept because they fail in
different directions (1 survives a commit-message change, 2 survives a
schema change to the bet record).
"""
import hashlib
import json
from datetime import date as _date
from datetime import datetime, timedelta, timezone

BETS_LEDGER_PATH = "data/edgelab/bets/bets.jsonl"

# Commit-message prefix every reconciliation commit carries. The
# workflow's own push-recursion guard greps for this, so it must stay
# stable and must never be reused by another workflow.
RECONCILE_COMMIT_MARKER = "edgelab settlement reconcile"

# The exact PlacedBet fields the canonical settlement path itself writes
# back onto a bet row. Source of truth:
# lib.edgelab.settlement.settle_bets_for_ticker (what it computes) and
# bet_needs_settlement_update (what it compares before persisting).
# A ledger row whose only changed fields are in this set was changed BY
# reconciliation/postgame settlement, so it is an echo, never a new
# wager needing a fresh settlement attempt.
SETTLEMENT_MAINTAINED_FIELDS = frozenset({
    "status",
    "result",
    "netProfitLoss",
    "returnAmount",
    "realizedROI",
    "grossSettlementPayout",
    "grossCashReturned",
    "confirmedReceiptSettlementComparison",
    "settlementRefusalReason",
    "settlementRefusalClass",
    "updatedAt",
})

# A bet in one of these record states is never a settlement candidate:
# CANCELLED means "logged in error, not a real wager" (see
# lib.edgelab.bets.cancel_placed_bet), and settle_markets.py already
# skips it outright.
NON_WAGER_RECORD_STATUSES = frozenset({"CANCELLED"})

DEFAULT_LOOKBACK_DAYS = 5

# Hard ceiling on how many dates one reconciliation run may process.
# settle_date() makes live MLB Stats API calls per game, so an
# unbounded date set (a mis-typed range, a corrupted ledger) must never
# turn into thousands of fetches. Exceeding it is an explicit, visible
# refusal -- never a silent truncation (see select_dates()).
MAX_DATES_PER_RUN = 45


# ── JSONL parsing (tolerant, never raises on a partial/legacy line) ──────

def parse_ledger_text(text):
    """
    Pure. Parse newline-delimited JSON bet rows out of `text` (which may
    be None/empty -- e.g. `git show <sha>:path` for a commit predating
    the file). A line that is blank or not a JSON object is skipped
    rather than raising: this module's job is to decide what to
    re-settle, and it must never be the thing that crashes a
    reconciliation run.
    """
    rows = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def index_by_bet_id(rows):
    """Pure. {betId: row} for every row carrying a betId. A later row
    with a duplicate betId wins (matches storage.upsert_records, where
    the last write for an id is the live one)."""
    return {r["betId"]: r for r in rows if r.get("betId")}


def bet_game_date(bet):
    """
    Pure. The wager DATE a bet belongs to, using the exact same
    convention scripts/edgelab/generate_daily_report.py uses to decide
    which bets appear in a date's report: gameDate first, entryTimestamp's
    date only as a fallback for older rows that predate gameDate.
    A timestamp-free canonical manual import (the router's normal shape)
    always has a real gameDate. None when neither is present -- never
    guessed from anything else.
    """
    game_date = bet.get("gameDate")
    if game_date:
        return str(game_date)[:10]
    entry = bet.get("entryTimestamp")
    if entry:
        return str(entry)[:10]
    return None


def is_settlement_candidate(bet):
    """
    Pure. True iff this row is a REAL wager that is still waiting on a
    result. Deliberately conservative: it only ever asks "is this row
    still ungraded", never "should it have won" -- nothing here can
    produce, imply, or default an outcome.
    """
    if (bet.get("recordStatus") or "ACTIVE") in NON_WAGER_RECORD_STATUSES:
        return False
    if bet.get("result") is not None:
        return False
    return (bet.get("status") or "") != "settled"


# ── Ledger-change classification ────────────────────────────────────────

CHANGE_NEW = "NEW_WAGER"
CHANGE_MATERIAL = "MATERIAL_CHANGE"
CHANGE_SETTLEMENT_ECHO = "SETTLEMENT_ECHO"
CHANGE_NONE = "UNCHANGED"


def classify_ledger_row_change(before_row, after_row):
    """
    Pure. How did one bet row change between two ledger revisions?

      NEW_WAGER        -- the betId did not exist before (a router/manual
                          import, the case this whole mission exists for).
      MATERIAL_CHANGE  -- an existing row changed in at least one field
                          settlement does NOT itself maintain (a corrected
                          ticker/stake/price, a re-linked recommendation,
                          a cancellation).
      SETTLEMENT_ECHO  -- the row changed ONLY in fields settlement itself
                          writes. This is reconciliation (or the nightly
                          postgame pass) seeing its own previous write --
                          re-settling because of it would be an infinite
                          loop that could never produce new information.
      UNCHANGED        -- byte-equivalent content.
    """
    if before_row is None:
        return CHANGE_NEW
    changed_fields = {
        key for key in set(before_row) | set(after_row)
        if before_row.get(key) != after_row.get(key)
    }
    if not changed_fields:
        return CHANGE_NONE
    if changed_fields <= SETTLEMENT_MAINTAINED_FIELDS:
        return CHANGE_SETTLEMENT_ECHO
    return CHANGE_MATERIAL


def analyze_ledger_change(before_text, after_text):
    """
    Pure. Compare two revisions of the canonical bet ledger and report
    which wager dates were ACTUALLY affected.

    Returns:
      {"affectedDates": [...sorted YYYY-MM-DD...],
       "echoOnlyDates": [...],          (changed, but settlement-echo only)
       "counts": {"newWagers", "materialChanges", "settlementEchoes",
                  "unchanged", "removed", "undatedRows"},
       "sampleBetIds": {"NEW_WAGER": [...], "MATERIAL_CHANGE": [...]}}

    A row that DISAPPEARS between revisions is counted but never makes a
    date affected: settlement cannot act on a row that is no longer in
    the ledger, and this module never resurrects anything.
    """
    before = index_by_bet_id(parse_ledger_text(before_text))
    after = index_by_bet_id(parse_ledger_text(after_text))

    affected = set()
    echo_only = set()
    counts = {
        "newWagers": 0, "materialChanges": 0, "settlementEchoes": 0,
        "unchanged": 0, "removed": 0, "undatedRows": 0,
    }
    samples = {CHANGE_NEW: [], CHANGE_MATERIAL: []}

    for bet_id, after_row in after.items():
        change = classify_ledger_row_change(before.get(bet_id), after_row)
        game_date = bet_game_date(after_row)
        if change == CHANGE_NEW:
            counts["newWagers"] += 1
        elif change == CHANGE_MATERIAL:
            counts["materialChanges"] += 1
        elif change == CHANGE_SETTLEMENT_ECHO:
            counts["settlementEchoes"] += 1
        else:
            counts["unchanged"] += 1
            continue

        if game_date is None:
            counts["undatedRows"] += 1
            continue
        if change in (CHANGE_NEW, CHANGE_MATERIAL):
            affected.add(game_date)
            if len(samples[change]) < 10:
                samples[change].append(bet_id)
        else:
            echo_only.add(game_date)

    counts["removed"] = len(set(before) - set(after))

    return {
        "affectedDates": sorted(affected),
        "echoOnlyDates": sorted(echo_only - affected),
        "counts": counts,
        "sampleBetIds": {"NEW_WAGER": samples[CHANGE_NEW], "MATERIAL_CHANGE": samples[CHANGE_MATERIAL]},
    }


# ── Lookback sweep ──────────────────────────────────────────────────────

def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, _date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def date_range(start, end):
    """Pure. Inclusive list of YYYY-MM-DD strings. Empty when end < start
    (never silently swapped -- a reversed range is the caller's bug and
    is surfaced by producing nothing, which the CLI reports)."""
    start_d, end_d = _as_date(start), _as_date(end)
    out = []
    current = start_d
    while current <= end_d:
        out.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return out


def lookback_window(days, today=None):
    """
    Pure. (start, end) YYYY-MM-DD for a `days`-long window ending on
    `today` INCLUSIVE. Today is included on purpose: a bet placed on an
    afternoon game can be imported, finish, and need settling all on the
    same calendar day.
    """
    end = _as_date(today or datetime.now(tz=timezone.utc))
    start = end - timedelta(days=max(0, int(days) - 1))
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def pending_wager_dates(rows, start_date=None, end_date=None):
    """
    Pure. Every wager date in [start_date, end_date] that still holds at
    least one ungraded real wager. This is what makes "imported before
    the game ended" settle automatically later: the bet simply stays a
    candidate until a settlement run can actually prove a result.
    """
    dates = set()
    for bet in rows:
        if not is_settlement_candidate(bet):
            continue
        game_date = bet_game_date(bet)
        if game_date is None:
            continue
        if start_date and game_date < str(start_date)[:10]:
            continue
        if end_date and game_date > str(end_date)[:10]:
            continue
        dates.add(game_date)
    return sorted(dates)


def pending_wager_counts(rows, dates):
    """Pure. {date: number of still-ungraded real wagers} -- reporting
    only, so a receipt can say what was still open before and after."""
    wanted = set(dates)
    counts = {d: 0 for d in wanted}
    for bet in rows:
        if not is_settlement_candidate(bet):
            continue
        game_date = bet_game_date(bet)
        if game_date in wanted:
            counts[game_date] += 1
    return counts


def select_dates(explicit=None, ledger_change_dates=None, sweep_dates=None,
                 max_dates=MAX_DATES_PER_RUN):
    """
    Pure. Merge the three independent date sources into the ONE ordered,
    de-duplicated list a run will process, oldest first.

    An explicit caller-supplied set (workflow_dispatch date / range,
    the recovery path) wins outright and suppresses the other two --
    a human asking for one specific date must get exactly that date.

    Returns (dates, refusal_reason). refusal_reason is None on success;
    when more than `max_dates` dates are selected NOTHING is returned
    and the reason says so, rather than silently processing a truncated
    prefix and reporting success.
    """
    if explicit:
        selected = sorted({str(d)[:10] for d in explicit})
    else:
        selected = sorted({str(d)[:10] for d in (list(ledger_change_dates or []) + list(sweep_dates or []))})
    if len(selected) > max_dates:
        return [], (
            f"refusing to process {len(selected)} dates in one run (limit {max_dates}); "
            f"narrow the range or raise --max-dates deliberately"
        )
    return selected, None


# ── Post-settlement decisions ───────────────────────────────────────────

def settlement_changed_canonical_state(summary):
    """
    Pure. Did this date's settle_date() summary actually change canonical
    state? Only then is regenerating the daily report worth doing --
    lib.edgelab.reports stamps a fresh `generatedAt` on every build, so
    an unconditional regeneration would produce a byte-diff (and a
    commit) on every single run with no new information in it.

    `settlementsMeaningfullyChanged` is settle_markets.py's own
    already-computed "content is new or genuinely different" counter
    (not the raw upsert `settlementsUpdated`, which counts every touched
    row); `betsSettled` is the count of bets whose grade actually moved.
    """
    if not summary:
        return False
    counts = summary.get("counts") or {}
    return bool(counts.get("settlementsMeaningfullyChanged") or counts.get("betsSettled"))


# ── Global pending status (NOT last-run scoped) ─────────────────────────

def build_global_pending_status(ledger_rows, *, as_of=None, run_summary=None):
    """
    Pure. The answer to "what settlement does this system still owe?",
    derived from the ENTIRE canonical ledger -- never from the dates one
    run happened to touch.

    THE BUG THIS EXISTS TO PREVENT
    ------------------------------
    The first version of the rolling status built `stillPendingByDate`
    from the current run's `perDate` list. A push-triggered run that
    reconciled only 2026-09-17 therefore rewrote the status with ONLY
    that date -- silently erasing 2026-09-16's still-unresolved wager
    from the one file a human (or a fresh chat) reads to answer "what is
    still open?". A status that can lose outstanding work is worse than
    no status at all.

    So this function ignores the run entirely for the pending set. Every
    ungraded, ACTIVE, user-confirmed wager in the ledger is counted,
    whatever date it belongs to and whether or not this run looked at it.

    `run_summary` is attached alongside purely as provenance ("this is
    the run that last refreshed this file"), never as the source of the
    pending set.

    Never asserts an outcome: a wager appears here precisely BECAUSE the
    system has not proven one, and a refusal class is reported verbatim
    when the ledger carries one.
    """
    pending = []
    for bet in ledger_rows or []:
        if not is_settlement_candidate(bet):
            continue
        pending.append({
            "betId": bet.get("betId"),
            "gameDate": bet_game_date(bet),
            "marketTicker": bet.get("marketTicker"),
            "marketFamily": bet.get("marketFamily"),
            "trackingType": bet.get("trackingType"),
            "settlementRefusalReason": bet.get("settlementRefusalReason"),
            "settlementRefusalClass": bet.get("settlementRefusalClass"),
        })

    by_date = {}
    for row in pending:
        by_date[row["gameDate"]] = by_date.get(row["gameDate"], 0) + 1
    dated = sorted(d for d in by_date if d)

    refusal_classes = {}
    for row in pending:
        cls = row["settlementRefusalClass"]
        if cls:
            refusal_classes[cls] = refusal_classes.get(cls, 0) + 1

    return {
        "schemaVersion": "2",
        "statusType": "GLOBAL_OUTSTANDING_SETTLEMENT",
        "scope": (
            "Every ungraded, ACTIVE wager in the canonical ledger "
            "(data/edgelab/bets/bets.jsonl), regardless of which dates the most "
            "recent reconciliation run processed. This is a GLOBAL outstanding-work "
            "snapshot; one run's own receipt describes ONE run."
        ),
        "asOf": as_of,
        "pendingTotal": len(pending),
        "pendingByDate": dict(sorted(by_date.items(), key=lambda kv: (kv[0] is None, kv[0]))),
        "oldestPendingDate": dated[0] if dated else None,
        "newestPendingDate": dated[-1] if dated else None,
        "undatedPendingCount": sum(1 for r in pending if not r["gameDate"]),
        "refusalClassCounts": dict(sorted(refusal_classes.items())),
        "pendingWagers": sorted(
            pending, key=lambda r: (r["gameDate"] or "", r["betId"] or "")
        ),
        "lastRun": run_summary,
    }


# ── Material-change detection (no timestamp-only commits) ───────────────

# Fields that legitimately differ on every run without anything having
# actually changed. They are excluded from the material fingerprint so a
# twice-daily no-op sweep cannot manufacture a Git commit.
_VOLATILE_STATUS_FIELDS = frozenset({"asOf", "lastRun"})


def material_status_fingerprint(status):
    """
    Pure. A stable digest of everything in the rolling status that
    MATTERS -- the pending set, its dates, its refusal classes -- with
    every wall-clock/provenance field stripped.

    Two runs that discovered the same outstanding work produce the same
    fingerprint even though their timestamps differ, which is exactly
    what lets the writer leave the committed bytes alone.
    """
    material = {k: v for k, v in (status or {}).items() if k not in _VOLATILE_STATUS_FIELDS}
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def status_is_materially_unchanged(new_status, existing_status):
    """Pure. True iff the versioned status file would say the same thing
    it already says. A True here means: do not rewrite the file."""
    if not existing_status:
        return False
    return material_status_fingerprint(new_status) == material_status_fingerprint(existing_status)


def build_receipt(*, dates, per_date, trigger, dry_run, started_at, completed_at,
                  ledger_analysis=None, sweep_window=None, refusal_reason=None,
                  github_run_id=None):
    """
    Pure. The machine-readable record of ONE reconciliation run:
    what triggered it, which dates it considered and why, what changed,
    and -- critically -- what is STILL unresolved. Never asserts an
    outcome for anything settlement left unresolved.
    """
    per_date = list(per_date or [])
    return {
        "schemaVersion": "1",
        "receiptType": "SETTLEMENT_RECONCILIATION",
        "trigger": trigger,
        "dryRun": bool(dry_run),
        "startedAt": started_at,
        "completedAt": completed_at,
        "githubRunId": github_run_id,
        "sweepWindow": sweep_window,
        "ledgerChangeAnalysis": ledger_analysis,
        "refusalReason": refusal_reason,
        "datesConsidered": list(dates),
        "perDate": per_date,
        "counts": {
            "datesConsidered": len(dates),
            "datesChanged": sum(1 for d in per_date if d.get("changedCanonicalState")),
            "betsSettled": sum((d.get("counts") or {}).get("betsSettled", 0) for d in per_date),
            "settlementsMeaningfullyChanged": sum(
                (d.get("counts") or {}).get("settlementsMeaningfullyChanged", 0) for d in per_date
            ),
            "reportsRegenerated": sum(1 for d in per_date if d.get("reportRegenerated")),
            "wagersStillPending": sum(d.get("pendingWagersAfter", 0) or 0 for d in per_date),
        },
    }
