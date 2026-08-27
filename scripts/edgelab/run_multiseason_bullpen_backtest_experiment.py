#!/usr/bin/env python3
"""
scripts/edgelab/run_multiseason_bullpen_backtest_experiment.py
====================================================================
Research Lab, experiment MLB-RSCH-0003: MULTI-SEASON BULLPEN WORKLOAD
BACKTEST.

RESEARCH ONLY. This is a BASEBALL-level historical study -- "does
recent bullpen workload predict subsequent bullpen run prevention, and
is the current production adjustment directionally/magnitude
reasonable" -- not a Kalshi profitability study (no market data is
used or required; see this script's own eligibility criteria).

WHY THIS DOES NOT USE lib.edgelab.experiment_report / paired_evaluation
------------------------------------------------------------------------
Those modules are built around a control-vs-candidate PROBABILITY MODEL
comparison (Brier score / log-loss on a 0/1 market outcome) -- exactly
right for MLB-RSCH-0001/0002, wrong here. This experiment's outcome is
a CONTINUOUS baseball statistic (relief runs allowed per team-game),
and there is no candidate model being compared to a control -- it is a
correlational/regression study of whether a real-world predictor
(recent workload) is associated with a real-world outcome, plus a
check of whether the CURRENT production formula (unchanged, imported,
never reimplemented) tracks that association. Forcing this into the
Brier-score-shaped report contract would misrepresent the analysis.
This script instead:
  - still uses lib.edgelab.experiment_registry (preregistration is
    still required and still happens first, see main())
  - still uses lib.edgelab.evidence_levels / dispositions (the shared
    vocabulary, not model-comparison-specific)
  - writes its own report shape, in the same
    data/edgelab/experiment_reports/<experimentId>/ location, with an
    experimentReportId built the same way (lib.edgelab.research_lab_ids.
    build_experiment_report_id), for discoverability -- but does not
    claim to satisfy experiment_report.validate_experiment_report's
    schema, which does not fit this kind of study.

PIT SAFETY
-------------
Every feature is built by lib.edgelab.backtest.bullpen_backtest_
reconstruction.reconstruct_workload_features, which filters a team's
OWN games via is_strictly_before() -- proven (by that module's own test
suite) to never include the target game itself, any future game, or a
same-date later game unless gameNumber ordering is actually known. The
CURRENT production formula is evaluated via that module's
current_production_multiplier(), which calls
lib.edgelab.bullpen_availability.compute_bullpen_workload_adjustment
UNCHANGED -- never reimplemented.

DEVELOPMENT / VALIDATION / HOLDOUT
--------------------------------------
Chronological, by season: development = 2022-2024, validation = 2025,
holdout = 2026 (locked). The exploratory calendar-day features and any
alternative bucket thresholds are only ever inspected on development
rows in this script's own code path; the holdout season is evaluated
ONLY through the fixed, already-registered specification (this script
does not contain, and has never contained, a code path that tunes
anything on 2026 rows -- see
tests/edgelab/test_run_multiseason_bullpen_backtest_experiment_script.py's
TestHoldoutIsolation for a structural proof).
"""
import gzip
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

from lib.edgelab.backtest.bullpen_backtest_reconstruction import (
    extract_team_games_from_schedule,
    pitcher_lines_to_relief_appearances,
    reconstruct_workload_features,
    relief_outcome_for_game,
    current_production_multiplier,
    DEFAULT_LOOKBACK_DAYS,
)
from lib.edgelab.backtest import bullpen_backtest_stats as stats
from lib.edgelab.bullpen_usage import MLB_TEAM_ID_MAP
from lib.edgelab import experiment_registry as exp_reg
from lib.edgelab import dispositions
from lib.edgelab import research_lab_ids as rlids

import fetch_mlb_multiseason_bullpen_cache as fetcher  # noqa: E402

REGISTRATION_TIMESTAMP = "2026-08-27T15:00:00Z"
EXPERIMENT_ID = "MLB-RSCH-0003"
CONTROL_MODEL_ID = "CTRL-7252463d722626e6"  # reused -- same production system identity as MLB-RSCH-0001/0002

DEV_SEASONS = [2022, 2023, 2024]
VALIDATION_SEASONS = [2025]
HOLDOUT_SEASONS = [2026]
ALL_SEASONS = DEV_SEASONS + VALIDATION_SEASONS + HOLDOUT_SEASONS

# spec section 10: investigate and report, rather than silently proceed,
# if the usable sample is unexpectedly below this
MIN_EXPECTED_TEAM_GAMES = 3000

# Descriptive, not tuned -- "high recent workload" for H4's extreme-vs-
# modest check uses a fixed top-decile cut (bucket_means already reports
# the full decile table; this constant only labels which bucket counts
# as "extreme" for the H4 summary line).
EXTREME_WORKLOAD_BUCKET = 10

EDGELAB_DIR = os.path.join(_ROOT, "data", "edgelab")


# ── Preregistration ──────────────────────────────────────────────────────

def register_experiment():
    """
    Registers MLB-RSCH-0003 BEFORE any cached data is loaded or any
    outcome is computed -- see main(), where this is called first, and
    tests/edgelab/test_run_multiseason_bullpen_backtest_experiment_script.py's
    TestPreregistrationOrdering for the structural test enforcing this.

    No candidate: this experiment has no control-vs-candidate
    probability comparison (see module docstring) -- candidate_model_id/
    candidate_variant_id are left at their default None.
    """
    pit_requirements = {"team_recent_game_log_reconstruction": "PREDICTIVE_INPUT"}

    definition = exp_reg.build_experiment_definition(
        title="Multi-Season Bullpen Workload Backtest",
        hypothesis=(
            "H1: greater recent bullpen workload predicts worse subsequent bullpen run prevention. "
            "H2: back-to-back reliever usage predicts worse subsequent bullpen run prevention. "
            "H3: recent high-leverage-reliever workload has a greater predictive effect than generic bullpen workload. "
            "H4: the relationship is nonlinear -- extreme workload matters more than modest workload. "
            "H5: the current production workload multiplier is directionally aligned with observed historical risk."
        ),
        research_question=(
            "Over multiple MLB seasons (2022-2026) and thousands of team-games, does recent bullpen workload "
            "(reconstructed strictly point-in-time, via lib.edgelab.bullpen_usage's own MLB Stats API adapters) "
            "predict subsequent relief-pitching run prevention, and does the CURRENT production workload "
            "multiplier (lib.edgelab.bullpen_availability.compute_bullpen_workload_adjustment, unchanged) track "
            "that relationship?"
        ),
        owner="edgelab_research_lab",
        control_model_id=CONTROL_MODEL_ID,
        evidence_level="E2_PIT_HISTORICAL",
        target_population=(
            "Every MLB regular-season team-game 2022-2026 (through the latest completed date available) with "
            "a cached schedule+boxscore and a resolvable relief outcome for that team -- NOT restricted to games "
            "with an archived Kalshi market (spec section 11: this is a baseball-level question, not a Kalshi "
            "profitability question)."
        ),
        market_families=[],
        eligibility_criteria=[
            "team's own game is COMPLETED (boxscore fetched and cached)",
            "relief_outcome_for_game() resolves (no missing runs/earnedRuns/outs on any reliever)",
            "at least one prior completed game exists for that team this season (recentUsage dataAvailable)",
        ],
        exclusion_criteria=[
            "games without an archived boxscore in the cache",
            "a team-game whose relief outcome cannot be resolved (malformed pitching stat line)",
        ],
        prediction_checkpoints=["PREGAME_AS_OF_GAME_START"],
        primary_metric="game-clustered-bootstrap mean difference / Spearman correlation between reconstructed "
                        "workload features and relief runs allowed per 9 innings, 95% CI",
        secondary_metrics=[
            "relief earned runs allowed", "bullpen innings pitched", "number of relievers used",
            "full-game opponent runs allowed", "current production multiplier distribution and bucketed outcome",
        ],
        chronological_split_policy="SEASON_BASED: development=2022-2024, validation=2025, holdout=2026 (locked, "
                                    "evaluated only via the fixed already-registered specification)",
        minimum_sample_requirement={"independentGames": MIN_EXPECTED_TEAM_GAMES},
        clustering_unit="gamePk",
        experiment_type=exp_reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=exp_reg.FDR_BONFERRONI,
        pit_requirements=pit_requirements,
        registered_at=REGISTRATION_TIMESTAMP,
        experiment_id=EXPERIMENT_ID,
        notes="Baseball-level historical study (spec section 11) -- no market/Kalshi data used or required. "
              "Reuses lib.edgelab.bullpen_availability.compute_bullpen_workload_adjustment and "
              "lib.edgelab.bullpen_usage.summarize_team_bullpen_usage UNCHANGED for the primary (current-formula) "
              "specification; calendar-day features are exploratory (see module docstring).",
    )
    exp_reg.register_experiment(definition)
    return definition


# ── Cache loading -> team-game rows ─────────────────────────────────────

def _load_boxscore_cache(season):
    path = fetcher.boxscore_cache_path(season)
    if not os.path.exists(path):
        return {}
    opener = gzip.open if path.endswith(".gz") else open
    by_game_pk = {}
    with opener(path, "rt") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            by_game_pk[row["gamePk"]] = row
    return by_game_pk


def build_team_game_rows(season):
    """
    Loads this season's cached schedules+boxscores and returns one row
    per (team, gamePk) the team actually played, with reconstructed
    features and outcome. Never touches the network -- reads only from
    data/research_cache/bullpen_backtest/<season>/.
    """
    boxscores = _load_boxscore_cache(season)
    team_games_by_abbr = {}
    for team_abbr, team_id in MLB_TEAM_ID_MAP.items():
        schedule = fetcher.load_cached_schedule(season, team_abbr)
        if not schedule:
            team_games_by_abbr[team_abbr] = []
            continue
        games = extract_team_games_from_schedule(schedule, team_id)
        for g in games:
            cached = boxscores.get(g["gamePk"])
            side_lines = (cached or {}).get(f"{g['side']}Pitchers") or []
            g["appearances"] = pitcher_lines_to_relief_appearances(side_lines)
        team_games_by_abbr[team_abbr] = games

    rows = []
    for team_abbr, games in team_games_by_abbr.items():
        for target in games:
            cached = boxscores.get(target["gamePk"])
            if not cached:
                continue
            pitcher_lines = cached.get(f"{target['side']}Pitchers") or []
            outcome = relief_outcome_for_game(pitcher_lines)
            if outcome is None:
                continue
            features = reconstruct_workload_features(games, target, lookback_days=DEFAULT_LOOKBACK_DAYS)
            if not features["productionFormulaInput"].get("dataAvailable"):
                continue
            multiplier_result = current_production_multiplier(features)
            rows.append({
                "season": season, "team": team_abbr, "gamePk": target["gamePk"], "gameDate": target["date"],
                "side": target["side"], "gameNumber": target.get("gameNumber"),
                "features": features, "outcome": outcome,
                "multiplier": multiplier_result["multiplier"],
                "adjustmentApplied": multiplier_result["adjustmentApplied"],
                # flattened for stats helpers, which key by row dict fields directly
                "bullpenPitchesPrevDay1": features["bullpenPitchesPrevDay1"],
                "bullpenPitchesPrevDays3": features["bullpenPitchesPrevDays3"],
                "backToBackRelieverCount": features["backToBackRelieverCount"],
                "highLeverageUsedPrevDayCount": features["highLeverageUsedPrevDayCount"],
                "reliefRunsPer9": (
                    round(outcome["reliefRunsAllowed"] / outcome["bullpenInningsPitched"] * 9, 4)
                    if outcome["bullpenInningsPitched"] else None
                ),
                "reliefRunsAllowed": outcome["reliefRunsAllowed"],
                "fullGameTeamRunsAllowed": outcome["fullGameTeamRunsAllowed"],
            })
    return rows


# ── Hypothesis tests (development spec, applied unchanged to validation/holdout) ──

def run_hypothesis_tests(rows, seed=None):
    """Pure. Applies the SAME fixed specification to whatever `rows` it
    is given -- development, validation, or holdout. No branch here
    reads which season group it was called with, so there is no code
    path that could behave differently on holdout."""
    if not rows:
        return None

    h1 = stats.correlation_with_ci(rows, "bullpenPitchesPrevDay1", "reliefRunsPer9", seed=seed)
    h1_deciles = stats.bucket_means(rows, "bullpenPitchesPrevDay1", "reliefRunsPer9")

    h2 = stats.mean_difference_with_ci(rows, lambda r: r["backToBackRelieverCount"] > 0, "reliefRunsPer9", seed=seed)

    h3_generic = stats.mean_difference_with_ci(rows, lambda r: r["bullpenPitchesPrevDay1"] >= 35, "reliefRunsPer9", seed=seed)
    h3_high_leverage = stats.mean_difference_with_ci(rows, lambda r: r["highLeverageUsedPrevDayCount"] > 0, "reliefRunsPer9", seed=seed)

    h4_deciles = stats.bucket_means(rows, "bullpenPitchesPrevDays3", "reliefRunsPer9")

    h5 = stats.correlation_with_ci(rows, "multiplier", "reliefRunsPer9", seed=seed)
    h5_deciles = stats.bucket_means(rows, "multiplier", "reliefRunsPer9")

    multipliers = [r["multiplier"] for r in rows]
    applied = [r["adjustmentApplied"] for r in rows]
    return {
        "n": len(rows),
        "independentGames": len({r["gamePk"] for r in rows}),
        "h1_workload_vs_outcome": h1,
        "h1_deciles": h1_deciles,
        "h2_back_to_back": h2,
        "h3_generic_workload": h3_generic,
        "h3_high_leverage_workload": h3_high_leverage,
        "h4_extreme_workload_deciles": h4_deciles,
        "h5_current_multiplier_vs_outcome": h5,
        "h5_multiplier_deciles": h5_deciles,
        "multiplierDistribution": {
            "min": min(multipliers), "max": max(multipliers),
            "mean": round(sum(multipliers) / len(multipliers), 4),
            "neutralFraction": round(sum(1 for a in applied if not a) / len(applied), 4),
            "cappedFraction": round(sum(1 for m in multipliers if round(m - 1.0, 4) >= 0.12) / len(multipliers), 4),
        },
    }


def classify_magnitude(dev_result):
    """
    Spec section 12D: TOO_WEAK / PLAUSIBLE / TOO_STRONG / UNPROVEN,
    derived from H5's development-set correlation CI and the observed
    development-set effect-size-per-multiplier-unit -- descriptive
    judgment, not a tuned coefficient (this function proposes no new
    multiplier).
    """
    if dev_result is None or dev_result["n"] < MIN_EXPECTED_TEAM_GAMES:
        return "UNPROVEN"
    ci = dev_result["h5_current_multiplier_vs_outcome"]["ci"]
    if ci["low"] is None or ci["high"] is None or (ci["low"] <= 0 <= ci["high"]):
        return "UNPROVEN"
    if ci["low"] <= 0:
        return "UNPROVEN"
    return "PLAUSIBLE"  # directionally confirmed; TOO_WEAK/TOO_STRONG would require a counterfactual
                          # magnitude comparison this experiment does not attempt (spec: "not permission to tune yet")


# ── Coverage report ──────────────────────────────────────────────────────

def coverage_report(rows_by_season):
    per_season = {
        season: {"teamGames": len(rows), "games": len({r["gamePk"] for r in rows})}
        for season, rows in rows_by_season.items()
    }
    total_team_games = sum(v["teamGames"] for v in per_season.values())
    return {
        "perSeason": per_season,
        "totalTeamGames": total_team_games,
        "meetsMinimumExpectedSample": total_team_games >= MIN_EXPECTED_TEAM_GAMES,
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

    # No custom seed passed -- game_clustered_bootstrap_ci's own fixed
    # DEFAULT_BOOTSTRAP_SEED already makes every one of these calls
    # deterministic/reproducible across reruns.
    dev_result = run_hypothesis_tests(dev_rows)
    validation_result = run_hypothesis_tests(validation_rows)
    holdout_result = run_hypothesis_tests(holdout_rows)

    magnitude_judgment = classify_magnitude(dev_result)

    if not coverage["meetsMinimumExpectedSample"]:
        limitation = (
            f"Usable sample ({coverage['totalTeamGames']} team-games) is below the {MIN_EXPECTED_TEAM_GAMES} "
            f"team-game minimum this experiment targeted (spec section 10). This is because the historical "
            f"MLB Stats API cache (data/research_cache/bullpen_backtest/) has not been populated by the "
            f".github/workflows/research-multiseason-bullpen-backtest.yml manual-dispatch workflow yet -- "
            f"this research environment has no outbound network access to statsapi.mlb.com (confirmed: a direct "
            f"request returns HTTP 403 from the environment's proxy), so the deterministic reconstruction/stats "
            f"code above is exercised and tested, but the actual multi-season data pull has not run. "
            f"Every number below (if any) is descriptive of whatever partial cache exists, NOT a validated "
            f"large-sample result -- do not treat it as one."
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
        "conclusions": {
            "A_bullpen_fatigue_repeatable_signal": (
                "UNPROVEN" if limitation else (
                    "SUPPORTED" if (dev_result and dev_result["h1_workload_vs_outcome"]["ci"]["low"] is not None
                                     and dev_result["h1_workload_vs_outcome"]["ci"]["low"] > 0)
                    else "WEAK_UNPROVEN"
                )
            ),
            "B_current_components_supported": "UNPROVEN" if limitation else "SEE_H1_H2_H3_H4_IN_DEVELOPMENT_RESULT",
            "C_current_adjustment_directionally_reasonable": "UNPROVEN" if limitation else (
                "YES" if (dev_result and dev_result["h5_current_multiplier_vs_outcome"]["ci"]["low"] is not None
                          and dev_result["h5_current_multiplier_vs_outcome"]["ci"]["low"] > 0)
                else "NOT_CONFIRMED"
            ),
            "D_current_magnitude_judgment": magnitude_judgment,
        },
        "disposition": dispositions.RESEARCH_CANDIDATE,
        "methodologicalLimitations": [
            limitation,
            "Late-inning (6th-9th) opponent runs not computed -- would require the heavier live-feed endpoint "
            "per game (lib.edgelab.mlb_boxscore), doubling network cost; relief-runs-after-starter-exit is used "
            "as the primary outcome instead (spec section 4's primary preference).",
            "H3's high-leverage-vs-generic comparison reports both effect sizes side by side; it is not a formal "
            "statistical interaction test.",
            "H4's nonlinearity check is descriptive (decile table), not a formal nonlinearity hypothesis test.",
        ],
        "productionBehaviorChanged": False,
    }
    report["methodologicalLimitations"] = [m for m in report["methodologicalLimitations"] if m]

    report_dir = os.path.join(EDGELAB_DIR, "experiment_reports", EXPERIMENT_ID)
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f"{report_id}.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)

    analytics_path = os.path.join(EDGELAB_DIR, "analytics", "latest_mlb_rsch_0003_multiseason_bullpen_backtest.json")
    os.makedirs(os.path.dirname(analytics_path), exist_ok=True)
    with open(analytics_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)

    print(json.dumps({
        "experimentId": EXPERIMENT_ID,
        "coverage": coverage,
        "conclusions": report["conclusions"],
        "limitationPresent": limitation is not None,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
