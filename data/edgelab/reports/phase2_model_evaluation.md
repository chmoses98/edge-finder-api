# EdgeLab Phase 2 Milestone 3 — Model Evaluation Report

_Generated 2026-08-01T14:30:35Z_

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
| thesisTags | 0 (0.0%) |
| linked to Recommendation | 275 (100.0%) |
| linked to PlacedBet | 7 (2.5%) |
| linked to Settlement | n/a (entity unavailable) |

## Breakdown by canonical market family
| Canonical family | n | % w/ prob | % w/ edge | % w/ confidence | % w/ tags |
|---|---|---|---|---|---|
| UNKNOWN | 146 | 0.0% | 0.0% | 0.0% | 0.0% |
| winning_margin | 40 | 0.0% | 0.0% | 0.0% | 0.0% |
| team_total | 38 | 100.0% | 100.0% | 10.5% | 0.0% |
| game_total | 19 | 0.0% | 0.0% | 0.0% | 0.0% |
| first_inning_run | 16 | 100.0% | 100.0% | 100.0% | 0.0% |
| game_result | 8 | 100.0% | 100.0% | 100.0% | 0.0% |
| inning_result | 8 | 100.0% | 100.0% | 100.0% | 0.0% |

## Breakdown by model version / source
| Model version | Model source | n |
|---|---|---|
| UNKNOWN | scripts/build_market_ledger.py | 275 |
