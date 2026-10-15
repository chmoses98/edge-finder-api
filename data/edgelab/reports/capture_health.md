# Near-close capture health

_generated 2026-10-15T11:19:35Z (schema `capture_health_v1`)_

**Requested** is what the schedule asked for. **Delivered** is what the
archive proves arrived. They are separate columns on purpose.

## Trailing 7 days

| | |
|---|---|
| dates with a slate | 6 |
| windows requested | 21 |
| windows delivered | 12 (**57.1%**) |
| windows missed (post-start) | 9 |
| markets archived | 2238 |
| TRUE_CLOSE-eligible | 943 (**42.1%**) |
| PRE_CLOSE only | 1282 |

## By date

| date | games | requested | delivered | missed | markets | TRUE_CLOSE | median s to start |
|---|---|---|---|---|---|---|---|
| 2026-10-14 | 1 | 3 | 3 (100.0%) | 0 | 317 | 317 | 441.7 |
| 2026-10-13 | 1 | 3 | 1 (33.3%) | 2 | 293 | 0 | 4906.3 |
| 2026-10-12 | 2 | 6 | 4 (66.7%) | 2 | 637 | 298 | 1837.1 |
| 2026-10-11 | 1 | 3 | 0 (0.0%) | 3 | 326 | 0 | 5770.9 |
| 2026-10-10 | 1 | 3 | 1 (33.3%) | 2 | 329 | 0 | 1864.4 |
| 2026-10-09 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-10-08 | 1 | 3 | 3 (100.0%) | 0 | 336 | 328 | 547.2 |

> windowsTargeted is what the schedule ASKED for; windowsDelivered is what the ARCHIVE proves arrived. They are reported separately on purpose: cron configuration is not coverage, and for 13 consecutive days the two differed by an order of magnitude without anything surfacing it.

