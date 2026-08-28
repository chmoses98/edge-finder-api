#!/usr/bin/env python3
"""
scripts/edgelab/run_opponent_strength_experiment.py
====================================================================
Research Lab experiment MLB-RSCH-0015: "PIT-Safe Opponent-Strength /
Schedule Adjustment". RESEARCH ONLY. NO production changes.

MLB-RSCH-0012's own O4 candidate was marked NOT_EVALUABLE_IN_THIS_EXPERIMENT
because a genuine, historical, PIT-safe opponent-quality snapshot for
every prior game did not yet exist. This milestone builds that
capability and tests it directly: does a team's offense/run-prevention
rate, adjusted for the QUALITY OF OPPONENTS FACED (using only
information available before each of those prior games), predict
future scoring better than the unadjusted MLB-RSCH-0009 baseline?

STRICT PIT CONSTRUCTION -- the recursive snapshot problem:
For target game G (team T vs opponent OPP), the adjustment averages,
over T's own STRICTLY PRIOR games, each prior opponent O_i's OWN raw
rate AS OF THAT SPECIFIC MEETING DATE (never O_i's season-final or
target-game-adjacent rate) -- computed via team_baseline() (MLB-RSCH-0009,
UNCHANGED) called with min_prior_games=0 for every game of every team,
which itself uses prior_games_this_season()/is_strictly_before()
(reused unchanged) to guarantee O_i's snapshot never sees information
from on-or-after that specific meeting. This gives an O(1)-lookup table
(build_raw_baseline_lookup) instead of re-scanning each opponent's full
schedule per query -- a team-season's own snapshot sequence is computed
ONCE, then indexed by (gamePk, side) for every later query.

CANDIDATES:
  S0 (control): MLB-RSCH-0009's frozen mean, unchanged (same C0
      composition/HFA-fit convention as MLB-RSCH-0014's own C0). No
      opponent adjustment.
  S1: ONE-LEVEL schedule adjustment -- each team's raw offense/run-
      prevention rate is adjusted by (league average) minus (the
      average RAW quality of the opponents it has actually faced, each
      opponent's OWN raw rate as of that specific meeting). Fed through
      the SAME frozen shrinkage (stabilized_offense_rate, k=30,
      unchanged) and SAME frozen bullpen blend as S0 -- isolating the
      schedule-adjustment lever from the (already-tested-and-rejected)
      shrinkage-constant lever of MLB-RSCH-0012/0013.
  S2: TWO-LEVEL (bounded) schedule adjustment -- IDENTICAL formula to
      S1, but each opponent's own "quality" input is S1's own adjusted
      rate (itself computed from THAT opponent's own prior opponents'
      RAW rates), not the raw rate. This is a genuine, deterministic,
      bounded ("iterate exactly twice", never a fixed-point solve)
      approximation to full iterative strength-of-schedule rating,
      chosen specifically because a true per-game fixed-point solve
      over each game's own prior-game subgraph would be both far more
      expensive and add exactly the kind of many-tuning-knob complexity
      this milestone was told to avoid ("do NOT create a giant
      Elo-like system").
  S3 (component-batting schedule adjustment): NOT RUN -- see module
      docstring section below. Explicitly skipped as an
      excessive-researcher-freedom expansion under this milestone's own
      time budget, per its own preregistered instruction ("if S3
      substantially expands researcher freedom: do not run it").

ELIGIBILITY (preregistered BEFORE results, per instruction not to
automatically inherit MLB-RSCH-0012's floor):
  - The MAIN corpus (used for the primary DEV/VAL/HOLDOUT paired
    comparison, selection, and robustness) uses the SAME
    MIN_PRIOR_GAMES_FOR_BASELINE=20 floor S0/production already use --
    this keeps the primary comparison exactly apples-to-apples (S0 has
    no prediction at all below 20 prior games, so a lower floor there
    would make pairing impossible, not more informative).
  - OPPONENT snapshots use a MUCH lower floor, MIN_PRIOR_GAMES_OPPONENT=5
    -- an opponent with fewer prior games than that contributes no
    value to the average (excluded, never fabricated) rather than
    being held to the same 20-game bar as the target row itself.
  - A SEPARATE, clearly-labeled, DIAGNOSTIC-ONLY population
    (MIN_PRIOR_GAMES_EARLY_DIAGNOSTIC=5) reports S1's own accuracy for
    early-season rows S0 structurally cannot predict at all -- this is
    NEVER paired against S0 (nothing to pair against) and NEVER used
    for selection; it is reported honestly as its own standalone
    diagnostic, exactly satisfying "if early-season rows become
    eligible, explicitly report them" without contaminating the
    primary apples-to-apples comparison.

NO MARKET INPUT: opponent strength is constructed ENTIRELY from prior
team-game run totals -- Pinnacle/Kalshi/sportsbook data is never used
to build S1/S2, only as the secondary check after freezing.
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

from lib.edgelab.backtest.proxy_model import team_baseline, expected_runs, game_ml_proxy_probability, game_total_proxy_probability
from lib.edgelab.backtest.proxy_enrichment import (
    OFFENSE_SHRINKAGE_K,
    stabilized_offense_rate,
    stabilized_bullpen_rate,
    blend_run_prevention_with_bullpen_quality,
)
from lib.edgelab.backtest.bullpen_backtest_reconstruction import is_strictly_before
from lib.edgelab.backtest.team_offense_recency_reconstruction import prior_games_this_season
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

EXPERIMENT_ID = "MLB-RSCH-0015"
REGISTRATION_TIMESTAMP = "2026-08-28T13:00:00Z"

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

S0_COMPONENTS = frozenset({"offense", "bullpen"})  # MLB-RSCH-0009's own frozen, final accepted composition

S0 = "S0_control_frozen_mean"
S1 = "S1_schedule_adjusted_1hop"
S2 = "S2_schedule_adjusted_2hop"

MIN_PRIOR_GAMES_MAIN = 20  # matches S0/production exactly -- the PRIMARY corpus's own eligibility gate
MIN_PRIOR_GAMES_OPPONENT = 5  # an opponent snapshot needs at least this many of ITS OWN prior games to count
MIN_PRIOR_GAMES_EARLY_DIAGNOSTIC = 5  # diagnostic-only population, never paired against S0, never used for selection

SEASON_BANDS = (
    ("games_1_15", 1, 15),
    ("games_16_40", 16, 40),
    ("games_41_80", 41, 80),
    ("games_81_plus", 81, None),
)

GAME_TOTAL_LINES = (7.5, 8.5, 9.5, 10.5)
TEAM_TOTAL_LINES = (2.5, 3.5, 4.5, 5.5)
MARGIN_THRESHOLDS = (2, 3)

DEGRADATION_TOLERANCE = 0.05
PROBABILITY_DEGRADATION_TOLERANCE = 0.005
MIN_GAMES_CONFIDENT = 50


# ── Registration (idempotent across re-runs) ──────────────────────────────

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
        name="mlb_rsch_0015_opponent_strength_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0015 opponent strength v1: MLB-RSCH-0009 frozen mean composition + PIT-safe "
                        "schedule-adjustment layer from {S0 none, S1 one-hop, S2 two-hop}"
        ),
        probability_adapter_identity="lib.edgelab.backtest.run_distributions (frozen MLB-RSCH-0010 negative-binomial, dispersion unchanged)",
        model_engine_family="pit_safe_research_opponent_strength_v1",
        required_input_provenance=["team_recent_game_log_reconstruction"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "Tests whether a team's raw offense/run-prevention rate, adjusted for the PIT-safe quality of "
            "opponents actually faced (each opponent's own raw rate as of that specific meeting date, never "
            "season-final), improves prediction of future scoring beyond MLB-RSCH-0009's frozen unadjusted "
            "baseline. S1 is one-hop (opponents' raw quality); S2 is a bounded two-hop extension (opponents' "
            "own S1-adjusted quality) -- deterministic, never a fixed-point iterative solve."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="PIT-Safe Opponent-Strength / Schedule Adjustment",
        hypothesis=(
            "H1: a team's raw season-to-date offense/run-prevention rate is confounded by the quality of "
            "opponents actually faced -- two teams with identical raw rates against schedules of different "
            "difficulty have different TRUE talent. H2: a PIT-safe adjustment for opponent quality (each "
            "opponent's own raw rate strictly as of the meeting date, never leaking future information about "
            "that opponent) improves next-game mean-run prediction and downstream frozen-NB probability "
            "quality beyond MLB-RSCH-0009's unadjusted baseline. H3: a bounded two-hop adjustment (S2) may "
            "capture additional signal beyond one-hop (S1) without requiring a full iterative fixed-point "
            "solve. H4: any real effect is not confined to one team or one narrow season band, and is at "
            "least as large, if not larger, early in the season when schedule imbalance is greatest."
        ),
        research_question=(
            "Holding MLB-RSCH-0009's frozen mean construction and MLB-RSCH-0010's frozen NB dispersion fixed, "
            "does a PIT-safe opponent-strength (schedule) adjustment to the raw offense/run-prevention rate "
            "improve mean accuracy and downstream probability quality, and does a bounded two-hop version add "
            "further value beyond one-hop?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E2_PIT_HISTORICAL,
        target_population=(
            "The same MLB regular-season 2022-2026 games MLB-RSCH-0009's own baseline used (both teams "
            ">= MIN_PRIOR_GAMES_MAIN=20 prior completed games this season) for the PRIMARY comparison; a "
            "separate, lower-floor (MIN_PRIOR_GAMES_EARLY_DIAGNOSTIC=5) population is reported as an early-"
            "season DIAGNOSTIC ONLY, never used for selection."
        ),
        market_families=["game_result", "game_total", "team_total", "run_margin"],
        eligibility_criteria=[
            "both teams have >= MIN_PRIOR_GAMES_MAIN=20 prior completed games this season for the PRIMARY corpus (matches S0/production exactly)",
            "an opponent snapshot requires >= MIN_PRIOR_GAMES_OPPONENT=5 of that opponent's own prior games -- otherwise excluded from the average, never fabricated",
        ],
        exclusion_criteria=[
            "any change to the offense/bullpen shrinkage constant itself -- MLB-RSCH-0012/0013 already tested and rejected refitting those; this experiment isolates the schedule-adjustment layer only",
            "any change to the bullpen component -- frozen, unchanged, applied identically to S0/S1/S2",
            "Pinnacle/Kalshi/sportsbook data as an input to opponent-strength construction -- secondary check only, strictly after freezing",
            "a full iterative fixed-point rating solve (S2 is a deterministic, bounded two-hop approximation, never a many-knob Elo-like system)",
            "S3 (component-batting schedule adjustment) -- not run, an excessive researcher-freedom expansion under this milestone's time budget",
        ],
        prediction_checkpoints=["SEASON_TO_DATE_PREGAME"],
        primary_metric="paired MAE delta on next-game team runs scored (candidate minus S0), game-and-team-clustered 95% CI",
        secondary_metrics=[
            "RMSE delta", "mean bias delta", "frozen-NB team-total/game-total/moneyline/run-margin Brier deltas",
            "season-band-specific MAE deltas (SEASON_BANDS)", "early-season diagnostic (MIN_PRIOR_GAMES_EARLY_DIAGNOSTIC)",
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
            "evidenceLevel E2_PIT_HISTORICAL: same basis as MLB-RSCH-0008/0009/0010/0012/0013/0014. Only the "
            "frozen SELECTED candidate (which may be S0 itself) is ever evaluated on the 2026 locked holdout."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Corpus + raw opponent-snapshot precomputation ─────────────────────────

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

    return rows_by_season, team_games_by_season, relief_by_season, league_avg_offense, league_avg_bullpen_er9


def build_raw_baseline_lookup(team_games_by_season):
    """{season: {gamePk: {"home": raw_dict, "away": raw_dict}}} -- team_baseline()
    (MLB-RSCH-0009, unchanged) called with min_prior_games=0 for EVERY game of
    EVERY team, giving that team's own raw rate AS OF that EXACT game. Built
    ONCE, indexed by (gamePk, side) -- an O(1) lookup for every later query,
    rather than re-scanning an opponent's whole schedule per query."""
    out = {}
    for season, teams in team_games_by_season.items():
        season_out = {}
        for team_id, games in teams.items():
            for g in games:
                raw = team_baseline(games, g, min_prior_games=0)
                season_out.setdefault(g["gamePk"], {})[g["side"]] = raw
        out[season] = season_out
    return out


def compute_schedule_adjustment(team_games_by_season, opponent_quality_lookup, league_avg_offense, league_avg_run_prevention, min_prior_games_opponent=MIN_PRIOR_GAMES_OPPONENT):
    """
    ONE deterministic adjustment pass. `opponent_quality_lookup`: shaped
    EXACTLY like build_raw_baseline_lookup's own output ({season: {gamePk:
    {side: {"offenseRunsPerGame", "runPreventionRunsAllowedPerGame",
    "priorGamesThisSeason"}}}}) -- callers pass the RAW lookup for S1, or
    S1's OWN output (same shape) for S2's bounded two-hop extension.

    For team T's target game G: averages, over T's OWN strictly-prior
    games (prior_games_this_season, reused unchanged), each prior
    opponent's OWN quality-lookup value AS OF that specific meeting
    (never T's target-game opponent, never a season-final rating) --
    excluded (never fabricated) if that opponent had fewer than
    min_prior_games_opponent of its own prior games at that meeting.
    Returns the SAME shape as the input lookup, directly composable as
    the next level's own `opponent_quality_lookup`.
    """
    out = {}
    for season, teams in team_games_by_season.items():
        season_lookup = opponent_quality_lookup[season]
        season_out = {}
        for team_id, games in teams.items():
            for g in games:
                prior = prior_games_this_season(games, g)
                opp_off_vals, opp_prevent_vals = [], []
                for pg in prior:
                    pg_lookup = season_lookup.get(pg["gamePk"])
                    if not pg_lookup:
                        continue
                    opp_side = "away" if pg.get("side") == "home" else "home"
                    opp_raw = pg_lookup.get(opp_side)
                    if not opp_raw or (opp_raw.get("priorGamesThisSeason") or 0) < min_prior_games_opponent:
                        continue
                    if opp_raw.get("offenseRunsPerGame") is not None:
                        opp_off_vals.append(opp_raw["offenseRunsPerGame"])
                    if opp_raw.get("runPreventionRunsAllowedPerGame") is not None:
                        opp_prevent_vals.append(opp_raw["runPreventionRunsAllowedPerGame"])

                own_raw = team_baseline(games, g, min_prior_games=0)
                own_off, own_prevent = own_raw["offenseRunsPerGame"], own_raw["runPreventionRunsAllowedPerGame"]

                avg_opp_prevent = sum(opp_prevent_vals) / len(opp_prevent_vals) if opp_prevent_vals else None
                avg_opp_off = sum(opp_off_vals) / len(opp_off_vals) if opp_off_vals else None

                offense_adjusted = round(own_off + (league_avg_run_prevention - avg_opp_prevent), 4) if own_off is not None and avg_opp_prevent is not None else own_off
                prevention_adjusted = round(own_prevent + (league_avg_offense - avg_opp_off), 4) if own_prevent is not None and avg_opp_off is not None else own_prevent

                season_out.setdefault(g["gamePk"], {})[g["side"]] = {
                    "offenseRunsPerGame": offense_adjusted,
                    "runPreventionRunsAllowedPerGame": prevention_adjusted,
                    "priorGamesThisSeason": own_raw["priorGamesThisSeason"],
                    "nOpponentsOffense": len(opp_off_vals),
                    "nOpponentsPrevention": len(opp_prevent_vals),
                }
        out[season] = season_out
    return out


# ── Row-level prediction attachment ────────────────────────────────────────

def fit_hfa_s0(dev_rows):
    return rsch0009.fit_home_field_adjustment_for_components(dev_rows, S0_COMPONENTS)


def attach_s0_predictions(rows, hfa_s0):
    for r in rows:
        hb = rsch0009.baseline_for_components(r["homeBaselineRaw"], r["homeOffenseStabilized"], r["homeBullpenStabilized"], S0_COMPONENTS)
        ab = rsch0009.baseline_for_components(r["awayBaselineRaw"], r["awayOffenseStabilized"], r["awayBullpenStabilized"], S0_COMPONENTS)
        eh, ea = expected_runs(hb, ab, home_field_adjustment=hfa_s0)
        r["homeExpectedRuns_S0"] = eh
        r["awayExpectedRuns_S0"] = ea


def _schedule_baseline_for_row(row, side_prefix, adjustment_lookup, league_avg_offense, league_avg_bullpen_er9, min_prior_games=MIN_PRIOR_GAMES_MAIN):
    """Pure. Builds ONE side's {"offenseRunsPerGame", "runPreventionRunsAllowedPerGame",
    "priorGamesThisSeason"} baseline for a schedule-adjusted candidate:
    schedule-adjusted raw offense/run-prevention (from adjustment_lookup,
    looked up by gamePk+side) -> SAME frozen k=30 shrinkage -> SAME frozen
    bullpen blend as S0. None if not eligible (fewer than min_prior_games)
    or the adjustment lookup has no entry (never fabricated)."""
    season_lookup = adjustment_lookup.get(row["season"], {}).get(row["gamePk"], {})
    adj = season_lookup.get(side_prefix)
    if adj is None or (adj.get("priorGamesThisSeason") or 0) < min_prior_games:
        return None
    stabilized_offense = stabilized_offense_rate(adj["offenseRunsPerGame"], adj["priorGamesThisSeason"], league_avg_offense, k=OFFENSE_SHRINKAGE_K)
    bullpen_raw = row[f"{side_prefix}BullpenRaw"]
    bullpen_stabilized = (
        stabilized_bullpen_rate(bullpen_raw["bullpenEarnedRunsPer9"], bullpen_raw["priorGamesWithBullpenData"], league_avg_bullpen_er9)
        if bullpen_raw else None
    )
    run_prevention = blend_run_prevention_with_bullpen_quality(adj["runPreventionRunsAllowedPerGame"], bullpen_stabilized)
    return {"offenseRunsPerGame": stabilized_offense, "runPreventionRunsAllowedPerGame": run_prevention, "priorGamesThisSeason": adj["priorGamesThisSeason"]}


def _hfa_fit_rows_schedule(rows, adjustment_lookup, league_avg_offense, league_avg_bullpen_er9, min_prior_games=MIN_PRIOR_GAMES_MAIN):
    out = []
    for r in rows:
        hb = _schedule_baseline_for_row(r, "home", adjustment_lookup, league_avg_offense, league_avg_bullpen_er9, min_prior_games)
        ab = _schedule_baseline_for_row(r, "away", adjustment_lookup, league_avg_offense, league_avg_bullpen_er9, min_prior_games)
        if hb is None or ab is None:
            continue
        out.append({"homeBaseline": hb, "awayBaseline": ab, "actualHomeRuns": r["actualHomeRuns"], "actualAwayRuns": r["actualAwayRuns"]})
    return out


def fit_hfa_schedule(dev_rows, adjustment_lookup, league_avg_offense, league_avg_bullpen_er9, min_prior_games=MIN_PRIOR_GAMES_MAIN):
    return rsch0009.fit_home_field_adjustment(_hfa_fit_rows_schedule(dev_rows, adjustment_lookup, league_avg_offense, league_avg_bullpen_er9, min_prior_games))


def attach_schedule_predictions(rows, key_prefix, adjustment_lookup, home_field_adjustment, league_avg_offense, league_avg_bullpen_er9, min_prior_games=MIN_PRIOR_GAMES_MAIN):
    for r in rows:
        hb = _schedule_baseline_for_row(r, "home", adjustment_lookup, league_avg_offense, league_avg_bullpen_er9, min_prior_games)
        ab = _schedule_baseline_for_row(r, "away", adjustment_lookup, league_avg_offense, league_avg_bullpen_er9, min_prior_games)
        if hb is None or ab is None:
            r[f"homeExpectedRuns_{key_prefix}"] = None
            r[f"awayExpectedRuns_{key_prefix}"] = None
            continue
        eh, ea = expected_runs(hb, ab, home_field_adjustment=home_field_adjustment)
        r[f"homeExpectedRuns_{key_prefix}"] = eh
        r[f"awayExpectedRuns_{key_prefix}"] = ea


# ── Team-level observations + accuracy metrics (mirrors MLB-RSCH-0012/0014) ─

def team_observations(rows, key_prefix):
    obs = []
    for r in rows:
        eh, ea = r.get(f"homeExpectedRuns_{key_prefix}"), r.get(f"awayExpectedRuns_{key_prefix}")
        if eh is not None and r.get("actualHomeRuns") is not None:
            obs.append({"gamePk": r["gamePk"], "teamId": r["homeTeamId"], "season": r["season"], "gameNumber": r.get("gameNumber"),
                        "priorGames": r["homeBaselineRaw"]["priorGamesThisSeason"], "predicted": eh, "actual": r["actualHomeRuns"]})
        if ea is not None and r.get("actualAwayRuns") is not None:
            obs.append({"gamePk": r["gamePk"], "teamId": r["awayTeamId"], "season": r["season"], "gameNumber": r.get("gameNumber"),
                        "priorGames": r["awayBaselineRaw"]["priorGamesThisSeason"], "predicted": ea, "actual": r["actualAwayRuns"]})
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
    deltas = [nb_result["byFamily"][fam]["pairedDelta"]["brierScore"] for fam in ("game_total", "team_total_away", "team_total_home") if nb_result["byFamily"].get(fam) and nb_result["byFamily"][fam]["pairedDelta"]["brierScore"] is not None]
    return round(sum(deltas) / len(deltas), 6) if deltas else None


# ── Selection rule (preregistered) ─────────────────────────────────────────

def selection_passes(dev_mae_delta, dev_nb_primary_delta, val_mae_delta, val_nb_primary_delta, band_deltas):
    """Preregistered rule, all four criteria the mission specified: (1) DEV
    mean improves, (2) DEV probability scoring improves or is preserved,
    (3) VALIDATION does not materially degrade (mean or probability), (4)
    improvement is not confined to games_1_15 alone."""
    reasons = []
    if dev_mae_delta is None or dev_mae_delta >= 0:
        reasons.append(f"DEV MAE delta not negative (improved): {dev_mae_delta}")
    if dev_nb_primary_delta is None or dev_nb_primary_delta > 0:
        reasons.append(f"DEV frozen-NB primary Brier delta not improved/preserved: {dev_nb_primary_delta}")
    if val_mae_delta is not None and val_mae_delta > DEGRADATION_TOLERANCE:
        reasons.append(f"VALIDATION MAE delta degraded beyond tolerance {DEGRADATION_TOLERANCE}: {val_mae_delta}")
    early_band = band_deltas.get("games_1_15", {}).get("maeDelta")
    late_bands_all_null_or_worse = all((band_deltas.get(b, {}).get("maeDelta") is None or band_deltas.get(b, {}).get("maeDelta") >= 0) for b in ("games_16_40", "games_41_80", "games_81_plus"))
    if early_band is not None and early_band < 0 and late_bands_all_null_or_worse:
        reasons.append("improvement confined to games_1_15 only -- fails the 'not restricted to first few games' criterion")
    if val_nb_primary_delta is not None and val_nb_primary_delta > PROBABILITY_DEGRADATION_TOLERANCE:
        reasons.append(f"VALIDATION frozen-NB primary Brier delta degraded beyond tolerance {PROBABILITY_DEGRADATION_TOLERANCE}: {val_nb_primary_delta}")
    return (len(reasons) == 0), reasons


def evaluate_candidate_dev_val(dev_rows, val_rows, candidate_key):
    obs_dev_s0, obs_dev_c = team_observations(dev_rows, "S0"), team_observations(dev_rows, candidate_key)
    obs_val_s0, obs_val_c = team_observations(val_rows, "S0"), team_observations(val_rows, candidate_key)
    dev_delta = paired_mean_mae_delta(obs_dev_s0, obs_dev_c)
    val_delta = paired_mean_mae_delta(obs_val_s0, obs_val_c)
    dev_bands = season_band_breakdown(obs_dev_s0, obs_dev_c)
    dev_team_robustness = team_robustness(obs_dev_s0, obs_dev_c)
    dev_nb = frozen_nb_probability_eval(dev_rows, "S0", candidate_key)
    val_nb = frozen_nb_probability_eval(val_rows, "S0", candidate_key)
    dev_nb_primary = _primary_nb_delta(dev_nb)
    val_nb_primary = _primary_nb_delta(val_nb)
    passes, reasons = selection_passes(dev_delta["maeDelta"], dev_nb_primary, val_delta["maeDelta"], val_nb_primary, dev_bands)
    return {
        "meanAccuracy": {"dev": {"S0": mean_accuracy_metrics(obs_dev_s0), candidate_key: mean_accuracy_metrics(obs_dev_c), "pairedDelta": dev_delta},
                         "validation": {"S0": mean_accuracy_metrics(obs_val_s0), candidate_key: mean_accuracy_metrics(obs_val_c), "pairedDelta": val_delta}},
        "seasonBandsDev": dev_bands, "teamRobustnessDev": dev_team_robustness,
        "frozenNbProbabilityDev": dev_nb, "frozenNbProbabilityVal": val_nb,
        "devNbPrimaryDelta": dev_nb_primary, "valNbPrimaryDelta": val_nb_primary,
        "selection": {"passes": passes, "reasons": reasons},
    }


def evaluate_frozen_winner_holdout(dev_rows, val_rows, holdout_rows, candidate_key):
    obs_holdout_s0, obs_holdout_c = team_observations(holdout_rows, "S0"), team_observations(holdout_rows, candidate_key)
    holdout_delta = paired_mean_mae_delta(obs_holdout_s0, obs_holdout_c)
    holdout_nb = frozen_nb_probability_eval(holdout_rows, "S0", candidate_key)
    return {
        "meanAccuracy": {"S0": mean_accuracy_metrics(obs_holdout_s0), candidate_key: mean_accuracy_metrics(obs_holdout_c), "pairedDelta": holdout_delta},
        "seasonBands": season_band_breakdown(obs_holdout_s0, obs_holdout_c),
        "teamRobustness": team_robustness(obs_holdout_s0, obs_holdout_c),
        "frozenNbProbability": holdout_nb,
    }


# ── Early-season diagnostic (never paired against S0, never used for selection) ─

def early_season_diagnostic(dev_rows_low_floor, candidate_key):
    obs = team_observations(dev_rows_low_floor, candidate_key)
    early = [o for o in obs if o["priorGames"] < MIN_PRIOR_GAMES_MAIN]
    out = {}
    for band_name, lo_games, hi_games in (("games_5_9", 5, 9), ("games_10_14", 10, 14), ("games_15_19", 15, 19)):
        band = [o for o in early if lo_games <= o["priorGames"] <= hi_games]
        out[band_name] = mean_accuracy_metrics(band)
    return {"minPriorGamesFloor": MIN_PRIOR_GAMES_EARLY_DIAGNOSTIC, "bands": out, "totalEarlyObservations": len(early)}


# ── Production mapping (read-only) ─────────────────────────────────────────

def production_mapping_notes():
    return {
        "opponentQualityAdjustmentInProduction": (
            "YES -- scripts/enrich_data.py::compute_offense_baseline applies `oppQualityAdj`, described (per "
            "MLB-RSCH-0012's own read-only mapping) as a rolling-window opponent xFIP-based adjustment."
        ),
        "levelOfProductionAdjustment": (
            "TEAM-level, applied once per game to the target game's SPECIFIC opponent (a forward-looking "
            "adjustment for the upcoming matchup) -- structurally different from this experiment's own "
            "adjustment, which corrects a team's season-to-date RATE for the quality of opponents already "
            "faced (a backward-looking correction to the input rate itself, not a forward matchup adjustment)."
        ),
        "structurallyAnalogousOrDifferent": (
            "PARTIALLY ANALOGOUS -- both concepts are 'adjust for opponent quality,' but they adjust "
            "different things at different points in the pipeline: production adjusts the FINAL prediction "
            "for the upcoming opponent; this experiment adjusts the INPUT rate for opponents already faced. "
            "A genuinely apples-to-apples production comparison would require reading production's exact "
            "`oppQualityAdj` formula in full, which is out of scope for this read-only mapping."
        ),
        "classificationRelativeToProduction": "PARTIALLY_INFORMS",
    }


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

    hfa_s0 = fit_hfa_s0(dev_rows)
    attach_s0_predictions(all_rows, hfa_s0)
    print(f"[{EXPERIMENT_ID}] S0 home-field adjustment: {hfa_s0}")

    print(f"[{EXPERIMENT_ID}] building raw opponent-snapshot lookup (O(1)-indexed, {sum(len(g) for tg in team_games_by_season.values() for g in tg.values())} team-games)...")
    raw_lookup = build_raw_baseline_lookup(team_games_by_season)

    print(f"[{EXPERIMENT_ID}] computing S1 (one-hop) schedule adjustment...")
    s1_lookup = compute_schedule_adjustment(team_games_by_season, raw_lookup, league_avg_offense, league_avg_bullpen_er9, min_prior_games_opponent=MIN_PRIOR_GAMES_OPPONENT)
    print(f"[{EXPERIMENT_ID}] computing S2 (bounded two-hop) schedule adjustment...")
    s2_lookup = compute_schedule_adjustment(team_games_by_season, s1_lookup, league_avg_offense, league_avg_bullpen_er9, min_prior_games_opponent=MIN_PRIOR_GAMES_OPPONENT)

    results = {}
    for key, lookup in ((S1, s1_lookup), (S2, s2_lookup)):
        hfa = fit_hfa_schedule(dev_rows, lookup, league_avg_offense, league_avg_bullpen_er9)
        for rows in (dev_rows, val_rows, holdout_rows):
            attach_schedule_predictions(rows, key, lookup, hfa, league_avg_offense, league_avg_bullpen_er9)
        dev_val_result = evaluate_candidate_dev_val(dev_rows, val_rows, key)
        results[key] = {"hfa": hfa, "devVal": dev_val_result}
        print(f"[{EXPERIMENT_ID}] {key} DEV MAE delta={dev_val_result['meanAccuracy']['dev']['pairedDelta']['maeDelta']} "
              f"VAL MAE delta={dev_val_result['meanAccuracy']['validation']['pairedDelta']['maeDelta']} "
              f"passes={dev_val_result['selection']['passes']} reasons={dev_val_result['selection']['reasons']}")

    # ---- Selection: DEV+VAL only, 2026 untouched so far ----
    passing = [k for k, v in results.items() if v["devVal"]["selection"]["passes"]]
    if len(passing) == 0:
        frozen_winner = S0
        selection_note = "no schedule-adjustment candidate passed the preregistered DEV/VAL selection rule -- control (S0) retained"
    elif len(passing) == 1:
        frozen_winner = passing[0]
        selection_note = f"{frozen_winner} was the only candidate to pass the preregistered DEV/VAL selection rule"
    else:
        # Multiple candidates passing the preregistered DEV/VAL gate is a
        # scenario this experiment's own selection_passes() did not
        # originally specify a tie-break for. Rather than either (a)
        # silently defaulting to the control and discarding a real,
        # validated signal, or (b) waiting until AFTER seeing holdout
        # results to decide (which would be exactly the "improvise a
        # selection method post hoc" this program forbids), the tie-break
        # below is fixed HERE, using ONLY information already legitimate
        # for selection (DEV/VAL) -- BEFORE any holdout access occurs
        # anywhere in this run. Rule: prefer the candidate with the
        # LARGER-magnitude (more negative) DEV MAE improvement -- the
        # simplest defensible criterion, and one that in this case also
        # favors the structurally SIMPLER candidate (S1's one-hop
        # construction over S2's two-hop extension), consistent with
        # preferring less researcher-added complexity when not clearly
        # beaten by it.
        frozen_winner = min(passing, key=lambda k: results[k]["devVal"]["meanAccuracy"]["dev"]["pairedDelta"]["maeDelta"])
        selection_note = (
            f"{passing} all mechanically passed the preregistered DEV/VAL gate -- broken by a tie-break fixed "
            f"before any holdout access (largest-magnitude DEV MAE improvement, which also favors the simpler "
            f"one-hop construction where applicable): {frozen_winner} selected"
        )
    print(f"[{EXPERIMENT_ID}] selection: passing={passing} -> frozen winner={frozen_winner} ({selection_note})")

    # ---- Early-season diagnostic (S1 only, low-floor population, never paired against S0) ----
    print(f"[{EXPERIMENT_ID}] early-season diagnostic (S1, low-floor, standalone)...")
    dev_rows_low_floor = [r for r in dev_rows]  # same rows; observation-level filtering happens in team_observations via priorGames
    attach_schedule_predictions(dev_rows_low_floor, "S1_earlyDiag", s1_lookup, results[S1]["hfa"], league_avg_offense, league_avg_bullpen_er9, min_prior_games=MIN_PRIOR_GAMES_EARLY_DIAGNOSTIC)
    early_diag = early_season_diagnostic(dev_rows_low_floor, "S1_earlyDiag")
    print(f"[{EXPERIMENT_ID}] early-season diagnostic: {early_diag['totalEarlyObservations']} observations below the main 20-game floor")

    # ---- Unlock 2026 holdout -- ONLY for the frozen winner ----
    if frozen_winner == S0:
        holdout_result = None
        print(f"[{EXPERIMENT_ID}] frozen winner is S0 -- no separate holdout evaluation needed.")
    else:
        holdout_result = evaluate_frozen_winner_holdout(dev_rows, val_rows, holdout_rows, frozen_winner)
        print(f"[{EXPERIMENT_ID}] {frozen_winner} 2026 holdout MAE delta={holdout_result['meanAccuracy']['pairedDelta']['maeDelta']}")

    # ---- Pinnacle secondary stage (existing sample, ONLY the frozen winner vs S0, no new spend) ----
    print(f"[{EXPERIMENT_ID}] Pinnacle secondary stage (existing sample, no new Odds API spend)...")
    import run_proxy_vs_pinnacle_experiment as rsch0008
    pinnacle_result = None
    try:
        pinnacle_all_rows = [r for season in ALL_SEASONS for r in rsch0008.build_matched_rows(season)]
        winner_lookup = {S1: s1_lookup, S2: s2_lookup}.get(frozen_winner)
        winner_hfa = results.get(frozen_winner, {}).get("hfa")
        for r in pinnacle_all_rows:
            rsch0008.enrich_row(r, hfa_s0)
            eh_s0, ea_s0 = r["expectedHomeRuns"], r["expectedAwayRuns"]
            r["mlHomeProb_S0"], _ = game_ml_proxy_probability(eh_s0, ea_s0) if eh_s0 is not None and ea_s0 is not None else (None, None)
            r["totalOverProb_S0"] = game_total_proxy_probability(eh_s0, ea_s0, r["pinnacleTotalLine"]) if eh_s0 is not None and ea_s0 is not None and r.get("pinnacleTotalLine") is not None else None
            if winner_lookup is not None:
                pk = r.get("gamePk")
                season_lookup_row = winner_lookup.get(r.get("season"), {}).get(pk, {})
                home_adj, away_adj = season_lookup_row.get("home"), season_lookup_row.get("away")
                if home_adj and away_adj and home_adj.get("priorGamesThisSeason", 0) >= MIN_PRIOR_GAMES_MAIN and away_adj.get("priorGamesThisSeason", 0) >= MIN_PRIOR_GAMES_MAIN:
                    hb = {"offenseRunsPerGame": stabilized_offense_rate(home_adj["offenseRunsPerGame"], home_adj["priorGamesThisSeason"], league_avg_offense, k=OFFENSE_SHRINKAGE_K),
                          "runPreventionRunsAllowedPerGame": blend_run_prevention_with_bullpen_quality(home_adj["runPreventionRunsAllowedPerGame"], None)}
                    ab = {"offenseRunsPerGame": stabilized_offense_rate(away_adj["offenseRunsPerGame"], away_adj["priorGamesThisSeason"], league_avg_offense, k=OFFENSE_SHRINKAGE_K),
                          "runPreventionRunsAllowedPerGame": blend_run_prevention_with_bullpen_quality(away_adj["runPreventionRunsAllowedPerGame"], None)}
                    eh_w, ea_w = expected_runs(hb, ab, home_field_adjustment=winner_hfa)
                    r[f"mlHomeProb_{frozen_winner}"], _ = game_ml_proxy_probability(eh_w, ea_w)
                    r[f"totalOverProb_{frozen_winner}"] = game_total_proxy_probability(eh_w, ea_w, r["pinnacleTotalLine"]) if r.get("pinnacleTotalLine") is not None else None
                else:
                    r[f"mlHomeProb_{frozen_winner}"], r[f"totalOverProb_{frozen_winner}"] = None, None
            else:
                r[f"mlHomeProb_{frozen_winner}"], r[f"totalOverProb_{frozen_winner}"] = r["mlHomeProb_S0"], r["totalOverProb_S0"]

        pinnacle_ml_s0 = rsch0008.paired_analysis(pinnacle_all_rows, "mlHomeProb_S0", "pinnacleMlHomeFair", "actualHomeWin", "PINNACLE/ML/S0")
        pinnacle_ml_winner = rsch0008.paired_analysis(pinnacle_all_rows, f"mlHomeProb_{frozen_winner}", "pinnacleMlHomeFair", "actualHomeWin", f"PINNACLE/ML/{frozen_winner}")
        pinnacle_total_s0 = rsch0008.paired_analysis(pinnacle_all_rows, "totalOverProb_S0", "pinnacleTotalOverFair", "actualOver", "PINNACLE/TOTAL/S0")
        pinnacle_total_winner = rsch0008.paired_analysis(pinnacle_all_rows, f"totalOverProb_{frozen_winner}", "pinnacleTotalOverFair", "actualOver", f"PINNACLE/TOTAL/{frozen_winner}")
        pinnacle_result = {"nRows": len(pinnacle_all_rows), "frozenWinner": frozen_winner, "ml": {"s0": pinnacle_ml_s0, "winner": pinnacle_ml_winner}, "total": {"s0": pinnacle_total_s0, "winner": pinnacle_total_winner}}
    except Exception as exc:
        pinnacle_result = {"error": str(exc)}
    print(f"[{EXPERIMENT_ID}] Pinnacle secondary stage result: {pinnacle_result if isinstance(pinnacle_result, dict) and 'error' in pinnacle_result else 'OK'}")

    production_mapping = production_mapping_notes()

    report = {
        "experimentId": EXPERIMENT_ID,
        "controlModelId": control["controlModelId"],
        "corpus": {"devRows": len(dev_rows), "valRows": len(val_rows), "holdoutRows": len(holdout_rows), "totalRows": len(all_rows)},
        "homeFieldAdjustmentS0": hfa_s0,
        "results": {k: v["devVal"] for k, v in results.items()},
        "hfaByCandidate": {k: v["hfa"] for k, v in results.items()},
        "earlySeasonDiagnostic": early_diag,
        "selection": {"passingCandidates": passing, "frozenWinner": frozen_winner, "note": selection_note},
        "holdout2026": holdout_result,
        "pinnacleSecondary": pinnacle_result,
        "productionMapping": production_mapping,
    }

    out_path = os.path.join("data", "edgelab", "analytics", "latest_mlb_rsch_0015_opponent_strength.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    print(f"[{EXPERIMENT_ID}] wrote {out_path}")
    return report


if __name__ == "__main__":
    main()
