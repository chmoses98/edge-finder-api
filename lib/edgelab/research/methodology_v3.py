#!/usr/bin/env python3
"""
lib/edgelab/research/methodology_v3.py
======================================
METHODOLOGY V3 -- MATERIALITY AND ACTIONABILITY.

WHY THIS EXISTS
---------------
MLB-RSCH-0030 fitted a hitter shrinkage parameter, passed every one of its
five preregistered success criteria, and was mechanically labelled
LEVEL_1_SHADOW_CANDIDATE. The same numbers said:

    alpha = 0.0590 with a playerGameKey-clustered CI of [-0.3621, +0.5247]
    validation Brier delta -0.000214 with a CI of [-0.0007, +0.0003]
    a DEV NLL gain over trusting the market outright of 5.3e-05
    4 of 7 held-out dates favourable -- a bare majority
    ZERO contracts clearing the canonical Kalshi fee

Every one of those is consistent with "this parameter is indistinguishable
from ignoring the model entirely". The rule passed anyway because it tested
the SIGN of each improvement and never asked whether the effect was
distinguishable from the null, large enough to matter, or executable.

V3 closes that gap for FUTURE experiments.

WHAT V3 IS NOT
--------------
It does NOT rewrite, re-score or re-dispose any prior experiment. Every
committed artifact and verdict stands exactly as merged -- rewriting a rule
after seeing its outcome is precisely the failure this program forbids, and
V3 would be self-defeating if it did that.

It is OPT-IN, exactly like V2: a future experiment adopts V3 by building a
MaterialityPreregistration before it looks at results and calling
`betting_shadow_gate_v3()` as its actionability rule. Nothing imports this
module implicitly, and no framework or production code is modified.

NO UNIVERSAL NUMERIC FLOORS
---------------------------
V3 deliberately ships NO default effect floor, score floor or sample floor.
A floor that is right for a game-level moneyline study is wrong for a
player-prop ladder. Every experiment must state and justify its own, before
results, and the constructor REFUSES to build without them. An unjustified
floor is a floor someone can quietly relax later.

FOUR LABELS, NEVER COLLAPSED
----------------------------
    STATISTICAL_SIGNAL      -- is the effect distinguishable from the null?
    PREDICTIVE_MATERIALITY  -- is it big enough to be worth anything?
    EXECUTABLE_CAPACITY     -- does anything survive price, spread and fees?
    IMPLEMENTATION_READINESS-- did it transport, on enough independent units?

RSCH-0030 would have earned a passing STATISTICAL_SIGNAL only by point
estimate, and would have failed PREDICTIVE_MATERIALITY and EXECUTABLE
CAPACITY outright. Reporting one collapsed verdict hid that.

ROI IS NEVER A FITTING CRITERION
--------------------------------
Economic capacity is a GATE, evaluated after the predictive verdict. V3
provides no way to rank, select or tune a parameter by ROI, and
`assert_roi_not_a_fitting_objective()` exists so an experiment can prove in
its own tests that it did not.
"""
from dataclasses import dataclass, field
from typing import Optional, Sequence

METHODOLOGY_VERSION = "v3"

# The four independent labels. They are reported separately, always.
LABEL_STATISTICAL_SIGNAL = "STATISTICAL_SIGNAL"
LABEL_PREDICTIVE_MATERIALITY = "PREDICTIVE_MATERIALITY"
LABEL_EXECUTABLE_CAPACITY = "EXECUTABLE_CAPACITY"
LABEL_IMPLEMENTATION_READINESS = "IMPLEMENTATION_READINESS"
V3_LABELS = (LABEL_STATISTICAL_SIGNAL, LABEL_PREDICTIVE_MATERIALITY,
             LABEL_EXECUTABLE_CAPACITY, LABEL_IMPLEMENTATION_READINESS)

# Transport evidence a betting-relevant candidate may rely on. DEV-only
# performance is deliberately absent: it is not transport.
TRANSPORT_CHRONOLOGICAL_VALIDATION = "CHRONOLOGICAL_VALIDATION"
TRANSPORT_WALK_FORWARD = "WALK_FORWARD_REPLICATION"
TRANSPORT_LEAVE_DATE_OUT = "LEAVE_ONE_DATE_OUT"
VALID_TRANSPORT = (TRANSPORT_CHRONOLOGICAL_VALIDATION, TRANSPORT_WALK_FORWARD,
                   TRANSPORT_LEAVE_DATE_OUT)


class MaterialityPreregistrationError(ValueError):
    """Raised when a preregistration is incomplete or unjustified."""


@dataclass(frozen=True)
class MaterialityPreregistration:
    """Every floor a betting-relevant V3 experiment must fix BEFORE results.

    Frozen on purpose: a preregistration that can be mutated after results
    is not a preregistration.
    """
    # 1. NULL / EFFECT MATERIALITY
    null_value: float
    effect_floor: float                 # |effect| below this is not meaningful
    harm_tolerance: float               # effect worse than this is unacceptable
    require_ci_excludes_null: bool
    # 2. PROPER-SCORE MATERIALITY -- one of these two must be set
    min_score_improvement: Optional[float] = None      # e.g. minimum Brier gain
    noninferiority_margin: Optional[float] = None      # or an explicit NI margin
    # 3. INDEPENDENCE / REPLICATION
    min_independent_games: int = 0
    min_independent_dates: int = 0
    min_independent_subjects: int = 0                  # players, teams, ...
    min_replicating_blocks: int = 0                    # e.g. dates that must agree
    # 4. TRANSPORT
    required_transport: str = TRANSPORT_CHRONOLOGICAL_VALIDATION
    # 5. ECONOMIC CAPACITY
    require_executable_capacity: bool = True
    min_executable_opportunities: int = 1
    # Justification is mandatory, and must be substantive.
    justification: str = ""
    subject_unit: str = "game"
    notes: str = ""

    def __post_init__(self):
        if self.effect_floor <= 0:
            raise MaterialityPreregistrationError(
                "effect_floor must be positive -- a floor of zero means any favourable sign "
                "counts, which is exactly the MLB-RSCH-0030 failure V3 exists to prevent")
        if self.harm_tolerance < 0:
            raise MaterialityPreregistrationError("harm_tolerance must be >= 0")
        if self.min_score_improvement is None and self.noninferiority_margin is None:
            raise MaterialityPreregistrationError(
                "declare either min_score_improvement or noninferiority_margin -- V3 ships no "
                "universal proper-score floor because the right value is domain-specific")
        if self.min_score_improvement is not None and self.min_score_improvement <= 0:
            raise MaterialityPreregistrationError("min_score_improvement must be positive")
        if self.required_transport not in VALID_TRANSPORT:
            raise MaterialityPreregistrationError(
                f"required_transport must be one of {VALID_TRANSPORT}; DEV-only performance is not "
                "transport evidence")
        if self.min_independent_games <= 0 and self.min_independent_dates <= 0:
            raise MaterialityPreregistrationError(
                "declare a positive independent-game or independent-date floor -- a large "
                "correlated row count is not independent information")
        if len((self.justification or "").strip()) < 40:
            raise MaterialityPreregistrationError(
                "justification must explain WHY these floors are right for this domain; an "
                "unjustified floor is one that can be quietly relaxed later")


@dataclass(frozen=True)
class ObservedEvidence:
    """What the experiment actually measured. Every field is optional so a
    partially-completed study can still be evaluated honestly -- a missing
    measurement fails its gate rather than silently passing it."""
    effect_estimate: Optional[float] = None
    effect_ci_low: Optional[float] = None
    effect_ci_high: Optional[float] = None
    score_improvement: Optional[float] = None      # positive == better
    score_ci_low: Optional[float] = None
    score_ci_high: Optional[float] = None
    independent_games: int = 0
    independent_dates: int = 0
    independent_subjects: int = 0
    replicating_blocks: int = 0
    transport_evidence: Optional[str] = None
    executable_opportunities: int = 0
    cluster_unit: str = "gameId"


def _statistical_signal(pre, obs):
    reasons = []
    if obs.effect_estimate is None:
        reasons.append("V3-1: no effect estimate reported")
        return False, reasons
    if abs(obs.effect_estimate - pre.null_value) < pre.effect_floor:
        reasons.append(
            f"V3-1: |effect - null| = {abs(obs.effect_estimate - pre.null_value):.6g} is below the "
            f"preregistered effect floor {pre.effect_floor:.6g}")
    if pre.require_ci_excludes_null:
        if obs.effect_ci_low is None or obs.effect_ci_high is None:
            reasons.append("V3-1: CI required by preregistration but not reported")
        elif obs.effect_ci_low <= pre.null_value <= obs.effect_ci_high:
            reasons.append(
                f"V3-1: CI [{obs.effect_ci_low:.6g}, {obs.effect_ci_high:.6g}] contains the null "
                f"{pre.null_value:.6g} -- a favourable point estimate alone cannot carry a betting "
                "shadow")
    return (len(reasons) == 0), reasons


def _predictive_materiality(pre, obs):
    reasons = []
    if obs.score_improvement is None:
        reasons.append("V3-2: no proper-score improvement reported")
        return False, reasons
    if pre.min_score_improvement is not None:
        if obs.score_improvement < pre.min_score_improvement:
            reasons.append(
                f"V3-2: score improvement {obs.score_improvement:.6g} is below the preregistered "
                f"minimum {pre.min_score_improvement:.6g}")
    else:
        # Noninferiority: the candidate must not be materially worse.
        if obs.score_ci_low is None:
            reasons.append("V3-2: noninferiority declared but no score CI reported")
        elif obs.score_ci_low < -abs(pre.noninferiority_margin):
            reasons.append(
                f"V3-2: score CI lower bound {obs.score_ci_low:.6g} breaches the noninferiority "
                f"margin {pre.noninferiority_margin:.6g}")
    if obs.score_improvement < -abs(pre.harm_tolerance):
        reasons.append(
            f"V3-2: score change {obs.score_improvement:.6g} exceeds the harm tolerance "
            f"{pre.harm_tolerance:.6g}")
    return (len(reasons) == 0), reasons


def _executable_capacity(pre, obs):
    reasons = []
    if not pre.require_executable_capacity:
        return True, ["V3-3: executable capacity not required by this preregistration"]
    if obs.executable_opportunities < pre.min_executable_opportunities:
        reasons.append(
            f"V3-3: {obs.executable_opportunities} executable opportunities after price, spread and "
            f"fees is below the preregistered minimum {pre.min_executable_opportunities} -- a "
            "candidate that cannot be traded is not a betting candidate")
    return (len(reasons) == 0), reasons


def _implementation_readiness(pre, obs):
    reasons = []
    if obs.transport_evidence not in VALID_TRANSPORT:
        reasons.append(
            f"V3-4: transport evidence {obs.transport_evidence!r} is not one of {VALID_TRANSPORT}; "
            "DEV-only performance is not transport")
    elif obs.transport_evidence != pre.required_transport:
        reasons.append(
            f"V3-4: preregistration required {pre.required_transport}, observed "
            f"{obs.transport_evidence}")
    if obs.independent_games < pre.min_independent_games:
        reasons.append(f"V3-4: {obs.independent_games} independent games below floor "
                       f"{pre.min_independent_games}")
    if obs.independent_dates < pre.min_independent_dates:
        reasons.append(f"V3-4: {obs.independent_dates} independent dates below floor "
                       f"{pre.min_independent_dates}")
    if obs.independent_subjects < pre.min_independent_subjects:
        reasons.append(f"V3-4: {obs.independent_subjects} independent {pre.subject_unit}s below floor "
                       f"{pre.min_independent_subjects}")
    if obs.replicating_blocks < pre.min_replicating_blocks:
        reasons.append(f"V3-4: {obs.replicating_blocks} replicating blocks below floor "
                       f"{pre.min_replicating_blocks}")
    return (len(reasons) == 0), reasons


def evaluate_materiality_v3(preregistration, observed):
    """The four labels, evaluated SEPARATELY and never collapsed.

    Returns {label: {"passes": bool, "reasons": [...]}} for all four."""
    if not isinstance(preregistration, MaterialityPreregistration):
        raise MaterialityPreregistrationError(
            "V3 requires a MaterialityPreregistration fixed before results were observed")
    out = {}
    for label, fn in ((LABEL_STATISTICAL_SIGNAL, _statistical_signal),
                      (LABEL_PREDICTIVE_MATERIALITY, _predictive_materiality),
                      (LABEL_EXECUTABLE_CAPACITY, _executable_capacity),
                      (LABEL_IMPLEMENTATION_READINESS, _implementation_readiness)):
        passes, reasons = fn(preregistration, observed)
        out[label] = {"passes": passes, "reasons": reasons}
    return out


def betting_shadow_gate_v3(preregistration, observed):
    """May this candidate become a BETTING-RELEVANT shadow candidate?

    Requires ALL FOUR labels. Returns (passes, reasons, labels) so a caller
    must handle the separated labels; the boolean alone never explains why."""
    labels = evaluate_materiality_v3(preregistration, observed)
    reasons = [r for label in V3_LABELS for r in labels[label]["reasons"]
               if not labels[label]["passes"]]
    passes = all(labels[label]["passes"] for label in V3_LABELS)
    return passes, reasons, labels


def assert_roi_not_a_fitting_objective(fitting_source_text: str) -> None:
    """Registration/test-time guard: raises if an experiment's own fitting
    code references profit, ROI or P&L. Economic capacity is a GATE applied
    after the predictive verdict, never an objective a parameter is tuned to."""
    lowered = (fitting_source_text or "").lower()
    for token in ("roi", "net_pnl", "netpnl", "profit", "pnl", "bankroll", "payout_maximiz"):
        if token in lowered:
            raise MaterialityPreregistrationError(
                f"V3: fitting objective references {token!r}. Parameters are fitted by proper "
                "scoring only; ROI is a gate, never an objective.")


def describe_v3(preregistration) -> dict:
    """Serialisable record of the floors, for an experiment's own artifact."""
    p = preregistration
    return {
        "methodologyVersion": METHODOLOGY_VERSION,
        "nullValue": p.null_value, "effectFloor": p.effect_floor,
        "harmTolerance": p.harm_tolerance, "requireCiExcludesNull": p.require_ci_excludes_null,
        "minScoreImprovement": p.min_score_improvement,
        "noninferiorityMargin": p.noninferiority_margin,
        "minIndependentGames": p.min_independent_games,
        "minIndependentDates": p.min_independent_dates,
        "minIndependentSubjects": p.min_independent_subjects,
        "minReplicatingBlocks": p.min_replicating_blocks,
        "requiredTransport": p.required_transport,
        "requireExecutableCapacity": p.require_executable_capacity,
        "minExecutableOpportunities": p.min_executable_opportunities,
        "subjectUnit": p.subject_unit,
        "justification": p.justification,
        "notes": p.notes,
        "appliesTo": "FUTURE experiments that opt in; no prior experiment or disposition is altered",
    }
