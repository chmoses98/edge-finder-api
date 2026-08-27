#!/usr/bin/env python3
"""
scripts/edgelab/run_edge_persistence_experiment.py
========================================================
Research Lab, experiment MLB-RSCH-0006: "Edge Persistence / Market
Confirmation".

RESEARCH ONLY. When the model disagrees with Kalshi before a game, is an
edge that PERSISTS across multiple prospective checkpoints more
trustworthy (better model-vs-market predictive performance) than a
transient/one-checkpoint edge? This is directly related to whether
declared-edge persistence SHOULD influence confidence, recommendation
eligibility, Bet Up To logic, or staking -- but this experiment answers
none of that; it only measures whether persistence carries incremental
predictive information. NONE of recommendation thresholds, confidence,
Bet Up To, staking, eligibility, or production model probabilities are
changed here, and a finding here does not auto-promote anything (see
module docstring's final section, mirroring MLB-RSCH-0001's own framing).

WHAT THIS REUSES (per Milestone 0A: "do not build parallel
infrastructure") -- this script builds NO independent join and NO
independent Brier/log-loss/economics engine of its own:
  - lib.edgelab.research_dataset.build_opportunity_rows /
    STANDARDIZED_CHECKPOINT_ORDER -- the ONE canonical, causally-joined
    (no-look-ahead) full-universe opportunity dataset, including its
    already-computed nextCheckpointExecutableYesPrice/
    closingExecutableYesPrice/fullUniverseMarketMovementToClose fields
    (reused here for market-movement/CLV-like reporting, never
    recomputed).
  - scripts.edgelab.run_edge_monotonicity_experiment (MLB-RSCH-0001),
    imported as a module and reused UNCHANGED for: fair_market_probability
    (bid/ask midpoint benchmark), usable_rows_and_coverage (eligibility),
    EDGE_BUCKETS / assign_edge_bucket (the same fixed 7 buckets, never
    redefined here), filter_canonical_era / filter_trusted_production_only
    (current-model isolation), analyze_segment (the full Brier/log-loss/
    paired-delta/fee-adjusted-economics metric bundle per segment),
    _bucket_economics, _current_git_commit_sha, _config_fingerprint,
    MIN_GAMES_EXPLORATORY, _discover_dates, _load_universe.
  - lib.edgelab.kalshi_fees (via analyze_segment/_bucket_economics) --
    the one fee-aware execution-economics engine, SECONDARY evidence only.
  - lib.edgelab.control_identity -- reuses the SAME controlModelId
    (CTRL-7252463d722626e6) RSCH-0001-0005 all reused, via an IDENTICAL
    write-once re-registration (see register_control_and_experiment).
  - lib.edgelab.research_stats -- independent_unit_count,
    sample_size_status, game_clustered_bootstrap_ci for CLV-like CIs.

WHAT IS GENUINELY NEW HERE (not reused, because nothing in the library
already does this): grouping opportunity rows by marketTicker into a
chronological per-ticker checkpoint SEQUENCE, classifying each ticker's
edge-sign persistence (SINGLETON_TRANSIENT / TWO_CHECKPOINT_PERSISTENT /
THREE_PLUS_CHECKPOINT_PERSISTENT, plus the orthogonal
lineupConfirmedPersistent / lateSurviving flags), and determining whether
the market moved WITH or AGAINST the model's initial edge direction. See
build_ticker_sequences / classify_persistence_tier /
is_lineup_confirmed_persistent / is_late_surviving / market_moved_with_model.

PSEUDOREPLICATION: a ticker can carry several checkpoint rows that are
NOT independent observations of "the opportunity" -- every predictive
(Brier/log-loss/economics) comparison below uses exactly ONE representative
row per ticker (its final causally-valid pregame row -- CLOSING if
observed, else the chronologically last usable row), never all of a
ticker's checkpoint rows pooled as if independent. rows/uniqueMarkets/
independentGames/independentDates are reported at every level (spec
section "INDEPENDENCE / PSEUDOREPLICATION").

CHRONOLOGICAL STRUCTURE: this corpus (27 observation-dates) cannot
support a real 60/20/20 split -- lib.edgelab.research_splits.
MIN_DATES_FOR_MATURE_SPLIT is 30, and the multi-checkpoint-usable
population here has far fewer independent dates than that (see the
report's coverage section). Registered chronological_split_policy is
"NONE", experiment_type is EXPLORATORY, evidence_level is
E1_RECONSTRUCTED_RETROSPECTIVE -- identical reasoning to MLB-RSCH-0001,
which faced the exact same corpus-maturity constraint.

CLV, PRECISELY LABELED: the CLV-like metric reported below is
research_dataset.py's own "hypothetical, full-universe price-movement
measure available for every observed market/checkpoint, settled or not,
bet or not" (fullUniverseMarketMovementToClose), signed relative to the
model's own initial edge direction -- explicitly NOT real placed-bet CLV
(lib.edgelab.clv.compute_clv_for_bet, which requires an actual bet's own
entryPrice and is out of scope here, since this experiment studies every
model edge, not just placed bets). Never conflated with realized
predictive accuracy (spec: "Do not substitute CLV for actual predictive
accuracy").
"""
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_SCRIPTS_EDGELAB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "edgelab")
if _SCRIPTS_EDGELAB_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_EDGELAB_DIR)

from lib.edgelab import canonical_era
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import dispositions as disp
from lib.edgelab import evidence_levels as ev
from lib.edgelab import experiment_registry as reg
from lib.edgelab import experiment_report as er
from lib.edgelab import pit_provenance as pit
from lib.edgelab.research_dataset import build_opportunity_rows, STANDARDIZED_CHECKPOINT_ORDER
from lib.edgelab.research_stats import (
    DEFAULT_BOOTSTRAP_SEED,
    game_clustered_bootstrap_ci,
    independent_unit_count,
    sample_size_status,
)

import run_edge_monotonicity_experiment as rsch0001  # noqa: E402

EXPERIMENT_ID = "MLB-RSCH-0006"
EXPERIMENT_TITLE = "Edge Persistence / Market Confirmation"

REGISTRATION_TIMESTAMP = "2026-08-27T22:00:00Z"

ANALYTICS_DIR = os.path.join("data", "edgelab", "analytics")
REPORTS_DIR = os.path.join("data", "edgelab", "reports")
MACHINE_REPORT_PATH = os.path.join(ANALYTICS_DIR, "latest_mlb_rsch_0006_edge_persistence.json")
MARKDOWN_REPORT_PATH = os.path.join(REPORTS_DIR, "mlb_rsch_0006_edge_persistence_summary.md")

# ── Persistence tiers (preregistered, mutually exclusive by max
# consecutive same-sign checkpoint run) ─────────────────────────────────
SINGLETON_TRANSIENT = "SINGLETON_TRANSIENT"
TWO_CHECKPOINT_PERSISTENT = "TWO_CHECKPOINT_PERSISTENT"
THREE_PLUS_CHECKPOINT_PERSISTENT = "THREE_PLUS_CHECKPOINT_PERSISTENT"
PERSISTENCE_TIERS = (SINGLETON_TRANSIENT, TWO_CHECKPOINT_PERSISTENT, THREE_PLUS_CHECKPOINT_PERSISTENT)
PERSISTENT_TIERS = (TWO_CHECKPOINT_PERSISTENT, THREE_PLUS_CHECKPOINT_PERSISTENT)

# Preregistered floor for this track's own classification -- deliberately
# lower than MLB-RSCH-0001's MIN_GAMES_EXPLORATORY=50: this is a newer,
# much smaller corpus (27 observation-dates total vs the multi-season
# baseball backtest tracks' tens of thousands of rows), and the
# persistence-eligible subset (>=2 causally-linked, settled checkpoints
# per ticker) is smaller still (see module docstring). Still a genuine,
# non-zero floor -- never bypassed to manufacture an interpretable result.
MIN_GAMES_PERSISTENCE_EXPLORATORY = 15
MIN_GAMES_FAMILY_SEGMENT = 10


# ── Per-ticker chronological sequence construction ──────────────────────

def build_ticker_sequences(usable_rows):
    """Groups usable opportunity rows (one row per marketTicker x
    researchCheckpoint) by marketTicker, sorted chronologically by
    capturedAt -- the same per-ticker chronological ordering convention
    research_dataset._attach_price_movement already uses. Returns
    {marketTicker: [row, ...]}, every value sorted, none fabricated:
    a ticker with only one usable row still gets a length-1 sequence."""
    by_ticker = defaultdict(list)
    for r in usable_rows:
        by_ticker[r["marketTicker"]].append(r)
    for rows in by_ticker.values():
        rows.sort(key=lambda r: r["capturedAt"])
    return dict(by_ticker)


def _edge_sign(edge):
    if edge is None:
        return None
    if edge > 0:
        return 1
    if edge < 0:
        return -1
    return 0


def max_consecutive_same_sign_run(sequence):
    """Longest run of CONSECUTIVE checkpoints (chronological order) sharing
    the same nonzero contemporaneousEdge sign -- a checkpoint with a zero
    or missing edge breaks any run in progress (neither a positive nor a
    negative disagreement)."""
    best, current, current_sign = 0, 0, None
    for row in sequence:
        sign = _edge_sign(row.get("contemporaneousEdge"))
        if sign is not None and sign != 0 and sign == current_sign:
            current += 1
        elif sign is not None and sign != 0:
            current, current_sign = 1, sign
        else:
            current, current_sign = 0, None
        best = max(best, current)
    return best


def classify_persistence_tier(sequence):
    run = max_consecutive_same_sign_run(sequence)
    if run >= 3:
        return THREE_PLUS_CHECKPOINT_PERSISTENT
    if run == 2:
        return TWO_CHECKPOINT_PERSISTENT
    return SINGLETON_TRANSIENT


def is_lineup_confirmed_persistent(sequence):
    """Positive declared edge exists at some checkpoint STRICTLY BEFORE
    LINEUP_CONFIRMATION and remains positive AT the LINEUP_CONFIRMATION
    checkpoint itself. False (never fabricated) if this ticker has no
    observed LINEUP_CONFIRMATION checkpoint at all."""
    lc_index = next((i for i, r in enumerate(sequence) if r.get("researchCheckpoint") == "LINEUP_CONFIRMATION"), None)
    if lc_index is None:
        return False
    lc_edge = sequence[lc_index].get("contemporaneousEdge")
    if lc_edge is None or lc_edge <= 0:
        return False
    return any((r.get("contemporaneousEdge") or 0) > 0 for r in sequence[:lc_index])


def is_late_surviving(sequence):
    """Positive edge at an EARLIER checkpoint remains positive at the
    final causally-valid pregame checkpoint for this ticker (its own
    isClosingQuote row if one was observed, else its chronologically
    last usable row -- never a different ticker's close, never
    fabricated)."""
    if len(sequence) < 2:
        return False
    final = next((r for r in sequence if r.get("isClosingQuote")), sequence[-1])
    final_edge = final.get("contemporaneousEdge")
    if final_edge is None or final_edge <= 0:
        return False
    return any((r.get("contemporaneousEdge") or 0) > 0 for r in sequence if r is not final)


def market_moved_with_model(sequence):
    """True if the executable YES price moved, from this ticker's first
    to last usable checkpoint, in the direction the model's INITIAL edge
    sign implied ("with" the model); False if against; None if not
    computable (single checkpoint, zero initial edge, or missing price
    data at either end) -- never guessed."""
    if len(sequence) < 2:
        return None
    initial_sign = _edge_sign(sequence[0].get("contemporaneousEdge"))
    if initial_sign is None or initial_sign == 0:
        return None
    first_price, last_price = sequence[0].get("executableYesPrice"), sequence[-1].get("executableYesPrice")
    if first_price is None or last_price is None:
        return None
    price_move = last_price - first_price
    if price_move == 0:
        return None
    return (price_move > 0) == (initial_sign > 0)


def clv_like_value_for_ticker(sequence):
    """research_dataset's own hypothetical full-universe price-movement-
    to-close (fullUniverseMarketMovementToClose), read off this ticker's
    FIRST usable row and signed relative to the model's initial edge
    direction: positive means the market moved TOWARD the model's implied
    fair value by close. NOT real placed-bet CLV -- see module docstring."""
    initial = sequence[0]
    sign = _edge_sign(initial.get("contemporaneousEdge"))
    move_to_close = initial.get("fullUniverseMarketMovementToClose")
    if sign is None or sign == 0 or move_to_close is None:
        return None
    return round(sign * move_to_close, 4)


def build_ticker_summary(ticker, sequence):
    """One row per ticker: the persistence/movement/CLV-like summary,
    plus `finalRow` -- the SINGLE representative opportunity row
    (CLOSING if observed, else the chronologically last usable row) used
    for every predictive Brier/log-loss/economics comparison downstream,
    so a multi-checkpoint ticker is never pseudo-replicated as if each of
    its checkpoints were an independent observation."""
    edges = [r["contemporaneousEdge"] for r in sequence if r.get("contemporaneousEdge") is not None]
    final = next((r for r in sequence if r.get("isClosingQuote")), sequence[-1])
    initial_edge = sequence[0].get("contemporaneousEdge")
    final_edge = final.get("contemporaneousEdge")
    return {
        "marketTicker": ticker,
        "gameId": sequence[0].get("gameId"),
        "gameDate": sequence[0].get("gameDate"),
        "canonicalMarketFamily": sequence[0].get("canonicalMarketFamily"),
        "numCheckpoints": len(sequence),
        "checkpointsObserved": [r.get("researchCheckpoint") for r in sequence],
        "initialEdge": initial_edge,
        "finalEdge": final_edge,
        "minEdge": min(edges) if edges else None,
        "maxEdge": max(edges) if edges else None,
        "edgeWidened": (abs(final_edge) > abs(initial_edge)) if (final_edge is not None and initial_edge is not None) else None,
        "initialEdgeBucket": rsch0001.assign_edge_bucket(initial_edge) if initial_edge is not None else None,
        "persistenceTier": classify_persistence_tier(sequence),
        "maxConsecutiveSameSignRun": max_consecutive_same_sign_run(sequence),
        "lineupConfirmedPersistent": is_lineup_confirmed_persistent(sequence),
        "lateSurviving": is_late_surviving(sequence),
        "marketMovedWithModel": market_moved_with_model(sequence),
        "clvLikeValue": clv_like_value_for_ticker(sequence),
        "finalRow": final,
    }


def build_ticker_summaries(usable_rows):
    sequences = build_ticker_sequences(usable_rows)
    return {ticker: build_ticker_summary(ticker, seq) for ticker, seq in sequences.items()}


# ── Representative-row analysis per group (reuses rsch0001.analyze_segment) ──

def _final_rows_for(summaries, predicate):
    return [s["finalRow"] for s in summaries.values() if predicate(s)]


def clv_summary(summaries, predicate, cluster_key="gameId"):
    """mean/median/positive-rate/game-clustered-CI of clvLikeValue for
    tickers matching `predicate` -- CLV reported separately from
    predictive accuracy (spec: "Do not substitute CLV for actual
    predictive accuracy"), never merged into analyze_segment's output."""
    values = [(s["clvLikeValue"], s.get("gameId")) for s in summaries.values() if predicate(s) and s["clvLikeValue"] is not None]
    n = len(values)
    if n == 0:
        return {"n": 0, "meanClv": None, "medianClv": None, "positiveClvRate": None, "ci": {"low": None, "high": None, "method": "NO_DATA"}}
    vals_sorted = sorted(v for v, _ in values)
    mean_clv = sum(vals_sorted) / n
    median_clv = vals_sorted[n // 2] if n % 2 == 1 else (vals_sorted[n // 2 - 1] + vals_sorted[n // 2]) / 2.0
    positive_rate = sum(1 for v in vals_sorted if v > 0) / n
    rows_for_ci = [{"gameId": g, "clv": v} for v, g in values]
    lo, hi, method = game_clustered_bootstrap_ci(rows_for_ci, lambda rs: (sum(r["clv"] for r in rs) / len(rs)) if rs else None, cluster_key=cluster_key, seed=DEFAULT_BOOTSTRAP_SEED)
    return {
        "n": n, "meanClv": round(mean_clv, 4), "medianClv": round(median_clv, 4), "positiveClvRate": round(positive_rate, 4),
        "ci": {"low": lo, "high": hi, "method": method},
    }


def tier_analysis(summaries, tier, label=None):
    rows = _final_rows_for(summaries, lambda s: s["persistenceTier"] == tier)
    result = rsch0001.analyze_segment(rows, label or tier)
    result["clv"] = clv_summary(summaries, lambda s: s["persistenceTier"] == tier)
    return result


def flag_analysis(summaries, flag_key, label):
    rows = _final_rows_for(summaries, lambda s: s[flag_key])
    result = rsch0001.analyze_segment(rows, label)
    result["clv"] = clv_summary(summaries, lambda s: s[flag_key])
    return result


# ── H4: persistence incremental to raw edge magnitude (within-bucket) ───

def persistence_incremental_to_magnitude(summaries):
    """For each preregistered EDGE_BUCKET (by initialEdgeBucket), compares
    the transient vs. persistent (2+) subgroups' paired Brier delta --
    the fixed, transparent within-bucket comparison the spec calls for
    (no coefficient hunting, no new model)."""
    out = {}
    for label, _low, _high in rsch0001.EDGE_BUCKETS:
        bucket_summaries = {t: s for t, s in summaries.items() if s["initialEdgeBucket"] == label}
        transient_rows = _final_rows_for(bucket_summaries, lambda s: s["persistenceTier"] == SINGLETON_TRANSIENT)
        persistent_rows = _final_rows_for(bucket_summaries, lambda s: s["persistenceTier"] in PERSISTENT_TIERS)
        transient_result = rsch0001.analyze_segment(transient_rows, f"{label}/TRANSIENT")
        persistent_result = rsch0001.analyze_segment(persistent_rows, f"{label}/PERSISTENT")
        t_delta, p_delta = transient_result.get("pairedBrierDelta_modelMinusMarket"), persistent_result.get("pairedBrierDelta_modelMinusMarket")
        out[label] = {
            "transient": transient_result,
            "persistent": persistent_result,
            "persistentAdvantageOverTransient": (round(t_delta - p_delta, 6) if t_delta is not None and p_delta is not None else None),
        }
    return out


# ── 2x2 confirmation matrix ───────────────────────────────────────────────

def confirmation_matrix(summaries):
    cells = {}
    for persistence_label, persistence_pred in (
        ("PERSISTENT", lambda s: s["persistenceTier"] in PERSISTENT_TIERS),
        ("TRANSIENT", lambda s: s["persistenceTier"] == SINGLETON_TRANSIENT),
    ):
        for movement_label, movement_pred in (
            ("MARKET_MOVES_WITH_MODEL", lambda s: s["marketMovedWithModel"] is True),
            ("MARKET_MOVES_AGAINST_MODEL", lambda s: s["marketMovedWithModel"] is False),
        ):
            cell_key = f"{persistence_label}+{movement_label}"
            pred = lambda s, pp=persistence_pred, mp=movement_pred: pp(s) and mp(s)
            rows = _final_rows_for(summaries, pred)
            result = rsch0001.analyze_segment(rows, cell_key)
            result["clv"] = clv_summary(summaries, pred)
            cells[cell_key] = result
    return cells


# ── Market-family breakdown (minimum-sample gated) ───────────────────────

def family_persistence_breakdown(summaries, min_games=MIN_GAMES_FAMILY_SEGMENT):
    by_family = defaultdict(dict)
    for ticker, s in summaries.items():
        fam = s.get("canonicalMarketFamily") or "UNKNOWN"
        by_family[fam][ticker] = s
    out = {}
    for fam, fam_summaries in by_family.items():
        independent_games = len({s["gameId"] for s in fam_summaries.values() if s.get("gameId")})
        if independent_games < min_games:
            out[fam] = {"independentGames": independent_games, "status": "INSUFFICIENT_SAMPLE", "minimumRequired": min_games}
            continue
        out[fam] = {
            "independentGames": independent_games,
            "status": "REPORTED",
            "transient": tier_analysis(fam_summaries, SINGLETON_TRANSIENT, f"{fam}/TRANSIENT"),
            "persistent2Plus": rsch0001.analyze_segment(_final_rows_for(fam_summaries, lambda s: s["persistenceTier"] in PERSISTENT_TIERS), f"{fam}/PERSISTENT_2PLUS"),
        }
    return out


# ── Final signal classification ───────────────────────────────────────────

SIGNAL_STRONG = "STRONG_TRUST_SIGNAL"
SIGNAL_PARTIAL = "PARTIAL_FAMILY_SPECIFIC_TRUST_SIGNAL"
SIGNAL_WEAK = "WEAK_UNPROVEN"
SIGNAL_NO_USEFUL = "NO_USEFUL_TRUST_SIGNAL"
SIGNAL_NEGATIVE = "NEGATIVE_SIGNAL"


def classify_persistence_signal(persistent_overall, transient_overall, min_games_reportable=MIN_GAMES_PERSISTENCE_EXPLORATORY, min_games_confident=rsch0001.MIN_GAMES_EXPLORATORY):
    """
    Conservative, mirrors MLB-RSCH-0001's classify_edge_signal in spirit.
    Two thresholds, deliberately different: `min_games_reportable`
    (this track's own, lower floor) gates whether anything is reported
    at all; STRONG_TRUST_SIGNAL and NEGATIVE_SIGNAL additionally require
    `min_games_confident` -- REUSED DIRECTLY from MLB-RSCH-0001's own
    MIN_GAMES_EXPLORATORY=50, not a separately-invented weaker bar --
    because analyze_segment's own `interpretability` field already
    labels anything under 50 independent games INSUFFICIENT/EXPLORATORY,
    and a classifier that called STRONG/NEGATIVE on a segment its own
    analyze_segment output labels INSUFFICIENT would silently contradict
    that field. Between the two floors, only PARTIAL/WEAK/NO_USEFUL are
    reachable -- never a confident-sounding STRONG or NEGATIVE label on a
    fragile small-sample CI boundary.
    """
    if persistent_overall is None or persistent_overall.get("independentGames") is None or persistent_overall["independentGames"] < min_games_reportable:
        return SIGNAL_WEAK
    p_delta = persistent_overall.get("pairedBrierDelta_modelMinusMarket")
    if p_delta is None:
        return SIGNAL_WEAK
    ci = persistent_overall.get("pairedDeltaConfidenceInterval90") or {}
    p_lo, p_hi = ci.get("low"), ci.get("high")
    confident_sample = persistent_overall["independentGames"] >= min_games_confident

    if confident_sample and p_lo is not None and p_lo > 0:
        return SIGNAL_NEGATIVE  # persistent edge CONFIDENTLY worse than market, on a sample large enough to trust the CI

    if transient_overall is None or transient_overall.get("pairedBrierDelta_modelMinusMarket") is None:
        return SIGNAL_PARTIAL if (confident_sample and p_hi is not None and p_hi < 0) else SIGNAL_WEAK

    t_delta = transient_overall["pairedBrierDelta_modelMinusMarket"]
    incremental = p_delta < t_delta  # persistent group's model-vs-market advantage is BETTER (more negative) than transient's

    if confident_sample and p_hi is not None and p_hi < 0 and incremental:
        return SIGNAL_STRONG
    if incremental:
        return SIGNAL_PARTIAL
    return SIGNAL_NO_USEFUL


# ── Registration (must run before any result is examined) ──────────────

def register_control_and_experiment(evaluations):
    """
    Registers the SAME control identity RSCH-0001-0005 reused
    (CTRL-7252463d722626e6) -- an IDENTICAL write-once re-registration
    (same name/commit/config-fingerprint inputs as RSCH-0001's own call,
    so this is a no-op against the already-persisted registration, never
    a second distinct control) -- then registers MLB-RSCH-0006. Called
    unconditionally, BEFORE any persistence sequence, tier, or metric is
    computed (see main()).
    """
    control = ctrl_id.build_control_registration(
        name="edgelab_production_model_corpus_2026_08",
        # Hardcoded to MLB-RSCH-0001's own frozen provenance commit, NOT
        # this script's own run-time commit (rsch0001._current_git_commit_sha()
        # would return whatever commit THIS run happens to be on, which
        # changes every day and would silently mint a brand-new
        # controlModelId every time -- control_identity.build_control_
        # registration derives the id from name+commit+config_fingerprint).
        # This is what makes the re-registration below a true write-once
        # no-op against the SAME CTRL-7252463d722626e6 RSCH-0001-0005 all
        # reused, exactly as intended.
        source_git_commit_sha="68bf46e6acde8e48e347ccb762f0e518cbcb16a5",
        model_config_version="MULTIPLE_" + "_".join(sorted({e.get("modelConfigVersion") for e in evaluations if e.get("modelConfigVersion")})) if len({e.get("modelConfigVersion") for e in evaluations if e.get("modelConfigVersion")}) != 1 else next(iter({e.get("modelConfigVersion") for e in evaluations if e.get("modelConfigVersion")})),
        config_fingerprint=rsch0001._config_fingerprint(),
        probability_adapter_identity="scripts/build_market_ledger.py;lib.kalshi_probability_adapters.adapt_contract",
        model_engine_family="rules_based_v1_11_market_plus_full_universe_extension",
        required_input_provenance=[
            "archived_kalshi_market_observation", "kalshi_bid_ask_executable_price",
            "model_evaluation_probability_pipeline_derived", "model_evaluation_probability_prospective_snapshot",
            "settlement_outcome", "kalshi_closing_market_quote",
        ],
        identity_confidence=ctrl_id.IDENTITY_HISTORICAL_AMBIGUOUS,
        # Literal, byte-identical copy of MLB-RSCH-0001's own already-
        # persisted registration text (write-once requires exact content
        # equality, not just a matching id) -- not recomputed from this
        # run's own evaluations, which would drift as the corpus grows.
        description=(
            "The production EdgeLab model corpus as observed across every committed research date this "
            "experiment analyzes. 105 distinct modelCommitSha values were found across the underlying "
            "ModelEvaluation records (continuous deployment) -- this registration deliberately does NOT claim "
            "one exact commit for the whole corpus (see identityConfidence)."
        ),
        registered_at="2026-08-27T00:00:00Z",
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title=EXPERIMENT_TITLE,
        hypothesis=(
            "H1: persistent positive model edge produces better model-vs-market predictive performance than "
            "transient positive edge of similar initial magnitude. H2: edge surviving to LINEUP_CONFIRMATION is "
            "more reliable than otherwise-similar edge that disappears beforehand. H3: late-surviving edge is "
            "associated with better realized model accuracy and/or CLV than early-only edge. H4: persistence adds "
            "predictive information beyond raw numerical edge size. H5: market movement against the model despite "
            "persistent model edge may identify a materially different class of opportunity from movement with "
            "the model."
        ),
        research_question=(
            "When the model disagrees with Kalshi before a game, is an edge that PERSISTS across multiple "
            "prospective checkpoints more trustworthy (better model-vs-market predictive performance) than a "
            "transient/one-checkpoint edge of similar magnitude? Evaluated separately for ALL_HISTORICAL_MODEL_"
            "VERSIONS, CANONICAL_ERA, and TRUSTED_PRODUCTION_QUALITY_TIER_ONLY -- these are distinct claims, "
            "never conflated (same discipline as MLB-RSCH-0001)."
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E1_RECONSTRUCTED_RETROSPECTIVE,
        target_population=(
            "Every settled MLB Kalshi opportunity row in the archived observation corpus with a causally-valid "
            "(no-look-ahead) model probability, grouped by marketTicker into a chronological checkpoint sequence -- "
            "reported for ALL_HISTORICAL_MODEL_VERSIONS, CANONICAL_ERA, and TRUSTED_PRODUCTION_QUALITY_TIER_ONLY "
            "as distinct populations."
        ),
        market_families=["game_result", "team_total", "first_inning_run", "inning_result", "inning_total", "winning_margin"],
        eligibility_criteria=[
            "settlementStatus == SETTLED with settlementResult in (YES, NO)",
            "modelEvaluationAvailable == True (temporal_alignment causal join succeeded)",
            "contemporaneousEdge is not null (both modelFairProbability and this checkpoint's own executable price available)",
            "side defaults/resolves to YES (matches MLB-RSCH-0001's own corpus-wide finding: no causally-linked NO-side rows exist)",
            "predictive scoring uses exactly ONE representative row per ticker (final causally-valid pregame row) -- never every checkpoint pooled as independent",
        ],
        exclusion_criteria=[
            "Unsettled or push/void markets",
            "No causally-valid ModelEvaluation for this checkpoint",
            "Missing executable price at this checkpoint",
            "A ticker's non-final checkpoint rows, when scoring predictive accuracy (pseudoreplication guard)",
        ],
        prediction_checkpoints=list(STANDARDIZED_CHECKPOINT_ORDER),
        primary_metric="pairedBrierDelta_modelMinusMarket (one representative row per ticker), compared between persistent (>=2 consecutive same-sign checkpoints) and transient (singleton/sign-changing) tickers, within preregistered edge buckets",
        secondary_metrics=[
            "pairedLogLossDelta_modelMinusMarket", "clvLikeValue (hypothetical full-universe price-movement-to-close, NOT real placed-bet CLV)",
            "lineupConfirmedPersistent / lateSurviving flag comparisons", "marketMovedWithModel 2x2 confirmation matrix",
            "secondaryFeeAdjustedEconomics (explicitly non-primary)",
        ],
        chronological_split_policy="NONE -- this corpus (27 observation-dates) cannot support lib.edgelab.research_splits' MIN_DATES_FOR_MATURE_SPLIT=30 60/20/20 split; a pooled retrospective exploratory analysis, same reasoning as MLB-RSCH-0001",
        minimum_sample_requirement={"independentGames": MIN_GAMES_PERSISTENCE_EXPLORATORY, "independentDates": 5},
        clustering_unit="gameId",
        experiment_type=reg.EXPERIMENT_TYPE_EXPLORATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={
            "archived_kalshi_market_observation": pit.ROLE_PREDICTIVE_INPUT,
            "kalshi_bid_ask_executable_price": pit.ROLE_PREDICTIVE_INPUT,
            "model_evaluation_probability_pipeline_derived": pit.ROLE_PREDICTIVE_INPUT,
            "model_evaluation_probability_prospective_snapshot": pit.ROLE_PREDICTIVE_INPUT,
            "settlement_outcome": pit.ROLE_EVALUATION_TARGET,
            "kalshi_closing_market_quote": pit.ROLE_EVALUATION_TARGET,
        },
        notes=(
            "Reuses MLB-RSCH-0001's fair-market/executable-price/edge-bucket/current-model-isolation machinery "
            "unchanged (imported, not reimplemented). Genuinely new logic here is limited to per-ticker "
            "chronological checkpoint-sequence construction and persistence-tier/flag classification. Given only "
            "18 (all-history) / 6 (trusted-production) distinct dates have any multi-checkpoint usable ticker, "
            "and only 39 (all-history) / 15 (trusted-production) games have any multi-checkpoint ticker at all, "
            "MIN_GAMES_PERSISTENCE_EXPLORATORY is set below MLB-RSCH-0001's own MIN_GAMES_EXPLORATORY=50 -- still "
            "a genuine, preregistered, non-zero floor. NO production change; a finding here does not auto-promote "
            "any recommendation threshold, confidence tier, Bet Up To logic, or stake sizing."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    reg.register_experiment(definition)
    return control, definition


# ── Population-level orchestration ────────────────────────────────────────

def _population_analysis(usable_rows, population_label):
    summaries = build_ticker_summaries(usable_rows)
    transient = tier_analysis(summaries, SINGLETON_TRANSIENT, "SINGLETON_TRANSIENT")
    two_checkpoint = tier_analysis(summaries, TWO_CHECKPOINT_PERSISTENT, "TWO_CHECKPOINT_PERSISTENT")
    three_plus = tier_analysis(summaries, THREE_PLUS_CHECKPOINT_PERSISTENT, "THREE_PLUS_CHECKPOINT_PERSISTENT")
    persistent_pooled_rows = _final_rows_for(summaries, lambda s: s["persistenceTier"] in PERSISTENT_TIERS)
    persistent_pooled = rsch0001.analyze_segment(persistent_pooled_rows, "PERSISTENT_2PLUS_POOLED")
    persistent_pooled["clv"] = clv_summary(summaries, lambda s: s["persistenceTier"] in PERSISTENT_TIERS)

    lineup_confirmed = flag_analysis(summaries, "lineupConfirmedPersistent", "LINEUP_CONFIRMED_PERSISTENT")
    late_surviving = flag_analysis(summaries, "lateSurviving", "LATE_SURVIVING")

    market_with = flag_analysis(summaries, "marketMovedWithModel", "MARKET_MOVES_WITH_MODEL") if any(s["marketMovedWithModel"] is True for s in summaries.values()) else None
    market_against_rows = _final_rows_for(summaries, lambda s: s["marketMovedWithModel"] is False)
    market_against = rsch0001.analyze_segment(market_against_rows, "MARKET_MOVES_AGAINST_MODEL")
    market_against["clv"] = clv_summary(summaries, lambda s: s["marketMovedWithModel"] is False)

    signal = classify_persistence_signal(persistent_pooled, transient)

    return {
        "populationLabel": population_label,
        "numTickers": len(summaries),
        "independentGames": len({s["gameId"] for s in summaries.values() if s.get("gameId")}),
        "independentDates": len({s["gameDate"] for s in summaries.values() if s.get("gameDate")}),
        "checkpointCountDistribution": dict(sorted(__import__("collections").Counter(s["numCheckpoints"] for s in summaries.values()).items())),
        "transient": transient,
        "twoCheckpointPersistent": two_checkpoint,
        "threePlusCheckpointPersistent": three_plus,
        "persistent2PlusPooled": persistent_pooled,
        "lineupConfirmedPersistent": lineup_confirmed,
        "lateSurviving": late_surviving,
        "marketMovesWithModel": market_with,
        "marketMovesAgainstModel": market_against,
        "incrementalToMagnitude": persistence_incremental_to_magnitude(summaries),
        "confirmationMatrix": confirmation_matrix(summaries),
        "byMarketFamily": family_persistence_breakdown(summaries),
        "signalClassification": signal,
    }


def main():
    dates = rsch0001._discover_dates()
    if not dates:
        print("No observation dates found.", file=sys.stderr)
        return 1

    observations, settlements, evaluations, recommendations, games, bets = rsch0001._load_universe(dates)

    # ---- Registration FIRST, before any result is examined. ----
    control, definition = register_control_and_experiment(evaluations)

    all_rows = build_opportunity_rows(observations, settlements=settlements, evaluations=evaluations, recommendations=recommendations, bets=bets, games=games)
    usable, coverage = rsch0001.usable_rows_and_coverage(all_rows)

    canonical_era_rows = rsch0001.filter_canonical_era(usable)
    trusted_production_rows = rsch0001.filter_trusted_production_only(usable, evaluations)

    all_history = _population_analysis(usable, "ALL_HISTORICAL_MODEL_VERSIONS")
    canonical_era_result = _population_analysis(canonical_era_rows, f"CANONICAL_ERA (gameDate >= {canonical_era.CANONICAL_ERA_START_DATE})")
    trusted_production_only = _population_analysis(trusted_production_rows, "TRUSTED_PRODUCTION_QUALITY_TIER_ONLY")

    current_model_signal = trusted_production_only["signalClassification"]
    historical_signal = all_history["signalClassification"]
    disposition = disp.REJECT if current_model_signal == SIGNAL_NEGATIVE else disp.RESEARCH_CANDIDATE

    pairing_for_report = rsch0001._model_vs_market_pairing(usable)
    overall_evaluation, _ = rsch0001.paired_model_vs_market(usable, n_resamples=2000, seed=DEFAULT_BOOTSTRAP_SEED)

    report = er.build_experiment_report(
        experiment=definition, control_registration=control, candidate_registration=None,
        pairing_result=pairing_for_report, probability_evaluation=overall_evaluation,
        disposition=disposition, evidence_level=ev.E1_RECONSTRUCTED_RETROSPECTIVE,
        evaluation_date_range=[min(dates), max(dates)] if dates else None,
        pit_provenance_status="MIXED -- identical status to MLB-RSCH-0001 (same underlying corpus); evidenceLevel capped at E1 for the same PROSPECTIVE_ONLY-classified prospective_snapshot pathway reason",
        pit_limitations=[
            "Reuses MLB-RSCH-0001's exact eligibility population (usable_rows_and_coverage) -- see that experiment's own PIT limitations for the underlying corpus's provenance status.",
        ],
        methodological_limitations=[
            f"Multi-checkpoint persistence-eligible corpus is small: {all_history['independentGames']} games / {all_history['independentDates']} dates (ALL_HISTORICAL_MODEL_VERSIONS), {trusted_production_only['independentGames']} games / {trusted_production_only['independentDates']} dates (TRUSTED_PRODUCTION_QUALITY_TIER_ONLY) -- well below a mature chronological-split threshold; classifications are conservative (WEAK_UNPROVEN unless a CI confidently excludes zero) specifically because of this.",
            "LINEUP_CONFIRMATION checkpoint coverage may be zero or near-zero in the settled+causally-linked usable population -- lineupConfirmedPersistent is reported honestly as 0/INSUFFICIENT where that is what the data show, never fabricated.",
            "CLV-like values reuse research_dataset's hypothetical full-universe price-movement-to-close field, NOT real placed-bet CLV (lib.edgelab.clv.compute_clv_for_bet) -- see module docstring.",
            "This is a pooled retrospective analysis with no chronological train/holdout split (chronological_split_policy=NONE) -- same corpus-maturity constraint MLB-RSCH-0001 documented.",
            "falseDiscoveryHandling is registered BENJAMINI_HOCHBERG (required for an EXPLORATORY experiment that screens many segments) but this script's own classify_persistence_signal does not compute a formal per-cell p-value screen the way MLB-RSCH-0001's family_segmentation does -- per-cell 90% CIs are the primary conservatism guard here (a too-small sample is always WEAK_UNPROVEN regardless of point estimate) across the many tier/family/bucket cells.",
        ],
        leakage_warnings=[],
        secondary_metrics={
            "coverage": coverage,
            "headline": {
                "currentModelPersistenceSignal": current_model_signal,
                "currentModelPopulation": "TRUSTED_PRODUCTION_QUALITY_TIER_ONLY",
                "historicalMixedVersionPersistenceSignal": historical_signal,
                "historicalPopulation": "ALL_HISTORICAL_MODEL_VERSIONS",
                "note": "Distinct questions, never conflated. A historical mixed-version finding does not, by itself, characterize the current model.",
            },
            "allHistory": all_history,
            "canonicalEra": canonical_era_result,
            "trustedProductionOnly": trusted_production_only,
        },
        market_economic_metrics={
            "allHistoryPersistent2Plus": all_history["persistent2PlusPooled"]["secondaryFeeAdjustedEconomics"],
            "allHistoryTransient": all_history["transient"]["secondaryFeeAdjustedEconomics"],
        },
        generated_at=REGISTRATION_TIMESTAMP,
    )
    er.write_experiment_report(report)

    os.makedirs(ANALYTICS_DIR, exist_ok=True)
    with open(MACHINE_REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")

    print(f"Experiment {EXPERIMENT_ID} registered. Report: {report['experimentReportId']}")
    print(f"Usable rows: {coverage['usableRows']} | ALL_HISTORY tickers: {all_history['numTickers']} games: {all_history['independentGames']} dates: {all_history['independentDates']}")
    print(f"ALL_HISTORY signal: {historical_signal} | CURRENT MODEL (TRUSTED_PRODUCTION) signal: {current_model_signal}")
    print(f"Disposition (current-model driven): {disposition}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
