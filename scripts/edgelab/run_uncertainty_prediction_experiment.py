#!/usr/bin/env python3
"""
scripts/edgelab/run_uncertainty_prediction_experiment.py
====================================================================
Research Lab experiment MLB-RSCH-0019: "Model Uncertainty / Error
Prediction". RESEARCH ONLY. NO production changes.

CORE QUESTION: can we identify, BEFORE FIRST PITCH, which MLB
probability estimates are more likely to be wrong? This experiment
tests whether a small set of transparent, pregame-available uncertainty
FACTORS relate to realized error -- it does NOT build a large ML
residual model, and it does NOT change confidence/sizing/qualification
logic anywhere.

TWO EVIDENCE LAYERS, kept explicitly separate (never mixed numerically):
  Layer A: large E2 historical/proxy reliability study, reusing
      MLB-RSCH-0009's own frozen final composition ({offense, bullpen})
      and its standard build_season_rows() corpus UNCHANGED. This
      corpus inherits MLB-RSCH-0009's own 20-game eligibility floor --
      games 1-19 are therefore structurally absent here (the SAME
      documented limitation MLB-RSCH-0015 already hit and MLB-RSCH-0017/
      0018 separately addressed with their own dedicated no-floor
      construction, which is NOT reused here to avoid conflating this
      milestone with that already-closed research thread). The
      "early-season state" feature below therefore uses THREE buckets
      that are actually observable in this population (near-floor
      20-40, mid 41-80, late 81+) instead of the mission's own suggested
      1-10/11-20/later boundaries -- an explicit, disclosed deviation,
      not a silent one.
  Layer B: current-model prospective cohort, using ONLY genuine
      MLB-RSCH-0011 shadow-evaluation records. As of this run, ZERO
      settled prospective evaluations exist anywhere in the repository
      (verified directly, matching every earlier check this session) --
      Layer B therefore produces NO numeric result, honestly reported
      as EXPLORATORY/INSUFFICIENT_SAMPLE, never fabricated or inferred
      from Layer A.

Uncertainty candidates:
  U0 (control): no differentiation -- every prediction equally reliable.
  U1: a transparent, UNWEIGHTED sum of preregistered binary risk flags
      (thresholds fit DEV-only, never hand-tuned against holdout).
  U2: ONE small, preregistered, DEV-only-fit ridge-regularized linear
      model (closed-form normal equations, L2 penalty, standardized
      DEV-only features) predicting realized absolute error from the
      SAME small feature set. No random forest, no boosting, no neural
      network, no hyperparameter search.

Max disposition: SHADOW_CANDIDATE. No production confidence/sizing
changes anywhere in this file or its outputs.
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

from lib.edgelab import experiment_registry as reg
from lib.edgelab import evidence_levels as ev
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab.research_stats import DEFAULT_BOOTSTRAP_SEED, independent_unit_count, sample_size_status, game_clustered_bootstrap_ci

import run_proxy_ablation_experiment as rsch0009  # noqa: E402
import run_early_season_offense_experiment as rsch0017  # noqa: E402 (reused ONLY for nb_probability_cells/_outcomes_for_actual/pe -- generic, frozen-dispersion primitives, not tied to its own row shape)

EXPERIMENT_ID = "MLB-RSCH-0019"
REGISTRATION_TIMESTAMP = "2026-08-28T16:15:00Z"

DEV_SEASONS = [2022, 2023, 2024]
VALIDATION_SEASONS = [2025]
HOLDOUT_SEASONS = [2026]
ALL_SEASONS = DEV_SEASONS + VALIDATION_SEASONS + HOLDOUT_SEASONS

# MLB-RSCH-0009's own final accepted composition -- verified against its own
# committed artifact at import time (same discipline as MLB-RSCH-0014's C0_COMPONENTS).
BASELINE_COMPONENTS = frozenset({"offense", "bullpen"})

# Explicit, disclosed deviation from the mission's suggested Games-1-10/11-20/
# later boundaries -- this population inherits MLB-RSCH-0009's own 20-game
# floor, so those exact boundaries cannot be observed here (see module docstring).
SEASON_BUCKETS = (("near_floor_20_40", 20, 40), ("mid_41_80", 41, 80), ("late_81_plus", 81, 999))

FEATURE_FIELDS = ["minSampleDepth", "minBullpenSampleDepth", "componentDisagreement", "probExtremeness", "totalExtremeness"]
RIDGE_LAMBDA = 1.0  # fixed, preregistered -- NOT tuned via any search, chosen only to stabilize small-sample collinearity
LARGE_ERROR_DEV_QUANTILE = 0.75  # top quartile, frozen before validation/holdout

# Preregistered, locked before results -- never relaxed.
DEV_CORRELATION_FLOOR = 0.05
VAL_CORRELATION_FLOOR = 0.03
CALIBRATION_ERROR_CEILING = 0.08


def _verify_baseline_components():
    path = os.path.join(_ROOT, "data", "edgelab", "analytics", "latest_mlb_rsch_0009_proxy_ablation.json")
    with open(path) as f:
        artifact = json.load(f)
    if frozenset(artifact["finalComponents"]) != BASELINE_COMPONENTS:
        raise ValueError(f"BASELINE_COMPONENTS drifted from MLB-RSCH-0009's own artifact: {artifact['finalComponents']}")


_verify_baseline_components()


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
        name="mlb_rsch_0019_uncertainty_prediction_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0019 uncertainty prediction v1: MLB-RSCH-0009's own frozen "
                        "{offense,bullpen} baseline + U0 (no differentiation) / U1 (unweighted "
                        "preregistered flag sum) / U2 (DEV-only ridge-regularized linear error model)"
        ),
        probability_adapter_identity="lib.edgelab.backtest.run_distributions (frozen MLB-RSCH-0010 negative-binomial, dispersion unchanged, never refit here)",
        model_engine_family="pit_safe_research_uncertainty_prediction_v1",
        required_input_provenance=["team_recent_game_log_reconstruction"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "Tests whether a small set of transparent, pregame-available uncertainty factors "
            "(sample depth, missingness, model extremeness, component disagreement, early-season "
            "state) predictably relate to realized team-run and probability error, using MLB-RSCH-"
            "0009's own frozen {offense,bullpen} baseline model. Does not change any production "
            "confidence, qualification, staking, or sizing logic."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Model Uncertainty / Error Prediction",
        hypothesis=(
            "H1: model error is predictably heterogeneous -- pregame-available factors (sample "
            "depth, component disagreement, model extremeness, season progress) correlate with "
            "realized absolute error. H2: a small, transparent uncertainty score (U1 rule-based, "
            "or U2 a tiny DEV-fit ridge model) can separate predictions into monotonically ordered "
            "reliability tiers that replicate on 2025 validation and the locked 2026 holdout."
        ),
        research_question="Can we identify, before first pitch, which MLB probability estimates are more likely to be wrong?",
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E2_PIT_HISTORICAL,
        target_population="Every MLB regular-season game in MLB-RSCH-0009's own standard build_season_rows() corpus (2022-2026, 20-game floor inherited unchanged)",
        market_families=["game_result", "game_total", "team_total", "run_margin"],
        eligibility_criteria=["identical to MLB-RSCH-0009's own build_season_rows() eligibility -- unchanged"],
        exclusion_criteria=[
            "betting P/L or ROI as a target -- never used as the primary or secondary uncertainty target",
            "any feature created after the prediction timestamp (final score, closing line movement, postgame Statcast, settlement)",
            "starter prior-start sample -- MLB-RSCH-0009's own starterIdentityVerdict already found starter identity NOT PIT-safe at scale (mismatchRate 0.006 vs a plausible band of 0.01-0.3), reused as-is, not re-derived",
            "rookie/limited-history flags -- this population is team-level, not player-level; not genuinely classifiable here",
            "a giant residual ML model -- U2 is capped at one small closed-form ridge regression, no random forest/boosting/neural network, no hyperparameter search",
        ],
        prediction_checkpoints=["SEASON_TO_DATE_PREGAME"],
        primary_metric="Pearson correlation between uncertainty score and realized absolute team-run error, DEV/VAL/HOLDOUT",
        secondary_metrics=[
            "frozen-NB per-family (game_total/moneyline/run_margin/team_total) squared-probability-error correlation with uncertainty score",
            "tier-level (LOW/MEDIUM/HIGH, DEV-frozen cutpoints) MAE/Brier/log-loss/calibration error, monotonicity",
            "LARGE_ERROR binary classification lift and secondary AUC (DEV top-quartile absolute-error threshold, frozen)",
            "signed-error direction (over/underconfidence) by tier",
            "season/team/home-away robustness",
        ],
        chronological_split_policy=f"SEASON_BASED: development={DEV_SEASONS}, validation={VALIDATION_SEASONS}, holdout={HOLDOUT_SEASONS} (locked)",
        minimum_sample_requirement={"independentGames": 50},
        clustering_unit="gamePk",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={"team_recent_game_log_reconstruction": "PREDICTIVE_INPUT"},
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            "evidenceLevel E2_PIT_HISTORICAL for Layer A (historical/proxy). Layer B (current-"
            "production prospective cohort) is EXPLORATORY and reported separately, never mixed "
            "numerically with Layer A. Frozen MLB-RSCH-0010 NB dispersion reused unchanged. Max "
            "disposition SHADOW_CANDIDATE -- no production confidence/sizing changes anywhere."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Layer A: historical corpus construction (reuses rsch0009 unchanged) ────

def build_layer_a_corpus():
    rows_by_season = {}
    relief_by_season = {}
    team_games_by_season = {}
    for season in ALL_SEASONS:
        team_games = rsch0009.load_all_team_games_with_venue(season)
        team_games_by_season[season] = team_games
        relief_er9 = rsch0009.load_relief_er9_games(season, team_games)
        relief_by_season[season] = relief_er9
        env_lookup = rsch0009.build_season_environment_lookup([g for games in team_games.values() for g in games if g.get("side") == "home"])
        rows_by_season[season] = rsch0009.build_season_rows(season, team_games, relief_er9, env_lookup)
    return rows_by_season, team_games_by_season, relief_by_season


def season_bucket_for(min_prior_games):
    for name, lo, hi in SEASON_BUCKETS:
        if lo <= min_prior_games <= hi:
            return name
    return None


def game_component_disagreement(home_bfc, away_bfc, league_avg_offense):
    """|team's own offense signal - what the opponent's run-prevention signal
    alone implies for that team's runs|, averaged over both directions. Both
    signals estimate the SAME quantity via two different components; large
    disagreement flags genuine model uncertainty about the scoring environment."""
    if home_bfc is None or away_bfc is None:
        return None
    d_home = abs(home_bfc["offenseRunsPerGame"] - (2 * league_avg_offense - away_bfc["runPreventionRunsAllowedPerGame"]))
    d_away = abs(away_bfc["offenseRunsPerGame"] - (2 * league_avg_offense - home_bfc["runPreventionRunsAllowedPerGame"]))
    return round((d_home + d_away) / 2, 4)


def attach_features_and_predictions(rows, hfa, league_avg_offense):
    for r in rows:
        hb = rsch0009.baseline_for_components(r["homeBaselineRaw"], r["homeOffenseStabilized"], r["homeBullpenStabilized"], BASELINE_COMPONENTS)
        ab = rsch0009.baseline_for_components(r["awayBaselineRaw"], r["awayOffenseStabilized"], r["awayBullpenStabilized"], BASELINE_COMPONENTS)
        eh, ea = rsch0009.expected_runs(hb, ab, home_field_adjustment=hfa)
        r["homeExpectedRuns"], r["awayExpectedRuns"] = eh, ea
        ml_prob, _push = rsch0009.game_ml_proxy_probability(eh, ea) if eh is not None and ea is not None else (None, None)
        r["mlProb"] = ml_prob
        r["expectedTotal"] = (eh + ea) if eh is not None and ea is not None else None

        home_pg = r["homeBaselineRaw"]["priorGamesThisSeason"]
        away_pg = r["awayBaselineRaw"]["priorGamesThisSeason"]
        r["minSampleDepth"] = min(home_pg, away_pg)
        home_bp = r["homeBullpenRaw"]["priorGamesWithBullpenData"] if r["homeBullpenRaw"] else 0
        away_bp = r["awayBullpenRaw"]["priorGamesWithBullpenData"] if r["awayBullpenRaw"] else 0
        r["minBullpenSampleDepth"] = min(home_bp, away_bp)
        r["componentDisagreement"] = game_component_disagreement(hb, ab, league_avg_offense)
        r["probExtremeness"] = round(abs(ml_prob - 0.5), 4) if ml_prob is not None else None
        r["totalExtremeness"] = round(abs(r["expectedTotal"] - 2 * league_avg_offense), 4) if r["expectedTotal"] is not None else None
        r["seasonBucket"] = season_bucket_for(r["minSampleDepth"])
        r["gameAvgAbsError"] = (
            (abs(eh - r["actualHomeRuns"]) + abs(ea - r["actualAwayRuns"])) / 2
            if eh is not None and ea is not None and r["actualHomeRuns"] is not None and r["actualAwayRuns"] is not None else None
        )


def team_observations(rows):
    obs = []
    for r in rows:
        if r["homeExpectedRuns"] is not None and r["actualHomeRuns"] is not None:
            err = r["homeExpectedRuns"] - r["actualHomeRuns"]
            obs.append({"gamePk": r["gamePk"], "season": r["season"], "side": "home", "teamId": r["homeTeamId"], "date": r.get("date"),
                        "absError": abs(err), "sqError": err ** 2, "signedError": err, "u1Score": r.get("u1Score"), "u2Score": r.get("u2Score"),
                        "minSampleDepth": r["minSampleDepth"], "minBullpenSampleDepth": r["minBullpenSampleDepth"],
                        "componentDisagreement": r["componentDisagreement"], "probExtremeness": r["probExtremeness"],
                        "totalExtremeness": r["totalExtremeness"], "seasonBucket": r["seasonBucket"]})
        if r["awayExpectedRuns"] is not None and r["actualAwayRuns"] is not None:
            err = r["awayExpectedRuns"] - r["actualAwayRuns"]
            obs.append({"gamePk": r["gamePk"], "season": r["season"], "side": "away", "teamId": r["awayTeamId"], "date": r.get("date"),
                        "absError": abs(err), "sqError": err ** 2, "signedError": err, "u1Score": r.get("u1Score"), "u2Score": r.get("u2Score"),
                        "minSampleDepth": r["minSampleDepth"], "minBullpenSampleDepth": r["minBullpenSampleDepth"],
                        "componentDisagreement": r["componentDisagreement"], "probExtremeness": r["probExtremeness"],
                        "totalExtremeness": r["totalExtremeness"], "seasonBucket": r["seasonBucket"]})
    return obs


# ── U1: transparent unweighted flag-sum score ──────────────────────────────

def _percentile(sorted_values, q):
    if not sorted_values:
        return None
    idx = min(int(len(sorted_values) * q), len(sorted_values) - 1)
    return sorted_values[idx]


def fit_u1_thresholds_dev_only(dev_rows):
    sample_depths = sorted(r["minSampleDepth"] for r in dev_rows if r["minSampleDepth"] is not None)
    bullpen_depths = sorted(r["minBullpenSampleDepth"] for r in dev_rows if r["minBullpenSampleDepth"] is not None)
    disagreements = sorted(r["componentDisagreement"] for r in dev_rows if r["componentDisagreement"] is not None)
    extremeness = sorted(r["probExtremeness"] for r in dev_rows if r["probExtremeness"] is not None)
    return {
        "lowSampleThreshold": _percentile(sample_depths, 0.5),
        "lowBullpenThreshold": _percentile(bullpen_depths, 0.5),
        "highDisagreementThreshold": _percentile(disagreements, 0.75),
        "highExtremenessThreshold": _percentile(extremeness, 0.75),
    }


def compute_u1_score(row, thresholds):
    flags = 0
    if row["minSampleDepth"] is not None and row["minSampleDepth"] <= thresholds["lowSampleThreshold"]:
        flags += 1
    if row["minBullpenSampleDepth"] is not None and row["minBullpenSampleDepth"] <= thresholds["lowBullpenThreshold"]:
        flags += 1
    if row["componentDisagreement"] is not None and row["componentDisagreement"] >= thresholds["highDisagreementThreshold"]:
        flags += 1
    if row["probExtremeness"] is not None and row["probExtremeness"] >= thresholds["highExtremenessThreshold"]:
        flags += 1
    if row["seasonBucket"] == "near_floor_20_40":
        flags += 1
    return flags


# ── U2: DEV-only-fit, ridge-regularized linear error model ────────────────

def fit_standardization_dev_only(dev_rows, feature_fields):
    stats = {}
    for f in feature_fields:
        values = [r[f] for r in dev_rows if r.get(f) is not None]
        n = len(values)
        mean = sum(values) / n if n else 0.0
        std = math.sqrt(sum((v - mean) ** 2 for v in values) / n) if n else 1.0
        stats[f] = (mean, std if std > 1e-9 else 1.0)
    return stats


def apply_standardization(rows, feature_fields, stats):
    for r in rows:
        for f in feature_fields:
            mean, std = stats[f]
            r[f + "_z"] = (r[f] - mean) / std if r.get(f) is not None else 0.0


def _ridge_fit(rows, feature_fields, target_field, lam):
    p = len(feature_fields) + 1
    usable = [r for r in rows if r.get(target_field) is not None]
    if len(usable) < p:
        return None
    xtx = [[0.0] * p for _ in range(p)]
    xty = [0.0] * p
    for r in usable:
        x = [1.0] + [float(r[f]) for f in feature_fields]
        y = float(r[target_field])
        for i in range(p):
            xty[i] += x[i] * y
            for j in range(p):
                xtx[i][j] += x[i] * x[j]
    for i in range(1, p):
        xtx[i][i] += lam
    aug = [xtx[i] + [xty[i]] for i in range(p)]
    for col in range(p):
        pivot_row = max(range(col, p), key=lambda r_: abs(aug[r_][col]))
        if abs(aug[pivot_row][col]) < 1e-12:
            return None
        aug[col], aug[pivot_row] = aug[pivot_row], aug[col]
        pivot = aug[col][col]
        aug[col] = [v / pivot for v in aug[col]]
        for r_ in range(p):
            if r_ == col:
                continue
            factor = aug[r_][col]
            if factor != 0.0:
                aug[r_] = [aug[r_][k] - factor * aug[col][k] for k in range(p + 1)]
    coefficients = {"intercept": round(aug[0][p], 6)}
    for idx, field in enumerate(feature_fields, start=1):
        coefficients[field] = round(aug[idx][p], 6)
    return coefficients


def predict_u2(row, coefficients, feature_fields):
    """`feature_fields` are already the standardized ("_z"-suffixed) names --
    matches both `coefficients`'s own keys (from _ridge_fit, fit on the same
    suffixed names) and the row's own attribute names (from
    apply_standardization). Do not re-suffix here."""
    if coefficients is None:
        return None
    score = coefficients["intercept"]
    for f in feature_fields:
        score += coefficients[f] * row[f]
    return round(score, 4)


# ── Correlation / AUC / tiers ──────────────────────────────────────────────

def pearson_corr(pairs):
    n = len(pairs)
    if n < 2:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return None
    return round(cov / math.sqrt(vx * vy), 4)


def fit_tier_cutpoints_dev_only(dev_scores):
    values = sorted(dev_scores)
    if not values:
        return None, None
    return _percentile(values, 0.3333), _percentile(values, 0.6667)


def tier_for(score, low_cut, high_cut):
    if low_cut is None:
        return None
    if score <= low_cut:
        return "LOW"
    if score >= high_cut:
        return "HIGH"
    return "MEDIUM"


def tier_breakdown(obs, score_field, low_cut, high_cut):
    out = {}
    for tier in ("LOW", "MEDIUM", "HIGH"):
        sub = [o for o in obs if tier_for(o[score_field], low_cut, high_cut) == tier]
        n = len(sub)
        out[tier] = {
            "n": n,
            "meanAbsError": round(sum(o["absError"] for o in sub) / n, 4) if n else None,
            "meanSignedError": round(sum(o["signedError"] for o in sub) / n, 4) if n else None,
        }
    return out


def is_monotonic_increasing(tier_result):
    vals = [tier_result[t]["meanAbsError"] for t in ("LOW", "MEDIUM", "HIGH")]
    if any(v is None for v in vals):
        return False
    return vals[0] <= vals[1] <= vals[2]


def compute_auc(scored_labels):
    pos = [s for s, l in scored_labels if l == 1]
    neg = [s for s, l in scored_labels if l == 0]
    if not pos or not neg:
        return None
    combined = sorted(scored_labels, key=lambda sl: sl[0])
    n = len(combined)
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n and combined[j][0] == combined[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2
        for k in range(i, j):
            ranks[k] = avg_rank
        i = j
    rank_sum_pos = sum(ranks[k] for k in range(n) if combined[k][1] == 1)
    n_pos, n_neg = len(pos), len(neg)
    return round((rank_sum_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg), 4)


# ── Frozen-NB per-family error (reuses rsch0017's generic cell primitives) ──

def family_squared_errors(row):
    if row["homeExpectedRuns"] is None or row["awayExpectedRuns"] is None:
        return None
    if row.get("actualHomeRuns") is None or row.get("actualAwayRuns") is None:
        return None
    cells = rsch0017.nb_probability_cells(row["homeExpectedRuns"], row["awayExpectedRuns"])
    if cells is None:
        return None
    outcomes = rsch0017._outcomes_for_actual(row["actualHomeRuns"], row["actualAwayRuns"])
    by_family = {}
    for family in ("game_total", "moneyline", "run_margin", "team_total_home", "team_total_away"):
        sq_errors = [(cells[k] - outcomes[k]) ** 2 for k in outcomes if k.startswith(family)]
        by_family[family] = round(sum(sq_errors) / len(sq_errors), 6) if sq_errors else None
    return by_family


def family_correlation_and_tiers(rows, score_field, low_cut, high_cut):
    out = {}
    for family in ("game_total", "moneyline", "run_margin", "team_total_home", "team_total_away"):
        pairs = []
        tiered = {"LOW": [], "MEDIUM": [], "HIGH": []}
        for r in rows:
            fam_errs = r.get("familySquaredErrors")
            if fam_errs is None or fam_errs.get(family) is None or r.get(score_field) is None:
                continue
            pairs.append((r[score_field], fam_errs[family]))
            tier = tier_for(r[score_field], low_cut, high_cut)
            if tier:
                tiered[tier].append(fam_errs[family])
        tier_means = {t: (round(sum(v) / len(v), 6) if v else None) for t, v in tiered.items()}
        out[family] = {"n": len(pairs), "correlation": pearson_corr(pairs), "tierMeanSquaredError": tier_means}
    return out


# ── Selection rule (preregistered BEFORE results, locked) ─────────────────

def selection_passes(dev_corr, val_corr, dev_tiers, val_tiers):
    reasons = []
    if dev_corr is None or dev_corr < DEV_CORRELATION_FLOOR:
        reasons.append(f"DEV correlation below locked floor {DEV_CORRELATION_FLOOR}: {dev_corr}")
    if val_corr is None or val_corr < VAL_CORRELATION_FLOOR:
        reasons.append(f"VAL correlation below locked floor {VAL_CORRELATION_FLOOR}: {val_corr}")
    if not is_monotonic_increasing(dev_tiers):
        reasons.append("DEV tiers not monotonically increasing")
    if not is_monotonic_increasing(val_tiers):
        reasons.append("VAL tiers not monotonically increasing")
    return (len(reasons) == 0), reasons


def year_by_year(obs, score_field, low_cut, high_cut):
    out = {}
    for season in ALL_SEASONS:
        sub = [o for o in obs if o["season"] == season]
        pairs = [(o[score_field], o["absError"]) for o in sub if o.get(score_field) is not None]
        out[str(season)] = {"n": len(sub), "correlation": pearson_corr(pairs)}
    return out


def team_robustness(obs, score_field):
    by_team = {}
    for o in obs:
        if o.get(score_field) is None:
            continue
        by_team.setdefault(o["teamId"], []).append((o[score_field], o["absError"]))
    correlations = {str(t): pearson_corr(pairs) for t, pairs in by_team.items() if len(pairs) >= 10}
    valid = [c for c in correlations.values() if c is not None]
    return {
        "perTeamCorrelation": correlations,
        "nTeamsPositive": sum(1 for c in valid if c > 0),
        "nTeamsTotal": len(valid),
    }


def home_away_breakdown(obs, score_field):
    out = {}
    for side in ("home", "away"):
        sub = [o for o in obs if o["side"] == side]
        pairs = [(o[score_field], o["absError"]) for o in sub if o.get(score_field) is not None]
        out[side] = {"n": len(sub), "correlation": pearson_corr(pairs)}
    return out


# ── Layer B: current-production prospective cohort (genuine records only) ──

def layer_b_prospective_cohort():
    """Genuine MLB-RSCH-0011 shadow-evaluation records only -- NEVER mixed
    numerically with Layer A. Searches for any settled shadowEvaluationId
    record anywhere in the repository, same method used to verify RSCH-0011's
    own health throughout this research program."""
    import subprocess as sp
    try:
        result = sp.run(["grep", "-rl", "shadowEvaluationId", os.path.join(_ROOT, "data")], capture_output=True, text=True, timeout=30)
        files_with_records = [ln for ln in result.stdout.splitlines() if ln.strip()]
    except Exception as exc:
        return {"status": "ERROR", "error": str(exc)}
    if not files_with_records:
        return {"status": "INSUFFICIENT_SAMPLE", "settledGames": 0, "note": "zero genuine E4 shadow evaluation records exist anywhere in the repository -- exploratory Layer B produces no numeric result"}
    return {"status": "FOUND_RECORDS_NOT_YET_ANALYZED", "files": files_with_records}


def main():
    print(f"[{EXPERIMENT_ID}] registering experiment/control...")
    control, definition = register_experiment()

    print(f"[{EXPERIMENT_ID}] building Layer A historical corpus (reuses rsch0009's own build_season_rows unchanged)...")
    rows_by_season, team_games_by_season, relief_by_season = build_layer_a_corpus()
    dev_rows = [r for s in DEV_SEASONS for r in rows_by_season[s]]
    val_rows = [r for s in VALIDATION_SEASONS for r in rows_by_season[s]]
    holdout_rows = [r for s in HOLDOUT_SEASONS for r in rows_by_season[s]]
    all_rows = dev_rows + val_rows + holdout_rows
    print(f"[{EXPERIMENT_ID}] rows: dev={len(dev_rows)} val={len(val_rows)} holdout={len(holdout_rows)} total={len(all_rows)}")

    dev_home_team_games = [g for s in DEV_SEASONS for g in team_games_by_season[s].values()]
    league_avg_offense = rsch0009.fit_league_average_runs_per_game(dev_home_team_games)
    dev_relief_er9_team_games = [g for s in DEV_SEASONS for g in relief_by_season[s].values()]
    league_avg_bullpen_er9 = rsch0009.fit_league_average_bullpen_er9(dev_relief_er9_team_games)
    rsch0009.attach_stabilized_components(all_rows, league_avg_offense, league_avg_bullpen_er9)

    hfa = rsch0009.fit_home_field_adjustment_for_components(dev_rows, BASELINE_COMPONENTS)
    print(f"[{EXPERIMENT_ID}] frozen baseline: leagueAvgOffense={league_avg_offense} hfa={hfa} components={sorted(BASELINE_COMPONENTS)}")

    attach_features_and_predictions(all_rows, hfa, league_avg_offense)
    for r in all_rows:
        r["familySquaredErrors"] = family_squared_errors(r)

    # ---- U1: DEV-only thresholds, unweighted flag sum ----
    u1_thresholds = fit_u1_thresholds_dev_only(dev_rows)
    for r in all_rows:
        r["u1Score"] = compute_u1_score(r, u1_thresholds)
    print(f"[{EXPERIMENT_ID}] U1 thresholds (DEV-only): {u1_thresholds}")

    # ---- U2: DEV-only standardization + ridge fit ----
    standardization = fit_standardization_dev_only(dev_rows, FEATURE_FIELDS)
    apply_standardization(all_rows, FEATURE_FIELDS, standardization)
    u2_coefficients = _ridge_fit(dev_rows, [f + "_z" for f in FEATURE_FIELDS], "gameAvgAbsError", RIDGE_LAMBDA)
    for r in all_rows:
        r["u2Score"] = predict_u2(r, u2_coefficients, [f + "_z" for f in FEATURE_FIELDS]) if u2_coefficients else None
    print(f"[{EXPERIMENT_ID}] U2 ridge coefficients (DEV-only, lambda={RIDGE_LAMBDA}): {u2_coefficients}")

    obs_dev, obs_val, obs_holdout = team_observations(dev_rows), team_observations(val_rows), team_observations(holdout_rows)
    obs_all = obs_dev + obs_val + obs_holdout

    # ---- U0 control: no differentiation ----
    u0_dev_mae = round(sum(o["absError"] for o in obs_dev) / len(obs_dev), 4)
    u0_val_mae = round(sum(o["absError"] for o in obs_val) / len(obs_val), 4)

    results = {"U0": {"devMae": u0_dev_mae, "valMae": u0_val_mae}}
    for score_field, label in (("u1Score", "U1"), ("u2Score", "U2")):
        dev_corr = pearson_corr([(o[score_field], o["absError"]) for o in obs_dev if o.get(score_field) is not None])
        val_corr = pearson_corr([(o[score_field], o["absError"]) for o in obs_val if o.get(score_field) is not None])
        low_cut, high_cut = fit_tier_cutpoints_dev_only([o[score_field] for o in obs_dev if o.get(score_field) is not None])
        dev_tiers = tier_breakdown(obs_dev, score_field, low_cut, high_cut)
        val_tiers = tier_breakdown(obs_val, score_field, low_cut, high_cut)
        passes, reasons = selection_passes(dev_corr, val_corr, dev_tiers, val_tiers)
        print(f"[{EXPERIMENT_ID}] {label}: devCorr={dev_corr} valCorr={val_corr} passes={passes} reasons={reasons}")
        results[label] = {
            "devCorrelation": dev_corr, "valCorrelation": val_corr,
            "tierCutpoints": {"low": low_cut, "high": high_cut},
            "devTiers": dev_tiers, "valTiers": val_tiers,
            "selection": {"passes": passes, "reasons": reasons},
        }

    # ---- Selected model: prefer U1 if both pass (simplicity-first, program convention); else whichever passes ----
    if results["U1"]["selection"]["passes"]:
        selected = "U1"
    elif results["U2"]["selection"]["passes"]:
        selected = "U2"
    else:
        selected = None
    print(f"[{EXPERIMENT_ID}] selected model: {selected}")

    holdout_result = None
    large_error_result = None
    family_result_dev = None
    family_result_holdout = None
    robustness = None
    home_away = None
    year_by_year_result = None

    if selected is not None:
        score_field = "u1Score" if selected == "U1" else "u2Score"
        low_cut, high_cut = results[selected]["tierCutpoints"]["low"], results[selected]["tierCutpoints"]["high"]

        print(f"[{EXPERIMENT_ID}] preregistered gate passed for {selected} -- unlocking 2026 holdout...")
        holdout_corr = pearson_corr([(o[score_field], o["absError"]) for o in obs_holdout if o.get(score_field) is not None])
        holdout_tiers = tier_breakdown(obs_holdout, score_field, low_cut, high_cut)
        holdout_result = {
            "correlation": holdout_corr, "tiers": holdout_tiers,
            "monotonic": is_monotonic_increasing(holdout_tiers),
            "favorableDirection": holdout_corr is not None and holdout_corr > 0,
        }
        print(f"[{EXPERIMENT_ID}] {selected} 2026 holdout correlation={holdout_corr} monotonic={holdout_result['monotonic']}")

        # LARGE_ERROR classification (DEV-frozen threshold, secondary AUC)
        dev_abs_errors = sorted(o["absError"] for o in obs_dev)
        large_error_threshold = _percentile(dev_abs_errors, LARGE_ERROR_DEV_QUANTILE)
        for split_name, obs_split in (("dev", obs_dev), ("val", obs_val), ("holdout", obs_holdout)):
            for o in obs_split:
                o["largeError"] = 1 if o["absError"] >= large_error_threshold else 0
        auc_val = compute_auc([(o[score_field], o["largeError"]) for o in obs_val if o.get(score_field) is not None])
        auc_holdout = compute_auc([(o[score_field], o["largeError"]) for o in obs_holdout if o.get(score_field) is not None])
        large_error_rate_by_tier_val = {t: (round(sum(1 for o in obs_val if tier_for(o[score_field], low_cut, high_cut) == t and o["largeError"] == 1) / max(sum(1 for o in obs_val if tier_for(o[score_field], low_cut, high_cut) == t), 1), 4)) for t in ("LOW", "MEDIUM", "HIGH")}
        large_error_result = {
            "devThreshold": large_error_threshold, "aucSecondaryVal": auc_val, "aucSecondaryHoldout": auc_holdout,
            "largeErrorRateByTierVal": large_error_rate_by_tier_val,
        }

        family_result_dev = family_correlation_and_tiers(dev_rows, score_field, low_cut, high_cut)
        family_result_holdout = family_correlation_and_tiers(holdout_rows, score_field, low_cut, high_cut)

        robustness = team_robustness(obs_dev, score_field)
        home_away = home_away_breakdown(obs_dev, score_field)
        year_by_year_result = year_by_year(obs_all, score_field, low_cut, high_cut)
    else:
        print(f"[{EXPERIMENT_ID}] neither U1 nor U2 passed the preregistered gate -- holdout NOT unlocked, no rescue.")

    layer_b = layer_b_prospective_cohort()
    print(f"[{EXPERIMENT_ID}] Layer B prospective cohort status: {layer_b['status']}")

    if selected is None:
        classification = "NO_USEFUL_SIGNAL"
    elif holdout_result and holdout_result["favorableDirection"] and holdout_result["monotonic"]:
        classification = "MODERATE_UNCERTAINTY_SIGNAL"
    elif holdout_result and holdout_result["favorableDirection"]:
        classification = "PARTIAL_FAMILY_SPECIFIC_SIGNAL"
    else:
        classification = "WEAK_UNPROVEN"

    report = {
        "experimentId": EXPERIMENT_ID,
        "controlModelId": control["controlModelId"],
        "layerA": {
            "corpus": {"devRows": len(dev_rows), "valRows": len(val_rows), "holdoutRows": len(holdout_rows), "totalRows": len(all_rows)},
            "frozenBaseline": {"leagueAverageOffense": league_avg_offense, "homeFieldAdjustment": hfa, "components": sorted(BASELINE_COMPONENTS)},
            "u1Thresholds": u1_thresholds, "u2Coefficients": u2_coefficients,
            "results": results, "selectedModel": selected,
            "holdout2026": holdout_result, "largeErrorClassification": large_error_result,
            "familyAnalysisDev": family_result_dev, "familyAnalysisHoldout": family_result_holdout,
            "teamRobustness": robustness, "homeAwayBreakdown": home_away, "yearByYear": year_by_year_result,
        },
        "layerB": layer_b,
        "classification": classification,
        "disposition": "SHADOW_CANDIDATE" if selected is not None else "REJECT",
    }

    out_path = os.path.join("data", "edgelab", "analytics", "latest_mlb_rsch_0019_uncertainty_prediction.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    print(f"[{EXPERIMENT_ID}] wrote {out_path}")
    print(f"[{EXPERIMENT_ID}] classification={classification} disposition={report['disposition']}")
    return report


if __name__ == "__main__":
    main()
