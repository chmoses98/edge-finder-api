"""
lib/edgelab/research/methodology_v2.py
=====================================
EdgeLab Research Lab Methodology V2 selection-gate contract for FUTURE
expected-run mean-model experiments. RESEARCH ONLY -- nothing in
production imports this module, and no prior experiment's registration,
artifact, or disposition is affected by it.

Produced from MLB-RSCH-0021's finding (MAE_PRIMARY_METRIC_INAPPROPRIATE):
MAE is minimized by the conditional MEDIAN, while this program's frozen
negative-binomial probability engine consumes the predicted expected-run
value as a conditional MEAN parameter. Two independent real candidates
(MLB-RSCH-0015's S1, MLB-RSCH-0020's B3) lowered MAE while worsening
MSE, NB negative log-likelihood, AND downstream Brier simultaneously --
so an experiment that gates candidate selection on MAE alone can select
exactly the wrong candidates.

V2 CONTRACT (per the program's own directive of 2026-08-28):
  PRIMARY MEAN METRIC:    MSE/RMSE delta (squared error is consistent
                          for the conditional mean).
  DISTRIBUTIONAL GATE:    frozen-distribution negative log-likelihood /
                          deviance delta, where a frozen scoring
                          distribution applies. NOT the sole definition
                          of mean quality -- it also depends on the
                          assumed distribution family -- hence a GATE
                          alongside MSE, not a replacement for it.
  PROBABILITY GATE:       proper scoring rules on derived market
                          probabilities (Brier / log loss / calibration).
  SECONDARY (never gating alone): signed bias, mean calibration
                          diagnostics, MAE for interpretability.

This module deliberately does NOT modify experiment_registry or any
canonical framework code -- a V2 experiment opts in by calling
mean_candidate_gates_v2() as its selection rule and saying so in its own
registration notes. V1 (MAE-primary) experiments remain reproducible
exactly as committed.
"""

METHODOLOGY_VERSION = "v2"

# A mean-model candidate must not degrade the probability gate beyond this
# (same spirit as the 0-tolerance most recent experiments already used;
# an experiment MAY preregister a stricter/looser fixed tolerance in its
# own registration, but the DEFAULT is improve-or-preserve).
DEFAULT_PROBABILITY_TOLERANCE = 0.0
DEFAULT_NLL_TOLERANCE = 0.0


def mean_candidate_gates_v2(
    *, dev_mse_delta, dev_nll_delta, dev_brier_delta,
    val_mse_delta=None, val_brier_delta=None,
    probability_tolerance=DEFAULT_PROBABILITY_TOLERANCE,
    nll_tolerance=DEFAULT_NLL_TOLERANCE,
    dev_mae_delta=None,
):
    """
    The V2 selection gate for an expected-run mean candidate vs its
    control. All deltas are candidate-minus-control (negative == candidate
    better). Returns (passes: bool, reasons: list[str]).

    Gates (all must hold):
      1. DEV MSE delta improves (< 0)               -- PRIMARY mean metric.
      2. DEV frozen-distribution NLL delta improves
         or is within nll_tolerance                  -- distributional gate.
      3. DEV probability (Brier) delta improves or
         is within probability_tolerance             -- probability gate.
      4. VAL MSE delta, when provided, does not
         flip sign (must be < 0)                     -- replication.
      5. VAL Brier delta, when provided, improves
         or is within probability_tolerance          -- replication.

    `dev_mae_delta` is accepted ONLY for reporting completeness and is
    deliberately IGNORED by the gate logic: per MLB-RSCH-0021, MAE must
    never independently qualify (or disqualify) an expected-run
    candidate. Passing a favorable MAE with unfavorable MSE/NLL/Brier
    still fails; the reverse still passes.
    """
    reasons = []
    if dev_mse_delta is None or dev_mse_delta >= 0:
        reasons.append(f"V2 gate 1: DEV MSE delta not improved (primary mean metric): {dev_mse_delta}")
    if dev_nll_delta is None or dev_nll_delta > nll_tolerance:
        reasons.append(f"V2 gate 2: DEV frozen-distribution NLL delta exceeds tolerance {nll_tolerance}: {dev_nll_delta}")
    if dev_brier_delta is None or dev_brier_delta > probability_tolerance:
        reasons.append(f"V2 gate 3: DEV probability (Brier) delta exceeds tolerance {probability_tolerance}: {dev_brier_delta}")
    if val_mse_delta is not None and val_mse_delta >= 0:
        reasons.append(f"V2 gate 4: VALIDATION MSE delta does not replicate improvement: {val_mse_delta}")
    if val_brier_delta is not None and val_brier_delta > probability_tolerance:
        reasons.append(f"V2 gate 5: VALIDATION probability (Brier) delta exceeds tolerance {probability_tolerance}: {val_brier_delta}")
    return (len(reasons) == 0), reasons


def assert_not_mae_primary(primary_metric_text: str) -> None:
    """
    Registration-time guard for a V2 experiment: raises if the
    registration's own primary_metric text declares MAE as the primary
    selection metric for an expected-run mean. Purely opt-in -- an
    experiment calls this on its own primary_metric string before
    registering; nothing scans or mutates existing registrations.
    """
    text = (primary_metric_text or "").lower()
    if "mae" in text and not any(term in text for term in ("mse", "rmse", "squared", "log-likelihood", "log likelihood", "nll", "deviance", "brier")):
        raise ValueError(
            "Methodology V2 violation: primary_metric declares MAE as the primary selection metric for an "
            "expected-run mean. Per MLB-RSCH-0021 (MAE_PRIMARY_METRIC_INAPPROPRIATE), use MSE/RMSE as the "
            "primary mean metric with NLL/deviance and Brier gates; MAE may appear only as a secondary, "
            "interpretability-only metric."
        )
