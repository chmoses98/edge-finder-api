# EdgeLab Phase 2 Milestone 6 — Market Intelligence Report

_Generated 2026-08-20T19:41:38Z_

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
| first_inning_run | 10 | 40.0% | -10.7% | n/a | 0.9% | 12.2% | 0.0% | 0.0% |
| game_result | 19 | 36.8% | -18.0% | -0.089 | 1.5% | 12.9% | 17.0% | 2.8% |
| game_total | 10 | 70.0% | 28.5% | n/a | 0.4% | 5.5% | 0.0% | 0.0% |
| hitter_hits | 2 | 50.0% | -0.2% | n/a | 0.0% | 0.0% | n/a | n/a |
| hitter_hits_runs_rbis | 0 | n/a | n/a | n/a | 0.0% | 0.0% | n/a | n/a |
| hitter_rbis | 0 | n/a | n/a | n/a | 0.0% | 0.0% | n/a | n/a |
| hitter_stolen_bases | 0 | n/a | n/a | n/a | 0.0% | 0.0% | n/a | n/a |
| hitter_total_bases | 0 | n/a | n/a | n/a | 0.0% | 0.0% | n/a | n/a |
| inning_result | 42 | 47.6% | -8.6% | -2.658 | 1.6% | 4.4% | 6.4% | 0.0% |
| inning_total | 0 | n/a | n/a | n/a | 0.0% | 0.0% | n/a | n/a |
| pitcher_outs | 6 | 16.7% | -61.1% | n/a | 1.7% | 0.0% | n/a | n/a |
| pitcher_strikeouts | 17 | 47.1% | -7.0% | -37.000 | 0.6% | 0.0% | n/a | n/a |
| team_total | 9 | 33.3% | -44.3% | -15.000 | 0.2% | 8.4% | 3.0% | 0.0% |
| winning_margin | 2 | 50.0% | -44.7% | n/a | 0.0% | 11.4% | 0.0% | 0.0% |

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
| INSUFFICIENT_SUPPORT | 317 | CALIBRATED | {} |
| PASS_NO_EDGE | 1181 | CALIBRATED | {'SETTLED': 536, 'SETTLEMENT_UNRESOLVED': 4} |
| RECOMMENDED_NOT_BET | 221 | CALIBRATED | {'SETTLED': 191, 'SETTLEMENT_UNRESOLVED': 1} |

## Strategy experiments (SIMULATION -- not real recorded outcomes)

**Baseline** (real, unmodified settled bets): n=117 (CALIBRATED), winRate=44.4%, ROI=-12.7%

| Experiment | n | Status | Win rate | ROI | Delta ROI vs baseline |
|---|---|---|---|---|---|
| DOMINATED_MARKETS_REPLACED_WITH_BEST_EXPRESSION | 117 | CALIBRATED | 44.4% | -12.7% | 0.0% |
| ALWAYS_PREFER_F5 | 117 | CALIBRATED | 44.4% | -12.7% | 0.0% |
| NEVER_FULL_GAME_ML_WITH_BULLPEN_DISADVANTAGE | 115 | CALIBRATED | 43.5% | -14.9% | -2.2% |
| REMOVE_NEGATIVE_CLV_MARKETS | 110 | CALIBRATED | 45.5% | -11.4% | 1.3% |

## Edge stability

| Edge bucket | n | Status | Stable | Volatile | False edge | Unknown |
|---|---|---|---|---|---|---|
| -20.0 | 2 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 2 |
| -16.0 | 2 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 2 |
| -14.0 | 2 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 2 |
| -12.0 | 8 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 8 |
| -10.0 | 32 | DESCRIPTIVE_ONLY | 6 | 0 | 0 | 26 |
| -8.0 | 88 | DESCRIPTIVE_ONLY | 20 | 0 | 0 | 68 |
| -6.0 | 145 | CALIBRATED | 50 | 0 | 0 | 95 |
| -4.0 | 195 | CALIBRATED | 93 | 0 | 0 | 102 |
| -2.0 | 144 | CALIBRATED | 67 | 0 | 0 | 77 |
| 0.0 | 179 | CALIBRATED | 60 | 0 | 4 | 115 |
| 2.0 | 171 | CALIBRATED | 64 | 0 | 5 | 102 |
| 4.0 | 31 | DESCRIPTIVE_ONLY | 17 | 0 | 0 | 14 |
| 6.0 | 5 | INSUFFICIENT_SAMPLE | 1 | 0 | 0 | 4 |
| 8.0 | 1 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 1 |
| 10.0 | 1 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 1 |
| 12.0 | 2 | INSUFFICIENT_SAMPLE | 0 | 0 | 0 | 2 |

## Market health scores

| Family | Health score | Sample n | Status |
|---|---|---|---|
| UNKNOWN | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| first_inning_run | 0.521 | 10 | INSUFFICIENT_SAMPLE |
| game_result | 0.549 | 19 | INSUFFICIENT_SAMPLE |
| game_total | 0.071 | 10 | INSUFFICIENT_SAMPLE |
| hitter_hits | 0.020 | 2 | INSUFFICIENT_SAMPLE |
| hitter_hits_runs_rbis | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| hitter_rbis | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| hitter_stolen_bases | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| hitter_total_bases | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| inning_result | 0.638 | 42 | DESCRIPTIVE_ONLY |
| inning_total | 0.000 | 0 | INSUFFICIENT_SAMPLE |
| pitcher_outs | 0.060 | 6 | INSUFFICIENT_SAMPLE |
| pitcher_strikeouts | 0.094 | 17 | INSUFFICIENT_SAMPLE |
| team_total | 0.454 | 9 | INSUFFICIENT_SAMPLE |
| winning_margin | 0.014 | 2 | INSUFFICIENT_SAMPLE |

## Historical trend (daily / weekly / season)

- Daily trend points: 3
- Weekly trend points: 3
- Season-to-date: {'period': 'SEASON_TO_DATE', 'periodType': 'season', 'n': 117, 'winRate': 0.4444444444444444, 'actualWinRate': 0.4444444444444444, 'expectedWinRate': 0.5327550000000001, 'calibrationError': -0.0883, 'roi': -0.12698520372979946, 'totalStake': 1334.1199999999994, 'totalNetProfitLoss': -169.4135, 'avgClv': -3.8359999999999994, 'status': 'CALIBRATED'}
