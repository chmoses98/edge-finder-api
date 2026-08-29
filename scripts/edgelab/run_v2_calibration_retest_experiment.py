#!/usr/bin/env python3
"""
scripts/edgelab/run_v2_calibration_retest_experiment.py
====================================================================
Research Lab experiment MLB-RSCH-0025: "Methodology-V2 Retest of the
MLB-RSCH-0014 Mean-Calibration Candidates". RESEARCH ONLY. NO
production changes, no candidate activation.

WHY THIS EXPERIMENT EXISTS (the Phase-1 triage finding):
MLB-RSCH-0014 tested three post-hoc calibration maps on the frozen
MLB-RSCH-0009 expected-run mean and rejected ALL THREE. Its own
committed artifact shows why, and it is exactly the failure mode
MLB-RSCH-0021 later identified:

  candidate   DEV MAE delta   DEV RMSE          DEV+VAL Brier deltas
  C0 control       --         3.1104            --
  C1            +0.00808      3.1076 (BETTER)   all 5 families NEGATIVE
  C2            +0.00753      3.1070 (BETTER)   all 5 families NEGATIVE
  C3            +0.00809      3.1076 (BETTER)   all 5 families NEGATIVE

Every candidate was rejected on "DEV MAE delta not negative" while
simultaneously improving RMSE (the mean-consistent metric) AND improving
downstream frozen-NB Brier in EVERY market family on BOTH the DEV and
VALIDATION splits. Under Methodology V2 (MSE/RMSE primary, NLL/deviance
distributional gate, Brier/log-loss/calibration probability gate, MAE
secondary only) these candidates would not have been rejected on that
basis. They are the clearest Methodology-V2 casualties in the program.

GOVERNANCE -- what this experiment is and is not:
  * MLB-RSCH-0014's own registration, artifact and conclusion are NOT
    modified or reinterpreted. Under its own preregistered (v1) rules it
    was decided correctly. This is a NEW experiment with a NEW id.
  * The candidate SPECIFICATIONS are frozen: this script imports
    run_mean_calibration_experiment and calls its own
    fit_c1_global_affine_dev_only / fit_c2_home_away_affine_dev_only /
    fit_c3_quadratic_dev_only / calibrate_value /
    attach_calibrated_predictions UNCHANGED. No formula, floor, or
    parameterization is altered, and no new candidate is invented.
  * The baseball hypothesis is not tuned after seeing results.

HOLDOUT LEGITIMACY (checked before designing this run): MLB-RSCH-0014's
own artifact records `holdout2026: null` and `passingCandidates: []` --
because no candidate passed its v1 gate, the 2026 holdout was NEVER
unlocked for C1/C2/C3. Their 2026 evidence is therefore genuinely
blind, and this experiment unlocks it exactly once, only for a
candidate that first passes the preregistered V2 DEV+VAL gate.

METHODOLOGY V2 GATE (locked before any result is examined; implemented
by lib.edgelab.research.methodology_v2.mean_candidate_gates_v2, whose
`dev_mae_delta` argument is structurally ignored):
  1. DEV MSE delta < 0            (PRIMARY mean metric)
  2. DEV frozen-NB NLL delta <= 0 (distributional gate)
  3. DEV Brier delta <= 0         (probability gate)
  4. VAL MSE delta < 0            (replication)
  5. VAL Brier delta <= 0         (replication)
MAE is computed and reported for interpretability ONLY and can neither
qualify nor disqualify a candidate. Simplicity-first tie-break: if more
than one candidate passes, prefer the one with the FEWEST parameters
(C1 global affine: 2 < C2 home/away affine: 4 < C3 quadratic: 3 -> order
C1, C3, C2), decided here BEFORE any holdout access.

MAX DISPOSITION: SHADOW_CANDIDATE. This experiment cannot promote
anything to production.
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

from lib.edgelab.backtest.run_distributions import negative_binomial_pmf
from lib.edgelab import experiment_registry as reg
from lib.edgelab import evidence_levels as ev
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab.research.methodology_v2 import mean_candidate_gates_v2, assert_not_mae_primary, METHODOLOGY_VERSION
from lib.edgelab.research_stats import (
    DEFAULT_BOOTSTRAP_SEED, independent_unit_count, sample_size_status, game_clustered_bootstrap_ci,
)

import run_mean_calibration_experiment as rsch0014  # noqa: E402 -- frozen candidate specs reused UNCHANGED

EXPERIMENT_ID = "MLB-RSCH-0025"
REGISTRATION_TIMESTAMP = "2026-08-28T21:00:00Z"

FROZEN_DISPERSION = 0.281513

DEV_SEASONS = [2022, 2023, 2024]
VALIDATION_SEASONS = [2025]
HOLDOUT_SEASONS = [2026]

# Preregistered simplicity-first order (parameter counts), fixed before any result.
CANDIDATE_ORDER = ("C1_global_affine", "C3_quadratic", "C2_home_away_affine")
# Kind tokens are MLB-RSCH-0014's OWN module constants (rsch0014.C1/C2/C3) --
# resolved at call time so this retest can never drift from the frozen spec.
CANDIDATE_KIND_ATTRS = {"C1_global_affine": "C1", "C3_quadratic": "C3", "C2_home_away_affine": "C2"}

PRIMARY_FAMILIES = ("game_total", "team_total_home", "team_total_away", "moneyline")


def _verify_frozen_dispersion():
    path = os.path.join(_ROOT, "data", "edgelab", "analytics", "latest_mlb_rsch_0010_run_distribution.json")
    with open(path) as f:
        canonical = json.load(f)["fittedParameters"]["overdispersion"]
    if FROZEN_DISPERSION != canonical:
        raise ValueError(f"FROZEN_DISPERSION drifted: {FROZEN_DISPERSION} != {canonical}")


_verify_frozen_dispersion()


def _current_git_commit_sha():
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


PRIMARY_METRIC_TEXT = (
    "paired MSE delta on next-game team runs scored (candidate minus C0), game-and-team-clustered 95% CI "
    "-- Methodology V2 primary mean metric; MAE reported as a secondary interpretability metric only"
)
assert_not_mae_primary(PRIMARY_METRIC_TEXT)  # V2 registration-time guard


def register_experiment():
    try:
        existing_definition = reg.load_experiment(EXPERIMENT_ID)
    except FileNotFoundError:
        existing_definition = None
    if existing_definition is not None:
        control = ctrl_id.load_control(existing_definition["controlModelId"])
        return control, existing_definition

    control = ctrl_id.build_control_registration(
        name="mlb_rsch_0025_v2_calibration_retest_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0025 V2 retest v1: MLB-RSCH-0014's own frozen C0 control and C1/C2/C3 "
                        "calibration maps, reused UNCHANGED, re-evaluated under Methodology V2 "
                        "(MSE primary, frozen-NB NLL gate, Brier gate; MAE secondary only)."
        ),
        probability_adapter_identity="lib.edgelab.backtest.run_distributions (frozen MLB-RSCH-0010 negative-binomial, dispersion unchanged, never refit)",
        model_engine_family="pit_safe_research_mean_calibration_v1",
        required_input_provenance=["team_recent_game_log_reconstruction"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "Methodology-V2 retest of MLB-RSCH-0014's three post-hoc mean-calibration candidates, which "
            "that experiment rejected on an MAE gate despite each improving RMSE and improving frozen-NB "
            "Brier in every family on both DEV and VALIDATION. Candidate specifications frozen and reused "
            "unchanged; MLB-RSCH-0014's own conclusion is untouched."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Methodology-V2 Retest of Mean-Calibration Candidates",
        hypothesis=(
            "H1: MLB-RSCH-0014's C1/C2/C3 calibration maps improve the CONDITIONAL MEAN of the frozen "
            "expected-run model (MSE/RMSE) and improve downstream frozen-NB probability scoring, and were "
            "rejected only because the v1 gate used MAE -- a median-targeting loss. H2: at least one "
            "candidate passes the full Methodology-V2 DEV+VAL gate. H3 (null, tested not assumed): the "
            "DEV/VAL probability gains may not replicate on the genuinely blind 2026 holdout, in which "
            "case the candidate is retired -- an MAE-methodology casualty is not automatically a winner."
        ),
        research_question=(
            "Do MLB-RSCH-0014's rejected mean-calibration candidates actually improve probability quality "
            "when evaluated under Methodology V2, including on a 2026 holdout that was never unlocked for "
            "them?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E2_PIT_HISTORICAL,
        target_population="Identical to MLB-RSCH-0014: MLB-RSCH-0009's own build_season_rows corpus, 2022-2026 (~10,204 games / 20,408 team observations)",
        market_families=["game_result", "game_total", "team_total", "run_margin"],
        eligibility_criteria=["identical to MLB-RSCH-0014's own eligibility -- its loaders and candidate functions are imported unchanged"],
        exclusion_criteria=[
            "any modification of the C1/C2/C3 formulas, floors, or parameterization",
            "any new calibration candidate invented for this retest",
            "MAE as a qualifying or disqualifying criterion (Methodology V2)",
            "holdout access for any candidate that has not first passed the V2 DEV+VAL gate",
            "modification or reinterpretation of MLB-RSCH-0014's own artifact or conclusion",
        ],
        prediction_checkpoints=["SEASON_TO_DATE_PREGAME"],
        primary_metric=PRIMARY_METRIC_TEXT,
        secondary_metrics=[
            "frozen-NB negative log-likelihood delta (distributional gate)",
            "frozen-NB Brier/log-loss deltas by market family (probability gate)",
            "RMSE, signed bias, mean/median residual, calibration diagnostics",
            "MAE (interpretability only -- never gating)",
        ],
        chronological_split_policy=(
            f"SEASON_BASED: development={DEV_SEASONS}, validation={VALIDATION_SEASONS}, holdout={HOLDOUT_SEASONS} "
            "(locked). MLB-RSCH-0014 recorded holdout2026=null with passingCandidates=[] -- the 2026 holdout "
            "was never unlocked for C1/C2/C3, so their 2026 evidence is genuinely blind here and is unlocked "
            "exactly once, only for a candidate that first passes the V2 DEV+VAL gate."
        ),
        minimum_sample_requirement={"independentGames": 50},
        clustering_unit="gamePk",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={"team_recent_game_log_reconstruction": "PREDICTIVE_INPUT"},
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            f"Uses EdgeLab Research Lab Methodology {METHODOLOGY_VERSION} -- MSE primary, frozen-NB NLL and "
            "Brier gates, MAE secondary only; the gate is lib.edgelab.research.methodology_v2."
            "mean_candidate_gates_v2, whose dev_mae_delta argument is structurally ignored. Frozen "
            "MLB-RSCH-0010 dispersion reused unchanged, never refit. MLB-RSCH-0014's own registration, "
            "artifact and conclusion are NOT modified. Max disposition SHADOW_CANDIDATE."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── Metrics (V2: MSE primary; MAE reported but never gating) ──────────────

def mean_metrics(obs):
    if not obs:
        return {"n": 0, "mse": None, "rmse": None, "mae": None, "bias": None, "medianResidual": None}
    residuals = [o["predicted"] - o["actual"] for o in obs]
    n = len(residuals)
    sq = [r * r for r in residuals]
    srt = sorted(residuals)
    med = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2
    return {
        "n": n, "independentGames": independent_unit_count(obs, key="gamePk"),
        "mse": round(sum(sq) / n, 6), "rmse": round(math.sqrt(sum(sq) / n), 6),
        "mae": round(sum(abs(r) for r in residuals) / n, 6),
        "bias": round(sum(residuals) / n, 6), "medianResidual": round(med, 6),
    }


def paired_mean_deltas(obs_a, obs_b):
    """Paired MSE (PRIMARY) and MAE (secondary) deltas on identical rows."""
    by_a = {(o["gamePk"], o["teamId"]): o for o in obs_a}
    by_b = {(o["gamePk"], o["teamId"]): o for o in obs_b}
    common = sorted(set(by_a) & set(by_b))
    rows = [{
        "gamePk": k[0],
        "sqA": (by_a[k]["predicted"] - by_a[k]["actual"]) ** 2, "sqB": (by_b[k]["predicted"] - by_b[k]["actual"]) ** 2,
        "abA": abs(by_a[k]["predicted"] - by_a[k]["actual"]), "abB": abs(by_b[k]["predicted"] - by_b[k]["actual"]),
    } for k in common]

    def _mse(subset):
        return sum(r["sqB"] - r["sqA"] for r in subset) / len(subset) if subset else None

    def _mae(subset):
        return sum(r["abB"] - r["abA"] for r in subset) / len(subset) if subset else None

    mse_pt, mae_pt = _mse(rows), _mae(rows)
    lo, hi, _ = game_clustered_bootstrap_ci(rows, _mse, cluster_key="gamePk", seed=DEFAULT_BOOTSTRAP_SEED)
    return {"n": len(rows), "independentGames": independent_unit_count(rows, key="gamePk"),
            "mseDelta": round(mse_pt, 6) if mse_pt is not None else None,
            "mseDeltaCI95": {"low": lo, "high": hi, "method": "GAME_CLUSTERED_BOOTSTRAP"},
            "maeDeltaSecondaryOnly": round(mae_pt, 6) if mae_pt is not None else None,
            "interpretation": "negative == candidate improved (V2 primary is mseDelta; maeDelta is reported, never gating)"}


def nb_nll(obs):
    """Frozen-distribution negative log-likelihood -- the V2 distributional gate."""
    vals = []
    for o in obs:
        mu = o["predicted"]
        if mu is None or mu <= 0:
            continue
        p = negative_binomial_pmf(int(round(o["actual"])), mu, FROZEN_DISPERSION)
        if p > 0:
            vals.append(-math.log(p))
    return round(sum(vals) / len(vals), 6) if vals else None


def paired_nll_delta(obs_a, obs_b):
    by_a = {(o["gamePk"], o["teamId"]): o for o in obs_a}
    by_b = {(o["gamePk"], o["teamId"]): o for o in obs_b}
    rows = []
    for k in sorted(set(by_a) & set(by_b)):
        oa, ob = by_a[k], by_b[k]
        if oa["predicted"] is None or oa["predicted"] <= 0 or ob["predicted"] is None or ob["predicted"] <= 0:
            continue
        y = int(round(oa["actual"]))
        pa, pb = negative_binomial_pmf(y, oa["predicted"], FROZEN_DISPERSION), negative_binomial_pmf(y, ob["predicted"], FROZEN_DISPERSION)
        if pa > 0 and pb > 0:
            rows.append({"gamePk": k[0], "d": -math.log(pb) + math.log(pa)})

    def _d(subset):
        return sum(r["d"] for r in subset) / len(subset) if subset else None

    pt = _d(rows)
    lo, hi, _ = game_clustered_bootstrap_ci(rows, _d, cluster_key="gamePk", seed=DEFAULT_BOOTSTRAP_SEED)
    return {"n": len(rows), "nllDelta": round(pt, 6) if pt is not None else None,
            "nllDeltaCI95": {"low": lo, "high": hi}, "interpretation": "negative == candidate better distributional fit"}


def primary_brier_delta(nb_result):
    ds = [nb_result["byFamily"][f]["pairedDelta"]["brierScore"] for f in PRIMARY_FAMILIES
          if nb_result["byFamily"].get(f) and nb_result["byFamily"][f]["pairedDelta"].get("brierScore") is not None]
    return round(sum(ds) / len(ds), 6) if ds else None


def main():
    print(f"[{EXPERIMENT_ID}] registering experiment/control (Methodology {METHODOLOGY_VERSION})...")
    control, definition = register_experiment()

    print(f"[{EXPERIMENT_ID}] building corpus via MLB-RSCH-0014's own loaders (unchanged)...")
    rows_by_season = rsch0014.build_corpus()
    if isinstance(rows_by_season, tuple):
        rows_by_season = rows_by_season[0]
    dev_rows = [r for s in DEV_SEASONS for r in rows_by_season[s]]
    val_rows = [r for s in VALIDATION_SEASONS for r in rows_by_season[s]]
    holdout_rows = [r for s in HOLDOUT_SEASONS for r in rows_by_season[s]]
    all_rows = dev_rows + val_rows + holdout_rows
    print(f"[{EXPERIMENT_ID}] rows: dev={len(dev_rows)} val={len(val_rows)} holdout={len(holdout_rows)}")

    hfa_c0 = rsch0014.fit_hfa_c0(dev_rows)
    rsch0014.attach_c0_predictions(all_rows, hfa_c0)
    print(f"[{EXPERIMENT_ID}] C0 HFA (frozen, reproduced): {hfa_c0}")

    dev_obs_c0 = rsch0014.team_observations(dev_rows, "C0")
    val_obs_c0 = rsch0014.team_observations(val_rows, "C0")

    # Fit each candidate on DEV only, using RSCH-0014's own frozen fitters.
    # Each fitter returns (params, diagnostics) -- that is its own committed
    # contract; only `params` feeds attach_calibrated_predictions.
    raw_fits = {
        "C1_global_affine": rsch0014.fit_c1_global_affine_dev_only(dev_obs_c0),
        "C2_home_away_affine": rsch0014.fit_c2_home_away_affine_dev_only(dev_obs_c0),
        "C3_quadratic": rsch0014.fit_c3_quadratic_dev_only(dev_obs_c0),
    }
    fits, fit_diagnostics = {}, {}
    for name, (params, diagnostics) in raw_fits.items():
        fits[name], fit_diagnostics[name] = params, diagnostics
        kind = getattr(rsch0014, CANDIDATE_KIND_ATTRS[name])  # rsch0014's own C1/C2/C3 constant
        print(f"[{EXPERIMENT_ID}] {name} DEV-fit params (frozen spec): {params}")
        rsch0014.attach_calibrated_predictions(all_rows, name, kind, params)

    results = {}
    for name in CANDIDATE_ORDER:
        dev_obs = rsch0014.team_observations(dev_rows, name)
        val_obs = rsch0014.team_observations(val_rows, name)
        dev_delta = paired_mean_deltas(dev_obs_c0, dev_obs)
        val_delta = paired_mean_deltas(val_obs_c0, val_obs)
        dev_nll_d = paired_nll_delta(dev_obs_c0, dev_obs)
        dev_nb = rsch0014.frozen_nb_probability_eval(dev_rows, "C0", name)
        val_nb = rsch0014.frozen_nb_probability_eval(val_rows, "C0", name)
        dev_brier, val_brier = primary_brier_delta(dev_nb), primary_brier_delta(val_nb)

        passes, reasons = mean_candidate_gates_v2(
            dev_mse_delta=dev_delta["mseDelta"], dev_nll_delta=dev_nll_d["nllDelta"], dev_brier_delta=dev_brier,
            val_mse_delta=val_delta["mseDelta"], val_brier_delta=val_brier,
            dev_mae_delta=dev_delta["maeDeltaSecondaryOnly"],  # accepted for reporting; structurally ignored
        )
        results[name] = {
            "params": fits[name], "fitDiagnostics": fit_diagnostics[name], "devMean": mean_metrics(dev_obs), "devControlMean": mean_metrics(dev_obs_c0),
            "valMean": mean_metrics(val_obs), "valControlMean": mean_metrics(val_obs_c0),
            "devPairedDelta": dev_delta, "valPairedDelta": val_delta,
            "devNllDelta": dev_nll_d, "devNllControl": nb_nll(dev_obs_c0), "devNllCandidate": nb_nll(dev_obs),
            "devBrierPrimaryDelta": dev_brier, "valBrierPrimaryDelta": val_brier,
            "devNbByFamily": {f: dev_nb["byFamily"][f]["pairedDelta"] for f in dev_nb["byFamily"]},
            "valNbByFamily": {f: val_nb["byFamily"][f]["pairedDelta"] for f in val_nb["byFamily"]},
            "v2Selection": {"passes": passes, "reasons": reasons},
        }
        print(f"[{EXPERIMENT_ID}] {name}: devMSE={dev_delta['mseDelta']} valMSE={val_delta['mseDelta']} "
              f"devNLL={dev_nll_d['nllDelta']} devBrier={dev_brier} valBrier={val_brier} "
              f"(MAE secondary={dev_delta['maeDeltaSecondaryOnly']}) passes={passes}")
        if reasons:
            print(f"[{EXPERIMENT_ID}]     reasons: {reasons}")

    passing = [n for n in CANDIDATE_ORDER if results[n]["v2Selection"]["passes"]]
    selected = passing[0] if passing else None  # simplicity-first order, fixed before any holdout access
    print(f"[{EXPERIMENT_ID}] V2 passing candidates: {passing} -> selected={selected}")

    holdout_result = None
    if selected is not None:
        print(f"[{EXPERIMENT_ID}] V2 gate passed -- unlocking the genuinely blind 2026 holdout ONCE for {selected}...")
        h_obs_c0 = rsch0014.team_observations(holdout_rows, "C0")
        h_obs = rsch0014.team_observations(holdout_rows, selected)
        h_delta = paired_mean_deltas(h_obs_c0, h_obs)
        h_nll = paired_nll_delta(h_obs_c0, h_obs)
        h_nb = rsch0014.frozen_nb_probability_eval(holdout_rows, "C0", selected)
        h_brier = primary_brier_delta(h_nb)
        holdout_result = {
            "candidate": selected, "controlMean": mean_metrics(h_obs_c0), "candidateMean": mean_metrics(h_obs),
            "pairedDelta": h_delta, "nllDelta": h_nll, "brierPrimaryDelta": h_brier,
            "nbByFamily": {f: h_nb["byFamily"][f]["pairedDelta"] for f in h_nb["byFamily"]},
            "holdoutConfirms": (h_delta["mseDelta"] is not None and h_delta["mseDelta"] < 0
                                and h_brier is not None and h_brier <= 0),
        }
        print(f"[{EXPERIMENT_ID}] HOLDOUT {selected}: mse={h_delta['mseDelta']} nll={h_nll['nllDelta']} "
              f"brier={h_brier} confirms={holdout_result['holdoutConfirms']}")
    else:
        print(f"[{EXPERIMENT_ID}] no candidate passed the V2 gate -- holdout NOT unlocked, path retired.")

    if selected is None:
        classification, disposition = "V2_RETEST_NO_CANDIDATE_PASSES", "REJECT"
    elif holdout_result["holdoutConfirms"]:
        classification, disposition = "V2_RETEST_CONFIRMED_ON_BLIND_HOLDOUT", "SHADOW_CANDIDATE"
    else:
        classification, disposition = "V2_RETEST_DEV_VAL_ONLY_HOLDOUT_DID_NOT_CONFIRM", "REJECT"

    report = {
        "experimentId": EXPERIMENT_ID, "controlModelId": control["controlModelId"],
        "methodology": METHODOLOGY_VERSION,
        "provenance": {
            "retestOf": "MLB-RSCH-0014",
            "originalConclusionUnchanged": "MLB-RSCH-0014 remains historically correct under its own v1 (MAE-primary) rules; nothing there is modified or reinterpreted.",
            "whyRetested": "Its own artifact shows C1/C2/C3 each improved RMSE and improved frozen-NB Brier in every family on DEV and VAL, yet were rejected on 'DEV MAE delta not negative'.",
            "holdoutLegitimacy": "MLB-RSCH-0014 recorded holdout2026=null / passingCandidates=[] -- the 2026 holdout was never unlocked for these candidates.",
            "candidateSpecsFrozen": "fit_c1/c2/c3_*_dev_only, calibrate_value, attach_calibrated_predictions imported from run_mean_calibration_experiment and called unchanged.",
        },
        "corpus": {"devRows": len(dev_rows), "valRows": len(val_rows), "holdoutRows": len(holdout_rows),
                   "devGames": independent_unit_count(dev_rows, key="gamePk")},
        "homeFieldAdjustmentC0": hfa_c0,
        "results": results, "v2PassingCandidates": passing, "selected": selected,
        "holdout2026": holdout_result,
        "classification": classification, "disposition": disposition,
        "governance": {"productionChanged": False, "maxDisposition": "SHADOW_CANDIDATE",
                       "maeCanNeverGate": True, "priorExperimentArtifactsUnmodified": True},
    }

    out_path = os.path.join("data", "edgelab", "analytics", "latest_mlb_rsch_0025_v2_calibration_retest.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    print(f"[{EXPERIMENT_ID}] wrote {out_path}")
    print(f"[{EXPERIMENT_ID}] classification={classification} disposition={disposition}")
    return report


if __name__ == "__main__":
    main()
