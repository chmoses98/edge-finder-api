# Near-close capture health

_generated 2026-10-04T12:13:45Z (schema `capture_health_v1`)_

**Requested** is what the schedule asked for. **Delivered** is what the
archive proves arrived. They are separate columns on purpose.

## Trailing 7 days

| | |
|---|---|
| dates with a slate | 5 |
| windows requested | 84 |
| windows delivered | 12 (**14.3%**) |
| windows missed (post-start) | 72 |
| markets archived | 9096 |
| TRUE_CLOSE-eligible | 1166 (**12.8%**) |
| PRE_CLOSE only | 1652 |

## By date

| date | games | requested | delivered | missed | markets | TRUE_CLOSE | median s to start |
|---|---|---|---|---|---|---|---|
| 2026-10-03 | 4 | 12 | 6 (50.0%) | 6 | 1297 | 658 | 677.4 |
| 2026-10-02 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-10-01 | 1 | 3 | 0 (0.0%) | 3 | 342 | 0 | 10847.2 |
| 2026-09-30 | 4 | 12 | 3 (25.0%) | 9 | 1192 | 196 | 5432.1 |
| 2026-09-29 | 4 | 12 | 2 (16.7%) | 10 | 1307 | 312 | 6109.8 |
| 2026-09-28 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-09-27 | 15 | 45 | 1 (2.2%) | 44 | 4958 | 0 | None |

> windowsTargeted is what the schedule ASKED for; windowsDelivered is what the ARCHIVE proves arrived. They are reported separately on purpose: cron configuration is not coverage, and for 13 consecutive days the two differed by an order of magnitude without anything surfacing it.

