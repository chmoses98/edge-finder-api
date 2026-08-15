# EdgeLab Phase 2 Milestone 1 — Query Foundation Report

_Generated 2026-08-15T15:47:36Z_

**This is a descriptive-statistics report, not a calibrated model.** Every
grouped metric below carries an explicit sample-size status; a group
marked `INSUFFICIENT_SAMPLE` (fewer than 20 observations) is noise, not
evidence, and must not be read as a recommendation to change strategy.

## Entity availability
- `bets`: available (1 file(s))
- `clv_quotes`: available (15 file(s))
- `games`: available (15 file(s))
- `markets`: available (15 file(s))
- `model_evaluations`: available (14 file(s))
- `observations`: available (15 file(s))
- `recommendations`: available (14 file(s))
- `research_runs`: available (17 file(s))
- `settlements`: available (13 file(s))

## Row counts by entity and date
| Entity | Date | Rows |
|---|---|---|
| bets | None | 115 |
| bets | 2026-06-12 | 17 |
| bets | 2026-06-17 | 6 |
| bets | 2026-06-18 | 5 |
| bets | 2026-06-19 | 24 |
| bets | 2026-07-02 | 1 |
| bets | 2026-07-05 | 4 |
| bets | 2026-07-06 | 2 |
| bets | 2026-07-07 | 1 |
| bets | 2026-07-09 | 1 |
| bets | 2026-07-12 | 3 |
| bets | 2026-07-21 | 3 |
| bets | 2026-07-24 | 3 |
| bets | 2026-07-30 | 3 |
| bets | 2026-07-31 | 4 |
| bets | 2026-08-01 | 3 |
| bets | 2026-08-02 | 2 |
| bets | 2026-08-04 | 5 |
| bets | 2026-08-05 | 6 |
| bets | 2026-08-06 | 1 |
| bets | 2026-08-07 | 5 |
| bets | 2026-08-08 | 3 |
| bets | 2026-08-09 | 6 |
| bets | 2026-08-10 | 3 |
| clv_quotes | 2026-08-01 | 4597 |
| clv_quotes | 2026-08-02 | 5468 |
| clv_quotes | 2026-08-03 | 1178 |
| clv_quotes | 2026-08-04 | 7005 |
| clv_quotes | 2026-08-05 | 7157 |
| clv_quotes | 2026-08-06 | 1935 |
| clv_quotes | 2026-08-07 | 4840 |
| clv_quotes | 2026-08-08 | 5220 |
| clv_quotes | 2026-08-09 | 8750 |
| clv_quotes | 2026-08-10 | 5847 |
| clv_quotes | 2026-08-11 | 4322 |
| clv_quotes | 2026-08-12 | 4950 |
| clv_quotes | 2026-08-13 | 2735 |
| clv_quotes | 2026-08-14 | 4637 |
| clv_quotes | 2026-08-15 | 1029 |
| games | 2026-08-01 | 28 |
| games | 2026-08-02 | 30 |
| games | 2026-08-03 | 16 |
| games | 2026-08-04 | 30 |
| games | 2026-08-05 | 26 |
| games | 2026-08-06 | 22 |
| games | 2026-08-07 | 30 |
| games | 2026-08-08 | 27 |
| games | 2026-08-09 | 30 |
| games | 2026-08-10 | 20 |
| games | 2026-08-11 | 15 |
| games | 2026-08-12 | 15 |
| games | 2026-08-13 | 9 |
| games | 2026-08-14 | 14 |
| games | 2026-08-15 | 15 |
| markets | 2026-08-01 | 4135 |
| markets | 2026-08-02 | 4848 |
| markets | 2026-08-03 | 1178 |
| markets | 2026-08-04 | 4973 |
| markets | 2026-08-05 | 4812 |
| markets | 2026-08-06 | 1932 |
| markets | 2026-08-07 | 3594 |
| markets | 2026-08-08 | 5036 |
| markets | 2026-08-09 | 5095 |
| markets | 2026-08-10 | 3429 |
| markets | 2026-08-11 | 4322 |
| markets | 2026-08-12 | 4950 |
| markets | 2026-08-13 | 2735 |
| markets | 2026-08-14 | 4563 |
| markets | 2026-08-15 | 1164 |
| model_evaluations | 2026-07-30 | 110 |
| model_evaluations | 2026-07-31 | 165 |
| model_evaluations | 2026-08-02 | 4949 |
| model_evaluations | 2026-08-03 | 1229 |
| model_evaluations | 2026-08-04 | 5055 |
| model_evaluations | 2026-08-05 | 4914 |
| model_evaluations | 2026-08-06 | 2036 |
| model_evaluations | 2026-08-07 | 3674 |
| model_evaluations | 2026-08-08 | 5127 |
| model_evaluations | 2026-08-09 | 5179 |
| model_evaluations | 2026-08-10 | 3484 |
| model_evaluations | 2026-08-11 | 4322 |
| model_evaluations | 2026-08-12 | 4950 |
| model_evaluations | 2026-08-13 | 2735 |
| observations | 2026-08-01 | 12366 |
| observations | 2026-08-02 | 14127 |
| observations | 2026-08-03 | 4892 |
| observations | 2026-08-04 | 13467 |
| observations | 2026-08-05 | 13176 |
| observations | 2026-08-06 | 3612 |
| observations | 2026-08-07 | 9243 |
| observations | 2026-08-08 | 12812 |
| observations | 2026-08-09 | 24155 |
| observations | 2026-08-10 | 12336 |
| observations | 2026-08-11 | 12258 |
| observations | 2026-08-12 | 16876 |
| observations | 2026-08-13 | 8988 |
| observations | 2026-08-14 | 15998 |
| observations | 2026-08-15 | 2427 |
| recommendations | 2026-07-30 | 110 |
| recommendations | 2026-07-31 | 165 |
| recommendations | 2026-08-02 | 4949 |
| recommendations | 2026-08-03 | 1229 |
| recommendations | 2026-08-04 | 5055 |
| recommendations | 2026-08-05 | 4914 |
| recommendations | 2026-08-06 | 2036 |
| recommendations | 2026-08-07 | 3674 |
| recommendations | 2026-08-08 | 5127 |
| recommendations | 2026-08-09 | 5179 |
| recommendations | 2026-08-10 | 3484 |
| recommendations | 2026-08-11 | 4322 |
| recommendations | 2026-08-12 | 4950 |
| recommendations | 2026-08-13 | 2735 |
| research_runs | 2026-07-30 | 1 |
| research_runs | 2026-07-31 | 6 |
| research_runs | 2026-08-01 | 27 |
| research_runs | 2026-08-02 | 32 |
| research_runs | 2026-08-03 | 35 |
| research_runs | 2026-08-04 | 33 |
| research_runs | 2026-08-05 | 37 |
| research_runs | 2026-08-06 | 20 |
| research_runs | 2026-08-07 | 36 |
| research_runs | 2026-08-08 | 35 |
| research_runs | 2026-08-09 | 50 |
| research_runs | 2026-08-10 | 68 |
| research_runs | 2026-08-11 | 42 |
| research_runs | 2026-08-12 | 38 |
| research_runs | 2026-08-13 | 38 |
| research_runs | 2026-08-14 | 50 |
| research_runs | 2026-08-15 | 10 |
| settlements | 2026-08-02 | 4848 |
| settlements | 2026-08-03 | 1178 |
| settlements | 2026-08-04 | 4973 |
| settlements | 2026-08-05 | 4812 |
| settlements | 2026-08-06 | 1932 |
| settlements | 2026-08-07 | 3594 |
| settlements | 2026-08-08 | 5036 |
| settlements | 2026-08-09 | 5095 |
| settlements | 2026-08-10 | 3429 |
| settlements | 2026-08-11 | 4322 |
| settlements | 2026-08-12 | 4950 |
| settlements | 2026-08-13 | 2735 |
| settlements | 2026-08-14 | 12 |

## Placed bets by canonical market family
| Canonical family | Count | Sample status |
|---|---|---|
| inning_result | 107 | DESCRIPTIVE_ONLY |
| game_result | 49 | DESCRIPTIVE_ONLY |
| pitcher_strikeouts | 19 | INSUFFICIENT_SAMPLE |
| first_inning_run | 12 | INSUFFICIENT_SAMPLE |
| team_total | 12 | INSUFFICIENT_SAMPLE |
| game_total | 10 | INSUFFICIENT_SAMPLE |
| pitcher_outs | 8 | INSUFFICIENT_SAMPLE |
| UNKNOWN | 4 | INSUFFICIENT_SAMPLE |
| hitter_hits | 2 | INSUFFICIENT_SAMPLE |
| winning_margin | 2 | INSUFFICIENT_SAMPLE |
| inning_total | 1 | INSUFFICIENT_SAMPLE |

## ROI by canonical market family (settled bets only)
| Canonical family | n | Total stake | Total P/L | ROI | Sample status |
|---|---|---|---|---|---|
| inning_result | 49 | 633.48 | -149.94160000000002 | -23.7% | DESCRIPTIVE_ONLY |
| game_result | 19 | 165.32999999999998 | -29.838600000000007 | -18.0% | INSUFFICIENT_SAMPLE |
| pitcher_strikeouts | 19 | 228.6 | -7.975499999999997 | -3.5% | INSUFFICIENT_SAMPLE |
| first_inning_run | 10 | 94.18999999999998 | -10.074600000000002 | -10.7% | INSUFFICIENT_SAMPLE |
| game_total | 10 | 136.32 | 38.7988 | 28.5% | INSUFFICIENT_SAMPLE |
| team_total | 9 | 138.95 | -61.6096 | -44.3% | INSUFFICIENT_SAMPLE |
| pitcher_outs | 8 | 101.53 | -30.9577 | -30.5% | INSUFFICIENT_SAMPLE |
| hitter_hits | 2 | 14.83 | -0.03369999999999962 | -0.2% | INSUFFICIENT_SAMPLE |
| winning_margin | 2 | 20.89 | -9.341000000000001 | -44.7% | INSUFFICIENT_SAMPLE |
| inning_total | 1 | 10.0 | 7.52 | 75.2% | INSUFFICIENT_SAMPLE |

## CLV summary by canonical market family
| Canonical family | n | Avg CLV (cents) | Positive | Negative | Sample status |
|---|---|---|---|---|---|
| inning_result | 16 | -1.432 | 6 | 3 | INSUFFICIENT_SAMPLE |
| game_result | 9 | -0.089 | 4 | 3 | INSUFFICIENT_SAMPLE |
| pitcher_strikeouts | 3 | -12.333 | 0 | 1 | INSUFFICIENT_SAMPLE |
| pitcher_outs | 2 | -0.190 | 1 | 1 | INSUFFICIENT_SAMPLE |
| inning_total | 1 | 14.000 | 1 | 0 | INSUFFICIENT_SAMPLE |
| team_total | 1 | -15.000 | 0 | 1 | INSUFFICIENT_SAMPLE |

## Unmapped market-family values
None — every observed `marketFamily` spelling is covered by the mapping table.

## Data-population completeness
| Entity | Field | Populated | Total | % | Status |
|---|---|---|---|---|---|
| bets | thesisTags | 0 | 226 | 0.0% | OK |
| bets | correlationGroup | 0 | 226 | 0.0% | OK |
| bets | recommendationId | 94 | 226 | 41.6% | OK |
| bets | sport | 226 | 226 | 100.0% | OK |
| bets | platform | 226 | 226 | 100.0% | OK |
| observations | lineupConfirmationState | 0 | 176733 | 0.0% | OK |
| observations | sport | 176733 | 176733 | 100.0% | OK |
| observations | platform | 176733 | 176733 | 100.0% | OK |
