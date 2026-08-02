"""
lib/edgelab/snapshot.py
==========================
Historical Capture Completeness and Immutable Snapshot Foundation
milestone: an immutable, hash-verifiable, write-once daily Snapshot --
a manifest of everything the production model knew (or explicitly did
NOT know) at one point in time for one MLB slate date.

See docs/SNAPSHOT_ARCHITECTURE.md for the full design writeup. This
module implements capture, integrity verification, and the smallest
possible read interface -- it does NOT implement replay, backtesting,
uncertainty estimation, or any research/handicapping computation. It
never reads or writes model probabilities, recommendation logic,
thresholds, staking, or settlement outcomes -- it only records pointers
to (or frozen copies of) artifacts other modules already produce.

MUTABLE vs IMMUTABLE CLASSIFICATION RULE (the design review's item 2)
----------------------------------------------------------------------
A source is REFERENCED_IMMUTABLE (path + SHA-256 only, no duplicate
bytes) when: a second read of the same path, for the same logical
(date, key), is guaranteed to return the same bytes it did at capture
time, because the writer uses either (a) lib.pipeline_artifacts's
write-once-per-(stage,date) artifact convention, (b) an append-only or
"dated, kept forever" storage convention (lib.edgelab.storage's JSONL
partitions, data/kalshi_registry_snapshots/kalshi_search_<date>.json),
or (c) an explicitly protected, non-overwritable path
(data/slates/<date>/authoritative.json, which scripts/protect_slate.py
never rewrites once written for a date). This guarantee is a strong
BELIEF, not a filesystem-enforced fact -- so every REFERENCED_IMMUTABLE
component's hash is re-checked at verify() time, and a violation is
reported loudly (INTEGRITY_FAILURE at read time), never silently
trusted forever.

A source must be FROZEN (its exact bytes copied into a snapshot-owned
location at capture time) when: the same path is a SINGLE, LIVE,
OVERWRITTEN-IN-PLACE file with no per-date history at all
(data/weather.json, data/bullpen.json, the legacy data/slate.json when
no authoritative.json exists yet for that date, config/rules.json).
For these, "reference by path" would silently describe TODAY's content
whenever a future reader opens the path, not what production actually
saw on the snapshotted date. Byte-for-byte duplication is unavoidable
here (there is no other way to preserve "what it was on date D"), so
it is a deliberate exception to the general "don't copy bulky immutable
files" preference, applied ONLY to sources that need it.

Postgame data (SETTLEMENT, CLV) is never a component of a
PRE_GAME_DECISION manifest -- see STAGE_COMPONENT_TYPES below; it is
always emitted as an explicit NOT_APPLICABLE_FOR_STAGE entry rather
than silently absent, so a reader can see the exclusion was deliberate.
"""

import hashlib
import json
import os
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

COMPLETE_FOR_PRODUCTION_REPLAY = "COMPLETE_FOR_PRODUCTION_REPLAY"
PARTIAL_REPLAY = "PARTIAL_REPLAY"
APPROXIMATE_ONLY = "APPROXIMATE_ONLY"
INTEGRITY_FAILURE = "INTEGRITY_FAILURE"
MISSING_REQUIRED_INPUT = "MISSING_REQUIRED_INPUT"

LEVEL_1_APPROXIMATE = "LEVEL_1_APPROXIMATE"
LEVEL_2_PRODUCTION_EQUIVALENT = "LEVEL_2_PRODUCTION_EQUIVALENT"
LEVEL_3_BIT_FOR_BIT = "LEVEL_3_BIT_FOR_BIT"

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
        "VALIDATION_ARTIFACT", "PROTECTION_ARTIFACT",
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
        "MARKET_UNIVERSE", "EFFECTIVE_CONFIG", "RISK_GATE_OUTPUT",
    }),
    STAGE_POST_GAME_SETTLEMENT: frozenset({"SETTLEMENT"}),
    STAGE_CLOSING_LINE: frozenset({"MARKET_OBSERVATIONS"}),
}

ALL_COMPONENT_TYPES = frozenset(
    t for types in STAGE_COMPONENT_TYPES.values() for t in types
) | frozenset({"SETTLEMENT", "CLV"})


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


# Fields deliberately excluded from the manifest-hash computation: these
# are "when/how this was captured" facts that legitimately differ between
# two otherwise-identical captures of the same underlying data (e.g. a
# manual rerun minutes later has a later capturedAt but must still be
# recognized as a no-op if every component's content is unchanged). Only
# the fields that describe WHAT was captured participate in the hash.
_MANIFEST_HASH_EXCLUDED_FIELDS = frozenset({
    "manifestHash", "capturedAt", "workflowRunId", "productionRunId",
    "snapshotWriterCommitSha", "provenance",
})


def compute_manifest_hash(manifest: dict) -> str:
    """SHA-256 over the manifest's canonical, content-only form (see
    _MANIFEST_HASH_EXCLUDED_FIELDS) -- proves the CAPTURED DATA hasn't
    changed, not that the wrapper metadata is byte-identical."""
    candidate = {k: v for k, v in manifest.items() if k not in _MANIFEST_HASH_EXCLUDED_FIELDS}
    return sha256_bytes(canonical_json_bytes(candidate))


# ── Paths ─────────────────────────────────────────────────────────────────

def snapshot_dir(stage: str, date: str) -> str:
    return os.path.join(SNAPSHOTS_ROOT, date, stage.lower())


def manifest_path(stage: str, date: str) -> str:
    return os.path.join(snapshot_dir(stage, date), "manifest.json")


def frozen_dir(stage: str, date: str) -> str:
    return os.path.join(snapshot_dir(stage, date), "frozen")


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
    """REFERENCED_IMMUTABLE: hash the source file in place, do not copy it."""
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
                           dest_filename=None, producer=None, captured_at=None,
                           missing_reason=REASON_OVERWRITTEN_SOURCE_NOT_YET_FROZEN):
    """
    FROZEN_COPY: copy source_path's exact bytes into staging_frozen_dir now
    (so a conflicting concurrent capture never touches the real frozen/
    directory before the write-once check runs -- see _commit_snapshot).
    The recorded snapshotPath is the FINAL path the bytes will occupy once
    committed, not the staging path.
    """
    if not source_path or not os.path.exists(source_path):
        return _missing_component(component_type, required_status, source_path, missing_reason)
    source_hash, source_size = _hash_and_size(source_path)
    dest_filename = dest_filename or os.path.basename(source_path)
    staged_path = os.path.join(staging_frozen_dir, dest_filename)
    _atomic_copy(source_path, staged_path)
    frozen_hash, frozen_size = _hash_and_size(staged_path)
    if frozen_hash != source_hash:
        raise SnapshotIntegrityError(
            f"frozen copy of {source_path!r} (hash {frozen_hash}) does not match "
            f"source hash {source_hash} immediately after copy"
        )
    return {
        "componentType": component_type, "sourcePath": source_path,
        "snapshotPath": os.path.join(final_frozen_dir, dest_filename),
        "storageMode": STORAGE_FROZEN_COPY, "contentHash": frozen_hash, "byteSize": frozen_size,
        "rowCount": None, "capturedAt": captured_at, "producer": producer,
        "requiredStatus": required_status, "availabilityStatus": AVAILABLE, "limitationReason": None,
    }


def freeze_record_component(component_type, record: dict, staging_frozen_dir, final_frozen_dir, dest_filename,
                             required_status, producer=None, captured_at=None):
    """FROZEN_COPY for a synthesized in-memory record (e.g. effective config) rather than a raw file copy."""
    data_bytes = canonical_json_bytes(record)
    staged_path = os.path.join(staging_frozen_dir, dest_filename)
    _atomic_write_bytes(staged_path, data_bytes)
    return {
        "componentType": component_type, "sourcePath": None,
        "snapshotPath": os.path.join(final_frozen_dir, dest_filename),
        "storageMode": STORAGE_FROZEN_COPY, "contentHash": sha256_bytes(data_bytes), "byteSize": len(data_bytes),
        "rowCount": None, "capturedAt": captured_at, "producer": producer,
        "requiredStatus": required_status, "availabilityStatus": AVAILABLE, "limitationReason": None,
    }


def _rederive_component(base_component, new_type, required_status=NICE_TO_HAVE, note_reason=None):
    """A second componentType that happens to be embedded in an already-captured component
    (e.g. PARK_FACTORS living inside EFFECTIVE_CONFIG's frozen record) -- denormalized
    pointer, never a second freeze/hash of the same bytes."""
    derived = dict(base_component)
    derived["componentType"] = new_type
    derived["requiredStatus"] = required_status
    if note_reason and derived["availabilityStatus"] != AVAILABLE:
        derived["limitationReason"] = note_reason
    return derived


# ── Effective production configuration (item 7) ──────────────────────────

def capture_effective_config(date: str, commit_sha):
    """
    The narrowest truthful mechanism for "what did production actually use":
    config/rules.json's own contents+version (real, but per lib/rules_config.py's
    own docstring NOT claimed to be the complete production rule set -- the
    live pipeline hardcodes some thresholds directly in code), PLUS the one
    real, live-importable versioned constant that exists today
    (F5_PRICING_VERSION_CURRENT), PLUS whatever rulesVersion literal that
    date's own execution.json artifact already recorded (risk_gate.py writes
    'rulesVersion': '1.0' into it) -- read back verbatim, never re-derived
    from source text. Nothing here is fabricated: every field is either
    read from a real file or read from a real, live code object.
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
        "note": (
            "rulesConfigContents is NOT the complete production rule set -- "
            "see lib/rules_config.py's own docstring: the live betting/pricing "
            "pipeline hardcodes some thresholds directly in code, not via this "
            "file. This record captures every live-constant value this "
            "milestone could honestly introspect; it does not claim more."
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
    return record


# ── Completeness / fidelity derivation (item 9 -- mechanical, not narrative) ──

def derive_completeness_status(components):
    required = [c for c in components if c["requiredStatus"] == REQUIRED]
    if any(c["availabilityStatus"] == MISSING for c in required):
        return MISSING_REQUIRED_INPUT
    if any(c["availabilityStatus"] == PARTIAL for c in required):
        return PARTIAL_REPLAY
    nice = [c for c in components if c["requiredStatus"] == NICE_TO_HAVE]
    if any(c["availabilityStatus"] in (MISSING, PARTIAL) for c in nice):
        return APPROXIMATE_ONLY
    return COMPLETE_FOR_PRODUCTION_REPLAY


def derive_replay_fidelity(completeness_status, production_commit_sha, snapshot_writer_commit_sha):
    if completeness_status != COMPLETE_FOR_PRODUCTION_REPLAY:
        return LEVEL_1_APPROXIMATE
    if production_commit_sha and snapshot_writer_commit_sha and production_commit_sha == snapshot_writer_commit_sha:
        return LEVEL_3_BIT_FOR_BIT
    return LEVEL_2_PRODUCTION_EQUIVALENT


def _limitation_reasons(components):
    return sorted({c["limitationReason"] for c in components if c.get("limitationReason")})


def _missing_component_types(components):
    return sorted(c["componentType"] for c in components if c["availabilityStatus"] == MISSING)


# ── Stage-specific component assembly ────────────────────────────────────

def _production_slate_input_component(date, staging_frozen_dir, final_frozen_dir):
    authoritative_path = os.path.join("data", "slates", date, "authoritative.json")
    if os.path.exists(authoritative_path):
        return build_referenced_component(
            "PRODUCTION_SLATE_INPUT", authoritative_path, REQUIRED, producer="scripts/protect_slate.py",
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
                dest_filename="slate.json", producer="scripts/protect_slate.py (legacy overwritten path)",
            )
    reason = REASON_OVERWRITTEN_SOURCE_NOT_YET_FROZEN
    slates_dir = os.path.join("data", "slates", date)
    if os.path.isdir(slates_dir) and any(fn.startswith("rejected_contaminated_") for fn in os.listdir(slates_dir)):
        reason = REASON_SOURCE_QUARANTINED
    return _missing_component("PRODUCTION_SLATE_INPUT", REQUIRED, authoritative_path, reason)


def _observations_component(component_type, date, required_status):
    path = os.path.join("data", "edgelab", "observations", f"{date}.jsonl.gz")
    return build_referenced_component(component_type, path, required_status, producer="scripts/edgelab/ingest_market_observations.py")


def _pipeline_component(component_type, stage_name, date, required_status, producer):
    path = pipeline_artifact_path(stage_name, date)
    return build_referenced_component(component_type, path, required_status, producer=producer)


def build_pre_game_manifest(date, workflow_run_id=None, production_run_id=None):
    """
    Assemble (but do not yet write) the PRE_GAME_DECISION manifest for
    `date` -- everything known before first pitch. SETTLEMENT/CLV are
    never assessed here; they are always emitted as explicit
    NOT_APPLICABLE_FOR_STAGE entries (item 4's look-ahead-leakage
    requirement). Frozen components are staged under a private temp
    directory; the caller (build_snapshot) commits or discards them.
    """
    staging_root = tempfile.mkdtemp(prefix=".snapshot_staging_")
    staging_frozen = os.path.join(staging_root, "frozen")
    final_frozen = frozen_dir(STAGE_PRE_GAME_DECISION, date)
    commit_sha = _git_commit_sha()
    captured_at = ids.utc_now_iso()

    components = []
    components.append(_production_slate_input_component(date, staging_frozen, final_frozen))
    components.append(_pipeline_component("NORMALIZED_SLATE", "normalized_slate", date, NICE_TO_HAVE, "scripts/enrich_data.py"))
    components.append(_pipeline_component("RAW_PROJECTIONS", "projections", date, REQUIRED, "scripts/build_market_ledger.py"))
    recommendation_output = _pipeline_component("RECOMMENDATION_OUTPUT", "recommendations", date, REQUIRED, "scripts/build_market_ledger.py")
    components.append(recommendation_output)
    components.append(build_referenced_component(
        "MARKET_UNIVERSE", os.path.join("data", "kalshi_registry_snapshots", f"kalshi_search_{date}.json"),
        REQUIRED, producer="fetch-slate.yml (Archive Kalshi registry snapshot step)",
    ))
    components.append(_observations_component("EXECUTABLE_PRICES", date, NICE_TO_HAVE))
    components.append(_observations_component("BID_ASK", date, NICE_TO_HAVE))
    # LINEUP_STATE: lineup fields (lineupConfirmationState, lineupStatus, etc.)
    # are embedded within RECOMMENDATION_OUTPUT's marketLedger rows -- no
    # separately-extracted durable lineup snapshot exists as of this
    # milestone, so this is a denormalized pointer to the same evidence,
    # never a second freeze/hash of the same bytes.
    components.append(_rederive_component(recommendation_output, "LINEUP_STATE", required_status=NICE_TO_HAVE))
    components.append(freeze_file_component(
        "BULLPEN_STATE", os.path.join("data", "bullpen.json"), staging_frozen, final_frozen, NICE_TO_HAVE,
        producer="api/bullpen.js (via fetch-slate.yml)",
    ))
    components.append(freeze_file_component(
        "WEATHER", os.path.join("data", "weather.json"), staging_frozen, final_frozen, NICE_TO_HAVE,
        producer="api/weather.js (via fetch-slate.yml)",
    ))
    effective_config_record = capture_effective_config(date, commit_sha)
    effective_config = freeze_record_component(
        "EFFECTIVE_CONFIG", effective_config_record, staging_frozen, final_frozen, "effective_config.json",
        REQUIRED, producer="lib/edgelab/snapshot.py:capture_effective_config",
    )
    components.append(effective_config)
    components.append(_rederive_component(effective_config, "PARK_FACTORS", required_status=NICE_TO_HAVE))
    components.append(build_referenced_component(
        "MODEL_EVALUATIONS", os.path.join("data", "edgelab", "model_evaluations", f"{date}.jsonl"),
        NICE_TO_HAVE, producer="lib/edgelab/model_evaluation.py",
    ))
    components.append(build_referenced_component(
        "RECOMMENDATIONS", os.path.join("data", "edgelab", "recommendations", f"{date}.jsonl"),
        NICE_TO_HAVE, producer="lib/edgelab/recommendations.py",
    ))
    components.append(_observations_component("MARKET_OBSERVATIONS", date, NICE_TO_HAVE))
    components.append(_pipeline_component("RISK_GATE_OUTPUT", "execution", date, REQUIRED, "scripts/risk_gate.py"))
    components.append(build_referenced_component(
        "EXECUTION_SLIP", os.path.join("data", f"execution_slip_{date}.json"), NICE_TO_HAVE,
        producer="scripts/risk_gate.py",
    ))
    components.append(_pipeline_component("VALIDATION_ARTIFACT", "validation", date, NICE_TO_HAVE, "scripts/validate_slate_final.py"))
    components.append(_pipeline_component("PROTECTION_ARTIFACT", "protection", date, NICE_TO_HAVE, "scripts/protect_slate.py"))
    components.append(not_applicable_component("SETTLEMENT", required_status=NICE_TO_HAVE, reason=REASON_POSTGAME_EXCLUDED))
    components.append(not_applicable_component("CLV", required_status=NICE_TO_HAVE, reason=REASON_POSTGAME_EXCLUDED))

    pricing_versions = {}
    if effective_config_record.get("f5PricingVersionCurrent"):
        pricing_versions["F5_ML"] = effective_config_record["f5PricingVersionCurrent"]

    completeness_status = derive_completeness_status(components)
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "snapshotId": ids.build_snapshot_id(STAGE_PRE_GAME_DECISION, date),
        "snapshotStage": STAGE_PRE_GAME_DECISION,
        "snapshotDate": date,
        "capturedAt": captured_at,
        "productionRunId": production_run_id,
        "workflowRunId": workflow_run_id,
        "productionCommitSha": None,  # see module docstring / docs: no upstream artifact records its own producing commit today
        "snapshotWriterCommitSha": commit_sha,
        "modelVersion": None,
        "pricingVersionsByFamily": pricing_versions,
        "rulesConfigVersion": effective_config_record.get("rulesConfigVersion"),
        "replayFidelityPotential": derive_replay_fidelity(completeness_status, None, commit_sha),
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
    return manifest, staging_root


def build_post_game_manifest(date, workflow_run_id=None, production_run_id=None):
    staging_root = tempfile.mkdtemp(prefix=".snapshot_staging_")
    commit_sha = _git_commit_sha()
    captured_at = ids.utc_now_iso()

    components = [
        build_referenced_component(
            "SETTLEMENT", os.path.join("data", "edgelab", "settlements", f"{date}.jsonl"),
            REQUIRED, producer="scripts/edgelab/settle_markets.py",
        ),
        build_referenced_component(
            "CLV", os.path.join("data", "edgelab", "clv_quotes", f"{date}.jsonl"),
            REQUIRED, producer="lib/edgelab/clv.py",
        ),
    ]
    completeness_status = derive_completeness_status(components)
    linked = [ids.build_snapshot_id(STAGE_PRE_GAME_DECISION, date)] if os.path.exists(manifest_path(STAGE_PRE_GAME_DECISION, date)) else []
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "snapshotId": ids.build_snapshot_id(STAGE_POST_GAME_SETTLEMENT, date),
        "snapshotStage": STAGE_POST_GAME_SETTLEMENT,
        "snapshotDate": date,
        "capturedAt": captured_at,
        "productionRunId": production_run_id,
        "workflowRunId": workflow_run_id,
        "productionCommitSha": None,
        "snapshotWriterCommitSha": commit_sha,
        "modelVersion": None,
        "pricingVersionsByFamily": {},
        "rulesConfigVersion": None,
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
    return manifest, staging_root


def build_closing_line_manifest(date, workflow_run_id=None, production_run_id=None):
    staging_root = tempfile.mkdtemp(prefix=".snapshot_staging_")
    commit_sha = _git_commit_sha()
    captured_at = ids.utc_now_iso()

    components = [_observations_component("MARKET_OBSERVATIONS", date, REQUIRED)]
    completeness_status = derive_completeness_status(components)
    linked = [ids.build_snapshot_id(STAGE_PRE_GAME_DECISION, date)] if os.path.exists(manifest_path(STAGE_PRE_GAME_DECISION, date)) else []
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "snapshotId": ids.build_snapshot_id(STAGE_CLOSING_LINE, date),
        "snapshotStage": STAGE_CLOSING_LINE,
        "snapshotDate": date,
        "capturedAt": captured_at,
        "productionRunId": production_run_id,
        "workflowRunId": workflow_run_id,
        "productionCommitSha": None,
        "snapshotWriterCommitSha": commit_sha,
        "modelVersion": None,
        "pricingVersionsByFamily": {},
        "rulesConfigVersion": None,
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
    return manifest, staging_root


_BUILDERS = {
    STAGE_PRE_GAME_DECISION: build_pre_game_manifest,
    STAGE_POST_GAME_SETTLEMENT: build_post_game_manifest,
    STAGE_CLOSING_LINE: build_closing_line_manifest,
}


# ── Write-once commit (item 8) ───────────────────────────────────────────

def _commit_snapshot(stage, date, candidate_manifest, staging_root):
    final_dir = snapshot_dir(stage, date)
    final_manifest_path = manifest_path(stage, date)
    staged_frozen = os.path.join(staging_root, "frozen")
    final_frozen = frozen_dir(stage, date)

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

    # Genuine conflict: preserve the existing snapshot untouched; stash the
    # candidate + its staged frozen bytes as diagnostic evidence alongside it.
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


def build_snapshot(stage: str, date: str, workflow_run_id=None, production_run_id=None):
    """
    Build and write-once-commit a snapshot manifest for (stage, date).
    Returns the _commit_snapshot() result dict: {"outcome": "created" |
    "noop_verified" | "conflict" | "existing_manifest_corrupted", ...}.
    Always cleans up its staging directory, whatever the outcome.
    """
    if stage not in VALID_STAGES:
        raise ValueError(f"unknown snapshotStage {stage!r}, must be one of {sorted(VALID_STAGES)}")
    manifest, staging_root = _BUILDERS[stage](date, workflow_run_id=workflow_run_id, production_run_id=production_run_id)
    try:
        return _commit_snapshot(stage, date, manifest, staging_root)
    finally:
        if os.path.isdir(staging_root):
            shutil.rmtree(staging_root, ignore_errors=True)


def classify_date(stage: str, date: str):
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
    manifest, staging_root = _BUILDERS[stage](date)
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


CLASSIFICATION_LABELS = {
    COMPLETE_FOR_PRODUCTION_REPLAY: "FULLY_SNAPSHOT_CAPABLE",
    PARTIAL_REPLAY: "PARTIAL",
    APPROXIMATE_ONLY: "APPROXIMATE_ONLY",
    MISSING_REQUIRED_INPUT: "NOT_RECONSTRUCTABLE",
}


# ── Replay-read interface (item 13 -- smallest possible) ─────────────────

def load_manifest(stage: str, date: str):
    path = manifest_path(stage, date)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def find_manifest_by_id(snapshot_id: str):
    """Scans data/edgelab/snapshots/*/*/manifest.json for a matching snapshotId.
    Manifests are small and few (~1/stage/day); a full index is not warranted
    at this milestone's data volume -- see docs/SNAPSHOT_ARCHITECTURE.md."""
    if not os.path.isdir(SNAPSHOTS_ROOT):
        return None
    for date_name in sorted(os.listdir(SNAPSHOTS_ROOT)):
        date_dir = os.path.join(SNAPSHOTS_ROOT, date_name)
        if not os.path.isdir(date_dir):
            continue
        for stage_name in sorted(os.listdir(date_dir)):
            candidate = os.path.join(date_dir, stage_name, "manifest.json")
            if not os.path.isfile(candidate):
                continue
            with open(candidate) as f:
                manifest = json.load(f)
            if manifest.get("snapshotId") == snapshot_id:
                return manifest
    return None


def list_components(manifest: dict):
    return manifest.get("components", [])


def completeness_report(manifest: dict):
    return {
        "snapshotId": manifest.get("snapshotId"),
        "snapshotStage": manifest.get("snapshotStage"),
        "snapshotDate": manifest.get("snapshotDate"),
        "completenessStatus": manifest.get("completenessStatus"),
        "replayFidelityPotential": manifest.get("replayFidelityPotential"),
        "missingComponents": manifest.get("missingComponents", []),
        "limitationReasons": manifest.get("limitationReasons", []),
    }


def verify_snapshot(manifest: dict):
    """
    Freshly re-verifies (never mutates the stored manifest): the
    manifest's own hash, and every component's contentHash against its
    live sourcePath (REFERENCED_IMMUTABLE) or snapshotPath (FROZEN_COPY).
    A REFERENCED_IMMUTABLE mismatch means the "believed immutable" source
    was in fact rewritten after capture -- reported here, never silently
    trusted (see module docstring).
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
    inputs (item 13)."""
    for component in manifest.get("components", []):
        if component["componentType"] == component_type and component.get("storageMode") == STORAGE_FROZEN_COPY:
            if component["availabilityStatus"] != AVAILABLE or not component.get("snapshotPath"):
                return None
            if not os.path.exists(component["snapshotPath"]):
                return None
            with open(component["snapshotPath"]) as f:
                return json.load(f)
    return None
