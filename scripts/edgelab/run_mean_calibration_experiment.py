#!/usr/bin/env python3
"""
scripts/edgelab/run_mean_calibration_experiment.py
====================================================================
Research Lab experiment MLB-RSCH-0014: "Expected-Run Mean Calibration".
RESEARCH ONLY. NO production changes.

MLB-RSCH-0009 found a better offense proxy + bullpen quality improved
the expected-run mean model (frozen: offense k=30, bullpen k=30, both
components accepted, home-field adjustment fit DEV-only). MLB-RSCH-0010
then improved the SCORING DISTRIBUTION on top of that mean (frozen
negative-binomial, dispersion=0.281513). MLB-RSCH-0012/0013 then tested
(and rejected) two further ways to change the MEAN's own INPUTS (a
refit offense/bullpen shrinkage constant, and a component-batting
regression). This milestone asks a structurally DIFFERENT question:
holding MLB-RSCH-0009's frozen mean construction completely fixed as
CONTROL (C0), is that mean itself systematically MISCALIBRATED -- does
a simple, DEV-fit post-hoc transformation of the already-computed
predicted mean improve accuracy and, more importantly, downstream
probability quality under the frozen MLB-RSCH-0010 NB distribution?

CRITICAL ISOLATION: this experiment changes ONLY the calibration layer
applied AFTER MLB-RSCH-0009's frozen mean is computed. It does not
change: the offense component, the bullpen component, the home-field
adjustment, the NB dispersion, or any production code. Pinnacle is
never used for selection, only as a secondary check after freezing.

CANDIDATES (all fit on DEVELOPMENT team-observations only, one
observation per team-game-side, applied to MLB-RSCH-0009's own frozen
C0 predicted mean -- never to the raw offense/bullpen components):
  C0 (control): MLB-RSCH-0009's frozen mean, unchanged --
      rsch0009.baseline_for_components(components={"offense","bullpen"})
      + expected_runs(home_field_adjustment=<DEV-fit, frozen>). No
      calibration transform.
  C1: global affine -- calibrated = a + b * predicted, ONE (a, b) pair
      fit by closed-form OLS on ALL DEV team-observations (home+away
      pooled).
  C2: home/away affine -- SEPARATE (a_h, b_h) / (a_a, b_a) pairs, each
      fit on that side's own DEV team-observations only. Four
      parameters total.
  C3: quadratic -- calibrated = a + b*predicted + c*predicted^2, ONE
      triple fit by closed-form OLS on ALL DEV team-observations
      (pooled, same population as C1, isolating curvature specifically
      rather than conflating it with the home/away question C2 tests).
Every candidate's calibrated mean is floored at CALIBRATION_FLOOR
(never non-positive -- the frozen NB distribution requires a positive
mean).

MEAN MODEL: entirely MLB-RSCH-0009's own frozen composition, reused
via `run_proxy_ablation_experiment` (rsch0009) UNCHANGED -- this script
imports and calls rsch0009's own loaders/baseline_for_components/
fit_home_field_adjustment_for_components, never reimplementing them.

PROBABILITY EVALUATION: MLB-RSCH-0010's own frozen negative-binomial
distribution (dispersion=0.281513), verified byte-exact at import time,
never refit here. Every candidate is scored through the IDENTICAL
frozen distribution, isolating any probability-quality change to the
calibration layer alone. Adds RUN-MARGIN cells (win/lose by >=2, >=3,
via lib.edgelab.backtest.run_distributions.margin_at_least_prob) to the
family set MLB-RSCH-0012 already established (moneyline/game_total/
team_total).

SELECTION (preregistered before any real result was computed): a
calibration candidate may become the frozen winner only if, versus C0:
(1) DEV primary mean metric (paired MAE) improves, (2) DEV primary
frozen-NB probability metric improves or is preserved (delta <=
PROBABILITY_IMPROVEMENT_TOLERANCE), (3) VALIDATION does not degrade
beyond DEGRADATION_TOLERANCE (mean) / PROBABILITY_DEGRADATION_TOLERANCE
(probability), (4) the improvement is not confined to one narrow
predicted-run band (CALIBRATION_BANDS). 2026 and Pinnacle are NEVER
consulted during selection. Only the frozen winner (which may be C0
itself, if no candidate passes) is ever evaluated on the 2026 locked
holdout.
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

from lib.edgelab.backtest.proxy_model import expected_runs, game_ml_proxy_probability, game_total_proxy_probability
from lib.edgelab.backtest.run_distributions import (
    negative_binomial_pmf,
    independent_joint_pmf,
    home_win_and_push_prob,
    total_over_prob,
    team_total_over_prob,
    margin_at_least_prob,
)
from lib.edgelab import experiment_registry as reg
from lib.edgelab import evidence_levels as ev
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import paired_evaluation as pe
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab.research_stats import (
    DEFAULT_BOOTSTRAP_SEED,
    independent_unit_count,
    sample_size_status,
    game_clustered_bootstrap_ci,
)

import run_proxy_ablation_experiment as rsch0009  # noqa: E402

EXPERIMENT_ID = "MLB-RSCH-0014"
REGISTRATION_TIMESTAMP = "2026-08-28T08:45:00Z"

# Frozen at MLB-RSCH-0010 -- verified byte-exact at import time below,
# never refit here. Duplicated locally (not imported from
# lib.edgelab.shadow_distribution) to avoid an inter-experiment-PR
# dependency, matching MLB-RSCH-0012/0013's own established convention.
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

# MLB-RSCH-0009's own frozen, final accepted composition (verified against
# data/edgelab/analytics/latest_mlb_rsch_0009_proxy_ablation.json's own
# "finalComponents": ["bullpen", "offense"] -- park was NOT accepted).
C0_COMPONENTS = frozenset({"offense", "bullpen"})

C0 = "C0_control_frozen_mean"
C1 = "C1_global_affine"
C2 = "C2_home_away_affine"
C3 = "C3_quadratic"

# Preregistered, fixed BEFORE any real result was computed -- predicted-
# run-level bands (NOT season-progress bands, unlike MLB-RSCH-0012).
CALIBRATION_BANDS = (
    ("lt_3_0", None, 3.0),
    ("3_0_to_3_75", 3.0, 3.75),
    ("3_75_to_4_5", 3.75, 4.5),
    ("4_5_to_5_25", 4.5, 5.25),
    ("5_25_to_6_0", 5.25, 6.0),
    ("6_0_plus", 6.0, None),
)

GAME_TOTAL_LINES = (7.5, 8.5, 9.5, 10.5)
TEAM_TOTAL_LINES = (2.5, 3.5, 4.5, 5.5)
MARGIN_THRESHOLDS = (2, 3)  # matches lib.edgelab.shadow_distribution's own MARGIN_THRESHOLDS

DEGRADATION_TOLERANCE = 0.05  # VALIDATION MAE degradation tolerance, runs/game -- fixed before results
PROBABILITY_DEGRADATION_TOLERANCE = 0.005  # VALIDATION frozen-NB primary Brier degradation tolerance
PROBABILITY_IMPROVEMENT_TOLERANCE = 0.0  # DEV frozen-NB primary Brier: must improve or be exactly preserved

CALIBRATION_FLOOR = 0.05  # calibrated means must stay strictly positive (frozen NB requires mean > 0)

MIN_GAMES_CONFIDENT = 50
MIN_INDEPENDENT_SETTLED_FOR_INTERPRETATION = 30  # MLB-RSCH-0011 shadow health check threshold


# ── Registration (idempotent across re-runs on the same branch) ──────────

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
    """
    Registers (or, on a later re-run continuing the SAME experiment,
    reuses -- see MLB-RSCH-0012's identical fix for why a later commit
    on the same research branch would otherwise trip the registry's
    write-once guard) this experiment's registration.
    """
    try:
        existing_definition = reg.load_experiment(EXPERIMENT_ID)
    except FileNotFoundError:
        existing_definition = None

    if existing_definition is not None:
        control = ctrl_id.load_control(existing_definition["controlModelId"])
        return control, existing_definition

    control = ctrl_id.build_control_registration(
        name="mlb_rsch_0014_mean_calibration_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0014 mean calibration v1: MLB-RSCH-0009 frozen mean (offense+bullpen, "
                        "unchanged) + post-hoc calibration transform from {C0 none, C1 global affine, "
                        "C2 home/away affine, C3 quadratic}"
        ),
        probability_adapter_identity="lib.edgelab.backtest.run_distributions (frozen MLB-RSCH-0010 negative-binomial, dispersion unchanged)",
        model_engine_family="pit_safe_research_mean_calibration_v1",
        required_input_provenance=["team_recent_game_log_reconstruction"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "A calibration LAYER tested strictly on top of MLB-RSCH-0009's frozen expected-run mean "
            "(offense+bullpen composition, unchanged). Tests whether C0's own predicted mean is "
            "systematically miscalibrated (intercept bias, slope/compression bias, home/away asymmetry, "
            "or curvature) and whether a simple DEV-fit correction improves both direct mean accuracy and "
            "downstream probability quality under the frozen MLB-RSCH-0010 NB distribution."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Expected-Run Mean Calibration",
        hypothesis=(
            "H1: MLB-RSCH-0009's frozen expected-run mean is systematically miscalibrated in at least one "
            "of: a constant (intercept) offset, a compression/expansion (slope != 1) pattern, a home/away "
            "asymmetry, or a curved (nonlinear) relationship between predicted and actual runs. H2: a "
            "simple, parsimonious, DEV-fit post-hoc correction for whichever of these applies improves "
            "both direct mean accuracy (MAE) and, more importantly, downstream probability quality "
            "(Brier/log loss) under the frozen MLB-RSCH-0010 NB distribution, across team-total, "
            "game-total, moneyline, and run-margin market families. H3: if a genuine correction exists, it "
            "is broad (not confined to one narrow predicted-run band) and survives the 2026 locked holdout."
        ),
        research_question=(
            "Holding MLB-RSCH-0009's frozen mean construction (offense+bullpen, unchanged) and MLB-RSCH-0010's "
            "frozen NB dispersion completely fixed, does a simple DEV-fit post-hoc calibration transform of "
            "the ALREADY-COMPUTED predicted mean improve accuracy and downstream probability quality, and if "
            "so, is the effect an intercept, a slope, a home/away asymmetry, or a curvature correction?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E2_PIT_HISTORICAL,
        target_population=(
            "The same MLB regular-season 2022-2026 games MLB-RSCH-0009's own baseline used (both teams "
            ">= MIN_PRIOR_GAMES_FOR_BASELINE prior completed games this season), reused via that "
            "milestone's own row-building functions, unchanged."
        ),
        market_families=["game_result", "game_total", "team_total", "run_margin"],
        eligibility_criteria=[
            "both teams have >= MIN_PRIOR_GAMES_FOR_BASELINE prior completed games this season (MLB-RSCH-0009's own eligibility rule, unchanged)",
        ],
        exclusion_criteria=[
            "any change to the offense or bullpen component itself -- MLB-RSCH-0012/0013 already tested and rejected refitting those; this experiment isolates the calibration LAYER only",
            "Pinnacle as a selection input -- secondary check only, strictly after freezing",
            "complex nonlinear calibration (random forests, gradient boosting, splines with many knots, neural nets) -- C3 is a single preregistered quadratic, chosen to test curvature, not to fit historical noise",
        ],
        prediction_checkpoints=["SEASON_TO_DATE_PREGAME"],
        primary_metric="paired MAE delta on next-game team runs scored (candidate minus C0), game-and-team-clustered 95% CI",
        secondary_metrics=[
            "calibration slope/intercept", "RMSE delta", "mean bias delta",
            "frozen-NB team-total/game-total/moneyline/run-margin Brier deltas",
            "predicted-run-band-specific MAE deltas (CALIBRATION_BANDS)",
            "home/away-specific deltas", "season-specific deltas",
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
            "evidenceLevel E2_PIT_HISTORICAL: same basis as MLB-RSCH-0008/0009/0010/0012/0013. Only the "
            "frozen SELECTED calibration winner (which may be C0 itself, i.e. no calibration) is ever "
            "evaluated on the 2026 locked holdout -- C1/C2/C3 selection uses DEV+VAL only."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Corpus construction (reuses MLB-RSCH-0009's own loaders unchanged) ───

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

    all_rows = [r for s in ALL_SEASONS for r in rows_by_season[s]]
    rsch0009.attach_stabilized_components(all_rows, league_avg_offense, league_avg_bullpen_er9)

    return rows_by_season, team_games_by_season, league_avg_offense, league_avg_bullpen_er9


# ── C0: MLB-RSCH-0009's frozen mean, EXACT reproduction ──────────────────

def fit_hfa_c0(dev_rows):
    return rsch0009.fit_home_field_adjustment_for_components(dev_rows, C0_COMPONENTS)


def attach_c0_predictions(rows, hfa_c0):
    """Mutates each row in place, adding homeExpectedRuns_C0/
    awayExpectedRuns_C0 -- MLB-RSCH-0009's own frozen composition
    (offense+bullpen), called via rsch0009.baseline_for_components +
    expected_runs UNCHANGED. This is the ONLY place a raw component
    value is ever touched; every calibration candidate transforms THIS
    output, never the raw offense/bullpen inputs."""
    for r in rows:
        hb = rsch0009.baseline_for_components(r["homeBaselineRaw"], r["homeOffenseStabilized"], r["homeBullpenStabilized"], C0_COMPONENTS)
        ab = rsch0009.baseline_for_components(r["awayBaselineRaw"], r["awayOffenseStabilized"], r["awayBullpenStabilized"], C0_COMPONENTS)
        eh, ea = expected_runs(hb, ab, home_field_adjustment=hfa_c0)
        r["homeExpectedRuns_C0"] = eh
        r["awayExpectedRuns_C0"] = ea


# ── Team-level observations ───────────────────────────────────────────────

def team_observations(rows, key_prefix):
    """One entry per team-game-side (2 per row) -- {gamePk, teamId,
    season, gameNumber, side, priorGames, predictedC0 (for banding,
    ALWAYS C0's own raw value so every candidate is sliced by the SAME
    game population), predicted (this candidate's own value), actual}."""
    obs = []
    for r in rows:
        eh_c0, ea_c0 = r.get("homeExpectedRuns_C0"), r.get("awayExpectedRuns_C0")
        eh, ea = r.get(f"homeExpectedRuns_{key_prefix}"), r.get(f"awayExpectedRuns_{key_prefix}")
        if eh is not None and eh_c0 is not None and r.get("actualHomeRuns") is not None:
            obs.append({
                "gamePk": r["gamePk"], "teamId": r["homeTeamId"], "season": r["season"], "gameNumber": r.get("gameNumber"),
                "side": "home", "priorGames": r["homeBaselineRaw"]["priorGamesThisSeason"],
                "predictedC0": eh_c0, "predicted": eh, "actual": r["actualHomeRuns"],
            })
        if ea is not None and ea_c0 is not None and r.get("actualAwayRuns") is not None:
            obs.append({
                "gamePk": r["gamePk"], "teamId": r["awayTeamId"], "season": r["season"], "gameNumber": r.get("gameNumber"),
                "side": "away", "priorGames": r["awayBaselineRaw"]["priorGamesThisSeason"],
                "predictedC0": ea_c0, "predicted": ea, "actual": r["actualAwayRuns"],
            })
    return obs


def mean_accuracy_metrics(obs):
    if not obs:
        return {"n": 0, "independentGames": 0, "mae": None, "rmse": None, "bias": None, "residualVariance": None,
                "calibrationSlope": None, "calibrationIntercept": None}
    errors = [o["predicted"] - o["actual"] for o in obs]
    n = len(errors)
    mae = round(sum(abs(e) for e in errors) / n, 4)
    rmse = round(math.sqrt(sum(e ** 2 for e in errors) / n), 4)
    bias = round(sum(errors) / n, 4)
    residual_variance = round(sum((e - bias) ** 2 for e in errors) / n, 4)
    slope, intercept = _simple_ols(obs, "predicted", "actual")
    independent_games = independent_unit_count(obs, key="gamePk")
    return {
        "n": n, "independentGames": independent_games,
        "sampleSizeStatus": sample_size_status(n, independent_games=independent_games),
        "mae": mae, "rmse": rmse, "bias": bias, "residualVariance": residual_variance,
        "calibrationSlope": round(slope, 4) if slope is not None else None,
        "calibrationIntercept": round(intercept, 4) if intercept is not None else None,
    }


def paired_mean_mae_delta(obs_a, obs_b):
    """Paired (candidate B minus A) MAE delta with a game-clustered
    bootstrap CI, paired by (gamePk, teamId)."""
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


def calibration_band_breakdown(obs_a, obs_b):
    """Per CALIBRATION_BANDS (fixed predicted-run bands, keyed by C0's
    OWN raw predicted value -- so every candidate is sliced by the SAME
    game population), the paired MAE delta."""
    out = {}
    for band_name, lo_v, hi_v in CALIBRATION_BANDS:
        def _in_band(o):
            return (lo_v is None or o["predictedC0"] >= lo_v) and (hi_v is None or o["predictedC0"] < hi_v)
        band_a = [o for o in obs_a if _in_band(o)]
        band_b = [o for o in obs_b if _in_band(o)]
        out[band_name] = paired_mean_mae_delta(band_a, band_b)
    return out


def c0_diagnostic_band_table(obs_c0):
    """C0's OWN calibration diagnostics by fixed predicted-run band --
    count, mean predicted, mean actual, bias, MAE, RMSE. Diagnostic
    only, computed BEFORE any candidate is examined."""
    out = {}
    for band_name, lo_v, hi_v in CALIBRATION_BANDS:
        band = [o for o in obs_c0 if (lo_v is None or o["predicted"] >= lo_v) and (hi_v is None or o["predicted"] < hi_v)]
        if not band:
            out[band_name] = {"n": 0}
            continue
        errors = [o["predicted"] - o["actual"] for o in band]
        out[band_name] = {
            "n": len(band),
            "independentGames": independent_unit_count(band, key="gamePk"),
            "meanPredicted": round(sum(o["predicted"] for o in band) / len(band), 4),
            "meanActual": round(sum(o["actual"] for o in band) / len(band), 4),
            "bias": round(sum(errors) / len(errors), 4),
            "mae": round(sum(abs(e) for e in errors) / len(errors), 4),
            "rmse": round(math.sqrt(sum(e ** 2 for e in errors) / len(errors)), 4),
        }
    return out


def side_breakdown(obs_a, obs_b):
    """Home/away-specific mean accuracy + paired delta."""
    out = {}
    for side in ("home", "away"):
        side_a = [o for o in obs_a if o["side"] == side]
        side_b = [o for o in obs_b if o["side"] == side]
        out[side] = {"metricsA": mean_accuracy_metrics(side_a), "metricsB": mean_accuracy_metrics(side_b), "pairedDelta": paired_mean_mae_delta(side_a, side_b)}
    return out


def season_breakdown(obs_a, obs_b, seasons):
    out = {}
    for season in seasons:
        season_a = [o for o in obs_a if o["season"] == season]
        season_b = [o for o in obs_b if o["season"] == season]
        out[str(season)] = {"metricsA": mean_accuracy_metrics(season_a), "metricsB": mean_accuracy_metrics(season_b), "pairedDelta": paired_mean_mae_delta(season_a, season_b)}
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

    leave_one_out = {}
    for excluded in per_team:
        remaining = [v for tid, v in per_team.items() if tid != excluded]
        leave_one_out[excluded] = round(sum(remaining) / len(remaining), 4) if remaining else None

    deltas = list(per_team.values())
    return {
        "perTeamMaeDelta": per_team,
        "leaveOneTeamOutDeltas": leave_one_out,
        "nTeamsPositive": sum(1 for d in deltas if d < 0),
        "nTeamsNegative": sum(1 for d in deltas if d > 0),
        "nTeamsTotal": len(deltas),
    }


# ── Calibration fitting (closed-form, DEVELOPMENT only) ───────────────────

def _simple_ols(rows, x_field, y_field):
    """Pure. Simple bivariate OLS (slope, intercept) via the standard
    closed-form covariance/variance formula. None, None if fewer than 2
    rows or the x values are degenerate (zero variance)."""
    n = len(rows)
    if n < 2:
        return None, None
    xs = [r[x_field] for r in rows]
    ys = [r[y_field] for r in rows]
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x == 0:
        return None, None
    cov_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = cov_xy / var_x
    intercept = mean_y - slope * mean_x
    return slope, intercept


def _ols_fit(rows, feature_fields, target_field):
    """Pure, closed-form ordinary least squares via the normal equations,
    solved by Gauss-Jordan elimination with partial pivoting -- no
    external dependency. Returns {"intercept": ..., <field>: ...}, or
    None if underdetermined or the design matrix is singular."""
    p = len(feature_fields) + 1
    if len(rows) < p:
        return None
    xtx = [[0.0] * p for _ in range(p)]
    xty = [0.0] * p
    for r in rows:
        x = [1.0] + [float(r[f]) for f in feature_fields]
        y = float(r[target_field])
        for i in range(p):
            xty[i] += x[i] * y
            for j in range(p):
                xtx[i][j] += x[i] * x[j]
    aug = [xtx[i] + [xty[i]] for i in range(p)]
    for col in range(p):
        pivot_row = max(range(col, p), key=lambda r_: abs(aug[r_][col]))
        if abs(aug[pivot_row][col]) < 1e-12:
            return None
        aug[col], aug[pivot_row] = aug[pivot_row], aug[col]
        pivot = aug[col][col]
        aug[col] = [v / pivot for v in aug[col]]
        for r_ in range(p):
            if r_ == col:
                continue
            factor = aug[r_][col]
            if factor != 0.0:
                aug[r_] = [aug[r_][k] - factor * aug[col][k] for k in range(p + 1)]
    coefficients = {"intercept": round(aug[0][p], 6)}
    for idx, field in enumerate(feature_fields, start=1):
        coefficients[field] = round(aug[idx][p], 6)
    return coefficients


def fit_c1_global_affine_dev_only(dev_obs):
    """C1: ONE (a, b) pair, pooled home+away DEV observations."""
    slope, intercept = _simple_ols(dev_obs, "predictedC0", "actual")
    if slope is None:
        return None, {"fallback": "degenerate DEV predicted-value variance"}
    return {"a": round(intercept, 6), "b": round(slope, 6)}, {"n": len(dev_obs), "slope": round(slope, 6), "intercept": round(intercept, 6)}


def fit_c2_home_away_affine_dev_only(dev_obs):
    """C2: separate (a_h, b_h) / (a_a, b_a), each fit on that side's own DEV observations only."""
    home_obs = [o for o in dev_obs if o["side"] == "home"]
    away_obs = [o for o in dev_obs if o["side"] == "away"]
    slope_h, intercept_h = _simple_ols(home_obs, "predictedC0", "actual")
    slope_a, intercept_a = _simple_ols(away_obs, "predictedC0", "actual")
    if slope_h is None or slope_a is None:
        return None, {"fallback": "degenerate DEV predicted-value variance on one side"}
    params = {"a_h": round(intercept_h, 6), "b_h": round(slope_h, 6), "a_a": round(intercept_a, 6), "b_a": round(slope_a, 6)}
    return params, {"nHome": len(home_obs), "nAway": len(away_obs), **params}


def fit_c3_quadratic_dev_only(dev_obs):
    """C3: ONE (a, b, c) triple, pooled home+away DEV observations,
    features [predictedC0, predictedC0^2]."""
    rows = [dict(o, predictedC0Sq=o["predictedC0"] ** 2) for o in dev_obs]
    coefficients = _ols_fit(rows, ["predictedC0", "predictedC0Sq"], "actual")
    if coefficients is None:
        return None, {"fallback": "insufficient DEV observations or a singular design matrix"}
    params = {"a": coefficients["intercept"], "b": coefficients["predictedC0"], "c": coefficients["predictedC0Sq"]}
    return params, {"n": len(rows), **params}


def calibrate_value(raw, kind, params, side=None):
    """Pure. None (never fabricated) if raw is None. Floored at
    CALIBRATION_FLOOR -- a calibrated mean must stay strictly positive
    (the frozen NB distribution requires mean > 0)."""
    if raw is None or params is None:
        return raw
    if kind == C1:
        value = params["a"] + params["b"] * raw
    elif kind == C2:
        a, b = (params["a_h"], params["b_h"]) if side == "home" else (params["a_a"], params["b_a"])
        value = a + b * raw
    elif kind == C3:
        value = params["a"] + params["b"] * raw + params["c"] * raw * raw
    else:
        raise ValueError(f"{kind!r} is not a recognized calibration kind")
    return round(max(value, CALIBRATION_FLOOR), 4)


def attach_calibrated_predictions(rows, key_prefix, kind, params):
    """Mutates each row in place, adding f'homeExpectedRuns_{key_prefix}'/
    f'awayExpectedRuns_{key_prefix}' by calibrating C0's OWN predicted
    mean -- never touches the raw offense/bullpen inputs."""
    for r in rows:
        r[f"homeExpectedRuns_{key_prefix}"] = calibrate_value(r.get("homeExpectedRuns_C0"), kind, params, side="home")
        r[f"awayExpectedRuns_{key_prefix}"] = calibrate_value(r.get("awayExpectedRuns_C0"), kind, params, side="away")


# ── Frozen-NB probability evaluation (dispersion NEVER refit here) ───────

def _nb_joint(home_mean, away_mean, dispersion=FROZEN_DISPERSION):
    def home_pmf(k):
        return negative_binomial_pmf(k, home_mean, dispersion)

    def away_pmf(k):
        return negative_binomial_pmf(k, away_mean, dispersion)
    return independent_joint_pmf(home_pmf, away_pmf), home_pmf, away_pmf


def nb_probability_cells(home_mean, away_mean):
    """Pure. Same cell-key scheme as MLB-RSCH-0012, extended with
    run_margin cells (win/lose by >=2, >=3) via margin_at_least_prob."""
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
    for margin in MARGIN_THRESHOLDS:
        cells[f"run_margin_win_by_at_least_{margin}"] = margin_at_least_prob(joint, margin)
        cells[f"run_margin_lose_by_at_least_{margin}"] = margin_at_least_prob(lambda h, a, _j=joint: _j(a, h), margin)
    return cells


def _outcomes_for_actual(actual_home, actual_away):
    actual_total = actual_home + actual_away
    margin = actual_home - actual_away
    outcomes = {
        "moneyline_home_win": 1 if actual_home > actual_away else 0,
        "moneyline_away_win": 1 if actual_away > actual_home else 0,
    }
    for line in GAME_TOTAL_LINES:
        outcomes[f"game_total_over_{line}"] = 1 if actual_total > line else 0
    for line in TEAM_TOTAL_LINES:
        outcomes[f"team_total_away_over_{line}"] = 1 if actual_away > line else 0
        outcomes[f"team_total_home_over_{line}"] = 1 if actual_home > line else 0
    for m in MARGIN_THRESHOLDS:
        outcomes[f"run_margin_win_by_at_least_{m}"] = 1 if margin >= m else 0
        outcomes[f"run_margin_lose_by_at_least_{m}"] = 1 if (-margin) >= m else 0
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
    for family_prefix in ("game_total", "team_total_away", "team_total_home", "moneyline", "run_margin"):
        fam_control = [r for r in control_rows if r["cellKey"].startswith(family_prefix)]
        fam_candidate = [r for r in candidate_rows if r["cellKey"].startswith(family_prefix)]
        fam_pairing = pe.pair_eligible_observations(fam_control, fam_candidate, key_fn=key_fn)
        by_family[family_prefix] = pe.evaluate_probability_model_pair(fam_pairing, game_key="gameId", date_key="gameDate")

    return {"overall": overall, "byFamily": by_family}


def _primary_nb_delta(nb_result):
    deltas = [
        nb_result["byFamily"][fam]["pairedDelta"]["brierScore"]
        for fam in ("game_total", "team_total_away", "team_total_home", "moneyline")
        if nb_result["byFamily"].get(fam) and nb_result["byFamily"][fam]["pairedDelta"]["brierScore"] is not None
    ]
    return round(sum(deltas) / len(deltas), 6) if deltas else None


# ── Selection rule (preregistered) ─────────────────────────────────────────

def selection_passes(dev_mae_delta, dev_nb_primary_delta, val_mae_delta, val_nb_primary_delta, band_deltas):
    reasons = []
    if dev_mae_delta is None or dev_mae_delta >= 0:
        reasons.append(f"DEV MAE delta not negative (improved): {dev_mae_delta}")
    if dev_nb_primary_delta is None or dev_nb_primary_delta > PROBABILITY_IMPROVEMENT_TOLERANCE:
        reasons.append(f"DEV frozen-NB primary Brier delta not improved/preserved: {dev_nb_primary_delta}")
    if val_mae_delta is not None and val_mae_delta > DEGRADATION_TOLERANCE:
        reasons.append(f"VALIDATION MAE delta degraded beyond tolerance {DEGRADATION_TOLERANCE}: {val_mae_delta}")
    if val_nb_primary_delta is not None and val_nb_primary_delta > PROBABILITY_DEGRADATION_TOLERANCE:
        reasons.append(f"VALIDATION frozen-NB primary Brier delta degraded beyond tolerance {PROBABILITY_DEGRADATION_TOLERANCE}: {val_nb_primary_delta}")

    improving_bands = [b for b, d in band_deltas.items() if d.get("maeDelta") is not None and d["maeDelta"] < 0]
    non_improving_bands = [b for b, d in band_deltas.items() if d.get("maeDelta") is None or d["maeDelta"] >= 0]
    if len(improving_bands) == 1 and len(non_improving_bands) >= len(CALIBRATION_BANDS) - 1:
        reasons.append(f"improvement confined to one predicted-run band only ({improving_bands[0]}) -- fails the 'not band-driven' criterion")

    return (len(reasons) == 0), reasons


def evaluate_candidate_dev_val(dev_rows, val_rows, candidate_key):
    """DEV+VAL-only evaluation for one candidate vs C0 (selection stage
    -- 2026 is never touched here)."""
    obs_dev_c0, obs_dev_c = team_observations(dev_rows, "C0"), team_observations(dev_rows, candidate_key)
    obs_val_c0, obs_val_c = team_observations(val_rows, "C0"), team_observations(val_rows, candidate_key)

    dev_delta = paired_mean_mae_delta(obs_dev_c0, obs_dev_c)
    val_delta = paired_mean_mae_delta(obs_val_c0, obs_val_c)
    dev_bands = calibration_band_breakdown(obs_dev_c0, obs_dev_c)

    dev_nb = frozen_nb_probability_eval(dev_rows, "C0", candidate_key)
    val_nb = frozen_nb_probability_eval(val_rows, "C0", candidate_key)
    dev_nb_primary = _primary_nb_delta(dev_nb)
    val_nb_primary = _primary_nb_delta(val_nb)

    passes, reasons = selection_passes(dev_delta["maeDelta"], dev_nb_primary, val_delta["maeDelta"], val_nb_primary, dev_bands)

    return {
        "meanAccuracy": {
            "dev": {"C0": mean_accuracy_metrics(obs_dev_c0), candidate_key: mean_accuracy_metrics(obs_dev_c), "pairedDelta": dev_delta},
            "validation": {"C0": mean_accuracy_metrics(obs_val_c0), candidate_key: mean_accuracy_metrics(obs_val_c), "pairedDelta": val_delta},
        },
        "calibrationBandsDev": dev_bands,
        "frozenNbProbability": {"dev": dev_nb, "validation": val_nb},
        "devNbPrimaryDelta": dev_nb_primary, "valNbPrimaryDelta": val_nb_primary,
        "selection": {"passes": passes, "reasons": reasons},
    }


def evaluate_frozen_winner_holdout(dev_rows, val_rows, holdout_rows, candidate_key):
    """2026-holdout evaluation -- ONLY ever called for the frozen winner,
    after selection is complete (never for a rejected candidate)."""
    obs_dev_c0, obs_dev_c = team_observations(dev_rows, "C0"), team_observations(dev_rows, candidate_key)
    obs_val_c0, obs_val_c = team_observations(val_rows, "C0"), team_observations(val_rows, candidate_key)
    obs_holdout_c0, obs_holdout_c = team_observations(holdout_rows, "C0"), team_observations(holdout_rows, candidate_key)

    holdout_delta = paired_mean_mae_delta(obs_holdout_c0, obs_holdout_c)
    holdout_nb = frozen_nb_probability_eval(holdout_rows, "C0", candidate_key)

    return {
        "meanAccuracy": {"C0": mean_accuracy_metrics(obs_holdout_c0), candidate_key: mean_accuracy_metrics(obs_holdout_c), "pairedDelta": holdout_delta},
        "sideBreakdown": side_breakdown(obs_holdout_c0, obs_holdout_c),
        "seasonBreakdown": season_breakdown(obs_dev_c0 + obs_val_c0 + obs_holdout_c0, obs_dev_c + obs_val_c + obs_holdout_c, ALL_SEASONS),
        "calibrationBands": calibration_band_breakdown(obs_holdout_c0, obs_holdout_c),
        "teamRobustness": team_robustness(obs_holdout_c0, obs_holdout_c),
        "frozenNbProbability": holdout_nb,
        "devValFrozenNbPrimaryDelta_forReference": None,  # filled by caller if desired
    }


# ── Early-season (games 1-20) feasibility diagnostic ──────────────────────

def early_season_feasibility_diagnostic(rows_by_season):
    """Diagnostic only -- confirms the inherited MIN_PRIOR_GAMES_FOR_BASELINE=20
    eligibility floor (unchanged, never silently modified here) structurally
    excludes games 1-20 from this corpus, so no calibration bias can be
    measured for that range from THIS corpus. Recommends a separately
    preregistered milestone if that range is ever wanted -- never builds
    or tunes an early-season candidate here."""
    from lib.edgelab.backtest.team_offense_recency_reconstruction import MIN_PRIOR_GAMES_FOR_BASELINE
    min_prior_games_observed = min(
        (r["homeBaselineRaw"]["priorGamesThisSeason"] for s in rows_by_season for r in rows_by_season[s]),
        default=None,
    )
    return {
        "inheritedEligibilityFloor": MIN_PRIOR_GAMES_FOR_BASELINE,
        "minPriorGamesObservedInCorpus": min_prior_games_observed,
        "gamesOneToTwentyPresentInCorpus": bool(min_prior_games_observed is not None and min_prior_games_observed < MIN_PRIOR_GAMES_FOR_BASELINE),
        "finding": (
            "The inherited MIN_PRIOR_GAMES_FOR_BASELINE=20 eligibility floor (unchanged here, per instruction) "
            "structurally excludes every team-game with fewer than 20 prior games this season from this "
            "corpus -- MLB-RSCH-0014's own predicted-vs-actual data contains ZERO observations in that range, "
            "so no calibration bias estimate for games 1-20 can be produced from this corpus. This is the "
            "same structural gap MLB-RSCH-0012 already documented and was explicitly told not to silently "
            "change here either."
        ),
        "recommendation": (
            "A genuine early-season calibration/prior study is technically feasible in principle (the "
            "underlying boxscore/schedule caches already cover every game of the season, including games "
            "1-20) but requires a SEPARATELY preregistered milestone that deliberately lowers the "
            "eligibility floor and defines a PIT-safe early-season prior (e.g. blended prior-year team "
            "offense + a shrinking-weight current-year estimate) BEFORE examining results -- not an ad hoc "
            "extension of this experiment's own corpus or candidates."
        ),
    }


# ── Production mapping (read-only) ─────────────────────────────────────────

def production_mapping_notes():
    """Read-only findings from scripts/enrich_data.py -- no production
    code inspected here is ever modified."""
    return {
        "explicitRunMeanCalibrationInProduction": (
            "NO explicit post-hoc calibration layer (no affine/quadratic correction applied to the final "
            "expected-run mean) was found in scripts/enrich_data.py::compute_offense_baseline or its "
            "callers -- production's mean is a direct output of its own recency/shrinkage/adjustment blend, "
            "never re-calibrated against actual outcomes afterward."
        ),
        "homeAwayBiasAdjustedInProduction": (
            "Production applies a single home-field adjustment additively to the home side only (matching "
            "this research program's own expected_runs() convention) -- this is a HOME-FIELD adjustment "
            "(a baseball effect), not a calibration correction for home/away PREDICTION bias, which is the "
            "distinct question this experiment tests."
        ),
        "compressionExpansionExplicitlyCalibrated": (
            "NO -- production's shrinkage weight (15-vs-20, per MLB-RSCH-0012's own production mapping) "
            "controls how much the mean regresses toward league average, but this is a FIXED, un-validated-"
            "against-actual-outcomes weight, not a fit calibration slope."
        ),
        "productionMarketsDependingOnTheseMeans": (
            "Every production market probability derived from expected-run means depends on this "
            "construction: moneyline, game total (all offered lines), team total (all offered lines), and "
            "any run-margin-derived market -- i.e., effectively every probability the production pipeline "
            "surfaces to Kalshi eligibility/recommendation logic."
        ),
    }


# ── main ─────────────────────────────────────────────────────────────────

def main():
    print(f"[{EXPERIMENT_ID}] registering experiment/control...")
    control, definition = register_experiment()

    print(f"[{EXPERIMENT_ID}] building corpus (2022-2026, reusing MLB-RSCH-0009 loaders unchanged)...")
    rows_by_season, team_games_by_season, league_avg_offense, league_avg_bullpen_er9 = build_corpus()
    dev_rows = [r for s in DEV_SEASONS for r in rows_by_season[s]]
    val_rows = [r for s in VALIDATION_SEASONS for r in rows_by_season[s]]
    holdout_rows = [r for s in HOLDOUT_SEASONS for r in rows_by_season[s]]
    all_rows = dev_rows + val_rows + holdout_rows
    print(f"[{EXPERIMENT_ID}] rows: dev={len(dev_rows)} val={len(val_rows)} holdout={len(holdout_rows)} total={len(all_rows)}")

    print(f"[{EXPERIMENT_ID}] fitting C0 (frozen mean) home-field adjustment on DEV only...")
    hfa_c0 = fit_hfa_c0(dev_rows)
    attach_c0_predictions(all_rows, hfa_c0)
    print(f"[{EXPERIMENT_ID}] C0 home-field adjustment: {hfa_c0}")

    # ---- C0 calibration diagnostics FIRST (before any candidate is fit) ----
    obs_dev_c0_all = team_observations(dev_rows, "C0")
    c0_diagnostics = {
        "overall": c0_diagnostic_band_table(obs_dev_c0_all),
        "home": c0_diagnostic_band_table([o for o in obs_dev_c0_all if o["side"] == "home"]),
        "away": c0_diagnostic_band_table([o for o in obs_dev_c0_all if o["side"] == "away"]),
        "bySeason": {str(s): c0_diagnostic_band_table([o for o in obs_dev_c0_all if o["season"] == s]) for s in DEV_SEASONS},
    }
    print(f"[{EXPERIMENT_ID}] C0 DEV overall calibration slope/intercept: {mean_accuracy_metrics(obs_dev_c0_all)['calibrationSlope']}/{mean_accuracy_metrics(obs_dev_c0_all)['calibrationIntercept']}")

    early_season = early_season_feasibility_diagnostic(rows_by_season)
    print(f"[{EXPERIMENT_ID}] early-season feasibility: gamesOneToTwentyPresent={early_season['gamesOneToTwentyPresentInCorpus']}")

    # ---- Fit C1/C2/C3 (DEVELOPMENT ONLY) ----
    print(f"[{EXPERIMENT_ID}] fitting C1/C2/C3 calibration candidates on DEVELOPMENT only...")
    c1_params, c1_diag = fit_c1_global_affine_dev_only(obs_dev_c0_all)
    c2_params, c2_diag = fit_c2_home_away_affine_dev_only(obs_dev_c0_all)
    c3_params, c3_diag = fit_c3_quadratic_dev_only(obs_dev_c0_all)
    print(f"[{EXPERIMENT_ID}] C1={c1_params} C2={c2_params} C3={c3_params}")

    candidates = {}
    for key, kind, params in ((C1, C1, c1_params), (C2, C2, c2_params), (C3, C3, c3_params)):
        if params is None:
            candidates[key] = {"params": None, "devVal": None}
            continue
        attach_calibrated_predictions(all_rows, key, kind, params)
        dev_val_result = evaluate_candidate_dev_val(dev_rows, val_rows, key)
        candidates[key] = {"params": params, "devVal": dev_val_result}
        print(f"[{EXPERIMENT_ID}] {key} DEV MAE delta={dev_val_result['meanAccuracy']['dev']['pairedDelta']['maeDelta']} "
              f"VAL MAE delta={dev_val_result['meanAccuracy']['validation']['pairedDelta']['maeDelta']} "
              f"passes={dev_val_result['selection']['passes']} reasons={dev_val_result['selection']['reasons']}")

    # ---- Selection: DEV+VAL only, 2026 untouched so far ----
    passing = [k for k, v in candidates.items() if v["devVal"] is not None and v["devVal"]["selection"]["passes"]]
    if len(passing) == 0:
        frozen_winner_key, frozen_winner_kind, frozen_winner_params = C0, None, None
        selection_note = "no calibration candidate passed the preregistered DEV/VAL selection rule -- control (C0, no calibration) retained"
    elif len(passing) == 1:
        frozen_winner_key = passing[0]
        frozen_winner_kind = frozen_winner_key
        frozen_winner_params = candidates[frozen_winner_key]["params"]
        selection_note = f"{frozen_winner_key} was the only candidate to pass the preregistered DEV/VAL selection rule"
    else:
        frozen_winner_key, frozen_winner_kind, frozen_winner_params = C0, None, None
        selection_note = (
            f"{passing} all mechanically passed the preregistered DEV/VAL selection rule -- no tie-break rule "
            "was preregistered for this scenario, so this experiment does not pick among them and defers to "
            "the control (C0) rather than improvising a selection method post hoc"
        )
    print(f"[{EXPERIMENT_ID}] selection across candidates: passing={passing} -> frozen winner={frozen_winner_key} ({selection_note})")

    # ---- Unlock 2026 holdout -- ONLY for the frozen winner (only after selection is frozen) ----
    if frozen_winner_key == C0:
        holdout_result = None
        print(f"[{EXPERIMENT_ID}] frozen winner is C0 (no calibration) -- no separate holdout evaluation needed (C0 vs itself is trivially zero).")
    else:
        holdout_result = evaluate_frozen_winner_holdout(dev_rows, val_rows, holdout_rows, frozen_winner_key)
        print(f"[{EXPERIMENT_ID}] {frozen_winner_key} 2026 holdout MAE delta={holdout_result['meanAccuracy']['pairedDelta']['maeDelta']}")

    # ---- Pinnacle secondary stage (existing sample, ONLY the frozen winner vs C0, no new spend) ----
    print(f"[{EXPERIMENT_ID}] Pinnacle secondary stage (existing sample, no new Odds API spend)...")
    import run_proxy_vs_pinnacle_experiment as rsch0008
    pinnacle_result = None
    try:
        pinnacle_all_rows = [r for season in ALL_SEASONS for r in rsch0008.build_matched_rows(season)]
        for r in pinnacle_all_rows:
            rsch0008.enrich_row(r, hfa_c0)  # C0 (this experiment's own frozen mean/hfa) -- populates expectedHomeRuns/awayRuns, pinnacleMlHomeFair, pinnacleTotalOverFair, actualHomeWin, actualOver
            eh_c0, ea_c0 = r["expectedHomeRuns"], r["expectedAwayRuns"]
            r["mlHomeProb_C0"], _ = game_ml_proxy_probability(eh_c0, ea_c0) if eh_c0 is not None and ea_c0 is not None else (None, None)
            r["totalOverProb_C0"] = game_total_proxy_probability(eh_c0, ea_c0, r["pinnacleTotalLine"]) if eh_c0 is not None and ea_c0 is not None and r.get("pinnacleTotalLine") is not None else None
            if frozen_winner_key != C0 and eh_c0 is not None and ea_c0 is not None:
                eh_cal = calibrate_value(eh_c0, frozen_winner_kind, frozen_winner_params, side="home")
                ea_cal = calibrate_value(ea_c0, frozen_winner_kind, frozen_winner_params, side="away")
                r[f"mlHomeProb_{frozen_winner_key}"], _ = game_ml_proxy_probability(eh_cal, ea_cal)
                r[f"totalOverProb_{frozen_winner_key}"] = game_total_proxy_probability(eh_cal, ea_cal, r["pinnacleTotalLine"]) if r.get("pinnacleTotalLine") is not None else None
            else:
                r[f"mlHomeProb_{frozen_winner_key}"] = r["mlHomeProb_C0"]
                r[f"totalOverProb_{frozen_winner_key}"] = r["totalOverProb_C0"]

        pinnacle_ml_c0 = rsch0008.paired_analysis(pinnacle_all_rows, "mlHomeProb_C0", "pinnacleMlHomeFair", "actualHomeWin", "PINNACLE/ML/C0")
        pinnacle_ml_winner = rsch0008.paired_analysis(pinnacle_all_rows, f"mlHomeProb_{frozen_winner_key}", "pinnacleMlHomeFair", "actualHomeWin", f"PINNACLE/ML/{frozen_winner_key}")
        pinnacle_total_c0 = rsch0008.paired_analysis(pinnacle_all_rows, "totalOverProb_C0", "pinnacleTotalOverFair", "actualOver", "PINNACLE/TOTAL/C0")
        pinnacle_total_winner = rsch0008.paired_analysis(pinnacle_all_rows, f"totalOverProb_{frozen_winner_key}", "pinnacleTotalOverFair", "actualOver", f"PINNACLE/TOTAL/{frozen_winner_key}")
        pinnacle_result = {
            "nRows": len(pinnacle_all_rows), "frozenWinner": frozen_winner_key,
            "ml": {"c0": pinnacle_ml_c0, "winner": pinnacle_ml_winner},
            "total": {"c0": pinnacle_total_c0, "winner": pinnacle_total_winner},
        }
    except Exception as exc:  # secondary stage only -- must never abort the primary result
        pinnacle_result = {"error": str(exc)}
    print(f"[{EXPERIMENT_ID}] Pinnacle secondary stage result: {pinnacle_result if isinstance(pinnacle_result, dict) and 'error' in pinnacle_result else 'OK'}")

    production_mapping = production_mapping_notes()

    report = {
        "experimentId": EXPERIMENT_ID,
        "controlModelId": control["controlModelId"],
        "corpus": {"devRows": len(dev_rows), "valRows": len(val_rows), "holdoutRows": len(holdout_rows), "totalRows": len(all_rows)},
        "homeFieldAdjustmentC0": hfa_c0,
        "c0Diagnostics": c0_diagnostics,
        "earlySeasonFeasibility": early_season,
        "candidateFits": {"c1Diagnostics": c1_diag, "c2Diagnostics": c2_diag, "c3Diagnostics": c3_diag},
        "candidates": candidates,
        "selection": {
            "passingCandidates": passing,
            "frozenWinner": frozen_winner_key,
            "frozenWinnerParams": frozen_winner_params,
            "note": selection_note,
        },
        "holdout2026": holdout_result,
        "pinnacleSecondary": pinnacle_result,
        "productionMapping": production_mapping,
    }

    out_path = os.path.join("data", "edgelab", "analytics", "latest_mlb_rsch_0014_mean_calibration.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    print(f"[{EXPERIMENT_ID}] wrote {out_path}")
    return report


if __name__ == "__main__":
    main()
