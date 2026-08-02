#!/usr/bin/env python3
"""
scripts/check_snapshot_capture.py
====================================
Historical Capture Completeness and Immutable Snapshot Foundation
milestone (maintainer review item 1): the dedicated capture-completeness
check. Run by its own workflow (.github/workflows/snapshot-capture-check.yml),
NOT by fetch-slate.yml/edgelab-postgame.yml -- see
scripts/create_snapshot.py's module docstring for the full "workflow
failure policy" writeup.

For every date this repo has real evidence a production run occurred
(data/pipeline/<date>/recommendations.json exists), a PRE_GAME_DECISION
Snapshot is EXPECTED. Same for POST_GAME_SETTLEMENT (evidence:
data/edgelab/settlements/<date>.jsonl or clv_quotes/<date>.jsonl exist)
and CLOSING_LINE (evidence: data/edgelab/observations/<date>.jsonl.gz
exists).

For every expected-but-missing snapshot, this script attempts SAFE
recovery: it just calls lib.edgelab.snapshot.build_snapshot() again.
Capture is naturally idempotent (write-once with verified no-op) and
reads whatever source data still exists on disk RIGHT NOW -- it never
fabricates a historical value for data that has since been overwritten
or pruned (build_snapshot() already reports MISSING_REQUIRED_INPUT
honestly in that case; this script does not special-case it).

Exit code IS meaningful here (unlike create_snapshot.py, which is always
non-fatal): exits 1 if any expected snapshot could not be created even
on retry (a real, unrecovered gap), so the DEDICATED workflow calling
this script is allowed to go red -- the production slate workflow's own
green checkmark is never used to hide this.

Usage:
  python3 scripts/check_snapshot_capture.py [--lookback-days N]
"""
import argparse
import json
import os
import re
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from lib.edgelab import snapshot as snap  # noqa: E402

_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_LOOKBACK_DAYS = 14


def _recent_dates_with_evidence(subdir_check, lookback_days):
    """Every date within the lookback window for which subdir_check(date)
    returns True -- e.g. 'does data/pipeline/<date>/recommendations.json
    exist'. Deliberately CWD-relative (not ROOT_DIR-based), consistent
    with lib.edgelab.snapshot and testable via monkeypatch.chdir()."""
    pipeline_root = os.path.join("data", "pipeline")
    candidates = set()
    if os.path.isdir(pipeline_root):
        candidates.update(d for d in os.listdir(pipeline_root) if _DATE_DIR_RE.match(d))
    edgelab_root = os.path.join("data", "edgelab")
    for sub in ("settlements", "clv_quotes", "observations"):
        d = os.path.join(edgelab_root, sub)
        if os.path.isdir(d):
            for fn in os.listdir(d):
                m = re.match(r"^(\d{4}-\d{2}-\d{2})\.jsonl(\.gz)?$", fn)
                if m:
                    candidates.add(m.group(1))
    return sorted(date for date in candidates if subdir_check(date))


def _has_pipeline_recommendations(date):
    return os.path.exists(os.path.join("data", "pipeline", date, "recommendations.json"))


def _has_settlement_or_clv_evidence(date):
    return (
        os.path.exists(os.path.join("data", "edgelab", "settlements", f"{date}.jsonl"))
        or os.path.exists(os.path.join("data", "edgelab", "clv_quotes", f"{date}.jsonl"))
    )


def _has_observations_evidence(date):
    return os.path.exists(os.path.join("data", "edgelab", "observations", f"{date}.jsonl.gz"))


def _has_any_pregame_snapshot(date):
    return len(snap.list_pregame_run_dirs(date)) > 0


def _has_stage_snapshot(stage, date):
    return snap.load_manifest(stage, date) is not None


def check_and_recover(lookback_days=DEFAULT_LOOKBACK_DAYS):
    report = {"schemaVersion": "1", "checkedStages": {}}
    any_unrecovered = False

    for stage, evidence_fn, has_snapshot_fn in (
        (snap.STAGE_PRE_GAME_DECISION, _has_pipeline_recommendations, _has_any_pregame_snapshot),
        (snap.STAGE_POST_GAME_SETTLEMENT, _has_settlement_or_clv_evidence, lambda d: _has_stage_snapshot(snap.STAGE_POST_GAME_SETTLEMENT, d)),
        (snap.STAGE_CLOSING_LINE, _has_observations_evidence, lambda d: _has_stage_snapshot(snap.STAGE_CLOSING_LINE, d)),
    ):
        expected_dates = _recent_dates_with_evidence(evidence_fn, lookback_days)
        missing_dates = [d for d in expected_dates if not has_snapshot_fn(d)]
        recovered, unrecovered = [], []

        for date in missing_dates:
            try:
                result = snap.build_snapshot(stage, date)
                if result["outcome"] in ("created", "noop_verified"):
                    recovered.append({"date": date, "outcome": result["outcome"], "completenessStatus": result["manifest"]["completenessStatus"]})
                else:
                    unrecovered.append({"date": date, "outcome": result["outcome"]})
                    any_unrecovered = True
            except snap.SnapshotIntegrityError as e:
                unrecovered.append({"date": date, "error": str(e)})
                any_unrecovered = True

        report["checkedStages"][stage] = {
            "expectedDates": expected_dates,
            "missingBeforeRecovery": [d for d in missing_dates],
            "recovered": recovered,
            "stillMissingAfterRecoveryAttempt": unrecovered,
        }

    report["anyUnrecoveredGaps"] = any_unrecovered
    return report


def main():
    parser = argparse.ArgumentParser(description="Detect and safely recover missing EdgeLab Snapshots.")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    args = parser.parse_args()

    report = check_and_recover(args.lookback_days)
    print(json.dumps(report, indent=2))

    github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if github_summary:
        with open(github_summary, "a") as f:
            f.write("\n## Snapshot Capture Completeness Check\n")
            for stage, detail in report["checkedStages"].items():
                f.write(f"\n### {stage}\n")
                f.write(f"- expected dates checked: {len(detail['expectedDates'])}\n")
                if detail["recovered"]:
                    f.write(f"- :white_check_mark: recovered {len(detail['recovered'])} missing snapshot(s): "
                            f"{', '.join(r['date'] for r in detail['recovered'])}\n")
                if detail["stillMissingAfterRecoveryAttempt"]:
                    f.write(f"- :rotating_light: **UNRECOVERED GAP** for "
                            f"{', '.join(r['date'] for r in detail['stillMissingAfterRecoveryAttempt'])}\n")
            if report["anyUnrecoveredGaps"]:
                f.write("\n:rotating_light: **This check is FAILING** -- at least one expected snapshot "
                        "could not be created even on retry. See detail above.\n")
            else:
                f.write("\n:white_check_mark: No unrecovered capture gaps.\n")

    if report["anyUnrecoveredGaps"]:
        print("SNAPSHOT CAPTURE CHECK FAILED: unrecovered gap(s) -- see report above.", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
