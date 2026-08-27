# MLB-RSCH-0001: Edge Validity / Edge Monotonicity

Research question: Does larger declared model edge correspond to genuinely greater predictive advantage over the Kalshi market, and does that relationship differ by market family? (Current-model and historical-mixed-version evidence are reported and classified separately -- never conflated.)

Evidence level: **E1_RECONSTRUCTED_RETROSPECTIVE** | Experiment type: EXPLORATORY | Disposition: **RESEARCH_CANDIDATE**

RESEARCH ONLY. productionBehaviorChanged: false. No production model, recommendation, fee, staking, eligibility, or risk-gate logic was changed by this experiment.

## Headline: two DISTINCT questions, never conflated

- **Current-model edge validity** (population: TRUSTED_PRODUCTION_QUALITY_TIER_ONLY): **WEAK / UNPROVEN**
- **Historical mixed-version edge validity** (population: ALL_HISTORICAL_MODEL_VERSIONS (105 distinct modelCommitSha values)): **MATERIAL PROBLEM FOUND**
- These are DISTINCT questions and must never be conflated. A historical mixed-version finding does not, by itself, characterize the current model.
- Disposition (**RESEARCH_CANDIDATE**) is driven ONLY by the current-model classification -- never the historical one.

## Usable data & fair-market benchmark coverage

- Total archived opportunity rows: 161,409
- Rows with settlement: 145,232
- Rows with a causally-valid model probability: 1,124
- Usable rows (settled + causal model probability + computable edge): **653**
- Independent games: **170** | independent dates: **17** | unique tickers: 523
- Market families represented: first_inning_run, game_result, inning_result, team_total
- Rows excluded for PIT/timing reasons: {'NO_EVALUATIONS_FOR_TICKER': 58427, 'NO_CAUSAL_TIMESTAMP_ON_ANY_CANDIDATE': 100241, 'ALL_EVALUATIONS_AFTER_CHECKPOINT': 1617}
- Fair-market (bid/ask midpoint) benchmark: Contemporaneous bid/ask midpoint (yesBid+yesAsk)/2 at this row's own checkpoint -- the PRIMARY market benchmark for predictive Brier/log-loss scoring (hardening pass item 1). Never a different/stale observation.
- Fair-market coverage: **653/653** usable rows (0 missing bid or ask) | 170 games | 17 dates

## ALL_HISTORICAL_MODEL_VERSIONS (fair-market benchmark, primary)

_Mixes 105 distinct modelCommitSha values -- NOT a test of the current model specifically._

- Population: ALL_HISTORICAL_MODEL_VERSIONS | Market benchmark: `fairMarketProbability`
- Rows: 653 | Independent games: **170** | Independent dates: **17** | Interpretability: INTERPRETABLE
- Model Brier: 0.262052 | Market Brier: 0.245353 | Paired delta (model-market, negative=model better): **0.016699**
- 90% game-clustered bootstrap CI on delta: [0.006, 0.0276]
- Monotonic non-increasing delta across buckets: False | Inverted/flat buckets: ['5-7.5%', '10-15%']
- **Edge signal classification: MATERIAL PROBLEM FOUND**

| Bucket | Rows | Games | Dates | Mean edge | Hit rate | Model Brier | Market Brier | Paired delta | 90% CI | Interpretability |
|---|---|---|---|---|---|---|---|---|---|---|
| <0% | 306 | 142 | 17 | -0.1813 | 0.4706 | 0.273363 | 0.249579 | 0.023784 | [0.0043, 0.0439] | INTERPRETABLE |
| 0-2.5% | 16 | 11 | 6 | 0.0134 | 0.375 | 0.251786 | 0.244162 | 0.007624 | [-0.0013, 0.0166] | INSUFFICIENT |
| 2.5-5% | 39 | 30 | 11 | 0.0424 | 0.5385 | 0.250011 | 0.25214 | -0.002129 | [-0.0164, 0.012] | INSUFFICIENT |
| 5-7.5% | 85 | 63 | 16 | 0.0616 | 0.3647 | 0.23755 | 0.219475 | 0.018075 | [0.0046, 0.03] | EXPLORATORY |
| 7.5-10% | 63 | 43 | 15 | 0.0884 | 0.5079 | 0.251111 | 0.247606 | 0.003505 | [-0.0189, 0.0224] | INSUFFICIENT |
| 10-15% | 121 | 86 | 16 | 0.1203 | 0.4463 | 0.269467 | 0.248709 | 0.020758 | [-0.0037, 0.0439] | EXPLORATORY |
| 15%+ | 23 | 17 | 9 | 0.1734 | 0.6087 | 0.220643 | 0.250259 | -0.029616 | [-0.0919, 0.0257] | INSUFFICIENT |

## TRUSTED_PRODUCTION_QUALITY_TIER_ONLY (current-model proxy)

_The most defensible 'current corrected methodology' cut this corpus supports -- real-money-gated production pipeline only._

- Population: TRUSTED_PRODUCTION_QUALITY_TIER_ONLY (current corrected methodology proxy) | Market benchmark: `fairMarketProbability`
- Rows: 221 | Independent games: **60** | Independent dates: **5** | Interpretability: EXPLORATORY
- Model Brier: 0.249064 | Market Brier: 0.237746 | Paired delta (model-market, negative=model better): **0.011318**
- 90% game-clustered bootstrap CI on delta: [-0.0011, 0.0232]
- Monotonic non-increasing delta across buckets: False | Inverted/flat buckets: ['0-2.5%', '2.5-5%', '10-15%']
- **Edge signal classification: WEAK / UNPROVEN**

| Bucket | Rows | Games | Dates | Mean edge | Hit rate | Model Brier | Market Brier | Paired delta | 90% CI | Interpretability |
|---|---|---|---|---|---|---|---|---|---|---|
| <0% | 56 | 36 | 5 | -0.0681 | 0.5 | 0.249157 | 0.240802 | 0.008355 | [-0.0122, 0.0256] | INSUFFICIENT |
| 0-2.5% | 15 | 10 | 5 | 0.0139 | 0.3333 | 0.249618 | 0.240638 | 0.00898 | [-0.0001, 0.0174] | INSUFFICIENT |
| 2.5-5% | 16 | 11 | 4 | 0.0409 | 0.375 | 0.26767 | 0.253481 | 0.014189 | [-0.0054, 0.0328] | INSUFFICIENT |
| 5-7.5% | 34 | 25 | 5 | 0.0613 | 0.4118 | 0.217681 | 0.207743 | 0.009938 | [-0.0092, 0.0292] | INSUFFICIENT |
| 7.5-10% | 25 | 17 | 4 | 0.0887 | 0.56 | 0.245379 | 0.247376 | -0.001997 | [-0.0398, 0.0321] | INSUFFICIENT |
| 10-15% | 58 | 37 | 5 | 0.1253 | 0.4138 | 0.272932 | 0.241663 | 0.031269 | [-0.0065, 0.0624] | INSUFFICIENT |
| 15%+ | 17 | 11 | 4 | 0.1748 | 0.5882 | 0.217512 | 0.2428 | -0.025288 | [-0.0998, 0.0392] | INSUFFICIENT |

## CANONICAL_ERA (gameDate >= 2026-08-03)

_An existing, repository-defined boundary (lib.edgelab.canonical_era), not chosen from this experiment's results -- in this short corpus it barely differs from ALL_HISTORY._

- Population: CANONICAL_ERA (gameDate >= 2026-08-03) | Market benchmark: `fairMarketProbability`
- Rows: 606 | Independent games: **156** | Independent dates: **16** | Interpretability: INTERPRETABLE
- Model Brier: 0.259434 | Market Brier: 0.243996 | Paired delta (model-market, negative=model better): **0.015438**
- 90% game-clustered bootstrap CI on delta: [0.004, 0.0277]
- Monotonic non-increasing delta across buckets: False | Inverted/flat buckets: ['5-7.5%', '10-15%']
- **Edge signal classification: MATERIAL PROBLEM FOUND**

| Bucket | Rows | Games | Dates | Mean edge | Hit rate | Model Brier | Market Brier | Paired delta | 90% CI | Interpretability |
|---|---|---|---|---|---|---|---|---|---|---|
| <0% | 280 | 129 | 16 | -0.1791 | 0.4679 | 0.269874 | 0.248467 | 0.021407 | [-0.0013, 0.0454] | INTERPRETABLE |
| 0-2.5% | 16 | 11 | 6 | 0.0134 | 0.375 | 0.251786 | 0.244162 | 0.007624 | [-0.0013, 0.0166] | INSUFFICIENT |
| 2.5-5% | 34 | 25 | 10 | 0.0425 | 0.5 | 0.247272 | 0.24631 | 0.000962 | [-0.0152, 0.0166] | INSUFFICIENT |
| 5-7.5% | 81 | 59 | 15 | 0.0616 | 0.3827 | 0.23616 | 0.220322 | 0.015838 | [0.0028, 0.0288] | EXPLORATORY |
| 7.5-10% | 59 | 40 | 14 | 0.0884 | 0.4915 | 0.251272 | 0.244238 | 0.007034 | [-0.0157, 0.0316] | INSUFFICIENT |
| 10-15% | 114 | 80 | 15 | 0.1207 | 0.4474 | 0.268004 | 0.247176 | 0.020828 | [-0.0037, 0.0451] | EXPLORATORY |
| 15%+ | 22 | 16 | 8 | 0.1734 | 0.6364 | 0.214074 | 0.253424 | -0.03935 | [-0.1051, 0.0236] | INSUFFICIENT |

## POSITIVE_EDGE_ONLY (edge > 0, bettable direction)

_Isolates the population that matters for ranking bettable opportunities -- the <0% bucket is diagnostic, not the basis for this classification._

- Population: POSITIVE_EDGE_ONLY (edge > 0) | Market benchmark: `fairMarketProbability`
- Rows: 347 | Independent games: **156** | Independent dates: **16** | Interpretability: INTERPRETABLE
- Model Brier: 0.252078 | Market Brier: 0.241626 | Paired delta (model-market, negative=model better): **0.010452**
- 90% game-clustered bootstrap CI on delta: [0.0004, 0.0209]
- Monotonic non-increasing delta across buckets: False | Inverted/flat buckets: ['5-7.5%', '10-15%']
- **Edge signal classification: MATERIAL PROBLEM FOUND**

| Bucket | Rows | Games | Dates | Mean edge | Hit rate | Model Brier | Market Brier | Paired delta | 90% CI | Interpretability |
|---|---|---|---|---|---|---|---|---|---|---|
| <0% | 0 | 0 | 0 | None | None | None | None | None | [None, None] | INSUFFICIENT |
| 0-2.5% | 16 | 11 | 6 | 0.0134 | 0.375 | 0.251786 | 0.244162 | 0.007624 | [-0.0013, 0.0166] | INSUFFICIENT |
| 2.5-5% | 39 | 30 | 11 | 0.0424 | 0.5385 | 0.250011 | 0.25214 | -0.002129 | [-0.0164, 0.012] | INSUFFICIENT |
| 5-7.5% | 85 | 63 | 16 | 0.0616 | 0.3647 | 0.23755 | 0.219475 | 0.018075 | [0.0046, 0.03] | EXPLORATORY |
| 7.5-10% | 63 | 43 | 15 | 0.0884 | 0.5079 | 0.251111 | 0.247606 | 0.003505 | [-0.0189, 0.0224] | INSUFFICIENT |
| 10-15% | 121 | 86 | 16 | 0.1203 | 0.4463 | 0.269467 | 0.248709 | 0.020758 | [-0.0037, 0.0439] | EXPLORATORY |
| 15%+ | 23 | 17 | 9 | 0.1734 | 0.6087 | 0.220643 | 0.250259 | -0.029616 | [-0.0919, 0.0257] | INSUFFICIENT |

## Positive-edge ordered trend statistic

- Spearman rank correlation (declared edge vs. per-row model advantage): **-0.02**
- 90% game-clustered bootstrap CI: {'low': -0.161, 'high': 0.1219}
- n=347 rows, 156 independent games
- Positive == larger declared edge is associated with greater model advantage over the market (the hypothesized monotonic relationship). Single preregistered-style method (Spearman rank correlation, game-clustered bootstrap 90% CI) -- no coefficient hunting, no other trend method tried.

## Secondary sensitivity: executable-price benchmark (ALL_HISTORY population)

- Paired Brier delta (executable price benchmark): 0.016462 | 90% CI: [0.0056, 0.0275]
- Fair-market and executable-price benchmarks are numerically close in this corpus (100% bid/ask coverage) -- the choice of benchmark does not qualitatively change the ALL_HISTORY finding.

## Market family findings (fair-market benchmark, ALL_HISTORY population)

| Family | Rows | Games | Dates | Paired delta | BH-significant (q=0.10) | Interpretability |
|---|---|---|---|---|---|---|
| first_inning_run | 179 | 144 | 16 | 0.014552 | False | INTERPRETABLE |
| game_result | 37 | 29 | 13 | 0.021408 | False | INSUFFICIENT |
| inning_result | 55 | 46 | 16 | 0.005185 | False | INSUFFICIENT |
| team_total | 382 | 162 | 17 | 0.018907 | False | INTERPRETABLE |

## Important checks (ALL_HISTORY population)

- Monotonic non-increasing delta across buckets: **False**
- Inverted/flat buckets (delta increased vs the prior bucket): ['5-7.5%', '10-15%']
- Families where model performed worse than market: ['first_inning_run', 'game_result', 'inning_result', 'team_total']
- Game-concentration warning by bucket: {'<0%': False, '0-2.5%': False, '2.5-5%': False, '5-7.5%': False, '7.5-10%': False, '10-15%': False, '15%+': False}
- qualityTier by bucket: {'2.5-5%': {'UNKNOWN_NULL': 23, 'TRUSTED_PRODUCTION': 16}, '<0%': {'UNKNOWN_NULL': 250, 'TRUSTED_PRODUCTION': 56}, '7.5-10%': {'UNKNOWN_NULL': 38, 'TRUSTED_PRODUCTION': 25}, '5-7.5%': {'UNKNOWN_NULL': 51, 'TRUSTED_PRODUCTION': 34}, '10-15%': {'UNKNOWN_NULL': 63, 'TRUSTED_PRODUCTION': 58}, '15%+': {'UNKNOWN_NULL': 6, 'TRUSTED_PRODUCTION': 17}, '0-2.5%': {'UNKNOWN_NULL': 1, 'TRUSTED_PRODUCTION': 15}}
- artifactSource breakdown (PIT pathway mix): {'recommendations': 264, 'prospective_snapshot': 389}

## Robustness: pipeline-timing-compatible subset

_Causally ordered (pipelineRunId <= checkpoint capturedAt), but upstream model inputs (season-to-date stats, hitter/pitcher snapshots) remain UNKNOWN_REQUIRES_AUDIT -- NOT called 'E2-eligible'._

- Rows: 264 | Games: 68 | Dates: 8 | Interpretability: EXPLORATORY
- Paired Brier delta: 0.025241

## Secondary evidence: fee-aware hypothetical economics (NOT the primary basis for any conclusion)

- Hypothetical ROI (ALL_HISTORY, fee-adjusted, executable price, 653 simulated orders): -0.0989

## Limitations

- 105 distinct modelCommitSha values were observed across the corpus (continuous deployment) -- controlModelId's identityConfidence is HISTORICAL_AMBIGUOUS, not EXACT. This is exactly why ALL_HISTORICAL_MODEL_VERSIONS and TRUSTED_PRODUCTION_QUALITY_TIER_ONLY are reported as distinct, separately-classified populations -- see the headline.
- CANONICAL_ERA_START_DATE (2026-08-03) is an existing, repository-defined boundary (lib.edgelab.canonical_era), never chosen from this experiment's own results -- but in this corpus's short 26-day history it excludes only 1-2 early dates and is NOT a strong current-model isolation by itself; TRUSTED_PRODUCTION qualityTier is the more meaningful 'current methodology' proxy here.
- TRUSTED_PRODUCTION_QUALITY_TIER_ONLY has only 5 independent dates (60 games) -- genuinely small; the current-model classification is conservative (WEAK/UNPROVEN unless the CI confidently excludes zero) specifically because of this.
- Fair-market (bid/ask midpoint) and executable-price benchmarks were both computed; in this corpus they are numerically close (100% of usable rows have both yesBid and yesAsk), so the choice of benchmark does not qualitatively change the historical-mixed-version finding -- see executablePriceSensitivity.
- This is a pooled retrospective analysis with no chronological train/holdout split -- a walk-forward confirmatory follow-up would be required to reach E3.

## PIT limitations

- model_evaluation_probability_prospective_snapshot carries pitStatus=PROSPECTIVE_ONLY in the Milestone 0A manifest; ~60% of usable rows come from this pathway, capping evidenceLevel at E1 for the pooled analysis.
- The PIPELINE_TIMING_COMPATIBLE_SUBSET is causally ordered (pipelineRunId <= checkpoint capturedAt) but its upstream model INPUTS (season-to-date stats, hitter/pitcher snapshots) are marked UNKNOWN_REQUIRES_AUDIT in the PIT manifest and were not independently re-audited here -- it is precise to call this subset 'pipeline-timing-compatible', not 'E2-eligible' or fully PIT-proven.

## Disposition

**RESEARCH_CANDIDATE** -- driven by the current-model (TRUSTED_PRODUCTION_QUALITY_TIER_ONLY) classification only. Per Milestone 0A policy, this control-only validation experiment can never be assigned SHADOW_CANDIDATE/PROMOTION_CANDIDATE regardless of result strength. This experiment does not recommend any production change; it informs the next research stage only.

