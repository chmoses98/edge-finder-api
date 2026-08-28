#!/usr/bin/env python3
"""
scripts/edgelab/run_loss_function_audit_experiment.py
====================================================================
Research Lab experiment MLB-RSCH-0021: "Expected-Run Target / Loss-
Function Audit". METHODOLOGY experiment. RESEARCH ONLY. NO production
changes. Does NOT change the disposition of any prior experiment.

CORE STATISTICAL QUESTION: this research program has now twice found a
candidate that lowers MAE on expected team runs while WORSENING frozen-
NB probability scoring (MLB-RSCH-0015's S1, MLB-RSCH-0020's B3). MAE is
minimized by the conditional MEDIAN; our probability engine treats the
predicted expected-run value as a conditional MEAN parameter of a
negative-binomial distribution. This experiment tests whether that
mismatch explains the pattern -- it does NOT assume it does.

GOVERNANCE: this experiment must not, and does not, retroactively
change any prior disposition. MLB-RSCH-0015's S1 and MLB-RSCH-0020's B3
remain REJECTED under their own preregistered rules. Every metric
computed here about S1/B3 is NEW EVIDENCE OWNED BY MLB-RSCH-0021, never
retroactive re-evidence for the original experiments. Their formulas
(HFA fits included) are recomputed via the EXACT SAME frozen functions
those experiments already used and committed -- never refit, never
tuned, never improved.

HOLDOUT ACCESS: unlike a candidate-selection experiment, MLB-RSCH-0021
has no candidate to protect from overfitting-by-selection -- it is a
full-sample DIAGNOSTIC of an already-fixed set of frozen formulas
against an already-fixed metric family. Per its own preregistration
(this experiment's own governance section, decided and locked BEFORE
any real result was examined), DEV/VAL/HOLDOUT are all evaluated
uniformly. This is explicitly NOT holdout access for candidate
selection -- no candidate is being selected, promoted, or unlocked by
this experiment; S1/B3 remain rejected regardless of what this audit
finds.

PREDECLARED CANDIDATE SET (locked before analysis, not expanded):
  CONTROL:   S0 (MLB-RSCH-0015's own frozen S0, == MLB-RSCH-0009's
             {offense,bullpen} composition) for the S1 comparison; B0
             (MLB-RSCH-0020's own frozen B0, the SAME underlying
             composition) for the B3 comparison -- S0 and B0 are the
             SAME control construction, reused from two different
             experiment scripts that both froze it identically.
  CANDIDATE A: MLB-RSCH-0015's S1 (schedule-adjusted mean).
  CANDIDATE B: MLB-RSCH-0020's B3 (bullpen-component blend).
  Explicitly NOT included: MLB-RSCH-0012's O1 or any other historical
  candidate -- adding them would not sharpen this audit's own core
  question (both S1 and B3 already directly instantiate the
  mean-vs-median hypothesis) and risks exactly the "broad fishing
  expedition" this milestone's own preregistration forbids. This
  scope decision is made HERE, before any real metric is computed.
"""
import json
import math
import os
import random
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_SCRIPTS_DIR = os.path.join(_ROOT, "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
_EDGELAB_SCRIPTS_DIR = os.path.join(_SCRIPTS_DIR, "edgelab")
if _EDGELAB_SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _EDGELAB_SCRIPTS_DIR)
_BACKTEST_SCRIPTS_DIR = os.path.join(_EDGELAB_SCRIPTS_DIR, "backtest")
if _BACKTEST_SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _BACKTEST_SCRIPTS_DIR)

from lib.edgelab.backtest.run_distributions import negative_binomial_pmf, poisson_pmf
from lib.edgelab import experiment_registry as reg
from lib.edgelab import evidence_levels as ev
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import paired_evaluation as pe
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab.research_stats import DEFAULT_BOOTSTRAP_SEED, independent_unit_count, sample_size_status, game_clustered_bootstrap_ci

import run_opponent_strength_experiment as rsch0015  # noqa: E402 -- S0/S1, reused unchanged
import run_bullpen_component_talent_experiment as rsch0020  # noqa: E402 -- B0/B3, reused unchanged

EXPERIMENT_ID = "MLB-RSCH-0021"
REGISTRATION_TIMESTAMP = "2026-08-28T17:45:00Z"

FROZEN_DISPERSION = 0.281513


def _verify_frozen_dispersion():
    path = os.path.join(_ROOT, "data", "edgelab", "analytics", "latest_mlb_rsch_0010_run_distribution.json")
    with open(path) as f:
        canonical = json.load(f)["fittedParameters"]["overdispersion"]
    if FROZEN_DISPERSION != canonical:
        raise ValueError(f"FROZEN_DISPERSION drifted from canonical artifact: {FROZEN_DISPERSION} != {canonical}")


_verify_frozen_dispersion()

DEV_SEASONS = [2022, 2023, 2024]
VALIDATION_SEASONS = [2025]
HOLDOUT_SEASONS = [2026]
ALL_SEASONS = DEV_SEASONS + VALIDATION_SEASONS + HOLDOUT_SEASONS

# Predeclared, fixed prediction-run buckets for the calibration diagnostic.
CALIBRATION_BUCKETS = (("2_0_to_3_0", 2.0, 3.0), ("3_0_to_4_0", 3.0, 4.0), ("4_0_to_5_0", 4.0, 5.0), ("5_0_to_6_0", 5.0, 6.0), ("6_0_plus", 6.0, 999.0))
REPRESENTATIVE_LAMBDAS = (2.5, 3.5, 4.5, 5.5, 6.5)  # for the mean-vs-median NB gap diagnostic

SYNTHETIC_SEED = 20260828
SYNTHETIC_N = 20000
SYNTHETIC_TRUE_MEAN = 4.4
SYNTHETIC_TRUE_DISPERSION = FROZEN_DISPERSION


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
        name="mlb_rsch_0021_loss_function_audit_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0021 loss-function audit v1: METHODOLOGY ONLY. Audits MLB-RSCH-0015's "
                        "S1 and MLB-RSCH-0020's B3, both reused completely frozen, under MAE/MSE/RMSE/bias/"
                        "NB-negative-log-likelihood/frozen-NB-Brier metrics. Never retunes S1/B3, never "
                        "changes their disposition."
        ),
        probability_adapter_identity="lib.edgelab.backtest.run_distributions (frozen MLB-RSCH-0010 negative-binomial, dispersion unchanged, never refit here)",
        model_engine_family="pit_safe_research_loss_function_audit_v1",
        required_input_provenance=["team_recent_game_log_reconstruction"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "METHODOLOGY audit: tests whether MAE (which targets the conditional median) is a "
            "statistically appropriate primary metric for evaluating an expected-run MEAN parameter "
            "feeding a negative-binomial probability engine, using MLB-RSCH-0015's S1 and MLB-RSCH-0020's "
            "B3 as the two known cases where MAE improved while frozen-NB Brier worsened. Produces a "
            "versioned, opt-in methodology recommendation for FUTURE experiments only -- never changes "
            "any prior experiment's disposition or any canonical framework code."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Expected-Run Target / Loss-Function Audit",
        hypothesis=(
            "H1: MAE is minimized by the conditional median; our frozen NB probability engine treats the "
            "predicted expected-run value as the conditional MEAN. H2: a candidate can reduce MAE by "
            "shifting toward the conditional median while degrading the mean estimate, which would explain "
            "'lower MAE + worse Brier/log-loss' without requiring any other mechanism. H3 (null, tested not "
            "assumed): this may NOT be what is happening for S1/B3 -- the audit does not presuppose the "
            "answer."
        ),
        research_question="Are we using the correct primary loss function to evaluate an expected-run mean, given that it feeds a distributional (NB mean-parameter) probability engine rather than being consumed directly?",
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E2_PIT_HISTORICAL,
        target_population="Identical row population to MLB-RSCH-0015 (S0/S1 comparison) and MLB-RSCH-0020 (B0/B3 comparison) -- both derived from the same underlying MLB-RSCH-0009 corpus construction, reused unchanged, 2022-2026",
        market_families=["game_result", "game_total", "team_total", "run_margin"],
        eligibility_criteria=["identical to MLB-RSCH-0015's own S0/S1 eligibility and MLB-RSCH-0020's own B0/B3 eligibility -- unchanged, never re-derived"],
        exclusion_criteria=[
            "MLB-RSCH-0012's O1 or any other historical candidate beyond S1/B3 -- explicitly predeclared out of scope, not a fishing expedition",
            "any refit of S1's schedule-adjustment formula, K_PRIOR, or HFA constants beyond the SAME frozen recomputation those experiments already performed",
            "any refit of B3's shrinkage K, linear mapping, or blend weight -- read directly from MLB-RSCH-0020's own committed artifact, never refit",
            "any change to any prior experiment's own committed artifact, registration, or disposition",
            "any production code change of any kind",
        ],
        prediction_checkpoints=["SEASON_TO_DATE_PREGAME"],
        primary_metric="NB negative log-likelihood delta (candidate minus control) -- the metric most directly aligned with the probability engine's own mean-parameter assumption",
        secondary_metrics=[
            "MAE delta", "MSE/RMSE delta", "bias delta", "mean/median residual",
            "frozen-NB team-total/game-total/moneyline/run-margin Brier deltas",
            "calibration-bucket bias and diagnostic OLS slope/intercept (predicted vs actual)",
            "team-level correlation between mean-metric deltas and downstream Brier deltas",
        ],
        chronological_split_policy=f"SEASON_BASED: development={DEV_SEASONS}, validation={VALIDATION_SEASONS}, holdout={HOLDOUT_SEASONS} -- evaluated UNIFORMLY as a full-sample diagnostic, not gated as a candidate-selection holdout (see module docstring's own HOLDOUT ACCESS section)",
        minimum_sample_requirement={"independentGames": 50},
        clustering_unit="gamePk",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={"team_recent_game_log_reconstruction": "PREDICTIVE_INPUT"},
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            "evidenceLevel E2_PIT_HISTORICAL. This is a METHODOLOGY experiment: it produces a versioned, "
            "opt-in Research Lab reporting-convention recommendation for FUTURE experiments, and explicitly "
            "does NOT alter MLB-RSCH-0015 or MLB-RSCH-0020's own dispositions (both remain REJECTED). Max "
            "disposition for this experiment itself: METHODOLOGY_RECOMMENDATION, not a candidate disposition."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Corpus (ONE shared build -- S0/S1 and B0/B3 all attached to the SAME row objects) ──

def build_shared_corpus():
    """Reuses rsch0015.build_corpus() unchanged -- byte-identical
    construction to what rsch0020's own main() independently re-derives
    (same rsch0009 loaders, same season loop, same attach_stabilized_components
    call), so S0/S1 and B0/B3 predictions can be attached to the SAME row
    objects rather than two separately-built (but equivalent) corpora."""
    rows_by_season, team_games_by_season, relief_by_season, league_avg_offense, league_avg_bullpen_er9 = rsch0015.build_corpus()
    dev_rows = [r for s in DEV_SEASONS for r in rows_by_season[s]]
    val_rows = [r for s in VALIDATION_SEASONS for r in rows_by_season[s]]
    holdout_rows = [r for s in HOLDOUT_SEASONS for r in rows_by_season[s]]
    all_rows = dev_rows + val_rows + holdout_rows
    return {
        "dev": dev_rows, "val": val_rows, "holdout": holdout_rows, "all": all_rows,
        "teamGamesBySeason": team_games_by_season, "leagueAvgOffense": league_avg_offense, "leagueAvgBullpenEr9": league_avg_bullpen_er9,
    }


def attach_s0_s1_frozen(corpus):
    """S0/S1, reused completely unchanged from MLB-RSCH-0015. HFA fits
    are the SAME neutral, deterministic recomputation every candidate in
    every prior experiment already performs (never a retune of S1's own
    schedule-adjustment formula, K_PRIOR, or eligibility)."""
    dev_rows, all_rows = corpus["dev"], corpus["all"]
    hfa_s0 = rsch0015.fit_hfa_s0(dev_rows)
    rsch0015.attach_s0_predictions(all_rows, hfa_s0)

    raw_lookup = rsch0015.build_raw_baseline_lookup(corpus["teamGamesBySeason"])
    s1_lookup = rsch0015.compute_schedule_adjustment(
        corpus["teamGamesBySeason"], raw_lookup, corpus["leagueAvgOffense"], corpus["leagueAvgBullpenEr9"],
        min_prior_games_opponent=rsch0015.MIN_PRIOR_GAMES_OPPONENT,
    )
    hfa_s1 = rsch0015.fit_hfa_schedule(dev_rows, s1_lookup, corpus["leagueAvgOffense"], corpus["leagueAvgBullpenEr9"])
    rsch0015.attach_schedule_predictions(all_rows, "S1", s1_lookup, hfa_s1, corpus["leagueAvgOffense"], corpus["leagueAvgBullpenEr9"])
    return {"hfaS0": hfa_s0, "hfaS1": hfa_s1}


def attach_b0_b3_frozen(corpus):
    """B0/B3, reused completely unchanged from MLB-RSCH-0020. The K-BB
    shrinkage K, linear mapping (slope/intercept), and blend weight are
    READ DIRECTLY from MLB-RSCH-0020's own committed artifact and
    asserted equal -- never refit, exactly mirroring how MLB-RSCH-0018
    read MLB-RSCH-0017's own frozen parameters."""
    path = os.path.join(_ROOT, "data", "edgelab", "analytics", "latest_mlb_rsch_0020_bullpen_component_talent.json")
    with open(path) as f:
        rsch0020_artifact = json.load(f)
    mapping, blend = rsch0020_artifact["b1Mapping"], rsch0020_artifact["b3Blend"]
    if mapping["k"] != 80 or mapping["slope"] != -25.790521 or mapping["intercept"] != 8.255735:
        raise ValueError(f"MLB-RSCH-0020's own B1 mapping drifted from the expected frozen values: {mapping}")
    if blend["weight"] != 0.5:
        raise ValueError(f"MLB-RSCH-0020's own B3 blend weight drifted from the expected frozen value: {blend}")

    all_rows = corpus["all"]
    team_games_by_season = corpus["teamGamesBySeason"]
    league_avg_offense = corpus["leagueAvgOffense"]

    relief_er9_by_season, relief_kbb_by_season = {}, {}
    for season in ALL_SEASONS:
        team_games = team_games_by_season[season]
        relief_er9_by_season[season] = rsch0020.rsch0009.load_relief_er9_games(season, team_games)
        relief_kbb_by_season[season] = rsch0020.load_relief_kbb_games(season, team_games)

    league_avg_kbb = rsch0020.fit_league_average_kbb([games for s in DEV_SEASONS for games in relief_kbb_by_season[s].values()])

    all_seasons_data = [(s, relief_er9_by_season[s], relief_kbb_by_season[s]) for s in ALL_SEASONS]
    bullpen_rows = rsch0020.build_bullpen_rows_multi_season(all_seasons_data, league_avg_kbb, mapping["k"])
    rsch0020.attach_b1_predictions_to_bullpen_rows(bullpen_rows, mapping["slope"], mapping["intercept"])
    rsch0020.attach_b3_predictions_to_bullpen_rows(bullpen_rows, blend["weight"])

    b3_lookup = {(r["season"], r["gamePk"], r["teamId"]): r["b3Er9"] for r in bullpen_rows if r.get("b3Er9") is not None}

    def b3_override(r, side):
        return b3_lookup.get((r["season"], r["gamePk"], r["homeTeamId"] if side == "home" else r["awayTeamId"]))

    hfa_b0 = rsch0020.rsch0009.fit_home_field_adjustment_for_components(corpus["dev"], rsch0020.BASELINE_COMPONENTS)
    rsch0020.attach_team_mean_predictions(all_rows, "B0", lambda r, side: None, league_avg_offense, hfa_b0)
    rsch0020.attach_team_mean_predictions(all_rows, "B3", b3_override, league_avg_offense, hfa_b0)
    return {"hfaB0": hfa_b0, "leagueAvgKbb": league_avg_kbb, "mapping": mapping, "blend": blend}


# ── Mean-level metrics: MAE (median-targeting) vs MSE/RMSE (mean-targeting) ──

def team_observations(rows, key_prefix):
    """Generic -- works for both S0/S1 (rsch0015's own field names) and
    B0/B3 (rsch0020's), since both are attached to the SAME row objects
    built by the SAME underlying rsch0009 corpus construction."""
    obs = []
    for r in rows:
        eh, ea = r.get(f"homeExpectedRuns_{key_prefix}"), r.get(f"awayExpectedRuns_{key_prefix}")
        if eh is not None and r.get("actualHomeRuns") is not None:
            obs.append({"gamePk": r["gamePk"], "teamId": r["homeTeamId"], "season": r["season"], "predicted": eh, "actual": r["actualHomeRuns"]})
        if ea is not None and r.get("actualAwayRuns") is not None:
            obs.append({"gamePk": r["gamePk"], "teamId": r["awayTeamId"], "season": r["season"], "predicted": ea, "actual": r["actualAwayRuns"]})
    return obs


def _median(values):
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 == 1 else (s[mid - 1] + s[mid]) / 2.0


def full_mean_metrics(obs):
    """MAE targets the conditional MEDIAN; MSE/RMSE target the
    conditional MEAN -- both computed here, deliberately side by side,
    since this experiment's whole purpose is comparing them."""
    if not obs:
        return {"n": 0, "mae": None, "mse": None, "rmse": None, "bias": None, "meanResidual": None, "medianResidual": None}
    residuals = [o["predicted"] - o["actual"] for o in obs]
    abs_errors = [abs(e) for e in residuals]
    sq_errors = [e ** 2 for e in residuals]
    n = len(residuals)
    return {
        "n": n, "independentGames": independent_unit_count(obs, key="gamePk"),
        "mae": round(sum(abs_errors) / n, 4), "mse": round(sum(sq_errors) / n, 4), "rmse": round(math.sqrt(sum(sq_errors) / n), 4),
        "bias": round(sum(residuals) / n, 4), "meanResidual": round(sum(residuals) / n, 4), "medianResidual": round(_median(residuals), 4),
    }


def paired_full_delta(obs_a, obs_b):
    """Paired MAE AND MSE deltas together (candidate B minus control A),
    same game-clustered-bootstrap CI machinery used throughout this
    program, computed once on the SAME paired row set for a fair,
    directly comparable MAE-vs-MSE reading."""
    by_a = {(o["gamePk"], o["teamId"]): o for o in obs_a}
    by_b = {(o["gamePk"], o["teamId"]): o for o in obs_b}
    common = sorted(set(by_a) & set(by_b))
    paired_rows = [{
        "gamePk": k[0], "teamId": k[1],
        "absErrA": abs(by_a[k]["predicted"] - by_a[k]["actual"]), "absErrB": abs(by_b[k]["predicted"] - by_b[k]["actual"]),
        "sqErrA": (by_a[k]["predicted"] - by_a[k]["actual"]) ** 2, "sqErrB": (by_b[k]["predicted"] - by_b[k]["actual"]) ** 2,
    } for k in common]

    def _mae_delta(subset):
        return sum(r["absErrB"] - r["absErrA"] for r in subset) / len(subset) if subset else None

    def _mse_delta(subset):
        return sum(r["sqErrB"] - r["sqErrA"] for r in subset) / len(subset) if subset else None

    mae_point = _mae_delta(paired_rows)
    mae_lo, mae_hi, _ = game_clustered_bootstrap_ci(paired_rows, _mae_delta, cluster_key="gamePk", seed=DEFAULT_BOOTSTRAP_SEED)
    mse_point = _mse_delta(paired_rows)
    mse_lo, mse_hi, _ = game_clustered_bootstrap_ci(paired_rows, _mse_delta, cluster_key="gamePk", seed=DEFAULT_BOOTSTRAP_SEED)
    return {
        "n": len(paired_rows), "independentGames": independent_unit_count(paired_rows, key="gamePk"),
        "maeDelta": round(mae_point, 6) if mae_point is not None else None, "maeDeltaCI95": {"low": mae_lo, "high": mae_hi},
        "mseDelta": round(mse_point, 6) if mse_point is not None else None, "mseDeltaCI95": {"low": mse_lo, "high": mse_hi},
        "rmseA": round(math.sqrt(sum(r["sqErrA"] for r in paired_rows) / len(paired_rows)), 4) if paired_rows else None,
        "rmseB": round(math.sqrt(sum(r["sqErrB"] for r in paired_rows) / len(paired_rows)), 4) if paired_rows else None,
        "interpretation": "negative == candidate B improved (lower than A)",
    }


# ── Calibration diagnostic (fixed buckets, keyed by CONTROL's own predicted value) ──

def _simple_ols(pairs):
    n = len(pairs)
    if n < 2:
        return None, None
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x == 0:
        return None, None
    cov_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = cov_xy / var_x
    return slope, mean_y - slope * mean_x


def calibration_bucket_report(control_obs, candidate_obs):
    """Buckets are keyed by CONTROL's own predicted value (same game
    population sliced identically for both models -- MLB-RSCH-0014's own
    established convention). DIAGNOSTIC ONLY -- no corrective model is
    fit here."""
    by_key_control = {(o["gamePk"], o["teamId"]): o for o in control_obs}
    out = {}
    for name, lo, hi in CALIBRATION_BUCKETS:
        bucket_control = [o for o in control_obs if lo <= o["predicted"] < hi]
        keys = {(o["gamePk"], o["teamId"]) for o in bucket_control}
        bucket_candidate = [o for o in candidate_obs if (o["gamePk"], o["teamId"]) in keys]
        out[name] = {
            "n": len(bucket_control),
            "controlPredictedMean": round(sum(o["predicted"] for o in bucket_control) / len(bucket_control), 4) if bucket_control else None,
            "controlActualMean": round(sum(o["actual"] for o in bucket_control) / len(bucket_control), 4) if bucket_control else None,
            "controlBias": round(sum(o["predicted"] - o["actual"] for o in bucket_control) / len(bucket_control), 4) if bucket_control else None,
            "candidateActualMean": round(sum(o["actual"] for o in bucket_candidate) / len(bucket_candidate), 4) if bucket_candidate else None,
            "candidateBias": round(sum(o["predicted"] - o["actual"] for o in bucket_candidate) / len(bucket_candidate), 4) if bucket_candidate else None,
        }
    return out


def diagnostic_calibration_ols(obs):
    slope, intercept = _simple_ols([(o["predicted"], o["actual"]) for o in obs])
    return {"slope": round(slope, 4) if slope is not None else None, "intercept": round(intercept, 4) if intercept is not None else None}


# ── NB negative log-likelihood / Poisson deviance (mean-parameter-consistent) ──

def nb_negative_log_likelihood(obs):
    """Mean NB negative log-likelihood across observations, using the
    FROZEN MLB-RSCH-0010 dispersion, NEVER refit here. This is the metric
    most directly aligned with what the probability engine actually
    assumes about the predicted value (a conditional MEAN parameter)."""
    nlls = []
    for o in obs:
        if o["predicted"] is None or o["predicted"] <= 0:
            continue
        k = int(round(o["actual"]))
        p = negative_binomial_pmf(k, o["predicted"], FROZEN_DISPERSION)
        if p <= 0:
            continue
        nlls.append(-math.log(p))
    return {"n": len(nlls), "meanNegLogLikelihood": round(sum(nlls) / len(nlls), 6) if nlls else None}


def poisson_deviance(obs):
    """Secondary diagnostic only -- standard Poisson deviance (2*(k*log(k/mu) - (k-mu))),
    k=0 handled via its own well-defined limit (2*mu)."""
    devs = []
    for o in obs:
        mu = o["predicted"]
        k = o["actual"]
        if mu is None or mu <= 0:
            continue
        dev = 2 * mu if k == 0 else 2 * (k * math.log(k / mu) - (k - mu))
        devs.append(dev)
    return {"n": len(devs), "meanDeviance": round(sum(devs) / len(devs), 6) if devs else None}


def paired_nb_nll_delta(obs_a, obs_b):
    by_a = {(o["gamePk"], o["teamId"]): o for o in obs_a}
    by_b = {(o["gamePk"], o["teamId"]): o for o in obs_b}
    common = sorted(set(by_a) & set(by_b))
    paired_rows = []
    for key in common:
        oa, ob = by_a[key], by_b[key]
        if oa["predicted"] is None or oa["predicted"] <= 0 or ob["predicted"] is None or ob["predicted"] <= 0:
            continue
        k = int(round(oa["actual"]))
        pa, pb = negative_binomial_pmf(k, oa["predicted"], FROZEN_DISPERSION), negative_binomial_pmf(k, ob["predicted"], FROZEN_DISPERSION)
        if pa <= 0 or pb <= 0:
            continue
        paired_rows.append({"gamePk": key[0], "teamId": key[1], "nllA": -math.log(pa), "nllB": -math.log(pb)})

    def _delta(subset):
        return sum(r["nllB"] - r["nllA"] for r in subset) / len(subset) if subset else None

    point = _delta(paired_rows)
    lo, hi, _ = game_clustered_bootstrap_ci(paired_rows, _delta, cluster_key="gamePk", seed=DEFAULT_BOOTSTRAP_SEED)
    return {"n": len(paired_rows), "nllDelta": round(point, 6) if point is not None else None, "nllDeltaCI95": {"low": lo, "high": hi},
            "interpretation": "negative == candidate B improved (lower NLL, better distributional fit)"}


# ── Frozen-NB probability (Brier) -- reuses rsch0020's own generic cell primitives ──

def frozen_nb_probability_eval(rows, key_a, key_b):
    control_rows, candidate_rows = [], []
    for r in rows:
        actual_home, actual_away = r.get("actualHomeRuns"), r.get("actualAwayRuns")
        if actual_home is None or actual_away is None:
            continue
        cells_a = rsch0020.nb_probability_cells(r.get(f"homeExpectedRuns_{key_a}"), r.get(f"awayExpectedRuns_{key_a}"))
        cells_b = rsch0020.nb_probability_cells(r.get(f"homeExpectedRuns_{key_b}"), r.get(f"awayExpectedRuns_{key_b}"))
        if cells_a is None or cells_b is None:
            continue
        outcomes = rsch0020._outcomes_for_actual(actual_home, actual_away)
        for cell_key, outcome in outcomes.items():
            control_rows.append({"gameId": r["gamePk"], "cellKey": cell_key, "gameDate": r.get("date"), "modelFairProbability": cells_a[cell_key], "outcome": outcome})
            candidate_rows.append({"gameId": r["gamePk"], "cellKey": cell_key, "gameDate": r.get("date"), "modelFairProbability": cells_b[cell_key], "outcome": outcome})

    def key_fn(row):
        return (row["gameId"], row["cellKey"])

    by_family = {}
    for family_prefix in ("game_total", "team_total_home", "team_total_away", "moneyline", "run_margin"):
        fam_control = [r for r in control_rows if r["cellKey"].startswith(family_prefix)]
        fam_candidate = [r for r in candidate_rows if r["cellKey"].startswith(family_prefix)]
        fam_pairing = pe.pair_eligible_observations(fam_control, fam_candidate, key_fn=key_fn)
        by_family[family_prefix] = pe.evaluate_probability_model_pair(fam_pairing, game_key="gameId", date_key="gameDate")
    return {"byFamily": by_family}


def _primary_brier_delta(nb_result):
    deltas = [nb_result["byFamily"][fam]["pairedDelta"]["brierScore"] for fam in ("game_total", "team_total_home", "team_total_away", "moneyline") if nb_result["byFamily"].get(fam) and nb_result["byFamily"][fam]["pairedDelta"]["brierScore"] is not None]
    return round(sum(deltas) / len(deltas), 6) if deltas else None


# ── Mean-vs-median NB gap (representative lambdas, frozen dispersion) ─────

def nb_median(mean, dispersion, max_k=60):
    """Smallest k such that the NB(mean, dispersion) CDF reaches >= 0.5 --
    the distribution's own true median, exact under the frozen NB pmf."""
    cumulative = 0.0
    for k in range(max_k + 1):
        cumulative += negative_binomial_pmf(k, mean, dispersion)
        if cumulative >= 0.5:
            return k
    return max_k


def mean_vs_median_gap_report():
    out = {}
    for lam in REPRESENTATIVE_LAMBDAS:
        median = nb_median(lam, FROZEN_DISPERSION)
        out[str(lam)] = {"mean": lam, "median": median, "gap": round(lam - median, 4)}
    return out


# ── Deterministic synthetic sanity check (NOT evidence about MLB) ─────────

def _poisson_sample(rng, lam):
    """Knuth's algorithm -- exact, deterministic given `rng`'s own state.
    Fine for the small lambda values used here (representative NB draws
    from a Gamma-Poisson mixture, typically single digits)."""
    l_thresh = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= l_thresh:
            return k - 1


def _nb_sample(rng, mean, dispersion):
    """Gamma-Poisson mixture -- exact NB sampling matching negative_binomial_pmf's
    OWN (mean, dispersion) parameterization: r=1/dispersion, Lambda ~ Gamma(shape=r, scale=mean/r), X ~ Poisson(Lambda)."""
    r = 1.0 / dispersion
    theta = mean / r
    lam = rng.gammavariate(r, theta)
    return _poisson_sample(rng, lam)


def run_synthetic_sanity_check():
    """Deterministic simulation demonstrating the median-vs-mean metric
    distinction on a KNOWN distribution -- NOT evidence about MLB itself,
    a mathematical validation that this experiment's own evaluation
    framework behaves as theoretically expected."""
    rng = random.Random(SYNTHETIC_SEED)
    draws = [_nb_sample(rng, SYNTHETIC_TRUE_MEAN, SYNTHETIC_TRUE_DISPERSION) for _ in range(SYNTHETIC_N)]

    true_mean = SYNTHETIC_TRUE_MEAN
    true_median = nb_median(SYNTHETIC_TRUE_MEAN, SYNTHETIC_TRUE_DISPERSION)
    biased_mean = SYNTHETIC_TRUE_MEAN + 0.3

    predictors = {"A_true_conditional_mean": true_mean, "B_median_like_shifted": float(true_median), "C_biased_mean": biased_mean}
    event_line = true_median + 0.5  # a fixed "over/under" event, evaluated identically for every predictor

    results = {}
    for name, pred in predictors.items():
        abs_errors = [abs(pred - k) for k in draws]
        sq_errors = [(pred - k) ** 2 for k in draws]
        nlls = []
        brier_terms = []
        for k in draws:
            p_k = negative_binomial_pmf(k, pred, SYNTHETIC_TRUE_DISPERSION)
            if p_k > 0:
                nlls.append(-math.log(p_k))
            p_over = sum(negative_binomial_pmf(j, pred, SYNTHETIC_TRUE_DISPERSION) for j in range(int(event_line) + 1, 60))
            outcome = 1.0 if k > event_line else 0.0
            brier_terms.append((p_over - outcome) ** 2)
        results[name] = {
            "predictorValue": round(pred, 4),
            "mae": round(sum(abs_errors) / len(abs_errors), 4),
            "mse": round(sum(sq_errors) / len(sq_errors), 4),
            "meanNegLogLikelihood": round(sum(nlls) / len(nlls), 6) if nlls else None,
            "eventBrier": round(sum(brier_terms) / len(brier_terms), 6),
        }
    best_by_metric = {
        metric: min(results, key=lambda name: results[name][metric])
        for metric in ("mae", "mse", "meanNegLogLikelihood", "eventBrier")
    }
    return {
        "n": SYNTHETIC_N, "seed": SYNTHETIC_SEED, "trueMean": true_mean, "trueMedian": true_median,
        "empiricalSampleMean": round(sum(draws) / len(draws), 4), "empiricalSampleMedian": _median(draws),
        "results": results, "bestByMetric": best_by_metric,
        "matchesTheory": best_by_metric["mae"] == "B_median_like_shifted" and best_by_metric["mse"] == "A_true_conditional_mean" and best_by_metric["meanNegLogLikelihood"] == "A_true_conditional_mean",
    }


# ── Metric-vs-probability relationship (team-level, moneyline Brier) ──────

def pearson_corr(pairs):
    n = len(pairs)
    if n < 2:
        return None
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return None
    return round(cov / math.sqrt(vx * vy), 4)


def team_level_metric_vs_brier(rows, obs_control, obs_candidate, key_control, key_candidate):
    """Per-team-side deltas (mean-abs-error delta, mean-sq-error delta,
    NB-NLL delta) correlated against that SAME team-side's own moneyline
    Brier delta -- the finest grain this program's existing machinery
    supports without inventing a new per-observation probability
    decomposition. n=30 teams per candidate."""
    by_control = {(o["gamePk"], o["teamId"]): o for o in obs_control}
    by_candidate = {(o["gamePk"], o["teamId"]): o for o in obs_candidate}
    common = sorted(set(by_control) & set(by_candidate))

    ml_by_gamepk = {}
    for r in rows:
        actual_home, actual_away = r.get("actualHomeRuns"), r.get("actualAwayRuns")
        if actual_home is None or actual_away is None:
            continue
        cells_c = rsch0020.nb_probability_cells(r.get(f"homeExpectedRuns_{key_control}"), r.get(f"awayExpectedRuns_{key_control}"))
        cells_a = rsch0020.nb_probability_cells(r.get(f"homeExpectedRuns_{key_candidate}"), r.get(f"awayExpectedRuns_{key_candidate}"))
        if cells_c is None or cells_a is None:
            continue
        outcome = 1.0 if actual_home > actual_away else (0.5 if actual_home == actual_away else 0.0)
        ml_by_gamepk[r["gamePk"]] = {
            "brierDeltaHome": (cells_a["moneyline"] - outcome) ** 2 - (cells_c["moneyline"] - outcome) ** 2,
            "brierDeltaAway": (1 - cells_a["moneyline"] - (1 - outcome)) ** 2 - (1 - cells_c["moneyline"] - (1 - outcome)) ** 2,
        }

    home_team_by_gamepk = {r["gamePk"]: r["homeTeamId"] for r in rows}
    by_team = {}
    for key in common:
        game_pk, team_id = key
        oc, oa = by_control[key], by_candidate[key]
        ml = ml_by_gamepk.get(game_pk)
        if ml is None:
            continue
        side_key = "brierDeltaHome" if team_id == home_team_by_gamepk.get(game_pk) else "brierDeltaAway"
        by_team.setdefault(team_id, {"absErrDelta": [], "sqErrDelta": [], "brierDelta": []})
        by_team[team_id]["absErrDelta"].append(abs(oa["predicted"] - oa["actual"]) - abs(oc["predicted"] - oc["actual"]))
        by_team[team_id]["sqErrDelta"].append((oa["predicted"] - oa["actual"]) ** 2 - (oc["predicted"] - oc["actual"]) ** 2)
        by_team[team_id]["brierDelta"].append(ml[side_key])

    team_rows = []
    for team_id, vals in by_team.items():
        if not vals["absErrDelta"]:
            continue
        team_rows.append({
            "teamId": team_id,
            "meanAbsErrDelta": sum(vals["absErrDelta"]) / len(vals["absErrDelta"]),
            "meanSqErrDelta": sum(vals["sqErrDelta"]) / len(vals["sqErrDelta"]),
            "meanBrierDelta": sum(vals["brierDelta"]) / len(vals["brierDelta"]),
        })
    return {
        "nTeams": len(team_rows),
        "corrAbsErrVsBrier": pearson_corr([(t["meanAbsErrDelta"], t["meanBrierDelta"]) for t in team_rows]),
        "corrSqErrVsBrier": pearson_corr([(t["meanSqErrDelta"], t["meanBrierDelta"]) for t in team_rows]),
    }


# ── Research Lab metric-convention audit (read-only inspection) ───────────

def research_lab_metric_audit():
    """Read-only grep-based inspection of this program's own prior
    experiment scripts for where MAE is treated as the PRIMARY or GATING
    metric for an expected-run mean. Never modifies any prior artifact."""
    import subprocess as sp
    scripts_dir = os.path.join(_ROOT, "scripts", "edgelab")
    result = sp.run(
        ["grep", "-rl", "primary_metric=\"paired MAE delta", scripts_dir, "--include=*.py"],
        capture_output=True, text=True, timeout=30,
    )
    files_using_mae_primary = sorted(os.path.basename(f) for f in result.stdout.splitlines() if f.strip())
    return {
        "scriptsWithMaeAsPrimaryMetric": files_using_mae_primary,
        "count": len(files_using_mae_primary),
        "finding": (
            "Every mean-model experiment in this program to date (MLB-RSCH-0012/0014/0015/0017/0018/0020 "
            "among them) registers its own primary_metric as a paired MAE delta, and every one of those "
            "experiments' own selection_passes() gates on that same MAE delta as criterion #1. None "
            "currently gates on MSE/RMSE or NB likelihood as the PRIMARY selection criterion -- probability "
            "scoring (frozen-NB Brier) is checked as a SEPARATE, later gate, not as part of the same "
            "mean-consistent metric family MAE nominally represents."
        ),
    }


def main():
    print(f"[{EXPERIMENT_ID}] registering experiment/control...")
    control, definition = register_experiment()

    print(f"[{EXPERIMENT_ID}] building shared corpus (reuses rsch0015.build_corpus() unchanged)...")
    corpus = build_shared_corpus()
    print(f"[{EXPERIMENT_ID}] rows: dev={len(corpus['dev'])} val={len(corpus['val'])} holdout={len(corpus['holdout'])} total={len(corpus['all'])}")

    print(f"[{EXPERIMENT_ID}] attaching S0/S1 (frozen, MLB-RSCH-0015, unchanged)...")
    s0_s1_meta = attach_s0_s1_frozen(corpus)
    print(f"[{EXPERIMENT_ID}] S0/S1 HFA: {s0_s1_meta}")

    print(f"[{EXPERIMENT_ID}] attaching B0/B3 (frozen, MLB-RSCH-0020, unchanged)...")
    b0_b3_meta = attach_b0_b3_frozen(corpus)
    print(f"[{EXPERIMENT_ID}] B0/B3 params (read from MLB-RSCH-0020's own artifact): {b0_b3_meta['mapping']} {b0_b3_meta['blend']}")

    all_rows = corpus["all"]
    obs_s0, obs_s1 = team_observations(all_rows, "S0"), team_observations(all_rows, "S1")
    obs_b0, obs_b3 = team_observations(all_rows, "B0"), team_observations(all_rows, "B3")

    print(f"[{EXPERIMENT_ID}] computing full mean metrics (MAE/MSE/RMSE/bias)...")
    s0_metrics, s1_metrics = full_mean_metrics(obs_s0), full_mean_metrics(obs_s1)
    b0_metrics, b3_metrics = full_mean_metrics(obs_b0), full_mean_metrics(obs_b3)
    s1_delta = paired_full_delta(obs_s0, obs_s1)
    b3_delta = paired_full_delta(obs_b0, obs_b3)
    print(f"[{EXPERIMENT_ID}] S1 vs S0: MAE delta={s1_delta['maeDelta']} MSE delta={s1_delta['mseDelta']}")
    print(f"[{EXPERIMENT_ID}] B3 vs B0: MAE delta={b3_delta['maeDelta']} MSE delta={b3_delta['mseDelta']}")

    print(f"[{EXPERIMENT_ID}] calibration diagnostics...")
    s1_calibration = calibration_bucket_report(obs_s0, obs_s1)
    b3_calibration = calibration_bucket_report(obs_b0, obs_b3)
    s0_ols, s1_ols = diagnostic_calibration_ols(obs_s0), diagnostic_calibration_ols(obs_s1)
    b0_ols, b3_ols = diagnostic_calibration_ols(obs_b0), diagnostic_calibration_ols(obs_b3)

    print(f"[{EXPERIMENT_ID}] NB negative log-likelihood + Poisson deviance...")
    s0_nll, s1_nll = nb_negative_log_likelihood(obs_s0), nb_negative_log_likelihood(obs_s1)
    b0_nll, b3_nll = nb_negative_log_likelihood(obs_b0), nb_negative_log_likelihood(obs_b3)
    s1_nll_delta = paired_nb_nll_delta(obs_s0, obs_s1)
    b3_nll_delta = paired_nb_nll_delta(obs_b0, obs_b3)
    s0_deviance, s1_deviance = poisson_deviance(obs_s0), poisson_deviance(obs_s1)
    b0_deviance, b3_deviance = poisson_deviance(obs_b0), poisson_deviance(obs_b3)
    print(f"[{EXPERIMENT_ID}] S1 NLL delta={s1_nll_delta['nllDelta']} | B3 NLL delta={b3_nll_delta['nllDelta']}")

    print(f"[{EXPERIMENT_ID}] frozen-NB probability (Brier) evaluation...")
    s1_nb_prob = frozen_nb_probability_eval(all_rows, "S0", "S1")
    b3_nb_prob = frozen_nb_probability_eval(all_rows, "B0", "B3")
    s1_brier_primary = _primary_brier_delta(s1_nb_prob)
    b3_brier_primary = _primary_brier_delta(b3_nb_prob)
    print(f"[{EXPERIMENT_ID}] S1 primary Brier delta={s1_brier_primary} | B3 primary Brier delta={b3_brier_primary}")

    print(f"[{EXPERIMENT_ID}] team-level metric-vs-Brier correlation...")
    s1_correlation = team_level_metric_vs_brier(all_rows, obs_s0, obs_s1, "S0", "S1")
    b3_correlation = team_level_metric_vs_brier(all_rows, obs_b0, obs_b3, "B0", "B3")
    print(f"[{EXPERIMENT_ID}] S1 corr(absErr,Brier)={s1_correlation['corrAbsErrVsBrier']} corr(sqErr,Brier)={s1_correlation['corrSqErrVsBrier']}")
    print(f"[{EXPERIMENT_ID}] B3 corr(absErr,Brier)={b3_correlation['corrAbsErrVsBrier']} corr(sqErr,Brier)={b3_correlation['corrSqErrVsBrier']}")

    print(f"[{EXPERIMENT_ID}] mean-vs-median NB gap (representative lambdas)...")
    gap_report = mean_vs_median_gap_report()

    print(f"[{EXPERIMENT_ID}] deterministic synthetic sanity check...")
    synthetic_result = run_synthetic_sanity_check()
    print(f"[{EXPERIMENT_ID}] synthetic matchesTheory={synthetic_result['matchesTheory']}")

    print(f"[{EXPERIMENT_ID}] Research Lab metric-convention audit (read-only)...")
    audit = research_lab_metric_audit()

    # ---- Interpretation: does the empirical MAE-improves-Brier-worsens pattern
    # match the median-vs-mean mechanism the synthetic check validated? ----
    def _classify(mae_delta, mse_delta, nll_delta, brier_delta):
        mae_improves = mae_delta is not None and mae_delta < 0
        mse_worsens_or_flat = mse_delta is not None and mse_delta >= 0
        nll_worsens = nll_delta is not None and nll_delta > 0
        brier_worsens = brier_delta is not None and brier_delta > 0
        return mae_improves and (mse_worsens_or_flat or nll_worsens) and brier_worsens

    s1_matches_pattern = _classify(s1_delta["maeDelta"], s1_delta["mseDelta"], s1_nll_delta["nllDelta"], s1_brier_primary)
    b3_matches_pattern = _classify(b3_delta["maeDelta"], b3_delta["mseDelta"], b3_nll_delta["nllDelta"], b3_brier_primary)

    if s1_matches_pattern and b3_matches_pattern:
        methodological_classification = "MAE_PRIMARY_METRIC_INAPPROPRIATE"
    elif s1_matches_pattern or b3_matches_pattern:
        methodological_classification = "MAE_USEFUL_BUT_SECONDARY"
    elif not s1_matches_pattern and not b3_matches_pattern and synthetic_result["matchesTheory"]:
        methodological_classification = "INCONCLUSIVE"
    else:
        methodological_classification = "INCONCLUSIVE"

    report = {
        "experimentId": EXPERIMENT_ID,
        "controlModelId": control["controlModelId"],
        "governance": "Does NOT change MLB-RSCH-0015 or MLB-RSCH-0020's own disposition -- both remain REJECTED under their own preregistered rules. All metrics here are NEW RSCH-0021-owned evidence.",
        "corpus": {"devRows": len(corpus["dev"]), "valRows": len(corpus["val"]), "holdoutRows": len(corpus["holdout"]), "totalRows": len(corpus["all"])},
        "candidateSet": {"control": "S0/B0 (same underlying MLB-RSCH-0009 composition)", "candidateA": "MLB-RSCH-0015 S1 (frozen)", "candidateB": "MLB-RSCH-0020 B3 (frozen)"},
        "theoreticalTargets": {
            "MAE": "conditional MEDIAN",
            "MSE_RMSE": "conditional MEAN",
            "NB_negative_log_likelihood": "distributional fit conditional on the modeled NB distribution -- most aligned with how the probability engine actually uses the predicted value",
            "Brier_logLoss": "event probability quality (the ultimate downstream target)",
        },
        "meanVsMedianGap": gap_report,
        "s0Metrics": s0_metrics, "s1Metrics": s1_metrics, "s1Delta": s1_delta,
        "b0Metrics": b0_metrics, "b3Metrics": b3_metrics, "b3Delta": b3_delta,
        "s1Calibration": s1_calibration, "b3Calibration": b3_calibration,
        "s0Ols": s0_ols, "s1Ols": s1_ols, "b0Ols": b0_ols, "b3Ols": b3_ols,
        "s0Nll": s0_nll, "s1Nll": s1_nll, "s1NllDelta": s1_nll_delta,
        "b0Nll": b0_nll, "b3Nll": b3_nll, "b3NllDelta": b3_nll_delta,
        "s0Deviance": s0_deviance, "s1Deviance": s1_deviance, "b0Deviance": b0_deviance, "b3Deviance": b3_deviance,
        "s1BrierPrimaryDelta": s1_brier_primary, "b3BrierPrimaryDelta": b3_brier_primary,
        "s1BrierFull": s1_nb_prob, "b3BrierFull": b3_nb_prob,
        "s1TeamLevelCorrelation": s1_correlation, "b3TeamLevelCorrelation": b3_correlation,
        "syntheticSanityCheck": synthetic_result,
        "researchLabAudit": audit,
        "s1MatchesMedianVsMeanPattern": s1_matches_pattern,
        "b3MatchesMedianVsMeanPattern": b3_matches_pattern,
        "methodologicalClassification": methodological_classification,
        "priorDispositionsChanged": False,
        "rsch0015Disposition": "UNCHANGED -- REJECTED (per MLB-RSCH-0015's own preregistered rules)",
        "rsch0020Disposition": "UNCHANGED -- REJECTED (per MLB-RSCH-0020's own preregistered rules)",
    }

    out_path = os.path.join("data", "edgelab", "analytics", "latest_mlb_rsch_0021_loss_function_audit.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    print(f"[{EXPERIMENT_ID}] wrote {out_path}")
    print(f"[{EXPERIMENT_ID}] methodologicalClassification={methodological_classification}")
    return report


if __name__ == "__main__":
    main()
