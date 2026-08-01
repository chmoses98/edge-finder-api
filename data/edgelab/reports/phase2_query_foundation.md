# EdgeLab Phase 2 Milestone 1 — Query Foundation Report

_Generated 2026-08-01T06:44:32Z_

**This is a descriptive-statistics report, not a calibrated model.** Every
grouped metric below carries an explicit sample-size status; a group
marked `INSUFFICIENT_SAMPLE` (fewer than 20 observations) is noise, not
evidence, and must not be read as a recommendation to change strategy.

## Entity availability
- `bets`: available (1 file(s))
- `clv_quotes`: available (1 file(s))
- `games`: no files yet (0 file(s))
- `markets`: no files yet (0 file(s))
- `observations`: no files yet (0 file(s))
- `recommendations`: no files yet (0 file(s))
- `research_runs`: available (2 file(s))
- `settlements`: no files yet (0 file(s))

## Row counts by entity and date
| Entity | Date | Rows |
|---|---|---|
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
| research_runs | 2026-07-31 | 5 |
| research_runs | 2026-08-01 | 1 |

## Placed bets by canonical market family
| Canonical family | Count | Sample status |
|---|---|---|
| inning_result | 42 | DESCRIPTIVE_ONLY |
| game_result | 26 | DESCRIPTIVE_ONLY |
| UNKNOWN | 4 | INSUFFICIENT_SAMPLE |
| team_total | 3 | INSUFFICIENT_SAMPLE |
| first_inning_run | 2 | INSUFFICIENT_SAMPLE |

## ROI by canonical market family (settled bets only)
| Canonical family | n | Total stake | Total P/L | ROI | Sample status |
|---|---|---|---|---|---|
| game_result | 7 | 33.0 | -12.39 | -37.5% | INSUFFICIENT_SAMPLE |
| inning_result | 7 | 43.0 | 38.47 | 89.5% | INSUFFICIENT_SAMPLE |

## CLV summary by canonical market family
| Canonical family | n | Avg CLV (cents) | Positive | Negative | Sample status |
|---|---|---|---|---|---|
| game_result | 7 | -0.257 | 3 | 3 | INSUFFICIENT_SAMPLE |
| inning_result | 5 | 0.416 | 3 | 1 | INSUFFICIENT_SAMPLE |

## Unmapped market-family values
None — every observed `marketFamily` spelling is covered by the mapping table.

## Data-population completeness
| Entity | Field | Populated | Total | % | Status |
|---|---|---|---|---|---|
| bets | thesisTags | 0 | 77 | 0.0% | OK |
| bets | correlationGroup | 0 | 77 | 0.0% | OK |
| bets | recommendationId | 0 | 77 | 0.0% | OK |
| bets | sport | 0 | 0 | 0.0% | FIELD_NEVER_WRITTEN |
| bets | platform | 0 | 0 | 0.0% | FIELD_NEVER_WRITTEN |
| observations | lineupConfirmationState | 0 | 0 | n/a | UNAVAILABLE |
| observations | sport | 0 | 0 | n/a | UNAVAILABLE |
| observations | platform | 0 | 0 | n/a | UNAVAILABLE |
