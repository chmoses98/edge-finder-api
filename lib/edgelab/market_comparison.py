"""
lib/edgelab/market_comparison.py
=====================================
EdgeLab Phase 2 Milestone 5 (docs/EDGELAB_MARKET_COMPARISON.md): a
RESEARCH-ONLY same-game market comparison engine. Groups markets within
one game that express overlapping or related theses about the same
underlying baseball outcome (e.g. full-game ML vs F5 ML vs run line for
the same team), normalizes their inputs onto one shape, and produces a
transparent, deterministic comparison score and status per market.

This module does not change production recommendations, staking, or bet
selection in any way -- it is read-only over lib.edgelab.analytics's
existing views and writes nothing back to data/edgelab/<entity>/. Every
score component is a visible, named number computed by an explicit
formula (docs/EDGELAB_MARKET_COMPARISON.md's "score formula" section) --
never a black-box model, never a claim of statistical significance
beyond what lib.edgelab.calibration's existing sample-size gate already
licenses.

Clustering is built ONLY from stable structured fields already on
ModelEvaluation (gameId, marketTicker, canonicalMarketFamily, threshold,
correlationGroups) plus a deterministic horizon/team derivation from
`selection` (the model's own market-naming convention, e.g. "F5_ML_Away")
-- NEVER from title text. lib.research.market_taxonomy.classify_market()
(the repo's one existing ticker classifier) is the authoritative source
for horizon/team whenever a real marketTicker resolved; the `selection`-
based mapping below is the fallback for the common real-data case where
it didn't (PARSER_UNRESOLVED evaluations, ~53% of real committed
records) and mirrors exactly the same naming convention
lib.edgelab.model_evaluation's thesis-tag/correlation-group functions
already established -- not a second, independently-invented convention.
"""
import collections

from lib.edgelab.calibration import calibration_status
from lib.edgelab.model_evaluation import (
    DATA_QUALITY_BLOCK,
    INVALID_PROBABILITY,
    MISSING_MARKET_PRICE,
    NO_MODEL_SUPPORT,
    PARSER_UNRESOLVED,
)
from lib.research.market_taxonomy import (
    FAMILY_FIRST_INNING_RUN,
    FAMILY_GAME_RESULT,
    FAMILY_GAME_TOTAL,
    FAMILY_INNING_RESULT,
    FAMILY_PITCHER_OUTS,
    FAMILY_PITCHER_STRIKEOUTS,
    FAMILY_TEAM_TOTAL,
    FAMILY_WINNING_MARGIN,
)

# ── Market horizon ────────────────────────────────────────────────────────

HORIZON_FULL_GAME = "FULL_GAME"
HORIZON_F3 = "F3"
HORIZON_F5 = "F5"
HORIZON_F7 = "F7"
HORIZON_F1 = "F1"
HORIZON_UNKNOWN = "UNKNOWN"

# (horizon, team) keyed by ModelEvaluation.selection -- the 11-market
# config's own naming convention (config/rules.json's market_list),
# identical mapping to lib.edgelab.model_evaluation's
# correlation_groups_for_row()/thesis_tags_and_evidence_for_row(), reused
# here rather than re-invented a third time.
_SELECTION_HORIZON_TEAM = {
    "ML_Away": (HORIZON_FULL_GAME, "AWAY"),
    "ML_Home": (HORIZON_FULL_GAME, "HOME"),
    "F5_ML_Away": (HORIZON_F5, "AWAY"),
    "F5_ML_Home": (HORIZON_F5, "HOME"),
    "TT_Away_Over": (HORIZON_FULL_GAME, "AWAY"),
    "TT_Home_Over": (HORIZON_FULL_GAME, "HOME"),
    "RL_Away": (HORIZON_FULL_GAME, "AWAY"),
    "RL_Home": (HORIZON_FULL_GAME, "HOME"),
    "Game_Total": (HORIZON_FULL_GAME, None),
    "NRFI": (HORIZON_F1, None),
    "YRFI": (HORIZON_F1, None),
}

# lib.research.market_taxonomy.HORIZON_MARKET_STATUS's own
# outcomeStructureStatus=="CONFIRMED_THREE_WAY" scopes -- a real, tradable
# Tie leg exists for these horizons (an F5/F3/F7 game that's tied at the
# end of that horizon is a real, final, settleable outcome, unlike the
# full game which always continues to extra innings and so has no Tie
# leg at all).
THREE_WAY_HORIZONS = frozenset({HORIZON_F3, HORIZON_F5, HORIZON_F7})

# Structural fact about which horizons typically involve the bullpen at
# all, not a handicapping judgment: a full-game market's outcome can be
# decided by innings 6-9 (bullpen innings); an F3/F5/F7 market settles
# before or right at the boundary where a bullpen typically enters, so
# it structurally carries less bullpen-outcome exposure. This is the
# same "F5 avoids bullpen risk" reasoning docs/EDGELAB_MARKET_COMPARISON.md's
# domination examples rely on -- not a new prediction, a fact about what
# a contract settles on.
BULLPEN_EXPOSED_HORIZONS = frozenset({HORIZON_FULL_GAME})

# ── Thesis groups (what a market is fundamentally a bet ON) ─────────────
THESIS_WIN = "WIN"                    # game_result, inning_result, winning_margin -- "will this team win/cover"
THESIS_TEAM_TOTAL = "TEAM_TOTAL"      # "will this team score over/under N" (also joined by the OPPOSING pitcher's props -- see cluster_key())
THESIS_GAME_TOTAL = "GAME_TOTAL"      # "will the combined score go over/under N"
THESIS_FIRST_INNING = "FIRST_INNING"  # NRFI/YRFI
THESIS_PLAYER_PROP = "PLAYER_PROP"    # pitcher_strikeouts/pitcher_outs for one player

_FAMILY_TO_THESIS = {
    FAMILY_GAME_RESULT: THESIS_WIN,
    FAMILY_INNING_RESULT: THESIS_WIN,
    FAMILY_WINNING_MARGIN: THESIS_WIN,
    FAMILY_TEAM_TOTAL: THESIS_TEAM_TOTAL,
    FAMILY_GAME_TOTAL: THESIS_GAME_TOTAL,
    FAMILY_FIRST_INNING_RUN: THESIS_FIRST_INNING,
    FAMILY_PITCHER_STRIKEOUTS: THESIS_PLAYER_PROP,
    FAMILY_PITCHER_OUTS: THESIS_PLAYER_PROP,
}

# Data-quality ordinal ranking, most-trustworthy first -- shared by the
# domination check ("no worse data quality") and the comparison score.
_DATA_QUALITY_RANK = {"full": 3, "partial": 2, "insufficient": 1, "none": 0}

# evaluationStatus values that mean "there is no usable model view of
# this market at all" (lib.edgelab.model_evaluation's own status
# vocabulary) -- these always resolve to NO_MODEL_SUPPORT regardless of
# any other field, since a comparison needs a real model opinion.
_NO_MODEL_SUPPORT_STATUSES = frozenset(
    {NO_MODEL_SUPPORT, INVALID_PROBABILITY, MISSING_MARKET_PRICE, DATA_QUALITY_BLOCK, PARSER_UNRESOLVED}
)

# Required for a comparison to be anything other than INCOMPLETE_COMPARISON
# (item 4: "missing required inputs must produce an incomplete comparison,
# never guessed values"). Liquidity/bid-ask-spread/starterExposure are
# deliberately NOT required -- they are genuinely unavailable for most
# evaluated-but-never-bet markets (CLV quotes only exist for placed bets)
# and their absence degrades the score/status (LOW_LIQUIDITY when a wide
# spread IS known) rather than blocking the comparison outright.
REQUIRED_INPUT_FIELDS = ("modelFairProbability", "marketImpliedProbability", "estimatedEdge", "dataQuality", "confidence")

# NOTE ON SCALES (real-data finding, not a hypothetical convention):
# modelFairProbability/marketImpliedProbability are stored as 0-100
# percentages throughout this codebase (e.g. 64.93), NOT 0-1 fractions --
# confirmed against real committed ModelEvaluation records. estimatedEdge
# is a smaller "percentage-edge" figure (observed real range roughly
# -11..+5, consistent with lib.edgelab.calibration's own edge-bucket
# width of 2). winProbability/tieProbability/lossProbability (this
# module's own three-way outputs) are kept on that SAME 0-100 scale for
# internal consistency with modelFairProbability.
#
# bidAskSpread CORRECTION (EdgeLab Research Trustworthiness milestone
# follow-up): this comment previously claimed ClvQuote's yesBid/yesAsk
# "ARE a genuine 0-1 dollar fraction" and left bid_ask_spread unrescaled
# in normalize_market_input() below. That was wrong -- confirmed against
# every real committed data/edgelab/clv_quotes/*.jsonl* file (yesAsk
# values are 0-100, e.g. 45.0, median ~26, 99.7% of real values > 1,
# impossible on a 0-1 scale). The cited justification
# ("lib.edgelab.clv.py's '(1 - yesBid)' NO-side derivation only makes
# sense on that scale") had it backwards: lib/edgelab/clv.py's
# _executable_closing_implied actually computes `1.0 - yes_bid / 100.0`
# -- it divides by 100 precisely BECAUSE the raw value is 0-100, which
# only reinforces that ClvQuote's own price fields are 0-100, same as
# every other raw price field in this schema (MarketObservation,
# Settlement). normalize_market_input() now divides by 100 at the one
# place bidAskSpread is computed, so LOW_LIQUIDITY_SPREAD and
# _component_bid_ask_spread's own 0-1-scale assumptions (both already
# correct) now receive a correctly-scaled input.
HIGH_TIE_RISK_THRESHOLD = 20      # tieProbability (0-100 scale) >= this -> HIGH_TIE_RISK (documented, illustrative)
LOW_LIQUIDITY_SPREAD = 0.15       # bidAskSpread (0-1 scale) > this (15c on a $1 contract) -> LOW_LIQUIDITY proxy
LOW_DATA_QUALITY_RANK = 1         # data_quality_rank() <= this ("insufficient"/"none") -> LOW_DATA_QUALITY
EV_SCALE_MIN, EV_SCALE_MAX = -15.0, 15.0   # documented illustrative estimatedEdge (percentage-edge) scale -> 0..1


def market_horizon_and_team(ticker, selection):
    """
    (horizon, team) for one market -- team in {"AWAY", "HOME", None}.
    Combines BOTH sources rather than an all-or-nothing preference: a
    real marketTicker's classify_market() result (the authoritative,
    already-tested parser) supplies the horizon whenever it resolved one,
    and _SELECTION_HORIZON_TEAM's selection-name mapping fills in
    whichever of (horizon, team) classify_market() left unresolved.
    This split is a real-data finding, not a hypothetical: classify_market()
    reliably resolves `scope` (horizon) for a "game_result" ticker but
    always returns `team: None` for that family (the parser doesn't
    extract a side for it) -- an early-return-on-any-classification
    would silently drop the team,  which is only known ~53% of the time,
    from `selection` (PARSER_UNRESOLVED evaluations, Milestone 3/4
    finding) so team is very often ONLY known via selection anyway.
    """
    classified_horizon, classified_team = None, None
    if ticker:
        try:
            from lib.research.market_taxonomy import classify_market
            classified = classify_market(ticker)
        except Exception:
            classified = None
        if classified and classified.get("classificationStatus") == "classified":
            scope = classified.get("scope")
            classified_horizon = {"full_game": HORIZON_FULL_GAME, "F3": HORIZON_F3, "F5": HORIZON_F5,
                                   "F7": HORIZON_F7, "F1": HORIZON_F1}.get(scope, HORIZON_UNKNOWN)
            raw_team = classified.get("team")
            classified_team = {"AWAY": "AWAY", "HOME": "HOME"}.get(raw_team, raw_team) if raw_team else None

    fallback_horizon, fallback_team = _SELECTION_HORIZON_TEAM.get(selection, (HORIZON_UNKNOWN, None))
    horizon = classified_horizon if classified_horizon not in (None, HORIZON_UNKNOWN) else fallback_horizon
    team = classified_team if classified_team is not None else fallback_team
    return horizon, team


def thesis_group(canonical_family):
    return _FAMILY_TO_THESIS.get(canonical_family)


def data_quality_rank(data_quality):
    return _DATA_QUALITY_RANK.get(data_quality, -1)


def _opposite_side(side):
    return {"AWAY": "HOME", "HOME": "AWAY"}.get(side)


# ── Normalization (item 4) ───────────────────────────────────────────────

def normalize_market_input(eval_row, bet_row=None, clv_row=None):
    """
    Builds one normalized comparison-input dict from a v_model_evaluations
    row plus its optionally-linked PlacedBet (entry/closing price, CLV)
    and most-recent ClvQuote (bid/ask spread). Every field is either a
    real value read off an existing record or None -- nothing here is
    computed by re-running any model logic, and `liquidity` is always
    None (documented limitation: no volume/depth field exists anywhere
    in this schema; bidAskSpread is the best available proxy and is used
    for the LOW_LIQUIDITY status instead).
    """
    ticker = eval_row.get("marketTicker")
    selection = eval_row.get("selection")
    horizon, team = market_horizon_and_team(ticker, selection)
    family = eval_row.get("canonicalMarketFamily")

    yes_bid = clv_row.get("yesBid") if clv_row else None
    yes_ask = clv_row.get("yesAsk") if clv_row else None
    # ClvQuote.yesBid/yesAsk are 0-100 on disk (see this module's "NOTE ON
    # SCALES" / bidAskSpread correction above) -- divide by 100 so
    # bidAskSpread is always 0-1, matching LOW_LIQUIDITY_SPREAD and
    # _component_bid_ask_spread's documented scale.
    bid_ask_spread = ((yes_ask - yes_bid) / 100.0) if (yes_bid is not None and yes_ask is not None) else None

    normalized = {
        "marketTicker": ticker,
        "gameId": eval_row.get("gameId"),
        "modelEvaluationId": eval_row.get("modelEvaluationId"),
        "canonicalMarketFamily": family,
        "thesisGroup": thesis_group(family),
        "selection": selection,
        "side": eval_row.get("side"),
        "threshold": eval_row.get("threshold"),
        "horizon": horizon,
        "team": team,
        "evaluationStatus": eval_row.get("evaluationStatus"),
        "modelFairProbability": eval_row.get("modelFairProbability"),
        "marketImpliedProbability": eval_row.get("marketImpliedProbability"),
        "estimatedEdge": eval_row.get("estimatedEdge"),
        "expectedValuePerDollar": eval_row.get("evPerDollar"),
        "confidence": eval_row.get("confidence"),
        "dataQuality": eval_row.get("dataQuality"),
        "lineupConfirmationState": eval_row.get("lineupConfirmationState"),
        "modelVersion": eval_row.get("modelVersion"),
        "modelSource": eval_row.get("modelSource"),
        "thesisTags": list(eval_row.get("thesisTags") or []),
        "correlationGroups": list(eval_row.get("correlationGroups") or []),
        "entryPrice": bet_row.get("entryPrice") if bet_row else None,
        "closingPrice": bet_row.get("closingPrice") if bet_row else None,
        "clv": bet_row.get("clv") if bet_row else None,
        "bidAskSpread": bid_ask_spread,
        "liquidity": None,
        "placedBetIndicator": bet_row is not None,
        "betId": bet_row.get("betId") if bet_row else None,
        # Three-way fields (item 5); populated in place by
        # apply_three_way_adjustment() for THREE_WAY_HORIZONS markets
        # only. comparisonEligibility starts True and is only ever
        # flipped False by that function when a three-way pair's inputs
        # don't support a tie adjustment.
        "winProbability": None,
        "tieProbability": None,
        "lossProbability": None,
        "tieAdjustedFairPrice": None,
        "comparisonEligibility": True,
    }
    normalized["missingFields"] = sorted(f for f in REQUIRED_INPUT_FIELDS if normalized.get(f) is None)
    return normalized


def latest_evaluations_per_market(eval_rows):
    """
    Collapses possibly-multiple ModelEvaluation rows per market down to
    the single most recent one (by createdAt, ties broken by runId) --
    comparisons always operate on the CURRENT edge landscape, never a
    blend of historical snapshots (lib.edgelab.calibration already
    measures how estimates evolved over time). Keyed by marketTicker when
    a real ticker resolved, else by (gameId, selection, side, threshold)
    -- mirroring lib.edgelab.model_evaluation's own market_key fallback
    convention, so PARSER_UNRESOLVED rows for different markets in the
    same game are never collapsed into one.
    """
    latest = {}
    for row in eval_rows:
        ticker = row.get("marketTicker")
        key = ticker if ticker else (row.get("gameId"), row.get("selection"), row.get("side"), row.get("threshold"))
        current = latest.get(key)
        if current is None or _row_is_newer(row, current):
            latest[key] = row
    return list(latest.values())


def _row_is_newer(row, current):
    row_ts, cur_ts = row.get("createdAt"), current.get("createdAt")
    if row_ts != cur_ts:
        return (row_ts or "") > (cur_ts or "")
    return (row.get("runId") or "") > (current.get("runId") or "")


def _fetch_dicts(session, sql):
    rel = session.sql(sql)
    cols = rel.columns
    return [dict(zip(cols, r)) for r in rel.fetchall()]


def build_normalized_inputs(session):
    """
    Reads v_model_evaluations (collapsed to the latest evaluation per
    market), left-joins each row's linked PlacedBet (via
    modelEvaluationId) and the most recent ClvQuote for that ticker, and
    returns one normalize_market_input() dict per market. Returns []
    when the model_evaluations entity isn't available in this session
    (nothing to compare) rather than raising.
    """
    if not session.is_available("model_evaluations"):
        return []

    eval_rows = latest_evaluations_per_market(_fetch_dicts(session, "SELECT * FROM v_model_evaluations"))

    bets_by_eval_id = {}
    if session.is_available("bets"):
        for b in _fetch_dicts(session, "SELECT * FROM v_placed_bets"):
            if b.get("modelEvaluationId"):
                bets_by_eval_id[b["modelEvaluationId"]] = b

    clv_by_ticker = {}
    if session.is_available("clv_quotes"):
        # ORDER BY capturedAt ASC -- later rows overwrite earlier ones in
        # the dict below, so clv_by_ticker[ticker] ends up holding the
        # most recent quote for that ticker.
        for q in _fetch_dicts(session, "SELECT * FROM v_clv_quotes ORDER BY capturedAt ASC"):
            ticker = q.get("marketTicker")
            if ticker:
                clv_by_ticker[ticker] = q

    return [
        normalize_market_input(row, bets_by_eval_id.get(row.get("modelEvaluationId")), clv_by_ticker.get(row.get("marketTicker")))
        for row in eval_rows
    ]


# ── Three-way market modeling (item 5) ───────────────────────────────────

def apply_three_way_adjustment(away_row, home_row):
    """
    Computes winProbability/tieProbability/lossProbability/
    tieAdjustedFairPrice IN PLACE on `away_row` and `home_row`, which must
    be the AWAY and HOME normalized markets for the SAME (gameId,
    horizon) pair where horizon is a THREE_WAY_HORIZONS value (F3/F5/F7).
    tieProbability = max(0, 1 - awayFairProb - homeFairProb) (the model's
    two per-side fair probabilities implicitly determine the tie
    probability since all three outcomes must sum to 1).
    tieAdjustedFairPrice renormalizes win/loss excluding the tie -- the
    fair two-way price this side would carry IF the game were guaranteed
    not to end in a tie at this horizon, which is what makes an F5 side
    comparable to a full-game (structurally tie-free) ML on a like-for-
    like basis. Sets comparisonEligibility=False on both rows (without
    guessing numbers) when either side lacks a real modelFairProbability.
    """
    if away_row.get("horizon") not in THREE_WAY_HORIZONS or home_row.get("horizon") not in THREE_WAY_HORIZONS:
        return
    away_p = away_row.get("modelFairProbability")
    home_p = home_row.get("modelFairProbability")
    if away_p is None or home_p is None:
        away_row["comparisonEligibility"] = False
        home_row["comparisonEligibility"] = False
        return

    tie_p = max(0.0, 100.0 - away_p - home_p)
    for row, win_p in ((away_row, away_p), (home_row, home_p)):
        loss_p = max(0.0, 100.0 - win_p - tie_p)
        row["winProbability"] = win_p
        row["tieProbability"] = tie_p
        row["lossProbability"] = loss_p
        denom = win_p + loss_p
        row["tieAdjustedFairPrice"] = (win_p / denom) if denom > 0 else None
        row["comparisonEligibility"] = True


def apply_three_way_adjustments_for_game(rows):
    """
    Finds every (horizon, side) pair among `rows` (one game's normalized
    markets) where horizon is three-way and both AWAY and HOME sides are
    present, and applies apply_three_way_adjustment() to each pair.
    """
    by_horizon = collections.defaultdict(dict)
    for row in rows:
        if row.get("horizon") in THREE_WAY_HORIZONS and row.get("team") in ("AWAY", "HOME"):
            by_horizon[row["horizon"]][row["team"]] = row
    for sides in by_horizon.values():
        if "AWAY" in sides and "HOME" in sides:
            apply_three_way_adjustment(sides["AWAY"], sides["HOME"])


# ── Clustering (item 3) ───────────────────────────────────────────────────

def cluster_key(row):
    """
    Deterministic cluster assignment (docs/EDGELAB_MARKET_COMPARISON.md
    "clustering rules"). Two markets share a clusterId only when they
    express substantially the same underlying edge for the same game:

    - WIN thesis (game_result/inning_result/winning_margin): one cluster
      per (gameId, side) -- full-game ML, F3/F5/F7 ML, and run line for
      one team are alternate horizons/instruments backing the SAME side.
    - TEAM_TOTAL thesis: one cluster per (gameId, side) for that team's
      own total. A PLAYER_PROP row (pitcher outs/strikeouts) for the
      OPPOSING side's pitcher joins this SAME cluster -- that pitcher's
      outs/strikeouts suppress THIS side's run production, i.e. the same
      underlying edge ("this side's offense will be limited") expressed
      via the opposing pitcher's box-score line instead of this team's
      own total line. See docs/EDGELAB_MARKET_COMPARISON.md's
      "comparability rules" for the side-flip convention and its
      direction caveat (handled at score/domination time, not here).
    - GAME_TOTAL and FIRST_INNING theses are game-level, not side-level:
      one cluster per gameId. These are deliberately never merged with
      the side-level TEAM_TOTAL/WIN clusters -- a combined-score or
      first-inning bet is a genuinely different claim from a single
      team's own total or win, so domination is never evaluated across
      that boundary (comparison_status() reports DISTINCT_THESIS for a
      cluster whose thesis is GAME_TOTAL/FIRST_INNING, never
      DOMINATED_MARKET) even though the two are still visibly grouped
      together in the same game for the historical "related markets"
      report.

    Returns None when the row's thesis is unrecognized (unmapped
    canonicalMarketFamily) or its side can't be resolved -- such a row is
    never clustered/compared (NOT_COMPARABLE).
    """
    thesis = row.get("thesisGroup")
    game_id = row.get("gameId")
    if thesis is None or game_id is None:
        return None

    if thesis in (THESIS_WIN, THESIS_TEAM_TOTAL):
        side = row.get("team")
        if side not in ("AWAY", "HOME"):
            return None
        return f"{game_id}:{side}:{thesis}"

    if thesis == THESIS_PLAYER_PROP:
        pitcher_side = row.get("team")
        opposing_side = _opposite_side(pitcher_side)
        if opposing_side is None:
            return None
        return f"{game_id}:{opposing_side}:{THESIS_TEAM_TOTAL}"

    if thesis in (THESIS_GAME_TOTAL, THESIS_FIRST_INNING):
        return f"{game_id}:GAME:{thesis}"

    return None


def build_clusters(rows):
    """Groups normalized rows by cluster_key(); rows with no key are omitted (reported separately as NOT_COMPARABLE by the caller)."""
    clusters = collections.defaultdict(list)
    for row in rows:
        key = cluster_key(row)
        if key is not None:
            clusters[key].append(row)
    return clusters


# ── Dominated-market detection (item 6) ──────────────────────────────────

def _material_risk(row):
    """
    Ordinal risk score (lower is safer): +1 for a bullpen-exposed
    horizon (BULLPEN_EXPOSED_HORIZONS), +1 for a known high tie
    probability (>= HIGH_TIE_RISK_THRESHOLD). This is a STRUCTURAL risk
    proxy, not a full risk model -- it captures exactly the two concrete
    risk dimensions the milestone's domination examples name (bullpen
    exposure, tie risk); see docs/EDGELAB_MARKET_COMPARISON.md.
    """
    risk = 0
    if row.get("horizon") in BULLPEN_EXPOSED_HORIZONS:
        risk += 1
    tie_p = row.get("tieProbability")
    if tie_p is not None and tie_p >= HIGH_TIE_RISK_THRESHOLD:
        risk += 1
    return risk


def _cleaner_price(other, candidate):
    """
    True when `other`'s bid/ask spread is no wider than `candidate`'s.
    Unknown (either spread missing) never counts against `other` --
    "cleaner price" is a bonus dimension, not a requirement, since
    bidAskSpread is only known for markets with a placed bet's CLV quote.
    """
    other_spread, candidate_spread = other.get("bidAskSpread"), candidate.get("bidAskSpread")
    if other_spread is None or candidate_spread is None:
        return True
    return other_spread <= candidate_spread


def is_dominated_by(candidate, other):
    """
    True when `other` dominates `candidate` per item 6's four criteria.
    Both rows must already be known to share a clusterId (the caller's
    responsibility) -- "substantially the same thesis" is established
    structurally by cluster membership, not re-checked here. Requires at
    least one dimension to be STRICTLY better so two identical markets
    never call each other dominated, and requires a real estimatedEdge on
    both sides (an incomplete comparison is never used to dominate
    anything). Correlation is never a factor here at all -- correlated-
    but-distinct-thesis markets never even share a clusterId (see
    cluster_key()), so they can't reach this function together.
    """
    cand_edge, other_edge = candidate.get("estimatedEdge"), other.get("estimatedEdge")
    if cand_edge is None or other_edge is None:
        return False
    if other_edge < cand_edge:
        return False

    cand_dq, other_dq = data_quality_rank(candidate.get("dataQuality")), data_quality_rank(other.get("dataQuality"))
    if cand_dq < 0 or other_dq < 0 or other_dq < cand_dq:
        return False

    if _material_risk(other) > _material_risk(candidate):
        return False
    if not _cleaner_price(other, candidate):
        return False

    strictly_better = (
        other_edge > cand_edge
        or other_dq > cand_dq
        or _material_risk(other) < _material_risk(candidate)
        or (other.get("bidAskSpread") is not None and candidate.get("bidAskSpread") is not None
            and other["bidAskSpread"] < candidate["bidAskSpread"])
    )
    return strictly_better


def find_dominator(candidate, cluster_rows):
    """Returns the first other row in cluster_rows that dominates `candidate` (deterministic tie-break by marketTicker), or None."""
    others = sorted((r for r in cluster_rows if r is not candidate), key=lambda r: r.get("marketTicker") or "")
    for other in others:
        if is_dominated_by(candidate, other):
            return other
    return None


def domination_reasons(candidate, dominator):
    reasons = []
    if dominator.get("estimatedEdge") is not None and candidate.get("estimatedEdge") is not None:
        if dominator["estimatedEdge"] > candidate["estimatedEdge"]:
            reasons.append("HIGHER_EV")
            # Self-descriptive mirror of HIGHER_EV, on the losing side --
            # matches lib.research.f5_tie_tax's INFERIOR_NET_EV convention
            # (winner tagged BEST_EXPRESSION, loser tagged INFERIOR_NET_EV)
            # so a caller reading ONLY the candidate's own reasons (never
            # cross-referencing the dominator) can still see why it lost.
            reasons.append("INFERIOR_NET_EV")
        elif dominator["estimatedEdge"] == candidate["estimatedEdge"]:
            reasons.append("EQUAL_EV")
    if data_quality_rank(dominator.get("dataQuality")) > data_quality_rank(candidate.get("dataQuality")):
        reasons.append("BETTER_DATA_QUALITY")
    if _material_risk(dominator) < _material_risk(candidate):
        reasons.append("LOWER_MATERIAL_RISK")
    if (dominator.get("bidAskSpread") is not None and candidate.get("bidAskSpread") is not None
            and dominator["bidAskSpread"] < candidate["bidAskSpread"]):
        reasons.append("CLEANER_PRICE")
    # MLB Model Expression Guardrails milestone: a more specific,
    # repo-consistent label for the single most common real-world case
    # of LOWER_MATERIAL_RISK -- an F5 row (never in
    # BULLPEN_EXPOSED_HORIZONS) dominating its own team's full-game
    # counterpart in the same WIN cluster specifically because the
    # full-game horizon carries bullpen exposure the F5 horizon doesn't.
    # Additive: only ever appended alongside LOWER_MATERIAL_RISK above,
    # never instead of it.
    if (dominator.get("horizon") == HORIZON_F5 and candidate.get("horizon") == HORIZON_FULL_GAME
            and dominator.get("team") == candidate.get("team")
            and _material_risk(dominator) < _material_risk(candidate)):
        reasons.append("STARTER_ONLY_THESIS_PREFERS_F5")
    # The mirror image: full-game dominating its own team's F5
    # counterpart on HIGHER_EV despite carrying MORE structural risk
    # (full-game is always >= F5's material risk -- see
    # BULLPEN_EXPOSED_HORIZONS) -- the edge advantage can only be coming
    # from the extra half-inning of bullpen-driven win probability the
    # F5 horizon doesn't capture, i.e. the bullpen is part of the thesis
    # rather than a risk to avoid.
    if (dominator.get("horizon") == HORIZON_FULL_GAME and candidate.get("horizon") == HORIZON_F5
            and dominator.get("team") == candidate.get("team")
            and dominator.get("estimatedEdge") is not None and candidate.get("estimatedEdge") is not None
            and dominator["estimatedEdge"] > candidate["estimatedEdge"]):
        reasons.append("FULL_GAME_BULLPEN_EDGE")
    # A WIN-thesis cluster (game_result/inning_result/winning_margin for
    # ONE team) is, by cluster_key()'s own construction, always alternate
    # horizons/instruments backing the IDENTICAL underlying side -- the
    # same relationship lib.edgelab.thesis_classification and
    # scripts/risk_gate.py already label DUPLICATE_THESIS for exactly
    # this pairing (ML+F5, same team). Surfacing the same, already-used
    # vocabulary here rather than a new one.
    if candidate.get("thesisGroup") == THESIS_WIN and candidate.get("team") == dominator.get("team"):
        reasons.append("DUPLICATE_THESIS")
    return reasons


# ── Transparent deterministic comparison score (item 7) ──────────────────
#
# Illustrative, hand-set default weights summing to 1.0 -- NOT tuned or
# backtested against any outcome data. Every component is independently
# visible in scoreComponents; missing (None) components are excluded from
# the weighted average and the remaining weights renormalized, they are
# never imputed with a guessed/neutral value.
SCORE_WEIGHTS = {
    "ev": 0.25,
    "confidence": 0.10,
    "dataQuality": 0.10,
    "liquidity": 0.05,
    "bidAskSpread": 0.05,
    "priceSensitivity": 0.05,
    "tieRisk": 0.10,
    "bullpenExposure": 0.10,
    "starterExposure": 0.05,
    "horizonFit": 0.05,
    "historicalCalibration": 0.05,
    "correlationConcentration": 0.05,
}


def _component_ev(row):
    edge = row.get("estimatedEdge")
    if edge is None:
        return None
    return max(0.0, min(1.0, (edge - EV_SCALE_MIN) / (EV_SCALE_MAX - EV_SCALE_MIN)))


def _component_confidence(row):
    return {"HIGH": 1.0, "MEDIUM": 0.6, "PAPER": 0.2}.get(row.get("confidence"))


def _component_data_quality(row):
    rank = data_quality_rank(row.get("dataQuality"))
    return (rank / 3.0) if rank >= 0 else None


def _component_liquidity(row):
    return None  # documented: no volume/depth field exists anywhere in this schema; permanently unavailable.


def _component_bid_ask_spread(row):
    spread = row.get("bidAskSpread")
    if spread is None:
        return None
    return max(0.0, min(1.0, 1.0 - spread / 0.20))  # documented scale: a 20c+ spread scores 0, a 0c spread scores 1


def _component_price_sensitivity(row):
    """How far the market price sits from a 50/50 coin flip -- a simple, documented proxy for how much a small probability-estimation error would move the fair-value calculus, not a real greeks-style sensitivity model. marketImpliedProbability is on the 0-100 scale, so 50 is the coin-flip point."""
    p = row.get("marketImpliedProbability")
    return (abs(p - 50.0) / 50.0) if p is not None else None


def _component_tie_risk(row):
    horizon = row.get("horizon")
    if horizon == HORIZON_UNKNOWN:
        return None  # can't assess tie risk without knowing the horizon at all
    tie_p = row.get("tieProbability")  # 0-100 scale, same as modelFairProbability
    if tie_p is None:
        return None if horizon in THREE_WAY_HORIZONS else 1.0
    return max(0.0, 1.0 - tie_p / 50.0)  # documented scale: 50%+ tie probability scores 0


def _component_bullpen_exposure(row):
    horizon = row.get("horizon")
    if horizon == HORIZON_UNKNOWN:
        return None  # can't assess bullpen exposure without knowing the horizon at all
    return 0.0 if horizon in BULLPEN_EXPOSED_HORIZONS else 1.0


def _component_starter_exposure(row):
    """Documented approximation: no dedicated starter-exposure field exists on ModelEvaluation; this reads the STARTER_EDGE/STARTER_FADE thesisTags already assigned from real pipeline evidence (lib.edgelab.model_evaluation) as a directional proxy."""
    tags = row.get("thesisTags") or []
    if "STARTER_EDGE" in tags:
        return 1.0
    if "STARTER_FADE" in tags:
        return 0.0
    return None


def _component_horizon_fit(row):
    return 1.0 if row.get("horizon") != HORIZON_UNKNOWN else None


def _component_historical_calibration(row, calibration_status_by_family):
    if not calibration_status_by_family:
        return None
    status = calibration_status_by_family.get(row.get("canonicalMarketFamily"))
    return {"CALIBRATED": 1.0, "DESCRIPTIVE_ONLY": 0.5}.get(status)  # INSUFFICIENT_SAMPLE/unknown -> None, no claim


def _component_correlation_concentration(row, cluster_rows):
    """Documented simple count-based proxy (NOT a real portfolio covariance model): the fraction of other markets in the same cluster that share at least one of this row's correlationGroups -- more overlap means less independent/diversified exposure."""
    groups = set(row.get("correlationGroups") or [])
    if not groups:
        return None
    peers = [r for r in cluster_rows if r is not row]
    if not peers:
        return 1.0
    overlapping = sum(1 for other in peers if groups & set(other.get("correlationGroups") or []))
    return max(0.0, 1.0 - overlapping / len(peers))


def comparison_score(row, cluster_rows, calibration_status_by_family=None):
    """
    Returns (total, components) where `components` is a dict of every
    named sub-score (item 7's 12 dimensions, None when not computable)
    and `total` is the renormalized weighted average of the available
    components (None when none are available at all).
    """
    components = {
        "ev": _component_ev(row),
        "confidence": _component_confidence(row),
        "dataQuality": _component_data_quality(row),
        "liquidity": _component_liquidity(row),
        "bidAskSpread": _component_bid_ask_spread(row),
        "priceSensitivity": _component_price_sensitivity(row),
        "tieRisk": _component_tie_risk(row),
        "bullpenExposure": _component_bullpen_exposure(row),
        "starterExposure": _component_starter_exposure(row),
        "horizonFit": _component_horizon_fit(row),
        "historicalCalibration": _component_historical_calibration(row, calibration_status_by_family),
        "correlationConcentration": _component_correlation_concentration(row, cluster_rows),
    }
    available = {k: v for k, v in components.items() if v is not None}
    if not available:
        return None, components
    weight_sum = sum(SCORE_WEIGHTS[k] for k in available)
    total = sum(SCORE_WEIGHTS[k] * v for k, v in available.items()) / weight_sum
    return total, components


# ── Comparison statuses (item 8) ─────────────────────────────────────────

STATUS_BEST_EXPRESSION = "BEST_EXPRESSION"
STATUS_ALTERNATIVE_EXPRESSION = "ALTERNATIVE_EXPRESSION"
STATUS_DOMINATED_MARKET = "DOMINATED_MARKET"
STATUS_INCOMPLETE_COMPARISON = "INCOMPLETE_COMPARISON"
STATUS_NO_MODEL_SUPPORT = "NO_MODEL_SUPPORT"
STATUS_LOW_DATA_QUALITY = "LOW_DATA_QUALITY"
STATUS_LOW_LIQUIDITY = "LOW_LIQUIDITY"
STATUS_HIGH_TIE_RISK = "HIGH_TIE_RISK"
STATUS_DISTINCT_THESIS = "DISTINCT_THESIS"
STATUS_NOT_COMPARABLE = "NOT_COMPARABLE"

# GAME_TOTAL/FIRST_INNING clusters are grouped for visibility but never
# domination-tested (see cluster_key()'s docstring) -- a row whose
# thesisGroup is one of these always resolves to DISTINCT_THESIS, never
# DOMINATED_MARKET/BEST_EXPRESSION.
_NON_DOMINATION_THESES = frozenset({THESIS_GAME_TOTAL, THESIS_FIRST_INNING})


def _status_precedence(row, cluster_rows, calibration_status_by_family):
    """
    Returns (status, dominatorRow-or-None) for one row, applying item 8's
    statuses in a fixed, documented precedence order (docs/EDGELAB_MARKET_COMPARISON.md):
    incompleteness and model-support gates first, then data-quality and
    risk gates, then domination, then thesis-boundary/unclustered
    fallbacks, and only then the relative BEST/ALTERNATIVE ranking
    (returned as (None, None) for the caller to resolve by rank).
    """
    if row.get("missingFields"):
        return STATUS_INCOMPLETE_COMPARISON, None
    if row.get("evaluationStatus") in _NO_MODEL_SUPPORT_STATUSES:
        return STATUS_NO_MODEL_SUPPORT, None
    if data_quality_rank(row.get("dataQuality")) <= LOW_DATA_QUALITY_RANK:
        return STATUS_LOW_DATA_QUALITY, None
    tie_p = row.get("tieProbability")
    if tie_p is not None and tie_p >= HIGH_TIE_RISK_THRESHOLD:
        return STATUS_HIGH_TIE_RISK, None
    spread = row.get("bidAskSpread")
    if spread is not None and spread > LOW_LIQUIDITY_SPREAD:
        return STATUS_LOW_LIQUIDITY, None
    if not row.get("comparisonEligibility", True):
        return STATUS_INCOMPLETE_COMPARISON, None

    if row.get("thesisGroup") in _NON_DOMINATION_THESES:
        return STATUS_DISTINCT_THESIS, None

    if cluster_key(row) is None:
        return STATUS_NOT_COMPARABLE, None

    dominator = find_dominator(row, cluster_rows)
    if dominator is not None:
        return STATUS_DOMINATED_MARKET, dominator

    return None, None  # resolved below by relative rank


def assign_comparison_statuses(cluster_rows, calibration_status_by_family=None):
    """
    Assigns comparisonStatus/comparisonRank/score/scoreComponents/
    dominantMarketTicker/dominationReasons to every row in one cluster
    (a list from build_clusters()'s values). Mutates and returns the SAME
    row dicts in place. Ranking among rows that reach the BEST/
    ALTERNATIVE stage is by score descending; ties are broken by
    marketTicker for full determinism.
    """
    scored = {}
    for row in cluster_rows:
        score, components = comparison_score(row, cluster_rows, calibration_status_by_family)
        scored[id(row)] = (score, components)

    gated = []
    for row in cluster_rows:
        status, dominator = _status_precedence(row, cluster_rows, calibration_status_by_family)
        score, components = scored[id(row)]
        row["score"] = score
        row["scoreComponents"] = components
        if status is not None:
            row["comparisonStatus"] = status
            row["dominantMarketTicker"] = dominator.get("marketTicker") if dominator else None
            row["dominationReasons"] = domination_reasons(row, dominator) if dominator else []
            row["comparisonRank"] = None
        else:
            gated.append(row)

    ranked = sorted(gated, key=lambda r: (-(r["score"] if r["score"] is not None else -1), r.get("marketTicker") or ""))
    for i, row in enumerate(ranked):
        row["comparisonStatus"] = STATUS_BEST_EXPRESSION if i == 0 else STATUS_ALTERNATIVE_EXPRESSION
        row["comparisonRank"] = i + 1
        row["dominantMarketTicker"] = None
        row["dominationReasons"] = []

    return cluster_rows


# ── Top-level orchestration ──────────────────────────────────────────────

def build_comparisons(session, calibration_status_by_family=None):
    """
    RESEARCH-ONLY end-to-end entry point (docs/EDGELAB_MARKET_COMPARISON.md):
    builds normalized inputs for every market with a ModelEvaluation,
    applies the three-way tie adjustment per game, clusters same-game
    markets by cluster_key(), scores and assigns a comparisonStatus to
    every market, and returns one flat list of comparison-result dicts
    (item 10's output shape). Writes nothing back to data/edgelab/ -- the
    caller (scripts/edgelab/run_market_comparison_report.py) is
    responsible for rendering this to a report file, if at all.
    """
    rows = build_normalized_inputs(session)

    by_game = collections.defaultdict(list)
    for row in rows:
        by_game[row.get("gameId")].append(row)
    for game_rows in by_game.values():
        apply_three_way_adjustments_for_game(game_rows)

    clusters = build_clusters(rows)
    clustered_ids = set()
    for cluster_id, cluster_rows in clusters.items():
        assign_comparison_statuses(cluster_rows, calibration_status_by_family)
        for row in cluster_rows:
            row["clusterId"] = cluster_id
            clustered_ids.add(id(row))

    unclustered = [row for row in rows if id(row) not in clustered_ids]
    for row in unclustered:
        status, _ = _status_precedence(row, [row], calibration_status_by_family)
        score, components = comparison_score(row, [row], calibration_status_by_family)
        row["clusterId"] = None
        row["score"] = score
        row["scoreComponents"] = components
        row["comparisonStatus"] = status or STATUS_NOT_COMPARABLE
        row["comparisonRank"] = None
        row["dominantMarketTicker"] = None
        row["dominationReasons"] = []

    return rows


def comparison_markets_lookup(comparisons):
    """
    MLB Model Expression Guardrails milestone. Pure. Reduces
    build_comparisons()'s flat output to
    {marketTicker: [otherMarketTicker in the same cluster, ...]} --
    exactly the shape lib.edgelab.recommendations.
    build_recommendations_from_pipeline()'s `comparison_lookup` parameter
    expects, so this module's already-built, already-tested clustering
    can populate Recommendation.comparisonMarkets (previously a
    documented, permanently-empty field) without recommendations.py ever
    needing to know how a cluster is computed.

    A row with no marketTicker or no clusterId (unclustered/
    NOT_COMPARABLE) is simply absent from the returned dict -- never a
    fabricated empty relationship.
    """
    by_cluster = collections.defaultdict(list)
    for row in comparisons:
        ticker = row.get("marketTicker")
        cluster_id = row.get("clusterId")
        if ticker and cluster_id:
            by_cluster[cluster_id].append(ticker)

    lookup = {}
    for tickers in by_cluster.values():
        if len(tickers) < 2:
            continue
        for t in tickers:
            lookup[t] = sorted(other for other in tickers if other != t)
    return lookup


def comparison_annotations_lookup(comparisons):
    """
    MLB Model Expression Guardrails milestone. Pure. Reduces
    build_comparisons()'s flat output to
    {marketTicker: {'comparisonStatus', 'dominantMarketTicker',
    'dominationReasons'}} -- the per-ticker verdict
    (BEST_EXPRESSION/ALTERNATIVE_EXPRESSION/DOMINATED_MARKET/
    DISTINCT_THESIS/NOT_COMPARABLE/...) and, when dominated, which
    ticker dominates it and why (HIGHER_EV/INFERIOR_NET_EV/
    BETTER_DATA_QUALITY/LOWER_MATERIAL_RISK/CLEANER_PRICE/
    STARTER_ONLY_THESIS_PREFERS_F5/FULL_GAME_BULLPEN_EDGE/
    DUPLICATE_THESIS -- see domination_reasons()).

    A row with no marketTicker is simply absent -- never a fabricated
    verdict. Every OTHER row (clustered or not) is included, since
    comparisonStatus is meaningful even for an unclustered
    NOT_COMPARABLE/DISTINCT_THESIS row (build_comparisons() always sets
    it), unlike comparison_markets_lookup() above, which only makes
    sense for actually-clustered rows.
    """
    lookup = {}
    for row in comparisons:
        ticker = row.get("marketTicker")
        if not ticker:
            continue
        lookup[ticker] = {
            "comparisonStatus": row.get("comparisonStatus"),
            "dominantMarketTicker": row.get("dominantMarketTicker"),
            "dominationReasons": list(row.get("dominationReasons") or []),
        }
    return lookup


# ── Historical analysis (item 9) ─────────────────────────────────────────

def historical_analysis(comparisons):
    """
    Aggregate research report over build_comparisons()'s output (item 9).
    Applies lib.edgelab.calibration's existing three-tier sample-size gate
    to the placed-bet-vs-top-alternative comparison specifically -- with
    fewer than MIN_N_INSUFFICIENT (20) cases, per-case examples are still
    returned (for manual review) but no aggregate rate/claim is computed,
    and nothing here is ever phrased as a superiority claim regardless of
    sample size (research measurement only, docs/EDGELAB_MARKET_COMPARISON.md).
    """
    by_cluster = collections.defaultdict(list)
    for c in comparisons:
        if c.get("clusterId"):
            by_cluster[c["clusterId"]].append(c)

    games_with_comparable_markets = len({
        c["gameId"] for members in by_cluster.values() if len(members) > 1 for c in members
    })
    expression_clusters = sum(1 for members in by_cluster.values() if len(members) > 1)

    best_expression_by_family = collections.Counter(
        c["canonicalMarketFamily"] for c in comparisons if c["comparisonStatus"] == STATUS_BEST_EXPRESSION
    )
    dominated_by_family = collections.Counter(
        c["canonicalMarketFamily"] for c in comparisons if c["comparisonStatus"] == STATUS_DOMINATED_MARKET
    )
    missing_data_blockers = collections.Counter(
        ",".join(c["missingFields"]) or "(none)"
        for c in comparisons if c["comparisonStatus"] == STATUS_INCOMPLETE_COMPARISON
    )

    placed_bet_audit = []
    for members in by_cluster.values():
        if len(members) < 2:
            continue
        ranked = [c for c in members if c.get("comparisonRank")]
        if not ranked:
            continue
        top = min(ranked, key=lambda c: c["comparisonRank"])
        for c in members:
            if not c.get("placedBetIndicator"):
                continue
            placed_bet_audit.append({
                "gameId": c["gameId"],
                "clusterId": c["clusterId"],
                "placedMarketTicker": c["marketTicker"],
                "topMarketTicker": top["marketTicker"],
                "wasTopRanked": c["marketTicker"] == top["marketTicker"],
                "placedClv": c.get("clv"),
                "topClv": top.get("clv"),
            })

    not_top_ranked = [a for a in placed_bet_audit if not a["wasTopRanked"]]
    n = len(placed_bet_audit)

    return {
        "gamesWithComparableMarkets": games_with_comparable_markets,
        "expressionClusters": expression_clusters,
        "bestExpressionCountsByFamily": dict(best_expression_by_family),
        "dominatedMarketCountsByFamily": dict(dominated_by_family),
        "missingDataBlockers": dict(missing_data_blockers),
        "placedBetAuditSampleSize": n,
        "placedBetAuditSampleStatus": calibration_status(n),
        "placedBetNotTopRankedCount": len(not_top_ranked),
        "placedBetAuditExamples": placed_bet_audit[:25],
    }
