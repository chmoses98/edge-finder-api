# Near-close capture health

_generated 2026-10-22T13:39:35Z (schema `capture_health_v1`)_

**Requested** is what the schedule asked for. **Delivered** is what the
archive proves arrived. They are separate columns on purpose.

## Trailing 7 days

| | |
|---|---|
| dates with a slate | 5 |
| windows requested | 18 |
| windows delivered | 4 (**22.2%**) |
| windows missed (post-start) | 14 |
| markets archived | 1453 |
| TRUE_CLOSE-eligible | 453 (**31.2%**) |
| PRE_CLOSE only | 663 |

## By date

| date | games | requested | delivered | missed | markets | TRUE_CLOSE | median s to start |
|---|---|---|---|---|---|---|---|
| 2026-10-21 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-10-20 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-10-19 | 1 | 3 | 0 (0.0%) | 3 | 317 | 0 | 3394.9 |
| 2026-10-18 | 1 | 3 | 0 (0.0%) | 3 | 307 | 0 | 5494.9 |
| 2026-10-17 | 1 | 3 | 1 (33.3%) | 2 | 42 | 0 | 2035.9 |
| 2026-10-16 | 1 | 3 | 2 (66.7%) | 1 | 149 | 149 | 1157.4 |
| 2026-10-15 | 2 | 6 | 1 (16.7%) | 5 | 638 | 304 | 1750.1 |

> windowsTargeted is what the schedule ASKED for; windowsDelivered is what the ARCHIVE proves arrived. They are reported separately on purpose: cron configuration is not coverage, and for 13 consecutive days the two differed by an order of magnitude without anything surfacing it.

