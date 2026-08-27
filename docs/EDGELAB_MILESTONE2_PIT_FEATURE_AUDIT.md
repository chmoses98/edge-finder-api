# EdgeLab Research Lab — Milestone 2: Historical Backtest Corpus / PIT Feature Audit

**Status: RESEARCH ONLY. No production model probability, feature, recommendation
logic, threshold, confidence tier, Bet Up To logic, Kalshi fee calculation,
bankroll/staking, market eligibility, lineup gate, slate output, risk gate,
settlement, or production cron behavior was changed by this milestone.**

## 1. Question this document answers

> Can we actually run thousands of leakage-safe historical MLB model experiments
> today, and for which feature families?

**Short answer: not yet at "thousands of games" scale for a full model
reconstruction, but yes for individual-family ablations that use only the
families this audit classifies as reconstructable, and the depth ceiling for
those families is set by MLB Stats API's own historical availability
(multiple past seasons), not by this repository's short EdgeLab corpus.**
The binding constraint on "thousands of games" is the *intersection* with a
contemporaneous archived Kalshi market or a captured model probability to
evaluate against — that intersection is bounded to this repo's own
~20–27 day corpus window (see §3).

This document does not implement every feature family, and does not run the
ablation it recommends in §5 — per this milestone's explicit scope, it audits,
builds the smallest safe reconstruction primitive proven necessary, and
recommends.

## 2. Method

Every classification below is based on reading the actual fetch/storage code
for that family (module docstrings, function signatures, and — where a
date-bounding claim is made — the literal date-handling logic), not on
inference from a file name or a docstring's stated intent. Where a mechanism's
date-bounding needed to be *proven*, not just read, a leakage test was written
(see §6) rather than assumed passing.

Classification vocabulary reuses `lib.edgelab.pit_provenance`'s existing
Milestone 0A vocabulary directly (no new taxonomy was invented):

| This milestone's spec label | `pit_provenance.py` constant |
|---|---|
| A. PIT_RECONSTRUCTABLE | `RECONSTRUCTABLE_FROM_DATED_RAW` |
| B. PROSPECTIVE_CAPTURED | `PROSPECTIVE_ONLY` |
| C. RETROSPECTIVE_LIMITED | `RETROSPECTIVELY_RECONSTRUCTED_WITH_LIMITATIONS` |
| D. UNAVAILABLE | `UNAVAILABLE_HISTORICALLY` |

## 3. Coverage report

Counts below are from the live `data/edgelab/` corpus as of 2026-08-27
(computed directly from the partitioned JSONL(.gz) files, not estimated).

| Entity | Dates | Records | Range |
|---|---|---|---|
| `observations` (archived Kalshi markets) | 27 | 404,144 | 2026-08-01 .. 2026-08-27 |
| `model_evaluations` (captured model probabilities, all quality tiers) | 26 | 80,361 | 2026-07-30 .. 2026-08-26 |
| `games` (settlement games) | 27 | 626 | 2026-08-01 .. 2026-08-27 |
| `settlements` | 23 | 98,191 | 2026-08-02 .. 2026-08-25 |
| `recommendations` | 20 | 76,762 | 2026-07-30 .. 2026-08-25 |

`model_evaluations` by `qualityTier` (the closest existing proxy for "current,
corrected model methodology" — see PR #117 / MLB-RSCH-0001):
`TRUSTED_PRODUCTION`=1,483, `RESEARCH_ONLY`=3,249, `UNSUPPORTED`=11,064,
untiered/null=64,565.

Statcast raw-pitch archive (`data/statcast_raw/`, new-ish "Hitter Projection
Engine" ingestion, growing daily):

| Index | Distinct gameDates | Range |
|---|---|---|
| `index/batter_games.jsonl` | 15 | 2026-08-11 .. 2026-08-25 |
| `index/pitcher_games.jsonl` | 15 | 2026-08-11 .. 2026-08-25 |

203 per-game pitch-log files archived under `data/statcast_raw/games/`.

`data/edgelab/hitter_projection_snapshots/`: 8 dates (2026-08-19 .. 2026-08-26),
a genuinely prospectively-captured archive of the model's own hitter-prop
probability outputs at the `LINEUP_CONFIRMATION` checkpoint — each row
self-documents its own `modelLimitations` (no archived raw pitch history
fallback cases, generic bullpen relief-pitcher fallback, a simplified
baserunning convention, down-weighted park orientation/wind). Useful evidence
of what the *current* hitter-prop model itself considers a known gap, not a
general-purpose reconstruction source.

**Three distinct research populations — do not conflate them:**

1. **Raw MLB games available for component ablations** (e.g. bullpen
   recent-usage vs. next-game outcome, using only MLB Stats API schedule/
   boxscore data): effectively **unbounded by this repo** — MLB Stats API
   serves full schedule/boxscore data for entire past seasons. Not quantified
   here (no local archive to count; this is an external-API capacity claim,
   confirmed by the endpoint shape, not by downloading multiple seasons in
   this milestone).
2. **Games with a contemporaneous archived Kalshi market** (this repo's own
   `observations` entity): 626 settlement games / 27 dates.
3. **Games with an actual captured model probability** (`model_evaluations`):
   26 dates, and only 1,483 rows at `TRUSTED_PRODUCTION` tier — this is the
   population any "does the CURRENT model's edge hold up" question is
   actually limited to, independent of how many raw games exist.

A full historical MODEL reconstruction (predictive input chain entirely PIT-
safe, scored against an archived market) is bounded by the *narrowest*
population it depends on — currently the Statcast-raw-archive families' 15
gameDates, or population (2)/(3) above if the ablation also needs an archived
market/probability to evaluate against.

## 4. Audit table

| Feature family | PIT status | Historical depth | Approx game count | Ready for ablation? | Main limitation |
|---|---|---|---|---|---|
| Team offense (Statcast/wOBA-level, production feature definition) | D UNAVAILABLE | none | 0 | No | `fetch_savant_team.py` hardcodes current `SEASON`, single overwritten file, no per-date archive anywhere. |
| Confirmed/projected lineups | B PROSPECTIVE_CAPTURED (at `LINEUP_CONFIRMATION` checkpoint only, since deployment) | since this system's deployment | not separately counted | Only at that checkpoint | No historical archive of lineup state before deployment; other checkpoints are a once-daily stale fetch, not a live check. |
| Handedness/platoon (static identity) | A-adjacent (essentially time-invariant, carried in every boxscore) | effectively all archived games | 203+ | Yes, as an auxiliary field | Not itself a standalone research input anywhere in this repo today. |
| Handedness/platoon (performance splits) | D UNAVAILABLE | none | 0 | No | Same `SEASON`-hardcoded, no-archive pattern as team offense. |
| Hitter season-to-date stats (current production artifact) | UNKNOWN_REQUIRES_AUDIT (unchanged — not re-audited this milestone) | unaudited | unaudited | No | Distinct from the raw-archive pathway below; `scripts/enrich_data.py`'s actual date-bounding was not traced this milestone. |
| Hitter Statcast/xwOBA/contact/discipline | **A PIT_RECONSTRUCTABLE** | 15 gameDates, 2026-08-11..2026-08-25, growing daily | 203 games | Yes, at this depth | Archive depth is short; not a multi-season backfill. |
| Starter quality (season-aggregate era/xFIP/whip) | D UNAVAILABLE | none | 0 | No | Same `SEASON`-hardcoded, no-archive pattern. |
| Starter velocity/pitch characteristics | **A PIT_RECONSTRUCTABLE** | 15 gameDates, same window as hitter Statcast | 203 games | Yes, at this depth | Same short-archive constraint. |
| Starter workload (recent starts/rest/pitch counts) | **A PIT_RECONSTRUCTABLE** (mechanism proven, not yet wired to a feature) | bounded only by MLB Stats API's own history | large, not locally quantified | Mechanism yes; feature computation not built | Same schedule+boxscore primitive as bullpen recent usage (§5) generalizes here, but no starter-workload feature function exists yet. |
| Bullpen talent (season-aggregate era/xFIP/grade/hlXFIP) | D UNAVAILABLE | none | 0 | No | Same `SEASON`-hardcoded, no-archive pattern. |
| **Bullpen recent usage/availability** | **A PIT_RECONSTRUCTABLE (built + tested this milestone)** | bounded only by MLB Stats API's own history | large, not locally quantified | **Yes** | New `lib.edgelab.pit_reconstruction` helper, proven by explicit leakage tests (§6). |
| Park factor | C RETROSPECTIVE_LIMITED | plausible for the whole corpus window | n/a (static table) | With a caveat | Static, unversioned table — stability assumed, not proven. |
| Weather | D UNAVAILABLE (unchanged from Milestone 0A) | none | 0 | No | Orphaned from production; no per-date archive. |
| Injuries/restrictions | D UNAVAILABLE (confirmed absent, not merely unaudited) | none | 0 | No | No data source of any kind exists in this repository. |

## 5. What can be reconstructed safely, and what cannot

**Safely reconstructable today, with a tested PIT-safe query interface:**
- Hitter and starter Statcast-level features (contact quality, spray profile,
  velocity, pitch mix/shape, location, count-state), via
  `lib.research.statcast_pitch_store.load_pitches_for_batter`/
  `load_pitches_for_pitcher` (exclusive `as_of` cutoff, index-first +
  per-pitch defense-in-depth refilter — pre-existing, already used in
  production by `lib.research.hitter_pitch_derivation` and
  `lib.research.hitter_feature_context`) — bounded to 15 gameDates of archive
  depth as of this audit.
- Team recent completed-game logs and bullpen recent-usage, via the new
  `lib.edgelab.pit_reconstruction` module (this milestone), reusing
  `lib.edgelab.bullpen_usage`'s existing MLB Stats API adapters — bounded
  only by MLB Stats API's own historical availability, not by this repo's
  corpus.

**Cannot be honestly reconstructed today:**
- Anything currently sourced from the Baseball-Savant/Vercel-proxied
  season-aggregate endpoints (team offense at Statcast-level, starter
  quality, bullpen talent, batter platoon performance splits) — every one of
  these fetch scripts hardcodes the current season and overwrites a single
  non-date-partitioned file. A "historical" query against these sources is
  indistinguishable from today's live aggregate; there is nothing to bound
  it. Confirmed by reading each fetch script, not inferred.
- Weather (orphaned from production, no archive) and injuries/restrictions
  (no data source exists at all) — both unchanged/confirmed findings, not new
  reconstructions.

**Explicitly not manufactured:** no historical model prediction was
fabricated by this milestone. The one new feature value this milestone builds
(`reconstruct_team_bullpen_usage_as_of`) is a deterministic aggregation over
provably-PIT-safe raw inputs (§6), not a model probability, and is not wired
into any experiment or disposition by this milestone.

## 6. Leakage tests performed (`tests/edgelab/test_pit_reconstruction.py`, 9 tests)

- Games on or after the requested `as_of_date` are excluded from
  `as_of_completed_team_games`, even when a **deliberately misbehaving**
  injected fetcher ignores the requested date range and returns future
  games anyway (defense-in-depth, not just "trust the query parameters").
- The schedule query window itself is verified to never request a date on
  or after `as_of_date` (`end_date = as_of_date - 1 day`).
- Only `COMPLETED`-status games are included — an in-progress, postponed, or
  scheduled game is excluded, not approximated.
- `reconstruct_team_bullpen_usage_as_of` never even **requests** a boxscore
  for a game the leakage guard has already excluded (asserted via a spy
  fetcher that records every `gamePk` it was called with).
- A reliever who appears **only** in a future (on/after `as_of_date`) game,
  fed through a misbehaving schedule fetcher, produces **zero trace** in the
  final reconstructed summary (explicit look-ahead test, per this milestone's
  own "test for look-ahead explicitly" requirement).
- `None` team_id/`as_of_date` return `[]` without ever calling the fetcher.
- Zero-completed-games and missing-boxscore edge cases reuse
  `lib.edgelab.bullpen_usage.summarize_team_bullpen_usage`'s own existing,
  already-tested `dataAvailable=False` contract.

## 7. Recommended first ablation (NOT run by this milestone)

**Bullpen recent-usage/fatigue proxy vs. declared model edge**, on the
`TRUSTED_PRODUCTION`-tier subset of `model_evaluations` (1,483 rows), paired
against `observations` via `lib.edgelab.research_dataset.build_opportunity_rows`
(the existing canonical join — reused, not rebuilt).

Why this one, not another family:
- Its reconstruction mechanism is the only family this milestone both built
  *and* proved safe with explicit leakage tests (§6), rather than reading
  code and trusting a docstring.
- Unlike the Statcast-raw-archive families (capped at 15 gameDates), its
  lookback depth is bounded only by `lookback_days`, not by an archive's
  start date — so it does not compound this corpus's own short window with a
  second, independent depth constraint.
- It needs no Savant/season-aggregate input at all, sidestepping the entire
  `UNAVAILABLE_HISTORICALLY` bucket in §4.

This document recommends it; it does not run it. Running it is future work
for a subsequent, separately-registered experiment under the Milestone 0A
framework.

## 8. PIT manifest changes

`lib/edgelab/pit_provenance.py`'s `PIT_MANIFEST` gained 9 new keys this
milestone (`hitter_statcast_raw_archive`, `pitcher_statcast_raw_archive`,
`team_recent_game_log_reconstruction`, `team_offense_savant_season_aggregate`,
`starter_quality_savant_season_aggregate`,
`bullpen_talent_savant_season_aggregate`,
`batter_platoon_split_savant_season_aggregate`, `park_factor_static_table`,
`injury_restriction_data`) — see each entry's `auditNotes`/`knownGaps` for the
specific evidence. The four pre-existing `UNKNOWN_REQUIRES_AUDIT` keys
(`sharp_sportsbook_observation`, `season_to_date_stats`, `hitter_snapshot`,
`pitcher_snapshot`) were deliberately left unchanged — they name different,
still-unaudited production artifacts, not the pathways audited here, and
per this milestone's own instruction ("do not upgrade uncertain inputs"),
an unrelated pathway being proven safe is not evidence that a *different*
named artifact is safe.
