# Results: sports-only 2026 postseason evaluation of the pitcher workload challenger

**Verdict.** Both challenger arms are **REJECTED** under the preregistered strict rule: `challenger`, the frozen
`mlb_pitcher_workload_joint_v1`, and `challenger_regime`, the same model with one sports-only postseason term.
**Postseason stays BLOCKED** for the challenger, and nothing is promoted. The regular-season
`ACCEPTED_CHALLENGER` verdict of #283 (research only) is unaffected.

- **Preregistration:** `docs/research/PITCHER_POSTSEASON_PREREG.md`, commit `b42beb1`. It was pushed before
  scoring.
- **Coverage:** `docs/research/PITCHER_POSTSEASON_COVERAGE.md`, commit `2b1d3c5`. Coverage is complete.
- **Outputs:** `data/research/pitcher_postseason/eval_2026/`. They are reproduced with:

  ```
  python3 scripts/research/mlb_pitcher_postseason_eval.py fit-regime
  python3 scripts/research/mlb_pitcher_postseason_eval.py evaluate --outdir data/research/pitcher_postseason/eval_2026 --reference-arm --prereg b42beb1fa
  ```

## Population

**Who.** Every final 2026 postseason game through ALDS Game 5 (2026-10-10): 25 games, 50 actual starters. The
population is sports-defined and not market-selected.

**Labels.** Sports-source outs, BF and K from the postseason box score cache.

**Exclusions** (4 starts):

| Reason | Starts |
|---|---:|
| Challenger has fewer than 3 prior starts | 3 |
| Champion's history contains a game completed after `as_of` | 1 |

**Scored:** **46 starts in 25 games**, one row per starter-game-stat, with every arm on identical rows.

## Regime term (fit on 2022–2025 postseasons only)

- **Rows:** 338 starts (79, 82, 85 and 92 per season).
- **Objective:** mean outs log loss.
- **Fitted value:** the grid minimum is δ = **−0.2** outs on the latent location. Outs log loss falls from
  2.9864 to 2.9835.
- **Mean residual:** the frozen challenger's mean outs residual on those postseasons is −0.83. The log-loss
  optimum is much smaller than the mean residual, because the kernel distribution is already wide.
- **File:** `data/research/pitcher_postseason/regime_params.json`.

## Results (46 starts / 25 games; paired bootstrap over whole games, 1,000 reps, seed 20261010)

| Stat | Arm | MAE | RMSE | Bias | p10–p90 coverage | Threshold Brier | Log loss |
|---|---|---:|---:|---:|---:|---:|---:|
| Outs | baseline | 3.590 | 4.733 | +2.642 | 0.826 | 0.2243 | 2.991 |
| Outs | champion (sports history) | **3.580** | 4.644 | +1.769 | 0.826 | **0.2179** | **2.960** |
| Outs | challenger | 3.829 | 4.860 | +1.840 | 0.804 | 0.2472 | 3.064 |
| Outs | challenger_regime | 3.756 | 4.799 | +1.678 | 0.804 | 0.2391 | 3.051 |
| BF | baseline | 4.259 | 5.769 | +3.329 | 0.761 | 0.2140 | 3.352 |
| BF | champion | **4.056** | 5.388 | +2.329 | 0.891 | **0.2035** | **3.083** |
| BF | challenger | 4.778 | 6.248 | +3.464 | 0.652 | 0.2913 | 3.296 |
| BF | challenger_regime | 4.707 | 6.162 | +3.324 | 0.652 | 0.2830 | 3.270 |
| K | baseline | 1.948 | 2.518 | +0.638 | 0.804 | 0.2490 | 2.344 |
| K | champion | **1.898** | 2.383 | +0.298 | 0.848 | **0.2396** | **2.268** |
| K | challenger | 2.020 | 2.499 | +0.494 | 0.804 | 0.2608 | 2.316 |
| K | challenger_regime | 2.013 | 2.491 | +0.458 | 0.804 | 0.2595 | 2.310 |

**Paired differences with 95% game-clustered CIs.** An asterisk (\*) marks a CI that excludes 0.

| Stat | Comparison | ΔMAE | ΔBrier | Δlog loss |
|---|---|---|---|---|
| Outs | challenger − champion | **+0.249 [+0.050, +0.471]\*** | +0.029 [−0.001, +0.059] | +0.104 [−0.049, +0.292] |
| Outs | challenger − baseline | +0.239 [−0.128, +0.585] | +0.023 [−0.019, +0.062] | +0.072 [−0.149, +0.319] |
| Outs | challenger_regime − champion | +0.177 [−0.015, +0.384] | +0.021 [−0.006, +0.048] | +0.091 [−0.054, +0.268] |
| Outs | challenger_regime − challenger | **−0.073 [−0.117, −0.026]\*** | **−0.008 [−0.014, −0.003]\*** | −0.013 [−0.041, +0.015] |
| BF | challenger − champion | **+0.722 [+0.268, +1.231]\*** | **+0.088 [+0.042, +0.139]\*** | **+0.213 [+0.003, +0.432]\*** |
| BF | challenger − baseline | **+0.519 [+0.034, +1.043]\*** | **+0.077 [+0.019, +0.136]\*** | −0.056 [−0.381, +0.243] |
| BF | challenger_regime − champion | **+0.651 [+0.215, +1.137]\*** | **+0.079 [+0.038, +0.126]\*** | +0.187 [−0.019, +0.399] |
| K | challenger − champion | **+0.123 [+0.018, +0.254]\*** | +0.021 [−0.000, +0.049] | +0.047 [−0.031, +0.154] |
| K | challenger_regime − champion | **+0.116 [+0.014, +0.238]\*** | **+0.020 [+0.000, +0.045]\*** | +0.042 [−0.031, +0.140] |
| K | challenger − baseline | +0.073 [−0.135, +0.273] | +0.012 [−0.024, +0.049] | −0.029 [−0.165, +0.090] |

**CRPS** (the vendored `pure_gate compare`, quantile approximation), challenger − champion:

| Stat | CRPS (champion → challenger) | 95% CI |
|---|---|---|
| Outs | 2.291 → 2.441 | [+0.013, +0.303] |
| BF | 2.629 → 3.228 | [+0.168, +1.071] |
| K | 1.128 → 1.225 | [+0.003, +0.197] |

All three CIs are above 0. For `challenger_regime`, the outs CI is [−0.037, +0.256].

**Preregistered subgroup** (outs ΔMAE, challenger − champion):

| Game type | Starts | Games | ΔMAE [95% CI] |
|---|---:|---:|---|
| Wild Card (F) | 16 | 9 | +0.543 [+0.184, +0.926] |
| Division Series (D) | 30 | 16 | +0.092 [−0.121, +0.299] |

**Reference only, not PURE.** The champion's deployed path, which reads settlement evidence as postseason
history, scored outs MAE 3.636, BF MAE 3.925 and K MAE 1.946 (n = 43 with a projection). It is not a gate
comparator.

## Decision (preregistered rule, section 5)

| Rule | challenger | challenger_regime |
|---|---|---|
| 1. Outs/K improvement vs champion (CI < 0) | fail | fail |
| 2. No significant worsening vs champion or baseline | **fail**: outs MAE; BF MAE, Brier and log loss; K MAE vs champion; BF MAE and Brier vs baseline | **fail**: BF MAE and Brier; K MAE and Brier vs champion; BF Brier vs baseline |
| 3. Outs/K coverage in [0.75, 0.92] | pass (0.804 / 0.804) | pass |
| 4. Beats baseline MAE on outs and K | **fail** (3.829 vs 3.590) | **fail** |
| 5. n ≥ 40 | pass (46) | pass |
| **Verdict** | **REJECTED, BLOCKED** | **REJECTED, BLOCKED** |

## Interpretation

- **The challenger over-projects postseason workload.** BF bias is +3.46 and outs bias +1.84. The frozen
  challenger's history is regular-season only, so postseason rest and short hooks are invisible to it. Its
  BF-given-outs non-out curve also inflates BF on short postseason outings: BF coverage is 0.65.
- **The champion does better here.** It uses the sports-source postseason starts as history, plus its frozen
  −1.21 outs postseason shift. That shift was fitted on settlement-evidence observations, an indirect market
  dependency of the *champion*. Even so, the champion's outs bias is +1.77; every arm over-projects postseason
  outs.
- **The regime term helps, but not enough.** The one sports-only term (δ = −0.2) significantly improves the
  challenger on outs MAE and Brier, and on BF. It does not close the gap to the champion.
- **The regular-season advantage does not transfer.** The challenger's regular-season advantage (#283) does not
  carry over to the postseason. Collecting the 2026 LCS and World Series prospectively (PR "W2 MLB Phase 2"
  shadow) is the next evidence step.

## Runtime non-leakage (`pure_gate rerun`): **PASS_RUNTIME_NONLEAKAGE**

**Command.** `tools/pure_gate/pure_gate.py rerun` with `scripts/research/mlb_pitcher_postseason_rerun_manifest.json`.
The report is `data/research/pitcher_postseason/eval_2026/pure_gate_rerun_report.json`. The rerun works in place on
`data/edgelab/settlements`, using the format-preserving randomiser from #283.

**Model** (`rerun --arm pure_arms`): `challenger`, `challenger_regime`, `champion` with sports postseason history,
and `baseline`, over the 2026 postseason population.

- 564 rows, sha256 `fde1c6f6…e3e8`.
- Bit-identical in all four runs: normal, normal repeat, market files deleted, and market files randomised.

**Negative control** (`rerun --arm champion_deployed_path`), the settlement-reading production path:

| Run | Output sha256 |
|---|---|
| normal | `35f7bb25…802c` |
| normal repeat | `35f7bb25…802c` (deterministic) |
| deleted | `b0285b3e…74d1` (**changed**) |
| randomised | `9127440f…c23` (**changed**) |

**Checks.** Every check passed:

- model deterministic;
- model identical under every mutation;
- control deterministic;
- control responded to every mutation;
- randomisation effective;
- market inputs restored byte-identical;
- sports inputs unchanged.

**Limits.** The rerun cannot see market information that is baked into frozen parameters. The champion's
`postseason_shift_outs` (−1.209) was fitted on settlement-evidence values. That is an *indirect* market dependency
of the champion comparator, which is documented and not removed here. The challenger and regime parameters were
fitted on sports data only: 2022–2024 regular seasons and 2022–2025 postseasons.
