#!/usr/bin/env python3
"""
scripts/edgelab/run_multiseason_starter_workload_experiment.py
====================================================================
Research Lab, experiment MLB-RSCH-0004: MULTI-SEASON STARTER WORKLOAD
/ REST BACKTEST.

RESEARCH ONLY. Baseball-level historical study -- "does starting-
pitcher recent workload/rest before Game N predict performance in Game
N, after accounting for pitcher baseline quality" -- not a Kalshi
profitability study (spec section: "Market Relevance — Secondary
Only"). No market data is used, loaded, or required.

WHY THIS DOES NOT USE lib.edgelab.experiment_report / paired_evaluation
------------------------------------------------------------------------
Same reasoning as MLB-RSCH-0003 (see
scripts/edgelab/run_multiseason_bullpen_backtest_experiment.py's own
module docstring): this is a correlational/regression study of a
continuous baseball outcome, not a control-vs-candidate probability
model comparison. Preregistration still goes through
lib.edgelab.experiment_registry; the report is this script's own shape,
written to the same data/edgelab/experiment_reports/<experimentId>/
location for discoverability.

PIT SAFETY
-------------
Every feature is built by lib.edgelab.backtest.
starter_workload_reconstruction.reconstruct_starter_features, which
filters a PITCHER's own start history (indexed by playerId, not team --
correctly handles a mid-season trade) via
lib.edgelab.backtest.bullpen_backtest_reconstruction.is_strictly_before
-- proven (by that module's own test suite, reused unchanged) to never
include the target start itself, any future start, or a same-date
later start unless gameNumber ordering is actually known.

PITCHER BASELINE QUALITY (spec's "critical confounding control")
-----------------------------------------------------------------
`ownBaselineRunsPer9` is a transparent, PIT-safe, non-tuned
within-pitcher baseline: that pitcher's own mean earned-run rate across
their OWN prior starts this season (strictly before the target start).
H5 tests the workload/rest predictors against the RESIDUAL outcome
(actual minus this baseline) -- a simple pitcher-fixed-effect
approximation (de-meaning), not a fitted model. No current-season
Savant/production aggregate is imported (would be UNAVAILABLE_
HISTORICALLY per the Milestone 2 PIT audit) -- only this study's own
prior-start data is used.

DEVELOPMENT / VALIDATION / HOLDOUT
--------------------------------------
Same season-based split as MLB-RSCH-0003: development = 2022-2024,
validation = 2025, holdout = 2026 (locked). run_hypothesis_tests()
is one fixed function applied unchanged to all three groups -- see
tests/edgelab/test_run_multiseason_starter_workload_experiment_script.py's
TestHoldoutIsolation.
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

from lib.edgelab.backtest.starter_workload_reconstruction import (
    build_pitcher_start_index,
    reconstruct_starter_features,
    starter_outcome_for_start,
    HIGH_PITCH_COUNT_THRESHOLD,
    REST_SHORT,
)
from lib.edgelab.backtest import bullpen_backtest_stats as stats
from lib.edgelab import experiment_registry as exp_reg
from lib.edgelab import dispositions
from lib.edgelab import research_lab_ids as rlids

import fetch_mlb_starter_workload_cache as fetcher  # noqa: E402

REGISTRATION_TIMESTAMP = "2026-08-27T20:00:00Z"
EXPERIMENT_ID = "MLB-RSCH-0004"
CONTROL_MODEL_ID = "CTRL-7252463d722626e6"  # reused -- same production system identity as prior MLB-RSCH experiments

DEV_SEASONS = [2022, 2023, 2024]
VALIDATION_SEASONS = [2025]
HOLDOUT_SEASONS = [2026]
ALL_SEASONS = DEV_SEASONS + VALIDATION_SEASONS + HOLDOUT_SEASONS

MIN_EXPECTED_PITCHER_STARTS = 3000

EDGELAB_DIR = os.path.join(_ROOT, "data", "edgelab")


# ── Preregistration ──────────────────────────────────────────────────────

def register_experiment():
    """
    Registers MLB-RSCH-0004 BEFORE any cached data is loaded or any
    outcome is computed -- first call in main(), structurally enforced
    by tests/edgelab/test_run_multiseason_starter_workload_experiment_script.py's
    TestPreregistrationOrdering.
    """
    pit_requirements = {"team_recent_game_log_reconstruction": "PREDICTIVE_INPUT"}

    definition = exp_reg.build_experiment_definition(
        title="Multi-Season Starter Workload / Rest Backtest",
        hypothesis=(
            "H1: short rest is associated with worse next-start performance. "
            "H2: higher previous-start pitch count is associated with worse next-start performance. "
            "H3: higher rolling 2-3 start workload is associated with worse next-start performance. "
            "H4: effects are nonlinear -- extreme workload matters more than moderate workload. "
            "H5: rest and workload effects remain after accounting for pitcher baseline quality."
        ),
        research_question=(
            "Does starter workload/rest before Game N predict starter performance in Game N, after "
            "accounting for pitcher baseline quality, across 2022-2026 and thousands of pitcher-starts?"
        ),
        owner="edgelab_research_lab",
        control_model_id=CONTROL_MODEL_ID,
        evidence_level="E2_PIT_HISTORICAL",
        target_population=(
            "Every MLB regular-season pitcher-start 2022-2026 (through the latest completed date available) "
            "with a cached boxscore, a confidently-known starter identity, and at least one prior start THIS "
            "SEASON for that pitcher (a pitcher's first start of a season has no valid rest/previous-start "
            "feature and is excluded, not approximated). No market/Kalshi data used or required."
        ),
        market_families=[],
        eligibility_criteria=[
            "starter identity resolvable (a non-empty pitchers list with orderIndex 0 present)",
            "starter_outcome_for_start() resolves (no missing runs/earnedRuns/outs, outs > 0)",
            "at least one prior start THIS SEASON for that pitcher, strictly before the target start",
        ],
        exclusion_criteria=[
            "games without an archived boxscore in the starter_workload cache",
            "a pitcher-start whose outcome cannot be resolved (malformed pitching stat line)",
            "a pitcher's first start of a season (no valid rest/previous-start feature)",
        ],
        prediction_checkpoints=["PREGAME_AS_OF_GAME_START"],
        primary_metric="game/pitcher-clustered bootstrap mean difference / Spearman correlation between "
                        "reconstructed rest/workload features and starter earned runs allowed per 9 innings, "
                        "95% CI",
        secondary_metrics=[
            "starter runs allowed", "innings pitched", "strikeouts", "walks", "hits allowed",
            "WHIP-like outcome", "probability of completing 5 innings", "pitcher-baseline-adjusted residual outcome",
        ],
        chronological_split_policy="SEASON_BASED: development=2022-2024, validation=2025, holdout=2026 (locked, "
                                    "evaluated only via the fixed already-registered specification)",
        minimum_sample_requirement={"independentGames": MIN_EXPECTED_PITCHER_STARTS},
        clustering_unit="playerId",
        experiment_type=exp_reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=exp_reg.FDR_BONFERRONI,
        pit_requirements=pit_requirements,
        registered_at=REGISTRATION_TIMESTAMP,
        experiment_id=EXPERIMENT_ID,
        notes="Baseball-level historical study -- no market/Kalshi data used or required. Reuses "
              "lib.edgelab.backtest.bullpen_backtest_reconstruction.is_strictly_before unchanged for the leakage "
              "guard, indexed per-pitcher (playerId) rather than per-team. Fixed thresholds (SHORT_REST_MAX_DAYS=4, "
              "HIGH_PITCH_COUNT_THRESHOLD=100, STRESSFUL_PITCHES_PER_OUT_THRESHOLD=4.5) are baseball-conventional, "
              "preregistered, never tuned from observed results.",
    )
    exp_reg.register_experiment(definition)
    return definition


# ── Cache loading -> pitcher-start rows ──────────────────────────────────

def _load_boxscore_cache(season):
    path = fetcher.boxscore_cache_path(season)
    if not os.path.exists(path):
        return []
    opener = gzip.open if path.endswith(".gz") else open
    games = []
    with opener(path, "rt") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            games.append(row)
    return games


def _load_all_team_games_with_dates(season):
    """Merges the reused schedule cache's date/gameNumber/doubleHeader
    metadata onto this season's starter_workload boxscore cache records
    (which carry only gamePk + pitcher lines) -- one merged game list,
    de-duplicated by gamePk."""
    from lib.edgelab.backtest.bullpen_backtest_reconstruction import extract_team_games_from_schedule
    from lib.edgelab.bullpen_usage import MLB_TEAM_ID_MAP

    boxscores_by_pk = {g["gamePk"]: g for g in _load_boxscore_cache(season)}
    game_meta_by_pk = {}
    for team_abbr, team_id in MLB_TEAM_ID_MAP.items():
        schedule = fetcher.load_reused_schedule(season, team_abbr)
        if not schedule:
            continue
        for g in extract_team_games_from_schedule(schedule, team_id):
            pk = g.get("gamePk")
            if pk is not None and pk not in game_meta_by_pk:
                game_meta_by_pk[pk] = {"date": g["date"], "gameNumber": g.get("gameNumber"), "doubleHeader": g.get("doubleHeader")}

    merged = []
    for pk, box in boxscores_by_pk.items():
        meta = game_meta_by_pk.get(pk)
        if not meta:
            continue
        merged.append({
            "gamePk": pk, "date": meta["date"], "gameNumber": meta["gameNumber"], "doubleHeader": meta["doubleHeader"],
            "awayPitchers": box.get("awayPitchers") or [], "homePitchers": box.get("homePitchers") or [],
        })
    return merged


def build_pitcher_start_rows(season):
    """
    Returns one row per eligible pitcher-start for `season`: merges the
    reused schedule metadata with the starter_workload boxscore cache,
    builds the per-pitcher start index, and reconstructs features +
    outcome for every start with a valid prior-start-this-season.
    """
    all_games = _load_all_team_games_with_dates(season)
    by_pitcher = build_pitcher_start_index(all_games)
    season_start_date = f"{season}-01-01"

    rows = []
    for player_id, starts in by_pitcher.items():
        for target in starts:
            features = reconstruct_starter_features(starts, target, season_start_date)
            if features is None:
                continue
            outcome = starter_outcome_for_start(target["pitcherLine"])
            if outcome is None:
                continue
            residual_outcome = (
                round(outcome["starterEarnedRunsPer9"] - features["ownBaselineRunsPer9"], 4)
                if features["ownBaselineRunsPer9"] is not None else None
            )
            rows.append({
                "season": season, "playerId": player_id, "gamePk": target["gamePk"], "gameDate": target["date"],
                "team": target["team"], "features": features, "outcome": outcome,
                "shortRest": features["restCategory"] == REST_SHORT,
                "highPitchCountPreviousStart": features["highPitchCountPreviousStart"],
                "pitchesOverPrior2Starts": features["pitchesOverPrior2Starts"],
                "pitchesOverPrior3Starts": features["pitchesOverPrior3Starts"],
                "daysSincePreviousStart": features["daysSincePreviousStart"],
                "starterEarnedRunsPer9": outcome["starterEarnedRunsPer9"],
                "starterRunsPer9": outcome["starterRunsPer9"],
                "completedFiveInnings": 1.0 if outcome["completedFiveInnings"] else 0.0,
                "residualEarnedRunsPer9": residual_outcome,
            })
    return rows


# ── Hypothesis tests (development spec, applied unchanged to validation/holdout) ──

def run_hypothesis_tests(rows):
    """Pure. Applies the SAME fixed specification to whatever `rows` it
    is given -- development, validation, or holdout. No branch here
    reads which season group it was called with."""
    if not rows:
        return None

    h1 = stats.mean_difference_with_ci(rows, lambda r: r["shortRest"], "starterEarnedRunsPer9", cluster_key="playerId")
    h2 = stats.mean_difference_with_ci(rows, lambda r: r["highPitchCountPreviousStart"], "starterEarnedRunsPer9", cluster_key="playerId")
    h3_2start = stats.correlation_with_ci(rows, "pitchesOverPrior2Starts", "starterEarnedRunsPer9", cluster_key="playerId")
    h3_3start = stats.correlation_with_ci(rows, "pitchesOverPrior3Starts", "starterEarnedRunsPer9", cluster_key="playerId")
    h4_deciles = stats.bucket_means(rows, "pitchesOverPrior3Starts", "starterEarnedRunsPer9")

    residual_rows = [r for r in rows if r["residualEarnedRunsPer9"] is not None]
    h5_short_rest = stats.mean_difference_with_ci(residual_rows, lambda r: r["shortRest"], "residualEarnedRunsPer9", cluster_key="playerId")
    h5_high_pitch = stats.mean_difference_with_ci(residual_rows, lambda r: r["highPitchCountPreviousStart"], "residualEarnedRunsPer9", cluster_key="playerId")

    five_innings_short_rest = stats.mean_difference_with_ci(rows, lambda r: r["shortRest"], "completedFiveInnings", cluster_key="playerId")

    return {
        "n": len(rows),
        "uniquePitchers": len({r["playerId"] for r in rows}),
        "uniqueGames": len({r["gamePk"] for r in rows}),
        "h1_short_rest": h1,
        "h2_previous_start_high_pitch_count": h2,
        "h3_rolling_2start_workload": h3_2start,
        "h3_rolling_3start_workload": h3_3start,
        "h4_extreme_workload_deciles": h4_deciles,
        "h5_short_rest_baseline_adjusted": h5_short_rest,
        "h5_high_pitch_count_baseline_adjusted": h5_high_pitch,
        "five_innings_probability_short_rest": five_innings_short_rest,
    }


def classify_signal(dev_result, validation_result, holdout_result):
    """
    Conservative classification per the mission's allowed labels.
    STRONG_REPEATABLE_SIGNAL requires confident (CI-excludes-zero)
    findings in ALL THREE groups, same direction. PARTIAL_CONDITIONAL
    requires development confident AND at least one of
    validation/holdout confident, same direction.
    """
    if dev_result is None or dev_result["n"] < MIN_EXPECTED_PITCHER_STARTS:
        return "WEAK_UNPROVEN"

    def _confident_positive(ci):
        return ci["low"] is not None and ci["low"] > 0

    dev_positive = [
        _confident_positive(dev_result["h1_short_rest"]["ci"]),
        _confident_positive(dev_result["h2_previous_start_high_pitch_count"]["ci"]),
    ]
    if not any(dev_positive):
        return "NO_USEFUL_SIGNAL"

    val_confident = validation_result and (
        _confident_positive(validation_result["h1_short_rest"]["ci"])
        or _confident_positive(validation_result["h2_previous_start_high_pitch_count"]["ci"])
    )
    hold_confident = holdout_result and (
        _confident_positive(holdout_result["h1_short_rest"]["ci"])
        or _confident_positive(holdout_result["h2_previous_start_high_pitch_count"]["ci"])
    )
    if val_confident and hold_confident:
        return "STRONG_REPEATABLE_SIGNAL"
    if val_confident or hold_confident:
        return "PARTIAL_CONDITIONAL_SIGNAL"
    return "WEAK_UNPROVEN"


# ── Coverage report ──────────────────────────────────────────────────────

def coverage_report(rows_by_season):
    per_season = {
        season: {"pitcherStarts": len(rows), "uniquePitchers": len({r["playerId"] for r in rows}),
                  "games": len({r["gamePk"] for r in rows})}
        for season, rows in rows_by_season.items()
    }
    total = sum(v["pitcherStarts"] for v in per_season.values())
    return {
        "perSeason": per_season,
        "totalPitcherStarts": total,
        "meetsMinimumExpectedSample": total >= MIN_EXPECTED_PITCHER_STARTS,
        "minimumExpected": MIN_EXPECTED_PITCHER_STARTS,
    }


# ── main ──────────────────────────────────────────────────────────────────

def main():
    experiment = register_experiment()

    rows_by_season = {season: build_pitcher_start_rows(season) for season in ALL_SEASONS}
    coverage = coverage_report(rows_by_season)

    dev_rows = [r for s in DEV_SEASONS for r in rows_by_season.get(s, [])]
    validation_rows = [r for s in VALIDATION_SEASONS for r in rows_by_season.get(s, [])]
    holdout_rows = [r for s in HOLDOUT_SEASONS for r in rows_by_season.get(s, [])]

    dev_result = run_hypothesis_tests(dev_rows)
    validation_result = run_hypothesis_tests(validation_rows)
    holdout_result = run_hypothesis_tests(holdout_rows)

    signal_classification = classify_signal(dev_result, validation_result, holdout_result)

    if not coverage["meetsMinimumExpectedSample"]:
        limitation = (
            f"Usable sample ({coverage['totalPitcherStarts']} pitcher-starts) is below the "
            f"{MIN_EXPECTED_PITCHER_STARTS} minimum this experiment targeted. Every number below (if any) is "
            f"descriptive of whatever partial cache exists, NOT a validated large-sample result."
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
        "signalClassification": signal_classification,
        "disposition": dispositions.RESEARCH_CANDIDATE,
        "methodologicalLimitations": [
            limitation,
            "First start of each pitcher's season excluded (no valid rest/previous-start feature) -- not "
            "approximated with a fabricated prior-start value.",
            "First-five-innings TEAM runs allowed not implemented -- not directly derivable from per-pitcher "
            "aggregate boxscore stats (would need an inning-by-inning line score).",
            "H4's nonlinearity check is descriptive (decile table), not a formal nonlinearity hypothesis test.",
            "H5's pitcher-baseline adjustment is a simple within-pitcher de-meaning (residual vs. that pitcher's "
            "own prior-start-this-season average earned-run rate), not a fitted mixed-effects model.",
        ],
        "productionBehaviorChanged": False,
    }
    report["methodologicalLimitations"] = [m for m in report["methodologicalLimitations"] if m]

    report_dir = os.path.join(EDGELAB_DIR, "experiment_reports", EXPERIMENT_ID)
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f"{report_id}.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)

    analytics_path = os.path.join(EDGELAB_DIR, "analytics", "latest_mlb_rsch_0004_starter_workload_backtest.json")
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
