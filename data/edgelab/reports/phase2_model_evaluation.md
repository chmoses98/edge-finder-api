# EdgeLab Phase 2 Milestone 3/4 — Model Evaluation Report

_Generated 2026-08-20T19:40:41Z_

**This report measures data completeness and linkage only.** It does not
evaluate model accuracy (see docs/EDGELAB_CALIBRATION.md for that) and makes
no betting recommendations.

## Population coverage
Total ModelEvaluation records: **59753**

| Field | Count (%) |
|---|---|
| modelFairProbability | 1750 (2.9%) |
| estimatedEdge | 1750 (2.9%) |
| confidence | 403 (0.7%) |
| thesisTags | 2164 (3.6%) |
| linked to Recommendation | 58235 (97.5%) |
| linked to PlacedBet | 94 (0.2%) |
| linked to Settlement | 57814 (96.8%) |

## Breakdown by canonical market family
| Canonical family | n | % w/ prob | % w/ edge | % w/ confidence | % w/ tags |
|---|---|---|---|---|---|
| hitter_hits_runs_rbis | 13806 | 0.0% | 0.0% | 0.0% | 0.0% |
| hitter_total_bases | 11671 | 0.0% | 0.0% | 0.0% | 0.0% |
| hitter_hits | 9671 | 0.0% | 0.0% | 0.0% | 0.0% |
| hitter_rbis | 6191 | 0.0% | 0.0% | 0.0% | 0.0% |
| team_total | 2950 | 18.2% | 18.2% | 1.4% | 18.8% |
| pitcher_strikeouts | 2692 | 0.0% | 0.0% | 0.0% | 0.0% |
| winning_margin | 2501 | 0.0% | 0.0% | 0.0% | 0.0% |
| hitter_stolen_bases | 2322 | 0.0% | 0.0% | 0.0% | 0.0% |
| game_total | 2301 | 0.0% | 0.0% | 0.0% | 8.7% |
| inning_result | 2067 | 18.7% | 18.7% | 3.8% | 12.8% |
| inning_total | 1316 | 0.0% | 0.0% | 0.0% | 0.0% |
| game_result | 758 | 52.5% | 52.5% | 7.5% | 51.3% |
| UNKNOWN | 597 | 0.0% | 0.0% | 0.2% | 62.3% |
| first_inning_run | 547 | 78.1% | 78.1% | 41.3% | 70.4% |
| pitcher_outs | 363 | 0.0% | 0.0% | 0.0% | 0.0% |

## Breakdown by model version / source
| Model version | Model source | n |
|---|---|---|
| UNKNOWN | UNKNOWN | 56486 |
| UNKNOWN | scripts/build_market_ledger.py | 2780 |
| f5_three_way_v1 | scripts/build_market_ledger.py | 487 |

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
| 2026-08-15 | 5074 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| 2026-08-16 | 5661 | 7.6% | 7.6% | 1.0% | 7.3% | 10.5% |
| 2026-08-17 | 363 | 68.6% | 68.6% | 11.8% | 65.8% | 100.0% |
| 2026-08-18 | 352 | 71.6% | 71.6% | 11.7% | 70.5% | 100.0% |
| 2026-08-19 | 286 | 55.9% | 55.9% | 10.5% | 66.8% | 100.0% |
| 2026-08-20 | 88 | 72.7% | 72.7% | 13.6% | 67.0% | 100.0% |

## Breakdown by recommendation status
| Recommendation status | n | % w/ prob | % w/ confidence | % w/ tags |
|---|---|---|---|---|
| INSUFFICIENT_MODEL_SUPPORT | 49882 | 0.0% | 0.0% | 0.0% |
| NOT_EVALUATED | 6530 | 0.0% | 0.0% | 0.0% |
| PASS_NO_EDGE | 1181 | 35.6% | 0.0% | 61.7% |
| PASS_DATA_QUALITY | 317 | 13.2% | 0.0% | 48.3% |
| RECOMMENDED | 221 | 100.0% | 100.0% | 98.2% |
| BET_PLACED | 90 | 17.8% | 17.8% | 17.8% |
| PASS_PRICE_TOO_HIGH | 13 | 100.0% | 0.0% | 100.0% |
| RECOMMENDED_NOT_BET | 1 | 0.0% | 100.0% | 100.0% |

## Unresolved / conflicting metadata
Of **1750** fully `EVALUATED` records:
- **1348** are missing `confidence` entirely.
- **309** have no lineup evidence at all (`lineupConfirmationState=UNKNOWN`).

A non-zero count here is a genuine data gap in the upstream pipeline artifact for that
specific row, not a query defect -- see docs/EDGELAB_EVALUATION_METADATA.md.
