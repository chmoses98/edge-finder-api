"""
lib/edgelab/candidate_identity.py
=====================================
Research Lab Milestone 0A: candidate-variant identity and execution
contract -- the generalized way a future experiment names "control model
+ some declared change" WITHOUT modifying production code.

This milestone does NOT implement any new baseball variant (spec: "For
0A, you do NOT need to implement new baseball variants"). It builds the
registration contract a future variant must satisfy, and it structurally
enforces isolation from production: `productionCodePathsModified` must
always be an empty list for a candidate registered through this module
-- validate_candidate_registration raises if it is ever non-empty,
because a candidate is by definition a RESEARCH artifact; a change to
actual production code is not a "candidate variant", it is a production
change and belongs to an entirely different (human) review process.
"""

import json
import os

from lib.edgelab import research_lab_ids as rlids

CANDIDATE_VARIANTS_ROOT = os.path.join("data", "edgelab", "candidate_variants")

CHANGE_TYPE_FEATURE_ADDITION = "FEATURE_ADDITION"
CHANGE_TYPE_FEATURE_REMOVAL = "FEATURE_REMOVAL"
CHANGE_TYPE_DISTRIBUTION_CHANGE = "DISTRIBUTION_CHANGE"
CHANGE_TYPE_PARAMETER_CHANGE = "PARAMETER_CHANGE"
CHANGE_TYPE_OTHER = "OTHER"

CHANGE_TYPES = frozenset({
    CHANGE_TYPE_FEATURE_ADDITION, CHANGE_TYPE_FEATURE_REMOVAL,
    CHANGE_TYPE_DISTRIBUTION_CHANGE, CHANGE_TYPE_PARAMETER_CHANGE, CHANGE_TYPE_OTHER,
})

REQUIRED_FIELDS = (
    "candidateVariantId", "name", "baseControlModelId", "changeDescription", "changeType",
    "implementationRef", "productionCodePathsModified", "registeredAt", "description",
)


def build_candidate_registration(
    *, name, base_control_model_id, change_description, change_type,
    implementation_ref, description="", registered_at=None,
):
    """
    `implementation_ref`: a module path / function name / doc reference
    describing where the candidate's logic lives (or the literal string
    "NOT_YET_IMPLEMENTED" for a 0A-style placeholder registration that
    only reserves identity ahead of real implementation work).
    `productionCodePathsModified` is always [] -- this contract has no
    parameter to set it otherwise; a real production change is not a
    candidate variant (see module docstring).
    """
    if change_type not in CHANGE_TYPES:
        raise ValueError(f"change_type must be one of {sorted(CHANGE_TYPES)}, got {change_type!r}")
    if not base_control_model_id:
        raise ValueError("base_control_model_id is required -- a candidate must declare which control it varies from")

    candidate_variant_id = rlids.build_candidate_variant_id(name, base_control_model_id, change_description)
    if registered_at is None:
        from datetime import datetime, timezone
        registered_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "candidateVariantId": candidate_variant_id,
        "name": name,
        "baseControlModelId": base_control_model_id,
        "changeDescription": change_description,
        "changeType": change_type,
        "implementationRef": implementation_ref,
        "productionCodePathsModified": [],
        "registeredAt": registered_at,
        "description": description,
    }


def validate_candidate_registration(registration: dict) -> None:
    missing = [f for f in REQUIRED_FIELDS if f not in registration]
    if missing:
        raise ValueError(f"Candidate registration missing required fields: {missing}")
    if registration["changeType"] not in CHANGE_TYPES:
        raise ValueError(f"changeType must be one of {sorted(CHANGE_TYPES)}")
    if registration["productionCodePathsModified"] != []:
        raise ValueError(
            "A candidate variant must never declare modified production code paths -- "
            "productionCodePathsModified must be []. A real production change is not a "
            "candidate variant; it belongs to a separate production-review process."
        )


def _path_for(candidate_variant_id: str) -> str:
    return os.path.join(CANDIDATE_VARIANTS_ROOT, f"{candidate_variant_id}.json")


def register_candidate(registration: dict) -> str:
    """Write-once persistence, same discipline as lib.edgelab.control_identity.register_control."""
    validate_candidate_registration(registration)
    path = _path_for(registration["candidateVariantId"])
    payload = json.dumps(registration, sort_keys=True, indent=2) + "\n"
    if os.path.exists(path):
        with open(path) as f:
            existing = f.read()
        if existing != payload:
            raise ValueError(
                f"Candidate variant {registration['candidateVariantId']!r} is already registered with "
                f"different content -- candidate identity is write-once."
            )
        return path
    os.makedirs(CANDIDATE_VARIANTS_ROOT, exist_ok=True)
    with open(path, "w") as f:
        f.write(payload)
    return path


def load_candidate(candidate_variant_id: str) -> dict:
    path = _path_for(candidate_variant_id)
    if not os.path.exists(path):
        raise FileNotFoundError(f"No registered candidate variant {candidate_variant_id!r} at {path}")
    with open(path) as f:
        return json.load(f)


def list_registered_candidate_ids():
    if not os.path.isdir(CANDIDATE_VARIANTS_ROOT):
        return []
    return sorted(fn[:-5] for fn in os.listdir(CANDIDATE_VARIANTS_ROOT) if fn.endswith(".json"))
