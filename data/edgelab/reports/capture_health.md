# Near-close capture health

_generated 2026-10-07T13:10:49Z (schema `capture_health_v1`)_

**Requested** is what the schedule asked for. **Delivered** is what the
archive proves arrived. They are separate columns on purpose.

## Trailing 7 days

| | |
|---|---|
| dates with a slate | 6 |
| windows requested | 45 |
| windows delivered | 12 (**26.7%**) |
| windows missed (post-start) | 33 |
| markets archived | 4788 |
| TRUE_CLOSE-eligible | 1190 (**24.9%**) |
| PRE_CLOSE only | 2429 |

## By date

| date | games | requested | delivered | missed | markets | TRUE_CLOSE | median s to start |
|---|---|---|---|---|---|---|---|
| 2026-10-06 | 2 | 6 | 1 (16.7%) | 5 | 661 | 0 | 3756.0 |
| 2026-10-05 | 2 | 6 | 0 (0.0%) | 6 | 637 | 0 | 5375.6 |
| 2026-10-04 | 2 | 6 | 2 (33.3%) | 4 | 659 | 336 | 1777.2 |
| 2026-10-03 | 4 | 12 | 6 (50.0%) | 6 | 1297 | 658 | 677.4 |
| 2026-10-02 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-10-01 | 1 | 3 | 0 (0.0%) | 3 | 342 | 0 | 10847.2 |
| 2026-09-30 | 4 | 12 | 3 (25.0%) | 9 | 1192 | 196 | 5432.1 |

> windowsTargeted is what the schedule ASKED for; windowsDelivered is what the ARCHIVE proves arrived. They are reported separately on purpose: cron configuration is not coverage, and for 13 consecutive days the two differed by an order of magnitude without anything surfacing it.

