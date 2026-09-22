# Near-close capture health

_generated 2026-09-21T22:31:40Z (schema `capture_health_v1`)_

**Requested** is what the schedule asked for. **Delivered** is what the
archive proves arrived. They are separate columns on purpose.

## Trailing 7 days

| | |
|---|---|
| dates with a slate | 7 |
| windows requested | 282 |
| windows delivered | 20 (**7.1%**) |
| windows missed (post-start) | 262 |
| markets archived | 31544 |
| TRUE_CLOSE-eligible | 5649 (**17.9%**) |
| PRE_CLOSE only | 17199 |

## By date

| date | games | requested | delivered | missed | markets | TRUE_CLOSE | median s to start |
|---|---|---|---|---|---|---|---|
| 2026-09-20 | 15 | 45 | 1 (2.2%) | 44 | 5163 | 365 | 5351.2 |
| 2026-09-19 | 15 | 45 | 0 (0.0%) | 45 | 5135 | 330 | 5486.0 |
| 2026-09-18 | 15 | 45 | 0 (0.0%) | 45 | 5179 | 1024 | 5570.9 |
| 2026-09-17 | 9 | 27 | 2 (7.4%) | 25 | 2641 | 986 | 1615.7 |
| 2026-09-16 | 15 | 45 | 7 (15.6%) | 38 | 4966 | 1248 | 2958.7 |
| 2026-09-15 | 15 | 45 | 4 (8.9%) | 41 | 5135 | 932 | 3285.7 |
| 2026-09-14 | 10 | 30 | 6 (20.0%) | 24 | 3325 | 764 | 8255.0 |

> windowsTargeted is what the schedule ASKED for; windowsDelivered is what the ARCHIVE proves arrived. They are reported separately on purpose: cron configuration is not coverage, and for 13 consecutive days the two differed by an order of magnitude without anything surfacing it.

