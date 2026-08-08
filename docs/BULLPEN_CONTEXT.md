# Bullpen context for pregame analysis

Data/context improvement only — this does **not** change recommendation
thresholds, staking, settlement, or ledger logic. Every field described
here is additive, and none of it is read by
`scripts/build_market_ledger.py`'s projection/pricing math (which only
ever reads `bullpen.xFIP`, unchanged) or by any YRFI/NRFI
generation-time guard (`api/slate.js`'s `YRFI_DISALLOWED_KEYS`,
`lib/yrfi_nrfi_validator.py`'s disallowed-key list) — none of the field
names introduced here collide with those.

## The limitation this closes

`data/bullpen.json` (via `api/bullpen.js`) and its high-leverage overlay
(`api/enrich.js?type=bullpen`, merged in by
`scripts/fetch_savant_bullpen_hl.py`) only ever described **season-long
bullpen quality** — ERA/xFIP/WHIP/K-BB rates and a saves+holds-weighted
high-leverage xFIP split. Nothing said whether *today's* bullpen is
actually rested and available: `api/slate.js`'s own `fetchBullpens()`
even carried two permanently-stubbed placeholder fields,
`last3DaysIP: null` and `fatigued: false`, that were never actually
computed.

## What was added

`scripts/fetch_bullpen_usage.py` (new script, same execution model and
MLB Stats API endpoints as `scripts/fetch_opp_quality.py` — schedule +
boxscore, no new API surface) computes, per team, a `recentUsage` block
merged into `data/bullpen.json`:

| Field | Meaning |
|---|---|
| `dataAvailable` / `unavailableReason` | Explicit, never guessed — `false` only when the lookback window found zero completed games (e.g. every fetch failed, or the team had no games) |
| `asOfDate` | Most recent completed game date considered |
| `gamesConsidered` | How many completed games (up to 2) fed this summary |
| `relieversUsedLastGame` | Relievers who appeared in the most recent game, with pitch counts |
| `backToBackRelievers` | Relievers who appeared in **both** of the last two games |
| `recentPitchCounts` | Per-reliever total pitches + appearance count across the window |
| `highLeverageRecentUsage` | Relievers who recorded a save or hold in the window (the same save/hold signal the existing HL-quality split already uses to identify leverage relievers), with their recent pitch counts |
| `handednessMix` | `{L, R, unknown}` counts of distinct relievers who appeared in the window |
| `teamPitchCountLastGame` / `teamPitchCountWindow` | Simple sums — team bullpen pitches thrown yesterday / across the window |

`scripts/enrich_data.py` copies this block verbatim into each game's
`away`/`home` `.bullpen.recentUsage` in `data/slate.json`, next to the
pre-existing `hlXFIP`/`hlGrade`/`hlAvailable`/`hlDivergence`/`hlSamplePA`
fields — same merge pattern, same file.

Deliberately **not** added: a single "is the bullpen rested" verdict, a
staking input, or a recommendation signal. This is raw, transparent
usage data so a human reading the slate can answer:

- **Is the bullpen rested?** — `recentPitchCounts` / `teamPitchCountWindow`
- **Which important relievers may be unavailable or compromised?** — `backToBackRelievers` / `highLeverageRecentUsage`'s recent pitch counts
- **Is full-game exposure materially worse/better than F5 because of bullpen context?** — cross-reference `recentUsage` against the existing season-quality `xFIP`/`grade`
- **Is a team-total/game-total thesis helped or hurt by bullpen condition?** — same cross-reference

## Data sources / fallback behavior

- Same MLB Stats API team-schedule + `/v1/game/{gamePk}/boxscore`
  endpoints `scripts/fetch_opp_quality.py` already calls for starter
  identification — no new API path.
- A team with no completed games in the 3-day lookback window (e.g. an
  All-Star break or a rainout-heavy stretch) gets
  `dataAvailable: false, unavailableReason: "no_completed_games_in_window"`
  — never a guessed/approximated summary.
- A malformed or missing boxscore/schedule response degrades to an
  empty result for that game/team (never raises, never crashes the
  batch — same convention as `lib/edgelab/mlb_boxscore.py` and
  `scripts/fetch_opp_quality.py`).
- Every counting statistic (pitch counts, saves, holds) is parsed via
  `lib.edgelab.player_stats.parse_nonnegative_int()` — a malformed value
  becomes `None`, never silently truncated or coerced.
- Handedness (`throwsHand`) is read directly from the boxscore response
  when present; a missing or unrecognized code is `None`, never guessed
  ("handedness mix where practical" — this repo does not make an extra
  per-player network call just to backfill it).

## Files

- `lib/edgelab/bullpen_usage.py` — pure parsers + network adapters
- `scripts/fetch_bullpen_usage.py` — CLI entry point, merges into `data/bullpen.json`
- `scripts/enrich_data.py` — merges `recentUsage` into `data/slate.json`
- `.github/workflows/fetch-slate.yml` — runs the fetch right after the existing HL-bullpen step
