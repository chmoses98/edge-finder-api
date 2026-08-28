#!/usr/bin/env python3
"""
scripts/edgelab/run_probability_recalibration_experiment.py
====================================================================
Research Lab experiment MLB-RSCH-0023: "Production Probability
Recalibration". RESEARCH ONLY. NO production changes, no staking/
execution/fee-logic changes.

MOTIVATION: MLB-RSCH-0022 measured production's archived pregame Kalshi
probabilities and found a large, systematic overconfidence signature
(probabilities too extreme in both directions; market Brier 0.172 vs
model 0.227). This experiment asks the natural corrective question AS A
SEPARATE, NEWLY PREREGISTERED STUDY:

  Can a TINY, DEV-fit, monotone recalibration of production's
  probability surface improve proper probability scoring out-of-time?

CANDIDATES (preregistered, frozen before any fit is run):
  R0 (control): production's archived probabilities, as-is.
  R1: ONE global logit-affine map  p' = sigmoid(a + b * logit(p)),
      two parameters, fit on DEV only by log-loss (deterministic
      Newton/IRLS). Monotone by construction for b > 0 -- it can shrink
      or expand the probability surface but never reorder it.
  R2: tier logit-affine -- the same two-parameter map fit separately
      for THREE preregistered family tiers (fixed here, before any fit):
        TIER_GAME:   game_result, game_total, winning_margin
        TIER_LOCAL:  team_total, inning_result, inning_total,
                     first_inning_run
        TIER_PROPS:  pitcher_strikeouts, pitcher_outs
      (6 parameters total). Tiers come from market-structure kinship,
      not from peeking at this experiment's own results.
  Simplicity-first rule: if both pass, R1 is selected.

DATA: identical corpus construction to MLB-RSCH-0022 (its loaders are
imported and reused unchanged): production's archived EVALUATED rows,
last per ticker, joined to settled Kalshi outcomes. Zero network calls.

SPLIT (preregistered):
  DEV:     settlement dates <= 2026-08-22   (fitting)
  VAL:     2026-08-23 .. 2026-08-28         (replication, never refit)
  FORWARD: settlement dates > 2026-08-28    (genuine holdout --
           preregistered, NOT computed in this run; to be scored in a
           future session as the season's remaining games settle).

CONTAMINATION DISCLOSURE (honest, up front): MLB-RSCH-0022 already
observed the pooled miscalibration DIRECTION on data through 08-28, so
this experiment's VAL window is not blind at the direction level -- only
the FORWARD window is fully blind. For exactly this reason the maximum
classification this experiment can award itself is LEVEL 1 (shadow
candidate: score the FORWARD window before any production-change
review), regardless of how strong DEV/VAL look.

SELECTION RULE (preregistered, locked):
  1. DEV paired Brier delta (recalibrated minus raw) < 0;
  2. DEV log-loss delta < 0;
  3. VAL paired Brier delta < 0 (strict replication -- no tolerance);
  4. recalibrated ECE <= raw ECE on DEV (calibration actually improves);
  5. for R2 only: improvement not driven by a single tier (at least two
     of three tiers with a negative DEV Brier delta).
Market comparison (does recalibration close the gap to Kalshi's own
price?) is SECONDARY/descriptive -- never a selection criterion, and
nothing here is fit to market prices or to ROI.
"""
import json
import math
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_EDGELAB_SCRIPTS_DIR = os.path.join(_ROOT, "scripts", "edgelab")
if _EDGELAB_SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _EDGELAB_SCRIPTS_DIR)

from lib.edgelab import experiment_registry as reg
from lib.edgelab import evidence_levels as ev
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab.research_stats import (
    DEFAULT_BOOTSTRAP_SEED,
    independent_unit_count,
    sample_size_status,
    expected_calibration_error,
    brier_and_log_loss_summary,
    game_clustered_bootstrap_ci,
)

import run_production_calibration_audit_experiment as rsch0022  # noqa: E402 -- loaders reused unchanged

EXPERIMENT_ID = "MLB-RSCH-0023"
REGISTRATION_TIMESTAMP = "2026-08-28T20:10:00Z"

DEV_DATE_MAX = "2026-08-22"
VAL_DATE_MAX = "2026-08-28"
# FORWARD: settlement dates > VAL_DATE_MAX -- preregistered, never computed here.

PROB_CLAMP = (0.01, 0.99)
NEWTON_ITERATIONS = 30  # fixed, deterministic; no convergence-dependent behavior

TIERS = {
    "TIER_GAME": ("game_result", "game_total", "winning_margin"),
    "TIER_LOCAL": ("team_total", "inning_result", "inning_total", "first_inning_run"),
    "TIER_PROPS": ("pitcher_strikeouts", "pitcher_outs"),
}


def tier_for_family(family):
    for tier, fams in TIERS.items():
        if family in fams:
            return tier
    return "TIER_LOCAL"  # preregistered default for any unforeseen family label


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
        name="mlb_rsch_0023_probability_recalibration_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0023 probability recalibration v1: R0 = production's archived "
                        "probabilities as-is; R1 = one global DEV-fit logit-affine map (2 params); "
                        "R2 = three preregistered family tiers x logit-affine (6 params). Deterministic "
                        "Newton/IRLS log-loss fit, DEV settle<=2026-08-22 only. FORWARD (>08-28) untouched."
        ),
        probability_adapter_identity="production pipeline's own archived modelFairProbability, post-hoc logit-affine map -- research-only, never wired into production",
        model_engine_family="production_probability_recalibration_v1",
        required_input_provenance=["model_evaluation_probability_pipeline_derived", "model_evaluation_probability_prospective_snapshot", "settlement_outcome"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "Tests whether a tiny, monotone, DEV-fit logit-affine recalibration of production's archived "
            "pregame Kalshi probabilities improves proper probability scoring out-of-time -- the corrective "
            "follow-up to MLB-RSCH-0022's systematic-overconfidence finding, preregistered as its own "
            "separate study with the FORWARD window (settle > 2026-08-28) as the fully blind holdout."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Production Probability Recalibration",
        hypothesis=(
            "H1: production's overconfidence (MLB-RSCH-0022) is stable enough that ONE global logit-affine "
            "shrink (expected b < 1) fit on DEV improves Brier/log-loss on later, unseen VAL days. H2: tier-"
            "level maps (R2) add value over the global map only if miscalibration differs materially by "
            "market-structure tier. H3 (null, tested not assumed): the miscalibration may be unstable "
            "week-to-week, in which case DEV-fit corrections will fail VAL and the path is retired."
        ),
        research_question=(
            "Can a tiny, monotone, DEV-fit recalibration of production's probability surface improve proper "
            "probability scoring on later unseen days -- and does it close any of the measured gap to "
            "Kalshi's own prices?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E4_PROSPECTIVE_SHADOW,
        target_population=(
            "Identical to MLB-RSCH-0022: production's archived EVALUATED rows (last per ticker) joined to "
            "settled Kalshi outcomes, 2026-08-02 .. 2026-08-28, ~3,100 tickers / ~290 games / 13 families."
        ),
        market_families=["game_result", "game_total", "team_total", "run_margin", "pitcher_props", "inning_markets"],
        eligibility_criteria=[
            "identical to MLB-RSCH-0022's own corpus rules (loaders imported unchanged)",
            "probabilities clamped to [0.01, 0.99] before logit transform (disclosed, fixed)",
        ],
        exclusion_criteria=[
            "any fit against Kalshi prices or realized ROI -- outcomes only",
            "any non-monotone or per-market map; any parameter count beyond 2 (R1) / 6 (R2)",
            "any use of FORWARD (settle > 2026-08-28) data -- preregistered blind window",
            "re-fitting after seeing VAL results -- a VAL failure retires the path",
        ],
        prediction_checkpoints=["ARCHIVED_PRODUCTION_PREGAME"],
        primary_metric="paired Brier delta (recalibrated minus raw production probability), game-clustered bootstrap CI, on DEV then VAL",
        secondary_metrics=[
            "log-loss delta", "expected calibration error before/after", "reliability by fixed price band",
            "per-tier deltas (R2 concentration check)",
            "SECONDARY/descriptive: remaining paired Brier gap to Kalshi's own price after recalibration",
        ],
        chronological_split_policy=(
            f"DATE_BASED: DEV = settle <= {DEV_DATE_MAX} (fitting), VAL = ({DEV_DATE_MAX}, {VAL_DATE_MAX}] "
            "(replication, never refit), FORWARD = settle > 2026-08-28 (fully blind, preregistered, not "
            "computed in this run). Contamination disclosure: MLB-RSCH-0022 observed the miscalibration "
            "direction on data through 08-28, so VAL is not direction-blind; the classification cap below "
            "accounts for this."
        ),
        minimum_sample_requirement={"independentGames": 30},
        clustering_unit="gameId",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={
            "model_evaluation_probability_pipeline_derived": "PREDICTIVE_INPUT",
            "model_evaluation_probability_prospective_snapshot": "PREDICTIVE_INPUT",
            "settlement_outcome": "EVALUATION_TARGET",
        },
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            "evidenceLevel E4_PROSPECTIVE_SHADOW (prospectively captured inputs). Probability-calibration "
            "experiment: proper scoring rules are primary throughout (Methodology V2's requirement for "
            "probability targets; its mean-metric rules do not apply -- no expected-run mean candidate). "
            "MAXIMUM classification this experiment can award: LEVEL 1 SHADOW CANDIDATE -- because VAL is "
            "not direction-blind (see split policy), the FORWARD window must be scored in a future session "
            "before any production-change review. Never wired into production by this experiment."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Logit-affine map + deterministic Newton/IRLS fit (DEV only) ───────────

def _clamp(p):
    return min(max(p, PROB_CLAMP[0]), PROB_CLAMP[1])


def _logit(p):
    p = _clamp(p)
    return math.log(p / (1 - p))


def _sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def apply_map(p, a, b):
    return _sigmoid(a + b * _logit(p))


def _log_loss(data, a, b):
    total = 0.0
    for x, y in data:
        p = min(max(_sigmoid(a + b * x), 1e-9), 1 - 1e-9)
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(data)


def fit_logit_affine_dev_only(rows):
    """Two-parameter logistic recalibration fit by log-loss on (logit(p),
    outcome) pairs -- deterministic damped Newton/IRLS: fixed iteration
    count, fixed (0,1) start, and a deterministic backtracking line
    search (halve the Newton step, at most 25 times, until the log-loss
    does not increase) so a raw Newton overshoot can never diverge.
    Returns {"a","b","n","devLogLoss"} or None if degenerate."""
    data = [(_logit(r["modelP"]), r["outcome"]) for r in rows]
    if len(data) < 30:
        return None
    a, b = 0.0, 1.0
    loss = _log_loss(data, a, b)
    for _ in range(NEWTON_ITERATIONS):
        g_a = g_b = h_aa = h_ab = h_bb = 0.0
        for x, y in data:
            p = _sigmoid(a + b * x)
            w = max(p * (1 - p), 1e-9)
            g_a += (p - y)
            g_b += (p - y) * x
            h_aa += w
            h_ab += w * x
            h_bb += w * x * x
        det = h_aa * h_bb - h_ab * h_ab
        if abs(det) < 1e-12:
            return None
        da = (h_bb * g_a - h_ab * g_b) / det
        db = (h_aa * g_b - h_ab * g_a) / det
        step = 1.0
        for _halving in range(25):
            new_loss = _log_loss(data, a - step * da, b - step * db)
            if new_loss <= loss + 1e-12:
                break
            step *= 0.5
        else:
            break  # no acceptable step -- converged as far as damping allows
        a, b = a - step * da, b - step * db
        loss = new_loss
    return {"a": round(a, 6), "b": round(b, 6), "n": len(data), "devLogLoss": round(loss, 6)}


def attach_recalibrated(rows, key, params_by_tier):
    """params_by_tier: {"GLOBAL": {...}} for R1, or {tier: {...}} for R2.
    Rows whose tier has no fitted params keep raw p (counted by caller)."""
    for r in rows:
        params = params_by_tier.get("GLOBAL") or params_by_tier.get(tier_for_family(r["family"]))
        r[key] = round(apply_map(r["modelP"], params["a"], params["b"]), 6) if params else r["modelP"]


# ── Paired evaluation ─────────────────────────────────────────────────────

def paired_brier_delta(rows, key_candidate, key_control="modelP"):
    paired = [{"gameId": r["gameId"], "d": (r[key_candidate] - r["outcome"]) ** 2 - (r[key_control] - r["outcome"]) ** 2} for r in rows]

    def _mean(subset):
        return sum(x["d"] for x in subset) / len(subset) if subset else None

    point = _mean(paired)
    lo, hi, _ = game_clustered_bootstrap_ci(paired, _mean, cluster_key="gameId", seed=DEFAULT_BOOTSTRAP_SEED)
    return {"n": len(paired), "independentGames": independent_unit_count(paired, key="gameId"),
            "brierDelta": round(point, 6) if point is not None else None,
            "ci": {"low": lo, "high": hi, "method": "GAME_CLUSTERED_BOOTSTRAP"},
            "interpretation": "negative == recalibrated beats raw production probability"}


def split_metrics(rows, key):
    pairs = [(r[key], r["outcome"]) for r in rows]
    brier, log_loss = brier_and_log_loss_summary(pairs)
    return {"brier": brier, "logLoss": log_loss, "ece": expected_calibration_error(pairs)}


def market_gap(rows, key):
    """SECONDARY/descriptive: paired Brier delta of `key` vs the market's
    own price -- how much of MLB-RSCH-0022's measured gap remains."""
    paired = [{"gameId": r["gameId"], "d": (r[key] - r["outcome"]) ** 2 - (r["marketP"] - r["outcome"]) ** 2} for r in rows]

    def _mean(subset):
        return sum(x["d"] for x in subset) / len(subset) if subset else None

    point = _mean(paired)
    return round(point, 6) if point is not None else None


def selection_passes(dev_brier_delta, dev_logloss_delta, val_brier_delta, dev_ece_raw, dev_ece_recal, tier_deltas=None):
    reasons = []
    if dev_brier_delta is None or dev_brier_delta >= 0:
        reasons.append(f"DEV Brier delta not improved: {dev_brier_delta}")
    if dev_logloss_delta is None or dev_logloss_delta >= 0:
        reasons.append(f"DEV log-loss delta not improved: {dev_logloss_delta}")
    if val_brier_delta is None or val_brier_delta >= 0:
        reasons.append(f"VAL Brier delta does not replicate improvement: {val_brier_delta}")
    if dev_ece_raw is not None and dev_ece_recal is not None and dev_ece_recal > dev_ece_raw:
        reasons.append(f"DEV calibration error worsened: {dev_ece_recal} > {dev_ece_raw}")
    if tier_deltas is not None:
        improving = sum(1 for d in tier_deltas.values() if d is not None and d < 0)
        if improving < 2:
            reasons.append(f"R2 improvement driven by fewer than two tiers: {tier_deltas}")
    return (len(reasons) == 0), reasons


def main():
    print(f"[{EXPERIMENT_ID}] registering experiment/control...")
    control, definition = register_experiment()

    print(f"[{EXPERIMENT_ID}] building corpus via MLB-RSCH-0022's own loaders (unchanged)...")
    outcomes, _ = rsch0022.load_settled_outcomes()
    evaluated_rows, _ = rsch0022.load_evaluated_rows()
    audit_rows = rsch0022.build_audit_rows(evaluated_rows, outcomes, pick="last")
    dev_rows = [r for r in audit_rows if r["settleDate"] <= DEV_DATE_MAX]
    val_rows = [r for r in audit_rows if DEV_DATE_MAX < r["settleDate"] <= VAL_DATE_MAX]
    print(f"[{EXPERIMENT_ID}] rows: total={len(audit_rows)} DEV(<= {DEV_DATE_MAX})={len(dev_rows)} VAL={len(val_rows)}")

    # ---- R1: one global logit-affine, DEV only ----
    r1_params = {"GLOBAL": fit_logit_affine_dev_only(dev_rows)}
    print(f"[{EXPERIMENT_ID}] R1 global params (DEV-fit): {r1_params['GLOBAL']}")

    # ---- R2: per-tier logit-affine, DEV only ----
    r2_params = {}
    for tier in TIERS:
        tier_rows = [r for r in dev_rows if tier_for_family(r["family"]) == tier]
        r2_params[tier] = fit_logit_affine_dev_only(tier_rows)
        print(f"[{EXPERIMENT_ID}] R2 {tier} params (DEV-fit, n={len(tier_rows)}): {r2_params[tier]}")
    r2_params = {t: p for t, p in r2_params.items() if p is not None}

    for rows in (dev_rows, val_rows):
        attach_recalibrated(rows, "r1P", r1_params)
        attach_recalibrated(rows, "r2P", r2_params)

    results = {}
    for key, label, params in (("r1P", "R1", r1_params), ("r2P", "R2", r2_params)):
        dev_delta = paired_brier_delta(dev_rows, key)
        val_delta = paired_brier_delta(val_rows, key)
        dev_raw = split_metrics(dev_rows, "modelP")
        dev_recal = split_metrics(dev_rows, key)
        val_raw = split_metrics(val_rows, "modelP")
        val_recal = split_metrics(val_rows, key)
        tier_deltas = None
        if label == "R2":
            tier_deltas = {}
            for tier in TIERS:
                t_rows = [r for r in dev_rows if tier_for_family(r["family"]) == tier]
                tier_deltas[tier] = paired_brier_delta(t_rows, key)["brierDelta"] if t_rows else None
        passes, reasons = selection_passes(
            dev_delta["brierDelta"], round(dev_recal["logLoss"] - dev_raw["logLoss"], 6),
            val_delta["brierDelta"], dev_raw["ece"], dev_recal["ece"], tier_deltas,
        )
        results[label] = {
            "params": params, "devPairedBrierDelta": dev_delta, "valPairedBrierDelta": val_delta,
            "devRaw": dev_raw, "devRecalibrated": dev_recal, "valRaw": val_raw, "valRecalibrated": val_recal,
            "tierDeltasDev": tier_deltas,
            "marketGapRawVal": market_gap(val_rows, "modelP"), "marketGapRecalVal": market_gap(val_rows, key),
            "selection": {"passes": passes, "reasons": reasons},
        }
        print(f"[{EXPERIMENT_ID}] {label}: DEV Brier delta={dev_delta['brierDelta']} VAL Brier delta={val_delta['brierDelta']} passes={passes} reasons={reasons}")

    if results["R1"]["selection"]["passes"]:
        selected = "R1"  # simplicity-first, preregistered
    elif results["R2"]["selection"]["passes"]:
        selected = "R2"
    else:
        selected = None
    print(f"[{EXPERIMENT_ID}] selected: {selected}")

    if selected is not None:
        classification = "LEVEL_1_SHADOW_CANDIDATE"  # hard cap -- VAL is not direction-blind; FORWARD must confirm
    else:
        classification = "LEVEL_0_NO_VALIDATED_CORRECTION"

    report = {
        "experimentId": EXPERIMENT_ID,
        "controlModelId": control["controlModelId"],
        "corpus": {"totalRows": len(audit_rows), "devRows": len(dev_rows), "valRows": len(val_rows),
                   "devGames": independent_unit_count(dev_rows, key="gameId"), "valGames": independent_unit_count(val_rows, key="gameId"),
                   "forward": "settle > 2026-08-28 -- preregistered blind window, NOT computed in this run"},
        "tiers": {t: list(f) for t, f in TIERS.items()},
        "results": results,
        "selected": selected,
        "classification": classification,
        "frozenSelectedParams": (results[selected]["params"] if selected else None),
        "governance": {
            "contaminationDisclosure": "VAL is not direction-blind (MLB-RSCH-0022 observed the pooled miscalibration direction through 08-28); FORWARD window is the fully blind test.",
            "maxClassification": "LEVEL_1_SHADOW_CANDIDATE", "productionChanged": False,
            "noMarketFit": True, "noRoiFit": True, "noNewApiCalls": True,
        },
    }

    out_path = os.path.join("data", "edgelab", "analytics", "latest_mlb_rsch_0023_probability_recalibration.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    print(f"[{EXPERIMENT_ID}] wrote {out_path}")
    print(f"[{EXPERIMENT_ID}] classification={classification}")
    return report


if __name__ == "__main__":
    main()
