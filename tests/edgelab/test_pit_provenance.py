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
