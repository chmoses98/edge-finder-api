#!/usr/bin/env python3
"""
scripts/edgelab/run_hitter_shrinkage_confirmation_experiment.py
===============================================================
Research Lab experiment MLB-RSCH-0030: "Hitter Signal-Shrinkage
Confirmation". CONFIRMATORY. RESEARCH ONLY. NO production changes, no
hitter-selection change, no edge-formula change, no shadow activation.

CORE QUESTION: does the hitter model carry genuine incremental
information beyond Kalshi fair value, but at materially smaller
magnitude than its raw disagreement -- and if so, does anything survive
the real executable ask and Kalshi fees?

WHY THIS IS A CONFIRMATION AND NOT A RESTATEMENT
-------------------------------------------------
MLB-RSCH-0029 reported a descriptive OLS coefficient of +0.2334 on the
model-signal term. That number was fitted on ALL rows, with no
out-of-sample design behind it, and it must NOT be read as "23% of
claimed edge is real". It is a HYPOTHESIS. This experiment tests it the
only way that counts: fit on DEVELOPMENT only, freeze, and apply
unchanged to VALIDATION.

CANDIDATE FORM (one parameter, no per-band freedom)
----------------------------------------------------
    m = logit(kalshi fair midpoint)
    r = logit(model probability) - m
    p_shrunk = sigmoid(m + alpha * r)

    alpha = 0  -> trust Kalshi completely            (S0, the benchmark)
    alpha = 1  -> trust the raw hitter model         (S2)
    0 < a < 1  -> real signal, overstated magnitude
    alpha < 0  -> disagreement is anti-signal

alpha is NOT forced positive. The bounds are preregistered wide enough
to express every one of those outcomes.

THREE CONCEPTS KEPT STRICTLY SEPARATE
--------------------------------------
  1. KALSHI FAIR MID  -- the predictive benchmark. Never an entry price.
  2. p_shrunk         -- the candidate probability estimate.
  3. EXECUTABLE ASK   -- entry cost only. Never a predictive benchmark.

Gross executable edge is p_shrunk - yesAsk, and net EV applies the
canonical Kalshi fee engine. alpha is fit ONLY by Bernoulli NLL on
DEVELOPMENT; economics are computed afterwards and never influence it.

THE SAMPLE IS THE BINDING CONSTRAINT, AND IT IS AUDITED FIRST
--------------------------------------------------------------
The archive has 7 usable dates and 261 playerGameKeys, and coverage is
severely uneven -- 2026-08-24 contributes 2 keys and one game, while
2026-08-19 and -20 carry ~50 keys across only 3 games each. Row count
does not fix that. So the design is:

  PRIMARY   DEV = dates <= 2026-08-22, VALIDATION = later dates,
            matching MLB-RSCH-0028/0029's already-preregistered split so
            nothing is re-cut here.
  DATE-AWARE  leave-one-date-out: refit alpha on the other six dates and
            score the held-out date, for all seven. This directly
            answers the uneven-composition problem and yields seven
            out-of-sample evaluations rather than one.

Neither is called confirmation on its own if the other disagrees; both
are reported, and disagreement is reported as disagreement.

MAXIMUM DISPOSITION: LEVEL_1_SHADOW_CANDIDATE. No 2026 production
approval is available from this experiment at any result.
"""
import collections
import json
import math
import os
import random
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
    DEFAULT_BOOTSTRAP_SEED, independent_unit_count, expected_calibration_error,
    brier_and_log_loss_summary, calibration_slope_intercept,
)

import run_hitter_prop_validity_experiment as rsch0028
import run_hitter_edge_decomposition_experiment as rsch0029

EXPERIMENT_ID = "MLB-RSCH-0030"
REGISTRATION_TIMESTAMP = "2026-08-29T05:40:00Z"

ANALYTICS_DIR = os.path.join(_ROOT, "data", "edgelab", "analytics")
ARTIFACT_PATH = os.path.join(ANALYTICS_DIR, "latest_mlb_rsch_0030_shrinkage_confirmation.json")
FROZEN_PATH = os.path.join(ANALYTICS_DIR, "frozen_mlb_rsch_0030_forward_model.json")
REPORT_PATH = os.path.join(_ROOT, "docs", "EDGELAB_MLB_RSCH_0030_SHRINKAGE_CONFIRMATION.md")

# ── Preregistered constants (locked before alpha was fitted) ─────────────
ALPHA_BOUNDS = (-1.0, 2.0)          # wide enough to express anti-signal and over-trust
ALPHA_GRID_STEPS = 61               # coarse grid, then deterministic golden refine
ALPHA_REFINE_ITERS = 40
PROB_CLAMP = (0.001, 0.999)
DEV_DATE_MAX = rsch0028.DEV_DATE_MAX          # 2026-08-22, inherited unchanged
PROBABILITY_BANDS = rsch0029.PROBABILITY_BANDS
SIGNAL_BUCKETS = rsch0029.SIGNAL_BUCKETS
FAMILIES = rsch0028.ELIGIBLE_FAMILIES
CLUSTER_KEY = rsch0028.CLUSTER_KEY
MIN_FAMILY_ROWS = 200
MIN_FAMILY_KEYS = 50
MIN_SEGMENT_ROWS = 100
MIN_SEGMENT_KEYS = 25
FDR_ALPHA = 0.10
BOOTSTRAP_RESAMPLES = 400
CAPACITY_THRESHOLDS = (0.0, 0.025, 0.05)      # net EV > 0, > 2.5pp, > 5pp

# Forward thresholds, chosen BEFORE any forward data exists.
FORWARD_MIN_KEYS = 100
FORWARD_MIN_GAMES = 30
FORWARD_MIN_DATES = 7


def _current_git_commit_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_ROOT).decode().strip()
    except Exception:
        return "unknown"


# ── Candidate form ────────────────────────────────────────────────────────

def _clamp(p):
    return min(max(p, PROB_CLAMP[0]), PROB_CLAMP[1])


def logit(p):
    p = _clamp(p)
    return math.log(p / (1.0 - p))


def sigmoid(x):
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def shrunk_probability(model_p, market_p, alpha):
    """sigmoid(logit(market) + alpha * (logit(model) - logit(market)))."""
    m = logit(market_p)
    r = logit(model_p) - m
    return sigmoid(m + alpha * r)


# ── Fitting: NLL only, never economics ───────────────────────────────────

def nll(rows, alpha):
    """Mean Bernoulli negative log likelihood of the shrunk probability.
    The ONLY objective alpha is ever fitted against."""
    if not rows:
        return None
    total = 0.0
    for r in rows:
        p = _clamp(shrunk_probability(r["modelP"], r["marketP"], alpha))
        total += -(r["outcome"] * math.log(p) + (1 - r["outcome"]) * math.log(1 - p))
    return total / len(rows)


def fit_alpha(rows, *, bounds=ALPHA_BOUNDS):
    """Deterministic coarse grid then golden-section refine. No randomness,
    no economics, no per-band freedom -- one scalar."""
    if len(rows) < MIN_SEGMENT_ROWS:
        return None
    lo, hi = bounds
    best_a, best_v = None, None
    for i in range(ALPHA_GRID_STEPS):
        a = lo + (hi - lo) * i / (ALPHA_GRID_STEPS - 1)
        v = nll(rows, a)
        if v is not None and (best_v is None or v < best_v):
            best_a, best_v = a, v
    step = (hi - lo) / (ALPHA_GRID_STEPS - 1)
    left, right = max(lo, best_a - step), min(hi, best_a + step)
    phi = (math.sqrt(5.0) - 1.0) / 2.0
    c, d = right - phi * (right - left), left + phi * (right - left)
    fc, fd = nll(rows, c), nll(rows, d)
    for _ in range(ALPHA_REFINE_ITERS):
        if fc < fd:
            right, d, fd = d, c, fc
            c = right - phi * (right - left)
            fc = nll(rows, c)
        else:
            left, c, fc = c, d, fd
            d = left + phi * (right - left)
            fd = nll(rows, d)
    a = (left + right) / 2.0
    return {"alpha": round(a, 6), "devNll": round(nll(rows, a), 6),
            "nllAtZero": round(nll(rows, 0.0), 6), "nllAtOne": round(nll(rows, 1.0), 6),
            "rows": len(rows), "playerGameKeys": independent_unit_count(rows, CLUSTER_KEY)}


def alpha_clustered_ci(rows, *, cluster_key=CLUSTER_KEY, n=BOOTSTRAP_RESAMPLES):
    """Bootstrap the FIT itself over whole player-games. Refitting alpha in
    every resample is the honest interval -- resampling rows would treat ~20
    correlated observations per player-game as independent."""
    by = collections.defaultdict(list)
    for r in rows:
        by[r.get(cluster_key)].append(r)
    clusters = sorted(by, key=str)
    if len(clusters) < 10:
        return None, None
    rng = random.Random(DEFAULT_BOOTSTRAP_SEED)
    est = []
    for _ in range(n):
        sample = [rng.choice(clusters) for _ in clusters]
        resampled = [row for c in sample for row in by[c]]
        fit = fit_alpha(resampled)
        if fit:
            est.append(fit["alpha"])
    if not est:
        return None, None
    est.sort()
    return round(est[int(0.025 * (len(est) - 1))], 4), round(est[int(0.975 * (len(est) - 1))], 4)


# ── Scoring the three candidates on identical rows ───────────────────────

def score_candidates(rows, alpha, label):
    """S0 market / S1 shrunk / S2 raw model, on exactly the same rows."""
    if not rows:
        return {"label": label, "rows": 0}
    s0 = [(r["marketP"], r["outcome"]) for r in rows]
    s1 = [(shrunk_probability(r["modelP"], r["marketP"], alpha), r["outcome"]) for r in rows]
    s2 = [(r["modelP"], r["outcome"]) for r in rows]
    out = {"label": label, "rows": len(rows),
           "playerGameKeys": independent_unit_count(rows, CLUSTER_KEY),
           "independentGames": independent_unit_count(rows, "gameId"),
           "independentDates": independent_unit_count(rows, "date"),
           "alphaApplied": alpha}
    for name, pairs in (("S0_market", s0), ("S1_shrunk", s1), ("S2_rawModel", s2)):
        brier, ll = brier_and_log_loss_summary(pairs)
        slope, intercept = calibration_slope_intercept(pairs)
        out[name] = {"brier": brier, "logLoss": ll,
                     "ece": round(expected_calibration_error(pairs, n_bins=10), 6),
                     "calibrationSlope": slope, "calibrationIntercept": intercept}
    out["S1_minus_S0_brier"] = round(out["S1_shrunk"]["brier"] - out["S0_market"]["brier"], 6)
    out["S1_minus_S0_logLoss"] = round(out["S1_shrunk"]["logLoss"] - out["S0_market"]["logLoss"], 6)
    out["S2_minus_S0_brier"] = round(out["S2_rawModel"]["brier"] - out["S0_market"]["brier"], 6)
    out["S2_minus_S0_logLoss"] = round(out["S2_rawModel"]["logLoss"] - out["S0_market"]["logLoss"], 6)
    out["S1_beats_S0_bothMetrics"] = bool(out["S1_minus_S0_brier"] < 0 and out["S1_minus_S0_logLoss"] < 0)
    return out


def paired_s1_minus_s0_brier(rows, alpha):
    if not rows:
        return None
    s1 = sum((shrunk_probability(r["modelP"], r["marketP"], alpha) - r["outcome"]) ** 2 for r in rows) / len(rows)
    s0 = sum((r["marketP"] - r["outcome"]) ** 2 for r in rows) / len(rows)
    return s1 - s0


def clustered_delta_ci(rows, alpha, *, cluster_key=CLUSTER_KEY):
    return rsch0028.game_clustered_bootstrap_ci(
        rows, lambda rs: paired_s1_minus_s0_brier(rs, alpha),
        cluster_key=cluster_key, n_resamples=BOOTSTRAP_RESAMPLES)


# ── Preregistered segment analyses ───────────────────────────────────────

def family_alphas(dev_rows, val_rows, global_alpha):
    """Secondary and EXPLORATORY. A family alpha is only reported where the
    floors are met, and a favourable tiny family is never promoted."""
    entries = []
    for fam in FAMILIES:
        d = [r for r in dev_rows if r["marketFamily"] == fam]
        v = [r for r in val_rows if r["marketFamily"] == fam]
        meets = len(d) >= MIN_FAMILY_ROWS and independent_unit_count(d, CLUSTER_KEY) >= MIN_FAMILY_KEYS
        fit = fit_alpha(d) if meets else None
        e = {"family": fam, "devRows": len(d), "valRows": len(v),
             "devKeys": independent_unit_count(d, CLUSTER_KEY),
             "meetsFloor": meets, "familyAlpha": fit["alpha"] if fit else None}
        # VALIDATION is always scored under the GLOBAL frozen alpha -- the
        # family alpha is reported for interest, never used to score.
        e["validationUnderGlobalAlpha"] = score_candidates(v, global_alpha, fam) if v else {"rows": 0}
        if meets and v:
            e["bootstrapPValue"] = _delta_pvalue(v, global_alpha)
        else:
            e["bootstrapPValue"] = None
        entries.append(e)
    rejected = rsch0028.benjamini_hochberg([e["bootstrapPValue"] for e in entries], FDR_ALPHA)
    for i, e in enumerate(entries):
        e["fdrSignificant"] = i in rejected
    return entries


def _delta_pvalue(rows, alpha, *, cluster_key=CLUSTER_KEY):
    by = collections.defaultdict(list)
    for r in rows:
        by[r.get(cluster_key)].append(r)
    clusters = sorted(by, key=str)
    observed = paired_s1_minus_s0_brier(rows, alpha)
    if not clusters or observed is None:
        return None
    rng = random.Random(DEFAULT_BOOTSTRAP_SEED)
    est = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample = [rng.choice(clusters) for _ in clusters]
        v = paired_s1_minus_s0_brier([row for c in sample for row in by[c]], alpha)
        if v is not None:
            est.append(v)
    if not est:
        return None
    mean = sum(est) / len(est)
    extreme = sum(1 for e in est if abs(e - mean) >= abs(observed))
    return round(min(1.0, (extreme + 1) / (len(est) + 1)), 4)


def band_analysis(rows, alpha):
    out = []
    for lo, hi in PROBABILITY_BANDS:
        sub = [r for r in rows if lo <= r["modelP"] < hi]
        blk = score_candidates(sub, alpha, f"[{lo:.2f},{hi:.2f})")
        blk["band"] = f"[{lo:.2f},{hi:.2f})"
        blk["meetsFloor"] = len(sub) >= MIN_SEGMENT_ROWS and independent_unit_count(sub, CLUSTER_KEY) >= MIN_SEGMENT_KEYS
        out.append(blk)
    return out


def signal_bucket_analysis(rows, alpha):
    """Does shrinkage restore monotonicity between claimed advantage and
    realised advantage? Buckets are RSCH-0029's, unchanged."""
    out = []
    for lo, hi in SIGNAL_BUCKETS:
        lbl = f"[{lo:+.3f},{hi:+.3f})"
        sub = [r for r in rows if lo <= (r["modelP"] - r["marketP"]) < hi]
        blk = score_candidates(sub, alpha, lbl)
        blk["bucket"] = lbl
        blk["meetsFloor"] = len(sub) >= MIN_SEGMENT_ROWS and independent_unit_count(sub, CLUSTER_KEY) >= MIN_SEGMENT_KEYS
        out.append(blk)
    qual = [b for b in out if b.get("meetsFloor")]
    raw = [b["S2_minus_S0_brier"] for b in qual]
    shr = [b["S1_minus_S0_brier"] for b in qual]
    return {"buckets": out,
            "qualifyingBuckets": len(qual),
            "rawMonotoneImproving": (all(raw[i] >= raw[i + 1] for i in range(len(raw) - 1))
                                     if len(raw) >= 3 else None),
            "shrunkMonotoneImproving": (all(shr[i] >= shr[i + 1] for i in range(len(shr) - 1))
                                        if len(shr) >= 3 else None),
            "rawInversion": (raw[-1] > raw[0]) if len(raw) >= 3 else None,
            "shrunkInversion": (shr[-1] > shr[0]) if len(shr) >= 3 else None}


def leave_one_date_out(rows):
    """Date-aware validation: refit on the other six dates, score the held-out
    one. Seven out-of-sample evaluations instead of a single split -- the
    honest answer to this archive's severely uneven date coverage."""
    dates = sorted({r["date"] for r in rows})
    out = []
    for d in dates:
        train = [r for r in rows if r["date"] != d]
        held = [r for r in rows if r["date"] == d]
        fit = fit_alpha(train)
        if fit is None or not held:
            out.append({"heldOutDate": d, "rows": len(held), "status": "INSUFFICIENT_SAMPLE"})
            continue
        sc = score_candidates(held, fit["alpha"], d)
        out.append({"heldOutDate": d, "refitAlpha": fit["alpha"], "rows": len(held),
                    "playerGameKeys": sc["playerGameKeys"],
                    "S1_minus_S0_brier": sc["S1_minus_S0_brier"],
                    "S1_minus_S0_logLoss": sc["S1_minus_S0_logLoss"],
                    "S1_beats_S0_bothMetrics": sc["S1_beats_S0_bothMetrics"]})
    scored = [o for o in out if "S1_beats_S0_bothMetrics" in o]
    return {"folds": out,
            "foldsEvaluated": len(scored),
            "foldsWhereS1Wins": sum(1 for o in scored if o["S1_beats_S0_bothMetrics"]),
            "refitAlphaRange": ([min(o["refitAlpha"] for o in scored),
                                 max(o["refitAlpha"] for o in scored)] if scored else None)}


# ── Honest executable economics (only after predictive validation) ───────

def executable_economics(rows, alpha, threshold, label):
    """p_shrunk vs the ACTUAL executable YES ask, with canonical Kalshi
    fees. `threshold` is a preregistered net-EV capacity cut, never tuned
    to ROI. Nothing here influences alpha, and nothing implies a bet was
    placed."""
    staked = fees = pnl = 0.0
    bets = wins = 0
    prices = []
    positive_gross = 0
    for r in rows:
        ask = r.get("yesAsk")
        if ask is None or not (0.0 < ask < 1.0):
            continue
        p = shrunk_probability(r["modelP"], r["marketP"], alpha)
        gross_edge = p - ask
        if gross_edge > 0:
            positive_gross += 1
        fee = taker_fee(1, ask)
        # Net EV per 1 contract: win (1-ask) w.p. p, lose ask otherwise, minus fee.
        net_ev = p * (1.0 - ask) - (1.0 - p) * ask - fee
        if net_ev <= threshold:
            continue
        bets += 1
        wins += r["outcome"]
        staked += ask
        fees += fee
        pnl += ((1.0 - ask) if r["outcome"] == 1 else -ask) - fee
        prices.append(ask)
    return {
        "segment": label, "netEvThreshold": threshold,
        "rowsWithPositiveGrossEdge": positive_gross,
        "opportunities": bets, "wins": wins, "losses": bets - wins,
        "averageAsk": round(sum(prices) / len(prices), 4) if prices else None,
        "grossStaked": round(staked, 4),
        "grossPnlBeforeFees": round(pnl + fees, 4),
        "totalFees": round(fees, 4), "netPnl": round(pnl, 4),
        "netRoi": round(pnl / staked, 4) if staked else None,
        "note": "SECONDARY. alpha never fitted to economics. Never implies a bet was placed.",
    }


# ── Preregistered success rule ───────────────────────────────────────────

def evaluate_success(val, lodo, families, bands):
    """All five preregistered criteria, evaluated exactly as written."""
    c1 = bool(val.get("S1_beats_S0_bothMetrics"))
    scored = [f for f in lodo["folds"] if "S1_beats_S0_bothMetrics" in f]
    c2 = bool(scored) and sum(1 for f in scored if f["S1_beats_S0_bothMetrics"]) > len(scored) / 2.0
    fam_wins = [f for f in families
                if f.get("meetsFloor")
                and f.get("validationUnderGlobalAlpha", {}).get("S1_beats_S0_bothMetrics")]
    fam_eligible = [f for f in families if f.get("meetsFloor")]
    c3 = len(fam_wins) >= 2 if len(fam_eligible) >= 2 else False
    s0_ece = val.get("S0_market", {}).get("ece")
    s1_ece = val.get("S1_shrunk", {}).get("ece")
    c4 = (s0_ece is not None and s1_ece is not None and s1_ece <= s0_ece + 0.005)
    c5 = val.get("playerGameKeys", 0) >= 50 and val.get("independentGames", 0) >= 10
    return {
        "1_S1_beats_S0_on_brier_and_logloss": c1,
        "2_direction_holds_on_majority_of_held_out_dates": c2,
        "3_not_concentrated_in_one_family": c3,
        "4_no_material_calibration_degradation": c4,
        "5_sufficient_independent_sample": c5,
        "allRequired": bool(c1 and c2 and c3 and c4 and c5),
    }


# ── Registration ──────────────────────────────────────────────────────────

def register_experiment():
    try:
        existing = reg.load_experiment(EXPERIMENT_ID)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        return ctrl_id.load_control(existing["controlModelId"]), existing

    control = ctrl_id.build_control_registration(
        name="mlb_rsch_0030_hitter_shrinkage_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0030 hitter signal shrinkage v1: control S0 = Kalshi vig-free fair "
                        "midpoint (alpha=0); candidate S1 = sigmoid(logit(mid) + alpha*(logit(model)-"
                        "logit(mid))) with ONE global alpha fitted on DEVELOPMENT by Bernoulli NLL, "
                        "bounded [-1,2], then FROZEN and applied unchanged to VALIDATION; reference "
                        "S2 = raw model (alpha=1). Economics never influence alpha."
        ),
        probability_adapter_identity=(
            "Kalshi vig-free fair midpoint as predictive benchmark; executable YES ask used only for "
            "entry economics, never as a benchmark"
        ),
        model_engine_family="hitter_signal_shrinkage_v1",
        required_input_provenance=["hitter_snapshot", "archived_kalshi_market_observation", "settlement_outcome"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=("Confirmatory out-of-sample test of whether hitter-model disagreement with Kalshi "
                     "carries genuine incremental information at reduced magnitude."),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Hitter Signal-Shrinkage Confirmation",
        hypothesis=(
            "H1: the hitter model carries genuine incremental information beyond Kalshi's fair midpoint, "
            "but its raw disagreement overstates that information, so some 0 < alpha < 1 fitted on "
            "DEVELOPMENT beats the market out-of-sample on VALIDATION. H2 (null, tested not assumed): "
            "alpha is indistinguishable from 0 and the hitter model adds nothing beyond Kalshi. H3: alpha "
            "is near 1, meaning the disagreement was never overstated and MLB-RSCH-0029's apparent "
            "inversion was composition or sampling noise. H4: alpha is unstable across held-out dates, in "
            "which case no reliable shrinkage exists yet. MLB-RSCH-0029's descriptive +0.2334 is an "
            "untested hypothesis here, NOT a prior to be confirmed."
        ),
        research_question=(
            "Does hitter-model disagreement with Kalshi contain real incremental signal at reduced "
            "magnitude, what frozen alpha transports out-of-time, and does any positive expected value "
            "survive the actual executable ask and Kalshi fees?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E4_PROSPECTIVE_SHADOW,
        target_population=("MLB-RSCH-0028's eligible hitter corpus, reused unchanged: the complete archived "
                           "prospective hitter snapshot universe 2026-08-19..25 across four supported "
                           "families, joined to a settlement and a contemporaneous valid pregame quote. "
                           "Not restricted to recommendations, positive edges or user wagers."),
        market_families=list(FAMILIES),
        eligibility_criteria=["identical to MLB-RSCH-0028 -- corpus builder reused unchanged",
                              "a contemporaneous quote carrying both yesBid and yesAsk, so the fair "
                              "midpoint and the executable ask are both measured, never inferred"],
        exclusion_criteria=[
            "economics as a fitting objective -- alpha is fitted ONLY by Bernoulli NLL",
            "per-band or per-family alpha in the primary test; family alphas are exploratory and never "
            "used to score validation",
            "the executable ask as a predictive benchmark",
            "the midpoint as an execution cost",
            "user-confirmed wagers; any inference that a recommendation was placed",
            "post-hoc adjustment of alpha bounds, bands, buckets, floors or capacity thresholds",
        ],
        prediction_checkpoints=list(rsch0028.CHECKPOINT_ORDER),
        primary_metric=("paired Brier and log-loss of S1 (frozen alpha) minus S0 (Kalshi fair midpoint) on "
                        "VALIDATION rows, playerGameKey-clustered"),
        secondary_metrics=[
            "alpha point estimate with playerGameKey-clustered bootstrap CI (the FIT itself resampled)",
            "leave-one-date-out refit and held-out scoring across all seven dates",
            "S2 raw model vs S0 for context",
            "fixed probability bands; RSCH-0029's fixed signal buckets and monotonicity restoration",
            "exploratory family alphas with BH-FDR",
            "SECONDARY fee-aware economics at the executable ask under preregistered net-EV capacity cuts",
        ],
        chronological_split_policy=(
            f"PRIMARY DATE_BASED: DEVELOPMENT = date <= {DEV_DATE_MAX}, VALIDATION = later dates -- the "
            "split MLB-RSCH-0028/0029 already preregistered, inherited unchanged so nothing is re-cut. "
            "DATE-AWARE SECONDARY: leave-one-date-out, refitting alpha on the other six dates and scoring "
            "the held-out date, for all seven. Coverage is severely uneven (2026-08-24 contributes 2 "
            "playerGameKeys and one game; 08-19 and 08-20 carry ~50 keys across only 3 games each), so "
            "the single split is not treated as sufficient on its own and disagreement between the two "
            "designs is reported as disagreement, never resolved in the candidate's favour. FORWARD = "
            "dates after registration, untouched."
        ),
        minimum_sample_requirement={"independentGames": 10},
        clustering_unit=CLUSTER_KEY,
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={
            "hitter_snapshot": "PREDICTIVE_INPUT",
            "archived_kalshi_market_observation": "PREDICTIVE_INPUT",
            "settlement_outcome": "EVALUATION_TARGET",
        },
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            "evidenceLevel E4_PROSPECTIVE_SHADOW. CONFIRMATORY: alpha is fitted on DEVELOPMENT only, then "
            "FROZEN and applied unchanged to VALIDATION -- fitting on all dates and reporting the fit "
            "would not be confirmation. alpha is NOT forced positive; bounds [-1,2] can express "
            "anti-signal and over-trust alike. Repeated measures throughout (~20 rows per player-game "
            "across five checkpoints and multiple ladder rungs), so every interval clusters on "
            "playerGameKey and the alpha CI resamples whole player-games and REFITS in each resample. "
            "MAXIMUM disposition LEVEL_1_SHADOW_CANDIDATE; no 2026 production approval is available here, "
            "and LEVEL 2 requires forward confirmation under the frozen artifact at "
            f">= {FORWARD_MIN_KEYS} new playerGameKeys, >= {FORWARD_MIN_GAMES} games and "
            f">= {FORWARD_MIN_DATES} dates, thresholds chosen before any forward data exists."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    control, _definition = register_experiment()
    rows, exclusions, identity = rsch0029.build_decomposed_corpus()

    dates = sorted({r["date"] for r in rows})
    coverage = {"rows": len(rows),
                "playerGameKeys": independent_unit_count(rows, CLUSTER_KEY),
                "players": independent_unit_count(rows, "playerId"),
                "independentGames": independent_unit_count(rows, "gameId"),
                "dates": dates,
                "families": dict(collections.Counter(r["marketFamily"] for r in rows)),
                "checkpoints": dict(collections.Counter(r["checkpoint"] for r in rows)),
                "perDate": [{"date": d,
                             "rows": sum(1 for r in rows if r["date"] == d),
                             "playerGameKeys": independent_unit_count([r for r in rows if r["date"] == d], CLUSTER_KEY),
                             "independentGames": independent_unit_count([r for r in rows if r["date"] == d], "gameId")}
                            for d in dates]}

    dev = [r for r in rows if r["date"] <= DEV_DATE_MAX]
    val = [r for r in rows if r["date"] > DEV_DATE_MAX]

    # ── FIT ON DEVELOPMENT ONLY, THEN FREEZE ────────────────────────────
    fit = fit_alpha(dev)
    if fit is None:
        raise SystemExit("DEVELOPMENT sample below the preregistered floor")
    alpha = fit["alpha"]
    ci_lo, ci_hi = alpha_clustered_ci(dev)
    game_lo, game_hi = alpha_clustered_ci(dev, cluster_key="gameId")
    fit.update({"alphaCI_playerGameClustered": {"low": ci_lo, "high": ci_hi},
                "alphaCI_gameClustered": {"low": game_lo, "high": game_hi},
                "bounds": list(ALPHA_BOUNDS),
                "objective": "Bernoulli NLL on DEVELOPMENT only; economics never enter"})

    dev_scores = score_candidates(dev, alpha, "DEVELOPMENT")
    val_scores = score_candidates(val, alpha, "VALIDATION")
    v_lo, v_hi, _m = clustered_delta_ci(val, alpha)
    val_scores["S1_minus_S0_brierCI"] = {"low": v_lo, "high": v_hi, "clusterUnit": CLUSTER_KEY}

    lodo = leave_one_date_out(rows)
    families = family_alphas(dev, val, alpha)
    bands = band_analysis(val, alpha)
    buckets = signal_bucket_analysis(val, alpha)

    success = evaluate_success(val_scores, lodo, families, bands)

    # ── Economics: only after the predictive verdict ────────────────────
    econ = {}
    for t in CAPACITY_THRESHOLDS:
        econ[f"validation_netEV_gt_{t}"] = executable_economics(val, alpha, t, "VALIDATION")
    econ["validation_S0_reference_netEV_gt_0"] = executable_economics(val, 0.0, 0.0, "VALIDATION_alpha0")

    # ── Classification ──────────────────────────────────────────────────
    lo, hi = (ci_lo if ci_lo is not None else alpha), (ci_hi if ci_hi is not None else alpha)
    if success["allRequired"]:
        classification = "CASE_B_VALIDATED_SHRINKAGE"
    elif lo <= 0.0 <= hi and abs(alpha) < 0.15:
        classification = "CASE_A_NO_INCREMENTAL_INFORMATION"
    elif lo <= 1.0 <= hi and alpha > 0.6:
        classification = "CASE_C_DISAGREEMENT_NOT_OVERSTATED"
    else:
        classification = "CASE_D_UNSTABLE_OR_FAILED_VALIDATION"

    # ── MATERIALITY, disclosed as a POST-HOC OBSERVATION ────────────────
    # This block was added AFTER seeing the results and is NOT a
    # preregistered gate. It does not alter the mechanical verdict above --
    # rewriting the rule after seeing the outcome is exactly what this
    # program forbids. It exists because the preregistered criteria, taken
    # from the mission spec, test the SIGN of the improvement and not its
    # magnitude or whether it is distinguishable from zero, and on this
    # sample all three of those come out weak at once.
    total_opportunities = sum(e["opportunities"] for k, e in econ.items()
                              if k.startswith("validation_netEV"))
    materiality = {
        "preregistered": False,
        "disclosedAs": "observed after results; does NOT change the mechanical verdict",
        "alphaCiIncludesZero": bool(lo <= 0.0 <= hi),
        "validationDeltaCiIncludesZero": bool(
            v_lo is not None and v_hi is not None and v_lo <= 0.0 <= v_hi),
        "validationBrierImprovement": val_scores.get("S1_minus_S0_brier"),
        "devNllGainOverPureMarket": round(fit["nllAtZero"] - fit["devNll"], 8),
        "leaveOneDateOutMajorityMargin": (
            f"{lodo['foldsWhereS1Wins']}/{lodo['foldsEvaluated']}"),
        "executableOpportunitiesAfterFees": total_opportunities,
        "rowsWithPositiveGrossEdgeBeforeFees":
            econ["validation_netEV_gt_0.0"]["rowsWithPositiveGrossEdge"],
        "actionableEdgeExists": bool(total_opportunities > 0),
        "readingForHumans": (
            "The preregistered criteria pass on point estimates, but the fitted alpha's confidence "
            "interval includes zero, the validation improvement's interval includes zero, the "
            "improvement is on the order of 1e-4 Brier, and ZERO contracts clear the canonical fee "
            "after shrinkage. A shrinkage factor that cannot be distinguished from 'ignore the model' "
            "and that yields no executable opportunity is not a betting lever, whatever the label says."
        ),
    }

    shadow = bool(success["allRequired"])
    disposition = "LEVEL_1_SHADOW_CANDIDATE" if shadow else "LEVEL_0_NO_VALIDATED_SIGNAL"

    artifact = {
        "experimentId": EXPERIMENT_ID,
        "title": "Hitter Signal-Shrinkage Confirmation",
        "controlModelId": control["controlModelId"],
        "evidenceLevel": ev.E4_PROSPECTIVE_SHADOW,
        "researchOnly": True, "productionChanged": False,
        "alphaFittedOnEconomics": False,
        "usesUserConfirmedWagers": False, "impliesRecommendationsWereBet": False,
        "rsch0029CoefficientTreatedAsHypothesisNotPrior": True,
        "coverage": coverage,
        "corpusExclusions": dict(exclusions),
        "decompositionIdentityAudit": identity,
        "chronologicalDesign": {
            "primary": {"devDateMax": DEV_DATE_MAX,
                        "devRows": len(dev), "devKeys": independent_unit_count(dev, CLUSTER_KEY),
                        "valRows": len(val), "valKeys": independent_unit_count(val, CLUSTER_KEY),
                        "valGames": independent_unit_count(val, "gameId"),
                        "valDates": independent_unit_count(val, "date")},
            "dateAwareSecondary": "leave-one-date-out refit across all seven dates",
        },
        "devFit": fit,
        "development": dev_scores,
        "validation": val_scores,
        "leaveOneDateOut": lodo,
        "familyAlphas": families,
        "probabilityBands": bands,
        "signalBuckets": buckets,
        "successCriteria": success,
        "materialityAssessment": materiality,
        "secondaryEconomics": econ,
        "classification": classification,
        "disposition": disposition,
        "maximumDisposition": "LEVEL_1_SHADOW_CANDIDATE",
        "shadowCandidateJustified": shadow,
        "productionActivationAuthorized": False,
        "forwardThresholds": {"minNewPlayerGameKeys": FORWARD_MIN_KEYS,
                              "minIndependentGames": FORWARD_MIN_GAMES,
                              "minNewDates": FORWARD_MIN_DATES,
                              "chosenBeforeForwardDataInspected": True},
    }

    os.makedirs(ANALYTICS_DIR, exist_ok=True)
    with open(ARTIFACT_PATH, "w") as f:
        json.dump(artifact, f, indent=2, sort_keys=True)
        f.write("\n")

    # Frozen forward artifact ONLY if the preregistered rule passed.
    if shadow:
        frozen = {
            "experimentId": EXPERIMENT_ID, "alpha": alpha,
            "trainingEndDate": DEV_DATE_MAX,
            "candidateForm": "sigmoid(logit(marketMid) + alpha * (logit(modelP) - logit(marketMid)))",
            "probabilityClamp": list(PROB_CLAMP),
            "eligibleFamilies": list(FAMILIES),
            "feeEngine": "lib.edgelab.kalshi_fees.taker_fee",
            "entryPrice": "executable YES ask", "benchmark": "Kalshi vig-free fair midpoint",
            "noRefitRule": "alpha is READ, never re-estimated, when scoring forward data",
            "forwardThresholds": artifact["forwardThresholds"],
            "productionActive": False,
        }
        with open(FROZEN_PATH, "w") as f:
            json.dump(frozen, f, indent=2, sort_keys=True)
            f.write("\n")
    _write_markdown(artifact)

    print(f"{EXPERIMENT_ID}: rows={len(rows)} keys={coverage['playerGameKeys']} "
          f"games={coverage['independentGames']} dates={len(dates)}")
    print(f"  DEV fit alpha = {alpha:.4f}  CI(playerGame) [{ci_lo}, {ci_hi}]  CI(game) [{game_lo}, {game_hi}]")
    print(f"       NLL at alpha {fit['devNll']:.6f} | at 0 {fit['nllAtZero']:.6f} | at 1 {fit['nllAtOne']:.6f}")
    print(f"  DEV  S1-S0 brier {dev_scores['S1_minus_S0_brier']:+.6f}  logloss {dev_scores['S1_minus_S0_logLoss']:+.6f}")
    print(f"  VAL  S1-S0 brier {val_scores['S1_minus_S0_brier']:+.6f} CI[{v_lo},{v_hi}] "
          f" logloss {val_scores['S1_minus_S0_logLoss']:+.6f}  -> S1 beats S0: {val_scores['S1_beats_S0_bothMetrics']}")
    print(f"  VAL  S2-S0 brier {val_scores['S2_minus_S0_brier']:+.6f} (raw model vs market)")
    print(f"  LODO: {lodo['foldsWhereS1Wins']}/{lodo['foldsEvaluated']} held-out dates where S1 wins both; "
          f"refit alpha range {lodo['refitAlphaRange']}")
    for k, v in success.items():
        print(f"    {k}: {v}")
    print(f"  classification={classification}  disposition={disposition}")
    print("  MATERIALITY (post-hoc, does not change the verdict):")
    print(f"    alpha CI includes 0: {materiality['alphaCiIncludesZero']} | "
          f"VAL delta CI includes 0: {materiality['validationDeltaCiIncludesZero']}")
    print(f"    DEV NLL gain over pure market: {materiality['devNllGainOverPureMarket']}")
    print(f"    executable opportunities after fees: {materiality['executableOpportunitiesAfterFees']} "
          f"(positive gross before fees: {materiality['rowsWithPositiveGrossEdgeBeforeFees']})")
    print(f"    actionable edge exists: {materiality['actionableEdgeExists']}")
    for k, e in econ.items():
        print(f"    {k}: opps={e['opportunities']} netRoi={e['netRoi']} posGross={e['rowsWithPositiveGrossEdge']}")
    return 0


def _write_markdown(a):
    f_ = a["devFit"]; d = a["development"]; v = a["validation"]
    lines = [
        f"# {a['experimentId']} -- {a['title']}",
        "",
        "**CONFIRMATORY. RESEARCH ONLY. No production change. alpha never fitted to economics.**",
        "",
        "## What is being tested, and what is not assumed",
        "",
        "MLB-RSCH-0029 reported a descriptive OLS coefficient of +0.2334 fitted on all rows with no",
        "out-of-sample design. That is **a hypothesis, not a prior** -- it is not carried in here. This",
        "experiment fits one scalar on DEVELOPMENT, freezes it, and applies it unchanged to VALIDATION.",
        "",
        "```",
        "p_shrunk = sigmoid( logit(kalshiMid) + alpha * ( logit(modelP) - logit(kalshiMid) ) )",
        "  alpha = 0  -> S0, trust Kalshi          alpha = 1 -> S2, trust the raw model",
        "```",
        "",
        f"alpha bounds `{f_['bounds']}` -- deliberately wide enough to express anti-signal (alpha<0) and",
        "over-trust (alpha>1). It is **not** forced positive.",
        "",
        "## Sample -- the binding constraint, audited before the design was chosen",
        "",
        f"{a['coverage']['rows']:,} rows · **{a['coverage']['playerGameKeys']} playerGameKeys** · "
        f"{a['coverage']['independentGames']} games · {len(a['coverage']['dates'])} dates",
        "",
        "| Date | Rows | Keys | Games |",
        "|---|---:|---:|---:|",
    ]
    for p in a["coverage"]["perDate"]:
        lines.append(f"| {p['date']} | {p['rows']} | {p['playerGameKeys']} | {p['independentGames']} |")
    lines += [
        "",
        "Coverage is severely uneven, so a single split is not treated as sufficient: leave-one-date-out",
        "is reported alongside it, and disagreement between the two is reported as disagreement.",
        "",
        "## Fitted alpha (DEVELOPMENT only)",
        "",
        f"**alpha = {f_['alpha']}**  ·  playerGame-clustered CI "
        f"[{f_['alphaCI_playerGameClustered']['low']}, {f_['alphaCI_playerGameClustered']['high']}]  ·  "
        f"game-clustered CI [{f_['alphaCI_gameClustered']['low']}, {f_['alphaCI_gameClustered']['high']}]",
        "",
        f"- NLL at fitted alpha: {f_['devNll']}",
        f"- NLL at alpha=0 (pure market): {f_['nllAtZero']}",
        f"- NLL at alpha=1 (raw model): {f_['nllAtOne']}",
        "",
        "The CI resamples whole player-games and **refits alpha in every resample** -- resampling rows",
        "would treat ~20 correlated observations per player-game as independent.",
        "",
        "## S0 / S1 / S2",
        "",
        "| | DEV Brier | DEV log loss | VAL Brier | VAL log loss | VAL ECE |",
        "|---|---:|---:|---:|---:|---:|",
        f"| S0 market | {d['S0_market']['brier']} | {d['S0_market']['logLoss']} | "
        f"{v['S0_market']['brier']} | {v['S0_market']['logLoss']} | {v['S0_market']['ece']} |",
        f"| **S1 shrunk** | {d['S1_shrunk']['brier']} | {d['S1_shrunk']['logLoss']} | "
        f"{v['S1_shrunk']['brier']} | {v['S1_shrunk']['logLoss']} | {v['S1_shrunk']['ece']} |",
        f"| S2 raw model | {d['S2_rawModel']['brier']} | {d['S2_rawModel']['logLoss']} | "
        f"{v['S2_rawModel']['brier']} | {v['S2_rawModel']['logLoss']} | {v['S2_rawModel']['ece']} |",
        "",
        f"**VALIDATION S1 - S0: Brier {v['S1_minus_S0_brier']:+.6f} "
        f"[{v['S1_minus_S0_brierCI']['low']}, {v['S1_minus_S0_brierCI']['high']}] · "
        f"log loss {v['S1_minus_S0_logLoss']:+.6f}**",
        "",
        f"S2 - S0 on VALIDATION: Brier {v['S2_minus_S0_brier']:+.6f}, log loss {v['S2_minus_S0_logLoss']:+.6f}",
        "",
        "## Leave-one-date-out (date-aware)",
        "",
        f"S1 wins both metrics on **{a['leaveOneDateOut']['foldsWhereS1Wins']} of "
        f"{a['leaveOneDateOut']['foldsEvaluated']}** held-out dates. Refit alpha range: "
        f"{a['leaveOneDateOut']['refitAlphaRange']}",
        "",
        "| Held-out date | Refit alpha | Rows | Keys | S1-S0 Brier | S1-S0 log loss | S1 wins both |",
        "|---|---:|---:|---:|---:|---:|:-:|",
    ]
    for fold in a["leaveOneDateOut"]["folds"]:
        if "S1_minus_S0_brier" not in fold:
            lines.append(f"| {fold['heldOutDate']} | - | {fold.get('rows',0)} | - | - | - | - |")
            continue
        lines.append(f"| {fold['heldOutDate']} | {fold['refitAlpha']} | {fold['rows']} | "
                     f"{fold['playerGameKeys']} | {fold['S1_minus_S0_brier']:+.5f} | "
                     f"{fold['S1_minus_S0_logLoss']:+.5f} | {'yes' if fold['S1_beats_S0_bothMetrics'] else 'no'} |")

    lines += ["", "## Preregistered success criteria", ""]
    for k, val_ in a["successCriteria"].items():
        if k == "allRequired":
            continue
        lines.append(f"- `{k}`: **{val_}**")
    lines += ["", f"**All required: {a['successCriteria']['allRequired']}**", ""]

    lines += ["## Families (exploratory; validation always scored under the GLOBAL frozen alpha)", "",
              "| Family | DEV rows | DEV keys | Family alpha | VAL S1-S0 Brier | FDR | Floor |",
              "|---|---:|---:|---:|---:|:-:|:-:|"]
    for e in a["familyAlphas"]:
        vv = e.get("validationUnderGlobalAlpha", {})
        lines.append(f"| {e['family']} | {e['devRows']} | {e['devKeys']} | {e.get('familyAlpha')} | "
                     f"{vv.get('S1_minus_S0_brier')} | {'yes' if e.get('fdrSignificant') else 'no'} | "
                     f"{'yes' if e.get('meetsFloor') else 'no'} |")

    b = a["signalBuckets"]
    lines += ["", "## Does shrinkage restore monotonicity?", "",
              f"- Raw model monotone improving: **{b['rawMonotoneImproving']}** · raw inversion: **{b['rawInversion']}**",
              f"- Shrunk monotone improving: **{b['shrunkMonotoneImproving']}** · shrunk inversion: **{b['shrunkInversion']}**",
              f"- Qualifying buckets: {b['qualifyingBuckets']}", "",
              "| Signal bucket | Rows | Keys | S2-S0 (raw) | S1-S0 (shrunk) | Floor |",
              "|---|---:|---:|---:|---:|:-:|"]
    for x in b["buckets"]:
        if not x.get("rows"):
            lines.append(f"| {x['bucket']} | 0 | - | - | - | no |")
            continue
        lines.append(f"| {x['bucket']} | {x['rows']} | {x.get('playerGameKeys','-')} | "
                     f"{x.get('S2_minus_S0_brier')} | {x.get('S1_minus_S0_brier')} | "
                     f"{'yes' if x.get('meetsFloor') else 'no'} |")

    lines += ["", "## Probability bands (VALIDATION, frozen alpha)", "",
              "| Band | Rows | Keys | S1-S0 Brier | S2-S0 Brier | Floor |", "|---|---:|---:|---:|---:|:-:|"]
    for x in a["probabilityBands"]:
        if not x.get("rows"):
            lines.append(f"| {x['band']} | 0 | - | - | - | no |")
            continue
        lines.append(f"| {x['band']} | {x['rows']} | {x.get('playerGameKeys','-')} | "
                     f"{x.get('S1_minus_S0_brier')} | {x.get('S2_minus_S0_brier')} | "
                     f"{'yes' if x.get('meetsFloor') else 'no'} |")

    lines += ["", "## Honest executable economics", "",
              "`p_shrunk` against the **actual executable YES ask**, with canonical Kalshi fees.",
              "Capacity thresholds are preregistered; none was chosen for its ROI.", "",
              "| Segment | net EV cut | Positive gross | Opportunities | Wins | Avg ask | Fees | Net | ROI |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for k, e in a["secondaryEconomics"].items():
        lines.append(f"| {k} | {e['netEvThreshold']} | {e['rowsWithPositiveGrossEdge']} | "
                     f"{e['opportunities']} | {e['wins']} | {e['averageAsk']} | {e['totalFees']} | "
                     f"{e['netPnl']} | {e['netRoi']} |")

    m = a["materialityAssessment"]
    lines += ["", "## Materiality -- read this before the label", "",
              "*Post-hoc observation, explicitly NOT a preregistered gate and NOT altering the verdict.*",
              "",
              f"- Fitted alpha CI includes zero: **{m['alphaCiIncludesZero']}**",
              f"- Validation delta CI includes zero: **{m['validationDeltaCiIncludesZero']}**",
              f"- Validation Brier improvement: **{m['validationBrierImprovement']}**",
              f"- DEV NLL gain over pure market: **{m['devNllGainOverPureMarket']}**",
              f"- Leave-one-date-out majority: **{m['leaveOneDateOutMajorityMargin']}**",
              f"- Rows with positive gross edge before fees: **{m['rowsWithPositiveGrossEdgeBeforeFees']}**",
              f"- **Executable opportunities after fees: {m['executableOpportunitiesAfterFees']}**",
              f"- **Actionable edge exists: {m['actionableEdgeExists']}**",
              "",
              m["readingForHumans"],
              "",
              "## Result", "",
              f"- Classification: **{a['classification']}**",
              f"- Disposition: **{a['disposition']}** (maximum permitted: {a['maximumDisposition']})",
              f"- Shadow candidate justified: **{a['shadowCandidateJustified']}**",
              f"- Production activation authorized: {a['productionActivationAuthorized']}",
              f"- Forward thresholds (chosen before any forward data): "
              f"{a['forwardThresholds']['minNewPlayerGameKeys']} keys / "
              f"{a['forwardThresholds']['minIndependentGames']} games / "
              f"{a['forwardThresholds']['minNewDates']} dates",
              ""]
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
