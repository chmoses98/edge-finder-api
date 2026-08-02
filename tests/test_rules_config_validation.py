#!/usr/bin/env python3
"""
tests/test_rules_config_validation.py
=========================================
Production Reliability and Settlement Recovery milestone: coverage for
lib/rules_config.py, the structural validator for config/rules.json.
"""
import copy
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.rules_config import (  # noqa: E402
    RulesConfigError,
    load_rules_config,
    validate_rules_config,
)


def _minimal_valid_config():
    return {
        "calibration": {
            "High": {"factor": 0.2, "n_settled": 50, "wr": 0.55, "min_n_to_update": 50},
        },
        "edge_thresholds": {
            "High": {"min_calibrated_pct": 3.0},
        },
        "base_sizes": {
            "High": 4.0,
            "max_per_bet": 8.0,
        },
        "multipliers": {
            "F5_ML": {"value": 1.5, "n": 45, "wr": 0.56, "avg_clv": 2.31, "status": "ACTIVE"},
        },
        "market_list": [
            {"id": 1, "name": "ML_Away", "series": "KXMLBGAME", "required": True},
        ],
        "validation": {
            "required_per_game": ["awayStarter"],
            "required_per_market_row": ["edge"],
            "rejection_required_if_no_bet": True,
            "min_qualifying_bets_full_slate": 12,
        },
    }


class TestRealProductionConfig:

    def test_current_production_config_validates_cleanly(self):
        rules = load_rules_config()  # default path: config/rules.json
        assert validate_rules_config(rules) == []

    def test_load_rules_config_does_not_alter_any_value(self):
        with open(os.path.join(ROOT, "config", "rules.json")) as f:
            raw = json.load(f)
        loaded = load_rules_config()
        assert loaded == raw


class TestMinimalValidConfig:

    def test_minimal_valid_config_has_no_errors(self):
        assert validate_rules_config(_minimal_valid_config()) == []

    def test_load_rules_config_strict_does_not_raise_on_valid_config(self, tmp_path):
        path = tmp_path / "rules.json"
        path.write_text(json.dumps(_minimal_valid_config()))
        result = load_rules_config(str(path))
        assert result["market_list"][0]["name"] == "ML_Away"


class TestMissingRequiredFields:

    @pytest.mark.parametrize("section", [
        "calibration", "edge_thresholds", "base_sizes", "multipliers", "market_list", "validation",
    ])
    def test_missing_top_level_section_is_an_error(self, section):
        config = _minimal_valid_config()
        del config[section]
        errors = validate_rules_config(config)
        assert any(section in e and "missing required top-level section" in e for e in errors)

    def test_missing_calibration_field_is_an_error(self):
        config = _minimal_valid_config()
        del config["calibration"]["High"]["min_n_to_update"]
        errors = validate_rules_config(config)
        assert any("calibration.High" in e and "min_n_to_update" in e for e in errors)

    def test_missing_market_list_entry_field_is_an_error(self):
        config = _minimal_valid_config()
        del config["market_list"][0]["series"]
        errors = validate_rules_config(config)
        assert any("market_list[0]" in e and "series" in e for e in errors)

    def test_missing_validation_field_is_an_error(self):
        config = _minimal_valid_config()
        del config["validation"]["min_qualifying_bets_full_slate"]
        errors = validate_rules_config(config)
        assert any("validation" in e and "min_qualifying_bets_full_slate" in e for e in errors)

    def test_load_rules_config_strict_raises_on_missing_field(self, tmp_path):
        config = _minimal_valid_config()
        del config["base_sizes"]
        path = tmp_path / "rules.json"
        path.write_text(json.dumps(config))
        with pytest.raises(RulesConfigError, match="base_sizes"):
            load_rules_config(str(path))

    def test_load_rules_config_non_strict_returns_dict_without_raising(self, tmp_path):
        config = _minimal_valid_config()
        del config["base_sizes"]
        path = tmp_path / "rules.json"
        path.write_text(json.dumps(config))
        result = load_rules_config(str(path), strict=False)
        assert "base_sizes" not in result


class TestInvalidTypesAndRanges:

    def test_wr_out_of_range_in_calibration_is_an_error(self):
        config = _minimal_valid_config()
        config["calibration"]["High"]["wr"] = 1.5
        errors = validate_rules_config(config)
        assert any("calibration.High" in e and "wr" in e for e in errors)

    def test_wr_out_of_range_in_multipliers_is_an_error(self):
        config = _minimal_valid_config()
        config["multipliers"]["F5_ML"]["wr"] = -0.1
        errors = validate_rules_config(config)
        assert any("multipliers.F5_ML" in e and "wr" in e for e in errors)

    def test_negative_base_size_is_an_error(self):
        config = _minimal_valid_config()
        config["base_sizes"]["High"] = -1.0
        errors = validate_rules_config(config)
        assert any("base_sizes.High" in e for e in errors)

    def test_string_where_number_expected_is_an_error(self):
        config = _minimal_valid_config()
        config["edge_thresholds"]["High"]["min_calibrated_pct"] = "3.0"
        errors = validate_rules_config(config)
        assert any("edge_thresholds.High" in e and "min_calibrated_pct" in e for e in errors)

    def test_market_list_id_must_be_integer(self):
        config = _minimal_valid_config()
        config["market_list"][0]["id"] = "1"
        errors = validate_rules_config(config)
        assert any("market_list[0]" in e and "'id'" in e for e in errors)

    def test_market_list_duplicate_id_is_an_error(self):
        config = _minimal_valid_config()
        config["market_list"].append({"id": 1, "name": "ML_Home", "series": "KXMLBGAME"})
        errors = validate_rules_config(config)
        assert any("duplicate" in e for e in errors)

    def test_market_list_must_not_be_empty(self):
        config = _minimal_valid_config()
        config["market_list"] = []
        errors = validate_rules_config(config)
        assert any("market_list" in e and "empty" in e for e in errors)

    def test_multipliers_negative_value_is_an_error(self):
        config = _minimal_valid_config()
        config["multipliers"]["F5_ML"]["value"] = -1
        errors = validate_rules_config(config)
        assert any("multipliers.F5_ML" in e and "'value'" in e for e in errors)

    def test_root_must_be_an_object(self):
        errors = validate_rules_config([1, 2, 3])
        assert len(errors) == 1
        assert "root must be an object" in errors[0]

    def test_calibration_section_wrong_type_is_an_error(self):
        config = _minimal_valid_config()
        config["calibration"] = ["not", "a", "dict"]
        errors = validate_rules_config(config)
        assert any(e.startswith("config/rules.json: calibration:") for e in errors)


class TestBackwardCompatibleOptionalFields:

    def test_absent_optional_sections_are_not_errors(self):
        config = _minimal_valid_config()
        assert "hard_gates_T1" not in config
        assert "signal_hierarchy" not in config
        assert validate_rules_config(config) == []

    def test_metadata_fields_never_required(self):
        config = _minimal_valid_config()
        assert "_comment" not in config and "_version" not in config and "_updated" not in config
        assert validate_rules_config(config) == []

    def test_present_optional_section_wrong_type_is_still_an_error(self):
        config = _minimal_valid_config()
        config["hard_gates_T1"] = ["not", "a", "dict"]
        errors = validate_rules_config(config)
        assert any("hard_gates_T1" in e for e in errors)

    def test_present_optional_section_as_dict_is_fine(self):
        config = _minimal_valid_config()
        config["hard_gates_T1"] = {"some_rule": "some description"}
        assert validate_rules_config(config) == []


class TestErrorAggregation:

    def test_multiple_errors_are_all_reported_not_just_the_first(self):
        config = _minimal_valid_config()
        del config["base_sizes"]
        del config["market_list"]
        errors = validate_rules_config(config)
        assert len(errors) >= 2

    def test_load_rules_config_error_message_lists_every_error(self, tmp_path):
        config = _minimal_valid_config()
        del config["base_sizes"]
        del config["multipliers"]
        path = tmp_path / "rules.json"
        path.write_text(json.dumps(config))
        with pytest.raises(RulesConfigError) as exc_info:
            load_rules_config(str(path))
        message = str(exc_info.value)
        assert "base_sizes" in message
        assert "multipliers" in message


class TestDeepCopyIsolation:

    def test_validate_rules_config_never_mutates_input(self):
        config = _minimal_valid_config()
        before = copy.deepcopy(config)
        validate_rules_config(config)
        assert config == before
