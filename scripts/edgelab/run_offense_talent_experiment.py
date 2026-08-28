#!/usr/bin/env python3
"""
scripts/edgelab/run_offense_talent_experiment.py
====================================================================
Research Lab experiment MLB-RSCH-0012: "Offensive Talent Estimation".
RESEARCH ONLY. NO production changes.

Isolates OFFENSE quality specifically: holds MLB-RSCH-0009's frozen
BULLPEN component (stabilized_bullpen_rate/blend_run_prevention_with_
bullpen_quality, k=BULLPEN_SHRINKAGE_K, unchanged) fixed, and searches
over alternative OFFENSE representations feeding the SAME
expected_runs() combination, exactly mirroring MLB-RSCH-0010's own
"freeze one side, vary the other" pattern.

CANDIDATES (this milestone's own new module for O2+):
  O0 (control): production's CURRENT offense component, reproduced
      EXACTLY -- proxy_enrichment.stabilized_offense_rate(raw,
      priorGames, leagueAvg, k=OFFENSE_SHRINKAGE_K=30), the fixed
      (never dev-fit) shrinkage constant MLB-RSCH-0009 already uses.
  O1: SAME shrinkage FORMULA, but k is a genuine closed-form
      empirical-Bayes estimate (k_hat = sigma^2/tau^2, normal-normal
      conjugate shrinkage constant) fit on DEVELOPMENT team-season
      data only -- tests whether the CURRENT fixed k=30 is actually
      close to the data-implied optimum, or whether a fit value
      changes early/mid-season estimates materially.
  O2/O3/O4: component-batting-based offense estimators -- REQUIRE the
      MLB-RSCH-0012 batting boxscore cache
      (data/research_cache/batting_backtest/, built by
      scripts/edgelab/backtest/fetch_mlb_multiseason_batting_cache.py
      via .github/workflows/research-multiseason-batting-backtest.yml,
      manual-dispatch, GitHub-hosted-runner network access required --
      this repository's own CI/local dev environments have no outbound
      access to statsapi.mlb.com). NOT implemented in this script until
      that cache exists -- see docs/EDGELAB_MLB_RSCH_0012_OFFENSE_TALENT.md
      for the honest current status.

MEAN MODEL: bullpen side is MLB-RSCH-0009's own frozen composition,
UNCHANGED -- this script calls team_baseline/bullpen_quality_baseline/
stabilized_bullpen_rate/blend_run_prevention_with_bullpen_quality/
expected_runs UNCHANGED (never reimplemented). It only produces
alternative OFFENSE component values.

PROBABILITY EVALUATION: reuses MLB-RSCH-0010's own frozen negative-
binomial distribution (lib.edgelab.backtest.run_distributions,
lib.edgelab.shadow_distribution.FROZEN_DISPERSION) UNCHANGED --
dispersion is NEVER refit here. Both O0 and O1 (and, once built, O2-O4)
are converted through the IDENTICAL frozen NB distribution, isolating
any probability-scoring improvement to the MEAN alone.

MODEL SELECTION (preregistered before any real result was computed): a
candidate may replace O0 in the FINAL offense model only if, versus O0,
its mean-accuracy metric (MAE on next-game team runs) is (1) better on
DEVELOPMENT, (2) not degraded beyond DEGRADATION_TOLERANCE on
VALIDATION, (3) not confined to the first 15 games of a season (see
SEASON_BANDS), and (4) preserves or improves frozen-NB probability
scoring (mean primary-cell Brier delta not worse than
PROBABILITY_DEGRADATION_TOLERANCE on VALIDATION). 2026/Pinnacle are
never consulted during selection.
"""
import json
import math
import os
import subprocess
import sys
from collections import defaultdict

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

from lib.edgelab.backtest.proxy_model import expected_runs, fit_home_field_adjustment, game_ml_proxy_probability
from lib.edgelab.backtest.proxy_enrichment import (
    OFFENSE_SHRINKAGE_K,
    stabilized_offense_rate,
    bullpen_quality_baseline,
    stabilized_bullpen_rate,
    blend_run_prevention_with_bullpen_quality,
)
from lib.edgelab.backtest.run_distributions import (
    negative_binomial_pmf,
    independent_joint_pmf,
    home_win_and_push_prob,
    total_over_prob,
    team_total_over_prob,
)
from lib.edgelab import experiment_registry as reg
from lib.edgelab import evidence_levels as ev
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import paired_evaluation as pe
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab.research_stats import (
    DEFAULT_BOOTSTRAP_SEED,
    brier_and_log_loss_summary,
    independent_unit_count,
    sample_size_status,
    game_clustered_bootstrap_ci,
)

import run_proxy_ablation_experiment as rsch0009  # noqa: E402

EXPERIMENT_ID = "MLB-RSCH-0012"
REGISTRATION_TIMESTAMP = "2026-08-28T05:30:00Z"

# Frozen at MLB-RSCH-0010 (data/edgelab/analytics/latest_mlb_rsch_0010_run_distribution.json's
# fittedParameters.overdispersion) -- verified byte-exact at import time
# below, never refit here. Duplicated as a local constant rather than
# importing lib.edgelab.shadow_distribution (an MLB-RSCH-0011 module not
# yet merged to main) to avoid an inter-experiment-PR dependency; the
# canonical source of truth is the committed artifact this constant is
# checked against, not either module.
FROZEN_DISPERSION = 0.281513


def _verify_frozen_dispersion():
    path = os.path.join(_ROOT, "data", "edgelab", "analytics", "latest_mlb_rsch_0010_run_distribution.json")
    with open(path) as f:
        canonical = json.load(f)["fittedParameters"]["overdispersion"]
    if canonical != FROZEN_DISPERSION:
        raise ValueError(
            f"FROZEN_DISPERSION={FROZEN_DISPERSION} does not match the canonical MLB-RSCH-0010 artifact "
            f"value {canonical} -- this constant must never silently drift from the frozen source of truth."
        )


_verify_frozen_dispersion()

DEV_SEASONS = [2022, 2023, 2024]
VALIDATION_SEASONS = [2025]
HOLDOUT_SEASONS = [2026]
ALL_SEASONS = DEV_SEASONS + VALIDATION_SEASONS + HOLDOUT_SEASONS

O0 = "O0_control_current"
O1 = "O1_empirical_bayes_shrinkage"
O2 = "O2_component_offense"
O3 = "O3_stabilized_component_offense"
O4 = "O4_opponent_adjusted_offense"
IMPLEMENTED_CANDIDATES = (O0, O1)  # O2/O3/O4 await the batting boxscore cache -- see module docstring

# Preregistered season-progress bands (games played THIS SEASON prior to
# the target game) -- fixed before any result was computed, never
# chosen post hoc.
SEASON_BANDS = (
    ("games_1_15", 1, 15),
    ("games_16_40", 16, 40),
    ("games_41_80", 41, 80),
    ("games_81_plus", 81, None),
)

GAME_TOTAL_LINES = (7.5, 8.5, 9.5, 10.5)
TEAM_TOTAL_LINES = (2.5, 3.5, 4.5, 5.5)

DEGRADATION_TOLERANCE = 0.05  # MAE degradation tolerance on VALIDATION, in runs/game -- fixed before results
PROBABILITY_DEGRADATION_TOLERANCE = 0.005  # frozen-NB primary-cell Brier degradation tolerance on VALIDATION

MIN_GAMES_CONFIDENT = 50


# ── Registration ──────────────────────────────────────────────────────────

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
    control = ctrl_id.build_control_registration(
        name="mlb_rsch_0012_offense_talent_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0012 offense talent v1: MLB-RSCH-0009 frozen bullpen component (unchanged) + "
                        "forward-selected offense component from {O0 current-fixed-shrinkage, O1 empirical-Bayes "
                        "shrinkage, O2 component offense, O3 stabilized component offense, O4 opponent-adjusted}"
        ),
        probability_adapter_identity="lib.edgelab.backtest.run_distributions (frozen MLB-RSCH-0010 negative-binomial, dispersion unchanged)",
        model_engine_family="pit_safe_research_offense_talent_v1",
        required_input_provenance=["team_recent_game_log_reconstruction"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "A NEW historical RESEARCH offense-component layer, built on top of MLB-RSCH-0009's frozen "
            "bullpen component (unchanged). Tests whether O0 (production's current fixed-k=30 shrinkage) is "
            "beaten by O1 (dev-fit empirical-Bayes k) or richer component-batting candidates O2/O3/O4, each "
            "scored on both direct mean accuracy and probability quality under the frozen MLB-RSCH-0010 NB "
            "distribution."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Offensive Talent Estimation",
        hypothesis=(
            "H1: production's current FIXED shrinkage constant (k=30) is close enough to a DEV-fit "
            "empirical-Bayes optimum that no material improvement is available from a better-fit shrinkage "
            "alone (O1 vs O0). H2: component batting statistics (BB/K/HR/XBH rates, OBP/SLG proxies) add "
            "incremental signal beyond season-to-date runs scored (O2/O3 vs O0/O1), once genuinely PIT-safe "
            "historical batting data is available. H3: any real improvement is concentrated in early-season "
            "small-sample estimates (SEASON_BANDS games_1_15/16_40), where shrinkage/richer priors matter "
            "most, and shrinks toward zero as within-season sample accumulates."
        ),
        research_question=(
            "Does a richer, better-stabilized measure of offensive talent predict next-game team runs "
            "scored (and, under the frozen MLB-RSCH-0010 distribution, team-total/game-total/moneyline "
            "probabilities) better than production's current season-to-date-runs-based representation, "
            "holding MLB-RSCH-0009's frozen bullpen component fixed?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E2_PIT_HISTORICAL,
        target_population=(
            "The same MLB regular-season 2022-2026 games MLB-RSCH-0009's own baseline used (both teams "
            ">= MIN_PRIOR_GAMES_FOR_BASELINE prior completed games this season) -- reused via that "
            "milestone's own row-building functions, unchanged."
        ),
        market_families=["game_result", "game_total", "team_total"],
        eligibility_criteria=[
            "both teams have >= MIN_PRIOR_GAMES_FOR_BASELINE prior completed games this season (MLB-RSCH-0009's own eligibility rule, unchanged)",
        ],
        exclusion_criteria=[
            "recent-form/momentum features of any window -- MLB-RSCH-0005 found 5/10/20-game hot/cold deviations carry NO_USEFUL_SIGNAL; this is a TALENT estimation experiment, never a recency one",
            "wOBA-style linear-weights constants -- would require full-season run-value data this experiment has no PIT-safe access to reconstruct",
        ],
        prediction_checkpoints=["SEASON_TO_DATE_PREGAME"],
        primary_metric="paired MAE delta on next-game team runs scored (candidate minus O0), game-and-team-clustered 95% CI",
        secondary_metrics=[
            "RMSE delta", "mean bias delta", "residual variance delta",
            "frozen-NB team-total/game-total/moneyline Brier deltas",
            "season-band-specific MAE deltas (games_1_15/16_40/41_80/81_plus)",
            "per-team effect distribution / leave-one-team-out robustness",
            "paired candidate-minus-Pinnacle Brier delta (secondary stage, existing MLB-RSCH-0008/0009 sample)",
        ],
        chronological_split_policy=f"SEASON_BASED: development={DEV_SEASONS}, validation={VALIDATION_SEASONS}, holdout={HOLDOUT_SEASONS} (locked)",
        minimum_sample_requirement={"independentGames": MIN_GAMES_CONFIDENT},
        clustering_unit="gamePk",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={"team_recent_game_log_reconstruction": "PREDICTIVE_INPUT"},
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            "evidenceLevel E2_PIT_HISTORICAL: same basis as MLB-RSCH-0008/0009/0010. O2/O3/O4 (component-"
            "batting candidates) require a new multi-season batting boxscore cache "
            "(data/research_cache/batting_backtest/) built via a manual-dispatch GitHub Actions workflow "
            "(GitHub-hosted runners have the outbound network access this repo's own CI/dev environments do "
            "not) -- until that cache exists, O2/O3/O4 are registered but not yet evaluated; see "
            "docs/EDGELAB_MLB_RSCH_0012_OFFENSE_TALENT.md for current status. O0/O1 do not depend on it."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Corpus construction (bullpen side frozen, offense side varies) ────────

def build_corpus():
    """
    Reuses MLB-RSCH-0009's own module-level functions UNCHANGED to load
    every season's team-game corpus, relief-ER9 games, and season
    run-environment lookup. Returns (rows_by_season, team_games_by_season,
    relief_by_season, league_avg_offense, league_avg_bullpen_er9) --
    rows carry RAW baselines only (home/awayBaselineRaw,
    home/awayBullpenRaw); offense-candidate-specific stabilized values
    are attached separately per candidate (attach_offense_and_bullpen).
    """
    rows_by_season, team_games_by_season, relief_by_season = {}, {}, {}
    for season in ALL_SEASONS:
        team_games = rsch0009.load_all_team_games_with_venue(season)
        team_games_by_season[season] = team_games
        relief_er9 = rsch0009.load_relief_er9_games(season, team_games)
        relief_by_season[season] = relief_er9
        env_lookup = rsch0009.build_season_environment_lookup(
            [g for games in team_games.values() for g in games if g.get("side") == "home"]
        )
        rows_by_season[season] = rsch0009.build_season_rows(season, team_games, relief_er9, env_lookup)

    dev_home_team_games = [g for s in DEV_SEASONS for g in team_games_by_season[s].values()]
    league_avg_offense = rsch0009.fit_league_average_runs_per_game(dev_home_team_games)
    dev_relief_er9_team_games = [g for s in DEV_SEASONS for g in relief_by_season[s].values()]
    league_avg_bullpen_er9 = rsch0009.fit_league_average_bullpen_er9(dev_relief_er9_team_games)

    return rows_by_season, team_games_by_season, relief_by_season, league_avg_offense, league_avg_bullpen_er9


# ── O1: closed-form, DEV-only empirical-Bayes shrinkage constant ─────────

def fit_empirical_bayes_offense_k_dev_only(team_games_by_season, league_avg_offense):
    """
    Pure (given its inputs). Standard normal-normal conjugate empirical-
    Bayes shrinkage constant k_hat = sigma^2 / tau^2, fit via closed-form
    method-of-moments over DEVELOPMENT team-seasons only -- NOT
    iterative, NOT tuned against any held-out result:
      sigma^2 (within-team-game variance): pooled mean of each eligible
        team-season's own SAMPLE variance of individual game runsScored
        around that team-season's own mean, weighted by game count.
      tau^2 (between-team talent variance): the standard ANOVA-style
        unbiased estimator -- Var(team-season means) minus the average
        sampling-error component sigma^2/mean(n) -- floored at a small
        positive epsilon (never zero/negative, which would make k
        undefined/nonsensical).
    Eligibility mirrors team_baseline's own rule (>= MIN_PRIOR_GAMES_FOR_BASELINE
    games) -- a team-season with too few games contributes no stable
    variance estimate and is excluded, never included with an unstable one.
    Returns (k_hat, diagnostics dict).
    """
    from lib.edgelab.backtest.team_offense_recency_reconstruction import MIN_PRIOR_GAMES_FOR_BASELINE

    team_season_means, team_season_vars, team_season_ns = [], [], []
    for season in DEV_SEASONS:
        team_games = team_games_by_season[season]
        for team_id, games in team_games.items():
            home_games_this_season = [g for g in games if g.get("runsScored") is not None]
            if len(home_games_this_season) < MIN_PRIOR_GAMES_FOR_BASELINE:
                continue
            values = [g["runsScored"] for g in home_games_this_season]
            n = len(values)
            mean_v = sum(values) / n
            var_v = sum((v - mean_v) ** 2 for v in values) / (n - 1) if n > 1 else None
            if var_v is None:
                continue
            team_season_means.append(mean_v)
            team_season_vars.append(var_v)
            team_season_ns.append(n)

    if not team_season_vars:
        return OFFENSE_SHRINKAGE_K, {"fallback": "no eligible team-seasons -- returned the current fixed constant unchanged"}

    total_n = sum(team_season_ns)
    sigma2 = sum(v * n for v, n in zip(team_season_vars, team_season_ns)) / total_n
    mean_n = total_n / len(team_season_ns)
    grand_mean = sum(m * n for m, n in zip(team_season_means, team_season_ns)) / total_n
    between_var = sum(n * (m - grand_mean) ** 2 for m, n in zip(team_season_means, team_season_ns)) / (len(team_season_means) - 1) if len(team_season_means) > 1 else 0.0
    tau2 = max(between_var - sigma2 / mean_n, 1e-4)  # floored -- never zero/negative
    k_hat = round(sigma2 / tau2, 4)

    return k_hat, {
        "teamSeasonsUsed": len(team_season_vars),
        "sigma2WithinTeamGame": round(sigma2, 4),
        "tau2BetweenTeamTalent": round(tau2, 4),
        "grandMeanOffense": round(grand_mean, 4),
        "kHat": k_hat,
        "currentFixedK": OFFENSE_SHRINKAGE_K,
    }


# ── Offense candidate dispatch + row prediction ────────────────────────────

def offense_component_for(candidate, raw_rate, prior_games, league_avg_offense, k_o1=None):
    if candidate == O0:
        return stabilized_offense_rate(raw_rate, prior_games, league_avg_offense, k=OFFENSE_SHRINKAGE_K)
    if candidate == O1:
        return stabilized_offense_rate(raw_rate, prior_games, league_avg_offense, k=k_o1)
    raise ValueError(f"{candidate!r} is not yet implemented -- requires the batting boxscore cache (O2/O3/O4)")


def _bullpen_stabilized(raw_bullpen, league_avg_bullpen_er9):
    if raw_bullpen is None:
        return None
    return stabilized_bullpen_rate(raw_bullpen["bullpenEarnedRunsPer9"], raw_bullpen["priorGamesWithBullpenData"], league_avg_bullpen_er9)


def _hfa_fit_rows_for_candidate(rows, candidate, league_avg_offense, league_avg_bullpen_er9, k_o1=None):
    out = []
    for r in rows:
        hb_off = offense_component_for(candidate, r["homeBaselineRaw"]["offenseRunsPerGame"], r["homeBaselineRaw"]["priorGamesThisSeason"], league_avg_offense, k_o1)
        ab_off = offense_component_for(candidate, r["awayBaselineRaw"]["offenseRunsPerGame"], r["awayBaselineRaw"]["priorGamesThisSeason"], league_avg_offense, k_o1)
        hb_bp = _bullpen_stabilized(r["homeBullpenRaw"], league_avg_bullpen_er9)
        ab_bp = _bullpen_stabilized(r["awayBullpenRaw"], league_avg_bullpen_er9)
        hb = dict(r["homeBaselineRaw"], offenseRunsPerGame=hb_off, runPreventionRunsAllowedPerGame=blend_run_prevention_with_bullpen_quality(r["homeBaselineRaw"]["runPreventionRunsAllowedPerGame"], hb_bp))
        ab = dict(r["awayBaselineRaw"], offenseRunsPerGame=ab_off, runPreventionRunsAllowedPerGame=blend_run_prevention_with_bullpen_quality(r["awayBaselineRaw"]["runPreventionRunsAllowedPerGame"], ab_bp))
        out.append({"homeBaseline": hb, "awayBaseline": ab, "actualHomeRuns": r["actualHomeRuns"], "actualAwayRuns": r["actualAwayRuns"]})
    return out


def fit_hfa_for_candidate(dev_rows, candidate, league_avg_offense, league_avg_bullpen_er9, k_o1=None):
    return fit_home_field_adjustment(_hfa_fit_rows_for_candidate(dev_rows, candidate, league_avg_offense, league_avg_bullpen_er9, k_o1))


def attach_predictions(rows, candidate, key_prefix, home_field_adjustment, league_avg_offense, league_avg_bullpen_er9, k_o1=None):
    """Mutates each row in place, adding f'homeExpectedRuns_{key_prefix}'/f'awayExpectedRuns_{key_prefix}'."""
    for r in rows:
        hb_off = offense_component_for(candidate, r["homeBaselineRaw"]["offenseRunsPerGame"], r["homeBaselineRaw"]["priorGamesThisSeason"], league_avg_offense, k_o1)
        ab_off = offense_component_for(candidate, r["awayBaselineRaw"]["offenseRunsPerGame"], r["awayBaselineRaw"]["priorGamesThisSeason"], league_avg_offense, k_o1)
        hb_bp = _bullpen_stabilized(r["homeBullpenRaw"], league_avg_bullpen_er9)
        ab_bp = _bullpen_stabilized(r["awayBullpenRaw"], league_avg_bullpen_er9)
        hb = dict(r["homeBaselineRaw"], offenseRunsPerGame=hb_off, runPreventionRunsAllowedPerGame=blend_run_prevention_with_bullpen_quality(r["homeBaselineRaw"]["runPreventionRunsAllowedPerGame"], hb_bp))
        ab = dict(r["awayBaselineRaw"], offenseRunsPerGame=ab_off, runPreventionRunsAllowedPerGame=blend_run_prevention_with_bullpen_quality(r["awayBaselineRaw"]["runPreventionRunsAllowedPerGame"], ab_bp))
        eh, ea = expected_runs(hb, ab, home_field_adjustment=home_field_adjustment)
        r[f"homeExpectedRuns_{key_prefix}"] = eh
        r[f"awayExpectedRuns_{key_prefix}"] = ea


# ── Team-level mean-accuracy evaluation ─────────────────────────────────

def team_observations(rows, key_prefix):
    """One entry per team-game-side (2 per row) -- {gamePk, teamId, season, gameNumber, priorGames, predicted, actual}."""
    obs = []
    for r in rows:
        eh, ea = r.get(f"homeExpectedRuns_{key_prefix}"), r.get(f"awayExpectedRuns_{key_prefix}")
        if eh is not None and r.get("actualHomeRuns") is not None:
            obs.append({
                "gamePk": r["gamePk"], "teamId": r["homeTeamId"], "season": r["season"], "gameNumber": r.get("gameNumber"),
                "priorGames": r["homeBaselineRaw"]["priorGamesThisSeason"], "predicted": eh, "actual": r["actualHomeRuns"],
            })
        if ea is not None and r.get("actualAwayRuns") is not None:
            obs.append({
                "gamePk": r["gamePk"], "teamId": r["awayTeamId"], "season": r["season"], "gameNumber": r.get("gameNumber"),
                "priorGames": r["awayBaselineRaw"]["priorGamesThisSeason"], "predicted": ea, "actual": r["actualAwayRuns"],
            })
    return obs


def mean_accuracy_metrics(obs):
    if not obs:
        return {"n": 0, "independentGames": 0, "mae": None, "rmse": None, "bias": None, "residualVariance": None}
    errors = [o["predicted"] - o["actual"] for o in obs]
    n = len(errors)
    mae = round(sum(abs(e) for e in errors) / n, 4)
    rmse = round(math.sqrt(sum(e ** 2 for e in errors) / n), 4)
    bias = round(sum(errors) / n, 4)
    residual_variance = round(sum((e - bias) ** 2 for e in errors) / n, 4)
    independent_games = independent_unit_count(obs, key="gamePk")
    return {
        "n": n, "independentGames": independent_games,
        "sampleSizeStatus": sample_size_status(n, independent_games=independent_games),
        "mae": mae, "rmse": rmse, "bias": bias, "residualVariance": residual_variance,
    }


def paired_mean_mae_delta(obs_a, obs_b, key_a="predicted_a", key_b="predicted_b"):
    """Paired (candidate B minus A) MAE delta with a game-clustered
    bootstrap CI. obs_a/obs_b must align 1:1 by (gamePk, teamId) --
    verified by key construction, never assumed by position."""
    by_key_a = {(o["gamePk"], o["teamId"]): o for o in obs_a}
    by_key_b = {(o["gamePk"], o["teamId"]): o for o in obs_b}
    common = sorted(set(by_key_a) & set(by_key_b))
    paired_rows = [{
        "gamePk": k[0], "teamId": k[1],
        "errA": abs(by_key_a[k]["predicted"] - by_key_a[k]["actual"]),
        "errB": abs(by_key_b[k]["predicted"] - by_key_b[k]["actual"]),
    } for k in common]

    def _delta(subset):
        if not subset:
            return None
        return sum(r["errB"] - r["errA"] for r in subset) / len(subset)

    point = _delta(paired_rows)
    lo, hi, _ = game_clustered_bootstrap_ci(paired_rows, _delta, cluster_key="gamePk", seed=DEFAULT_BOOTSTRAP_SEED)
    return {
        "n": len(paired_rows), "independentGames": independent_unit_count(paired_rows, key="gamePk"),
        "maeDelta": round(point, 6) if point is not None else None,
        "maeDeltaCI95": {"low": lo, "high": hi, "method": "GAME_CLUSTERED_BOOTSTRAP"},
        "interpretation": "negative == candidate B improved (lower MAE than A)",
    }


def season_band_breakdown(obs_a, obs_b):
    """Per SEASON_BANDS, the paired MAE delta -- keyed by the OBSERVATION's
    own priorGames (season-progress-at-prediction-time), never chosen
    after inspecting results."""
    out = {}
    for band_name, lo_games, hi_games in SEASON_BANDS:
        def _in_band(o):
            return o["priorGames"] >= lo_games and (hi_games is None or o["priorGames"] <= hi_games)
        band_a = [o for o in obs_a if _in_band(o)]
        band_b = [o for o in obs_b if _in_band(o)]
        out[band_name] = paired_mean_mae_delta(band_a, band_b)
    return out


def team_robustness(obs_a, obs_b):
    """Per-team paired MAE delta (never a single pooled number hiding a
    one-team-driven effect) + leave-one-team-out re-computation of the
    overall delta, so a genuine improvement can be distinguished from an
    artifact of a handful of teams."""
    by_key_a = {(o["gamePk"], o["teamId"]): o for o in obs_a}
    by_key_b = {(o["gamePk"], o["teamId"]): o for o in obs_b}
    common = sorted(set(by_key_a) & set(by_key_b))
    team_ids = sorted({k[1] for k in common})

    per_team = {}
    for team_id in team_ids:
        team_keys = [k for k in common if k[1] == team_id]
        team_obs_a = [by_key_a[k] for k in team_keys]
        team_obs_b = [by_key_b[k] for k in team_keys]
        errors_a = [abs(o["predicted"] - o["actual"]) for o in team_obs_a]
        errors_b = [abs(o["predicted"] - o["actual"]) for o in team_obs_b]
        if not errors_a:
            continue
        per_team[str(team_id)] = round(sum(errors_b) / len(errors_b) - sum(errors_a) / len(errors_a), 4)

    overall_delta = round(sum(per_team.values()) / len(per_team), 4) if per_team else None
    leave_one_out = {}
    for excluded in per_team:
        remaining = [v for tid, v in per_team.items() if tid != excluded]
        leave_one_out[excluded] = round(sum(remaining) / len(remaining), 4) if remaining else None

    deltas = list(per_team.values())
    return {
        "perTeamMaeDelta": per_team,
        "overallMeanOfPerTeamDeltas": overall_delta,
        "leaveOneTeamOutDeltas": leave_one_out,
        "leaveOneTeamOutRange": {"min": round(min(leave_one_out.values()), 4), "max": round(max(leave_one_out.values()), 4)} if leave_one_out else None,
        "nTeamsPositive": sum(1 for d in deltas if d < 0),  # negative delta == improvement
        "nTeamsNegative": sum(1 for d in deltas if d > 0),
        "nTeamsTotal": len(deltas),
    }


# ── Frozen-NB probability evaluation (dispersion NEVER refit here) ───────

def _nb_joint(home_mean, away_mean, dispersion=FROZEN_DISPERSION):
    def home_pmf(k):
        return negative_binomial_pmf(k, home_mean, dispersion)

    def away_pmf(k):
        return negative_binomial_pmf(k, away_mean, dispersion)
    return independent_joint_pmf(home_pmf, away_pmf), home_pmf, away_pmf


def nb_probability_cells(home_mean, away_mean):
    """Pure. Same cell-key scheme as lib.edgelab.shadow_distribution,
    but BOTH sides of the comparison use the SAME frozen NB distribution
    (only the underlying MEAN differs) -- isolates mean-quality
    improvement from any distribution-layer effect."""
    if home_mean is None or away_mean is None or home_mean <= 0 or away_mean <= 0:
        return None
    joint, home_pmf, away_pmf = _nb_joint(home_mean, away_mean)
    cells = {}
    home_win, push = home_win_and_push_prob(joint)
    cells["moneyline_home_win"] = home_win
    cells["moneyline_away_win"] = 1.0 - home_win - push
    for line in GAME_TOTAL_LINES:
        cells[f"game_total_over_{line}"] = total_over_prob(joint, line)
    for line in TEAM_TOTAL_LINES:
        cells[f"team_total_away_over_{line}"] = team_total_over_prob(away_pmf, line)
        cells[f"team_total_home_over_{line}"] = team_total_over_prob(home_pmf, line)
    return cells


def _outcomes_for_actual(actual_home, actual_away):
    actual_total = actual_home + actual_away
    outcomes = {
        "moneyline_home_win": 1 if actual_home > actual_away else 0,
        "moneyline_away_win": 1 if actual_away > actual_home else 0,
    }
    for line in GAME_TOTAL_LINES:
        outcomes[f"game_total_over_{line}"] = 1 if actual_total > line else 0
    for line in TEAM_TOTAL_LINES:
        outcomes[f"team_total_away_over_{line}"] = 1 if actual_away > line else 0
        outcomes[f"team_total_home_over_{line}"] = 1 if actual_home > line else 0
    return outcomes


def frozen_nb_probability_eval(rows, key_a, key_b):
    """Paired frozen-NB probability comparison (candidate B minus A),
    reusing lib.edgelab.paired_evaluation exactly as every other
    Research Lab milestone does. Returns {"overall": ..., "byFamily": ...}."""
    control_rows, candidate_rows = [], []
    for r in rows:
        actual_home, actual_away = r.get("actualHomeRuns"), r.get("actualAwayRuns")
        if actual_home is None or actual_away is None:
            continue
        cells_a = nb_probability_cells(r.get(f"homeExpectedRuns_{key_a}"), r.get(f"awayExpectedRuns_{key_a}"))
        cells_b = nb_probability_cells(r.get(f"homeExpectedRuns_{key_b}"), r.get(f"awayExpectedRuns_{key_b}"))
        if cells_a is None or cells_b is None:
            continue
        outcomes = _outcomes_for_actual(actual_home, actual_away)
        for cell_key, outcome in outcomes.items():
            control_rows.append({"gameId": r["gamePk"], "cellKey": cell_key, "gameDate": r.get("date"), "modelFairProbability": cells_a[cell_key], "outcome": outcome})
            candidate_rows.append({"gameId": r["gamePk"], "cellKey": cell_key, "gameDate": r.get("date"), "modelFairProbability": cells_b[cell_key], "outcome": outcome})

    def key_fn(row):
        return (row["gameId"], row["cellKey"])

    pairing = pe.pair_eligible_observations(control_rows, candidate_rows, key_fn=key_fn)
    overall = pe.evaluate_probability_model_pair(pairing, game_key="gameId", date_key="gameDate")

    by_family = {}
    for family_prefix in ("game_total", "team_total_away", "team_total_home", "moneyline"):
        fam_control = [r for r in control_rows if r["cellKey"].startswith(family_prefix)]
        fam_candidate = [r for r in candidate_rows if r["cellKey"].startswith(family_prefix)]
        fam_pairing = pe.pair_eligible_observations(fam_control, fam_candidate, key_fn=key_fn)
        by_family[family_prefix] = pe.evaluate_probability_model_pair(fam_pairing, game_key="gameId", date_key="gameDate")

    return {"overall": overall, "byFamily": by_family}


# ── Selection rule (preregistered) ─────────────────────────────────────────

def selection_passes(dev_mae_delta, val_mae_delta, band_deltas, val_nb_primary_delta):
    """
    Preregistered selection rule -- see module docstring's MODEL
    SELECTION section. Returns (passes: bool, reasons: list[str]).
    """
    reasons = []
    if dev_mae_delta is None or dev_mae_delta >= 0:
        reasons.append(f"DEV MAE delta not negative (improved): {dev_mae_delta}")
    if val_mae_delta is not None and val_mae_delta > DEGRADATION_TOLERANCE:
        reasons.append(f"VALIDATION MAE delta degraded beyond tolerance {DEGRADATION_TOLERANCE}: {val_mae_delta}")
    early_band = band_deltas.get("games_1_15", {}).get("maeDelta")
    late_bands_all_null_or_worse = all(
        (band_deltas.get(b, {}).get("maeDelta") is None or band_deltas.get(b, {}).get("maeDelta") >= 0)
        for b in ("games_16_40", "games_41_80", "games_81_plus")
    )
    if early_band is not None and early_band < 0 and late_bands_all_null_or_worse:
        reasons.append("improvement confined to games_1_15 only -- fails the 'not restricted to first few games' criterion")
    if val_nb_primary_delta is not None and val_nb_primary_delta > PROBABILITY_DEGRADATION_TOLERANCE:
        reasons.append(f"VALIDATION frozen-NB primary Brier delta degraded beyond tolerance {PROBABILITY_DEGRADATION_TOLERANCE}: {val_nb_primary_delta}")
    return (len(reasons) == 0), reasons


# ── main ─────────────────────────────────────────────────────────────────

def main():
    print(f"[{EXPERIMENT_ID}] registering experiment/control...")
    control, definition = register_experiment()

    print(f"[{EXPERIMENT_ID}] building corpus (2022-2026, reusing MLB-RSCH-0009 loaders unchanged)...")
    rows_by_season, team_games_by_season, relief_by_season, league_avg_offense, league_avg_bullpen_er9 = build_corpus()
    dev_rows = [r for s in DEV_SEASONS for r in rows_by_season[s]]
    val_rows = [r for s in VALIDATION_SEASONS for r in rows_by_season[s]]
    holdout_rows = [r for s in HOLDOUT_SEASONS for r in rows_by_season[s]]
    all_rows = dev_rows + val_rows + holdout_rows
    print(f"[{EXPERIMENT_ID}] rows: dev={len(dev_rows)} val={len(val_rows)} holdout={len(holdout_rows)} total={len(all_rows)}")

    print(f"[{EXPERIMENT_ID}] fitting O1 empirical-Bayes k on DEVELOPMENT only...")
    k_o1, k_diagnostics = fit_empirical_bayes_offense_k_dev_only(team_games_by_season, league_avg_offense)
    print(f"[{EXPERIMENT_ID}] O1 k_hat={k_o1} (current fixed O0 k={OFFENSE_SHRINKAGE_K}) diagnostics={k_diagnostics}")

    hfa_o0 = fit_hfa_for_candidate(dev_rows, O0, league_avg_offense, league_avg_bullpen_er9)
    hfa_o1 = fit_hfa_for_candidate(dev_rows, O1, league_avg_offense, league_avg_bullpen_er9, k_o1=k_o1)
    for rows in (dev_rows, val_rows, holdout_rows):
        attach_predictions(rows, O0, "O0", hfa_o0, league_avg_offense, league_avg_bullpen_er9)
        attach_predictions(rows, O1, "O1", hfa_o1, league_avg_offense, league_avg_bullpen_er9, k_o1=k_o1)

    obs_dev_o0, obs_dev_o1 = team_observations(dev_rows, "O0"), team_observations(dev_rows, "O1")
    obs_val_o0, obs_val_o1 = team_observations(val_rows, "O0"), team_observations(val_rows, "O1")
    obs_holdout_o0, obs_holdout_o1 = team_observations(holdout_rows, "O0"), team_observations(holdout_rows, "O1")

    dev_o0_metrics, dev_o1_metrics = mean_accuracy_metrics(obs_dev_o0), mean_accuracy_metrics(obs_dev_o1)
    val_o0_metrics, val_o1_metrics = mean_accuracy_metrics(obs_val_o0), mean_accuracy_metrics(obs_val_o1)
    holdout_o0_metrics, holdout_o1_metrics = mean_accuracy_metrics(obs_holdout_o0), mean_accuracy_metrics(obs_holdout_o1)

    dev_delta = paired_mean_mae_delta(obs_dev_o0, obs_dev_o1)
    val_delta = paired_mean_mae_delta(obs_val_o0, obs_val_o1)
    holdout_delta = paired_mean_mae_delta(obs_holdout_o0, obs_holdout_o1)
    print(f"[{EXPERIMENT_ID}] O1 vs O0 MAE delta: dev={dev_delta['maeDelta']} val={val_delta['maeDelta']} holdout={holdout_delta['maeDelta']}")

    dev_bands = season_band_breakdown(obs_dev_o0, obs_dev_o1)
    val_bands = season_band_breakdown(obs_val_o0, obs_val_o1)
    dev_team_robustness = team_robustness(obs_dev_o0, obs_dev_o1)

    val_nb = frozen_nb_probability_eval(val_rows, "O0", "O1")
    val_nb_primary_deltas = [
        val_nb["byFamily"][fam]["pairedDelta"]["brierScore"]
        for fam in ("game_total", "team_total_away", "team_total_home")
        if val_nb["byFamily"].get(fam) and val_nb["byFamily"][fam]["pairedDelta"]["brierScore"] is not None
    ]
    val_nb_primary_delta = round(sum(val_nb_primary_deltas) / len(val_nb_primary_deltas), 6) if val_nb_primary_deltas else None

    passes, reasons = selection_passes(dev_delta["maeDelta"], val_delta["maeDelta"], dev_bands, val_nb_primary_delta)
    final_offense_model = O1 if passes else O0
    print(f"[{EXPERIMENT_ID}] selection: O1 passes={passes} reasons={reasons} -> final={final_offense_model}")

    # ---- Unlock 2026 holdout (only after selection is frozen) ----
    holdout_nb = frozen_nb_probability_eval(holdout_rows, "O0", "O1")

    # ---- Pinnacle secondary stage (existing MLB-RSCH-0008/0009 sample, O0 vs O1 only) ----
    print(f"[{EXPERIMENT_ID}] Pinnacle secondary stage (existing sample, no new Odds API spend)...")
    import run_proxy_vs_pinnacle_experiment as rsch0008
    pinnacle_result = None
    try:
        pinnacle_all_rows = [r for season in ALL_SEASONS for r in rsch0008.build_matched_rows(season)]
        for r in pinnacle_all_rows:
            rsch0008.enrich_row(r, hfa_o0)  # populates pinnacleMlHomeFair/actualHomeWin -- HFA choice here doesn't affect those fields
            for candidate, key, k_val, hfa in ((O0, "O0", None, hfa_o0), (O1, "O1", k_o1, hfa_o1)):
                hb_off = offense_component_for(candidate, r["homeBaseline"]["offenseRunsPerGame"], r["homeBaseline"]["priorGamesThisSeason"], league_avg_offense, k_val)
                ab_off = offense_component_for(candidate, r["awayBaseline"]["offenseRunsPerGame"], r["awayBaseline"]["priorGamesThisSeason"], league_avg_offense, k_val)
                hb = dict(r["homeBaseline"], offenseRunsPerGame=hb_off)
                ab = dict(r["awayBaseline"], offenseRunsPerGame=ab_off)
                eh, ea = expected_runs(hb, ab, home_field_adjustment=hfa)
                r[f"mlHomeProb_{key}"], _ = game_ml_proxy_probability(eh, ea) if eh is not None and ea is not None else (None, None)
        pinnacle_o0 = rsch0008.paired_analysis(pinnacle_all_rows, "mlHomeProb_O0", "pinnacleMlHomeFair", "actualHomeWin", "PINNACLE/ML/O0")
        pinnacle_o1 = rsch0008.paired_analysis(pinnacle_all_rows, "mlHomeProb_O1", "pinnacleMlHomeFair", "actualHomeWin", "PINNACLE/ML/O1")
        pinnacle_result = {"nRows": len(pinnacle_all_rows), "o0": pinnacle_o0, "o1": pinnacle_o1}
    except Exception as exc:  # secondary stage only -- must never abort the primary result
        pinnacle_result = {"error": str(exc)}
    print(f"[{EXPERIMENT_ID}] Pinnacle secondary stage result: {pinnacle_result if isinstance(pinnacle_result, dict) and 'error' in pinnacle_result else 'OK'}")

    report = {
        "experimentId": EXPERIMENT_ID,
        "controlModelId": control["controlModelId"],
        "implementedCandidates": list(IMPLEMENTED_CANDIDATES),
        "notImplementedCandidates": {
            "candidates": [O2, O3, O4],
            "reason": "await data/research_cache/batting_backtest/ -- see docs/EDGELAB_MLB_RSCH_0012_OFFENSE_TALENT.md",
        },
        "corpus": {"devRows": len(dev_rows), "valRows": len(val_rows), "holdoutRows": len(holdout_rows), "totalRows": len(all_rows)},
        "empiricalBayesK": {"kHat": k_o1, "currentFixedK": OFFENSE_SHRINKAGE_K, "diagnostics": k_diagnostics},
        "homeFieldAdjustment": {"O0": hfa_o0, "O1": hfa_o1},
        "meanAccuracy": {
            "dev": {"O0": dev_o0_metrics, "O1": dev_o1_metrics, "pairedDelta": dev_delta},
            "validation": {"O0": val_o0_metrics, "O1": val_o1_metrics, "pairedDelta": val_delta},
            "holdout2026": {"O0": holdout_o0_metrics, "O1": holdout_o1_metrics, "pairedDelta": holdout_delta},
        },
        "seasonBands": {"dev": dev_bands, "validation": val_bands},
        "teamRobustnessDev": dev_team_robustness,
        "frozenNbProbability": {"validation": val_nb, "holdout2026": holdout_nb},
        "selection": {"o1PassesSelectionRule": passes, "reasons": reasons, "finalOffenseModel": final_offense_model},
        "pinnacleSecondary": pinnacle_result,
    }

    out_path = os.path.join("data", "edgelab", "analytics", "latest_mlb_rsch_0012_offense_talent.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    print(f"[{EXPERIMENT_ID}] wrote {out_path}")
    return report


if __name__ == "__main__":
    main()
