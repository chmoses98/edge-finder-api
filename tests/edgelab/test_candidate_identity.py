import pytest

from lib.edgelab import candidate_identity as cand


def _build(**overrides):
    kwargs = dict(
        name="offense_pitch_type_matchup_feature",
        base_control_model_id="CTRL-deadbeefdeadbeef",
        change_description="Adds a pitch-type matchup adjustment to the offense projection.",
        change_type=cand.CHANGE_TYPE_FEATURE_ADDITION,
        implementation_ref="NOT_YET_IMPLEMENTED",
        description="Milestone 0A placeholder registration -- no real variant implemented yet.",
    )
    kwargs.update(overrides)
    return cand.build_candidate_registration(**kwargs)


def test_build_candidate_registration_has_all_required_fields():
    reg = _build()
    for field in cand.REQUIRED_FIELDS:
        assert field in reg


def test_candidate_never_declares_modified_production_paths():
    reg = _build()
    assert reg["productionCodePathsModified"] == []


def test_candidate_id_deterministic_and_distinguishes_change():
    reg1 = _build()
    reg2 = _build()
    assert reg1["candidateVariantId"] == reg2["candidateVariantId"]
    reg3 = _build(change_description="a totally different change")
    assert reg3["candidateVariantId"] != reg1["candidateVariantId"]


def test_build_candidate_registration_rejects_unknown_change_type():
    with pytest.raises(ValueError):
        _build(change_type="MADE_UP_TYPE")


def test_build_candidate_registration_requires_base_control_model_id():
    with pytest.raises(ValueError):
        _build(base_control_model_id=None)


def test_validate_candidate_registration_rejects_nonempty_production_paths():
    reg = _build()
    reg["productionCodePathsModified"] = ["scripts/build_market_ledger.py"]
    with pytest.raises(ValueError):
        cand.validate_candidate_registration(reg)


def test_register_and_load_round_trips(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reg = _build()
    cand.register_candidate(reg)
    loaded = cand.load_candidate(reg["candidateVariantId"])
    assert loaded == reg
    assert reg["candidateVariantId"] in cand.list_registered_candidate_ids()


def test_register_candidate_refuses_overwrite_with_different_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reg = _build()
    cand.register_candidate(reg)
    mutated = dict(reg)
    mutated["description"] = "different"
    with pytest.raises(ValueError):
        cand.register_candidate(mutated)
