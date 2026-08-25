#!/usr/bin/env python3
"""
scripts/corpus_health_report.py
===================================
Forward Replay Corpus and Production Provenance milestone (items 10/11);
enforcement-boundary redesign under a second maintainer review of PR #37
("corpus-health-check.yml must not begin life red solely because
historical backfill dates predate the new forward-capture system").

Daily + cumulative report on the health of the growing replay corpus --
production runs, snapshot capture coverage, provenance/config coverage,
candidate replay coverage, settlement/CLV linkage coverage, storage
growth, and mechanically-derived per-date quality gate statuses.

Read-only: scans data/pipeline/, data/edgelab/snapshots/,
data/edgelab/replay_runs/, data/edgelab/snapshot_recovery_log.jsonl, and
data/edgelab/forward_replay_status.json. Writes its own report to
data/edgelab/reports/corpus_health_report.json (machine-readable) and
.md (human-readable), plus (once, ever) the enforcement-boundary marker
at data/edgelab/corpus_enforcement_boundary.json.

── ENFORCEMENT BOUNDARY (new) ─────────────────────────────────────────────
Historical/backfilled dates (captureMode=HISTORICAL_BACKFILL, or any date
whose provenance predates real GITHUB_SHA-based capture) can NEVER
honestly show CAPTURED provenance -- that data genuinely does not exist
for them, and never will. Treating that as an operational failure created
exactly the alert-fatigue problem this redesign fixes: a check that is
red from the day it's turned on, for a reason nobody can act on, trains
everyone to ignore it.

The fix separates two different questions this report used to conflate:
  1. "How good is the corpus, overall, including everything we inherited
     before this system existed?" -- historicalCorpusQuality. Always
     computed, always shown, NEVER drives the exit code.
  2. "Is the forward-capture system, now that it exists, actually
     operating correctly on every date it applies to?" -- forwardOperationalHealth.
     This DOES drive the exit code -- but only once the system has a real
     date to hold itself accountable to.

The ENFORCEMENT BOUNDARY is the first production date whose
PRE_GAME_DECISION snapshot was captured LIVE (captureMode=LIVE_CAPTURE)
with productionProvenance.status == CAPTURED -- i.e. the first real,
GITHUB_SHA-based forward capture, not a guess, not a config knob someone
has to remember to update, not `datetime.now()` on the day this code
merged (an unstable wall-clock value is explicitly what item 1 of the
follow-up review forbade). The FIRST time this script finds such a date,
it persists that date to data/edgelab/corpus_enforcement_boundary.json
and never recomputes it again -- every later run reads that file
verbatim. This is deliberate: if the boundary could shift, a maintainer
under pressure (or a bug) could quietly move it later to dodge an
accumulating failure. Once set, it is immutable, full stop.

Before that first qualifying date exists, enforcement.status is
AWAITING_FIRST_FORWARD_CAPTURE and the script always exits 0 -- see
requirement 4 of the follow-up review: it must not report HEALTHY before
the system has observed a qualifying forward run, and it must not fail
for not yet having one either.

── CORPUS-HEALTH AUDIT, 2026-08-25 (10 hard-fail forward dates) ───────────
A live audit of every hard-fail forward date found three distinct, real
defects -- fixed here -- plus one genuinely unrecoverable historical gap
this script must never silently make green:

  1. "unkeyed" run-directory sort bug (2026-08-19): a snapshot capture
     that runs before that day's recommendations.json exists gets the
     "unkeyed" run-key slug, which sorts AFTER every real ISO-timestamp
     slug as a plain string -- so a garbage early capture could shadow a
     real, later, healthy one. Fixed in lib.edgelab.snapshot's
     list_pregame_run_dirs() (sorts by each manifest's own capturedAt,
     not the directory name).
  2. "latest run, not best run" (2026-08-21/22/23): fetch-slate.yml's
     schedule-triggered runs never execute the risk-gate/execution chain
     (a deliberate, unmodified safety boundary -- see fetch-slate.yml's
     BLOCK 7). A later same-day schedule-only refresh can therefore be a
     strictly WORSE capture than an earlier same-day run. Both
     scripts/run_forward_replay.py and this script now select each
     date's BEST (not merely most recent) PRE_GAME_DECISION run via
     lib.edgelab.snapshot.select_canonical_pregame_manifest().
  3. Reporting-metric population mismatch ("17 captured / 0 missing" vs.
     8 dates flagged FORWARD_MISSING_SNAPSHOT): forwardOperationalHealth's
     top-level counters were computed over a DIFFERENT date population
     (dates with a currently-existing, overwrite-prone
     data/pipeline/<date>/recommendations.json) than the per-date gate
     statuses (every date in the forward era, from either production or
     snapshot evidence). Fixed below: every forwardOperationalHealth
     counter now shares one documented population
     (forward_expected_records, excluding same-day-pending dates -- see
     #4). See also the FORWARD_MISSING_SNAPSHOT / FORWARD_INCOMPLETE_CAPTURE
     split just below STATUS_FORWARD_MISSING_SNAPSHOT.
  4. Same-day false-positive (2026-08-25): this check has historically
     run at ~07:00 UTC, hours before fetch-slate.yml's first scheduled
     opportunity that same day (16:00 UTC) or any manual dispatch. A
     forward-era date equal to THIS SCRIPT'S OWN run date, with no
     PRE_GAME_DECISION snapshot yet, is not evidence of a missed capture
     -- the day's production opportunity simply hasn't happened yet. This
     is an objective fact about the calendar (today's own date, from the
     report's own generation clock), not an arbitrary hour-of-day cutoff:
     see STATUS_FORWARD_PENDING_TODAY. The very next day's run
     re-evaluates that date as a normal (no-longer-"today") forward date,
     so a genuine miss still hard-fails exactly one day later -- nothing
     about this exempts a date forever.

── ACKNOWLEDGED LEGACY FORWARD GAPS (2026-08-11..15) ───────────────────────
2026-08-11 through 2026-08-15 have NO PRE_GAME_DECISION capture of any
kind and never will: data/pipeline/<date>/recommendations.json was never
written for these five dates (verified via `git log --all`, zero commits,
ever) because fetch-slate.yml had no schedule trigger before PR #105 and
nobody manually dispatched it for six days -- see fetch-slate.yml's own
docstring and docs/POSTMORTEM_PRODUCTION_RELIABILITY_2026.md.
recommendations.json is overwritten-not-versioned by design (see
lib/edgelab/snapshot.py's module docstring), so there is no historical
copy anywhere to recover -- this is category (B) from the audit request:
a permanent, unrecoverable gap caused by earlier infrastructure behavior,
not a reconstructable defect and not a misclassification.

These five dates are, and must remain, real hard-fail forward dates
(FORWARD_MISSING_SNAPSHOT) forever -- this script never marks them
healthy, never deletes their evidence, and never moves the enforcement
boundary to dodge them. What it DOES do is read a small, human-curated,
append-only allowlist (ACKNOWLEDGED_GAPS_PATH) of exact (date, reason,
evidence) entries a maintainer has reviewed and written down, and exclude
ONLY those exact dates from driving exitShouldFail -- see
_load_acknowledged_gaps() and hard_fail_records below. This script never
writes to that file itself (no self-acknowledgment), so a NEW hard-fail
date -- one nobody has reviewed and added evidence for -- still fails the
check immediately, exactly as before. Every acknowledged date still shows
its real forwardGateStatus in perDate and still counts in
forwardOperationalHealth's gateStatusCounts; only exitShouldFail treats it
differently. This is what lets five acknowledged, permanently-irrecoverable
2026-08-11..15 gaps stop poisoning every future run without requiring the
enforcement boundary to move or the evidence to be hidden.

Usage:
  python3 scripts/corpus_health_report.py [--report-path PATH]
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from lib.edgelab import ids  # noqa: E402
from lib.edgelab import replay  # noqa: E402
from lib.edgelab import snapshot as snap  # noqa: E402

_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

REPORT_JSON_PATH = os.path.join("data", "edgelab", "reports", "corpus_health_report.json")
REPORT_MD_PATH = os.path.join("data", "edgelab", "reports", "corpus_health_report.md")
ENFORCEMENT_BOUNDARY_PATH = os.path.join("data", "edgelab", "corpus_enforcement_boundary.json")
# Manually curated ONLY -- this script reads this file but never writes to
# it (see _load_acknowledged_gaps / module docstring's "ACKNOWLEDGED LEGACY
# FORWARD GAPS" section below). A date can only stop being a hard-fail
# because a human reviewed it, wrote down concrete evidence that it is
# permanently unrecoverable, and committed that evidence here -- never
# because this script decided so on its own.
ACKNOWLEDGED_GAPS_PATH = os.path.join("data", "edgelab", "corpus_acknowledged_forward_gaps.json")

# Item 11 (original): mechanically-derived per-date quality gate statuses,
# worst-first (first true condition wins) -- retained unmodified as the
# "how good is everything, historical included" signal. Never drives the
# exit code on its own any more; see STATUS_FORWARD_* below for that.
STATUS_INTEGRITY_FAILURE = "INTEGRITY_FAILURE"
STATUS_DEGRADED_MISSING_SNAPSHOT = "DEGRADED_MISSING_SNAPSHOT"
STATUS_DEGRADED_CONFIG_PARTIAL = "DEGRADED_CONFIG_PARTIAL"
STATUS_DEGRADED_REPLAY_FAILURE = "DEGRADED_REPLAY_FAILURE"
STATUS_DEGRADED_CLOSING_DATA = "DEGRADED_CLOSING_DATA"
STATUS_DEGRADED_SETTLEMENT_DATA = "DEGRADED_SETTLEMENT_DATA"
STATUS_HEALTHY = "HEALTHY"
# A date with no production run and no snapshot (a genuine no-slate day,
# e.g. the MLB offseason) never appears in `all_dates` at all -- it is
# structurally excluded rather than labeled, so it can never be
# mistaken for a capture failure (item 11's "distinguish capture failure
# from legitimate no-slate/no-market days").

# ── Forward-era-only gate statuses (new) -- these, and only these, can
# fail the workflow, and only for dates at/after the enforcement boundary.
STATUS_FORWARD_MISSING_SNAPSHOT = "FORWARD_MISSING_SNAPSHOT"
# Split out from STATUS_FORWARD_MISSING_SNAPSHOT under the 2026-08-25
# audit (see module docstring, finding #3): "no manifest exists at all"
# (STATUS_FORWARD_MISSING_SNAPSHOT) and "a manifest exists but is missing
# a REQUIRED component" (STATUS_FORWARD_INCOMPLETE_CAPTURE) are different
# facts about the corpus -- conflating them under one name is exactly the
# kind of population/label mismatch that produced the "17 captured / 0
# missing" vs. "8 FORWARD_MISSING_SNAPSHOT" reporting inconsistency this
# audit was asked to reconcile. Both remain hard-fail; only the label and
# the metric they roll up into differ.
STATUS_FORWARD_INCOMPLETE_CAPTURE = "FORWARD_INCOMPLETE_CAPTURE"
STATUS_FORWARD_PROVENANCE_AMBIGUOUS = "FORWARD_PROVENANCE_AMBIGUOUS"
STATUS_FORWARD_REPLAY_FAILURE = "FORWARD_REPLAY_FAILURE"
STATUS_FORWARD_CLOSING_DATA_PENDING = "FORWARD_CLOSING_DATA_PENDING"
STATUS_FORWARD_SETTLEMENT_DATA_PENDING = "FORWARD_SETTLEMENT_DATA_PENDING"
# A forward-era date equal to this script's OWN run date (see _today_utc()),
# with no PRE_GAME_DECISION snapshot yet -- the day's single production
# opportunity has not happened yet, so absence of a snapshot is not
# evidence of a miss (module docstring finding #4). Deliberately excluded
# from HARD_FAIL_FORWARD_STATUSES: the very next day's run re-evaluates
# this same date as an ordinary (no-longer-"today") forward date, so a
# genuine miss still hard-fails exactly one day later.
STATUS_FORWARD_PENDING_TODAY = "FORWARD_PENDING_TODAY"
# Corpus-health audit (2026-08-25 follow-up, run-type-aware completeness):
# a schedule-triggered run (lib.edgelab.snapshot.is_schedule_triggered_run)
# never had an authoritative, risk-gated decision to replay in the first
# place -- fetch-slate.yml's BLOCK 7 never executes on a `schedule`
# trigger (a deliberate safety boundary that keeps automated real-money
# bet placement workflow_dispatch/push-only, untouched by this fix).
# Deliberately its own terminal, NON-hard-fail status: "there was never
# supposed to be a decision in this run type" is a fundamentally
# different fact from "there was a decision and we cannot replay it"
# (STATUS_FORWARD_REPLAY_FAILURE) -- the former must never be reported
# as a capture/replay failure just because a decision-only artifact
# (RISK_GATE_OUTPUT) is structurally absent by design.
STATUS_FORWARD_RESEARCH_ONLY_NO_DECISION = "FORWARD_RESEARCH_ONLY_NO_DECISION"
STATUS_FORWARD_HEALTHY = "FORWARD_HEALTHY"

# Hard-fail on sight -- one occurrence is enough, no grace period, because
# each of these already reflects the OUTCOME of any automatic recovery
# this repository attempts (snapshot-capture-check.yml's daily recovery
# pass runs before this check does -- see .github/workflows/corpus-health-check.yml's
# cron ordering comment) -- if it's still broken by the time this report
# runs, recovery already had its window.
HARD_FAIL_FORWARD_STATUSES = frozenset({
    STATUS_INTEGRITY_FAILURE,
    STATUS_FORWARD_MISSING_SNAPSHOT,
    STATUS_FORWARD_INCOMPLETE_CAPTURE,
    STATUS_FORWARD_PROVENANCE_AMBIGUOUS,
    STATUS_FORWARD_REPLAY_FAILURE,
})
# Non-fatal on their own (postgame data naturally lags same-day capture),
# but still count toward the consecutive-degraded-forward-runs escalation
# signal so a persistent, multi-day gap is still eventually visible.
NON_FATAL_DEGRADED_FORWARD_STATUSES = frozenset({
    STATUS_FORWARD_CLOSING_DATA_PENDING,
    STATUS_FORWARD_SETTLEMENT_DATA_PENDING,
    STATUS_FORWARD_PENDING_TODAY,
})

ERA_HISTORICAL = "HISTORICAL"
ERA_FORWARD = "FORWARD"

ENFORCEMENT_ACTIVE = "ACTIVE"
ENFORCEMENT_AWAITING_FIRST_FORWARD_CAPTURE = "AWAITING_FIRST_FORWARD_CAPTURE"

CONSECUTIVE_DEGRADED_FORWARD_THRESHOLD = 3


def _production_run_dates():
    pipeline_root = os.path.join("data", "pipeline")
    if not os.path.isdir(pipeline_root):
        return []
    return sorted(
        d for d in os.listdir(pipeline_root)
        if _DATE_DIR_RE.match(d) and os.path.exists(os.path.join(pipeline_root, d, "recommendations.json"))
    )


def _all_snapshot_dates():
    if not os.path.isdir(snap.SNAPSHOTS_ROOT):
        return []
    return sorted(d for d in os.listdir(snap.SNAPSHOTS_ROOT) if _DATE_DIR_RE.match(d))


def _recovery_log_by_date():
    path = os.path.join("data", "edgelab", "snapshot_recovery_log.jsonl")
    by_date = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                by_date.setdefault(entry.get("date"), []).append(entry)
    return by_date


def _forward_replay_status():
    path = os.path.join("data", "edgelab", "forward_replay_status.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _dir_size_bytes(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for fn in files:
            fp = os.path.join(root, fn)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _today_utc():
    """This script's own run date, UTC, as YYYY-MM-DD. A single injection
    point (build_report()'s `today` parameter threads through to here via
    the caller, never read a second time mid-run) so a date's
    "is this today, i.e. not-yet-due" classification is stable for the
    whole report and deterministically testable -- see
    STATUS_FORWARD_PENDING_TODAY."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_acknowledged_gaps():
    """Human-curated, append-only allowlist of exact forward-era dates a
    maintainer has reviewed and documented as permanently unrecoverable
    (see module docstring's "ACKNOWLEDGED LEGACY FORWARD GAPS" section).
    Read-only from this script's perspective -- it is never written here,
    by design: only a human commit can acknowledge a date. A malformed or
    missing file degrades to "no acknowledgments" (never silently expands
    to "acknowledge everything"), so a corrupted file fails safe (more
    hard-fail dates visible, not fewer)."""
    if not os.path.exists(ACKNOWLEDGED_GAPS_PATH):
        return {}
    try:
        with open(ACKNOWLEDGED_GAPS_PATH) as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return {}
    by_date = {}
    for entry in entries:
        if isinstance(entry, dict) and entry.get("date"):
            by_date[entry["date"]] = entry
    return by_date


# ── Enforcement boundary (new) ────────────────────────────────────────────

def _is_qualifying_forward_manifest(manifest):
    """A genuine forward, GITHUB_SHA-based provenance capture -- never a
    historical backfill (which can never honestly have this), never a
    manifest whose provenance is merely present but untrusted (MISSING/
    AMBIGUOUS both fail this check, same honesty rule
    lib.edgelab.snapshot._production_provenance already applies)."""
    if manifest is None:
        return False
    if manifest.get("captureMode") != snap.CAPTURE_MODE_LIVE:
        return False
    provenance = manifest.get("productionProvenance") or {}
    return provenance.get("status") == "CAPTURED"


def _load_persisted_enforcement_boundary():
    if not os.path.exists(ENFORCEMENT_BOUNDARY_PATH):
        return None
    try:
        with open(ENFORCEMENT_BOUNDARY_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _persist_enforcement_boundary(boundary):
    os.makedirs(os.path.dirname(ENFORCEMENT_BOUNDARY_PATH), exist_ok=True)
    with open(ENFORCEMENT_BOUNDARY_PATH, "w") as f:
        json.dump(boundary, f, indent=2, sort_keys=True)


def _determine_enforcement_boundary(all_dates):
    """
    Returns the boundary dict, or None if forward enforcement has never
    activated. IMMUTABLE once persisted: a boundary file that already
    exists on disk is read verbatim and returned as-is -- this function
    never overwrites, recomputes, or moves an existing boundary, by
    design (see module docstring). Only computes and persists a NEW
    boundary when no file exists yet, scanning dates chronologically for
    the first one whose latest PRE_GAME_DECISION manifest qualifies.
    """
    existing = _load_persisted_enforcement_boundary()
    if existing is not None:
        return existing

    for date in all_dates:  # chronological -- earliest qualifying date wins, once, forever
        manifest = snap.select_canonical_pregame_manifest(date)
        if _is_qualifying_forward_manifest(manifest):
            boundary = {
                "schemaVersion": "1",
                "enforcementBoundaryDate": date,
                "activatedAt": ids.utc_now_iso(),
                "activatingSnapshotId": manifest.get("snapshotId"),
                "note": (
                    "First production date whose PRE_GAME_DECISION snapshot was "
                    "captured LIVE with productionProvenance.status == CAPTURED -- "
                    "the first real, forward, GITHUB_SHA-based provenance capture. "
                    "Immutable once written: this file is read verbatim by every "
                    "future run of scripts/corpus_health_report.py and is never "
                    "recomputed, regardless of later degraded runs."
                ),
            }
            _persist_enforcement_boundary(boundary)
            return boundary
    return None


def _forward_gate_status(date, rec, manifest, closing_manifest, postgame_manifest, today):
    """
    First-match-wins, forward-era-only rule table (see
    HARD_FAIL_FORWARD_STATUSES for which of these can fail the workflow).

    Run-type-aware (corpus-health audit, 2026-08-25 follow-up): a
    schedule-triggered run (snap.is_schedule_triggered_run) never had an
    authoritative, risk-gated decision to begin with -- fetch-slate.yml's
    BLOCK 7 never executes on a `schedule` trigger, a deliberate,
    untouched safety boundary. Two consequences, both reused from
    lib.edgelab.snapshot/replay rather than re-implemented here:
      - effective_completeness_status() re-derives whether
        MISSING_REQUIRED_INPUT is a REAL gap or was caused SOLELY by
        RISK_GATE_OUTPUT's structural absence for this run type -- this
        is a LIVE re-interpretation, never a rewrite of the manifest's
        own immutable stored completenessStatus (see that function's
        docstring for why an already-committed manifest can be
        reclassified honestly without altering historical evidence).
      - a schedule-triggered manifest's forward replay status is
        expected to be NOT_APPLICABLE_NO_DECISION (see
        lib.edgelab.replay.RUN_STATUS_NOT_APPLICABLE_NO_DECISION), never
        COMPLETED -- so it is never held to the "replay must have
        COMPLETED" bar STATUS_FORWARD_REPLAY_FAILURE enforces for an
        authoritative-decision run.
    """
    if manifest is None:
        if date == today:
            # This date's single daily production opportunity has not
            # happened yet (this script's own run date == the date being
            # evaluated) -- not evidence of a miss. See
            # STATUS_FORWARD_PENDING_TODAY / module docstring finding #4.
            return STATUS_FORWARD_PENDING_TODAY
        return STATUS_FORWARD_MISSING_SNAPSHOT
    verification = snap.verify_snapshot(manifest)
    if verification["overallStatus"] != "VERIFIED":
        return STATUS_INTEGRITY_FAILURE
    provenance_status = (manifest.get("productionProvenance") or {}).get("status")
    if provenance_status in ("MISSING", "AMBIGUOUS"):
        return STATUS_FORWARD_PROVENANCE_AMBIGUOUS
    if snap.effective_completeness_status(manifest) == "MISSING_REQUIRED_INPUT":
        # A manifest DOES exist (provenance itself was just checked and
        # ruled out above) but is missing a different REQUIRED component
        # -- a real, actionable capture gap, but a distinct fact from "no
        # manifest exists at all" (STATUS_FORWARD_MISSING_SNAPSHOT). See
        # module docstring finding #3. Uses the LIVE, run-type-aware
        # verdict (never the raw stored field directly) so a
        # schedule-triggered run missing ONLY RISK_GATE_OUTPUT -- expected
        # by architecture, not a defect -- is not hard-failed here.
        return STATUS_FORWARD_INCOMPLETE_CAPTURE

    is_research_only = snap.is_schedule_triggered_run(manifest)
    if not is_research_only and rec["forwardReplayStatus"] not in ("COMPLETED", "completed"):
        return STATUS_FORWARD_REPLAY_FAILURE
    if closing_manifest is None:
        return STATUS_FORWARD_CLOSING_DATA_PENDING
    if postgame_manifest is None:
        return STATUS_FORWARD_SETTLEMENT_DATA_PENDING
    return STATUS_FORWARD_RESEARCH_ONLY_NO_DECISION if is_research_only else STATUS_FORWARD_HEALTHY


def _per_date_record(date, recovery_by_date, forward_status):
    """
    Builds one date's full record: pregame manifest (if any), quality gate
    status, and every metric item 10 asks for at the per-date grain.
    era/forwardGateStatus are filled in by build_report() once the
    enforcement boundary is known (a single date's era depends on global
    state, not just its own data).

    Uses select_canonical_pregame_manifest() (the BEST run for this date),
    not load_latest_pregame_manifest() (the most RECENT run) -- see
    lib.edgelab.snapshot.select_canonical_pregame_manifest's docstring and
    module docstring finding #2: a later same-day schedule-only refresh
    can be a strictly worse capture than an earlier same-day run.
    """
    manifest = snap.select_canonical_pregame_manifest(date)
    run_dirs = snap.list_pregame_run_dirs(date)
    recoveries = recovery_by_date.get(date, [])
    fwd = forward_status.get(date)

    record = {
        "date": date,
        "productionRunsCaptured": len(run_dirs),
        "snapshotId": manifest.get("snapshotId") if manifest else None,
        "manifestHash": manifest.get("manifestHash") if manifest else None,
        "completenessStatus": manifest.get("completenessStatus") if manifest else None,
        "productionCommitShaKnown": bool(manifest and manifest.get("productionCommitSha")),
        "productionProvenanceStatus": (manifest.get("productionProvenance") or {}).get("status") if manifest else None,
        "captureMode": manifest.get("captureMode") if manifest else None,
        "effectiveConfigHashKnown": False,
        "recoveredCount": len(recoveries),
        "recoveryAttempted": len(recoveries) > 0,
        "forwardReplayStatus": (fwd or {}).get("runStatus") or (fwd or {}).get("outcome"),
        "forwardReplayId": (fwd or {}).get("replayRunId"),
        "gateStatus": None,
        "era": None,
        "forwardGateStatus": None,
        "acknowledgedLegacyGap": False,
        "acknowledgedGapReason": None,
        # Run-type-aware completeness (corpus-health audit, 2026-08-25
        # follow-up). isResearchOnlyRun: GITHUB_EVENT_NAME=='schedule' for
        # this run -- never had an authoritative, risk-gated decision to
        # begin with (fetch-slate.yml's BLOCK 7 gating). effectiveCompleteness
        # Status: the LIVE, run-type-aware re-derivation (see
        # lib.edgelab.snapshot.effective_completeness_status) -- may differ
        # from the raw, immutable stored `completenessStatus` above for a
        # schedule-triggered run whose only REQUIRED gap is
        # RISK_GATE_OUTPUT; both are shown side by side so a reader can
        # see the historical record AND the current, correct interpretation.
        "isResearchOnlyRun": bool(manifest and snap.is_schedule_triggered_run(manifest)),
        "effectiveCompletenessStatus": snap.effective_completeness_status(manifest) if manifest else None,
    }

    if manifest:
        effective_config = next((c for c in manifest.get("components", []) if c["componentType"] == "EFFECTIVE_CONFIG"), None)
        record["effectiveConfigHashKnown"] = bool(effective_config and effective_config.get("availabilityStatus") in ("AVAILABLE", "PARTIAL"))

    # ── Item 11 (original): overall gate status, first match wins. Kept
    # exactly as before -- this is the "historical corpus quality" signal
    # and must keep reflecting reality (including MISSING/PARTIAL) for
    # historical dates; it simply no longer drives the exit code alone.
    # Run-type-aware (corpus-health audit, 2026-08-25 follow-up): uses
    # record["effectiveCompletenessStatus"] (LIVE re-derivation) rather
    # than the raw stored field, and accepts
    # RUN_STATUS_NOT_APPLICABLE_NO_DECISION as a non-degraded replay
    # outcome for a schedule-triggered run, for the exact same reason
    # _forward_gate_status() does -- see that function's docstring.
    if manifest is None:
        record["gateStatus"] = STATUS_DEGRADED_MISSING_SNAPSHOT
    else:
        verification = snap.verify_snapshot(manifest)
        if verification["overallStatus"] != "VERIFIED":
            record["gateStatus"] = STATUS_INTEGRITY_FAILURE
        elif record["effectiveCompletenessStatus"] == "MISSING_REQUIRED_INPUT":
            record["gateStatus"] = STATUS_DEGRADED_MISSING_SNAPSHOT
        elif not record["productionCommitShaKnown"]:
            record["gateStatus"] = STATUS_DEGRADED_CONFIG_PARTIAL
        elif fwd is None and not record["isResearchOnlyRun"]:
            record["gateStatus"] = STATUS_DEGRADED_REPLAY_FAILURE
        elif fwd is not None and fwd.get("runStatus") not in (None, replay.RUN_STATUS_COMPLETED, "NOT_APPLICABLE_NO_DECISION") \
                and fwd.get("outcome") not in ("completed", "not_applicable_no_decision"):
            record["gateStatus"] = STATUS_DEGRADED_REPLAY_FAILURE
        else:
            postgame = snap.load_manifest(snap.STAGE_POST_GAME_SETTLEMENT, date)
            closing = snap.load_manifest(snap.STAGE_CLOSING_LINE, date)
            if closing is None:
                record["gateStatus"] = STATUS_DEGRADED_CLOSING_DATA
            elif postgame is None:
                record["gateStatus"] = STATUS_DEGRADED_SETTLEMENT_DATA
            else:
                record["gateStatus"] = STATUS_HEALTHY

    return record, manifest


def build_report(today=None):
    production_dates = _production_run_dates()
    snapshot_dates = _all_snapshot_dates()
    all_dates = sorted(set(production_dates) | set(snapshot_dates))
    recovery_by_date = _recovery_log_by_date()
    forward_status = _forward_replay_status()
    acknowledged_gaps = _load_acknowledged_gaps()

    # Injectable so a date's "is this today, i.e. not-yet-due" call is
    # stable for the whole report and deterministically testable -- see
    # STATUS_FORWARD_PENDING_TODAY / module docstring finding #4.
    if today is None:
        today = _today_utc()

    per_date = []
    manifests_by_date = {}
    for d in all_dates:
        rec, manifest = _per_date_record(d, recovery_by_date, forward_status)
        per_date.append(rec)
        manifests_by_date[d] = manifest

    # ── Enforcement boundary (new) -- determined from REAL evidence only,
    # before "today" is ever synthesized in below. "Today" has no manifest
    # by construction (see below) so it could never qualify as a boundary
    # candidate anyway; computing the boundary first just keeps that
    # obviously true rather than incidentally true.
    boundary = _determine_enforcement_boundary(all_dates)
    boundary_date = boundary["enforcementBoundaryDate"] if boundary else None

    # "Today" may not have a production/snapshot evidence trail at all yet
    # (nothing has run) -- it must still be evaluated (as PENDING, not
    # silently absent) once forward enforcement is active AND today would
    # actually fall in the forward era, so make sure it's in the date set
    # the era/gate logic below sees. Deliberately NOT added when
    # enforcement hasn't activated yet (boundary_date is None) or today
    # precedes the boundary: a synthetic "today" has zero real evidence
    # and must never inflate historicalCorpusQuality's descriptive counts
    # with a phantom date nothing produced.
    if boundary_date and today >= boundary_date and today not in manifests_by_date:
        rec, manifest = _per_date_record(today, recovery_by_date, forward_status)
        per_date.append(rec)
        manifests_by_date[today] = manifest
        per_date.sort(key=lambda r: r["date"])
    for rec in per_date:
        rec["era"] = ERA_FORWARD if (boundary_date and rec["date"] >= boundary_date) else ERA_HISTORICAL
        if rec["era"] == ERA_FORWARD:
            manifest = manifests_by_date[rec["date"]]
            closing = snap.load_manifest(snap.STAGE_CLOSING_LINE, rec["date"])
            postgame = snap.load_manifest(snap.STAGE_POST_GAME_SETTLEMENT, rec["date"])
            rec["forwardGateStatus"] = _forward_gate_status(rec["date"], rec, manifest, closing, postgame, today)
            gap_entry = acknowledged_gaps.get(rec["date"])
            rec["acknowledgedLegacyGap"] = gap_entry is not None
            rec["acknowledgedGapReason"] = gap_entry.get("reason") if gap_entry else None

    historical_records = [r for r in per_date if r["era"] == ERA_HISTORICAL]
    forward_records = [r for r in per_date if r["era"] == ERA_FORWARD]

    gate_counts = _count_by(per_date, "gateStatus")

    # Consecutive-degraded streak, most-recent-date-backward, computed
    # over ALL dates -- retained for backward-compat display only; the
    # exit code no longer depends on this field (see
    # forwardOperationalHealth.consecutiveDegradedForwardRuns below).
    consecutive_degraded = 0
    for rec in reversed(per_date):
        if rec["gateStatus"] not in (STATUS_HEALTHY, None):
            consecutive_degraded += 1
        else:
            break

    expected_snapshots = len(production_dates)
    captured_snapshots = sum(1 for rec in per_date if rec["snapshotId"])
    missing_snapshots = [rec["date"] for rec in per_date if rec["date"] in production_dates and not rec["snapshotId"]]
    total_recovered = sum(rec["recoveredCount"] for rec in per_date)

    replay_run_ids = []
    replay_runs_root = replay.REPLAY_RUNS_ROOT
    if os.path.isdir(replay_runs_root):
        replay_run_ids = sorted(os.listdir(replay_runs_root))

    replays_attempted, replays_completed, replays_failed = 0, 0, 0
    markets_evaluated = markets_comparable = clv_linked = settlement_linked = 0
    unresolved_settlement_reasons, unresolved_clv_reasons = {}, {}
    trustworthy_dates = []
    forward_replay = {"attempted": 0, "completed": 0, "failed": 0}
    forward_clv_linked = forward_settlement_linked = 0
    for run_id in replay_run_ids:
        run = replay.load_replay_run(run_id)
        if run is None:
            continue
        replays_attempted += 1
        run_date = run.get("snapshotDate")
        run_is_forward = bool(boundary_date and run_date and run_date >= boundary_date)
        if run_is_forward:
            forward_replay["attempted"] += 1
        if run["runStatus"] == replay.RUN_STATUS_COMPLETED:
            replays_completed += 1
            if run_is_forward:
                forward_replay["completed"] += 1
            s = run["summary"]
            markets_evaluated += s["marketsEvaluated"]
            markets_comparable += s["marketsComparable"]
            clv_linked += s["clvResolved"]
            settlement_linked += s["settledResolved"]
            if run_is_forward:
                forward_clv_linked += s["clvResolved"]
                forward_settlement_linked += s["settledResolved"]
            if run["eligibilityStatus"] == replay.ELIGIBLE_LEVEL_2:
                trustworthy_dates.append(run["snapshotDate"])
            for result in replay.load_replay_results(run_id):
                if result["settlementLinkage"]["status"] == "UNRESOLVED":
                    reason = result["settlementLinkage"]["reason"]
                    unresolved_settlement_reasons[reason] = unresolved_settlement_reasons.get(reason, 0) + 1
                if result["clvLinkage"]["status"] == "UNRESOLVED":
                    reason = result["clvLinkage"]["reason"]
                    unresolved_clv_reasons[reason] = unresolved_clv_reasons.get(reason, 0) + 1
        else:
            replays_failed += 1
            if run_is_forward:
                forward_replay["failed"] += 1

    storage = {
        "snapshotsBytes": _dir_size_bytes(snap.SNAPSHOTS_ROOT) if os.path.isdir(snap.SNAPSHOTS_ROOT) else 0,
        "replayRunsBytes": _dir_size_bytes(replay_runs_root) if os.path.isdir(replay_runs_root) else 0,
    }
    storage["totalBytes"] = storage["snapshotsBytes"] + storage["replayRunsBytes"]

    # ── historicalCorpusQuality (new) -- descriptive only, never fails ───
    historical_corpus_quality = {
        "datesCovered": len(historical_records),
        "gateStatusCounts": _count_by(historical_records, "gateStatus"),
        "note": (
            "Dates before the forward-capture enforcement boundary (or ALL "
            "dates if enforcement has not yet activated). These predate "
            "real GITHUB_SHA-based provenance capture by construction -- "
            "DEGRADED_CONFIG_PARTIAL / DEGRADED_MISSING_SNAPSHOT / "
            "approximate-or-non-reconstructable statuses here are expected "
            "and never fail this check."
        ),
    }

    # ── forwardOperationalHealth (new) -- this drives the exit code ──────
    # ONE population, shared by every counter below: every forward-era
    # date this report knows about (from either production or snapshot
    # evidence -- same as forward_records), EXCLUDING same-day-pending
    # dates (STATUS_FORWARD_PENDING_TODAY -- there is nothing to count as
    # "expected yet" for a date whose single daily opportunity hasn't
    # happened). This directly fixes the "17 captured / 0 missing" vs. "8
    # FORWARD_MISSING_SNAPSHOT dates" reporting bug (module docstring
    # finding #3): the old `forward_expected_dates` was computed from
    # `production_dates` (dates with a CURRENTLY-existing
    # data/pipeline/<date>/recommendations.json -- an overwrite-prone
    # file, see lib/edgelab/snapshot.py's module docstring), a DIFFERENT,
    # narrower population than forward_records (used for every other
    # forward counter and for the per-date gate statuses) -- so a date
    # missing its snapshot for a reason that ALSO left it without a
    # current recommendations.json (exactly what happened for
    # 2026-08-11..15 and 2026-08-25) silently fell out of
    # `snapshotsMissing` while still being flagged FORWARD_MISSING_SNAPSHOT
    # per-date. Every counter below now shares forward_expected_records.
    forward_expected_records = [r for r in forward_records if r["forwardGateStatus"] != STATUS_FORWARD_PENDING_TODAY]
    forward_expected_dates = [r["date"] for r in forward_expected_records]
    pending_today_dates = [r["date"] for r in forward_records if r["forwardGateStatus"] == STATUS_FORWARD_PENDING_TODAY]
    forward_gate_counts = _count_by(forward_records, "forwardGateStatus")
    forward_consecutive_degraded = 0
    for rec in reversed(forward_records):
        if rec["forwardGateStatus"] == STATUS_FORWARD_PENDING_TODAY:
            # Not yet due -- neither healthy nor degraded; skip without
            # breaking the backward scan, so a pending "today" can never
            # mask (or reset) a real streak accumulating just before it.
            continue
        if rec["forwardGateStatus"] in (STATUS_FORWARD_HEALTHY, STATUS_FORWARD_RESEARCH_ONLY_NO_DECISION):
            # RESEARCH_ONLY_NO_DECISION is a fully-resolved terminal state
            # (correctly, honestly: no decision existed to replay), not a
            # degraded one -- it must break the streak exactly like
            # HEALTHY does, or a run of ordinary schedule-triggered
            # research days would be misread as an accumulating outage.
            break
        forward_consecutive_degraded += 1
    all_hard_fail_records = [r for r in forward_records if r["forwardGateStatus"] in HARD_FAIL_FORWARD_STATUSES]
    # Acknowledged legacy gaps (see module docstring's "ACKNOWLEDGED LEGACY
    # FORWARD GAPS" section) keep their real hard-fail forwardGateStatus in
    # gateStatusCounts/perDate -- they are excluded ONLY from the set that
    # drives exitShouldFail, never from the visible record of the gap.
    hard_fail_records = [r for r in all_hard_fail_records if not r["acknowledgedLegacyGap"]]
    acknowledged_hard_fail_records = [r for r in all_hard_fail_records if r["acknowledgedLegacyGap"]]

    forward_operational_health = {
        "expectedRuns": len(forward_expected_dates),
        "snapshotsCaptured": sum(1 for r in forward_expected_records if r["snapshotId"]),
        "snapshotsMissing": [r["date"] for r in forward_expected_records if not r["snapshotId"]],
        # Manifest EXISTS but is missing a different REQUIRED component
        # (STATUS_FORWARD_INCOMPLETE_CAPTURE) -- a distinct fact from
        # snapshotsMissing above; see module docstring finding #3.
        "incompleteCaptures": [
            r["date"] for r in forward_expected_records
            if r["snapshotId"] and r["completenessStatus"] == "MISSING_REQUIRED_INPUT"
        ],
        "pendingTodayDates": pending_today_dates,
        "provenanceCoverage": {
            "known": sum(1 for r in forward_expected_records if r["productionCommitShaKnown"]),
            "total": len(forward_expected_records),
        },
        "replayCompletion": forward_replay,
        "clvCoverage": {"linkedMarkets": forward_clv_linked},
        "settlementCoverage": {"linkedMarkets": forward_settlement_linked},
        "gateStatusCounts": forward_gate_counts,
        "consecutiveDegradedForwardRuns": forward_consecutive_degraded,
        "hardFailDates": [r["date"] for r in hard_fail_records],
        "acknowledgedLegacyGapDates": [r["date"] for r in acknowledged_hard_fail_records],
        "populationNote": (
            "expectedRuns/snapshotsCaptured/snapshotsMissing/incompleteCaptures/"
            "provenanceCoverage all share ONE population: every known forward-era "
            "date (from production OR snapshot evidence) excluding pendingTodayDates. "
            "snapshotsCaptured + len(snapshotsMissing) == expectedRuns always; "
            "incompleteCaptures is a SUBSET of dates counted inside snapshotsCaptured "
            "(they have a manifest, it's just incomplete), never inside snapshotsMissing."
        ),
    }

    # ── Enforcement status + exit-code reason (new) ──────────────────────
    if boundary is None:
        enforcement_status = ENFORCEMENT_AWAITING_FIRST_FORWARD_CAPTURE
        exit_should_fail = False
        exit_code_reason = (
            "No qualifying forward production run has been captured yet "
            "(no PRE_GAME_DECISION snapshot with captureMode=LIVE_CAPTURE "
            "and productionProvenance.status=CAPTURED exists) -- enforcement "
            "is not yet active, so this check always passes. See "
            "historicalCorpusQuality for descriptive-only corpus state."
        )
    else:
        enforcement_status = ENFORCEMENT_ACTIVE
        if hard_fail_records:
            exit_should_fail = True
            exit_code_reason = (
                f"{len(hard_fail_records)} forward-era date(s) with an unacknowledged hard-fail "
                f"gate status: {[(r['date'], r['forwardGateStatus']) for r in hard_fail_records]}"
            )
            if acknowledged_hard_fail_records:
                exit_code_reason += (
                    f" (plus {len(acknowledged_hard_fail_records)} acknowledged legacy gap(s), "
                    f"excluded from this failure per data/edgelab/corpus_acknowledged_forward_gaps.json: "
                    f"{[r['date'] for r in acknowledged_hard_fail_records]})"
                )
        elif forward_consecutive_degraded >= CONSECUTIVE_DEGRADED_FORWARD_THRESHOLD:
            exit_should_fail = True
            exit_code_reason = (
                f"{forward_consecutive_degraded} consecutive degraded forward runs "
                f"(threshold {CONSECUTIVE_DEGRADED_FORWARD_THRESHOLD})"
            )
        elif acknowledged_hard_fail_records:
            exit_should_fail = False
            exit_code_reason = (
                f"Forward operational health is otherwise clean -- the only hard-fail-status "
                f"forward date(s) are acknowledged, permanently-unrecoverable legacy gaps "
                f"({[r['date'] for r in acknowledged_hard_fail_records]}), which never resolve and "
                f"therefore never drive this exit code -- see "
                f"data/edgelab/corpus_acknowledged_forward_gaps.json."
            )
        else:
            exit_should_fail = False
            exit_code_reason = "Forward operational health is clean -- no hard-fail dates, no consecutive-degraded escalation."

    report = {
        "schemaVersion": "2",
        "generatedAt": None,
        "enforcement": {
            "status": enforcement_status,
            "boundaryDate": boundary_date,
            "activatedAt": boundary["activatedAt"] if boundary else None,
            "activatingSnapshotId": boundary["activatingSnapshotId"] if boundary else None,
        },
        "historicalCorpusQuality": historical_corpus_quality,
        "forwardOperationalHealth": forward_operational_health,
        "exitShouldFail": exit_should_fail,
        "exitCodeReason": exit_code_reason,
        # ── Legacy top-level fields (unchanged computation, ALL dates) --
        # kept for backward compatibility with existing consumers/tests;
        # none of these drive the exit code any more.
        "datesCovered": len(per_date),  # matches len(perDate) exactly -- includes a synthesized pending "today" record, if any
        "productionRuns": len(production_dates),
        "expectedPregameSnapshots": expected_snapshots,
        "snapshotsSuccessfullyCaptured": captured_snapshots,
        "missingSnapshots": missing_snapshots,
        "snapshotsRecovered": total_recovered,
        "completenessStatusCounts": _count_by(per_date, "completenessStatus"),
        "productionCommitShaCoverage": {
            "known": sum(1 for r in per_date if r["productionCommitShaKnown"]),
            "total": len(per_date),
        },
        "effectiveConfigHashCoverage": {
            "known": sum(1 for r in per_date if r["effectiveConfigHashKnown"]),
            "total": len(per_date),
        },
        "candidateReplays": {
            "attempted": replays_attempted, "completed": replays_completed, "failed": replays_failed,
        },
        "marketsReplayed": markets_evaluated,
        "marketsComparable": markets_comparable,
        "clvLinkedMarkets": clv_linked,
        "settlementLinkedMarkets": settlement_linked,
        "unresolvedSettlementReasons": unresolved_settlement_reasons,
        "unresolvedClvReasons": unresolved_clv_reasons,
        "storageBytes": storage,
        "oldestTrustworthyReplayDate": min(trustworthy_dates) if trustworthy_dates else None,
        "newestTrustworthyReplayDate": max(trustworthy_dates) if trustworthy_dates else None,
        "gateStatusCounts": gate_counts,
        "consecutiveDegradedRuns": consecutive_degraded,
        "perDate": per_date,
    }
    report["generatedAt"] = ids.utc_now_iso()
    return report


def _count_by(records, field):
    """Coerces a missing/None value to the string "null" -- json.dump's
    sort_keys=True sorts raw Python dict keys before ever converting them
    to JSON strings, so a dict mixing a real None key with string keys
    (e.g. completenessStatus is None for a date with no manifest at all,
    alongside dates that DO have one) crashes TypeError: '<' not
    supported between 'str' and 'NoneType'. String keys throughout avoid
    that unconditionally, not just in the cases exercised so far."""
    counts = {}
    for r in records:
        v = r.get(field)
        key = v if v is not None else "null"
        counts[key] = counts.get(key, 0) + 1
    return counts


def render_markdown(report):
    enf = report["enforcement"]
    hist = report["historicalCorpusQuality"]
    fwd = report["forwardOperationalHealth"]
    lines = [
        "# EdgeLab Forward Replay Corpus Health Report",
        f"Generated: {report['generatedAt']}",
        "",
        "## Enforcement",
        f"- Status: **{enf['status']}**",
        f"- Boundary date: {enf['boundaryDate']}",
        f"- Activated at: {enf['activatedAt']}",
        f"- Exit should fail: {report['exitShouldFail']}",
        f"- Exit-code reason: {report['exitCodeReason']}",
        "",
        "## Historical corpus quality (descriptive only -- never fails this check)",
        f"- Historical/backfill dates: {hist['datesCovered']}",
    ]
    for status, count in sorted(hist["gateStatusCounts"].items(), key=lambda kv: str(kv[0])):
        lines.append(f"- {status}: {count}")
    lines += [
        "",
        "## Forward operational health (drives pass/fail)",
        f"- Population note: {fwd['populationNote']}",
        f"- Expected forward runs: {fwd['expectedRuns']}",
        f"- Forward snapshots captured: {fwd['snapshotsCaptured']}",
        f"- Forward snapshots missing (no manifest at all): {len(fwd['snapshotsMissing'])} {fwd['snapshotsMissing']}",
        f"- Forward incomplete captures (manifest exists, missing a required component): "
        f"{len(fwd['incompleteCaptures'])} {fwd['incompleteCaptures']}",
        f"- Forward dates pending today (not yet due): {len(fwd['pendingTodayDates'])} {fwd['pendingTodayDates']}",
        f"- Forward provenance coverage: {fwd['provenanceCoverage']['known']}/{fwd['provenanceCoverage']['total']}",
        f"- Forward replay: attempted {fwd['replayCompletion']['attempted']}, "
        f"completed {fwd['replayCompletion']['completed']}, failed {fwd['replayCompletion']['failed']}",
        f"- Forward CLV-linked markets: {fwd['clvCoverage']['linkedMarkets']}",
        f"- Forward settlement-linked markets: {fwd['settlementCoverage']['linkedMarkets']}",
        f"- Consecutive degraded forward runs: {fwd['consecutiveDegradedForwardRuns']}",
        f"- Hard-fail dates (drive exitShouldFail): {fwd['hardFailDates']}",
        f"- Acknowledged legacy gap dates (excluded from exitShouldFail, see "
        f"data/edgelab/corpus_acknowledged_forward_gaps.json): {fwd['acknowledgedLegacyGapDates']}",
    ]
    for status, count in sorted(fwd["gateStatusCounts"].items(), key=lambda kv: str(kv[0])):
        lines.append(f"- {status}: {count}")
    lines += [
        "",
        "## Storage",
        f"- Snapshots: {report['storageBytes']['snapshotsBytes']:,} bytes",
        f"- Replay runs: {report['storageBytes']['replayRunsBytes']:,} bytes",
        f"- Total: {report['storageBytes']['totalBytes']:,} bytes",
        "",
        "## Per-date detail",
        "| Date | Era | Gate Status | Forward Gate Status | Stored Completeness | Effective Completeness | Research-Only | Commit SHA Known | Replay | Runs | Acknowledged Gap |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for rec in report["perDate"]:
        ack = f"YES: {rec['acknowledgedGapReason']}" if rec["acknowledgedLegacyGap"] else ""
        lines.append(
            f"| {rec['date']} | {rec['era']} | {rec['gateStatus']} | {rec['forwardGateStatus']} | {rec['completenessStatus']} | "
            f"{rec['effectiveCompletenessStatus']} | {rec['isResearchOnlyRun']} | "
            f"{rec['productionCommitShaKnown']} | {rec['forwardReplayStatus']} | {rec['productionRunsCaptured']} | {ack} |"
        )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="EdgeLab forward replay corpus health report.")
    parser.add_argument("--report-path", default=REPORT_JSON_PATH)
    args = parser.parse_args()

    report = build_report()
    print(json.dumps({k: v for k, v in report.items() if k != "perDate"}, indent=2))

    os.makedirs(os.path.dirname(args.report_path), exist_ok=True)
    with open(args.report_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    md_path = os.path.splitext(args.report_path)[0] + ".md"
    with open(md_path, "w") as f:
        f.write(render_markdown(report))

    print(f"\nFull report written to {args.report_path} and {md_path}", file=sys.stderr)
    print(f"Enforcement: {report['enforcement']['status']} (boundary={report['enforcement']['boundaryDate']})", file=sys.stderr)

    if report["exitShouldFail"]:
        print(f"ALERT: {report['exitCodeReason']}", file=sys.stderr)
        sys.exit(1)
    print(f"OK: {report['exitCodeReason']}", file=sys.stderr)


if __name__ == "__main__":
    main()
