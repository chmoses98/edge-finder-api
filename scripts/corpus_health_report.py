#!/usr/bin/env python3
"""
scripts/corpus_health_report.py
===================================
Forward Replay Corpus and Production Provenance milestone (items 10/11):
daily + cumulative report on the health of the growing replay corpus --
production runs, snapshot capture coverage, provenance/config coverage,
candidate replay coverage, settlement/CLV linkage coverage, storage
growth, and mechanically-derived per-date quality gate statuses.

Read-only: scans data/pipeline/, data/edgelab/snapshots/,
data/edgelab/replay_runs/, data/edgelab/snapshot_recovery_log.jsonl, and
data/edgelab/forward_replay_status.json. Never writes to any production
file; its own output goes to data/edgelab/reports/corpus_health_report.json
(machine-readable) and .md (human-readable).

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

from lib.edgelab import replay  # noqa: E402
from lib.edgelab import snapshot as snap  # noqa: E402

_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

REPORT_JSON_PATH = os.path.join("data", "edgelab", "reports", "corpus_health_report.json")
REPORT_MD_PATH = os.path.join("data", "edgelab", "reports", "corpus_health_report.md")

# Item 11: mechanically-derived per-date quality gate statuses, worst-first
# (first true condition wins -- a date can match more than one, and the
# most severe one is what's reported, exactly like
# lib.edgelab.replay.assess_replay_eligibility's own first-match-wins
# rule table).
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


def _per_date_record(date, recovery_by_date, forward_status):
    """
    Builds one date's full record: pregame manifest (if any), quality gate
    status, and every metric item 10 asks for at the per-date grain.
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
        "effectiveConfigHashKnown": False,
        "recoveredCount": len(recoveries),
        "forwardReplayStatus": (fwd or {}).get("runStatus") or (fwd or {}).get("outcome"),
        "forwardReplayId": (fwd or {}).get("replayRunId"),
        "gateStatus": None,
    }

    if manifest:
        effective_config = next((c for c in manifest.get("components", []) if c["componentType"] == "EFFECTIVE_CONFIG"), None)
        record["effectiveConfigHashKnown"] = bool(effective_config and effective_config.get("availabilityStatus") in ("AVAILABLE", "PARTIAL"))

    # ── Item 11: gate status, first match wins ───────────────────────────
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

    return record


def build_report():
    production_dates = _production_run_dates()
    snapshot_dates = _all_snapshot_dates()
    all_dates = sorted(set(production_dates) | set(snapshot_dates))
    recovery_by_date = _recovery_log_by_date()
    forward_status = _forward_replay_status()

    per_date = [_per_date_record(d, recovery_by_date, forward_status) for d in all_dates]

    gate_counts = {}
    for rec in per_date:
        gate_counts[rec["gateStatus"]] = gate_counts.get(rec["gateStatus"], 0) + 1

    # Consecutive-degraded streak, most-recent-date-backward (item 11).
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
    for run_id in replay_run_ids:
        run = replay.load_replay_run(run_id)
        if run is None:
            continue
        replays_attempted += 1
        if run["runStatus"] == replay.RUN_STATUS_COMPLETED:
            replays_completed += 1
            s = run["summary"]
            markets_evaluated += s["marketsEvaluated"]
            markets_comparable += s["marketsComparable"]
            clv_linked += s["clvResolved"]
            settlement_linked += s["settledResolved"]
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

    storage = {
        "snapshotsBytes": _dir_size_bytes(snap.SNAPSHOTS_ROOT) if os.path.isdir(snap.SNAPSHOTS_ROOT) else 0,
        "replayRunsBytes": _dir_size_bytes(replay_runs_root) if os.path.isdir(replay_runs_root) else 0,
    }
    storage["totalBytes"] = storage["snapshotsBytes"] + storage["replayRunsBytes"]

    report = {
        "schemaVersion": "1",
        "generatedAt": None,
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
    from lib.edgelab import ids
    report["generatedAt"] = ids.utc_now_iso()
    return report


def _count_by(records, field):
    counts = {}
    for r in records:
        v = r.get(field)
        counts[v] = counts.get(v, 0) + 1
    return counts


def render_markdown(report):
    lines = [
        "# EdgeLab Forward Replay Corpus Health Report",
        f"Generated: {report['generatedAt']}",
        "",
        "## Coverage",
        f"- Production runs: {report['productionRuns']}",
        f"- Expected pregame snapshots: {report['expectedPregameSnapshots']}",
        f"- Snapshots captured: {report['snapshotsSuccessfullyCaptured']}",
        f"- Missing snapshots: {len(report['missingSnapshots'])} {report['missingSnapshots']}",
        f"- Snapshots recovered (cumulative): {report['snapshotsRecovered']}",
        f"- productionCommitSha coverage: {report['productionCommitShaCoverage']['known']}/{report['productionCommitShaCoverage']['total']}",
        f"- effectiveConfigHash coverage: {report['effectiveConfigHashCoverage']['known']}/{report['effectiveConfigHashCoverage']['total']}",
        "",
        "## Candidate replay",
        f"- Attempted: {report['candidateReplays']['attempted']}",
        f"- Completed: {report['candidateReplays']['completed']}",
        f"- Failed: {report['candidateReplays']['failed']}",
        f"- Markets replayed: {report['marketsReplayed']} (comparable: {report['marketsComparable']})",
        f"- CLV-linked: {report['clvLinkedMarkets']}, settlement-linked: {report['settlementLinkedMarkets']}",
        f"- Oldest trustworthy (Level 2) replay date: {report['oldestTrustworthyReplayDate']}",
        f"- Newest trustworthy (Level 2) replay date: {report['newestTrustworthyReplayDate']}",
        "",
        "## Storage",
        f"- Snapshots: {report['storageBytes']['snapshotsBytes']:,} bytes",
        f"- Replay runs: {report['storageBytes']['replayRunsBytes']:,} bytes",
        f"- Total: {report['storageBytes']['totalBytes']:,} bytes",
        "",
        "## Quality gates",
        f"- Consecutive degraded runs (most recent backward): {report['consecutiveDegradedRuns']}",
    ]
    for status, count in sorted(report["gateStatusCounts"].items(), key=lambda kv: str(kv[0])):
        lines.append(f"- {status}: {count}")
    lines.append("")
    lines.append("## Per-date detail")
    lines.append("| Date | Gate Status | Completeness | Commit SHA Known | Replay | Runs |")
    lines.append("|---|---|---|---|---|---|")
    for rec in report["perDate"]:
        lines.append(
            f"| {rec['date']} | {rec['gateStatus']} | {rec['completenessStatus']} | "
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

    integrity_failure_dates = [r["date"] for r in report["perDate"] if r["gateStatus"] == STATUS_INTEGRITY_FAILURE]

    # Maintainer review finding (item 10, PR #37 review): this script
    # computed consecutiveDegradedRuns and printed an ALERT line, but
    # main() always exited 0 regardless -- and nothing in this repository's
    # CI/workflows ever invoked this script at all. A dedicated check that
    # can never actually fail, and is never actually run automatically, is
    # not a check -- corpus degradation could accumulate indefinitely with
    # no visible signal beyond a human manually running this script. Fixed
    # two ways: (1) this script now exits 1 on the same conditions its own
    # ALERT/gate logic already treats as severe (3+ consecutive degraded
    # runs, or any INTEGRITY_FAILURE date in the checked window) so its
    # exit code is finally meaningful; (2) .github/workflows/corpus-health-check.yml
    # (new this review) runs it on a schedule and lets that non-zero exit
    # code actually fail the dedicated workflow, mirroring the existing
    # snapshot-capture-check.yml pattern exactly.
    if report["consecutiveDegradedRuns"] >= 3:
        print(f"ALERT: {report['consecutiveDegradedRuns']} consecutive degraded/missing runs.", file=sys.stderr)
    if integrity_failure_dates:
        print(f"ALERT: INTEGRITY_FAILURE for date(s): {', '.join(integrity_failure_dates)}", file=sys.stderr)

    if report["consecutiveDegradedRuns"] >= 3 or integrity_failure_dates:
        sys.exit(1)


if __name__ == "__main__":
    main()
