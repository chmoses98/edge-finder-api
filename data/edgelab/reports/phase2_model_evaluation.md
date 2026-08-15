# EdgeLab Phase 2 Milestone 3/4 — Model Evaluation Report

_Generated 2026-08-15T15:48:26Z_

**This report measures data completeness and linkage only.** It does not
evaluate model accuracy (see docs/EDGELAB_CALIBRATION.md for that) and makes
no betting recommendations.

## Population coverage
Total ModelEvaluation records: **47929**

| Field | Count (%) |
|---|---|
| modelFairProbability | 595 (1.2%) |
| estimatedEdge | 595 (1.2%) |
| confidence | 219 (0.5%) |
| thesisTags | 1015 (2.1%) |
| linked to Recommendation | 47929 (100.0%) |
| linked to PlacedBet | 94 (0.2%) |
| linked to Settlement | 47008 (98.1%) |

## Breakdown by canonical market family
| Canonical family | n | % w/ prob | % w/ edge | % w/ confidence | % w/ tags |
|---|---|---|---|---|---|
| hitter_hits_runs_rbis | 11160 | 0.0% | 0.0% | 0.0% | 0.0% |
| hitter_total_bases | 9591 | 0.0% | 0.0% | 0.0% | 0.0% |
| hitter_hits | 7966 | 0.0% | 0.0% | 0.0% | 0.0% |
| hitter_rbis | 5058 | 0.0% | 0.0% | 0.0% | 0.0% |
| pitcher_strikeouts | 2263 | 0.0% | 0.0% | 0.0% | 0.0% |
| team_total | 2254 | 11.0% | 11.0% | 1.4% | 11.0% |
| winning_margin | 1861 | 0.0% | 0.0% | 0.0% | 0.0% |
| hitter_stolen_bases | 1856 | 0.0% | 0.0% | 0.0% | 0.0% |
| game_total | 1802 | 0.0% | 0.0% | 0.0% | 4.5% |
| inning_result | 1495 | 7.0% | 7.0% | 3.0% | 5.1% |
| inning_total | 1106 | 0.0% | 0.0% | 0.0% | 0.0% |
| UNKNOWN | 597 | 0.0% | 0.0% | 0.2% | 62.3% |
| game_result | 393 | 26.5% | 26.5% | 9.9% | 26.2% |
| pitcher_outs | 305 | 0.0% | 0.0% | 0.0% | 0.0% |
| first_inning_run | 222 | 62.6% | 62.6% | 46.4% | 60.4% |

## Breakdown by model version / source
| Model version | Model source | n |
|---|---|---|
| UNKNOWN | UNKNOWN | 46345 |
| UNKNOWN | scripts/build_market_ledger.py | 1380 |
| f5_three_way_v1 | scripts/build_market_ledger.py | 204 |

## Breakdown by date
| Date | n | % w/ prob | % w/ edge | % w/ confidence | % w/ tags | % w/ correlation groups |
|---|---|---|---|---|---|---|
| 2026-07-30 | 110 | 18.2% | 18.2% | 10.9% | 57.3% | 100.0% |
| 2026-07-31 | 165 | 30.3% | 30.3% | 14.6% | 74.5% | 100.0% |
| 2026-08-02 | 4949 | 1.0% | 1.0% | 0.6% | 2.2% | 3.3% |
| 2026-08-03 | 1229 | 1.8% | 1.8% | 0.7% | 4.0% | 7.2% |
| 2026-08-04 | 5055 | 1.1% | 1.1% | 0.6% | 2.1% | 3.3% |
| 2026-08-05 | 4914 | 0.8% | 0.8% | 0.4% | 2.2% | 3.4% |
| 2026-08-06 | 2036 | 0.5% | 0.5% | 0.2% | 2.7% | 5.9% |
| 2026-08-07 | 3674 | 1.5% | 1.5% | 0.8% | 3.1% | 4.5% |
| 2026-08-08 | 5127 | 2.0% | 2.0% | 0.4% | 2.0% | 3.2% |
| 2026-08-09 | 5179 | 2.1% | 2.1% | 0.5% | 2.2% | 3.2% |
| 2026-08-10 | 3484 | 2.3% | 2.3% | 0.4% | 2.2% | 3.2% |
| 2026-08-11 | 4322 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| 2026-08-12 | 4950 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| 2026-08-13 | 2735 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |

## Breakdown by recommendation status
| Recommendation status | n | % w/ prob | % w/ confidence | % w/ tags |
|---|---|---|---|---|
| INSUFFICIENT_MODEL_SUPPORT | 40855 | 0.0% | 0.0% | 0.0% |
| NOT_EVALUATED | 5416 | 0.0% | 0.0% | 0.0% |
| PASS_NO_EDGE | 1067 | 33.0% | 0.0% | 61.7% |
| PASS_DATA_QUALITY | 293 | 6.8% | 0.0% | 46.8% |
| RECOMMENDED | 202 | 100.0% | 100.0% | 98.0% |
| BET_PLACED | 90 | 17.8% | 17.8% | 17.8% |
| PASS_PRICE_TOO_HIGH | 5 | 100.0% | 0.0% | 100.0% |
| RECOMMENDED_NOT_BET | 1 | 0.0% | 100.0% | 100.0% |

## Unresolved / conflicting metadata
Of **595** fully `EVALUATED` records:
- **377** are missing `confidence` entirely.
- **59** have no lineup evidence at all (`lineupConfirmationState=UNKNOWN`).

A non-zero count here is a genuine data gap in the upstream pipeline artifact for that
specific row, not a query defect -- see docs/EDGELAB_EVALUATION_METADATA.md.
