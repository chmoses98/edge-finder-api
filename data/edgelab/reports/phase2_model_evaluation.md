# EdgeLab Phase 2 Milestone 3/4 — Model Evaluation Report

_Generated 2026-08-01T15:21:21Z_

**This report measures data completeness and linkage only.** It does not
evaluate model accuracy (see docs/EDGELAB_CALIBRATION.md for that) and makes
no betting recommendations.

## Population coverage
Total ModelEvaluation records: **275**

| Field | Count (%) |
|---|---|
| modelFairProbability | 70 (25.4%) |
| estimatedEdge | 70 (25.4%) |
| confidence | 36 (13.1%) |
| thesisTags | 186 (67.6%) |
| linked to Recommendation | 275 (100.0%) |
| linked to PlacedBet | 7 (2.5%) |
| linked to Settlement | n/a (entity unavailable) |

## Breakdown by canonical market family
| Canonical family | n | % w/ prob | % w/ edge | % w/ confidence | % w/ tags |
|---|---|---|---|---|---|
| UNKNOWN | 146 | 0.0% | 0.0% | 0.0% | 69.2% |
| winning_margin | 40 | 0.0% | 0.0% | 0.0% | 0.0% |
| team_total | 38 | 100.0% | 100.0% | 10.5% | 97.4% |
| game_total | 19 | 0.0% | 0.0% | 0.0% | 84.2% |
| first_inning_run | 16 | 100.0% | 100.0% | 100.0% | 100.0% |
| game_result | 8 | 100.0% | 100.0% | 100.0% | 100.0% |
| inning_result | 8 | 100.0% | 100.0% | 100.0% | 100.0% |

## Breakdown by model version / source
| Model version | Model source | n |
|---|---|---|
| UNKNOWN | scripts/build_market_ledger.py | 275 |

## Breakdown by date
| Date | n | % w/ prob | % w/ edge | % w/ confidence | % w/ tags | % w/ correlation groups |
|---|---|---|---|---|---|---|
| 2026-07-30 | 110 | 18.2% | 18.2% | 10.9% | 57.3% | 100.0% |
| 2026-07-31 | 165 | 30.3% | 30.3% | 14.6% | 74.5% | 100.0% |

## Breakdown by recommendation status
| Recommendation status | n | % w/ prob | % w/ confidence | % w/ tags |
|---|---|---|---|---|
| PASS_NO_EDGE | 176 | 19.3% | 0.0% | 66.5% |
| PASS_DATA_QUALITY | 63 | 0.0% | 0.0% | 54.0% |
| RECOMMENDED | 29 | 100.0% | 100.0% | 96.5% |
| BET_PLACED | 7 | 100.0% | 100.0% | 100.0% |

## Unresolved / conflicting metadata
Of **70** fully `EVALUATED` records:
- **34** are missing `confidence` entirely.
- **0** have no lineup evidence at all (`lineupConfirmationState=UNKNOWN`).

A non-zero count here is a genuine data gap in the upstream pipeline artifact for that
specific row, not a query defect -- see docs/EDGELAB_EVALUATION_METADATA.md.
