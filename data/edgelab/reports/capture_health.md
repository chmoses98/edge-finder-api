# Near-close capture health

_generated 2026-10-12T15:10:46Z (schema `capture_health_v1`)_
> **No near-close window was covered today.** Every CLV figure for this date rests on quotes taken well before first pitch.


**Requested** is what the schedule asked for. **Delivered** is what the
archive proves arrived. They are separate columns on purpose.

## Trailing 7 days

| | |
|---|---|
| dates with a slate | 6 |
| windows requested | 33 |
| windows delivered | 13 (**39.4%**) |
| windows missed (post-start) | 20 |
| markets archived | 3571 |
| TRUE_CLOSE-eligible | 342 (**9.6%**) |
| PRE_CLOSE only | 2237 |

## By date

| date | games | requested | delivered | missed | markets | TRUE_CLOSE | median s to start |
|---|---|---|---|---|---|---|---|
| 2026-10-11 | 1 | 3 | 0 (0.0%) | 3 | 326 | 0 | 5770.9 |
| 2026-10-10 | 1 | 3 | 1 (33.3%) | 2 | 329 | 0 | 1864.4 |
| 2026-10-09 | 0 | 0 | 0 (None%) | 0 | 0 | 0 | None |
| 2026-10-08 | 1 | 3 | 3 (100.0%) | 0 | 336 | 328 | 547.2 |
| 2026-10-07 | 4 | 12 | 8 (66.7%) | 4 | 1282 | 14 | 5230.0 |
| 2026-10-06 | 2 | 6 | 1 (16.7%) | 5 | 661 | 0 | 3756.0 |
| 2026-10-05 | 2 | 6 | 0 (0.0%) | 6 | 637 | 0 | 5375.6 |

> windowsTargeted is what the schedule ASKED for; windowsDelivered is what the ARCHIVE proves arrived. They are reported separately on purpose: cron configuration is not coverage, and for 13 consecutive days the two differed by an order of magnitude without anything surfacing it.

