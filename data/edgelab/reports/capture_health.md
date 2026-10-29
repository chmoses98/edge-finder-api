# Near-close capture health

_generated 2026-10-29T12:55:03Z (schema `capture_health_v1`)_

**Requested** is what the schedule asked for. **Delivered** is what the
archive proves arrived. They are separate columns on purpose.

## Trailing 7 days

| | |
|---|---|
| dates with a slate | 5 |
| windows requested | 15 |
| windows delivered | 12 (**80.0%**) |
| windows missed (post-start) | 3 |
| markets archived | 1668 |
| TRUE_CLOSE-eligible | 974 (**58.4%**) |
| PRE_CLOSE only | 646 |

## By date

| date | games | requested | delivered | missed | markets | TRUE_CLOSE | median s to start |
|---|---|---|---|---|---|---|---|
| 2026-10-28 | 1 | 3 | 3 (100.0%) | 0 | 328 | 326 | 57.3 |
| 2026-10-27 | 1 | 3 | 0 (0.0%) | 3 | 329 | 0 | 9223.2 |
| 2026-10-26 | 1 | 3 | 3 (100.0%) | 0 | 344 | 0 | 2944.1 |
| 2026-10-25 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-10-24 | 1 | 3 | 3 (100.0%) | 0 | 337 | 329 | 752.5 |
| 2026-10-23 | 1 | 3 | 3 (100.0%) | 0 | 330 | 319 | 350.1 |
| 2026-10-22 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |

> windowsTargeted is what the schedule ASKED for; windowsDelivered is what the ARCHIVE proves arrived. They are reported separately on purpose: cron configuration is not coverage, and for 13 consecutive days the two differed by an order of magnitude without anything surfacing it.

