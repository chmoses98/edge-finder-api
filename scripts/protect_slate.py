#!/usr/bin/env python3
"""
scripts/protect_slate.py
=========================
Post-write slate protection step for the GitHub Actions workflow.

Called after `data/slate.json` has been written by the fetch step.

Workflow:
  1. Read data/slate.json
  2. Strip any sentinel metadata fields written by prior runs (prevents self-referential false positives)
  3. Scan for sentinel prices using field-aware scanner (hard reject: 19900, -19900, 100000, -100000)
  4. Determine run type via slate_manager.detect_run_type()
  5. Route to correct file path:
       - OFFICIAL_PREGAME  → data/slates/DATE/official_<ts>.json + authoritative.json
       - LINEUP_RECHECK    → data/slates/DATE/recheck_<ts>.json  (updates authoritative for new games)
       - IN_PLAY_RECHECK   → data/slates/DATE/recheck_<ts>.json  (frozen games protected)
       - REJECTED_CONTAMINATED → data/slates/DATE/rejected_contaminated_<ts>.json
  6. data/slate.json remains for backwards compatibility (always written as copy of authoritative)
  7. Post-slate review MUST use data/slates/DATE/authoritative.json as source of truth

Usage:
  python scripts/protect_slate.py [DATE]

  DATE: YYYY-MM-DD (default: today ET)
  
Exit codes:
  0 — protection applied (even if run was quarantined — that is normal)
  1 — hard failure (slate.json not found or unreadable)
"""

import json
import os
import sys
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR    = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, ROOT_DIR)

from lib.slate_manager import (
    detect_run_type,
    save_slate,
    get_authoritative_path,
    authoritative_exists,
    load_authoritative,
    RUN_TYPE_OFFICIAL_PREGAME,
    RUN_TYPE_LINEUP_RECHECK,
    RUN_TYPE_IN_PLAY_RECHECK,
    RUN_TYPE_SCHEDULED_REFRESH,
    RUN_TYPE_REJECTED_CONTAMINATED,
    TRIGGER_SCHEDULE,
    TRIGGER_MANUAL,
)

# Use the field-aware sentinel scanner from sentinel_validator.
# This scanner only checks known price/odds fields (price, kalshiPrice, etc.)
# and does NOT flag non-price fields like gameId, volume, attendance, etc.
from lib.sentinel_validator import scan_for_sentinels

# Metadata keys written by prior protection runs — strip before scanning to
# prevent self-referential false positives (a quarantined run writes violation
# values into slate.json; the next run re-scans those same values and quarantines again).
_SENTINEL_METADATA_KEYS = {
    "_sentinelViolations",
    "_containsSentinels",
    "_sentinelViolationCount",
    "_sentinelCheckRan",
    "_runType",
    "_quarantined",
}


def _strip_sentinel_metadata(slate_data):
    """Return a shallow copy of slate_data with sentinel metadata keys removed."""
    return {k: v for k, v in slate_data.items() if k not in _SENTINEL_METADATA_KEYS}


def evaluate_date_mismatch_pure(slate_data, expected_date):
    """
    Pure (Phase 9 Part 9): returns the date-mismatch warning text, or
    None. Extracted at the SAME point in the original control flow it
    always ran at -- BEFORE any sentinel-metadata stripping/scanning --
    deliberately preserving the original's exact statement order and
    exception-origination point (a non-dict `slate_data` raises
    AttributeError here, on `.get("date", "")`, exactly as the
    original did, not at some later reordered point).
    """
    slate_date = slate_data.get("date", "")
    if slate_date and slate_date != expected_date:
        return (
            f"[protect_slate] WARNING: slate.json date={slate_date} "
            f"does not match expected {expected_date}"
        )
    return None


def evaluate_sentinel_gate_pure(sentinels):
    """
    Pure (Phase 9 Part 9): accepts the already-computed sentinel scan
    result (from scan_for_sentinels(), itself pure, called by the
    shell on the already-pure _strip_sentinel_metadata() output).
    Returns a plain dict with no side effects: no file I/O, no clock
    reads, no printing, no sys.exit(), no mutation of `sentinels`.

    Deliberately does NOT decide the full run_type: when no sentinels
    are present, the legacy behavior calls lib.slate_manager.detect_run_type(),
    which reads authoritative.json from disk (I/O owned by the shared,
    out-of-scope library, not this script) -- 'runTypeOverride' is None
    in that case, signaling the caller must still call detect_run_type()
    itself. This is not a forced abstraction: it reflects the actual
    data dependency in the original code (the sentinel branch is fully
    decidable from already-in-memory data; the non-sentinel branch is
    not).
    """
    if not sentinels:
        return {"runTypeOverride": None, "sentinelLines": []}

    paths_str = "; ".join(f"{s['path']}={s['value']}" for s in sentinels[:10])
    return {
        "runTypeOverride": RUN_TYPE_REJECTED_CONTAMINATED,
        "sentinelLines": [
            f"[protect_slate] SENTINEL PRICES DETECTED ({len(sentinels)} occurrences): {paths_str}",
            "[protect_slate] Run will be quarantined as REJECTED_CONTAMINATED",
        ],
    }


def should_sync_legacy_slate_json_pure(run_type, auth_path_exists):
    """
    Pure predicate (Phase 9 Part 9): whether main() should copy
    authoritative.json over data/slate.json for backwards compat.
    Identical condition to the original inline `if` check, extracted
    verbatim: never sync a quarantined run; otherwise sync only if
    authoritative.json actually exists on disk (existence itself must
    be checked by the caller and passed in explicitly -- this function
    performs no I/O of its own).
    """
    return run_type != RUN_TYPE_REJECTED_CONTAMINATED and auth_path_exists


def build_protection_artifact_payload(date_str, run_type, sentinels, result, synced, auth_exists):
    """
    Pure (Phase 9 Part 16): builds the narrow, additive payload for the
    data/pipeline/<date>/protection.json artifact from values main()
    has already computed by this point in its own execution -- no
    second protection computation. Deliberately excludes the full
    per-game accepted/rejected/frozen breakdown lib.slate_manager's
    `runReport` carries (that detail reflects slate_manager.py's OWN
    merge decisions, a stage this script doesn't own -- narrowed to
    just the counts, matching the precedent set by validation.json in
    Phase 8, which excluded decision detail owned by another stage).
    """
    run_report = result.get("runReport") or {}
    return {
        "date": date_str,
        "runType": run_type,
        "status": "quarantined" if run_type == RUN_TYPE_REJECTED_CONTAMINATED else "ok",
        "sentinelCount": len(sentinels),
        "savedPaths": list(result.get("savedPaths", [])),
        "authoritativeWritten": result.get("authoritativeWritten"),
        "authoritativeUpdated": result.get("authoritativeUpdated"),
        "runReportSummary": {
            "acceptedCount": run_report.get("acceptedCount"),
            "rejectedCount": run_report.get("rejectedCount"),
            "frozenCount": run_report.get("frozenCount"),
            "quarantined": run_report.get("quarantined"),
        } if run_report else None,
        "syncedLegacySlateJson": synced,
        "authoritativeExists": auth_exists,
    }


def main(date_str=None, trigger_source=None):
    if not date_str:
        now_et = datetime.now(timezone(timedelta(hours=-4)))
        date_str = now_et.strftime("%Y-%m-%d")

    trigger_source = trigger_source if trigger_source == TRIGGER_SCHEDULE else TRIGGER_MANUAL

    now_utc = datetime.now(timezone.utc)
    print(f"[protect_slate] Running for {date_str} at {now_utc.isoformat()} (trigger={trigger_source})")

    slate_path = os.path.join(ROOT_DIR, "data", "slate.json")
    if not os.path.exists(slate_path):
        print(f"[protect_slate] FAIL: data/slate.json not found at {slate_path}", file=sys.stderr)
        sys.exit(1)

    with open(slate_path) as f:
        try:
            slate_data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"[protect_slate] FAIL: Cannot parse data/slate.json: {e}", file=sys.stderr)
            sys.exit(1)

    # Validate date matches -- computed and printed at the exact same
    # point the original always did, before any sentinel-related code
    # runs (Phase 9 Part 10: exact statement-order preservation).
    date_mismatch_warning = evaluate_date_mismatch_pure(slate_data, date_str)
    if date_mismatch_warning:
        print(date_mismatch_warning)

    # ── Sentinel check (hard reject) ──────────────────────────────────────────
    # Strip prior-run metadata first to prevent self-referential false positives,
    # then use the field-aware scanner that only checks actual price/odds fields.
    scan_data = _strip_sentinel_metadata(slate_data)
    sentinels = scan_for_sentinels(scan_data)

    # One pure evaluation of the sentinel gate (Phase 9 Part 17: one
    # decision, multiple print-outputs below all read from this single
    # `sentinel_decision` dict, never recomputed).
    sentinel_decision = evaluate_sentinel_gate_pure(sentinels)

    for line in sentinel_decision["sentinelLines"]:
        print(line)

    if sentinel_decision["runTypeOverride"] is not None:
        run_type = sentinel_decision["runTypeOverride"]
    else:
        # Detect run type based on trigger_source and whether
        # authoritative.json already exists (see lib.slate_manager's
        # "Scheduled vs. manual authority" docstring section).
        run_type = detect_run_type(date_str, ROOT_DIR, now_utc, trigger_source=trigger_source)

    print(f"[protect_slate] Run type: {run_type}")

    # ── Save to structured path ───────────────────────────────────────────────
    result = save_slate(date_str, ROOT_DIR, slate_data, run_type, trigger_source=trigger_source)

    print(f"[protect_slate] Saved paths: {result.get('savedPaths', [])}")
    if result.get("runReport"):
        rr = result["runReport"]
        print(f"[protect_slate] Run report: accepted={rr.get('acceptedCount', 'N/A')}, "
              f"rejected={rr.get('rejectedCount', 'N/A')}, frozen={rr.get('frozenCount', 'N/A')}")

    # ── Backwards compat: update data/slate.json to be a copy of authoritative ──
    auth_path = get_authoritative_path(date_str, ROOT_DIR)
    synced = should_sync_legacy_slate_json_pure(run_type, os.path.exists(auth_path))
    if synced:
        shutil.copy2(auth_path, slate_path)
        print(f"[protect_slate] data/slate.json updated to match authoritative.json")
    elif run_type == RUN_TYPE_REJECTED_CONTAMINATED:
        # Do NOT update data/slate.json from a quarantined run
        # Keep previous slate.json (if any) or leave as-is
        print(f"[protect_slate] Quarantined run — data/slate.json NOT updated")

    # ── Summary ───────────────────────────────────────────────────────────────
    auth_exists = os.path.exists(auth_path)
    print(f"[protect_slate] authoritative.json exists: {auth_exists}")
    print(f"[protect_slate] Done. Run type: {run_type}")

    # ── Phase 9: immutable pipeline protection artifact ─────────────────────
    # Best-effort, additive, non-authoritative -- published from values
    # main() has already computed by this point (run_type, sentinels,
    # result, synced, auth_exists), never a second protection
    # computation. Wrapped so any failure only prints a warning; it
    # never changes this function's return value, never touches
    # data/slate.json or authoritative.json.
    try:
        from lib.pipeline_artifacts import write_stage_artifact
        payload = build_protection_artifact_payload(
            date_str, run_type, sentinels, result, synced, auth_exists,
        )
        write_stage_artifact(
            "protection", date_str, payload,
            produced_by="scripts/protect_slate.py",
            status="canonical",
            source_stage="validation",
        )
        print(f"[protect_slate] protection pipeline artifact written for {date_str}")
    except Exception as e:
        print(f"[protect_slate] WARNING: could not write protection pipeline artifact: {e}")

    return 0


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    # Scheduled Research Freshness mission: fetch-slate.yml passes
    # SLATE_TRIGGER_SOURCE=schedule|manual (env var, not a positional CLI
    # arg, so it never collides with a future extra positional argument).
    # Unset/unrecognized defaults to "manual" -- see
    # lib.slate_manager._normalize_trigger_source's own docstring for why
    # that is the safe default for any pre-existing/unaware caller.
    trigger_arg = os.environ.get("SLATE_TRIGGER_SOURCE")
    sys.exit(main(date_arg, trigger_source=trigger_arg))
