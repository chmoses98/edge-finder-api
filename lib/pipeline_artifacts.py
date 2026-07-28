#!/usr/bin/env python3
"""
lib/pipeline_artifacts.py
===========================
Shared helper for writing immutable, versioned pipeline-stage artifacts
alongside the legacy data/slate.json working file.

This is the first building block of the Phase 3 immutable-pipeline
architecture (see docs/IMMUTABLE_PIPELINE.md). A stage that adopts it:

  1. Computes its output exactly as it did before — no change to WHAT is
     computed, only to where the result additionally gets written.
  2. Continues writing data/slate.json (or whatever legacy path
     downstream scripts still expect) exactly as before, so production
     behavior is completely unchanged.
  3. ALSO writes that same output to data/pipeline/<date>/<stage>.json —
     a new, distinctly-named artifact this stage owns, that no other
     script writes.

This additive pattern is deliberate: it does not require every consumer
of data/slate.json to be migrated before any stage can start producing an
immutable artifact. Consumers can switch to reading a stage's immutable
artifact directly at their own pace in a future phase, instead of all at
once. Per the same reasoning, callers should wrap write_stage_artifact()
in a try/except and treat any failure as non-fatal — this is a new,
additive code path and must never be able to break the pipeline stage
that adopts it.

ARTIFACT SHAPE (schema version "1.0")
--------------------------------------
Every artifact written by write_stage_artifact() is a JSON object with
exactly two top-level keys:

    {
      "meta": {
        "stage":          "<stage name>",
        "slateDate":      "<slate date, YYYY-MM-DD>",
        "createdAt":      "<ISO 8601 UTC timestamp>",
        "schemaVersion":  "1.0",
        "producedBy":     "<script/module that called write_stage_artifact>",
        "status":         "canonical" | "transitional",
        "sourceStage":    "<parent stage name, or null if none/not applicable>"
      },
      "data": <the payload passed by the caller, unmodified>
    }

`status` and `sourceStage` (Phase 4 additions) are optional metadata for a
reader trying to decide how much to trust an artifact's shape without
already knowing which stage produced it:

  - "canonical" (the default) means the artifact's schema is the intended,
    narrowed shape for that stage — safe to depend on going forward.
  - "transitional" means the artifact's payload is a stopgap (e.g. a full
    legacy slate snapshot) that is expected to be narrowed in a future
    phase — see docs/IMMUTABLE_PIPELINE.md for which artifacts currently
    carry this label and why.

`sourceStage` names the stage this artifact was derived from (e.g.
"normalized_slate"), when the caller can identify one — it is purely
informational (never read back to resolve a path or validate anything)
and defaults to None when there isn't a single clear parent stage.

The envelope exists so a reader that finds one of these files — even
without already knowing which stage/date it belongs to — can identify
what produced it, when, and under what format, without depending on the
file's path alone. `read_stage_artifact()` returns the full envelope;
callers that only want the payload read `result["data"]`.

`slateDate` (not `date`) deliberately matches the field name
`data/pipeline_status.json` already uses (see
`.github/workflows/fetch-slate.yml`'s stage-status step) — the same
concept should have the same name across the two artifact systems this
repository now has, rather than letting them drift as a "second
competing architecture." `createdAt` is intentionally NOT named
`completedAt` (pipeline_status.json's field) — it means something
different: the moment this one stage's artifact was written, not the
moment the whole pipeline run finished. See docs/IMMUTABLE_PIPELINE.md's
architecture-collision section for the full comparison against
lib/slate_manager.py and data/pipeline_status.json.

SAFETY PROPERTIES
------------------
- stage/date are validated against a strict filename-safe pattern before
  being used to build a path — neither can contain "/", "\\", "..", or
  any other path-traversal-relevant character. A stage or date derived
  from untrusted input cannot be used to write outside data/pipeline/.
- Writes are atomic: the payload is written to a temporary file in the
  same directory as the final path, flushed and fsync'd, then moved into
  place with os.replace() (atomic on POSIX and Windows). A process that
  crashes mid-write leaves only a stray .tmp file — readers of the real
  artifact path either see the previous valid version (rerun case) or no
  file at all (first-run case). They can never observe a truncated or
  partially-written JSON file at the artifact's real path.
- json.dump uses sort_keys=True so two calls with equivalent data produce
  byte-identical output, regardless of the caller's dict insertion order.
"""

import json
import os
import re
import tempfile
from datetime import datetime, timezone

PIPELINE_ROOT = os.path.join("data", "pipeline")
SCHEMA_VERSION = "1.0"

# Filename-safe: letters, digits, underscore, hyphen only. Deliberately
# excludes "/", "\\", ".", and anything else that could be interpreted as
# a path component (including "..") by os.path.join.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_VALID_STATUSES = frozenset({"canonical", "transitional"})


def _validate_component(value: str, label: str) -> str:
    if not value:
        raise ValueError(f"{label} is required to build an artifact path")
    if not _SAFE_NAME_RE.match(value):
        raise ValueError(
            f"{label} {value!r} is not filename-safe — only letters, digits, "
            f"'_' and '-' are allowed (this guards against path traversal)"
        )
    return value


def artifact_path(stage: str, date: str) -> str:
    """
    Return the path a stage's immutable artifact is written to. Raises
    ValueError if stage or date is missing or contains any character that
    could escape data/pipeline/ (e.g. "/", "..") — the shared
    letters/digits/underscore/hyphen pattern allows a plain YYYY-MM-DD
    date and a plain stage name, and nothing else, in particular no "."
    at all, which is what would be needed to spell "..".
    """
    stage = _validate_component(stage, "stage name")
    date = _validate_component(date, "date")
    return os.path.join(PIPELINE_ROOT, date, f"{stage}.json")


def write_stage_artifact(
    stage: str,
    date: str,
    data,
    produced_by: str = None,
    status: str = "canonical",
    source_stage: str = None,
) -> str:
    """
    Write `data` to this stage's immutable artifact path, wrapped in the
    metadata envelope described in the module docstring, and return the
    path written. Does not touch any other file — in particular, never
    touches data/slate.json or any other stage's artifact.

    The write is atomic (temp file + os.replace) and never mutates `data`
    itself — json.dump only reads it.

    Artifacts are write-once per (stage, date) in intent — a rerun that
    calls this again for the same stage/date overwrites the artifact with
    the new run's output, mirroring how data/slate.json itself is
    refreshed on a rerun. This does not currently implement the
    official/recheck versioning lib/slate_manager.py uses for the
    authoritative slate — that is a candidate for a future phase, not
    introduced here to keep this change additive and low-risk.

    `status` ("canonical" or "transitional") and `source_stage` are
    optional Phase 4 metadata — see the module docstring's ARTIFACT SHAPE
    section. Both default to values that preserve the artifact shape
    Phase 3 callers already depend on: `status` defaults to "canonical"
    (existing callers that never set it are asserting the payload IS the
    intended schema — callers writing an intentionally transitional
    payload, e.g. a full-slate snapshot, must pass status="transitional"
    explicitly), and `source_stage` defaults to None.

    Raises ValueError (before any filesystem write, same as an invalid
    stage/date) if `status` is anything other than "canonical" or
    "transitional" — the module docstring documents this as a closed
    two-value enum, so a typo (e.g. "cannonical") must fail loudly here
    rather than silently writing a status value no reader recognizes.
    """
    if status not in _VALID_STATUSES:
        raise ValueError(
            f"status {status!r} is not valid — must be one of "
            f"{sorted(_VALID_STATUSES)} (a typo here would otherwise "
            f"silently write metadata no reader recognizes)"
        )
    path = artifact_path(stage, date)
    envelope = {
        "meta": {
            "stage": stage,
            "slateDate": date,
            "createdAt": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "schemaVersion": SCHEMA_VERSION,
            "producedBy": produced_by or stage,
            "status": status,
            "sourceStage": source_stage,
        },
        "data": data,
    }

    dest_dir = os.path.dirname(path)
    os.makedirs(dest_dir, exist_ok=True)

    # Atomic write: serialize to a temp file in the same directory (so the
    # final os.replace is a same-filesystem rename, not a cross-device
    # copy), fsync it, then atomically move it into place. A crash or
    # exception at any point before os.replace leaves the real artifact
    # path untouched — either absent (first run) or still holding the
    # previous valid content (rerun) — never a partial write.
    fd, tmp_path = tempfile.mkstemp(prefix=f".{stage}.", suffix=".json.tmp", dir=dest_dir)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(envelope, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise

    return path


def read_stage_artifact(stage: str, date: str) -> dict:
    """
    Read a previously-written stage artifact and return the full envelope
    (both "meta" and "data"). Raises FileNotFoundError if the artifact is
    absent, or json.JSONDecodeError if the file exists but is not valid
    JSON (e.g. corrupted) — callers that need to distinguish these should
    catch them separately; both are real, distinct failure modes and
    neither is silently converted into the other.
    """
    path = artifact_path(stage, date)
    with open(path) as f:
        return json.load(f)


def stage_artifact_exists(stage: str, date: str) -> bool:
    return os.path.exists(artifact_path(stage, date))
