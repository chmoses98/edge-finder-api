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

Usage:
  python3 scripts/corpus_health_report.py [--report-path PATH]
"""
import argparse
import json
import os
import re
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from lib.edgelab import ids  # noqa: E402
from lib.edgelab import replay  # noqa: E402
from lib.edgelab import snapshot as snap  # noqa: E402

_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

REPORT_JSON_PATH = os.path.join("data", "edgelab", "reports", "corpus_health_report.json")
REPORT_MD_PATH = os.path.join("data", "edgelab", "reports", "corpus_health_report.md")
ENFORCEMENT_BOUNDARY_PATH = os.path.join("data", "edgelab", "corpus_enforcement_boundary.json")

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
STATUS_FORWARD_PROVENANCE_AMBIGUOUS = "FORWARD_PROVENANCE_AMBIGUOUS"
STATUS_FORWARD_REPLAY_FAILURE = "FORWARD_REPLAY_FAILURE"
STATUS_FORWARD_CLOSING_DATA_PENDING = "FORWARD_CLOSING_DATA_PENDING"
STATUS_FORWARD_SETTLEMENT_DATA_PENDING = "FORWARD_SETTLEMENT_DATA_PENDING"
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
    STATUS_FORWARD_PROVENANCE_AMBIGUOUS,
    STATUS_FORWARD_REPLAY_FAILURE,
})
# Non-fatal on their own (postgame data naturally lags same-day capture),
# but still count toward the consecutive-degraded-forward-runs escalation
# signal so a persistent, multi-day gap is still eventually visible.
NON_FATAL_DEGRADED_FORWARD_STATUSES = frozenset({
    STATUS_FORWARD_CLOSING_DATA_PENDING,
    STATUS_FORWARD_SETTLEMENT_DATA_PENDING,
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
        manifest = snap.load_latest_pregame_manifest(date)
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


def _forward_gate_status(rec, manifest, closing_manifest, postgame_manifest):
    """
    First-match-wins, forward-era-only rule table (see
    HARD_FAIL_FORWARD_STATUSES for which of these can fail the workflow).
    """
    if manifest is None:
        return STATUS_FORWARD_MISSING_SNAPSHOT
    verification = snap.verify_snapshot(manifest)
    if verification["overallStatus"] != "VERIFIED":
        return STATUS_INTEGRITY_FAILURE
    provenance_status = (manifest.get("productionProvenance") or {}).get("status")
    if provenance_status in ("MISSING", "AMBIGUOUS"):
        return STATUS_FORWARD_PROVENANCE_AMBIGUOUS
    if manifest.get("completenessStatus") == "MISSING_REQUIRED_INPUT":
        # A required input other than provenance is missing (provenance
        # itself was just checked and ruled out above) -- still an
        # actionable forward capture gap, not a silent pass.
        return STATUS_FORWARD_MISSING_SNAPSHOT
    if rec["forwardReplayStatus"] not in ("COMPLETED", "completed"):
        return STATUS_FORWARD_REPLAY_FAILURE
    if closing_manifest is None:
        return STATUS_FORWARD_CLOSING_DATA_PENDING
    if postgame_manifest is None:
        return STATUS_FORWARD_SETTLEMENT_DATA_PENDING
    return STATUS_FORWARD_HEALTHY


def _per_date_record(date, recovery_by_date, forward_status):
    """
    Builds one date's full record: pregame manifest (if any), quality gate
    status, and every metric item 10 asks for at the per-date grain.
    era/forwardGateStatus are filled in by build_report() once the
    enforcement boundary is known (a single date's era depends on global
    state, not just its own data).
    """
    manifest = snap.load_latest_pregame_manifest(date)
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
    }

    if manifest:
        effective_config = next((c for c in manifest.get("components", []) if c["componentType"] == "EFFECTIVE_CONFIG"), None)
        record["effectiveConfigHashKnown"] = bool(effective_config and effective_config.get("availabilityStatus") in ("AVAILABLE", "PARTIAL"))

    # ── Item 11 (original): overall gate status, first match wins. Kept
    # exactly as before -- this is the "historical corpus quality" signal
    # and must keep reflecting reality (including MISSING/PARTIAL) for
    # historical dates; it simply no longer drives the exit code alone.
    if manifest is None:
        record["gateStatus"] = STATUS_DEGRADED_MISSING_SNAPSHOT
    else:
        verification = snap.verify_snapshot(manifest)
        if verification["overallStatus"] != "VERIFIED":
            record["gateStatus"] = STATUS_INTEGRITY_FAILURE
        elif manifest["completenessStatus"] == "MISSING_REQUIRED_INPUT":
            record["gateStatus"] = STATUS_DEGRADED_MISSING_SNAPSHOT
        elif not record["productionCommitShaKnown"]:
            record["gateStatus"] = STATUS_DEGRADED_CONFIG_PARTIAL
        elif fwd is None:
            record["gateStatus"] = STATUS_DEGRADED_REPLAY_FAILURE
        elif fwd.get("runStatus") not in (None, replay.RUN_STATUS_COMPLETED) and fwd.get("outcome") not in ("completed",):
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


def build_report():
    production_dates = _production_run_dates()
    snapshot_dates = _all_snapshot_dates()
    all_dates = sorted(set(production_dates) | set(snapshot_dates))
    recovery_by_date = _recovery_log_by_date()
    forward_status = _forward_replay_status()

    per_date = []
    manifests_by_date = {}
    for d in all_dates:
        rec, manifest = _per_date_record(d, recovery_by_date, forward_status)
        per_date.append(rec)
        manifests_by_date[d] = manifest

    # ── Enforcement boundary + era assignment (new) ──────────────────────
    boundary = _determine_enforcement_boundary(all_dates)
    boundary_date = boundary["enforcementBoundaryDate"] if boundary else None
    for rec in per_date:
        rec["era"] = ERA_FORWARD if (boundary_date and rec["date"] >= boundary_date) else ERA_HISTORICAL
        if rec["era"] == ERA_FORWARD:
            manifest = manifests_by_date[rec["date"]]
            closing = snap.load_manifest(snap.STAGE_CLOSING_LINE, rec["date"])
            postgame = snap.load_manifest(snap.STAGE_POST_GAME_SETTLEMENT, rec["date"])
            rec["forwardGateStatus"] = _forward_gate_status(rec, manifest, closing, postgame)

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
    forward_expected_dates = [d for d in production_dates if boundary_date and d >= boundary_date]
    forward_gate_counts = _count_by(forward_records, "forwardGateStatus")
    forward_consecutive_degraded = 0
    for rec in reversed(forward_records):
        if rec["forwardGateStatus"] != STATUS_FORWARD_HEALTHY:
            forward_consecutive_degraded += 1
        else:
            break
    hard_fail_records = [r for r in forward_records if r["forwardGateStatus"] in HARD_FAIL_FORWARD_STATUSES]

    forward_operational_health = {
        "expectedRuns": len(forward_expected_dates),
        "snapshotsCaptured": sum(1 for r in forward_records if r["snapshotId"]),
        "snapshotsMissing": [
            r["date"] for r in forward_records if r["date"] in forward_expected_dates and not r["snapshotId"]
        ],
        "provenanceCoverage": {
            "known": sum(1 for r in forward_records if r["productionCommitShaKnown"]),
            "total": len(forward_records),
        },
        "replayCompletion": forward_replay,
        "clvCoverage": {"linkedMarkets": forward_clv_linked},
        "settlementCoverage": {"linkedMarkets": forward_settlement_linked},
        "gateStatusCounts": forward_gate_counts,
        "consecutiveDegradedForwardRuns": forward_consecutive_degraded,
        "hardFailDates": [r["date"] for r in hard_fail_records],
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
                f"{len(hard_fail_records)} forward-era date(s) with a hard-fail "
                f"gate status: {[(r['date'], r['forwardGateStatus']) for r in hard_fail_records]}"
            )
        elif forward_consecutive_degraded >= CONSECUTIVE_DEGRADED_FORWARD_THRESHOLD:
            exit_should_fail = True
            exit_code_reason = (
                f"{forward_consecutive_degraded} consecutive degraded forward runs "
                f"(threshold {CONSECUTIVE_DEGRADED_FORWARD_THRESHOLD})"
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
        "datesCovered": len(all_dates),
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
        f"- Expected forward runs: {fwd['expectedRuns']}",
        f"- Forward snapshots captured: {fwd['snapshotsCaptured']}",
        f"- Forward snapshots missing: {len(fwd['snapshotsMissing'])} {fwd['snapshotsMissing']}",
        f"- Forward provenance coverage: {fwd['provenanceCoverage']['known']}/{fwd['provenanceCoverage']['total']}",
        f"- Forward replay: attempted {fwd['replayCompletion']['attempted']}, "
        f"completed {fwd['replayCompletion']['completed']}, failed {fwd['replayCompletion']['failed']}",
        f"- Forward CLV-linked markets: {fwd['clvCoverage']['linkedMarkets']}",
        f"- Forward settlement-linked markets: {fwd['settlementCoverage']['linkedMarkets']}",
        f"- Consecutive degraded forward runs: {fwd['consecutiveDegradedForwardRuns']}",
        f"- Hard-fail dates: {fwd['hardFailDates']}",
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
        "| Date | Era | Gate Status | Forward Gate Status | Completeness | Commit SHA Known | Replay | Runs |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for rec in report["perDate"]:
        lines.append(
            f"| {rec['date']} | {rec['era']} | {rec['gateStatus']} | {rec['forwardGateStatus']} | {rec['completenessStatus']} | "
            f"{rec['productionCommitShaKnown']} | {rec['forwardReplayStatus']} | {rec['productionRunsCaptured']} |"
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
