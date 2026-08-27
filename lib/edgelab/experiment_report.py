"""
lib/edgelab/experiment_report.py
====================================
Research Lab Milestone 0A: the standard research report contract (spec
section "STANDARD RESEARCH REPORT CONTRACT") -- the one function every
future experiment's evaluation script should call to produce its final,
persisted, auditable output.

PRODUCTION PROMOTION FIREWALL: build_experiment_report() calls
lib.edgelab.dispositions.assign_disposition() on its `disposition`
argument -- this raises ProductionDispositionForbiddenError
unconditionally for PRODUCTION, with no override parameter anywhere in
this function's signature capable of bypassing that. There is no code
path in this milestone that can write a report claiming disposition ==
PRODUCTION. `productionBehaviorChanged` is hardcoded to False and is not
a parameter this function accepts -- it cannot be set to anything else by
a caller, by construction (spec: "The report must never silently claim
stronger evidence than the underlying data warrants" applies here too:
a Research Lab report can never claim to have changed production).

MILESTONE 0A HARDENING PASS (4 governance gaps closed, narrow scope --
no redesign, no new research, no production-behavior change):

1. PIT COMPATIBILITY, NOT JUST EXISTENCE. An experiment's
   pitRequirements now declare a ROLE per input
   (lib.edgelab.pit_provenance.PIT_ROLES) and are validated for
   role/evidence-level compatibility at registration time
   (lib.edgelab.experiment_registry) AND re-checked here for any
   favorable disposition (see _validate_favorable_disposition_gates) --
   defense in depth, so a later relaxation elsewhere can never silently
   let an incompatible input through into a promotable result.
2. EXPERIMENT/CONTROL/CANDIDATE CONSISTENCY. build_experiment_report()
   now calls _validate_registration_consistency(): the supplied control
   registration must match experiment.controlModelId; if the experiment
   declares a candidateVariantId, a matching, correctly-based candidate
   registration is REQUIRED; a control-only experiment must not receive
   an unexpected candidate registration either.
3. FAVORABLE DISPOSITIONS ARE GATED ON OBJECTIVE VALIDITY, NOT A
   "WINNER DETECTOR". SHADOW_CANDIDATE/PROMOTION_CANDIDATE now
   structurally fail when the sample requirement isn't met, a blocking
   leakage warning is present, the primary evaluation is empty/missing,
   or a PIT requirement is incompatible with the report's own evidence
   level. Whether a real metric improvement is GOOD ENOUGH remains a
   human/research-review judgment -- this only blocks objectively
   invalid promotions, never picks a "winner."
4. NO EVIDENCE SELF-UPGRADE. A report's evidence_level can never exceed
   the experiment's own registered evidenceLevel (rank comparison via
   lib.edgelab.evidence_levels) -- a genuine E2 -> E3 -> E4 progression
   requires a new experiment registration/stage, never a report simply
   claiming a stronger level than what was preregistered.
"""

import json
import os

from lib.edgelab import candidate_identity
from lib.edgelab import control_identity
from lib.edgelab import dispositions
from lib.edgelab import evidence_levels
from lib.edgelab import pit_provenance
from lib.edgelab import research_lab_ids as rlids

EXPERIMENT_REPORTS_ROOT = os.path.join("data", "edgelab", "experiment_reports")

REQUIRED_REPORT_FIELDS = (
    "experimentReportId", "experimentId", "controlModelId", "candidateId", "evidenceLevel",
    "experimentType", "generatedAt", "trainDateRange", "validationDateRange", "holdoutDateRange",
    "evaluationDateRange", "nRows", "nIndependentGames", "nIndependentDates", "nPlayers",
    "missingDataSummary", "unpairedObservationSummary", "pitProvenanceStatus", "pitLimitations",
    "pitRequirements", "primaryMetric", "primaryResult", "pairedDeltaVsControl", "uncertainty",
    "secondaryMetrics", "marketEconomicMetrics", "falseDiscoveryTreatment", "minimumSampleRequirement",
    "sampleRequirementMet", "disposition", "methodologicalLimitations", "leakageWarnings",
    "blockingLeakageWarnings", "overfittingWarnings", "productionBehaviorChanged",
)


def _validate_registration_consistency(experiment: dict, control_registration: dict, candidate_registration) -> None:
    """
    Hardening pass item 2. Raises ValueError for any mismatch between
    the registered experiment design and the registrations actually
    supplied to build a report against it. Also runs each registration
    through its own module's structural validator first, so an
    arbitrary/malformed dict can never be used in place of a real
    registration (lib.edgelab.control_identity.validate_control_registration /
    lib.edgelab.candidate_identity.validate_candidate_registration --
    both raise ValueError on a structurally invalid dict).

    Scope note: this checks `candidateVariantId` consistency (the
    candidate_identity contract this milestone actually builds).
    `candidateModelId` (the alternate, less-specified experiment field
    for a candidate that isn't expressed as a candidate_identity
    registration) has no equivalent registration object to cross-check
    against in this milestone and is intentionally left unvalidated
    here -- lib.edgelab.experiment_registry already forbids declaring
    both fields at once, so at most one applies per experiment.
    """
    control_identity.validate_control_registration(control_registration)
    if control_registration["controlModelId"] != experiment["controlModelId"]:
        raise ValueError(
            f"control_registration.controlModelId ({control_registration['controlModelId']!r}) does not match "
            f"experiment.controlModelId ({experiment['controlModelId']!r})."
        )

    experiment_candidate_variant_id = experiment.get("candidateVariantId")
    if experiment_candidate_variant_id:
        if candidate_registration is None:
            raise ValueError(
                f"experiment {experiment['experimentId']!r} declares candidateVariantId="
                f"{experiment_candidate_variant_id!r} -- a matching candidate_registration is required."
            )
        candidate_identity.validate_candidate_registration(candidate_registration)
        if candidate_registration["candidateVariantId"] != experiment_candidate_variant_id:
            raise ValueError(
                f"candidate_registration.candidateVariantId ({candidate_registration['candidateVariantId']!r}) "
                f"does not match experiment.candidateVariantId ({experiment_candidate_variant_id!r})."
            )
        if candidate_registration["baseControlModelId"] != experiment["controlModelId"]:
            raise ValueError(
                f"candidate_registration.baseControlModelId ({candidate_registration['baseControlModelId']!r}) "
                f"does not match experiment.controlModelId ({experiment['controlModelId']!r}) -- this candidate "
                f"was registered against a different control."
            )
    elif candidate_registration is not None:
        raise ValueError(
            f"experiment {experiment['experimentId']!r} is control-only (no candidateVariantId) but a "
            f"candidate_registration was supplied -- omit it, or register the experiment with a candidateVariantId."
        )


def _sample_requirement_met(minimum_requirement, independent_games, independent_dates):
    """
    Hardening pass item 3 (dual-dimension support, no generalized DSL).
    `minimum_requirement`: a plain positive number (independentGames,
    the original shape) or a dict with one or both of
    {"independentGames", "independentDates"} -- see
    lib.edgelab.experiment_registry.SAMPLE_REQUIREMENT_DIMENSIONS. ALL
    declared dimensions must be met (never "any one of"). Returns a
    plain bool; never None given minimum_requirement is always present
    on a validly-registered experiment.
    """
    if isinstance(minimum_requirement, dict):
        checks = []
        if "independentGames" in minimum_requirement:
            checks.append(independent_games is not None and independent_games >= minimum_requirement["independentGames"])
        if "independentDates" in minimum_requirement:
            checks.append(independent_dates is not None and independent_dates >= minimum_requirement["independentDates"])
        return bool(checks) and all(checks)
    return independent_games is not None and independent_games >= minimum_requirement


def build_experiment_report(
    *, experiment, control_registration, candidate_registration=None, pairing_result,
    probability_evaluation, disposition, evidence_level, train_date_range=None,
    validation_date_range=None, holdout_date_range=None, evaluation_date_range=None,
    n_players=None, pit_provenance_status="SEE_EXPERIMENT_PIT_REQUIREMENTS", pit_limitations=(),
    market_economic_metrics=None, false_discovery_treatment=None, methodological_limitations=(),
    leakage_warnings=(), blocking_leakage_warnings=(), overfitting_warnings=(),
    secondary_metrics=None, generated_at=None,
):
    """
    Assembles (does not persist -- see write_experiment_report) a
    standard experiment report from already-computed pieces: an
    experiment definition (lib.edgelab.experiment_registry), a control
    registration (lib.edgelab.control_identity), an optional candidate
    registration (lib.edgelab.candidate_identity), a paired-observation
    result (lib.edgelab.paired_evaluation.pair_eligible_observations),
    and its probability evaluation
    (lib.edgelab.paired_evaluation.evaluate_probability_model_pair).

    `disposition` is validated through
    lib.edgelab.dispositions.assign_disposition() -- PRODUCTION always
    raises here, unconditionally (see module docstring).

    `evidence_level` can never exceed the experiment's own registered
    evidenceLevel (item 4 -- no evidence self-upgrade); raises
    ValueError otherwise.

    `control_registration`/`candidate_registration` are cross-checked
    against `experiment` for identity consistency (item 2 -- see
    _validate_registration_consistency).

    `blocking_leakage_warnings`: the subset of concerns serious enough
    to structurally block SHADOW_CANDIDATE/PROMOTION_CANDIDATE (item 3)
    -- distinct from `leakage_warnings`, which is purely informational/
    advisory and never gates a disposition on its own.

    `false_discovery_treatment` defaults to the experiment's own
    registered `falseDiscoveryHandling` when not explicitly overridden,
    so a report can never silently omit how multiple-testing was (or
    deliberately wasn't) handled.
    """
    evidence_levels.validate_evidence_level(evidence_level)
    if evidence_levels.rank(evidence_level) > evidence_levels.rank(experiment["evidenceLevel"]):
        raise ValueError(
            f"Report evidenceLevel {evidence_level!r} exceeds experiment {experiment['experimentId']!r}'s "
            f"registered evidenceLevel {experiment['evidenceLevel']!r} -- a report can never self-upgrade "
            f"evidence beyond what the experiment's design was preregistered to support. A genuine E2 -> E3 -> "
            f"E4 progression requires a new experiment registration/stage, not a stronger claim on this report."
        )
    safe_disposition = dispositions.assign_disposition(disposition)
    _validate_registration_consistency(experiment, control_registration, candidate_registration)

    if generated_at is None:
        from datetime import datetime, timezone
        generated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    control_model_id = control_registration["controlModelId"]
    candidate_id = candidate_registration["candidateVariantId"] if candidate_registration else None
    experiment_report_id = rlids.build_experiment_report_id(experiment["experimentId"], control_model_id, candidate_id, generated_at)

    if false_discovery_treatment is None:
        false_discovery_treatment = experiment.get("falseDiscoveryHandling")

    sample_status = probability_evaluation.get("sampleSizeStatus", {})
    minimum_requirement = experiment.get("minimumSampleRequirement")
    n_independent_games = probability_evaluation.get("independentGames")
    n_independent_dates = probability_evaluation.get("independentDates")
    sample_requirement_met = _sample_requirement_met(minimum_requirement, n_independent_games, n_independent_dates)

    report = {
        "experimentReportId": experiment_report_id,
        "experimentId": experiment["experimentId"],
        "controlModelId": control_model_id,
        "candidateId": candidate_id,
        "evidenceLevel": evidence_level,
        "experimentType": experiment.get("experimentType"),
        "generatedAt": generated_at,
        "trainDateRange": train_date_range,
        "validationDateRange": validation_date_range,
        "holdoutDateRange": holdout_date_range,
        "evaluationDateRange": evaluation_date_range,
        "nRows": probability_evaluation.get("n"),
        "nIndependentGames": n_independent_games,
        "nIndependentDates": n_independent_dates,
        "nPlayers": n_players if n_players is not None else probability_evaluation.get("playerGames"),
        "missingDataSummary": {
            "droppedForMissingProbability": probability_evaluation.get("droppedForMissingProbability"),
            "droppedForOutcomeMismatch": probability_evaluation.get("droppedForOutcomeMismatch"),
        },
        "unpairedObservationSummary": {
            "nControlOnly": pairing_result.get("nControlOnly"),
            "nCandidateOnly": pairing_result.get("nCandidateOnly"),
            "nControlDuplicates": pairing_result.get("nControlDuplicates"),
            "nCandidateDuplicates": pairing_result.get("nCandidateDuplicates"),
        },
        "pitProvenanceStatus": pit_provenance_status,
        "pitLimitations": list(pit_limitations),
        "pitRequirements": dict(experiment.get("pitRequirements") or {}),
        "primaryMetric": experiment.get("primaryMetric"),
        "primaryResult": {
            "control": probability_evaluation.get("control"),
            "candidate": probability_evaluation.get("candidate"),
        },
        "pairedDeltaVsControl": probability_evaluation.get("pairedDelta"),
        "uncertainty": probability_evaluation.get("pairedDeltaConfidenceInterval"),
        "secondaryMetrics": secondary_metrics or {},
        "marketEconomicMetrics": market_economic_metrics,
        "falseDiscoveryTreatment": false_discovery_treatment,
        "minimumSampleRequirement": minimum_requirement,
        "sampleRequirementMet": sample_requirement_met,
        "disposition": safe_disposition,
        "sampleSizeStatus": sample_status,
        "methodologicalLimitations": list(methodological_limitations),
        "leakageWarnings": list(leakage_warnings),
        "blockingLeakageWarnings": list(blocking_leakage_warnings),
        "overfittingWarnings": list(overfitting_warnings),
        "productionBehaviorChanged": False,
    }
    validate_experiment_report(report)
    return report


def _validate_favorable_disposition_gates(report: dict) -> None:
    """
    Hardening pass item 3. SHADOW_CANDIDATE/PROMOTION_CANDIDATE are
    structurally impossible when a basic validity gate fails -- this is
    NOT an automatic "winner detector": whether a real metric
    improvement is GOOD ENOUGH remains a human/research-review call,
    made by whoever chose `disposition` in the first place. This only
    blocks OBJECTIVE invalidity. REJECT and RESEARCH_CANDIDATE are never
    gated here -- both remain valid at any evidence level, any sample
    size, any leakage-warning state.
    """
    if report["disposition"] not in (dispositions.SHADOW_CANDIDATE, dispositions.PROMOTION_CANDIDATE):
        return

    if report.get("sampleRequirementMet") is not True:
        raise ValueError(
            f"disposition={report['disposition']!r} requires sampleRequirementMet == True, got "
            f"{report.get('sampleRequirementMet')!r} -- use RESEARCH_CANDIDATE for an insufficient-sample result."
        )
    if report.get("blockingLeakageWarnings"):
        raise ValueError(
            f"disposition={report['disposition']!r} is not permitted while blockingLeakageWarnings is "
            f"non-empty: {report['blockingLeakageWarnings']!r}."
        )
    primary = report.get("primaryResult") or {}
    control_result = primary.get("control") or {}
    candidate_result = primary.get("candidate") or {}
    if not report.get("nRows") or control_result.get("brierScore") is None or candidate_result.get("brierScore") is None:
        raise ValueError(
            f"disposition={report['disposition']!r} requires a real primary evaluation (nRows > 0 and both "
            f"control/candidate primary results present) -- got nRows={report.get('nRows')!r}."
        )
    try:
        pit_provenance.validate_pit_requirements(report.get("pitRequirements") or {}, report["evidenceLevel"])
    except (ValueError, KeyError) as exc:
        raise ValueError(
            f"disposition={report['disposition']!r} requires every declared PIT requirement to remain "
            f"compatible with evidenceLevel={report['evidenceLevel']!r}: {exc}"
        ) from exc


def validate_experiment_report(report: dict) -> None:
    missing = [f for f in REQUIRED_REPORT_FIELDS if f not in report]
    if missing:
        raise ValueError(f"Experiment report missing required fields: {missing}")
    evidence_levels.validate_evidence_level(report["evidenceLevel"])
    dispositions.validate_disposition(report["disposition"])
    if report["disposition"] == dispositions.PRODUCTION:
        raise dispositions.ProductionDispositionForbiddenError(
            "An experiment report must never carry disposition == PRODUCTION -- see lib.edgelab.dispositions."
        )
    if report["productionBehaviorChanged"] is not False:
        raise ValueError("productionBehaviorChanged must always be False for a Research Lab report.")

    # Disposition-vs-evidence-level consistency (spec: SHADOW_CANDIDATE
    # requires walk-forward+ evidence; PROMOTION_CANDIDATE requires
    # prospective-shadow+ evidence). REJECT/RESEARCH_CANDIDATE have no
    # evidence-level floor -- an experiment can be rejected or flagged
    # for further research at any evidence level.
    if report["disposition"] == dispositions.SHADOW_CANDIDATE and not evidence_levels.meets_minimum(
        report["evidenceLevel"], evidence_levels.MIN_EVIDENCE_LEVEL_FOR_SHADOW_CANDIDATE
    ):
        raise ValueError(
            f"disposition=SHADOW_CANDIDATE requires evidence level >= "
            f"{evidence_levels.MIN_EVIDENCE_LEVEL_FOR_SHADOW_CANDIDATE}, got {report['evidenceLevel']!r}."
        )
    if report["disposition"] == dispositions.PROMOTION_CANDIDATE and not evidence_levels.meets_minimum(
        report["evidenceLevel"], evidence_levels.MIN_EVIDENCE_LEVEL_FOR_PROMOTION_CANDIDATE
    ):
        raise ValueError(
            f"disposition=PROMOTION_CANDIDATE requires evidence level >= "
            f"{evidence_levels.MIN_EVIDENCE_LEVEL_FOR_PROMOTION_CANDIDATE}, got {report['evidenceLevel']!r}."
        )
    if not evidence_levels.is_promotable(report["evidenceLevel"]) and report["disposition"] in (
        dispositions.SHADOW_CANDIDATE, dispositions.PROMOTION_CANDIDATE,
    ):
        raise ValueError(
            f"Evidence level {report['evidenceLevel']!r} (E0/E1) is never promotable -- "
            f"disposition {report['disposition']!r} is not permitted at this evidence level."
        )
    _validate_favorable_disposition_gates(report)


def _path_for(experiment_id: str, experiment_report_id: str) -> str:
    return os.path.join(EXPERIMENT_REPORTS_ROOT, experiment_id, f"{experiment_report_id}.json")


def write_experiment_report(report: dict) -> str:
    """
    Reports ACCUMULATE over an experiment's life (unlike experiment/
    control/candidate registration, which are write-once) -- a
    development-stage look and a later confirmatory holdout look are
    both legitimate, separate, permanently-retained reports. A
    byte-identical rerun (same report content) is a true no-op;
    identical experimentReportId with DIFFERENT content is refused
    (should not happen given the id already incorporates generatedAt,
    but guarded anyway, same discipline as every other write-once
    function in this milestone).
    """
    validate_experiment_report(report)
    path = _path_for(report["experimentId"], report["experimentReportId"])
    payload = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if os.path.exists(path):
        with open(path) as f:
            existing = f.read()
        if existing != payload:
            raise ValueError(f"Report {report['experimentReportId']!r} already exists with different content.")
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(payload)
    return path


def list_reports_for_experiment(experiment_id: str):
    directory = os.path.join(EXPERIMENT_REPORTS_ROOT, experiment_id)
    if not os.path.isdir(directory):
        return []
    reports = []
    for fn in sorted(os.listdir(directory)):
        if fn.endswith(".json"):
            with open(os.path.join(directory, fn)) as f:
                reports.append(json.load(f))
    return reports
