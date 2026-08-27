import pytest

from lib.edgelab import evidence_levels as ev


def test_all_six_levels_present_and_ordered():
    assert ev.ORDERED_EVIDENCE_LEVELS == (
        ev.E0_DESCRIPTIVE, ev.E1_RECONSTRUCTED_RETROSPECTIVE, ev.E2_PIT_HISTORICAL,
        ev.E3_WALK_FORWARD_HOLDOUT, ev.E4_PROSPECTIVE_SHADOW, ev.E5_REAL_MONEY_EXECUTION,
    )


def test_validate_evidence_level_rejects_unknown():
    with pytest.raises(ValueError):
        ev.validate_evidence_level("E6_MADE_UP")


def test_e0_and_e1_are_never_promotable():
    assert ev.is_promotable(ev.E0_DESCRIPTIVE) is False
    assert ev.is_promotable(ev.E1_RECONSTRUCTED_RETROSPECTIVE) is False


def test_e2_through_e5_are_promotable():
    for level in (ev.E2_PIT_HISTORICAL, ev.E3_WALK_FORWARD_HOLDOUT, ev.E4_PROSPECTIVE_SHADOW, ev.E5_REAL_MONEY_EXECUTION):
        assert ev.is_promotable(level) is True


def test_meets_minimum_is_rank_ordered():
    assert ev.meets_minimum(ev.E3_WALK_FORWARD_HOLDOUT, ev.E2_PIT_HISTORICAL) is True
    assert ev.meets_minimum(ev.E1_RECONSTRUCTED_RETROSPECTIVE, ev.E2_PIT_HISTORICAL) is False
    assert ev.meets_minimum(ev.E2_PIT_HISTORICAL, ev.E2_PIT_HISTORICAL) is True


def test_rank_rejects_unknown_level():
    with pytest.raises(ValueError):
        ev.rank("NOT_A_LEVEL")


def test_shadow_and_promotion_minimums_are_distinct_and_ordered():
    assert ev.rank(ev.MIN_EVIDENCE_LEVEL_FOR_PROMOTION_CANDIDATE) > ev.rank(ev.MIN_EVIDENCE_LEVEL_FOR_SHADOW_CANDIDATE)
