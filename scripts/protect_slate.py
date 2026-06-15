#!/usr/bin/env python3
"""
scripts/protect_slate.py
=========================
Post-write slate protection step for the GitHub Actions workflow.

Called after `data/slate.json` has been written by the fetch step.

Workflow:
  1. Read data/slate.json
  2. Scan for sentinel prices (hard reject: 19900, -19900, 100000, -100000)
  3. Determine run type via slate_manager.detect_run_type()
  4. Route to correct file path:
       - OFFICIAL_PREGAME  → data/slates/DATE/official_<ts>.json + authoritative.json
       - LINEUP_RECHECK    → data/slates/DATE/recheck_<ts>.json  (updates authoritative for new games)
       - IN_PLAY_RECHECK   → data/slates/DATE/recheck_<ts>.json  (frozen games protected)
       - REJECTED_CONTAMINATED → data/slates/DATE/rejected_contaminated_<ts>.json
  5. data/slate.json remains for backwards compatibility (always written as copy of authoritative)
  6. Post-slate review MUST use data/slates/DATE/authoritative.json as source of truth

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
    find_sentinel_in_object,
    RUN_TYPE_OFFICIAL_PREGAME,
    RUN_TYPE_LINEUP_RECHECK,
    RUN_TYPE_IN_PLAY_RECHECK,
    RUN_TYPE_REJECTED_CONTAMINATED,
)


def main(date_str=None):
    if not date_str:
        now_et = datetime.now(timezone(timedelta(hours=-4)))
        date_str = now_et.strftime("%Y-%m-%d")

    now_utc = datetime.now(timezone.utc)
    print(f"[protect_slate] Running for {date_str} at {now_utc.isoformat()}")

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

    # Validate date matches
    slate_date = slate_data.get("date", "")
    if slate_date and slate_date != date_str:
        print(f"[protect_slate] WARNING: slate.json date={slate_date} does not match expected {date_str}")

    # ── Sentinel check (hard reject) ──────────────────────────────────────────
    sentinels = find_sentinel_in_object(slate_data)
    if sentinels:
        paths_str = "; ".join(f"{p}={v}" for p, v in sentinels[:10])
        print(f"[protect_slate] SENTINEL PRICES DETECTED ({len(sentinels)} occurrences): {paths_str}")
        print(f"[protect_slate] Run will be quarantined as REJECTED_CONTAMINATED")
        run_type = RUN_TYPE_REJECTED_CONTAMINATED
    else:
        # Detect run type based on whether authoritative.json already exists
        run_type = detect_run_type(date_str, ROOT_DIR, now_utc)

    print(f"[protect_slate] Run type: {run_type}")

    # ── Save to structured path ───────────────────────────────────────────────
    result = save_slate(date_str, ROOT_DIR, slate_data, run_type)

    print(f"[protect_slate] Saved paths: {result.get('savedPaths', [])}")
    if result.get("runReport"):
        rr = result["runReport"]
        print(f"[protect_slate] Run report: accepted={rr.get('acceptedCount', 'N/A')}, "
              f"rejected={rr.get('rejectedCount', 'N/A')}, frozen={rr.get('frozenCount', 'N/A')}")

    # ── Backwards compat: update data/slate.json to be a copy of authoritative ──
    auth_path = get_authoritative_path(date_str, ROOT_DIR)
    if run_type != RUN_TYPE_REJECTED_CONTAMINATED and os.path.exists(auth_path):
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

    return 0


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(main(date_arg))
