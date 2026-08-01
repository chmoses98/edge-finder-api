# EdgeLab Phase 2 Milestone 2 — Calibration Report

_Generated 2026-08-01T06:44:32Z_

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
| 0-2 | 3 | 33.3% | n/a | n/a | -44.7% | 0.833 | INSUFFICIENT_SAMPLE |
| 2-4 | 10 | 70.0% | n/a | n/a | 75.6% | -0.154 | INSUFFICIENT_SAMPLE |
| 4-6 | 1 | 0.0% | n/a | n/a | -100.0% | -0.990 | INSUFFICIENT_SAMPLE |

## Confidence calibration
| Confidence | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| MEDIUM | 7 | 57.1% | n/a | n/a | 6.0% | -0.070 | INSUFFICIENT_SAMPLE |
| HIGH | 6 | 66.7% | n/a | n/a | 66.0% | 0.105 | INSUFFICIENT_SAMPLE |
| LOW | 1 | 0.0% | n/a | n/a | -100.0% | 0.000 | INSUFFICIENT_SAMPLE |

## Market-family report
| Canonical family | Bets | Win % | ROI | Avg CLV | Avg edge | Avg confidence (1-3) | Calibration error | Status |
|---|---|---|---|---|---|---|---|---|
| game_result | 7 | 28.6% | -37.5% | -0.257 | 2.980 | 2.286 | n/a | INSUFFICIENT_SAMPLE |
| inning_result | 7 | 85.7% | 89.5% | 0.416 | 2.686 | 2.429 | n/a | INSUFFICIENT_SAMPLE |

## Thesis-tag calibration
_(no tagged decided bets yet -- thesisTags coverage is 0% in the real
ledger today; this is a known, honest gap, see docs/EDGELAB_CALIBRATION.md)_

## Thesis-tag co-occurrence
_(no co-tagged bets yet)_

## CLV bucket calibration
| CLV bucket | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| -5-0 | 4 | 25.0% | n/a | n/a | 10.3% | -0.948 | INSUFFICIENT_SAMPLE |
| 0-5 | 8 | 62.5% | n/a | n/a | 30.9% | 0.509 | INSUFFICIENT_SAMPLE |
| UNKNOWN | 2 | 100.0% | n/a | n/a | 112.6% | n/a | INSUFFICIENT_SAMPLE |

## CLV sign study (positive / neutral / negative)
| CLV sign | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| POSITIVE | 4 | 75.0% | n/a | n/a | 46.5% | 0.995 | INSUFFICIENT_SAMPLE |
| NEUTRAL | 4 | 50.0% | n/a | n/a | 9.9% | 0.022 | INSUFFICIENT_SAMPLE |
| NEGATIVE | 4 | 25.0% | n/a | n/a | 10.3% | -0.948 | INSUFFICIENT_SAMPLE |
| UNKNOWN | 2 | 100.0% | n/a | n/a | 112.6% | n/a | INSUFFICIENT_SAMPLE |

## Timing-bucket calibration
| Timing bucket | n | Win rate | Expected win rate | Calibration error | ROI | Avg CLV | Status |
|---|---|---|---|---|---|---|---|
| T_MINUS_5 | 2 | 100.0% | n/a | n/a | 112.6% | n/a | INSUFFICIENT_SAMPLE |
| INTERMEDIATE | 12 | 50.0% | n/a | n/a | 23.8% | 0.023 | INSUFFICIENT_SAMPLE |

## Recommendation-path analysis
| Path | n | Win rate | ROI | Avg CLV | Avg model prob | Avg market prob | Avg edge | Status |
|---|---|---|---|---|---|---|---|---|
| OTHER_BET | 12 | 50.0% | 23.8% | 0.023 | n/a | n/a | n/a | INSUFFICIENT_SAMPLE |
| MANUAL_BET | 2 | 100.0% | 112.6% | n/a | n/a | n/a | n/a | INSUFFICIENT_SAMPLE |

_`RECOMMENDED_NOT_BET`/`PASSED` rows have no win rate/ROI/CLV: no bet was ever placed on
them, so there is no real stake or outcome to measure -- only what the model/market
recorded at decision time. See docs/EDGELAB_CALIBRATION.md._

## Daily trend
| Period | n | Win rate | ROI | Avg CLV | Status |
|---|---|---|---|---|---|
| 2026-06-12 | 12 | 50.0% | 23.8% | 0.023 | INSUFFICIENT_SAMPLE |
| 2026-06-17 | 2 | 100.0% | 112.6% | n/a | INSUFFICIENT_SAMPLE |

## Weekly trend
| Period | n | Win rate | ROI | Avg CLV | Status |
|---|---|---|---|---|---|
| 2026-06-08 | 12 | 50.0% | 23.8% | 0.023 | INSUFFICIENT_SAMPLE |
| 2026-06-15 | 2 | 100.0% | 112.6% | n/a | INSUFFICIENT_SAMPLE |

## Monthly trend
| Period | n | Win rate | ROI | Avg CLV | Status |
|---|---|---|---|---|---|
| 2026-06 | 14 | 57.1% | 34.3% | 0.023 | INSUFFICIENT_SAMPLE |

## Season-to-date
| Period | n | Win rate | ROI | Avg CLV | Status |
|---|---|---|---|---|---|
| SEASON_TO_DATE | 14 | 57.1% | 34.3% | 0.023 | INSUFFICIENT_SAMPLE |
