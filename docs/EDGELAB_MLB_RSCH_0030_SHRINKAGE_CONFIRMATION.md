# MLB-RSCH-0030 -- Hitter Signal-Shrinkage Confirmation

**CONFIRMATORY. RESEARCH ONLY. No production change. alpha never fitted to economics.**

## Verdict at two levels -- read both

This experiment records two distinct statements, and conflating them is the
main way this document can be misread.

| | |
|---|---|
| **MECHANICAL PREREGISTERED VERDICT** | `LEVEL_1_SHADOW_CANDIDATE` |
| **EXECUTIVE / MATERIALITY INTERPRETATION** | **NO ACTIONABLE HITTER SHRINKAGE SIGNAL** |

The mechanical verdict is preserved exactly as the preregistered rule produced
it. It is **not** rewritten, because rewriting a rule after seeing its outcome
is precisely what this program forbids.

The executive interpretation is the honest reading of the same numbers, and it
is not a retroactive rule change. It is a statement that **the old success rule
was insufficiently strict**: the rule tested the SIGN of the improvement and
never required the effect to be distinguishable from the null, to exceed a
materiality floor, or to leave any executable capacity after fees. On this
sample all of those fail at once -- the fitted alpha's CI spans zero, the
validation delta's CI spans zero, the improvement is ~1e-4 Brier, and ZERO
contracts clear the canonical Kalshi fee.

**Operationally: alpha = 0.059 is NOT validated, the frozen candidate must NOT
be activated, and hitter edge logic must NOT change on the strength of this
experiment.** The frozen artifact exists because the preregistered rule
required emitting one, not because the signal earned activation.

That gap between "passes the rule" and "is worth acting on" is what
Methodology V3's materiality gate exists to close for FUTURE experiments.

## What is being tested, and what is not assumed

MLB-RSCH-0029 reported a descriptive OLS coefficient of +0.2334 fitted on all rows with no
out-of-sample design. That is **a hypothesis, not a prior** -- it is not carried in here. This
experiment fits one scalar on DEVELOPMENT, freezes it, and applies it unchanged to VALIDATION.

```
p_shrunk = sigmoid( logit(kalshiMid) + alpha * ( logit(modelP) - logit(kalshiMid) ) )
  alpha = 0  -> S0, trust Kalshi          alpha = 1 -> S2, trust the raw model
```

alpha bounds `[-1.0, 2.0]` -- deliberately wide enough to express anti-signal (alpha<0) and
over-trust (alpha>1). It is **not** forced positive.

## Sample -- the binding constraint, audited before the design was chosen

5,168 rows · **261 playerGameKeys** · 36 games · 7 dates

| Date | Rows | Keys | Games |
|---|---:|---:|---:|
| 2026-08-19 | 579 | 52 | 3 |
| 2026-08-20 | 1639 | 50 | 3 |
| 2026-08-21 | 283 | 32 | 8 |
| 2026-08-22 | 1825 | 74 | 12 |
| 2026-08-23 | 260 | 17 | 1 |
| 2026-08-24 | 26 | 2 | 1 |
| 2026-08-25 | 556 | 34 | 8 |

Coverage is severely uneven, so a single split is not treated as sufficient: leave-one-date-out
is reported alongside it, and disagreement between the two is reported as disagreement.

## Fitted alpha (DEVELOPMENT only)

**alpha = 0.059034**  ·  playerGame-clustered CI [-0.3621, 0.5247]  ·  game-clustered CI [-0.4756, 0.7811]

- NLL at fitted alpha: 0.475078
- NLL at alpha=0 (pure market): 0.475131
- NLL at alpha=1 (raw model): 0.487673

The CI resamples whole player-games and **refits alpha in every resample** -- resampling rows
would treat ~20 correlated observations per player-game as independent.

## S0 / S1 / S2

| | DEV Brier | DEV log loss | VAL Brier | VAL log loss | VAL ECE |
|---|---:|---:|---:|---:|---:|
| S0 market | 0.154507 | 0.475131 | 0.160094 | 0.488277 | 0.031194 |
| **S1 shrunk** | 0.154469 | 0.475078 | 0.15988 | 0.48783 | 0.033714 |
| S2 raw model | 0.158075 | 0.487673 | 0.160174 | 0.493251 | 0.046194 |

**VALIDATION S1 - S0: Brier -0.000214 [-0.0007, 0.0003] · log loss -0.000447**

S2 - S0 on VALIDATION: Brier +0.000080, log loss +0.004974

## Leave-one-date-out (date-aware)

S1 wins both metrics on **4 of 7** held-out dates. Refit alpha range: [0.035901, 0.247142]

| Held-out date | Refit alpha | Rows | Keys | S1-S0 Brier | S1-S0 log loss | S1 wins both |
|---|---:|---:|---:|---:|---:|:-:|
| 2026-08-19 | 0.081664 | 579 | 52 | -0.00025 | -0.00045 | yes |
| 2026-08-20 | 0.083807 | 1639 | 50 | -0.00003 | -0.00018 | yes |
| 2026-08-21 | 0.047856 | 283 | 32 | -0.00050 | -0.00129 | yes |
| 2026-08-22 | 0.247142 | 1825 | 74 | +0.00060 | +0.00221 | no |
| 2026-08-23 | 0.035901 | 260 | 17 | -0.00048 | -0.00129 | yes |
| 2026-08-24 | 0.096446 | 26 | 2 | +0.00019 | +0.00026 | no |
| 2026-08-25 | 0.125781 | 556 | 34 | +0.00012 | +0.00078 | no |

## Preregistered success criteria

- `1_S1_beats_S0_on_brier_and_logloss`: **True**
- `2_direction_holds_on_majority_of_held_out_dates`: **True**
- `3_not_concentrated_in_one_family`: **True**
- `4_no_material_calibration_degradation`: **True**
- `5_sufficient_independent_sample`: **True**

**All required: True**

## Families (exploratory; validation always scored under the GLOBAL frozen alpha)

| Family | DEV rows | DEV keys | Family alpha | VAL S1-S0 Brier | FDR | Floor |
|---|---:|---:|---:|---:|:-:|:-:|
| hitter_hits | 978 | 201 | 0.165619 | -0.00047 | no | yes |
| hitter_total_bases | 1188 | 199 | 0.003768 | 9e-05 | no | yes |
| hitter_hits_runs_rbis | 1507 | 203 | 0.110686 | -0.000304 | no | yes |
| hitter_rbis | 653 | 199 | -0.044622 | -0.000175 | no | yes |

## Does shrinkage restore monotonicity?

- Raw model monotone improving: **True** · raw inversion: **False**
- Shrunk monotone improving: **True** · shrunk inversion: **False**
- Qualifying buckets: 3

| Signal bucket | Rows | Keys | S2-S0 (raw) | S1-S0 (shrunk) | Floor |
|---|---:|---:|---:|---:|:-:|
| [-1.000,+0.000) | 462 | 52 | 0.003995 | 5e-06 | yes |
| [+0.000,+0.025) | 127 | 47 | -6.3e-05 | -1.6e-05 | yes |
| [+0.025,+0.050) | 122 | 42 | -0.001878 | -0.00019 | yes |
| [+0.050,+0.075) | 57 | 32 | -0.002062 | -0.000349 | no |
| [+0.075,+0.100) | 35 | 21 | -0.011359 | -0.001121 | no |
| [+0.100,+0.150) | 33 | 15 | -0.033233 | -0.002742 | no |
| [+0.150,+1.010) | 6 | 4 | 0.011844 | -0.001193 | no |

## Probability bands (VALIDATION, frozen alpha)

| Band | Rows | Keys | S1-S0 Brier | S2-S0 Brier | Floor |
|---|---:|---:|---:|---:|:-:|
| [0.00,0.10) | 272 | 52 | 2.8e-05 | 0.002209 | yes |
| [0.10,0.25) | 253 | 53 | 6.3e-05 | 0.006191 | yes |
| [0.25,0.50) | 191 | 53 | -0.00047 | -0.004927 | yes |
| [0.50,0.75) | 110 | 53 | -0.000725 | -0.006555 | yes |
| [0.75,1.00) | 16 | 13 | -0.002088 | -0.027324 | no |

## Honest executable economics

`p_shrunk` against the **actual executable YES ask**, with canonical Kalshi fees.
Capacity thresholds are preregistered; none was chosen for its ROI.

| Segment | net EV cut | Positive gross | Opportunities | Wins | Avg ask | Fees | Net | ROI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| validation_netEV_gt_0.0 | 0.0 | 31 | 0 | 0 | None | 0.0 | 0.0 | None |
| validation_netEV_gt_0.025 | 0.025 | 31 | 0 | 0 | None | 0.0 | 0.0 | None |
| validation_netEV_gt_0.05 | 0.05 | 31 | 0 | 0 | None | 0.0 | 0.0 | None |
| validation_S0_reference_netEV_gt_0 | 0.0 | 0 | 0 | 0 | None | 0.0 | 0.0 | None |

## Materiality -- read this before the label

*Post-hoc observation, explicitly NOT a preregistered gate and NOT altering the verdict.*

- Fitted alpha CI includes zero: **True**
- Validation delta CI includes zero: **True**
- Validation Brier improvement: **-0.000214**
- DEV NLL gain over pure market: **5.3e-05**
- Leave-one-date-out majority: **4/7**
- Rows with positive gross edge before fees: **31**
- **Executable opportunities after fees: 0**
- **Actionable edge exists: False**

The preregistered criteria pass on point estimates, but the fitted alpha's confidence interval includes zero, the validation improvement's interval includes zero, the improvement is on the order of 1e-4 Brier, and ZERO contracts clear the canonical fee after shrinkage. A shrinkage factor that cannot be distinguished from 'ignore the model' and that yields no executable opportunity is not a betting lever, whatever the label says.

## Result

- Classification: **CASE_B_VALIDATED_SHRINKAGE**
- Disposition: **LEVEL_1_SHADOW_CANDIDATE** (maximum permitted: LEVEL_1_SHADOW_CANDIDATE)
- Shadow candidate justified: **True**
- Production activation authorized: False
- Forward thresholds (chosen before any forward data): 100 keys / 30 games / 7 dates
