#!/usr/bin/env python3
"""
tests/research/test_pitcher_workload_evidence_quality.py
=============================================================
Regression tests for the workload evidence-quality provenance hierarchy
(MLB Model Expression Guardrails milestone) added to
lib/research/pitcher_workload_projection.py: VERIFIED_WORKLOAD_SIGNAL /
SUPPORTIVE_USAGE_TREND / WEAK_INFERENCE / NO_WORKLOAD_SIGNAL.

Motivating contrast from the spec: the Aug 14 postmortem's "Chase Burns
workload under was justified by an unsupported workload narrative" (a
WEAK_INFERENCE-shaped case) vs. the Aug 16 "Hunter Brown under-18-outs"
wager, grounded in actual recent innings/start usage (a
VERIFIED_WORKLOAD_SIGNAL-shaped case). These specific pitchers/games are
NOT hardcoded here (synthetic inputs only, matching this module's
existing test-suite convention) -- only the evidence-quality DISTINCTION
those two cases illustrate is what's under test.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.research.pitcher_workload_projection import (
    RECENT_WORKLOAD_PENALTY,
    VERIFIED_WORKLOAD_SIGNAL,
    SUPPORTIVE_USAGE_TREND,
    WEAK_INFERENCE,
    NO_WORKLOAD_SIGNAL,
    cap_confidence_for_workload_evidence_quality,
    project_pitcher_workload,
    survival_curve,
)


def _baseline(**overrides):
    kwargs = dict(avg_ip_per_start=6.0, k_pct=22.0, bb_pct=8.5)
    kwargs.update(overrides)
    return project_pitcher_workload(**kwargs)


# ── Backward compatibility ───────────────────────────────────────────────

class TestBackwardCompatibilityWithLegacyBoolean:
    def test_legacy_true_matches_verified_signal_penalty(self):
        legacy = _baseline(recent_workload_restricted=True)
        explicit = _baseline(workload_evidence_quality=VERIFIED_WORKLOAD_SIGNAL)
        assert legacy["expectedOuts"] == explicit["expectedOuts"]

    def test_legacy_false_matches_no_signal(self):
        legacy = _baseline(recent_workload_restricted=False)
        explicit = _baseline(workload_evidence_quality=NO_WORKLOAD_SIGNAL)
        assert legacy["expectedOuts"] == explicit["expectedOuts"]

    def test_legacy_none_matches_no_signal(self):
        legacy = _baseline()
        explicit = _baseline(workload_evidence_quality=NO_WORKLOAD_SIGNAL)
        assert legacy["expectedOuts"] == explicit["expectedOuts"]

    def test_diagnostics_reports_resolved_tier_for_legacy_true(self):
        result = _baseline(recent_workload_restricted=True)
        assert result["diagnostics"]["workloadEvidenceQuality"] == VERIFIED_WORKLOAD_SIGNAL

    def test_diagnostics_reports_no_signal_when_nothing_supplied(self):
        result = _baseline()
        assert result["diagnostics"]["workloadEvidenceQuality"] == NO_WORKLOAD_SIGNAL


# ── Monotonic penalty scaling by evidence tier ───────────────────────────

class TestEvidenceQualityScalesThePenalty:
    def test_verified_produces_the_largest_reduction(self):
        base = _baseline()["expectedOuts"]
        verified = _baseline(workload_evidence_quality=VERIFIED_WORKLOAD_SIGNAL)["expectedOuts"]
        supportive = _baseline(workload_evidence_quality=SUPPORTIVE_USAGE_TREND)["expectedOuts"]
        weak = _baseline(workload_evidence_quality=WEAK_INFERENCE)["expectedOuts"]
        none_ = _baseline(workload_evidence_quality=NO_WORKLOAD_SIGNAL)["expectedOuts"]
        assert none_ == base
        assert verified < supportive < weak < none_

    def test_verified_materially_alters_the_projection(self):
        base = _baseline()["expectedOuts"]
        verified = _baseline(workload_evidence_quality=VERIFIED_WORKLOAD_SIGNAL)["expectedOuts"]
        assert (base - verified) >= 0.5   # a materially real reduction, not a rounding blip

    def test_no_signal_never_changes_the_projection(self):
        base = _baseline()["expectedOuts"]
        none_ = _baseline(workload_evidence_quality=NO_WORKLOAD_SIGNAL)["expectedOuts"]
        assert base == none_


class TestWeakInferenceCannotMateriallyDepressProjectedOuts:
    """
    Core guardrail: unsupported "workload concern" language (WEAK_INFERENCE)
    must never manufacture a workload restriction with real teeth -- its
    effect must be small relative to a genuinely verified signal.
    """

    def test_weak_inference_effect_is_a_small_fraction_of_verified(self):
        base = _baseline()["expectedOuts"]
        verified_delta = base - _baseline(workload_evidence_quality=VERIFIED_WORKLOAD_SIGNAL)["expectedOuts"]
        weak_delta = base - _baseline(workload_evidence_quality=WEAK_INFERENCE)["expectedOuts"]
        assert weak_delta < verified_delta * 0.1

    def test_weak_inference_delta_is_bounded_in_absolute_terms(self):
        base = _baseline()["expectedOuts"]
        weak = _baseline(workload_evidence_quality=WEAK_INFERENCE)["expectedOuts"]
        assert (base - weak) < 1.0

    def test_stacking_multiple_weak_signals_does_not_escalate_the_tier(self):
        """There is no mechanism to combine multiple WEAK_INFERENCE calls into a stronger one -- the caller must supply a single resolved tier."""
        single = _baseline(workload_evidence_quality=WEAK_INFERENCE)["expectedOuts"]
        # Calling it again with the same tier is idempotent -- proves there
        # is no hidden accumulating state across calls.
        again = _baseline(workload_evidence_quality=WEAK_INFERENCE)["expectedOuts"]
        assert single == again


# ── Invalid input handling ───────────────────────────────────────────────

class TestInvalidEvidenceQuality:
    def test_unknown_tier_raises(self):
        with pytest.raises(ValueError):
            _baseline(workload_evidence_quality="MADE_UP_TIER")

    def test_explicit_tier_takes_priority_over_legacy_boolean(self):
        """If both are somehow supplied, the richer explicit tier wins -- never silently ignored."""
        result = survival_curve(
            6.0, recent_workload_restricted=False,
            workload_evidence_quality=VERIFIED_WORKLOAD_SIGNAL,
        )
        _, diagnostics = result
        assert diagnostics["workloadEvidenceQuality"] == VERIFIED_WORKLOAD_SIGNAL


# ── Confidence cap (cap_confidence_for_workload_evidence_quality) ───────

class TestConfidenceCap:
    def test_weak_inference_caps_high_to_paper(self):
        assert cap_confidence_for_workload_evidence_quality("HIGH", WEAK_INFERENCE) == "PAPER"

    def test_weak_inference_caps_medium_to_paper(self):
        assert cap_confidence_for_workload_evidence_quality("MEDIUM", WEAK_INFERENCE) == "PAPER"

    def test_weak_inference_never_produces_a_high_confidence_under(self):
        for conf in ("HIGH", "MEDIUM", "PAPER"):
            assert cap_confidence_for_workload_evidence_quality(conf, WEAK_INFERENCE) != "HIGH"

    def test_supportive_usage_trend_caps_high_to_medium(self):
        assert cap_confidence_for_workload_evidence_quality("HIGH", SUPPORTIVE_USAGE_TREND) == "MEDIUM"

    def test_supportive_usage_trend_does_not_touch_medium_or_paper(self):
        assert cap_confidence_for_workload_evidence_quality("MEDIUM", SUPPORTIVE_USAGE_TREND) == "MEDIUM"
        assert cap_confidence_for_workload_evidence_quality("PAPER", SUPPORTIVE_USAGE_TREND) == "PAPER"

    def test_verified_signal_never_caps(self):
        for conf in ("HIGH", "MEDIUM", "PAPER"):
            assert cap_confidence_for_workload_evidence_quality(conf, VERIFIED_WORKLOAD_SIGNAL) == conf

    def test_no_signal_never_caps(self):
        for conf in ("HIGH", "MEDIUM", "PAPER"):
            assert cap_confidence_for_workload_evidence_quality(conf, NO_WORKLOAD_SIGNAL) == conf

    def test_none_confidence_is_always_a_noop(self):
        for quality in (VERIFIED_WORKLOAD_SIGNAL, SUPPORTIVE_USAGE_TREND, WEAK_INFERENCE, NO_WORKLOAD_SIGNAL):
            assert cap_confidence_for_workload_evidence_quality(None, quality) is None

    def test_never_raises_a_tier(self):
        for quality in (VERIFIED_WORKLOAD_SIGNAL, SUPPORTIVE_USAGE_TREND, WEAK_INFERENCE, NO_WORKLOAD_SIGNAL):
            assert cap_confidence_for_workload_evidence_quality("PAPER", quality) == "PAPER"


# ── Joint K/outs coherence still holds with the new parameter ───────────

class TestJointCoherenceWithEvidenceQuality:
    def test_strikeouts_also_move_with_evidence_quality(self):
        """K projection derives from the SAME survival curve -- a verified workload restriction must suppress both outs and Ks together, not just one."""
        base = _baseline()["expectedStrikeouts"]
        verified = _baseline(workload_evidence_quality=VERIFIED_WORKLOAD_SIGNAL)["expectedStrikeouts"]
        assert verified < base
