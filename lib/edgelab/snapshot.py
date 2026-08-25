"""
lib/edgelab/snapshot.py
==========================
Historical Capture Completeness and Immutable Snapshot Foundation
milestone: an immutable, hash-verifiable, write-once Snapshot -- a
manifest of everything the production model knew (or explicitly did
NOT know) at one point in time for one MLB slate date and one
production decision moment.

See docs/SNAPSHOT_ARCHITECTURE.md for the full design writeup,
including the maintainer-review revision history. This module
implements capture, integrity verification, and the smallest possible
read interface -- it does NOT implement replay, backtesting,
uncertainty estimation, or any research/handicapping computation. It
never reads or writes model probabilities, recommendation logic,
thresholds, staking, or settlement outcomes -- it only records frozen
copies of (or, for the few sources proven safe, hash-verified pointers
to) artifacts other modules already produce.

MUTABLE vs IMMUTABLE CLASSIFICATION RULE (revised after maintainer review)
----------------------------------------------------------------------
The original design classified several sources as REFERENCED_IMMUTABLE
(path + SHA-256 only) based on the belief that they were write-once.
A maintainer review audited every such source against its ACTUAL writer
code and found every one of them is, in fact, mutable in some real
scenario:

  - data/pipeline/<date>/*.json: lib.pipeline_artifacts.write_stage_artifact()'s
    own docstring admits "a rerun that calls this again for the same
    stage/date overwrites the artifact" -- a same-day re-dispatch of
    fetch-slate.yml genuinely does this.
  - data/kalshi_registry_snapshots/kalshi_search_<date>.json (the
    "dated, kept forever" file): fetch-slate.yml's archive step does an
    unconditional `cp`, so a second run for the same date overwrites it.
  - data/slates/<date>/authoritative.json: scripts/protect_slate.py's
    own docstring says a LINEUP_RECHECK run legitimately UPDATES
    authoritative.json for new games -- not write-once either.
  - data/edgelab/<entity>/<date>.jsonl(.gz): lib.edgelab.storage's
    append_records()/upsert_records() both REWRITE THE WHOLE FILE on
    every call (read-existing, merge, atomic-write-all) -- even the
    "append-only" entities' FULL FILE BYTES change whenever a later
    capture adds rows for the same date, and upsertable entities
    (model_evaluations, recommendations, settlements) can have existing
    rows literally replaced in place.

Conclusion: this repository currently has NO source that is safely
REFERENCED_IMMUTABLE by construction. Every component with a real
source path is therefore FROZEN_COPY. The REFERENCED_IMMUTABLE
mechanism (build_referenced_component) is kept as a primitive -- future
genuinely write-once-and-never-touched-again storage should use it --
but no current PRE_GAME_DECISION/POST_GAME_SETTLEMENT/CLOSING_LINE
component uses it. Every FROZEN_COPY component's hash is still
re-checked at verify_snapshot() time against its OWN frozen bytes
(proving the snapshot itself wasn't tampered with after the fact), even
though it is no longer checked against a (mutable) live source path.

SNAPSHOT IDENTITY / GRANULARITY (revised after maintainer review)
----------------------------------------------------------------------
PRE_GAME_DECISION snapshots are keyed by (snapshotStage, snapshotDate,
productionRunKey) -- NOT just (stage, date). productionRunKey is
derived from data/pipeline/<date>/recommendations.json's own
meta.createdAt, which genuinely changes every time
scripts/build_market_ledger.py actually reruns for that date (lineup
recheck, doubleheader, retry after partial failure, manual
re-dispatch). This means a second, legitimately different production
run for the same date gets its OWN snapshot slot instead of either (a)
silently overwriting the earlier valid decision snapshot, or (b) being
misclassified as an anomalous "conflict" against it. A genuine conflict
(same productionRunKey, different bytes -- which should only happen if
capture itself is non-deterministic) is still caught and quarantined as
before. POST_GAME_SETTLEMENT/CLOSING_LINE remain date-keyed (not
run-keyed): they represent "the current best-known settled truth for
this date", not a decision moment, and are explicitly out of scope for
the look-ahead-bias concern this distinction exists to address.

Postgame data (SETTLEMENT, CLV) is never a component of a
PRE_GAME_DECISION manifest -- see STAGE_COMPONENT_TYPES below; it is
always emitted as an explicit NOT_APPLICABLE_FOR_STAGE entry rather
than silently absent, so a reader can see the exclusion was deliberate.
"""

import hashlib
import gzip
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

from lib.edgelab import ids
from lib.pipeline_artifacts import stage_artifact_exists, read_stage_artifact, artifact_path as pipeline_artifact_path
from lib.rules_config import load_rules_config, RULES_PATH as RULES_CONFIG_PATH

SNAPSHOTS_ROOT = os.path.join("data", "edgelab", "snapshots")
SCHEMA_VERSION = "1"

# ── Controlled vocabularies (mirrors data/edgelab/schema_v1/snapshot_*.schema.json) ──

STAGE_PRE_GAME_DECISION = "PRE_GAME_DECISION"
STAGE_POST_GAME_SETTLEMENT = "POST_GAME_SETTLEMENT"
STAGE_CLOSING_LINE = "CLOSING_LINE"
VALID_STAGES = frozenset({STAGE_PRE_GAME_DECISION, STAGE_POST_GAME_SETTLEMENT, STAGE_CLOSING_LINE})

STORAGE_REFERENCED_IMMUTABLE = "REFERENCED_IMMUTABLE"
STORAGE_FROZEN_COPY = "FROZEN_COPY"

REQUIRED = "REQUIRED"
NICE_TO_HAVE = "NICE_TO_HAVE"

AVAILABLE = "AVAILABLE"
PARTIAL = "PARTIAL"
MISSING = "MISSING"
NOT_APPLICABLE_FOR_STAGE = "NOT_APPLICABLE_FOR_STAGE"

REASON_NEVER_CAPTURED_HISTORICALLY = "NEVER_CAPTURED_HISTORICALLY"
REASON_OVERWRITTEN_SOURCE_NOT_YET_FROZEN = "OVERWRITTEN_SOURCE_NOT_YET_FROZEN"
REASON_INGESTION_GAP = "INGESTION_GAP"
REASON_NOT_KNOWN_AT_DECISION_TIME = "NOT_KNOWN_AT_DECISION_TIME"
REASON_POSTGAME_EXCLUDED = "POSTGAME_DATA_EXCLUDED_FROM_PREGAME_SNAPSHOT"
REASON_SOURCE_QUARANTINED = "SOURCE_ARTIFACT_QUARANTINED"
REASON_PARTIAL_FIELD_POPULATION = "PARTIAL_FIELD_POPULATION"
REASON_NOT_APPLICABLE_FOR_STAGE = "NOT_APPLICABLE_FOR_STAGE"
REASON_PRODUCTION_COMMIT_AMBIGUOUS = "PRODUCTION_COMMIT_AMBIGUOUS"

COMPLETE_FOR_PRODUCTION_REPLAY = "COMPLETE_FOR_PRODUCTION_REPLAY"
PARTIAL_REPLAY = "PARTIAL_REPLAY"
APPROXIMATE_ONLY = "APPROXIMATE_ONLY"
INTEGRITY_FAILURE = "INTEGRITY_FAILURE"
MISSING_REQUIRED_INPUT = "MISSING_REQUIRED_INPUT"

LEVEL_1_APPROXIMATE = "LEVEL_1_APPROXIMATE"
LEVEL_2_PRODUCTION_EQUIVALENT = "LEVEL_2_PRODUCTION_EQUIVALENT"
# Renamed from LEVEL_3_BIT_FOR_BIT under the PR #37 maintainer review (item
# 2): what this mechanism actually verifies is that the candidate replay
# ran against the IDENTICAL, clean (non-dirty) commit as production --
# i.e. the executing code is pinned to production's code, byte-for-byte.
# It does NOT verify interpreter version, dependency versions, OS/runtime
# image, locale/timezone, other production env vars, deterministic random
# seeds, source data ordering, wall-clock inputs, or external API payload
# identity -- none of which this repository captures or pins today (see
# docs/PRODUCTION_PROVENANCE.md's "Level-3 fidelity requirement table").
# "Bit-for-bit reproducible output" is a strictly stronger claim than
# "identical code" and was not honestly earned by the old name.
LEVEL_3_CODE_PINNED = "LEVEL_3_CODE_PINNED"

CAPTURE_MODE_LIVE = "LIVE_CAPTURE"
CAPTURE_MODE_BACKFILL = "HISTORICAL_BACKFILL"
VALID_CAPTURE_MODES = frozenset({CAPTURE_MODE_LIVE, CAPTURE_MODE_BACKFILL})

# componentTypes that only ever apply within a given stage -- everything
# else is emitted as an explicit NOT_APPLICABLE_FOR_STAGE row for that
# stage rather than omitted (see module docstring).
STAGE_COMPONENT_TYPES = {
    STAGE_PRE_GAME_DECISION: [
        "PRODUCTION_SLATE_INPUT", "NORMALIZED_SLATE", "RAW_PROJECTIONS",
        "RECOMMENDATION_OUTPUT", "MARKET_UNIVERSE", "EXECUTABLE_PRICES",
        "BID_ASK", "LINEUP_STATE", "BULLPEN_STATE", "WEATHER", "PARK_FACTORS",
        "EFFECTIVE_CONFIG", "MODEL_EVALUATIONS", "RECOMMENDATIONS",
        "MARKET_OBSERVATIONS", "RISK_GATE_OUTPUT", "EXECUTION_SLIP",
        "VALIDATION_ARTIFACT", "PROTECTION_ARTIFACT", "PRODUCTION_PROVENANCE",
    ],
    STAGE_POST_GAME_SETTLEMENT: ["SETTLEMENT", "CLV"],
    STAGE_CLOSING_LINE: ["MARKET_OBSERVATIONS"],
}

# Within each stage's own component types, which are REQUIRED (a MISSING
# required component forces MISSING_REQUIRED_INPUT) vs NICE_TO_HAVE (a
# MISSING nice-to-have caps completeness at APPROXIMATE_ONLY, never blocks
# it entirely).
REQUIRED_COMPONENT_TYPES = {
    STAGE_PRE_GAME_DECISION: frozenset({
        "PRODUCTION_SLATE_INPUT", "RAW_PROJECTIONS", "RECOMMENDATION_OUTPUT",
        "MARKET_UNIVERSE", "EFFECTIVE_CONFIG", "RISK_GATE_OUTPUT", "PRODUCTION_PROVENANCE",
    }),
    STAGE_POST_GAME_SETTLEMENT: frozenset({"SETTLEMENT"}),
    STAGE_CLOSING_LINE: frozenset({"MARKET_OBSERVATIONS"}),
}

ALL_COMPONENT_TYPES = frozenset(
    t for types in STAGE_COMPONENT_TYPES.values() for t in types
) | frozenset({"SETTLEMENT", "CLV"})

# How long a pipeline-stage artifact's own meta.createdAt may diverge from
# the productionRunKey reference timestamp before this is flagged as
# possible workflow-run skew (components drawn from meaningfully
# different production runs mixed into one manifest). Generous enough for
# one real job's wall-clock duration; tight enough to catch a genuinely
# stale artifact left over from an earlier day.
#
# Tightened from 6.0 to 1.0 under the PR #37 maintainer review (item 4,
# "provenance-to-artifact binding"): fetch-slate.yml's own steps (data
# fetch + validation + build_market_ledger.py + risk_gate.py chain)
# complete in well under an hour in every observed real run, so 1.0 hour
# still leaves ample margin against a false positive on a single
# legitimately-slow job, while being tight enough to actually catch the
# concrete scenario this item flags: an hours-later rerun (e.g. the
# documented "starters not yet posted -- re-run after ~3pm ET" case in
# fetch-slate.yml) that leaves some earlier stage artifacts (e.g. a stale
# projections.json) in place while others (recommendations.json) get
# freshly overwritten. The OLD 6.0-hour value would NOT have caught that
# same scenario (see test_temporal_skew_catches_hours_later_partial_rerun_mixing
# in tests/edgelab/test_production_provenance.py) -- a real, previously
# undetected gap.
#
# KNOWN RESIDUAL LIMITATION, documented rather than silently left: two
# separate production runs less than MAX_RUN_SKEW_HOURS apart on the same
# date (e.g. a rapid manual retry a few minutes after an initial run)
# could still mix components across runs undetected by this timestamp-
# proximity check alone. Closing that fully would require plumbing an
# explicit shared run identifier (e.g. GITHUB_RUN_ID) through every
# pipeline-artifact-writing script in the production chain
# (build_market_ledger.py, risk_gate.py, enrich_data.py, protect_slate.py,
# validate_slate_final.py) and checking exact identity rather than time
# proximity -- a materially larger change than this review's scope,
# deliberately not attempted here rather than implemented partially and
# claimed complete.
MAX_RUN_SKEW_HOURS = 1.0


class SnapshotIntegrityError(Exception):
    """Raised when a frozen copy's hash doesn't match its source at freeze time."""


# ── Hashing / canonical serialization ────────────────────────────────────

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json_bytes(obj) -> bytes:
    """Deterministic serialization: sorted keys, no incidental whitespace variance."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _count_lines(path: str):
    """Row count for JSONL/JSONL.gz files (one record per non-empty line).
    Returns None for anything else -- a non-tabular JSON file has no
    meaningful 'row count', and None is the correct value, not a bug."""
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rt", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except (OSError, UnicodeDecodeError):
        return None


# Fields deliberately excluded from the manifest-hash computation: these
# are "when/how this was captured" facts that legitimately differ between
# two otherwise-identical captures of the same underlying data (e.g. a
# manual rerun minutes later has a later capturedAt but must still be
# recognized as a no-op if every component's content is unchanged). Only
# the fields that describe WHAT was captured participate in the hash.
_MANIFEST_HASH_EXCLUDED_FIELDS = frozenset({
    "manifestHash", "capturedAt", "workflowRunId",
    "snapshotWriterCommitSha", "provenance",
})


def compute_manifest_hash(manifest: dict) -> str:
    """SHA-256 over the manifest's canonical, content-only form (see
    _MANIFEST_HASH_EXCLUDED_FIELDS) -- proves the CAPTURED DATA hasn't
    changed, not that the wrapper metadata is byte-identical."""
    candidate = {k: v for k, v in manifest.items() if k not in _MANIFEST_HASH_EXCLUDED_FIELDS}
    return sha256_bytes(canonical_json_bytes(candidate))


# ── Run-key derivation (item 4 -- snapshot granularity) ──────────────────

def _production_run_key(date):
    """
    The one piece of REAL evidence this repo has for 'which production
    run produced this'. Reads data/pipeline/<date>/recommendations.json's
    own meta.createdAt (set by scripts/build_market_ledger.py at the
    moment IT wrote that artifact) -- distinct on every genuine rerun for
    the same date (lineup recheck, doubleheader, retry). Returns None if
    the artifact doesn't exist yet or can't be read; callers fall back to
    a single "unkeyed" slot for that date in that case.
    """
    if not stage_artifact_exists("recommendations", date):
        return None
    try:
        env = read_stage_artifact("recommendations", date)
        return env.get("meta", {}).get("createdAt")
    except (OSError, json.JSONDecodeError):
        return None


def current_production_run_key(date):
    """Public wrapper around _production_run_key() -- the run key the NEXT
    build_snapshot(PRE_GAME_DECISION, date) call would use right now, based
    on whatever data/pipeline/<date>/recommendations.json currently holds.
    For external callers (e.g. scripts/check_snapshot_capture.py) that need
    to check "does a snapshot exist for the CURRENT run" without reaching
    into this module's own derivation internals."""
    return _production_run_key(date)


def _run_key_slug(run_key):
    if not run_key:
        return "unkeyed"
    slug = re.sub(r"[^A-Za-z0-9_-]", "", run_key)
    return slug or "unkeyed"


# ── Paths ─────────────────────────────────────────────────────────────────

def snapshot_dir(stage: str, date: str, run_key=None) -> str:
    if stage == STAGE_PRE_GAME_DECISION:
        return os.path.join(SNAPSHOTS_ROOT, date, stage.lower(), _run_key_slug(run_key))
    return os.path.join(SNAPSHOTS_ROOT, date, stage.lower())


def manifest_path(stage: str, date: str, run_key=None) -> str:
    return os.path.join(snapshot_dir(stage, date, run_key), "manifest.json")


def frozen_dir(stage: str, date: str, run_key=None) -> str:
    return os.path.join(snapshot_dir(stage, date, run_key), "frozen")


def list_pregame_run_dirs(date: str):
    """All existing PRE_GAME_DECISION run-key slugs for `date`, oldest
    first -- ordered by each manifest's own `capturedAt` (the true
    "when was this written" fact), NOT by directory-name string.

    Corpus-health audit finding (2026-08-25, the 2026-08-19
    FORWARD_PROVENANCE_AMBIGUOUS regression): directory names are usually
    ISO-timestamp-derived run keys, which do sort chronologically as
    strings -- EXCEPT for the "unkeyed" sentinel (_run_key_slug's fallback
    when _production_run_key() finds no recommendations.json yet, e.g. a
    snapshot-capture step that runs -- via fetch-slate.yml's `if: always()`
    -- before that same day's real production run has written
    recommendations.json). "unkeyed" sorts AFTER every real ISO-8601
    directory name (e.g. "2026-08-19T214316Z" < "unkeyed" as plain
    strings), so a garbage early capture with no real data could
    previously be mistaken for the LATEST run even though it was
    chronologically FIRST, hiding the real, later, healthy capture from
    every caller of load_latest_pregame_manifest(). Sorting by the
    manifest's own capturedAt fixes this regardless of what the run-key
    slug looks like, without deleting or rewriting either run's evidence."""
    base = os.path.join(SNAPSHOTS_ROOT, date, STAGE_PRE_GAME_DECISION.lower())
    if not os.path.isdir(base):
        return []
    candidates = [d for d in os.listdir(base) if os.path.isfile(os.path.join(base, d, "manifest.json"))]

    def _sort_key(run_dir):
        manifest_file = os.path.join(base, run_dir, "manifest.json")
        try:
            with open(manifest_file) as f:
                captured_at = json.load(f).get("capturedAt") or ""
        except (OSError, json.JSONDecodeError):
            captured_at = ""
        # capturedAt alone has only whole-second resolution (see
        # ids.utc_now_iso()) -- two runs captured within the same second
        # (e.g. a fast automated retry, or this repo's own test suite)
        # would otherwise tie and fall back to comparing run_dir strings,
        # silently reintroducing the exact "unkeyed sorts last" bug this
        # sort exists to fix. The manifest file's own write-time mtime
        # (sub-second resolution, monotonic for this repo's atomic
        # write-then-rename commit sequence) breaks that tie correctly.
        try:
            mtime = os.path.getmtime(manifest_file)
        except OSError:
            mtime = 0.0
        return (captured_at, mtime)

    return sorted(candidates, key=_sort_key)


_COMPLETENESS_RANK = {
    COMPLETE_FOR_PRODUCTION_REPLAY: 3,
    PARTIAL_REPLAY: 2,
    APPROXIMATE_ONLY: 1,
    MISSING_REQUIRED_INPUT: 0,
}


def select_canonical_pregame_manifest(date: str):
    """
    The single PRE_GAME_DECISION manifest corpus-health reporting and
    forward replay should treat as "the date's decision", when more than
    one production run exists for the same date. Prefers the run with
    the BEST completenessStatus and no temporal skew, tie-broken by
    capture order (the chronologically LATEST run wins within the same
    quality tier) -- deliberately NOT simply "whichever run happened
    most recently" as the PRIMARY criterion.

    Corpus-health audit finding (2026-08-21..23): fetch-slate.yml's
    schedule-triggered runs never execute the risk-gate/execution chain
    (see fetch-slate.yml's own "BLOCK 7" `if: github.event_name !=
    'schedule'` gating -- a deliberate, reviewed safety boundary that
    keeps automated real-money bet placement workflow_dispatch/push-only,
    and which this fix does not touch). A later same-day SCHEDULE-only
    refresh can therefore be a genuinely WORSE capture than an earlier
    same-day capture that did have a full risk-gated decision -- either
    because RISK_GATE_OUTPUT is missing outright (MISSING_REQUIRED_INPUT)
    or because the schedule refresh leaves an earlier run's now-stale
    RISK_GATE_OUTPUT/execution.json in place, which detect_temporal_skew()
    correctly flags (INELIGIBLE_TEMPORAL_SKEW). Picking "latest by
    wall-clock" in that situation silently throws away a real, good,
    already-captured decision in favor of a strictly worse one. Falls
    back to the literal latest run when every run for the date is
    equally (materially) incomplete -- this never fabricates completeness
    that doesn't exist for ANY of that date's real runs.
    """
    run_dirs = list_pregame_run_dirs(date)  # already chronological -- see that function's own capturedAt+mtime sort
    if not run_dirs:
        return None
    base = os.path.join(SNAPSHOTS_ROOT, date, STAGE_PRE_GAME_DECISION.lower())
    manifests = []
    for run_dir in run_dirs:
        try:
            with open(os.path.join(base, run_dir, "manifest.json")) as f:
                manifests.append(json.load(f))
        except (OSError, json.JSONDecodeError):
            continue
    if not manifests:
        return None

    def _quality_key(indexed):
        index, m = indexed
        rank = _COMPLETENESS_RANK.get(m.get("completenessStatus"), -1)
        skewed = bool((m.get("temporalConsistency") or {}).get("skewDetected"))
        # `index` (position within the already time-ordered `manifests`
        # list, per list_pregame_run_dirs) is the final tiebreaker, NOT
        # capturedAt directly: capturedAt has only whole-second resolution
        # (ids.utc_now_iso()), so two runs captured within the same real
        # second (a fast automated retry, or this repo's own test suite)
        # would otherwise tie there too, and max()'s first-occurrence-wins
        # behavior on an exact tie would silently pick the WRONG (earlier)
        # run back out again -- exactly the bug this function exists to
        # fix. Reusing list_pregame_run_dirs' own already-correct
        # chronological order (capturedAt, then manifest-file mtime) sidesteps
        # that: whichever run is later in `manifests` is unambiguously later.
        return (rank, 0 if not skewed else -1, index)

    _, best = max(enumerate(manifests), key=_quality_key)
    return best


# ── Atomic filesystem primitives ─────────────────────────────────────────

def _atomic_write_bytes(dest_path: str, data: bytes):
    dest_dir = os.path.dirname(dest_path) or "."
    os.makedirs(dest_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".snapshot.", suffix=".tmp", dir=dest_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, dest_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _atomic_copy(src_path: str, dest_path: str):
    dest_dir = os.path.dirname(dest_path) or "."
    os.makedirs(dest_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".snapshot.", suffix=".tmp", dir=dest_dir)
    try:
        with os.fdopen(fd, "wb") as out_f, open(src_path, "rb") as in_f:
            shutil.copyfileobj(in_f, out_f)
            out_f.flush()
            os.fsync(out_f.fileno())
        os.replace(tmp_path, dest_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _atomic_gzip_copy(src_path: str, dest_path: str):
    """
    Freezes src_path's content as a deterministic gzip file (mtime=0, same
    convention as lib.edgelab.storage's gzip writer -- byte-identical
    logical content must produce byte-identical compressed output, or a
    rerun against unchanged input would look like a change). Storage
    review finding (item 9): once every mutable pipeline artifact is
    frozen rather than referenced, the biggest JSON/JSONL sources
    (recommendations.json, clv_quotes.jsonl, etc.) dominate snapshot
    storage growth -- compressing them here is the direct mitigation.
    """
    dest_dir = os.path.dirname(dest_path) or "."
    os.makedirs(dest_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".snapshot.", suffix=".tmp", dir=dest_dir)
    try:
        with open(src_path, "rb") as in_f:
            data = in_f.read()
        with os.fdopen(fd, "wb") as raw_f:
            with gzip.GzipFile(fileobj=raw_f, mode="wb", mtime=0, compresslevel=9) as gz_f:
                gz_f.write(data)
            raw_f.flush()
            os.fsync(raw_f.fileno())
        os.replace(tmp_path, dest_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    return sha256_bytes(data)


def _git_commit_sha():
    """Same convention as lib.edgelab.model_evaluation._git_commit_sha() -- the commit of
    THIS checkout at the moment this function runs, never a fabricated placeholder."""
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def _parse_iso(ts):
    if not ts:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ── Component builders ───────────────────────────────────────────────────

def _hash_and_size(path):
    if not path or not os.path.exists(path) or not os.path.isfile(path):
        return None, None
    return sha256_file(path), os.path.getsize(path)


def _missing_component(component_type, required_status, source_path, reason, availability=MISSING):
    return {
        "componentType": component_type, "sourcePath": source_path, "snapshotPath": None,
        "storageMode": None, "contentHash": None, "byteSize": None, "rowCount": None,
        "capturedAt": None, "producer": None, "requiredStatus": required_status,
        "availabilityStatus": availability, "limitationReason": reason,
    }


def not_applicable_component(component_type, required_status=NICE_TO_HAVE, reason=REASON_NOT_APPLICABLE_FOR_STAGE):
    return _missing_component(component_type, required_status, None, reason, availability=NOT_APPLICABLE_FOR_STAGE)


def build_referenced_component(component_type, source_path, required_status, producer=None,
                                captured_at=None, row_count=None, missing_reason=REASON_INGESTION_GAP):
    """
    REFERENCED_IMMUTABLE: hash the source file in place, do not copy it.
    NOT USED by any current stage builder below (see module docstring --
    no source in this repository is currently safe to reference rather
    than freeze). Kept as a primitive for a future genuinely write-once
    source, and directly exercised by tests/edgelab/test_snapshot.py to
    prove the mechanism itself (hash-in-place, integrity-violation
    detection, pruned-source detection) still works correctly.
    """
    if source_path and os.path.exists(source_path):
        content_hash, byte_size = _hash_and_size(source_path)
        return {
            "componentType": component_type, "sourcePath": source_path, "snapshotPath": None,
            "storageMode": STORAGE_REFERENCED_IMMUTABLE, "contentHash": content_hash, "byteSize": byte_size,
            "rowCount": row_count, "capturedAt": captured_at, "producer": producer,
            "requiredStatus": required_status, "availabilityStatus": AVAILABLE, "limitationReason": None,
        }
    return _missing_component(component_type, required_status, source_path, missing_reason)


def freeze_file_component(component_type, source_path, staging_frozen_dir, final_frozen_dir, required_status,
                           dest_filename=None, producer=None, captured_at=None, count_rows=False, compress=False,
                           missing_reason=REASON_OVERWRITTEN_SOURCE_NOT_YET_FROZEN):
    """
    FROZEN_COPY: copy source_path's exact bytes into staging_frozen_dir now
    (so a conflicting concurrent capture never touches the real frozen/
    directory before the write-once check runs -- see _commit_snapshot).
    The recorded snapshotPath is the FINAL path the bytes will occupy once
    committed, not the staging path. count_rows=True computes a JSONL(.gz)
    line count on the FROZEN copy's DECODED content (item 5: rowCount must
    reflect what replay will actually consume, not the possibly-since-
    changed source). compress=True gzips the frozen copy (mtime=0,
    deterministic -- see _atomic_gzip_copy); contentHash is always the
    hash of the bytes actually AT snapshotPath (compressed or not), since
    that is what verify_snapshot() re-checks -- a separate immediate
    decompress-and-compare against source_hash below still proves
    compression didn't corrupt anything at freeze time.
    """
    if not source_path or not os.path.exists(source_path):
        return _missing_component(component_type, required_status, source_path, missing_reason)
    source_hash, source_size = _hash_and_size(source_path)
    dest_filename = dest_filename or os.path.basename(source_path)
    if compress and not dest_filename.endswith(".gz"):
        dest_filename = dest_filename + ".gz"
    staged_path = os.path.join(staging_frozen_dir, dest_filename)
    if compress:
        decompressed_hash = _atomic_gzip_copy(source_path, staged_path)
        if decompressed_hash != source_hash:
            raise SnapshotIntegrityError(
                f"gzip-frozen copy of {source_path!r} decodes to hash {decompressed_hash}, "
                f"does not match source hash {source_hash} immediately after copy"
            )
    else:
        _atomic_copy(source_path, staged_path)
    frozen_hash, frozen_size = _hash_and_size(staged_path)
    if not compress and frozen_hash != source_hash:
        raise SnapshotIntegrityError(
            f"frozen copy of {source_path!r} (hash {frozen_hash}) does not match "
            f"source hash {source_hash} immediately after copy"
        )
    row_count = _count_lines(staged_path) if count_rows else None
    return {
        "componentType": component_type, "sourcePath": source_path,
        "snapshotPath": os.path.join(final_frozen_dir, dest_filename),
        "storageMode": STORAGE_FROZEN_COPY, "contentHash": frozen_hash, "byteSize": frozen_size,
        "rowCount": row_count, "capturedAt": captured_at, "producer": producer,
        "requiredStatus": required_status, "availabilityStatus": AVAILABLE, "limitationReason": None,
    }


def freeze_record_component(component_type, record: dict, staging_frozen_dir, final_frozen_dir, dest_filename,
                             required_status, producer=None, captured_at=None,
                             availability_status=AVAILABLE, limitation_reason=None):
    """FROZEN_COPY for a synthesized in-memory record (e.g. effective config) rather than a raw file copy."""
    data_bytes = canonical_json_bytes(record)
    staged_path = os.path.join(staging_frozen_dir, dest_filename)
    _atomic_write_bytes(staged_path, data_bytes)
    return {
        "componentType": component_type, "sourcePath": None,
        "snapshotPath": os.path.join(final_frozen_dir, dest_filename),
        "storageMode": STORAGE_FROZEN_COPY, "contentHash": sha256_bytes(data_bytes), "byteSize": len(data_bytes),
        "rowCount": None, "capturedAt": captured_at, "producer": producer,
        "requiredStatus": required_status, "availabilityStatus": availability_status,
        "limitationReason": limitation_reason,
    }


def _rederive_component(base_component, new_type, required_status=NICE_TO_HAVE, note_reason=None):
    """A second componentType that happens to be embedded in an already-captured component
    (e.g. PARK_FACTORS living inside EFFECTIVE_CONFIG's frozen record, or EXECUTABLE_PRICES/
    BID_ASK living inside the same frozen MARKET_OBSERVATIONS file) -- denormalized
    pointer, never a second freeze/hash of the same bytes."""
    derived = dict(base_component)
    derived["componentType"] = new_type
    derived["requiredStatus"] = required_status
    if note_reason and derived["availabilityStatus"] != AVAILABLE:
        derived["limitationReason"] = note_reason
    return derived


# ── Effective production configuration (item 6/7) ────────────────────────

# Forward Replay Corpus and Production Provenance milestone (item 3):
# every live-importable module-level constant this milestone could find by
# direct source inspection of scripts/build_market_ledger.py and
# scripts/risk_gate.py that actually gates a recommendation/tier/staking/
# market-eligibility decision. Introspected via getattr on the real,
# live-imported module -- never copy-pasted as a literal here, so a future
# code change to any of these constants is automatically reflected the
# next time this runs, with no maintenance burden and no risk of drift
# between "what capture_effective_config claims" and "what actually
# executes". Deliberately NOT a refactor of these constants (they stay
# exactly where they live in production code) -- this only reads them.
_BUILD_MARKET_LEDGER_CONSTANTS = (
    "THRESHOLD_HIGH", "THRESHOLD_MEDIUM", "THRESHOLD_PAPER",
    "CAL_HIGH", "CAL_MEDIUM", "CAL_PAPER",
    "REQUIRED_MARKETS", "F5_PRICING_VERSION_CURRENT",
    # Added under the PR #37 maintainer review (item 3, effective-config
    # completeness audit): MARKET_MULTIPLIERS is the per-market-family
    # staking multiplier table `bet_size()` reads directly -- a real,
    # named, decision-driving constant this milestone previously left
    # uncaptured (only its source-file hash proved code identity, not
    # the effective values).
    "MARKET_MULTIPLIERS",
)
_RISK_GATE_CONSTANTS = (
    "REAL_MONEY_TIERS", "TT_MARKETS", "ML_F5_MARKETS",
    "TT_MIN_EDGE_PCT", "TT_MAX_BETS", "TT_MAX_STAKE", "TT_MAX_STAKE_PCT",
    "ML_F5_MIN_STAKE_PCT", "DAILY_RISK_CAP",
    # Added under the PR #37 maintainer review (item 3): TT_CRITICAL_FIELDS/
    # TT_CRITICAL_SIDE_FIELDS were previously listed in _UNREPRESENTED_LOGIC
    # as "inline conditionals, not named constants" -- on closer inspection
    # they ARE real, directly introspectable module-level constants (a list
    # and a dict). Only the DECISION LOGIC that consumes them
    # (evaluate_candidate_tt_risk's PAPER-downgrade rule) remains genuinely
    # inline and unrepresented -- see the narrowed _UNREPRESENTED_LOGIC
    # entry below.
    "TT_CRITICAL_FIELDS", "TT_CRITICAL_SIDE_FIELDS",
)
# Every source file known to contain hardcoded thresholds/gates/staking
# logic this milestone cannot yet structurally represent as named
# constants (e.g. inline gate conditions, lineup-confirmation ratios).
# Hashing the file is the mechanical, no-refactor answer to item 3's
# "source-file hashes for any remaining hardcoded logic" requirement --
# it can't say WHAT changed, but it can prove WHETHER the file that
# contains it changed between two captures, which a future replay-vs-
# original mismatch investigation can use exactly the way
# classify_mismatch_reason() already uses f5PricingVersion.
_HARDCODED_LOGIC_SOURCE_FILES = (
    os.path.join("scripts", "build_market_ledger.py"),
    os.path.join("scripts", "risk_gate.py"),
    os.path.join("lib", "postponed_guard.py"),
)


def _introspect_live_constants():
    """Best-effort: a constant this milestone knows about but that a
    future code change renames/removes is simply absent from the result
    (never a fabricated placeholder) -- see per-module try/except below."""
    values = {}
    try:
        import scripts.build_market_ledger as bml
        for name in _BUILD_MARKET_LEDGER_CONSTANTS:
            if hasattr(bml, name):
                v = getattr(bml, name)
                values[name] = sorted(v) if isinstance(v, (set, frozenset)) else v
    except ImportError:
        pass
    try:
        import scripts.risk_gate as rg
        for name in _RISK_GATE_CONSTANTS:
            if hasattr(rg, name):
                v = getattr(rg, name)
                values[name] = sorted(v) if isinstance(v, (set, frozenset)) else v
    except ImportError:
        pass
    return values


def _hardcoded_logic_source_hashes():
    hashes = {}
    for path in _HARDCODED_LOGIC_SOURCE_FILES:
        hashes[path] = sha256_file(path) if os.path.exists(path) else None
    return hashes


# Known gaps in what _introspect_live_constants()/_hardcoded_logic_source_hashes()
# can honestly represent -- classified explicitly rather than silently
# absent, per item 3's "classify any values that cannot yet be represented
# structurally". Each entry names the concrete logic and where it lives;
# this list is reviewed/extended, never silently allowed to go stale, each
# time a new hardcoded gate is discovered (same discipline as
# lib.edgelab.snapshot's own component-completeness rule table).
_UNREPRESENTED_LOGIC = [
    {"description": "Lineup-confirmation gate threshold (battersFound/battersExpected ratio for lineupConfirmed)",
     "location": "scripts/fetch_lineups.py, scripts/enrich_lineup_confirmed.py"},
    {"description": "Rule 51/52/71 gate conditions (ML lineup gate, YRFI lineup gate, Pinnacle-gap check) -- inline conditionals, not named constants",
     "location": "scripts/build_market_ledger.py"},
    {"description": "TT PAPER-downgrade decision logic (the field lists themselves -- TT_CRITICAL_FIELDS, TT_CRITICAL_SIDE_FIELDS -- are captured in liveConstants as of the PR #37 review; only the branching logic that consumes them remains inline)",
     "location": "scripts/risk_gate.py (evaluate_candidate_tt_risk)"},
    {"description": "Postponed/live-game status classification (which raw status strings count as postponed/in-play/final)",
     "location": "lib/postponed_guard.py"},
    {"description": "Base stake size by confidence tier (HIGH/MEDIUM/PAPER -> units), inline dict literal inside bet_size(), not a module-level constant",
     "location": "scripts/build_market_ledger.py (bet_size)"},
]


def capture_effective_config(date: str, commit_sha, production_commit_sha=None, production_run_id=None):
    """
    The narrowest truthful mechanism for "what did production actually use":
    config/rules.json's own contents+version (real, but per lib/rules_config.py's
    own docstring NOT claimed to be the complete production rule set), PLUS
    every hardcoded module-level constant this milestone can honestly
    introspect from the live scripts.build_market_ledger/scripts.risk_gate
    modules (_introspect_live_constants), PLUS a source-file hash for the
    files known to contain remaining hardcoded logic that isn't yet a
    named constant (_hardcoded_logic_source_hashes), PLUS whatever
    rulesVersion literal that date's own execution.json artifact already
    recorded. Nothing here is fabricated: every field is either read from
    a real file or read from a real, live code object.

    Maintainer review finding (item 6, still true after the Forward Replay
    Corpus milestone's expansion): this record remains PARTIAL, always --
    not a complete effective-configuration extractor. `unrepresentedLogic`
    honestly lists the concrete gates/conditionals this milestone still
    cannot structurally represent (see _UNREPRESENTED_LOGIC) -- the source-
    file hash at least proves whether the FILE containing them changed
    between two captures, even though it can't say what changed. The
    caller marks this component's availabilityStatus as PARTIAL (never
    AVAILABLE) for exactly this reason -- see build_pre_game_manifest.
    """
    # Deliberately no wall-clock "capturedAt" field inside this record's
    # own content: it would make the frozen copy's bytes (and therefore
    # its contentHash) differ on every rerun even when the underlying
    # config/constants are byte-identical, which would make a genuine
    # no-op rerun look like a false conflict (see manifest capturedAt's
    # own exclusion from compute_manifest_hash for the same reason).
    record = {
        "rulesConfigPath": RULES_CONFIG_PATH,
        "rulesConfigContents": None,
        "rulesConfigVersion": None,
        "f5PricingVersionCurrent": None,
        "executionArtifactRulesVersion": None,
        "snapshotWriterCommitSha": commit_sha,
        "productionCommitSha": production_commit_sha,
        "productionRunId": production_run_id,
        "liveConstants": _introspect_live_constants(),
        "hardcodedLogicSourceHashes": _hardcoded_logic_source_hashes(),
        "unrepresentedLogic": _UNREPRESENTED_LOGIC,
        "note": (
            "PARTIAL record, not a complete effective-configuration extractor. "
            "rulesConfigContents is NOT the complete production rule set -- "
            "see lib/rules_config.py's own docstring. liveConstants covers "
            "every hardcoded threshold/gate/staking value this milestone could "
            "introspect from the live scripts.build_market_ledger/scripts.risk_gate "
            "modules; hardcodedLogicSourceHashes and unrepresentedLogic honestly "
            "name what remains uncaptured (inline conditionals, not named "
            "constants). Replay fidelity claims must not treat this record as "
            "more complete than what these three fields actually cover."
        ),
    }
    try:
        rules = load_rules_config(RULES_CONFIG_PATH, strict=False)
        record["rulesConfigContents"] = rules
        record["rulesConfigVersion"] = rules.get("_version")
    except (OSError, json.JSONDecodeError):
        pass
    try:
        from scripts.build_market_ledger import F5_PRICING_VERSION_CURRENT
        record["f5PricingVersionCurrent"] = F5_PRICING_VERSION_CURRENT
    except ImportError:
        pass
    if stage_artifact_exists("execution", date):
        try:
            execution_env = read_stage_artifact("execution", date)
            record["executionArtifactRulesVersion"] = (execution_env.get("data") or {}).get("rulesVersion")
        except (OSError, json.JSONDecodeError):
            pass
    record["effectiveConfigHash"] = sha256_bytes(canonical_json_bytes(record))
    return record


# ── Temporal / run consistency (item 2) ──────────────────────────────────

_PIPELINE_STAGES_FOR_SKEW_CHECK = (
    "provenance", "normalized_slate", "projections", "recommendations", "execution", "validation", "protection",
)


def detect_temporal_skew(date, reference_created_at, max_skew_hours=MAX_RUN_SKEW_HOURS):
    """
    Compares every pipeline-stage artifact's own meta.createdAt against the
    reference timestamp (RECOMMENDATION_OUTPUT's own createdAt -- the same
    value used as productionRunKey) -- flags skew if any stage's artifact
    is more than max_skew_hours away, meaning this manifest's components
    may have been drawn from meaningfully different production runs (e.g.
    a stale validation.json left over from an earlier day's partial
    failure). Returns (skewed: bool, detail: dict).
    """
    reference = _parse_iso(reference_created_at)
    if reference is None:
        return False, {}
    detail = {}
    skewed = False
    for stage_name in _PIPELINE_STAGES_FOR_SKEW_CHECK:
        if not stage_artifact_exists(stage_name, date):
            continue
        try:
            env = read_stage_artifact(stage_name, date)
        except (OSError, json.JSONDecodeError):
            continue
        created_at = env.get("meta", {}).get("createdAt")
        parsed = _parse_iso(created_at)
        if parsed is None:
            continue
        hours = abs((parsed - reference).total_seconds()) / 3600.0
        detail[stage_name] = {"createdAt": created_at, "hoursFromReference": round(hours, 2)}
        if hours > max_skew_hours:
            skewed = True
    return skewed, detail


# ── Completeness / fidelity derivation (item 7 -- mechanical, not narrative) ──

def derive_completeness_status(components, commit_ambiguous=False, temporal_skew_detected=False):
    """
    Exact rule table (evaluated in order, first match wins):

      1. MISSING_REQUIRED_INPUT -- any REQUIRED component is MISSING.
      2. PARTIAL_REPLAY         -- any REQUIRED component is PARTIAL
                                    (this includes EFFECTIVE_CONFIG, which
                                    this milestone always marks PARTIAL --
                                    see capture_effective_config).
      3. APPROXIMATE_ONLY       -- no REQUIRED gap, but some NICE_TO_HAVE
                                    component is MISSING or PARTIAL.
      4. PARTIAL_REPLAY         -- every component AVAILABLE, but the
                                    production commit is ambiguous
                                    (commit_ambiguous=True) or components
                                    show workflow-run skew
                                    (temporal_skew_detected=True) -- a
                                    snapshot must never claim COMPLETE
                                    when it cannot prove internal
                                    consistency, even if every field is
                                    individually populated.
      5. COMPLETE_FOR_PRODUCTION_REPLAY -- otherwise.

    INTEGRITY_FAILURE is deliberately NOT produced by this function: it
    is a READ-time verdict (see verify_snapshot()/completeness_report()),
    never a value the builder writes into a stored manifest -- a build
    whose own frozen-copy hash check fails raises SnapshotIntegrityError
    and is never committed at all (see freeze_file_component).
    """
    required = [c for c in components if c["requiredStatus"] == REQUIRED]
    if any(c["availabilityStatus"] == MISSING for c in required):
        return MISSING_REQUIRED_INPUT
    if any(c["availabilityStatus"] == PARTIAL for c in required):
        return PARTIAL_REPLAY
    nice = [c for c in components if c["requiredStatus"] == NICE_TO_HAVE]
    if any(c["availabilityStatus"] in (MISSING, PARTIAL) for c in nice):
        return APPROXIMATE_ONLY
    if commit_ambiguous or temporal_skew_detected:
        return PARTIAL_REPLAY
    return COMPLETE_FOR_PRODUCTION_REPLAY


def derive_replay_fidelity(completeness_status, production_commit_sha, snapshot_writer_commit_sha):
    if completeness_status != COMPLETE_FOR_PRODUCTION_REPLAY:
        return LEVEL_1_APPROXIMATE
    if production_commit_sha and snapshot_writer_commit_sha and production_commit_sha == snapshot_writer_commit_sha:
        return LEVEL_3_CODE_PINNED
    return LEVEL_2_PRODUCTION_EQUIVALENT


def _limitation_reasons(components):
    return sorted({c["limitationReason"] for c in components if c.get("limitationReason")})


def _missing_component_types(components):
    return sorted(c["componentType"] for c in components if c["availabilityStatus"] == MISSING)


# ── Stage-specific component assembly ────────────────────────────────────

def _production_slate_input_component(date, staging_frozen_dir, final_frozen_dir):
    authoritative_path = os.path.join("data", "slates", date, "authoritative.json")
    if os.path.exists(authoritative_path):
        return freeze_file_component(
            "PRODUCTION_SLATE_INPUT", authoritative_path, staging_frozen_dir, final_frozen_dir, REQUIRED,
            dest_filename="production_slate_input.json", producer="scripts/protect_slate.py", compress=True,
        )
    slate_path = os.path.join("data", "slate.json")
    if os.path.exists(slate_path):
        try:
            with open(slate_path) as f:
                slate = json.load(f)
        except (OSError, json.JSONDecodeError):
            slate = {}
        if slate.get("date") == date:
            return freeze_file_component(
                "PRODUCTION_SLATE_INPUT", slate_path, staging_frozen_dir, final_frozen_dir, REQUIRED,
                dest_filename="production_slate_input.json", producer="scripts/protect_slate.py (legacy overwritten path)",
                compress=True,
            )
    reason = REASON_OVERWRITTEN_SOURCE_NOT_YET_FROZEN
    slates_dir = os.path.join("data", "slates", date)
    if os.path.isdir(slates_dir) and any(fn.startswith("rejected_contaminated_") for fn in os.listdir(slates_dir)):
        reason = REASON_SOURCE_QUARANTINED
    return _missing_component("PRODUCTION_SLATE_INPUT", REQUIRED, authoritative_path, reason)


def _pipeline_component(component_type, stage_name, date, required_status, producer,
                         staging_frozen_dir, final_frozen_dir, dest_filename, compress=True):
    path = pipeline_artifact_path(stage_name, date)
    return freeze_file_component(
        component_type, path, staging_frozen_dir, final_frozen_dir, required_status,
        dest_filename=dest_filename, producer=producer, compress=compress,
    )


def _production_provenance(date):
    """
    Forward Replay Corpus and Production Provenance milestone, revised
    under the PR #37 maintainer review (item 1): resolves
    data/pipeline/<date>/provenance.json (written by
    scripts/capture_production_provenance.py, positioned in fetch-slate.yml
    after every in-job `git rebase --autostash origin/main` and immediately
    before the model-execution chain begins -- see that script's module
    docstring) into (component, productionProvenance dict).

    NOTE ON A REJECTED DESIGN: earlier revisions of this function
    cross-checked the captured commitSha against snapshot_writer_commit_sha
    (this same job's own live `git rev-parse HEAD`, computed later in the
    run) for equality. That check was found to be structurally inert in
    real CI -- both values are computed via _git_commit_sha(), which always
    prefers the same static GITHUB_SHA env var, so they could never
    actually disagree inside a real GitHub Actions job (only in local runs
    where GITHUB_SHA happens to be unset). Naively "fixing" it by comparing
    live git state on both sides was ALSO rejected: HEAD legitimately
    advances between provenance-capture time and snapshot-write time (end
    of job) due to routine, code-untouching data commits this same job
    makes (pipeline-status, execution-artifact commits) -- a live-HEAD
    equality check would false-positive AMBIGUOUS on nearly every real
    production run.

    The actual authenticity signal used instead is workingTreeDirty (see
    scripts/capture_production_provenance.py): whether the CODE paths
    (scripts/, lib/, config/) were clean relative to HEAD at the moment of
    capture. A dirty code tree at capture time means the commit SHA,
    however recorded, does not honestly describe what is about to execute
    -- so it can never be trusted as CAPTURED. Missing/unknown dirty state
    (None -- e.g. git itself failed) is treated the same as dirty: an
    unknown state must never be silently treated as clean.

    Temporal staleness (an artifact left over from an earlier, different
    run whose capture step ran but whose later steps never overwrote it
    before this run started) is now covered by detect_temporal_skew(),
    which includes "provenance" in _PIPELINE_STAGES_FOR_SKEW_CHECK -- reusing
    the existing, already-tested cross-artifact skew mechanism rather than
    inventing a second, narrower one here.

    Never reconstructs a commit SHA after the fact -- if the artifact is
    missing, productionCommitSha stays None, full stop.
    """
    status = "MISSING"
    reason = "NEVER_CAPTURED_HISTORICALLY"
    commit_sha = None
    payload = {}
    if stage_artifact_exists("provenance", date):
        try:
            envelope = read_stage_artifact("provenance", date)
            payload = envelope.get("data") or {}
        except (OSError, json.JSONDecodeError):
            payload = {}
        candidate_sha = payload.get("commitSha")
        working_tree_dirty = payload.get("workingTreeDirty")
        if not candidate_sha:
            status, reason = "MISSING", "INGESTION_GAP"
        elif working_tree_dirty is not False:
            # True (actually dirty) and None (unknown/git failed) both
            # fall here -- an unproven-clean code tree is never CAPTURED.
            status, reason = "AMBIGUOUS", "PRODUCTION_COMMIT_AMBIGUOUS"
        else:
            status, reason, commit_sha = "CAPTURED", None, candidate_sha

    provenance = {
        "status": status,
        "commitSha": commit_sha,
        "gitHeadShaAtCapture": payload.get("gitHeadShaAtCapture"),
        "workingTreeDirty": payload.get("workingTreeDirty"),
        "workflowRunId": payload.get("workflowRunId"),
        "workflowRunAttempt": payload.get("workflowRunAttempt"),
        "ref": payload.get("ref"),
        "refName": payload.get("refName"),
        "repository": payload.get("repository"),
        "capturedAt": payload.get("capturedAt"),
        "reason": reason,
    }
    return provenance


def build_pre_game_manifest(date, workflow_run_id=None):
    """
    Assemble (but do not yet write) a PRE_GAME_DECISION manifest for
    `date` -- everything known before first pitch. SETTLEMENT/CLV are
    never assessed here; they are always emitted as explicit
    NOT_APPLICABLE_FOR_STAGE entries (look-ahead-bias guard). Frozen
    components are staged under a private temp directory; the caller
    (build_snapshot) commits or discards them.

    Snapshot identity is (date, productionRunKey) -- see module
    docstring's "SNAPSHOT IDENTITY" section.
    """
    staging_root = tempfile.mkdtemp(prefix=".snapshot_staging_")
    staging_frozen = os.path.join(staging_root, "frozen")
    run_key = _production_run_key(date)
    final_frozen = frozen_dir(STAGE_PRE_GAME_DECISION, date, run_key)
    commit_sha = _git_commit_sha()
    captured_at = ids.utc_now_iso()
    production_provenance = _production_provenance(date)

    components = []
    components.append(_production_slate_input_component(date, staging_frozen, final_frozen))
    components.append(_pipeline_component(
        "NORMALIZED_SLATE", "normalized_slate", date, NICE_TO_HAVE, "scripts/enrich_data.py",
        staging_frozen, final_frozen, "normalized_slate.json",
    ))
    components.append(_pipeline_component(
        "RAW_PROJECTIONS", "projections", date, REQUIRED, "scripts/build_market_ledger.py",
        staging_frozen, final_frozen, "raw_projections.json",
    ))
    recommendation_output = _pipeline_component(
        "RECOMMENDATION_OUTPUT", "recommendations", date, REQUIRED, "scripts/build_market_ledger.py",
        staging_frozen, final_frozen, "recommendation_output.json",
    )
    components.append(recommendation_output)
    components.append(freeze_file_component(
        "MARKET_UNIVERSE", os.path.join("data", "kalshi_registry_snapshots", f"kalshi_search_{date}.json"),
        staging_frozen, final_frozen, REQUIRED, dest_filename="market_universe.json",
        producer="fetch-slate.yml (Archive Kalshi registry snapshot step)", compress=True,
    ))
    market_observations = freeze_file_component(
        "MARKET_OBSERVATIONS", os.path.join("data", "edgelab", "observations", f"{date}.jsonl.gz"),
        staging_frozen, final_frozen, NICE_TO_HAVE, dest_filename="market_observations.jsonl.gz",
        producer="scripts/edgelab/ingest_market_observations.py", count_rows=True,
    )
    # Bug found during the Level 2 Historical Replay Engine milestone's
    # eligibility-check development: MARKET_OBSERVATIONS was frozen to
    # disk (real bytes written under frozen/) but never appended to the
    # manifest's own components list -- only its two derived pointers
    # were, leaving an orphaned frozen file no component referenced and
    # making MARKET_OBSERVATIONS look MISSING to any reader (e.g. replay
    # eligibility) even when the observations file genuinely existed and
    # was successfully frozen. Narrow, isolated fix: append it.
    components.append(market_observations)
    # EXECUTABLE_PRICES / BID_ASK are the same underlying evidence as
    # MARKET_OBSERVATIONS -- denormalized pointers, never a second freeze.
    components.append(_rederive_component(market_observations, "EXECUTABLE_PRICES", required_status=NICE_TO_HAVE))
    components.append(_rederive_component(market_observations, "BID_ASK", required_status=NICE_TO_HAVE))
    # LINEUP_STATE: lineup fields (lineupConfirmationState, lineupStatus, etc.)
    # are embedded within RECOMMENDATION_OUTPUT's marketLedger rows -- no
    # separately-extracted durable lineup snapshot exists as of this
    # milestone, so this is a denormalized pointer to the same evidence.
    components.append(_rederive_component(recommendation_output, "LINEUP_STATE", required_status=NICE_TO_HAVE))
    components.append(freeze_file_component(
        "BULLPEN_STATE", os.path.join("data", "bullpen.json"), staging_frozen, final_frozen, NICE_TO_HAVE,
        dest_filename="bullpen.json", producer="api/bullpen.js (via fetch-slate.yml)",
    ))
    components.append(freeze_file_component(
        "WEATHER", os.path.join("data", "weather.json"), staging_frozen, final_frozen, NICE_TO_HAVE,
        dest_filename="weather.json", producer="api/weather.js (via fetch-slate.yml)",
    ))
    effective_config_record = capture_effective_config(
        date, commit_sha, production_commit_sha=production_provenance["commitSha"], production_run_id=run_key,
    )
    effective_config = freeze_record_component(
        "EFFECTIVE_CONFIG", effective_config_record, staging_frozen, final_frozen, "effective_config.json",
        REQUIRED, producer="lib/edgelab/snapshot.py:capture_effective_config",
        # Always PARTIAL -- see capture_effective_config's docstring and
        # docs/SNAPSHOT_ARCHITECTURE.md: hardcoded production thresholds
        # outside config/rules.json are not structurally represented here.
        availability_status=PARTIAL, limitation_reason=REASON_PARTIAL_FIELD_POPULATION,
    )
    components.append(effective_config)
    components.append(_rederive_component(effective_config, "PARK_FACTORS", required_status=NICE_TO_HAVE))
    components.append(freeze_file_component(
        "MODEL_EVALUATIONS", os.path.join("data", "edgelab", "model_evaluations", f"{date}.jsonl"),
        staging_frozen, final_frozen, NICE_TO_HAVE, dest_filename="model_evaluations.jsonl",
        producer="lib/edgelab/model_evaluation.py", count_rows=True, compress=True,
    ))
    components.append(freeze_file_component(
        "RECOMMENDATIONS", os.path.join("data", "edgelab", "recommendations", f"{date}.jsonl"),
        staging_frozen, final_frozen, NICE_TO_HAVE, dest_filename="recommendations_ledger.jsonl",
        producer="lib/edgelab/recommendations.py", count_rows=True, compress=True,
    ))
    components.append(_pipeline_component(
        "RISK_GATE_OUTPUT", "execution", date, REQUIRED, "scripts/risk_gate.py",
        staging_frozen, final_frozen, "risk_gate_output.json",
    ))
    components.append(freeze_file_component(
        "EXECUTION_SLIP", os.path.join("data", f"execution_slip_{date}.json"),
        staging_frozen, final_frozen, NICE_TO_HAVE, dest_filename="execution_slip.json",
        producer="scripts/risk_gate.py", compress=True,
    ))
    components.append(_pipeline_component(
        "VALIDATION_ARTIFACT", "validation", date, NICE_TO_HAVE, "scripts/validate_slate_final.py",
        staging_frozen, final_frozen, "validation_artifact.json",
    ))
    components.append(_pipeline_component(
        "PROTECTION_ARTIFACT", "protection", date, NICE_TO_HAVE, "scripts/protect_slate.py",
        staging_frozen, final_frozen, "protection_artifact.json",
    ))
    provenance_component = _pipeline_component(
        "PRODUCTION_PROVENANCE", "provenance", date, REQUIRED, "scripts/capture_production_provenance.py",
        staging_frozen, final_frozen, "production_provenance.json",
    )
    if production_provenance["status"] == "AMBIGUOUS":
        provenance_component["availabilityStatus"] = PARTIAL
        provenance_component["limitationReason"] = REASON_PRODUCTION_COMMIT_AMBIGUOUS
    elif production_provenance["status"] == "MISSING":
        provenance_component["availabilityStatus"] = MISSING
        provenance_component["storageMode"] = None
        provenance_component["snapshotPath"] = None
        provenance_component["contentHash"] = None
        provenance_component["limitationReason"] = REASON_INGESTION_GAP
    components.append(provenance_component)
    components.append(not_applicable_component("SETTLEMENT", required_status=NICE_TO_HAVE, reason=REASON_POSTGAME_EXCLUDED))
    components.append(not_applicable_component("CLV", required_status=NICE_TO_HAVE, reason=REASON_POSTGAME_EXCLUDED))

    pricing_versions = {}
    if effective_config_record.get("f5PricingVersionCurrent"):
        pricing_versions["F5_ML"] = effective_config_record["f5PricingVersionCurrent"]

    # Commit is ambiguous unless production provenance was captured (see
    # _production_provenance's docstring) -- MISSING or AMBIGUOUS status
    # both count as ambiguous, per item 2's "fail or downgrade completeness
    # if productionCommitSha is missing or ambiguous". productionCommitSha
    # itself is set ONLY when status == CAPTURED -- never reconstructed
    # from any other source.
    production_commit_sha = production_provenance["commitSha"]
    commit_ambiguous = production_provenance["status"] != "CAPTURED"
    skewed, skew_detail = detect_temporal_skew(date, run_key) if run_key else (False, {})

    completeness_status = derive_completeness_status(
        components, commit_ambiguous=commit_ambiguous, temporal_skew_detected=skewed,
    )
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "snapshotId": ids.build_snapshot_id(STAGE_PRE_GAME_DECISION, date, run_key),
        "snapshotStage": STAGE_PRE_GAME_DECISION,
        "snapshotDate": date,
        "captureMode": CAPTURE_MODE_LIVE,
        "capturedAt": captured_at,
        "productionRunId": run_key,
        "workflowRunId": workflow_run_id,
        "productionCommitSha": production_commit_sha,
        "productionProvenance": production_provenance,
        "snapshotWriterCommitSha": commit_sha,
        "modelVersion": None,
        "pricingVersionsByFamily": pricing_versions,
        "rulesConfigVersion": effective_config_record.get("rulesConfigVersion"),
        "temporalConsistency": {
            "skewDetected": skewed, "maxSkewHours": MAX_RUN_SKEW_HOURS, "referenceTimestamp": run_key,
            "detail": skew_detail,
        },
        "replayFidelityPotential": derive_replay_fidelity(completeness_status, production_commit_sha, commit_sha),
        "completenessStatus": completeness_status,
        "validationStatus": "valid",
        "components": components,
        "missingComponents": _missing_component_types(components),
        "limitationReasons": _limitation_reasons(components),
        "linkedSnapshotIds": [],
        "provenance": {
            "sourceSystem": "snapshot_foundation", "sourceFile": None, "sourceKey": None,
            "capturedAt": captured_at, "ingestedAt": captured_at,
        },
    }
    manifest["manifestHash"] = compute_manifest_hash(manifest)
    return manifest, staging_root, run_key


def build_post_game_manifest(date, workflow_run_id=None):
    staging_root = tempfile.mkdtemp(prefix=".snapshot_staging_")
    staging_frozen = os.path.join(staging_root, "frozen")
    final_frozen = frozen_dir(STAGE_POST_GAME_SETTLEMENT, date)
    commit_sha = _git_commit_sha()
    captured_at = ids.utc_now_iso()

    components = [
        freeze_file_component(
            "SETTLEMENT", os.path.join("data", "edgelab", "settlements", f"{date}.jsonl"),
            staging_frozen, final_frozen, REQUIRED, dest_filename="settlement.jsonl",
            producer="scripts/edgelab/settle_markets.py", count_rows=True, compress=True,
        ),
        freeze_file_component(
            "CLV", os.path.join("data", "edgelab", "clv_quotes", f"{date}.jsonl"),
            staging_frozen, final_frozen, REQUIRED, dest_filename="clv_quotes.jsonl",
            producer="lib/edgelab/clv.py", count_rows=True, compress=True,
        ),
    ]
    completeness_status = derive_completeness_status(components, commit_ambiguous=True)
    linked = _linked_pregame_ids(date)
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "snapshotId": ids.build_snapshot_id(STAGE_POST_GAME_SETTLEMENT, date),
        "snapshotStage": STAGE_POST_GAME_SETTLEMENT,
        "snapshotDate": date,
        "captureMode": CAPTURE_MODE_LIVE,
        "capturedAt": captured_at,
        "productionRunId": None,
        "workflowRunId": workflow_run_id,
        "productionCommitSha": None,
        "productionProvenance": None,
        "snapshotWriterCommitSha": commit_sha,
        "modelVersion": None,
        "pricingVersionsByFamily": {},
        "rulesConfigVersion": None,
        "temporalConsistency": {"skewDetected": False, "maxSkewHours": MAX_RUN_SKEW_HOURS, "referenceTimestamp": None, "detail": {}},
        "replayFidelityPotential": derive_replay_fidelity(completeness_status, None, commit_sha),
        "completenessStatus": completeness_status,
        "validationStatus": "valid",
        "components": components,
        "missingComponents": _missing_component_types(components),
        "limitationReasons": _limitation_reasons(components),
        "linkedSnapshotIds": linked,
        "provenance": {
            "sourceSystem": "snapshot_foundation", "sourceFile": None, "sourceKey": None,
            "capturedAt": captured_at, "ingestedAt": captured_at,
        },
    }
    manifest["manifestHash"] = compute_manifest_hash(manifest)
    return manifest, staging_root, None


def _linked_pregame_ids(date):
    """Every existing PRE_GAME_DECISION snapshotId for `date`, oldest
    first -- POST_GAME_SETTLEMENT/CLOSING_LINE link BACKWARD to all of
    them (there may be more than one distinct production run per date;
    see module docstring) without ever mutating an earlier manifest."""
    ids_found = []
    for run_dir in list_pregame_run_dirs(date):
        path = os.path.join(SNAPSHOTS_ROOT, date, STAGE_PRE_GAME_DECISION.lower(), run_dir, "manifest.json")
        try:
            with open(path) as f:
                m = json.load(f)
            ids_found.append(m.get("snapshotId"))
        except (OSError, json.JSONDecodeError):
            continue
    return ids_found


def build_closing_line_manifest(date, workflow_run_id=None):
    staging_root = tempfile.mkdtemp(prefix=".snapshot_staging_")
    staging_frozen = os.path.join(staging_root, "frozen")
    final_frozen = frozen_dir(STAGE_CLOSING_LINE, date)
    commit_sha = _git_commit_sha()
    captured_at = ids.utc_now_iso()

    components = [freeze_file_component(
        "MARKET_OBSERVATIONS", os.path.join("data", "edgelab", "observations", f"{date}.jsonl.gz"),
        staging_frozen, final_frozen, REQUIRED, dest_filename="market_observations.jsonl.gz",
        producer="scripts/edgelab/ingest_market_observations.py", count_rows=True,
    )]
    completeness_status = derive_completeness_status(components, commit_ambiguous=True)
    linked = _linked_pregame_ids(date)
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "snapshotId": ids.build_snapshot_id(STAGE_CLOSING_LINE, date),
        "snapshotStage": STAGE_CLOSING_LINE,
        "snapshotDate": date,
        "captureMode": CAPTURE_MODE_LIVE,
        "capturedAt": captured_at,
        "productionRunId": None,
        "workflowRunId": workflow_run_id,
        "productionCommitSha": None,
        "productionProvenance": None,
        "snapshotWriterCommitSha": commit_sha,
        "modelVersion": None,
        "pricingVersionsByFamily": {},
        "rulesConfigVersion": None,
        "temporalConsistency": {"skewDetected": False, "maxSkewHours": MAX_RUN_SKEW_HOURS, "referenceTimestamp": None, "detail": {}},
        "replayFidelityPotential": derive_replay_fidelity(completeness_status, None, commit_sha),
        "completenessStatus": completeness_status,
        "validationStatus": "valid",
        "components": components,
        "missingComponents": _missing_component_types(components),
        "limitationReasons": _limitation_reasons(components),
        "linkedSnapshotIds": linked,
        "provenance": {
            "sourceSystem": "snapshot_foundation", "sourceFile": None, "sourceKey": None,
            "capturedAt": captured_at, "ingestedAt": captured_at,
        },
    }
    manifest["manifestHash"] = compute_manifest_hash(manifest)
    return manifest, staging_root, None


_BUILDERS = {
    STAGE_PRE_GAME_DECISION: build_pre_game_manifest,
    STAGE_POST_GAME_SETTLEMENT: build_post_game_manifest,
    STAGE_CLOSING_LINE: build_closing_line_manifest,
}


# ── Write-once commit (item 8/5) ──────────────────────────────────────────

def _commit_snapshot(stage, date, run_key, candidate_manifest, staging_root):
    final_dir = snapshot_dir(stage, date, run_key)
    final_manifest_path = manifest_path(stage, date, run_key)
    staged_frozen = os.path.join(staging_root, "frozen")
    final_frozen = frozen_dir(stage, date, run_key)

    if not os.path.exists(final_manifest_path):
        os.makedirs(final_dir, exist_ok=True)
        if os.path.isdir(staged_frozen):
            if os.path.isdir(final_frozen):
                shutil.rmtree(final_frozen)
            shutil.move(staged_frozen, final_frozen)
        _atomic_write_bytes(final_manifest_path, json.dumps(candidate_manifest, indent=2, sort_keys=True).encode("utf-8"))
        return {"outcome": "created", "manifest": candidate_manifest, "path": final_manifest_path}

    with open(final_manifest_path) as f:
        existing_manifest = json.load(f)
    existing_hash = existing_manifest.get("manifestHash")
    recomputed_existing = compute_manifest_hash(existing_manifest)

    if recomputed_existing != existing_hash:
        return {
            "outcome": "existing_manifest_corrupted", "manifest": existing_manifest, "path": final_manifest_path,
            "diagnostics": {"storedHash": existing_hash, "recomputedHash": recomputed_existing},
        }

    if candidate_manifest["manifestHash"] == existing_hash:
        # Deterministic no-op: existing snapshot already reflects this exact
        # content. Verified, not rewritten. Discard the newly-staged frozen
        # bytes (they are byte-identical to what's already committed).
        return {"outcome": "noop_verified", "manifest": existing_manifest, "path": final_manifest_path}

    # Genuine conflict (same identity key -- same date/stage/runKey -- but
    # different content, which should only happen for a non-deterministic
    # capture bug, since a real different production run gets its OWN
    # runKey and therefore its own slot): preserve the existing snapshot
    # untouched; stash the candidate + its staged frozen bytes as
    # diagnostic evidence alongside it.
    conflict_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    conflict_dir = os.path.join(final_dir, "conflicts", conflict_ts)
    os.makedirs(conflict_dir, exist_ok=True)
    _atomic_write_bytes(
        os.path.join(conflict_dir, "candidate_manifest.json"),
        json.dumps(candidate_manifest, indent=2, sort_keys=True).encode("utf-8"),
    )
    if os.path.isdir(staged_frozen):
        shutil.move(staged_frozen, os.path.join(conflict_dir, "frozen"))
    return {
        "outcome": "conflict", "manifest": existing_manifest, "path": final_manifest_path,
        "conflictEvidencePath": conflict_dir,
    }


def build_snapshot(stage: str, date: str, workflow_run_id=None):
    """
    Build and write-once-commit a snapshot manifest for (stage, date).
    For PRE_GAME_DECISION, the identity also includes the auto-derived
    productionRunKey (see module docstring) -- a genuinely different
    production run for the same date gets its own slot rather than
    conflicting with an earlier valid decision snapshot.

    Returns the _commit_snapshot() result dict: {"outcome": "created" |
    "noop_verified" | "conflict" | "existing_manifest_corrupted", ...}.
    Always cleans up its staging directory, whatever the outcome.
    """
    if stage not in VALID_STAGES:
        raise ValueError(f"unknown snapshotStage {stage!r}, must be one of {sorted(VALID_STAGES)}")
    manifest, staging_root, run_key = _BUILDERS[stage](date, workflow_run_id=workflow_run_id)
    try:
        return _commit_snapshot(stage, date, run_key, manifest, staging_root)
    finally:
        if os.path.isdir(staging_root):
            shutil.rmtree(staging_root, ignore_errors=True)


def classify_date(stage: str, date: str, capture_mode=CAPTURE_MODE_LIVE):
    """
    DRY RUN -- assesses what a Snapshot for (stage, date) WOULD contain,
    without writing or freezing anything (the staging directory is
    discarded, never committed). Used by scripts/backfill_snapshots.py to
    classify historical dates (fully snapshot-capable / partial /
    approximate only / not reconstructable) without mutating the repo for
    dates this milestone should not actually backfill.
    """
    if stage not in VALID_STAGES:
        raise ValueError(f"unknown snapshotStage {stage!r}, must be one of {sorted(VALID_STAGES)}")
    manifest, staging_root, _run_key = _BUILDERS[stage](date)
    if os.path.isdir(staging_root):
        shutil.rmtree(staging_root, ignore_errors=True)
    return {
        "snapshotStage": stage,
        "snapshotDate": date,
        "completenessStatus": manifest["completenessStatus"],
        "replayFidelityPotential": manifest["replayFidelityPotential"],
        "missingComponents": manifest["missingComponents"],
        "limitationReasons": manifest["limitationReasons"],
    }


def build_snapshot_as_backfill(stage: str, date: str):
    """
    Same as build_snapshot(), but stamps captureMode=HISTORICAL_BACKFILL
    so a backfilled manifest can never be confused with a contemporaneous
    production capture (item 8). Used only by scripts/backfill_snapshots.py.
    """
    if stage not in VALID_STAGES:
        raise ValueError(f"unknown snapshotStage {stage!r}, must be one of {sorted(VALID_STAGES)}")
    manifest, staging_root, run_key = _BUILDERS[stage](date)
    manifest["captureMode"] = CAPTURE_MODE_BACKFILL
    manifest["manifestHash"] = compute_manifest_hash(manifest)
    try:
        return _commit_snapshot(stage, date, run_key, manifest, staging_root)
    finally:
        if os.path.isdir(staging_root):
            shutil.rmtree(staging_root, ignore_errors=True)


CLASSIFICATION_LABELS = {
    COMPLETE_FOR_PRODUCTION_REPLAY: "FULLY_SNAPSHOT_CAPABLE",
    PARTIAL_REPLAY: "PARTIAL",
    APPROXIMATE_ONLY: "APPROXIMATE_ONLY",
    MISSING_REQUIRED_INPUT: "NOT_RECONSTRUCTABLE",
}


# ── Replay-read interface (item 13 -- smallest possible) ─────────────────

def load_manifest(stage: str, date: str, run_key=None):
    path = manifest_path(stage, date, run_key)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def load_latest_pregame_manifest(date: str):
    """Convenience reader: the most recent PRE_GAME_DECISION run for
    `date` (run keys are ISO-timestamp-derived, so the lexicographically
    last existing run directory is also the chronologically latest)."""
    run_dirs = list_pregame_run_dirs(date)
    if not run_dirs:
        return None
    path = os.path.join(SNAPSHOTS_ROOT, date, STAGE_PRE_GAME_DECISION.lower(), run_dirs[-1], "manifest.json")
    with open(path) as f:
        return json.load(f)


def find_manifest_by_id(snapshot_id: str):
    """Scans data/edgelab/snapshots/**/manifest.json for a matching snapshotId.
    Manifests are small and few; a full index is not warranted at this
    milestone's data volume -- see docs/SNAPSHOT_ARCHITECTURE.md."""
    if not os.path.isdir(SNAPSHOTS_ROOT):
        return None
    for root, _dirs, files in os.walk(SNAPSHOTS_ROOT):
        if "manifest.json" not in files:
            continue
        candidate = os.path.join(root, "manifest.json")
        try:
            with open(candidate) as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("snapshotId") == snapshot_id:
            return manifest
    return None


def list_components(manifest: dict):
    return manifest.get("components", [])


def completeness_report(manifest: dict):
    """
    The "live truth" reader (item 10): re-verifies the manifest and, if
    verification fails NOW, reports completenessStatus as INTEGRITY_FAILURE
    regardless of what the stored manifest's own completenessStatus field
    says -- the stored field is a historical record of completeness AT
    CAPTURE TIME; this function is what a caller should actually trust.
    """
    verification = verify_snapshot(manifest)
    reported_status = INTEGRITY_FAILURE if verification["overallStatus"] == "INTEGRITY_FAILURE" else manifest.get("completenessStatus")
    return {
        "snapshotId": manifest.get("snapshotId"),
        "snapshotStage": manifest.get("snapshotStage"),
        "snapshotDate": manifest.get("snapshotDate"),
        "captureMode": manifest.get("captureMode"),
        "storedCompletenessStatus": manifest.get("completenessStatus"),
        "completenessStatus": reported_status,
        "replayFidelityPotential": manifest.get("replayFidelityPotential") if reported_status != INTEGRITY_FAILURE else LEVEL_1_APPROXIMATE,
        "missingComponents": manifest.get("missingComponents", []),
        "limitationReasons": manifest.get("limitationReasons", []),
        "integrityVerification": verification,
    }


def verify_snapshot(manifest: dict):
    """
    Freshly re-verifies (never mutates the stored manifest): the
    manifest's own hash, and every component's contentHash against its
    live sourcePath (REFERENCED_IMMUTABLE) or snapshotPath (FROZEN_COPY).
    A REFERENCED_IMMUTABLE mismatch means the "believed immutable" source
    was in fact rewritten (or pruned) after capture -- reported here,
    never silently trusted (see module docstring). A FROZEN_COPY mismatch
    means the snapshot's own frozen bytes were tampered with after the
    fact -- the more serious of the two, since freezing exists precisely
    to make this impossible under normal operation.
    """
    manifest_hash_valid = compute_manifest_hash(manifest) == manifest.get("manifestHash")
    component_results = []
    any_integrity_failure = not manifest_hash_valid
    for component in manifest.get("components", []):
        entry = {"componentType": component["componentType"], "availabilityStatus": component["availabilityStatus"]}
        if component["availabilityStatus"] != AVAILABLE or not component.get("contentHash"):
            entry["hashValid"] = None
        else:
            check_path = component["snapshotPath"] if component["storageMode"] == STORAGE_FROZEN_COPY else component["sourcePath"]
            if check_path and os.path.exists(check_path):
                entry["hashValid"] = sha256_file(check_path) == component["contentHash"]
            else:
                entry["hashValid"] = False
            if not entry["hashValid"]:
                any_integrity_failure = True
        component_results.append(entry)

    overall = "INTEGRITY_FAILURE" if any_integrity_failure else "VERIFIED"
    return {
        "manifestHashValid": manifest_hash_valid,
        "components": component_results,
        "overallStatus": overall,
    }


def load_frozen_component(manifest: dict, component_type: str):
    """Returns the parsed JSON content of a FROZEN_COPY component, or None
    if the component isn't frozen/available. Exposes the frozen decision-time
    inputs (item 13). Returns raw bytes (not parsed) for a non-JSON frozen
    file if json.load fails -- currently every frozen component in this
    module is JSON or JSONL(.gz), so JSON parsing (with gzip transparently
    handled) is always attempted first."""
    for component in manifest.get("components", []):
        if component["componentType"] == component_type and component.get("storageMode") == STORAGE_FROZEN_COPY:
            if component["availabilityStatus"] not in (AVAILABLE, PARTIAL) or not component.get("snapshotPath"):
                return None
            path = component["snapshotPath"]
            if not os.path.exists(path):
                return None
            opener = gzip.open if path.endswith(".gz") else open
            with opener(path, "rt", encoding="utf-8") as f:
                if path.endswith(".jsonl") or path.endswith(".jsonl.gz"):
                    return [json.loads(line) for line in f if line.strip()]
                return json.load(f)
    return None
