import pytest

from lib.edgelab import dispositions as disp


def test_all_five_dispositions_named():
    assert disp.ALL_DISPOSITIONS == {disp.REJECT, disp.RESEARCH_CANDIDATE, disp.SHADOW_CANDIDATE, disp.PROMOTION_CANDIDATE, disp.PRODUCTION}


def test_production_excluded_from_automatically_assignable_set():
    assert disp.PRODUCTION not in disp.AUTOMATICALLY_ASSIGNABLE_DISPOSITIONS
    assert disp.AUTOMATICALLY_ASSIGNABLE_DISPOSITIONS == {disp.REJECT, disp.RESEARCH_CANDIDATE, disp.SHADOW_CANDIDATE, disp.PROMOTION_CANDIDATE}


def test_assign_disposition_passes_through_normal_values():
    for value in disp.AUTOMATICALLY_ASSIGNABLE_DISPOSITIONS:
        assert disp.assign_disposition(value) == value


def test_assign_disposition_always_refuses_production():
    with pytest.raises(disp.ProductionDispositionForbiddenError):
        disp.assign_disposition(disp.PRODUCTION)


def test_assign_disposition_has_no_override_parameter():
    """Structural guard: the function signature must not have grown a
    bypass parameter that could let a caller force PRODUCTION through."""
    import inspect
    sig = inspect.signature(disp.assign_disposition)
    assert list(sig.parameters) == ["disposition"]


def test_validate_disposition_rejects_unknown_value():
    with pytest.raises(ValueError):
        disp.validate_disposition("NOT_A_REAL_DISPOSITION")


def test_validate_disposition_accepts_production_as_a_known_name_but_assign_still_refuses():
    """PRODUCTION is a documented vocabulary member (so schemas/reports can
    validate against it), but assign_disposition is the enforcement point."""
    disp.validate_disposition(disp.PRODUCTION)  # does not raise
    with pytest.raises(disp.ProductionDispositionForbiddenError):
        disp.assign_disposition(disp.PRODUCTION)
