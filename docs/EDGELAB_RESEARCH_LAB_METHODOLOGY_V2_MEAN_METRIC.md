# EdgeLab Research Lab Methodology v2: Primary Mean Metric

Status: **ADOPTED FOR FUTURE EXPERIMENTS. OPT-IN. VERSIONED.**

Produced by MLB-RSCH-0021 (see `docs/EDGELAB_MLB_RSCH_0021_LOSS_FUNCTION_AUDIT.md`
for the full evidence), refined per the research program's directive of
2026-08-28. **Does not modify any canonical framework code, any prior
experiment's registration, artifact, or disposition.** Prior experiments
(MLB-RSCH-0009 through MLB-RSCH-0021) remain reproducible exactly as
committed, under v1 (MAE-primary) conventions. MLB-RSCH-0015's S1 and
MLB-RSCH-0020's B3 remain REJECTED; this document does not resurrect
them.

## Why

MAE is minimized by the conditional median. This program's frozen
negative-binomial probability engine (`lib.edgelab.backtest.run_distributions`)
consumes a candidate's predicted expected-run value as a conditional
**mean** parameter. MLB-RSCH-0021 found -- empirically on two
independent frozen candidates (MLB-RSCH-0015's S1, MLB-RSCH-0020's B3)
and by deterministic synthetic construction -- that a candidate can
lower MAE while simultaneously worsening MSE, NB negative
log-likelihood, and frozen-NB Brier. An MAE-primary selection gate can
therefore select exactly the wrong candidates.

## The v2 contract (for future expected-run mean-model experiments)

**PRIMARY MEAN METRIC: MSE / RMSE.** Squared error is consistent for
the conditional mean -- the quantity the probability engine actually
consumes.

**DISTRIBUTIONAL GATE: frozen-distribution negative log-likelihood /
deviance**, where a frozen scoring distribution applies (currently
MLB-RSCH-0010's NB, dispersion never refit per candidate).
Deliberately a GATE alongside MSE, **not** the sole definition of mean
quality -- NLL also depends on the assumed distribution family, so
making it the single primary metric would entangle mean-quality
conclusions with distribution-family assumptions.

**PROBABILITY GATE: proper scoring rules** on the derived market
probabilities -- Brier, log loss, calibration -- unchanged in spirit
from existing practice, now explicitly paired with a mean metric that
measures the same target.

**SECONDARY (reported, never gating alone):** signed bias, mean
calibration diagnostics (fixed predicted-run buckets + diagnostic OLS),
and MAE for interpretability. **MAE must not independently qualify (or
disqualify) an expected-run candidate.**

## Enforcement helper (opt-in)

`lib/edgelab/research/methodology_v2.py` provides:

- `mean_candidate_gates_v2(...)` -- the V2 selection gate as a single
  reusable function (DEV MSE improves; DEV NLL within tolerance; DEV
  Brier within tolerance; VAL MSE/Brier replicate when provided). Its
  `dev_mae_delta` argument is accepted for reporting completeness and
  **ignored by the gate logic**, structurally preventing an accidental
  reversion to MAE-primary selection.
- `assert_not_mae_primary(primary_metric_text)` -- a registration-time
  guard a V2 experiment calls on its own `primary_metric` string; raises
  if MAE is declared as the primary selection metric with no
  mean-consistent metric alongside it.

A future experiment adopts v2 by using this gate as its selection rule
and stating so in its registration `notes` (e.g. "uses EdgeLab Research
Lab Methodology v2 -- MSE primary, NLL + Brier gates, MAE secondary").
Nothing scans or mutates historical registrations; v1 experiments are
not re-run or reinterpreted.

## Minimum reporting set for a v2 mean-model experiment

1. MSE, RMSE (primary)
2. Frozen-distribution NLL delta (distributional gate)
3. Frozen-NB Brier deltas by market family (probability gate)
4. Signed bias (mean residual) and median residual
5. Calibration bucket table + diagnostic OLS slope/intercept
6. MAE (secondary/interpretability)

## What does NOT change

- No canonical framework code (`lib/edgelab/*` outside the opt-in
  research helper) is modified.
- No prior experiment's `selection_passes()`, registration, or committed
  artifact is touched or reinterpreted.
- S1 and B3 remain **REJECTED**. A future experiment MAY re-test a
  materially similar hypothesis under v2, but that is a **new,
  separately preregistered** experiment -- never a silent
  reinterpretation of RSCH-0015/0020's own results.
