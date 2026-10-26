# Near-close capture health

_generated 2026-10-26T12:46:31Z (schema `capture_health_v1`)_

**Requested** is what the schedule asked for. **Delivered** is what the
archive proves arrived. They are separate columns on purpose.

## Trailing 7 days

| | |
|---|---|
| dates with a slate | 3 |
| windows requested | 9 |
| windows delivered | 6 (**66.7%**) |
| windows missed (post-start) | 3 |
| markets archived | 984 |
| TRUE_CLOSE-eligible | 648 (**65.9%**) |
| PRE_CLOSE only | 314 |

## By date

| date | games | requested | delivered | missed | markets | TRUE_CLOSE | median s to start |
|---|---|---|---|---|---|---|---|
| 2026-10-25 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-10-24 | 1 | 3 | 3 (100.0%) | 0 | 337 | 329 | 752.5 |
| 2026-10-23 | 1 | 3 | 3 (100.0%) | 0 | 330 | 319 | 350.1 |
| 2026-10-22 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-10-21 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-10-20 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-10-19 | 1 | 3 | 0 (0.0%) | 3 | 317 | 0 | 3394.9 |

> windowsTargeted is what the schedule ASKED for; windowsDelivered is what the ARCHIVE proves arrived. They are reported separately on purpose: cron configuration is not coverage, and for 13 consecutive days the two differed by an order of magnitude without anything surfacing it.

