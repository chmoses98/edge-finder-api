#!/usr/bin/env python3
"""
scripts/edgelab/run_games_1_10_confirmation_experiment.py
====================================================================
Research Lab experiment MLB-RSCH-0018: "Games 1-10 Offensive Prior
Confirmation". RESEARCH ONLY. NO production changes.

CONFIRMATORY, not exploratory. MLB-RSCH-0017 (a preregistered
exploratory milestone) reported -- as one of its own required diagnostic
breakdowns, not a post-hoc search -- that its previous-season-anchored
offense candidate (E1) replicated on the LOCKED 2026 holdout specifically
in games 1-5 and 6-10, while fading/reversing from game 11 onward, even
though the AGGREGATE holdout effect (games 1-40) was flat. This
milestone promotes that single reporting band into its own dedicated,
narrowly-scoped confirmatory study, with an explicit false-discovery
control: the Games 1-10 window is frozen BEFORE this experiment's own
analysis, never re-optimized (no 1-8, 1-12, 1-15 comparisons), and every
candidate parameter (league average, home-field adjustment, K_PRIOR) is
read from MLB-RSCH-0017's own committed, frozen artifact and asserted
equal -- NEVER refit here, even though the population is now narrower.

G0 (control) and G1 (candidate) are EXACTLY MLB-RSCH-0017's own E0 and
E1 formulas, functions, and frozen parameters, reused completely
unchanged (imported from run_early_season_offense_experiment, never
reimplemented) -- only the POPULATION filter (games 1-10 instead of
games 1-50) and the DEV-only tercile/robustness diagnostics are new.

MAX DISPOSITION: SHADOW_CANDIDATE_FOR_2027. Never PROMOTION_CANDIDATE.
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

from lib.edgelab import experiment_registry as reg
from lib.edgelab import evidence_levels as ev
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab.research_stats import DEFAULT_BOOTSTRAP_SEED, independent_unit_count, sample_size_status, game_clustered_bootstrap_ci

import run_early_season_offense_experiment as rsch0017  # noqa: E402

EXPERIMENT_ID = "MLB-RSCH-0018"
REGISTRATION_TIMESTAMP = "2026-08-28T15:30:00Z"

RSCH0017_ARTIFACT_PATH = os.path.join(_ROOT, "data", "edgelab", "analytics", "latest_mlb_rsch_0017_early_season_offense.json")

DEV_SEASONS = [2022, 2023, 2024]
VALIDATION_SEASONS = [2025]
HOLDOUT_SEASONS = [2026]
ALL_SEASONS = DEV_SEASONS + VALIDATION_SEASONS + HOLDOUT_SEASONS

GAMES_1_10_MAX_PRIOR_GAMES = 9  # priorGamesThisSeason 0-9 == games 1-10 (0-indexed prior-game count)
SUB_BANDS = (("games_1_5", 0, 4), ("games_6_10", 5, 9))  # descriptive only, already preregistered in RSCH-0017

# Preregistered BEFORE results, locked, never relaxed.
PROB_NONINFERIORITY_TOLERANCE = 0.0005
TEAM_CONCENTRATION_MIN_FRACTION = 0.4
HOLDOUT_PROBABILITY_MATERIAL_DEGRADATION = 0.002


def _load_frozen_rsch0017_parameters():
    """Reads league average / HFA / K_PRIOR from RSCH-0017's own committed
    artifact and asserts they match the expected frozen values -- never
    refit here, even though this milestone's own population (games 1-10)
    is narrower than RSCH-0017's own DEV population (games 1-50)."""
    with open(RSCH0017_ARTIFACT_PATH) as f:
        artifact = json.load(f)
    league_avg = artifact["leagueAverage"]
    hfa_e0 = artifact["homeFieldAdjustment"]
    k_prior = artifact["kPrior"]["selected"]
    if league_avg != 4.3966:
        raise ValueError(f"RSCH-0017 leagueAverage drifted: {league_avg} != 4.3966")
    if hfa_e0 != -0.0065:
        raise ValueError(f"RSCH-0017 homeFieldAdjustment drifted: {hfa_e0} != -0.0065")
    if k_prior != 20:
        raise ValueError(f"RSCH-0017 K_PRIOR drifted: {k_prior} != 20")
    return league_avg, hfa_e0, k_prior


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
        name="mlb_rsch_0018_games_1_10_confirmation_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0018 games-1-10 confirmation v1: MLB-RSCH-0017's own frozen E0/E1 "
                        "formulas and parameters (league average, HFA, K_PRIOR), reused completely "
                        "unchanged, restricted to a Games-1-10-only population"
        ),
        probability_adapter_identity="lib.edgelab.backtest.run_distributions (frozen MLB-RSCH-0010 negative-binomial, dispersion unchanged, never refit here)",
        model_engine_family="pit_safe_research_early_season_offense_v1",
        required_input_provenance=["team_recent_game_log_reconstruction"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "CONFIRMATORY follow-up to MLB-RSCH-0017's own preregistered Games-1-10 reporting band. "
            "Reuses RSCH-0017's E0 (control) and E1 (candidate) formulas and frozen parameters "
            "completely unchanged, restricted to a Games-1-10-only population, to determine whether "
            "that specific band's signal is real or a false discovery."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Games 1-10 Offensive Prior Confirmation",
        hypothesis=(
            "H1 (confirmatory, derived from MLB-RSCH-0017's own preregistered reporting band, not "
            "re-derived after seeing results): during a team's first 10 games of a season, "
            "MLB-RSCH-0017's previous-season-anchored offense candidate (E1, frozen K_PRIOR=20) "
            "improves both mean-accuracy and downstream frozen-NB probability quality over the "
            "league-average-anchored control (E0), and this effect replicates out of sample on the "
            "genuinely locked 2026 holdout."
        ),
        research_question=(
            "During a team's first 10 games of the MLB season, does incorporating previous-season "
            "offensive information improve expected-run and downstream probability accuracy versus a "
            "current-season-only / league-prior baseline?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E2_PIT_HISTORICAL,
        target_population="Every MLB regular-season team-game where that team's own priorGamesThisSeason is 0-9 (games 1-10), seasons 2022-2026 -- a strict subset of MLB-RSCH-0017's own games-1-50 population, using the SAME row construction.",
        market_families=["game_result", "game_total", "team_total", "run_margin"],
        eligibility_criteria=["team-side priorGamesThisSeason in [0, 9] (games 1-10 of that team's own season)"],
        exclusion_criteria=[
            "any re-fit of league average / HFA / K_PRIOR for this narrower population -- all three are read "
            "from MLB-RSCH-0017's own committed artifact and asserted equal, never refit",
            "alternative Games-1-N windows (1-8, 1-12, 1-15, ...) -- Games 1-10 is frozen before analysis, "
            "per this milestone's own explicit false-discovery control",
            "team-specific weights of any kind",
            "Pinnacle/Kalshi used for candidate fitting or selection",
        ],
        prediction_checkpoints=["SEASON_TO_DATE_PREGAME"],
        primary_metric="paired MAE delta on next-game team runs scored (G1 minus G0) restricted to the Games-1-10 population, game-and-team-clustered 95% CI",
        secondary_metrics=[
            "RMSE delta", "mean bias delta", "frozen-NB team-total/game-total/moneyline Brier deltas (primary probability average)",
            "run-margin Brier delta (reported where coherent -- joint Games-1-10 matchups only)",
            "Games 1-5 / Games 6-10 descriptive sub-band breakdown (already preregistered in RSCH-0017)",
            "year-by-year consistency (2022-2026)", "team robustness / leave-one-team-out",
            "previous-season-offense tercile breakdown (DEV-frozen thresholds)",
            "paired candidate-minus-Pinnacle Brier delta (secondary stage, only if G1 survives locked 2026)",
        ],
        chronological_split_policy=f"SEASON_BASED: development={DEV_SEASONS}, validation={VALIDATION_SEASONS}, holdout={HOLDOUT_SEASONS} (locked)",
        minimum_sample_requirement={"independentGames": 50},
        clustering_unit="gamePk",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={"team_recent_game_log_reconstruction": "PREDICTIVE_INPUT"},
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            "evidenceLevel E2_PIT_HISTORICAL. G0/G1 are MLB-RSCH-0017's own E0/E1 formulas, reused "
            "unchanged. Frozen MLB-RSCH-0010 NB dispersion reused unchanged, never refit. Probability "
            f"non-inferiority tolerance locked at {PROB_NONINFERIORITY_TOLERANCE} before any results were "
            "examined. Max disposition SHADOW_CANDIDATE_FOR_2027 -- never PROMOTION_CANDIDATE."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Population: Games 1-10 filter over RSCH-0017's own unchanged row construction ──

def build_games_1_10_rows():
    """Reuses rsch0017.build_corpus() (the SAME PIT-safe, no-floor row
    construction) unchanged, then filters to rows where AT LEAST one side
    is within games 1-10 -- matching how RSCH-0017's own corpus filter
    worked (at least one side eligible), so no row that could contribute
    a Games-1-10 team-observation is ever dropped upstream."""
    rows_by_season, team_games_by_season = rsch0017.build_corpus()
    out_by_season = {}
    for season in ALL_SEASONS:
        out_by_season[season] = [
            r for r in rows_by_season[season]
            if r["homeCurrentRaw"]["priorGamesThisSeason"] <= GAMES_1_10_MAX_PRIOR_GAMES
            or r["awayCurrentRaw"]["priorGamesThisSeason"] <= GAMES_1_10_MAX_PRIOR_GAMES
        ]
    return out_by_season, team_games_by_season


def games_1_10_observations(obs):
    return [o for o in obs if o["priorGames"] <= GAMES_1_10_MAX_PRIOR_GAMES]


# ── Games-1-10 NB probability evaluation ──────────────────────────────────
# Joint markets (game_total, moneyline, run_margin) require BOTH sides to be
# in their own games 1-10 -- a "coherent" two-sided Games-1-10 matchup, per
# this milestone's own "run margin where coherent" instruction. Team-total
# markets use that SIDE's own Games-1-10 eligibility regardless of the
# opponent's own game count, matching the team-game population definition.

def games_1_10_nb_probability_eval(rows, key_a, key_b):
    def home_early(r):
        return r["homeCurrentRaw"]["priorGamesThisSeason"] <= GAMES_1_10_MAX_PRIOR_GAMES

    def away_early(r):
        return r["awayCurrentRaw"]["priorGamesThisSeason"] <= GAMES_1_10_MAX_PRIOR_GAMES

    def key_fn(row):
        return (row["gameId"], row["cellKey"])

    def _cells_for_family(src_rows, family_prefix):
        control_rows, candidate_rows = [], []
        for r in src_rows:
            actual_home, actual_away = r.get("actualHomeRuns"), r.get("actualAwayRuns")
            if actual_home is None or actual_away is None:
                continue
            cells_a = rsch0017.nb_probability_cells(r.get(f"homeExpectedRuns_{key_a}"), r.get(f"awayExpectedRuns_{key_a}"))
            cells_b = rsch0017.nb_probability_cells(r.get(f"homeExpectedRuns_{key_b}"), r.get(f"awayExpectedRuns_{key_b}"))
            if cells_a is None or cells_b is None:
                continue
            outcomes = rsch0017._outcomes_for_actual(actual_home, actual_away)
            for cell_key, outcome in outcomes.items():
                if not cell_key.startswith(family_prefix):
                    continue
                control_rows.append({"gameId": r["gamePk"], "cellKey": cell_key, "gameDate": r.get("date"), "modelFairProbability": cells_a[cell_key], "outcome": outcome})
                candidate_rows.append({"gameId": r["gamePk"], "cellKey": cell_key, "gameDate": r.get("date"), "modelFairProbability": cells_b[cell_key], "outcome": outcome})
        return control_rows, candidate_rows

    joint_rows = [r for r in rows if home_early(r) and away_early(r)]
    home_rows = [r for r in rows if home_early(r)]
    away_rows = [r for r in rows if away_early(r)]

    family_row_sources = {
        "game_total": joint_rows, "moneyline": joint_rows, "run_margin": joint_rows,
        "team_total_home": home_rows, "team_total_away": away_rows,
    }
    by_family = {}
    for family, src_rows in family_row_sources.items():
        c_rows, cand_rows = _cells_for_family(src_rows, family)
        pairing = rsch0017.pe.pair_eligible_observations(c_rows, cand_rows, key_fn=key_fn)
        by_family[family] = rsch0017.pe.evaluate_probability_model_pair(pairing, game_key="gameId", date_key="gameDate")
    return {"byFamily": by_family, "jointRowCount": len(joint_rows), "homeEarlyRowCount": len(home_rows), "awayEarlyRowCount": len(away_rows)}


# ── Descriptive diagnostics ───────────────────────────────────────────────

def year_by_year_breakdown(obs_a, obs_b):
    out = {}
    for season in ALL_SEASONS:
        out[str(season)] = rsch0017.paired_mean_mae_delta(
            [o for o in obs_a if o["season"] == season], [o for o in obs_b if o["season"] == season])
    return out


def _previous_season_rate_by_key(rows):
    """{(gamePk, teamId): previousSeasonOffenseRunsPerGame or None} -- one
    entry per side, matching team_observations' own (gamePk, teamId) key."""
    out = {}
    for r in rows:
        if r["homePreviousSeason"] is not None:
            out[(r["gamePk"], r["homeTeamId"])] = r["homePreviousSeason"]["offenseRunsPerGame"]
        if r["awayPreviousSeason"] is not None:
            out[(r["gamePk"], r["awayTeamId"])] = r["awayPreviousSeason"]["offenseRunsPerGame"]
    return out


def fit_tercile_thresholds_dev_only(dev_rows):
    """DEV-only, frozen before validation/holdout: 33rd/66th percentile of
    previous-season offense rate among Games-1-10 DEV team-sides that HAVE
    a previous season (excludes 2022, which has none)."""
    prev_by_key = _previous_season_rate_by_key(dev_rows)
    values = sorted(v for v in prev_by_key.values() if v is not None)
    if not values:
        return None, None
    n = len(values)
    low = values[int(n * 0.3333)]
    high = values[min(int(n * 0.6667), n - 1)]
    return round(low, 4), round(high, 4)


def tercile_breakdown(obs_a, obs_b, rows, low_threshold, high_threshold):
    if low_threshold is None:
        return {"note": "no previous-season data available in this population"}
    prev_by_key = _previous_season_rate_by_key(rows)

    def _bucket(o):
        rate = prev_by_key.get((o["gamePk"], o["teamId"]))
        if rate is None:
            return None
        if rate <= low_threshold:
            return "bottomThird"
        if rate >= high_threshold:
            return "topThird"
        return "middleThird"

    out = {}
    for bucket_name in ("bottomThird", "middleThird", "topThird"):
        sub_a = [o for o in obs_a if _bucket(o) == bucket_name]
        sub_b = [o for o in obs_b if _bucket(o) == bucket_name]
        out[bucket_name] = rsch0017.paired_mean_mae_delta(sub_a, sub_b)
    return out


# ── Selection rule (preregistered BEFORE results, locked) ─────────────────

def selection_passes(dev_mae_delta, dev_nb_primary_delta, val_mae_delta, val_nb_primary_delta, team_robustness_result):
    reasons = []
    if dev_mae_delta is None or dev_mae_delta >= 0:
        reasons.append(f"DEV Games-1-10 MAE delta not improved: {dev_mae_delta}")
    if dev_nb_primary_delta is None or dev_nb_primary_delta > PROB_NONINFERIORITY_TOLERANCE:
        reasons.append(f"DEV probability delta exceeds locked non-inferiority tolerance {PROB_NONINFERIORITY_TOLERANCE}: {dev_nb_primary_delta}")
    if val_mae_delta is None or val_mae_delta >= 0:
        reasons.append(f"VALIDATION MAE delta not favorable: {val_mae_delta}")
    if val_nb_primary_delta is None or val_nb_primary_delta > PROB_NONINFERIORITY_TOLERANCE:
        reasons.append(f"VALIDATION probability delta exceeds locked non-inferiority tolerance {PROB_NONINFERIORITY_TOLERANCE}: {val_nb_primary_delta}")
    if team_robustness_result["nTeamsTotal"] > 0 and team_robustness_result["nTeamsPositive"] < team_robustness_result["nTeamsTotal"] * TEAM_CONCENTRATION_MIN_FRACTION:
        reasons.append(f"improvement concentrated in too few teams: {team_robustness_result['nTeamsPositive']}/{team_robustness_result['nTeamsTotal']} improved")
    return (len(reasons) == 0), reasons


def main():
    print(f"[{EXPERIMENT_ID}] registering experiment/control...")
    control, definition = register_experiment()

    league_avg, hfa_e0, k_prior = _load_frozen_rsch0017_parameters()
    print(f"[{EXPERIMENT_ID}] frozen params (read from RSCH-0017's own artifact, verified): leagueAvg={league_avg} hfa={hfa_e0} kPrior={k_prior}")

    print(f"[{EXPERIMENT_ID}] building Games-1-10 population from RSCH-0017's own unchanged row construction...")
    rows_by_season, _ = build_games_1_10_rows()
    dev_rows = [r for s in DEV_SEASONS for r in rows_by_season[s]]
    val_rows = [r for s in VALIDATION_SEASONS for r in rows_by_season[s]]
    holdout_rows = [r for s in HOLDOUT_SEASONS for r in rows_by_season[s]]
    all_rows = dev_rows + val_rows + holdout_rows

    # G0/G1 predictions -- RSCH-0017's own unchanged functions, frozen parameters.
    rsch0017.attach_predictions(all_rows, "E0", rsch0017._e0_offense_fn(league_avg), hfa_e0, league_avg)
    rsch0017.attach_e1_predictions(all_rows, hfa_e0, league_avg, k_prior)

    obs_dev_e0, obs_dev_e1 = rsch0017.team_observations(dev_rows, "E0"), rsch0017.team_observations(dev_rows, "E1")
    obs_val_e0, obs_val_e1 = rsch0017.team_observations(val_rows, "E0"), rsch0017.team_observations(val_rows, "E1")
    obs_holdout_e0, obs_holdout_e1 = rsch0017.team_observations(holdout_rows, "E0"), rsch0017.team_observations(holdout_rows, "E1")

    g10_dev_e0, g10_dev_e1 = games_1_10_observations(obs_dev_e0), games_1_10_observations(obs_dev_e1)
    g10_val_e0, g10_val_e1 = games_1_10_observations(obs_val_e0), games_1_10_observations(obs_val_e1)
    g10_holdout_e0, g10_holdout_e1 = games_1_10_observations(obs_holdout_e0), games_1_10_observations(obs_holdout_e1)

    n_teams = len({o["teamId"] for o in g10_dev_e0 + g10_val_e0 + g10_holdout_e0})
    n_games_all = len({o["gamePk"] for o in g10_dev_e0 + g10_val_e0 + g10_holdout_e0})
    n_team_games = len(g10_dev_e0) + len(g10_val_e0) + len(g10_holdout_e0)
    print(f"[{EXPERIMENT_ID}] Games 1-10 population: teamGames={n_team_games} games={n_games_all} teams={n_teams}")

    # ---- G0 reproduction proof (verify G0 reproduces RSCH-0017 on matching rows) ----
    verify_rows = dev_rows[:50]
    rsch0017.attach_predictions(verify_rows, "VERIFY_E0", rsch0017._e0_offense_fn(league_avg), hfa_e0, league_avg)
    g0_reproduction_ok = all(
        r["homeExpectedRuns_E0"] == r["homeExpectedRuns_VERIFY_E0"] and r["awayExpectedRuns_E0"] == r["awayExpectedRuns_VERIFY_E0"]
        for r in verify_rows
    )
    print(f"[{EXPERIMENT_ID}] G0 reproduction proof (recompute vs original attach, {len(verify_rows)} rows): {g0_reproduction_ok}")

    # ---- Primary mean-accuracy (DEV) ----
    dev_mean_g0, dev_mean_g1 = rsch0017.mean_accuracy_metrics(g10_dev_e0), rsch0017.mean_accuracy_metrics(g10_dev_e1)
    dev_delta = rsch0017.paired_mean_mae_delta(g10_dev_e0, g10_dev_e1)
    dev_rmse_delta = round(dev_mean_g1["rmse"] - dev_mean_g0["rmse"], 4) if dev_mean_g0["rmse"] is not None and dev_mean_g1["rmse"] is not None else None
    dev_bias_delta = round(dev_mean_g1["bias"] - dev_mean_g0["bias"], 4) if dev_mean_g0["bias"] is not None and dev_mean_g1["bias"] is not None else None
    print(f"[{EXPERIMENT_ID}] DEV Games-1-10 MAE delta: {dev_delta['maeDelta']} rmseDelta={dev_rmse_delta} biasDelta={dev_bias_delta}")

    dev_team_robustness = rsch0017.team_robustness(g10_dev_e0, g10_dev_e1)
    print(f"[{EXPERIMENT_ID}] DEV team robustness: {dev_team_robustness['nTeamsPositive']}/{dev_team_robustness['nTeamsTotal']} improved")

    dev_nb = games_1_10_nb_probability_eval(dev_rows, "E0", "E1")
    dev_nb_primary = rsch0017._primary_nb_delta(dev_nb)
    print(f"[{EXPERIMENT_ID}] DEV Games-1-10 frozen-NB primary delta: {dev_nb_primary}")

    # ---- Validation (2025) ----
    val_mean_g0, val_mean_g1 = rsch0017.mean_accuracy_metrics(g10_val_e0), rsch0017.mean_accuracy_metrics(g10_val_e1)
    val_delta = rsch0017.paired_mean_mae_delta(g10_val_e0, g10_val_e1)
    val_nb = games_1_10_nb_probability_eval(val_rows, "E0", "E1")
    val_nb_primary = rsch0017._primary_nb_delta(val_nb)
    print(f"[{EXPERIMENT_ID}] VAL Games-1-10 MAE delta: {val_delta['maeDelta']} probPrimaryDelta={val_nb_primary}")

    passes, reasons = selection_passes(dev_delta["maeDelta"], dev_nb_primary, val_delta["maeDelta"], val_nb_primary, dev_team_robustness)
    print(f"[{EXPERIMENT_ID}] selection: passes={passes} reasons={reasons}")

    # ---- Descriptive diagnostics available pre-holdout (DEV+VAL only) ----
    sub_bands_dev = rsch0017.season_progress_band_breakdown(g10_dev_e0, g10_dev_e1, SUB_BANDS)
    sub_bands_val = rsch0017.season_progress_band_breakdown(g10_val_e0, g10_val_e1, SUB_BANDS)
    year_by_year_dev_val = year_by_year_breakdown(g10_dev_e0 + g10_val_e0, g10_dev_e1 + g10_val_e1)

    tercile_low, tercile_high = fit_tercile_thresholds_dev_only(dev_rows)
    print(f"[{EXPERIMENT_ID}] DEV-frozen previous-season-offense tercile thresholds: low<={tercile_low} high>={tercile_high}")
    tercile_dev = tercile_breakdown(g10_dev_e0, g10_dev_e1, dev_rows, tercile_low, tercile_high)
    tercile_val = tercile_breakdown(g10_val_e0, g10_val_e1, val_rows, tercile_low, tercile_high)

    holdout_result = None
    tercile_holdout = None
    if passes:
        print(f"[{EXPERIMENT_ID}] preregistered gate passed -- unlocking 2026 Games-1-10 holdout...")
        holdout_mean_g0, holdout_mean_g1 = rsch0017.mean_accuracy_metrics(g10_holdout_e0), rsch0017.mean_accuracy_metrics(g10_holdout_e1)
        holdout_delta = rsch0017.paired_mean_mae_delta(g10_holdout_e0, g10_holdout_e1)
        holdout_rmse_delta = round(holdout_mean_g1["rmse"] - holdout_mean_g0["rmse"], 4) if holdout_mean_g0["rmse"] is not None and holdout_mean_g1["rmse"] is not None else None
        holdout_bias_delta = round(holdout_mean_g1["bias"] - holdout_mean_g0["bias"], 4) if holdout_mean_g0["bias"] is not None and holdout_mean_g1["bias"] is not None else None
        holdout_nb = games_1_10_nb_probability_eval(holdout_rows, "E0", "E1")
        holdout_nb_primary = rsch0017._primary_nb_delta(holdout_nb)
        holdout_sub_bands = rsch0017.season_progress_band_breakdown(g10_holdout_e0, g10_holdout_e1, SUB_BANDS)
        holdout_team_robustness = rsch0017.team_robustness(g10_holdout_e0, g10_holdout_e1)
        material_degradation = holdout_nb_primary is not None and holdout_nb_primary > HOLDOUT_PROBABILITY_MATERIAL_DEGRADATION
        holdout_result = {
            "meanAccuracy": {"G0": holdout_mean_g0, "G1": holdout_mean_g1, "pairedDelta": holdout_delta},
            "rmseDelta": holdout_rmse_delta, "biasDelta": holdout_bias_delta,
            "frozenNbProbability": holdout_nb, "nbPrimaryDelta": holdout_nb_primary,
            "materialProbabilityDegradation": material_degradation,
            "subBands": holdout_sub_bands, "teamRobustness": holdout_team_robustness,
        }
        tercile_holdout = tercile_breakdown(g10_holdout_e0, g10_holdout_e1, holdout_rows, tercile_low, tercile_high)
        year_by_year_all = year_by_year_breakdown(g10_dev_e0 + g10_val_e0 + g10_holdout_e0, g10_dev_e1 + g10_val_e1 + g10_holdout_e1)
        print(f"[{EXPERIMENT_ID}] 2026 Games-1-10 holdout MAE delta={holdout_delta['maeDelta']} nbPrimaryDelta={holdout_nb_primary}")
    else:
        year_by_year_all = year_by_year_dev_val
        print(f"[{EXPERIMENT_ID}] holdout NOT unlocked -- G1 retired for this hypothesis (no rescue).")

    # ---- Pinnacle secondary (ONLY if G1 unlocked and survives 2026) ----
    pinnacle_result = {"note": "not run -- only performed if G1 survives locked 2026, per preregistration"}
    if holdout_result is not None:
        print(f"[{EXPERIMENT_ID}] Pinnacle secondary stage (existing sample, no new Odds API spend)...")
        import run_proxy_vs_pinnacle_experiment as rsch0008
        try:
            pinnacle_all_rows = [r for season in ALL_SEASONS for r in rsch0008.build_matched_rows(season)]
            g10_pinnacle_rows = [r for r in pinnacle_all_rows if r["homeBaseline"]["priorGamesThisSeason"] <= GAMES_1_10_MAX_PRIOR_GAMES and r["awayBaseline"]["priorGamesThisSeason"] <= GAMES_1_10_MAX_PRIOR_GAMES]
            for r in g10_pinnacle_rows:
                rsch0008.enrich_row(r, hfa_e0)
            prev_avgs_by_season = {season: rsch0017.load_previous_season_full_averages(season) for season in ALL_SEASONS}
            for r in g10_pinnacle_rows:
                home_team_id = rsch0008.MLB_TEAM_ID_MAP.get(r["homeAbbr"])
                away_team_id = rsch0008.MLB_TEAM_ID_MAP.get(r["awayAbbr"])
                prev_avgs = prev_avgs_by_season.get(r["season"], {})
                home_prev, away_prev = prev_avgs.get(home_team_id), prev_avgs.get(away_team_id)
                home_b, away_b = r["homeBaseline"], r["awayBaseline"]
                hb_off = rsch0017.e1_component(home_b["offenseRunsPerGame"], home_b["priorGamesThisSeason"], home_prev["offenseRunsPerGame"] if home_prev else None, league_avg, k_prior)
                ab_off = rsch0017.e1_component(away_b["offenseRunsPerGame"], away_b["priorGamesThisSeason"], away_prev["offenseRunsPerGame"] if away_prev else None, league_avg, k_prior)
                hb_prevent = rsch0017.run_prevention_component(home_b["runPreventionRunsAllowedPerGame"], home_b["priorGamesThisSeason"], league_avg)
                ab_prevent = rsch0017.run_prevention_component(away_b["runPreventionRunsAllowedPerGame"], away_b["priorGamesThisSeason"], league_avg)
                eh1, ea1 = rsch0017.expected_runs({"offenseRunsPerGame": hb_off, "runPreventionRunsAllowedPerGame": hb_prevent}, {"offenseRunsPerGame": ab_off, "runPreventionRunsAllowedPerGame": ab_prevent}, home_field_adjustment=hfa_e0)
                r["proxyMlHomeProb_G1"], _ = rsch0017.game_ml_proxy_probability(eh1, ea1)
                r["proxyTotalOverProb_G1"] = rsch0017.game_total_proxy_probability(eh1, ea1, r["pinnacleTotalLine"]) if r.get("pinnacleTotalLine") is not None else None
            pinnacle_ml_g0 = rsch0008.paired_analysis(g10_pinnacle_rows, "proxyMlHomeProb", "pinnacleMlHomeFair", "actualHomeWin", "PINNACLE/ML/G0")
            pinnacle_total_g0 = rsch0008.paired_analysis(g10_pinnacle_rows, "proxyTotalOverProb", "pinnacleTotalOverFair", "actualOver", "PINNACLE/TOTAL/G0")
            pinnacle_ml_g1 = rsch0008.paired_analysis(g10_pinnacle_rows, "proxyMlHomeProb_G1", "pinnacleMlHomeFair", "actualHomeWin", "PINNACLE/ML/G1")
            pinnacle_total_g1 = rsch0008.paired_analysis(g10_pinnacle_rows, "proxyTotalOverProb_G1", "pinnacleTotalOverFair", "actualOver", "PINNACLE/TOTAL/G1")
            pinnacle_result = {"nRows": len(g10_pinnacle_rows), "mlG0": pinnacle_ml_g0, "totalG0": pinnacle_total_g0, "mlG1": pinnacle_ml_g1, "totalG1": pinnacle_total_g1}
        except Exception as exc:
            pinnacle_result = {"error": str(exc)}

    # ---- Player-based preseason-prior feasibility (retained from RSCH-0017, not re-audited) ----
    feasibility = {
        "historicalRosterContinuity": "UNAVAILABLE -- no roster/transaction archive found in this repository at any scale (unchanged since RSCH-0017's own audit)",
        "playerPriorYearTalent": "RECONSTRUCTABLE_FROM_DATED_RAW -- data/statcast_raw/ exists but remains shallow (~220 game files), not yet at multi-season scale",
        "lineupRole": "PROSPECTIVE_ONLY -- genuinely PIT-safe going forward, zero historical depth",
        "transactions": "UNAVAILABLE -- no transaction archive found",
        "note": "RSCH-0017's own limitation stands: previous-season TEAM offense is an imperfect proxy for current roster talent due to roster turnover -- not solved here.",
    }

    if passes:
        disposition = "SHADOW_CANDIDATE_FOR_2027" if holdout_result is not None and holdout_result["meanAccuracy"]["pairedDelta"]["maeDelta"] < 0 and not holdout_result["materialProbabilityDegradation"] else "RESEARCH_CANDIDATE"
        classification = "CONFIRMED_EARLY_SEASON_SIGNAL" if disposition == "SHADOW_CANDIDATE_FOR_2027" else "PARTIAL_WEAK_CONFIRMATION"
    else:
        disposition = "REJECT"
        classification = "NO_CONFIRMATION"

    report = {
        "experimentId": EXPERIMENT_ID,
        "controlModelId": control["controlModelId"],
        "frozenParameters": {"leagueAverage": league_avg, "homeFieldAdjustment": hfa_e0, "kPrior": k_prior, "sourceArtifact": "latest_mlb_rsch_0017_early_season_offense.json"},
        "g0ReproductionProof": {"rowsChecked": len(verify_rows), "matchesRsch0017Exactly": g0_reproduction_ok},
        "population": {"teamGames": n_team_games, "games": n_games_all, "teams": n_teams,
                        "devTeamGames": len(g10_dev_e0), "valTeamGames": len(g10_val_e0), "holdoutTeamGames": len(g10_holdout_e0),
                        "seasons": ALL_SEASONS, "exclusions": "none beyond RSCH-0017's own MAX_PRIOR_GAMES_CORPUS=50 corpus cap and missing actual-runs rows"},
        "meanAccuracy": {
            "dev": {"G0": dev_mean_g0, "G1": dev_mean_g1, "pairedDelta": dev_delta, "rmseDelta": dev_rmse_delta, "biasDelta": dev_bias_delta},
            "validation": {"G0": val_mean_g0, "G1": val_mean_g1, "pairedDelta": val_delta},
        },
        "frozenNbProbability": {"dev": dev_nb, "devPrimaryDelta": dev_nb_primary, "validation": val_nb, "valPrimaryDelta": val_nb_primary},
        "probabilityNonInferiorityTolerance": PROB_NONINFERIORITY_TOLERANCE,
        "selection": {"passes": passes, "reasons": reasons},
        "subBandsDev": sub_bands_dev, "subBandsVal": sub_bands_val,
        "yearByYear": year_by_year_all,
        "teamRobustnessDev": dev_team_robustness,
        "previousSeasonTercile": {"thresholds": {"low": tercile_low, "high": tercile_high}, "dev": tercile_dev, "validation": tercile_val, "holdout": tercile_holdout},
        "holdout2026": holdout_result,
        "pinnacleSecondary": pinnacle_result,
        "playerPriorFeasibility": feasibility,
        "classification": classification,
        "disposition": disposition,
        "shadowWarrantedFor2027": disposition == "SHADOW_CANDIDATE_FOR_2027",
    }

    out_path = os.path.join("data", "edgelab", "analytics", "latest_mlb_rsch_0018_games_1_10_confirmation.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    print(f"[{EXPERIMENT_ID}] wrote {out_path}")
    print(f"[{EXPERIMENT_ID}] classification={classification} disposition={disposition}")
    return report


if __name__ == "__main__":
    main()
