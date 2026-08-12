# Hitter Projection Engine — Phase 3: Environment + Contact-Conversion Context

Data-foundation milestone only. No hit/HR/total-bases/RBI/other prop
probability is computed here, no Monte Carlo, no recommendation logic,
no betting-gate/staking/settlement/ledger change. This extends PR #78's
schema and PR #79's raw-pitch archive with park/weather/spray/defense/
catcher/umpire context so a future model can go from `EV × LAUNCH ANGLE
× SPRAY` to `CONTACT CONVERSION` (park geometry, weather, defense,
speed) and improve pitch/count progression with catcher/umpire context.

## 1. Bat-tracking source verification result

**Could not verify.** This sandboxed environment blocks outbound
network to every external host, confirmed by direct `curl` against both
`baseballsavant.mlb.com` and `statsapi.mlb.com` (`CONNECT tunnel failed,
response 403` for both) — the same restriction PR #79 hit. Per this
milestone's own instruction ("if network policy still prevents
verification, do not guess"), `api/savantbattracking.js` is unchanged
from PR #79: it still attempts Savant's bat-tracking leaderboard with
multi-candidate column-name resolution, and every field still reports
`UNAVAILABLE_FROM_CURRENT_SOURCES` until run somewhere with real network
access. Nothing here should be read as "verified."

## 2. Verified bat-tracking fields

None — see above. The candidate column names from PR #79 are unchanged.

## 3. Unavailable bat-tracking fields

All of them, pending live verification: `avgBatSpeed`, `maxBatSpeed`,
`fastSwingPct`, `squaredUpRate`, `squaredUpPerSwing`, `blastRate`,
`swingLength`, `attackAngle`, `idealAttackAngleRate`, `attackDirection`,
`swingTilt`. `timingEarlyPct`/`timingOnTimePct`/`timingLatePct` and
`horizontalMissClass`/`verticalMissClass` remain **not attempted** (they
need per-swing event data, not a leaderboard).

## 4. Spray data implemented

`lib/research/hitter_pitch_derivation.derive_spray_profile()` (new):
continuous spray angle (mean/std, never collapsed to buckets only),
Pull/Center/Oppo (handedness-normalized), fly-ball-only spray, a
documented "damaging air ball" heuristic (EV≥95, LA 10–35° — explicitly
**not** Statcast's official Barrel% definition, see the function's own
docstring for why), HR-only spray, EV-by-direction, launch-angle-by-
direction. All computed from PR #79's already-archived `hitCoordX/Y`,
`batterHand`, `launchSpeed`, `launchAngle`, `battedBallType`, `events` —
no new raw fields needed.

## 5. Park geometry source/schema

`config/park_geometry.json` (new) — a static, versioned reference table
for all 30 MLB teams: foul-line/power-alley/CF distances, wall heights
at those three points, altitude, roof type, foul-territory size, and
field orientation. Wall distances/heights/altitude/roof type are
publicly documented, stable stadium facts (not fetched — there's no live
source for physical stadium dimensions, same category as api/slate.js's
existing PARK_WEATHER team/name mapping). **Orientation degrees could
not be verified against a live/authoritative geodata source in this
environment** — every entry is marked `orientationConfidence:
"approximate_unverified"` and should be independently checked before
being trusted for a real wind adjustment. This is a simplified
foul-line/power-alley/CF **segment** model, not a full wall polygon — no
stable, authoritative, programmatic source for full polygons was
identified, so this is the "best reliable representation," explicitly
documented as such (not a claim of exact geometry). `lib/research/
park_geometry.py` is the loader (`resolve_park_geometry(team, as_of)`).

## 6. Park orientation support

Each team's geometry entry carries `orientationDeg` (home-plate-to-CF
compass bearing) and `orientationConfidence`. `lib/research/
park_geometry.field_relative_direction()` classifies any bearing (e.g.
wind direction) into `toward_cf`/`toward_lf`/`toward_rf`/`toward_plate`
relative to that specific park.

## 7. Empirical park-factor support

`lib/research/park_factor_derivation.py` (new) — kept **structurally
separate** from geometry (never imports it; a test enforces this). The
only real empirical signal in this repo is still the single overall
run-scoring index (`api/slate.js` `PARK_WEATHER`, reused unchanged).
Event-specific (1B/2B/3B/HR) and handedness-specific factors are
`NOT_COMPUTED` in the live schema — `derive_empirical_park_factors()` is
real, working, tested code that computes them correctly from an
accumulated raw-pitch archive (with a `MIN_PARK_PA_FOR_FACTOR=500`
sample floor), it just has no archived data to run against yet in this
environment (no live ingestion has been run here). `parkContext` exposes
`geometry` and `empiricalFactors` as two clearly separate sub-blocks so
a future model/ablation can tell how much each contributes independently.

## 8. Field-relative weather/wind support

`lib/research/park_wind_derivation.py` (new): `wind_field_relative_
components()` decomposes wind speed + raw compass degrees into
`componentTowardCF` (positive = blowing out) and `componentTowardRF`
(positive = toward the RF line), using vector projection onto the
park's own orientation axis. `build_field_relative_wind_context()`
combines this with a weather record and reports `NOT_APPLICABLE` (never
a fabricated component) when the park's `roofType` is `fixed_dome`, or
when the weather feed's `dome` flag is true for today. `weatherContext.
windRelativeToParkOrientation` carries this; raw temp/humidity/wind/
precip fields are preserved unchanged from Phase 1. Full air-density/
ball-flight physics stays an explicit `NOT_COMPUTED` placeholder
(`ballFlightAdjustment`) — deliberately not built here, per this
milestone's own scope limit.

## 9. Spray x park x wind representation

`hitter_feature_context.sprayContext.parkWindContext` (new sub-block):
combines this hitter's own pull-side direction (from `batSide`), the
damaging-air-ball spray distribution, and the field-relative wind
component resolved onto that hitter's specific pull side (sign-flipped
for a LHB vs RHB) — a raw, combined **representation**, explicitly no
betting adjustment computed. `AVAILABLE` only when a raw archive,
resolvable park geometry, and available field-relative wind all line up;
`NOT_COMPUTED` otherwise, never a partial guess.

## 10. Defensive data source/support

`api/enrich.js?type=defense` (new) attempts Savant's Outs Above Average
leaderboard, aggregated to TEAM level server-side (this repo has no
reliable, snapshot-safe source for tonight's actual defensive alignment
— see that file's own docstring). Same unverified-live-response caveat
as bat tracking. `lib/research/defense_store.py` (new, thin wrapper over
the new generic `player_metric_snapshot_store` engine) preserves dated,
never-overwritten snapshots keyed by team abbreviation.
`defenseContext.opponentDefense` reports `teamOAA`/`infieldOAA`/
`outfieldOAA`/`armStrengthOAA` when a snapshot exists, `NOT_COMPUTED` otherwise.

## 11. Sprint-speed/baserunning support

`api/enrich.js?type=sprintspeed` (new) attempts Savant's Sprint Speed
leaderboard (same unverified-caveat). `lib/research/sprint_speed_store.py`
preserves dated snapshots per batter. `defenseContext.hitterSpeed`
carries `sprintSpeedFtPerSec`/`homeToFirstSec`/`boltPct` — explicitly
foundation data only, **not** used for any stolen-base modeling in this
milestone (out of scope per spec).

## 12. Catcher source/support

Identity is **free** — `scripts/fetch_lineups.py` now also captures each
confirmed batter's defensive `position` from the exact same MLB Stats
API boxscore response it already fetches (universal DH means the
starting catcher is always one of the 9 confirmed batters) — zero
additional network call. `hitter_feature_context._catcher_context()`
resolves the OPPOSING side's confirmed catcher this way. Framing metrics
(`api/enrich.js?type=catcherframing`, `lib/research/catcher_framing_store.py`)
follow the same unverified-live-response pattern as bat tracking.
Blocking/pop-time deliberately not attempted (deprioritized per spec).

## 13. Umpire source/support

`scripts/fetch_umpire_assignment.py` (new) reuses `scripts/
fetch_lineups.py`'s existing `fetch_boxscore()` (same MLB Stats API
host, no new vendor) and reads that response's `officials` list for the
first time in this repo. **Identity only** — no stable, authoritative,
programmatic source for umpire zone-size/called-strike-tendency was
identified (third-party "umpire scorecard" sites are unstable HTML, out
of scope per this milestone's scraping restriction), so tendency fields
stay `UNAVAILABLE_FROM_CURRENT_SOURCES` honestly.

## 14. Historically reconstructable fields

Park geometry (static facts, reconstructable for any date), empirical
park factors once an archive exists (derivable retroactively from
archived pitches), defense/sprint-speed/catcher-framing season
aggregates (Savant leaderboards are queryable for a past season after
the fact — though a specific PAST DATE's exact value requires having
archived a dated snapshot at the time, not re-derived from "current"
aggregates).

## 15. Prospectively snapshot-dependent fields

**Umpire identity is the critical case**: MLB umpire crew assignments
are typically only known same-day, sometimes only a few hours pregame.
`scripts/fetch_umpire_assignment.py`'s storage enforces this structurally
— `data/umpire_assignments.jsonl` is keyed by `gamePk` alone via
`lib.edgelab.storage.append_records()`, so the FIRST successful capture
for a game is permanent; a later (possibly postgame) re-fetch can never
overwrite it (tested explicitly — see item 20). Weather is inherently
prospective too (a forecast, not a fact) — this repo still has no
historical weather archive (unchanged limitation from Phase 1), so a
real historical backtest cannot yet reconstruct a past date's actual
pregame forecast; this milestone's contribution is that the pure
`build_hitter_feature_context()` function itself has no clock/network
access, so it can only ever reflect whatever `weather_by_team` its
caller explicitly hands it for that run.

## 16. Remaining unavailable fields

Umpire zone-size/called-strike tendency (no source), catcher
blocking/pop-time (deprioritized, no cheap source), all bat-tracking
fields (unverified), full air-density/ball-flight physics (deliberately
not built), event-specific/handedness park factors (mechanism ready, no
archived data), defensive alignment/positioning (no reliable snapshot-
safe source — only team-aggregate OAA is supported).

## 17. Phase #78 schema blocks newly populated

`batTracking` (Phase 3 re-verification note, still unresolved),
`parkContext` (`geometry`, `empiricalFactors` sub-blocks; `wallDistances`/
`wallHeights`/`altitude`/`foulTerritory` now real when the team resolves),
`weatherContext` (`windRelativeToParkOrientation`), `sprayContext`
(continuous spray + `parkWindContext`), `defenseContext`
(`opponentDefense` + `hitterSpeed`), `catcherContext` (identity +
framing), `umpireContext` (identity + tendency placeholders).
`recentChangeContext` unchanged from Phase 2 (no new comparison inputs
added this milestone beyond what Phase 2 already wired).

## 18. Storage/cache changes

`config/park_geometry.json` — static, versioned, never refetched.
`lib/research/player_metric_snapshot_store.py` (new, generic) —
`lib.research.bat_tracking_store` now delegates to it rather than
duplicating its ~40 lines a second time; `defense_store`/
`sprint_speed_store`/`catcher_framing_store` are thin instances of the
same engine. `data/defense_history.jsonl`, `data/sprint_speed_history.jsonl`,
`data/catcher_framing_history.jsonl`, `data/umpire_assignments.jsonl` —
all dated-snapshot-never-overwritten JSONL, same pattern as PR #79's raw
pitch archive, reusing `lib.edgelab.storage`'s existing primitives.
`scripts/build_hitter_feature_board.py`'s new lookups are all read-only
against already-archived data — populating those archives is each
dedicated `scripts/fetch_*.py`'s job, run separately.

## 19. Files changed

New: `config/park_geometry.json`, `lib/research/park_geometry.py`,
`lib/research/park_wind_derivation.py`, `lib/research/park_factor_derivation.py`,
`lib/research/player_metric_snapshot_store.py`, `lib/research/defense_store.py`,
`lib/research/sprint_speed_store.py`, `lib/research/catcher_framing_store.py`,
`scripts/fetch_savant_defense.py`, `scripts/fetch_savant_sprint_speed.py`,
`scripts/fetch_savant_catcher_framing.py`, `scripts/fetch_umpire_assignment.py`,
this doc, and the Phase 3 test files listed below. Modified:
`api/enrich.js` (new `type=defense`/`type=sprintspeed`/`type=catcherframing`
branches -- this project deploys on Vercel's Hobby tier, capped at 12
serverless functions per deployment; three standalone files initially
added here pushed the count to 15 and failed deployment, so all three
were consolidated into the existing `type=` dispatcher instead, the
same pattern `api/enrich.js` already used for `batting`/`tto`/`bullpen`/
etc. -- no new file, no new vendor, same fix pattern this repo already
established), `lib/research/hitter_pitch_derivation.py` (spray profile,
extracted shared `_signed_pull_angle` helper), `lib/research/
bat_tracking_store.py` (now delegates to the generic engine, same
public API), `lib/research/hitter_feature_context.py` (park/weather/
spray/defense/catcher/umpire wiring), `scripts/fetch_lineups.py`
(position field), `scripts/build_hitter_feature_board.py` (load + wire
all Phase 3 sources).

## 20. Tests/results

New: `tests/test_park_geometry.py`, `tests/test_park_wind_derivation.py`,
`tests/test_park_factor_derivation.py`, `tests/test_phase3_metric_stores.py`,
`tests/test_fetch_umpire_assignment.py`,
`tests/test_hitter_feature_context_phase3.py`, plus additions to
`tests/test_hitter_pitch_derivation.py` (spray profile) and
`tests/test_build_hitter_feature_board.py` (as-of leakage guards for
defense/bat-tracking/sprint-speed/weather/umpire, end-to-end through the
real I/O shell). Two Phase 1/2 test assertions were updated to match
legitimate status upgrades (catcher/umpire now `MISSING_DATA` instead of
`UNAVAILABLE_FROM_CURRENT_SOURCES` since a real data path now exists;
`hrFactor` now `NOT_COMPUTED` instead of `UNAVAILABLE_FROM_CURRENT_SOURCES`
since the derivation mechanism now exists) — same pattern as Phase 2's
own semantic upgrades. Full suite: `python3 -m pytest tests/ -q` → 4585
passed, 6 skipped, 4 failed (same 4 pre-existing, unrelated failures
reproduced on a clean checkout — historical git SHAs unavailable in this
shallow clone).

## 21. Remaining data limitations

Every "attempted but unverified" Savant endpoint (bat tracking, OAA,
sprint speed, catcher framing) needs to actually run somewhere with
real network access before any of those fields can move past
`UNAVAILABLE_FROM_CURRENT_SOURCES`/`NOT_COMPUTED`. Park orientation
degrees need independent verification. No historical weather archive
exists. Umpire/catcher tendency metrics have no identified source at
all. Empirical park factors need real accumulated archive volume.

## 22. Recommended next PR

The full pitch-aware hitter outcome and game-simulation engine capable
of producing coherent probability distributions for ALL hitter prop
families (1+ hit, 2+ hits, alternate lines, HR, total bases, RBI, runs,
walks, strikeouts, fantasy score, ...) from one shared simulated
plate-appearance-outcome distribution — built on the full MATCHUP STATE
→ PITCH ENVIRONMENT → HITTER DECISION → CONTACT CONVERSION → PA RESULT →
GAME SIMULATION → MARKET DISTRIBUTIONS pipeline this and the two prior
milestones' records now support end-to-end. Not a narrow 1+ hit model.
