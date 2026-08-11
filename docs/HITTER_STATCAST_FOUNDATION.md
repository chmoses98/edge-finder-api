# Hitter Projection Engine — Phase 2: Raw Statcast + Bat-Tracking Data Foundation

Data-foundation milestone only. No hit/HR/total-bases/RBI probability is
computed here, no Monte Carlo simulation, no betting-gate/staking/
settlement/ledger change. This extends PR #78's canonical per-hitter
feature record (`lib/research/hitter_feature_context.py`) with real,
raw-pitch-derived data wherever a batter's history has been archived,
and leaves every field this repo genuinely cannot populate honestly
flagged (never fabricated).

## 1. Existing Savant hitter fields found/reused

`api/enrich.js`'s `type=batting` CSV request was already asking Savant
for `k_percent`, `bb_percent`, `hard_hit_percent`, `barrel_batted_rate`
in its `selections=` query string — the same HTTP response `xwoba` and
`fb_percent` were parsed from — but the parsing loop only ever read
`xw`/`fb` out of each row and threw the rest away. This milestone adds
`whiff_percent`/`exit_velocity_avg` to the same request (zero extra
fetches) and parses **all six** columns into a new, additive
`battersDiscipline` map alongside the existing `batters` (still a flat
`{playerId: xwOBA}` scalar map, byte-for-byte unchanged shape) — so
`scripts/fetch_lineups.py`'s `seasonWOBA` reader, the one existing
consumer of `batters`, needed zero changes.

## 2. Pitch-level data source and ingestion approach

Baseball Savant's `statcast_search/csv` endpoint — the exact same host
and CSV-export mechanism every other Savant fetcher in this repo already
uses — returns one row **per pitch** when `group_by` is omitted (every
existing fetcher always sets `group_by=name`, which is what collapses
it to a season aggregate). New `api/savantpitches.js` (Vercel) requests
this with `game_pk=<id>` (one game per request, ~250-300 rows, well
inside a normal function's budget) and normalizes each row into this
repo's canonical field names. `scripts/fetch_statcast_pitch_log.py`
calls it once per not-yet-archived `gamePk` (from `data/slate.json`'s
games by default) and ingests the result.

## 3. Raw pitch schema

See `lib/research/hitter_pitch_derivation.py`'s module docstring for the
authoritative field list. Summary: identity (`gamePk`, `gameDate`,
`batterId`, `pitcherId`, `batterHand`, `pitcherHand`, `inning`,
`atBatIndex`, `pitchNumber`), count/state (`balls`, `strikes`,
`outsWhenUp`, `onFirst/Second/Third`), pitch type (`pitchType`,
`pitchName`), velocity/shape (`releaseSpeed`, `spinRate`,
`inducedVertBreak`, `horizontalBreak`, `releaseHeight`, `releaseSide`,
`extension`, `armAngle`), location (`plateX`, `plateZ`, `szTop`,
`szBot`), result (`pitchCallType`, `description`, `events`), contact
(`launchSpeed`, `launchAngle`, `hitCoordX/Y`, `battedBallType`,
`estimatedBA`, `estimatedWOBA`, `wobaValue`). Every field is `None` when
the source didn't expose it — never fabricated.

## 4. Dedupe/stable identity strategy

`lib.research.statcast_pitch_store.pitch_identity()` builds a `pitchId`
from `(gamePk, atBatIndex, pitchNumber)` — fields that can never change
between two fetches of the same real pitch — falling back to
`(gamePk, pitcherId, batterId, inning, pitchNumber)` when `atBatIndex`
isn't available. Ingestion reuses `lib.edgelab.storage.append_records()`
(the same append-with-dedup JSONL primitive EdgeLab's own settlement
records already use — not reimplemented) keyed on this id, so
re-ingesting an already-archived game writes zero new rows.

## 5. Cache/incremental update design

Three layers, per this mission's spec:
- **A. Raw historical pitch archive**: one file per game,
  `data/statcast_raw/games/<gamePk>.jsonl`, plus a small per-batter index
  `data/statcast_raw/index/batter_games.jsonl` so a per-batter history
  load reads only the specific game files that index says involve that
  batter, never a full-directory scan.
- **B. Derived hitter feature tables**: `lib/research/
  hitter_pitch_derivation.py`, computed on demand from (A), never itself
  persisted (cheap to recompute; avoids a second place windowed stats
  could go stale).
- **C. Daily pregame snapshot**: PR #78's `hitter_features.json` pipeline
  artifact, now populated from (B) with an as-of cutoff.

`scripts/fetch_statcast_pitch_log.py` checks `has_game(gamePk)`
(a single `os.path.exists()`) before ever fetching — a normal slate run
only fetches gamePks not yet archived, never a player's full history.

## 6. As-of/no-leakage design

`statcast_pitch_store.load_pitches_for_batter(batter_id, as_of=date)` is
the only read path the feature-context builder uses, and `as_of` is
**exclusive** (a pitch on `gameDate == as_of` is excluded) — this is what
makes `as_of=<today's slate date>` mean "everything known strictly
before today's games." Every derived-window function
(`hitter_pitch_derivation.window_bounds` + `_filter_window`) re-applies
the same `until` bound independently, including the `career` window
(bounded above by `as_of_date` even though unbounded below) — two
independent layers a caller would have to defeat simultaneously to leak
future data in. See `tests/test_statcast_pitch_store.py`'s
`TestAsOfNoLeakage` and `tests/test_build_hitter_feature_board.py`'s
`test_future_dated_archive_entries_excluded_from_pregame_slate` for the
end-to-end proof.

## 7. Historical windows implemented

`career`, `previousSeason`, `currentSeason`, `rolling90d`, `rolling60d`,
`rolling30d` — each computed **independently** (never blended; shrinkage
across horizons is future modeling work) via
`hitter_pitch_derivation.derive_baseline_talent_window()`, with real
`PA/AB/H/1B/2B/3B/HR/BB/IBB/HBP/K/SF/AVG/OBP/SLG/ISO/BABIP/wOBA/K%/BB%/
HR%/GB%/FB%/LD%/Pull%/Center%/Oppo%` from raw pitch `events`, whenever a
batter has an archived window. `wOBA` reuses `api/enrich.js`'s exact
linear-weight formula (0.69 BB / 0.89 1B / 1.27 2B / 1.62 3B / 2.10 HR)
rather than inventing a second one.

## 8. Statcast hitter fields populated

Real (when a raw archive exists): `xBA`, `xwOBAcon`, `avgEV`, `maxEV`,
`ev90`, `avgLaunchAngle`, `sweetSpotPct`, `hardHitPct` (also from
`battersDiscipline`), GB/FB/LD distribution, Pull/Center/Oppo spray.
Real (from `battersDiscipline`, independent of a raw archive): `kPct`,
`bbPct`, `whiffPct`, `hardHitPct`, `barrelPct`, `exitVeloAvg`.
Deliberately **not** computed: an approximate `barrelPct` from raw
batted balls — Statcast's real barrel definition is a two-dimensional
EV/LA matrix, not a threshold, and guessing risks silently disagreeing
with the authoritative season-leaderboard value already wired above
(see `hitter_pitch_derivation.derive_contact_quality`'s own docstring).
`xSLG`, `barrelsPerPA`, `barrelsPerBBE`, `xHR`: still nowhere in this
repo's reach.

## 9. Pitch-type support

`hitter_pitch_derivation.derive_pitch_type_breakdown()` groups every
archived pitch by `pitch_taxonomy.classify_pitch_family()` (four-seam,
sinker, cutter, slider, sweeper, curve, knuckle curve, changeup,
splitter, other) and reports discipline + contact-quality per family.

## 10. Velocity support

`pitch_taxonomy.velocity_bucket()` only buckets `FASTBALL_FAMILIES`
(four-seam/sinker/cutter) into `<93/93-95/95-97/97-99/99+`; every other
family returns `None` from that function — an 87mph slider can never
join an 87mph fastball's bucket (tested explicitly in
`tests/test_pitch_taxonomy.py::TestVelocityBucket::
test_non_fastball_family_never_bucketed_even_at_fastball_speed`).
Continuous `releaseSpeed` is preserved on every archived pitch for
future continuous-response modeling.

## 11. Pitch-shape representation

`hitter_feature_context._pitch_shape_context()` reports a per-pitch-type
average shape (`releaseSpeed`, `inducedVertBreak`, `horizontalBreak`,
`spinRate`, `releaseHeight`, `releaseSide`, `extension`, `armAngle`) —
representation only, deterministic, no clustering or nearest-neighbor
similarity (explicitly future work).

## 12. Location support

`pitch_taxonomy.classify_zone()` (Heart/Shadow/Chase/Waste, Savant-style)
and `spatial_grid_bin()` (arbitrary-resolution grid, not hardcoded to
nine boxes) both operate on `plateX`/`plateZ` without ever discarding
the original continuous coordinate from the archived pitch record.
`hitter_feature_context.locationContext` currently reports zone-
frequency only; per-pitch grid binning is available directly off the
archive for a future heat-map model.

## 13. Count-state support

`pitch_taxonomy.classify_count_state()` groups a `(balls, strikes)` pair
into exact count, hitter/pitcher-ahead, two-strikes, three-ball-count,
0-2, 1-2, first-pitch. `hitter_pitch_derivation.
derive_count_state_breakdown()` reports discipline outcomes per bucket —
sequencing/simulation across pitches within a PA is explicitly future work.

## 14. Bat-tracking source/results

`api/savantbattracking.js` requests Savant's bat-tracking leaderboard
(`/leaderboard/bat-tracking?...&csv=true`) — the same CSV-export
mechanism every other Savant fetcher here uses, not a scrape of unstable
HTML. **Column names could not be verified against a live response in
this development environment** (Savant traffic is blocked here, same as
documented in `api/savant.js`'s own fallback message). Parsing uses the
same multi-candidate `findCol()` resilience `api/savant.js`'s
`fetchPlatoonSplits()` already relies on for exactly this reason.

## 15. Bat-tracking fields successfully supported (once verified live)

`avgBatSpeed`, `maxBatSpeed`, `fastSwingPct`, `squaredUpRate`,
`squaredUpPerSwing`, `blastRate`, `swingLength`, `attackAngle`,
`idealAttackAngleRate`, `attackDirection`, `swingTilt` — each resolves to
`AVAILABLE` only if the live fetch's column-name candidates actually
matched; a field this environment couldn't verify degrades to
`UNAVAILABLE_FROM_CURRENT_SOURCES` automatically, never a fabricated
number. History (not just today's snapshot) is preserved via
`lib/research/bat_tracking_store.py` — one dated row per
`(playerId, asOfDate)`, never overwritten, so recent-vs-baseline
comparison has real data.

## 16. Bat-tracking fields unavailable

`timingEarlyPct`/`timingOnTimePct`/`timingLatePct`,
`horizontalMissClass`/`verticalMissClass` — these require per-swing
event-level bat-tracking data, not a season leaderboard; not attempted.

## 17. Phase #78 schema fields newly populated

`baselineTalent` (all six horizons, when archived), `statcastContact`
(`hardHitPct`, `barrelPct`, `xBA`, `xwOBAcon`, `avgEV`, `maxEV`, `ev90`,
`avgLaunchAngle`, `sweetSpotPct`), `plateDiscipline` (`kPct`, `bbPct`,
`whiffPct`, `swingPct`, `contactPct`, `zSwingPct`, `zContactPct`,
`oSwingPct`/`chasePct`, `oContactPct`, `zonePct`, `calledStrikePct`,
`firstPitchSwingPct`, `firstPitchStrikePct`), `batTracking`,
`pitchTypeMatchup`, `velocityMatchup`, `pitchShapeContext`,
`locationContext`, `countContext`, `sprayContext`, `recentChangeContext`.

## 18. Files changed

New: `api/savantpitches.js`, `api/savantbattracking.js`,
`lib/research/pitch_taxonomy.py`, `lib/research/statcast_pitch_store.py`,
`lib/research/hitter_pitch_derivation.py`,
`lib/research/bat_tracking_store.py`,
`scripts/fetch_statcast_pitch_log.py`,
`scripts/fetch_savant_bat_tracking.py`, this doc, and the Phase 2 test
files listed below. Modified: `api/enrich.js` (battersDiscipline),
`scripts/fetch_savant_team.py` (persist battersDiscipline),
`scripts/build_hitter_feature_board.py` (load + wire raw pitches/bat
tracking/battersDiscipline), `lib/research/hitter_feature_context.py`
(consume the above, same status semantics, extended not rewritten).

## 19. Tests/results

New: `tests/test_pitch_taxonomy.py`, `tests/test_statcast_pitch_store.py`,
`tests/test_hitter_pitch_derivation.py`, `tests/test_bat_tracking_store.py`,
`tests/test_fetch_statcast_pitch_log.py`,
`tests/test_hitter_feature_context_phase2.py`,
`tests/test_fetch_savant_team_battersdiscipline.py`, plus additions to
`tests/test_build_hitter_feature_board.py`. Full suite:
`python3 -m pytest tests/ -q` → 4506 passed, 6 skipped, 4 failed (same 4
pre-existing, unrelated failures reproduced on a clean checkout — two
tests that shell out to `git diff` against historical SHAs unavailable
in this shallow clone).

## 20. Storage/performance impact

No new committed data volume in this PR (no live ingestion was run from
this sandboxed environment — Savant traffic is blocked here). Per-game
JSONL files keep any future archive naturally partitioned and
independently compressible later if volume grows (same `.jsonl`/`.jsonl.gz`
convention `lib.edgelab.storage` already supports). `has_game()`-gated
fetching means a slate run's incremental cost is proportional to new
games only, never full history.

## 21. Remaining data gaps

Catcher framing, umpire tendencies, sprint speed/defense (OAA) — still
`UNAVAILABLE_FROM_CURRENT_SOURCES`, unchanged from Phase 1, no
programmatic source identified. Exact Barrel% still comes from the
season leaderboard only (no raw approximation). Bat-tracking column
names are unverified pending a live Savant response.

## 22. Recommended next PR

Run `scripts/fetch_statcast_pitch_log.py` and `scripts/
fetch_savant_bat_tracking.py` for a real slate in an environment with
Savant access, verify `api/savantbattracking.js`'s column candidates
against the real response (adjust if needed), then build the first
actual hitter probability model (1+ hit) on top of this foundation —
`baselineTalent` + `platoonContext` + `pitchTypeMatchup`/
`velocityMatchup` — with sample-size-gated shrinkage toward league
priors, matching `platoon_context.py`'s existing convention.
