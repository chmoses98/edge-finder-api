# Near-close capture health

_generated 2026-10-02T14:37:32Z (schema `capture_health_v1`)_
> **No near-close window was covered today.** Every CLV figure for this date rests on quotes taken well before first pitch.


**Requested** is what the schedule asked for. **Delivered** is what the
archive proves arrived. They are separate columns on purpose.

## Trailing 7 days

| | |
|---|---|
| dates with a slate | 6 |
| windows requested | 162 |
| windows delivered | 42 (**25.9%**) |
| windows missed (post-start) | 120 |
| markets archived | 17485 |
| TRUE_CLOSE-eligible | 4935 (**28.2%**) |
| PRE_CLOSE only | 4057 |

## By date

| date | games | requested | delivered | missed | markets | TRUE_CLOSE | median s to start |
|---|---|---|---|---|---|---|---|
| 2026-10-01 | 1 | 3 | 0 (0.0%) | 3 | 342 | 0 | 10847.2 |
| 2026-09-30 | 4 | 12 | 3 (25.0%) | 9 | 1192 | 196 | 5432.1 |
| 2026-09-29 | 4 | 12 | 2 (16.7%) | 10 | 1307 | 312 | 6109.8 |
| 2026-09-28 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-09-27 | 15 | 45 | 1 (2.2%) | 44 | 4958 | 0 | None |
| 2026-09-26 | 13 | 39 | 19 (48.7%) | 20 | 4507 | 2345 | 1475.5 |
| 2026-09-25 | 17 | 51 | 17 (33.3%) | 34 | 5179 | 2082 | 1273.8 |

> windowsTargeted is what the schedule ASKED for; windowsDelivered is what the ARCHIVE proves arrived. They are reported separately on purpose: cron configuration is not coverage, and for 13 consecutive days the two differed by an order of magnitude without anything surfacing it.

