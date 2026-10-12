# MLB postseason pitcher box scores: sports-source coverage report (W2 Phase 3)

**Status: research only.** This repairs the `BLOCKED_DATA` gap recorded in `PITCHER_WORKLOAD_SOURCE_AUDIT.md`
§3: no committed sports-source postseason pitcher lines. It changes no export, settlement, staking or model
code.

Every number below comes from `docs/research/pitcher_postseason_coverage.json`, written by
`python3 scripts/research/mlb_postseason_coverage_report.py` on the data committed by the backfill runs.

## Source and layout

**Fetcher.** `scripts/research/mlb_postseason_boxscore_backfill.py`.

- It runs on GitHub Actions, because the sandbox blocks `statsapi.mlb.com`.
- The workflow is `.github/workflows/research-mlb-postseason-boxscore-backfill.yml`. It runs only on PR
  branches and never targets main.

**Runs.**

| Run | Commit | Contents |
|---|---|---|
| 38103797521 | `f2454025` | The 2022–2026 backfill |
| 38108418319 | `07791fb5` | Re-run after 2026 ALDS Game 5 went final; added one game |

**Schedule.** `data/research_cache/starter_workload/<season>/postseason_schedule.json` is a snapshot of the
official StatsAPI schedule (`/api/v1/schedule?gameType=F,D,L,W`), with `fetched_at`, the source URL and the
payload sha256.

**Pitcher lines.** `data/research_cache/starter_workload/<season>/postseason_boxscores.jsonl.gz` holds one record
per **final** game. Each pitcher line has:

- `outs`, `battersFaced`, `strikeOuts`, `numberOfPitches`, `inningsPitched`;
- `baseOnBalls`, `hits`, `runs`, `earnedRuns`;
- `orderIndex` (0 = starter), `playerId` (MLBAM id) and pitcher team.

These come from the unchanged `extract_pitcher_lines`, so the fields match the regular-season cache.

**Per-game metadata:**

- `gamePk`, `gameType` (F, D, L or W), series and series game number;
- `scheduledStart`, `firstPitch`, `gameEnd`;
- `fetched_at` and `source_url`;
- a per-endpoint `sources[]` list with URL, `fetched_at` and payload sha256.

**Endpoints.** Two endpoints are fetched per game: `v1.1/game/<pk>/feed/live` (primary) and
`v1/game/<pk>/boxscore` (cross-check).

**Isolation from the frozen models.** The files sit outside the frozen challenger's `SOURCE_GLOBS` and outside
the champion's loaders, both of which read `*/boxscores.jsonl.gz` and `bullpen_backtest/*/schedules`. A test
asserts this. The #283 inputs are therefore byte-identical.

**Idempotent.** Already-cached gamePks are never re-fetched or rewritten. The re-run left the first 24 2026
records byte-identical once decompressed.

## 1. Completeness: every final game in the official schedule is cached

| Season | Schedule games | Final | Cached | Missing | F | D | L | W | Series (games each) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 2022 | 40 | 40 | 40 | 0 | 9 | 16 | 9 | 6 | WC 2,2,2,3 · DS 3,4,4,5 · CS 4,5 · WS 6 |
| 2023 | 41 | 41 | 41 | 0 | 8 | 14 | 14 | 5 | WC 2,2,2,2 · DS 3,3,4,4 · CS 7,7 · WS 5 |
| 2024 | 43 | 43 | 43 | 0 | 9 | 18 | 11 | 5 | WC 2,2,2,3 · DS 4,4,5,5 · CS 5,6 · WS 5 |
| 2025 | 47 | 47 | 47 | 0 | 11 | 18 | 11 | 7 | WC 2,3,3,3 · DS 4,4,5,5 · CS 4,7 · WS 7 |
| 2026 | 46 | 25 | 25 | 0 | 9 | 16 | 0 | 0 | WC 2,2,2,3 · DS 3,4,4,5 · LCS/WS not started |

**2026 status.** 2026 runs through the latest completed game, 2026 ALDS Game 5 (gamePk 849831, officialDate
2026-10-10). The 21 non-final schedule entries are the not-yet-played LCS and World Series games, including
if-necessary games. They are listed in the JSON and excluded.

**Line integrity:**

- Every cached side (50/50 in 2026, 392/392 overall) has a starter line (`orderIndex` 0) with outs, BF, K and
  pitches all present.
- No side records fewer than 24 team outs.
- The feed/live and boxscore endpoints agree field by field on every pitcher line in every game (196/196 games,
  1,966 pitcher lines).

`complete_all_seasons: true`. **Coverage is complete.**

## 2. Identity: MLBAM ids against the regular-season caches

| Season | Pitcher lines | Id found in that season's regular-season cache | Name mismatches |
|---|---:|---:|---:|
| 2022 | 381 | 381 | 0 |
| 2023 | 412 | 412 | 0 |
| 2024 | 451 | 451 | 0 |
| 2025 | 476 | 476 | 0 |
| 2026 | 246 | 244 (1 found only in Statcast logs) | 0 |

The 2026 exceptions are not identity errors:

- **Joe Musgrove (605397), 2 lines.** He is absent from the 2026 regular-season caches. The box-score cache ends
  2026-08-26, and the Statcast logs cover only part of 08-11 to 09-27. His id is the same MLBAM id found in the
  2022, 2023 and 2024 regular-season caches.
- **5 lines from 3 pitchers (663460, 668941, 670167).** Their postseason team differs from their last
  regular-season box appearance (2026-05-14, 07-12 and 08-25). That is consistent with mid-season roster moves.
  The id and name are unchanged.

## 3. Settlement-evidence cross-check (a check only, never an input)

The 40 postseason pitcher-games whose settled Kalshi KS/OUTS markets carry `settlementEvidence` values were
joined on (gamePk, playerId):

| Statistic | Settlement observations | Found in cache | Agree |
|---|---:|---:|---:|
| Outs | 39 | 39 | **39 (100%)** |
| Strikeouts | 40 | 40 | **40 (100%)** |

All 40 are starters in the cache. There are no disagreements. The sports-source cache covers all 50 2026
postseason starts, not just the 40 market-selected ones, and it adds BF and pitch counts, which the settlement
evidence never carried.

## 4. Timestamps

- `fetched_at` is after `gameEnd` for 196/196 records. The fetcher refuses to cache a game whose feed was fetched
  before its last play ended.
- `gameEnd` is after `firstPitch` for 196/196. First pitch to last play took 129 to 400 minutes, median 185.
- Every endpoint source carries a URL and `fetched_at` (392/392).
- The fetch lag after the game end runs from 0.7 h for ALDS Game 5 to about 4 years for 2022. This is a backfill.

**As-of rule for any use of this cache.** A cached postseason line may enter a projection only if its
`gameEnd` < the projection cutoff `as_of`. `fetched_at` is the archive time of this backfill, not the time the
line became available, so it must not be used as the availability time. A prospective collector would record
its own fetch time.

## 5. What this unblocks, and what it does not

- **Unblocked:** a sports-only 2026 postseason evaluation with full-population, sports-source labels (outs, BF,
  K), and a sports-only postseason regime term fitted on 2022–2025. Both are preregistered in
  `PITCHER_POSTSEASON_PREREG.md`.
- **Not unblocked:**
  - postseason bullpen usage and lineup status as of each game (not fetched);
  - manager identity;
  - `fetched_at` as a true availability time for the historical seasons.
