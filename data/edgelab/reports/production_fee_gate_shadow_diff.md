# Production Fee-Aware Net EV -- Shadow Behavior Diff

**DESCRIPTIVE / IN-SAMPLE BEHAVIOR AUDIT** -- this compares what the OLD (fee-blind) and NEW (fee-aware) production decision logic would have done on the causal historical corpus. It is not proof the new gate is superior out of sample.

- Causal opportunities audited: **528**
- Old qualifiers: **256**
- New qualifiers: **223**
- Retained: **192**
- Rejected by fees: **33**
- Tier downgraded (still qualifies, lower tier): **31**
- Unchanged (never qualified either way): **272**
- Average Bet Up To reduction: **1.597 cents**

Chronological split maturity: **FRAMEWORK_ONLY_INSUFFICIENT_DATES** (14 distinct dates).
The strategy-validation dataset is still immature for a real DEV/VALIDATION/HOLDOUT split (fewer than lib.edgelab.research_splits.MIN_DATES_FOR_MATURE_SPLIT dates) -- the split is computed and labeled honestly above, not skipped, but should not be treated as a real out-of-sample validation yet.

## Old vs. new qualifying-set descriptive outcomes

| Metric | Old qualifying set | New qualifying set |
|---|---:|---:|
| n | 256 | 223 |
| Independent games | 68 | 68 |
| Gross ROI | 0.0022 | -0.0024 |
| Fee-only ROI | -0.0331 | -0.0379 |
| Realistic-execution ROI | -0.0324 | -0.0369 |
| YES / NO split | 112/144 | 79/144 |
