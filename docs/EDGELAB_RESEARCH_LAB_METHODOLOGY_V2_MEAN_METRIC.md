# EdgeLab Research Lab Methodology v2: Primary Mean Metric

Status: **RECOMMENDATION. OPT-IN. VERSIONED. Applies to FUTURE
experiments only.**

Produced by MLB-RSCH-0021 (see `docs/EDGELAB_MLB_RSCH_0021_LOSS_FUNCTION_AUDIT.md`
for the full evidence). **Does not modify any canonical framework code,
any prior experiment's registration, artifact, or disposition.** Prior
experiments (MLB-RSCH-0009 through MLB-RSCH-0020) remain reproducible
exactly as committed, under v1 (MAE-primary) conventions.

## Why

MAE is minimized by the conditional median. This program's frozen
negative-binomial probability engine (`lib.edgelab.backtest.run_distributions`)
treats a candidate's predicted expected-run value as a conditional
**mean** parameter. MLB-RSCH-0021 found, empirically (two independent
candidates: MLB-RSCH-0015's S1 and MLB-RSCH-0020's B3) and by
deterministic synthetic construction, that a candidate can lower MAE
while simultaneously worsening MSE, NB negative log-likelihood, and
frozen-NB Brier -- exactly what median-targeting under mean-based
downstream evaluation predicts.

## v2 recommendation (opt-in for new experiments)

**PRIMARY mean metric**: NB negative log-likelihood (frozen dispersion,
never refit per candidate), OR MSE/RMSE as a simpler, cheaper proxy when
NB-NLL is impractical to compute at a given experiment's scale. Either
is acceptable as PRIMARY going forward; NB-NLL is theoretically
preferred because it directly matches what the probability engine
assumes about the predicted value, while MSE/RMSE is the more familiar,
cheaper mean-consistent alternative.

**SECONDARY metric**: MAE, retained for interpretability (it remains
intuitive and is not being discarded) -- but never the sole or primary
gating criterion for a mean-model candidate going forward.

**PROBABILITY GATE**: unchanged in spirit, now explicitly paired with the
new primary metric rather than with MAE -- a candidate must improve or
preserve the primary mean-consistent metric AND improve or preserve
frozen-NB probability scoring (Brier/log-loss) to advance. This was
already required practice in every recent experiment; v2 just aligns the
mean-side gate with a metric that is actually consistent with the
probability-side gate, instead of leaving them measuring conceptually
different targets.

**CALIBRATION DIAGNOSTIC**: report, for every mean-model candidate,
fixed predicted-run-bucket bias AND a diagnostic OLS (`actual ~
intercept + slope*predicted`) -- informational, never used to fit a
correction. (Already established practice since MLB-RSCH-0014; v2 makes
it a required minimum, not an optional addition.)

**MINIMUM REPORTING SET** for a future mean-model experiment's own
control/candidate comparison:
1. MAE (secondary/interpretability)
2. MSE, RMSE (primary or co-primary)
3. Bias (mean residual), median residual
4. NB negative log-likelihood delta (primary or co-primary), frozen
   dispersion, never refit
5. Frozen-NB Brier deltas by market family (unchanged requirement)
6. Calibration bucket table + diagnostic OLS slope/intercept

## What does NOT change

- No canonical framework code (`lib/edgelab/*`) is modified by this
  recommendation.
- No prior experiment's `selection_passes()`, registration, or committed
  artifact is touched or reinterpreted.
- MLB-RSCH-0015's S1 and MLB-RSCH-0020's B3 remain **REJECTED** --
  this recommendation does not retroactively rescue either. A future
  experiment MAY re-test a materially similar hypothesis under v2
  methodology, but that would be a **new, separately preregistered**
  experiment, never a silent reinterpretation of RSCH-0015/0020's own
  results.
- v1 (MAE-primary) experiments are not required to be redone. This is
  forward-looking guidance, not a retroactive standard.

## Adoption

A future experiment adopts v2 by explicitly stating so in its own
registration (`notes` field, e.g. "uses EdgeLab Research Lab Methodology
v2 -- NB-NLL primary, MAE secondary") and by implementing the minimum
reporting set above. There is no code-level flag or version switch in
the framework itself -- this is a reporting-convention recommendation
for the human/agent designing the next experiment to follow, not a
software mechanism.
