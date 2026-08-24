"""
lib/edgelab/probability_status.py
=====================================
Phase 2 (Full-Universe MLB Kalshi Probability Persistence): pure,
additive vocabulary + math shared by every full-universe evaluation
writer (RECOMMENDATION_SYNC's scheduled extension, the new standalone
research snapshot, and any future caller). No I/O, no network, no
mutation of any argument.

Three concerns, kept in one small module because every caller that
needs one of them needs all three:

1. PROBABILITY STATUS VOCABULARY -- maps this repo's EXISTING,
   already-tested evaluationStatus vocabulary
   (lib.edgelab.model_evaluation) onto the additive
   probabilityStatus/probabilityMissingReason fields. evaluationStatus
   remains the field every existing reader/writer already depends on;
   these are NEW, parallel fields, never a replacement -- see
   data/edgelab/schema_v1/model_evaluation.schema.json, where they are
   added as optional (non-required) properties.

2. PROTECTED-EXPRESSION ALGEBRA -- generalizes
   lib.research.f5_tie_tax.evaluate_f5_tie_tax()'s existing F5-only
   "opponent NO captures the tie" formula to any period whose outcome
   structure is independently CONFIRMED_THREE_WAY (today: F3, F5, F7 --
   see lib.research.market_taxonomy.HORIZON_MARKET_STATUS). Uses ONLY
   the same joint Away/Tie/Home distribution
   lib.research.three_way_projection.three_way_result_probs() already
   produces for those periods -- no independence assumption, no new
   statistical model, and no market-price approximation.

3. CONSISTENCY INVARIANTS -- deterministic YES/NO complementarity and
   N-way-outcome-sum checks.
"""

from lib.research.market_taxonomy import HORIZON_MARKET_STATUS

# ── 1. Probability status vocabulary ─────────────────────────────────

PROBABILITY_STATUS_EVALUATED = "EVALUATED"
PROBABILITY_STATUS_MISSING_INPUT = "MISSING_INPUT"
PROBABILITY_STATUS_UNSUPPORTED_FAMILY = "UNSUPPORTED_FAMILY"
PROBABILITY_STATUS_PARSER_UNRESOLVED = "PARSER_UNRESOLVED"
PROBABILITY_STATUS_SUSPENDED_FAMILY = "SUSPENDED_FAMILY"
PROBABILITY_STATUS_STALE_MARKET = "STALE_MARKET"
PROBABILITY_STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"

VALID_PROBABILITY_STATUSES = frozenset({
    PROBABILITY_STATUS_EVALUATED, PROBABILITY_STATUS_MISSING_INPUT,
    PROBABILITY_STATUS_UNSUPPORTED_FAMILY, PROBABILITY_STATUS_PARSER_UNRESOLVED,
    PROBABILITY_STATUS_SUSPENDED_FAMILY, PROBABILITY_STATUS_STALE_MARKET,
    PROBABILITY_STATUS_NOT_APPLICABLE,
})

MISSING_REASON_MISSING_STARTER = "MISSING_STARTER"
MISSING_REASON_MISSING_LINEUP = "MISSING_LINEUP"
MISSING_REASON_MISSING_HAND_SPLIT = "MISSING_HAND_SPLIT"
MISSING_REASON_MISSING_PITCHER_PROJECTION = "MISSING_PITCHER_PROJECTION"
MISSING_REASON_MISSING_HITTER_PROJECTION = "MISSING_HITTER_PROJECTION"
MISSING_REASON_MISSING_RUN_DISTRIBUTION = "MISSING_RUN_DISTRIBUTION"
MISSING_REASON_OTHER = "OTHER"

VALID_MISSING_REASONS = frozenset({
    MISSING_REASON_MISSING_STARTER, MISSING_REASON_MISSING_LINEUP,
    MISSING_REASON_MISSING_HAND_SPLIT, MISSING_REASON_MISSING_PITCHER_PROJECTION,
    MISSING_REASON_MISSING_HITTER_PROJECTION, MISSING_REASON_MISSING_RUN_DISTRIBUTION,
    MISSING_REASON_OTHER,
})

SOURCE_CAPTURE_TYPE_PROSPECTIVE_LIVE = "PROSPECTIVE_LIVE"
SOURCE_CAPTURE_TYPE_PROSPECTIVE_SCHEDULED = "PROSPECTIVE_SCHEDULED"
SOURCE_CAPTURE_TYPE_PROSPECTIVE_STANDALONE = "PROSPECTIVE_STANDALONE"
SOURCE_CAPTURE_TYPE_REPLAYED_RESEARCH = "REPLAYED_RESEARCH"

VALID_SOURCE_CAPTURE_TYPES = frozenset({
    SOURCE_CAPTURE_TYPE_PROSPECTIVE_LIVE, SOURCE_CAPTURE_TYPE_PROSPECTIVE_SCHEDULED,
    SOURCE_CAPTURE_TYPE_PROSPECTIVE_STANDALONE, SOURCE_CAPTURE_TYPE_REPLAYED_RESEARCH,
})

# lib.edgelab.model_evaluation evaluationStatus values that mean "a real
# modelFairProbability is present" -- both map to probabilityStatus
# EVALUATED (probabilityStatus is about whether a NUMBER exists, not
# whether a market-implied comparison price was also available --
# that distinction stays evaluationStatus's job alone).
_EVALUATED_STATUSES = frozenset({"EVALUATED", "PARTIAL_EVALUATION"})

# Keyword substrings actually found in lib.kalshi_probability_adapters.py's
# and scripts/build_market_ledger.py's own STATUS_MISSING_DATA reason
# text -- checked top to bottom, first match wins. Never invented text;
# only used to route an EXISTING reason string onto the closed
# probabilityMissingReason vocabulary.
_KEYWORD_TO_MISSING_REASON = (
    ("starter", MISSING_REASON_MISSING_STARTER),
    ("lineup", MISSING_REASON_MISSING_LINEUP),
    ("hand split", MISSING_REASON_MISSING_HAND_SPLIT),
    ("platoon", MISSING_REASON_MISSING_HAND_SPLIT),
    ("savant", MISSING_REASON_MISSING_PITCHER_PROJECTION),
    ("workload", MISSING_REASON_MISSING_PITCHER_PROJECTION),
    ("pitcher", MISSING_REASON_MISSING_PITCHER_PROJECTION),
    ("hitter", MISSING_REASON_MISSING_HITTER_PROJECTION),
    ("projection board", MISSING_REASON_MISSING_HITTER_PROJECTION),
    ("run distribution", MISSING_REASON_MISSING_RUN_DISTRIBUTION),
    ("proj", MISSING_REASON_MISSING_RUN_DISTRIBUTION),
)


def missing_reason_for_text(reason_text):
    """
    Best-effort, deterministic keyword match of an existing
    unsupportedReason/dataQualityReasons string onto the closed
    probabilityMissingReason vocabulary. Never fabricates a reason that
    isn't grounded in the actual text -- returns MISSING_REASON_OTHER
    (never None) when no keyword matches, so the field is always
    populated whenever probabilityStatus is MISSING_INPUT.
    """
    if not reason_text:
        return MISSING_REASON_OTHER
    lowered = reason_text.lower()
    for keyword, reason in _KEYWORD_TO_MISSING_REASON:
        if keyword in lowered:
            return reason
    return MISSING_REASON_OTHER


def probability_status_for_evaluation(evaluation_status, model_fair_probability, discovery_covered):
    """
    Pure. Maps lib.edgelab.model_evaluation's existing evaluationStatus
    (+ whether a real modelFairProbability is present, + whether this
    ticker was covered by a discovery run at all this run) onto
    (probabilityStatus, needs_missing_reason: bool):

      EVALUATED / PARTIAL_EVALUATION       -> EVALUATED, False
      PARSER_UNRESOLVED                    -> PARSER_UNRESOLVED, False
      NO_MODEL_SUPPORT                     -> UNSUPPORTED_FAMILY, False
      NOT_EVALUATED, discovery_covered     -> UNSUPPORTED_FAMILY, False
                                               (the adapter ran THIS run
                                               and reported UNSUPPORTED
                                               for this exact ticker/rung)
      NOT_EVALUATED, not discovery_covered -> MISSING_INPUT, True
                                               (no discovery run reached
                                               this ticker at all this
                                               run -- a genuine missing
                                               input, not a family gap)
      anything else (DATA_QUALITY_BLOCK,
      MISSING_MARKET_PRICE, INVALID_PROBABILITY,
      or a future status this function doesn't
      yet recognize)                        -> MISSING_INPUT, True

    Never silently omits a status or defaults to EVALUATED for anything
    that isn't genuinely EVALUATED/PARTIAL_EVALUATION or carries a real
    probability.
    """
    if model_fair_probability is not None or evaluation_status in _EVALUATED_STATUSES:
        return PROBABILITY_STATUS_EVALUATED, False
    if evaluation_status == "PARSER_UNRESOLVED":
        return PROBABILITY_STATUS_PARSER_UNRESOLVED, False
    if evaluation_status == "NO_MODEL_SUPPORT":
        return PROBABILITY_STATUS_UNSUPPORTED_FAMILY, False
    if evaluation_status == "NOT_EVALUATED":
        if discovery_covered:
            return PROBABILITY_STATUS_UNSUPPORTED_FAMILY, False
        return PROBABILITY_STATUS_MISSING_INPUT, True
    return PROBABILITY_STATUS_MISSING_INPUT, True


# ── 2. Protected-expression algebra ──────────────────────────────────

_CONFIRMED_THREE_WAY_PERIODS = frozenset(
    scope for scope, status in HORIZON_MARKET_STATUS.items()
    if status.get("outcomeStructureStatus") == "CONFIRMED_THREE_WAY"
)


def protected_expression_supported(period):
    """
    True iff `period` (e.g. 'F3'/'F5'/'F7') has an independently
    CONFIRMED_THREE_WAY outcome structure -- the only condition under
    which a protected-NO probability can be derived from the model's
    own joint distribution rather than an unverified assumption about
    how many outcomes exist. Sourced from the SAME single source of
    truth (lib.research.market_taxonomy.HORIZON_MARKET_STATUS)
    lib.kalshi_probability_adapters.py itself gates its own F3/F5/F7
    winner-market support on -- never a second, independently
    maintained list.
    """
    return period in _CONFIRMED_THREE_WAY_PERIODS


def compute_protected_no_probability(favored_side, away_win_prob, tie_prob, home_win_prob, period=None):
    """
    Pure. Generalizes lib.research.f5_tie_tax.evaluate_f5_tie_tax()'s
    F5-only "PROTECTED_NO" formula (NO on the OPPOSING side's winner
    contract wins on a `favored_side` lead OR a tie) to any period whose
    outcome structure is CONFIRMED_THREE_WAY. f5_tie_tax's own formula
    (p_favored_lead + p_tie) never actually depended on which horizon's
    projections produced those two numbers -- only on all three legs
    summing correctly, which three_way_result_probs() already guarantees
    for F3/F5/F7 alike. No independence assumption is introduced: the
    two summed legs come from ONE joint distribution call, not two
    independently-estimated probabilities multiplied/added together.

    Never approximates from market prices -- this function's only
    inputs are the three joint outcome probabilities themselves.

    Returns (protected_no_probability, basis) where `basis` is a short,
    human-auditable string naming exactly which legs were summed --
    never a bare float with no way to verify the algebra. Returns
    (None, None) -- never a fabricated number -- if:
      - `period` is given and protected_expression_supported(period) is
        False (the exact "document the gap before adding new math" case
        for a period whose outcome structure isn't independently
        verified -- e.g. any future horizon this repo hasn't confirmed
        three-way yet).
      - `favored_side` isn't 'away' or 'home'.
      - any of the three joint probabilities is missing.
    """
    if period is not None and not protected_expression_supported(period):
        return None, None
    if favored_side not in ("away", "home"):
        return None, None
    if away_win_prob is None or tie_prob is None or home_win_prob is None:
        return None, None
    if favored_side == "away":
        return away_win_prob + tie_prob, "P(away leads) + P(tie) [protected NO on the home winner contract]"
    return home_win_prob + tie_prob, "P(home leads) + P(tie) [protected NO on the away winner contract]"


# ── 3. Consistency invariants ────────────────────────────────────────

DEFAULT_TOLERANCE = 1e-6


def binary_complementarity_holds(p_yes, p_no, tolerance=DEFAULT_TOLERANCE):
    """True iff p_yes + p_no == 1 within tolerance. A missing (None)
    probability never trivially "holds" -- callers must check for None
    separately if that's a legitimate case for them."""
    if p_yes is None or p_no is None:
        return False
    return abs((p_yes + p_no) - 1.0) <= tolerance


def outcomes_sum_to_one(probabilities, tolerance=DEFAULT_TOLERANCE):
    """True iff every value in `probabilities` is non-None and they sum
    to 1 within tolerance -- the general N-way-outcome invariant (used
    for Away/Tie/Home, or any other closed outcome set)."""
    if any(p is None for p in probabilities):
        return False
    return abs(sum(probabilities) - 1.0) <= tolerance
