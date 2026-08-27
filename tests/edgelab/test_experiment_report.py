import pytest

from lib.edgelab import candidate_identity as cand
from lib.edgelab import control_identity as ci
from lib.edgelab import dispositions as disp
from lib.edgelab import evidence_levels as ev
from lib.edgelab import experiment_registry as reg
from lib.edgelab import experiment_report as er
from lib.edgelab import paired_evaluation as pe
from lib.edgelab import pit_provenance as pit


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


def _setup(tmp_path, monkeypatch, evidence_level=ev.E2_PIT_HISTORICAL, minimum_sample=5, control_only=False):
    monkeypatch.chdir(tmp_path)
    control_reg = ci.build_control_registration(
        name="rules_v1", source_git_commit_sha="sha1", model_config_version="1.0", config_fingerprint="fp",
        probability_adapter_identity="adapter", model_engine_family="rules_based_v1",
        required_input_provenance=["archived_kalshi_market_observation"], identity_confidence=ci.IDENTITY_EXACT,
    )
    ci.register_control(control_reg)
    candidate_reg = None
    candidate_variant_id = None
    if not control_only:
        candidate_reg = cand.build_candidate_registration(
            name="candidate_a", base_control_model_id=control_reg["controlModelId"],
            change_description="a synthetic test candidate", change_type=cand.CHANGE_TYPE_FEATURE_ADDITION,
            implementation_ref="NOT_YET_IMPLEMENTED",
        )
        cand.register_candidate(candidate_reg)
        candidate_variant_id = candidate_reg["candidateVariantId"]

    definition = reg.build_experiment_definition(
        title="synthetic test experiment", hypothesis="h", research_question="q", owner="test",
        control_model_id=control_reg["controlModelId"], candidate_variant_id=candidate_variant_id,
        evidence_level=evidence_level, target_population="synthetic", market_families=["game_result"],
        eligibility_criteria=[], exclusion_criteria=[], prediction_checkpoints=["T_MINUS_30"],
        primary_metric="brierScore", secondary_metrics=["logLoss"], chronological_split_policy="n/a",
        minimum_sample_requirement=minimum_sample, clustering_unit="gameId",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY, false_discovery_handling=reg.FDR_NONE_SINGLE_HYPOTHESIS,
        pit_requirements={"archived_kalshi_market_observation": pit.ROLE_PREDICTIVE_INPUT},
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


# ── Hardening pass item 2: experiment/control/candidate consistency ────────

def test_control_only_experiment_still_works(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch, control_only=True)
    assert candidate_reg is None
    assert definition["candidateVariantId"] is None
    report = er.build_experiment_report(
        experiment=definition, control_registration=control_reg, candidate_registration=None,
        pairing_result=pairing, probability_evaluation=probability_evaluation,
        disposition=disp.RESEARCH_CANDIDATE, evidence_level=ev.E2_PIT_HISTORICAL,
    )
    assert report["candidateId"] is None


def test_control_only_experiment_rejects_an_unexpected_candidate_registration(tmp_path, monkeypatch):
    definition, control_reg, _unused, pairing, probability_evaluation = _setup(tmp_path, monkeypatch, control_only=True)
    stray_candidate = cand.build_candidate_registration(
        name="stray", base_control_model_id=control_reg["controlModelId"], change_description="unexpected",
        change_type=cand.CHANGE_TYPE_OTHER, implementation_ref="NOT_YET_IMPLEMENTED",
    )
    with pytest.raises(ValueError):
        er.build_experiment_report(
            experiment=definition, control_registration=control_reg, candidate_registration=stray_candidate,
            pairing_result=pairing, probability_evaluation=probability_evaluation,
            disposition=disp.RESEARCH_CANDIDATE, evidence_level=ev.E2_PIT_HISTORICAL,
        )


def test_report_fails_when_control_registration_does_not_match_experiment(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch)
    wrong_control = ci.build_control_registration(
        name="a_totally_different_control", source_git_commit_sha="other-sha", model_config_version="2.0",
        config_fingerprint="other-fp", probability_adapter_identity="adapter", model_engine_family="rules_based_v1",
        required_input_provenance=["archived_kalshi_market_observation"], identity_confidence=ci.IDENTITY_EXACT,
    )
    with pytest.raises(ValueError):
        er.build_experiment_report(
            experiment=definition, control_registration=wrong_control, candidate_registration=candidate_reg,
            pairing_result=pairing, probability_evaluation=probability_evaluation,
            disposition=disp.RESEARCH_CANDIDATE, evidence_level=ev.E2_PIT_HISTORICAL,
        )


def test_report_fails_when_experiment_declares_candidate_but_none_supplied(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        er.build_experiment_report(
            experiment=definition, control_registration=control_reg, candidate_registration=None,
            pairing_result=pairing, probability_evaluation=probability_evaluation,
            disposition=disp.RESEARCH_CANDIDATE, evidence_level=ev.E2_PIT_HISTORICAL,
        )


def test_report_fails_when_candidate_variant_id_does_not_match_experiment(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch)
    other_candidate = cand.build_candidate_registration(
        name="a_different_candidate", base_control_model_id=control_reg["controlModelId"],
        change_description="not the one this experiment registered", change_type=cand.CHANGE_TYPE_OTHER,
        implementation_ref="NOT_YET_IMPLEMENTED",
    )
    with pytest.raises(ValueError):
        er.build_experiment_report(
            experiment=definition, control_registration=control_reg, candidate_registration=other_candidate,
            pairing_result=pairing, probability_evaluation=probability_evaluation,
            disposition=disp.RESEARCH_CANDIDATE, evidence_level=ev.E2_PIT_HISTORICAL,
        )


def test_report_fails_when_candidate_is_based_on_a_different_control(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch)
    other_control = ci.build_control_registration(
        name="a_second_control", source_git_commit_sha="another-sha", model_config_version="1.1",
        config_fingerprint="another-fp", probability_adapter_identity="adapter", model_engine_family="rules_based_v1",
        required_input_provenance=["archived_kalshi_market_observation"], identity_confidence=ci.IDENTITY_EXACT,
    )
    ci.register_control(other_control)
    # A candidate whose candidateVariantId happens to match the experiment's,
    # but whose baseControlModelId points at a DIFFERENT control -- must
    # still be caught, not just an id-string match.
    mismatched_base_candidate = dict(candidate_reg)
    mismatched_base_candidate["baseControlModelId"] = other_control["controlModelId"]
    with pytest.raises(ValueError):
        er.build_experiment_report(
            experiment=definition, control_registration=control_reg, candidate_registration=mismatched_base_candidate,
            pairing_result=pairing, probability_evaluation=probability_evaluation,
            disposition=disp.RESEARCH_CANDIDATE, evidence_level=ev.E2_PIT_HISTORICAL,
        )


# ── Hardening pass item 4: no evidence self-upgrade ─────────────────────────

def test_report_cannot_claim_evidence_stronger_than_registered_experiment(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch, evidence_level=ev.E1_RECONSTRUCTED_RETROSPECTIVE)
    with pytest.raises(ValueError):
        er.build_experiment_report(
            experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
            pairing_result=pairing, probability_evaluation=probability_evaluation,
            disposition=disp.RESEARCH_CANDIDATE, evidence_level=ev.E3_WALK_FORWARD_HOLDOUT,
        )


def test_report_may_claim_weaker_evidence_than_registered_experiment(tmp_path, monkeypatch):
    """A downgrade (report evidence < registered evidence) is an honest
    admission, not an upgrade -- must remain allowed."""
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch, evidence_level=ev.E3_WALK_FORWARD_HOLDOUT)
    report = er.build_experiment_report(
        experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
        pairing_result=pairing, probability_evaluation=probability_evaluation,
        disposition=disp.RESEARCH_CANDIDATE, evidence_level=ev.E1_RECONSTRUCTED_RETROSPECTIVE,
    )
    assert report["evidenceLevel"] == ev.E1_RECONSTRUCTED_RETROSPECTIVE


# ── Hardening pass item 3: favorable dispositions gated on objective validity ──

def test_shadow_candidate_fails_when_sample_requirement_unmet(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(
        tmp_path, monkeypatch, evidence_level=ev.E3_WALK_FORWARD_HOLDOUT, minimum_sample=1000,
    )
    with pytest.raises(ValueError):
        er.build_experiment_report(
            experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
            pairing_result=pairing, probability_evaluation=probability_evaluation,
            disposition=disp.SHADOW_CANDIDATE, evidence_level=ev.E3_WALK_FORWARD_HOLDOUT,
        )


def test_promotion_candidate_fails_when_sample_requirement_unmet(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(
        tmp_path, monkeypatch, evidence_level=ev.E4_PROSPECTIVE_SHADOW, minimum_sample=1000,
    )
    with pytest.raises(ValueError):
        er.build_experiment_report(
            experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
            pairing_result=pairing, probability_evaluation=probability_evaluation,
            disposition=disp.PROMOTION_CANDIDATE, evidence_level=ev.E4_PROSPECTIVE_SHADOW,
        )


def test_research_candidate_still_works_with_insufficient_sample(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch, minimum_sample=1000)
    report = er.build_experiment_report(
        experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
        pairing_result=pairing, probability_evaluation=probability_evaluation,
        disposition=disp.RESEARCH_CANDIDATE, evidence_level=ev.E2_PIT_HISTORICAL,
    )
    assert report["sampleRequirementMet"] is False
    assert report["disposition"] == disp.RESEARCH_CANDIDATE


def test_reject_still_works_with_insufficient_sample_and_blocking_leakage(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch, minimum_sample=1000)
    report = er.build_experiment_report(
        experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
        pairing_result=pairing, probability_evaluation=probability_evaluation,
        disposition=disp.REJECT, evidence_level=ev.E2_PIT_HISTORICAL,
        blocking_leakage_warnings=["a serious leakage concern found during review"],
    )
    assert report["disposition"] == disp.REJECT


def test_shadow_candidate_fails_on_blocking_leakage_warning(tmp_path, monkeypatch):
    definition, control_reg, candidate_reg, pairing, probability_evaluation = _setup(tmp_path, monkeypatch, evidence_level=ev.E3_WALK_FORWARD_HOLDOUT)
    with pytest.raises(ValueError):
        er.build_experiment_report(
            experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
            pairing_result=pairing, probability_evaluation=probability_evaluation,
            disposition=disp.SHADOW_CANDIDATE, evidence_level=ev.E3_WALK_FORWARD_HOLDOUT,
            blocking_leakage_warnings=["undisclosed look-ahead risk in a feature"],
        )


def test_favorable_disposition_gate_rejects_missing_primary_evaluation_directly():
    """Isolated unit test of the 'missing primary evaluation' gate,
    independent of the sample-size gate (which would also legitimately
    fail on empty data -- this constructs a report dict where
    sampleRequirementMet is already True, so ONLY the missing-primary-
    result gate can be what fails)."""
    report = {
        "experimentReportId": "RPT-x", "experimentId": "MLB-RSCH-0001", "controlModelId": "CTRL-x",
        "candidateId": "CAND-x", "evidenceLevel": ev.E3_WALK_FORWARD_HOLDOUT, "experimentType": "CONFIRMATORY",
        "generatedAt": "2026-08-01T00:00:00Z", "trainDateRange": None, "validationDateRange": None,
        "holdoutDateRange": None, "evaluationDateRange": None, "nRows": 0, "nIndependentGames": 0,
        "nIndependentDates": 0, "nPlayers": None, "missingDataSummary": {}, "unpairedObservationSummary": {},
        "pitProvenanceStatus": "x", "pitLimitations": [], "pitRequirements": {}, "primaryMetric": "brierScore",
        "primaryResult": {"control": {"brierScore": None}, "candidate": {"brierScore": None}},
        "pairedDeltaVsControl": None, "uncertainty": None, "secondaryMetrics": {}, "marketEconomicMetrics": None,
        "falseDiscoveryTreatment": "NONE_SINGLE_HYPOTHESIS", "minimumSampleRequirement": 1,
        "sampleRequirementMet": True, "disposition": disp.SHADOW_CANDIDATE, "methodologicalLimitations": [],
        "leakageWarnings": [], "blockingLeakageWarnings": [], "overfittingWarnings": [], "productionBehaviorChanged": False,
    }
    with pytest.raises(ValueError):
        er.validate_experiment_report(report)


def test_shadow_candidate_fails_end_to_end_on_completely_empty_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    control_reg = ci.build_control_registration(
        name="rules_v1", source_git_commit_sha="sha1", model_config_version="1.0", config_fingerprint="fp",
        probability_adapter_identity="adapter", model_engine_family="rules_based_v1",
        required_input_provenance=["archived_kalshi_market_observation"], identity_confidence=ci.IDENTITY_EXACT,
    )
    ci.register_control(control_reg)
    candidate_reg = cand.build_candidate_registration(
        name="candidate_a", base_control_model_id=control_reg["controlModelId"], change_description="x",
        change_type=cand.CHANGE_TYPE_FEATURE_ADDITION, implementation_ref="NOT_YET_IMPLEMENTED",
    )
    cand.register_candidate(candidate_reg)
    definition = reg.build_experiment_definition(
        title="empty-data experiment", hypothesis="h", research_question="q", owner="test",
        control_model_id=control_reg["controlModelId"], candidate_variant_id=candidate_reg["candidateVariantId"],
        evidence_level=ev.E3_WALK_FORWARD_HOLDOUT, target_population="synthetic", market_families=["game_result"],
        eligibility_criteria=[], exclusion_criteria=[], prediction_checkpoints=["T_MINUS_30"],
        primary_metric="brierScore", secondary_metrics=[], chronological_split_policy="n/a",
        minimum_sample_requirement=0.0001, clustering_unit="gameId",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY, false_discovery_handling=reg.FDR_NONE_SINGLE_HYPOTHESIS,
        pit_requirements={"archived_kalshi_market_observation": pit.ROLE_PREDICTIVE_INPUT},
    )
    reg.register_experiment(definition)
    empty_pairing = pe.pair_eligible_observations([], [])
    empty_evaluation = pe.evaluate_probability_model_pair(empty_pairing)
    with pytest.raises(ValueError):
        er.build_experiment_report(
            experiment=definition, control_registration=control_reg, candidate_registration=candidate_reg,
            pairing_result=empty_pairing, probability_evaluation=empty_evaluation,
            disposition=disp.SHADOW_CANDIDATE, evidence_level=ev.E3_WALK_FORWARD_HOLDOUT,
        )
