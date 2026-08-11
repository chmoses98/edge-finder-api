# Hitter Projection Engine — Phase 1: Canonical Data + Feature Foundation

This is a **foundation-only** milestone. It adds no betting gate, no
market pricing, no staking change, and no settlement change. It does not
price any hitter market (1+ hit, HR, total bases, RBI, ...) — nothing
here is safe to bet on yet. It produces exactly one new thing: a
canonical, non-fabricated **pregame feature record per confirmed
hitter**, consolidating every piece of pregame-available matchup
information this repo already has, plus explicit, honest placeholders
for every dimension a future hitter probability model will need but
this repo cannot yet populate.

## Targeted repository audit

Every item below was audited by direct inspection of the relevant code
and committed data files (not guessed from commit messages or docs
alone). Status legend: **EXISTS** (real, wired, persisted data),
**PARTIAL** (real data, materially thinner than the long-term design),
**MISSING** (no data, but plausibly obtainable from an existing vendor
this repo already uses), **UNAVAILABLE_FROM_CURRENT_SOURCES** (confirmed
absent from every source this repo can reach without a new vendor).

| Area | Status | Evidence |
|---|---|---|
| Confirmed lineup identity/order/handedness | EXISTS | `scripts/fetch_lineups.py` → `confirmedLineup[]` (`order`, `playerId`, `name`, `batSide`, `seasonWOBA`) |
| Per-batter platoon splits (wOBA/ISO/SLG/K%/BB% vs LHP/RHP) | EXISTS | `scripts/fetch_batter_platoon_splits.py` + `api/enrich.js?type=batterplatoon`, MLB Stats API `sitCodes=vl/vr` |
| Starter identity/handedness | EXISTS | `api/pitchers.js` / `api/slate.js` `pitcher.pitchHand` |
| Starter platoon splits, first-inning splits, velocity, TTO | EXISTS | `api/savant.js`, `scripts/fetch_savant_pitchers.py` |
| Confirmed-lineup vs starter-hand run adjustment | EXISTS | `lib/research/platoon_context.py` (PR #77) — team-level aggregate only, never per-hitter |
| Bullpen season quality + recent workload/availability | EXISTS | `api/bullpen.js`, `scripts/fetch_bullpen_usage.py` |
| Park run index | PARTIAL | `api/slate.js` `PARK_WEATHER` — single run-scoring index, 29/30 teams, no event-specific or handedness-specific factor, no wall dimensions, no altitude field, no roof-status granularity |
| Weather | PARTIAL | `api/weather.js` / `data/weather.json` — live OpenWeatherMap feed, but only 15/30 parks, no pressure field, wind is raw compass degrees with no park-orientation table to resolve blowing-out/in |
| Batter Statcast contact quality (xwOBA, hardHit%, barrel%, EV) | PARTIAL | `api/savant.js`'s batter leaderboard fetch *can* return these given `playerIds`, but only the xwOBA half is ever persisted (`data/savant_team.json` `batters` is a flat `{playerId: xwOBA}` map) — the rest is a wiring gap, not a missing source |
| Batter plate discipline (K%/BB%/whiff%) | PARTIAL (wiring gap) | Same fetch as above, same non-persistence |
| Batter plate discipline (Swing%/Zone%/Chase%/Heart-Shadow-Chase-Waste) | UNAVAILABLE_FROM_CURRENT_SOURCES | No fetcher anywhere requests these Savant leaderboard columns |
| Batter counting-stat history (H, HR, RBI, BB, K, ...) at any horizon | MISSING | No file, no fetch script, no field anywhere — every batter file stores at most a single rate-stat scalar |
| Career / previous-season / rolling 90d/60d/30d horizons | MISSING | Every existing batter fetch returns current-season aggregate only |
| Pitch-level / plate-appearance-level Statcast data (any field) | UNAVAILABLE_FROM_CURRENT_SOURCES | Every Savant fetch in this repo (`api/savant.js`, all three `scripts/fetch_savant_*.py`) uses `group_by=name` or leaderboard CSV exports — pre-aggregated by Savant before this code ever sees a row |
| Bat tracking (bat speed, swing length, attack angle, squared-up%) | UNAVAILABLE_FROM_CURRENT_SOURCES | Zero references anywhere in the repo |
| Pitcher arsenal (per-pitch-type usage/velocity/movement/release) | UNAVAILABLE_FROM_CURRENT_SOURCES | Every pitcher fetch returns season-aggregate rate stats only |
| Spray (Pull%/Center%/Oppo%, spray angle) | UNAVAILABLE_FROM_CURRENT_SOURCES | No batter-level spray data ingested |
| Catcher (framing/blocking) | UNAVAILABLE_FROM_CURRENT_SOURCES | Confirmed absent by this repo's own prior audit docs (`docs/research/PROJECTION_AUDIT.md`) |
| Umpire (zone size, called-strike tendency) | UNAVAILABLE_FROM_CURRENT_SOURCES | Same — only a null-filled placeholder key name exists, no producer |
| Hitter speed (sprint speed) / defense (OAA) | UNAVAILABLE_FROM_CURRENT_SOURCES | Zero references anywhere, confirmed absent by `docs/research/PROJECTION_AUDIT.md` |
| Kalshi hitter-prop market classification/taxonomy | EXISTS | `lib/kalshi_mlb_market_classifier.py`, `lib/research/market_taxonomy.py` recognize `hitter_hits`/`hitter_total_bases`/`hitter_rbis`/`hitter_stolen_bases`/`hitter_hits_runs_rbis` (no confirmed live series for `hitter_home_runs`) |
| Kalshi hitter-prop parsing/team-resolution/settlement | EXISTS | `lib/research/player_prop_parser.py`, `lib/edgelab/player_resolution.py`, `lib/edgelab/player_prop_settlement.py` — fully built, running nightly in production for post-hoc grading |
| Hitter probability model / projection of any kind | MISSING | `lib/kalshi_probability_adapters.py`'s own `_NEVER_MODELED_FAMILIES` and `docs/PROJECTION_BOARD.md` both explicitly confirm no per-batter probability distribution exists anywhere in this codebase |

**Bottom line:** this repo has real handedness/platoon/lineup-order
infrastructure to build on, and a mature settlement pipeline that
already understands hitter-prop markets exist — but zero hitter
stat-projection code, and zero per-batter counting-stat, bat-tracking,
pitch-level, park-detail, catcher, umpire, or defense data.

## What this milestone adds

- **`lib/research/hitter_feature_context.py`** (new, pure — no file I/O,
  no network, no clock reads): `build_hitter_feature_context(g,
  offense_side, weather_by_team=None, source_meta=None)`. For a
  confirmed lineup, returns one canonical feature record per hitter with
  26 domain blocks (`playerIdentity`, `lineupContext`, `paContext`,
  `baselineTalent`, `platoonContext`, `statcastContact`,
  `plateDiscipline`, `batTracking`, `starterContext`,
  `pitchTypeMatchup`, `velocityMatchup`, `pitchShapeContext`,
  `locationContext`, `countContext`, `bullpenContext`, `parkContext`,
  `weatherContext`, `sprayContext`, `defenseContext`, `catcherContext`,
  `umpireContext`, `recentChangeContext`, `dataAvailability`,
  `dataFreshness`, `sampleSizes`, `fallbacksUsed`, `uncertaintyFlags`).
  Every block/field carries an explicit status
  (`AVAILABLE`/`PARTIAL`/`MISSING_DATA`/`NOT_COMPUTED`/
  `UNAVAILABLE_FROM_CURRENT_SOURCES`/`LINEUP_UNCONFIRMED`/`OK`) — never a
  fabricated value. `LINEUP_UNCONFIRMED`/`MISSING_DATA`/`OK` are the
  literal constants `lib.research.platoon_context` already defines,
  imported rather than redeclared.
- **`scripts/build_hitter_feature_board.py`** (new, I/O-only shell):
  reads `data/slate.json` (plus optional `data/weather.json` /
  `data/savant_team.json` lookups), calls the module above once per
  offense side per game, and writes the result via
  `lib.pipeline_artifacts.write_stage_artifact("hitter_features", date,
  ...)` → `data/pipeline/<date>/hitter_features.json`. Never touches
  `data/slate.json`, `bets.json`, `config/rules.json`, `marketLedger`,
  or any settlement/staking/risk-gate file. Never fails the pipeline — a
  missing input or write error is reported and the script exits 0,
  mirroring `scripts/build_projection_board.py`'s own failure posture.
  **Not wired into `.github/workflows/fetch-slate.yml`** in this phase
  — it is a standalone, safe-to-run-anytime artifact, not yet a required
  pipeline stage.

## Reuse, not duplication

Every input this module reads is read as-is from existing infrastructure
— nothing is re-fetched or re-derived:

- Confirmed lineup / handedness / platoon splits ← PR #77's
  `scripts/fetch_lineups.py` + `scripts/fetch_batter_platoon_splits.py`
  output on `g[side]TeamStats.confirmedLineup`.
- The platoon value for one hitter vs one pitcher hand ← PR #77's
  `lib.research.platoon_context.hitter_platoon_value()`, called
  directly (a regression test asserts this module's `platoonContext`
  output matches a direct call to that function bit-for-bit).
- Starter identity/handedness/rate stats ← `g[oppSide].pitcher` /
  `g[oppSide].pitcherSavant` (`api/pitchers.js`, `api/savant.js`).
- Bullpen quality/workload ← `g[oppSide].bullpen` (`api/bullpen.js`,
  `scripts/fetch_bullpen_usage.py`).
- Park run index ← `g.park` (`api/slate.js` `PARK_WEATHER`).
- Weather ← caller-supplied lookup only (`data/weather.json`'s `parks`
  list, matched by home-team full name) — this module has no fetch
  logic of its own.

## Data-source classification for every new field

Per the historical-data rule this milestone was scoped under, every
field in the schema is one of:

- **A. Historically reconstructable** — `platoonContext`,
  `starterContext`, `bullpenContext`, `baselineTalent.currentSeason`
  (all derived from data already flowing through the existing pregame
  pipeline; a past date's values can be reconstructed from that date's
  frozen pregame snapshot once one exists for this artifact).
- **B. Must be snapshotted prospectively** — `parkContext`/
  `weatherContext` (weather in particular is time-of-fetch-dependent and
  must never be re-fetched for a past date) and every currently-`NOT_COMPUTED`
  block once it is eventually wired (pitch-type/velocity/location/count
  matchups) — these will only ever be correct if captured at or before
  first pitch, never reconstructed after the fact.
- **C. Current/research only** — none in this milestone; every field
  either reflects pregame-only inputs or is honestly marked
  unavailable/not-computed rather than populated from postgame data.

This module itself never reads a clock and never mutates its inputs
(see `TestHistoricalSnapshotNotOverwrittenByCurrentValues` in
`tests/test_hitter_feature_context.py`), so it is safe to call from a
future replay harness once `hitter_features` is registered as a snapshot
component type (not done in this phase — see Next PR below).

## Storage design

Three separate layers, matching this repo's existing conventions:

1. **Raw historical data** — unchanged in this phase (still whatever
   `data/savant_team.json`, `data/weather.json`, `data/bullpen.json`,
   and `data/slate.json.*TeamStats.confirmedLineup` already hold).
2. **Derived player features** — the per-hitter record this module
   computes, held only in memory unless a caller persists it.
3. **Pregame snapshot** — `data/pipeline/<date>/hitter_features.json`,
   written once per run via `lib.pipeline_artifacts.write_stage_artifact()`
   (the exact same envelope/atomicity/non-fatal-failure contract
   `projection_board.json` already uses) — a new, narrow, non-authoritative
   artifact that competes with nothing in `docs/SOURCE_OF_TRUTH_MAP.md`.

No slate run re-downloads any Statcast history — this milestone adds no
new network fetch at all; it only re-shapes data the existing pipeline
already fetched.

## Sample-size / shrinkage metadata

`platoonContext` carries `platoonPA`, `sampleThresholds.minPA`, and
`usedSeasonFallback` (surfaced from `hitter_platoon_value()`, not
re-derived). `baselineTalent` marks exactly which horizon is real vs.
unavailable so a future model knows which of the "older data as prior"
horizons it can actually shrink toward today (currently only
`currentSeason`). Every hitter record's `sampleSizes`, `fallbacksUsed`,
and `uncertaintyFlags` top-level fields give a caller (or a future
backtest) a single place to check what was real vs. assumed for that
hitter, that day, without re-deriving it from the 26 domain blocks.

## Tests

- `tests/test_hitter_feature_context.py` — pure-module unit tests:
  confirmed hitter receives a full-schema record; unconfirmed lineup
  (both `lineupConfirmedOfficial=False` and an empty `confirmedLineup`)
  never fabricates hitters; handedness/effective-side/platoon value flow
  correctly from PR #77's `confirmedLineup`/`platoon_context`; starter
  arsenal fields associate with the correct opposing hitters on both
  offense sides; pitch-type/velocity/shape/location/count blocks are all
  present with honest statuses; missing bat-tracking/catcher/umpire data
  degrades gracefully (never raises, never fabricates a value);
  `source_meta` timestamps are echoed into `dataFreshness` without
  mutation of any input; sample sizes and fallback state are exposed;
  this module's platoon output matches a direct call to
  `lib.research.platoon_context.hitter_platoon_value()`.
- `tests/test_build_hitter_feature_board.py` — I/O-shell tests: the
  artifact round-trips through `lib.pipeline_artifacts.read_stage_artifact()`;
  `data/slate.json` is never touched; `dry_run=True` writes nothing;
  a missing slate file returns a status instead of raising; missing
  weather/savant files degrade gracefully instead of raising.
- Full existing suite (`python3 -m pytest tests/ -q`) passes unchanged
  aside from 4 pre-existing failures on a clean checkout (two tests that
  shell out to `git diff` against historical SHAs unavailable in a
  shallow clone — unrelated to this change, reproduced on `main` before
  this branch's commit).

## Data-access limitations

- Baseball Savant's `statcast_search` CSV export is only ever queried
  with `group_by=name` in this repo — no per-pitch or per-batted-ball
  row has ever been fetched, and this milestone does not add that
  fetcher (explicitly out of scope: "smallest safe canonical feature
  foundation", not the pitch-level ingestion itself).
- Bat-tracking, catcher framing, and umpire data have no known
  programmatic source already in use by this repo; adding them requires
  a new Savant leaderboard/endpoint integration in a future phase, not a
  scrape of an unstable webpage.
- `data/weather.json` only covers 15 of 30 parks and carries no
  park-orientation table — `windRelativeToParkOrientation` is marked
  `UNAVAILABLE_FROM_CURRENT_SOURCES` rather than guessed.

## Recommended next PR

The first actual hitter probability model built on this foundation:
wire `scripts/fetch_savant_pitchers.py`'s already-working `/api/savant`
call pattern to also persist per-batter `kPct`/`bbPct`/`whiffPct`/
`hardHitPct`/`barrelPct`/`exitVeloAvg` (currently computed in-memory by
`api/savant.js` but discarded before disk — a wiring fix, not new
ingestion), then build a first, deliberately narrow PA-outcome model
(e.g. 1+ hit only) on top of `baselineTalent.currentSeason` +
`platoonContext` + `starterContext`, with sample-size-gated shrinkage
toward league-average priors exactly like `platoon_context.py` already
does for team-level platoon adjustments — before attempting any
pitch-level, bat-tracking, or park-detail dependent market.
