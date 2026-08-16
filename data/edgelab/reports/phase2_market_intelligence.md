# EdgeLab Phase 2 Milestone 6 — Market Intelligence Report

_Generated 2026-08-16T18:43:30Z_

**RESEARCH ONLY.** This report measures how historical expressions of the
model's edge performed. It does not change, and is not consulted by, any
production recommendation, staking, or bet-selection code path. Every
`strategyExperiments` result is a labeled hypothetical simulation of the real
settled bet ledger, never a real recorded outcome or a recommendation to
change strategy. See docs/EDGELAB_MARKET_INTELLIGENCE.md.

## Expression performance profiles

| Family | n | Win rate | ROI | Avg CLV | Rec. freq | Pass freq | Best-expr freq | Dominated freq |
|---|---|---|---|---|---|---|---|---|
| UNKNOWN | 0 | n/a | n/a | n/a | 0.0% | 99.8% | n/a | n/a |
| first_inning_run | 10 | 40.0% | -10.7% | n/a | 1.8% | 17.3% | 0.0% | 0.0% |
| game_result | 21 | 38.1% | -14.7% | -0.089 | 2.4% | 14.9% | 23.1% | 4.5% |
| game_total | 10 | 70.0% | 28.5% | n/a | 0.5% | 5.6% | 0.0% | 0.0% |
| hitter_hits | 2 | 50.0% | -0.2% | n/a | 0.0% | 0.0% | n/a | n/a |
| hitter_hits_runs_rbis | 0 | n/a | n/a | n/a | 0.0% | 0.0% | n/a | n/a |
| hitter_rbis | 0 | n/a | n/a | n/a | 0.0% | 0.0% | n/a | n/a |
| hitter_stolen_bases | 0 | n/a | n/a | n/a | 0.0% | 0.0% | n/a | n/a |
| hitter_total_bases | 0 | n/a | n/a | n/a | 0.0% | 0.0% | n/a | n/a |
| inning_result | 48 | 50.0% | -4.4% | -2.658 | 2.0% | 3.9% | 6.6% | 0.0% |
| inning_total | 4 | 50.0% | -30.2% | n/a | 0.0% | 0.0% | n/a | n/a |
| pitcher_outs | 7 | 28.6% | -28.1% | n/a | 1.8% | 0.0% | n/a | n/a |
| pitcher_strikeouts | 20 | 45.0% | -18.3% | -37.000 | 0.7% | 0.0% | n/a | n/a |
| team_total | 10 | 30.0% | -49.6% | -15.000 | 0.2% | 8.8% | 2.9% | 0.0% |
| winning_margin | 2 | 50.0% | -44.7% | n/a | 0.0% | 12.3% | 0.0% | 0.0% |

## Opportunity cost analysis

Sample size: **7** (INSUFFICIENT_SAMPLE) — 2 case(s), frequency 28.6%

| Bet market | Better expression | Lost edge | Lost CLV | Lost ROI | Dominated |
|---|---|---|---|---|---|
| KXMLBF5-26AUG091410CLECWS-CWS | KXMLBGAME-26AUG091410CLECWS-CWS | -1.430 | n/a | n/a | False |
| KXMLBF5-26AUG041840ATHCIN-ATH | KXMLBGAME-26AUG041840ATHCIN-ATH | -0.063 | n/a | n/a | False |

## Pass analysis

_No hypothetical win/loss or return is computed for never-bet markets -- Recommendation/ModelEvaluation
never record which side (YES/NO) was implicitly favored, so a settlement outcome can't be honestly
attributed. Only settlement STATUS coverage (did the market resolve at all) is reported._

| Category | n | Status | Settlement status counts |
|---|---|---|---|
| DOMINATED | 6 | INSUFFICIENT_SAMPLE | {'SETTLED': 3} |
| INSUFFICIENT_SUPPORT | 293 | CALIBRATED | {} |
| PASS_NO_EDGE | 1067 | CALIBRATED | {'SETTLED': 472, 'SETTLEMENT_UNRESOLVED': 4} |
| RECOMMENDED_NOT_BET | 202 | CALIBRATED | {'SETTLED': 172, 'SETTLEMENT_UNRESOLVED': 1} |

## Strategy experiments (SIMULATION -- not real recorded outcomes)

**Baseline** (real, unmodified settled bets): n=134 (CALIBRATED), winRate=45.5%, ROI=-12.4%

| Experiment | n | Status | Win rate | ROI | Delta ROI vs baseline |
|---|---|---|---|---|---|
| DOMINATED_MARKETS_REPLACED_WITH_BEST_EXPRESSION | 134 | CALIBRATED | 45.5% | -12.4% | 0.0% |
| ALWAYS_PREFER_F5 | 134 | CALIBRATED | 45.5% | -12.4% | 0.0% |
| NEVER_FULL_GAME_ML_WITH_BULLPEN_DISADVANTAGE | 132 | CALIBRATED | 44.7% | -14.2% | -1.8% |
| REMOVE_NEGATIVE_CLV_MARKETS | 127 | CALIBRATED | 46.5% | -11.3% | 1.1% |

## Edge stability

| Edge bucket | n | Status | Stable | Volatile | False edge | Unknown |
|---|---|---|---|---|---|---|
| -20.0 | 2 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 2 |
| -16.0 | 2 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 2 |
| -14.0 | 2 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 2 |
| -12.0 | 8 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 8 |
| -10.0 | 28 | DESCRIPTIVE_ONLY | 0 | 0 | 0 | 28 |
| -8.0 | 72 | DESCRIPTIVE_ONLY | 2 | 0 | 0 | 70 |
| -6.0 | 108 | CALIBRATED | 7 | 0 | 0 | 101 |
| -4.0 | 119 | CALIBRATED | 20 | 0 | 0 | 99 |
| -2.0 | 83 | DESCRIPTIVE_ONLY | 5 | 0 | 0 | 78 |
| 0.0 | 131 | CALIBRATED | 8 | 0 | 4 | 119 |
| 2.0 | 120 | CALIBRATED | 15 | 0 | 5 | 100 |
| 4.0 | 16 | INSUFFICIENT_SAMPLE | 2 | 0 | 0 | 14 |
| 6.0 | 4 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 4 |
| 8.0 | 1 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 1 |
| 10.0 | 1 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 1 |
| 12.0 | 2 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 2 |

## Market health scores

| Family | Health score | Sample n | Status |
|---|---|---|---|
| UNKNOWN | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| first_inning_run | 0.484 | 10 | INSUFFICIENT_SAMPLE |
| game_result | 0.545 | 21 | DESCRIPTIVE_ONLY |
| game_total | 0.071 | 10 | INSUFFICIENT_SAMPLE |
| hitter_hits | 0.020 | 2 | INSUFFICIENT_SAMPLE |
| hitter_hits_runs_rbis | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| hitter_rbis | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| hitter_stolen_bases | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| hitter_total_bases | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| inning_result | 0.619 | 48 | DESCRIPTIVE_ONLY |
| inning_total | 0.040 | 4 | INSUFFICIENT_SAMPLE |
| pitcher_outs | 0.070 | 7 | INSUFFICIENT_SAMPLE |
| pitcher_strikeouts | 0.111 | 20 | DESCRIPTIVE_ONLY |
| team_total | 0.448 | 10 | INSUFFICIENT_SAMPLE |
| winning_margin | 0.014 | 2 | INSUFFICIENT_SAMPLE |

## Historical trend (daily / weekly / season)

- Daily trend points: 3
- Weekly trend points: 3
- Season-to-date: {'period': 'SEASON_TO_DATE', 'periodType': 'season', 'n': 134, 'winRate': 0.4552238805970149, 'actualWinRate': 0.4552238805970149, 'expectedWinRate': 0.5327550000000001, 'calibrationError': -0.0775, 'roi': -0.12385428943233155, 'totalStake': 1629.12, 'totalNetProfitLoss': -201.77349999999996, 'avgClv': -3.8359999999999994, 'status': 'CALIBRATED'}
