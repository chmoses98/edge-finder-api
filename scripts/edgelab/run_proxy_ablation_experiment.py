#!/usr/bin/env python3
"""
scripts/edgelab/run_proxy_ablation_experiment.py
====================================================================
Research Lab, experiment MLB-RSCH-0009: "Historical Proxy Enrichment /
Component Ablation". Determines whether adding legitimately
reconstructable baseball components to MLB-RSCH-0008's simple proxy
(M0) materially improves predictive performance (1) against actual MLB
outcomes over the large 2022-2026 historical corpus, and (2) against
historical Pinnacle on MLB-RSCH-0008's already-acquired matched sample.
RESEARCH ONLY. NO production changes. NO new Odds API spend (reuses
MLB-RSCH-0008's existing Pinnacle cache unchanged).

PREREGISTERED ABLATION SEQUENCE (never reordered based on results):
  M0: MLB-RSCH-0008's simple proxy, unchanged (offense + run-prevention
      season-to-date rates + one dev-fit home-field constant).
  candidate "offense": M0 + stabilized (shrinkage) offense baseline.
  candidate "starter" (M2): PHASE A PROBE RESULT
      (data/research_cache/sharp_market_probe/starter_identity_probe_result.json,
      real MLB Stats API data, 668 comparable rows across 28 dates,
      2022-2026): mismatch rate between hydrate=probablePitcher on a
      PAST date and the boxscore-confirmed actual starter was 0.6%
      (4/668) -- below this milestone's own preregistered plausible
      floor of 1% (real-world MLB scratch/rotation-change rates run
      several points higher over a season). Verdict:
      STARTER_IDENTITY_NOT_PIT_SAFE_AT_SCALE. Per the mission's own
      explicit instruction ("Be extremely strict... classify as
      unavailable rather than leaking final-game information"), this
      component is UNAVAILABLE -- not merely rejected on performance --
      and is EXCLUDED from the testable candidate sequence entirely
      (never silently treated as "tested and found useless").
  candidate "bullpen": (offense, if kept) + bullpen quality.
  candidate "park": (offense/bullpen, if kept) + park factor / season
      run environment.

Each candidate is tested, in this FIXED order, against the CURRENT
best-so-far accepted composition (not against M0 every time) -- exactly
mirrors a forward-selection ablation with a fixed, preregistered
candidate order. A rejected candidate is dropped; later candidates are
still tested (built on the last ACCEPTED composition), per the
mission's own per-component keep/reject framing ("A component can
remain in the FINAL HISTORICAL PROXY only if...").

MODEL SELECTION RULE (preregistered, before any real result was
computed): a candidate is KEPT only if its incremental mean Brier delta
(mean of the game-ML delta and the fixed-total-line-8.5 delta, vs the
current accepted composition) is NEGATIVE on DEVELOPMENT (2022-2024)
AND does not exceed DEGRADATION_TOLERANCE on VALIDATION (2025). 2026 is
NEVER inspected during selection -- the final composition is frozen
BEFORE evaluating the locked holdout.

REUSES, DOES NOT REIMPLEMENT:
  - lib.edgelab.backtest.proxy_model's team_baseline/expected_runs/
    game_ml_proxy_probability/game_total_proxy_probability/
    fit_home_field_adjustment (MLB-RSCH-0008) -- UNCHANGED at every
    ablation level; only the BASELINE DICTS fed into them differ.
  - lib.edgelab.backtest.proxy_enrichment (this milestone's own new,
    separately-tested module) for every M1/M3/M4 primitive.
  - lib.edgelab.backtest.bullpen_backtest_reconstruction's
    extract_pitcher_lines/relief_outcome_for_game (MLB-RSCH-0003) for
    the bullpen quality component's raw per-game relief lines.
  - lib.edgelab.research_stats' brier_and_log_loss_summary/
    expected_calibration_error/independent_unit_count/
    sample_size_status/game_clustered_bootstrap_ci -- the PREFERRED,
    generic game-clustered bootstrap CI mechanism, applied here to
    baseball-outcome paired deltas (a new, legitimate use of the same
    reused primitive, not a probability-vs-market pairing).
  - lib.edgelab.paired_evaluation (MLB-RSCH-0001's own MODEL-vs-MARKET
    repurposing, reused a THIRD time) for the second-stage Pinnacle
    comparison only, matching MLB-RSCH-0008's own exact pattern for
    direct comparability.
  - scripts.build_market_ledger's poisson_pmf/p_team_wins/p_over_total,
    UNCHANGED, via proxy_model's own reuse.

DATA SCALE: the baseball-level primary evaluation runs on the FULL
2022-2026 schedule+boxscore corpus (~2,400 games/season x 5 seasons),
not merely the ~834-game Pinnacle-matched sample -- bounded only by
each team's >=20-prior-games-this-season eligibility floor, giving
genuinely "thousands of games" for the ablation itself. The Pinnacle
comparison (second stage) stays scoped to MLB-RSCH-0008's existing
matched sample, unchanged, per this mission's "no new Odds API spend"
instruction.
"""
import bisect
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
_BACKTEST_SCRIPTS_DIR = os.path.join(_SCRIPTS_DIR, "edgelab", "backtest")
if _BACKTEST_SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _BACKTEST_SCRIPTS_DIR)

from lib.edgelab.backtest.bullpen_backtest_reconstruction import relief_outcome_for_game
from lib.edgelab.backtest.proxy_model import (
    team_baseline,
    expected_runs,
    game_ml_proxy_probability,
    game_total_proxy_probability,
    fit_home_field_adjustment,
)
from lib.edgelab.backtest.proxy_enrichment import (
    extract_team_games_with_venue,
    fit_league_average_runs_per_game,
    stabilized_offense_rate,
    team_relief_er9_games,
    bullpen_quality_baseline,
    fit_league_average_bullpen_er9,
    stabilized_bullpen_rate,
    blend_run_prevention_with_bullpen_quality,
    fit_park_factors,
    fit_reference_season_run_environment,
    park_and_environment_multiplier,
    apply_runs_multiplier,
)
from lib.edgelab.storage import read_records
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

from build_market_ledger import poisson_pmf  # noqa: E402

EXPERIMENT_ID = "MLB-RSCH-0009"
REGISTRATION_TIMESTAMP = "2026-08-28T00:20:00Z"

DEV_SEASONS = [2022, 2023, 2024]
VALIDATION_SEASONS = [2025]
HOLDOUT_SEASONS = [2026]
ALL_SEASONS = DEV_SEASONS + VALIDATION_SEASONS + HOLDOUT_SEASONS

# Fixed conventional total-runs line for the baseball-level (non-Pinnacle)
# Over/Under evaluation -- a PIT-safe constant (8.5 is MLB's own most
# common single total line), never derived from any market, never tuned.
BASEBALL_LEVEL_TOTAL_LINE = 8.5

# Preregistered candidate sequence -- "starter" (M2) is EXCLUDED entirely
# per the real Phase A probe verdict (see module docstring).
CANDIDATE_SEQUENCE = ("offense", "bullpen", "park")

# Preregistered model-selection rule, fixed before any result was computed.
DEGRADATION_TOLERANCE = 0.005

MIN_GAMES_EXPLORATORY = 50
MIN_GAMES_CONFIDENT = 50

MLB_TEAM_IDS = [
    108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 133, 134,
    135, 136, 137, 138, 139, 140, 141, 142, 143, 144, 145, 146, 147, 158,
]

BULLPEN_CACHE_ROOT = os.path.join(_ROOT, "data", "research_cache", "bullpen_backtest")
STARTER_IDENTITY_PROBE_PATH = os.path.join(
    _ROOT, "data", "research_cache", "sharp_market_probe", "starter_identity_probe_result.json"
)
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


def load_starter_identity_verdict():
    """Reads the real Phase A probe result committed to this branch.
    Never fabricated -- raises if the probe was never actually run,
    rather than silently assuming a verdict."""
    with open(STARTER_IDENTITY_PROBE_PATH) as f:
        return json.load(f)


def register_experiment():
    """Registers MLB-RSCH-0009 BEFORE any cache is loaded or result is
    computed -- first call in main(), structurally enforced by this
    script's own test file's TestPreregistrationOrdering."""
    control = ctrl_id.build_control_registration(
        name="mlb_rsch_0009_enriched_proxy_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0009 enriched proxy v1: MLB-RSCH-0008 M0 + forward-selected "
                        "{offense shrinkage, bullpen quality, park/environment} components"
        ),
        probability_adapter_identity="lib.edgelab.backtest.proxy_model.game_ml_proxy_probability;game_total_proxy_probability",
        model_engine_family="pit_safe_research_proxy_v1_pythagorean_poisson_enriched",
        required_input_provenance=["team_recent_game_log_reconstruction"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "A NEW historical RESEARCH proxy, built by forward-selecting PIT-safe enrichment "
            "components onto MLB-RSCH-0008's simple proxy (unchanged): stabilized offense, "
            "bullpen quality, and park/run-environment. Starting-pitcher quality (M2) is "
            "EXCLUDED -- Phase A empirically found historical starter identity is not "
            "PIT-safe at scale (0.6% mismatch rate, below the 1% preregistered floor)."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Historical Proxy Enrichment / Component Ablation",
        hypothesis=(
            "H1: stabilized (shrinkage) season-to-date offense improves out-of-sample baseball "
            "prediction over MLB-RSCH-0008's raw season-to-date rate. H2: starting-pitcher "
            "quality would add value if PIT-safe (excluded this milestone -- see Phase A). "
            "H3: bullpen quality (season-to-date relief ER/9, shrunk toward league average) "
            "improves out-of-sample baseball prediction. H4: a development-era-frozen park "
            "factor plus season run-environment improves out-of-sample baseball prediction. "
            "H5: any accepted enrichment narrows (without necessarily closing) the gap to "
            "historical Pinnacle fair probability on MLB-RSCH-0008's existing matched sample."
        ),
        research_question=(
            "Does adding legitimately PIT-safe-reconstructable baseball components to MLB-RSCH-0008's "
            "simple proxy materially improve predictive performance against actual MLB outcomes over "
            "the large 2022-2026 historical corpus, and does any resulting improvement narrow the gap "
            "to historical Pinnacle fair probability on the existing matched sample?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E2_PIT_HISTORICAL,
        target_population=(
            "MLB regular-season games 2022-2026 where both teams have >= MIN_PRIOR_GAMES_FOR_BASELINE "
            "prior completed games this season (M0 baseline eligibility, unchanged from MLB-RSCH-0008) -- "
            "the full schedule+boxscore corpus, not merely the smaller Pinnacle-matched sample."
        ),
        market_families=["game_result", "game_total"],
        eligibility_criteria=[
            "both teams have >= MIN_PRIOR_GAMES_FOR_BASELINE prior completed games this season (M0 baseline eligibility, unchanged)",
            "bullpen/park/environment components gracefully degrade to the prior accepted composition when their own data is unavailable for a given row (never excludes the row)",
        ],
        exclusion_criteria=[
            "starting-pitcher quality (M2) -- entirely excluded, not PIT-safe at scale per real Phase A probe",
            "either team's first ~20 games of a season (no reliable baseline)",
        ],
        prediction_checkpoints=["SEASON_TO_DATE_PREGAME"],
        primary_metric="incremental mean Brier delta (mean of game-ML and fixed-8.5-total-line Brier deltas) vs the current accepted composition, game-clustered 95% CI",
        secondary_metrics=[
            "incremental log-loss delta", "MAE/RMSE of expected total runs", "Poisson log-likelihood of actual total",
            "paired proxy-minus-Pinnacle Brier/log-loss delta (second stage, MLB-RSCH-0008's existing matched sample)",
        ],
        chronological_split_policy=f"SEASON_BASED: development={DEV_SEASONS}, validation={VALIDATION_SEASONS}, holdout={HOLDOUT_SEASONS} (locked)",
        minimum_sample_requirement={"independentGames": MIN_GAMES_CONFIDENT},
        clustering_unit="gamePk",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={"team_recent_game_log_reconstruction": "PREDICTIVE_INPUT"},
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            "evidenceLevel E2_PIT_HISTORICAL: same basis as MLB-RSCH-0008 -- the proxy's own "
            "inputs are PIT-safe reconstructions proven by this and prior milestones' own test "
            "suites; not E3 (single dev/val/holdout split, not a rolling chronological walk-forward). "
            "Starting-pitcher quality (M2) excluded per a real Phase A probe: 0.6% mismatch rate "
            "(4/668) between hydrate=probablePitcher on a past date and the boxscore-confirmed "
            "actual starter, below the 1% preregistered plausible floor -- STARTER_IDENTITY_NOT_PIT_SAFE_AT_SCALE."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Data loading (all local, no network -- MLB Stats API reuse only via
#    the Phase A probe, already run and committed) ─────────────────────

def load_team_schedule_with_venue(season, team_abbr):
    from fetch_mlb_multiseason_bullpen_cache import schedule_cache_path
    path = schedule_cache_path(season, team_abbr)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def load_all_team_games_with_venue(season):
    """{teamId: games_with_venue} for one season, every MLB team."""
    from lib.edgelab.bullpen_usage import MLB_ID_TO_ABBR
    out = {}
    for team_id in MLB_TEAM_IDS:
        abbr = MLB_ID_TO_ABBR.get(team_id)
        if not abbr:
            continue
        schedule = load_team_schedule_with_venue(season, abbr)
        out[team_id] = extract_team_games_with_venue(schedule, team_id) if schedule else []
    return out


def load_relief_er9_games(season, team_games_by_id):
    """{teamId: relief_er9_games} for one season, reusing MLB-RSCH-0003's
    own committed boxscore cache and relief_outcome_for_game (UNCHANGED).
    That cache's own records ALREADY ARE extract_pitcher_lines()'s output
    shape ({"gamePk", "awayPitchers", "homePitchers"} -- MLB-RSCH-0003's
    fetch script applies extract_pitcher_lines at fetch time, not at read
    time), so this reads each side's pitcher-line list directly rather
    than re-calling extract_pitcher_lines on an already-extracted record."""
    boxscore_path = os.path.join(BULLPEN_CACHE_ROOT, str(season), "boxscores.jsonl.gz")
    boxscores_by_game_pk = {row["gamePk"]: row for row in read_records(boxscore_path) if row.get("gamePk") is not None}

    out = {}
    for team_id, games in team_games_by_id.items():
        outcomes_by_game_pk = {}
        for g in games:
            box = boxscores_by_game_pk.get(g["gamePk"])
            if box is None:
                continue
            lines = box.get(f"{g['side']}Pitchers") or []
            outcomes_by_game_pk[g["gamePk"]] = relief_outcome_for_game(lines)
        out[team_id] = team_relief_er9_games(games, outcomes_by_game_pk)
    return out


def build_season_environment_lookup(home_games_this_season):
    """Fast O(log n)-per-lookup equivalent of
    proxy_enrichment.season_run_environment (proven equivalent by this
    script's own test file) -- precomputes a sorted (date, cumulative
    total-runs) index ONCE per season instead of rescanning every game
    for every row."""
    entries = sorted(
        (g["date"], g["runsScored"] + g["runsAllowed"])
        for g in home_games_this_season
        if g.get("runsScored") is not None and g.get("runsAllowed") is not None and g.get("date")
    )
    dates = [e[0] for e in entries]
    cumsum = [0]
    for _, total in entries:
        cumsum.append(cumsum[-1] + total)

    def lookup(as_of_date):
        idx = bisect.bisect_left(dates, as_of_date)
        if idx == 0:
            return None
        return round(cumsum[idx] / idx, 4)

    return lookup


# ── Row building ─────────────────────────────────────────────────────────

def build_season_rows(season, team_games_by_id, relief_er9_by_id, env_lookup):
    """One row per real game (from the home team's own schedule entry --
    each real game appears exactly once). Carries RAW baselines only;
    stabilized/blended values are attached in a later pass once
    DEVELOPMENT-only constants are frozen. `env_lookup`: this season's
    own build_season_environment_lookup() result, built ONCE by the
    caller and shared with the Pinnacle-stage row enrichment too (same
    season, same league-wide run-environment index either way)."""
    rows = []
    for home_team_id, home_games in team_games_by_id.items():
        for g in home_games:
            if g.get("side") != "home":
                continue
            away_team_id = g.get("opponentTeamId")
            away_games = team_games_by_id.get(away_team_id, [])
            home_baseline_raw = team_baseline(home_games, g)
            away_baseline_raw = team_baseline(away_games, g)
            if home_baseline_raw is None or away_baseline_raw is None:
                continue
            home_bullpen_raw = bullpen_quality_baseline(relief_er9_by_id.get(home_team_id, []), g)
            away_bullpen_raw = bullpen_quality_baseline(relief_er9_by_id.get(away_team_id, []), g)
            actual_home_runs, actual_away_runs = g.get("runsScored"), g.get("runsAllowed")
            rows.append({
                "season": season, "gamePk": g["gamePk"], "date": g["date"], "gameNumber": g.get("gameNumber"),
                "homeTeamId": home_team_id, "awayTeamId": away_team_id, "venueId": g.get("venueId"),
                "homeBaselineRaw": home_baseline_raw, "awayBaselineRaw": away_baseline_raw,
                "homeBullpenRaw": home_bullpen_raw, "awayBullpenRaw": away_bullpen_raw,
                "seasonEnvironmentAsOfDate": env_lookup(g["date"]),
                "actualHomeRuns": actual_home_runs, "actualAwayRuns": actual_away_runs,
                "actualHomeWin": (1 if actual_home_runs > actual_away_runs else 0) if actual_home_runs is not None and actual_away_runs is not None else None,
                "actualOver85": (
                    1 if (actual_home_runs + actual_away_runs) > BASEBALL_LEVEL_TOTAL_LINE else 0
                ) if actual_home_runs is not None and actual_away_runs is not None else None,
            })
    return rows


def attach_stabilized_components(rows, league_avg_offense, league_avg_bullpen_er9):
    """Mutates each row in place, adding the frozen-constant-derived
    stabilized offense/bullpen values -- pure functions from
    proxy_enrichment, called with the SAME frozen constants for every
    row regardless of split (dev/val/holdout all reuse these unchanged)."""
    for r in rows:
        hb, ab = r["homeBaselineRaw"], r["awayBaselineRaw"]
        r["homeOffenseStabilized"] = stabilized_offense_rate(hb["offenseRunsPerGame"], hb["priorGamesThisSeason"], league_avg_offense)
        r["awayOffenseStabilized"] = stabilized_offense_rate(ab["offenseRunsPerGame"], ab["priorGamesThisSeason"], league_avg_offense)
        hbp, abp = r["homeBullpenRaw"], r["awayBullpenRaw"]
        r["homeBullpenStabilized"] = (
            stabilized_bullpen_rate(hbp["bullpenEarnedRunsPer9"], hbp["priorGamesWithBullpenData"], league_avg_bullpen_er9) if hbp else None
        )
        r["awayBullpenStabilized"] = (
            stabilized_bullpen_rate(abp["bullpenEarnedRunsPer9"], abp["priorGamesWithBullpenData"], league_avg_bullpen_er9) if abp else None
        )


def attach_park_multiplier(rows, park_factors, reference_env):
    for r in rows:
        r["parkEnvMultiplier"] = park_and_environment_multiplier(
            r["venueId"], park_factors, r["seasonEnvironmentAsOfDate"], reference_env
        )


# ── Composable model levels ─────────────────────────────────────────────

def baseline_for_components(raw, offense_stabilized, bullpen_stabilized, components):
    if raw is None:
        return None
    out = dict(raw)
    if "offense" in components:
        out["offenseRunsPerGame"] = offense_stabilized if offense_stabilized is not None else raw["offenseRunsPerGame"]
    if "bullpen" in components:
        out["runPreventionRunsAllowedPerGame"] = blend_run_prevention_with_bullpen_quality(
            raw["runPreventionRunsAllowedPerGame"], bullpen_stabilized
        )
    return out


def _hfa_fit_rows(rows, components):
    out = []
    for r in rows:
        hb = baseline_for_components(r["homeBaselineRaw"], r["homeOffenseStabilized"], r["homeBullpenStabilized"], components)
        ab = baseline_for_components(r["awayBaselineRaw"], r["awayOffenseStabilized"], r["awayBullpenStabilized"], components)
        out.append({"homeBaseline": hb, "awayBaseline": ab, "actualHomeRuns": r["actualHomeRuns"], "actualAwayRuns": r["actualAwayRuns"]})
    return out


def fit_home_field_adjustment_for_components(dev_rows, components):
    """Closed-form, DEVELOPMENT-only, fit ONCE PER composition (the
    correct residual to absorb changes as the baseline construction
    changes) -- reuses fit_home_field_adjustment (MLB-RSCH-0008)
    UNCHANGED, just fed this composition's own baselines."""
    return fit_home_field_adjustment(_hfa_fit_rows(dev_rows, components))


def predict_for_components(row, components, home_field_adjustment):
    hb = baseline_for_components(row["homeBaselineRaw"], row["homeOffenseStabilized"], row["homeBullpenStabilized"], components)
    ab = baseline_for_components(row["awayBaselineRaw"], row["awayOffenseStabilized"], row["awayBullpenStabilized"], components)
    eh, ea = expected_runs(hb, ab, home_field_adjustment=home_field_adjustment)
    if "park" in components:
        eh, ea = apply_runs_multiplier(eh, ea, row["parkEnvMultiplier"])
    ml_prob, _push = game_ml_proxy_probability(eh, ea)
    total_over_prob = game_total_proxy_probability(eh, ea, BASEBALL_LEVEL_TOTAL_LINE)
    expected_total = (eh + ea) if eh is not None and ea is not None else None
    return ml_prob, total_over_prob, expected_total


def attach_predictions(rows, components, home_field_adjustment, key_prefix):
    for r in rows:
        ml_prob, total_over_prob, expected_total = predict_for_components(r, components, home_field_adjustment)
        r[f"mlProb_{key_prefix}"] = ml_prob
        r[f"totalOverProb_{key_prefix}"] = total_over_prob
        r[f"expectedTotal_{key_prefix}"] = expected_total


# ── Baseball-level evaluation (no Pinnacle) ─────────────────────────────

def evaluate_split(rows, key_prefix):
    ml_pairs = [(r[f"mlProb_{key_prefix}"], r["actualHomeWin"]) for r in rows if r.get(f"mlProb_{key_prefix}") is not None and r.get("actualHomeWin") is not None]
    total_pairs = [(r[f"totalOverProb_{key_prefix}"], r["actualOver85"]) for r in rows if r.get(f"totalOverProb_{key_prefix}") is not None and r.get("actualOver85") is not None]

    n = len(ml_pairs)
    independent_games = independent_unit_count(rows, key="gamePk")

    ml_brier, ml_logloss = brier_and_log_loss_summary(ml_pairs)
    ml_ece = expected_calibration_error(ml_pairs)
    total_brier, total_logloss = brier_and_log_loss_summary(total_pairs)
    total_ece = expected_calibration_error(total_pairs)

    total_errors = [
        (r[f"expectedTotal_{key_prefix}"], r["actualHomeRuns"] + r["actualAwayRuns"])
        for r in rows if r.get(f"expectedTotal_{key_prefix}") is not None and r.get("actualHomeRuns") is not None and r.get("actualAwayRuns") is not None
    ]
    mae = round(sum(abs(exp - act) for exp, act in total_errors) / len(total_errors), 4) if total_errors else None
    rmse = round(math.sqrt(sum((exp - act) ** 2 for exp, act in total_errors) / len(total_errors)), 4) if total_errors else None
    nll_vals = [
        -math.log(max(poisson_pmf(int(act), exp), 1e-12))
        for exp, act in total_errors if exp is not None and exp > 0
    ]
    poisson_nll = round(sum(nll_vals) / len(nll_vals), 4) if nll_vals else None

    return {
        "n": n, "independentGames": independent_games,
        "sampleSizeStatus": sample_size_status(n, independent_games=independent_games),
        "gameMlBrier": ml_brier, "gameMlLogLoss": ml_logloss, "gameMlCalibrationECE": ml_ece,
        "totalOverBrier": total_brier, "totalOverLogLoss": total_logloss, "totalOverCalibrationECE": total_ece,
        "totalExpectedMAE": mae, "totalExpectedRMSE": rmse, "totalPoissonNLL": poisson_nll,
    }


def incremental_delta(rows_a, rows_b, key_a, key_b):
    """Paired (key_b minus key_a) delta with a game-clustered bootstrap
    CI, for BOTH the ML-Brier and total-Brier metrics, plus their mean
    (this milestone's own preregistered primary metric). rows_a/rows_b
    must be the SAME row objects (both key_a/key_b predictions already
    attached) -- callers always pass the identical row list twice."""
    assert rows_a is rows_b, "incremental_delta expects the SAME row list with both keys attached"
    rows = rows_a

    def _ml_delta(subset):
        pairs = [(r.get(f"mlProb_{key_a}"), r.get(f"mlProb_{key_b}"), r.get("actualHomeWin")) for r in subset]
        pairs = [(pa, pb, o) for pa, pb, o in pairs if pa is not None and pb is not None and o is not None]
        if not pairs:
            return None
        deltas = [((pb - o) ** 2) - ((pa - o) ** 2) for pa, pb, o in pairs]
        return sum(deltas) / len(deltas)

    def _total_delta(subset):
        pairs = [(r.get(f"totalOverProb_{key_a}"), r.get(f"totalOverProb_{key_b}"), r.get("actualOver85")) for r in subset]
        pairs = [(pa, pb, o) for pa, pb, o in pairs if pa is not None and pb is not None and o is not None]
        if not pairs:
            return None
        deltas = [((pb - o) ** 2) - ((pa - o) ** 2) for pa, pb, o in pairs]
        return sum(deltas) / len(deltas)

    ml_point = _ml_delta(rows)
    total_point = _total_delta(rows)
    ml_lo, ml_hi, _ = game_clustered_bootstrap_ci(rows, _ml_delta, cluster_key="gamePk", seed=DEFAULT_BOOTSTRAP_SEED)
    total_lo, total_hi, _ = game_clustered_bootstrap_ci(rows, _total_delta, cluster_key="gamePk", seed=DEFAULT_BOOTSTRAP_SEED)

    mean_point = None
    if ml_point is not None and total_point is not None:
        mean_point = round((ml_point + total_point) / 2, 6)

    return {
        "gameMlBrierDelta": round(ml_point, 6) if ml_point is not None else None,
        "gameMlBrierDeltaCI95": {"low": ml_lo, "high": ml_hi, "method": "GAME_CLUSTERED_BOOTSTRAP"},
        "totalOverBrierDelta": round(total_point, 6) if total_point is not None else None,
        "totalOverBrierDeltaCI95": {"low": total_lo, "high": total_hi, "method": "GAME_CLUSTERED_BOOTSTRAP"},
        "meanBrierDelta": mean_point,
    }


# ── Second stage: Pinnacle comparison (reuses MLB-RSCH-0008's cache) ────

def evaluate_against_pinnacle(pinnacle_rows, components, home_field_adjustment, key_prefix):
    """Attaches this composition's predictions to MLB-RSCH-0008's own
    already-matched Pinnacle rows (built by that script's own
    build_matched_rows/enrich_row, imported and reused UNCHANGED) and
    reuses lib.edgelab.paired_evaluation exactly as MLB-RSCH-0008 did,
    for direct numeric comparability."""
    import run_proxy_vs_pinnacle_experiment as rsch0008

    for r in pinnacle_rows:
        hb = baseline_for_components(r["homeBaseline"], r.get("homeOffenseStabilized"), r.get("homeBullpenStabilized"), components)
        ab = baseline_for_components(r["awayBaseline"], r.get("awayOffenseStabilized"), r.get("awayBullpenStabilized"), components)
        eh, ea = expected_runs(hb, ab, home_field_adjustment=home_field_adjustment)
        if "park" in components:
            eh, ea = apply_runs_multiplier(eh, ea, r.get("parkEnvMultiplier", 1.0))
        ml_prob, _push = game_ml_proxy_probability(eh, ea)
        r[f"proxyMlHomeProb_{key_prefix}"] = ml_prob
        if r.get("pinnacleTotalLine") is not None and eh is not None and ea is not None:
            r[f"proxyTotalOverProb_{key_prefix}"] = game_total_proxy_probability(eh, ea, r["pinnacleTotalLine"])
        else:
            r[f"proxyTotalOverProb_{key_prefix}"] = None

    ml_result = rsch0008.paired_analysis(pinnacle_rows, f"proxyMlHomeProb_{key_prefix}", "pinnacleMlHomeFair", "actualHomeWin", f"PINNACLE/ML/{key_prefix}")
    total_result = rsch0008.paired_analysis(pinnacle_rows, f"proxyTotalOverProb_{key_prefix}", "pinnacleTotalOverFair", "actualOver", f"PINNACLE/TOTAL/{key_prefix}")
    return ml_result, total_result


# ── main ──────────────────────────────────────────────────────────────

def main():
    experiment = register_experiment()[1]
    starter_verdict = load_starter_identity_verdict()

    rows_by_season = {}
    relief_by_season = {}
    team_games_by_season = {}
    env_lookup_by_season = {}
    for season in ALL_SEASONS:
        team_games = load_all_team_games_with_venue(season)
        team_games_by_season[season] = team_games
        relief_er9 = load_relief_er9_games(season, team_games)
        relief_by_season[season] = relief_er9
        env_lookup = build_season_environment_lookup([g for games in team_games.values() for g in games if g.get("side") == "home"])
        env_lookup_by_season[season] = env_lookup
        rows_by_season[season] = build_season_rows(season, team_games, relief_er9, env_lookup)

    dev_rows = [r for s in DEV_SEASONS for r in rows_by_season[s]]
    val_rows = [r for s in VALIDATION_SEASONS for r in rows_by_season[s]]
    holdout_rows = [r for s in HOLDOUT_SEASONS for r in rows_by_season[s]]
    all_rows = dev_rows + val_rows + holdout_rows

    # ---- Phase 1: fit shared, frozen, DEVELOPMENT-only constants ----
    dev_home_team_games = [g for s in DEV_SEASONS for g in team_games_by_season[s].values()]
    league_avg_offense = fit_league_average_runs_per_game(dev_home_team_games)
    dev_relief_er9_team_games = [g for s in DEV_SEASONS for g in relief_by_season[s].values()]
    league_avg_bullpen_er9 = fit_league_average_bullpen_er9(dev_relief_er9_team_games)

    attach_stabilized_components(all_rows, league_avg_offense, league_avg_bullpen_er9)

    dev_home_games_with_venue = [
        g for s in DEV_SEASONS for games in team_games_by_season[s].values() for g in games if g.get("side") == "home"
    ]
    park_factors = fit_park_factors(dev_home_games_with_venue)
    reference_env = fit_reference_season_run_environment(dev_home_games_with_venue)
    attach_park_multiplier(all_rows, park_factors, reference_env)

    # ---- Phase 2: forward-selection ablation (DEV+VAL only, 2026 untouched) ----
    current_components = frozenset()
    current_key = "M0"
    hfa_m0 = fit_home_field_adjustment_for_components(dev_rows, current_components)
    attach_predictions(all_rows, current_components, hfa_m0, current_key)
    frozen_hfa_by_key = {"M0": hfa_m0}
    accepted_sequence = ["M0"]
    contribution_table = []

    for candidate in CANDIDATE_SEQUENCE:
        trial_components = current_components | {candidate}
        trial_key = current_key + "+" + candidate
        trial_hfa = fit_home_field_adjustment_for_components(dev_rows, trial_components)
        attach_predictions(all_rows, trial_components, trial_hfa, trial_key)

        dev_delta = incremental_delta(dev_rows, dev_rows, current_key, trial_key)
        val_delta = incremental_delta(val_rows, val_rows, current_key, trial_key)

        keep = (
            dev_delta["meanBrierDelta"] is not None and dev_delta["meanBrierDelta"] < 0
            and val_delta["meanBrierDelta"] is not None and val_delta["meanBrierDelta"] <= DEGRADATION_TOLERANCE
        )
        contribution_table.append({
            "component": candidate,
            "developmentIncremental": dev_delta,
            "validationIncremental": val_delta,
            "decision": "KEEP" if keep else "REJECT",
        })
        if keep:
            current_components = trial_components
            current_key = trial_key
            frozen_hfa_by_key[current_key] = trial_hfa
            accepted_sequence.append(candidate)

    final_components = current_components
    final_key = current_key
    final_hfa = frozen_hfa_by_key[final_key]

    # ---- Phase 3: unlock 2026, evaluate M0 and the FROZEN final proxy only ----
    m0_holdout = evaluate_split(holdout_rows, "M0")
    final_holdout = evaluate_split(holdout_rows, final_key) if final_key != "M0" else m0_holdout

    m0_dev, m0_val = evaluate_split(dev_rows, "M0"), evaluate_split(val_rows, "M0")
    final_dev = evaluate_split(dev_rows, final_key)
    final_val = evaluate_split(val_rows, final_key)

    holdout_gain_vs_m0 = incremental_delta(holdout_rows, holdout_rows, "M0", final_key) if final_key != "M0" else None

    # ---- Phase 4: second-stage Pinnacle comparison (existing cache, no new acquisition) ----
    import run_proxy_vs_pinnacle_experiment as rsch0008
    from lib.edgelab.bullpen_usage import MLB_TEAM_ID_MAP

    # gamePk -> that game's own home-side schedule entry (carries
    # venueId/gameNumber/date/opponentTeamId) -- built from the SAME
    # already-loaded team_games_by_season this script's own baseball-
    # level rows use, so the Pinnacle-stage "final" proxy gets the exact
    # same bullpen/park signals as the baseball-level evaluation, never
    # a silently degraded subset.
    home_entry_by_pk_by_season = {
        season: {g["gamePk"]: g for games in team_games_by_season[season].values() for g in games if g.get("side") == "home"}
        for season in ALL_SEASONS
    }

    pinnacle_rows_by_season = {season: rsch0008.build_matched_rows(season) for season in ALL_SEASONS}
    for season, rows in pinnacle_rows_by_season.items():
        home_entry_by_pk = home_entry_by_pk_by_season[season]
        for r in rows:
            hb, ab = r["homeBaseline"], r["awayBaseline"]
            r["homeOffenseStabilized"] = stabilized_offense_rate(hb["offenseRunsPerGame"], hb["priorGamesThisSeason"], league_avg_offense)
            r["awayOffenseStabilized"] = stabilized_offense_rate(ab["offenseRunsPerGame"], ab["priorGamesThisSeason"], league_avg_offense)

            home_entry = home_entry_by_pk.get(r["gamePk"])
            if home_entry is None:
                r["homeBullpenStabilized"] = None
                r["awayBullpenStabilized"] = None
                r["parkEnvMultiplier"] = 1.0
                continue

            home_team_id = MLB_TEAM_ID_MAP.get(r["homeAbbr"])
            away_team_id = home_entry.get("opponentTeamId")
            home_bullpen_raw = bullpen_quality_baseline(relief_by_season[season].get(home_team_id, []), home_entry)
            away_bullpen_raw = bullpen_quality_baseline(relief_by_season[season].get(away_team_id, []), home_entry)
            r["homeBullpenStabilized"] = (
                stabilized_bullpen_rate(home_bullpen_raw["bullpenEarnedRunsPer9"], home_bullpen_raw["priorGamesWithBullpenData"], league_avg_bullpen_er9)
                if home_bullpen_raw else None
            )
            r["awayBullpenStabilized"] = (
                stabilized_bullpen_rate(away_bullpen_raw["bullpenEarnedRunsPer9"], away_bullpen_raw["priorGamesWithBullpenData"], league_avg_bullpen_er9)
                if away_bullpen_raw else None
            )
            r["parkEnvMultiplier"] = park_and_environment_multiplier(
                home_entry.get("venueId"), park_factors, env_lookup_by_season[season](r["date"]), reference_env
            )

    pinnacle_dev_rows = [r for s in DEV_SEASONS for r in pinnacle_rows_by_season[s]]
    pinnacle_val_rows = [r for s in VALIDATION_SEASONS for r in pinnacle_rows_by_season[s]]
    pinnacle_holdout_rows = [r for s in HOLDOUT_SEASONS for r in pinnacle_rows_by_season[s]]
    pinnacle_all_rows = pinnacle_dev_rows + pinnacle_val_rows + pinnacle_holdout_rows

    # rsch0008.enrich_row (UNCHANGED) populates the Pinnacle-side de-vigged
    # fields (pinnacleMlHomeFair, pinnacleTotalLine, pinnacleTotalOverFair,
    # actualHomeWin, actualOver) every paired_analysis call below needs --
    # never computed by this script itself, always the SAME reused
    # de-vig/exact-line-matching logic MLB-RSCH-0008 already validated.
    # Its own proxyMlHomeProb/proxyTotalOverProb output (computed from the
    # RAW M0 baseline with hfa_m0) doubles as this milestone's own M0
    # prediction, so M0 needs no separate prediction pass.
    for r in pinnacle_all_rows:
        rsch0008.enrich_row(r, hfa_m0)

    m0_ml_pinnacle = rsch0008.paired_analysis(pinnacle_all_rows, "proxyMlHomeProb", "pinnacleMlHomeFair", "actualHomeWin", "PINNACLE/ML/M0")
    m0_total_pinnacle = rsch0008.paired_analysis(pinnacle_all_rows, "proxyTotalOverProb", "pinnacleTotalOverFair", "actualOver", "PINNACLE/TOTAL/M0")
    final_ml_pinnacle, final_total_pinnacle = evaluate_against_pinnacle(
        pinnacle_all_rows, final_components, final_hfa, "FINAL"
    ) if final_key != "M0" else (m0_ml_pinnacle, m0_total_pinnacle)

    def _brier_gap(result):
        """proxy Brier minus Pinnacle Brier -- positive = Pinnacle better,
        matching MLB-RSCH-0008's own sign convention exactly."""
        if result.get("proxyBrierScore") is None or result.get("pinnacleBrierScore") is None:
            return None
        return round(result["proxyBrierScore"] - result["pinnacleBrierScore"], 6)

    ml_gap_m0 = _brier_gap(m0_ml_pinnacle)
    ml_gap_final = _brier_gap(final_ml_pinnacle)
    ml_gap_closed = round(ml_gap_m0 - ml_gap_final, 6) if ml_gap_m0 is not None and ml_gap_final is not None else None

    total_gap_m0 = _brier_gap(m0_total_pinnacle)
    total_gap_final = _brier_gap(final_total_pinnacle)
    total_gap_closed = round(total_gap_m0 - total_gap_final, 6) if total_gap_m0 is not None and total_gap_final is not None else None

    disposition = disp.RESEARCH_CANDIDATE

    report = {
        "experimentId": EXPERIMENT_ID,
        "evidenceLevel": experiment["evidenceLevel"],
        "generatedAt": REGISTRATION_TIMESTAMP,
        "starterIdentityVerdict": starter_verdict,
        "frozenConstants": {
            "leagueAverageOffenseRunsPerGame": league_avg_offense,
            "leagueAverageBullpenEarnedRunsPer9": league_avg_bullpen_er9,
            "parkFactorsVenueCount": len(park_factors),
            "referenceSeasonRunEnvironment": reference_env,
            "homeFieldAdjustmentByComposition": frozen_hfa_by_key,
        },
        "acceptedSequence": accepted_sequence,
        "finalComponents": sorted(final_components),
        "contributionTable": contribution_table,
        "baseball": {
            "development": {"M0": m0_dev, "final": final_dev},
            "validation": {"M0": m0_val, "final": final_val},
            "holdout": {"M0": m0_holdout, "final": final_holdout},
            "holdoutGainVsM0": holdout_gain_vs_m0,
        },
        "pinnacle": {
            "gameMl": {"M0": m0_ml_pinnacle, "final": final_ml_pinnacle, "gapM0": ml_gap_m0, "gapFinal": ml_gap_final, "gapClosed": ml_gap_closed},
            "gameTotal": {"M0": m0_total_pinnacle, "final": final_total_pinnacle, "gapM0": total_gap_m0, "gapFinal": total_gap_final, "gapClosed": total_gap_closed},
        },
        "disposition": disposition,
        "productionBehaviorChanged": False,
    }
    print(json.dumps(report, indent=2, default=str))

    os.makedirs(os.path.join(EDGELAB_DIR, "analytics"), exist_ok=True)
    with open(os.path.join(EDGELAB_DIR, "analytics", "latest_mlb_rsch_0009_proxy_ablation.json"), "w") as f:
        json.dump(report, f, indent=2, default=str)
    return report


if __name__ == "__main__":
    main()
