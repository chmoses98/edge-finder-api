# EdgeLab Phase 2 Milestone 6 — Market Intelligence Report

_Generated 2026-08-15T15:48:10Z_

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
| first_inning_run | 10 | 40.0% | -10.7% | n/a | 2.3% | 21.6% | 0.0% | 0.0% |
| game_result | 19 | 36.8% | -18.0% | -0.089 | 2.8% | 17.6% | 27.8% | 5.6% |
| game_total | 10 | 70.0% | 28.5% | n/a | 0.5% | 6.2% | 0.0% | 0.0% |
| hitter_hits | 2 | 50.0% | -0.2% | n/a | 0.0% | 0.0% | n/a | n/a |
| hitter_hits_runs_rbis | 0 | n/a | n/a | n/a | 0.0% | 0.0% | n/a | n/a |
| hitter_rbis | 0 | n/a | n/a | n/a | 0.0% | 0.0% | n/a | n/a |
| hitter_stolen_bases | 0 | n/a | n/a | n/a | 0.0% | 0.0% | n/a | n/a |
| hitter_total_bases | 0 | n/a | n/a | n/a | 0.0% | 0.0% | n/a | n/a |
| inning_result | 49 | 42.9% | -23.7% | -1.432 | 2.3% | 4.3% | 5.5% | 0.0% |
| inning_total | 1 | 100.0% | 75.2% | 14.000 | 0.0% | 0.0% | n/a | n/a |
| pitcher_outs | 8 | 25.0% | -30.5% | -0.190 | 2.0% | 0.0% | n/a | n/a |
| pitcher_strikeouts | 19 | 47.4% | -3.5% | -12.333 | 0.8% | 0.0% | n/a | n/a |
| team_total | 9 | 33.3% | -44.3% | -15.000 | 0.2% | 9.8% | 2.8% | 0.0% |
| winning_margin | 2 | 50.0% | -44.7% | n/a | 0.1% | 13.6% | 0.0% | 0.0% |

## Opportunity cost analysis

Sample size: **7** (INSUFFICIENT_SAMPLE) — 2 case(s), frequency 28.6%

| Bet market | Better expression | Lost edge | Lost CLV | Lost ROI | Dominated |
|---|---|---|---|---|---|
| KXMLBF5-26AUG041840ATHCIN-ATH | KXMLBGAME-26AUG041840ATHCIN-ATH | -0.063 | n/a | n/a | False |
| KXMLBF5-26AUG091410CLECWS-CWS | KXMLBGAME-26AUG091410CLECWS-CWS | -1.430 | n/a | n/a | False |

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

**Baseline** (real, unmodified settled bets): n=129 (CALIBRATED), winRate=43.4%, ROI=-16.4%

| Experiment | n | Status | Win rate | ROI | Delta ROI vs baseline |
|---|---|---|---|---|---|
| DOMINATED_MARKETS_REPLACED_WITH_BEST_EXPRESSION | 129 | CALIBRATED | 43.4% | -16.4% | 0.0% |
| ALWAYS_PREFER_F5 | 129 | CALIBRATED | 43.4% | -16.4% | 0.0% |
| NEVER_FULL_GAME_ML_WITH_BULLPEN_DISADVANTAGE | 127 | CALIBRATED | 42.5% | -18.4% | -2.0% |
| REMOVE_NEGATIVE_CLV_MARKETS | 120 | CALIBRATED | 44.2% | -14.4% | 2.0% |

## Edge stability

| Edge bucket | n | Status | Stable | Volatile | False edge | Unknown |
|---|---|---|---|---|---|---|
| -20.0 | 2 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 2 |
| -16.0 | 2 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 2 |
| -14.0 | 2 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 2 |
| -12.0 | 8 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 8 |
| -10.0 | 26 | DESCRIPTIVE_ONLY | 0 | 0 | 0 | 26 |
| -8.0 | 66 | DESCRIPTIVE_ONLY | 0 | 0 | 0 | 66 |
| -6.0 | 92 | DESCRIPTIVE_ONLY | 0 | 0 | 0 | 92 |
| -4.0 | 91 | DESCRIPTIVE_ONLY | 3 | 0 | 0 | 88 |
| -2.0 | 69 | DESCRIPTIVE_ONLY | 0 | 0 | 0 | 69 |
| 0.0 | 117 | CALIBRATED | 3 | 0 | 4 | 110 |
| 2.0 | 101 | CALIBRATED | 5 | 0 | 5 | 91 |
| 4.0 | 11 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 11 |
| 6.0 | 4 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 4 |
| 8.0 | 1 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 1 |
| 10.0 | 1 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 1 |
| 12.0 | 2 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 2 |

## Market health scores

| Family | Health score | Sample n | Status |
|---|---|---|---|
| UNKNOWN | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| first_inning_run | 0.378 | 10 | INSUFFICIENT_SAMPLE |
| game_result | 0.497 | 19 | INSUFFICIENT_SAMPLE |
| game_total | 0.071 | 10 | INSUFFICIENT_SAMPLE |
| hitter_hits | 0.020 | 2 | INSUFFICIENT_SAMPLE |
| hitter_hits_runs_rbis | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| hitter_rbis | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| hitter_stolen_bases | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| hitter_total_bases | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| inning_result | 0.504 | 49 | DESCRIPTIVE_ONLY |
| inning_total | 0.450 | 1 | INSUFFICIENT_SAMPLE |
| pitcher_outs | 0.267 | 8 | INSUFFICIENT_SAMPLE |
| pitcher_strikeouts | 0.106 | 19 | INSUFFICIENT_SAMPLE |
| team_total | 0.454 | 9 | INSUFFICIENT_SAMPLE |
| winning_margin | 0.014 | 2 | INSUFFICIENT_SAMPLE |

## Historical trend (daily / weekly / season)

- Daily trend points: 3
- Weekly trend points: 3
- Season-to-date: {'period': 'SEASON_TO_DATE', 'periodType': 'season', 'n': 129, 'winRate': 0.43410852713178294, 'actualWinRate': 0.43410852713178294, 'expectedWinRate': 0.5327550000000001, 'calibrationError': -0.0986, 'roi': -0.16414106416599747, 'totalStake': 1544.12, 'totalNetProfitLoss': -253.4535, 'avgClv': -1.9406249999999998, 'status': 'CALIBRATED'}
