# Near-close capture health

_generated 2026-10-14T11:41:31Z (schema `capture_health_v1`)_

**Requested** is what the schedule asked for. **Delivered** is what the
archive proves arrived. They are separate columns on purpose.

## Trailing 7 days

| | |
|---|---|
| dates with a slate | 6 |
| windows requested | 30 |
| windows delivered | 17 (**56.7%**) |
| windows missed (post-start) | 13 |
| markets archived | 3203 |
| TRUE_CLOSE-eligible | 640 (**20.0%**) |
| PRE_CLOSE only | 1905 |

## By date

| date | games | requested | delivered | missed | markets | TRUE_CLOSE | median s to start |
|---|---|---|---|---|---|---|---|
| 2026-10-13 | 1 | 3 | 1 (33.3%) | 2 | 293 | 0 | 4906.3 |
| 2026-10-12 | 2 | 6 | 4 (66.7%) | 2 | 637 | 298 | 1837.1 |
| 2026-10-11 | 1 | 3 | 0 (0.0%) | 3 | 326 | 0 | 5770.9 |
| 2026-10-10 | 1 | 3 | 1 (33.3%) | 2 | 329 | 0 | 1864.4 |
| 2026-10-09 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-10-08 | 1 | 3 | 3 (100.0%) | 0 | 336 | 328 | 547.2 |
| 2026-10-07 | 4 | 12 | 8 (66.7%) | 4 | 1282 | 14 | 5230.0 |

> windowsTargeted is what the schedule ASKED for; windowsDelivered is what the ARCHIVE proves arrived. They are reported separately on purpose: cron configuration is not coverage, and for 13 consecutive days the two differed by an order of magnitude without anything surfacing it.

