"""
lib/snapshot_retention.py
============================
Production Reliability and Settlement Recovery milestone: retention logic
for data/kalshi_registry_snapshots/, the repo's dominant storage-growth
driver (126MB / 370 files at the time of this milestone's audit -- by far
the largest data/ subtree; data/slates/ was also audited and found to grow
at a bounded ~1 directory/day, i.e. NOT a high-frequency archive, so it is
out of scope here).

Two file shapes exist in that directory (see its own README.md and
.github/workflows/capture-snapshots-scheduled.yml):
  - "dated" files:       kalshi_search_YYYY-MM-DD.json (one per slate date,
    the primary snapshot scripts/backfill_market_identity.py matches bets
    against -- KEPT FOREVER, never pruned by this module).
  - "timestamped" files: kalshi_search_YYYY-MM-DD_HHMM.json (up to ~28/day,
    written every 30 minutes by capture-snapshots-scheduled.yml "for
    multi-snapshot CLV precision" -- scripts/clv_from_snapshot.py globs
    these to find the price closest to a game's first pitch). These are
    the actual source of the unbounded growth.

Root cause of the growth (found during this milestone, previously
undocumented): capture-snapshots-scheduled.yml already contains a "Clean
up timestamped snapshots older than 3 days" step, but it uses `find
-mtime +3` against the FILESYSTEM's modification time. `actions/checkout`
does a fresh checkout on every job run, and git checkouts do not preserve
historical commit timestamps -- every file's mtime becomes "now" (the
checkout time), not the date embedded in its own filename. `-mtime +3`
therefore can never match anything on a real runner (every file was just
"modified" by the checkout), so that step has silently done nothing since
it was written -- confirmed by the archive itself containing timestamped
files back to 2026-06-08, far older than the "3 days" the step claims to
enforce.

This module fixes the underlying logic (parse the DATE FROM THE FILENAME,
never the filesystem mtime) and is shared by both call sites: the ongoing
per-run enforcement in capture-snapshots-scheduled.yml (via
scripts/prune_kalshi_snapshots.py) and the one-time backlog cleanup for
the files that already over-accumulated while the old check was broken.

Retention window: this milestone widens the originally-intended 3-day
window to 21 days (three weeks). 3 days is demonstrably too short for
this repo's own recovery needs -- this same milestone found real
settlement/CLV runs that went unnoticed and unresolved for WEEKS (see
docs/POSTMORTEM_PRODUCTION_RELIABILITY_2026.md), and a timestamped
snapshot pruned before anyone notices a failed run cannot be used to
recover it. 21 days is a conservative buffer past that observed recovery
latency while still bounding growth to a small, fixed multiple of the
per-day snapshot count (roughly 28 snapshots/day * 21 days per market
family window, instead of unbounded calendar history).
"""

import os
import re

DEFAULT_RETENTION_DAYS = 21

_DATED_RE = re.compile(r"^kalshi_search_(\d{4}-\d{2}-\d{2})\.json$")
_TIMESTAMPED_RE = re.compile(r"^kalshi_search_(\d{4}-\d{2}-\d{2})_(\d{4})\.json$")


def classify_filename(filename):
    """
    Returns ("dated", date_str) or ("timestamped", date_str) or
    (None, None) for anything that doesn't match either pattern (e.g.
    README.md) -- unrecognized files are never touched by this module.
    """
    m = _DATED_RE.match(filename)
    if m:
        return "dated", m.group(1)
    m = _TIMESTAMPED_RE.match(filename)
    if m:
        return "timestamped", m.group(1)
    return None, None


def _parse_date(date_str):
    from datetime import date
    y, m, d = (int(part) for part in date_str.split("-"))
    return date(y, m, d)


def build_retention_plan(snapshot_dir, today, retention_days=DEFAULT_RETENTION_DAYS):
    """
    today: a datetime.date (caller-supplied, so this is deterministic and
    testable -- never reads the system clock itself).

    Returns a dict:
      {
        "schemaVersion": "1",
        "snapshotDir": <path>,
        "today": "YYYY-MM-DD",
        "retentionDays": <int>,
        "totalFilesConsidered": <int>,
        "unrecognizedFilesSkipped": [<filename>, ...],
        "datedFilesKeptForever": <int>,
        "timestampedFilesKept": <int>,
        "timestampedFilesToPrune": [<filename>, ...],
        "projectedBytesReclaimed": <int>,
    }

    Never deletes anything itself -- purely a plan builder. Deterministic:
    the same directory contents + today + retention_days always produce
    the same plan.
    """
    entries = sorted(os.listdir(snapshot_dir))
    unrecognized = []
    dated_count = 0
    timestamped_kept = 0
    to_prune = []
    bytes_reclaimed = 0

    for filename in entries:
        path = os.path.join(snapshot_dir, filename)
        if not os.path.isfile(path):
            continue
        kind, date_str = classify_filename(filename)
        if kind is None:
            unrecognized.append(filename)
            continue
        if kind == "dated":
            dated_count += 1
            continue
        # timestamped
        age_days = (today - _parse_date(date_str)).days
        if age_days > retention_days:
            to_prune.append(filename)
            bytes_reclaimed += os.path.getsize(path)
        else:
            timestamped_kept += 1

    return {
        "schemaVersion": "1",
        "snapshotDir": snapshot_dir,
        "today": today.isoformat(),
        "retentionDays": retention_days,
        "totalFilesConsidered": len(entries),
        "unrecognizedFilesSkipped": unrecognized,
        "datedFilesKeptForever": dated_count,
        "timestampedFilesKept": timestamped_kept,
        "timestampedFilesToPrune": to_prune,
        "projectedBytesReclaimed": bytes_reclaimed,
    }
