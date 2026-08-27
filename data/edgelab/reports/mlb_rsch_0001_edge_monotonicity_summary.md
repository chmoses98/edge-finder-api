# MLB-RSCH-0001: Edge Validity / Edge Monotonicity

Research question: Does larger declared model edge correspond to genuinely greater predictive advantage over the Kalshi market, and does that relationship differ by market family?

Evidence level: **E1_RECONSTRUCTED_RETROSPECTIVE** | Experiment type: EXPLORATORY | Disposition: **REJECT**

Edge signal classification: **MATERIAL PROBLEM FOUND**

RESEARCH ONLY. productionBehaviorChanged: false. No production model, recommendation, fee, staking, eligibility, or risk-gate logic was changed by this experiment.

## Usable data

- Total archived opportunity rows: 161,409
- Rows with settlement: 145,232
- Rows with a causally-valid model probability: 1,124
- Usable rows (settled + causal model probability + computable edge): **653**
- Independent games: **170** | independent dates: **17** | unique tickers: 523
- Market families represented: first_inning_run, game_result, inning_result, team_total
- Rows excluded for PIT/timing reasons: {'NO_EVALUATIONS_FOR_TICKER': 58427, 'NO_CAUSAL_TIMESTAMP_ON_ANY_CANDIDATE': 100241, 'ALL_EVALUATIONS_AFTER_CHECKPOINT': 1617}

## Primary result (overall, all buckets pooled)

- Model Brier score: 0.262052 | Market benchmark Brier score: 0.24559
- Paired Brier delta (model - market, negative = model better): **0.016462**
- 90% game-clustered bootstrap CI on delta: {'low': 0.0056, 'high': 0.0275, 'method': 'GAME_CLUSTERED_BOOTSTRAP', 'metric': 'brierScoreDelta'}
- Paired log-loss delta: 0.040777

## Edge bucket table

| Bucket | Rows | Games | Dates | Mean edge | Hit rate | Model Brier | Market Brier | Paired delta | 90% CI | Interpretability |
|---|---|---|---|---|---|---|---|---|---|---|
| <0% | 306 | 142 | 17 | -0.1813 | 0.4706 | 0.273363 | 0.249916 | 0.023447 | [0.0035, 0.0438] | INTERPRETABLE |
| 0-2.5% | 16 | 11 | 6 | 0.0134 | 0.375 | 0.251786 | 0.245413 | 0.006374 | [-0.0004, 0.0129] | INSUFFICIENT |
| 2.5-5% | 39 | 30 | 11 | 0.0424 | 0.5385 | 0.250011 | 0.251841 | -0.00183 | [-0.0145, 0.0108] | INSUFFICIENT |
| 5-7.5% | 85 | 63 | 16 | 0.0616 | 0.3647 | 0.23755 | 0.220625 | 0.016925 | [0.0046, 0.028] | EXPLORATORY |
| 7.5-10% | 63 | 43 | 15 | 0.0884 | 0.5079 | 0.251111 | 0.247314 | 0.003797 | [-0.0173, 0.0218] | INSUFFICIENT |
| 10-15% | 121 | 86 | 16 | 0.1203 | 0.4463 | 0.269467 | 0.248807 | 0.02066 | [-0.0029, 0.0426] | EXPLORATORY |
| 15%+ | 23 | 17 | 9 | 0.1734 | 0.6087 | 0.220643 | 0.248161 | -0.027518 | [-0.0875, 0.0258] | INSUFFICIENT |

## Market family findings

| Family | Rows | Games | Dates | Paired delta | BH-significant (q=0.10) | Interpretability |
|---|---|---|---|---|---|---|
| first_inning_run | 179 | 144 | 16 | 0.014452 | False | INTERPRETABLE |
| game_result | 37 | 29 | 13 | 0.020184 | False | INSUFFICIENT |
| inning_result | 55 | 46 | 16 | 0.004991 | False | INSUFFICIENT |
| team_total | 382 | 162 | 17 | 0.018696 | False | INTERPRETABLE |

## Important checks

- Monotonic non-increasing delta across buckets: **False**
- Inverted/flat buckets (delta increased vs the prior bucket): ['5-7.5%', '10-15%']
- Families where model performed worse than market: ['first_inning_run', 'game_result', 'inning_result', 'team_total']
- Game-concentration warning by bucket: {'<0%': False, '0-2.5%': False, '2.5-5%': False, '5-7.5%': False, '7.5-10%': False, '10-15%': False, '15%+': False}
- qualityTier by bucket: {'2.5-5%': {'UNKNOWN_NULL': 23, 'TRUSTED_PRODUCTION': 16}, '<0%': {'UNKNOWN_NULL': 250, 'TRUSTED_PRODUCTION': 56}, '7.5-10%': {'UNKNOWN_NULL': 38, 'TRUSTED_PRODUCTION': 25}, '5-7.5%': {'UNKNOWN_NULL': 51, 'TRUSTED_PRODUCTION': 34}, '10-15%': {'UNKNOWN_NULL': 63, 'TRUSTED_PRODUCTION': 58}, '15%+': {'UNKNOWN_NULL': 6, 'TRUSTED_PRODUCTION': 17}, '0-2.5%': {'UNKNOWN_NULL': 1, 'TRUSTED_PRODUCTION': 15}}
- artifactSource breakdown (PIT pathway mix): {'recommendations': 264, 'prospective_snapshot': 389}

## Robustness: pipeline-derived-only subset (E2-eligible pathway)

- Rows: 264 | Games: 68 | Dates: 8 | Interpretability: EXPLORATORY
- Paired Brier delta: 0.024981

## Secondary evidence: fee-aware hypothetical economics (NOT the primary basis for any conclusion)

- Hypothetical ROI (overall, fee-adjusted, 653 simulated orders): -0.0989

## Limitations

- 105 distinct modelCommitSha values were observed across the corpus (continuous deployment) -- controlModelId's identityConfidence is HISTORICAL_AMBIGUOUS, not EXACT.
- Market benchmark probability is the executable YES ask/bid-fallback price at this checkpoint (matching lib.edgelab.research_dataset's own contemporaneousEdge convention), not a vig-free midpoint -- this may modestly overstate apparent model edge in wide-spread markets.
- This is a pooled retrospective analysis with no chronological train/holdout split -- a walk-forward confirmatory follow-up would be required to reach E3.
- Independent-game counts range from tiny to moderate per segment (overall 170 games / 17 dates) -- see each segment's own interpretability label.

## PIT limitations

- model_evaluation_probability_prospective_snapshot carries pitStatus=PROSPECTIVE_ONLY in the Milestone 0A manifest; ~60% of usable rows come from this pathway, capping evidenceLevel at E1 for the pooled analysis.
- season_to_date_stats/hitter_snapshot/pitcher_snapshot inputs feeding the model's own probability computation are marked UNKNOWN_REQUIRES_AUDIT in the PIT manifest and were not independently re-audited by this experiment.

## Disposition

**REJECT** -- per Milestone 0A policy, this control-only validation experiment can never be assigned SHADOW_CANDIDATE/PROMOTION_CANDIDATE regardless of result strength. This experiment does not recommend any production change; it informs the next research stage only.

