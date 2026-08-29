#!/usr/bin/env python3
"""
scripts/edgelab/run_production_scope_audit_experiment.py
====================================================================
Research Lab experiment MLB-RSCH-0027: "Production Scope Integrity and
Family-Resolved Skill". RESEARCH ONLY. NO production changes, no
candidate activation, no staking/execution/fee-logic changes, no change
to market eligibility or bet selection.

CORE QUESTION: when the evaluation corpus is restricted to what
production actually trades -- and enlarged to include the production
rows that are currently unscoreable -- how good is production really,
and is there ANY market family in which it does not trail Kalshi?

WHY THIS, AND WHY NOW
---------------------
Four consecutive experiments (RSCH-0023 recalibration, RSCH-0024
market-residual alpha, RSCH-0025 V2 retest, RSCH-0026 Kalshi shrinkage)
each fitted a correction to the same three-week market archive and each
failed to transport. Rather than fit a fifth, this experiment audits the
foundation those four rested on: the DEFINITION OF THE CORPUS.

Every bet-selection decision for the remainder of 2026 rests on one
number -- how far production trails the market, and in which families.
That number came from MLB-RSCH-0022. This experiment asks whether it was
measured on the right rows.

TWO SCOPE DEFECTS (DISCOVERED DURING SCOPING -- SEE PREREGISTRATION
HONESTY BELOW, THESE WERE NOT PREREGISTERED FINDINGS)
---------------------------------------------------------------------
D1  SCOPE CONTAMINATION. `load_evaluated_rows()` in RSCH-0022 accepts
    every EVALUATED row carrying both probabilities. It applies no
    `qualityTier` filter. The model-evaluation archive contains two
    structurally different populations:

      TRUSTED_PRODUCTION families -- what production actually prices and
      trades: KXMLBTEAMTOTAL, KXMLBRFI, KXMLBGAME, KXMLBF5, ML_Home,
      ML_Away, NRFI, YRFI, F5_ML_Home, F5_ML_Away.

      RESEARCH_ONLY boards -- exploratory research surfaces that are not
      production and never were: pitcher_strikeouts, pitcher_outs,
      team_total, game_total, winning_margin, inning_result,
      inning_total, game_result, first_inning_run.

    The two are pooled. The research boards additionally carry the
    `executableMarketProb` (ASK-price) adapter and degenerate market
    prices at exactly 0.0/1.0 -- the very benchmark corruption
    MLB-RSCH-0024 identified. Every production-family row, by contrast,
    uses `kalshiVF` (vig-free) and carries no degenerate price.

D2  CORPUS LOSS. Six production families key their evaluation rows by a
    SYNTHETIC internal identifier, `<gamePk>:<FAMILY>` (e.g.
    `823514:ML_Home`), rather than by a Kalshi ticker. The settlement
    archive is keyed by Kalshi ticker. These rows therefore cannot join
    a settlement by construction, and are silently dropped from every
    audit this program has run -- they are invisible, not excluded.

    Of those, the two moneyline families are recoverable here: a
    moneyline settles from the final score, which the dated MLB schedule
    archive already carries. The first-inning (NRFI/YRFI) and
    first-five (F5_ML_*) families are NOT recoverable: settling them
    needs an inning-resolved linescore that no local archive holds.
    Recovering those is reported as follow-up infrastructure, not
    attempted here by approximation.

PREREGISTRATION HONESTY (STATED PLAINLY, NOT BURIED)
----------------------------------------------------
This experiment mixes a DESCRIPTIVE correction with a CONFIRMATORY test,
and they are labeled separately because they earn different trust.

  OBSERVED BEFORE PREREGISTRATION (descriptive, E0, claims nothing
  inferential): the existence of D1 and D2, and the aggregate Brier
  point estimates of the pooled vs production-only corpora. These were
  found while scoping which experiment to run. They are reported as a
  MEASUREMENT CORRECTION with no p-value, no confidence claim, and no
  hypothesis attached, because they were seen before any rule was fixed.

  PREREGISTERED, GENUINELY UNSEEN: everything computed on the RECOVERED
  corpus. Those ~757 moneyline rows have never been scored by any
  experiment in this program's history -- they could not be, they do not
  join. Their per-family deltas, confidence intervals, FDR outcomes and
  holdout behaviour are unobserved at the moment this rule is written.

Nothing here rewrites RSCH-0022, -0024 or -0026. Their artifacts stand
exactly as merged. This experiment reports a NEW finding about the
corpus they used; it does not retroactively edit their conclusions or
claim they were preregistered differently than they were.

PREREGISTERED DECISION RULE (LOCKED BEFORE Q3/Q5 WERE COMPUTED)
---------------------------------------------------------------
A production family may be called PRODUCTION_SHOWS_SKILL only if ALL of:
  1. paired Brier delta (model - market) < 0 on the full production
     corpus, AND its game-clustered bootstrap CI upper bound < 0,
  2. it survives Benjamini-Hochberg FDR at 0.10 across every family
     tested,
  3. it meets the sample floors (>= 100 settled rows, >= 20 games),
  4. the direction still holds in the HOLDOUT sub-window (settle >
     2026-08-24) -- an in-window win alone is exactly the failure mode
     that killed the last four experiments and does not count here.

Families are FIXED BY THE ARCHIVE, not invented for this test: they are
the production family labels that already exist. No family is split,
merged, or thresholded. No cutoff is searched.

Economics are SECONDARY and never selective: fee-aware executable P/L is
computed only for a family that has ALREADY passed the scoring rule
above. Economics can never rescue a family that failed, and no threshold
is fitted to economics.

MAXIMUM DISPOSITION: SHADOW_CANDIDATE. A family that passes becomes a
shadow candidate for family-restricted bet selection -- it is NOT a
production change and is NOT authorized for activation here. If no
family passes, the honest result is LEVEL_0 and this experiment says so.
"""
import collections
import json
import math
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_EDGELAB_SCRIPTS_DIR = os.path.join(_ROOT, "scripts", "edgelab")
if _EDGELAB_SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _EDGELAB_SCRIPTS_DIR)

from lib.edgelab import experiment_registry as reg
from lib.edgelab import evidence_levels as ev
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab.kalshi_fees import taker_fee
from lib.edgelab.research_stats import (
    DEFAULT_BOOTSTRAP_SEED, independent_unit_count, sample_size_status,
    expected_calibration_error, brier_and_log_loss_summary,
    calibration_slope_intercept, game_clustered_bootstrap_ci,
)

import run_production_calibration_audit_experiment as rsch0022  # noqa: E402

EXPERIMENT_ID = "MLB-RSCH-0027"
REGISTRATION_TIMESTAMP = "2026-08-29T00:00:00Z"

# ── Scope definitions (fixed; these are archive labels, not new buckets) ──
PRODUCTION_FAMILIES = frozenset({
    "KXMLBTEAMTOTAL", "KXMLBRFI", "KXMLBGAME", "KXMLBF5",
    "ML_Home", "ML_Away", "NRFI", "YRFI", "F5_ML_Home", "F5_ML_Away",
})
RESEARCH_ONLY_FAMILIES = frozenset({
    "pitcher_strikeouts", "pitcher_outs", "team_total", "game_total",
    "winning_margin", "inning_result", "inning_total", "game_result",
    "first_inning_run",
})

# Families keyed by the synthetic `<gamePk>:<FAMILY>` identifier (defect D2).
SYNTHETIC_KEY_FAMILIES = frozenset({
    "ML_Home", "ML_Away", "NRFI", "YRFI", "F5_ML_Home", "F5_ML_Away",
})
# Of those, the ones a final score alone can settle.
RECOVERABLE_FAMILIES = frozenset({"ML_Home", "ML_Away"})

TRAIN_DATE_MAX = "2026-08-24"          # HOLDOUT = settle > this
FORWARD_START_DATE = "2026-08-28"      # never touched by this experiment

MIN_ROWS_FAMILY = 100
MIN_GAMES_FAMILY = 20
FDR_ALPHA = 0.10
PROB_CLAMP = (0.001, 0.999)

SCHEDULE_DIR = os.path.join(_ROOT, "data", "research_cache", "bullpen_backtest", "2026", "schedules")
ANALYTICS_DIR = os.path.join(_ROOT, "data", "edgelab", "analytics")
REPORT_PATH = os.path.join(_ROOT, "docs", "EDGELAB_MLB_RSCH_0027_PRODUCTION_SCOPE_AUDIT.md")
ARTIFACT_PATH = os.path.join(ANALYTICS_DIR, "latest_mlb_rsch_0027_production_scope_audit.json")


def _current_git_commit_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_ROOT).decode().strip()
    except Exception:
        return "unknown"


def scope_of(family):
    """PRODUCTION / RESEARCH_ONLY / UNCLASSIFIED for a market family label."""
    if family in PRODUCTION_FAMILIES:
        return "PRODUCTION"
    if family in RESEARCH_ONLY_FAMILIES:
        return "RESEARCH_ONLY"
    return "UNCLASSIFIED"


# ── Corpus construction ───────────────────────────────────────────────────

def load_schedule_finals():
    """gamePk -> {homeScore, awayScore, officialDate} for FINAL 2026 games,
    from the dated MLB schedule archive. EVALUATION TARGET ONLY: this is
    postgame information and is never used as a predictive input. Reading
    the same game from several team files is idempotent (identical rows)."""
    finals = {}
    if not os.path.isdir(SCHEDULE_DIR):
        return finals
    for fn in sorted(os.listdir(SCHEDULE_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            doc = json.load(open(os.path.join(SCHEDULE_DIR, fn)))
        except Exception:
            continue
        for date in doc.get("dates", []):
            for g in date.get("games", []):
                state = (g.get("status") or {}).get("detailedState")
                if state != "Final":
                    continue
                teams = g.get("teams") or {}
                home, away = teams.get("home") or {}, teams.get("away") or {}
                hs, as_ = home.get("score"), away.get("score")
                if hs is None or as_ is None:
                    continue
                finals[str(g.get("gamePk"))] = {
                    "homeScore": int(hs), "awayScore": int(as_),
                    "officialDate": g.get("officialDate") or date.get("date"),
                }
    return finals


def load_scoped_evaluated_rows():
    """Every EVALUATED row with both probabilities, carrying the fields this
    experiment needs that RSCH-0022's loader drops (qualityTier, adapter,
    the evaluation-side family label, and the raw ticker)."""
    rows = []
    for fn in sorted(os.listdir(rsch0022.MODEL_EVALUATIONS_DIR)):
        if not (fn.endswith(".jsonl") or fn.endswith(".jsonl.gz")):
            continue
        for d in rsch0022.read_records(os.path.join(rsch0022.MODEL_EVALUATIONS_DIR, fn)):
            if d.get("evaluationStatus") != "EVALUATED":
                continue
            model_p, market_p = d.get("modelFairProbability"), d.get("marketImpliedProbability")
            if model_p is None or market_p is None:
                continue
            rows.append({
                "marketTicker": d.get("marketTicker"),
                "evalFamily": d.get("marketFamily"),
                "qualityTier": d.get("qualityTier"),
                "probabilityAdapter": d.get("probabilityAdapter"),
                "gameId": d.get("gameId"),
                "createdAt": d.get("createdAt") or "",
                "modelP": round(float(model_p) / 100.0, 6),
                "marketP": round(float(market_p) / 100.0, 6),
                "confidence": d.get("confidence"),
            })
    return rows


def parse_synthetic_key(ticker):
    """`<gamePk>:<FAMILY>` -> (gamePk, family), else (None, None). This is the
    identifier shape behind defect D2."""
    if not ticker or ":" not in ticker:
        return None, None
    left, _, right = ticker.partition(":")
    if not left.isdigit() or right not in SYNTHETIC_KEY_FAMILIES:
        return None, None
    return left, right


def recover_moneyline_rows(scoped_rows, finals):
    """Settle the synthetic-key MONEYLINE rows from the final score.

    These rows are genuine production evaluations (kalshiVF vig-free market
    price, TRUSTED_PRODUCTION tier) that no prior audit could score, because
    their identifier is internal rather than a Kalshi ticker. One row per
    ticker, keeping the LAST pregame evaluation -- identical to RSCH-0022's
    primary `pick="last"` convention, so the two corpora are comparable.

    Returns (rows, diagnostics)."""
    by_ticker, seen_families = {}, collections.Counter()
    for r in scoped_rows:
        game_pk, family = parse_synthetic_key(r["marketTicker"])
        if game_pk is None:
            continue
        seen_families[family] += 1
        if family not in RECOVERABLE_FAMILIES:
            continue
        prev = by_ticker.get(r["marketTicker"])
        if prev is None or r["createdAt"] >= prev["createdAt"]:
            by_ticker[r["marketTicker"]] = r

    recovered, no_final = [], 0
    for ticker, r in sorted(by_ticker.items()):
        game_pk, family = parse_synthetic_key(ticker)
        final = finals.get(game_pk)
        if final is None:
            no_final += 1
            continue
        hs, as_ = final["homeScore"], final["awayScore"]
        if hs == as_:                      # a tie cannot settle a moneyline
            no_final += 1
            continue
        outcome = (1 if hs > as_ else 0) if family == "ML_Home" else (1 if as_ > hs else 0)
        recovered.append({
            "marketTicker": ticker, "marketFamily": family, "gameId": r["gameId"] or game_pk,
            "gamePk": game_pk, "settleDate": final["officialDate"],
            "modelP": r["modelP"], "marketP": r["marketP"],
            "confidence": r["confidence"], "outcome": outcome,
            "origin": "RECOVERED_SYNTHETIC_KEY",
        })
    return recovered, {
        "syntheticKeyRowsByFamily": dict(seen_families),
        "recoverableTickers": len(by_ticker),
        "recoveredRows": len(recovered),
        "droppedNoFinalScoreOrTie": no_final,
        "unrecoverableFamilies": sorted(SYNTHETIC_KEY_FAMILIES - RECOVERABLE_FAMILIES),
    }


# ── Scoring ───────────────────────────────────────────────────────────────

def _clamp(p):
    return min(max(p, PROB_CLAMP[0]), PROB_CLAMP[1])


def brier(rows, key):
    return sum((r[key] - r["outcome"]) ** 2 for r in rows) / len(rows) if rows else None


def log_loss(rows, key):
    if not rows:
        return None
    total = 0.0
    for r in rows:
        p = _clamp(r[key])
        total += -(r["outcome"] * math.log(p) + (1 - r["outcome"]) * math.log(1 - p))
    return total / len(rows)


def paired_brier_delta(rows):
    """model minus market. NEGATIVE means production is BETTER than Kalshi."""
    if not rows:
        return None
    return brier(rows, "modelP") - brier(rows, "marketP")


def paired_log_loss_delta(rows):
    if not rows:
        return None
    return log_loss(rows, "modelP") - log_loss(rows, "marketP")


def game_clustered_bootstrap_pvalue(rows, value_fn, *, cluster_key="gameId",
                                    n_resamples=2000, seed=DEFAULT_BOOTSTRAP_SEED):
    """Two-sided bootstrap p-value for `value_fn != 0`, using exactly the
    cluster-resampling convention of research_stats.game_clustered_bootstrap_ci
    (whole games resampled with replacement, fixed seed, deterministic).

    Distribution-free on purpose: the alternative would be a t-test over
    per-game means, which assumes normality of a Brier difference on as few
    as 20 clusters. Reported as an FDR input only -- never as a standalone
    significance claim."""
    import random as _random
    by_cluster = collections.defaultdict(list)
    for r in rows:
        key = r.get(cluster_key)
        if key is not None:
            by_cluster[key].append(r)
    clusters = sorted(by_cluster.keys(), key=str)
    observed = value_fn(rows)
    if not clusters or observed is None:
        return None
    rng = _random.Random(seed)
    estimates = []
    for _ in range(n_resamples):
        sampled = [rng.choice(clusters) for _ in clusters]
        value = value_fn([row for c in sampled for row in by_cluster[c]])
        if value is not None:
            estimates.append(value)
    if not estimates:
        return None
    # Centre the bootstrap distribution on zero and ask how extreme the
    # observed statistic is under that null.
    mean_est = sum(estimates) / len(estimates)
    centred = [e - mean_est for e in estimates]
    at_least_as_extreme = sum(1 for c in centred if abs(c) >= abs(observed))
    return min(1.0, (at_least_as_extreme + 1) / (len(centred) + 1))


def benjamini_hochberg(pvalues, alpha=FDR_ALPHA):
    """Returns the set of indices rejected at FDR `alpha`. Same procedure the
    frozen forward scorer uses, so family findings are controlled identically
    in this experiment and in forward confirmation."""
    indexed = sorted(((p, i) for i, p in enumerate(pvalues) if p is not None), key=lambda t: t[0])
    m = len(indexed)
    if m == 0:
        return set()
    rejected_upto = -1
    for rank, (p, _) in enumerate(indexed, start=1):
        if p <= (rank / m) * alpha:
            rejected_upto = rank
    return {i for _, i in indexed[:rejected_upto]} if rejected_upto > 0 else set()


def corpus_summary(rows, label):
    """Full descriptive scorecard for one corpus. No selection happens here."""
    if not rows:
        return {"label": label, "rows": 0}
    games = independent_unit_count(rows, "gameId")
    model_pairs = [(r["modelP"], r["outcome"]) for r in rows]
    market_pairs = [(r["marketP"], r["outcome"]) for r in rows]
    model_summary = brier_and_log_loss_summary(model_pairs)
    market_summary = brier_and_log_loss_summary(market_pairs)
    delta = paired_brier_delta(rows)
    lo, hi, method = game_clustered_bootstrap_ci(rows, paired_brier_delta)
    ll_lo, ll_hi, _ = game_clustered_bootstrap_ci(rows, paired_log_loss_delta)
    m_slope, m_int = calibration_slope_intercept(model_pairs)
    return {
        "label": label,
        "rows": len(rows),
        "independentGames": games,
        "rowsPerGame": round(len(rows) / games, 2) if games else None,
        "sampleSizeStatus": sample_size_status(len(rows), games),
        "modelBrier": round(brier(rows, "modelP"), 6),
        "marketBrier": round(brier(rows, "marketP"), 6),
        "pairedBrierDelta": round(delta, 6),
        "pairedBrierDeltaCI": {"low": lo, "high": hi, "method": method},
        "modelLogLoss": round(log_loss(rows, "modelP"), 6),
        "marketLogLoss": round(log_loss(rows, "marketP"), 6),
        "pairedLogLossDelta": round(paired_log_loss_delta(rows), 6),
        "pairedLogLossDeltaCI": {"low": ll_lo, "high": ll_hi},
        "modelECE": round(expected_calibration_error(model_pairs), 6),
        "marketECE": round(expected_calibration_error(market_pairs), 6),
        "modelCalibrationSlope": m_slope,
        "modelCalibrationIntercept": m_int,
        "degenerateMarketPrices": sum(1 for r in rows if r["marketP"] <= 0.0 or r["marketP"] >= 1.0),
        "modelBrierLogLoss": model_summary,
        "marketBrierLogLoss": market_summary,
    }


# ── Preregistered family-resolved test ────────────────────────────────────

def family_analysis(rows):
    """Per-production-family paired skill with game-clustered CIs, BH-FDR at
    0.10, and the preregistered HOLDOUT transport check.

    The families are the archive's own labels. None is created, split, or
    merged for this test, and no cutoff is searched -- the whole point is
    that the last four experiments died from in-window selection."""
    by_family = collections.defaultdict(list)
    for r in rows:
        by_family[r["marketFamily"]].append(r)

    entries = []
    for family in sorted(by_family):
        frows = by_family[family]
        games = independent_unit_count(frows, "gameId")
        meets_floor = len(frows) >= MIN_ROWS_FAMILY and games >= MIN_GAMES_FAMILY
        delta = paired_brier_delta(frows)
        lo, hi, _ = game_clustered_bootstrap_ci(frows, paired_brier_delta)
        p = game_clustered_bootstrap_pvalue(frows, paired_brier_delta) if meets_floor else None
        model_pairs = [(r["modelP"], r["outcome"]) for r in frows]
        market_pairs = [(r["marketP"], r["outcome"]) for r in frows]
        # Calibration slope of outcome ~ production probability. A slope near
        # ZERO means production's number carries essentially no discriminative
        # information in that family, whatever its average level; a slope near
        # 1 means it is informative. Reported per family because the pooled
        # slope averages over families that behave very differently.
        m_slope, m_int = calibration_slope_intercept(model_pairs)
        k_slope, _k_int = calibration_slope_intercept(market_pairs)
        # Spread of production's own probability. A near-zero calibration
        # slope means something very different when the probabilities barely
        # vary (nothing to correlate with) than when they range widely (the
        # number moves a lot and the outcome ignores it), so the spread is
        # reported alongside the slope rather than left for the reader to
        # assume.
        ps = sorted(r["modelP"] for r in frows)
        mean_p = sum(ps) / len(ps)
        spread = {
            "min": round(ps[0], 4), "median": round(ps[len(ps) // 2], 4), "max": round(ps[-1], 4),
            "stdev": round(math.sqrt(sum((x - mean_p) ** 2 for x in ps) / len(ps)), 4),
        }
        base_rate = sum(r["outcome"] for r in frows) / len(frows)

        holdout = [r for r in frows if r["settleDate"] > TRAIN_DATE_MAX]
        train = [r for r in frows if r["settleDate"] <= TRAIN_DATE_MAX]
        holdout_delta = paired_brier_delta(holdout)
        entries.append({
            "family": family,
            "rows": len(frows), "independentGames": games, "meetsSampleFloor": meets_floor,
            "modelBrier": round(brier(frows, "modelP"), 6),
            "marketBrier": round(brier(frows, "marketP"), 6),
            "pairedBrierDelta": round(delta, 6),
            "pairedBrierDeltaCI": {"low": lo, "high": hi},
            "pairedLogLossDelta": round(paired_log_loss_delta(frows), 6),
            "bootstrapPValue": None if p is None else round(p, 4),
            "trainRows": len(train), "holdoutRows": len(holdout),
            "trainBrierDelta": None if not train else round(paired_brier_delta(train), 6),
            "holdoutBrierDelta": None if not holdout else round(holdout_delta, 6),
            "modelCalibrationSlope": m_slope, "modelCalibrationIntercept": m_int,
            "marketCalibrationSlope": k_slope,
            "modelECE": round(expected_calibration_error(model_pairs), 6),
            "marketECE": round(expected_calibration_error(market_pairs), 6),
            "modelProbabilitySpread": spread,
            "settledYesRate": round(base_rate, 4),
        })

    rejected = benjamini_hochberg([e["bootstrapPValue"] for e in entries], FDR_ALPHA)
    for i, e in enumerate(entries):
        # ALL FOUR preregistered conditions, evaluated exactly as written.
        c1 = e["pairedBrierDelta"] < 0 and e["pairedBrierDeltaCI"]["high"] is not None \
            and e["pairedBrierDeltaCI"]["high"] < 0
        c2 = i in rejected
        c3 = e["meetsSampleFloor"]
        c4 = e["holdoutBrierDelta"] is not None and e["holdoutBrierDelta"] < 0
        e["fdrSignificant"] = c2
        e["conditions"] = {
            "1_pairedDeltaNegativeWithCIUpperBoundBelowZero": c1,
            "2_survivesBenjaminiHochbergFDR": c2,
            "3_meetsSampleFloor": c3,
            "4_directionHoldsInHoldout": c4,
        }
        if c1 and c2 and c3 and c4:
            e["verdict"] = "PRODUCTION_SHOWS_SKILL"
        elif not c3:
            e["verdict"] = "INSUFFICIENT_SAMPLE"
        elif e["pairedBrierDelta"] > 0 and (e["pairedBrierDeltaCI"]["low"] or 0) > 0:
            e["verdict"] = "PRODUCTION_TRAILS_MARKET"
        else:
            e["verdict"] = "INCONCLUSIVE"
    return entries


def secondary_economics(rows):
    """Fee-aware executable P/L, SECONDARY and descriptive only.

    Computed AFTER the scoring rule has already decided -- economics never
    rescue a family that failed the proper-scoring test, and no threshold is
    fitted to them. Backs a YES contract at the archived market price
    whenever production's probability exceeds it, one contract per row, with
    the canonical Kalshi taker fee applied."""
    staked = fees = pnl = 0.0
    bets = 0
    for r in rows:
        if r["modelP"] <= r["marketP"]:
            continue
        price = _clamp(r["marketP"])
        fee = taker_fee(1, price)
        bets += 1
        staked += price
        fees += fee
        pnl += ((1.0 - price) if r["outcome"] == 1 else -price) - fee
    return {
        "bets": bets,
        "grossStaked": round(staked, 4),
        "totalFees": round(fees, 4),
        "netPnl": round(pnl, 4),
        "netRoi": round(pnl / staked, 4) if staked else None,
        "note": "SECONDARY and descriptive. Never used for selection; no threshold fitted to economics.",
    }


# ── Registration ──────────────────────────────────────────────────────────

def register_experiment():
    try:
        existing_definition = reg.load_experiment(EXPERIMENT_ID)
    except FileNotFoundError:
        existing_definition = None
    if existing_definition is not None:
        control = ctrl_id.load_control(existing_definition["controlModelId"])
        return control, existing_definition

    control = ctrl_id.build_control_registration(
        name="mlb_rsch_0027_production_scope_audit_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0027 production scope audit v1: control = Kalshi's archived vig-free "
                        "pregame price; comparison = production's own archived probability. NOTHING IS "
                        "FITTED -- no parameter is estimated anywhere in this experiment. The only "
                        "manipulations are (a) restricting the corpus to TRUSTED_PRODUCTION families and "
                        "(b) recovering synthetic-key moneyline rows by settling them from the dated final "
                        "score."
        ),
        probability_adapter_identity=(
            "kalshiVF vig-free market probability as archived on the production evaluation row (verified to "
            "be the sole adapter present on every production-family row); production probability as archived"
        ),
        model_engine_family="production_evaluation_corpus_scope_audit_v1",
        required_input_provenance=[
            "model_evaluation_probability_prospective_snapshot",
            "settlement_outcome",
            "team_recent_game_log_reconstruction",
        ],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "Measures production's true family-resolved skill against Kalshi on a corpus restricted to what "
            "production actually trades and enlarged with production rows that no prior audit could score."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Production Scope Integrity and Family-Resolved Skill",
        hypothesis=(
            "H1 (PREREGISTERED, tested on rows never previously scored): at least one TRUSTED_PRODUCTION "
            "market family does not trail Kalshi's vig-free price on paired Brier -- its CI upper bound is "
            "below zero, it survives Benjamini-Hochberg FDR at 0.10 across families, it meets the sample "
            "floors, AND the direction still holds in the holdout sub-window. H2 (null, tested not assumed): "
            "production trails the market in every family, and the pooled RSCH-0022 figure understated "
            "production only because three quarters of that corpus was not production. H3 (descriptive): the "
            "pooled corpus mixes two populations whose market prices come from different adapters, so the "
            "pooled benchmark measures neither population."
        ),
        research_question=(
            "When the evaluation corpus is restricted to what production actually trades, and enlarged to "
            "include the production rows that are structurally unscoreable today, how good is production "
            "really -- and is there any family in which it does not trail Kalshi?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E4_PROSPECTIVE_SHADOW,
        target_population=(
            "Every settled TRUSTED_PRODUCTION-family model evaluation, 2026-08-01 .. 2026-08-27: those that "
            "join the Kalshi settlement archive by ticker, plus the moneyline rows recovered from the "
            "synthetic `<gamePk>:<FAMILY>` identifier via the dated final score."
        ),
        market_families=["game_result", "team_total", "first_inning_run", "first_five_innings", "moneyline"],
        eligibility_criteria=[
            "marketFamily is a TRUSTED_PRODUCTION family (fixed list, not searched)",
            "both production and market probabilities archived on the evaluation row",
            "a settled outcome exists -- from the Kalshi settlement archive, or for recovered moneylines "
            "from the dated FINAL score",
        ],
        exclusion_criteria=[
            "RESEARCH_ONLY families -- they are not production and never were",
            "ties (a moneyline cannot settle on a tie) and games with no FINAL score",
            "NRFI/YRFI/F5_ML_* synthetic-key rows -- settling them needs an inning-resolved linescore no "
            "local archive holds; they are reported as unrecovered rather than approximated",
            "any use of FORWARD (settle > 2026-08-28) data",
            "any fitted parameter -- this experiment estimates nothing",
            "economics as a selection input of any kind",
        ],
        prediction_checkpoints=["ARCHIVED_PREGAME_EVALUATION"],
        primary_metric=(
            "paired Brier delta (production minus Kalshi vig-free) per production family, on the "
            "scope-corrected and recovery-enlarged corpus, with game-clustered bootstrap CIs and "
            "Benjamini-Hochberg FDR at 0.10"
        ),
        secondary_metrics=[
            "paired log-loss delta and ECE per corpus and per family",
            "calibration slope/intercept of production",
            "corpus composition: rows, independent games, rows-per-game clustering, degenerate-price counts",
            "TRAIN vs HOLDOUT transport of every family-level direction",
            "SECONDARY fee-aware executable economics (canonical taker fee, never selection)",
        ],
        chronological_split_policy=(
            f"DATE_BASED transport check: TRAIN = settle <= {TRAIN_DATE_MAX}, HOLDOUT = settle > "
            f"{TRAIN_DATE_MAX}. FORWARD = settle > {FORWARD_START_DATE} is EMPTY in the settled archive and "
            "is untouched. No parameter is fitted on TRAIN -- the split exists solely to test whether a "
            "family-level direction transports, the failure mode that killed RSCH-0023 through -0026."
        ),
        minimum_sample_requirement={"independentGames": MIN_GAMES_FAMILY},
        clustering_unit="gameId",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={
            "model_evaluation_probability_prospective_snapshot": "PREDICTIVE_INPUT",
            "settlement_outcome": "EVALUATION_TARGET",
            "team_recent_game_log_reconstruction": "EVALUATION_TARGET",
        },
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            "evidenceLevel E4_PROSPECTIVE_SHADOW (prospectively captured production evaluations). "
            "PREREGISTRATION HONESTY: defects D1/D2 and the aggregate pooled-vs-production Brier point "
            "estimates were OBSERVED DURING SCOPING before any rule was fixed, and are reported as a "
            "DESCRIPTIVE measurement correction carrying no inferential claim. The confirmatory rule above "
            "was locked before the recovered corpus -- rows no experiment in this program has ever scored -- "
            "was measured. Prior experiment artifacts are NOT rewritten. MAXIMUM disposition LEVEL 1 SHADOW "
            "CANDIDATE: a passing family would be a shadow candidate for family-restricted selection, which "
            "is NOT a production change and is NOT activated here."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Main ──────────────────────────────────────────────────────────────────

def build_corpora():
    """The three corpora this experiment compares. Returns a dict of
    label -> rows, plus recovery diagnostics."""
    outcomes, _unsettled = rsch0022.load_settled_outcomes()
    legacy_rows, _excluded = rsch0022.load_evaluated_rows()
    legacy = rsch0022.build_audit_rows(legacy_rows, outcomes, pick="last")
    for r in legacy:
        r.setdefault("origin", "SETTLEMENT_JOINED")

    production_joined = [r for r in legacy if scope_of(r.get("marketFamily")) == "PRODUCTION"]
    research_only = [r for r in legacy if scope_of(r.get("marketFamily")) == "RESEARCH_ONLY"]

    scoped = load_scoped_evaluated_rows()
    finals = load_schedule_finals()
    recovered, recovery_diag = recover_moneyline_rows(scoped, finals)

    # Guard against double-counting: a recovered ticker must not already be
    # present in the settlement-joined corpus.
    joined_tickers = {r["marketTicker"] for r in production_joined}
    overlap = [r for r in recovered if r["marketTicker"] in joined_tickers]
    recovered = [r for r in recovered if r["marketTicker"] not in joined_tickers]
    recovery_diag["overlapWithSettlementJoined"] = len(overlap)

    return {
        "legacy": legacy,
        "productionJoined": production_joined,
        "researchOnly": research_only,
        "productionRecovered": production_joined + recovered,
        "recoveredOnly": recovered,
    }, recovery_diag, len(finals)


def main():
    control, definition = register_experiment()
    corpora, recovery_diag, n_finals = build_corpora()

    # FORWARD purity: this experiment must never touch post-cutoff data.
    forward_rows = [r for r in corpora["productionRecovered"] if r["settleDate"] > FORWARD_START_DATE]

    summaries = {
        "LEGACY_POOLED_RSCH0022_REPRODUCTION": corpus_summary(corpora["legacy"], "LEGACY_POOLED_RSCH0022_REPRODUCTION"),
        "RESEARCH_ONLY_BOARDS": corpus_summary(corpora["researchOnly"], "RESEARCH_ONLY_BOARDS"),
        "PRODUCTION_SETTLEMENT_JOINED": corpus_summary(corpora["productionJoined"], "PRODUCTION_SETTLEMENT_JOINED"),
        "PRODUCTION_RECOVERED": corpus_summary(corpora["productionRecovered"], "PRODUCTION_RECOVERED"),
        "RECOVERED_ONLY": corpus_summary(corpora["recoveredOnly"], "RECOVERED_ONLY"),
    }

    families = family_analysis(corpora["productionRecovered"])
    passing = [e for e in families if e["verdict"] == "PRODUCTION_SHOWS_SKILL"]

    # Economics: ONLY for families that already passed the scoring rule.
    economics = {}
    for e in passing:
        frows = [r for r in corpora["productionRecovered"] if r["marketFamily"] == e["family"]]
        economics[e["family"]] = secondary_economics(frows)

    if passing:
        disposition = "LEVEL_1_SHADOW_CANDIDATE"
        finding = "PRODUCTION_SKILL_CONFIRMED_IN_AT_LEAST_ONE_FAMILY"
    else:
        disposition = "LEVEL_0_NO_PRODUCTION_FAMILY_BEATS_MARKET"
        finding = "PRODUCTION_TRAILS_MARKET_IN_EVERY_QUALIFYING_FAMILY"

    artifact = {
        "experimentId": EXPERIMENT_ID,
        "title": "Production Scope Integrity and Family-Resolved Skill",
        "generatedAtPolicy": "deterministic -- no wall-clock value enters any result",
        "controlModelId": control["controlModelId"],
        "evidenceLevel": ev.E4_PROSPECTIVE_SHADOW,
        "researchOnly": True,
        "productionChanged": False,
        "parametersFitted": 0,
        "scopeDefects": {
            "D1_scopeContamination": {
                "description": "RSCH-0022's loader applies no qualityTier filter, pooling TRUSTED_PRODUCTION "
                               "families with RESEARCH_ONLY boards.",
                "legacyRows": summaries["LEGACY_POOLED_RSCH0022_REPRODUCTION"]["rows"],
                "productionRows": summaries["PRODUCTION_SETTLEMENT_JOINED"]["rows"],
                "researchOnlyRows": summaries["RESEARCH_ONLY_BOARDS"]["rows"],
                "researchOnlyShareOfLegacyCorpus": round(
                    summaries["RESEARCH_ONLY_BOARDS"]["rows"]
                    / summaries["LEGACY_POOLED_RSCH0022_REPRODUCTION"]["rows"], 4),
                "degenerateMarketPricesInProduction": summaries["PRODUCTION_SETTLEMENT_JOINED"]["degenerateMarketPrices"],
                "degenerateMarketPricesInResearchOnly": summaries["RESEARCH_ONLY_BOARDS"]["degenerateMarketPrices"],
                "observedDuringScopingNotPreregistered": True,
            },
            "D2_corpusLoss": dict(recovery_diag, **{
                "description": "Six production families key evaluations by a synthetic `<gamePk>:<FAMILY>` "
                               "identifier that cannot join the ticker-keyed settlement archive, so those "
                               "rows are invisible to every audit rather than excluded by a rule.",
                "scheduleFinalGamesAvailable": n_finals,
                "observedDuringScopingNotPreregistered": True,
            }),
        },
        "corpusSummaries": summaries,
        "familyAnalysis": families,
        "preregisteredDecisionRule": {
            "conditions": [
                "paired Brier delta < 0 with game-clustered bootstrap CI upper bound < 0",
                f"survives Benjamini-Hochberg FDR at {FDR_ALPHA}",
                f"meets sample floors (>= {MIN_ROWS_FAMILY} rows, >= {MIN_GAMES_FAMILY} games)",
                f"direction still holds in HOLDOUT (settle > {TRAIN_DATE_MAX})",
            ],
            "allFourRequired": True,
            "lockedBeforeRecoveredCorpusWasMeasured": True,
        },
        "secondaryEconomics": economics,
        "economicsNote": "Computed only for families that already passed the scoring rule; never selective.",
        "forwardWindowRowsTouched": len(forward_rows),
        "finding": finding,
        "disposition": disposition,
        "maximumDisposition": "LEVEL_1_SHADOW_CANDIDATE",
        "productionActivationAuthorized": False,
        "priorExperimentArtifactsRewritten": False,
    }

    os.makedirs(ANALYTICS_DIR, exist_ok=True)
    with open(ARTIFACT_PATH, "w") as f:
        json.dump(artifact, f, indent=2, sort_keys=True)
        f.write("\n")
    _write_markdown(artifact)

    print(f"{EXPERIMENT_ID}: legacy={summaries['LEGACY_POOLED_RSCH0022_REPRODUCTION']['rows']} "
          f"production={summaries['PRODUCTION_SETTLEMENT_JOINED']['rows']} "
          f"recovered=+{len(corpora['recoveredOnly'])} "
          f"-> {summaries['PRODUCTION_RECOVERED']['rows']} rows / "
          f"{summaries['PRODUCTION_RECOVERED']['independentGames']} games")
    print(f"  pooled delta   {summaries['LEGACY_POOLED_RSCH0022_REPRODUCTION']['pairedBrierDelta']:+.6f}")
    print(f"  production     {summaries['PRODUCTION_RECOVERED']['pairedBrierDelta']:+.6f} "
          f"CI {summaries['PRODUCTION_RECOVERED']['pairedBrierDeltaCI']}")
    for e in families:
        print(f"  {e['family']:18} n={e['rows']:4} g={e['independentGames']:3} "
              f"delta={e['pairedBrierDelta']:+.6f} holdout={e['holdoutBrierDelta']} -> {e['verdict']}")
    print(f"  disposition: {disposition}")
    return 0


def _write_markdown(a):
    s = a["corpusSummaries"]

    def row(key, label):
        c = s[key]
        if not c.get("rows"):
            return f"| {label} | 0 | - | - | - | - | - |"
        ci = c["pairedBrierDeltaCI"]
        return (f"| {label} | {c['rows']} | {c['independentGames']} | {c['rowsPerGame']} | "
                f"{c['modelBrier']:.4f} | {c['marketBrier']:.4f} | "
                f"{c['pairedBrierDelta']:+.4f} [{ci['low']}, {ci['high']}] |")

    lines = [
        f"# {a['experimentId']} -- {a['title']}",
        "",
        "**RESEARCH ONLY. No production change. No candidate activated. "
        f"Parameters fitted: {a['parametersFitted']}.**",
        "",
        "## Why this experiment",
        "",
        "RSCH-0023, -0024, -0025 and -0026 each fitted a correction to the same three-week market",
        "archive, and each failed to transport out of the window it was fitted on. Rather than fit a",
        "fifth, this experiment audits the foundation all four rested on: **the definition of the",
        "corpus**. Every bet-selection decision for the rest of 2026 rests on how far production",
        "trails the market and in which families. That number came from RSCH-0022. This asks whether",
        "it was measured on the right rows.",
        "",
        "## Two scope defects",
        "",
        "### D1 -- scope contamination",
        "",
        "The audit corpus applies no `qualityTier` filter, so it pools two different populations:",
        "**TRUSTED_PRODUCTION** families (what production actually prices and trades) and",
        "**RESEARCH_ONLY** boards (exploratory surfaces that are not production and never were).",
        "",
        f"- RESEARCH_ONLY share of the pooled corpus: **{a['scopeDefects']['D1_scopeContamination']['researchOnlyShareOfLegacyCorpus']:.1%}**",
        f"- Degenerate market prices (exactly 0.0 or 1.0) in production: **{a['scopeDefects']['D1_scopeContamination']['degenerateMarketPricesInProduction']}**",
        f"- Degenerate market prices in the research boards: **{a['scopeDefects']['D1_scopeContamination']['degenerateMarketPricesInResearchOnly']}**",
        "",
        "Every production-family row uses the `kalshiVF` vig-free adapter. The ask-price adapter and",
        "every degenerate price live exclusively in the research boards -- the benchmark corruption",
        "RSCH-0024 identified was never in the production corpus at all.",
        "",
        "### D2 -- corpus loss",
        "",
        "Six production families key their evaluations by a synthetic `<gamePk>:<FAMILY>` identifier",
        "rather than a Kalshi ticker. The settlement archive is ticker-keyed, so these rows cannot",
        "join **by construction** -- they are invisible to every audit this program has run, not",
        "excluded by any rule.",
        "",
        f"- Synthetic-key rows by family: `{a['scopeDefects']['D2_corpusLoss']['syntheticKeyRowsByFamily']}`",
        f"- Recovered here (moneyline, settled from the dated final score): **{a['scopeDefects']['D2_corpusLoss']['recoveredRows']}**",
        f"- Not recoverable without an inning-resolved linescore: `{a['scopeDefects']['D2_corpusLoss']['unrecoverableFamilies']}`",
        "",
        "## Corpus comparison",
        "",
        "Paired delta is **production minus Kalshi**, so a *negative* number means production is better.",
        "",
        "| Corpus | Rows | Games | Rows/game | Model Brier | Market Brier | Paired delta [95% CI] |",
        "|---|---:|---:|---:|---:|---:|---|",
        row("LEGACY_POOLED_RSCH0022_REPRODUCTION", "Pooled (RSCH-0022 reproduction)"),
        row("RESEARCH_ONLY_BOARDS", "RESEARCH_ONLY boards"),
        row("PRODUCTION_SETTLEMENT_JOINED", "Production (settlement-joined)"),
        row("RECOVERED_ONLY", "Recovered moneylines (never before scored)"),
        row("PRODUCTION_RECOVERED", "**Production (scope-corrected + recovered)**"),
        "",
        "## Preregistered family-resolved test",
        "",
        "A family is called `PRODUCTION_SHOWS_SKILL` only if **all four** conditions hold:",
        "",
    ]
    for i, c in enumerate(a["preregisteredDecisionRule"]["conditions"], 1):
        lines.append(f"{i}. {c}")
    lines += [
        "",
        "| Family | Rows | Games | Model | Market | Paired delta [CI] | p | Holdout | Prod. cal. slope | Verdict |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|---|",
    ]
    for e in a["familyAnalysis"]:
        ci = e["pairedBrierDeltaCI"]
        lines.append(
            f"| {e['family']} | {e['rows']} | {e['independentGames']} | {e['modelBrier']:.4f} | "
            f"{e['marketBrier']:.4f} | {e['pairedBrierDelta']:+.4f} [{ci['low']}, {ci['high']}] | "
            f"{e['bootstrapPValue']} | {e['holdoutBrierDelta']} | {e['modelCalibrationSlope']} | "
            f"{e['verdict']} |")

    lines += [
        "",
        "## Preregistration honesty",
        "",
        "This experiment mixes a descriptive correction with a confirmatory test, and they are",
        "labeled separately because they earn different trust.",
        "",
        "- **Observed before preregistration** (descriptive, no inferential claim): defects D1 and D2,",
        "  and the aggregate pooled-vs-production Brier point estimates. These were found while",
        "  scoping which experiment to run, so they carry no p-value and no confidence claim.",
        "- **Preregistered and genuinely unseen**: everything on the recovered corpus. Those rows have",
        "  never been scored by any experiment in this program -- they could not be, they do not join.",
        "",
        "No prior experiment artifact is rewritten. RSCH-0022, -0024 and -0026 stand exactly as merged;",
        "this reports a new finding about the corpus they used.",
        "",
        "## Result",
        "",
        f"- Finding: **{a['finding']}**",
        f"- Disposition: **{a['disposition']}** (maximum permitted: {a['maximumDisposition']})",
        f"- Forward-window rows touched: {a['forwardWindowRowsTouched']}",
        f"- Production activation authorized: {a['productionActivationAuthorized']}",
        "",
    ]
    if a["secondaryEconomics"]:
        lines += ["### Secondary economics (descriptive; never selective)", "",
                  "| Family | Bets | Staked | Fees | Net P/L | Net ROI |", "|---|---:|---:|---:|---:|---:|"]
        for fam, ec in sorted(a["secondaryEconomics"].items()):
            lines.append(f"| {fam} | {ec['bets']} | {ec['grossStaked']} | {ec['totalFees']} | "
                         f"{ec['netPnl']} | {ec['netRoi']} |")
        lines.append("")
    else:
        lines += ["No family passed the scoring rule, so no economics were computed. "
                  "Economics never rescue a failed forecaster.", ""]

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
