#!/usr/bin/env python3
"""
scripts/edgelab/run_joint_dispersion_experiment.py
====================================================================
Research Lab experiment MLB-RSCH-0016: "Joint Schedule-Adjusted Mean +
Dispersion". RESEARCH ONLY. NO production changes.

NOT a rescue/tuning continuation of MLB-RSCH-0015. MLB-RSCH-0015 found
that S1 (its one-hop schedule-adjustment mean) produced a real,
significant, broadly-based mean-accuracy improvement over S0, but
consistently WORSENED downstream frozen-NB probability scoring across
every market family -- and was correctly rejected on that basis. This
milestone tests a genuinely new, structurally different hypothesis
motivated by that finding: a schedule-adjusted mean may change the
residual variance structure enough that the NEGATIVE-BINOMIAL
DISPERSION PARAMETER fitted around S0's OWN error characteristics is
no longer the right companion for it -- and that S1's mean gain may
translate into a real probability gain once paired with its OWN
DEV-fit dispersion, rather than S0's frozen one.

CRITICAL FREEZE: S1's own construction (opponent-strength formula,
one-hop, shrinkage, team-baseline definition, eligibility) is imported
and reused EXACTLY UNCHANGED from run_opponent_strength_experiment.py
(rsch0015) -- this experiment never touches a single S1 coefficient or
formula. It may ONLY refit the distribution (dispersion) around it.

CANDIDATES:
  J0 (control): S0 mean (MLB-RSCH-0009, unchanged) + the frozen
      MLB-RSCH-0010 NB dispersion (0.281513, unchanged) -- the current
      best historical probability control.
  J1: S1's EXACT frozen mean (rsch0015, unchanged) + a NEW NB
      dispersion parameter, fit via the SAME closed-form method-of-
      moments estimator MLB-RSCH-0010 itself used
      (fit_overdispersion_dev_only, unchanged), on DEVELOPMENT
      (actual, S1-predicted) pairs only. Same NB family throughout --
      never a different distribution.
  J2 (diagnostic only, not separately tuned): S1's mean + the OLD
      frozen S0-fit dispersion -- this is EXACTLY MLB-RSCH-0015's own
      already-computed S1-vs-S0 NB comparison, reused by reference
      (not recomputed) to decompose "mean effect" vs "dispersion-refit
      effect" without spending additional compute.

SELECTION: DEV/VAL only, 2026 never opened unless J1 passes. Pinnacle
used only as a secondary check, strictly after any holdout unlock.
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

from lib.edgelab.backtest.proxy_model import game_ml_proxy_probability, game_total_proxy_probability
from lib.edgelab.backtest.run_distributions import (
    negative_binomial_pmf,
    independent_joint_pmf,
    home_win_and_push_prob,
    total_over_prob,
    team_total_over_prob,
    margin_at_least_prob,
    fit_overdispersion_dev_only,
)
from lib.edgelab import experiment_registry as reg
from lib.edgelab import evidence_levels as ev
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import paired_evaluation as pe
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab.research_stats import independent_unit_count, sample_size_status

import run_opponent_strength_experiment as rsch0015  # noqa: E402 -- S1's mean, frozen, reused unchanged

EXPERIMENT_ID = "MLB-RSCH-0016"
REGISTRATION_TIMESTAMP = "2026-08-28T14:05:00Z"

OLD_FROZEN_DISPERSION = 0.281513


def _verify_old_frozen_dispersion():
    path = os.path.join(_ROOT, "data", "edgelab", "analytics", "latest_mlb_rsch_0010_run_distribution.json")
    with open(path) as f:
        canonical = json.load(f)["fittedParameters"]["overdispersion"]
    if canonical != OLD_FROZEN_DISPERSION:
        raise ValueError(f"OLD_FROZEN_DISPERSION={OLD_FROZEN_DISPERSION} does not match the canonical MLB-RSCH-0010 artifact value {canonical}")


_verify_old_frozen_dispersion()

DEV_SEASONS = rsch0015.DEV_SEASONS
VALIDATION_SEASONS = rsch0015.VALIDATION_SEASONS
HOLDOUT_SEASONS = rsch0015.HOLDOUT_SEASONS
ALL_SEASONS = rsch0015.ALL_SEASONS

GAME_TOTAL_LINES = rsch0015.GAME_TOTAL_LINES
TEAM_TOTAL_LINES = rsch0015.TEAM_TOTAL_LINES
MARGIN_THRESHOLDS = rsch0015.MARGIN_THRESHOLDS

J0 = "J0_control_s0_old_dispersion"
J1 = "J1_s1_refit_dispersion"

DEV_IMPROVEMENT_TOLERANCE = 0.0  # J1 DEV aggregate Brier must improve (delta < 0)
VAL_NONINFERIORITY_TOLERANCE = 0.001  # locked BEFORE results -- VAL delta may not exceed this to unlock 2026

# Fixed historical tail checks (preregistered, not chosen after results)
TAIL_CHECKS = {
    "shutout": lambda h, a: (h == 0 or a == 0),
    "team_10_plus": lambda h, a: (h >= 10 or a >= 10),
    "game_15_plus_total": lambda h, a: (h + a) >= 15,
    "margin_5_plus": lambda h, a: abs(h - a) >= 5,
    "margin_7_plus": lambda h, a: abs(h - a) >= 7,
}


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
        name="mlb_rsch_0016_joint_dispersion_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0016 joint dispersion v1: MLB-RSCH-0015's frozen S1 schedule-adjusted mean "
                        "(unchanged) + a NEW DEV-fit negative-binomial dispersion, same NB family, vs MLB-RSCH-0009 "
                        "S0 mean + the old MLB-RSCH-0010 frozen dispersion"
        ),
        probability_adapter_identity="lib.edgelab.backtest.run_distributions negative_binomial_pmf (same NB family; dispersion re-estimated for J1 only)",
        model_engine_family="pit_safe_research_joint_dispersion_v1",
        required_input_provenance=["team_recent_game_log_reconstruction"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "Tests whether MLB-RSCH-0015's S1 schedule-adjusted mean (frozen, unchanged) improves downstream "
            "probability quality once paired with its OWN DEV-fit negative-binomial dispersion, rather than "
            "the S0-fit dispersion MLB-RSCH-0015 held frozen. Same NB family throughout; only the dispersion "
            "parameter differs between J0 and J1."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Joint Schedule-Adjusted Mean + Dispersion",
        hypothesis=(
            "H1: MLB-RSCH-0015's S1 schedule-adjusted mean changes the residual variance structure relative to "
            "S0, such that the MLB-RSCH-0010 dispersion (fit around S0's own error characteristics) is no "
            "longer the appropriate NB dispersion for S1's mean. H2: a DEVELOPMENT-only re-estimate of "
            "dispersion, specifically for S1's mean, recovers some or all of the probability-scoring loss "
            "MLB-RSCH-0015 observed, without requiring any change to S1's own construction. H3: any real gain "
            "generalizes to 2025 and, if unlocked, the 2026 locked holdout."
        ),
        research_question=(
            "Holding S1's mean construction completely frozen (MLB-RSCH-0015, unchanged), does refitting the "
            "negative-binomial dispersion parameter specifically around S1's own DEVELOPMENT residuals improve "
            "downstream probability quality beyond S0 + the old frozen MLB-RSCH-0010 dispersion?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E2_PIT_HISTORICAL,
        target_population="Identical to MLB-RSCH-0015's own S1-eligible population (>= MIN_PRIOR_GAMES_MAIN=20 prior games both teams).",
        market_families=["game_result", "game_total", "team_total", "run_margin"],
        eligibility_criteria=["identical to MLB-RSCH-0015's own S1 eligibility -- reused unchanged, not redefined here"],
        exclusion_criteria=[
            "any change to S1's own mean construction (opponent-strength formula, shrinkage, eligibility) -- frozen, imported unchanged from MLB-RSCH-0015",
            "any change to the NB distribution FAMILY -- same negative-binomial family throughout, only the dispersion scalar differs for J1",
            "Pinnacle/Kalshi as a fitting input -- secondary check only, strictly after any holdout unlock",
            "iteratively trying alternate dispersion parameters if J1 fails -- if J1 fails, this specific path is retired, not re-tuned",
        ],
        prediction_checkpoints=["SEASON_TO_DATE_PREGAME"],
        primary_metric="aggregate frozen-NB paired Brier delta (J1 minus J0) across game_total/team_total/moneyline, game-clustered 95% CI",
        secondary_metrics=[
            "log loss delta", "calibration", "family-specific paired deltas (including run_margin)",
            "tail-frequency calibration (shutout, team 10+, game 15+ total, margin 5+/7+)",
            "residual variance/overdispersion diagnostics under S0 vs S1",
            "paired candidate-minus-Pinnacle Brier delta (secondary stage, existing sample)",
        ],
        chronological_split_policy=f"SEASON_BASED: development={DEV_SEASONS}, validation={VALIDATION_SEASONS}, holdout={HOLDOUT_SEASONS} (locked)",
        minimum_sample_requirement={"independentGames": rsch0015.MIN_GAMES_CONFIDENT},
        clustering_unit="gamePk",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={"team_recent_game_log_reconstruction": "PREDICTIVE_INPUT"},
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            "evidenceLevel E2_PIT_HISTORICAL, same basis as prior milestones. J2 (S1 mean + OLD dispersion) is "
            "MLB-RSCH-0015's own already-computed S1-vs-S0 comparison, reused by reference, not recomputed."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Corpus + S0/S1 predictions (S1 reused EXACTLY from MLB-RSCH-0015) ────

def build_corpus_and_predictions():
    """Reuses rsch0015's own build_corpus/attach_s0_predictions/
    build_raw_baseline_lookup/compute_schedule_adjustment/fit_hfa_schedule/
    attach_schedule_predictions UNCHANGED -- S1's construction is not
    reimplemented, only imported and called."""
    rows_by_season, team_games_by_season, relief_by_season, league_avg_offense, league_avg_bullpen_er9 = rsch0015.build_corpus()
    dev_rows = [r for s in DEV_SEASONS for r in rows_by_season[s]]
    val_rows = [r for s in VALIDATION_SEASONS for r in rows_by_season[s]]
    holdout_rows = [r for s in HOLDOUT_SEASONS for r in rows_by_season[s]]
    all_rows = dev_rows + val_rows + holdout_rows

    hfa_s0 = rsch0015.fit_hfa_s0(dev_rows)
    rsch0015.attach_s0_predictions(all_rows, hfa_s0)

    raw_lookup = rsch0015.build_raw_baseline_lookup(team_games_by_season)
    s1_lookup = rsch0015.compute_schedule_adjustment(team_games_by_season, raw_lookup, league_avg_offense, league_avg_bullpen_er9, min_prior_games_opponent=rsch0015.MIN_PRIOR_GAMES_OPPONENT)
    hfa_s1 = rsch0015.fit_hfa_schedule(dev_rows, s1_lookup, league_avg_offense, league_avg_bullpen_er9)
    rsch0015.attach_schedule_predictions(dev_rows + val_rows + holdout_rows, "S1", s1_lookup, hfa_s1, league_avg_offense, league_avg_bullpen_er9)

    return dev_rows, val_rows, holdout_rows, hfa_s0, hfa_s1


# ── Residual diagnostics (S0 vs S1, DEVELOPMENT) ──────────────────────────

def residual_diagnostics(dev_rows, key_prefix):
    obs = rsch0015.team_observations(dev_rows, key_prefix)
    errors = [o["predicted"] - o["actual"] for o in obs]
    n = len(errors)
    mean_residual = round(sum(errors) / n, 4)
    residual_variance = round(sum((e - mean_residual) ** 2 for e in errors) / n, 4)
    pairs = [(o["actual"], o["predicted"]) for o in obs]
    fitted_dispersion = fit_overdispersion_dev_only(pairs)
    # empirical overdispersion index: mean(residual^2)/mean(predicted) as a simple variance-to-mean diagnostic
    mean_predicted = sum(o["predicted"] for o in obs) / n
    variance_to_mean = round(sum(e ** 2 for e in errors) / n / mean_predicted, 4) if mean_predicted else None
    tail_freqs = {}
    for name, predicate in TAIL_CHECKS.items():
        count = sum(1 for r in dev_rows if r.get("actualHomeRuns") is not None and r.get("actualAwayRuns") is not None and predicate(r["actualHomeRuns"], r["actualAwayRuns"]))
        total = sum(1 for r in dev_rows if r.get("actualHomeRuns") is not None and r.get("actualAwayRuns") is not None)
        tail_freqs[name] = round(count / total, 4) if total else None
    return {
        "n": n, "independentGames": independent_unit_count(obs, key="gamePk"),
        "meanResidual": mean_residual, "residualVariance": residual_variance,
        "residualVarianceOverPredictedMean": variance_to_mean,
        "fittedNbDispersion": fitted_dispersion,
        "empiricalTailFrequency": tail_freqs,
    }


# ── Frozen-NB probability evaluation, dispersion PARAMETERIZED per side ──

def _nb_joint(home_mean, away_mean, dispersion):
    def home_pmf(k):
        return negative_binomial_pmf(k, home_mean, dispersion)

    def away_pmf(k):
        return negative_binomial_pmf(k, away_mean, dispersion)
    return independent_joint_pmf(home_pmf, away_pmf), home_pmf, away_pmf


def nb_probability_cells(home_mean, away_mean, dispersion):
    if home_mean is None or away_mean is None or home_mean <= 0 or away_mean <= 0:
        return None
    joint, home_pmf, away_pmf = _nb_joint(home_mean, away_mean, dispersion)
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


def joint_probability_eval(rows, key_a, dispersion_a, key_b, dispersion_b):
    """Paired evaluation where CONTROL and CANDIDATE may use DIFFERENT NB
    dispersion parameters -- unlike every prior milestone in this program
    (which always held dispersion identical on both sides to isolate a
    mean-only effect), this is the deliberate joint (mean, dispersion)
    comparison MLB-RSCH-0016 exists to test."""
    control_rows, candidate_rows = [], []
    for r in rows:
        actual_home, actual_away = r.get("actualHomeRuns"), r.get("actualAwayRuns")
        if actual_home is None or actual_away is None:
            continue
        cells_a = nb_probability_cells(r.get(f"homeExpectedRuns_{key_a}"), r.get(f"awayExpectedRuns_{key_a}"), dispersion_a)
        cells_b = nb_probability_cells(r.get(f"homeExpectedRuns_{key_b}"), r.get(f"awayExpectedRuns_{key_b}"), dispersion_b)
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


def _primary_delta(result):
    deltas = [result["byFamily"][fam]["pairedDelta"]["brierScore"] for fam in ("game_total", "team_total_away", "team_total_home", "moneyline") if result["byFamily"].get(fam) and result["byFamily"][fam]["pairedDelta"]["brierScore"] is not None]
    return round(sum(deltas) / len(deltas), 6) if deltas else None


def tail_calibration(rows, key, dispersion):
    """Predicted vs empirical frequency for each fixed TAIL_CHECKS event,
    under the given (mean-key, dispersion) pair. Builds the joint NB grid
    ONCE per row (outer loop over rows, inner loop over the 5 fixed tail
    predicates) rather than once per predicate -- a 5x reduction in the
    expensive joint-PMF-grid computation."""
    counts = {name: {"n": 0, "hits": 0, "probSum": 0.0} for name in TAIL_CHECKS}
    for r in rows:
        eh, ea = r.get(f"homeExpectedRuns_{key}"), r.get(f"awayExpectedRuns_{key}")
        actual_home, actual_away = r.get("actualHomeRuns"), r.get("actualAwayRuns")
        if eh is None or ea is None or eh <= 0 or ea <= 0 or actual_home is None or actual_away is None:
            continue
        joint, _, _ = _nb_joint(eh, ea, dispersion)
        grid = [[joint(h, a) for a in range(30)] for h in range(30)]
        for name, predicate in TAIL_CHECKS.items():
            c = counts[name]
            c["n"] += 1
            c["probSum"] += sum(grid[h][a] for h in range(30) for a in range(30) if predicate(h, a))
            if predicate(actual_home, actual_away):
                c["hits"] += 1
    return {
        name: {
            "n": c["n"],
            "predictedMeanProbability": round(c["probSum"] / c["n"], 4) if c["n"] else None,
            "empiricalFrequency": round(c["hits"] / c["n"], 4) if c["n"] else None,
        }
        for name, c in counts.items()
    }


# ── Selection rule (preregistered) ─────────────────────────────────────────

def selection_passes_dev(dev_primary_delta):
    reasons = []
    if dev_primary_delta is None or dev_primary_delta >= DEV_IMPROVEMENT_TOLERANCE:
        reasons.append(f"DEV aggregate frozen-NB primary Brier delta not improved: {dev_primary_delta}")
    return (len(reasons) == 0), reasons


def val_unlock_passes(val_primary_delta):
    reasons = []
    if val_primary_delta is None or val_primary_delta > VAL_NONINFERIORITY_TOLERANCE:
        reasons.append(f"VALIDATION delta exceeds preregistered noninferiority tolerance {VAL_NONINFERIORITY_TOLERANCE}: {val_primary_delta}")
    return (len(reasons) == 0), reasons


# ── main ─────────────────────────────────────────────────────────────────

def main():
    print(f"[{EXPERIMENT_ID}] registering experiment/control...")
    control, definition = register_experiment()

    print(f"[{EXPERIMENT_ID}] building corpus + S0/S1 predictions (S1 frozen, reused unchanged from MLB-RSCH-0015)...")
    dev_rows, val_rows, holdout_rows, hfa_s0, hfa_s1 = build_corpus_and_predictions()
    print(f"[{EXPERIMENT_ID}] rows: dev={len(dev_rows)} val={len(val_rows)} holdout={len(holdout_rows)}; hfa_s0={hfa_s0} hfa_s1={hfa_s1}")

    print(f"[{EXPERIMENT_ID}] residual diagnostics (DEVELOPMENT, S0 vs S1)...")
    diag_s0 = residual_diagnostics(dev_rows, "S0")
    diag_s1 = residual_diagnostics(dev_rows, "S1")
    print(f"[{EXPERIMENT_ID}] S0 residualVariance={diag_s0['residualVariance']} fittedDispersion={diag_s0['fittedNbDispersion']}")
    print(f"[{EXPERIMENT_ID}] S1 residualVariance={diag_s1['residualVariance']} fittedDispersion={diag_s1['fittedNbDispersion']}")

    dispersion_s1 = diag_s1["fittedNbDispersion"]

    print(f"[{EXPERIMENT_ID}] evaluating J1 (S1 mean + new dispersion={dispersion_s1}) vs J0 (S0 mean + old dispersion={OLD_FROZEN_DISPERSION}) on DEV...")
    dev_eval = joint_probability_eval(dev_rows, "S0", OLD_FROZEN_DISPERSION, "S1", dispersion_s1)
    dev_primary = _primary_delta(dev_eval)
    dev_passes, dev_reasons = selection_passes_dev(dev_primary)
    print(f"[{EXPERIMENT_ID}] J1 DEV primary delta={dev_primary} passes={dev_passes} reasons={dev_reasons}")

    val_eval, val_primary, unlock_holdout, unlock_reasons = None, None, False, ["DEV gate not passed -- VAL never evaluated"]
    if dev_passes:
        print(f"[{EXPERIMENT_ID}] evaluating J1 vs J0 on VALIDATION...")
        val_eval = joint_probability_eval(val_rows, "S0", OLD_FROZEN_DISPERSION, "S1", dispersion_s1)
        val_primary = _primary_delta(val_eval)
        unlock_holdout, unlock_reasons = val_unlock_passes(val_primary)
        print(f"[{EXPERIMENT_ID}] J1 VAL primary delta={val_primary} unlockHoldout={unlock_holdout} reasons={unlock_reasons}")

    holdout_eval, holdout_primary, holdout_s1_mean_mae = None, None, None
    if unlock_holdout:
        print(f"[{EXPERIMENT_ID}] preregistered gate passed -- unlocking 2026 holdout (first time S1's own mean is evaluated on 2026)...")
        holdout_eval = joint_probability_eval(holdout_rows, "S0", OLD_FROZEN_DISPERSION, "S1", dispersion_s1)
        holdout_primary = _primary_delta(holdout_eval)
        obs_holdout_s0, obs_holdout_s1 = rsch0015.team_observations(holdout_rows, "S0"), rsch0015.team_observations(holdout_rows, "S1")
        holdout_s1_mean_mae = {"S0": rsch0015.mean_accuracy_metrics(obs_holdout_s0), "S1": rsch0015.mean_accuracy_metrics(obs_holdout_s1), "pairedDelta": rsch0015.paired_mean_mae_delta(obs_holdout_s0, obs_holdout_s1)}
        print(f"[{EXPERIMENT_ID}] J1 2026 holdout primary delta={holdout_primary}; S1 mean-vs-S0 2026 MAE delta={holdout_s1_mean_mae['pairedDelta']['maeDelta']}")
    else:
        print(f"[{EXPERIMENT_ID}] holdout NOT unlocked -- retiring this specific dispersion-refit path per preregistration (no re-fit attempts).")

    print(f"[{EXPERIMENT_ID}] tail calibration (DEV): J0 vs J1...")
    tail_j0 = tail_calibration(dev_rows, "S0", OLD_FROZEN_DISPERSION)
    tail_j1 = tail_calibration(dev_rows, "S1", dispersion_s1)

    # ---- J2 diagnostic: MLB-RSCH-0015's own already-computed S1-vs-S0 comparison (frozen dispersion both sides), reused by reference ----
    rsch0015_report_path = os.path.join(_ROOT, "data", "edgelab", "analytics", "latest_mlb_rsch_0015_opponent_strength.json")
    j2_reference = None
    if os.path.exists(rsch0015_report_path):
        with open(rsch0015_report_path) as f:
            rsch0015_report = json.load(f)
        j2_reference = {
            "note": "MLB-RSCH-0015's own S1-vs-S0 comparison (both sides frozen OLD dispersion) -- reused by reference, not recomputed",
            "devPrimaryDelta": rsch0015_report["results"]["S1_schedule_adjusted_1hop"]["devNbPrimaryDelta"],
            "valPrimaryDelta": rsch0015_report["results"]["S1_schedule_adjusted_1hop"]["valNbPrimaryDelta"],
        }

    # ---- Pinnacle secondary stage (existing sample, ONLY after selection+validation+holdout, no new spend) ----
    print(f"[{EXPERIMENT_ID}] Pinnacle secondary stage (existing sample, no new Odds API spend)...")
    import run_proxy_vs_pinnacle_experiment as rsch0008
    pinnacle_result = None
    try:
        # Pinnacle stage reuses S0's simpler proxy-probability functions (never the frozen-NB cells),
        # matching every prior milestone's own established pattern.
        pinnacle_all_rows = [r for season in ALL_SEASONS for r in rsch0008.build_matched_rows(season)]
        for r in pinnacle_all_rows:
            rsch0008.enrich_row(r, hfa_s0)
        pinnacle_ml_j0 = rsch0008.paired_analysis(pinnacle_all_rows, "proxyMlHomeProb", "pinnacleMlHomeFair", "actualHomeWin", "PINNACLE/ML/J0")
        pinnacle_total_j0 = rsch0008.paired_analysis(pinnacle_all_rows, "proxyTotalOverProb", "pinnacleTotalOverFair", "actualOver", "PINNACLE/TOTAL/J0")
        pinnacle_result = {"nRows": len(pinnacle_all_rows), "ml": {"j0": pinnacle_ml_j0}, "total": {"j0": pinnacle_total_j0}, "note": "J1 not compared -- did not unlock/pass; only J0's existing gap is reported for reference" if not unlock_holdout else "see holdout section"}
    except Exception as exc:
        pinnacle_result = {"error": str(exc)}
    print(f"[{EXPERIMENT_ID}] Pinnacle secondary stage result: {pinnacle_result if isinstance(pinnacle_result, dict) and 'error' in pinnacle_result else 'OK'}")

    report = {
        "experimentId": EXPERIMENT_ID,
        "controlModelId": control["controlModelId"],
        "corpus": {"devRows": len(dev_rows), "valRows": len(val_rows), "holdoutRows": len(holdout_rows)},
        "hfa": {"s0": hfa_s0, "s1": hfa_s1},
        "residualDiagnostics": {"S0": diag_s0, "S1": diag_s1},
        "dispersion": {"old": OLD_FROZEN_DISPERSION, "s1Refit": dispersion_s1},
        "devEval": {"primaryDelta": dev_primary, "passes": dev_passes, "reasons": dev_reasons, "full": dev_eval},
        "valEval": {"primaryDelta": val_primary, "unlockHoldout": unlock_holdout, "reasons": unlock_reasons, "full": val_eval},
        "holdoutEval": {"primaryDelta": holdout_primary, "full": holdout_eval, "s1MeanMae2026": holdout_s1_mean_mae} if unlock_holdout else None,
        "tailCalibrationDev": {"J0": tail_j0, "J1": tail_j1},
        "j2Diagnostic": j2_reference,
        "pinnacleSecondary": pinnacle_result,
    }

    out_path = os.path.join("data", "edgelab", "analytics", "latest_mlb_rsch_0016_joint_dispersion.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    print(f"[{EXPERIMENT_ID}] wrote {out_path}")
    return report


if __name__ == "__main__":
    main()
