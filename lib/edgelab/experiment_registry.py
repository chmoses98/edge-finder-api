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

# Hardening pass item 3: the only keys a dict-shaped minimumSampleRequirement
# may use. Deliberately just these two (spec: "Do not build a large
# generalized sample DSL").
SAMPLE_REQUIREMENT_DIMENSIONS = frozenset({"independentGames", "independentDates"})


def _validate_minimum_sample_requirement(value) -> None:
    """
    Accepts EITHER a plain positive number (interpreted as a minimum
    independentGames count -- preserves the original, pre-hardening-pass
    shape) OR a dict with one or both keys from
    SAMPLE_REQUIREMENT_DIMENSIONS mapping to positive numbers. Anything
    else raises.
    """
    if isinstance(value, dict):
        if not value:
            raise ValueError("minimumSampleRequirement dict must declare at least one of independentGames/independentDates")
        unknown = set(value) - SAMPLE_REQUIREMENT_DIMENSIONS
        if unknown:
            raise ValueError(f"minimumSampleRequirement dict has unknown key(s) {sorted(unknown)} -- only {sorted(SAMPLE_REQUIREMENT_DIMENSIONS)} are supported")
        for key, n in value.items():
            if not isinstance(n, (int, float)) or isinstance(n, bool) or n <= 0:
                raise ValueError(f"minimumSampleRequirement[{key!r}] must be a positive number, got {n!r}")
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"minimumSampleRequirement must be a positive number or a dict of {sorted(SAMPLE_REQUIREMENT_DIMENSIONS)}, got {value!r}")


def _validate_candidate_identifiers(candidate_model_id, candidate_variant_id) -> None:
    """
    Hardening pass item 2: forbids declaring BOTH candidateModelId and
    candidateVariantId on the same experiment -- there is no
    clearly-defined need for a single experiment to carry two distinct
    candidate identities at once, and allowing both invites exactly the
    kind of ambiguous-registration mismatch this hardening pass exists
    to close off. A future experiment that genuinely needs both must
    document why and extend this check deliberately, not fall through
    it by accident.
    """
    if candidate_model_id is not None and candidate_variant_id is not None:
        raise ValueError(
            "An experiment must not declare both candidateModelId and candidateVariantId simultaneously -- "
            "exactly one candidate identity (or neither, for a control-only experiment) is permitted."
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

    `pit_requirements`: {dataFamilyKey: role}, role one of
    lib.edgelab.pit_provenance.PIT_ROLES (PREDICTIVE_INPUT/
    EVALUATION_TARGET/AUXILIARY_METADATA). Validated against BOTH manifest
    existence AND role/evidence-level compatibility
    (pit_provenance.validate_pit_requirements) -- an experiment can
    never register a PIT-incompatible predictive input at all, not
    merely an unlisted one.

    `minimum_sample_requirement`: a positive number (interpreted as a
    minimum independentGames count) or a dict with one or both of
    {"independentGames", "independentDates"} -> positive number.
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
    pit_provenance.validate_pit_requirements(pit_requirements, evidence_level)
    _validate_minimum_sample_requirement(minimum_sample_requirement)
    _validate_candidate_identifiers(candidate_model_id, candidate_variant_id)
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
        "pitRequirements": dict(pit_requirements),
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
    pit_provenance.validate_pit_requirements(definition["pitRequirements"], definition["evidenceLevel"])
    _validate_minimum_sample_requirement(definition["minimumSampleRequirement"])
    _validate_candidate_identifiers(definition.get("candidateModelId"), definition.get("candidateVariantId"))
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
