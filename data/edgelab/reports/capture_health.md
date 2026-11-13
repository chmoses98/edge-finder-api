# Near-close capture health

_generated 2026-11-13T14:44:22Z (schema `capture_health_v1`)_

**Requested** is what the schedule asked for. **Delivered** is what the
archive proves arrived. They are separate columns on purpose.

## Trailing 7 days

| | |
|---|---|
| dates with a slate | 0 |
| windows requested | 0 |
| windows delivered | 0 (**None%**) |
| windows missed (post-start) | 0 |
| markets archived | 0 |
| TRUE_CLOSE-eligible | 0 (**None%**) |
| PRE_CLOSE only | 0 |

## By date

| date | games | requested | delivered | missed | markets | TRUE_CLOSE | median s to start |
|---|---|---|---|---|---|---|---|
| 2026-11-12 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-11-11 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-11-10 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-11-09 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-11-08 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-11-07 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-11-06 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |

> windowsTargeted is what the schedule ASKED for; windowsDelivered is what the ARCHIVE proves arrived. They are reported separately on purpose: cron configuration is not coverage, and for 13 consecutive days the two differed by an order of magnitude without anything surfacing it.

