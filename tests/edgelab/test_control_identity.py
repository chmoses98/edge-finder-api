import pytest

from lib.edgelab import control_identity as ci


def _build(**overrides):
    kwargs = dict(
        name="rules_v1_11_market",
        source_git_commit_sha="abc123",
        model_config_version="1.0",
        config_fingerprint="fingerprint-abc",
        probability_adapter_identity="lib.kalshi_probability_adapters.adapt_contract",
        model_engine_family="rules_based_v1",
        required_input_provenance=["archived_kalshi_market_observation", "model_evaluation_probability_pipeline_derived"],
        identity_confidence=ci.IDENTITY_EXACT,
        description="The current production 11-market rules-based model.",
    )
    kwargs.update(overrides)
    return ci.build_control_registration(**kwargs)


def test_build_control_registration_has_all_required_fields():
    reg = _build()
    for field in ci.REQUIRED_FIELDS:
        assert field in reg


def test_control_model_id_is_deterministic_over_identity_inputs():
    reg1 = _build()
    reg2 = _build()
    assert reg1["controlModelId"] == reg2["controlModelId"]


def test_control_model_id_changes_when_commit_changes():
    reg1 = _build(source_git_commit_sha="commitA")
    reg2 = _build(source_git_commit_sha="commitB")
    assert reg1["controlModelId"] != reg2["controlModelId"]


def test_build_control_registration_rejects_unknown_pit_input():
    with pytest.raises(KeyError):
        _build(required_input_provenance=["not_a_real_pit_key"])


def test_build_control_registration_rejects_bad_identity_confidence():
    with pytest.raises(ValueError):
        _build(identity_confidence="TOTALLY_SURE")


def test_historical_reconstruction_must_not_claim_exact_confidence():
    """This module does not itself forbid EXACT for a historical
    reconstruction (it can't know intent from the fields alone), but the
    HISTORICAL_* tiers exist specifically so a caller reconstructing an
    old control never has to lie and say EXACT."""
    reg = _build(identity_confidence=ci.IDENTITY_HISTORICAL_AMBIGUOUS)
    assert reg["identityConfidence"] == ci.IDENTITY_HISTORICAL_AMBIGUOUS


def test_register_and_load_round_trips(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reg = _build()
    path = ci.register_control(reg)
    assert path.endswith(".json")
    loaded = ci.load_control(reg["controlModelId"])
    assert loaded == reg
    assert reg["controlModelId"] in ci.list_registered_control_ids()


def test_register_control_is_idempotent_for_identical_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reg = _build()
    path1 = ci.register_control(reg)
    path2 = ci.register_control(reg)
    assert path1 == path2


def test_register_control_refuses_to_silently_overwrite_different_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reg = _build()
    ci.register_control(reg)
    # Force a collision: same controlModelId, different description --
    # simulated by hand-editing the dict after building (registration
    # content mutated post-hoc), since normally id and content move together.
    mutated = dict(reg)
    mutated["description"] = "a different description entirely"
    with pytest.raises(ValueError):
        ci.register_control(mutated)


def test_load_missing_control_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        ci.load_control("CTRL-doesnotexist")
