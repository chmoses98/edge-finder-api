# EdgeLab Phase 2 Milestone 2 — Calibration Report

_Generated 2026-08-12T06:08:34Z_

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
| -4--2 | 3 | 100.0% | 4178.7% | -40.7867 | 89.9% | n/a | INSUFFICIENT_SAMPLE |
| 0-2 | 10 | 40.0% | 5316.9% | -52.7686 | -17.7% | 0.625 | INSUFFICIENT_SAMPLE |
| 2-4 | 20 | 60.0% | 5679.7% | -56.1970 | 30.6% | -0.137 | DESCRIPTIVE_ONLY |
| 4-6 | 1 | 0.0% | n/a | n/a | -100.0% | -0.990 | INSUFFICIENT_SAMPLE |
| UNKNOWN | 69 | 39.1% | n/a | n/a | -25.6% | -12.833 | DESCRIPTIVE_ONLY |

## Confidence calibration
| Confidence | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| UNKNOWN | 72 | 41.7% | 4178.7% | -41.3700 | -20.0% | -12.833 | DESCRIPTIVE_ONLY |
| MEDIUM | 15 | 60.0% | 5232.3% | -51.7225 | 26.5% | -0.050 | INSUFFICIENT_SAMPLE |
| PAPER | 9 | 33.3% | 5795.2% | -57.6189 | -28.8% | n/a | INSUFFICIENT_SAMPLE |
| HIGH | 6 | 66.7% | n/a | n/a | 66.0% | 0.105 | INSUFFICIENT_SAMPLE |
| LOW | 1 | 0.0% | n/a | n/a | -100.0% | 0.000 | INSUFFICIENT_SAMPLE |

## Market-family report
| Canonical family | Bets | Win % | ROI | Avg CLV | Avg edge | Avg confidence (1-3) | Calibration error | Status |
|---|---|---|---|---|---|---|---|---|
| inning_result | 37 | 48.6% | -5.3% | -2.658 | 2.530 | 2.250 | -48.6085 | DESCRIPTIVE_ONLY |
| game_result | 19 | 36.8% | -18.0% | -0.089 | 2.334 | 2.200 | -56.4899 | INSUFFICIENT_SAMPLE |
| pitcher_strikeouts | 14 | 42.9% | -14.0% | -37.000 | n/a | n/a | n/a | INSUFFICIENT_SAMPLE |
| first_inning_run | 10 | 40.0% | -10.7% | n/a | 2.724 | n/a | -60.4860 | INSUFFICIENT_SAMPLE |
| game_total | 8 | 62.5% | 16.1% | n/a | n/a | n/a | n/a | INSUFFICIENT_SAMPLE |
| team_total | 7 | 42.9% | -32.5% | -15.000 | -2.774 | n/a | -41.3581 | INSUFFICIENT_SAMPLE |
| pitcher_outs | 5 | 20.0% | -52.7% | n/a | n/a | n/a | n/a | INSUFFICIENT_SAMPLE |
| winning_margin | 2 | 50.0% | -44.7% | n/a | n/a | n/a | n/a | INSUFFICIENT_SAMPLE |
| hitter_hits | 1 | 100.0% | 85.2% | n/a | n/a | n/a | n/a | INSUFFICIENT_SAMPLE |

## Thesis-tag calibration
| Thesis tag | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| LINEUP_EDGE | 20 | 55.0% | 5327.6% | -52.7255 | 18.5% | 0.000 | DESCRIPTIVE_ONLY |
| PRICE_DISLOCATION | 17 | 47.1% | 5530.3% | -54.8324 | 3.9% | 0.000 | INSUFFICIENT_SAMPLE |
| BULLPEN_DISADVANTAGE | 4 | 50.0% | 5381.8% | -53.3175 | -5.7% | 0.000 | INSUFFICIENT_SAMPLE |
| BULLPEN_EDGE | 4 | 50.0% | 5496.8% | -54.4675 | 10.5% | 0.000 | INSUFFICIENT_SAMPLE |
| F5_OVER_FULL_GAME | 2 | 50.0% | 5215.0% | -51.6500 | 20.9% | 0.000 | INSUFFICIENT_SAMPLE |
| MARKET_EXPRESSION | 2 | 100.0% | 4323.5% | -42.2350 | 90.6% | n/a | INSUFFICIENT_SAMPLE |
| PARK_FACTOR | 2 | 100.0% | 4256.5% | -41.5650 | 84.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_EDGE | 1 | 0.0% | 5848.0% | -58.4800 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FADE | 1 | 100.0% | 4582.0% | -44.8200 | 185.7% | 0.000 | INSUFFICIENT_SAMPLE |

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
| 0-5 | 13 | 53.8% | 4799.0% | -47.4515 | 25.6% | 0.467 | INSUFFICIENT_SAMPLE |
| UNKNOWN | 83 | 44.6% | 5386.3% | -53.4170 | -14.1% | n/a | DESCRIPTIVE_ONLY |

## CLV sign study (positive / neutral / negative)
| CLV sign | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| POSITIVE | 6 | 50.0% | n/a | n/a | -5.2% | 0.997 | INSUFFICIENT_SAMPLE |
| NEUTRAL | 7 | 57.1% | 4799.0% | -47.4186 | 46.3% | 0.013 | INSUFFICIENT_SAMPLE |
| NEGATIVE | 7 | 28.6% | n/a | n/a | -36.3% | -11.827 | INSUFFICIENT_SAMPLE |
| UNKNOWN | 83 | 44.6% | 5386.3% | -53.4170 | -14.1% | n/a | DESCRIPTIVE_ONLY |

## Timing-bucket calibration
| Timing bucket | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| T_MINUS_5 | 2 | 100.0% | n/a | n/a | 112.6% | n/a | INSUFFICIENT_SAMPLE |
| INTERMEDIATE | 12 | 50.0% | n/a | n/a | 23.8% | 0.023 | INSUFFICIENT_SAMPLE |
| UNKNOWN | 89 | 42.7% | 5327.6% | -52.8485 | -15.4% | -9.625 | DESCRIPTIVE_ONLY |

## Recommendation-path analysis
| Path | n | Win rate | ROI | Avg CLV | Avg model prob | Avg market prob | Avg edge | Status |
|---|---|---|---|---|---|---|---|---|
| RECOMMENDED_AND_BET | 80 | 43.8% | -10.5% | -9.625 | n/a | n/a | n/a | DESCRIPTIVE_ONLY |
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
| UNKNOWN | UNKNOWN | 82 | 41.5% | -21.2% | -4.262 | DESCRIPTIVE_ONLY |
| UNKNOWN | scripts/build_market_ledger.py | 15 | 66.7% | 37.5% | 0.000 | INSUFFICIENT_SAMPLE |
| f5_three_way_v1 | scripts/build_market_ledger.py | 6 | 33.3% | -15.7% | 0.000 | INSUFFICIENT_SAMPLE |

## Data-quality calibration
| Data quality | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| UNKNOWN | 83 | 42.2% | n/a | n/a | -20.4% | -4.262 | DESCRIPTIVE_ONLY |
| full | 20 | 55.0% | 5327.6% | -52.7255 | 18.5% | 0.000 | DESCRIPTIVE_ONLY |

## Correlation-group calibration
| Correlation group | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| YRFI | 5 | 40.0% | 6088.6% | -60.4860 | -12.4% | n/a | INSUFFICIENT_SAMPLE |
| F5_SIDE_CWS | 2 | 50.0% | 4889.5% | -48.3950 | 5.5% | n/a | INSUFFICIENT_SAMPLE |
| GAME_SIDE_WSH | 2 | 100.0% | 5580.5% | -54.8050 | 101.3% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Davis Martin | 2 | 50.0% | 4889.5% | -48.3950 | 5.5% | n/a | INSUFFICIENT_SAMPLE |
| F5_SIDE_ATH | 1 | 0.0% | 4518.0% | -45.1800 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| F5_SIDE_KC | 1 | 0.0% | 5848.0% | -58.4800 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| F5_SIDE_MIA | 1 | 0.0% | 4730.0% | -47.3000 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| F5_SIDE_TOR | 1 | 100.0% | 4582.0% | -44.8200 | 185.7% | 0.000 | INSUFFICIENT_SAMPLE |
| GAME_SIDE_CHC | 1 | 100.0% | n/a | n/a | 96.1% | n/a | INSUFFICIENT_SAMPLE |
| GAME_SIDE_DET | 1 | 100.0% | 5970.0% | -58.7000 | 81.8% | n/a | INSUFFICIENT_SAMPLE |
| GAME_SIDE_LAA | 1 | 0.0% | 4607.0% | -46.0700 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| GAME_SIDE_MIN | 1 | 0.0% | 6308.0% | -63.0800 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| GAME_SIDE_NYY | 1 | 100.0% | 6069.0% | -59.6900 | 78.6% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Aaron Nola | 1 | 100.0% | 4582.0% | -44.8200 | 185.7% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Brady Singer | 1 | 0.0% | 4518.0% | -45.1800 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Bryce Elder | 1 | 0.0% | 4730.0% | -47.3000 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Cade Povich | 1 | 0.0% | 4607.0% | -46.0700 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Chase Burns | 1 | 100.0% | 5016.0% | -49.1600 | 127.3% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Chase Petty | 1 | 100.0% | 6145.0% | -60.4500 | 85.2% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Dean Kremer | 1 | 0.0% | 5848.0% | -58.4800 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Joey Cantillo | 1 | 100.0% | 5025.0% | -49.2500 | 156.4% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Logan Webb | 1 | 100.0% | 5970.0% | -58.7000 | 81.8% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Patrick Sandoval | 1 | 0.0% | 4754.0% | -47.5400 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Randy Dobnak | 1 | 0.0% | 6308.0% | -63.0800 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_FAILURE_Tyler Mahle | 1 | 100.0% | 6069.0% | -59.6900 | 78.6% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Andrew Alvarez | 1 | 100.0% | 5016.0% | -49.1600 | 127.3% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Cade Cavalli | 1 | 100.0% | 6145.0% | -60.4500 | 85.2% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Eury Pérez | 1 | 0.0% | 4730.0% | -47.3000 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Grayson Rodriguez | 1 | 0.0% | 4607.0% | -46.0700 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_J.T. Ginn | 1 | 0.0% | 4518.0% | -45.1800 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Joe Ryan | 1 | 0.0% | 6308.0% | -63.0800 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Max Fried | 1 | 100.0% | 6069.0% | -59.6900 | 78.6% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Max Scherzer | 1 | 100.0% | 4582.0% | -44.8200 | 185.7% | 0.000 | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Noah Cameron | 1 | 0.0% | 5848.0% | -58.4800 | -100.0% | n/a | INSUFFICIENT_SAMPLE |
| STARTER_SUCCESS_Troy Melton | 1 | 100.0% | 5970.0% | -58.7000 | 81.8% | n/a | INSUFFICIENT_SAMPLE |
| TEAM_RUNS_OVER_COL | 1 | 100.0% | 4624.0% | -45.2400 | 78.6% | n/a | INSUFFICIENT_SAMPLE |
| TEAM_RUNS_OVER_CWS | 1 | 100.0% | 3889.0% | -37.8900 | 88.7% | n/a | INSUFFICIENT_SAMPLE |
| TEAM_RUNS_OVER_SD | 1 | 100.0% | 4023.0% | -39.2300 | 100.0% | n/a | INSUFFICIENT_SAMPLE |

## Daily trend
| Period | n | Win rate | ROI | Avg CLV | Status |
|---|---|---|---|---|---|
| 2026-06-12 | 12 | 50.0% | 23.8% | 0.023 | INSUFFICIENT_SAMPLE |
| 2026-06-17 | 2 | 100.0% | 112.6% | n/a | INSUFFICIENT_SAMPLE |
| None | 89 | 42.7% | -15.4% | -9.625 | DESCRIPTIVE_ONLY |

## Weekly trend
| Period | n | Win rate | ROI | Avg CLV | Status |
|---|---|---|---|---|---|
| 2026-06-08 | 12 | 50.0% | 23.8% | 0.023 | INSUFFICIENT_SAMPLE |
| 2026-06-15 | 2 | 100.0% | 112.6% | n/a | INSUFFICIENT_SAMPLE |
| None | 89 | 42.7% | -15.4% | -9.625 | DESCRIPTIVE_ONLY |

## Monthly trend
| Period | n | Win rate | ROI | Avg CLV | Status |
|---|---|---|---|---|---|
| 2026-06 | 14 | 57.1% | 34.3% | 0.023 | INSUFFICIENT_SAMPLE |
| None | 89 | 42.7% | -15.4% | -9.625 | DESCRIPTIVE_ONLY |

## Season-to-date
| Period | n | Win rate | ROI | Avg CLV | Status |
|---|---|---|---|---|---|
| SEASON_TO_DATE | 103 | 44.7% | -12.0% | -3.836 | CALIBRATED |
