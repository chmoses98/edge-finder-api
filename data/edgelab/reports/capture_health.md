# Near-close capture health

_generated 2026-09-26T12:51:30Z (schema `capture_health_v1`)_

**Requested** is what the schedule asked for. **Delivered** is what the
archive proves arrived. They are separate columns on purpose.

## Trailing 7 days

| | |
|---|---|
| dates with a slate | 7 |
| windows requested | 282 |
| windows delivered | 47 (**16.7%**) |
| windows missed (post-start) | 235 |
| markets archived | 30824 |
| TRUE_CLOSE-eligible | 5505 (**17.9%**) |
| PRE_CLOSE only | 11707 |

## By date

| date | games | requested | delivered | missed | markets | TRUE_CLOSE | median s to start |
|---|---|---|---|---|---|---|---|
| 2026-09-25 | 17 | 51 | 17 (33.3%) | 34 | 5179 | 2082 | 1273.8 |
| 2026-09-24 | 12 | 36 | 3 (8.3%) | 33 | 4038 | 678 | 8978.6 |
| 2026-09-23 | 16 | 48 | 16 (33.3%) | 32 | 5191 | 695 | 3103.9 |
| 2026-09-22 | 16 | 48 | 10 (20.8%) | 38 | 5068 | 1355 | 3519.0 |
| 2026-09-21 | 3 | 9 | 0 (0.0%) | 9 | 1050 | 0 | 7964.1 |
| 2026-09-20 | 15 | 45 | 1 (2.2%) | 44 | 5163 | 365 | 5351.2 |
| 2026-09-19 | 15 | 45 | 0 (0.0%) | 45 | 5135 | 330 | 5486.0 |

> windowsTargeted is what the schedule ASKED for; windowsDelivered is what the ARCHIVE proves arrived. They are reported separately on purpose: cron configuration is not coverage, and for 13 consecutive days the two differed by an order of magnitude without anything surfacing it.

