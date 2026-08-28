# MLB-RSCH-0021: Expected-Run Target / Loss-Function Audit

Status: **COMPLETE. MAE_PRIMARY_METRIC_INAPPROPRIATE.**

METHODOLOGY experiment. RESEARCH ONLY. No production changes. **Does NOT
change the disposition of any prior experiment** -- MLB-RSCH-0015's S1
and MLB-RSCH-0020's B3 remain **REJECTED** under their own preregistered
rules. Every number below is NEW evidence owned by MLB-RSCH-0021.

## 1. Purpose

Two separate candidates in this program (RSCH-0015's S1, RSCH-0020's B3)
lowered MAE on expected team runs while *worsening* frozen-NB probability
scoring. MAE is minimized by the conditional median; the probability
engine treats the predicted value as a conditional *mean* parameter of a
negative-binomial distribution. This experiment tests -- does not assume
-- whether that mismatch explains the pattern.

## 2. Predeclared candidate set

**Control**: S0/B0 (the identical MLB-RSCH-0009 `{offense,bullpen}`
composition, reused from both MLB-RSCH-0015 and MLB-RSCH-0020 --
verified byte-identical: both produce `homeExpectedRuns=4.6627` on the
same first row). **Candidate A**: MLB-RSCH-0015's S1, reused completely
frozen (HFA recomputed via the identical function, 0.0129, byte-exact
match to RSCH-0015's own committed artifact). **Candidate B**:
MLB-RSCH-0020's B3, reused completely frozen (K-BB shrinkage K=80, slope
-25.790521, intercept 8.255735, blend weight 0.5 -- all read from
RSCH-0020's own committed artifact and asserted equal, never refit).
Explicitly excluded: RSCH-0012's O1 or any other historical candidate --
both S1 and B3 already directly instantiate the core hypothesis; adding
more would not sharpen the question and risks the "fishing expedition"
this milestone's own preregistration forbids.

**Corpus**: 10,204 games / 20,408 team observations, dev=6,378 /
val=2,127 / holdout=1,699 -- evaluated **uniformly across all three
splits**, per this experiment's own preregistered governance: RSCH-0021
is a full-sample diagnostic of already-frozen formulas, not a
candidate-selection experiment with something to protect from
overfitting-by-selection. See module docstring's own "HOLDOUT ACCESS"
section for the full reasoning, locked before any real result was
examined.

## 3. Theoretical targets (documented, then verified empirically)

| Metric | Targets |
|---|---|
| MAE | conditional **median** |
| MSE / RMSE | conditional **mean** |
| NB negative log-likelihood / deviance | distributional fit conditional on the modeled NB distribution -- most aligned with how the probability engine actually uses the predicted value |
| Brier / log loss | event probability quality -- the ultimate downstream target |

## 4. Empirical results

| | S0 (control) | S1 | S1 delta | B0 (control) | B3 | B3 delta |
|---|---|---|---|---|---|---|
| MAE | 2.4525 | 2.4449 | **-0.0075** (CI [-0.0091,-0.0059], improved) | 2.4525 | 2.4497 | **-0.0027** (CI [-0.0042,-0.0013], improved) |
| MSE | 9.9492 | 9.9833 | **+0.0349** (CI [0.0240,0.0452], worsened) | 9.9492 | 9.9905 | **+0.0416** (CI [0.0320,0.0514], worsened) |
| RMSE | 3.1542 | 3.1596 | worsened | 3.1542 | 3.1608 | worsened |
| Bias (mean residual) | -0.0954 | -0.1811 | ~2x more negative | -0.0954 | -0.1488 | more negative |
| NB mean NLL | 2.4534 | 2.4551 | **+0.00172** (CI [0.0012,0.0023], worsened) | 2.4534 | 2.4555 | **+0.00206** (CI [0.0016,0.0026], worsened) |
| Poisson deviance | 2.2933 | 2.3009 | worsened | 2.2933 | 2.3025 | worsened |
| Frozen-NB primary Brier delta | -- | -- | **+0.000608** (worsened) | -- | -- | **+0.00054** (worsened) |

**Both candidates show the exact predicted signature: MAE improves,
while MSE, NB likelihood, Poisson deviance, AND Brier all worsen
together, consistently.** S1's bias roughly doubles versus S0 (matching
MLB-RSCH-0016's own independent finding); B3's bias also grows.

## 5. Calibration diagnostic (informational, not corrective)

Diagnostic OLS (`actual ~ intercept + slope*predicted`):

| | intercept | slope |
|---|---|---|
| S0/B0 | -1.0067 | 1.2531 |
| S1 | -1.0418 | 1.2865 (slightly more extreme than S0) |
| B3 | -0.5654 | 1.1661 (notably closer to slope=1 than B0) |

An honest nuance: S1's own calibration slope moves *further* from 1
(consistent with worse mean-calibration), but B3's calibration slope
actually moves *closer* to 1 despite its NLL/Brier also worsening -- the
mean-vs-median mechanism is not a fully clean, single-variable
explanation for B3 specifically. Reported as-is, not smoothed over.

## 6. Mean-vs-median NB gap (frozen dispersion, representative lambdas)

| Mean (λ) | Median | Gap |
|---|---|---|
| 2.5 | 2 | 0.5 |
| 3.5 | 3 | 0.5 |
| 4.5 | 4 | 0.5 |
| 5.5 | 5 | 0.5 |
| 6.5 | 6 | 0.5 |

A strikingly constant ~0.5-run gap across the entire realistic range of
team-run predictions under the frozen dispersion. S1's own bias increase
(~-0.09 additional versus S0) is real but well short of the full 0.5-run
gap -- consistent with a partial, not complete, shift toward
median-like behavior.

## 7. Team-level metric-vs-Brier correlation (n=30 teams per candidate)

| | corr(|ΔMAE|, ΔBrier) | corr(ΔMSE, ΔBrier) |
|---|---|---|
| S1 | 0.4355 | 0.1635 |
| B3 | 0.1473 | 0.1004 |

Moderate for S1, weak for B3 -- the metric-to-probability relationship
is real in direction but not strongly linear at the team level,
especially for B3. An honestly-reported open question, not resolved by
this audit.

## 8. Synthetic sanity check (NOT evidence about MLB)

Deterministic simulation (seed `20260828`, n=20,000 draws from a known
NB(mean=4.4, dispersion=0.281513)), three predictors: the true
conditional mean, a median-like shifted predictor, and a biased mean.

| Predictor | MAE | MSE | NB NLL | Event Brier |
|---|---|---|---|---|
| A: true mean (4.4) | 2.4413 | **9.8647** | **2.4420** | **0.24300** |
| B: median-like (4.0) | **2.3743** | 10.0453 | 2.4521 | 0.24595 |
| C: biased mean (4.7) | 2.4915 | 9.9393 | 2.4456 | 0.24410 |

**Exactly as theory predicts**: MAE is minimized by the median-like
predictor; MSE, NB likelihood, AND event Brier are all minimized by the
true conditional mean. `matchesTheory: true`. This is a mathematical
validation of the evaluation framework, not evidence about baseball --
it demonstrates the mechanism this experiment's empirical MLB findings
are consistent with.

## 9. Research Lab metric-convention audit (read-only)

7 experiment scripts in this program (`run_bullpen_component_talent_experiment.py`,
`run_bullpen_talent_experiment.py`, `run_early_season_offense_experiment.py`,
`run_games_1_10_confirmation_experiment.py`, `run_mean_calibration_experiment.py`,
`run_offense_talent_experiment.py`, `run_opponent_strength_experiment.py`)
register a paired MAE delta as their own `primary_metric` and gate
`selection_passes()` on it as criterion #1. **None currently gates on
MSE/RMSE or NB likelihood as the primary selection criterion** --
probability scoring is checked as a separate, later gate, never as part
of the same mean-consistent metric family MAE nominally represents. No
prior artifact was modified by this audit.

## 10. Tests

- `tests/edgelab/test_run_loss_function_audit_experiment_script.py` --
  26 tests: frozen-dispersion verification, registration idempotency,
  frozen-S1/B3-exact-reuse proofs (AST-verified calls to `rsch0015.*`/
  `rsch0020.*`, never reimplemented), no-candidate-refit proofs, MAE/MSE
  correctness (including a constructed skewed-distribution case proving
  MAE rewards the median and MSE rewards the mean), NB
  likelihood/deviance correctness, deterministic-and-theory-matching
  synthetic check, mean-vs-median-gap-uses-frozen-dispersion proof,
  no-market-fitting proof, governance proof (report explicitly states
  `priorDispositionsChanged: False`), predeclared-candidate-set proof
  (no O1/other candidates referenced operationally).
- Full `tests/edgelab/` suite: **3,043 passed**.
- Verified zero diff against every production file and every PRIOR
  experiment's own committed artifact.

## 11. Decision

**A. Is MAE conceptually misaligned as the primary metric?** Yes --
theoretically (targets the median, not the mean the probability engine
assumes) and empirically confirmed for both S1 and B3.

**B. Does empirical MLB evidence show the expected median-vs-mean
conflict?** Yes, clearly, for both candidates: MAE improves while MSE,
NB likelihood, and Brier all worsen together.

**C. Do S1/B3's MAE gains survive under RMSE/MSE?** No -- both worsen.

**D. Do they survive under NB likelihood?** No -- both worsen.

**E. Which mean metric best predicts downstream probability
performance?** NB negative log-likelihood and MSE both move in the same
direction as Brier for both candidates in this audit; team-level
correlation is moderate for S1, weak for B3 -- NB-NLL is the
theoretically best-aligned choice (it directly matches what the
probability engine assumes), with MSE as a simpler, cheaper proxy.

**F. Should future mean-model experiments change their primary
selection metric?** Yes -- see the versioned methodology recommendation
below.

## 12. Methodological classification

**MAE_PRIMARY_METRIC_INAPPROPRIATE** (both S1 and B3 independently match
the full predicted pattern: MAE improves, MSE and/or NB-NLL worsen, and
Brier worsens).

## 13. Governance confirmation

**MLB-RSCH-0015's S1 remains REJECTED. MLB-RSCH-0020's B3 remains
REJECTED.** Neither disposition changes. No prior artifact was modified.
This experiment's own findings are new, RSCH-0021-owned evidence used
only to inform a versioned methodology recommendation for FUTURE
experiments -- see `docs/EDGELAB_RESEARCH_LAB_METHODOLOGY_V2_MEAN_METRIC.md`.

## 14. Recommended next research action

Given this audit produced a clear methodological conclusion and the
program's own philosophy of applying it going forward, the highest-value
next step is not a new candidate hunt but validating the NEW metric
framework prospectively: the next mean-model experiment (e.g., an
early-season bullpen talent/component study) should be the first to use
the v2 methodology (NB-NLL or MSE/RMSE as primary, MAE secondary) end to
end, to confirm the corrected selection criterion actually changes which
candidates survive DEV/VAL gating in practice, not just in retrospective
audit.
