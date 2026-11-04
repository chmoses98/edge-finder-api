# Near-close capture health

_generated 2026-11-04T14:19:27Z (schema `capture_health_v1`)_

**Requested** is what the schedule asked for. **Delivered** is what the
archive proves arrived. They are separate columns on purpose.

## Trailing 7 days

| | |
|---|---|
| dates with a slate | 1 |
| windows requested | 3 |
| windows delivered | 3 (**100.0%**) |
| windows missed (post-start) | 0 |
| markets archived | 328 |
| TRUE_CLOSE-eligible | 326 (**99.4%**) |
| PRE_CLOSE only | 0 |

## By date

| date | games | requested | delivered | missed | markets | TRUE_CLOSE | median s to start |
|---|---|---|---|---|---|---|---|
| 2026-11-03 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-11-02 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-11-01 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-10-31 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-10-30 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-10-29 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-10-28 | 1 | 3 | 3 (100.0%) | 0 | 328 | 326 | 57.3 |

> windowsTargeted is what the schedule ASKED for; windowsDelivered is what the ARCHIVE proves arrived. They are reported separately on purpose: cron configuration is not coverage, and for 13 consecutive days the two differed by an order of magnitude without anything surfacing it.

