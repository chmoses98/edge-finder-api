"""
lib/edgelab/experiment_registry.py
======================================
Research Lab Milestone 0A: the canonical experiment registry --
auditable, version-controlled, deterministic JSON experiment
definitions with stable identifiers (MLB-RSCH-0001, ...), per the spec's
explicit preference ("Do not overengineer an external database if
repository-native deterministic JSON/YAML/JSONL plus validation is more
appropriate").

An experiment definition, once registered, is WRITE-ONCE (same
discipline as lib.edgelab.control_identity/candidate_identity) --
re-registering the identical experimentId with different content raises,
never silently overwrites. This is deliberate: spec section
"EXPERIMENT REGISTRY" requires "enough information to make the intended
analysis immutable/reproducible" -- a confirmatory experiment whose
eligibility/holdout/primary-metric could be quietly edited after seeing
results would defeat the entire confirmatory-vs-exploratory distinction
this milestone exists to make explicit (see EXPERIMENT_TYPE_* below).
Only a DISPOSITION is allowed to change over an experiment's life, and
that lives on separately-written ExperimentReport records (see
lib.edgelab.experiment_report), never on the registration itself.
"""

import json
import os
import re

from lib.edgelab import control_identity
from lib.edgelab import dispositions
from lib.edgelab import evidence_levels
from lib.edgelab import pit_provenance

EXPERIMENTS_ROOT = os.path.join("data", "edgelab", "experiments")

EXPERIMENT_ID_PREFIX = "MLB-RSCH-"
_EXPERIMENT_ID_RE = re.compile(r"^MLB-RSCH-(\d{4,})$")

EXPERIMENT_TYPE_EXPLORATORY = "EXPLORATORY"
EXPERIMENT_TYPE_CONFIRMATORY = "CONFIRMATORY"
EXPERIMENT_TYPES = frozenset({EXPERIMENT_TYPE_EXPLORATORY, EXPERIMENT_TYPE_CONFIRMATORY})

# False-discovery / multiple-testing handling an experiment declares.
# CONFIRMATORY experiments should normally use NONE_SINGLE_HYPOTHESIS
# (a fixed, pre-registered single test needs no multiple-comparisons
# correction); EXPLORATORY experiments that screen many
# segments/hypotheses at once should declare one of the correction
# methods, never NONE_SINGLE_HYPOTHESIS.
FDR_NONE_SINGLE_HYPOTHESIS = "NONE_SINGLE_HYPOTHESIS"
FDR_BENJAMINI_HOCHBERG = "BENJAMINI_HOCHBERG"
FDR_BONFERRONI = "BONFERRONI"
FDR_OTHER_DOCUMENTED = "OTHER_DOCUMENTED"
FALSE_DISCOVERY_HANDLING_OPTIONS = frozenset({
    FDR_NONE_SINGLE_HYPOTHESIS, FDR_BENJAMINI_HOCHBERG, FDR_BONFERRONI, FDR_OTHER_DOCUMENTED,
})

REQUIRED_FIELDS = (
    "experimentId", "title", "hypothesis", "researchQuestion", "registeredAt", "owner",
    "controlModelId", "evidenceLevel", "targetPopulation", "marketFamilies",
    "eligibilityCriteria", "exclusionCriteria", "predictionCheckpoints", "primaryMetric",
    "secondaryMetrics", "expectedDirection", "chronologicalSplitPolicy", "minimumSampleRequirement",
    "clusteringUnit", "permittedParameterVariants", "experimentType", "falseDiscoveryHandling",
    "pitRequirements", "status", "notes",
)


def next_experiment_id() -> str:
    """
    Deterministic given the current on-disk registry state (never
    wall-clock/random) -- the next unused MLB-RSCH-NNNN sequence number,
    one past the highest already-registered id. Two callers racing on an
    empty/stale registry can still collide (register_experiment's
    write-once check catches that as a loud failure, never a silent
    overwrite) -- this function itself makes no filesystem-locking
    claim, matching lib.edgelab.storage's own documented same-host-race
    caveat.
    """
    existing = list_experiment_ids()
    max_seq = 0
    for exp_id in existing:
        m = _EXPERIMENT_ID_RE.match(exp_id)
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return f"{EXPERIMENT_ID_PREFIX}{max_seq + 1:04d}"


def build_experiment_definition(
    *, title, hypothesis, research_question, owner, control_model_id, evidence_level,
    target_population, market_families, eligibility_criteria, exclusion_criteria,
    prediction_checkpoints, primary_metric, secondary_metrics, chronological_split_policy,
    minimum_sample_requirement, clustering_unit, experiment_type, false_discovery_handling,
    pit_requirements, experiment_id=None, expected_direction=None, permitted_parameter_variants=None,
    candidate_model_id=None, candidate_variant_id=None, registered_at=None, notes="",
):
    """
    Builds (does not persist) an experiment definition dict. Every field
    the spec's "EXPERIMENT REGISTRY" section lists is required except
    the two explicitly-optional ones (expectedDirection -- only
    meaningful for a pre-registered directional hypothesis;
    permittedParameterVariants -- only meaningful when a search space is
    genuinely being declared). `status` starts at REGISTERED, distinct
    from any evaluation disposition (see lib.edgelab.dispositions) --
    an experiment's registration status and its evaluation disposition
    are deliberately different fields on different objects (this
    definition vs. a later ExperimentReport).
    """
    evidence_levels.validate_evidence_level(evidence_level)
    if experiment_type not in EXPERIMENT_TYPES:
        raise ValueError(f"experiment_type must be one of {sorted(EXPERIMENT_TYPES)}, got {experiment_type!r}")
    if false_discovery_handling not in FALSE_DISCOVERY_HANDLING_OPTIONS:
        raise ValueError(f"false_discovery_handling must be one of {sorted(FALSE_DISCOVERY_HANDLING_OPTIONS)}")
    if experiment_type == EXPERIMENT_TYPE_EXPLORATORY and false_discovery_handling == FDR_NONE_SINGLE_HYPOTHESIS:
        raise ValueError(
            "An EXPLORATORY experiment (which by definition may screen many hypotheses/segments) must not "
            "declare falseDiscoveryHandling=NONE_SINGLE_HYPOTHESIS -- declare an explicit correction method "
            "(BENJAMINI_HOCHBERG/BONFERRONI/OTHER_DOCUMENTED)."
        )
    pit_provenance.assert_known_inputs(pit_requirements)
    if not control_identity_is_registered(control_model_id):
        raise ValueError(
            f"controlModelId {control_model_id!r} is not a registered control (lib.edgelab.control_identity) -- "
            f"register the control before registering an experiment against it."
        )

    experiment_id = experiment_id or next_experiment_id()
    if registered_at is None:
        from datetime import datetime, timezone
        registered_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "experimentId": experiment_id,
        "title": title,
        "hypothesis": hypothesis,
        "researchQuestion": research_question,
        "registeredAt": registered_at,
        "owner": owner,
        "controlModelId": control_model_id,
        "candidateModelId": candidate_model_id,
        "candidateVariantId": candidate_variant_id,
        "evidenceLevel": evidence_level,
        "targetPopulation": target_population,
        "marketFamilies": list(market_families),
        "eligibilityCriteria": list(eligibility_criteria),
        "exclusionCriteria": list(exclusion_criteria),
        "predictionCheckpoints": list(prediction_checkpoints),
        "primaryMetric": primary_metric,
        "secondaryMetrics": list(secondary_metrics),
        "expectedDirection": expected_direction,
        "chronologicalSplitPolicy": chronological_split_policy,
        "minimumSampleRequirement": minimum_sample_requirement,
        "clusteringUnit": clustering_unit,
        "permittedParameterVariants": list(permitted_parameter_variants or []),
        "experimentType": experiment_type,
        "falseDiscoveryHandling": false_discovery_handling,
        "pitRequirements": list(pit_requirements),
        "status": "REGISTERED",
        "notes": notes,
    }


def control_identity_is_registered(control_model_id: str) -> bool:
    return control_model_id in control_identity.list_registered_control_ids()


def validate_experiment_definition(definition: dict) -> None:
    missing = [f for f in REQUIRED_FIELDS if f not in definition]
    if missing:
        raise ValueError(f"Experiment definition missing required fields: {missing}")
    if not _EXPERIMENT_ID_RE.match(definition["experimentId"]):
        raise ValueError(f"experimentId {definition['experimentId']!r} does not match {EXPERIMENT_ID_PREFIX}NNNN")
    evidence_levels.validate_evidence_level(definition["evidenceLevel"])
    if definition["experimentType"] not in EXPERIMENT_TYPES:
        raise ValueError(f"experimentType must be one of {sorted(EXPERIMENT_TYPES)}")
    if definition["falseDiscoveryHandling"] not in FALSE_DISCOVERY_HANDLING_OPTIONS:
        raise ValueError(f"falseDiscoveryHandling must be one of {sorted(FALSE_DISCOVERY_HANDLING_OPTIONS)}")
    pit_provenance.assert_known_inputs(definition["pitRequirements"])
    if definition["status"] == dispositions.PRODUCTION:
        raise dispositions.ProductionDispositionForbiddenError(
            "An experiment definition's status field must never be PRODUCTION -- see lib.edgelab.dispositions."
        )


def _path_for(experiment_id: str) -> str:
    return os.path.join(EXPERIMENTS_ROOT, f"{experiment_id}.json")


def register_experiment(definition: dict) -> str:
    """Write-once persistence -- see module docstring."""
    validate_experiment_definition(definition)
    path = _path_for(definition["experimentId"])
    payload = json.dumps(definition, sort_keys=True, indent=2) + "\n"
    if os.path.exists(path):
        with open(path) as f:
            existing = f.read()
        if existing != payload:
            raise ValueError(
                f"Experiment {definition['experimentId']!r} is already registered with different content -- "
                f"experiment definitions are write-once. Register a new experiment id for a materially "
                f"different design instead of editing this one in place."
            )
        return path
    os.makedirs(EXPERIMENTS_ROOT, exist_ok=True)
    with open(path, "w") as f:
        f.write(payload)
    return path


def load_experiment(experiment_id: str) -> dict:
    path = _path_for(experiment_id)
    if not os.path.exists(path):
        raise FileNotFoundError(f"No registered experiment {experiment_id!r} at {path}")
    with open(path) as f:
        return json.load(f)


def list_experiment_ids():
    if not os.path.isdir(EXPERIMENTS_ROOT):
        return []
    return sorted(fn[:-5] for fn in os.listdir(EXPERIMENTS_ROOT) if fn.endswith(".json"))
