import pytest

from lib.edgelab import pit_provenance as pit


def test_every_manifest_entry_has_a_valid_pit_status():
    for key, entry in pit.PIT_MANIFEST.items():
        assert entry.pitStatus in pit.PIT_STATUSES, key


def test_every_manifest_entry_has_source_and_data_family():
    for key, entry in pit.PIT_MANIFEST.items():
        assert entry.sourceIdentifier, key
        assert entry.dataFamily, key


def test_spec_named_inputs_are_present():
    expected = {
        "archived_kalshi_market_observation", "kalshi_bid_ask_executable_price", "model_evaluation_probability_pipeline_derived",
        "model_evaluation_probability_prospective_snapshot", "settlement_outcome", "kalshi_closing_market_quote",
        "lineup_status_official", "sharp_sportsbook_observation", "weather_input", "season_to_date_stats",
        "hitter_snapshot", "pitcher_snapshot",
    }
    assert expected.issubset(set(pit.PIT_MANIFEST))


def test_get_entry_raises_for_unlisted_input():
    with pytest.raises(KeyError):
        pit.get_entry("made_up_data_family_nobody_registered")


def test_assert_known_inputs_raises_on_any_unlisted_key():
    with pytest.raises(KeyError):
        pit.assert_known_inputs(["archived_kalshi_market_observation", "totally_unknown_input"])


def test_assert_known_inputs_passes_for_all_known_keys():
    pit.assert_known_inputs(["archived_kalshi_market_observation", "settlement_outcome"])  # does not raise


def test_uncertain_inputs_are_marked_unknown_requires_audit_not_assumed_safe():
    """Spec: 'For anything uncertain, mark it UNKNOWN / REQUIRES_AUDIT rather than assuming PIT-safe.'"""
    for key in ("sharp_sportsbook_observation", "season_to_date_stats", "hitter_snapshot", "pitcher_snapshot"):
        assert pit.PIT_MANIFEST[key].pitStatus == pit.UNKNOWN_REQUIRES_AUDIT, key


def test_settlement_outcome_is_never_pit_safe():
    """A leakage guard entry: outcome data must never be treated as available pre-decision."""
    entry = pit.PIT_MANIFEST["settlement_outcome"]
    assert entry.pitStatus == pit.UNAVAILABLE_HISTORICALLY
    assert not pit.is_pit_safe_for_e2_historical("settlement_outcome")


def test_archived_kalshi_observation_is_pit_safe_for_e2():
    assert pit.is_pit_safe_for_e2_historical("archived_kalshi_market_observation") is True


def test_unknown_requires_audit_inputs_are_never_pit_safe_for_e2():
    for key in ("sharp_sportsbook_observation", "season_to_date_stats"):
        assert pit.is_pit_safe_for_e2_historical(key) is False


# ── Hardening pass item 1: role-based PIT compatibility ────────────────────

def test_e2_predictive_input_with_unknown_requires_audit_status_fails():
    ok, reason = pit.check_predictive_compatibility("season_to_date_stats", "E2_PIT_HISTORICAL")
    assert ok is False
    assert reason


def test_e3_predictive_input_with_unknown_requires_audit_status_fails():
    ok, _ = pit.check_predictive_compatibility("sharp_sportsbook_observation", "E3_WALK_FORWARD_HOLDOUT")
    assert ok is False


def test_e0_predictive_input_with_unknown_requires_audit_status_is_allowed():
    ok, reason = pit.check_predictive_compatibility("season_to_date_stats", "E0_DESCRIPTIVE")
    assert ok is True
    assert reason is None


def test_e4_predictive_input_prospective_only_status_is_allowed():
    """PROSPECTIVE_ONLY inputs are exactly what E4 is for -- must not be blocked there."""
    ok, _ = pit.check_predictive_compatibility("lineup_status_official", "E4_PROSPECTIVE_SHADOW")
    assert ok is True


def test_e2_predictive_input_prospective_only_status_fails():
    """PROSPECTIVE_ONLY must not be treated as historical PIT-safe automatically."""
    ok, _ = pit.check_predictive_compatibility("lineup_status_official", "E2_PIT_HISTORICAL")
    assert ok is False


def test_unavailable_historically_predictive_input_fails_at_every_evidence_level():
    for level in ("E0_DESCRIPTIVE", "E1_RECONSTRUCTED_RETROSPECTIVE", "E2_PIT_HISTORICAL", "E4_PROSPECTIVE_SHADOW"):
        ok, _ = pit.check_predictive_compatibility("weather_input", level)
        assert ok is False, level


def test_settlement_outcome_allowed_as_evaluation_target_role():
    pit.validate_pit_requirement("settlement_outcome", pit.ROLE_EVALUATION_TARGET, "E2_PIT_HISTORICAL")  # does not raise


def test_settlement_outcome_rejected_as_predictive_input_role():
    with pytest.raises(ValueError):
        pit.validate_pit_requirement("settlement_outcome", pit.ROLE_PREDICTIVE_INPUT, "E0_DESCRIPTIVE")


def test_closing_quote_allowed_as_evaluation_target_role():
    pit.validate_pit_requirement("kalshi_closing_market_quote", pit.ROLE_EVALUATION_TARGET, "E2_PIT_HISTORICAL")  # does not raise


def test_closing_quote_rejected_as_predictive_input_role_at_any_evidence_level():
    for level in ("E0_DESCRIPTIVE", "E2_PIT_HISTORICAL", "E4_PROSPECTIVE_SHADOW"):
        with pytest.raises(ValueError):
            pit.validate_pit_requirement("kalshi_closing_market_quote", pit.ROLE_PREDICTIVE_INPUT, level)


def test_validate_pit_requirements_checks_every_entry_in_dict():
    with pytest.raises(ValueError):
        pit.validate_pit_requirements(
            {"archived_kalshi_market_observation": pit.ROLE_PREDICTIVE_INPUT, "settlement_outcome": pit.ROLE_PREDICTIVE_INPUT},
            "E2_PIT_HISTORICAL",
        )


def test_validate_pit_requirement_rejects_unknown_role():
    with pytest.raises(ValueError):
        pit.validate_pit_requirement("archived_kalshi_market_observation", "NOT_A_REAL_ROLE", "E2_PIT_HISTORICAL")
