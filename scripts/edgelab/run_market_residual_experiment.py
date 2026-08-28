#!/usr/bin/env python3
"""
scripts/edgelab/run_market_residual_experiment.py
====================================================================
Research Lab experiment MLB-RSCH-0024: "Market-Anchored Residual
Model". RESEARCH ONLY. NO production changes, no staking/execution/fee
changes, no candidate activation.

CORE QUESTION: when production and Kalshi disagree, does the production
baseball model contain INCREMENTAL predictive information beyond
Kalshi's own contemporaneous fair probability?

FORM (parsimonious, logit-space):
    m = logit(kalshi fair prob)
    r = logit(model prob) - m
    p_candidate = sigmoid(m + alpha * r)
  alpha=0 -> Kalshi alone (M0); alpha=1 -> production as-is (M1);
  0<alpha<1 -> production carries incremental info but should be shrunk
  toward the market; alpha<0 -> production disagreement is an
  ANTI-signal. Alpha is fit on TRAIN only, bounded to [-2, 3]
  (preregistered, generous enough to admit alpha>1 or alpha<0 while
  preventing pathological fits), by Bernoulli NLL (log loss).

THE PRIMARY TEST IS M2 vs M0, NOT M2 vs M1. MLB-RSCH-0022 showed M1 is
currently the weaker standalone forecaster, so beating it is easy and
uninformative. Only beating the MARKET demonstrates incremental
information.

CANONICAL FAIR PRICE -- a correction this experiment had to make:
MLB-RSCH-0022 used each row's archived `marketImpliedProbability`, whose
`probabilityAdapter` is `kalshiVF` (vig-free mid-derived: correct) on
some rows but `executableMarketProb` (an ASK price) on others. Measured
directly here: ask-adapter rows carry +0.049 mean upward bias vs +0.013
for vig-free rows -- pooling them would corrupt a market benchmark. So
this experiment RECONSTRUCTS a canonical fair probability from the
observation archive's own `yesBid`/`yesAsk` (latest VALID PREGAME
observation per ticker, `isValidPregameObservation` and NOT
`gameStartedAtCapture`), fair = midpoint((yesBid+yesAsk)/2)/100. Bid,
ask, midpoint and the executable side price are all retained
separately; the executable price is used ONLY in the secondary
fee-aware economics, never as a truth probability.

CHRONOLOGICAL DESIGN (audited first, then locked -- explicitly designed
to avoid MLB-RSCH-0023's failure mode, where a DEV window missing major
families produced a fit that could not transport):
  Full 9-family capture begins 2026-08-23. Both halves below therefore
  contain ALL NINE eligible families.
    TRAIN: settle <= 2026-08-24   (1,454 rows / 179 games / 9 families)
    VAL:   2026-08-25 .. 08-28    (1,181 rows /  56 games / 9 families)
    FORWARD: settle > 2026-08-28  -- untouched, frozen-model evaluation
             in a future session (a deterministic forward evaluator and
             a frozen-parameter artifact are emitted by this run).

ELIGIBILITY / SEMANTICS: binary YES-outcome contracts only. Model and
market probabilities must refer to the SAME YES outcome -- both are
taken from the same archived ModelEvaluation row (production's own
aligned pairing) and the settlement outcome is that same ticker's YES/NO
result, so alignment is structural rather than re-derived here. Hitter
prop families are absent from the evaluated+settled join and are not
forced in. Three-way/tie structures are not binarized.

MAX CLASSIFICATION: LEVEL 1 (shadow candidate) -- LEVEL 2 requires the
untouched FORWARD window under the frozen model.
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

from lib.edgelab.storage import read_records
from lib.edgelab import experiment_registry as reg
from lib.edgelab import evidence_levels as ev
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab.kalshi_fees import taker_fee
from lib.edgelab.research_stats import (
    DEFAULT_BOOTSTRAP_SEED,
    independent_unit_count,
    sample_size_status,
    expected_calibration_error,
    brier_and_log_loss_summary,
    game_clustered_bootstrap_ci,
)

import run_production_calibration_audit_experiment as rsch0022  # noqa: E402 -- loaders reused unchanged

EXPERIMENT_ID = "MLB-RSCH-0024"
REGISTRATION_TIMESTAMP = "2026-08-28T20:30:00Z"

OBSERVATIONS_DIR = os.path.join(_ROOT, "data", "edgelab", "observations")

TRAIN_DATE_MAX = "2026-08-24"
VAL_DATE_MAX = "2026-08-28"
# FORWARD: settle > VAL_DATE_MAX -- preregistered, never computed here.

ALPHA_BOUNDS = (-2.0, 3.0)      # preregistered, admits anti-signal and >production weighting
ALPHA_GRID_STEPS = 51           # deterministic coarse grid, then golden-section refine (exact same optimum,
                                # far cheaper -- the NLL in alpha is smooth and unimodal over these bounds)
PROB_CLAMP = (0.01, 0.99)

MIN_ROWS_TIER = 150             # preregistered minimum for a tier alpha
MIN_GAMES_TIER = 25
MIN_ROWS_FAMILY = 100           # preregistered minimum for exploratory family alpha
MIN_GAMES_FAMILY = 20

# Preregistered tiers (structural/economic kinship, fixed before fitting).
TIERS = {
    "TIER_GAME_OUTCOME": ("game_result",),
    "TIER_TOTALS": ("game_total", "team_total", "inning_total"),
    "TIER_INNING": ("inning_result", "first_inning_run"),
    "TIER_MARGIN": ("winning_margin",),
    "TIER_PROPS": ("pitcher_strikeouts", "pitcher_outs"),
}

# Fixed disagreement buckets in probability points (|model - market|).
DISAGREEMENT_BUCKETS = ((0.0, 0.025), (0.025, 0.05), (0.05, 0.075), (0.075, 0.10), (0.10, 1.01))
PRICE_BANDS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0))
FDR_ALPHA = 0.10


def tier_for_family(family):
    for tier, fams in TIERS.items():
        if family in fams:
            return tier
    return None


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
        name="mlb_rsch_0024_market_residual_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0024 market-anchored residual v1: M0 = Kalshi vig-free fair midpoint "
                        "(reconstructed from archived yesBid/yesAsk), M1 = archived production probability, "
                        "M2 = sigmoid(logit(M0) + alpha * (logit(M1) - logit(M0))) with alpha fit on TRAIN "
                        "(settle <= 2026-08-24) by Bernoulli NLL, bounded [-2, 3]. Primary test M2 vs M0."
        ),
        probability_adapter_identity="Kalshi vig-free fair midpoint reconstructed from archived yesBid/yesAsk (latest valid pregame observation per ticker) + production's own archived modelFairProbability -- both read-only",
        model_engine_family="market_anchored_residual_v1",
        required_input_provenance=[
            "model_evaluation_probability_pipeline_derived",
            "model_evaluation_probability_prospective_snapshot",
            "archived_kalshi_market_observation",
            "settlement_outcome",
        ],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "Tests whether production's baseball model carries incremental predictive information beyond "
            "Kalshi's own contemporaneous vig-free fair probability, via a one-parameter logit-space "
            "residual weight fit on a family-balanced TRAIN window. The decisive comparison is candidate "
            "vs MARKET, not candidate vs production."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Market-Anchored Residual Model",
        hypothesis=(
            "H1: at least one market/tier shows alpha > 0 with out-of-time improvement over Kalshi alone. "
            "H2: residual information differs materially by market family. H3: families where MLB-RSCH-0022 "
            "showed the smallest model-vs-market gap (notably first_inning_run) are likelier to retain "
            "positive incremental information. H4: large raw model-market disagreement is NOT automatically "
            "more informative and may correspond to smaller or negative alpha."
        ),
        research_question=(
            "When production and Kalshi disagree, does the production model contain incremental information "
            "beyond Kalshi's contemporaneous fair probability -- and if so, how much and where?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E4_PROSPECTIVE_SHADOW,
        target_population=(
            "Every archived EVALUATED production row (last per ticker) whose ticker BOTH settled YES/NO and "
            "has a reconstructable vig-free fair midpoint from a valid pregame observation: ~2,635 tickers / "
            "~235 games / 9 binary families, 2026-08-04 .. 2026-08-26."
        ),
        market_families=["game_result", "game_total", "team_total", "run_margin", "inning_markets", "pitcher_props"],
        eligibility_criteria=[
            "binary YES-outcome contract with a YES/NO settlement",
            "a valid pregame observation (isValidPregameObservation and not gameStartedAtCapture) carrying BOTH yesBid and yesAsk",
            "model and market probabilities taken from the SAME archived ModelEvaluation row (structural YES-alignment, never re-derived)",
        ],
        exclusion_criteria=[
            "executable ask price as a truth probability -- measured +0.049 upward bias; used only in secondary economics",
            "hitter prop families -- absent from the evaluated+settled join; never forced in",
            "three-way/tie structures binarized -- excluded rather than coerced",
            "any fit to realized ROI, any post-hoc threshold or cutoff optimization",
            "any use of FORWARD (settle > 2026-08-28) data",
        ],
        prediction_checkpoints=["ARCHIVED_PRODUCTION_PREGAME"],
        primary_metric="paired Brier and log-loss delta of M2 (market-anchored residual) MINUS M0 (Kalshi fair), on VALIDATION, game-clustered bootstrap CI",
        secondary_metrics=[
            "fitted alpha with uncertainty (TRAIN)", "M1 - M0 reference deltas",
            "tier alphas (preregistered tiers, minimum sample enforced)",
            "exploratory family alphas with BH-FDR", "fixed disagreement-bucket and price-band breakdowns",
            "directional (model>market vs model<market) analysis", "input-quality interaction",
            "Kalshi's own structural calibration (distinct from model signal)",
            "SECONDARY fee-aware executable economics; capacity-only bet-filter simulation",
        ],
        chronological_split_policy=(
            f"DATE_BASED, family-balanced by design: TRAIN = settle <= {TRAIN_DATE_MAX}, VAL = "
            f"({TRAIN_DATE_MAX}, {VAL_DATE_MAX}], FORWARD = settle > {VAL_DATE_MAX} (untouched). Full "
            "nine-family capture begins 2026-08-23, so BOTH halves contain all nine eligible families -- "
            "explicitly preventing the family-composition shift that invalidated MLB-RSCH-0023."
        ),
        minimum_sample_requirement={"independentGames": MIN_GAMES_TIER},
        clustering_unit="gameId",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={
            "model_evaluation_probability_pipeline_derived": "PREDICTIVE_INPUT",
            "model_evaluation_probability_prospective_snapshot": "PREDICTIVE_INPUT",
            "archived_kalshi_market_observation": "PREDICTIVE_INPUT",
            "settlement_outcome": "EVALUATION_TARGET",
        },
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            "evidenceLevel E4_PROSPECTIVE_SHADOW (prospectively captured model and market inputs). The "
            "decisive comparison is M2 vs M0 (market), never M2 vs M1. Alpha bounds [-2, 3] preregistered. "
            "MAXIMUM classification: LEVEL 1 SHADOW CANDIDATE; LEVEL 2 requires the untouched FORWARD "
            "window scored under the frozen alpha artifact this run emits. Never wired into production."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Canonical fair-price reconstruction (vig-free midpoint) ───────────────

def load_pregame_fair_prices():
    """{marketTicker: {"yesBid","yesAsk","fairMid","executableAsk","capturedAt","spreadCents"}}
    from the LATEST VALID PREGAME observation per ticker carrying both
    sides. `fairMid` is the vig-free midpoint benchmark; `executableAsk`
    is retained separately and used ONLY in secondary economics."""
    best = {}
    for fn in sorted(os.listdir(OBSERVATIONS_DIR)):
        if not (fn.endswith(".jsonl") or fn.endswith(".jsonl.gz")):
            continue
        for d in read_records(os.path.join(OBSERVATIONS_DIR, fn)):
            if not d.get("isValidPregameObservation") or d.get("gameStartedAtCapture"):
                continue
            yes_bid, yes_ask = d.get("yesBid"), d.get("yesAsk")
            if yes_bid is None or yes_ask is None:
                continue
            ticker, captured = d.get("marketTicker"), d.get("capturedAt") or ""
            if not ticker:
                continue
            if ticker not in best or captured > best[ticker]["capturedAt"]:
                best[ticker] = {
                    "yesBid": yes_bid, "yesAsk": yes_ask,
                    "fairMid": round(((yes_bid + yes_ask) / 2.0) / 100.0, 6),
                    "executableAsk": round(yes_ask / 100.0, 6),
                    "capturedAt": captured, "spreadCents": d.get("spreadCents"),
                }
    return best


def build_rows():
    """Audit rows (MLB-RSCH-0022 loaders, unchanged) joined to the
    reconstructed vig-free fair price. Rows without a reconstructable
    fair price are excluded and counted -- never imputed."""
    outcomes, _ = rsch0022.load_settled_outcomes()
    evaluated, _ = rsch0022.load_evaluated_rows()
    audit = rsch0022.build_audit_rows(evaluated, outcomes, pick="last")
    fair = load_pregame_fair_prices()
    rows, excluded_no_fair = [], 0
    for r in audit:
        f = fair.get(r["marketTicker"])
        if f is None:
            excluded_no_fair += 1
            continue
        if tier_for_family(r["family"]) is None:
            continue  # family outside the preregistered eligible tier map
        rows.append(dict(r, marketFair=f["fairMid"], yesBid=f["yesBid"], yesAsk=f["yesAsk"],
                         executableAsk=f["executableAsk"], spreadCents=f["spreadCents"]))
    return rows, excluded_no_fair, len(audit)


# ── Residual model ────────────────────────────────────────────────────────

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


def residual_probability(model_p, market_p, alpha):
    m = _logit(market_p)
    r = _logit(model_p) - m
    return _sigmoid(m + alpha * r)


def _nll(rows, alpha):
    total = 0.0
    for r in rows:
        p = min(max(residual_probability(r["modelP"], r["marketFair"], alpha), 1e-9), 1 - 1e-9)
        y = r["outcome"]
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(rows)


def fit_alpha(rows):
    """Deterministic bounded 1-D minimization of Bernoulli NLL: a coarse
    grid over the preregistered ALPHA_BOUNDS followed by a local golden-
    section refine. No randomness, no optimizer library, fully
    reproducible."""
    if len(rows) < 30:
        return None
    lo, hi = ALPHA_BOUNDS
    best_alpha, best_loss = None, None
    for i in range(ALPHA_GRID_STEPS):
        a = lo + (hi - lo) * i / (ALPHA_GRID_STEPS - 1)
        loss = _nll(rows, a)
        if best_loss is None or loss < best_loss:
            best_alpha, best_loss = a, loss
    step = (hi - lo) / (ALPHA_GRID_STEPS - 1)
    left, right = max(lo, best_alpha - step), min(hi, best_alpha + step)
    for _ in range(40):
        mid1 = left + (right - left) * 0.382
        mid2 = left + (right - left) * 0.618
        if _nll(rows, mid1) < _nll(rows, mid2):
            right = mid2
        else:
            left = mid1
    alpha = (left + right) / 2
    return {"alpha": round(alpha, 4), "trainNll": round(_nll(rows, alpha), 6), "n": len(rows),
            "nllAtZero": round(_nll(rows, 0.0), 6), "nllAtOne": round(_nll(rows, 1.0), 6)}


def alpha_bootstrap_ci(rows, seed=DEFAULT_BOOTSTRAP_SEED, n_resamples=200):
    """Game-clustered bootstrap CI for the fitted alpha (fewer resamples
    than the metric bootstraps -- each one is a full refit; deterministic
    seed, disclosed count)."""
    import random
    from collections import defaultdict
    by_game = defaultdict(list)
    for r in rows:
        by_game[r["gameId"]].append(r)
    games = sorted(by_game.keys(), key=str)
    if not games:
        return {"low": None, "high": None}
    rng = random.Random(seed)
    estimates = []
    for _ in range(n_resamples):
        sampled = [rng.choice(games) for _ in games]
        resampled = [row for g in sampled for row in by_game[g]]
        fit = fit_alpha(resampled)
        if fit:
            estimates.append(fit["alpha"])
    if not estimates:
        return {"low": None, "high": None}
    estimates.sort()
    lo_i = max(0, round(0.05 * (len(estimates) - 1)))
    hi_i = min(len(estimates) - 1, round(0.95 * (len(estimates) - 1)))
    return {"low": round(estimates[lo_i], 4), "high": round(estimates[hi_i], 4),
            "method": "GAME_CLUSTERED_BOOTSTRAP", "resamples": n_resamples}


# ── Scoring ───────────────────────────────────────────────────────────────

def score_forecaster(rows, prob_fn):
    pairs = [(prob_fn(r), r["outcome"]) for r in rows]
    brier, log_loss = brier_and_log_loss_summary(pairs)
    return {"n": len(rows), "independentGames": independent_unit_count(rows, key="gameId"),
            "brier": brier, "logLoss": log_loss, "ece": expected_calibration_error(pairs)}


def paired_delta(rows, prob_fn_candidate, prob_fn_reference):
    paired = [{"gameId": r["gameId"],
               "b": (prob_fn_candidate(r) - r["outcome"]) ** 2 - (prob_fn_reference(r) - r["outcome"]) ** 2,
               "l": _row_logloss(prob_fn_candidate(r), r["outcome"]) - _row_logloss(prob_fn_reference(r), r["outcome"])}
              for r in rows]

    def _mb(subset):
        return sum(x["b"] for x in subset) / len(subset) if subset else None

    def _ml(subset):
        return sum(x["l"] for x in subset) / len(subset) if subset else None

    b_point, l_point = _mb(paired), _ml(paired)
    b_lo, b_hi, _ = game_clustered_bootstrap_ci(paired, _mb, cluster_key="gameId", seed=DEFAULT_BOOTSTRAP_SEED)
    l_lo, l_hi, _ = game_clustered_bootstrap_ci(paired, _ml, cluster_key="gameId", seed=DEFAULT_BOOTSTRAP_SEED)
    return {"n": len(paired), "independentGames": independent_unit_count(paired, key="gameId"),
            "brierDelta": round(b_point, 6) if b_point is not None else None,
            "brierDeltaCI": {"low": b_lo, "high": b_hi},
            "logLossDelta": round(l_point, 6) if l_point is not None else None,
            "logLossDeltaCI": {"low": l_lo, "high": l_hi},
            "interpretation": "negative == candidate better than reference"}


def _row_logloss(p, y):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _market_fn(r):
    return r["marketFair"]


def _model_fn(r):
    return r["modelP"]


def _residual_fn(alpha):
    def fn(r):
        return residual_probability(r["modelP"], r["marketFair"], alpha)
    return fn


def benjamini_hochberg(pvalues_by_key, alpha=FDR_ALPHA):
    items = sorted(((k, p) for k, p in pvalues_by_key.items() if p is not None), key=lambda kv: kv[1])
    m = len(items)
    max_i = 0
    for i, (_, p) in enumerate(items, start=1):
        if p <= alpha * i / m:
            max_i = i
    return {k: (i <= max_i) for i, (k, _) in enumerate(items, start=1)}


def _alpha_pvalue(rows, seed=DEFAULT_BOOTSTRAP_SEED, n_resamples=200):
    """Two-sided bootstrap sign p-value for alpha != 0."""
    import random
    from collections import defaultdict
    by_game = defaultdict(list)
    for r in rows:
        by_game[r["gameId"]].append(r)
    games = sorted(by_game.keys(), key=str)
    if not games:
        return None
    rng = random.Random(seed)
    estimates = []
    for _ in range(n_resamples):
        sampled = [rng.choice(games) for _ in games]
        fit = fit_alpha([row for g in sampled for row in by_game[g]])
        if fit:
            estimates.append(fit["alpha"])
    if not estimates:
        return None
    frac_pos = sum(1 for e in estimates if e > 0) / len(estimates)
    return round(min(1.0, 2 * min(frac_pos, 1 - frac_pos)), 4)


# ── Breakdowns ────────────────────────────────────────────────────────────

def disagreement_bucket_analysis(train_rows, val_rows, global_alpha):
    out = {}
    for lo, hi in DISAGREEMENT_BUCKETS:
        key = f"{lo:.3f}_{hi:.3f}"
        tr = [r for r in train_rows if lo <= abs(r["modelP"] - r["marketFair"]) < hi]
        va = [r for r in val_rows if lo <= abs(r["modelP"] - r["marketFair"]) < hi]
        fit = fit_alpha(tr) if len(tr) >= MIN_ROWS_FAMILY else None
        out[key] = {
            "trainN": len(tr), "valN": len(va),
            "bucketAlphaTrain": fit["alpha"] if fit else None,
            "valDeltaVsMarketGlobalAlpha": paired_delta(va, _residual_fn(global_alpha), _market_fn)["brierDelta"] if va else None,
            "valModelVsMarket": paired_delta(va, _model_fn, _market_fn)["brierDelta"] if va else None,
        }
    return out


def directional_analysis(train_rows, val_rows, global_alpha):
    out = {}
    for label, pred in (("MODEL_ABOVE_MARKET", lambda r: r["modelP"] > r["marketFair"]),
                        ("MODEL_BELOW_MARKET", lambda r: r["modelP"] < r["marketFair"])):
        tr = [r for r in train_rows if pred(r)]
        va = [r for r in val_rows if pred(r)]
        fit = fit_alpha(tr) if len(tr) >= MIN_ROWS_FAMILY else None
        out[label] = {
            "trainN": len(tr), "valN": len(va),
            "alphaTrain": fit["alpha"] if fit else None,
            "valDeltaVsMarket": paired_delta(va, _residual_fn(global_alpha), _market_fn)["brierDelta"] if va else None,
        }
    return out


def input_quality_analysis(train_rows, val_rows, global_alpha):
    """Preregistered SECONDARY interaction (from MLB-RSCH-0022's finding
    that complete-input rows had roughly half the model-vs-market gap).
    Categories are the archived values themselves -- no new category is
    defined after seeing results."""
    def is_high(r):
        return (r.get("dataQuality") == "full") or (r.get("lineupConfirmationState") == "CONFIRMED")

    out = {}
    for label, pred in (("HIGH_QUALITY_INPUT", is_high), ("LOWER_OR_UNKNOWN_INPUT", lambda r: not is_high(r))):
        tr = [r for r in train_rows if pred(r)]
        va = [r for r in val_rows if pred(r)]
        fit = fit_alpha(tr) if len(tr) >= MIN_ROWS_FAMILY else None
        out[label] = {
            "trainN": len(tr), "valN": len(va), "alphaTrain": fit["alpha"] if fit else None,
            "valDeltaVsMarket": paired_delta(va, _residual_fn(global_alpha), _market_fn)["brierDelta"] if va else None,
            "valModelVsMarket": paired_delta(va, _model_fn, _market_fn)["brierDelta"] if va else None,
        }
    return out


def price_band_analysis(rows, global_alpha):
    out = {}
    for lo, hi in PRICE_BANDS:
        band = [r for r in rows if lo <= r["marketFair"] < hi]
        n = len(band)
        out[f"{lo:.1f}_{hi:.1f}"] = {
            "n": n,
            "meanMarketFair": round(sum(r["marketFair"] for r in band) / n, 4) if n else None,
            "outcomeRate": round(sum(r["outcome"] for r in band) / n, 4) if n else None,
            "marketBias": round(sum(r["marketFair"] - r["outcome"] for r in band) / n, 4) if n else None,
            "residualVsMarketBrierDelta": paired_delta(band, _residual_fn(global_alpha), _market_fn)["brierDelta"] if n >= 30 else None,
        }
    return out


def market_structural_calibration(rows):
    """Kalshi's OWN calibration -- reported separately so market
    mispricing is never confused with model incremental signal."""
    pairs = [(r["marketFair"], r["outcome"]) for r in rows]
    brier, log_loss = brier_and_log_loss_summary(pairs)
    return {"n": len(rows), "brier": brier, "logLoss": log_loss, "ece": expected_calibration_error(pairs),
            "meanFair": round(sum(r["marketFair"] for r in rows) / len(rows), 4) if rows else None,
            "outcomeRate": round(sum(r["outcome"] for r in rows) / len(rows), 4) if rows else None}


def secondary_economics(rows, alpha):
    """SECONDARY, descriptive. Uses the EXECUTABLE ask price and the
    canonical taker-fee engine. Positive-EV-under-M2 rows only, at a
    fixed $1/contract convention. No threshold is optimized; ROI never
    selects anything."""
    opportunities, gross, fees, wins = 0, 0.0, 0.0, 0
    for r in rows:
        p = residual_probability(r["modelP"], r["marketFair"], alpha)
        price = min(max(r["executableAsk"], 0.01), 0.99)
        if p <= price:
            continue  # no positive expected value at the executable price
        opportunities += 1
        won = r["outcome"] == 1
        gross += (1 - price) if won else (-price)
        fees += taker_fee(1, price)
        wins += 1 if won else 0
    net = gross - fees
    return {"opportunities": opportunities, "wins": wins,
            "winRate": round(wins / opportunities, 4) if opportunities else None,
            "avgExecutablePrice": round(sum(min(max(r["executableAsk"], 0.01), 0.99) for r in rows) / len(rows), 4) if rows else None,
            "grossPl": round(gross, 4), "fees": round(fees, 4), "netPl": round(net, 4),
            "roi": round(net / opportunities, 4) if opportunities else None,
            "note": "descriptive only -- executable ask, taker fee, $1/contract; never optimized, never a selection criterion"}


def selection_passes(val_delta_vs_market, alpha_ci, family_concentration_ok, val_ece_residual, val_ece_market):
    """LEVEL 1 requires: beats the MARKET out-of-time on Brier; alpha CI
    excludes 0; not driven by a single family; calibration not materially
    worse than the market's."""
    reasons = []
    if val_delta_vs_market is None or val_delta_vs_market >= 0:
        reasons.append(f"VALIDATION Brier delta vs MARKET not negative: {val_delta_vs_market}")
    if alpha_ci is None or alpha_ci.get("low") is None or (alpha_ci["low"] <= 0 <= alpha_ci["high"]):
        reasons.append(f"alpha confidence interval includes 0: {alpha_ci}")
    if not family_concentration_ok:
        reasons.append("improvement concentrated in a single family")
    if val_ece_residual is not None and val_ece_market is not None and val_ece_residual > val_ece_market * 1.5:
        reasons.append(f"calibration materially worse than market: {val_ece_residual} vs {val_ece_market}")
    return (len(reasons) == 0), reasons


def main():
    print(f"[{EXPERIMENT_ID}] registering experiment/control...")
    control, definition = register_experiment()

    print(f"[{EXPERIMENT_ID}] building rows (RSCH-0022 loaders + reconstructed vig-free fair price)...")
    rows, excluded_no_fair, audit_total = build_rows()
    train = [r for r in rows if r["settleDate"] <= TRAIN_DATE_MAX]
    val = [r for r in rows if TRAIN_DATE_MAX < r["settleDate"] <= VAL_DATE_MAX]
    print(f"[{EXPERIMENT_ID}] eligible={len(rows)} (auditTotal={audit_total}, excludedNoFairPrice={excluded_no_fair})")
    print(f"[{EXPERIMENT_ID}] TRAIN={len(train)} ({independent_unit_count(train, key='gameId')} games) VAL={len(val)} ({independent_unit_count(val, key='gameId')} games)")

    # ---- Baselines on identical rows ----
    baselines = {
        "trainMarketM0": score_forecaster(train, _market_fn), "trainProductionM1": score_forecaster(train, _model_fn),
        "valMarketM0": score_forecaster(val, _market_fn), "valProductionM1": score_forecaster(val, _model_fn),
    }
    val_m1_vs_m0 = paired_delta(val, _model_fn, _market_fn)
    print(f"[{EXPERIMENT_ID}] VAL M1-M0 (reference): brier={val_m1_vs_m0['brierDelta']} logloss={val_m1_vs_m0['logLossDelta']}")

    # ---- R0: global alpha ----
    global_fit = fit_alpha(train)
    global_alpha = global_fit["alpha"] if global_fit else 0.0
    global_alpha_ci = alpha_bootstrap_ci(train)
    print(f"[{EXPERIMENT_ID}] GLOBAL alpha (TRAIN-fit) = {global_alpha} CI={global_alpha_ci} (nll@0={global_fit['nllAtZero']} nll@1={global_fit['nllAtOne']} nll@alpha={global_fit['trainNll']})")

    val_m2_vs_m0 = paired_delta(val, _residual_fn(global_alpha), _market_fn)
    val_m2_vs_m1 = paired_delta(val, _residual_fn(global_alpha), _model_fn)
    val_m2_score = score_forecaster(val, _residual_fn(global_alpha))
    print(f"[{EXPERIMENT_ID}] VAL M2-M0 (PRIMARY): brier={val_m2_vs_m0['brierDelta']} CI={val_m2_vs_m0['brierDeltaCI']} logloss={val_m2_vs_m0['logLossDelta']}")

    # ---- R1: preregistered tiers ----
    tier_results = {}
    for tier, fams in TIERS.items():
        tr = [r for r in train if r["family"] in fams]
        va = [r for r in val if r["family"] in fams]
        if len(tr) < MIN_ROWS_TIER or independent_unit_count(tr, key="gameId") < MIN_GAMES_TIER:
            tier_results[tier] = {"status": "BELOW_MINIMUM_SAMPLE", "trainN": len(tr), "trainGames": independent_unit_count(tr, key="gameId")}
            continue
        fit = fit_alpha(tr)
        tier_results[tier] = {
            "status": "FIT", "trainN": len(tr), "valN": len(va), "alpha": fit["alpha"],
            "alphaCI": alpha_bootstrap_ci(tr),
            "valDeltaVsMarket": paired_delta(va, _residual_fn(fit["alpha"]), _market_fn) if va else None,
        }
        print(f"[{EXPERIMENT_ID}] {tier}: alpha={fit['alpha']} valDeltaVsMarket={tier_results[tier]['valDeltaVsMarket']['brierDelta'] if va else None}")

    # ---- R2: exploratory family alphas (BH-FDR) ----
    family_results, family_pvals = {}, {}
    for family in sorted({r["family"] for r in rows}):
        tr = [r for r in train if r["family"] == family]
        va = [r for r in val if r["family"] == family]
        if len(tr) < MIN_ROWS_FAMILY or independent_unit_count(tr, key="gameId") < MIN_GAMES_FAMILY:
            family_results[family] = {"status": "BELOW_MINIMUM_SAMPLE", "trainN": len(tr)}
            continue
        fit = fit_alpha(tr)
        p = _alpha_pvalue(tr)
        family_results[family] = {
            "status": "EXPLORATORY_FIT", "trainN": len(tr), "valN": len(va), "alpha": fit["alpha"],
            "alphaCI": alpha_bootstrap_ci(tr), "pTwoSided": p,
            "valDeltaVsMarket": paired_delta(va, _residual_fn(fit["alpha"]), _market_fn) if va else None,
            "valModelVsMarket": paired_delta(va, _model_fn, _market_fn) if va else None,
        }
        family_pvals[family] = p
    fdr = benjamini_hochberg(family_pvals) if family_pvals else {}
    for fam, sig in fdr.items():
        family_results[fam]["fdrSignificantAt10pct"] = sig
    print(f"[{EXPERIMENT_ID}] family alphas: { {f: v.get('alpha') for f, v in family_results.items() if v.get('alpha') is not None} }")

    # ---- Breakdowns ----
    disagreement = disagreement_bucket_analysis(train, val, global_alpha)
    directional = directional_analysis(train, val, global_alpha)
    input_quality = input_quality_analysis(train, val, global_alpha)
    price_bands = price_band_analysis(val, global_alpha)
    market_calibration = {"train": market_structural_calibration(train), "val": market_structural_calibration(val)}

    # ---- Selection ----
    fam_deltas = {f: v["valDeltaVsMarket"]["brierDelta"] for f, v in family_results.items()
                  if v.get("valDeltaVsMarket") and v["valDeltaVsMarket"].get("brierDelta") is not None}
    improving_families = [f for f, d in fam_deltas.items() if d < 0]
    family_concentration_ok = len(improving_families) >= 2
    passes, reasons = selection_passes(
        val_m2_vs_m0["brierDelta"], global_alpha_ci, family_concentration_ok,
        val_m2_score["ece"], baselines["valMarketM0"]["ece"],
    )
    print(f"[{EXPERIMENT_ID}] SELECTION: passes={passes} reasons={reasons}")

    classification = "LEVEL_1_SHADOW_CANDIDATE" if passes else "LEVEL_0_NO_INCREMENTAL_SIGNAL_DEMONSTRATED"

    # ---- Frozen forward-model specification (always emitted) ----
    frozen_spec = {
        "candidateId": f"{EXPERIMENT_ID}-R0-GLOBAL-ALPHA",
        "form": "p = sigmoid(logit(marketFairMid) + alpha * (logit(modelFairProbability) - logit(marketFairMid)))",
        "alpha": global_alpha, "alphaCI": global_alpha_ci,
        "trainingEndDate": TRAIN_DATE_MAX, "validationEndDate": VAL_DATE_MAX,
        "eligibleFamilies": sorted({r["family"] for r in rows}),
        "tierMapping": {t: list(f) for t, f in TIERS.items()},
        "probClamp": list(PROB_CLAMP), "alphaBounds": list(ALPHA_BOUNDS),
        "marketFairDefinition": "midpoint of yesBid/yesAsk from the latest valid pregame observation per ticker, /100",
        "version": "v1", "frozenAt": REGISTRATION_TIMESTAMP,
        "forwardEvaluationRule": (
            "Score settle-date > 2026-08-28 rows with this EXACT alpha and mapping; never refit. Compare "
            "M2 vs M0 on Brier/log loss with game-clustered CIs. LEVEL 2 requires this forward evidence."
        ),
        "classificationAtFreeze": classification,
    }

    # ---- Secondary economics + capacity (descriptive only) ----
    economics = {
        "valAllRows": secondary_economics(val, global_alpha),
        "valHighQualityInputOnly": secondary_economics([r for r in val if (r.get("dataQuality") == "full") or (r.get("lineupConfirmationState") == "CONFIRMED")], global_alpha),
    }
    capacity = {
        "valRows": len(val), "valGames": independent_unit_count(val, key="gameId"),
        "positiveEvUnderM2": economics["valAllRows"]["opportunities"],
        "note": "capacity only -- NOT an authorization to bet, no threshold optimized",
    }

    report = {
        "experimentId": EXPERIMENT_ID, "controlModelId": control["controlModelId"],
        "corpus": {
            "auditRowsTotal": audit_total, "eligibleRows": len(rows), "excludedNoFairPrice": excluded_no_fair,
            "trainRows": len(train), "trainGames": independent_unit_count(train, key="gameId"),
            "valRows": len(val), "valGames": independent_unit_count(val, key="gameId"),
            "families": sorted({r["family"] for r in rows}),
            "trainDates": sorted({r["settleDate"] for r in train}), "valDates": sorted({r["settleDate"] for r in val}),
            "tickers": len({r["marketTicker"] for r in rows}),
            "forward": f"settle > {VAL_DATE_MAX} -- untouched, frozen-model evaluation only",
        },
        "fairPriceCorrection": (
            "Reconstructed vig-free midpoint from archived yesBid/yesAsk. MLB-RSCH-0022's own "
            "marketImpliedProbability mixed kalshiVF (vig-free) with executableMarketProb (ask, measured "
            "+0.049 upward bias); pooling those would corrupt a market benchmark."
        ),
        "baselines": baselines, "valM1vsM0Reference": val_m1_vs_m0,
        "globalResidual": {"fit": global_fit, "alphaCI": global_alpha_ci, "valScore": val_m2_score,
                           "valM2vsM0Primary": val_m2_vs_m0, "valM2vsM1Reference": val_m2_vs_m1},
        "tierResults": tier_results, "familyResults": family_results,
        "disagreementBuckets": disagreement, "directional": directional,
        "inputQuality": input_quality, "priceBands": price_bands,
        "marketStructuralCalibration": market_calibration,
        "selection": {"passes": passes, "reasons": reasons, "improvingFamilies": improving_families},
        "classification": classification, "frozenForwardModel": frozen_spec,
        "secondaryEconomics": economics, "capacity": capacity,
        "governance": {"productionChanged": False, "noRoiFitting": True, "noNewApiCalls": True,
                       "maxClassification": "LEVEL_1_SHADOW_CANDIDATE",
                       "primaryTestIsVsMarketNotVsProduction": True},
    }

    out_path = os.path.join("data", "edgelab", "analytics", "latest_mlb_rsch_0024_market_residual.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")

    frozen_path = os.path.join("data", "edgelab", "analytics", "frozen_mlb_rsch_0024_forward_model.json")
    with open(frozen_path, "w") as f:
        json.dump(frozen_spec, f, indent=2, sort_keys=True, default=str)
        f.write("\n")

    print(f"[{EXPERIMENT_ID}] wrote {out_path} and {frozen_path}")
    print(f"[{EXPERIMENT_ID}] classification={classification}")
    return report


if __name__ == "__main__":
    main()
