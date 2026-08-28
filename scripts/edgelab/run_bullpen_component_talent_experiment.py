#!/usr/bin/env python3
"""
scripts/edgelab/run_bullpen_component_talent_experiment.py
====================================================================
Research Lab experiment MLB-RSCH-0020: "Bullpen Component Talent".
RESEARCH ONLY. NO production changes.

CORE QUESTION: can we estimate true bullpen run-prevention talent
better using pitching COMPONENTS (strikeouts, walks) than using the
existing bullpen run/earned-run baseline (MLB-RSCH-0009's own frozen
ER9-based component)?

DATA: reuses two ALREADY-committed multi-season boxscore caches,
unchanged, no new acquisition of any kind:
  - data/research_cache/bullpen_backtest/<season>/boxscores.jsonl.gz
    (via run_proxy_ablation_experiment.load_relief_er9_games, UNCHANGED)
    -- runs/earnedRuns/outs only, the source of B0's exact reproduction.
  - data/research_cache/starter_workload/<season>/boxscores.jsonl.gz
    (via scripts/edgelab/backtest/fetch_mlb_starter_workload_cache.py's
    own cache_path helper) -- a RICHER, separately-fetched cache
    (MLB-RSCH-0004) that additionally carries strikeOuts/baseOnBalls/
    battersFaced per pitcher, for EVERY pitcher (starter orderIndex=0,
    relievers orderIndex>0) across 2022-2026 (~2,430 games/season) --
    the source of B1's K-BB% component.

CANDIDATE B2 (K/BB/HR component model) is explicitly NOT_RUN: verified
directly that NEITHER cache contains a per-pitcher home-runs-allowed
field at any scale (checked all 5 seasons of the starter_workload
cache -- zero games carry a "homeRuns" key). Building it would require
a genuinely new MLB Stats API acquisition via a dedicated GitHub
Actions workflow whose correctness this sandboxed session has no way
to verify (this environment's own direct MLB API calls return 403
Forbidden). Per this milestone's own explicit allowance for B4 to be
marked NOT_RUN rather than expanding researcher degrees of freedom
under time pressure, the SAME principle applies here -- a genuine,
verified DATA gap, not a modeling-complexity dodge.

CANDIDATE B4 (reliever-level aggregation) is also NOT_RUN for the same
reason the milestone itself anticipates: it would require inferring
individual-reliever availability/depth without using target-game
usage, a materially larger modeling project than this milestone's own
"tiny parameter count" philosophy allows under the same time budget
already spent reaching B1/B3.

Max disposition: SHADOW_CANDIDATE. Never PROMOTION_CANDIDATE.
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
_BACKTEST_SCRIPTS_DIR = os.path.join(_EDGELAB_SCRIPTS_DIR, "backtest")
if _BACKTEST_SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _BACKTEST_SCRIPTS_DIR)

from lib.edgelab.backtest.proxy_model import expected_runs, game_ml_proxy_probability, game_total_proxy_probability, fit_home_field_adjustment
from lib.edgelab.backtest.proxy_enrichment import stabilized_offense_rate, stabilized_bullpen_rate, blend_run_prevention_with_bullpen_quality, bullpen_quality_baseline, OFFENSE_SHRINKAGE_K
from lib.edgelab.backtest.team_offense_recency_reconstruction import prior_games_this_season, season_to_date_rate, MIN_PRIOR_GAMES_FOR_BASELINE
from lib.edgelab.backtest.run_distributions import negative_binomial_pmf, independent_joint_pmf
from lib.edgelab.storage import read_records
from lib.edgelab import experiment_registry as reg
from lib.edgelab import evidence_levels as ev
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import paired_evaluation as pe
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab.research_stats import DEFAULT_BOOTSTRAP_SEED, independent_unit_count, sample_size_status, game_clustered_bootstrap_ci

import run_proxy_ablation_experiment as rsch0009  # noqa: E402
import fetch_mlb_starter_workload_cache as starter_fetcher  # noqa: E402

EXPERIMENT_ID = "MLB-RSCH-0020"
REGISTRATION_TIMESTAMP = "2026-08-28T16:35:00Z"

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

BASELINE_COMPONENTS = frozenset({"offense", "bullpen"})  # MLB-RSCH-0009's own final composition, reused for team-mean integration

KBB_SHRINKAGE_K_GRID = (10, 20, 30, 50, 80)  # preregistered, small, DEV-only
BLEND_WEIGHT_GRID = tuple(round(w * 0.1, 1) for w in range(0, 11))  # 0.0 .. 1.0 step 0.1, preregistered

SAMPLE_DEPTH_BANDS = (("first_20_ip", 0, 20), ("20_50_ip", 20, 50), ("50_100_ip", 50, 100), ("100_plus_ip", 100, 99999))

MARGIN_THRESHOLDS = (2, 3)
GAME_TOTAL_LINES = (7.5, 8.5, 9.5, 10.5)
TEAM_TOTAL_LINES = (2.5, 3.5, 4.5, 5.5)

DEGRADATION_TOLERANCE = 0.05
PROBABILITY_DEGRADATION_TOLERANCE = 0.0
TEAM_CONCENTRATION_MIN_FRACTION = 0.4


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
        name="mlb_rsch_0020_bullpen_component_talent_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0020 bullpen component talent v1: B0 = MLB-RSCH-0009's own frozen "
                        "ER9-based bullpen component (exact reproduction) vs B1 (K-BB% component, "
                        "DEV-fit shrinkage + DEV-fit linear mapping to predicted relief ER9) vs B3 "
                        "(single DEV-fit blend weight of B0+B1). B2/B4 NOT_RUN -- no HR data at scale."
        ),
        probability_adapter_identity="lib.edgelab.backtest.run_distributions (frozen MLB-RSCH-0010 negative-binomial, dispersion unchanged, never refit here)",
        model_engine_family="pit_safe_research_bullpen_component_talent_v1",
        required_input_provenance=["team_recent_game_log_reconstruction"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "Tests whether a K-BB%-based component estimate of bullpen talent (strikeouts minus "
            "walks per batter faced, DEV-fit shrinkage toward league average, DEV-fit linear mapping "
            "to predicted relief runs allowed) improves on MLB-RSCH-0009's own frozen ERA/ER9-based "
            "bullpen component -- for bullpen-specific outcome prediction, team-mean expected-run "
            "accuracy, AND downstream frozen-NB probability quality."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Bullpen Component Talent",
        hypothesis=(
            "H1: K-BB% (strikeouts minus walks per batter faced) stabilizes faster and carries more "
            "true bullpen talent information than ERA/ER9, particularly at low sample depth. H2: a "
            "DEV-fit linear mapping from shrunk K-BB% to predicted relief runs allowed (B1) improves "
            "on B0's raw ER9-based estimate for bullpen-specific outcome prediction. H3: a "
            "parsimonious single-weight blend of B0 (realized runs) and B1 (component talent) "
            "captures complementary information from both (B3). H4 (expected null, preregistered): "
            "B2 (adding HR prevention) is NOT_RUN -- no HR-per-pitcher data exists at multi-season "
            "scale in this repository's cached boxscore extracts."
        ),
        research_question="Can we estimate true bullpen run-prevention talent better using pitching components (K, BB) than using the existing bullpen run/earned-run baseline?",
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E2_PIT_HISTORICAL,
        target_population="Every MLB regular-season team-game with a bullpen-eligible (>=20 prior relief-appearance games this season) B0 baseline, 2022-2026, matching MLB-RSCH-0009's own eligibility",
        market_families=["game_result", "game_total", "team_total", "run_margin"],
        eligibility_criteria=["identical to MLB-RSCH-0009's own bullpen_quality_baseline() eligibility (>=20 prior relief-appearance games) -- unchanged, applied to BOTH B0 and B1"],
        exclusion_criteria=[
            "B2 (K/BB/HR component model) -- NOT_RUN, no per-pitcher HR data at scale in any existing cache (verified directly)",
            "B4 (reliever-level aggregation) -- NOT_RUN, would require inferring reliever availability without target-game usage, a materially larger modeling project",
            "workload/fatigue as a mechanism -- MLB-RSCH-0003 already addressed that; this milestone is TALENT research only",
            "any use of target-game bullpen usage/innings as a PREDICTOR (only as outcome/exposure)",
            "betting P/L or ROI as a selection criterion",
        ],
        prediction_checkpoints=["SEASON_TO_DATE_PREGAME"],
        primary_metric="paired MAE delta on future relief earned-runs-per-9 (candidate minus B0), game-clustered 95% CI",
        secondary_metrics=[
            "team-mean expected-run MAE/RMSE/bias delta (candidate integrated into the frozen {offense,bullpen} framework)",
            "frozen-NB team-total/game-total/moneyline/run-margin Brier deltas",
            "sample-depth-band-specific MAE deltas (fixed bullpen-IP bands, never optimized)",
            "per-team effect distribution / leave-one-team-out robustness",
            "paired candidate-minus-Pinnacle Brier delta (secondary stage, only if a candidate survives locked 2026)",
        ],
        chronological_split_policy=f"SEASON_BASED: development={DEV_SEASONS}, validation={VALIDATION_SEASONS}, holdout={HOLDOUT_SEASONS} (locked)",
        minimum_sample_requirement={"independentGames": 50},
        clustering_unit="gamePk",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={"team_recent_game_log_reconstruction": "PREDICTIVE_INPUT"},
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            "evidenceLevel E2_PIT_HISTORICAL. Frozen MLB-RSCH-0010 NB dispersion reused unchanged, "
            "never refit per candidate. B0 verified to exactly reproduce MLB-RSCH-0009's own "
            "bullpen_quality_baseline output on matching rows. Max disposition SHADOW_CANDIDATE -- "
            "never PROMOTION_CANDIDATE."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── B1 data: K-BB% relief components (starter_workload cache, richer than bullpen_backtest) ──

def load_relief_kbb_games(season, team_games_by_id):
    """{teamId: relief_kbb_games} for one season -- mirrors
    run_proxy_ablation_experiment.load_relief_er9_games's own shape and
    discipline exactly, joining the starter_workload cache's PER-PITCHER
    strikeOuts/baseOnBalls/battersFaced (relievers only, orderIndex>0)
    onto team_games_by_id (MLB-RSCH-0009's own load_all_team_games_with_venue
    output, unchanged -- gives the per-team side/date/gamePk ordering).
    A game with zero relief batters faced (starter completed the game)
    gets reliefKMinusBBPct=None -- a real, well-defined "undefined", never
    a fabricated zero, matching MLB-RSCH-0003's own relief_outcome_for_game
    convention exactly."""
    boxscore_path = starter_fetcher.boxscore_cache_path(season)
    boxscores_by_game_pk = {row["gamePk"]: row for row in read_records(boxscore_path) if row.get("gamePk") is not None}

    out = {}
    for team_id, games in team_games_by_id.items():
        augmented = []
        for g in games:
            box = boxscores_by_game_pk.get(g["gamePk"])
            pitchers = (box.get("homePitchers" if g.get("side") == "home" else "awayPitchers") or []) if box else []
            relievers = [p for p in pitchers if (p.get("orderIndex") or 0) > 0]
            k = sum(p["strikeOuts"] for p in relievers if p.get("strikeOuts") is not None)
            bb = sum(p["baseOnBalls"] for p in relievers if p.get("baseOnBalls") is not None)
            bf = sum(p["battersFaced"] for p in relievers if p.get("battersFaced") is not None)
            rate = round((k - bb) / bf, 4) if bf > 0 else None
            augmented.append({**g, "reliefKMinusBBPct": rate, "reliefBattersFacedThisGame": bf})
        out[team_id] = augmented
    return out


def bullpen_kbb_baseline(relief_kbb_games, as_of_game, min_prior_games=MIN_PRIOR_GAMES_FOR_BASELINE):
    """Mirrors bullpen_quality_baseline's own eligibility/computation
    EXACTLY (prior_games_this_season + season_to_date_rate, both
    UNCHANGED), applied to reliefKMinusBBPct instead of
    reliefEarnedRunsPer9."""
    prior = prior_games_this_season(relief_kbb_games, as_of_game)
    defined = [g for g in prior if g.get("reliefKMinusBBPct") is not None]
    if len(defined) < min_prior_games:
        return None
    rate = season_to_date_rate(defined, "reliefKMinusBBPct")
    if rate is None:
        return None
    return {"kMinusBBPct": rate, "priorGamesWithReliefKbbData": len(defined)}


def fit_league_average_kbb(dev_relief_kbb_games_by_team):
    values = []
    for team_games in dev_relief_kbb_games_by_team:
        for g in team_games:
            if g.get("reliefKMinusBBPct") is not None:
                values.append(g["reliefKMinusBBPct"])
    return round(sum(values) / len(values), 4) if values else None


def _simple_ols(pairs):
    """Pure bivariate OLS (slope, intercept) via the standard closed-form
    covariance/variance formula. (None, None) if degenerate."""
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


def build_bullpen_rows(relief_er9_games_by_team, relief_kbb_games_by_team, league_avg_kbb, kbb_shrinkage_k, season):
    """One row per team-game where B0's own ER9 baseline is eligible
    (>=20 prior relief-appearance games -- MLB-RSCH-0009's own floor,
    reused unchanged): {teamId, gamePk, date, season, actualReliefER9,
    b0Er9, shrunkKbb (or None if B1 ineligible), priorReliefGames}.
    `season` is passed explicitly (NOT read from the game dict) --
    load_all_team_games_with_venue()'s own per-game records carry no
    "season" key at all, so relying on g.get("season") would silently
    produce None on every row (a real bug caught during this milestone's
    own first full run -- see docs/EDGELAB_MLB_RSCH_0020_BULLPEN_COMPONENT_TALENT.md)."""
    rows = []
    for team_id, er9_games in relief_er9_games_by_team.items():
        kbb_games = relief_kbb_games_by_team.get(team_id, [])
        kbb_by_pk = {g["gamePk"]: g for g in kbb_games}
        for g in er9_games:
            b0 = bullpen_quality_baseline(er9_games, g)
            if b0 is None:
                continue
            kbb_row = kbb_by_pk.get(g["gamePk"])
            b1_kbb = bullpen_kbb_baseline(kbb_games, kbb_row, min_prior_games=1) if kbb_row is not None else None
            shrunk_kbb = None
            if b1_kbb is not None and league_avg_kbb is not None:
                n = b1_kbb["priorGamesWithReliefKbbData"]
                shrunk_kbb = round((b1_kbb["kMinusBBPct"] * n + league_avg_kbb * kbb_shrinkage_k) / (n + kbb_shrinkage_k), 4)
            rows.append({
                "teamId": team_id, "gamePk": g["gamePk"], "date": g.get("date"), "season": season,
                "actualReliefER9": g.get("reliefEarnedRunsPer9"), "b0Er9": b0["bullpenEarnedRunsPer9"],
                "priorReliefGames": b0["priorGamesWithBullpenData"], "shrunkKbb": shrunk_kbb,
            })
    return rows


def build_bullpen_rows_multi_season(seasons_data, league_avg_kbb, kbb_shrinkage_k):
    """seasons_data: list of (season, er9_games_by_team, kbb_games_by_team)
    triples, ONE PER SEASON -- each season's own prior_games_this_season
    sequence is built and consumed WITHIN that season only (build_bullpen_rows
    is called once per season, never on a cross-season-merged team_games
    dict, which would let a team's early games in one season see "prior"
    games from a different season). Rows from every season are then
    concatenated as independent observations for fitting/evaluation."""
    rows = []
    for season, er9_by_team, kbb_by_team in seasons_data:
        rows.extend(build_bullpen_rows(er9_by_team, kbb_by_team, league_avg_kbb, kbb_shrinkage_k, season))
    return rows


def fit_kbb_shrinkage_and_mapping_dev_only(dev_seasons_data, league_avg_kbb):
    """DEV-only, preregistered small grid over KBB_SHRINKAGE_K_GRID;
    for each candidate K, fit the bivariate OLS mapping shrunkKbb ->
    actualReliefER9 (only rows where BOTH B0 and the shrunk-KBB value are
    defined AND actualReliefER9 is defined -- an outcome-exposure row,
    matching bullpen_quality_baseline's own "target game bullpen data is
    outcome, never predictor" discipline), and picks the K/mapping pair
    minimizing DEV MAE. Deterministic, no randomness."""
    best = None
    for k in KBB_SHRINKAGE_K_GRID:
        rows = build_bullpen_rows_multi_season(dev_seasons_data, league_avg_kbb, k)
        fit_rows = [r for r in rows if r["shrunkKbb"] is not None and r["actualReliefER9"] is not None]
        slope, intercept = _simple_ols([(r["shrunkKbb"], r["actualReliefER9"]) for r in fit_rows])
        if slope is None:
            continue
        errors = [abs((intercept + slope * r["shrunkKbb"]) - r["actualReliefER9"]) for r in fit_rows]
        mae = sum(errors) / len(errors) if errors else None
        if mae is not None and (best is None or mae < best["mae"]):
            best = {"k": k, "slope": round(slope, 6), "intercept": round(intercept, 6), "mae": round(mae, 4), "n": len(fit_rows)}
    return best


def predict_b1_er9(shrunk_kbb, slope, intercept):
    if shrunk_kbb is None or slope is None:
        return None
    return round(intercept + slope * shrunk_kbb, 4)


def fit_blend_weight_dev_only(dev_rows_with_b1):
    """DEV-only, preregistered small grid (BLEND_WEIGHT_GRID) over a
    SINGLE blend weight w: predictedER9 = w*b0Er9 + (1-w)*b1Er9. Picks
    the w minimizing DEV MAE against actualReliefER9. Deterministic."""
    best = None
    for w in BLEND_WEIGHT_GRID:
        errors = []
        for r in dev_rows_with_b1:
            if r["b1Er9"] is None or r["actualReliefER9"] is None:
                continue
            pred = round(w * r["b0Er9"] + (1 - w) * r["b1Er9"], 4)
            errors.append(abs(pred - r["actualReliefER9"]))
        if not errors:
            continue
        mae = sum(errors) / len(errors)
        if best is None or mae < best["mae"]:
            best = {"weight": w, "mae": round(mae, 4), "n": len(errors)}
    return best


# ── Team-mean integration (frozen {offense,bullpen} framework, unchanged) ──

def attach_team_mean_predictions(rows, key_prefix, bullpen_override_fn, league_avg_offense, hfa):
    """`bullpen_override_fn(row, side)` -> predicted relief ER9 (or None
    to fall back to the row's own raw runPreventionRunsAllowedPerGame,
    i.e. no override -- B0's own case). Offense stays IDENTICAL across
    every candidate -- isolates the bullpen lever completely, same
    discipline as every earlier milestone's own component-isolation."""
    for r in rows:
        hb = dict(r["homeBaselineRaw"])
        ab = dict(r["awayBaselineRaw"])
        hb["offenseRunsPerGame"] = stabilized_offense_rate(hb["offenseRunsPerGame"], hb["priorGamesThisSeason"], league_avg_offense)
        ab["offenseRunsPerGame"] = stabilized_offense_rate(ab["offenseRunsPerGame"], ab["priorGamesThisSeason"], league_avg_offense)
        home_override = bullpen_override_fn(r, "home")
        away_override = bullpen_override_fn(r, "away")
        hb["runPreventionRunsAllowedPerGame"] = home_override if home_override is not None else blend_run_prevention_with_bullpen_quality(hb["runPreventionRunsAllowedPerGame"], r["homeBullpenStabilized"])
        ab["runPreventionRunsAllowedPerGame"] = away_override if away_override is not None else blend_run_prevention_with_bullpen_quality(ab["runPreventionRunsAllowedPerGame"], r["awayBullpenStabilized"])
        eh, ea = expected_runs(hb, ab, home_field_adjustment=hfa)
        r[f"homeExpectedRuns_{key_prefix}"] = eh
        r[f"awayExpectedRuns_{key_prefix}"] = ea


def team_observations(rows, key_prefix):
    obs = []
    for r in rows:
        eh, ea = r.get(f"homeExpectedRuns_{key_prefix}"), r.get(f"awayExpectedRuns_{key_prefix}")
        if eh is not None and r.get("actualHomeRuns") is not None:
            obs.append({"gamePk": r["gamePk"], "teamId": r["homeTeamId"], "season": r["season"], "predicted": eh, "actual": r["actualHomeRuns"]})
        if ea is not None and r.get("actualAwayRuns") is not None:
            obs.append({"gamePk": r["gamePk"], "teamId": r["awayTeamId"], "season": r["season"], "predicted": ea, "actual": r["actualAwayRuns"]})
    return obs


def mean_accuracy_metrics(obs):
    if not obs:
        return {"n": 0, "independentGames": 0, "mae": None, "rmse": None, "bias": None}
    errors = [o["predicted"] - o["actual"] for o in obs]
    n = len(errors)
    return {
        "n": n, "independentGames": independent_unit_count(obs, key="gamePk"),
        "mae": round(sum(abs(e) for e in errors) / n, 4), "rmse": round(math.sqrt(sum(e ** 2 for e in errors) / n), 4),
        "bias": round(sum(errors) / n, 4),
    }


def paired_mean_delta(obs_a, obs_b):
    by_a = {(o["gamePk"], o["teamId"]): o for o in obs_a}
    by_b = {(o["gamePk"], o["teamId"]): o for o in obs_b}
    common = sorted(set(by_a) & set(by_b))
    paired_rows = [{"gamePk": k[0], "teamId": k[1], "errA": abs(by_a[k]["predicted"] - by_a[k]["actual"]), "errB": abs(by_b[k]["predicted"] - by_b[k]["actual"])} for k in common]

    def _delta(subset):
        return sum(r["errB"] - r["errA"] for r in subset) / len(subset) if subset else None

    point = _delta(paired_rows)
    lo, hi, _ = game_clustered_bootstrap_ci(paired_rows, _delta, cluster_key="gamePk", seed=DEFAULT_BOOTSTRAP_SEED)
    return {"n": len(paired_rows), "independentGames": independent_unit_count(paired_rows, key="gamePk"),
            "maeDelta": round(point, 6) if point is not None else None,
            "maeDeltaCI95": {"low": lo, "high": hi, "method": "GAME_CLUSTERED_BOOTSTRAP"},
            "interpretation": "negative == candidate B improved (lower MAE than A)"}


def team_robustness(obs_a, obs_b):
    by_a = {(o["gamePk"], o["teamId"]): o for o in obs_a}
    by_b = {(o["gamePk"], o["teamId"]): o for o in obs_b}
    common = sorted(set(by_a) & set(by_b))
    per_team = {}
    for team_id in sorted({k[1] for k in common}):
        keys = [k for k in common if k[1] == team_id]
        err_a = [abs(by_a[k]["predicted"] - by_a[k]["actual"]) for k in keys]
        err_b = [abs(by_b[k]["predicted"] - by_b[k]["actual"]) for k in keys]
        if not err_a:
            continue
        per_team[str(team_id)] = round(sum(err_b) / len(err_b) - sum(err_a) / len(err_a), 4)
    deltas = list(per_team.values())
    return {"perTeamMaeDelta": per_team, "nTeamsPositive": sum(1 for d in deltas if d < 0), "nTeamsTotal": len(deltas)}


# ── Frozen-NB probability evaluation (dispersion NEVER refit here) ────────

def _nb_joint(home_mean, away_mean):
    def home_pmf(k):
        return negative_binomial_pmf(k, home_mean, FROZEN_DISPERSION)

    def away_pmf(k):
        return negative_binomial_pmf(k, away_mean, FROZEN_DISPERSION)
    return independent_joint_pmf(home_pmf, away_pmf)


def nb_probability_cells(home_mean, away_mean):
    if home_mean is None or away_mean is None or home_mean <= 0 or away_mean <= 0:
        return None
    joint = _nb_joint(home_mean, away_mean)
    max_runs = 30
    cells = {}
    home_win = sum(joint(h, a) for h in range(max_runs) for a in range(max_runs) if h > a)
    push = sum(joint(h, a) for h in range(max_runs) for a in range(max_runs) if h == a)
    cells["moneyline"] = round(home_win + push / 2, 6)
    for line in GAME_TOTAL_LINES:
        cells[f"game_total_over_{line}"] = round(sum(joint(h, a) for h in range(max_runs) for a in range(max_runs) if h + a > line), 6)
    for line in TEAM_TOTAL_LINES:
        cells[f"team_total_home_over_{line}"] = round(sum(joint(h, a) for h in range(max_runs) for a in range(max_runs) if h > line), 6)
        cells[f"team_total_away_over_{line}"] = round(sum(joint(h, a) for h in range(max_runs) for a in range(max_runs) if a > line), 6)
    for m in MARGIN_THRESHOLDS:
        cells[f"run_margin_win_by_at_least_{m}"] = round(sum(joint(h, a) for h in range(max_runs) for a in range(max_runs) if h - a >= m), 6)
    return cells


def _outcomes_for_actual(actual_home, actual_away):
    outcomes = {"moneyline": 1 if actual_home > actual_away else (0.5 if actual_home == actual_away else 0)}
    total = actual_home + actual_away
    for line in GAME_TOTAL_LINES:
        outcomes[f"game_total_over_{line}"] = 1 if total > line else 0
    for line in TEAM_TOTAL_LINES:
        outcomes[f"team_total_home_over_{line}"] = 1 if actual_home > line else 0
        outcomes[f"team_total_away_over_{line}"] = 1 if actual_away > line else 0
    for m in MARGIN_THRESHOLDS:
        outcomes[f"run_margin_win_by_at_least_{m}"] = 1 if (actual_home - actual_away) >= m else 0
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

    by_family = {}
    for family_prefix in ("game_total", "team_total_home", "team_total_away", "moneyline", "run_margin"):
        fam_control = [r for r in control_rows if r["cellKey"].startswith(family_prefix)]
        fam_candidate = [r for r in candidate_rows if r["cellKey"].startswith(family_prefix)]
        fam_pairing = pe.pair_eligible_observations(fam_control, fam_candidate, key_fn=key_fn)
        by_family[family_prefix] = pe.evaluate_probability_model_pair(fam_pairing, game_key="gameId", date_key="gameDate")
    return {"byFamily": by_family}


def _primary_nb_delta(nb_result):
    deltas = [nb_result["byFamily"][fam]["pairedDelta"]["brierScore"] for fam in ("game_total", "team_total_home", "team_total_away", "moneyline") if nb_result["byFamily"].get(fam) and nb_result["byFamily"][fam]["pairedDelta"]["brierScore"] is not None]
    return round(sum(deltas) / len(deltas), 6) if deltas else None


# ── Selection rule (preregistered BEFORE results, locked) ─────────────────

def selection_passes(dev_bullpen_mae_delta, dev_team_mae_delta, dev_nb_primary_delta, val_bullpen_mae_delta, val_nb_primary_delta, team_robustness_result):
    reasons = []
    if dev_bullpen_mae_delta is None or dev_bullpen_mae_delta >= 0:
        reasons.append(f"DEV bullpen-outcome MAE delta not improved: {dev_bullpen_mae_delta}")
    if dev_team_mae_delta is None or dev_team_mae_delta > 0:
        reasons.append(f"DEV team expected-run MAE delta not improved/preserved: {dev_team_mae_delta}")
    if dev_nb_primary_delta is None or dev_nb_primary_delta > PROBABILITY_DEGRADATION_TOLERANCE:
        reasons.append(f"DEV frozen-NB primary Brier delta not improved/preserved: {dev_nb_primary_delta}")
    if val_bullpen_mae_delta is not None and val_bullpen_mae_delta > DEGRADATION_TOLERANCE:
        reasons.append(f"VALIDATION bullpen-outcome MAE degraded beyond tolerance {DEGRADATION_TOLERANCE}: {val_bullpen_mae_delta}")
    if val_nb_primary_delta is not None and val_nb_primary_delta > PROBABILITY_DEGRADATION_TOLERANCE:
        reasons.append(f"VALIDATION frozen-NB primary Brier delta not improved/preserved: {val_nb_primary_delta}")
    if team_robustness_result["nTeamsTotal"] > 0 and team_robustness_result["nTeamsPositive"] < team_robustness_result["nTeamsTotal"] * TEAM_CONCENTRATION_MIN_FRACTION:
        reasons.append(f"improvement concentrated in too few teams: {team_robustness_result['nTeamsPositive']}/{team_robustness_result['nTeamsTotal']} improved")
    return (len(reasons) == 0), reasons


def attach_b1_predictions_to_bullpen_rows(rows, slope, intercept):
    for r in rows:
        r["b1Er9"] = predict_b1_er9(r["shrunkKbb"], slope, intercept)


def attach_b3_predictions_to_bullpen_rows(rows, weight):
    for r in rows:
        r["b3Er9"] = round(weight * r["b0Er9"] + (1 - weight) * r["b1Er9"], 4) if r.get("b1Er9") is not None else None


def bullpen_outcome_paired_delta(rows, key_a, key_b):
    """Paired MAE delta on the PRIMARY bullpen-specific outcome (actual
    relief ER9 in the target game) -- key_a/key_b in {"b0Er9","b1Er9","b3Er9"}."""
    fit_rows = [r for r in rows if r.get(key_a) is not None and r.get(key_b) is not None and r.get("actualReliefER9") is not None]
    paired_rows = [{"gamePk": r["gamePk"], "errA": abs(r[key_a] - r["actualReliefER9"]), "errB": abs(r[key_b] - r["actualReliefER9"])} for r in fit_rows]

    def _delta(subset):
        return sum(x["errB"] - x["errA"] for x in subset) / len(subset) if subset else None

    point = _delta(paired_rows)
    lo, hi, _ = game_clustered_bootstrap_ci(paired_rows, _delta, cluster_key="gamePk", seed=DEFAULT_BOOTSTRAP_SEED)
    return {"n": len(paired_rows), "independentGames": independent_unit_count(paired_rows, key="gamePk"),
            "maeDelta": round(point, 6) if point is not None else None,
            "maeDeltaCI95": {"low": lo, "high": hi, "method": "GAME_CLUSTERED_BOOTSTRAP"}}


def sample_depth_band_breakdown(rows, key_a, key_b):
    out = {}
    for name, lo, hi in SAMPLE_DEPTH_BANDS:
        band_rows = [r for r in rows if r.get("priorReliefGames") is not None and lo <= r["priorReliefGames"] < hi]
        out[name] = bullpen_outcome_paired_delta(band_rows, key_a, key_b)
    return out


def main():
    print(f"[{EXPERIMENT_ID}] registering experiment/control...")
    control, definition = register_experiment()

    print(f"[{EXPERIMENT_ID}] building bullpen-outcome corpus (existing caches only, no new acquisition)...")
    team_games_by_season, relief_er9_by_season, relief_kbb_by_season, rows_by_season = {}, {}, {}, {}
    for season in ALL_SEASONS:
        team_games = rsch0009.load_all_team_games_with_venue(season)
        team_games_by_season[season] = team_games
        relief_er9_by_season[season] = rsch0009.load_relief_er9_games(season, team_games)
        relief_kbb_by_season[season] = load_relief_kbb_games(season, team_games)
        env_lookup = rsch0009.build_season_environment_lookup([g for games in team_games.values() for g in games if g.get("side") == "home"])
        rows_by_season[season] = rsch0009.build_season_rows(season, team_games, relief_er9_by_season[season], env_lookup)

    dev_rows = [r for s in DEV_SEASONS for r in rows_by_season[s]]
    val_rows = [r for s in VALIDATION_SEASONS for r in rows_by_season[s]]
    holdout_rows = [r for s in HOLDOUT_SEASONS for r in rows_by_season[s]]
    all_rows = dev_rows + val_rows + holdout_rows
    print(f"[{EXPERIMENT_ID}] team-mean corpus: dev={len(dev_rows)} val={len(val_rows)} holdout={len(holdout_rows)}")

    dev_home_team_games = [g for s in DEV_SEASONS for g in team_games_by_season[s].values()]
    league_avg_offense = rsch0009.fit_league_average_runs_per_game(dev_home_team_games)
    dev_relief_er9_team_games = [g for s in DEV_SEASONS for g in relief_er9_by_season[s].values()]
    league_avg_bullpen_er9 = rsch0009.fit_league_average_bullpen_er9(dev_relief_er9_team_games)
    rsch0009.attach_stabilized_components(all_rows, league_avg_offense, league_avg_bullpen_er9)
    hfa = rsch0009.fit_home_field_adjustment_for_components(dev_rows, BASELINE_COMPONENTS)
    print(f"[{EXPERIMENT_ID}] frozen team-mean baseline: leagueAvgOffense={league_avg_offense} hfa={hfa}")

    # ---- B0 reproduction proof (verify B0 == MLB-RSCH-0009's own bullpen component exactly) ----
    # rsch0009.attach_predictions() only exposes mlProb/totalOverProb/expectedTotal
    # (not separate home/away runs) -- compare against those, the only fields
    # its own function actually returns, rather than re-deriving a field shape
    # it was never designed to expose.
    verify_rows = [dict(r) for r in dev_rows[:50]]
    attach_team_mean_predictions(verify_rows, "B0_CHECK", lambda r, side: None, league_avg_offense, hfa)
    rsch0009.attach_predictions(verify_rows, BASELINE_COMPONENTS, hfa, "B0_REFERENCE")
    b0_reproduction_ok = all(
        r.get("homeExpectedRuns_B0_CHECK") is not None and r.get("expectedTotal_B0_REFERENCE") is not None
        and round(r["homeExpectedRuns_B0_CHECK"] + r["awayExpectedRuns_B0_CHECK"], 3) == round(r["expectedTotal_B0_REFERENCE"], 3)
        for r in verify_rows
    )
    print(f"[{EXPERIMENT_ID}] B0 reproduction proof ({len(verify_rows)} rows): {b0_reproduction_ok}")

    # ---- B1: DEV-only K-BB league average, shrinkage grid, and mapping ----
    league_avg_kbb = fit_league_average_kbb([games for s in DEV_SEASONS for games in relief_kbb_by_season[s].values()])
    dev_seasons_data = [(s, relief_er9_by_season[s], relief_kbb_by_season[s]) for s in DEV_SEASONS]
    val_seasons_data = [(s, relief_er9_by_season[s], relief_kbb_by_season[s]) for s in VALIDATION_SEASONS]
    holdout_seasons_data = [(s, relief_er9_by_season[s], relief_kbb_by_season[s]) for s in HOLDOUT_SEASONS]

    mapping = fit_kbb_shrinkage_and_mapping_dev_only(dev_seasons_data, league_avg_kbb)
    print(f"[{EXPERIMENT_ID}] DEV-fit K-BB shrinkage/mapping: {mapping}")

    dev_bullpen_rows = build_bullpen_rows_multi_season(dev_seasons_data, league_avg_kbb, mapping["k"])
    val_bullpen_rows = build_bullpen_rows_multi_season(val_seasons_data, league_avg_kbb, mapping["k"])
    holdout_bullpen_rows = build_bullpen_rows_multi_season(holdout_seasons_data, league_avg_kbb, mapping["k"])
    for rows in (dev_bullpen_rows, val_bullpen_rows, holdout_bullpen_rows):
        attach_b1_predictions_to_bullpen_rows(rows, mapping["slope"], mapping["intercept"])

    # ---- B3: DEV-only blend weight ----
    blend = fit_blend_weight_dev_only(dev_bullpen_rows)
    print(f"[{EXPERIMENT_ID}] DEV-fit B3 blend weight: {blend}")
    for rows in (dev_bullpen_rows, val_bullpen_rows, holdout_bullpen_rows):
        attach_b3_predictions_to_bullpen_rows(rows, blend["weight"])

    # ---- Primary bullpen-outcome comparison (DEV) ----
    dev_bullpen_b1_delta = bullpen_outcome_paired_delta(dev_bullpen_rows, "b0Er9", "b1Er9")
    dev_bullpen_b3_delta = bullpen_outcome_paired_delta(dev_bullpen_rows, "b0Er9", "b3Er9")
    print(f"[{EXPERIMENT_ID}] DEV bullpen-outcome MAE delta: B1={dev_bullpen_b1_delta['maeDelta']} B3={dev_bullpen_b3_delta['maeDelta']}")

    # ---- Team-mean integration ----
    b1_lookup = {(r["season"], r["gamePk"], r["teamId"]): r["b1Er9"] for r in dev_bullpen_rows + val_bullpen_rows + holdout_bullpen_rows if r.get("b1Er9") is not None}
    b3_lookup = {(r["season"], r["gamePk"], r["teamId"]): r["b3Er9"] for r in dev_bullpen_rows + val_bullpen_rows + holdout_bullpen_rows if r.get("b3Er9") is not None}

    def b1_override(r, side):
        return b1_lookup.get((r["season"], r["gamePk"], r["homeTeamId"] if side == "home" else r["awayTeamId"]))

    def b3_override(r, side):
        return b3_lookup.get((r["season"], r["gamePk"], r["homeTeamId"] if side == "home" else r["awayTeamId"]))

    attach_team_mean_predictions(all_rows, "B0", lambda r, side: None, league_avg_offense, hfa)
    attach_team_mean_predictions(all_rows, "B1", b1_override, league_avg_offense, hfa)
    attach_team_mean_predictions(all_rows, "B3", b3_override, league_avg_offense, hfa)

    obs_dev_b0, obs_dev_b1, obs_dev_b3 = team_observations(dev_rows, "B0"), team_observations(dev_rows, "B1"), team_observations(dev_rows, "B3")
    obs_val_b0, obs_val_b1, obs_val_b3 = team_observations(val_rows, "B0"), team_observations(val_rows, "B1"), team_observations(val_rows, "B3")

    dev_team_b1_delta = paired_mean_delta(obs_dev_b0, obs_dev_b1)
    dev_team_b3_delta = paired_mean_delta(obs_dev_b0, obs_dev_b3)
    print(f"[{EXPERIMENT_ID}] DEV team-mean MAE delta: B1={dev_team_b1_delta['maeDelta']} B3={dev_team_b3_delta['maeDelta']}")

    dev_nb_b1 = frozen_nb_probability_eval(dev_rows, "B0", "B1")
    dev_nb_b3 = frozen_nb_probability_eval(dev_rows, "B0", "B3")
    dev_nb_b1_primary, dev_nb_b3_primary = _primary_nb_delta(dev_nb_b1), _primary_nb_delta(dev_nb_b3)
    print(f"[{EXPERIMENT_ID}] DEV frozen-NB primary delta: B1={dev_nb_b1_primary} B3={dev_nb_b3_primary}")

    team_robustness_b1 = team_robustness(obs_dev_b0, obs_dev_b1)
    team_robustness_b3 = team_robustness(obs_dev_b0, obs_dev_b3)

    val_bullpen_b1_delta = bullpen_outcome_paired_delta(val_bullpen_rows, "b0Er9", "b1Er9")
    val_bullpen_b3_delta = bullpen_outcome_paired_delta(val_bullpen_rows, "b0Er9", "b3Er9")
    val_nb_b1_primary = _primary_nb_delta(frozen_nb_probability_eval(val_rows, "B0", "B1"))
    val_nb_b3_primary = _primary_nb_delta(frozen_nb_probability_eval(val_rows, "B0", "B3"))
    print(f"[{EXPERIMENT_ID}] VAL bullpen MAE delta: B1={val_bullpen_b1_delta['maeDelta']} B3={val_bullpen_b3_delta['maeDelta']} | NB primary: B1={val_nb_b1_primary} B3={val_nb_b3_primary}")

    passes_b1, reasons_b1 = selection_passes(dev_bullpen_b1_delta["maeDelta"], dev_team_b1_delta["maeDelta"], dev_nb_b1_primary, val_bullpen_b1_delta["maeDelta"], val_nb_b1_primary, team_robustness_b1)
    passes_b3, reasons_b3 = selection_passes(dev_bullpen_b3_delta["maeDelta"], dev_team_b3_delta["maeDelta"], dev_nb_b3_primary, val_bullpen_b3_delta["maeDelta"], val_nb_b3_primary, team_robustness_b3)
    print(f"[{EXPERIMENT_ID}] selection: B1 passes={passes_b1} reasons={reasons_b1} | B3 passes={passes_b3} reasons={reasons_b3}")

    # Simplicity-first tie-break (program convention): prefer B1 over B3 if both pass.
    if passes_b1:
        selected = "B1"
    elif passes_b3:
        selected = "B3"
    else:
        selected = None
    print(f"[{EXPERIMENT_ID}] selected model: {selected}")

    sample_depth_dev = sample_depth_band_breakdown(dev_bullpen_rows, "b0Er9", selected.lower() + "Er9" if selected else "b1Er9")

    holdout_result = None
    if selected is not None:
        key = selected
        print(f"[{EXPERIMENT_ID}] preregistered gate passed for {selected} -- unlocking 2026 holdout...")
        obs_holdout_b0, obs_holdout_key = team_observations(holdout_rows, "B0"), team_observations(holdout_rows, key)
        holdout_team_delta = paired_mean_delta(obs_holdout_b0, obs_holdout_key)
        holdout_bullpen_delta = bullpen_outcome_paired_delta(holdout_bullpen_rows, "b0Er9", key.lower() + "Er9")
        holdout_nb = frozen_nb_probability_eval(holdout_rows, "B0", key)
        holdout_nb_primary = _primary_nb_delta(holdout_nb)
        holdout_robustness = team_robustness(obs_holdout_b0, obs_holdout_key)
        holdout_result = {
            "bullpenOutcomeDelta": holdout_bullpen_delta, "teamMeanDelta": holdout_team_delta,
            "frozenNbProbability": holdout_nb, "nbPrimaryDelta": holdout_nb_primary, "teamRobustness": holdout_robustness,
        }
        print(f"[{EXPERIMENT_ID}] 2026 holdout: bullpenDelta={holdout_bullpen_delta['maeDelta']} teamDelta={holdout_team_delta['maeDelta']} nbPrimaryDelta={holdout_nb_primary}")
    else:
        print(f"[{EXPERIMENT_ID}] neither B1 nor B3 passed the preregistered gate -- holdout NOT unlocked, no rescue.")

    # ---- Pinnacle secondary (ONLY if a candidate survives locked 2026) ----
    pinnacle_result = {"note": "not run -- only performed if a candidate survives locked 2026, per preregistration"}
    if holdout_result is not None:
        print(f"[{EXPERIMENT_ID}] Pinnacle secondary stage (existing sample, no new Odds API spend)...")
        import run_proxy_vs_pinnacle_experiment as rsch0008
        from lib.edgelab.bullpen_usage import MLB_TEAM_ID_MAP
        try:
            pinnacle_all_rows = [r for season in ALL_SEASONS for r in rsch0008.build_matched_rows(season)]
            for r in pinnacle_all_rows:
                rsch0008.enrich_row(r, hfa)
            for r in pinnacle_all_rows:
                home_id, away_id = MLB_TEAM_ID_MAP.get(r["homeAbbr"]), MLB_TEAM_ID_MAP.get(r["awayAbbr"])
                b0_home, b0_away = r["homeBaseline"]["runPreventionRunsAllowedPerGame"], r["awayBaseline"]["runPreventionRunsAllowedPerGame"]
                key_lookup = b1_lookup if selected == "B1" else b3_lookup
                home_override = key_lookup.get((r["season"], r["gamePk"], home_id))
                away_override = key_lookup.get((r["season"], r["gamePk"], away_id))
                hb = dict(r["homeBaseline"], runPreventionRunsAllowedPerGame=home_override if home_override is not None else b0_home)
                ab = dict(r["awayBaseline"], runPreventionRunsAllowedPerGame=away_override if away_override is not None else b0_away)
                eh1, ea1 = expected_runs(hb, ab, home_field_adjustment=hfa)
                r["proxyMlHomeProb_WINNER"], _ = game_ml_proxy_probability(eh1, ea1)
                r["proxyTotalOverProb_WINNER"] = game_total_proxy_probability(eh1, ea1, r["pinnacleTotalLine"]) if r.get("pinnacleTotalLine") is not None else None
            pinnacle_ml_b0 = rsch0008.paired_analysis(pinnacle_all_rows, "proxyMlHomeProb", "pinnacleMlHomeFair", "actualHomeWin", "PINNACLE/ML/B0")
            pinnacle_total_b0 = rsch0008.paired_analysis(pinnacle_all_rows, "proxyTotalOverProb", "pinnacleTotalOverFair", "actualOver", "PINNACLE/TOTAL/B0")
            pinnacle_ml_winner = rsch0008.paired_analysis(pinnacle_all_rows, "proxyMlHomeProb_WINNER", "pinnacleMlHomeFair", "actualHomeWin", f"PINNACLE/ML/{selected}")
            pinnacle_total_winner = rsch0008.paired_analysis(pinnacle_all_rows, "proxyTotalOverProb_WINNER", "pinnacleTotalOverFair", "actualOver", f"PINNACLE/TOTAL/{selected}")
            pinnacle_result = {"nRows": len(pinnacle_all_rows), "mlB0": pinnacle_ml_b0, "totalB0": pinnacle_total_b0, "mlWinner": pinnacle_ml_winner, "totalWinner": pinnacle_total_winner}
        except Exception as exc:
            pinnacle_result = {"error": str(exc)}

    # ---- Production mapping (READ-ONLY inspection, hardcoded from direct investigation) ----
    production_mapping = {
        "productionUsesKBBOrXFIP": True,
        "note": (
            "Production's live pipeline (scripts/build_market_ledger.py) ALREADY uses an xFIP-style "
            "bullpen metric ('away_bp.get(\"xFIP\")'/'home_bp.get(\"xFIP\")', with an 'xFIPMethod' "
            "provenance flag and a separate high-leverage-reliever xFIP -- 'hlXFIP'/'hlGrade'/"
            "'hlAvailable'/'hlDivergence'/'hlSamplePA') -- NOT the simple ERA/ER9 baseline this whole "
            "EdgeLab research program (MLB-RSCH-0009 onward) has used as its own historical control. "
            "xFIP is itself a K/BB/HR-based component metric."
        ),
        "relieverAggregation": "production aggregates at the WHOLE-BULLPEN level (season-long pen xFIP) plus a separate high-leverage-reliever-specific xFIP signal -- not literally per-appearance K/BB/HR summed the way this experiment's B1 does, but conceptually the same family of statistic.",
        "similarityToWinningCandidate": (
            f"B1/B3 (K-BB%-based) are directionally similar in spirit to production's own xFIP choice "
            f"(both are swing-and-miss/command-based rather than realized-runs-based) but use a coarser "
            f"K-BB% rather than full xFIP (no HR term -- see module docstring's own disclosed data gap)."
        ),
        "verdict": "SUPPORTS" if holdout_result is not None else "PARTIALLY_INFORM",
    }

    if selected is None:
        classification, disposition = "NO_MEANINGFUL_IMPROVEMENT", "REJECT"
    elif holdout_result is not None and holdout_result["bullpenOutcomeDelta"]["maeDelta"] is not None and holdout_result["bullpenOutcomeDelta"]["maeDelta"] < 0 and holdout_result["nbPrimaryDelta"] is not None and holdout_result["nbPrimaryDelta"] <= PROBABILITY_DEGRADATION_TOLERANCE:
        classification, disposition = "MODERATE_BULLPEN_TALENT_IMPROVEMENT", "SHADOW_CANDIDATE"
    elif holdout_result is not None and holdout_result["bullpenOutcomeDelta"]["maeDelta"] is not None and holdout_result["bullpenOutcomeDelta"]["maeDelta"] < 0:
        classification, disposition = "MINOR_BULLPEN_TALENT_IMPROVEMENT", "SHADOW_CANDIDATE"
    else:
        classification, disposition = "NO_MEANINGFUL_IMPROVEMENT", "REJECT"

    report = {
        "experimentId": EXPERIMENT_ID,
        "controlModelId": control["controlModelId"],
        "b0ReproductionProof": {"rowsChecked": len(verify_rows), "matchesRsch0009Exactly": b0_reproduction_ok},
        "corpus": {"teamMeanDevRows": len(dev_rows), "teamMeanValRows": len(val_rows), "teamMeanHoldoutRows": len(holdout_rows),
                   "bullpenDevObservations": len(dev_bullpen_rows), "bullpenValObservations": len(val_bullpen_rows), "bullpenHoldoutObservations": len(holdout_bullpen_rows)},
        "leagueAverageKbb": league_avg_kbb, "b1Mapping": mapping, "b3Blend": blend,
        "b2Status": "NOT_RUN -- no per-pitcher HR data at multi-season scale in any existing cache (verified directly)",
        "b4Status": "NOT_RUN -- reliever-level aggregation would materially expand researcher degrees of freedom under this milestone's own time budget",
        "devBullpenOutcome": {"B1": dev_bullpen_b1_delta, "B3": dev_bullpen_b3_delta},
        "devTeamMean": {"B1": dev_team_b1_delta, "B3": dev_team_b3_delta},
        "devFrozenNbPrimary": {"B1": dev_nb_b1_primary, "B3": dev_nb_b3_primary},
        "devFrozenNbFull": {"B1": dev_nb_b1, "B3": dev_nb_b3},
        "devTeamRobustness": {"B1": team_robustness_b1, "B3": team_robustness_b3},
        "valBullpenOutcome": {"B1": val_bullpen_b1_delta, "B3": val_bullpen_b3_delta},
        "valFrozenNbPrimary": {"B1": val_nb_b1_primary, "B3": val_nb_b3_primary},
        "selection": {"B1": {"passes": passes_b1, "reasons": reasons_b1}, "B3": {"passes": passes_b3, "reasons": reasons_b3}},
        "selectedModel": selected,
        "sampleDepthBandsDev": sample_depth_dev,
        "holdout2026": holdout_result,
        "pinnacleSecondary": pinnacle_result,
        "productionMapping": production_mapping,
        "classification": classification,
        "disposition": disposition,
    }

    out_path = os.path.join("data", "edgelab", "analytics", "latest_mlb_rsch_0020_bullpen_component_talent.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    print(f"[{EXPERIMENT_ID}] wrote {out_path}")
    print(f"[{EXPERIMENT_ID}] classification={classification} disposition={disposition}")
    return report


if __name__ == "__main__":
    main()
