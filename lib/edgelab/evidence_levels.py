"""
lib/edgelab/evidence_levels.py
==================================
Research Lab Milestone 0A: the canonical E0-E5 evidence taxonomy every
research experiment must declare, and the promotion-eligibility rules
attached to it.

RECONCILIATION WITH EXISTING TAXONOMIES (spec requirement -- "if the
repository already has a similar taxonomy, reconcile rather than
duplicate it, and document the mapping"). Three existing, narrower
concepts already encode PARTS of this ladder; none of them is a
superset of it, so none is reused as-is -- each is mapped below instead:

  1. lib.edgelab.replay's ELIGIBLE_LEVEL_2 / ELIGIBLE_LEVEL_1_ONLY --
     answers "how complete is the frozen Snapshot this replay ran
     against" (a REPLAY-INPUT-COMPLETENESS axis), not "how much PIT/
     causal evidence does this specific research claim have" (this
     module's axis). A CANDIDATE_MODEL replay at ELIGIBLE_LEVEL_2 is
     evidence that TODAY's code, run against a truthfully-frozen
     historical Snapshot, reproduces a certain output -- that is at
     best E2 (PIT_HISTORICAL) evidence for a claim about the CONTROL
     model's own historical behavior, and it says NOTHING about a
     candidate variant's real-world predictive value, which is what E3+
     requires. Never treat ELIGIBLE_LEVEL_2 alone as E3+.
  2. ModelEvaluation.sourceCaptureType (PROSPECTIVE_LIVE/
     PROSPECTIVE_SCHEDULED/PROSPECTIVE_STANDALONE/REPLAYED_RESEARCH) --
     PROSPECTIVE_* rows are captured before the event and are never bet
     on, which is necessary but not sufficient for E4
     (PROSPECTIVE_SHADOW): E4 additionally requires that every INPUT the
     candidate/control actually used was itself prospectively available
     (see lib.edgelab.pit_provenance) -- a PROSPECTIVE_LIVE row that
     reused persisted, day-old inputs for most fields
     (inputFreshnessNote=ALL_INPUTS_PERSISTED_FROM_SLATE_AT_LAST_PIPELINE_FETCH,
     see docs/EDGELAB_PROSPECTIVE_MODEL_SNAPSHOTS.md section 3b) is
     still genuinely prospective for THOSE fields (they were fixed
     before the event either way), but a NEW research feature this
     milestone's future experiments might add is not automatically
     PIT-safe just because it rides along on a PROSPECTIVE_LIVE row --
     each experiment must audit its OWN candidate's inputs against
     lib.edgelab.pit_provenance, never inherit E4 automatically from
     sourceCaptureType alone. REPLAYED_RESEARCH is never E3+ regardless
     of how the replay was run.
  3. ModelEvaluation.qualityTier (TRUSTED_PRODUCTION/RESEARCH_ONLY/
     UNSUPPORTED) -- answers "is this family part of the real-money-
     gated production pipeline", a PRODUCTION-ELIGIBILITY axis, entirely
     orthogonal to evidence level. A TRUSTED_PRODUCTION-tier market's
     historical ModelEvaluation rows are still only E1/E2 evidence for a
     NEW research question about them (see the module's own historical-
     identity caveats) -- qualityTier says nothing about how the
     evidence for a research claim was gathered.

None of the three above is an ordered ladder from "weak" to "strong"
evidence the way this module's E0-E5 is -- that ladder did not
previously exist in this repository and is this milestone's own
contribution.
"""

from collections import namedtuple

EvidenceLevelSpec = namedtuple("EvidenceLevelSpec", ["code", "name", "description", "rank"])

E0_DESCRIPTIVE = "E0_DESCRIPTIVE"
E1_RECONSTRUCTED_RETROSPECTIVE = "E1_RECONSTRUCTED_RETROSPECTIVE"
E2_PIT_HISTORICAL = "E2_PIT_HISTORICAL"
E3_WALK_FORWARD_HOLDOUT = "E3_WALK_FORWARD_HOLDOUT"
E4_PROSPECTIVE_SHADOW = "E4_PROSPECTIVE_SHADOW"
E5_REAL_MONEY_EXECUTION = "E5_REAL_MONEY_EXECUTION"

EVIDENCE_LEVELS = {
    E0_DESCRIPTIVE: EvidenceLevelSpec(
        E0_DESCRIPTIVE, "Descriptive",
        "Historical descriptive analysis only. No causal/replay claim -- reports what happened in a sample, "
        "makes no assertion that the same inputs would have been knowable or actionable at decision time.",
        rank=0,
    ),
    E1_RECONSTRUCTED_RETROSPECTIVE: EvidenceLevelSpec(
        E1_RECONSTRUCTED_RETROSPECTIVE, "Reconstructed retrospective",
        "Historical analysis using inputs reconstructed after the fact, where true point-in-time availability "
        "is not fully proven (lib.edgelab.pit_provenance status RETROSPECTIVELY_RECONSTRUCTED_WITH_LIMITATIONS "
        "or UNKNOWN_REQUIRES_AUDIT for one or more inputs the analysis depends on).",
        rank=1,
    ),
    E2_PIT_HISTORICAL: EvidenceLevelSpec(
        E2_PIT_HISTORICAL, "Point-in-time historical",
        "Historical experiment where every candidate/control input is demonstrably available as of the "
        "prediction/checkpoint time (lib.edgelab.pit_provenance status OBSERVED_AT_DECISION_TIME or "
        "RECONSTRUCTABLE_FROM_DATED_RAW for every input used, with a documented reconstruction method for the latter).",
        rank=2,
    ),
    E3_WALK_FORWARD_HOLDOUT: EvidenceLevelSpec(
        E3_WALK_FORWARD_HOLDOUT, "Walk-forward holdout",
        "Chronological out-of-sample validation / locked holdout using point-in-time-safe inputs -- built on "
        "lib.edgelab.research_splits' chronological DEVELOPMENT/VALIDATION/HOLDOUT partitioning, with the "
        "HOLDOUT partition evaluated exactly once and never re-tuned against.",
        rank=3,
    ),
    E4_PROSPECTIVE_SHADOW: EvidenceLevelSpec(
        E4_PROSPECTIVE_SHADOW, "Prospective shadow",
        "Prediction captured prospectively before event start, with no production betting activation -- every "
        "input genuinely fixed/observed before the event, not merely captured by a PROSPECTIVE_* pipeline path "
        "(see this module's docstring, item 2, for why sourceCaptureType alone is not sufficient).",
        rank=4,
    ),
    E5_REAL_MONEY_EXECUTION: EvidenceLevelSpec(
        E5_REAL_MONEY_EXECUTION, "Real-money execution",
        "Actual user-confirmed wager / real-money outcome evidence (a genuine PlacedBet, never a hypothetical "
        "return computed over an unbet market).",
        rank=5,
    ),
}

ORDERED_EVIDENCE_LEVELS = tuple(
    level for level, _ in sorted(((l, s.rank) for l, s in EVIDENCE_LEVELS.items()), key=lambda pair: pair[1])
)

# E0/E1 must never be promotable to production -- explicit spec requirement,
# enforced structurally (not merely documented) by is_promotable().
NON_PROMOTABLE_EVIDENCE_LEVELS = frozenset({E0_DESCRIPTIVE, E1_RECONSTRUCTED_RETROSPECTIVE})

# The minimum evidence level a SHADOW_CANDIDATE disposition requires --
# spec: "Passed a leakage-safe chronological/OOS stage strongly enough to
# justify prospective shadow evaluation" implies the evidence that earns
# SHADOW_CANDIDATE must itself be E3+ (walk-forward or better); E4/E5 is
# the evidence a SHADOW_CANDIDATE stage itself then PRODUCES.
MIN_EVIDENCE_LEVEL_FOR_SHADOW_CANDIDATE = E3_WALK_FORWARD_HOLDOUT

# PROMOTION_CANDIDATE requires prospective shadow evidence per spec section
# "REQUIRED EXPERIMENT DISPOSITIONS".
MIN_EVIDENCE_LEVEL_FOR_PROMOTION_CANDIDATE = E4_PROSPECTIVE_SHADOW


def rank(level: str) -> int:
    validate_evidence_level(level)
    return EVIDENCE_LEVELS[level].rank


def validate_evidence_level(level: str) -> None:
    if level not in EVIDENCE_LEVELS:
        raise ValueError(f"Unknown evidence level {level!r}. Known: {sorted(EVIDENCE_LEVELS)}")


def is_promotable(level: str) -> bool:
    """
    Whether evidence at this level is EVER eligible to support a
    promotion-track disposition (SHADOW_CANDIDATE/PROMOTION_CANDIDATE) --
    never true for E0/E1 (spec: "E0/E1 evidence MUST NOT be promotable to
    production"). True here means "not structurally disqualified", not
    "sufficient" -- a report's disposition logic still applies its own
    minimum-rank gates on top (see MIN_EVIDENCE_LEVEL_FOR_*).
    """
    validate_evidence_level(level)
    return level not in NON_PROMOTABLE_EVIDENCE_LEVELS


def meets_minimum(level: str, minimum: str) -> bool:
    """True iff `level` is at least as strong as `minimum` on the E0-E5 ladder."""
    return rank(level) >= rank(minimum)
