# EdgeLab Phase 2 Milestone 2 — Calibration Report

_Generated 2026-09-03T21:21:10Z_

**This report measures historical model performance only. It makes no
betting recommendations and does not influence production recommendation
or staking logic in any way.** Every bucket below carries an explicit
sample-size status: `INSUFFICIENT_SAMPLE` (n<20) is noise, not evidence;
`DESCRIPTIVE_ONLY` (20<=n<100) is a real number that is not yet a
calibrated statistical claim; `CALIBRATED` (n>=100) means enough volume
exists for the reliability numbers to be a meaningful summary -- still
not, by itself, a signal to change strategy.

## Edge bucket calibration
| Edge bucket | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| -56--54 | 1 | 100.0% | 44.5% | 0.5552 | 104.8% | -0.880 | INSUFFICIENT_SAMPLE |
| -46--44 | 1 | 100.0% | 53.5% | 0.4649 | 66.1% | 1.000 | INSUFFICIENT_SAMPLE |
| -40--38 | 1 | 100.0% | 61.6% | 0.3840 | 76.7% | 0.000 | INSUFFICIENT_SAMPLE |
| -36--34 | 2 | 100.0% | 65.1% | 0.3489 | 67.7% | 0.000 | INSUFFICIENT_SAMPLE |
| -34--32 | 1 | 100.0% | 66.6% | 0.3343 | 56.5% | 0.000 | INSUFFICIENT_SAMPLE |
| -22--20 | 2 | 100.0% | 47.5% | 0.5252 | 81.6% | 2.060 | INSUFFICIENT_SAMPLE |
| -4--2 | 4 | 75.0% | 39.2% | 0.3581 | 27.7% | 0.000 | INSUFFICIENT_SAMPLE |
| -2-0 | 1 | 0.0% | 41.0% | -0.4095 | -99.9% | 2.000 | INSUFFICIENT_SAMPLE |
| 0-2 | 27 | 48.1% | 3217.6% | -31.6948 | -19.4% | 0.436 | DESCRIPTIVE_ONLY |
| 2-4 | 43 | 48.8% | 3131.6% | -30.8275 | 13.6% | 0.120 | DESCRIPTIVE_ONLY |
| 4-6 | 2 | 50.0% | 53.3% | -0.0328 | 66.4% | 10.455 | INSUFFICIENT_SAMPLE |
| 22-24 | 2 | 50.0% | 54.5% | -0.0454 | 7.7% | 1.070 | INSUFFICIENT_SAMPLE |
| 24-26 | 1 | 0.0% | 37.6% | -0.3761 | -98.4% | n/a | INSUFFICIENT_SAMPLE |
| 28-30 | 1 | 0.0% | 60.5% | -0.6054 | -98.6% | n/a | INSUFFICIENT_SAMPLE |
| 32-34 | 1 | 100.0% | 48.4% | 0.5155 | 76.9% | 2.120 | INSUFFICIENT_SAMPLE |
| 50-52 | 1 | 0.0% | 62.6% | -0.6261 | -96.7% | 0.000 | INSUFFICIENT_SAMPLE |
| UNKNOWN | 207 | 46.4% | n/a | n/a | -11.5% | -0.030 | CALIBRATED |

## Confidence calibration
| Confidence | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| UNKNOWN | 229 | 48.5% | 52.2% | -0.0369 | -8.5% | 0.133 | CALIBRATED |
| MEDIUM | 53 | 49.1% | 3911.2% | -38.6218 | 10.1% | 0.579 | DESCRIPTIVE_ONLY |
| PAPER | 9 | 33.3% | 58.0% | -0.2462 | -28.8% | -1.419 | INSUFFICIENT_SAMPLE |
| HIGH | 6 | 66.7% | n/a | n/a | 66.0% | -0.105 | INSUFFICIENT_SAMPLE |
| LOW | 1 | 0.0% | n/a | n/a | -100.0% | 0.000 | INSUFFICIENT_SAMPLE |

## Market-family report
| Canonical family | Bets | Win % | ROI | Avg CLV | Avg edge | Avg confidence (1-3) | Calibration error | Status |
|---|---|---|---|---|---|---|---|---|
| inning_result | 131 | 45.0% | -9.8% | 0.822 | 3.001 | 2.079 | -34.7076 | CALIBRATED |
| team_total | 61 | 50.8% | -8.4% | 0.764 | -4.289 | 2.000 | -0.0183 | DESCRIPTIVE_ONLY |
| game_result | 52 | 50.0% | -5.8% | 0.451 | 0.192 | 2.095 | -27.1923 | DESCRIPTIVE_ONLY |
| pitcher_strikeouts | 21 | 57.1% | 10.5% | 0.930 | -21.353 | n/a | 0.2550 | DESCRIPTIVE_ONLY |
| game_total | 12 | 66.7% | 24.4% | -11.066 | n/a | n/a | n/a | INSUFFICIENT_SAMPLE |
| first_inning_run | 11 | 45.5% | 4.4% | -0.823 | 2.724 | n/a | -0.1543 | INSUFFICIENT_SAMPLE |
| pitcher_outs | 6 | 16.7% | -61.1% | 1.532 | n/a | n/a | n/a | INSUFFICIENT_SAMPLE |
| hitter_hits | 2 | 50.0% | -0.2% | -0.475 | n/a | n/a | n/a | INSUFFICIENT_SAMPLE |
| winning_margin | 2 | 50.0% | -44.7% | -0.370 | n/a | n/a | n/a | INSUFFICIENT_SAMPLE |

## Thesis-tag calibration
| Thesis tag | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| LINEUP_EDGE | 24 | 50.0% | 54.2% | -0.0416 | -3.8% | -0.637 | DESCRIPTIVE_ONLY |
| PRICE_DISLOCATION | 19 | 47.4% | 56.6% | -0.0919 | 3.5% | -0.910 | INSUFFICIENT_SAMPLE |
| BULLPEN_DISADVANTAGE | 4 | 50.0% | 53.8% | -0.0382 | -5.7% | -0.960 | INSUFFICIENT_SAMPLE |
| BULLPEN_EDGE | 4 | 50.0% | 55.0% | -0.0497 | 10.5% | -0.920 | INSUFFICIENT_SAMPLE |
| MARKET_EXPRESSION | 4 | 50.0% | 46.6% | 0.0338 | -40.8% | 0.500 | INSUFFICIENT_SAMPLE |
| F5_OVER_FULL_GAME | 3 | 66.7% | 56.7% | 0.1001 | 44.6% | -0.953 | INSUFFICIENT_SAMPLE |
| PARK_FACTOR | 3 | 66.7% | 48.1% | 0.1860 | -27.8% | 0.333 | INSUFFICIENT_SAMPLE |
| STARTER_EDGE | 2 | 50.0% | 62.1% | -0.1207 | 10.1% | -1.430 | INSUFFICIENT_SAMPLE |
| STARTER_FADE | 1 | 100.0% | 45.8% | 0.5418 | 185.7% | 0.000 | INSUFFICIENT_SAMPLE |

## Thesis-tag co-occurrence
| Tag A | Tag B | Co-occurrence count |
|---|---|---|
| LINEUP_EDGE | PRICE_DISLOCATION | 19 |
| BULLPEN_DISADVANTAGE | LINEUP_EDGE | 4 |
| BULLPEN_DISADVANTAGE | PRICE_DISLOCATION | 4 |
| BULLPEN_EDGE | LINEUP_EDGE | 4 |
| BULLPEN_EDGE | PRICE_DISLOCATION | 4 |
| LINEUP_EDGE | MARKET_EXPRESSION | 4 |
| BULLPEN_DISADVANTAGE | BULLPEN_EDGE | 3 |
| F5_OVER_FULL_GAME | LINEUP_EDGE | 3 |
| F5_OVER_FULL_GAME | PRICE_DISLOCATION | 3 |
| LINEUP_EDGE | PARK_FACTOR | 3 |
| F5_OVER_FULL_GAME | STARTER_EDGE | 2 |
| LINEUP_EDGE | STARTER_EDGE | 2 |
| MARKET_EXPRESSION | PARK_FACTOR | 2 |
| PRICE_DISLOCATION | STARTER_EDGE | 2 |
| BULLPEN_DISADVANTAGE | F5_OVER_FULL_GAME | 1 |
| BULLPEN_DISADVANTAGE | STARTER_EDGE | 1 |
| F5_OVER_FULL_GAME | STARTER_FADE | 1 |
| LINEUP_EDGE | STARTER_FADE | 1 |
| PRICE_DISLOCATION | STARTER_FADE | 1 |

## CLV bucket calibration
| CLV bucket | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| -20--15 | 4 | 75.0% | n/a | n/a | 34.3% | -16.953 | INSUFFICIENT_SAMPLE |
| -15--10 | 4 | 75.0% | n/a | n/a | 37.9% | -12.542 | INSUFFICIENT_SAMPLE |
| -10--5 | 4 | 75.0% | n/a | n/a | 37.5% | -7.860 | INSUFFICIENT_SAMPLE |
| -5-0 | 75 | 58.7% | 1422.4% | -13.6375 | 13.0% | -1.793 | DESCRIPTIVE_ONLY |
| 0-5 | 171 | 41.5% | 2685.0% | -26.4345 | -17.5% | 0.677 | CALIBRATED |
| 5-10 | 3 | 0.0% | n/a | n/a | -100.0% | 6.997 | INSUFFICIENT_SAMPLE |
| 10-15 | 2 | 50.0% | 6223.0% | -61.7300 | -66.3% | 12.745 | INSUFFICIENT_SAMPLE |
| 15-20 | 2 | 50.0% | 53.3% | -0.0328 | 26.6% | 17.460 | INSUFFICIENT_SAMPLE |
| 20-25 | 1 | 0.0% | n/a | n/a | -100.0% | 23.120 | INSUFFICIENT_SAMPLE |
| 25-30 | 1 | 0.0% | n/a | n/a | -100.0% | 27.000 | INSUFFICIENT_SAMPLE |
| 35-40 | 1 | 100.0% | n/a | n/a | 63.9% | 37.000 | INSUFFICIENT_SAMPLE |
| 40-45 | 1 | 0.0% | n/a | n/a | -98.5% | 42.000 | INSUFFICIENT_SAMPLE |
| UNKNOWN | 29 | 58.6% | 1219.5% | -11.6088 | 6.5% | n/a | DESCRIPTIVE_ONLY |

## CLV sign study (positive / neutral / negative)
| CLV sign | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| POSITIVE | 102 | 45.1% | 3732.9% | -36.8777 | -0.7% | 3.198 | CALIBRATED |
| NEUTRAL | 82 | 36.6% | 56.4% | -0.1978 | -35.8% | 0.000 | DESCRIPTIVE_ONLY |
| NEGATIVE | 85 | 60.0% | 1422.4% | -13.6241 | 15.6% | -3.338 | DESCRIPTIVE_ONLY |
| UNKNOWN | 29 | 58.6% | 1219.5% | -11.6088 | 6.5% | n/a | DESCRIPTIVE_ONLY |

## Timing-bucket calibration
| Timing bucket | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| T_MINUS_60 | 3 | 33.3% | 5292.0% | -52.5867 | -24.3% | 1.197 | INSUFFICIENT_SAMPLE |
| T_MINUS_30 | 5 | 40.0% | 4778.6% | -47.3860 | 1.9% | 0.253 | INSUFFICIENT_SAMPLE |
| T_MINUS_15 | 4 | 75.0% | 5656.8% | -55.8175 | 30.0% | 2.972 | INSUFFICIENT_SAMPLE |
| T_MINUS_5 | 4 | 75.0% | 5415.5% | -53.4050 | 49.3% | 0.475 | INSUFFICIENT_SAMPLE |
| INTERMEDIATE | 34 | 44.1% | 4824.4% | -47.8029 | 7.2% | 0.453 | DESCRIPTIVE_ONLY |
| UNKNOWN | 248 | 48.4% | 54.2% | -0.0581 | -7.7% | 0.043 | CALIBRATED |

## Recommendation-path analysis
| Path | n | Win rate | ROI | Avg CLV | Avg model prob | Avg market prob | Avg edge | Status |
|---|---|---|---|---|---|---|---|---|
| RECOMMENDED_AND_BET | 181 | 49.2% | -4.8% | -0.376 | n/a | n/a | n/a | CALIBRATED |
| MANUAL_BET | 69 | 47.8% | -13.1% | 1.349 | n/a | n/a | n/a | DESCRIPTIVE_ONLY |
| MODEL_BET | 36 | 44.4% | -2.2% | 0.947 | n/a | n/a | n/a | DESCRIPTIVE_ONLY |
| OTHER_BET | 12 | 50.0% | 23.8% | -0.023 | n/a | n/a | n/a | INSUFFICIENT_SAMPLE |
| PASSED | 2881 | n/a | n/a | n/a | 40.304 | 48.781 | -1.987 | CALIBRATED |
| RECOMMENDED_NOT_BET | 4 | n/a | n/a | n/a | 44.173 | 25.258 | 4.762 | INSUFFICIENT_SAMPLE |

_`RECOMMENDED_NOT_BET`/`PASSED` rows have no win rate/ROI/CLV: no bet was ever placed on
them, so there is no real stake or outcome to measure -- only what the model/market
recorded at decision time. See docs/EDGELAB_CALIBRATION.md._

## Model version/source calibration
| Model version | Model source | n | Win rate | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|
| UNKNOWN | UNKNOWN | 256 | 46.5% | -10.4% | 0.119 | CALIBRATED |
| UNKNOWN | scripts/build_market_ledger.py | 18 | 55.6% | -8.7% | -0.495 | INSUFFICIENT_SAMPLE |
| UNKNOWN | lib.kalshi_probability_adapters.adapt_contract | 17 | 70.6% | 30.9% | 2.173 | INSUFFICIENT_SAMPLE |
| f5_three_way_v1 | scripts/build_market_ledger.py | 7 | 42.9% | 11.4% | -0.911 | INSUFFICIENT_SAMPLE |

## Data-quality calibration
| Data quality | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| UNKNOWN | 274 | 48.2% | 3401.6% | -33.5345 | -7.1% | 0.236 | CALIBRATED |
| full | 24 | 50.0% | 54.2% | -0.0416 | -3.8% | -0.637 | DESCRIPTIVE_ONLY |

## Correlation-group calibration
| Correlation group | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| YRFI | 5 | 40.0% | 60.9% | -0.2089 | -12.4% | -1.866 | INSUFFICIENT_SAMPLE |
| F5_SIDE_CWS | 2 | 50.0% | 48.9% | 0.0111 | 5.5% | 0.090 | INSUFFICIENT_SAMPLE |
| GAME_SIDE_WSH | 2 | 100.0% | 55.8% | 0.4420 | 101.3% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Davis Martin | 2 | 50.0% | 48.9% | 0.0111 | 5.5% | 0.090 | INSUFFICIENT_SAMPLE |
| F5_SIDE_ATH | 1 | 0.0% | 45.2% | -0.4518 | -100.0% | -1.840 | INSUFFICIENT_SAMPLE |
| F5_SIDE_KC | 1 | 0.0% | 58.5% | -0.5848 | -100.0% | -2.000 | INSUFFICIENT_SAMPLE |
| F5_SIDE_MIA | 1 | 0.0% | 47.3% | -0.4730 | -100.0% | -1.860 | INSUFFICIENT_SAMPLE |
| F5_SIDE_PHI | 1 | 100.0% | 65.7% | 0.3434 | 65.1% | -0.860 | INSUFFICIENT_SAMPLE |
| F5_SIDE_TOR | 1 | 100.0% | 45.8% | 0.5418 | 185.7% | 0.000 | INSUFFICIENT_SAMPLE |
| GAME_SIDE_CHC | 1 | 100.0% | n/a | n/a | 96.1% | 0.000 | INSUFFICIENT_SAMPLE |
| GAME_SIDE_DET | 1 | 100.0% | 59.7% | 0.4030 | 81.8% | 0.000 | INSUFFICIENT_SAMPLE |
| GAME_SIDE_LAA | 1 | 0.0% | 46.1% | -0.4607 | -100.0% | 0.260 | INSUFFICIENT_SAMPLE |
| GAME_SIDE_MIN | 1 | 0.0% | 63.1% | -0.6308 | -100.0% | -1.840 | INSUFFICIENT_SAMPLE |
| GAME_SIDE_NYY | 1 | 100.0% | 60.7% | 0.3931 | 78.6% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Aaron Nola | 1 | 100.0% | 45.8% | 0.5418 | 185.7% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Brady Singer | 1 | 0.0% | 45.2% | -0.4518 | -100.0% | -1.840 | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Bryce Elder | 1 | 0.0% | 47.3% | -0.4730 | -100.0% | -1.860 | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Cade Povich | 1 | 0.0% | 46.1% | -0.4607 | -100.0% | 0.260 | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Chase Burns | 1 | 100.0% | 50.2% | 0.4984 | 127.3% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Chase Petty | 1 | 100.0% | 61.5% | 0.3855 | 85.2% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Dean Kremer | 1 | 0.0% | 58.5% | -0.5848 | -100.0% | -2.000 | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Joey Cantillo | 1 | 100.0% | 50.2% | 0.4975 | 156.4% | 1.000 | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Logan Webb | 1 | 100.0% | 59.7% | 0.4030 | 81.8% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Patrick Sandoval | 1 | 0.0% | 47.5% | -0.4754 | -100.0% | -0.820 | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Randy Dobnak | 1 | 0.0% | 63.1% | -0.6308 | -100.0% | -1.840 | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Ryan Johnson | 1 | 100.0% | 65.7% | 0.3434 | 65.1% | -0.860 | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Tyler Mahle | 1 | 100.0% | 60.7% | 0.3931 | 78.6% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Andrew Alvarez | 1 | 100.0% | 50.2% | 0.4984 | 127.3% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Cade Cavalli | 1 | 100.0% | 61.5% | 0.3855 | 85.2% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Cristopher Sánchez | 1 | 100.0% | 65.7% | 0.3434 | 65.1% | -0.860 | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Eury Pérez | 1 | 0.0% | 47.3% | -0.4730 | -100.0% | -1.860 | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Grayson Rodriguez | 1 | 0.0% | 46.1% | -0.4607 | -100.0% | 0.260 | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_J.T. Ginn | 1 | 0.0% | 45.2% | -0.4518 | -100.0% | -1.840 | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Joe Ryan | 1 | 0.0% | 63.1% | -0.6308 | -100.0% | -1.840 | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Max Fried | 1 | 100.0% | 60.7% | 0.3931 | 78.6% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Max Scherzer | 1 | 100.0% | 45.8% | 0.5418 | 185.7% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Noah Cameron | 1 | 0.0% | 58.5% | -0.5848 | -100.0% | -2.000 | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Troy Melton | 1 | 100.0% | 59.7% | 0.4030 | 81.8% | 0.000 | INSUFFICIENT_SAMPLE |
| TEAM_RUNS_OVER_BAL | 1 | 0.0% | 41.0% | -0.4095 | -99.9% | 2.000 | INSUFFICIENT_SAMPLE |
| TEAM_RUNS_OVER_COL | 1 | 100.0% | 46.2% | 0.5376 | 78.6% | 1.000 | INSUFFICIENT_SAMPLE |
| TEAM_RUNS_OVER_CWS | 1 | 100.0% | 38.9% | 0.6111 | 88.7% | 0.000 | INSUFFICIENT_SAMPLE |
| TEAM_RUNS_OVER_MIL | 1 | 0.0% | 68.8% | -0.6875 | -98.1% | 0.000 | INSUFFICIENT_SAMPLE |
| TEAM_RUNS_OVER_PHI | 1 | 0.0% | 59.1% | -0.5907 | -99.3% | 0.000 | INSUFFICIENT_SAMPLE |
| TEAM_RUNS_OVER_SD | 1 | 100.0% | 40.2% | 0.5977 | 100.0% | -1.000 | INSUFFICIENT_SAMPLE |

## Daily trend
| Period | n | Win rate | ROI | Avg CLV | Status |
|---|---|---|---|---|---|
| 2026-06-12 | 12 | 50.0% | 23.8% | -0.023 | INSUFFICIENT_SAMPLE |
| 2026-06-17 | 2 | 100.0% | 112.6% | n/a | INSUFFICIENT_SAMPLE |
| 2026-08-02 | 2 | 50.0% | 38.9% | 0.505 | INSUFFICIENT_SAMPLE |
| 2026-08-04 | 5 | 0.0% | -95.5% | 1.322 | INSUFFICIENT_SAMPLE |
| 2026-08-05 | 6 | 33.3% | -53.5% | 0.518 | INSUFFICIENT_SAMPLE |
| 2026-08-06 | 1 | 100.0% | 45.0% | -0.540 | INSUFFICIENT_SAMPLE |
| 2026-08-07 | 5 | 60.0% | 32.4% | 0.278 | INSUFFICIENT_SAMPLE |
| 2026-08-08 | 3 | 66.7% | 76.0% | 0.527 | INSUFFICIENT_SAMPLE |
| 2026-08-09 | 6 | 83.3% | 55.6% | 2.388 | INSUFFICIENT_SAMPLE |
| 2026-08-10 | 3 | 33.3% | 37.1% | 0.517 | INSUFFICIENT_SAMPLE |
| 2026-08-16 | 1 | 0.0% | -90.4% | 1.520 | INSUFFICIENT_SAMPLE |
| 2026-08-18 | 1 | 0.0% | -93.7% | 0.560 | INSUFFICIENT_SAMPLE |
| 2026-08-19 | 1 | 100.0% | 126.2% | 1.510 | INSUFFICIENT_SAMPLE |
| 2026-08-20 | 1 | 0.0% | -96.4% | 0.500 | INSUFFICIENT_SAMPLE |
| 2026-08-27 | 1 | 0.0% | -98.2% | n/a | INSUFFICIENT_SAMPLE |
| None | 248 | 48.4% | -7.7% | 0.043 | CALIBRATED |

## Weekly trend
| Period | n | Win rate | ROI | Avg CLV | Status |
|---|---|---|---|---|---|
| 2026-06-08 | 12 | 50.0% | 23.8% | -0.023 | INSUFFICIENT_SAMPLE |
| 2026-06-15 | 2 | 100.0% | 112.6% | n/a | INSUFFICIENT_SAMPLE |
| 2026-07-27 | 2 | 50.0% | 38.9% | 0.505 | INSUFFICIENT_SAMPLE |
| 2026-08-03 | 26 | 50.0% | -1.1% | 1.018 | DESCRIPTIVE_ONLY |
| 2026-08-10 | 4 | 25.0% | 2.3% | 0.768 | INSUFFICIENT_SAMPLE |
| 2026-08-17 | 3 | 33.3% | -12.3% | 0.857 | INSUFFICIENT_SAMPLE |
| 2026-08-24 | 1 | 0.0% | -98.2% | n/a | INSUFFICIENT_SAMPLE |
| None | 248 | 48.4% | -7.7% | 0.043 | CALIBRATED |

## Monthly trend
| Period | n | Win rate | ROI | Avg CLV | Status |
|---|---|---|---|---|---|
| 2026-06 | 14 | 57.1% | 34.3% | -0.023 | INSUFFICIENT_SAMPLE |
| 2026-08 | 36 | 44.4% | -2.2% | 0.947 | DESCRIPTIVE_ONLY |
| None | 248 | 48.4% | -7.7% | 0.043 | CALIBRATED |

## Season-to-date
| Period | n | Win rate | ROI | Avg CLV | Status |
|---|---|---|---|---|---|
| SEASON_TO_DATE | 298 | 48.3% | -6.8% | 0.158 | CALIBRATED |
