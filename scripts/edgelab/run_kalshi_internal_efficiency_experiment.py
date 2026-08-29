#!/usr/bin/env python3
"""
scripts/edgelab/run_kalshi_internal_efficiency_experiment.py
====================================================================
Research Lab experiment MLB-RSCH-0026: "Kalshi Internal Market
Efficiency". RESEARCH ONLY. NO production changes, no candidate
activation, no staking/execution/fee-logic changes.

CORE QUESTION: independent of our baseball model entirely, can Kalshi's
own pregame vig-free fair price be transformed using ONLY information
available at decision time (its own price, market family) so that it
predicts settled outcomes better out-of-time?

MOTIVATION: MLB-RSCH-0024 established that production's model carries
essentially zero incremental information beyond Kalshi's price (global
alpha 0.0004). The one positive signal it surfaced was Kalshi's OWN
measured miscalibration. That is a market inefficiency, categorically
different from model skill, and it is the only remaining lever this
program has identified.

STRICT INDEPENDENCE FROM OUR MODEL (enforced, not merely intended):
production's probability (`modelP`) is NEVER read by any fitting or
scoring path in this experiment. The corpus builder is reused from
MLB-RSCH-0024 (which needs modelP for its own purpose) but this
experiment drops that field immediately and a test asserts no scoring
function references it.

PREREGISTERED HYPOTHESIS (fixed BEFORE the chronological split was
evaluated, motivated by MLB-RSCH-0024's own reported band biases rather
than by searching): Kalshi exhibits a classic FAVOURITE-LONGSHOT bias --
low-priced contracts settle YES more often than their price implies and
high-priced contracts less often. The single candidate is therefore ONE
monotone logit-shrink toward the base rate:

    p_candidate = sigmoid( logit(base) + beta * (logit(fair) - logit(base)) )

with `base` the TRAIN-period YES rate and a SINGLE parameter beta fit on
TRAIN by Bernoulli NLL, bounded [0.2, 2.0]. beta<1 shrinks extremes
toward the base rate (the favourite-longshot correction); beta=1 is the
market unchanged; beta>1 sharpens. ONE parameter, no per-band free
parameters, no threshold search.

Secondary, preregistered, reported-not-selected-on: fixed price-band and
family breakdowns, and a family-tier variant ONLY where sample floors
allow. No band cutoff is optimized; the bands are the same fixed
quintiles MLB-RSCH-0024 used.

CHRONOLOGICAL DESIGN: TRAIN = settle <= 2026-08-24, VAL = 08-25..08-28
(both carry all nine eligible families -- full-family capture began
08-23). FORWARD = settle > 2026-08-28: verified at design time to be
EMPTY in the settled archive (which ends 2026-08-27), so it is untouched
by construction; this run emits a frozen artifact so it can be scored
later without refitting.

SUCCESS RULE (locked before results): the candidate is useful only if
  1. VAL Brier delta vs the raw market < 0, AND
  2. VAL log-loss delta < 0, AND
  3. VAL ECE <= raw market VAL ECE, AND
  4. the improvement is not confined to a single price band (at least
     two of the five fixed bands improve), AND
  5. beta's game-clustered bootstrap CI excludes 1.0.
Executable fee-aware economics are computed ONLY afterward, never used
for selection, and calibration bias is NEVER equated with profit.

MAX DISPOSITION: LEVEL 1 SHADOW CANDIDATE (a frozen filter awaiting
FORWARD confirmation). This experiment cannot promote anything.
"""
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
    expected_calibration_error, brier_and_log_loss_summary, game_clustered_bootstrap_ci,
)

import run_market_residual_experiment as rsch0024  # noqa: E402 -- corpus/fair-price reconstruction reused

EXPERIMENT_ID = "MLB-RSCH-0026"
REGISTRATION_TIMESTAMP = "2026-08-28T21:40:00Z"

TRAIN_DATE_MAX = "2026-08-24"
VAL_DATE_MAX = "2026-08-28"

BETA_BOUNDS = (0.2, 2.0)
BETA_GRID_STEPS = 61
PROB_CLAMP = (0.01, 0.99)

PRICE_BANDS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0))
MIN_ROWS_FAMILY = 100
MIN_GAMES_FAMILY = 20
MIN_IMPROVING_BANDS = 2


def _current_git_commit_sha():
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def register_experiment():
    try:
        existing_definition = reg.load_experiment(EXPERIMENT_ID)
    except FileNotFoundError:
        existing_definition = None
    if existing_definition is not None:
        control = ctrl_id.load_control(existing_definition["controlModelId"])
        return control, existing_definition

    control = ctrl_id.build_control_registration(
        name="mlb_rsch_0026_kalshi_internal_efficiency_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0026 Kalshi internal efficiency v1: control = Kalshi's own vig-free fair "
                        "midpoint; candidate = ONE monotone logit-shrink toward the TRAIN base rate, single "
                        "parameter beta bounded [0.2, 2.0] fit on TRAIN by Bernoulli NLL. Production model "
                        "probability is never read by any fitting or scoring path."
        ),
        probability_adapter_identity="Kalshi vig-free fair midpoint reconstructed from archived yesBid/yesAsk (latest valid pregame observation per ticker) -- market data only, no model input",
        model_engine_family="kalshi_market_internal_calibration_v1",
        required_input_provenance=["archived_kalshi_market_observation", "settlement_outcome"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "Tests whether Kalshi's own pregame fair price can be transformed using only decision-time market "
            "information (its own price) to predict settled outcomes better out-of-time -- a market-inefficiency "
            "question strictly independent of our baseball model."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Kalshi Internal Market Efficiency",
        hypothesis=(
            "H1 (favourite-longshot, fixed before evaluating the chronological split): Kalshi's pregame fair "
            "price is systematically too extreme -- low-priced contracts settle YES more often than priced and "
            "high-priced contracts less often -- so a single monotone shrink toward the base rate (beta < 1) "
            "improves proper scoring out-of-time. H2: the effect is broad across price bands rather than "
            "confined to one. H3 (null, tested not assumed): the apparent pooled bias may be a short-sample or "
            "composition artifact that does not survive chronological validation."
        ),
        research_question=(
            "Can Kalshi's own pregame fair price be transformed or filtered using information available at "
            "decision time so that it predicts outcomes better out-of-time, and does any such edge survive "
            "executable prices and fees?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E4_PROSPECTIVE_SHADOW,
        target_population=(
            "Every settled binary Kalshi MLB contract with a reconstructable pregame vig-free fair midpoint, "
            "2026-08-04 .. 2026-08-26, nine families (~2,635 tickers / ~235 games)."
        ),
        market_families=["game_result", "game_total", "team_total", "run_margin", "inning_markets", "pitcher_props"],
        eligibility_criteria=[
            "binary YES-outcome contract with a YES/NO settlement",
            "a valid pregame observation carrying both yesBid and yesAsk (vig-free midpoint reconstructable)",
        ],
        exclusion_criteria=[
            "production model probability as a feature or fitting input -- never read by any fitting or scoring path",
            "per-band free parameters, optimized band cutoffs, or any threshold search",
            "ROI-based selection of any kind; economics are computed only after proper scoring",
            "any use of FORWARD (settle > 2026-08-28) data",
        ],
        prediction_checkpoints=["ARCHIVED_PREGAME_MARKET"],
        primary_metric="paired Brier delta of the shrunk price minus the raw Kalshi fair price, on VALIDATION, game-clustered bootstrap CI",
        secondary_metrics=[
            "log-loss delta and ECE before/after", "fitted beta with game-clustered bootstrap CI",
            "fixed price-band reliability and per-band improvement count",
            "family-level breakdown where sample floors allow",
            "date-block stability", "SECONDARY fee-aware executable economics (taker fees, never selection)",
        ],
        chronological_split_policy=(
            f"DATE_BASED: TRAIN = settle <= {TRAIN_DATE_MAX}, VAL = ({TRAIN_DATE_MAX}, {VAL_DATE_MAX}], "
            "FORWARD = settle > 2026-08-28. Full nine-family capture begins 2026-08-23 so both halves carry all "
            "families. FORWARD verified EMPTY at design time (settled archive ends 2026-08-27) -- untouched by "
            "construction; a frozen artifact is emitted for later scoring without refitting."
        ),
        minimum_sample_requirement={"independentGames": MIN_GAMES_FAMILY},
        clustering_unit="gameId",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={
            "archived_kalshi_market_observation": "PREDICTIVE_INPUT",
            "settlement_outcome": "EVALUATION_TARGET",
        },
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            "evidenceLevel E4_PROSPECTIVE_SHADOW (prospectively captured market inputs). Strictly market-only: "
            "no production model probability enters any fitting or scoring path. Calibration bias is NEVER "
            "equated with profitable execution. MAXIMUM disposition LEVEL 1 SHADOW CANDIDATE -- LEVEL 2 would "
            "require the untouched FORWARD window scored under the frozen beta."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Market-only corpus (production probability dropped immediately) ───────

def build_market_only_rows():
    """Reuses MLB-RSCH-0024's corpus/fair-price reconstruction, then DROPS
    the production probability so it cannot be read downstream."""
    rows, excluded_no_fair, audit_total = rsch0024.build_rows()
    market_rows = [{
        "marketTicker": r["marketTicker"], "gameId": r["gameId"], "family": r["family"],
        "settleDate": r["settleDate"], "outcome": r["outcome"],
        "marketFair": r["marketFair"], "executableAsk": r["executableAsk"],
        "yesBid": r["yesBid"], "yesAsk": r["yesAsk"],
    } for r in rows]
    return market_rows, excluded_no_fair, audit_total


def _clamp(p):
    return min(max(p, PROB_CLAMP[0]), PROB_CLAMP[1])


def _logit(p):
    p = _clamp(p)
    return math.log(p / (1 - p))


def _sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def shrunk_probability(fair, beta, base):
    b = _logit(base)
    return _sigmoid(b + beta * (_logit(fair) - b))


def _nll(rows, beta, base):
    total = 0.0
    for r in rows:
        p = min(max(shrunk_probability(r["marketFair"], beta, base), 1e-9), 1 - 1e-9)
        y = r["outcome"]
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(rows)


def fit_beta(rows, base):
    """Deterministic bounded 1-D NLL minimization: coarse grid over the
    preregistered bounds then a golden-section refine. No randomness."""
    if len(rows) < 30:
        return None
    lo, hi = BETA_BOUNDS
    best_b, best_l = None, None
    for i in range(BETA_GRID_STEPS):
        b = lo + (hi - lo) * i / (BETA_GRID_STEPS - 1)
        l = _nll(rows, b, base)
        if best_l is None or l < best_l:
            best_b, best_l = b, l
    step = (hi - lo) / (BETA_GRID_STEPS - 1)
    left, right = max(lo, best_b - step), min(hi, best_b + step)
    for _ in range(40):
        m1, m2 = left + (right - left) * 0.382, left + (right - left) * 0.618
        if _nll(rows, m1, base) < _nll(rows, m2, base):
            right = m2
        else:
            left = m1
    beta = (left + right) / 2
    return {"beta": round(beta, 4), "trainNll": round(_nll(rows, beta, base), 6),
            "nllAtOne": round(_nll(rows, 1.0, base), 6), "base": round(base, 6), "n": len(rows)}


def beta_bootstrap_ci(rows, base, seed=DEFAULT_BOOTSTRAP_SEED, n_resamples=200):
    import random
    from collections import defaultdict
    by_game = defaultdict(list)
    for r in rows:
        by_game[r["gameId"]].append(r)
    games = sorted(by_game.keys(), key=str)
    if not games:
        return {"low": None, "high": None}
    rng = random.Random(seed)
    est = []
    for _ in range(n_resamples):
        sampled = [rng.choice(games) for _ in games]
        fit = fit_beta([row for g in sampled for row in by_game[g]], base)
        if fit:
            est.append(fit["beta"])
    if not est:
        return {"low": None, "high": None}
    est.sort()
    return {"low": round(est[max(0, round(0.05 * (len(est) - 1)))], 4),
            "high": round(est[min(len(est) - 1, round(0.95 * (len(est) - 1)))], 4),
            "method": "GAME_CLUSTERED_BOOTSTRAP", "resamples": n_resamples}


def score(rows, prob_fn):
    pairs = [(prob_fn(r), r["outcome"]) for r in rows]
    brier, log_loss = brier_and_log_loss_summary(pairs)
    return {"n": len(rows), "independentGames": independent_unit_count(rows, key="gameId"),
            "brier": brier, "logLoss": log_loss, "ece": expected_calibration_error(pairs)}


def _row_ll(p, y):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def paired_delta(rows, cand_fn, ref_fn):
    paired = [{"gameId": r["gameId"],
               "b": (cand_fn(r) - r["outcome"]) ** 2 - (ref_fn(r) - r["outcome"]) ** 2,
               "l": _row_ll(cand_fn(r), r["outcome"]) - _row_ll(ref_fn(r), r["outcome"])} for r in rows]

    def _mb(s):
        return sum(x["b"] for x in s) / len(s) if s else None

    def _ml(s):
        return sum(x["l"] for x in s) / len(s) if s else None

    b, l = _mb(paired), _ml(paired)
    blo, bhi, _ = game_clustered_bootstrap_ci(paired, _mb, cluster_key="gameId", seed=DEFAULT_BOOTSTRAP_SEED)
    return {"n": len(paired), "independentGames": independent_unit_count(paired, key="gameId"),
            "brierDelta": round(b, 6) if b is not None else None, "brierDeltaCI": {"low": blo, "high": bhi},
            "logLossDelta": round(l, 6) if l is not None else None,
            "interpretation": "negative == shrunk price beats the raw Kalshi fair price"}


def band_analysis(rows, beta, base):
    out = {}
    for lo, hi in PRICE_BANDS:
        band = [r for r in rows if lo <= r["marketFair"] < hi]
        n = len(band)
        if not n:
            out[f"{lo:.1f}_{hi:.1f}"] = {"n": 0}
            continue
        d = paired_delta(band, lambda r: shrunk_probability(r["marketFair"], beta, base), lambda r: r["marketFair"])
        out[f"{lo:.1f}_{hi:.1f}"] = {
            "n": n, "meanFair": round(sum(r["marketFair"] for r in band) / n, 4),
            "outcomeRate": round(sum(r["outcome"] for r in band) / n, 4),
            "marketBias": round(sum(r["marketFair"] - r["outcome"] for r in band) / n, 4),
            "shrunkBrierDelta": d["brierDelta"],
        }
    return out


def family_analysis(train_rows, val_rows, beta, base):
    out = {}
    for fam in sorted({r["family"] for r in train_rows} | {r["family"] for r in val_rows}):
        tr = [r for r in train_rows if r["family"] == fam]
        va = [r for r in val_rows if r["family"] == fam]
        if len(tr) < MIN_ROWS_FAMILY or independent_unit_count(tr, key="gameId") < MIN_GAMES_FAMILY:
            out[fam] = {"status": "BELOW_MINIMUM_SAMPLE", "trainN": len(tr), "valN": len(va)}
            continue
        fam_fit = fit_beta(tr, base)
        out[fam] = {
            "status": "FIT", "trainN": len(tr), "valN": len(va),
            "familyBetaTrain": fam_fit["beta"] if fam_fit else None,
            "trainMarketBias": round(sum(r["marketFair"] - r["outcome"] for r in tr) / len(tr), 4),
            "valMarketBias": round(sum(r["marketFair"] - r["outcome"] for r in va) / len(va), 4) if va else None,
            "valGlobalBetaBrierDelta": paired_delta(va, lambda r: shrunk_probability(r["marketFair"], beta, base), lambda r: r["marketFair"])["brierDelta"] if va else None,
        }
    return out


def date_stability(rows, beta, base):
    out = {}
    for d in sorted({r["settleDate"] for r in rows}):
        day = [r for r in rows if r["settleDate"] == d]
        if len(day) < 30:
            out[d] = {"n": len(day), "brierDelta": None}
            continue
        out[d] = {"n": len(day),
                  "brierDelta": paired_delta(day, lambda r: shrunk_probability(r["marketFair"], beta, base), lambda r: r["marketFair"])["brierDelta"]}
    return out


def secondary_economics(rows, beta, base):
    """SECONDARY, descriptive. Buys the side the shrunk price says is
    underpriced at the EXECUTABLE ask, $1/contract, canonical taker fee.
    Never optimized, never a selection criterion."""
    opps, gross, fees, wins = 0, 0.0, 0.0, 0
    for r in rows:
        p = shrunk_probability(r["marketFair"], beta, base)
        yes_price = min(max(r["executableAsk"], 0.01), 0.99)
        no_price = min(max(1 - r["yesBid"] / 100.0, 0.01), 0.99)
        if p > yes_price:
            price, won = yes_price, r["outcome"] == 1
        elif (1 - p) > no_price:
            price, won = no_price, r["outcome"] == 0
        else:
            continue
        opps += 1
        gross += (1 - price) if won else (-price)
        fees += taker_fee(1, price)
        wins += 1 if won else 0
    return {"opportunities": opps, "wins": wins,
            "winRate": round(wins / opps, 4) if opps else None,
            "grossPl": round(gross, 4), "fees": round(fees, 4), "netPl": round(gross - fees, 4),
            "netPlPerContract": round((gross - fees) / opps, 4) if opps else None,
            "note": "descriptive only -- executable prices, canonical taker fee; never optimized, never selection"}


def selection_passes(val_brier_delta, val_logloss_delta, val_ece_cand, val_ece_market, improving_bands, beta_ci):
    reasons = []
    if val_brier_delta is None or val_brier_delta >= 0:
        reasons.append(f"VAL Brier delta not negative: {val_brier_delta}")
    if val_logloss_delta is None or val_logloss_delta >= 0:
        reasons.append(f"VAL log-loss delta not negative: {val_logloss_delta}")
    if val_ece_cand is not None and val_ece_market is not None and val_ece_cand > val_ece_market:
        reasons.append(f"VAL calibration worse than raw market: {val_ece_cand} > {val_ece_market}")
    if improving_bands < MIN_IMPROVING_BANDS:
        reasons.append(f"improvement confined to fewer than {MIN_IMPROVING_BANDS} price bands: {improving_bands}")
    if beta_ci is None or beta_ci.get("low") is None or (beta_ci["low"] <= 1.0 <= beta_ci["high"]):
        reasons.append(f"beta confidence interval includes 1.0 (market unchanged): {beta_ci}")
    return (len(reasons) == 0), reasons


def main():
    print(f"[{EXPERIMENT_ID}] registering experiment/control...")
    control, definition = register_experiment()

    print(f"[{EXPERIMENT_ID}] building MARKET-ONLY corpus (production probability dropped)...")
    rows, excluded_no_fair, audit_total = build_market_only_rows()
    train = [r for r in rows if r["settleDate"] <= TRAIN_DATE_MAX]
    val = [r for r in rows if TRAIN_DATE_MAX < r["settleDate"] <= VAL_DATE_MAX]
    forward = [r for r in rows if r["settleDate"] > VAL_DATE_MAX]
    print(f"[{EXPERIMENT_ID}] rows={len(rows)} TRAIN={len(train)} ({independent_unit_count(train, key='gameId')} games) "
          f"VAL={len(val)} ({independent_unit_count(val, key='gameId')} games) FORWARD_available={len(forward)}")

    base = sum(r["outcome"] for r in train) / len(train)
    fit = fit_beta(train, base)
    beta = fit["beta"]
    beta_ci = beta_bootstrap_ci(train, base)
    print(f"[{EXPERIMENT_ID}] TRAIN base rate={base:.4f} fitted beta={beta} CI={beta_ci} "
          f"(nll@beta={fit['trainNll']} nll@1={fit['nllAtOne']})")

    def cand(r):
        return shrunk_probability(r["marketFair"], beta, base)

    def mkt(r):
        return r["marketFair"]

    train_market, train_cand = score(train, mkt), score(train, cand)
    val_market, val_cand = score(val, mkt), score(val, cand)
    val_delta = paired_delta(val, cand, mkt)
    train_delta = paired_delta(train, cand, mkt)
    print(f"[{EXPERIMENT_ID}] TRAIN brier {train_market['brier']} -> {train_cand['brier']} (delta {train_delta['brierDelta']})")
    print(f"[{EXPERIMENT_ID}] VAL   brier {val_market['brier']} -> {val_cand['brier']} (delta {val_delta['brierDelta']} CI {val_delta['brierDeltaCI']}) "
          f"logloss delta {val_delta['logLossDelta']} ece {val_market['ece']} -> {val_cand['ece']}")

    bands_val = band_analysis(val, beta, base)
    bands_train = band_analysis(train, beta, base)
    improving = sum(1 for b in bands_val.values() if b.get("shrunkBrierDelta") is not None and b["shrunkBrierDelta"] < 0)
    families = family_analysis(train, val, beta, base)
    stability = date_stability(val, beta, base)

    passes, reasons = selection_passes(val_delta["brierDelta"], val_delta["logLossDelta"],
                                       val_cand["ece"], val_market["ece"], improving, beta_ci)
    print(f"[{EXPERIMENT_ID}] improving VAL bands: {improving}/5")
    print(f"[{EXPERIMENT_ID}] SELECTION passes={passes} reasons={reasons}")

    economics = {"valAllRows": secondary_economics(val, beta, base)} if passes else {
        "note": "not computed -- candidate did not pass proper-scoring selection; economics never rescue a failed forecaster"}

    classification = "LEVEL_1_SHADOW_CANDIDATE_PENDING_FORWARD" if passes else "LEVEL_0_NO_VALIDATED_MARKET_INEFFICIENCY"

    frozen = {
        "candidateId": f"{EXPERIMENT_ID}-BETA-SHRINK",
        "form": "p = sigmoid(logit(base) + beta * (logit(kalshiFairMid) - logit(base)))",
        "beta": beta, "betaCI": beta_ci, "base": round(base, 6),
        "trainingEndDate": TRAIN_DATE_MAX, "validationEndDate": VAL_DATE_MAX,
        "probClamp": list(PROB_CLAMP), "betaBounds": list(BETA_BOUNDS),
        "marketFairDefinition": "midpoint of yesBid/yesAsk from the latest valid pregame observation per ticker, /100",
        "usesProductionModel": False, "version": "v1", "frozenAt": REGISTRATION_TIMESTAMP,
        "forwardEvaluationRule": (
            "Score settle-date > 2026-08-28 rows with this EXACT beta and base; never refit. Compare shrunk vs "
            "raw Kalshi fair on Brier/log loss with game-clustered CIs. LEVEL 2 requires that forward evidence."
        ),
        "rerunThresholdIfInconclusive": {
            "minForwardRows": 1000, "minForwardGames": 60,
            "note": "if this run is inconclusive, rerun automatically once the settled archive contains at least this much post-2026-08-28 data",
        },
        "classificationAtFreeze": classification,
    }

    report = {
        "experimentId": EXPERIMENT_ID, "controlModelId": control["controlModelId"],
        "independenceFromModel": "production modelP is dropped at corpus construction and never read by any fitting or scoring path",
        "corpus": {"rows": len(rows), "excludedNoFairPrice": excluded_no_fair, "auditTotal": audit_total,
                   "trainRows": len(train), "trainGames": independent_unit_count(train, key="gameId"),
                   "valRows": len(val), "valGames": independent_unit_count(val, key="gameId"),
                   "forwardRowsAvailable": len(forward),
                   "families": sorted({r["family"] for r in rows}),
                   "trainDates": sorted({r["settleDate"] for r in train}), "valDates": sorted({r["settleDate"] for r in val})},
        "baseRateTrain": round(base, 6), "fit": fit, "betaCI": beta_ci,
        "trainScores": {"market": train_market, "shrunk": train_cand, "pairedDelta": train_delta},
        "valScores": {"market": val_market, "shrunk": val_cand, "pairedDelta": val_delta},
        "priceBandsTrain": bands_train, "priceBandsVal": bands_val, "improvingValBands": improving,
        "familyAnalysis": families, "dateStabilityVal": stability,
        "selection": {"passes": passes, "reasons": reasons},
        "secondaryEconomics": economics,
        "classification": classification, "frozenForwardModel": frozen,
        "governance": {"productionChanged": False, "usesProductionModelProbability": False,
                       "noRoiSelection": True, "noNewApiCalls": True,
                       "maxDisposition": "LEVEL_1_SHADOW_CANDIDATE",
                       "calibrationBiasIsNotProfit": True},
    }

    out_path = os.path.join("data", "edgelab", "analytics", "latest_mlb_rsch_0026_kalshi_internal_efficiency.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    frozen_path = os.path.join("data", "edgelab", "analytics", "frozen_mlb_rsch_0026_forward_model.json")
    with open(frozen_path, "w") as f:
        json.dump(frozen, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    print(f"[{EXPERIMENT_ID}] wrote {out_path} and {frozen_path}")
    print(f"[{EXPERIMENT_ID}] classification={classification}")
    return report


if __name__ == "__main__":
    main()
