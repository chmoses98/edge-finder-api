# Near-close capture health

_generated 2026-10-01T15:17:38Z (schema `capture_health_v1`)_

**Requested** is what the schedule asked for. **Delivered** is what the
archive proves arrived. They are separate columns on purpose.

## Trailing 7 days

| | |
|---|---|
| dates with a slate | 6 |
| windows requested | 195 |
| windows delivered | 45 (**23.1%**) |
| windows missed (post-start) | 150 |
| markets archived | 21181 |
| TRUE_CLOSE-eligible | 5613 (**26.5%**) |
| PRE_CLOSE only | 5012 |

## By date

| date | games | requested | delivered | missed | markets | TRUE_CLOSE | median s to start |
|---|---|---|---|---|---|---|---|
| 2026-09-30 | 4 | 12 | 3 (25.0%) | 9 | 1192 | 196 | 5432.1 |
| 2026-09-29 | 4 | 12 | 2 (16.7%) | 10 | 1307 | 312 | 6109.8 |
| 2026-09-28 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-09-27 | 15 | 45 | 1 (2.2%) | 44 | 4958 | 0 | None |
| 2026-09-26 | 13 | 39 | 19 (48.7%) | 20 | 4507 | 2345 | 1475.5 |
| 2026-09-25 | 17 | 51 | 17 (33.3%) | 34 | 5179 | 2082 | 1273.8 |
| 2026-09-24 | 12 | 36 | 3 (8.3%) | 33 | 4038 | 678 | 8978.6 |

> windowsTargeted is what the schedule ASKED for; windowsDelivered is what the ARCHIVE proves arrived. They are reported separately on purpose: cron configuration is not coverage, and for 13 consecutive days the two differed by an order of magnitude without anything surfacing it.

