# EdgeLab Research Trustworthiness Summary

Date range: 2026-08-01 to 2026-08-13
Unique games: 241 | Unique market tickers: 49349 | Opportunity rows: 78633

## Market calibration (full universe, YES side)
- n=68230, avgImpliedProbability=0.3483, actualYesRate=0.2932, calibrationError=-0.0551, status=CALIBRATED

## Model calibration (causally-valid rows only)
- n=264, avgModelProbability=0.4024, actualWinRate=0.4545, calibrationError=0.0521, status=CALIBRATED

## Edge backtest (top buckets by n)
- edge <0%: n=264 (68 games), winRate=0.5076, roi=-0.0084, status=CALIBRATED
- edge 10+%: n=168 (67 games), winRate=0.5179, roi=0.0454, status=CALIBRATED
- edge 4-6%: n=34 (22 games), winRate=0.4706, roi=-0.0484, status=DESCRIPTIVE_ONLY
- edge 8-10%: n=29 (21 games), winRate=0.3793, roi=-0.1937, status=DESCRIPTIVE_ONLY
- edge 6-8%: n=21 (19 games), winRate=0.5238, roi=0.0282, status=DESCRIPTIVE_ONLY

## Strategy validation framework
- maturity: FRAMEWORK_ONLY_INSUFFICIENT_DATES
- FRAMEWORK ONLY -- no strategy has been optimized, tuned, or threshold-selected on any partition here, including HOLDOUT. Intended workflow: discover on DEVELOPMENT, test once on VALIDATION, freeze the rule, then evaluate on untouched HOLDOUT.

_All findings above are exploratory/descriptive. None constitute a validated betting edge until they survive out-of-sample HOLDOUT evaluation on a mature (30+ trading date) corpus. See research_data_quality for coverage caveats._
