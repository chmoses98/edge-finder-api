# Full-Universe MLB Kalshi Calibration Audit — 2026-08-24

**Main SHA audited:** `266880d3f9bc03f8cf50b6594f63e7077fee3bf8`  
**Dates in archived universe:** 2026-08-01 to 2026-08-23 (23 dates)  
**Generated:** 2026-08-24T11:29:08Z

This is a READ-ONLY research audit. No production model probabilities, thresholds, fee logic, stake sizing, market-family qualification logic, or canonical bet records were changed. See the accompanying full_universe_calibration_audit.json for every number in machine-readable form; this file is a narrative summary.

## Phase 2 — Overall coverage

- Total archived unique MLB market instances: **97645**
- withRecognizedFamily: **97645** (100.0%)
- withGameDateLinkage: **97235** (99.6%)
- withPersistedModelProbability: **685** (0.7%)
- withExecutablePrice: **92240** (94.5%)
- settled: **82691** (84.7%)
- fullyCalibrationJoinable: **577** (0.6%)
- clvJoinable: **57751** (59.1%)

## Phase 2 — Coverage by market family

| family | archived | recognized | withProb | prob% | withPrice | price% | settled | settle% | joinable | joinable% | withClose | clv% | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hitter_hits_runs_rbis | 24190 | 24190 | 0 | 0.0% | 22726 | 94.0% | 20321 | 84.0% | 0 | 0.0% | 14275 | 59.0% | Probability adapter exists but is reachable ONLY via scripts... |
| hitter_total_bases | 19921 | 19921 | 0 | 0.0% | 18412 | 92.4% | 16779 | 84.2% | 0 | 0.0% | 11030 | 55.4% | Probability adapter exists but is reachable ONLY via scripts... |
| hitter_hits | 16566 | 16566 | 0 | 0.0% | 15755 | 95.1% | 13956 | 84.2% | 0 | 0.0% | 9885 | 59.7% | Probability adapter exists but is reachable ONLY via scripts... |
| hitter_rbis | 10667 | 10667 | 0 | 0.0% | 10027 | 94.0% | 8928 | 83.7% | 0 | 0.0% | 6392 | 59.9% | Probability adapter exists but is reachable ONLY via scripts... |
| pitcher_strikeouts | 4382 | 4382 | 0 | 0.0% | 4233 | 96.6% | 3781 | 86.3% | 0 | 0.0% | 2823 | 64.4% | Probability adapter exists but is reachable ONLY via scripts... |
| team_total | 4368 | 4368 | 402 | 9.2% | 4221 | 96.6% | 3780 | 86.5% | 335 | 7.7% | 2811 | 64.3% | Covered by the production 11-REQUIRED_MARKETS pipeline (scri... |
| hitter_stolen_bases | 4070 | 4070 | 0 | 0.0% | 3924 | 96.4% | 3487 | 85.7% | 0 | 0.0% | 2218 | 54.5% | UNSUPPORTED_FAMILY -- no probability projection adapter exis... |
| game_total | 3545 | 3545 | 0 | 0.0% | 3420 | 96.5% | 3066 | 86.5% | 0 | 0.0% | 2194 | 61.9% | Covered by the production 11-REQUIRED_MARKETS pipeline (scri... |
| winning_margin | 3419 | 3419 | 0 | 0.0% | 3259 | 95.3% | 2941 | 86.0% | 0 | 0.0% | 1990 | 58.2% | A research-only probability adapter exists, but scripts/buil... |
| inning_result | 2808 | 2808 | 57 | 2.0% | 2696 | 96.0% | 2430 | 86.5% | 53 | 1.9% | 1799 | 64.1% | Covered by the production 11-REQUIRED_MARKETS pipeline (scri... |
| inning_total | 2184 | 2184 | 0 | 0.0% | 2096 | 96.0% | 1897 | 86.9% | 0 | 0.0% | 1390 | 63.6% | UNSUPPORTED_FAMILY -- no probability projection adapter exis... |
| game_result | 624 | 624 | 48 | 7.7% | 603 | 96.6% | 540 | 86.5% | 37 | 5.9% | 394 | 63.1% | Covered by the production 11-REQUIRED_MARKETS pipeline (scri... |
| pitcher_outs | 589 | 589 | 0 | 0.0% | 569 | 96.6% | 514 | 87.3% | 0 | 0.0% | 359 | 61.0% | Probability adapter exists but is reachable ONLY via scripts... |
| first_inning_run | 312 | 312 | 178 | 57.0% | 299 | 95.8% | 271 | 86.9% | 152 | 48.7% | 191 | 61.2% | Covered by the production 11-REQUIRED_MARKETS pipeline (scri... |

## Phase 3 — Missing-probability reason codes

Total missing probability: **96960** of 97645 archived instances.

| reason | count |
|---|---|
| OTHER | 45708 |
| NO_PROJECTION_ADAPTER | 31921 |
| UNSUPPORTED_FAMILY | 9673 |
| TRUNCATION | 9658 |

## Phase 9 — RECOMMENDATION_SYNC gap (verified independently)

- Dates WITH a recommendations/ file (RECOMMENDATION_SYNC ran): ['2026-08-02', '2026-08-03', '2026-08-04', '2026-08-05', '2026-08-06', '2026-08-07', '2026-08-08', '2026-08-09', '2026-08-10', '2026-08-11', '2026-08-12', '2026-08-13', '2026-08-15', '2026-08-16']
- Dates WITHOUT a recommendations/ file (RECOMMENDATION_SYNC did not run): ['2026-08-01', '2026-08-14', '2026-08-17', '2026-08-18', '2026-08-19', '2026-08-20', '2026-08-21', '2026-08-22', '2026-08-23']

See full_universe_calibration_audit.json for Phase 5 (reliability buckets), Phase 6 (edge monotonicity), Phase 7/8 (family/expression comparisons), and every underlying number this summary references.
