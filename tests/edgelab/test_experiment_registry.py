import pytest

from lib.edgelab import control_identity as ci
from lib.edgelab import dispositions as disp
from lib.edgelab import evidence_levels as ev
from lib.edgelab import experiment_registry as reg
from lib.edgelab import pit_provenance as pit


def _register_a_control(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    control = ci.build_control_registration(
        name="rules_v1_11_market", source_git_commit_sha="abc123", model_config_version="1.0",
        config_fingerprint="fp1", probability_adapter_identity="adapter_v1", model_engine_family="rules_based_v1",
        required_input_provenance=["archived_kalshi_market_observation"], identity_confidence=ci.IDENTITY_EXACT,
    )
    ci.register_control(control)
    return control


def _build_definition(control, **overrides):
    kwargs = dict(
        title="Does declared edge correspond to genuine predictive advantage?",
        hypothesis="Higher declared model edge corresponds to a monotonically larger true predictive edge vs. executable Kalshi price.",
        research_question="Is declared edge monotonic in true predictive value, by market family?",
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E2_PIT_HISTORICAL,
        target_population="All observed MLB Kalshi contracts",
        market_families=["game_result", "team_total"],
        eligibility_criteria=["modelEvaluationAvailable == True", "settlementStatus == SETTLED"],
        exclusion_criteria=["marketSelectionAmbiguous == True"],
        prediction_checkpoints=["T_MINUS_30", "T_MINUS_60"],
        primary_metric="brierScore",
        secondary_metrics=["logLoss", "calibrationError"],
        chronological_split_policy="DEVELOPMENT_60_VALIDATION_20_HOLDOUT_20",
        minimum_sample_requirement=20,
        clustering_unit="gameId",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_NONE_SINGLE_HYPOTHESIS,
        pit_requirements={
            "archived_kalshi_market_observation": pit.ROLE_PREDICTIVE_INPUT,
            "model_evaluation_probability_pipeline_derived": pit.ROLE_PREDICTIVE_INPUT,
            "settlement_outcome": pit.ROLE_EVALUATION_TARGET,
        },
    )
    kwargs.update(overrides)
    return reg.build_experiment_definition(**kwargs)


def test_build_and_register_experiment(tmp_path, monkeypatch):
    control = _register_a_control(tmp_path, monkeypatch)
    definition = _build_definition(control)
    for field in reg.REQUIRED_FIELDS:
        assert field in definition
    path = reg.register_experiment(definition)
    assert path.endswith(".json")
    loaded = reg.load_experiment(definition["experimentId"])
    assert loaded == definition


def test_experiment_ids_are_stable_mlb_rsch_format(tmp_path, monkeypatch):
    control = _register_a_control(tmp_path, monkeypatch)
    d1 = _build_definition(control)
    assert d1["experimentId"] == "MLB-RSCH-0001"
    reg.register_experiment(d1)
    d2 = _build_definition(control, title="a second, different experiment")
    assert d2["experimentId"] == "MLB-RSCH-0002"


def test_next_experiment_id_is_deterministic_given_registry_state(tmp_path, monkeypatch):
    control = _register_a_control(tmp_path, monkeypatch)
    assert reg.next_experiment_id() == "MLB-RSCH-0001"
    reg.register_experiment(_build_definition(control))
    assert reg.next_experiment_id() == "MLB-RSCH-0002"
    assert reg.next_experiment_id() == "MLB-RSCH-0002"  # calling again without registering is stable


def test_register_experiment_requires_a_registered_control(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        reg.build_experiment_definition(
            title="t", hypothesis="h", research_question="q", owner="me",
            control_model_id="CTRL-not-registered", evidence_level=ev.E0_DESCRIPTIVE,
            target_population="x", market_families=[], eligibility_criteria=[], exclusion_criteria=[],
            prediction_checkpoints=[], primary_metric="brierScore", secondary_metrics=[],
            chronological_split_policy="x", minimum_sample_requirement=20, clustering_unit="gameId",
            experiment_type=reg.EXPERIMENT_TYPE_EXPLORATORY, false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
            pit_requirements={},
        )


def test_build_experiment_definition_rejects_unlisted_pit_input(tmp_path, monkeypatch):
    control = _register_a_control(tmp_path, monkeypatch)
    with pytest.raises(KeyError):
        _build_definition(control, pit_requirements={"not_a_real_input": pit.ROLE_PREDICTIVE_INPUT})


# ── Hardening pass item 1: PIT compatibility, not just existence ───────────

def test_e2_experiment_with_unknown_requires_audit_predictive_input_fails(tmp_path, monkeypatch):
    control = _register_a_control(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        _build_definition(
            control, evidence_level=ev.E2_PIT_HISTORICAL,
            pit_requirements={"season_to_date_stats": pit.ROLE_PREDICTIVE_INPUT},
        )


def test_e0_experiment_with_unknown_requires_audit_predictive_input_is_allowed(tmp_path, monkeypatch):
    """E0/E1 make no historical-PIT-safety claim, so UNKNOWN_REQUIRES_AUDIT is not blocked there."""
    control = _register_a_control(tmp_path, monkeypatch)
    definition = _build_definition(
        control, evidence_level=ev.E0_DESCRIPTIVE,
        pit_requirements={"season_to_date_stats": pit.ROLE_PREDICTIVE_INPUT},
    )
    assert definition["pitRequirements"]["season_to_date_stats"] == pit.ROLE_PREDICTIVE_INPUT


def test_settlement_outcome_allowed_as_evaluation_target(tmp_path, monkeypatch):
    control = _register_a_control(tmp_path, monkeypatch)
    definition = _build_definition(
        control, pit_requirements={"settlement_outcome": pit.ROLE_EVALUATION_TARGET},
    )
    assert definition["pitRequirements"]["settlement_outcome"] == pit.ROLE_EVALUATION_TARGET


def test_settlement_outcome_rejected_as_predictive_input(tmp_path, monkeypatch):
    control = _register_a_control(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        _build_definition(control, pit_requirements={"settlement_outcome": pit.ROLE_PREDICTIVE_INPUT})


def test_closing_quote_allowed_as_evaluation_target(tmp_path, monkeypatch):
    control = _register_a_control(tmp_path, monkeypatch)
    definition = _build_definition(
        control, pit_requirements={"kalshi_closing_market_quote": pit.ROLE_EVALUATION_TARGET},
    )
    assert definition["pitRequirements"]["kalshi_closing_market_quote"] == pit.ROLE_EVALUATION_TARGET


def test_closing_quote_rejected_as_predictive_input():
    with pytest.raises(ValueError):
        pit.validate_pit_requirement("kalshi_closing_market_quote", pit.ROLE_PREDICTIVE_INPUT, ev.E0_DESCRIPTIVE)


# ── Hardening pass item 2: candidateModelId/candidateVariantId ambiguity ───

def test_experiment_cannot_declare_both_candidate_model_id_and_candidate_variant_id(tmp_path, monkeypatch):
    control = _register_a_control(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        _build_definition(control, candidate_model_id="some-model-id", candidate_variant_id="CAND-1234567890abcdef")


def test_experiment_with_only_candidate_variant_id_is_fine(tmp_path, monkeypatch):
    control = _register_a_control(tmp_path, monkeypatch)
    definition = _build_definition(control, candidate_variant_id="CAND-1234567890abcdef")
    assert definition["candidateVariantId"] == "CAND-1234567890abcdef"
    assert definition["candidateModelId"] is None


def test_control_only_experiment_has_no_candidate_identifiers(tmp_path, monkeypatch):
    control = _register_a_control(tmp_path, monkeypatch)
    definition = _build_definition(control)
    assert definition["candidateModelId"] is None
    assert definition["candidateVariantId"] is None


# ── Hardening pass item 3: dual-dimension minimum sample requirement ───────

def test_minimum_sample_requirement_accepts_dict_of_both_dimensions(tmp_path, monkeypatch):
    control = _register_a_control(tmp_path, monkeypatch)
    definition = _build_definition(control, minimum_sample_requirement={"independentGames": 30, "independentDates": 10})
    assert definition["minimumSampleRequirement"] == {"independentGames": 30, "independentDates": 10}


def test_minimum_sample_requirement_rejects_unknown_dimension(tmp_path, monkeypatch):
    control = _register_a_control(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        _build_definition(control, minimum_sample_requirement={"independentPlayers": 5})


def test_minimum_sample_requirement_rejects_non_positive_number(tmp_path, monkeypatch):
    control = _register_a_control(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        _build_definition(control, minimum_sample_requirement=0)


def test_exploratory_experiment_must_not_use_none_single_hypothesis(tmp_path, monkeypatch):
    control = _register_a_control(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        _build_definition(control, experiment_type=reg.EXPERIMENT_TYPE_EXPLORATORY, false_discovery_handling=reg.FDR_NONE_SINGLE_HYPOTHESIS)


def test_confirmatory_experiment_may_use_none_single_hypothesis(tmp_path, monkeypatch):
    control = _register_a_control(tmp_path, monkeypatch)
    definition = _build_definition(control, experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY, false_discovery_handling=reg.FDR_NONE_SINGLE_HYPOTHESIS)
    assert definition["falseDiscoveryHandling"] == reg.FDR_NONE_SINGLE_HYPOTHESIS


def test_experiment_status_can_never_be_production(tmp_path, monkeypatch):
    control = _register_a_control(tmp_path, monkeypatch)
    definition = _build_definition(control)
    definition["status"] = disp.PRODUCTION
    with pytest.raises(disp.ProductionDispositionForbiddenError):
        reg.validate_experiment_definition(definition)


def test_register_experiment_is_write_once(tmp_path, monkeypatch):
    control = _register_a_control(tmp_path, monkeypatch)
    definition = _build_definition(control)
    reg.register_experiment(definition)
    reg.register_experiment(definition)  # identical content -- no-op, does not raise
    mutated = dict(definition)
    mutated["notes"] = "a change to an already-registered experiment"
    with pytest.raises(ValueError):
        reg.register_experiment(mutated)


def test_validate_experiment_definition_rejects_bad_experiment_id():
    bad = {"experimentId": "NOT-A-VALID-ID"}
    with pytest.raises(ValueError):
        reg.validate_experiment_definition(bad)


def test_list_experiment_ids_empty_registry_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert reg.list_experiment_ids() == []
