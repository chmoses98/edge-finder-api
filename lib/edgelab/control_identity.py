"""
lib/edgelab/control_identity.py
===================================
Research Lab Milestone 0A: immutable control-model identity registration.

WHY THIS EXISTS (spec's "important existing limitation" section):
historical ModelEvaluation metadata already carries modelCommitSha/
modelConfigVersion/pipelineRunId/modelSource/artifactSource, but
modelVersion is null for every real record today (no script anywhere
captures a model-ALGORITHM version -- see
docs/EDGELAB_EVALUATION_METADATA.md section 2/10 and
docs/EDGELAB_MODEL_EVALUATION.md section 7). This module does NOT
pretend that gap is closed. It gives every NEW research experiment a way
to register an explicit, honestly-scoped control identity going
forward, and it gives a HISTORICAL control (one being reconstructed
after the fact from existing ModelEvaluation rows) an explicit
IDENTITY_CONFIDENCE tier so a report can never silently claim more
precision than the underlying data proves.

A control registration is WRITE-ONCE per controlModelId (deterministic,
see lib.edgelab.research_lab_ids.build_control_model_id) -- re-registering
with identical inputs is a no-op; re-registering the same name with
DIFFERENT commit/config inputs produces a genuinely different id rather
than silently overwriting the old one, so no experiment that already
cited the old id is ever retroactively repointed at different code.
"""

import json
import os

from lib.edgelab import research_lab_ids as rlids

CONTROL_MODELS_ROOT = os.path.join("data", "edgelab", "control_models")

# How confidently this registration's identity fields describe the ACTUAL
# algorithm that produced a given historical record -- never let a
# reconstructed-after-the-fact control claim EXACT confidence.
IDENTITY_EXACT = "EXACT"                        # registered live, at the moment the control was defined/run
IDENTITY_HISTORICAL_AMBIGUOUS = "HISTORICAL_AMBIGUOUS"  # reconstructed after the fact; commit/config known but algorithm identity not independently provable
IDENTITY_HISTORICAL_UNKNOWN = "HISTORICAL_UNKNOWN"      # reconstructed after the fact; even commit/config is unknown/null in the source records

IDENTITY_CONFIDENCE_LEVELS = frozenset({IDENTITY_EXACT, IDENTITY_HISTORICAL_AMBIGUOUS, IDENTITY_HISTORICAL_UNKNOWN})

REQUIRED_FIELDS = (
    "controlModelId", "name", "sourceGitCommitSha", "modelConfigVersion", "configFingerprint",
    "probabilityAdapterIdentity", "modelEngineFamily", "registeredAt", "requiredInputProvenance",
    "identityConfidence", "description",
)


def build_control_registration(
    *, name, source_git_commit_sha, model_config_version, config_fingerprint,
    probability_adapter_identity, model_engine_family, required_input_provenance,
    identity_confidence, description="", registered_at=None,
):
    """
    Builds (does not persist) a control-model registration dict.

    `required_input_provenance`: list of lib.edgelab.pit_provenance
    manifest keys this control's inputs depend on -- validated eagerly
    (raises KeyError for an unlisted input, never silently accepted).

    `identity_confidence`: one of IDENTITY_CONFIDENCE_LEVELS. EXACT is
    only appropriate when this registration is being created AT THE TIME
    the control is defined (a genuinely live registration); anything
    reconstructing a control identity for PAST records must use one of
    the HISTORICAL_* tiers, never EXACT -- validated below.
    """
    from lib.edgelab import pit_provenance

    if identity_confidence not in IDENTITY_CONFIDENCE_LEVELS:
        raise ValueError(f"identity_confidence must be one of {sorted(IDENTITY_CONFIDENCE_LEVELS)}, got {identity_confidence!r}")
    pit_provenance.assert_known_inputs(required_input_provenance)

    control_model_id = rlids.build_control_model_id(name, source_git_commit_sha, config_fingerprint)
    if registered_at is None:
        from datetime import datetime, timezone
        registered_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "controlModelId": control_model_id,
        "name": name,
        "sourceGitCommitSha": source_git_commit_sha,
        "modelConfigVersion": model_config_version,
        "configFingerprint": config_fingerprint,
        "probabilityAdapterIdentity": probability_adapter_identity,
        "modelEngineFamily": model_engine_family,
        "registeredAt": registered_at,
        "requiredInputProvenance": list(required_input_provenance),
        "identityConfidence": identity_confidence,
        "description": description,
    }


def validate_control_registration(registration: dict) -> None:
    missing = [f for f in REQUIRED_FIELDS if f not in registration]
    if missing:
        raise ValueError(f"Control registration missing required fields: {missing}")
    if registration["identityConfidence"] not in IDENTITY_CONFIDENCE_LEVELS:
        raise ValueError(f"identityConfidence must be one of {sorted(IDENTITY_CONFIDENCE_LEVELS)}")
    from lib.edgelab import pit_provenance
    pit_provenance.assert_known_inputs(registration["requiredInputProvenance"])


def _path_for(control_model_id: str) -> str:
    return os.path.join(CONTROL_MODELS_ROOT, f"{control_model_id}.json")


def register_control(registration: dict) -> str:
    """
    Write-once persistence: if a file for this controlModelId already
    exists, its content must match byte-for-byte (same registration
    content) or this raises -- a control identity is never silently
    overwritten with different content under the same id. Returns the
    path written (or the existing path, for a no-op re-registration).
    """
    validate_control_registration(registration)
    path = _path_for(registration["controlModelId"])
    payload = json.dumps(registration, sort_keys=True, indent=2) + "\n"
    if os.path.exists(path):
        with open(path) as f:
            existing = f.read()
        if existing != payload:
            raise ValueError(
                f"Control model {registration['controlModelId']!r} is already registered with different "
                f"content -- control identity is write-once. Register a new control (a different name/commit/"
                f"config necessarily produces a different controlModelId) instead of overwriting."
            )
        return path
    os.makedirs(CONTROL_MODELS_ROOT, exist_ok=True)
    with open(path, "w") as f:
        f.write(payload)
    return path


def load_control(control_model_id: str) -> dict:
    path = _path_for(control_model_id)
    if not os.path.exists(path):
        raise FileNotFoundError(f"No registered control model {control_model_id!r} at {path}")
    with open(path) as f:
        return json.load(f)


def list_registered_control_ids():
    if not os.path.isdir(CONTROL_MODELS_ROOT):
        return []
    return sorted(fn[:-5] for fn in os.listdir(CONTROL_MODELS_ROOT) if fn.endswith(".json"))
