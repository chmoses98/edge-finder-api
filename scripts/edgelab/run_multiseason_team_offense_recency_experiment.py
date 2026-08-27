#!/usr/bin/env python3
"""
scripts/edgelab/run_multiseason_team_offense_recency_experiment.py
====================================================================
Research Lab, experiment MLB-RSCH-0005: MULTI-SEASON TEAM OFFENSE
RECENCY / FORM BACKTEST.

RESEARCH ONLY. Baseball-level historical study -- "does a team's recent
offensive performance, relative to its own point-in-time season-to-date
baseline, predict NEXT-GAME scoring after accounting for obvious
PIT-safe context" -- not a Kalshi profitability study (spec: "Market
Relevance -- After Baseball Result"). No market data is used, loaded,
or required.

NO NEW FETCH NEEDED
-----------------------
Every schedule payload already committed under
data/research_cache/bullpen_backtest/<season>/schedules/ (by MLB-RSCH-0003,
reused read-only a THIRD time -- once by MLB-RSCH-0004's boxscore fetcher,
now here) carries both teams' final scores per game. This experiment reads
that cache directly via fetch_mlb_multiseason_bullpen_cache.load_cached_
schedule() and lib.edgelab.backtest.bullpen_backtest_reconstruction.
extract_team_games_from_schedule() (additively extended this milestone
with runsScored/runsAllowed/opponentTeamId -- see that module's own
docstring), both unchanged/reused, not reimplemented. No GitHub Actions
workflow, no network access, no new cache namespace.

PIT SAFETY
-------------
lib.edgelab.backtest.team_offense_recency_reconstruction.
reconstruct_offense_features filters a team's own game list via
is_strictly_before() (imported unchanged from bullpen_backtest_
reconstruction) -- proven by that module's test suite to exclude the
target game itself, every future game, and any same-date later game
unless gameNumber ordering is actually known. A team's first 20
completed games of a season are excluded outright (MIN_PRIOR_GAMES_FOR_
BASELINE), not approximated -- see that module's own docstring for why
this also guarantees every recent-form window (5/10/20) is fillable
whenever a row is eligible at all.

FROZEN CONTROL-VS-CANDIDATE COMPARISON (H4)
------------------------------------------------
A simple ordinary-least-squares regression (lib.edgelab.backtest.
team_offense_recency_stats.ols_fit -- no numpy, closed-form normal
equations, no hyperparameters, no regularization) is fit ONCE on
development rows for two feature sets: CONTROL (season baseline +
opponent baseline + home/away) and CANDIDATE (CONTROL + the three
recent-form deviations). Both frozen coefficient dicts are then applied
UNCHANGED (never refit) to validation and holdout via evaluate_
predictions -- proven by TestFrozenCandidateUnchanged in this script's
test file (object-identity check: the exact same coefs dict is reused
across all three splits).

EXTREME HOT/COLD (H5)
-------------------------
Cutoffs (10th/90th percentile of development-era recentFormDeviation_10)
are computed ONCE from development rows only and frozen -- never
recalculated separately in validation/holdout to maximize an effect,
per the mission's explicit instruction.

DEVELOPMENT / VALIDATION / HOLDOUT
--------------------------------------
Same season-based split as MLB-RSCH-0003/0004: development = 2022-2024,
validation = 2025, holdout = 2026 (locked). run_hypothesis_tests() is
one fixed function applied unchanged to all three groups.
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_SCRIPTS_DIR = os.path.join(_ROOT, "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
_BACKTEST_SCRIPTS_DIR = os.path.join(_SCRIPTS_DIR, "edgelab", "backtest")
if _BACKTEST_SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _BACKTEST_SCRIPTS_DIR)

from lib.edgelab.backtest.bullpen_backtest_reconstruction import extract_team_games_from_schedule
from lib.edgelab.backtest.team_offense_recency_reconstruction import (
    reconstruct_offense_features,
    offense_outcome_for_game,
    RECENT_FORM_WINDOWS,
)
from lib.edgelab.backtest import bullpen_backtest_stats as stats
from lib.edgelab.backtest import team_offense_recency_stats as recency_stats
from lib.edgelab.bullpen_usage import MLB_TEAM_ID_MAP
from lib.edgelab import experiment_registry as exp_reg
from lib.edgelab import dispositions
from lib.edgelab import research_lab_ids as rlids

import fetch_mlb_multiseason_bullpen_cache as fetcher  # noqa: E402

REGISTRATION_TIMESTAMP = "2026-08-27T21:00:00Z"
EXPERIMENT_ID = "MLB-RSCH-0005"
CONTROL_MODEL_ID = "CTRL-7252463d722626e6"  # reused -- same production system identity as prior MLB-RSCH experiments

DEV_SEASONS = [2022, 2023, 2024]
VALIDATION_SEASONS = [2025]
HOLDOUT_SEASONS = [2026]
ALL_SEASONS = DEV_SEASONS + VALIDATION_SEASONS + HOLDOUT_SEASONS

MIN_EXPECTED_TEAM_GAMES = 10000

CONTROL_FEATURES = ["seasonToDateRunsPerGame", "opponentSeasonToDateRunsAllowedPerGame", "isHome"]
CANDIDATE_FEATURES = CONTROL_FEATURES + [f"recentFormDeviation_{w}" for w in RECENT_FORM_WINDOWS]

EXTREME_LOW_PCT = 10
EXTREME_HIGH_PCT = 90
EXTREME_WINDOW = 10  # the middle preregistered window -- used for the single extreme-group analysis (H5)

EDGELAB_DIR = os.path.join(_ROOT, "data", "edgelab")


# ── Preregistration ──────────────────────────────────────────────────────

def register_experiment():
    """
    Registers MLB-RSCH-0005 BEFORE any cached data is loaded or any
    outcome is computed -- first call in main(), structurally enforced
    by this script's own test file's TestPreregistrationOrdering.
    """
    pit_requirements = {"team_recent_game_log_reconstruction": "PREDICTIVE_INPUT"}

    definition = exp_reg.build_experiment_definition(
        title="Multi-Season Team Offense Recency / Form Backtest",
        hypothesis=(
            "H1: 5-game recent-form deviation from season baseline has positive persistence into next-game "
            "scoring. H2: 10-game recent-form deviation has positive persistence. H3: 20-game recent-form "
            "deviation has positive persistence. H4: recency adds predictive information beyond PIT season "
            "baseline and opponent baseline (frozen control-vs-candidate regression comparison). H5: extreme "
            "hot/cold deviations may show stronger persistence than ordinary variation."
        ),
        research_question=(
            "Does recent offensive performance relative to a team's prior season-to-date baseline predict "
            "next-game scoring, after accounting for PIT-safe context, across 2022-2026 and tens of thousands "
            "of team-games -- or is it mostly noise/mean reversion?"
        ),
        owner="edgelab_research_lab",
        control_model_id=CONTROL_MODEL_ID,
        evidence_level="E2_PIT_HISTORICAL",
        target_population=(
            "Every MLB regular-season team-game 2022-2026 (through the latest completed date available), one "
            "row per team entering one game, with at least 20 prior completed games THIS SEASON for that team "
            "(a team's first 20 games of a season have no reliable season-to-date baseline / recent-form window "
            "and are excluded, not approximated). No market/Kalshi data used or required."
        ),
        market_families=[],
        eligibility_criteria=[
            "team has >= 20 prior completed games this season, strictly before the target game",
            "the target game's own runsScored is present (offense_outcome_for_game resolves)",
            "the opponent's own prior-game log is available for the opponent baseline",
        ],
        exclusion_criteria=[
            "a team's first 20 completed games of a season (no reliable baseline/recent-form window)",
            "a game with no cached schedule entry for either team",
        ],
        prediction_checkpoints=["PREGAME_AS_OF_GAME_START"],
        primary_metric="team-clustered bootstrap Spearman correlation between reconstructed recent-form "
                        "deviation (5/10/20-game windows) and next-game runs scored, 95% CI; frozen "
                        "control-vs-candidate regression MAE/RMSE/Poisson-deviance comparison",
        secondary_metrics=[
            "scored 3+/4+/5+ runs indicators", "shutout indicator", "extreme hot/cold group persistence fraction",
        ],
        chronological_split_policy="SEASON_BASED: development=2022-2024, validation=2025, holdout=2026 (locked, "
                                    "evaluated only via the fixed already-registered specification; the frozen "
                                    "regression coefficients and extreme-group percentile cutoffs are fit ONCE on "
                                    "development and reused unchanged, never refit, on validation/holdout)",
        minimum_sample_requirement={"independentGames": MIN_EXPECTED_TEAM_GAMES},
        clustering_unit="team",
        experiment_type=exp_reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=exp_reg.FDR_BONFERRONI,
        pit_requirements=pit_requirements,
        registered_at=REGISTRATION_TIMESTAMP,
        experiment_id=EXPERIMENT_ID,
        notes="Baseball-level historical study -- no market/Kalshi data used or required. No new fetch: reuses "
              "MLB-RSCH-0003's already-committed schedule cache read-only via extract_team_games_from_schedule, "
              "additively extended with runsScored/runsAllowed/opponentTeamId. Fixed windows (5/10/20 games), a "
              "fixed 20-prior-game eligibility minimum, and fixed extreme-group percentile cutoffs (10th/90th of "
              "development's own recentFormDeviation_10 distribution) are all preregistered, never tuned from "
              "observed results.",
    )
    exp_reg.register_experiment(definition)
    return definition


# ── Cache loading -> team-game rows ──────────────────────────────────────

def _load_all_team_games(season):
    """Returns {team_id: sorted team-game list} for every MLB team this
    season, reusing the already-committed schedule cache read-only. No
    network access."""
    games_by_team_id = {}
    for team_abbr, team_id in MLB_TEAM_ID_MAP.items():
        schedule = fetcher.load_cached_schedule(season, team_abbr)
        if not schedule:
            games_by_team_id[team_id] = []
            continue
        games = extract_team_games_from_schedule(schedule, team_id)
        for g in games:
            g["team"] = team_abbr
        games_by_team_id[team_id] = games
    return games_by_team_id


def build_team_game_rows(season):
    """
    Returns one row per eligible (team, target-game): reconstructs
    offense features (season baseline, 5/10/20-game recent form and
    deviation, opponent baseline) and the next-game outcome for every
    team-game with >= MIN_PRIOR_GAMES_FOR_BASELINE prior completed games
    this season.
    """
    games_by_team_id = _load_all_team_games(season)

    rows = []
    for team_id, team_games in games_by_team_id.items():
        for target in team_games:
            opponent_id = target.get("opponentTeamId")
            opponent_games = games_by_team_id.get(opponent_id, [])
            features = reconstruct_offense_features(team_games, opponent_games, target)
            if features is None:
                continue
            outcome = offense_outcome_for_game(target)
            if outcome is None:
                continue
            row = {
                "season": season,
                "team": target["team"],
                "gamePk": target["gamePk"],
                "gameDate": target["date"],
                "isHome": 1.0 if target["side"] == "home" else 0.0,
                "features": features,
                "outcome": outcome,
                "seasonToDateRunsPerGame": features["seasonToDateRunsPerGame"],
                "opponentSeasonToDateRunsAllowedPerGame": features["opponentSeasonToDateRunsAllowedPerGame"],
                "runsScored": outcome["runsScored"],
                "scored3Plus": outcome["scored3Plus"],
                "scored4Plus": outcome["scored4Plus"],
                "scored5Plus": outcome["scored5Plus"],
                "shutout": outcome["shutout"],
            }
            for window in RECENT_FORM_WINDOWS:
                row[f"recentFormRate_{window}"] = features[f"recentFormRate_{window}"]
                row[f"recentFormDeviation_{window}"] = features[f"recentFormDeviation_{window}"]
            rows.append(row)
    return rows


# ── Hypothesis tests (development spec, applied unchanged to validation/holdout) ──

def run_hypothesis_tests(rows):
    """Pure. Applies the SAME fixed specification (H1-H3, persistence
    correlations) to whatever `rows` it is given -- development,
    validation, or holdout. No branch here reads which season group it
    was called with. H4 (frozen regression) and H5 (frozen extreme
    groups) are NOT here -- they require dev-only-fit state passed in
    from main(), computed once and reused, never refit per split."""
    if not rows:
        return None

    result = {
        "n": len(rows),
        "uniqueTeams": len({r["team"] for r in rows}),
        "uniqueGames": len({r["gamePk"] for r in rows}),
    }
    for window in RECENT_FORM_WINDOWS:
        result[f"h_form_deviation_{window}"] = stats.correlation_with_ci(
            rows, f"recentFormDeviation_{window}", "runsScored", cluster_key="team"
        )
    return result


def evaluate_frozen_candidate(rows, control_coefs, candidate_coefs):
    """Applies the FROZEN (development-fit) control/candidate
    coefficient dicts, unchanged, to `rows`. Never refits."""
    if not rows:
        return None
    return {
        "n": len(rows),
        "control": recency_stats.evaluate_predictions(rows, control_coefs, CONTROL_FEATURES, "runsScored"),
        "candidate": recency_stats.evaluate_predictions(rows, candidate_coefs, CANDIDATE_FEATURES, "runsScored"),
    }


def evaluate_frozen_extreme_groups(rows, hot_cutoff, cold_cutoff):
    """Applies FROZEN (development-only) percentile cutoffs, unchanged,
    to `rows`. Never recalculates cutoffs per split."""
    if not rows or hot_cutoff is None or cold_cutoff is None:
        return None
    hot_rows = [r for r in rows if r.get(f"recentFormDeviation_{EXTREME_WINDOW}") is not None and r[f"recentFormDeviation_{EXTREME_WINDOW}"] >= hot_cutoff]
    cold_rows = [r for r in rows if r.get(f"recentFormDeviation_{EXTREME_WINDOW}") is not None and r[f"recentFormDeviation_{EXTREME_WINDOW}"] <= cold_cutoff]
    return {
        "hotCutoff": hot_cutoff,
        "coldCutoff": cold_cutoff,
        "extremeHot": recency_stats.extreme_group_summary(hot_rows, f"recentFormRate_{EXTREME_WINDOW}"),
        "extremeCold": recency_stats.extreme_group_summary(cold_rows, f"recentFormRate_{EXTREME_WINDOW}"),
    }


def classify_signal(dev_result, validation_result, holdout_result, dev_predictive, validation_predictive, holdout_predictive):
    """
    Conservative classification per the mission's allowed labels. A
    "confident positive" H1/H2/H3 finding requires the CI to exclude
    zero on the positive side. Predictive-improvement requires the
    CANDIDATE's frozen MAE to beat CONTROL's frozen MAE on that split
    (H4 -- persistence alone is not "useful" if it doesn't also improve
    prediction accuracy).
    """
    if dev_result is None or dev_result["n"] < MIN_EXPECTED_TEAM_GAMES:
        return "WEAK_UNPROVEN"

    def _confident_positive(ci):
        return ci is not None and ci.get("low") is not None and ci["low"] > 0

    def _confident_negative(ci):
        return ci is not None and ci.get("high") is not None and ci["high"] < 0

    dev_cis = [dev_result[f"h_form_deviation_{w}"]["ci"] for w in RECENT_FORM_WINDOWS]
    dev_positive = [_confident_positive(ci) for ci in dev_cis]
    dev_negative = [_confident_negative(ci) for ci in dev_cis]

    def _predictive_improvement(predictive):
        if not predictive or not predictive.get("control") or not predictive.get("candidate"):
            return False
        return predictive["candidate"]["mae"] < predictive["control"]["mae"]

    if any(dev_negative) and not any(dev_positive):
        return "MEAN_REVERSION_SIGNAL"
    if not any(dev_positive):
        return "NO_USEFUL_SIGNAL"

    def _confident_any(result):
        if result is None:
            return False
        return any(_confident_positive(result[f"h_form_deviation_{w}"]["ci"]) for w in RECENT_FORM_WINDOWS)

    val_confident = _confident_any(validation_result) and _predictive_improvement(validation_predictive)
    hold_confident = _confident_any(holdout_result) and _predictive_improvement(holdout_predictive)

    if val_confident and hold_confident:
        return "STRONG_REPEATABLE_SIGNAL"
    if val_confident or hold_confident:
        return "PARTIAL_CONDITIONAL_SIGNAL"
    return "WEAK_UNPROVEN"


# ── Coverage report ──────────────────────────────────────────────────────

def coverage_report(rows_by_season):
    per_season = {
        season: {"teamGames": len(rows), "uniqueTeams": len({r["team"] for r in rows}), "games": len({r["gamePk"] for r in rows})}
        for season, rows in rows_by_season.items()
    }
    total = sum(v["teamGames"] for v in per_season.values())
    return {
        "perSeason": per_season,
        "totalTeamGames": total,
        "meetsMinimumExpectedSample": total >= MIN_EXPECTED_TEAM_GAMES,
        "minimumExpected": MIN_EXPECTED_TEAM_GAMES,
    }


# ── main ──────────────────────────────────────────────────────────────────

def main():
    experiment = register_experiment()

    rows_by_season = {season: build_team_game_rows(season) for season in ALL_SEASONS}
    coverage = coverage_report(rows_by_season)

    dev_rows = [r for s in DEV_SEASONS for r in rows_by_season.get(s, [])]
    validation_rows = [r for s in VALIDATION_SEASONS for r in rows_by_season.get(s, [])]
    holdout_rows = [r for s in HOLDOUT_SEASONS for r in rows_by_season.get(s, [])]

    dev_result = run_hypothesis_tests(dev_rows)
    validation_result = run_hypothesis_tests(validation_rows)
    holdout_result = run_hypothesis_tests(holdout_rows)

    # H4: fit ONCE on development, freeze, apply unchanged to all three splits.
    control_coefs = recency_stats.ols_fit(dev_rows, CONTROL_FEATURES, "runsScored")
    candidate_coefs = recency_stats.ols_fit(dev_rows, CANDIDATE_FEATURES, "runsScored")
    dev_predictive = evaluate_frozen_candidate(dev_rows, control_coefs, candidate_coefs)
    validation_predictive = evaluate_frozen_candidate(validation_rows, control_coefs, candidate_coefs)
    holdout_predictive = evaluate_frozen_candidate(holdout_rows, control_coefs, candidate_coefs)

    # H5: cutoffs computed ONCE from development only, frozen.
    dev_deviations = [r[f"recentFormDeviation_{EXTREME_WINDOW}"] for r in dev_rows if r.get(f"recentFormDeviation_{EXTREME_WINDOW}") is not None]
    hot_cutoff = recency_stats.percentile(dev_deviations, EXTREME_HIGH_PCT)
    cold_cutoff = recency_stats.percentile(dev_deviations, EXTREME_LOW_PCT)
    dev_extreme = evaluate_frozen_extreme_groups(dev_rows, hot_cutoff, cold_cutoff)
    validation_extreme = evaluate_frozen_extreme_groups(validation_rows, hot_cutoff, cold_cutoff)
    holdout_extreme = evaluate_frozen_extreme_groups(holdout_rows, hot_cutoff, cold_cutoff)

    signal_classification = classify_signal(
        dev_result, validation_result, holdout_result, dev_predictive, validation_predictive, holdout_predictive
    )

    if not coverage["meetsMinimumExpectedSample"]:
        limitation = (
            f"Usable sample ({coverage['totalTeamGames']} team-games) is below the {MIN_EXPECTED_TEAM_GAMES} "
            f"minimum this experiment targeted. Every number below (if any) is descriptive of whatever partial "
            f"cache exists, NOT a validated large-sample result."
        )
    else:
        limitation = None

    report_id = rlids.build_experiment_report_id(EXPERIMENT_ID, CONTROL_MODEL_ID, None, REGISTRATION_TIMESTAMP)
    report = {
        "experimentReportId": report_id,
        "experimentId": EXPERIMENT_ID,
        "controlModelId": CONTROL_MODEL_ID,
        "evidenceLevel": experiment["evidenceLevel"],
        "generatedAt": REGISTRATION_TIMESTAMP,
        "coverage": coverage,
        "development": dev_result,
        "validation": validation_result,
        "holdout": holdout_result,
        "developmentPredictive": dev_predictive,
        "validationPredictive": validation_predictive,
        "holdoutPredictive": holdout_predictive,
        "controlCoefficients": control_coefs,
        "candidateCoefficients": candidate_coefs,
        "developmentExtremeGroups": dev_extreme,
        "validationExtremeGroups": validation_extreme,
        "holdoutExtremeGroups": holdout_extreme,
        "signalClassification": signal_classification,
        "disposition": dispositions.RESEARCH_CANDIDATE,
        "methodologicalLimitations": [
            limitation,
            "A team's first 20 completed games of each season are excluded (no reliable season-to-date baseline "
            "or recent-form window) -- not approximated with a fabricated prior-game value.",
            "Component measures (hits, walks, strikeouts, extra-base hits) not implemented -- the reused schedule "
            "cache carries only final scores per game, not team batting lines; adding those would require a new "
            "team-level batting boxscore fetch, out of scope per the mission's 'do not let this become a giant "
            "new feature-building project' instruction. Primary analysis (runs scored) uses no new fetch at all.",
            "Starting-pitcher identity/quality was NOT included as a robustness control in this pass -- kept out "
            "per the same efficiency instruction; a candidate for a future, separately registered experiment.",
            "H4's frozen regression is a simple closed-form OLS (no regularization, no interaction terms, no "
            "nonlinear terms) -- a deliberately simple, non-tuned baseline-vs-candidate comparison, not a "
            "production-grade scoring model.",
            "First-five-innings / inning-level normalization not implemented -- the reused schedule cache carries "
            "only final game scores.",
        ],
        "productionBehaviorChanged": False,
    }
    report["methodologicalLimitations"] = [m for m in report["methodologicalLimitations"] if m]

    report_dir = os.path.join(EDGELAB_DIR, "experiment_reports", EXPERIMENT_ID)
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f"{report_id}.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)

    analytics_path = os.path.join(EDGELAB_DIR, "analytics", "latest_mlb_rsch_0005_team_offense_recency_backtest.json")
    os.makedirs(os.path.dirname(analytics_path), exist_ok=True)
    with open(analytics_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)

    print(json.dumps({
        "experimentId": EXPERIMENT_ID,
        "coverage": coverage,
        "signalClassification": signal_classification,
        "limitationPresent": limitation is not None,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
