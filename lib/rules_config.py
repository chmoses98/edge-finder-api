"""
lib/rules_config.py
======================
Production Reliability and Settlement Recovery milestone: structural
validation for config/rules.json at load time.

Before this milestone, config/rules.json (the "machine-readable model
configuration... source of truth for all numeric thresholds" per its own
_comment field) had no schema of any kind -- a hand-edit that dropped a
required key, mistyped a number as a string, or put a win rate outside
[0, 1] would only surface later, indirectly, wherever some downstream
reader happened to touch the broken field (or never surface at all, if
nothing reads that field yet).

Only two call sites in this repo actually parse config/rules.json today
(confirmed by grep across the whole tree): lib/edgelab/recommendations.py's
load_model_covered_series() and lib/edgelab/model_evaluation.py's
_model_config_version(). Both are EdgeLab research-only readers, not the
live production betting/pricing pipeline (which hardcodes its thresholds
directly in code, not via this file) -- so this validation is a research-
data-quality gate, not a change to any handicapping/pricing/staking
behavior, and it validates STRUCTURE only. No numeric value in
config/rules.json is read, altered, or reinterpreted by this module.

Deliberately hand-rolled against a plain dict, no jsonschema dependency --
same precedent as lib/edgelab/schema.py ("not currently installed in this
repo"). Not a full JSON Schema implementation.

Backward compatibility: only the sections below marked required=True must
be present. Every other top-level section in the current file (
hard_gates_T1, soft_gates_T2, signal_hierarchy, park_factors,
elite_offense_thresholds, run_projection_constraints,
offense_baseline_weights, regression_weights_by_pitcher_type,
clv_targets_by_market, model_health_targets, and any "_"-prefixed
metadata field) is validated for shape ONLY IF PRESENT -- a future,
genuinely optional field being absent is never an error.
"""

import json
import os

RULES_PATH = os.path.join("config", "rules.json")

REQUIRED_TOP_LEVEL_SECTIONS = (
    "calibration",
    "edge_thresholds",
    "base_sizes",
    "multipliers",
    "market_list",
    "validation",
)

OPTIONAL_TOP_LEVEL_SECTIONS = (
    "hard_gates_T1",
    "soft_gates_T2",
    "signal_hierarchy",
    "park_factors",
    "elite_offense_thresholds",
    "run_projection_constraints",
    "offense_baseline_weights",
    "regression_weights_by_pitcher_type",
    "clv_targets_by_market",
    "model_health_targets",
)


class RulesConfigError(ValueError):
    """Raised by load_rules_config() when config/rules.json fails structural validation."""


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _err(errors, path, message):
    errors.append(f"config/rules.json: {path}: {message}")


def _validate_calibration(section, errors):
    if not isinstance(section, dict):
        _err(errors, "calibration", f"must be an object, got {type(section).__name__}")
        return
    for tier, spec in section.items():
        prefix = f"calibration.{tier}"
        if not isinstance(spec, dict):
            _err(errors, prefix, f"must be an object, got {type(spec).__name__}")
            continue
        for field in ("factor", "n_settled", "wr", "min_n_to_update"):
            if field not in spec:
                _err(errors, prefix, f"missing required field '{field}'")
        if "factor" in spec and not _is_number(spec["factor"]):
            _err(errors, prefix, "'factor' must be a number")
        if "wr" in spec and _is_number(spec["wr"]) and not (0.0 <= spec["wr"] <= 1.0):
            _err(errors, prefix, f"'wr' must be within [0, 1], got {spec['wr']!r}")
        for field in ("n_settled", "min_n_to_update"):
            if field in spec and not (isinstance(spec[field], int) and not isinstance(spec[field], bool) and spec[field] >= 0):
                _err(errors, prefix, f"'{field}' must be a non-negative integer")


def _validate_edge_thresholds(section, errors):
    if not isinstance(section, dict):
        _err(errors, "edge_thresholds", f"must be an object, got {type(section).__name__}")
        return
    for tier, spec in section.items():
        prefix = f"edge_thresholds.{tier}"
        if not isinstance(spec, dict):
            _err(errors, prefix, f"must be an object, got {type(spec).__name__}")
            continue
        if "min_calibrated_pct" not in spec:
            _err(errors, prefix, "missing required field 'min_calibrated_pct'")
        elif not _is_number(spec["min_calibrated_pct"]) or spec["min_calibrated_pct"] < 0:
            _err(errors, prefix, "'min_calibrated_pct' must be a non-negative number")


def _validate_base_sizes(section, errors):
    if not isinstance(section, dict):
        _err(errors, "base_sizes", f"must be an object, got {type(section).__name__}")
        return
    if not section:
        _err(errors, "base_sizes", "must not be empty")
        return
    for key, value in section.items():
        if not _is_number(value) or value < 0:
            _err(errors, f"base_sizes.{key}", f"must be a non-negative number, got {value!r}")


def _validate_multipliers(section, errors):
    if not isinstance(section, dict):
        _err(errors, "multipliers", f"must be an object, got {type(section).__name__}")
        return
    for market, spec in section.items():
        prefix = f"multipliers.{market}"
        if not isinstance(spec, dict):
            _err(errors, prefix, f"must be an object, got {type(spec).__name__}")
            continue
        for field in ("value", "n", "status"):
            if field not in spec:
                _err(errors, prefix, f"missing required field '{field}'")
        if "value" in spec and (not _is_number(spec["value"]) or spec["value"] < 0):
            _err(errors, prefix, "'value' must be a non-negative number")
        if "n" in spec and not (isinstance(spec["n"], int) and not isinstance(spec["n"], bool) and spec["n"] >= 0):
            _err(errors, prefix, "'n' must be a non-negative integer")
        if "wr" in spec and spec["wr"] is not None and _is_number(spec["wr"]) and not (0.0 <= spec["wr"] <= 1.0):
            _err(errors, prefix, f"'wr' must be within [0, 1], got {spec['wr']!r}")
        if "status" in spec and not isinstance(spec["status"], str):
            _err(errors, prefix, "'status' must be a string")


def _validate_market_list(section, errors):
    if not isinstance(section, list):
        _err(errors, "market_list", f"must be an array, got {type(section).__name__}")
        return
    if not section:
        _err(errors, "market_list", "must not be empty")
        return
    seen_ids = set()
    for i, entry in enumerate(section):
        prefix = f"market_list[{i}]"
        if not isinstance(entry, dict):
            _err(errors, prefix, f"must be an object, got {type(entry).__name__}")
            continue
        for field in ("id", "name", "series"):
            if field not in entry:
                _err(errors, prefix, f"missing required field '{field}'")
        if "id" in entry:
            if not (isinstance(entry["id"], int) and not isinstance(entry["id"], bool)):
                _err(errors, prefix, "'id' must be an integer")
            elif entry["id"] in seen_ids:
                _err(errors, prefix, f"duplicate 'id' {entry['id']!r}")
            else:
                seen_ids.add(entry["id"])
        if "name" in entry and not isinstance(entry["name"], str):
            _err(errors, prefix, "'name' must be a string")
        if "series" in entry and not isinstance(entry["series"], str):
            _err(errors, prefix, "'series' must be a string")
        if "required" in entry and not isinstance(entry["required"], bool):
            _err(errors, prefix, "'required' must be a boolean")


def _validate_validation_section(section, errors):
    if not isinstance(section, dict):
        _err(errors, "validation", f"must be an object, got {type(section).__name__}")
        return
    for field in ("required_per_game", "required_per_market_row", "rejection_required_if_no_bet",
                  "min_qualifying_bets_full_slate"):
        if field not in section:
            _err(errors, "validation", f"missing required field '{field}'")
    for field in ("required_per_game", "required_per_market_row"):
        if field in section:
            value = section[field]
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                _err(errors, f"validation.{field}", "must be an array of strings")
    if "rejection_required_if_no_bet" in section and not isinstance(section["rejection_required_if_no_bet"], bool):
        _err(errors, "validation.rejection_required_if_no_bet", "must be a boolean")
    if "min_qualifying_bets_full_slate" in section:
        value = section["min_qualifying_bets_full_slate"]
        if not (isinstance(value, int) and not isinstance(value, bool) and value >= 0):
            _err(errors, "validation.min_qualifying_bets_full_slate", "must be a non-negative integer")


_SECTION_VALIDATORS = {
    "calibration": _validate_calibration,
    "edge_thresholds": _validate_edge_thresholds,
    "base_sizes": _validate_base_sizes,
    "multipliers": _validate_multipliers,
    "market_list": _validate_market_list,
    "validation": _validate_validation_section,
}


def validate_rules_config(rules: dict) -> list:
    """
    Returns a list of human-readable error strings; empty list means
    valid. Never raises -- callers (including load_rules_config below)
    decide what to do with the errors. Mirrors lib/edgelab/schema.py's
    validate_record() convention.
    """
    errors = []
    if not isinstance(rules, dict):
        return [f"config/rules.json: root must be an object, got {type(rules).__name__}"]

    for section_name in REQUIRED_TOP_LEVEL_SECTIONS:
        if section_name not in rules:
            _err(errors, section_name, "missing required top-level section")

    for section_name, validator in _SECTION_VALIDATORS.items():
        if section_name in rules:
            validator(rules[section_name], errors)

    for section_name in OPTIONAL_TOP_LEVEL_SECTIONS:
        if section_name in rules and not isinstance(rules[section_name], dict):
            _err(errors, section_name, f"must be an object if present, got {type(rules[section_name]).__name__}")

    return errors


def load_rules_config(path: str = RULES_PATH, strict: bool = True) -> dict:
    """
    Parses and structurally validates config/rules.json. Raises
    RulesConfigError (with every error found, not just the first) when
    strict=True and the file fails validation. Does not alter any value
    read from the file -- returns the parsed dict unchanged.

    A missing FILE is not this function's concern (existing call sites
    already have their own, deliberately lenient, missing-file handling)
    -- callers should check os.path.exists() themselves before calling,
    exactly as they did before this milestone.
    """
    with open(path) as f:
        rules = json.load(f)
    errors = validate_rules_config(rules)
    if errors and strict:
        raise RulesConfigError(
            f"config/rules.json failed validation with {len(errors)} error(s):\n" + "\n".join(f"  - {e}" for e in errors)
        )
    return rules
