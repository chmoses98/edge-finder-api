#!/usr/bin/env python3
"""
scripts/edgelab/run_early_season_offense_experiment.py
====================================================================
Research Lab experiment MLB-RSCH-0017: "Early-Season Offensive Talent".
RESEARCH ONLY. NO production changes.

Every prior offense study in this program (MLB-RSCH-0009/0012/0014/0015)
reused rsch0009.build_season_rows(), which applies
MIN_PRIOR_GAMES_FOR_BASELINE=20 UPSTREAM -- games 1-19 of every season
were structurally absent from every one of those corpora. This
milestone deliberately builds a SEPARATE, NEW, genuinely PIT-safe
research-only row construction starting from each team's own game 1,
to finally test: how should a team's offensive scoring ability be
estimated before current-season samples stabilize?

STRICT PIT CONSTRUCTION: every current-season statistic for a target
game still uses ONLY that team's own strictly-prior games this season
(team_baseline(), MLB-RSCH-0009, unchanged, called with
min_prior_games=0 so game 1 -- zero prior games -- is a legitimate,
explicitly-handled case rather than an excluded one). PREVIOUS-season
statistics use that team's own COMPLETE previous season (entirely in
the past relative to any target-season game, so inherently leakage-
free) -- unavailable and honestly reported as None for 2022 targets,
since no 2021 cache exists in this repository.

CANDIDATES (offense-side ONLY -- the opponent's own run-prevention
component uses the IDENTICAL E0-style, no-floor, league-average-
anchored construction for every candidate, isolating the offense-prior
lever completely; this is an explicit, disclosed simplification, NOT
MLB-RSCH-0009's own bullpen-blended run-prevention, since re-deriving a
no-floor bullpen-quality component is out of this milestone's scope):
  E0 (control): league-average-anchored current-season-only estimate,
      explicitly defined at every game count -- league average exactly
      at game 1 (zero prior games, never fabricated), the SAME frozen
      stabilized_offense_rate(k=30, unchanged) shrinkage formula
      thereafter (no 20-game floor gate -- the formula itself already
      handles small samples via shrinkage, which is exactly why no
      floor is structurally required here).
  E1 (= E3): previous-season-anchored shrinkage blend -- previous-
      season rate is the shrinkage CENTER (in place of league average)
      with a DEV-fit pseudo-game weight K_PRIOR (one global parameter,
      selected from a small preregistered grid, never a per-band
      value) that governs how fast current-season information takes
      over as it accumulates. This already embodies the "decay" concept
      E3 asked for -- run as ONE candidate, not duplicated.
  E2 (component prior): NOT RUN -- see module docstring's own
      justification below; marked NOT_RUN rather than improvising,
      per this milestone's own explicit instruction.

E2 justification: MLB-RSCH-0012 already found that a DEV-fit component-
batting regression significantly UNDERPERFORMS the simple season-
average baseline, even on the DEV data it was fit on -- for the SAME
underlying signal (BB/K/HR/XBH/OBP/SLG component rates) applied as a
PRIOR-SEASON aggregate instead of a season-to-date one, there is no
principled reason to expect a qualitatively different outcome, and
building a new prior-season-component regression pipeline (distinct
data slicing, a new DEV fit, its own eligibility handling) is
substantial new scope for a milestone explicitly told to avoid
repeating MLB-RSCH-0012's component-regression search. Marked NOT_RUN.
"""
import json
import math
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

from lib.edgelab.backtest.proxy_model import team_baseline, expected_runs, fit_home_field_adjustment, game_ml_proxy_probability, game_total_proxy_probability
from lib.edgelab.backtest.proxy_enrichment import OFFENSE_SHRINKAGE_K, stabilized_offense_rate
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

EXPERIMENT_ID = "MLB-RSCH-0017"
REGISTRATION_TIMESTAMP = "2026-08-28T14:30:00Z"

FROZEN_DISPERSION = 0.281513


def _verify_frozen_dispersion():
    path = os.path.join(_ROOT, "data", "edgelab", "analytics", "latest_mlb_rsch_0010_run_distribution.json")
    with open(path) as f:
        canonical = json.load(f)["fittedParameters"]["overdispersion"]
    if canonical != FROZEN_DISPERSION:
        raise ValueError(f"FROZEN_DISPERSION={FROZEN_DISPERSION} does not match the canonical MLB-RSCH-0010 artifact value {canonical}")


_verify_frozen_dispersion()

DEV_SEASONS = [2022, 2023, 2024]
VALIDATION_SEASONS = [2025]
HOLDOUT_SEASONS = [2026]
ALL_SEASONS = DEV_SEASONS + VALIDATION_SEASONS + HOLDOUT_SEASONS

EARLIEST_CACHED_SEASON = 2022  # no 2021 cache exists -- 2022 target games have NO previous-season prior available
MAX_PRIOR_GAMES_CORPUS = 50  # a game-1-of-season through game-51 corpus comfortably covers every requested band (up to 31-40) with buffer

E0 = "E0_league_average_anchored"
E1 = "E1_previous_season_anchored"  # also satisfies E3 (decay embodied in the shrinkage weight)

K_PRIOR_GRID = (5, 10, 15, 20, 30, 50, 80)  # preregistered, fixed before results -- a small number of global parameters, never a per-band search

# Preregistered season-progress bands, keyed by GAME NUMBER this season (priorGames + 1)
SEASON_BANDS = (
    ("games_1_5", 0, 4), ("games_6_10", 5, 9), ("games_11_15", 10, 14),
    ("games_16_20", 15, 19), ("games_21_30", 20, 29), ("games_31_40", 30, 39),
)
AGGREGATE_BANDS = (
    ("games_1_15", 0, 14), ("games_1_20", 0, 19), ("games_1_30", 0, 29), ("games_1_40", 0, 39),
)

GAME_TOTAL_LINES = (7.5, 8.5, 9.5, 10.5)
TEAM_TOTAL_LINES = (2.5, 3.5, 4.5, 5.5)
MARGIN_THRESHOLDS = (2, 3)

DEGRADATION_TOLERANCE = 0.05
PROBABILITY_DEGRADATION_TOLERANCE = 0.005
MIN_GAMES_CONFIDENT = 50


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
        name="mlb_rsch_0017_early_season_offense_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0017 early-season offense v1: a NEW no-floor PIT-safe row construction "
                        "(games 1-50 of each season) + {E0 league-average-anchored, E1 previous-season-anchored "
                        "shrinkage blend}, opponent run-prevention held identical (E0-style) across candidates"
        ),
        probability_adapter_identity="lib.edgelab.backtest.run_distributions (frozen MLB-RSCH-0010 negative-binomial, dispersion unchanged, never refit here)",
        model_engine_family="pit_safe_research_early_season_offense_v1",
        required_input_provenance=["team_recent_game_log_reconstruction"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "A NEW, no-floor, genuinely PIT-safe row construction (unlike every prior offense milestone in "
            "this program, which inherited MLB-RSCH-0009's 20-game eligibility floor) covering games 1-50 of "
            "every season. Tests whether previous-season team offense adds incremental predictive value beyond "
            "league-average shrinkage during the early season, before current-season samples stabilize."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Early-Season Offensive Talent",
        hypothesis=(
            "H1: league-average shrinkage alone (E0) is insufficient at game 1 -- some teams have genuinely "
            "different offensive talent than league average, and a team's own previous-season performance "
            "carries real incremental information about it, despite roster turnover. H2: a DEV-fit shrinkage "
            "blend of previous-season rate and accumulating current-season rate (E1) improves both mean "
            "accuracy and downstream frozen-NB probability quality over E0, most strongly in the earliest "
            "season-progress bands, with the advantage shrinking (but not necessarily disappearing) as "
            "current-season games accumulate. H3: component-batting regression (E2) is not expected to add "
            "value beyond a simple previous-season runs-scored average, consistent with MLB-RSCH-0012's own "
            "finding for the season-to-date case -- E2 is therefore not run."
        ),
        research_question=(
            "How should a team's true offensive scoring ability be estimated during games 1-50 of a season, "
            "before current-season samples stabilize -- does previous-season team offense (blended via a "
            "single DEV-fit shrinkage weight) improve on league-average shrinkage, and if so, when during the "
            "season does that advantage fade?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E2_PIT_HISTORICAL,
        target_population="Every MLB regular-season game where at least one team is within its own first 50 games of that season (2022-2026) -- a NEW, no-floor population, distinct from every prior offense milestone's own 20-game-floor corpus.",
        market_families=["game_result", "game_total", "team_total", "run_margin"],
        eligibility_criteria=["at least one side (home or away) is within MAX_PRIOR_GAMES_CORPUS=50 prior games this season -- no 20-game floor, unlike every prior offense milestone"],
        exclusion_criteria=[
            "any change to the run-prevention/opponent-side construction across candidates -- held IDENTICAL (E0-style) for every offense candidate, isolating the offense-prior lever only",
            "bullpen quality re-derivation -- out of scope, not attempted for a no-floor population",
            "E2 (component-batting prior) -- not run, see module docstring for the preregistered justification",
            "a per-band decay curve -- E1 uses ONE global DEV-fit shrinkage weight (from a small preregistered grid), never a per-band-optimized value",
            "roster-continuity information -- audited for feasibility only, never added to the candidate ladder without its own preregistration",
        ],
        prediction_checkpoints=["SEASON_TO_DATE_PREGAME"],
        primary_metric="paired MAE delta on next-game team runs scored (candidate minus E0), game-and-team-clustered 95% CI",
        secondary_metrics=[
            "RMSE delta", "mean bias delta", "frozen-NB team-total/game-total/moneyline/run-margin Brier deltas",
            "season-progress-band-specific MAE deltas (SEASON_BANDS, AGGREGATE_BANDS)",
            "per-team effect distribution / leave-one-team-out robustness",
            "paired candidate-minus-Pinnacle Brier delta (secondary stage, only if a candidate survives locked 2026)",
        ],
        chronological_split_policy=f"SEASON_BASED: development={DEV_SEASONS}, validation={VALIDATION_SEASONS}, holdout={HOLDOUT_SEASONS} (locked)",
        minimum_sample_requirement={"independentGames": MIN_GAMES_CONFIDENT},
        clustering_unit="gamePk",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={"team_recent_game_log_reconstruction": "PREDICTIVE_INPUT"},
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            "evidenceLevel E2_PIT_HISTORICAL. Frozen MLB-RSCH-0010 NB dispersion reused unchanged, never refit "
            "for early-season candidates (per this milestone's own explicit instruction). No 2021 cache exists "
            "-- 2022 target games have NO previous-season prior available for E1, honestly reported as None "
            "(never fabricated), not excluded from the corpus (E1 gracefully degrades to E0's own formula)."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Row construction (NEW, no-floor, games 1-50 of every season) ─────────

def load_previous_season_full_averages(season):
    """{teamId: {"offenseRunsPerGame", "runPreventionRunsAllowedPerGame"}}
    -- the team's COMPLETE previous season (entirely in the past, so
    inherently leakage-free). None (the whole dict is empty) if
    season-1 predates this repository's own cache (2022)."""
    prev_season = season - 1
    if prev_season < EARLIEST_CACHED_SEASON:
        return {}
    prev_team_games = rsch0009.load_all_team_games_with_venue(prev_season)
    out = {}
    for team_id, games in prev_team_games.items():
        scored = [g["runsScored"] for g in games if g.get("runsScored") is not None]
        allowed = [g["runsAllowed"] for g in games if g.get("runsAllowed") is not None]
        if not scored or not allowed:
            continue
        out[team_id] = {
            "offenseRunsPerGame": round(sum(scored) / len(scored), 4),
            "runPreventionRunsAllowedPerGame": round(sum(allowed) / len(allowed), 4),
            "gamesPlayed": len(games),
        }
    return out


def build_corpus():
    """Returns rows_by_season: {season: [row, ...]} -- one row per real
    game where AT LEAST one side is within MAX_PRIOR_GAMES_CORPUS of its
    own season. Each row carries BOTH sides' current-season raw baseline
    (team_baseline, min_prior_games=0 -- game 1 is a legitimate, explicit
    zero-prior-games case) and previous-season full-season average
    (None if unavailable, e.g. 2022 -- never fabricated)."""
    rows_by_season = {}
    team_games_by_season = {}
    for season in ALL_SEASONS:
        team_games = rsch0009.load_all_team_games_with_venue(season)
        team_games_by_season[season] = team_games
        prev_season_avgs = load_previous_season_full_averages(season)

        rows = []
        for home_team_id, home_games in team_games.items():
            for g in home_games:
                if g.get("side") != "home":
                    continue
                away_team_id = g.get("opponentTeamId")
                away_games = team_games.get(away_team_id, [])
                home_raw = team_baseline(home_games, g, min_prior_games=0)
                away_raw = team_baseline(away_games, g, min_prior_games=0)
                if home_raw["priorGamesThisSeason"] > MAX_PRIOR_GAMES_CORPUS and away_raw["priorGamesThisSeason"] > MAX_PRIOR_GAMES_CORPUS:
                    continue
                rows.append({
                    "season": season, "gamePk": g["gamePk"], "date": g["date"], "gameNumber": g.get("gameNumber"),
                    "homeTeamId": home_team_id, "awayTeamId": away_team_id,
                    "homeCurrentRaw": home_raw, "awayCurrentRaw": away_raw,
                    "homePreviousSeason": prev_season_avgs.get(home_team_id),
                    "awayPreviousSeason": prev_season_avgs.get(away_team_id),
                    "actualHomeRuns": g.get("runsScored"), "actualAwayRuns": g.get("runsAllowed"),
                })
        rows_by_season[season] = rows
    return rows_by_season, team_games_by_season


def fit_league_average(dev_rows_by_season):
    """DEV-only, closed-form: mean runsScored per team-game across the
    NEW early-season corpus's own DEV rows (not reused from rsch0009's
    own full-season fit -- this corpus's own population, matching this
    experiment's own eligibility, is the correct DEV basis)."""
    values = []
    for rows in dev_rows_by_season:
        for r in rows:
            if r["actualHomeRuns"] is not None:
                values.append(r["actualHomeRuns"])
            if r["actualAwayRuns"] is not None:
                values.append(r["actualAwayRuns"])
    return round(sum(values) / len(values), 4) if values else None


# ── Candidate formulas ──────────────────────────────────────────────────

def e0_component(raw_rate, prior_games, league_avg):
    """E0: league-average at game 1 (explicit, never fabricated), the
    SAME frozen stabilized_offense_rate(k=30, unchanged) shrinkage
    formula thereafter -- no 20-game floor gate; the shrinkage formula
    already handles small samples on its own."""
    if prior_games == 0 or raw_rate is None:
        return league_avg
    return stabilized_offense_rate(raw_rate, prior_games, league_avg, k=OFFENSE_SHRINKAGE_K)


def e1_component(raw_rate, prior_games, previous_season_rate, league_avg, k_prior):
    """E1: previous-season rate as the shrinkage CENTER (in place of
    league average), weighted by k_prior pseudo-games against real
    current-season games. Gracefully degrades to E0's own formula when
    no previous season is available (2022) -- never fabricated."""
    if previous_season_rate is None:
        return e0_component(raw_rate, prior_games, league_avg)
    if prior_games == 0 or raw_rate is None:
        return previous_season_rate
    return round((previous_season_rate * k_prior + raw_rate * prior_games) / (k_prior + prior_games), 4)


def run_prevention_component(raw_rate, prior_games, league_avg):
    """IDENTICAL E0-style construction for every candidate's opponent
    side -- isolates the offense-prior lever completely. An explicit,
    disclosed simplification (not MLB-RSCH-0009's own bullpen-blended
    run-prevention), out of scope to re-derive for a no-floor population."""
    return e0_component(raw_rate, prior_games, league_avg)


def attach_predictions(rows, key_prefix, offense_fn, home_field_adjustment, league_avg):
    for r in rows:
        hb_off = offense_fn(r["homeCurrentRaw"], "home")
        ab_off = offense_fn(r["awayCurrentRaw"], "away")
        hb_prevent = run_prevention_component(r["homeCurrentRaw"]["runPreventionRunsAllowedPerGame"], r["homeCurrentRaw"]["priorGamesThisSeason"], league_avg)
        ab_prevent = run_prevention_component(r["awayCurrentRaw"]["runPreventionRunsAllowedPerGame"], r["awayCurrentRaw"]["priorGamesThisSeason"], league_avg)
        hb = {"offenseRunsPerGame": hb_off, "runPreventionRunsAllowedPerGame": hb_prevent}
        ab = {"offenseRunsPerGame": ab_off, "runPreventionRunsAllowedPerGame": ab_prevent}
        eh, ea = expected_runs(hb, ab, home_field_adjustment=home_field_adjustment)
        r[f"homeExpectedRuns_{key_prefix}"] = eh
        r[f"awayExpectedRuns_{key_prefix}"] = ea


def _e0_offense_fn(league_avg):
    def fn(raw, side):
        return e0_component(raw["offenseRunsPerGame"], raw["priorGamesThisSeason"], league_avg)
    return fn


def attach_e1_predictions(rows, home_field_adjustment, league_avg, k_prior):
    """E1 needs each row's own previous-season lookup (per side), which
    the generic attach_predictions()/offense_fn signature (raw+side
    only) doesn't carry -- a small dedicated attach function, still
    reusing e1_component/run_prevention_component/expected_runs
    unchanged."""
    for r in rows:
        home_prev = r["homePreviousSeason"]["offenseRunsPerGame"] if r["homePreviousSeason"] else None
        away_prev = r["awayPreviousSeason"]["offenseRunsPerGame"] if r["awayPreviousSeason"] else None
        hb_off = e1_component(r["homeCurrentRaw"]["offenseRunsPerGame"], r["homeCurrentRaw"]["priorGamesThisSeason"], home_prev, league_avg, k_prior)
        ab_off = e1_component(r["awayCurrentRaw"]["offenseRunsPerGame"], r["awayCurrentRaw"]["priorGamesThisSeason"], away_prev, league_avg, k_prior)
        hb_prevent = run_prevention_component(r["homeCurrentRaw"]["runPreventionRunsAllowedPerGame"], r["homeCurrentRaw"]["priorGamesThisSeason"], league_avg)
        ab_prevent = run_prevention_component(r["awayCurrentRaw"]["runPreventionRunsAllowedPerGame"], r["awayCurrentRaw"]["priorGamesThisSeason"], league_avg)
        hb = {"offenseRunsPerGame": hb_off, "runPreventionRunsAllowedPerGame": hb_prevent}
        ab = {"offenseRunsPerGame": ab_off, "runPreventionRunsAllowedPerGame": ab_prevent}
        eh, ea = expected_runs(hb, ab, home_field_adjustment=home_field_adjustment)
        r["homeExpectedRuns_E1"] = eh
        r["awayExpectedRuns_E1"] = ea


def _hfa_fit_rows(rows, offense_fn, league_avg):
    out = []
    for r in rows:
        hb_off = offense_fn(r["homeCurrentRaw"], "home")
        ab_off = offense_fn(r["awayCurrentRaw"], "away")
        hb_prevent = run_prevention_component(r["homeCurrentRaw"]["runPreventionRunsAllowedPerGame"], r["homeCurrentRaw"]["priorGamesThisSeason"], league_avg)
        ab_prevent = run_prevention_component(r["awayCurrentRaw"]["runPreventionRunsAllowedPerGame"], r["awayCurrentRaw"]["priorGamesThisSeason"], league_avg)
        out.append({"homeBaseline": {"offenseRunsPerGame": hb_off, "runPreventionRunsAllowedPerGame": hb_prevent},
                    "awayBaseline": {"offenseRunsPerGame": ab_off, "runPreventionRunsAllowedPerGame": ab_prevent},
                    "actualHomeRuns": r["actualHomeRuns"], "actualAwayRuns": r["actualAwayRuns"]})
    return out


def fit_hfa_e0(dev_rows, league_avg):
    return fit_home_field_adjustment(_hfa_fit_rows(dev_rows, _e0_offense_fn(league_avg), league_avg))


# ── K_PRIOR fit (DEV only, small preregistered grid) ──────────────────────

def fit_k_prior_dev_only(dev_rows, league_avg, hfa_e0):
    """Grid search over K_PRIOR_GRID (fixed, preregistered before
    results), selecting the value minimizing DEV MAE. A deterministic,
    tiny-parameter procedure -- never a continuous per-band optimization."""
    best_k, best_mae = None, None
    diagnostics = []
    for k in K_PRIOR_GRID:
        errors = []
        for r in dev_rows:
            home_prev = r["homePreviousSeason"]["offenseRunsPerGame"] if r["homePreviousSeason"] else None
            away_prev = r["awayPreviousSeason"]["offenseRunsPerGame"] if r["awayPreviousSeason"] else None
            hb_off = e1_component(r["homeCurrentRaw"]["offenseRunsPerGame"], r["homeCurrentRaw"]["priorGamesThisSeason"], home_prev, league_avg, k)
            ab_off = e1_component(r["awayCurrentRaw"]["offenseRunsPerGame"], r["awayCurrentRaw"]["priorGamesThisSeason"], away_prev, league_avg, k)
            hb_prevent = run_prevention_component(r["homeCurrentRaw"]["runPreventionRunsAllowedPerGame"], r["homeCurrentRaw"]["priorGamesThisSeason"], league_avg)
            ab_prevent = run_prevention_component(r["awayCurrentRaw"]["runPreventionRunsAllowedPerGame"], r["awayCurrentRaw"]["priorGamesThisSeason"], league_avg)
            eh, ea = expected_runs({"offenseRunsPerGame": hb_off, "runPreventionRunsAllowedPerGame": hb_prevent},
                                    {"offenseRunsPerGame": ab_off, "runPreventionRunsAllowedPerGame": ab_prevent}, home_field_adjustment=hfa_e0)
            if eh is not None and r["actualHomeRuns"] is not None:
                errors.append(abs(eh - r["actualHomeRuns"]))
            if ea is not None and r["actualAwayRuns"] is not None:
                errors.append(abs(ea - r["actualAwayRuns"]))
        mae = round(sum(errors) / len(errors), 4) if errors else None
        diagnostics.append({"k": k, "devMae": mae})
        if mae is not None and (best_mae is None or mae < best_mae):
            best_k, best_mae = k, mae
    return best_k, diagnostics


# ── Observations, metrics, bands (mirrors MLB-RSCH-0012/0015/0016 patterns) ─

def team_observations(rows, key_prefix):
    obs = []
    for r in rows:
        eh, ea = r.get(f"homeExpectedRuns_{key_prefix}"), r.get(f"awayExpectedRuns_{key_prefix}")
        if eh is not None and r.get("actualHomeRuns") is not None:
            obs.append({"gamePk": r["gamePk"], "teamId": r["homeTeamId"], "season": r["season"],
                        "priorGames": r["homeCurrentRaw"]["priorGamesThisSeason"], "predicted": eh, "actual": r["actualHomeRuns"]})
        if ea is not None and r.get("actualAwayRuns") is not None:
            obs.append({"gamePk": r["gamePk"], "teamId": r["awayTeamId"], "season": r["season"],
                        "priorGames": r["awayCurrentRaw"]["priorGamesThisSeason"], "predicted": ea, "actual": r["actualAwayRuns"]})
    return obs


def mean_accuracy_metrics(obs):
    if not obs:
        return {"n": 0, "independentGames": 0, "mae": None, "rmse": None, "bias": None}
    errors = [o["predicted"] - o["actual"] for o in obs]
    n = len(errors)
    mae = round(sum(abs(e) for e in errors) / n, 4)
    rmse = round(math.sqrt(sum(e ** 2 for e in errors) / n), 4)
    bias = round(sum(errors) / n, 4)
    independent_games = independent_unit_count(obs, key="gamePk")
    return {"n": n, "independentGames": independent_games, "sampleSizeStatus": sample_size_status(n, independent_games=independent_games), "mae": mae, "rmse": rmse, "bias": bias}


def paired_mean_mae_delta(obs_a, obs_b):
    by_key_a = {(o["gamePk"], o["teamId"]): o for o in obs_a}
    by_key_b = {(o["gamePk"], o["teamId"]): o for o in obs_b}
    common = sorted(set(by_key_a) & set(by_key_b))
    paired_rows = [{"gamePk": k[0], "teamId": k[1], "errA": abs(by_key_a[k]["predicted"] - by_key_a[k]["actual"]), "errB": abs(by_key_b[k]["predicted"] - by_key_b[k]["actual"])} for k in common]

    def _delta(subset):
        return sum(r["errB"] - r["errA"] for r in subset) / len(subset) if subset else None

    point = _delta(paired_rows)
    lo, hi, _ = game_clustered_bootstrap_ci(paired_rows, _delta, cluster_key="gamePk", seed=DEFAULT_BOOTSTRAP_SEED)
    return {"n": len(paired_rows), "independentGames": independent_unit_count(paired_rows, key="gamePk"),
            "maeDelta": round(point, 6) if point is not None else None,
            "maeDeltaCI95": {"low": lo, "high": hi, "method": "GAME_CLUSTERED_BOOTSTRAP"},
            "interpretation": "negative == candidate B improved (lower MAE than A)"}


def season_progress_band_breakdown(obs_a, obs_b, bands):
    out = {}
    for band_name, lo_games, hi_games in bands:
        def _in_band(o):
            return lo_games <= o["priorGames"] <= hi_games
        out[band_name] = paired_mean_mae_delta([o for o in obs_a if _in_band(o)], [o for o in obs_b if _in_band(o)])
    return out


def team_robustness(obs_a, obs_b):
    by_key_a = {(o["gamePk"], o["teamId"]): o for o in obs_a}
    by_key_b = {(o["gamePk"], o["teamId"]): o for o in obs_b}
    common = sorted(set(by_key_a) & set(by_key_b))
    team_ids = sorted({k[1] for k in common})
    per_team = {}
    for team_id in team_ids:
        keys = [k for k in common if k[1] == team_id]
        errors_a = [abs(by_key_a[k]["predicted"] - by_key_a[k]["actual"]) for k in keys]
        errors_b = [abs(by_key_b[k]["predicted"] - by_key_b[k]["actual"]) for k in keys]
        if not errors_a:
            continue
        per_team[str(team_id)] = round(sum(errors_b) / len(errors_b) - sum(errors_a) / len(errors_a), 4)
    leave_one_out = {excluded: round(sum(v for tid, v in per_team.items() if tid != excluded) / max(len(per_team) - 1, 1), 4) for excluded in per_team}
    deltas = list(per_team.values())
    return {"perTeamMaeDelta": per_team, "leaveOneTeamOutDeltas": leave_one_out,
            "nTeamsPositive": sum(1 for d in deltas if d < 0), "nTeamsNegative": sum(1 for d in deltas if d > 0), "nTeamsTotal": len(deltas)}


# ── Frozen-NB probability evaluation (dispersion NEVER refit here) ───────

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
    for margin in MARGIN_THRESHOLDS:
        cells[f"run_margin_win_by_at_least_{margin}"] = margin_at_least_prob(joint, margin)
        cells[f"run_margin_lose_by_at_least_{margin}"] = margin_at_least_prob(lambda h, a, _j=joint: _j(a, h), margin)
    return cells


def _outcomes_for_actual(actual_home, actual_away):
    actual_total = actual_home + actual_away
    margin = actual_home - actual_away
    outcomes = {"moneyline_home_win": 1 if actual_home > actual_away else 0, "moneyline_away_win": 1 if actual_away > actual_home else 0}
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
    deltas = [nb_result["byFamily"][fam]["pairedDelta"]["brierScore"] for fam in ("game_total", "team_total_away", "team_total_home", "moneyline") if nb_result["byFamily"].get(fam) and nb_result["byFamily"][fam]["pairedDelta"]["brierScore"] is not None]
    return round(sum(deltas) / len(deltas), 6) if deltas else None


# ── Selection rule (preregistered) ─────────────────────────────────────────

def selection_passes(dev_mae_delta, dev_nb_primary_delta, val_mae_delta, band_deltas, team_robustness_result):
    reasons = []
    if dev_mae_delta is None or dev_mae_delta >= 0:
        reasons.append(f"DEV MAE delta not negative (improved): {dev_mae_delta}")
    if dev_nb_primary_delta is None or dev_nb_primary_delta > 0:
        reasons.append(f"DEV frozen-NB primary Brier delta not improved/preserved: {dev_nb_primary_delta}")
    if val_mae_delta is not None and val_mae_delta > DEGRADATION_TOLERANCE:
        reasons.append(f"VALIDATION MAE delta degraded beyond tolerance {DEGRADATION_TOLERANCE}: {val_mae_delta}")
    if val_mae_delta is not None and val_mae_delta >= 0:
        reasons.append(f"VALIDATION does not replicate DEV direction: {val_mae_delta}")
    if team_robustness_result["nTeamsTotal"] > 0 and team_robustness_result["nTeamsPositive"] < team_robustness_result["nTeamsTotal"] * 0.4:
        reasons.append(f"improvement concentrated in too few teams: {team_robustness_result['nTeamsPositive']}/{team_robustness_result['nTeamsTotal']} improved")
    return (len(reasons) == 0), reasons


def main():
    print(f"[{EXPERIMENT_ID}] registering experiment/control...")
    control, definition = register_experiment()

    print(f"[{EXPERIMENT_ID}] building NEW no-floor early-season corpus (games 1-{MAX_PRIOR_GAMES_CORPUS} of every season)...")
    rows_by_season, team_games_by_season = build_corpus()
    dev_rows = [r for s in DEV_SEASONS for r in rows_by_season[s]]
    val_rows = [r for s in VALIDATION_SEASONS for r in rows_by_season[s]]
    holdout_rows = [r for s in HOLDOUT_SEASONS for r in rows_by_season[s]]
    all_rows = dev_rows + val_rows + holdout_rows
    print(f"[{EXPERIMENT_ID}] rows: dev={len(dev_rows)} val={len(val_rows)} holdout={len(holdout_rows)} total={len(all_rows)}")

    league_avg = fit_league_average([dev_rows])
    print(f"[{EXPERIMENT_ID}] DEV-fit league average (this corpus's own population): {league_avg}")

    hfa_e0 = fit_hfa_e0(dev_rows, league_avg)
    attach_predictions(all_rows, "E0", _e0_offense_fn(league_avg), hfa_e0, league_avg)
    print(f"[{EXPERIMENT_ID}] E0 home-field adjustment: {hfa_e0}")

    print(f"[{EXPERIMENT_ID}] fitting K_PRIOR (E1) on DEVELOPMENT only, grid={K_PRIOR_GRID}...")
    k_prior, k_diagnostics = fit_k_prior_dev_only(dev_rows, league_avg, hfa_e0)
    print(f"[{EXPERIMENT_ID}] K_PRIOR={k_prior} diagnostics={k_diagnostics}")

    attach_e1_predictions(all_rows, hfa_e0, league_avg, k_prior)

    obs_dev_e0, obs_dev_e1 = team_observations(dev_rows, "E0"), team_observations(dev_rows, "E1")
    obs_val_e0, obs_val_e1 = team_observations(val_rows, "E0"), team_observations(val_rows, "E1")

    dev_delta = paired_mean_mae_delta(obs_dev_e0, obs_dev_e1)
    val_delta = paired_mean_mae_delta(obs_val_e0, obs_val_e1)
    print(f"[{EXPERIMENT_ID}] E1 vs E0 MAE delta: dev={dev_delta['maeDelta']} val={val_delta['maeDelta']}")

    dev_bands = season_progress_band_breakdown(obs_dev_e0, obs_dev_e1, SEASON_BANDS)
    dev_agg_bands = season_progress_band_breakdown(obs_dev_e0, obs_dev_e1, AGGREGATE_BANDS)
    dev_team_robustness = team_robustness(obs_dev_e0, obs_dev_e1)
    print(f"[{EXPERIMENT_ID}] DEV season bands: { {k: v['maeDelta'] for k, v in dev_bands.items()} }")
    print(f"[{EXPERIMENT_ID}] DEV team robustness: {dev_team_robustness['nTeamsPositive']}/{dev_team_robustness['nTeamsTotal']} improved")

    dev_nb = frozen_nb_probability_eval(dev_rows, "E0", "E1")
    dev_nb_primary = _primary_nb_delta(dev_nb)
    print(f"[{EXPERIMENT_ID}] DEV frozen-NB primary delta: {dev_nb_primary}")

    passes, reasons = selection_passes(dev_delta["maeDelta"], dev_nb_primary, val_delta["maeDelta"], dev_bands, dev_team_robustness)
    print(f"[{EXPERIMENT_ID}] selection: passes={passes} reasons={reasons}")

    val_nb, val_bands, val_agg_bands = None, None, None
    if passes:
        val_nb = frozen_nb_probability_eval(val_rows, "E0", "E1")
        val_bands = season_progress_band_breakdown(obs_val_e0, obs_val_e1, SEASON_BANDS)
        val_agg_bands = season_progress_band_breakdown(obs_val_e0, obs_val_e1, AGGREGATE_BANDS)

    holdout_result = None
    if passes:
        print(f"[{EXPERIMENT_ID}] preregistered gate passed -- unlocking 2026 holdout...")
        obs_holdout_e0, obs_holdout_e1 = team_observations(holdout_rows, "E0"), team_observations(holdout_rows, "E1")
        holdout_delta = paired_mean_mae_delta(obs_holdout_e0, obs_holdout_e1)
        holdout_bands = season_progress_band_breakdown(obs_holdout_e0, obs_holdout_e1, SEASON_BANDS)
        holdout_nb = frozen_nb_probability_eval(holdout_rows, "E0", "E1")
        holdout_result = {
            "meanAccuracy": {"E0": mean_accuracy_metrics(obs_holdout_e0), "E1": mean_accuracy_metrics(obs_holdout_e1), "pairedDelta": holdout_delta},
            "seasonBands": holdout_bands, "frozenNbProbability": holdout_nb,
            "teamRobustness": team_robustness(obs_holdout_e0, obs_holdout_e1),
        }
        print(f"[{EXPERIMENT_ID}] E1 2026 holdout MAE delta={holdout_delta['maeDelta']}")
    else:
        print(f"[{EXPERIMENT_ID}] holdout NOT unlocked -- E1 retired per preregistration (no re-tuning).")

    pinnacle_result = {"note": "not run -- only performed if a candidate survives locked 2026, per preregistration"}
    if holdout_result is not None:
        print(f"[{EXPERIMENT_ID}] Pinnacle secondary stage (existing sample, no new Odds API spend)...")
        import run_proxy_vs_pinnacle_experiment as rsch0008
        try:
            pinnacle_all_rows = [r for season in ALL_SEASONS for r in rsch0008.build_matched_rows(season)]
            for r in pinnacle_all_rows:
                rsch0008.enrich_row(r, hfa_e0)

            # E1-specific proxy probabilities for the SAME matched rows -- required
            # now that E1 survived to unlock 2026 ("evaluate whether the candidate
            # narrows the Pinnacle gap"). Reuses e1_component/run_prevention_component/
            # expected_runs unchanged; never fit to Pinnacle (previous-season data and
            # k_prior are both already frozen from the DEV-only fit above).
            prev_avgs_by_season = {season: load_previous_season_full_averages(season) for season in ALL_SEASONS}
            for r in pinnacle_all_rows:
                home_team_id = rsch0008.MLB_TEAM_ID_MAP.get(r["homeAbbr"])
                away_team_id = rsch0008.MLB_TEAM_ID_MAP.get(r["awayAbbr"])
                prev_avgs = prev_avgs_by_season.get(r["season"], {})
                home_prev = prev_avgs.get(home_team_id)
                away_prev = prev_avgs.get(away_team_id)
                home_b, away_b = r["homeBaseline"], r["awayBaseline"]
                hb_off = e1_component(home_b["offenseRunsPerGame"], home_b["priorGamesThisSeason"], home_prev["offenseRunsPerGame"] if home_prev else None, league_avg, k_prior)
                ab_off = e1_component(away_b["offenseRunsPerGame"], away_b["priorGamesThisSeason"], away_prev["offenseRunsPerGame"] if away_prev else None, league_avg, k_prior)
                hb_prevent = run_prevention_component(home_b["runPreventionRunsAllowedPerGame"], home_b["priorGamesThisSeason"], league_avg)
                ab_prevent = run_prevention_component(away_b["runPreventionRunsAllowedPerGame"], away_b["priorGamesThisSeason"], league_avg)
                eh1, ea1 = expected_runs({"offenseRunsPerGame": hb_off, "runPreventionRunsAllowedPerGame": hb_prevent},
                                          {"offenseRunsPerGame": ab_off, "runPreventionRunsAllowedPerGame": ab_prevent},
                                          home_field_adjustment=hfa_e0)
                r["expectedHomeRuns_E1"], r["expectedAwayRuns_E1"] = eh1, ea1
                proxy_ml_home_e1, _ = game_ml_proxy_probability(eh1, ea1)
                r["proxyMlHomeProb_E1"] = proxy_ml_home_e1
                r["proxyTotalOverProb_E1"] = game_total_proxy_probability(eh1, ea1, r["pinnacleTotalLine"]) if r.get("pinnacleTotalLine") is not None else None

            pinnacle_ml_e0 = rsch0008.paired_analysis(pinnacle_all_rows, "proxyMlHomeProb", "pinnacleMlHomeFair", "actualHomeWin", "PINNACLE/ML/E0")
            pinnacle_total_e0 = rsch0008.paired_analysis(pinnacle_all_rows, "proxyTotalOverProb", "pinnacleTotalOverFair", "actualOver", "PINNACLE/TOTAL/E0")
            pinnacle_ml_e1 = rsch0008.paired_analysis(pinnacle_all_rows, "proxyMlHomeProb_E1", "pinnacleMlHomeFair", "actualHomeWin", "PINNACLE/ML/E1")
            pinnacle_total_e1 = rsch0008.paired_analysis(pinnacle_all_rows, "proxyTotalOverProb_E1", "pinnacleTotalOverFair", "actualOver", "PINNACLE/TOTAL/E1")
            pinnacle_result = {
                "nRows": len(pinnacle_all_rows),
                "ml": pinnacle_ml_e0, "total": pinnacle_total_e0,
                "mlE1": pinnacle_ml_e1, "totalE1": pinnacle_total_e1,
            }
        except Exception as exc:
            pinnacle_result = {"error": str(exc)}

    # ---- Player-based preseason-prior feasibility audit (documented, not built) ----
    feasibility = {
        "historicalRosterContinuity": "UNAVAILABLE -- no roster/transaction archive found in this repository at any scale",
        "playerPriorYearTalent": "RECONSTRUCTABLE_FROM_DATED_RAW -- data/statcast_raw/ exists but is shallow (~220 game files, matching MLB-RSCH-0011's own prior ~15-gameDate finding for the same underlying archive), not yet at multi-season scale",
        "lineupRole": "PROSPECTIVE_ONLY -- lib.edgelab.prospective_snapshot's own LINEUP_CONFIRMATION checkpoint is genuinely PIT-safe going forward, but has zero historical depth before this system's own recent deployment (per MLB-RSCH-0012/0013's own prior feasibility audit)",
        "transactions": "UNAVAILABLE -- no transaction archive found",
        "verdict": "A player-level preseason offensive prior is NOT feasible at useful historical scale today -- every required input is either UNAVAILABLE or RECONSTRUCTABLE_FROM_DATED_RAW-but-currently-shallow. A future milestone could reconstruct one once the Statcast raw archive grows past its current ~220-game depth.",
        "recommendedFutureDesign": "Once data/statcast_raw/ reaches multi-season depth, a player-level early-season prior (aggregating individual returning players' own prior-year Statcast performance, weighted by expected playing time) could directly test whether roster-turnover-aware talent estimation beats this milestone's own TEAM-level previous-season prior -- not attempted here.",
    }

    report = {
        "experimentId": EXPERIMENT_ID,
        "controlModelId": control["controlModelId"],
        "corpus": {"devRows": len(dev_rows), "valRows": len(val_rows), "holdoutRows": len(holdout_rows), "totalRows": len(all_rows)},
        "leagueAverage": league_avg,
        "homeFieldAdjustment": hfa_e0,
        "kPrior": {"selected": k_prior, "grid": list(K_PRIOR_GRID), "diagnostics": k_diagnostics},
        "notRun": {"E2": "component-batting prior -- see module docstring for justification"},
        "meanAccuracy": {
            "dev": {"E0": mean_accuracy_metrics(obs_dev_e0), "E1": mean_accuracy_metrics(obs_dev_e1), "pairedDelta": dev_delta},
            "validation": {"E0": mean_accuracy_metrics(obs_val_e0), "E1": mean_accuracy_metrics(obs_val_e1), "pairedDelta": val_delta},
        },
        "seasonBandsDev": dev_bands, "aggregateBandsDev": dev_agg_bands,
        "seasonBandsVal": val_bands, "aggregateBandsVal": val_agg_bands,
        "teamRobustnessDev": dev_team_robustness,
        "frozenNbProbability": {"dev": dev_nb, "devPrimaryDelta": dev_nb_primary, "validation": val_nb},
        "selection": {"passes": passes, "reasons": reasons},
        "holdout2026": holdout_result,
        "pinnacleSecondary": pinnacle_result,
        "playerPriorFeasibility": feasibility,
    }

    out_path = os.path.join("data", "edgelab", "analytics", "latest_mlb_rsch_0017_early_season_offense.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    print(f"[{EXPERIMENT_ID}] wrote {out_path}")
    return report


if __name__ == "__main__":
    main()
