#!/usr/bin/env python3
"""
tests/edgelab/test_player_stats.py
=======================================
Coverage for lib/edgelab/player_stats.py (GitHub issue #43): pure
extraction/derivation of one player-prop family's authoritative final
stat value -- never estimated, never rounded, rejects malformed or
internally-inconsistent source fields rather than guessing.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import math

from lib.edgelab.player_stats import (
    extract_pitching_outs,
    extract_stat_value,
    extract_total_bases,
    parse_nonnegative_int,
)


def test_pitcher_strikeouts():
    value, category, fields, reason = extract_stat_value("pitcher_strikeouts", None, {"strikeOuts": 9})
    assert (value, category, reason) == (9, "pitching", None)
    assert fields == {"strikeOuts": 9}


def test_pitcher_outs_from_direct_field():
    outs, fields, reason = extract_pitching_outs({"outs": 17, "inningsPitched": "5.2"})
    assert outs == 17
    assert reason is None
    assert fields == {"outs": 17}


def test_pitcher_outs_from_innings_pitched_5_0():
    outs, fields, reason = extract_pitching_outs({"inningsPitched": "5.0"})
    assert outs == 15
    assert reason is None
    assert fields["outsDerivedFromInningsPitched"] is True


def test_pitcher_outs_from_innings_pitched_5_1():
    outs, _, reason = extract_pitching_outs({"inningsPitched": "5.1"})
    assert outs == 16
    assert reason is None


def test_pitcher_outs_from_innings_pitched_5_2():
    outs, _, reason = extract_pitching_outs({"inningsPitched": "5.2"})
    assert outs == 17
    assert reason is None


def test_pitcher_outs_invalid_innings_pitched_value_rejected():
    outs, _, reason = extract_pitching_outs({"inningsPitched": "5.3"})
    assert outs is None
    assert reason == "invalid_innings_pitched_format"


def test_pitcher_outs_non_numeric_whole_part_rejected():
    outs, _, reason = extract_pitching_outs({"inningsPitched": "abc.1"})
    assert outs is None
    assert reason == "invalid_innings_pitched_format"


def test_pitcher_outs_missing_pitching_stat():
    outs, _, reason = extract_pitching_outs(None)
    assert outs is None
    assert reason == "missing_pitching_stat"


def test_pitcher_outs_missing_innings_pitched():
    outs, _, reason = extract_pitching_outs({"someOtherField": 1})
    assert outs is None
    assert reason == "missing_innings_pitched"


def test_hitter_hits():
    value, category, fields, reason = extract_stat_value("hitter_hits", {"hits": 3}, None)
    assert (value, category, reason) == (3, "batting", None)


def test_hitter_total_bases_direct_field():
    total_bases, fields, reason = extract_total_bases({"totalBases": 7})
    assert total_bases == 7
    assert reason is None


def test_hitter_total_bases_derived_from_components():
    # 2 hits: 1 double, 1 single -> singles=2-1-0-0=1, TB = 1 + 2*1 = 3
    total_bases, fields, reason = extract_total_bases({"hits": 2, "doubles": 1, "triples": 0, "homeRuns": 0})
    assert total_bases == 3
    assert reason is None
    assert fields["singlesDerived"] == 1


def test_hitter_total_bases_inconsistent_components_rejected():
    # doubles+triples+homeRuns > hits -> negative singles -> reject
    total_bases, fields, reason = extract_total_bases({"hits": 1, "doubles": 1, "triples": 1, "homeRuns": 0})
    assert total_bases is None
    assert reason == "inconsistent_total_base_components"


def test_hitter_total_bases_missing_components_rejected():
    total_bases, _, reason = extract_total_bases({"hits": 2})
    assert total_bases is None
    assert reason == "missing_total_bases_components"


def test_hitter_total_bases_negative_component_rejected():
    total_bases, _, reason = extract_total_bases({"hits": 2, "doubles": -1, "triples": 0, "homeRuns": 0})
    assert total_bases is None
    assert reason == "invalid_total_bases_components"


def test_hitter_hits_runs_rbis():
    value, category, fields, reason = extract_stat_value(
        "hitter_hits_runs_rbis", {"hits": 2, "runs": 1, "rbi": 3}, None,
    )
    assert (value, category, reason) == (6, "batting", None)


def test_hitter_rbis():
    value, category, fields, reason = extract_stat_value("hitter_rbis", {"rbi": 2}, None)
    assert (value, category, reason) == (2, "batting", None)


def test_hitter_stolen_bases():
    value, category, fields, reason = extract_stat_value("hitter_stolen_bases", {"stolenBases": 1}, None)
    assert (value, category, reason) == (1, "batting", None)


def test_official_appearance_with_zero_relevant_statistic_is_valid_not_missing():
    value, _, _, reason = extract_stat_value("hitter_hits", {"hits": 0}, None)
    assert value == 0
    assert reason is None

    value, _, _, reason = extract_stat_value("pitcher_strikeouts", None, {"strikeOuts": 0})
    assert value == 0
    assert reason is None


def test_unrecognized_family():
    value, category, fields, reason = extract_stat_value("not_a_real_family", {}, {})
    assert value is None
    assert reason == "unrecognized_player_prop_family"


def test_missing_batting_stat_for_hits():
    value, _, _, reason = extract_stat_value("hitter_hits", None, None)
    assert value is None
    assert reason == "missing_hits_stat"


# ── parse_nonnegative_int strict validation (GitHub issue #43 correction round) ──

def test_parse_nonnegative_int_accepts_plain_int():
    assert parse_nonnegative_int(3) == 3
    assert parse_nonnegative_int(0) == 0


def test_parse_nonnegative_int_accepts_exact_whole_float():
    assert parse_nonnegative_int(3.0) == 3
    assert parse_nonnegative_int(0.0) == 0


def test_parse_nonnegative_int_accepts_digit_string():
    assert parse_nonnegative_int("3") == 3
    assert parse_nonnegative_int("0") == 0


def test_parse_nonnegative_int_rejects_non_integral_float():
    assert parse_nonnegative_int(3.5) is None


def test_parse_nonnegative_int_rejects_decimal_string():
    assert parse_nonnegative_int("3.5") is None


def test_parse_nonnegative_int_rejects_negative_int():
    assert parse_nonnegative_int(-1) is None


def test_parse_nonnegative_int_rejects_negative_float():
    assert parse_nonnegative_int(-1.0) is None


def test_parse_nonnegative_int_rejects_negative_string():
    assert parse_nonnegative_int("-1") is None


def test_parse_nonnegative_int_rejects_booleans():
    assert parse_nonnegative_int(True) is None
    assert parse_nonnegative_int(False) is None


def test_parse_nonnegative_int_rejects_nan_and_infinity():
    assert parse_nonnegative_int(float("nan")) is None
    assert parse_nonnegative_int(float("inf")) is None
    assert parse_nonnegative_int(float("-inf")) is None


def test_parse_nonnegative_int_rejects_malformed_strings():
    for bad in ("abc", "", "   ", "3.5.1", "+3", "١٢٣"):
        assert parse_nonnegative_int(bad) is None, repr(bad)


def test_parse_nonnegative_int_accepts_whitespace_padded_digit_string():
    assert parse_nonnegative_int("3 ") == 3
    assert parse_nonnegative_int(" 3") == 3


def test_parse_nonnegative_int_rejects_arbitrary_objects():
    assert parse_nonnegative_int(None) is None
    assert parse_nonnegative_int([]) is None
    assert parse_nonnegative_int({}) is None
    assert parse_nonnegative_int(object()) is None


def test_parse_nonnegative_int_never_truncates():
    """int(3.5) == 3 in plain Python -- this parser must NEVER do that."""
    assert parse_nonnegative_int(3.999999) is None
    assert parse_nonnegative_int("3.999999") is None


# ── Strict validation applied at every counting-stat call site ─────────────

def test_strikeouts_rejects_non_integral_float():
    value, _, _, reason = extract_stat_value("pitcher_strikeouts", None, {"strikeOuts": 9.5})
    assert value is None
    assert reason == "invalid_strikeOuts"


def test_hits_rejects_decimal_string():
    value, _, _, reason = extract_stat_value("hitter_hits", {"hits": "2.5"}, None)
    assert value is None
    assert reason == "invalid_hits"


def test_rbis_rejects_negative():
    value, _, _, reason = extract_stat_value("hitter_rbis", {"rbi": -1}, None)
    assert value is None
    assert reason == "invalid_rbi"


def test_stolen_bases_rejects_boolean():
    value, _, _, reason = extract_stat_value("hitter_stolen_bases", {"stolenBases": True}, None)
    assert value is None
    assert reason == "invalid_stolenBases"


def test_direct_pitcher_outs_rejects_non_integral_float():
    outs, _, reason = extract_pitching_outs({"outs": 17.5, "inningsPitched": None})
    assert outs is None
    # falls through to inningsPitched, which is also unusable here
    assert reason == "missing_innings_pitched"


def test_direct_total_bases_rejects_decimal_string():
    total_bases, fields, reason = extract_total_bases({"totalBases": "5.5", "hits": 2, "doubles": 0, "triples": 0, "homeRuns": 0})
    # falls through to component derivation since the direct field is malformed
    assert total_bases == 2
    assert reason is None


def test_total_bases_component_rejects_non_integral_float():
    total_bases, fields, reason = extract_total_bases({"hits": 2.5, "doubles": 0, "triples": 0, "homeRuns": 0})
    assert total_bases is None
    assert reason == "invalid_total_bases_components"


def test_total_bases_component_rejects_negative():
    total_bases, fields, reason = extract_total_bases({"hits": 2, "doubles": -1, "triples": 0, "homeRuns": 0})
    assert total_bases is None
    assert reason == "invalid_total_bases_components"


def test_total_bases_component_rejects_boolean():
    total_bases, fields, reason = extract_total_bases({"hits": True, "doubles": 0, "triples": 0, "homeRuns": 0})
    assert total_bases is None
    assert reason == "invalid_total_bases_components"


def test_hits_runs_rbis_component_rejects_non_integral_float():
    value, _, fields, reason = extract_stat_value("hitter_hits_runs_rbis", {"hits": 2.5, "runs": 1, "rbi": 0}, None)
    assert value is None
    assert reason == "invalid_hits_runs_rbi_components"


def test_hits_runs_rbis_component_rejects_malformed_string():
    value, _, fields, reason = extract_stat_value("hitter_hits_runs_rbis", {"hits": "abc", "runs": 1, "rbi": 0}, None)
    assert value is None
    assert reason == "invalid_hits_runs_rbi_components"


def test_innings_pitched_rejects_malformed_decimal_component():
    for bad_frac in (".3", ".4", ".5", ".6", ".7", ".8", ".9"):
        outs, _, reason = extract_pitching_outs({"inningsPitched": f"5{bad_frac}"})
        assert outs is None, bad_frac
        assert reason == "invalid_innings_pitched_format"


def test_innings_pitched_rejects_negative():
    outs, _, reason = extract_pitching_outs({"inningsPitched": "-5.1"})
    assert outs is None
    assert reason == "invalid_innings_pitched_format"


def test_innings_pitched_rejects_boolean():
    outs, _, reason = extract_pitching_outs({"inningsPitched": True})
    assert outs is None
    assert reason == "invalid_innings_pitched_format"


def test_innings_pitched_rejects_nan_and_inf():
    outs, _, reason = extract_pitching_outs({"inningsPitched": float("nan")})
    assert outs is None
    assert reason == "invalid_innings_pitched_format"
    outs, _, reason = extract_pitching_outs({"inningsPitched": float("inf")})
    assert outs is None
    assert reason == "invalid_innings_pitched_format"


def test_innings_pitched_still_accepts_valid_numeric_form():
    outs, _, reason = extract_pitching_outs({"inningsPitched": 5.1})
    assert outs == 16
    assert reason is None
