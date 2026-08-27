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
"""

import json
import os

from lib.edgelab import dispositions
from lib.edgelab import evidence_levels
from lib.edgelab import research_lab_ids as rlids

EXPERIMENT_REPORTS_ROOT = os.path.join("data", "edgelab", "experiment_reports")

REQUIRED_REPORT_FIELDS = (
    "experimentReportId", "experimentId", "controlModelId", "candidateId", "evidenceLevel",
    "experimentType", "generatedAt", "trainDateRange", "validationDateRange", "holdoutDateRange",
    "evaluationDateRange", "nRows", "nIndependentGames", "nIndependentDates", "nPlayers",
    "missingDataSummary", "unpairedObservationSummary", "pitProvenanceStatus", "pitLimitations",
    "primaryMetric", "primaryResult", "pairedDeltaVsControl", "uncertainty", "secondaryMetrics",
    "marketEconomicMetrics", "falseDiscoveryTreatment", "minimumSampleRequirement",
    "sampleRequirementMet", "disposition", "methodologicalLimitations", "leakageWarnings",
    "overfittingWarnings", "productionBehaviorChanged",
)


def build_experiment_report(
    *, experiment, control_registration, candidate_registration=None, pairing_result,
    probability_evaluation, disposition, evidence_level, train_date_range=None,
    validation_date_range=None, holdout_date_range=None, evaluation_date_range=None,
    n_players=None, pit_provenance_status="SEE_EXPERIMENT_PIT_REQUIREMENTS", pit_limitations=(),
    market_economic_metrics=None, false_discovery_treatment=None, methodological_limitations=(),
    leakage_warnings=(), overfitting_warnings=(), secondary_metrics=None, generated_at=None,
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

    `false_discovery_treatment` defaults to the experiment's own
    registered `falseDiscoveryHandling` when not explicitly overridden,
    so a report can never silently omit how multiple-testing was (or
    deliberately wasn't) handled.
    """
    evidence_levels.validate_evidence_level(evidence_level)
    safe_disposition = dispositions.assign_disposition(disposition)

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
    sample_requirement_met = (
        n_independent_games is not None and minimum_requirement is not None
        and n_independent_games >= minimum_requirement
    )

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
        "nIndependentDates": probability_evaluation.get("independentDates"),
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
        "overfittingWarnings": list(overfitting_warnings),
        "productionBehaviorChanged": False,
    }
    validate_experiment_report(report)
    return report


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
