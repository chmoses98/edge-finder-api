# EdgeLab Phase 2 Milestone 2 — Calibration Report

_Generated 2026-08-14T16:42:53Z_

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
| -4--2 | 3 | 100.0% | 41.8% | 0.5821 | 89.9% | n/a | INSUFFICIENT_SAMPLE |
| 0-2 | 10 | 40.0% | 53.2% | -0.1317 | -17.7% | 0.625 | INSUFFICIENT_SAMPLE |
| 2-4 | 20 | 60.0% | 56.8% | 0.0320 | 30.6% | -0.137 | DESCRIPTIVE_ONLY |
| 4-6 | 1 | 0.0% | n/a | n/a | -100.0% | -0.990 | INSUFFICIENT_SAMPLE |
| UNKNOWN | 83 | 39.8% | n/a | n/a | -23.6% | -12.833 | DESCRIPTIVE_ONLY |

## Confidence calibration
| Confidence | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| UNKNOWN | 86 | 41.9% | 41.8% | 0.0007 | -19.2% | -12.833 | DESCRIPTIVE_ONLY |
| MEDIUM | 15 | 60.0% | 52.3% | 0.0768 | 26.5% | -0.050 | INSUFFICIENT_SAMPLE |
| PAPER | 9 | 33.3% | 58.0% | -0.2462 | -28.8% | n/a | INSUFFICIENT_SAMPLE |
| HIGH | 6 | 66.7% | n/a | n/a | 66.0% | 0.105 | INSUFFICIENT_SAMPLE |
| LOW | 1 | 0.0% | n/a | n/a | -100.0% | 0.000 | INSUFFICIENT_SAMPLE |

## Market-family report
| Canonical family | Bets | Win % | ROI | Avg CLV | Avg edge | Avg confidence (1-3) | Calibration error | Status |
|---|---|---|---|---|---|---|---|---|
| inning_result | 42 | 47.6% | -8.6% | -2.658 | 2.530 | 2.250 | -0.0148 | DESCRIPTIVE_ONLY |
| game_result | 19 | 36.8% | -18.0% | -0.089 | 2.334 | 2.200 | -0.2002 | INSUFFICIENT_SAMPLE |
| pitcher_strikeouts | 17 | 47.1% | -7.0% | -37.000 | n/a | n/a | n/a | INSUFFICIENT_SAMPLE |
| first_inning_run | 10 | 40.0% | -10.7% | n/a | 2.724 | n/a | -0.2089 | INSUFFICIENT_SAMPLE |
| game_total | 10 | 70.0% | 28.5% | n/a | n/a | n/a | n/a | INSUFFICIENT_SAMPLE |
| team_total | 9 | 33.3% | -44.3% | -15.000 | -2.774 | n/a | -0.0845 | INSUFFICIENT_SAMPLE |
| pitcher_outs | 6 | 16.7% | -61.1% | n/a | n/a | n/a | n/a | INSUFFICIENT_SAMPLE |
| hitter_hits | 2 | 50.0% | -0.2% | n/a | n/a | n/a | n/a | INSUFFICIENT_SAMPLE |
| winning_margin | 2 | 50.0% | -44.7% | n/a | n/a | n/a | n/a | INSUFFICIENT_SAMPLE |

## Thesis-tag calibration
| Thesis tag | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| LINEUP_EDGE | 20 | 55.0% | 53.3% | 0.0172 | 18.5% | 0.000 | DESCRIPTIVE_ONLY |
| PRICE_DISLOCATION | 17 | 47.1% | 55.3% | -0.0824 | 3.9% | 0.000 | INSUFFICIENT_SAMPLE |
| BULLPEN_DISADVANTAGE | 4 | 50.0% | 53.8% | -0.0382 | -5.7% | 0.000 | INSUFFICIENT_SAMPLE |
| BULLPEN_EDGE | 4 | 50.0% | 55.0% | -0.0497 | 10.5% | 0.000 | INSUFFICIENT_SAMPLE |
| F5_OVER_FULL_GAME | 2 | 50.0% | 52.1% | -0.0215 | 20.9% | 0.000 | INSUFFICIENT_SAMPLE |
| MARKET_EXPRESSION | 2 | 100.0% | 43.2% | 0.5676 | 90.6% | n/a | INSUFFICIENT_SAMPLE |
| PARK_FACTOR | 2 | 100.0% | 42.6% | 0.5743 | 84.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_EDGE | 1 | 0.0% | 58.5% | -0.5848 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FADE | 1 | 100.0% | 45.8% | 0.5418 | 185.7% | 0.000 | INSUFFICIENT_SAMPLE |

## Thesis-tag co-occurrence
| Tag A | Tag B | Co-occurrence count |
|---|---|---|
| LINEUP_EDGE | PRICE_DISLOCATION | 17 |
| BULLPEN_DISADVANTAGE | LINEUP_EDGE | 4 |
| BULLPEN_DISADVANTAGE | PRICE_DISLOCATION | 4 |
| BULLPEN_EDGE | LINEUP_EDGE | 4 |
| BULLPEN_EDGE | PRICE_DISLOCATION | 4 |
| BULLPEN_DISADVANTAGE | BULLPEN_EDGE | 3 |
| F5_OVER_FULL_GAME | LINEUP_EDGE | 2 |
| F5_OVER_FULL_GAME | PRICE_DISLOCATION | 2 |
| LINEUP_EDGE | MARKET_EXPRESSION | 2 |
| LINEUP_EDGE | PARK_FACTOR | 2 |
| BULLPEN_DISADVANTAGE | F5_OVER_FULL_GAME | 1 |
| BULLPEN_DISADVANTAGE | STARTER_EDGE | 1 |
| F5_OVER_FULL_GAME | STARTER_EDGE | 1 |
| F5_OVER_FULL_GAME | STARTER_FADE | 1 |
| LINEUP_EDGE | STARTER_EDGE | 1 |
| LINEUP_EDGE | STARTER_FADE | 1 |
| MARKET_EXPRESSION | PARK_FACTOR | 1 |
| PRICE_DISLOCATION | STARTER_EDGE | 1 |
| PRICE_DISLOCATION | STARTER_FADE | 1 |

## CLV bucket calibration
| CLV bucket | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| -40--35 | 1 | 100.0% | n/a | n/a | 63.9% | -37.000 | INSUFFICIENT_SAMPLE |
| -30--25 | 1 | 0.0% | n/a | n/a | -100.0% | -27.000 | INSUFFICIENT_SAMPLE |
| -15--10 | 1 | 0.0% | n/a | n/a | -100.0% | -15.000 | INSUFFICIENT_SAMPLE |
| -5-0 | 4 | 25.0% | n/a | n/a | 10.3% | -0.948 | INSUFFICIENT_SAMPLE |
| 0-5 | 13 | 53.8% | 48.0% | 0.0586 | 25.5% | 0.467 | INSUFFICIENT_SAMPLE |
| UNKNOWN | 97 | 44.3% | 53.9% | -0.0953 | -14.5% | n/a | DESCRIPTIVE_ONLY |

## CLV sign study (positive / neutral / negative)
| CLV sign | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| POSITIVE | 6 | 50.0% | n/a | n/a | -5.2% | 0.997 | INSUFFICIENT_SAMPLE |
| NEUTRAL | 7 | 57.1% | 48.0% | 0.0915 | 46.1% | 0.013 | INSUFFICIENT_SAMPLE |
| NEGATIVE | 7 | 28.6% | n/a | n/a | -36.3% | -11.827 | INSUFFICIENT_SAMPLE |
| UNKNOWN | 97 | 44.3% | 53.9% | -0.0953 | -14.5% | n/a | DESCRIPTIVE_ONLY |

## Timing-bucket calibration
| Timing bucket | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| T_MINUS_5 | 2 | 100.0% | n/a | n/a | 112.6% | n/a | INSUFFICIENT_SAMPLE |
| INTERMEDIATE | 12 | 50.0% | n/a | n/a | 23.8% | 0.023 | INSUFFICIENT_SAMPLE |
| UNKNOWN | 103 | 42.7% | 53.3% | -0.1056 | -15.5% | -9.625 | CALIBRATED |

## Recommendation-path analysis
| Path | n | Win rate | ROI | Avg CLV | Avg model prob | Avg market prob | Avg edge | Status |
|---|---|---|---|---|---|---|---|---|
| RECOMMENDED_AND_BET | 94 | 43.6% | -11.6% | -9.625 | n/a | n/a | n/a | DESCRIPTIVE_ONLY |
| OTHER_BET | 12 | 50.0% | 23.8% | 0.023 | n/a | n/a | n/a | INSUFFICIENT_SAMPLE |
| MANUAL_BET | 11 | 45.5% | -41.0% | n/a | n/a | n/a | n/a | INSUFFICIENT_SAMPLE |
| PASSED | 1365 | n/a | n/a | n/a | 37.929 | 48.575 | -2.438 | CALIBRATED |
| RECOMMENDED_NOT_BET | 1 | n/a | n/a | n/a | 17.460 | 0.990 | 4.198 | INSUFFICIENT_SAMPLE |

_`RECOMMENDED_NOT_BET`/`PASSED` rows have no win rate/ROI/CLV: no bet was ever placed on
them, so there is no real stake or outcome to measure -- only what the model/market
recorded at decision time. See docs/EDGELAB_CALIBRATION.md._

## Model version/source calibration
| Model version | Model source | n | Win rate | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|
| UNKNOWN | UNKNOWN | 96 | 41.7% | -20.2% | -4.262 | DESCRIPTIVE_ONLY |
| UNKNOWN | scripts/build_market_ledger.py | 15 | 66.7% | 37.5% | 0.000 | INSUFFICIENT_SAMPLE |
| f5_three_way_v1 | scripts/build_market_ledger.py | 6 | 33.3% | -15.7% | 0.000 | INSUFFICIENT_SAMPLE |

## Data-quality calibration
| Data quality | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| UNKNOWN | 97 | 42.3% | n/a | n/a | -19.5% | -4.262 | DESCRIPTIVE_ONLY |
| full | 20 | 55.0% | 53.3% | 0.0172 | 18.5% | 0.000 | DESCRIPTIVE_ONLY |

## Correlation-group calibration
| Correlation group | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| YRFI | 5 | 40.0% | 60.9% | -0.2089 | -12.4% | n/a | INSUFFICIENT_SAMPLE |
| F5_SIDE_CWS | 2 | 50.0% | 48.9% | 0.0111 | 5.5% | n/a | INSUFFICIENT_SAMPLE |
| GAME_SIDE_WSH | 2 | 100.0% | 55.8% | 0.4420 | 101.3% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Davis Martin | 2 | 50.0% | 48.9% | 0.0111 | 5.5% | n/a | INSUFFICIENT_SAMPLE |
| F5_SIDE_ATH | 1 | 0.0% | 45.2% | -0.4518 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| F5_SIDE_KC | 1 | 0.0% | 58.5% | -0.5848 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| F5_SIDE_MIA | 1 | 0.0% | 47.3% | -0.4730 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| F5_SIDE_TOR | 1 | 100.0% | 45.8% | 0.5418 | 185.7% | 0.000 | INSUFFICIENT_SAMPLE |
| GAME_SIDE_CHC | 1 | 100.0% | n/a | n/a | 96.1% | n/a | INSUFFICIENT_SAMPLE |
| GAME_SIDE_DET | 1 | 100.0% | 59.7% | 0.4030 | 81.8% | n/a | INSUFFICIENT_SAMPLE |
| GAME_SIDE_LAA | 1 | 0.0% | 46.1% | -0.4607 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| GAME_SIDE_MIN | 1 | 0.0% | 63.1% | -0.6308 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| GAME_SIDE_NYY | 1 | 100.0% | 60.7% | 0.3931 | 78.6% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Aaron Nola | 1 | 100.0% | 45.8% | 0.5418 | 185.7% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Brady Singer | 1 | 0.0% | 45.2% | -0.4518 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Bryce Elder | 1 | 0.0% | 47.3% | -0.4730 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Cade Povich | 1 | 0.0% | 46.1% | -0.4607 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Chase Burns | 1 | 100.0% | 50.2% | 0.4984 | 127.3% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Chase Petty | 1 | 100.0% | 61.5% | 0.3855 | 85.2% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Dean Kremer | 1 | 0.0% | 58.5% | -0.5848 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Joey Cantillo | 1 | 100.0% | 50.2% | 0.4975 | 156.4% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Logan Webb | 1 | 100.0% | 59.7% | 0.4030 | 81.8% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Patrick Sandoval | 1 | 0.0% | 47.5% | -0.4754 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Randy Dobnak | 1 | 0.0% | 63.1% | -0.6308 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Tyler Mahle | 1 | 100.0% | 60.7% | 0.3931 | 78.6% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Andrew Alvarez | 1 | 100.0% | 50.2% | 0.4984 | 127.3% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Cade Cavalli | 1 | 100.0% | 61.5% | 0.3855 | 85.2% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Eury Pérez | 1 | 0.0% | 47.3% | -0.4730 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Grayson Rodriguez | 1 | 0.0% | 46.1% | -0.4607 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_J.T. Ginn | 1 | 0.0% | 45.2% | -0.4518 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Joe Ryan | 1 | 0.0% | 63.1% | -0.6308 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Max Fried | 1 | 100.0% | 60.7% | 0.3931 | 78.6% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Max Scherzer | 1 | 100.0% | 45.8% | 0.5418 | 185.7% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Noah Cameron | 1 | 0.0% | 58.5% | -0.5848 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Troy Melton | 1 | 100.0% | 59.7% | 0.4030 | 81.8% | n/a | INSUFFICIENT_SAMPLE |
| TEAM_RUNS_OVER_COL | 1 | 100.0% | 46.2% | 0.5376 | 78.6% | n/a | INSUFFICIENT_SAMPLE |
| TEAM_RUNS_OVER_CWS | 1 | 100.0% | 38.9% | 0.6111 | 88.7% | n/a | INSUFFICIENT_SAMPLE |
| TEAM_RUNS_OVER_SD | 1 | 100.0% | 40.2% | 0.5977 | 100.0% | n/a | INSUFFICIENT_SAMPLE |

## Daily trend
| Period | n | Win rate | ROI | Avg CLV | Status |
|---|---|---|---|---|---|
| 2026-06-12 | 12 | 50.0% | 23.8% | 0.023 | INSUFFICIENT_SAMPLE |
| 2026-06-17 | 2 | 100.0% | 112.6% | n/a | INSUFFICIENT_SAMPLE |
| None | 103 | 42.7% | -15.5% | -9.625 | CALIBRATED |

## Weekly trend
| Period | n | Win rate | ROI | Avg CLV | Status |
|---|---|---|---|---|---|
| 2026-06-08 | 12 | 50.0% | 23.8% | 0.023 | INSUFFICIENT_SAMPLE |
| 2026-06-15 | 2 | 100.0% | 112.6% | n/a | INSUFFICIENT_SAMPLE |
| None | 103 | 42.7% | -15.5% | -9.625 | CALIBRATED |

## Monthly trend
| Period | n | Win rate | ROI | Avg CLV | Status |
|---|---|---|---|---|---|
| 2026-06 | 14 | 57.1% | 34.3% | 0.023 | INSUFFICIENT_SAMPLE |
| None | 103 | 42.7% | -15.5% | -9.625 | CALIBRATED |

## Season-to-date
| Period | n | Win rate | ROI | Avg CLV | Status |
|---|---|---|---|---|---|
| SEASON_TO_DATE | 117 | 44.4% | -12.7% | -3.836 | CALIBRATED |
