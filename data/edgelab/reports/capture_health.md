# Near-close capture health

_generated 2026-09-24T13:26:24Z (schema `capture_health_v1`)_

**Requested** is what the schedule asked for. **Delivered** is what the
archive proves arrived. They are separate columns on purpose.

## Trailing 7 days

| | |
|---|---|
| dates with a slate | 7 |
| windows requested | 267 |
| windows delivered | 29 (**10.9%**) |
| windows missed (post-start) | 238 |
| markets archived | 29427 |
| TRUE_CLOSE-eligible | 4755 (**16.2%**) |
| PRE_CLOSE only | 13619 |

## By date

| date | games | requested | delivered | missed | markets | TRUE_CLOSE | median s to start |
|---|---|---|---|---|---|---|---|
| 2026-09-23 | 16 | 48 | 16 (33.3%) | 32 | 5191 | 695 | 3103.9 |
| 2026-09-22 | 16 | 48 | 10 (20.8%) | 38 | 5068 | 1355 | 3519.0 |
| 2026-09-21 | 3 | 9 | 0 (0.0%) | 9 | 1050 | 0 | 7964.1 |
| 2026-09-20 | 15 | 45 | 1 (2.2%) | 44 | 5163 | 365 | 5351.2 |
| 2026-09-19 | 15 | 45 | 0 (0.0%) | 45 | 5135 | 330 | 5486.0 |
| 2026-09-18 | 15 | 45 | 0 (0.0%) | 45 | 5179 | 1024 | 5570.9 |
| 2026-09-17 | 9 | 27 | 2 (7.4%) | 25 | 2641 | 986 | 1615.7 |

> windowsTargeted is what the schedule ASKED for; windowsDelivered is what the ARCHIVE proves arrived. They are reported separately on purpose: cron configuration is not coverage, and for 13 consecutive days the two differed by an order of magnitude without anything surfacing it.

