# MLB-RSCH-0033 -- Team-Run Mean Root-Cause Audit

**RESEARCH ONLY. No production change. Parameters fitted: 0.**

## This corrects MLB-RSCH-0032, and the correction is the headline

MLB-RSCH-0032 did not have production's archived projection. It RECOVERED a team-run mean by
inverting production's archived probability through a Poisson form, reported a calibration
slope of 0.3065 with the projection losing to a constant, and concluded
`CASE_B_TEAM_RUN_MEAN_UNINFORMATIVE`.

Production's real projection **is** archived, per date, in `data/pipeline/<date>/projections.json`.
Against it, that recovery does not round-trip:

| Inversion convention | Team-games | mean(inverted − archived) | RMSE |
|---|---:|---:|---:|
| invert P(X >= T+1) -- RSCH-0032's assumption | 568 | 0.3818 | 0.6383 |
| invert P(X >= T) | 568 | -0.5484 | 0.702 |

Neither convention reproduces production's archived mean. The archived projection's own standard deviation is ~0.60, so a recovery RMSE of that order is as large as the entire signal. MLB-RSCH-0032's team-run-mean section rests on an invalid reconstruction and is superseded here; its merged artifact is NOT rewritten.

## Control validation -- can we reproduce production?

Production's own `compute_projections` re-run over archived `normalized_slate.json`
inputs, compared against archived `projections.json`:

- Team-games checked: **676**
- Reproduced within 0.001: **636** (**0.940828**)
- Max abs difference: 0.717 · mean abs: 0.007985
- **Control valid: True**

A component study whose control cannot reproduce production would be worthless, so this
is checked before any ablation is believed.

## The projection, measured properly

| | |
|---|---:|
| Team-games | 542 |
| Independent games / dates | 284 / 23 |
| Mean projected / actual | 4.0917 / 4.4207 |
| **Bias** | **-0.3289** |
| SD projected / actual | 0.6036 / 3.1988 (ratio 0.1887) |
| **MSE** vs constant baseline | **9.9556** vs 10.2326 |
| **Beats a constant** | **True** |
| **Calibration slope** | **1.0287** |
| Pearson r / r² | 0.1941 / 0.0377 |

MAE is reported for interpretability only and qualifies nothing (Methodology V2): 2.361.

## Do teams projected to score more actually score more?

Spearman **0.2041** · monotone across quintiles: **False** ·
top−bottom actual gap **1.7147** runs (projected gap 1.6874)

| Quintile | n | Mean projected | Mean actual |
|---|---:|---:|---:|
| 1 | 108 | 3.2839 | 3.6944 |
| 2 | 108 | 3.7132 | 4.2222 |
| 3 | 108 | 4.0672 | 4.1296 |
| 4 | 108 | 4.4068 | 4.6296 |
| 5 | 110 | 4.9713 | 5.4091 |

## Component ablations

Each neutralises ONE component to production's own league-average fallback and re-runs
production's own function. **Negative delta means removing the component HELPS.**

| Component | MSE | Δ vs control | SD ratio | Slope | Removing it helps |
|---|---:|---:|---:|---:|:-:|
| OFFENSE_BASELINE | 10.0058 | +0.0502 | 0.9185 | 1.0172 | no |
| OPPOSING_STARTER | 10.1771 | +0.2215 | 0.4934 | 1.4501 | no |
| OPPOSING_BULLPEN | 10.173 | +0.2174 | 0.8557 | 1.0459 | no |
| PARK | 9.967 | +0.0114 | 0.9935 | 1.0204 | no |
| STARTER_WORKLOAD_SPLIT | 9.9834 | +0.0278 | 1.0686 | 0.9491 | no |
| PLATOON_LINEUP | 9.9556 | +0.0000 | 1.0 | 1.0287 | no |

## Root cause

**CASE_E_DISTRIBUTION_CONVERSION_PRIMARY_PROBLEM**

the mean is correctly scaled and beats a constant but explains little variance, so the loss is downstream of the mean rather than in it.

- Components whose removal improves MSE: `[]`
- Strongest harmful component: **None**
- Components ranked by variance contribution: `[('OPPOSING_STARTER', 0.4934), ('OPPOSING_BULLPEN', 0.8557), ('OFFENSE_BASELINE', 0.9185), ('PARK', 0.9935), ('PLATOON_LINEUP', 1.0), ('STARTER_WORKLOAD_SPLIT', 1.0686)]`

Production action authorized: False
