import pytest

from lib.edgelab import candidate_identity as cand
from lib.edgelab import control_identity as ci
from lib.edgelab import dispositions as disp
from lib.edgelab import evidence_levels as ev
from lib.edgelab import experiment_registry as reg
from lib.edgelab import experiment_report as er
from lib.edgelab import paired_evaluation as pe


def _row(game_id, ticker, checkpoint, **extra):
    row = {"gameId": game_id, "marketTicker": ticker, "researchCheckpoint": checkpoint}
    row.update(extra)
    return row


def _paired_probability_fixture(evidence_level_rows=25):
    control, candidate = [], []
    for i in range(evidence_level_rows):
        o = i % 2
        control.append(_row(f"g{i}", f"T{i}", "T_MINUS_30", modelFairProbability=0.5, outcome=o, gameDate="2026-08-01"))
        candidate.append(_row(f"g{i}", f"T{i}", "T_MINUS_30", modelFairProbability=0.7 if o == 1 else 0.3, outcome=o, gameDate="2026-08-01"))
    return control, candidate


def _setup(tmp_path, monkeypatch, evidence_level=ev.E2_PIT_HISTORICAL, minimum_sample=5):
    monkeypatch.chdir(tmp_path)
    control_reg = ci.build_control_registration(
        name="rules_v1", source_git_commit_sha="sha1", model_config_version="1.0", config_fingerprint="fp",
        probability_adapter_identity="adapter", model_engine_family="rules_based_v1",
        required_input_provenance=["archived_kalshi_market_observation"], identity_confidence=ci.IDENTITY_EXACT,
    )
    ci.register_control(control_reg)
    candidate_reg = cand.build_candidate_registration(
        name="candidate_a", base_control_model_id=control_reg["controlModelId"],
        change_description="a synthetic test candidate", change_type=cand.CHANGE_TYPE_FEATURE_ADDITION,
        implementation_ref="NOT_YET_IMPLEMENTED",
    )
    cand.register_candidate(candidate_reg)

    definition = reg.build_experiment_definition(
        title="synthetic test experiment", hypothesis="h", research_question="q", owner="test",
        control_model_id=control_reg["controlModelId"], candidate_variant_id=candidate_reg["candidateVariantId"],
        evidence_level=evidence_level, target_population="synthetic", market_families=["game_result"],
        eligibility_criteria=[], exclusion_criteria=[], prediction_checkpoints=["T_MINUS_30"],
        primary_metric="brierScore", secondary_metrics=["logLoss"], chronological_split_policy="n/a",
        minimum_sample_requirement=minimum_sample, clustering_unit="gameId",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY, false_discovery_handling=reg.FDR_NONE_SINGLE_HYPOTHESIS,
        pit_requirements=["archived_kalshi_market_observation"],
    )
    reg.register_experiment(definition)

    control_rows, candidate_rows = _paired_probability_fixture()
    pairing = pe.pair_eligible_observations(control_rows, candidate_rows)
    probability_evaluation = pe.evaluate_probability_model_pair(pairing, n_resamples=200, seed=1)
    return definition, control_reg, candidate_reg, pairing, probability_evaluation


def test_build_experiment_report_has_all_required_fields(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch)
    report = er.build_experiment_report(
        experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
        pairing_result=pairing, probability_evaluation=probability_evaluation,
        disposition=disp.RESEARCH_CANDIDATE, evidence_level=ev.E2_PIT_HISTORICAL,
    )
    for field in er.REQUIRED_REPORT_FIELDS:
        assert field in report


def test_report_production_behavior_changed_is_always_false(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch)
    report = er.build_experiment_report(
        experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
        pairing_result=pairing, probability_evaluation=probability_evaluation,
        disposition=disp.RESEARCH_CANDIDATE, evidence_level=ev.E2_PIT_HISTORICAL,
    )
    assert report["productionBehaviorChanged"] is False


def test_build_experiment_report_never_accepts_production_disposition(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch)
    with pytest.raises(disp.ProductionDispositionForbiddenError):
        er.build_experiment_report(
            experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
            pairing_result=pairing, probability_evaluation=probability_evaluation,
            disposition=disp.PRODUCTION, evidence_level=ev.E2_PIT_HISTORICAL,
        )


def test_build_experiment_report_has_no_parameter_that_can_set_production_behavior_changed(tmp_path, monkeypatch):
    import inspect
    sig = inspect.signature(er.build_experiment_report)
    assert "production_behavior_changed" not in sig.parameters


def test_shadow_candidate_disposition_requires_walk_forward_or_better_evidence(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch, evidence_level=ev.E1_RECONSTRUCTED_RETROSPECTIVE)
    with pytest.raises(ValueError):
        er.build_experiment_report(
            experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
            pairing_result=pairing, probability_evaluation=probability_evaluation,
            disposition=disp.SHADOW_CANDIDATE, evidence_level=ev.E1_RECONSTRUCTED_RETROSPECTIVE,
        )


def test_shadow_candidate_permitted_at_walk_forward_evidence(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch, evidence_level=ev.E3_WALK_FORWARD_HOLDOUT)
    report = er.build_experiment_report(
        experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
        pairing_result=pairing, probability_evaluation=probability_evaluation,
        disposition=disp.SHADOW_CANDIDATE, evidence_level=ev.E3_WALK_FORWARD_HOLDOUT,
    )
    assert report["disposition"] == disp.SHADOW_CANDIDATE


def test_promotion_candidate_requires_prospective_shadow_evidence(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch, evidence_level=ev.E3_WALK_FORWARD_HOLDOUT)
    with pytest.raises(ValueError):
        er.build_experiment_report(
            experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
            pairing_result=pairing, probability_evaluation=probability_evaluation,
            disposition=disp.PROMOTION_CANDIDATE, evidence_level=ev.E3_WALK_FORWARD_HOLDOUT,
        )


def test_reject_permitted_at_any_evidence_level_including_e0(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch, evidence_level=ev.E0_DESCRIPTIVE)
    report = er.build_experiment_report(
        experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
        pairing_result=pairing, probability_evaluation=probability_evaluation,
        disposition=disp.REJECT, evidence_level=ev.E0_DESCRIPTIVE,
    )
    assert report["disposition"] == disp.REJECT


def test_sample_requirement_met_reflects_independent_games_vs_minimum(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch, minimum_sample=100)
    report = er.build_experiment_report(
        experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
        pairing_result=pairing, probability_evaluation=probability_evaluation,
        disposition=disp.RESEARCH_CANDIDATE, evidence_level=ev.E2_PIT_HISTORICAL,
    )
    assert report["sampleRequirementMet"] is False
    assert report["minimumSampleRequirement"] == 100


def test_write_and_list_reports_round_trip(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch)
    report = er.build_experiment_report(
        experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
        pairing_result=pairing, probability_evaluation=probability_evaluation,
        disposition=disp.RESEARCH_CANDIDATE, evidence_level=ev.E2_PIT_HISTORICAL,
    )
    er.write_experiment_report(report)
    reports = er.list_reports_for_experiment(definition["experimentId"])
    assert len(reports) == 1
    assert reports[0]["experimentReportId"] == report["experimentReportId"]


def test_write_experiment_report_accumulates_multiple_reports(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch)
    report1 = er.build_experiment_report(
        experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
        pairing_result=pairing, probability_evaluation=probability_evaluation,
        disposition=disp.RESEARCH_CANDIDATE, evidence_level=ev.E2_PIT_HISTORICAL, generated_at="2026-08-01T00:00:00Z",
    )
    report2 = er.build_experiment_report(
        experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
        pairing_result=pairing, probability_evaluation=probability_evaluation,
        disposition=disp.REJECT, evidence_level=ev.E2_PIT_HISTORICAL, generated_at="2026-08-02T00:00:00Z",
    )
    er.write_experiment_report(report1)
    er.write_experiment_report(report2)
    reports = er.list_reports_for_experiment(definition["experimentId"])
    assert len(reports) == 2


def test_false_discovery_treatment_defaults_from_experiment_registration(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch)
    report = er.build_experiment_report(
        experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
        pairing_result=pairing, probability_evaluation=probability_evaluation,
        disposition=disp.RESEARCH_CANDIDATE, evidence_level=ev.E2_PIT_HISTORICAL,
    )
    assert report["falseDiscoveryTreatment"] == definition["falseDiscoveryHandling"]


def test_unpaired_observation_summary_reflects_pairing_result(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch)
    extra_control_only = pe.pair_eligible_observations(
        [_row("gX", "TX", "T_MINUS_30", modelFairProbability=0.5, outcome=1)] + [
            _row(f"g{i}", f"T{i}", "T_MINUS_30", modelFairProbability=0.5, outcome=i % 2, gameDate="2026-08-01") for i in range(25)
        ],
        [_row(f"g{i}", f"T{i}", "T_MINUS_30", modelFairProbability=0.6, outcome=i % 2, gameDate="2026-08-01") for i in range(25)],
    )
    probability_evaluation2 = pe.evaluate_probability_model_pair(extra_control_only)
    report = er.build_experiment_report(
        experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
        pairing_result=extra_control_only, probability_evaluation=probability_evaluation2,
        disposition=disp.RESEARCH_CANDIDATE, evidence_level=ev.E2_PIT_HISTORICAL,
    )
    assert report["unpairedObservationSummary"]["nControlOnly"] == 1
