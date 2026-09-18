#!/usr/bin/env python3
"""
scripts/edgelab/run_team_offense_consistency_experiment.py
==============================================================
Research Lab, experiment MLB-RSCH-0036: OFFENSIVE-FORM CONSISTENCY AND
MARKET-RELATIVE FORM.

RESEARCH ONLY. Nothing this script produces changes a production weight,
and it is structurally incapable of doing so: it writes only under
data/edgelab/experiment_reports/ and data/edgelab/analytics/.

THE QUESTION
------------
MLB-RSCH-0005 already showed that recent runs/game DEVIATION from a
team's own season baseline is noise for next-game scoring
(NO_USEFUL_SIGNAL; 12,800 development team-games). A mean, though, is
one number about a distribution. This experiment asks whether the SHAPE
the mean discards carries anything:

  H1  median runs over the last 5/7/10 games
  H2  threshold-clear RATE (>=3/4/5/6 runs) over the same windows
  H3  outlier dependence -- mean minus trimmed mean, and the share of
      the window's runs that came from its single best game
  H4  a trimmed mean and an EWMA as alternatives to the plain mean
  H5  frozen control-vs-candidate prediction of NEXT-GAME RUNS
  H6  frozen control-vs-candidate prediction of CLEARING the next
      team-total threshold (a probability forecast, scored with Brier
      and log loss -- the outcome that actually matches a Kalshi
      team-total contract)
  H7  MARKET-RELATIVE form: over the window where this repository's own
      Kalshi archive supports it, how often a team beat its OWN closing
      team total, and the mean/median margin versus that line

METHODOLOGY (leakage-free, walk-forward)
----------------------------------------
Identical discipline to MLB-RSCH-0003/0004/0005, reused rather than
reinvented:
  * every feature is reconstructed from STRICTLY PRIOR games only
    (lib.edgelab.backtest.team_offense_consistency, which filters via
    is_strictly_before and handles doubleheader ordering);
  * development = 2022-2024, validation = 2025, holdout = 2026;
  * the control and candidate regressions are fit ONCE on development,
    FROZEN, and applied unchanged to validation and holdout -- never
    refit per split (object identity is asserted in this script's tests);
  * windows (5/7/10), thresholds (3/4/5/6) and the eligibility floor
    (20 prior games) are preregistered constants in the shared library,
    never chosen after looking at a result;
  * the market-relative arm is evaluated ONLY on the dates the Kalshi
    observation archive actually covers, and its sample size is reported
    as a first-class limitation rather than buried.

DELIBERATELY NOT DONE
---------------------
No production weight is proposed, tuned, or changed here, whatever the
numbers say. `productionBehaviorChanged` is hard-coded False. A single
retrospective window -- especially the market-relative arm's few weeks
of archive -- is nowhere near enough to move a real-money model, and the
mission this work came from says so explicitly.

Usage:
    python3 scripts/edgelab/run_team_offense_consistency_experiment.py
    python3 scripts/edgelab/run_team_offense_consistency_experiment.py --skip-market-arm
"""
import argparse
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

from lib.edgelab import dispositions, experiment_registry as exp_reg, research_lab_ids as rlids, storage
from lib.edgelab.backtest import bullpen_backtest_stats as stats
from lib.edgelab.backtest import team_offense_consistency as consistency
from lib.edgelab.backtest import team_offense_recency_stats as recency_stats
from lib.edgelab.backtest.bullpen_backtest_reconstruction import extract_team_games_from_schedule
from lib.edgelab.bullpen_usage import MLB_TEAM_ID_MAP
from lib.team_offense_form import FORM_WINDOWS, SCORING_THRESHOLDS
from lib.team_total_line_history import team_lines_for_date

import fetch_mlb_multiseason_bullpen_cache as fetcher  # noqa: E402

EXPERIMENT_ID = "MLB-RSCH-0036"
CONTROL_MODEL_ID = "CTRL-7252463d722626e6"  # same production system identity as MLB-RSCH-0003/0004/0005
REGISTRATION_TIMESTAMP = "2026-09-17T18:00:00Z"

DEV_SEASONS = [2022, 2023, 2024]
VALIDATION_SEASONS = [2025]
HOLDOUT_SEASONS = [2026]
ALL_SEASONS = DEV_SEASONS + VALIDATION_SEASONS + HOLDOUT_SEASONS

MIN_EXPECTED_TEAM_GAMES = 10000

# CONTROL: exactly what MLB-RSCH-0005's control used -- the PIT season
# baseline, the opponent's runs-allowed baseline, and home/away. Nothing
# about recent form.
CONTROL_FEATURES = ["seasonToDateRunsPerGame", "opponentSeasonToDateRunsAllowedPerGame", "isHome"]

# MEAN-ONLY candidate: the status quo the slate has always shown. Its
# job is to reproduce MLB-RSCH-0005's null on these rows, so the
# consistency candidate is measured against the RIGHT baseline rather
# than against the control alone.
MEAN_ONLY_FEATURES = CONTROL_FEATURES + [f"formMean_{w}" for w in FORM_WINDOWS]

# CONSISTENCY candidate: the shape the mean throws away.
CONSISTENCY_FEATURES = CONTROL_FEATURES + [
    f for w in FORM_WINDOWS for f in (
        f"formMedian_{w}", f"formGe4Rate_{w}", f"formGe5Rate_{w}",
        f"formTrimmedMean_{w}", f"formEwma_{w}",
        f"formTopGameShare_{w}", f"formMeanMinusTrimmed_{w}",
    )
]

# The single hypothesis-test predictors (H1-H4), preregistered.
PERSISTENCE_PREDICTORS = (
    [f"formMedian_{w}" for w in FORM_WINDOWS]
    + [f"formGe4Rate_{w}" for w in FORM_WINDOWS]
    + [f"formGe5Rate_{w}" for w in FORM_WINDOWS]
    + [f"formTrimmedMean_{w}" for w in FORM_WINDOWS]
    + [f"formEwma_{w}" for w in FORM_WINDOWS]
    + [f"formTopGameShare_{w}" for w in FORM_WINDOWS]
    + [f"formMeanMinusTrimmed_{w}" for w in FORM_WINDOWS]
)

PROBABILITY_OUTCOME = "scored4Plus"   # the threshold nearest a typical Kalshi team-total line

EDGELAB_DIR = os.path.join(_ROOT, "data", "edgelab")


# ── Preregistration ─────────────────────────────────────────────────────

def register_experiment():
    """Registered BEFORE any cached data is read or any outcome computed
    -- the first call in main(), the same structural ordering every other
    MLB-RSCH experiment in this repository uses."""
    definition = exp_reg.build_experiment_definition(
        title="Team Offensive-Form Consistency and Market-Relative Form",
        hypothesis=(
            "H1 median recent runs predicts next-game scoring beyond the PIT season baseline. "
            "H2 threshold-clear rate (>=3/4/5/6) predicts it. H3 outlier dependence (mean minus "
            "trimmed mean, top-game share of window runs) predicts it, in either direction. "
            "H4 a trimmed mean / EWMA predicts it better than the plain mean. "
            "H5 a frozen consistency regression beats a frozen control AND a frozen mean-only "
            "candidate at predicting next-game runs out of sample. "
            "H6 the same comparison for the PROBABILITY of clearing the next team-total threshold, "
            "scored with Brier and log loss. "
            "H7 market-relative form (games beating the team's own closing team total, and the "
            "mean/median margin versus that line) predicts next-game scoring and threshold clearing."
        ),
        research_question=(
            "MLB-RSCH-0005 showed recent runs/game deviation is noise. A mean is one number about a "
            "distribution -- does the SHAPE it discards (median, threshold-clear rate, outlier "
            "dependence, trimmed mean, EWMA), or performance relative to the market's own team-total "
            "line, carry out-of-sample predictive value the mean does not?"
        ),
        owner="edgelab_research_lab",
        control_model_id=CONTROL_MODEL_ID,
        evidence_level="E2_PIT_HISTORICAL",
        target_population=(
            "Every MLB regular-season team-game 2022-2026 in the already-committed schedule cache, one "
            "row per team entering one game, with at least 20 strictly-prior completed games that "
            "season. The market-relative arm (H7) is restricted to the subset of dates this "
            "repository's Kalshi observation archive actually covers."
        ),
        market_families=["team_total"],
        eligibility_criteria=[
            "team has >= 20 prior completed games this season, strictly before the target game",
            "the target game's own runsScored is present",
            "the opponent's prior-game log is available for the opponent baseline",
            "H7 only: the prior game has a usable archived Kalshi team-total ladder for that team",
        ],
        exclusion_criteria=[
            "a team's first 20 completed games of a season",
            "a game with no cached schedule entry for either team",
            "H7 only: a game whose team-total ladder does not bracket 50%, is non-monotone, or is absent",
        ],
        prediction_checkpoints=["PREGAME_AS_OF_GAME_START"],
        primary_metric=(
            "team-clustered bootstrap Spearman correlation between each preregistered consistency "
            "predictor and next-game runs scored, 95% CI; frozen control vs mean-only vs consistency "
            "regression MAE/RMSE for runs, and Brier/log loss for clearing the next threshold"
        ),
        secondary_metrics=[
            "scored 3+/4+/5+/6+ indicators",
            "market-relative over rate and mean/median margin versus the team's own closing team total",
        ],
        chronological_split_policy=(
            "SEASON_BASED: development=2022-2024, validation=2025, holdout=2026 (locked). Every "
            "regression coefficient set is fit ONCE on development and reused unchanged on validation "
            "and holdout, never refit."
        ),
        minimum_sample_requirement={"independentGames": MIN_EXPECTED_TEAM_GAMES},
        clustering_unit="team",
        experiment_type=exp_reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=exp_reg.FDR_BONFERRONI,
        pit_requirements={
            "team_recent_game_log_reconstruction": "PREDICTIVE_INPUT",
            "archived_kalshi_market_observation": "PREDICTIVE_INPUT",
        },
        registered_at=REGISTRATION_TIMESTAMP,
        experiment_id=EXPERIMENT_ID,
        notes=(
            "No new fetch. Reuses MLB-RSCH-0003's committed schedule cache read-only for runs, and this "
            "repository's own committed Kalshi observation archive for team-total lines (zero Kalshi API "
            "calls). Windows (5/7/10), thresholds (3/4/5/6) and the 20-prior-game floor are preregistered "
            "constants in lib/team_offense_form.py -- the SAME module the production slate context uses, "
            "so a research number and a displayed number cannot drift. Production behaviour is NOT changed "
            "by this experiment under any outcome."
        ),
    )
    exp_reg.register_experiment(definition)
    return definition


# ── Row construction ────────────────────────────────────────────────────

def _load_all_team_games(season):
    games_by_team_id = {}
    for team_abbr, team_id in MLB_TEAM_ID_MAP.items():
        schedule = fetcher.load_cached_schedule(season, team_abbr)
        if not schedule:
            games_by_team_id[team_id] = []
            continue
        games = extract_team_games_from_schedule(schedule, team_id)
        for game in games:
            game["team"] = team_abbr
        games_by_team_id[team_id] = games
    return games_by_team_id


def load_archived_team_total_lines():
    """
    {(date, team): implied_line} from this repository's OWN committed
    Kalshi observations. No network access. Returns (lines, coverage).
    """
    observations_dir = os.path.join(storage.EDGELAB_ROOT, "observations")
    lines, dates_seen = {}, []
    if not os.path.isdir(observations_dir):
        return lines, {"status": "NO_OBSERVATION_ARCHIVE", "dates": 0, "teamGameLines": 0}
    for name in sorted(os.listdir(observations_dir)):
        date = name.split(".")[0]
        if len(date) != 10:
            continue
        records = list(storage.read_records(os.path.join(observations_dir, name)))
        if not records:
            continue
        dates_seen.append(date)
        per_team, _reasons = team_lines_for_date(records)
        for team, line in per_team.items():
            lines[(date, team)] = line
    return lines, {
        "status": "LOADED" if lines else "NO_USABLE_LADDERS",
        "dates": len(dates_seen),
        "firstDate": dates_seen[0] if dates_seen else None,
        "lastDate": dates_seen[-1] if dates_seen else None,
        "teamGameLines": len(lines),
    }


def build_rows(season, line_by_key=None):
    games_by_team_id = _load_all_team_games(season)
    rows = []
    for team_id, team_games in games_by_team_id.items():
        for target in team_games:
            opponent_games = games_by_team_id.get(target.get("opponentTeamId"), [])
            features = consistency.reconstruct_consistency_features(
                team_games, opponent_games, target, line_by_key=line_by_key)
            if features is None:
                continue
            outcome = consistency.consistency_outcome(target)
            if outcome is None:
                continue
            if features["seasonToDateRunsPerGame"] is None or features["opponentSeasonToDateRunsAllowedPerGame"] is None:
                continue
            row = {
                "season": season, "team": target["team"], "gamePk": target["gamePk"],
                "gameDate": target["date"],
            }
            row.update(features)
            row.update(outcome)
            rows.append(row)
    return rows


# ── Hypothesis tests (one fixed spec, applied unchanged to every split) ──

def run_persistence_tests(rows, predictors=PERSISTENCE_PREDICTORS):
    """Pure. Team-clustered bootstrap Spearman of each preregistered
    predictor against next-game runs. No branch here reads which split
    it was handed."""
    if not rows:
        return None
    result = {"n": len(rows), "uniqueTeams": len({r["team"] for r in rows}),
              "uniqueGames": len({r["gamePk"] for r in rows}), "predictors": {}}
    for predictor in predictors:
        usable = [r for r in rows if r.get(predictor) is not None]
        if len(usable) < 100:
            result["predictors"][predictor] = {"n": len(usable), "insufficientSample": True}
            continue
        result["predictors"][predictor] = stats.correlation_with_ci(
            usable, predictor, "runsScored", cluster_key="team")
    return result


def evaluate_frozen_runs(rows, coefficient_sets):
    if not rows:
        return None
    out = {"n": len(rows)}
    for name, (coefs, features) in coefficient_sets.items():
        usable = [r for r in rows if all(r.get(f) is not None for f in features)]
        out[name] = {
            **recency_stats.evaluate_predictions(usable, coefs, features, "runsScored"),
            "rowsScored": len(usable),
        }
    return out


def evaluate_frozen_probabilities(rows, coefficient_sets, outcome_key=PROBABILITY_OUTCOME):
    """
    Frozen LINEAR PROBABILITY models applied to a 0/1 outcome, scored
    with Brier and log loss. A linear probability model is a deliberately
    simple, non-tuned comparator (same spirit as MLB-RSCH-0005's OLS):
    predictions are clipped into [0.01, 0.99] before scoring, which is a
    scoring-safety step, never a fitted parameter.
    """
    if not rows:
        return None
    out = {"n": len(rows), "outcome": outcome_key,
           "baseRate": round(sum(r[outcome_key] for r in rows) / len(rows), 4)}
    for name, (coefs, features) in coefficient_sets.items():
        usable = [r for r in rows if all(r.get(f) is not None for f in features)]
        scored = []
        for row in usable:
            raw = recency_stats.ols_predict(coefs, row, features)
            scored.append({**row, "_p": min(0.99, max(0.01, raw))})
        out[name] = {
            "rowsScored": len(scored),
            "brier": consistency.brier_score(scored, "_p", outcome_key),
            "logLoss": consistency.log_loss(scored, "_p", outcome_key),
        }
    # An always-predict-the-base-rate forecast: the honest floor any
    # candidate has to beat before "predictive" means anything.
    base = [{"_p": out["baseRate"], outcome_key: r[outcome_key]} for r in rows]
    out["alwaysBaseRate"] = {
        "rowsScored": len(base),
        "brier": consistency.brier_score(base, "_p", outcome_key),
        "logLoss": consistency.log_loss(base, "_p", outcome_key),
    }
    return out


def market_relative_arm(rows):
    """
    H7. Descriptive coverage plus the same clustered-correlation test for
    the three market-relative predictors, on whatever subset of rows the
    archive actually supports. Sample size is reported prominently; this
    arm is expected to be small.
    """
    predictors = [f for w in FORM_WINDOWS for f in
                  (f"formTtOverRate_{w}", f"formTtMeanMargin_{w}", f"formTtMedianMargin_{w}")]
    covered = [r for r in rows if any(r.get(p) is not None for p in predictors)]
    arm = {
        "rowsWithAnyMarketRelativeFeature": len(covered),
        "rowsTotal": len(rows),
        "coverageRate": round(len(covered) / len(rows), 4) if rows else None,
        "predictors": {},
    }
    for predictor in predictors:
        usable = [r for r in covered if r.get(predictor) is not None]
        if len(usable) < 100:
            arm["predictors"][predictor] = {"n": len(usable), "insufficientSample": True}
            continue
        arm["predictors"][predictor] = stats.correlation_with_ci(
            usable, predictor, "runsScored", cluster_key="team")
    return arm


def classify_finding(dev, validation, holdout, dev_runs, val_runs, hold_runs,
                     val_probs, hold_probs):
    """
    Pure, conservative. Three labels, matching the distinction the
    mission asked for:

      DESCRIPTIVE_ONLY          -- useful to look at, no demonstrated
                                   out-of-sample predictive value
      STATISTICALLY_SUPPORTED   -- a CI excluding zero on development AND
                                   an out-of-sample improvement over BOTH
                                   the control and the mean-only
                                   candidate, on validation AND holdout
      INSUFFICIENT_EVIDENCE     -- not enough sample to say either way
    """
    if dev is None or dev["n"] < MIN_EXPECTED_TEAM_GAMES:
        return "INSUFFICIENT_EVIDENCE"

    def _confident(entry):
        ci = (entry or {}).get("ci")
        return bool(ci and ci.get("low") is not None
                    and (ci["low"] > 0 or (ci.get("high") is not None and ci["high"] < 0)))

    dev_any_confident = any(_confident(e) for e in dev["predictors"].values())

    def _beats_both(runs):
        if not runs:
            return False
        candidate = (runs.get("consistency") or {}).get("mae")
        control = (runs.get("control") or {}).get("mae")
        mean_only = (runs.get("meanOnly") or {}).get("mae")
        return bool(candidate is not None and control is not None and mean_only is not None
                    and candidate < control and candidate < mean_only)

    def _beats_probability(probs):
        if not probs:
            return False
        candidate = (probs.get("consistency") or {}).get("brier")
        control = (probs.get("control") or {}).get("brier")
        base = (probs.get("alwaysBaseRate") or {}).get("brier")
        return bool(candidate is not None and control is not None and base is not None
                    and candidate < control and candidate < base)

    out_of_sample = (_beats_both(val_runs) and _beats_both(hold_runs)) or \
                    (_beats_probability(val_probs) and _beats_probability(hold_probs))

    if dev_any_confident and out_of_sample:
        return "STATISTICALLY_SUPPORTED"
    return "DESCRIPTIVE_ONLY"


# ── main ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-market-arm", action="store_true",
                        help="Skip H7 (no archived team-total ladder pass)")
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args()

    experiment = register_experiment()

    if args.skip_market_arm:
        line_by_key, line_coverage = None, {"status": "SKIPPED_BY_FLAG"}
    else:
        line_by_key, line_coverage = load_archived_team_total_lines()

    rows_by_season = {season: build_rows(season, line_by_key) for season in ALL_SEASONS}
    coverage = {
        "perSeason": {
            season: {"teamGames": len(rows), "uniqueTeams": len({r["team"] for r in rows}),
                     "games": len({r["gamePk"] for r in rows})}
            for season, rows in rows_by_season.items()
        },
        "totalTeamGames": sum(len(r) for r in rows_by_season.values()),
        "minimumExpected": MIN_EXPECTED_TEAM_GAMES,
        "marketLineArchive": line_coverage,
    }
    coverage["meetsMinimumExpectedSample"] = coverage["totalTeamGames"] >= MIN_EXPECTED_TEAM_GAMES

    dev_rows = [r for s in DEV_SEASONS for r in rows_by_season.get(s, [])]
    validation_rows = [r for s in VALIDATION_SEASONS for r in rows_by_season.get(s, [])]
    holdout_rows = [r for s in HOLDOUT_SEASONS for r in rows_by_season.get(s, [])]

    dev = run_persistence_tests(dev_rows)
    validation = run_persistence_tests(validation_rows)
    holdout = run_persistence_tests(holdout_rows)

    # Fit ONCE on development, freeze, reuse unchanged everywhere else.
    def _fit(features, target):
        usable = [r for r in dev_rows if all(r.get(f) is not None for f in features)]
        return recency_stats.ols_fit(usable, features, target)

    runs_sets = {
        "control": (_fit(CONTROL_FEATURES, "runsScored"), CONTROL_FEATURES),
        "meanOnly": (_fit(MEAN_ONLY_FEATURES, "runsScored"), MEAN_ONLY_FEATURES),
        "consistency": (_fit(CONSISTENCY_FEATURES, "runsScored"), CONSISTENCY_FEATURES),
    }
    probability_sets = {
        "control": (_fit(CONTROL_FEATURES, PROBABILITY_OUTCOME), CONTROL_FEATURES),
        "meanOnly": (_fit(MEAN_ONLY_FEATURES, PROBABILITY_OUTCOME), MEAN_ONLY_FEATURES),
        "consistency": (_fit(CONSISTENCY_FEATURES, PROBABILITY_OUTCOME), CONSISTENCY_FEATURES),
    }

    dev_runs = evaluate_frozen_runs(dev_rows, runs_sets)
    val_runs = evaluate_frozen_runs(validation_rows, runs_sets)
    hold_runs = evaluate_frozen_runs(holdout_rows, runs_sets)
    dev_probs = evaluate_frozen_probabilities(dev_rows, probability_sets)
    val_probs = evaluate_frozen_probabilities(validation_rows, probability_sets)
    hold_probs = evaluate_frozen_probabilities(holdout_rows, probability_sets)

    market_arm = None if args.skip_market_arm else {
        "development": market_relative_arm(dev_rows),
        "validation": market_relative_arm(validation_rows),
        "holdout": market_relative_arm(holdout_rows),
    }

    finding = classify_finding(dev, validation, holdout, dev_runs, val_runs, hold_runs,
                               val_probs, hold_probs)

    limitations = [
        "A team's first 20 completed games of each season are excluded -- no reliable baseline or "
        "10-game window exists yet; never approximated.",
        "The frozen regressions are simple closed-form OLS with no regularization, interactions or "
        "nonlinear terms -- a non-tuned comparator, not a production-grade scoring model.",
        "Starting-pitcher identity/quality, park and weather are NOT controlled for. This experiment "
        "measures what a form statistic adds over a season/opponent baseline only.",
        "The probability arm uses a LINEAR probability model, which can predict outside [0,1]; "
        "predictions are clipped to [0.01, 0.99] purely for scoring safety.",
    ]
    if not args.skip_market_arm:
        limitations.append(
            f"H7's market-relative arm is bounded by this repository's Kalshi observation archive "
            f"({line_coverage.get('dates')} archived dates, {line_coverage.get('teamGameLines')} "
            f"team-game lines, {line_coverage.get('firstDate')}..{line_coverage.get('lastDate')}). "
            f"That is a few weeks, not a few seasons: treat every H7 number as descriptive coverage "
            f"evidence, NOT as a validated predictive result."
        )
    if not coverage["meetsMinimumExpectedSample"]:
        limitations.insert(0, (
            f"Usable sample ({coverage['totalTeamGames']} team-games) is below the "
            f"{MIN_EXPECTED_TEAM_GAMES} minimum this experiment targeted -- every number below is "
            f"descriptive of a partial cache, not a validated large-sample result."
        ))

    report_id = rlids.build_experiment_report_id(EXPERIMENT_ID, CONTROL_MODEL_ID, None, REGISTRATION_TIMESTAMP)
    report = {
        "experimentReportId": report_id,
        "experimentId": EXPERIMENT_ID,
        "controlModelId": CONTROL_MODEL_ID,
        "evidenceLevel": experiment["evidenceLevel"],
        "generatedAt": REGISTRATION_TIMESTAMP,
        "coverage": coverage,
        "persistence": {"development": dev, "validation": validation, "holdout": holdout},
        "runsPrediction": {"development": dev_runs, "validation": val_runs, "holdout": hold_runs},
        "thresholdProbabilityPrediction": {
            "development": dev_probs, "validation": val_probs, "holdout": hold_probs},
        "frozenCoefficients": {name: coefs for name, (coefs, _f) in runs_sets.items()},
        "frozenProbabilityCoefficients": {name: coefs for name, (coefs, _f) in probability_sets.items()},
        "marketRelativeArm": market_arm,
        "finding": finding,
        "disposition": dispositions.RESEARCH_CANDIDATE,
        "methodologicalLimitations": limitations,
        "productionBehaviorChanged": False,
        "productionChangeProposed": False,
        "scoringThresholds": list(SCORING_THRESHOLDS),
        "formWindows": list(FORM_WINDOWS),
    }

    out_dir = args.out_dir or os.path.join(EDGELAB_DIR, "experiment_reports", EXPERIMENT_ID)
    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, f"{report_id}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)

    analytics_path = os.path.join(EDGELAB_DIR, "analytics",
                                  "latest_mlb_rsch_0036_team_offense_consistency.json")
    os.makedirs(os.path.dirname(analytics_path), exist_ok=True)
    with open(analytics_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)

    print(json.dumps({
        "experimentId": EXPERIMENT_ID,
        "coverage": {k: v for k, v in coverage.items() if k != "perSeason"},
        "finding": finding,
        "reportPath": report_path,
    }, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
