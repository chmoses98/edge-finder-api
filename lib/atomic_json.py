#!/usr/bin/env python3
"""
lib/atomic_json.py
=====================
Shared plain-JSON atomic-write helper (Phase 6 Part 5).

Phase 5 found the same small pattern -- serialize to a temp file in the
destination directory, fsync it, os.replace() it into place, chmod the
temp file to the umask-default mode first so the atomic write doesn't
silently narrow the destination's permissions -- independently inlined,
byte-for-byte identically, into BOTH scripts/fetch_lineups.py's and
scripts/fetch_savant_pitchers.py's private `_write_slate_atomic()`
helpers. That PR's review explicitly deferred consolidating them
("do not consolidate... unless a correctness issue requires it... document
the duplication for a later phase"). Phase 6 needs a third copy of the
exact same pattern for scripts/post_fetch_gate.py's slate.json write-back
-- this is that later phase, and the mission explicitly invites
introducing one small shared helper now rather than a fourth inline copy.

This is deliberately NOT lib/pipeline_artifacts.py's write_stage_artifact():
that helper wraps its payload in a {"meta": ..., "data": ...} envelope
tailored to versioned pipeline-stage artifacts. data/slate.json's format
is a raw slate object with no envelope, and always has been -- wrapping
it would be a real output-format change, not a reliability fix. This
module writes exactly the payload it's given, nothing more.

Migrated callers (this phase): scripts/fetch_lineups.py,
scripts/fetch_savant_pitchers.py (their private _write_slate_atomic()
functions now delegate here instead of duplicating the logic),
scripts/post_fetch_gate.py's new slate.json write-back.

Deliberately NOT migrated: lib/slate_manager.py's `_write_json()` (used
for data/authoritative.json and related slate-snapshot paths) --
authoritative.json is explicitly out of scope for Phase 6, and
slate_manager.py is not named anywhere in the Phase 6 mission. This is a
scoped, small migration of the three directly-relevant callers already
named in this phase's own work, not a repository-wide write-helper
migration.
"""

import json
import os
import tempfile


def write_json_atomic(payload, path):
    """
    Write `payload` as plain JSON to `path` atomically: serialize to a
    temp file in the same directory as `path` (so the final replace is a
    same-filesystem rename, never a cross-device copy), fsync it, then
    move it into place with os.replace(). A plain `open(path, 'w')` +
    `json.dump()` writes incrementally, so an exception partway through
    serialization can leave a truncated, invalid file at `path` -- this
    never happens here: any exception before the final os.replace()
    leaves the previous valid file (or no file, on a first run)
    completely untouched, and the stray temp file is removed.

    No indent, no sort_keys -- byte-for-byte what `json.dump(payload, f)`
    on its own would produce, matching every one of this helper's
    callers' pre-existing output format exactly.

    File permissions: tempfile.mkstemp() creates its file with mode 0600
    regardless of the process umask, and os.replace() preserves the
    source file's mode on rename -- so without an explicit chmod, this
    write would silently narrow `path`'s permissions from the
    umask-default mode a plain `open(path, 'w')` produces (0644 under the
    common 0022 umask) down to 0600 on every run. The temp file's mode is
    reset to the umask-default before the rename so this is truly a
    write-mechanism-only change, never a permissions change too.
    """
    dest_dir = os.path.dirname(path) or '.'
    umask = os.umask(0o022)
    os.umask(umask)  # os.umask() has no read-only form; restore immediately
    default_mode = 0o666 & ~umask
    prefix = f'.{os.path.basename(path)}.'
    fd, tmp_path = tempfile.mkstemp(prefix=prefix, suffix='.json.tmp', dir=dest_dir)
    try:
        os.chmod(tmp_path, default_mode)
        with os.fdopen(fd, 'w') as f:
            json.dump(payload, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
