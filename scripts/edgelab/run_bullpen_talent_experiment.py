#!/usr/bin/env python3
"""
scripts/edgelab/run_bullpen_talent_experiment.py
====================================================================
Research Lab experiment MLB-RSCH-0013: "Bullpen Talent Refinement".
RESEARCH ONLY. NO production changes.

MLB-RSCH-0009 found bullpen quality added real signal to the mean
model. This milestone asks the natural mirror of MLB-RSCH-0012's own
question: is production's CURRENT bullpen shrinkage constant (a fixed
k=30, same shape/discipline as the offense constant MLB-RSCH-0012 just
tested) close to a data-implied empirical-Bayes optimum, or does a
DEV-fit constant materially change bullpen-quality estimates?

Deliberately scoped to STABILIZATION ONLY (mirrors MLB-RSCH-0012's O0/O1
exactly, on the bullpen side) -- NOT component pitching rates (K%, BB%,
HR rate), which would require a new MLB Stats API fetch of per-pitcher
strikeout/walk/homerun fields the existing MLB-RSCH-0003 bullpen cache
never persisted (only outs/runs/earnedRuns/numberOfPitches/saves/holds
were kept -- see lib.edgelab.backtest.bullpen_backtest_reconstruction.
extract_pitcher_lines). Using the ALREADY-CACHED
data/research_cache/bullpen_backtest/ ER9 data (zero new network calls,
zero new GitHub Actions minutes) is the resource-efficient choice for
this specific question tonight; a genuine bullpen-component experiment
(K%/BB%/HR-rate) is a natural, separate future milestone once that data
is fetched (analogous to MLB-RSCH-0012's O2/O3).

Deliberately EXCLUDES workload/fatigue as the object of study --
MLB-RSCH-0003 already found weak/non-replicating workload evidence; this
is a TALENT (rate quality) estimation question, not a recency/workload one.

HOLDS OFFENSE FIXED: uses MLB-RSCH-0009's own frozen offense component
(stabilized_offense_rate, k=30) UNCHANGED -- this experiment's ENTIRE
scope is the bullpen side, mirroring MLB-RSCH-0012's own "freeze one
side, vary the other" discipline in reverse. MLB-RSCH-0012's own O1
(offense shrinkage) finding was NOT validated (NO MEANINGFUL IMPROVEMENT,
holdout reversal) and is NOT used here -- the offense side stays exactly
at MLB-RSCH-0009's original frozen composition.

CANDIDATES:
  P0 (control): production's current bullpen component, reproduced
      EXACTLY -- stabilized_bullpen_rate(raw, priorGames, leagueAvg,
      k=BULLPEN_SHRINKAGE_K=30).
  P1: SAME shrinkage formula, k fit via closed-form empirical-Bayes
      (k_hat = sigma^2/tau^2) on DEVELOPMENT bullpen ER9 data only.

PROBABILITY EVALUATION: reuses the frozen MLB-RSCH-0010 negative-binomial
distribution UNCHANGED -- dispersion is NEVER refit here.

MODEL SELECTION (preregistered before any real result was computed):
identical rule to MLB-RSCH-0012 -- a candidate may replace P0 only if
(1) DEV mean-accuracy improves, (2) VAL degradation is within tolerance,
(3) improvement is not confined to the (empty, same inherited 20-game
floor) games_1_15 band, (4) VAL frozen-NB primary Brier delta is within
tolerance. 2026/Pinnacle are never consulted during selection.
"""
import json
import os
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

from lib.edgelab.backtest.proxy_model import expected_runs, fit_home_field_adjustment, game_ml_proxy_probability
from lib.edgelab.backtest.proxy_enrichment import (
    BULLPEN_SHRINKAGE_K,
    OFFENSE_SHRINKAGE_K,
    stabilized_offense_rate,
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
from lib.edgelab.research_stats import independent_unit_count, sample_size_status, game_clustered_bootstrap_ci, DEFAULT_BOOTSTRAP_SEED

import run_proxy_ablation_experiment as rsch0009  # noqa: E402

EXPERIMENT_ID = "MLB-RSCH-0013"
REGISTRATION_TIMESTAMP = "2026-08-28T06:15:00Z"

DEV_SEASONS = [2022, 2023, 2024]
VALIDATION_SEASONS = [2025]
HOLDOUT_SEASONS = [2026]
ALL_SEASONS = DEV_SEASONS + VALIDATION_SEASONS + HOLDOUT_SEASONS

P0 = "P0_control_current"
P1 = "P1_empirical_bayes_shrinkage"

SEASON_BANDS = (
    ("games_1_15", 1, 15),
    ("games_16_40", 16, 40),
    ("games_41_80", 41, 80),
    ("games_81_plus", 81, None),
)

GAME_TOTAL_LINES = (7.5, 8.5, 9.5, 10.5)
TEAM_TOTAL_LINES = (2.5, 3.5, 4.5, 5.5)

DEGRADATION_TOLERANCE = 0.05
PROBABILITY_DEGRADATION_TOLERANCE = 0.005
MIN_GAMES_CONFIDENT = 50

FROZEN_DISPERSION = 0.281513


def _verify_frozen_dispersion():
    path = os.path.join(_ROOT, "data", "edgelab", "analytics", "latest_mlb_rsch_0010_run_distribution.json")
    with open(path) as f:
        canonical = json.load(f)["fittedParameters"]["overdispersion"]
    if canonical != FROZEN_DISPERSION:
        raise ValueError(f"FROZEN_DISPERSION={FROZEN_DISPERSION} does not match canonical artifact value {canonical}")


_verify_frozen_dispersion()


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
        name="mlb_rsch_0013_bullpen_talent_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0013 bullpen talent v1: MLB-RSCH-0009 frozen offense component (unchanged) + "
                        "forward-selected bullpen shrinkage from {P0 current-fixed-k=30, P1 empirical-Bayes shrinkage}"
        ),
        probability_adapter_identity="lib.edgelab.backtest.run_distributions (frozen MLB-RSCH-0010 negative-binomial, dispersion unchanged)",
        model_engine_family="pit_safe_research_bullpen_talent_v1",
        required_input_provenance=["team_recent_game_log_reconstruction"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "A NEW historical RESEARCH bullpen-shrinkage layer, built on top of MLB-RSCH-0009's frozen "
            "offense component (unchanged). Tests whether P0 (production's current fixed-k=30 bullpen "
            "shrinkage) is beaten by P1 (dev-fit empirical-Bayes k), using the ALREADY-CACHED MLB-RSCH-0003 "
            "bullpen ER9 corpus -- zero new data acquisition."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Bullpen Talent Refinement",
        hypothesis=(
            "H1: production's current FIXED bullpen shrinkage constant (k=30) is close enough to a DEV-fit "
            "empirical-Bayes optimum that no material improvement is available from a better-fit shrinkage "
            "alone (P1 vs P0) -- mirroring MLB-RSCH-0012's own offense-side finding."
        ),
        research_question=(
            "Holding MLB-RSCH-0009's frozen offense component fixed, does a DEV-fit empirical-Bayes bullpen-"
            "ER9 shrinkage constant produce a repeatable improvement in next-game team runs allowed (and, "
            "under the frozen MLB-RSCH-0010 distribution, downstream probabilities) over production's "
            "current fixed constant?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E2_PIT_HISTORICAL,
        target_population=(
            "The same MLB regular-season 2022-2026 games MLB-RSCH-0009's own baseline used (both teams "
            ">= MIN_PRIOR_GAMES_FOR_BASELINE prior completed games this season, AND >= MIN_PRIOR_GAMES_FOR_BASELINE "
            "games with defined relief ER9) -- reused via that milestone's own row-building functions, unchanged."
        ),
        market_families=["game_result", "game_total", "team_total"],
        eligibility_criteria=[
            "both teams have >= MIN_PRIOR_GAMES_FOR_BASELINE prior completed games this season (MLB-RSCH-0009's own eligibility rule, unchanged)",
            "both teams have >= MIN_PRIOR_GAMES_FOR_BASELINE prior games with a DEFINED relief ER9 (bullpen_quality_baseline's own rule, unchanged)",
        ],
        exclusion_criteria=[
            "component pitching rates (K%, BB%, HR rate) -- would require new data acquisition not done this pass; see module docstring",
            "workload/fatigue features of any kind -- MLB-RSCH-0003 found weak/non-replicating evidence; this is a TALENT (rate-quality) estimation question, not recency/workload",
        ],
        prediction_checkpoints=["SEASON_TO_DATE_PREGAME"],
        primary_metric="paired MAE delta on next-game team runs scored (candidate minus P0), game-and-team-clustered 95% CI",
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
            f"FROZEN dispersion={FROZEN_DISPERSION}, never refit. Reuses the ALREADY-CACHED "
            "data/research_cache/bullpen_backtest/ ER9 corpus (MLB-RSCH-0003) -- zero new network calls this "
            "milestone. Mirrors MLB-RSCH-0012's O0/O1 methodology exactly, applied to the bullpen side, "
            "offense held at MLB-RSCH-0009's original frozen composition (MLB-RSCH-0012's own O1 finding was "
            "NOT validated and is not used here)."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


def build_corpus():
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

    return rows_by_season, relief_by_season, league_avg_offense, league_avg_bullpen_er9


def fit_empirical_bayes_bullpen_k_dev_only(relief_by_season, league_avg_bullpen_er9):
    """
    Same closed-form method-of-moments empirical-Bayes estimator as
    MLB-RSCH-0012's fit_empirical_bayes_offense_k_dev_only, applied to
    each DEV team-season's own DEFINED reliefEarnedRunsPer9 values
    (games with no relief innings, e.g. a complete-game start, have no
    defined ER9 and are excluded from the variance estimate -- never
    treated as 0).
    """
    from lib.edgelab.backtest.team_offense_recency_reconstruction import MIN_PRIOR_GAMES_FOR_BASELINE

    team_season_means, team_season_vars, team_season_ns = [], [], []
    for season in DEV_SEASONS:
        relief_games = relief_by_season[season]
        for team_id, games in relief_games.items():
            defined = [g["reliefEarnedRunsPer9"] for g in games if g.get("reliefEarnedRunsPer9") is not None]
            if len(defined) < MIN_PRIOR_GAMES_FOR_BASELINE:
                continue
            n = len(defined)
            mean_v = sum(defined) / n
            var_v = sum((v - mean_v) ** 2 for v in defined) / (n - 1) if n > 1 else None
            if var_v is None:
                continue
            team_season_means.append(mean_v)
            team_season_vars.append(var_v)
            team_season_ns.append(n)

    if not team_season_vars:
        return BULLPEN_SHRINKAGE_K, {"fallback": "no eligible team-seasons -- returned the current fixed constant unchanged"}

    total_n = sum(team_season_ns)
    sigma2 = sum(v * n for v, n in zip(team_season_vars, team_season_ns)) / total_n
    mean_n = total_n / len(team_season_ns)
    grand_mean = sum(m * n for m, n in zip(team_season_means, team_season_ns)) / total_n
    between_var = sum(n * (m - grand_mean) ** 2 for m, n in zip(team_season_means, team_season_ns)) / (len(team_season_means) - 1) if len(team_season_means) > 1 else 0.0
    tau2 = max(between_var - sigma2 / mean_n, 1e-4)
    k_hat = round(sigma2 / tau2, 4)

    return k_hat, {
        "teamSeasonsUsed": len(team_season_vars),
        "sigma2WithinTeamGame": round(sigma2, 4),
        "tau2BetweenTeamTalent": round(tau2, 4),
        "grandMeanBullpenEr9": round(grand_mean, 4),
        "kHat": k_hat,
        "currentFixedK": BULLPEN_SHRINKAGE_K,
    }


def bullpen_component_for(candidate, raw_bullpen, league_avg_bullpen_er9, k_p1=None):
    if raw_bullpen is None:
        return None
    if candidate == P0:
        return stabilized_bullpen_rate(raw_bullpen["bullpenEarnedRunsPer9"], raw_bullpen["priorGamesWithBullpenData"], league_avg_bullpen_er9, k=BULLPEN_SHRINKAGE_K)
    if candidate == P1:
        return stabilized_bullpen_rate(raw_bullpen["bullpenEarnedRunsPer9"], raw_bullpen["priorGamesWithBullpenData"], league_avg_bullpen_er9, k=k_p1)
    raise ValueError(f"{candidate!r} is not implemented")


def _frozen_offense(raw_offense, league_avg_offense):
    return stabilized_offense_rate(raw_offense["offenseRunsPerGame"], raw_offense["priorGamesThisSeason"], league_avg_offense, k=OFFENSE_SHRINKAGE_K)


def _hfa_fit_rows_for_candidate(rows, candidate, league_avg_offense, league_avg_bullpen_er9, k_p1=None):
    out = []
    for r in rows:
        hb_off = _frozen_offense(r["homeBaselineRaw"], league_avg_offense)
        ab_off = _frozen_offense(r["awayBaselineRaw"], league_avg_offense)
        hb_bp = bullpen_component_for(candidate, r["homeBullpenRaw"], league_avg_bullpen_er9, k_p1)
        ab_bp = bullpen_component_for(candidate, r["awayBullpenRaw"], league_avg_bullpen_er9, k_p1)
        hb = dict(r["homeBaselineRaw"], offenseRunsPerGame=hb_off, runPreventionRunsAllowedPerGame=blend_run_prevention_with_bullpen_quality(r["homeBaselineRaw"]["runPreventionRunsAllowedPerGame"], hb_bp))
        ab = dict(r["awayBaselineRaw"], offenseRunsPerGame=ab_off, runPreventionRunsAllowedPerGame=blend_run_prevention_with_bullpen_quality(r["awayBaselineRaw"]["runPreventionRunsAllowedPerGame"], ab_bp))
        out.append({"homeBaseline": hb, "awayBaseline": ab, "actualHomeRuns": r["actualHomeRuns"], "actualAwayRuns": r["actualAwayRuns"]})
    return out


def fit_hfa_for_candidate(dev_rows, candidate, league_avg_offense, league_avg_bullpen_er9, k_p1=None):
    return fit_home_field_adjustment(_hfa_fit_rows_for_candidate(dev_rows, candidate, league_avg_offense, league_avg_bullpen_er9, k_p1))


def attach_predictions(rows, candidate, key_prefix, home_field_adjustment, league_avg_offense, league_avg_bullpen_er9, k_p1=None):
    for r in rows:
        hb_off = _frozen_offense(r["homeBaselineRaw"], league_avg_offense)
        ab_off = _frozen_offense(r["awayBaselineRaw"], league_avg_offense)
        hb_bp = bullpen_component_for(candidate, r["homeBullpenRaw"], league_avg_bullpen_er9, k_p1)
        ab_bp = bullpen_component_for(candidate, r["awayBullpenRaw"], league_avg_bullpen_er9, k_p1)
        hb = dict(r["homeBaselineRaw"], offenseRunsPerGame=hb_off, runPreventionRunsAllowedPerGame=blend_run_prevention_with_bullpen_quality(r["homeBaselineRaw"]["runPreventionRunsAllowedPerGame"], hb_bp))
        ab = dict(r["awayBaselineRaw"], offenseRunsPerGame=ab_off, runPreventionRunsAllowedPerGame=blend_run_prevention_with_bullpen_quality(r["awayBaselineRaw"]["runPreventionRunsAllowedPerGame"], ab_bp))
        eh, ea = expected_runs(hb, ab, home_field_adjustment=home_field_adjustment)
        r[f"homeExpectedRuns_{key_prefix}"] = eh
        r[f"awayExpectedRuns_{key_prefix}"] = ea


def team_observations(rows, key_prefix):
    obs = []
    for r in rows:
        eh, ea = r.get(f"homeExpectedRuns_{key_prefix}"), r.get(f"awayExpectedRuns_{key_prefix}")
        if eh is not None and r.get("actualHomeRuns") is not None:
            obs.append({"gamePk": r["gamePk"], "teamId": r["homeTeamId"], "season": r["season"], "priorGames": r["homeBaselineRaw"]["priorGamesThisSeason"], "predicted": eh, "actual": r["actualHomeRuns"]})
        if ea is not None and r.get("actualAwayRuns") is not None:
            obs.append({"gamePk": r["gamePk"], "teamId": r["awayTeamId"], "season": r["season"], "priorGames": r["awayBaselineRaw"]["priorGamesThisSeason"], "predicted": ea, "actual": r["actualAwayRuns"]})
    return obs


def mean_accuracy_metrics(obs):
    import math
    if not obs:
        return {"n": 0, "independentGames": 0, "mae": None, "rmse": None, "bias": None, "residualVariance": None}
    errors = [o["predicted"] - o["actual"] for o in obs]
    n = len(errors)
    mae = round(sum(abs(e) for e in errors) / n, 4)
    rmse = round(math.sqrt(sum(e ** 2 for e in errors) / n), 4)
    bias = round(sum(errors) / n, 4)
    residual_variance = round(sum((e - bias) ** 2 for e in errors) / n, 4)
    independent_games = independent_unit_count(obs, key="gamePk")
    return {"n": n, "independentGames": independent_games, "sampleSizeStatus": sample_size_status(n, independent_games=independent_games), "mae": mae, "rmse": rmse, "bias": bias, "residualVariance": residual_variance}


def paired_mean_mae_delta(obs_a, obs_b):
    by_key_a = {(o["gamePk"], o["teamId"]): o for o in obs_a}
    by_key_b = {(o["gamePk"], o["teamId"]): o for o in obs_b}
    common = sorted(set(by_key_a) & set(by_key_b))
    paired_rows = [{"gamePk": k[0], "teamId": k[1], "errA": abs(by_key_a[k]["predicted"] - by_key_a[k]["actual"]), "errB": abs(by_key_b[k]["predicted"] - by_key_b[k]["actual"])} for k in common]

    def _delta(subset):
        if not subset:
            return None
        return sum(r["errB"] - r["errA"] for r in subset) / len(subset)

    point = _delta(paired_rows)
    lo, hi, _ = game_clustered_bootstrap_ci(paired_rows, _delta, cluster_key="gamePk", seed=DEFAULT_BOOTSTRAP_SEED)
    return {"n": len(paired_rows), "independentGames": independent_unit_count(paired_rows, key="gamePk"), "maeDelta": round(point, 6) if point is not None else None, "maeDeltaCI95": {"low": lo, "high": hi, "method": "GAME_CLUSTERED_BOOTSTRAP"}, "interpretation": "negative == candidate B improved (lower MAE than A)"}


def season_band_breakdown(obs_a, obs_b):
    out = {}
    for band_name, lo_games, hi_games in SEASON_BANDS:
        def _in_band(o):
            return o["priorGames"] >= lo_games and (hi_games is None or o["priorGames"] <= hi_games)
        out[band_name] = paired_mean_mae_delta([o for o in obs_a if _in_band(o)], [o for o in obs_b if _in_band(o)])
    return out


def team_robustness(obs_a, obs_b):
    by_key_a = {(o["gamePk"], o["teamId"]): o for o in obs_a}
    by_key_b = {(o["gamePk"], o["teamId"]): o for o in obs_b}
    common = sorted(set(by_key_a) & set(by_key_b))
    team_ids = sorted({k[1] for k in common})
    per_team = {}
    for team_id in team_ids:
        team_keys = [k for k in common if k[1] == team_id]
        errors_a = [abs(by_key_a[k]["predicted"] - by_key_a[k]["actual"]) for k in team_keys]
        errors_b = [abs(by_key_b[k]["predicted"] - by_key_b[k]["actual"]) for k in team_keys]
        if not errors_a:
            continue
        per_team[str(team_id)] = round(sum(errors_b) / len(errors_b) - sum(errors_a) / len(errors_a), 4)
    overall_delta = round(sum(per_team.values()) / len(per_team), 4) if per_team else None
    leave_one_out = {excluded: round(sum(v for tid, v in per_team.items() if tid != excluded) / max(len(per_team) - 1, 1), 4) for excluded in per_team}
    deltas = list(per_team.values())
    return {
        "perTeamMaeDelta": per_team, "overallMeanOfPerTeamDeltas": overall_delta, "leaveOneTeamOutDeltas": leave_one_out,
        "leaveOneTeamOutRange": {"min": round(min(leave_one_out.values()), 4), "max": round(max(leave_one_out.values()), 4)} if leave_one_out else None,
        "nTeamsPositive": sum(1 for d in deltas if d < 0), "nTeamsNegative": sum(1 for d in deltas if d > 0), "nTeamsTotal": len(deltas),
    }


def _nb_joint(home_mean, away_mean, dispersion=FROZEN_DISPERSION):
    def home_pmf(k):
        return negative_binomial_pmf(k, home_mean, dispersion)

    def away_pmf(k):
        return negative_binomial_pmf(k, away_mean, dispersion)
    return independent_joint_pmf(home_pmf, away_pmf), home_pmf, away_pmf


def nb_probability_cells(home_mean, away_mean):
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
    outcomes = {"moneyline_home_win": 1 if actual_home > actual_away else 0, "moneyline_away_win": 1 if actual_away > actual_home else 0}
    for line in GAME_TOTAL_LINES:
        outcomes[f"game_total_over_{line}"] = 1 if actual_total > line else 0
    for line in TEAM_TOTAL_LINES:
        outcomes[f"team_total_away_over_{line}"] = 1 if actual_away > line else 0
        outcomes[f"team_total_home_over_{line}"] = 1 if actual_home > line else 0
    return outcomes


def frozen_nb_probability_eval(rows, key_a, key_b):
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


def selection_passes(dev_mae_delta, val_mae_delta, band_deltas, val_nb_primary_delta):
    reasons = []
    if dev_mae_delta is None or dev_mae_delta >= 0:
        reasons.append(f"DEV MAE delta not negative (improved): {dev_mae_delta}")
    if val_mae_delta is not None and val_mae_delta > DEGRADATION_TOLERANCE:
        reasons.append(f"VALIDATION MAE delta degraded beyond tolerance {DEGRADATION_TOLERANCE}: {val_mae_delta}")
    early_band = band_deltas.get("games_1_15", {}).get("maeDelta")
    late_bands_all_null_or_worse = all((band_deltas.get(b, {}).get("maeDelta") is None or band_deltas.get(b, {}).get("maeDelta") >= 0) for b in ("games_16_40", "games_41_80", "games_81_plus"))
    if early_band is not None and early_band < 0 and late_bands_all_null_or_worse:
        reasons.append("improvement confined to games_1_15 only -- fails the 'not restricted to first few games' criterion")
    if val_nb_primary_delta is not None and val_nb_primary_delta > PROBABILITY_DEGRADATION_TOLERANCE:
        reasons.append(f"VALIDATION frozen-NB primary Brier delta degraded beyond tolerance {PROBABILITY_DEGRADATION_TOLERANCE}: {val_nb_primary_delta}")
    return (len(reasons) == 0), reasons


def _primary_aggregate_delta(by_family_result):
    deltas = [by_family_result[fam]["pairedDelta"]["brierScore"] for fam in ("game_total", "team_total_away", "team_total_home") if by_family_result.get(fam) and by_family_result[fam]["pairedDelta"]["brierScore"] is not None]
    return round(sum(deltas) / len(deltas), 6) if deltas else None


def main():
    print(f"[{EXPERIMENT_ID}] registering experiment/control...")
    control, definition = register_experiment()

    print(f"[{EXPERIMENT_ID}] building corpus (reusing MLB-RSCH-0009 loaders + MLB-RSCH-0003's already-cached bullpen ER9 data)...")
    rows_by_season, relief_by_season, league_avg_offense, league_avg_bullpen_er9 = build_corpus()
    dev_rows = [r for s in DEV_SEASONS for r in rows_by_season[s]]
    val_rows = [r for s in VALIDATION_SEASONS for r in rows_by_season[s]]
    holdout_rows = [r for s in HOLDOUT_SEASONS for r in rows_by_season[s]]
    print(f"[{EXPERIMENT_ID}] rows: dev={len(dev_rows)} val={len(val_rows)} holdout={len(holdout_rows)}")

    print(f"[{EXPERIMENT_ID}] fitting P1 empirical-Bayes k on DEVELOPMENT only...")
    k_p1, k_diagnostics = fit_empirical_bayes_bullpen_k_dev_only(relief_by_season, league_avg_bullpen_er9)
    print(f"[{EXPERIMENT_ID}] P1 k_hat={k_p1} (current fixed P0 k={BULLPEN_SHRINKAGE_K}) diagnostics={k_diagnostics}")

    hfa_p0 = fit_hfa_for_candidate(dev_rows, P0, league_avg_offense, league_avg_bullpen_er9)
    hfa_p1 = fit_hfa_for_candidate(dev_rows, P1, league_avg_offense, league_avg_bullpen_er9, k_p1=k_p1)
    for rows in (dev_rows, val_rows, holdout_rows):
        attach_predictions(rows, P0, "P0", hfa_p0, league_avg_offense, league_avg_bullpen_er9)
        attach_predictions(rows, P1, "P1", hfa_p1, league_avg_offense, league_avg_bullpen_er9, k_p1=k_p1)

    obs_dev_p0, obs_dev_p1 = team_observations(dev_rows, "P0"), team_observations(dev_rows, "P1")
    obs_val_p0, obs_val_p1 = team_observations(val_rows, "P0"), team_observations(val_rows, "P1")
    obs_holdout_p0, obs_holdout_p1 = team_observations(holdout_rows, "P0"), team_observations(holdout_rows, "P1")

    dev_o0_metrics, dev_o1_metrics = mean_accuracy_metrics(obs_dev_p0), mean_accuracy_metrics(obs_dev_p1)
    val_o0_metrics, val_o1_metrics = mean_accuracy_metrics(obs_val_p0), mean_accuracy_metrics(obs_val_p1)
    holdout_o0_metrics, holdout_o1_metrics = mean_accuracy_metrics(obs_holdout_p0), mean_accuracy_metrics(obs_holdout_p1)

    dev_delta = paired_mean_mae_delta(obs_dev_p0, obs_dev_p1)
    val_delta = paired_mean_mae_delta(obs_val_p0, obs_val_p1)
    holdout_delta = paired_mean_mae_delta(obs_holdout_p0, obs_holdout_p1)
    print(f"[{EXPERIMENT_ID}] P1 vs P0 MAE delta: dev={dev_delta['maeDelta']} val={val_delta['maeDelta']} holdout={holdout_delta['maeDelta']}")

    dev_bands = season_band_breakdown(obs_dev_p0, obs_dev_p1)
    val_bands = season_band_breakdown(obs_val_p0, obs_val_p1)
    dev_team_robustness = team_robustness(obs_dev_p0, obs_dev_p1)

    val_nb = frozen_nb_probability_eval(val_rows, "P0", "P1")
    val_nb_primary_delta = _primary_aggregate_delta(val_nb["byFamily"])

    passes, reasons = selection_passes(dev_delta["maeDelta"], val_delta["maeDelta"], dev_bands, val_nb_primary_delta)
    final_bullpen_model = P1 if passes else P0
    print(f"[{EXPERIMENT_ID}] selection: P1 passes={passes} reasons={reasons} -> final={final_bullpen_model}")

    holdout_nb = frozen_nb_probability_eval(holdout_rows, "P0", "P1")

    print(f"[{EXPERIMENT_ID}] Pinnacle secondary stage (existing sample, no new Odds API spend)...")
    import run_proxy_vs_pinnacle_experiment as rsch0008
    pinnacle_result = None
    try:
        pinnacle_all_rows = [r for season in ALL_SEASONS for r in rsch0008.build_matched_rows(season)]
        for r in pinnacle_all_rows:
            rsch0008.enrich_row(r, hfa_p0)
            for candidate, key, k_val, hfa in ((P0, "P0", None, hfa_p0), (P1, "P1", k_p1, hfa_p1)):
                hb_off = _frozen_offense(r["homeBaseline"], league_avg_offense)
                ab_off = _frozen_offense(r["awayBaseline"], league_avg_offense)
                hb = dict(r["homeBaseline"], offenseRunsPerGame=hb_off)
                ab = dict(r["awayBaseline"], offenseRunsPerGame=ab_off)
                eh, ea = expected_runs(hb, ab, home_field_adjustment=hfa)
                r[f"mlHomeProb_{key}"], _ = game_ml_proxy_probability(eh, ea) if eh is not None and ea is not None else (None, None)
        pinnacle_p0 = rsch0008.paired_analysis(pinnacle_all_rows, "mlHomeProb_P0", "pinnacleMlHomeFair", "actualHomeWin", "PINNACLE/ML/P0")
        pinnacle_p1 = rsch0008.paired_analysis(pinnacle_all_rows, "mlHomeProb_P1", "pinnacleMlHomeFair", "actualHomeWin", "PINNACLE/ML/P1")
        pinnacle_result = {"nRows": len(pinnacle_all_rows), "p0": pinnacle_p0, "p1": pinnacle_p1}
    except Exception as exc:
        pinnacle_result = {"error": str(exc)}
    print(f"[{EXPERIMENT_ID}] Pinnacle secondary stage result: {pinnacle_result if isinstance(pinnacle_result, dict) and 'error' in pinnacle_result else 'OK'}")

    report = {
        "experimentId": EXPERIMENT_ID,
        "controlModelId": control["controlModelId"],
        "corpus": {"devRows": len(dev_rows), "valRows": len(val_rows), "holdoutRows": len(holdout_rows)},
        "empiricalBayesK": {"kHat": k_p1, "currentFixedK": BULLPEN_SHRINKAGE_K, "diagnostics": k_diagnostics},
        "homeFieldAdjustment": {"P0": hfa_p0, "P1": hfa_p1},
        "meanAccuracy": {
            "dev": {"P0": dev_o0_metrics, "P1": dev_o1_metrics, "pairedDelta": dev_delta},
            "validation": {"P0": val_o0_metrics, "P1": val_o1_metrics, "pairedDelta": val_delta},
            "holdout2026": {"P0": holdout_o0_metrics, "P1": holdout_o1_metrics, "pairedDelta": holdout_delta},
        },
        "seasonBands": {"dev": dev_bands, "validation": val_bands},
        "teamRobustnessDev": dev_team_robustness,
        "frozenNbProbability": {"validation": val_nb, "holdout2026": holdout_nb},
        "selection": {"p1PassesSelectionRule": passes, "reasons": reasons, "finalBullpenModel": final_bullpen_model},
        "pinnacleSecondary": pinnacle_result,
    }

    out_path = os.path.join("data", "edgelab", "analytics", "latest_mlb_rsch_0013_bullpen_talent.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    print(f"[{EXPERIMENT_ID}] wrote {out_path}")
    return report


if __name__ == "__main__":
    main()
