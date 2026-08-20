# EdgeLab Research Trustworthiness Summary

Date range: 2026-08-01 to 2026-08-20
Unique games: 353 | Unique market tickers: 80526 | Opportunity rows: 138158

## Market calibration (full universe, YES side)
- n=120765, avgImpliedProbability=0.3522, actualYesRate=0.2882, calibrationError=-0.0641, status=CALIBRATED

## Model calibration (causally-valid rows only)
- n=404, avgModelProbability=0.4081, actualWinRate=0.4703, calibrationError=0.0622, status=CALIBRATED

## Edge backtest (top buckets by n)
- edge <0%: n=404 (104 games), winRate=0.495, roi=-0.0365, status=CALIBRATED
- edge 10+%: n=263 (102 games), winRate=0.5361, roi=0.0947, status=CALIBRATED
- edge 6-8%: n=44 (35 games), winRate=0.4091, roi=-0.1993, status=DESCRIPTIVE_ONLY
- edge 8-10%: n=41 (30 games), winRate=0.3659, roi=-0.2171, status=DESCRIPTIVE_ONLY
- edge 4-6%: n=40 (26 games), winRate=0.525, roi=0.052, status=DESCRIPTIVE_ONLY

## Strategy validation framework
- maturity: FRAMEWORK_ONLY_INSUFFICIENT_DATES
- FRAMEWORK ONLY -- no strategy has been optimized, tuned, or threshold-selected on any partition here, including HOLDOUT. Intended workflow: discover on DEVELOPMENT, test once on VALIDATION, freeze the rule, then evaluate on untouched HOLDOUT.

_All findings above are exploratory/descriptive. None constitute a validated betting edge until they survive out-of-sample HOLDOUT evaluation on a mature (30+ trading date) corpus. See research_data_quality for coverage caveats._
