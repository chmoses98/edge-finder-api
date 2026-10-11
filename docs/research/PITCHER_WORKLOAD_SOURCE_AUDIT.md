# Pitcher workload: sports-source audit (W1-D, 2026-10-10)

**Status: research only.** Nothing here changes production, the app export, settlement, staking or the champion
`lib/research/pitcher_prop_projection.py`, which stays byte-for-byte unchanged.

**Reproduce:** `python3 scripts/research/mlb_pitcher_workload_source_audit.py`. It writes
`docs/research/pitcher_workload_source_audit.json`. Every number below comes from that file. The audit read the
committed repository tree at `origin/main` 29abfc8.

**Network:** `statsapi.mlb.com` is blocked by the environment's network policy. No box score could be fetched
for this audit. Any gap below that needs the StatsAPI is reported as a gap and is not estimated.

## 1. Committed sports datasets

| Dataset (under `data/`) | What it carries | Coverage | Timestamps |
|---|---|---|---|
| `research_cache/starter_workload/<yr>/boxscores.jsonl.gz` | Box-score pitching line for **every** pitcher in a game, in appearance order: `outs`, `battersFaced`, `strikeOuts`, `numberOfPitches`, `baseOnBalls`, `hits`, `runs`, `orderIndex` | Regular season only, 2022 to 2026-08-26 | No fetch time is stored. A line is final after the game, so the challenger treats it as observable at the scheduled first pitch + 5 h. Only games dated before the projected game are used. |
| `research_cache/bullpen_backtest/<yr>/schedules/*.json` | StatsAPI schedule: `officialDate`, scheduled first pitch `gameDate` (UTC), `gameType`, teams, doubleheader number, status | Regular season only (`gameType` = R for all 12,150 entries), 2022 to 2026-09-27 | The first-pitch time is the forecast "kickoff". 2026 statuses stop updating after about 08-26, which affects status but not schedule identity. |
| `research_cache/bullpen_backtest/<yr>/boxscores.jsonl.gz` | Same games as `starter_workload`, but without BF and K | Same as above | Not used, because `starter_workload` is a superset. |
| `research_cache/batting_backtest/<yr>/boxscores.jsonl.gz` | Team batting totals per game (PA, K, BB, ...) | Regular season 2022 to 2026-08-26 | Not used. Opponent K/PA and non-out rates come from the pitching lines. |
| `statcast_raw/games/<gamePk>.jsonl` (+ `index/`) | Statcast pitch log: every pitch with `atBatIndex`, `pitcherId`, `outsWhenUp`, `events` | 643 games, 2026-08-11 to 2026-09-27, **regular season only** | `gameDate` only. |

### Regular-season completeness (starter lines usable as history or outcome)

| Season | Scheduled R games | Games with pitching lines | Starter lines | Missing BF | Missing pitches | Source |
|---|---:|---:|---:|---:|---:|---|
| 2022 | 2,430 | 2,430 | 4,860 | 4 | 3 | box score |
| 2023 | 2,430 | 2,430 | 4,860 | 0 | 0 | box score |
| 2024 | 2,430 (1 postponed) | 2,429 | 4,858 | 3 | 1 | box score |
| 2025 | 2,430 | 2,430 | 4,860 | 1 | 1 | box score |
| 2026 | 2,430 | 2,429 | 4,858 | 1 | 1 | box score to 08-26 (4,008 team-games); Statcast-derived 08-27 to 09-27 (850 team-games) |

No starter is missing outs or strikeouts in any season.

**Statcast-derived lines vs box scores.** On the overlap window (2026-08-11 to 08-26) there are 436 starts. The
starter identity matches on all 436 and K matches on all 436. Outs match on 434; the other 2 are +1. BF matches
on 413; 22 are +1 and 1 is −1. Pitch count matches on 414; 18 are +1 and 4 are +2. So 08-27 to 09-27 lines carry
a small, documented upward BF/pitch noise of about 5% of starts at +1. This affects 2026 late-season history and
outcomes only.

## 2. Field-by-field availability (as-of, sports sources only)

| Field | Regular season 2022–2026 | 2026 postseason |
|---|---|---|
| Official outs | Box score / Statcast reconstruction | **Not in any committed sports dataset** |
| Batters faced | Box score / Statcast | **Not committed anywhere**, not even in the settlements |
| Strikeouts | Box score / Statcast | **Not in any committed sports dataset** |
| Pitch counts | Box score `numberOfPitches` / Statcast pitch rows | **Not committed** for starters |
| Start time | Schedule `gameDate` (UTC) for all 12,150 regular-season games | `data/pipeline/<date>/normalized_slate.json` `startTime` (StatsAPI schedule) for 25 postseason games. The file also embeds odds, which the challenger never reads. |
| Innings | outs / 3 | Not available |
| Previous 12 outings | Built from the above, strictly before the game date | Only **regular-season** outings. Postseason outings are missing. |
| Rest days | From the dates of previous starts | Only measurable back to the last regular-season start. Postseason starts in between are unknown. |
| Opponent K rate | Season-to-date opponent K/PA from the pitching lines | Regular-season-to-date only. The confirmed-lineup K% in pipeline snapshots exists only for 2026-07-30 to 10-10 and is not validated. |
| Manager hooks | **No manager identity or hook event in any dataset.** The only proxy is team-season starter pitch/BF tendency, built from box scores. | Not available |
| Bullpen rest | Relievers' pitches per team-game from box scores / Statcast (previous 1–3 days) | `handicap_runtime/<date>/games/*.json` `bullpen.recentUsage` covers 13 of 25 postseason games. Those files exist only for games that had Kalshi markets, so the population is market-selected, and they are not used. |
| Lineup confirmed / unconfirmed | **No lineup status or lineup timestamps for 2022–2025.** Pipeline snapshots cover 65 dates, 2026-07-30 to 2026-10-10. | Same pipeline snapshots, with all 25 games flagged confirmed in the last write. That is the post-game state, so it is not usable as-of. |

## 3. The champion's postseason path reads Kalshi settlements as observations

Every place where the champion's postseason projections take values from Kalshi settlement records:

1. **`scripts/research/mlb_pitcher_prop_calibration.py`**
   - `load_settled_props()` reads `data/edgelab/settlements/*.jsonl(.gz)` (pitcher_outs / pitcher_strikeouts,
     `settlementEvidence.actualValue`).
   - `postseason_starts()` converts those rows into postseason "starts" (outs and K, **no BF**).
   - `main()` appends those starts (`< asof`) to the pitcher history (`all_starts`). They then enter
     `expected_outs` for any later projection.
   - `postseason_shift()` estimates the postseason outs shift **only** from those settlement-derived starts. The
     frozen `data/edgelab/analytics/mlb_pitcher_prop_model_params.json` holds `postseason_shift_outs = −1.209`
     (raw −2.217, n = 30, shrink n0 = 25).
   - `evaluate_market()` applies a leave-future-out shift from the same rows. It also scores against
     `edgelab/observations` quotes, but that is the market comparison, not a model input.
2. **`lib/research/mlb_player_props.py`**
   - `postseason_starts_from_settlements()` does the same conversion for the research publication.
   - `PitcherContext` merges those rows into each pitcher's history.
   - `_pitcher()` applies the params-file postseason shift when `game_type ∈ {F, D, L, W}` or the date is on or
     after 2026-09-29.
3. **`scripts/app_export.py`** builds `inputs["postseason_settlements"]` from settlement partitions dated
   2026-09-29 or later and up to the export date (line 267). It passes them to `PitcherContext` (lines 489–491).

**What the settlement rows contain.** 40 postseason pitcher-games are dated 2026-09-29 to 10-08. Of these, 39
have outs (from OUTS markets) and all 40 have K (from KS markets). None has BF or pitch counts. Every row's
evidence is `sourceSystem = MLB_STATS_API` (`https://statsapi.mlb.com/api/v1.1/game/<pk>/feed/live`). The
**values** are therefore official box-score numbers. **Access** to them is market-conditional:

- **Selection.** A postseason start has a value only if Kalshi listed an OUTS or KS market for that pitcher and
  the market settled. The history and the n = 30 shift are therefore a market-selected population.
- **Timing.** Fetch lag after the game date was 1 day for 33 pitcher-games and 5 days for 7. In the calibration
  backtest, a postseason start dated D is used for every projected date after D, regardless of fetch time. Of the
  172 (later slate date × prior postseason start) uses, **64 used a value first fetched on or after the projected
  date**. That is an as-of violation inside the documented leave-future-out evaluation. The production export
  filters by settlement *partition* date (≤ export date), which is as-of-correct in time but still
  market-selected.
- **Pinned dependency.** The frozen params file carries the settlement-derived shift. Changing the settlement
  files changes the champion's postseason output. `scripts/research/mlb_pitcher_workload_mutation.py` (negative
  control) demonstrates this at runtime.

**Do sports-source box scores cover those starts?** **No.**

- `statcast_raw` ends 2026-09-27, because the Statcast postgame archive has added no postseason game files.
- The research-cache box scores end 2026-08-26 and contain only regular-season games.
- No other committed file carries a postseason starter's outs, BF, K or pitch count, except the settlement
  evidence above.
- The StatsAPI is blocked from this environment.

**Verdict for this piece: BLOCKED_DATA.** A sports-source-only postseason history, a postseason regime estimate
and a sports-sourced postseason outcome set cannot be built from committed data today.

**Unblocking.** Either of these would do it:

- Run `scripts/statcast_completed_game_catchup.py` for the postseason dates (2026-09-29 onward) from an
  environment that can reach Baseball Savant / StatsAPI.
- Add a box-score cache for postseason `gamePk`s, with fetch timestamps.

## 4. Consequences for the W1-D challenger

- The challenger (`lib/research/pitcher_workload_joint.py`) reads only `lib/research/pitcher_workload_sources.py`.
  Its source globs are the three sports sub-trees in §1.
- Postseason projections use regular-season history only. Rest days count from the last regular-season start,
  and postseason bullpen usage is unknown, so the bullpen feature is 0 in the postseason. No postseason regime
  term is fitted, because there are no sports-source postseason observations to fit it on.
- Primary evaluation is the 2025 + 2026 regular season. Postseason scoring is a **diagnostic only**: its outcomes
  are the settlement evidence values, the population is market-selected, and it is excluded from the verdict.
- "Lineup confirmed / unconfirmed" and "manager hook" subgroups are **BLOCKED_DATA** for the historical holdout.
  There is no as-of lineup status before 2026-07-30, and there is no manager identity anywhere.
