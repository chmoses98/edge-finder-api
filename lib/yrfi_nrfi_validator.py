#!/usr/bin/env python3
"""
lib/yrfi_nrfi_validator.py
===========================
YRFI/NRFI Input Validator

YRFI/NRFI bets are only valid when first-inning-specific inputs are used.
Bullpen exposure, full-game metrics, and general starter quality are NOT valid
inputs for first-inning market predictions.

ALLOWED inputs:
  - first_inning_xera_away / first_inning_xera_home (from Baseball Savant 1st-inn xERA)
  - first_inning_form_away / first_inning_form_home (last 5 starts 1st-inning results)
  - first_inning_run_rate_away / first_inning_run_rate_home (team 1st-inn R/game)
  - leadoff_quality_away / leadoff_quality_home
  - top_order_quality_away / top_order_quality_home (1-3 or 1-4 in lineup)
  - park_first_inning_factor (park factor specific to 1st inning)
  - weather_first_inning (wind/dome affecting first inning scoring)
  - umpire_run_environment (umpire's historical run environment impact)
  - starter_first_inning_weakness (specific 1st-inning pattern)
  - lambda_first_inning (combined first-inning lambda for Poisson)
  - first_inning_scoring_rate_season (team's 1st-inning scoring rate, season)
  - first_inning_scoring_rate_l15 (team's 1st-inning scoring rate, last 15 games)

DISALLOWED inputs (must reject if present as primary signals):
  - bullpen_exposure (F1 markets don't use bullpen)
  - bullpen_weakness / bullpen_xfip (full-game bullpen metrics)
  - short_starter_leash / early_hook (relates to bullpen involvement)
  - avg_innings_per_start (full-game metric)
  - bullpen_fatigue / pen_fatigued (full-game bullpen fatigue)
  - full_game_total (not converted to 1st-inning lambda)
  - generic_era / generic_xfip (without 1st-inning split)
  - total_runs_projection (full-game, not 1st-inning)

Required output fields for every YRFI/NRFI:
  - lambda_used: float — the lambda used for Poisson calculation
  - lambda_formula: str — how lambda was computed
  - lambda_is_first_inning_specific: bool — True if based on 1st-inning rates
  - lambda_derived_from_full_game: bool — True if derived from full-game total
  - park_first_inning_included: bool
  - team_first_inning_rates_included: bool
  - independent_poisson_first_inning_valid: bool
"""

from typing import Optional, List, Dict, Tuple

# ── Allowed first-inning input keys ──────────────────────────────────────────
ALLOWED_FIRST_INNING_KEYS = {
    "first_inning_xera_away",
    "first_inning_xera_home",
    "first_inning_form_away",
    "first_inning_form_home",
    "first_inning_run_rate_away",
    "first_inning_run_rate_home",
    "leadoff_quality_away",
    "leadoff_quality_home",
    "top_order_quality_away",
    "top_order_quality_home",
    "top3_lineup_away",
    "top3_lineup_home",
    "top4_lineup_away",
    "top4_lineup_home",
    "park_first_inning_factor",
    "weather_first_inning",
    "umpire_run_environment",
    "starter_first_inning_weakness",
    "starter_first_inning_xera",
    "lambda_first_inning",
    "first_inning_scoring_rate_season",
    "first_inning_scoring_rate_l15",
    "first_inning_run_rate_season",
    "first_inning_run_rate_l15",
    "away_first_inning_rate",
    "home_first_inning_rate",
    # Allowed as context (but not primary drivers)
    "nrfi_score",
    "yrfi_score",
    "lean",
    "leanStrength",
    "reasons",
    # Standard metadata
    "market",
    "side",
    "edge",
    "modelProb",
    "kalshiPct",
    "confidence",
    "ticker",
    "betSize",
    "trackingType",
}

# ── Disallowed keys (bullpen/full-game factors) ───────────────────────────────
DISALLOWED_KEYS = {
    "bullpen_exposure",
    "bullpen_weakness",
    "bullpen_xfip",
    "bullpen_era",
    "bullpenXFIP",
    "bullpenERA",
    "short_starter_leash",
    "early_hook",
    "avg_innings_per_start",
    "avgIP",
    "bullpen_fatigue",
    "bullpenFatigued",
    "pen_fatigued",
    "pen_arrives_inning",
    "full_game_bullpen_fatigue",
}

# Disallowed explanation phrases (in reasons[] or explanation text)
DISALLOWED_PHRASES = [
    "bullpen",
    "pen arrives",
    "short leash",
    "average innings per start",
    "avg innings",
    "bullpen arrives",
    "full-game bullpen",
    "bullpen fatigue",
    "relievers",
    "reliever usage",
    "innings 2-3",
    "by inning 2",
    "by inning 3",
]


class YRFINRFIValidationError(ValueError):
    """Raised when YRFI/NRFI inputs contain disallowed factors."""
    def __init__(self, message, violations=None):
        super().__init__(message)
        self.violations = violations or []


def check_explanation_for_disallowed_phrases(explanation: str) -> List[str]:
    """
    Check explanation text for disallowed phrases.
    Returns list of found disallowed phrases.
    """
    if not explanation:
        return []
    lower = explanation.lower()
    found = []
    for phrase in DISALLOWED_PHRASES:
        if phrase.lower() in lower:
            found.append(phrase)
    return found


def validate_yrfi_nrfi_inputs(bet_dict) -> Tuple[bool, List[str]]:
    """
    Validate that YRFI/NRFI bet uses only first-inning-specific inputs.

    Args:
        bet_dict: dict with market data

    Returns:
        (is_valid, violations) where violations is list of violation strings
    """
    violations = []

    # Check factors dict for disallowed keys
    factors = bet_dict.get("factors", {})
    if isinstance(factors, dict):
        for key in factors:
            if key.lower() in {k.lower() for k in DISALLOWED_KEYS}:
                violations.append(f"Disallowed factor key: '{key}' (bullpen/full-game metric)")

    # Check reasons list
    reasons = bet_dict.get("reasons", [])
    if isinstance(reasons, list):
        for reason in reasons:
            bad_phrases = check_explanation_for_disallowed_phrases(str(reason))
            for phrase in bad_phrases:
                violations.append(f"Disallowed phrase in reasons: '{phrase}' in '{str(reason)[:80]}'")

    # Check notes field
    notes = bet_dict.get("notes", "")
    if notes:
        # Notes may reference bullpen in context — only flag as driver if primary
        # For now just flag as warning (not hard violation)
        bad = check_explanation_for_disallowed_phrases(str(notes))
        for phrase in bad:
            violations.append(f"Warning — notes reference disallowed factor: '{phrase}' (may be context only)")

    return len(violations) == 0, violations


def validate_yrfi_nrfi_output_fields(bet_dict) -> Tuple[bool, List[str]]:
    """
    Validate that a YRFI/NRFI output includes all required tracking fields.

    Returns:
        (is_valid, missing_fields)
    """
    required_fields = [
        "lambda_used",
        "lambda_formula",
        "lambda_is_first_inning_specific",
        "lambda_derived_from_full_game",
        "park_first_inning_included",
        "team_first_inning_rates_included",
        "independent_poisson_first_inning_valid",
    ]

    missing = []
    for field in required_fields:
        if field not in bet_dict:
            missing.append(field)

    return len(missing) == 0, missing


def build_yrfi_nrfi_output_template(
    lambda_used: float,
    lambda_formula: str,
    is_first_inning_specific: bool,
    derived_from_full_game: bool,
    park_included: bool,
    team_rates_included: bool,
    poisson_valid: bool,
) -> dict:
    """
    Build the required output fields for a YRFI/NRFI bet.

    Args:
        lambda_used: the Poisson lambda used
        lambda_formula: human-readable formula description
        is_first_inning_specific: True if lambda is based on 1st-inning rates
        derived_from_full_game: True if derived from full-game total (not ideal)
        park_included: True if park/team 1st-inning rates included
        team_rates_included: True if team 1st-inning scoring rates included
        poisson_valid: True if independent Poisson check is 1st-inning valid

    Returns:
        dict with all required fields
    """
    return {
        "lambda_used": lambda_used,
        "lambda_formula": lambda_formula,
        "lambda_is_first_inning_specific": is_first_inning_specific,
        "lambda_derived_from_full_game": derived_from_full_game,
        "park_first_inning_included": park_included,
        "team_first_inning_rates_included": team_rates_included,
        "independent_poisson_first_inning_valid": poisson_valid,
    }


def check_probe_eligibility(bet_dict, block_classes_fired=None) -> Tuple[bool, str]:
    """
    Check if a YRFI/NRFI bet is eligible for REAL_PROBE.

    Eligible when:
    - All first-inning-specific fields exist
    - Edge clears lower probe threshold
    - CLV capture available (has ticker)
    - No invalid explanation (no bullpen/full-game phrases)

    Returns:
        (is_eligible, reason)
    """
    block_classes = block_classes_fired or []

    # Check for hard blocks
    from lib.tracking_type import BLOCK_CLASS_DATA_HARD, BLOCK_CLASS_MARKET_MECHANICS_HARD
    for bc in block_classes:
        if bc in (BLOCK_CLASS_DATA_HARD, BLOCK_CLASS_MARKET_MECHANICS_HARD):
            return False, f"Hard block fired: {bc}"

    # Check output fields present
    has_fields, missing = validate_yrfi_nrfi_output_fields(bet_dict)
    if not has_fields:
        return False, f"Missing required first-inning fields: {missing}"

    # Check lambda is first-inning specific
    if not bet_dict.get("lambda_is_first_inning_specific"):
        return False, "Lambda is not first-inning specific"

    # Check for disallowed inputs
    is_valid_inputs, violations = validate_yrfi_nrfi_inputs(bet_dict)
    if not is_valid_inputs:
        hard_violations = [v for v in violations if "Warning" not in v]
        if hard_violations:
            return False, f"Disallowed inputs: {hard_violations[0]}"

    # Ticker required
    if not (bet_dict.get("ticker") or bet_dict.get("marketTicker")):
        return False, "No Kalshi ticker — CLV capture unavailable"

    # Edge threshold
    edge = bet_dict.get("edge") or bet_dict.get("edgePct") or 0
    probe_threshold = 0.8  # Lower threshold for probes
    if float(edge) < probe_threshold:
        return False, f"Edge {edge}% below probe threshold {probe_threshold}%"

    return True, "REAL_PROBE eligible"


if __name__ == "__main__":
    # Test: disallowed factors
    bad_bet = {
        "market": "YRFI",
        "factors": {
            "bullpen_exposure": 0.3,
            "first_inning_xera_away": 3.5,
        },
        "reasons": ["starter has short leash", "bullpen arrives by inning 2"],
        "edge": 2.5,
    }

    is_valid, violations = validate_yrfi_nrfi_inputs(bad_bet)
    print(f"Valid: {is_valid}")
    print(f"Violations: {violations}")

    # Good bet
    good_bet = {
        "market": "YRFI",
        "factors": {
            "first_inning_xera_away": 3.5,
            "first_inning_run_rate_home": 0.65,
        },
        "reasons": ["Away starter weak in 1st innings (xERA 3.5)", "Home team scores 0.65 R/game in 1st"],
        "lambda_used": 0.85,
        "lambda_formula": "avg(away_1inn_xera_lambda, home_1inn_rate)",
        "lambda_is_first_inning_specific": True,
        "lambda_derived_from_full_game": False,
        "park_first_inning_included": True,
        "team_first_inning_rates_included": True,
        "independent_poisson_first_inning_valid": True,
        "ticker": "KXMLBRFI-26JUN14ATLNYM",
        "edge": 2.5,
    }

    is_valid2, violations2 = validate_yrfi_nrfi_inputs(good_bet)
    print(f"\nGood bet valid: {is_valid2}, violations: {violations2}")
    eligible, reason = check_probe_eligibility(good_bet)
    print(f"Probe eligible: {eligible}, reason: {reason}")
