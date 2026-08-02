# EdgeLab Phase 2 Milestone 6 — Market Intelligence Report

_Generated 2026-08-02T01:46:04Z_

**RESEARCH ONLY.** This report measures how historical expressions of the
model's edge performed. It does not change, and is not consulted by, any
production recommendation, staking, or bet-selection code path. Every
`strategyExperiments` result is a labeled hypothetical simulation of the real
settled bet ledger, never a real recorded outcome or a recommendation to
change strategy. See docs/EDGELAB_MARKET_INTELLIGENCE.md.

## Expression performance profiles

| Family | n | Win rate | ROI | Avg CLV | Rec. freq | Pass freq | Best-expr freq | Dominated freq |
|---|---|---|---|---|---|---|---|---|
| UNKNOWN | 0 | n/a | n/a | n/a | 0.0% | 100.0% | n/a | n/a |
| first_inning_run | 0 | n/a | n/a | n/a | 0.0% | 0.0% | 0.0% | 0.0% |
| game_result | 7 | 28.6% | -37.5% | -0.257 | 25.0% | 0.0% | 50.0% | 37.5% |
| game_total | 0 | n/a | n/a | n/a | 0.0% | 100.0% | 0.0% | 0.0% |
| inning_result | 7 | 85.7% | 89.5% | 0.416 | 62.5% | 0.0% | 0.0% | 0.0% |
| team_total | 0 | n/a | n/a | n/a | 0.0% | 89.5% | 0.0% | 0.0% |
| winning_margin | 0 | n/a | n/a | n/a | 0.0% | 100.0% | 0.0% | 0.0% |

## Opportunity cost analysis

Sample size: **0** (INSUFFICIENT_SAMPLE) — 0 case(s), frequency n/a

_(no cases yet)_

## Pass analysis

_No hypothetical win/loss or return is computed for never-bet markets -- Recommendation/ModelEvaluation
never record which side (YES/NO) was implicitly favored, so a settlement outcome can't be honestly
attributed. Only settlement STATUS coverage (did the market resolve at all) is reported._

| Category | n | Status | Settlement status counts |
|---|---|---|---|
| DOMINATED | 3 | INSUFFICIENT_SAMPLE | {} |
| INSUFFICIENT_SUPPORT | 63 | DESCRIPTIVE_ONLY | {} |
| PASS_NO_EDGE | 176 | CALIBRATED | {} |
| RECOMMENDED_NOT_BET | 29 | DESCRIPTIVE_ONLY | {} |

## Strategy experiments (SIMULATION -- not real recorded outcomes)

**Baseline** (real, unmodified settled bets): n=14 (INSUFFICIENT_SAMPLE), winRate=57.1%, ROI=34.3%

| Experiment | n | Status | Win rate | ROI | Delta ROI vs baseline |
|---|---|---|---|---|---|
| DOMINATED_MARKETS_REPLACED_WITH_BEST_EXPRESSION | 14 | INSUFFICIENT_SAMPLE | 57.1% | 34.3% | 0.0% |
| ALWAYS_PREFER_F5 | 14 | INSUFFICIENT_SAMPLE | 57.1% | 34.3% | 0.0% |
| NEVER_FULL_GAME_ML_WITH_BULLPEN_DISADVANTAGE | 14 | INSUFFICIENT_SAMPLE | 57.1% | 34.3% | 0.0% |
| REMOVE_NEGATIVE_CLV_MARKETS | 10 | INSUFFICIENT_SAMPLE | 70.0% | 44.8% | 10.4% |

## Edge stability

| Edge bucket | n | Status | Stable | Volatile | False edge | Unknown |
|---|---|---|---|---|---|---|
| -12.0 | 2 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 2 |
| -10.0 | 8 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 8 |
| -8.0 | 9 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 9 |
| -6.0 | 8 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 8 |
| -4.0 | 9 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 9 |
| -2.0 | 2 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 2 |
| 0.0 | 15 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 15 |
| 2.0 | 13 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 13 |
| 4.0 | 4 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 4 |

## Market health scores

| Family | Health score | Sample n | Status |
|---|---|---|---|
| UNKNOWN | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| first_inning_run | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| game_result | 0.279 | 7 | INSUFFICIENT_SAMPLE |
| game_total | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| inning_result | 0.250 | 7 | INSUFFICIENT_SAMPLE |
| team_total | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| winning_margin | 0.000 | 0 | INSUFFICIENT_SAMPLE |

## Historical trend (daily / weekly / season)

- Daily trend points: 2
- Weekly trend points: 2
- Season-to-date: {'period': 'SEASON_TO_DATE', 'periodType': 'season', 'n': 14, 'winRate': 0.5714285714285714, 'actualWinRate': 0.5714285714285714, 'expectedWinRate': None, 'calibrationError': None, 'roi': 0.34315789473684205, 'totalStake': 76.0, 'totalNetProfitLoss': 26.079999999999995, 'avgClv': 0.023333333333333317, 'status': 'INSUFFICIENT_SAMPLE'}
