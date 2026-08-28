#!/usr/bin/env python3
"""
scripts/edgelab/run_distribution_calibration_experiment.py
====================================================================
Research Lab, experiment MLB-RSCH-0010: "Run Distribution / Probability
Calibration". Separates MEAN QUALITY (frozen, MLB-RSCH-0009's own
{offense, bullpen} proxy, never touched here) from DISTRIBUTION QUALITY
(this milestone's only concern): does a better scoring distribution
than the Poisson baseline (D0) produce repeatable out-of-sample
probability improvement, over the same large 2022-2026 historical
corpus, for game result, game totals, team totals, and run
lines/margins? RESEARCH ONLY. NO production changes. NO new Odds API
spend (reuses MLB-RSCH-0008/0009's existing Pinnacle cache unchanged,
secondary stage only, never used for candidate selection).

CANDIDATES (lib.edgelab.backtest.run_distributions, this milestone's
own new module):
  D0 (control): independent Poisson marginals -- reproduces
      scripts/build_market_ledger.py's own p_team_wins/p_over_total
      exactly (proven by that module's own test suite).
  D1: independent negative-binomial marginals, SAME means, ONE dev-fit
      overdispersion parameter (method-of-moments, closed-form).
  D2: bivariate Poisson (shared-environment correlation), SAME
      marginals as D0 (structural), ONE dev-fit correlation parameter
      (method-of-moments, closed-form).
  D3: NOT included -- combining overdispersion with correlation in one
      parsimonious, cleanly preregistrable closed-form model would
      require a genuine bivariate negative-binomial family (multiple
      competing parameterizations exist, none with as clean a single-
      parameter method-of-moments fit as D1/D2 individually have) --
      exactly the "would materially complicate interpretation or create
      tuning freedom" case the mission's own instruction says to skip.

MEAN MODEL: lambda_home/lambda_away for every row come from
MLB-RSCH-0009's own frozen final composition ({offense, bullpen}) --
this script calls that milestone's own team_baseline/expected_runs/
baseline_for_components/fit_home_field_adjustment_for_components
functions UNCHANGED (never reimplemented, never adjusted) to
reconstruct the identical mean model; it does not re-run MLB-RSCH-0009's
own forward-selection (park was already rejected there and is not
revisited here).

MODEL SELECTION (preregistered before any real result was computed): a
candidate may become the FINAL DISTRIBUTION only if, versus D0, its
PRIMARY_METRIC_CELLS-averaged Brier is (1) lower (better) on
DEVELOPMENT, (2) not degraded by more than DEGRADATION_TOLERANCE on
VALIDATION, and (3) improved (not degraded) in a MAJORITY (>= 3 of 5)
of the individual primary cells on DEVELOPMENT -- never confined to one
cell. Between D1 and D2, if both pass, the one with the more negative
DEVELOPMENT aggregate delta wins. Pinnacle and 2026 are never consulted
during selection.
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

from lib.edgelab.backtest.proxy_model import expected_runs
from lib.edgelab.backtest.run_distributions import (
    negative_binomial_pmf,
    independent_joint_pmf,
    bivariate_poisson_joint_pmf,
    fit_overdispersion_dev_only,
    fit_correlation_dev_only,
    empirical_mean_variance,
    empirical_correlation,
    empirical_tail_frequency,
    poisson_implied_tail_frequency,
    candidate_implied_tail_frequency,
)
from lib.edgelab import experiment_registry as reg
from lib.edgelab import dispositions as disp
from lib.edgelab import evidence_levels as ev
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import paired_evaluation as pe
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab.research_stats import (
    DEFAULT_BOOTSTRAP_SEED,
    brier_and_log_loss_summary,
    expected_calibration_error,
    independent_unit_count,
    sample_size_status,
    game_clustered_bootstrap_ci,
)

import run_proxy_ablation_experiment as rsch0009  # noqa: E402
from build_market_ledger import poisson_pmf  # noqa: E402

EXPERIMENT_ID = "MLB-RSCH-0010"
REGISTRATION_TIMESTAMP = "2026-08-28T01:30:00Z"

DEV_SEASONS = [2022, 2023, 2024]
VALIDATION_SEASONS = [2025]
HOLDOUT_SEASONS = [2026]
ALL_SEASONS = DEV_SEASONS + VALIDATION_SEASONS + HOLDOUT_SEASONS

# The FROZEN MLB-RSCH-0009 final composition -- never revisited here.
FROZEN_MEAN_COMPONENTS = frozenset({"offense", "bullpen"})

# Preregistered evaluation lines/thresholds -- fixed before any result
# was computed, never chosen post hoc.
GAME_TOTAL_LINES = (7.5, 8.5, 9.5, 10.5)
TEAM_TOTAL_LINES = (2.5, 3.5, 4.5, 5.5)
MARGIN_THRESHOLDS = (2, 3)  # "win by N+"; run-line +/-1.5 is IDENTICAL to win/lose-by-2+ for integer run scoring -- computed once, not duplicated

# Preregistered tail definitions (never chosen after viewing results).
TEAM_RUN_TAILS = (
    ("0_runs", 0, "exactly"),
    ("1_run", 1, "exactly"),
    ("5_plus", 5, "at_least"),
    ("7_plus", 7, "at_least"),
    ("10_plus", 10, "at_least"),
)
GAME_TOTAL_TAILS = (7.5, 12.5, 14.5)  # low/high game-total tails
MARGIN_TAILS = (5, 7)  # large-margin blowouts

MAX_RUNS = 25  # generous truncation for this milestone's own summations (Poisson/NB tail mass beyond 25 combined runs at typical MLB lambdas is < 1e-8) -- a performance choice, not a modeling one; separate from run_distributions.MAX_RUNS=30, which stays the library's own tested default

MIN_GAMES_EXPLORATORY = 50
MIN_GAMES_CONFIDENT = 50
DEGRADATION_TOLERANCE = 0.005

D0, D1, D2 = "D0_poisson", "D1_negative_binomial", "D2_bivariate_poisson"

EDGELAB_DIR = os.path.join(_ROOT, "data", "edgelab")


# ── Registration ────────────────────────────────────────────────────────

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
        name="mlb_rsch_0010_run_distribution_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0010 run distribution v1: MLB-RSCH-0009 frozen {offense,bullpen} means "
                        "+ forward-selected scoring distribution from {D0 Poisson, D1 negative-binomial, D2 bivariate Poisson}"
        ),
        probability_adapter_identity="lib.edgelab.backtest.run_distributions.home_win_and_push_prob;total_over_prob;team_total_over_prob;margin_at_least_prob",
        model_engine_family="pit_safe_research_distribution_v1",
        required_input_provenance=["team_recent_game_log_reconstruction"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "A NEW historical RESEARCH distribution layer, built on top of MLB-RSCH-0009's frozen "
            "{offense, bullpen} mean model (unchanged). Tests whether Poisson (D0, control) is beaten "
            "by an overdispersed negative-binomial (D1) or a correlated bivariate-Poisson (D2) "
            "candidate, each with exactly one dev-fit closed-form parameter."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Run Distribution / Probability Calibration",
        hypothesis=(
            "H1: MLB scoring is overdispersed relative to Poisson (D1 improves probability quality). "
            "H2: home and away scores are meaningfully correlated after conditioning on expected runs "
            "(D2 improves probability quality). H3: any improvement is not uniform across market "
            "families -- distribution changes may matter more for game totals/margins than moneyline. "
            "H4: distribution improvement, if real, narrows (without necessarily closing) the "
            "MLB-RSCH-0008/0009 Pinnacle gap on the existing matched sample."
        ),
        research_question=(
            "Does a better scoring distribution than the current Poisson baseline (holding "
            "MLB-RSCH-0009's frozen expected-runs mean model fixed) produce repeatable out-of-sample "
            "probability improvement over thousands of games, for game result, game totals, team "
            "totals, and run lines/margins?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E2_PIT_HISTORICAL,
        target_population=(
            "The same MLB regular-season 2022-2026 games MLB-RSCH-0009's own baseball-level ablation "
            "used (both teams >= MIN_PRIOR_GAMES_FOR_BASELINE prior completed games this season) -- "
            "reused via that milestone's own row-building functions, unchanged."
        ),
        market_families=["game_result", "game_total", "team_total", "run_line_margin"],
        eligibility_criteria=[
            "both teams have >= MIN_PRIOR_GAMES_FOR_BASELINE prior completed games this season (MLB-RSCH-0009's own eligibility rule, unchanged)",
        ],
        exclusion_criteria=[
            "D3 (overdispersion + correlation combined) -- not preregistered; would require a materially more complex bivariate negative-binomial family without a single clean closed-form fit",
        ],
        prediction_checkpoints=["SEASON_TO_DATE_PREGAME"],
        primary_metric="mean Brier across {game_result, game_total@7.5/8.5/9.5/10.5} vs D0 (candidate minus D0), game-clustered 95% CI",
        secondary_metrics=[
            "log-loss delta", "team-total Brier deltas", "margin/run-line Brier deltas",
            "tail-frequency calibration (shutouts, high-scoring, large margins)",
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
            "evidenceLevel E2_PIT_HISTORICAL: same basis as MLB-RSCH-0008/0009. Mean model is frozen "
            "and reused UNCHANGED from MLB-RSCH-0009 -- this experiment's entire scope is the "
            "probability-distribution layer on top of that fixed mean, never the mean itself."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Reconstruct MLB-RSCH-0009's frozen mean model (lambda_home/lambda_away) ──

def build_rows_with_frozen_lambdas():
    """
    Reuses MLB-RSCH-0009's own module-level functions UNCHANGED to
    reconstruct the identical row corpus and the identical frozen
    {offense, bullpen} mean model (same closed-form dev-only fits on
    the same data always produce the same frozen constants). Returns
    (rows_by_season, dev_rows, val_rows, holdout_rows) with
    "lambdaHome"/"lambdaAway"/"actualHomeRuns"/"actualAwayRuns" attached
    to every row -- this script's ENTIRE input; nothing about offense/
    bullpen feature construction is touched.
    """
    rows_by_season, relief_by_season, team_games_by_season, env_lookup_by_season = {}, {}, {}, {}
    for season in ALL_SEASONS:
        team_games = rsch0009.load_all_team_games_with_venue(season)
        team_games_by_season[season] = team_games
        relief_er9 = rsch0009.load_relief_er9_games(season, team_games)
        relief_by_season[season] = relief_er9
        env_lookup = rsch0009.build_season_environment_lookup(
            [g for games in team_games.values() for g in games if g.get("side") == "home"]
        )
        env_lookup_by_season[season] = env_lookup
        rows_by_season[season] = rsch0009.build_season_rows(season, team_games, relief_er9, env_lookup)

    dev_rows = [r for s in DEV_SEASONS for r in rows_by_season[s]]
    val_rows = [r for s in VALIDATION_SEASONS for r in rows_by_season[s]]
    holdout_rows = [r for s in HOLDOUT_SEASONS for r in rows_by_season[s]]
    all_rows = dev_rows + val_rows + holdout_rows

    dev_home_team_games = [g for s in DEV_SEASONS for g in team_games_by_season[s].values()]
    league_avg_offense = rsch0009.fit_league_average_runs_per_game(dev_home_team_games)
    dev_relief_er9_team_games = [g for s in DEV_SEASONS for g in relief_by_season[s].values()]
    league_avg_bullpen_er9 = rsch0009.fit_league_average_bullpen_er9(dev_relief_er9_team_games)
    rsch0009.attach_stabilized_components(all_rows, league_avg_offense, league_avg_bullpen_er9)

    frozen_hfa = rsch0009.fit_home_field_adjustment_for_components(dev_rows, FROZEN_MEAN_COMPONENTS)

    for r in all_rows:
        hb = rsch0009.baseline_for_components(r["homeBaselineRaw"], r["homeOffenseStabilized"], r["homeBullpenStabilized"], FROZEN_MEAN_COMPONENTS)
        ab = rsch0009.baseline_for_components(r["awayBaselineRaw"], r["awayOffenseStabilized"], r["awayBullpenStabilized"], FROZEN_MEAN_COMPONENTS)
        eh, ea = expected_runs(hb, ab, home_field_adjustment=frozen_hfa)
        r["lambdaHome"], r["lambdaAway"] = eh, ea

    return (
        rows_by_season, dev_rows, val_rows, holdout_rows, frozen_hfa, league_avg_offense, league_avg_bullpen_er9,
        relief_by_season, team_games_by_season, env_lookup_by_season,
    )


# ── Per-row-per-candidate evaluation (matrix-cached for performance) ────

def _joint_matrix(joint_fn, max_runs=MAX_RUNS):
    return [[joint_fn(h, a) for a in range(max_runs + 1)] for h in range(max_runs + 1)]


def evaluate_row(matrix, max_runs=MAX_RUNS):
    """
    Pure. From ONE precomputed joint matrix (avoids re-invoking the
    candidate's own joint(h, a) closure once per derived quantity),
    returns every probability this milestone needs for one game: game
    result, game totals at GAME_TOTAL_LINES, team totals at
    TEAM_TOTAL_LINES for both sides, and margins at MARGIN_THRESHOLDS
    (plus their complements).
    """
    p_home_win = p_push = 0.0
    total_dist = defaultdict(float)
    margin_dist = defaultdict(float)
    home_marginal = [0.0] * (max_runs + 1)
    away_marginal = [0.0] * (max_runs + 1)

    for h in range(max_runs + 1):
        row = matrix[h]
        for a in range(max_runs + 1):
            p = row[a]
            if p == 0.0:
                continue
            if h > a:
                p_home_win += p
            elif h == a:
                p_push += p
            total_dist[h + a] += p
            margin_dist[h - a] += p
            home_marginal[h] += p
            away_marginal[a] += p

    result = {"pHomeWin": p_home_win, "pPush": p_push, "pAwayWin": 1.0 - p_home_win - p_push}

    for line in GAME_TOTAL_LINES:
        result[f"pTotalOver_{line}"] = sum(v for total, v in total_dist.items() if total > line)

    for line in TEAM_TOTAL_LINES:
        result[f"pHomeTeamTotalOver_{line}"] = sum(home_marginal[k] for k in range(int(line) + 1, max_runs + 1))
        result[f"pAwayTeamTotalOver_{line}"] = sum(away_marginal[k] for k in range(int(line) + 1, max_runs + 1))

    for m in MARGIN_THRESHOLDS:
        result[f"pWinByAtLeast_{m}"] = sum(v for margin, v in margin_dist.items() if margin >= m)
    result["pLoseByAtLeast_2"] = sum(v for margin, v in margin_dist.items() if margin <= -2)

    result["expectedTotal"] = sum((h + a) * matrix[h][a] for h in range(max_runs + 1) for a in range(max_runs + 1))
    return result


def joint_for_candidate(candidate, lambda_home, lambda_away, dispersion=None, lambda_c=None):
    if candidate == D0:
        return independent_joint_pmf(lambda k: poisson_pmf(k, lambda_home), lambda k: poisson_pmf(k, lambda_away))
    if candidate == D1:
        return independent_joint_pmf(
            lambda k: negative_binomial_pmf(k, lambda_home, dispersion),
            lambda k: negative_binomial_pmf(k, lambda_away, dispersion),
        )
    if candidate == D2:
        return bivariate_poisson_joint_pmf(lambda_home, lambda_away, lambda_c)
    raise ValueError(f"Unknown candidate {candidate!r}")


def attach_candidate_predictions(rows, candidate, key, dispersion=None, lambda_c=None):
    for r in rows:
        lam_h, lam_a = r.get("lambdaHome"), r.get("lambdaAway")
        if lam_h is None or lam_a is None:
            r[f"pred_{key}"] = None
            continue
        joint_fn = joint_for_candidate(candidate, lam_h, lam_a, dispersion=dispersion, lambda_c=lambda_c)
        matrix = _joint_matrix(joint_fn)
        r[f"pred_{key}"] = evaluate_row(matrix)


# ── Primary metric evaluation (game-clustered) ──────────────────────────

def _actual_over(row, line):
    total = row["actualHomeRuns"] + row["actualAwayRuns"]
    return 1 if total > line else 0


def _actual_team_over(row, side, line):
    runs = row["actualHomeRuns"] if side == "home" else row["actualAwayRuns"]
    return 1 if runs > line else 0


def _actual_margin_at_least(row, m):
    return 1 if (row["actualHomeRuns"] - row["actualAwayRuns"]) >= m else 0


def evaluate_split(rows, key):
    """Full metric bundle for one split/candidate: Brier/logloss/ECE for
    game result and every game-total/team-total/margin cell, plus MAE/
    RMSE/Poisson-style NLL of expected total runs."""
    valid = [r for r in rows if r.get(f"pred_{key}") is not None and r.get("actualHomeRuns") is not None and r.get("actualAwayRuns") is not None]
    n = len(valid)
    independent_games = independent_unit_count(valid, key="gamePk")

    def _pairs_for(prob_key, actual_fn):
        return [(r[f"pred_{key}"][prob_key], actual_fn(r)) for r in valid]

    out = {
        "n": n, "independentGames": independent_games,
        "sampleSizeStatus": sample_size_status(n, independent_games=independent_games),
    }

    ml_pairs = _pairs_for("pHomeWin", lambda r: 1 if r["actualHomeRuns"] > r["actualAwayRuns"] else 0)
    out["gameResult"] = dict(zip(("brier", "logLoss"), brier_and_log_loss_summary(ml_pairs)))
    out["gameResult"]["ece"] = expected_calibration_error(ml_pairs)

    out["gameTotal"] = {}
    for line in GAME_TOTAL_LINES:
        pairs = _pairs_for(f"pTotalOver_{line}", lambda r, ln=line: _actual_over(r, ln))
        brier, logloss = brier_and_log_loss_summary(pairs)
        out["gameTotal"][str(line)] = {"brier": brier, "logLoss": logloss, "ece": expected_calibration_error(pairs)}

    out["teamTotal"] = {}
    for line in TEAM_TOTAL_LINES:
        for side in ("home", "away"):
            pairs = _pairs_for(f"p{side.capitalize()}TeamTotalOver_{line}", lambda r, ln=line, sd=side: _actual_team_over(r, sd, ln))
            brier, logloss = brier_and_log_loss_summary(pairs)
            out["teamTotal"][f"{side}_{line}"] = {"brier": brier, "logLoss": logloss}

    out["margin"] = {}
    for m in MARGIN_THRESHOLDS:
        pairs = _pairs_for(f"pWinByAtLeast_{m}", lambda r, mm=m: _actual_margin_at_least(r, mm))
        brier, logloss = brier_and_log_loss_summary(pairs)
        out["margin"][f"winBy{m}Plus"] = {"brier": brier, "logLoss": logloss}
    pairs = _pairs_for("pLoseByAtLeast_2", lambda r: 1 if (r["actualHomeRuns"] - r["actualAwayRuns"]) <= -2 else 0)
    brier, logloss = brier_and_log_loss_summary(pairs)
    out["margin"]["loseBy2Plus"] = {"brier": brier, "logLoss": logloss}

    total_errors = [(r[f"pred_{key}"]["expectedTotal"], r["actualHomeRuns"] + r["actualAwayRuns"]) for r in valid]
    if total_errors:
        out["totalExpectedMAE"] = round(sum(abs(e - a) for e, a in total_errors) / len(total_errors), 4)
        out["totalExpectedRMSE"] = round(math.sqrt(sum((e - a) ** 2 for e, a in total_errors) / len(total_errors)), 4)
    else:
        out["totalExpectedMAE"] = out["totalExpectedRMSE"] = None

    return out


PRIMARY_CELLS = ["gameResult"] + [f"gameTotal_{line}" for line in GAME_TOTAL_LINES]


def _cell_brier(split_result, cell):
    if cell == "gameResult":
        return split_result["gameResult"]["brier"]
    line = cell.split("_", 1)[1]
    return split_result["gameTotal"][line]["brier"]


def aggregate_primary_delta(candidate_split, d0_split):
    cell_deltas = [
        _cell_brier(candidate_split, cell) - _cell_brier(d0_split, cell)
        for cell in PRIMARY_CELLS
        if _cell_brier(candidate_split, cell) is not None and _cell_brier(d0_split, cell) is not None
    ]
    if not cell_deltas:
        return None, 0
    improved_cells = sum(1 for d in cell_deltas if d < 0)
    return round(sum(cell_deltas) / len(cell_deltas), 6), improved_cells


def bootstrap_primary_delta_ci(rows, key_candidate, key_d0):
    def _value_fn(subset):
        valid = [r for r in subset if r.get(f"pred_{key_candidate}") is not None and r.get(f"pred_{key_d0}") is not None]
        if not valid:
            return None
        deltas = []
        for cell in PRIMARY_CELLS:
            if cell == "gameResult":
                prob_key, actual_fn = "pHomeWin", (lambda r: 1 if r["actualHomeRuns"] > r["actualAwayRuns"] else 0)
            else:
                line = float(cell.split("_", 1)[1])
                prob_key, actual_fn = f"pTotalOver_{line}", (lambda r, ln=line: _actual_over(r, ln))
            pairs_c = [(r[f"pred_{key_candidate}"][prob_key], actual_fn(r)) for r in valid]
            pairs_0 = [(r[f"pred_{key_d0}"][prob_key], actual_fn(r)) for r in valid]
            brier_c, _ = brier_and_log_loss_summary(pairs_c)
            brier_0, _ = brier_and_log_loss_summary(pairs_0)
            if brier_c is not None and brier_0 is not None:
                deltas.append(brier_c - brier_0)
        return sum(deltas) / len(deltas) if deltas else None

    lo, hi, method = game_clustered_bootstrap_ci(rows, _value_fn, cluster_key="gamePk", seed=DEFAULT_BOOTSTRAP_SEED)
    return {"low": lo, "high": hi, "method": method}


# ── main ──────────────────────────────────────────────────────────────

def main():
    experiment = register_experiment()[1]

    (
        rows_by_season, dev_rows, val_rows, holdout_rows, frozen_hfa, league_avg_offense, league_avg_bullpen_er9,
        relief_by_season, team_games_by_season, env_lookup_by_season,
    ) = build_rows_with_frozen_lambdas()
    all_rows = dev_rows + val_rows + holdout_rows

    # ---- Empirical diagnostics (DEVELOPMENT only) ----
    dev_home_runs = [r["actualHomeRuns"] for r in dev_rows]
    dev_away_runs = [r["actualAwayRuns"] for r in dev_rows]
    dev_totals = [r["actualHomeRuns"] + r["actualAwayRuns"] for r in dev_rows]
    home_mean, home_var = empirical_mean_variance(dev_home_runs)
    away_mean, away_var = empirical_mean_variance(dev_away_runs)
    total_mean, total_var = empirical_mean_variance(dev_totals)
    home_away_corr = empirical_correlation([(r["actualHomeRuns"], r["actualAwayRuns"]) for r in dev_rows])

    diagnostics = {
        "homeRuns": {"empiricalMean": home_mean, "empiricalVariance": home_var, "poissonImpliedVariance": home_mean},
        "awayRuns": {"empiricalMean": away_mean, "empiricalVariance": away_var, "poissonImpliedVariance": away_mean},
        "gameTotal": {"empiricalMean": total_mean, "empiricalVariance": total_var, "poissonImpliedVariance": total_mean},
        "homeAwayCorrelation": home_away_corr,
        "teamRunTails": {
            label: {
                "empirical": empirical_tail_frequency(dev_home_runs + dev_away_runs, threshold, mode),
                "poissonImplied": poisson_implied_tail_frequency(
                    [r.get("lambdaHome") for r in dev_rows] + [r.get("lambdaAway") for r in dev_rows], threshold, mode
                ),
            }
            for label, threshold, mode in TEAM_RUN_TAILS
        },
        "gameTotalTails": {
            f"over_{line}": empirical_tail_frequency(dev_totals, line, "at_least") for line in GAME_TOTAL_TAILS
        },
        "marginTails": {
            f"margin_{m}_plus": empirical_tail_frequency(
                [abs(r["actualHomeRuns"] - r["actualAwayRuns"]) for r in dev_rows], m, "at_least"
            ) for m in MARGIN_TAILS
        },
    }

    # ---- Dev-only closed-form parameter fitting ----
    overdispersion_pairs = (
        [(r["actualHomeRuns"], r.get("lambdaHome")) for r in dev_rows]
        + [(r["actualAwayRuns"], r.get("lambdaAway")) for r in dev_rows]
    )
    dispersion = fit_overdispersion_dev_only(overdispersion_pairs)

    correlation_triples = [
        (r["actualHomeRuns"], r.get("lambdaHome"), r["actualAwayRuns"], r.get("lambdaAway")) for r in dev_rows
    ]
    lambda_c = fit_correlation_dev_only(correlation_triples)

    # ---- Attach predictions for all three candidates, all splits ----
    attach_candidate_predictions(all_rows, D0, D0)
    attach_candidate_predictions(all_rows, D1, D1, dispersion=dispersion)
    attach_candidate_predictions(all_rows, D2, D2, lambda_c=lambda_c)

    d0_dev, d1_dev, d2_dev = evaluate_split(dev_rows, D0), evaluate_split(dev_rows, D1), evaluate_split(dev_rows, D2)
    d0_val, d1_val, d2_val = evaluate_split(val_rows, D0), evaluate_split(val_rows, D1), evaluate_split(val_rows, D2)

    d1_dev_delta, d1_dev_improved_cells = aggregate_primary_delta(d1_dev, d0_dev)
    d2_dev_delta, d2_dev_improved_cells = aggregate_primary_delta(d2_dev, d0_dev)
    d1_val_delta, _ = aggregate_primary_delta(d1_val, d0_val)
    d2_val_delta, _ = aggregate_primary_delta(d2_val, d0_val)

    def _passes(dev_delta, val_delta, improved_cells):
        return (
            dev_delta is not None and dev_delta < 0
            and val_delta is not None and val_delta <= DEGRADATION_TOLERANCE
            and improved_cells >= 3
        )

    d1_passes = _passes(d1_dev_delta, d1_val_delta, d1_dev_improved_cells)
    d2_passes = _passes(d2_dev_delta, d2_val_delta, d2_dev_improved_cells)

    if d1_passes and d2_passes:
        final_candidate = D1 if d1_dev_delta <= d2_dev_delta else D2
    elif d1_passes:
        final_candidate = D1
    elif d2_passes:
        final_candidate = D2
    else:
        final_candidate = D0

    # ---- Unlock 2026 holdout ----
    d0_holdout = evaluate_split(holdout_rows, D0)
    final_holdout = evaluate_split(holdout_rows, final_candidate) if final_candidate != D0 else d0_holdout
    holdout_delta, holdout_improved_cells = aggregate_primary_delta(final_holdout, d0_holdout) if final_candidate != D0 else (0.0, len(PRIMARY_CELLS))
    holdout_delta_ci = bootstrap_primary_delta_ci(holdout_rows, final_candidate, D0) if final_candidate != D0 else None

    # ---- Family-by-family holdout deltas ----
    family_deltas = {}
    if final_candidate != D0:
        family_deltas["gameResult"] = round(final_holdout["gameResult"]["brier"] - d0_holdout["gameResult"]["brier"], 6)
        family_deltas["gameTotal"] = {
            str(line): round(final_holdout["gameTotal"][str(line)]["brier"] - d0_holdout["gameTotal"][str(line)]["brier"], 6)
            for line in GAME_TOTAL_LINES
        }
        family_deltas["teamTotal"] = {
            k: round(final_holdout["teamTotal"][k]["brier"] - d0_holdout["teamTotal"][k]["brier"], 6)
            for k in final_holdout["teamTotal"]
        }
        family_deltas["margin"] = {
            k: round(final_holdout["margin"][k]["brier"] - d0_holdout["margin"][k]["brier"], 6)
            for k in final_holdout["margin"]
        }

    # ---- Tail calibration on holdout (D0 vs final) ----
    holdout_home_runs = [r["actualHomeRuns"] for r in holdout_rows]
    holdout_away_runs = [r["actualAwayRuns"] for r in holdout_rows]
    holdout_totals = [r["actualHomeRuns"] + r["actualAwayRuns"] for r in holdout_rows]
    holdout_margins = [abs(r["actualHomeRuns"] - r["actualAwayRuns"]) for r in holdout_rows]

    holdout_lambdas = [r.get("lambdaHome") for r in holdout_rows] + [r.get("lambdaAway") for r in holdout_rows]

    if final_candidate == D0:
        final_team_run_pmf_fns = None  # identical to D0 -- see below, no separate computation needed
    elif final_candidate == D1:
        final_team_run_pmf_fns = [
            (lambda k, lam=lam: negative_binomial_pmf(k, lam, dispersion))
            for lam in holdout_lambdas if lam is not None and lam > 0
        ]
    else:  # D2 -- bivariate Poisson's own MARGINALS are structurally identical to D0's Poisson marginals
        final_team_run_pmf_fns = None

    tail_calibration = {
        "teamRunTails": {
            label: {
                "empirical": empirical_tail_frequency(holdout_home_runs + holdout_away_runs, threshold, mode),
                "d0Implied": poisson_implied_tail_frequency(holdout_lambdas, threshold, mode),
                "finalImplied": (
                    poisson_implied_tail_frequency(holdout_lambdas, threshold, mode) if final_team_run_pmf_fns is None
                    else candidate_implied_tail_frequency(final_team_run_pmf_fns, threshold, mode)
                ),
            }
            for label, threshold, mode in TEAM_RUN_TAILS
        },
        "gameTotalTails": {f"over_{line}": empirical_tail_frequency(holdout_totals, line, "at_least") for line in GAME_TOTAL_TAILS},
        "marginTails": {f"margin_{m}_plus": empirical_tail_frequency(holdout_margins, m, "at_least") for m in MARGIN_TAILS},
        "note": (
            "finalImplied equals d0Implied for team-run tails whenever the final candidate is D0 or D2 -- "
            "D2's bivariate-Poisson construction has marginals STRUCTURALLY identical to D0's Poisson marginals "
            "(see lib.edgelab.backtest.run_distributions.bivariate_poisson_joint_pmf's own docstring), so only D1 "
            "(negative binomial) can ever change a team-run tail-frequency prediction versus D0."
        ),
    }

    # ---- Secondary Pinnacle stage (existing cache, no new acquisition) ----
    import run_proxy_vs_pinnacle_experiment as rsch0008
    from lib.edgelab.backtest.proxy_enrichment import stabilized_offense_rate, stabilized_bullpen_rate, bullpen_quality_baseline
    from lib.edgelab.bullpen_usage import MLB_TEAM_ID_MAP

    # Same gamePk -> home-side-schedule-entry index MLB-RSCH-0009's own
    # Pinnacle-stage enrichment uses, so this stage's mean model is the
    # SAME frozen {offense, bullpen} composition everywhere else in this
    # script -- never a silently weaker offense-only substitute.
    home_entry_by_pk_by_season = {
        season: {g["gamePk"]: g for games in team_games_by_season[season].values() for g in games if g.get("side") == "home"}
        for season in ALL_SEASONS
    }

    pinnacle_rows_by_season = {season: rsch0008.build_matched_rows(season) for season in ALL_SEASONS}
    pinnacle_all_rows = [r for season in ALL_SEASONS for r in pinnacle_rows_by_season[season]]
    for season, rows in pinnacle_rows_by_season.items():
        home_entry_by_pk = home_entry_by_pk_by_season[season]
        for r in rows:
            rsch0008.enrich_row(r, frozen_hfa)  # populates pinnacleMlHomeFair/pinnacleTotalLine/pinnacleTotalOverFair/actualHomeWin/actualOver
            home_offense_stabilized = stabilized_offense_rate(r["homeBaseline"]["offenseRunsPerGame"], r["homeBaseline"]["priorGamesThisSeason"], league_avg_offense)
            away_offense_stabilized = stabilized_offense_rate(r["awayBaseline"]["offenseRunsPerGame"], r["awayBaseline"]["priorGamesThisSeason"], league_avg_offense)

            home_entry = home_entry_by_pk.get(r["gamePk"])
            home_bullpen_stabilized = away_bullpen_stabilized = None
            if home_entry is not None:
                home_team_id = MLB_TEAM_ID_MAP.get(r["homeAbbr"])
                away_team_id = home_entry.get("opponentTeamId")
                home_bullpen_raw = bullpen_quality_baseline(relief_by_season[season].get(home_team_id, []), home_entry)
                away_bullpen_raw = bullpen_quality_baseline(relief_by_season[season].get(away_team_id, []), home_entry)
                if home_bullpen_raw:
                    home_bullpen_stabilized = stabilized_bullpen_rate(home_bullpen_raw["bullpenEarnedRunsPer9"], home_bullpen_raw["priorGamesWithBullpenData"], league_avg_bullpen_er9)
                if away_bullpen_raw:
                    away_bullpen_stabilized = stabilized_bullpen_rate(away_bullpen_raw["bullpenEarnedRunsPer9"], away_bullpen_raw["priorGamesWithBullpenData"], league_avg_bullpen_er9)

            hb = rsch0009.baseline_for_components(r["homeBaseline"], home_offense_stabilized, home_bullpen_stabilized, FROZEN_MEAN_COMPONENTS)
            ab = rsch0009.baseline_for_components(r["awayBaseline"], away_offense_stabilized, away_bullpen_stabilized, FROZEN_MEAN_COMPONENTS)
            eh, ea = expected_runs(hb, ab, home_field_adjustment=frozen_hfa)
            r["lambdaHome"], r["lambdaAway"] = eh, ea

            if eh is None or ea is None:
                r["pD0Ml"] = r["pD0TotalOver"] = r["pFinalMl"] = r["pFinalTotalOver"] = None
                continue
            d0_joint = joint_for_candidate(D0, eh, ea)
            d0_matrix = _joint_matrix(d0_joint)
            d0_pred = evaluate_row(d0_matrix)
            r["pD0Ml"] = d0_pred["pHomeWin"]
            r["pD0TotalOver"] = (
                sum(v for total, v in ((h + a, d0_matrix[h][a]) for h in range(MAX_RUNS + 1) for a in range(MAX_RUNS + 1)) if total > r["pinnacleTotalLine"])
                if r.get("pinnacleTotalLine") is not None else None
            )
            if final_candidate == D0:
                r["pFinalMl"], r["pFinalTotalOver"] = r["pD0Ml"], r["pD0TotalOver"]
            else:
                final_joint = joint_for_candidate(final_candidate, eh, ea, dispersion=dispersion, lambda_c=lambda_c)
                final_matrix = _joint_matrix(final_joint)
                final_pred = evaluate_row(final_matrix)
                r["pFinalMl"] = final_pred["pHomeWin"]
                r["pFinalTotalOver"] = (
                    sum(v for total, v in ((h + a, final_matrix[h][a]) for h in range(MAX_RUNS + 1) for a in range(MAX_RUNS + 1)) if total > r["pinnacleTotalLine"])
                    if r.get("pinnacleTotalLine") is not None else None
                )

    d0_ml_pinnacle = rsch0008.paired_analysis(pinnacle_all_rows, "pD0Ml", "pinnacleMlHomeFair", "actualHomeWin", "PINNACLE/ML/D0")
    final_ml_pinnacle = rsch0008.paired_analysis(pinnacle_all_rows, "pFinalMl", "pinnacleMlHomeFair", "actualHomeWin", "PINNACLE/ML/FINAL")
    d0_total_pinnacle = rsch0008.paired_analysis(pinnacle_all_rows, "pD0TotalOver", "pinnacleTotalOverFair", "actualOver", "PINNACLE/TOTAL/D0")
    final_total_pinnacle = rsch0008.paired_analysis(pinnacle_all_rows, "pFinalTotalOver", "pinnacleTotalOverFair", "actualOver", "PINNACLE/TOTAL/FINAL")

    def _gap(result):
        if result.get("proxyBrierScore") is None or result.get("pinnacleBrierScore") is None:
            return None
        return round(result["proxyBrierScore"] - result["pinnacleBrierScore"], 6)

    ml_gap_d0, ml_gap_final = _gap(d0_ml_pinnacle), _gap(final_ml_pinnacle)
    total_gap_d0, total_gap_final = _gap(d0_total_pinnacle), _gap(final_total_pinnacle)

    classification = "NO MEANINGFUL IMPROVEMENT"
    if final_candidate != D0 and holdout_delta is not None:
        if holdout_delta_ci and holdout_delta_ci["high"] is not None and holdout_delta_ci["high"] < 0:
            classification = "MODERATE DISTRIBUTION IMPROVEMENT" if abs(holdout_delta) >= 0.002 else "MINOR DISTRIBUTION IMPROVEMENT"
        elif holdout_delta < 0:
            classification = "MINOR DISTRIBUTION IMPROVEMENT"
        elif holdout_delta > 0:
            classification = "POISSON SUPERIOR"
    elif final_candidate == D0:
        classification = "POISSON SUPERIOR" if (d1_dev_delta or 0) > 0 and (d2_dev_delta or 0) > 0 else "NO MEANINGFUL IMPROVEMENT"

    report = {
        "experimentId": EXPERIMENT_ID,
        "evidenceLevel": experiment["evidenceLevel"],
        "generatedAt": REGISTRATION_TIMESTAMP,
        "frozenMeanModel": {
            "components": sorted(FROZEN_MEAN_COMPONENTS),
            "homeFieldAdjustment": frozen_hfa,
            "leagueAverageOffenseRunsPerGame": league_avg_offense,
            "leagueAverageBullpenEarnedRunsPer9": league_avg_bullpen_er9,
        },
        "diagnostics": diagnostics,
        "fittedParameters": {"overdispersion": dispersion, "correlationLambdaC": lambda_c},
        "coverage": {
            "development": {"n": len(dev_rows)}, "validation": {"n": len(val_rows)}, "holdout": {"n": len(holdout_rows)},
        },
        "candidates": {
            "D0": {"development": d0_dev, "validation": d0_val, "holdout": d0_holdout},
            "D1": {
                "development": d1_dev, "validation": d1_val,
                "devAggregateDelta": d1_dev_delta, "devImprovedCells": d1_dev_improved_cells, "valAggregateDelta": d1_val_delta,
                "passesSelectionRule": d1_passes,
            },
            "D2": {
                "development": d2_dev, "validation": d2_val,
                "devAggregateDelta": d2_dev_delta, "devImprovedCells": d2_dev_improved_cells, "valAggregateDelta": d2_val_delta,
                "passesSelectionRule": d2_passes,
            },
        },
        "finalCandidate": final_candidate,
        "holdout": {
            "d0": d0_holdout, "final": final_holdout,
            "aggregatePrimaryDelta": holdout_delta, "aggregatePrimaryDeltaCI95": holdout_delta_ci,
            "familyByFamilyDelta": family_deltas,
        },
        "tailCalibrationHoldout": tail_calibration,
        "pinnacle": {
            "gameMl": {"d0": d0_ml_pinnacle, "final": final_ml_pinnacle, "gapD0": ml_gap_d0, "gapFinal": ml_gap_final, "gapClosed": round(ml_gap_d0 - ml_gap_final, 6) if ml_gap_d0 is not None and ml_gap_final is not None else None},
            "gameTotal": {"d0": d0_total_pinnacle, "final": final_total_pinnacle, "gapD0": total_gap_d0, "gapFinal": total_gap_final, "gapClosed": round(total_gap_d0 - total_gap_final, 6) if total_gap_d0 is not None and total_gap_final is not None else None},
        },
        "classification": classification,
        "disposition": disp.RESEARCH_CANDIDATE,
        "productionBehaviorChanged": False,
    }
    print(json.dumps(report, indent=2, default=str))

    os.makedirs(os.path.join(EDGELAB_DIR, "analytics"), exist_ok=True)
    with open(os.path.join(EDGELAB_DIR, "analytics", "latest_mlb_rsch_0010_run_distribution.json"), "w") as f:
        json.dump(report, f, indent=2, default=str)
    return report


if __name__ == "__main__":
    main()
