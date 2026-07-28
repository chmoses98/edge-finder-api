# CANONICAL_SCHEMAS.md

Phase 2, Part 4 — designed (not migrated) canonical schemas for the eight
core objects in the pipeline. These are **contracts for future phases**,
not changes made now. Field names are drawn directly from what the
codebase already writes/reads today (confirmed via the Phase 2 research
passes) so that a future migration is a rename/formalize exercise, not a
redesign.

Legend: **R** = required, **O** = optional (may be `null` or absent
depending on pipeline stage/data availability).

---

## 1. `RawGame`

The as-fetched game object, before any enrichment. Source: `api/slate.js`
+ `api/pitchers.js` responses, as they land in `data/slate.json` on first
fetch.

| Field | R/O | Type | Source | Notes |
|---|---|---|---|---|
| `gameId` / `gamePk` | R | string | MLB Stats API | Stable ID — RUN_THE_SLATE.md rule #9 ("use stable event/game IDs, not team+date") already requires this; currently inconsistently named `gameId` in some places, `gamePk` in others (`lib/postponed_guard.py` accepts both) |
| `away.abbr` / `home.abbr` | R | string | MLB Stats API | Display only — never used as the identity key going forward |
| `status` | R | string | MLB Stats API | Raw status string (`"Scheduled"`, `"In Progress"`, `"Final"`, etc.) — see `lib/postponed_guard.py` for the canonical status-string taxonomy |
| `scheduledStartTime` / `gameTime` | R | ISO 8601 UTC string | MLB Stats API | Two field names currently coexist (`postponed_guard.check_game_status` checks both) — future schema should pick one |
| `away.pitcher` / `home.pitcher` | O | object `{name, id}` | MLB Stats API `probablePitcher` | May be absent/TBD pre-announcement |
| `venue` | O | string | MLB Stats API | Used for park factors |
| `doubleheaderGameNumber` | O | int (1 or 2) | MLB Stats API | RUN_THE_SLATE.md rule #7 ("handle doubleheaders correctly") — currently not a consistently-populated field; flagged as a gap, not fixed |

**Lifecycle:** written once per fetch, at the top of `fetch-slate.yml`.
Never re-fetched within a run (all enrichment mutates the same object).

---

## 2. `Game`

The enriched per-game object as it exists inside `data/slate.json` after
the enrichment stages (`fetch_lineups.py` → `enrich_lineup_confirmed.py` →
`merge_odds.py` → `enrich_data.py`), immediately before
`build_market_ledger.py` runs.

| Field | R/O | Type | Source | Notes |
|---|---|---|---|---|
| *(all `RawGame` fields)* | — | — | — | `Game` extends `RawGame` |
| `awayTeamStats` / `homeTeamStats` | R | object | `enrich_data.py` | Contains `lineupConfirmed`, `last7RpG`, `last15RpG`, `runsPerGame`, `offenseBaselineAdj` |
| `awayTeamStats.lineupConfirmed` / `homeTeamStats.lineupConfirmed` | O (null pre-lineup-post) | bool | `fetch_lineups.py` | `None` = not yet posted (expected pre-~1pm ET), not a failure |
| `away.pitcherSavant` / `home.pitcherSavant` | O | object | `fetch_savant_pitchers.py` | Contains `xFIP`, `seasonFIP`, `recentFIP`, `startsSampled`; entire block may be `null` if starter TBD |
| `excludedFromSlate` | O | bool | `post_fetch_gate.py` | Present only on quarantined games; absence implies `False` |
| `exclusionReason` | O | string | `post_fetch_gate.py` | Required if `excludedFromSlate=True` |
| `odds.kalshi.*` | O | object | `merge_odds.py` | Per-market Kalshi ticker/price structure (ml/rl/total/tt/f5ml/rfi) |
| `odds.pinnacle` | O | object | `merge_odds.py` (from `data/odds.json`) | Sanity-check source, never the edge target |
| `marketLedger` | R (after `build_market_ledger.py` runs) | array of `Recommendation` (see §5) | `build_market_ledger.py` | Exactly 11 rows required per `config/rules.json`'s `market_list` |

**Lifecycle:** mutated in place across ~9 pipeline stages (see
`docs/MODEL_V2_ARCHITECTURE.md` §7) within a single `fetch-slate.yml` run.
No versioning of intermediate states beyond what `data/slates/<date>/`
snapshots capture at named checkpoints (`official_*`, `recheck_*`).

---

## 3. `Projection`

The model's per-game run/probability output. **Currently not a
standalone object** — embedded as fields directly on `Game`. Documented
here as the target shape for a future extraction.

**Phase 4 update:** a standalone snapshot now exists —
`data/pipeline/<date>/projections.json` (see
`docs/IMMUTABLE_PIPELINE.md` §3) — but it is a narrower, purely
operational artifact (`awayProjRuns`, `homeProjRuns`, `totalProj`,
`f5AwayProj`, `f5HomeProj`, `missingFields`, plus `away`/`home`/
`kalshiKey`/`excludedFromSlate` for joining back to the slate), not the
full target schema below (`true_xFIP`, `parkAdj`, `modelVersion`, etc.
are not part of it). It is written *in addition to* the embedded
`Game` fields, not instead of them — `Game.awayProjRuns`/`homeProjRuns`
remain the fields every other script actually reads. Treat this section
as still describing the target shape for a future, more complete
extraction; `projections.json` is real progress toward it, not its
final form.

| Field | R/O | Type | Source | Notes |
|---|---|---|---|---|
| `awayProjRuns` / `homeProjRuns` | R | float | `api/slate.js` (Poisson engine) → adjusted by `enrich_data.py` | Full-game projected runs |
| `awayF5ProjRuns` / `homeF5ProjRuns` | O | float | Same | First-5-innings projections |
| `offenseBaselineAdj` (away/home) | R | float | `enrich_data.py` | Applied only when `lineupConfirmed=True`; falls back to `offenseBaselineOppAdj` otherwise |
| `true_xFIP` (away/home starter) | R | float | `api/slate.js` + regression weights per `config/rules.json` `regression_weights_by_pitcher_type` | The starter-quality input to run projection |
| `parkAdj` | O | float | `config/rules.json` `park_factors` | Park + weather-adjusted |
| `modelVersion` | **missing today** | string | — | **Gap**: no field currently records which model/calibration version produced a given projection. Required for Phase 4's calibration-by-version tracking (per the original Model V2 objective's "model version, feature version, calibration version" requirement). Recommend adding as part of any future migration, not retrofitted here. |

**Lifecycle:** computed once per game per run; not currently versioned or
independently persisted (only exists as a snapshot inside whichever
`Game` object it's embedded in).

---

## 4. `MarketProbability`

The comparison between model and market for one game+market+side.
**Currently embedded as fields within each `marketLedger` row** (see
`Recommendation` below) rather than as its own object — documented
separately here because the phase brief calls it out explicitly, and
because a future refactor may want to separate "what do we think the
probability is" from "did we recommend a bet" (the portfolio-decision
concern).

| Field | R/O | Type | Source | Notes |
|---|---|---|---|---|
| `modelProb` | R | float (0-100) | `build_market_ledger.py` | Raw model probability before calibration |
| `kalshiVF` / `kalshiImplied` | R (if price available) | float (0-100) | `merge_odds.py` → `build_market_ledger.py`'s `vig_free_2way`/`vig_free_1way` | No-vig Kalshi probability — the edge target |
| `pinnacleVF` | O | float (0-100) | `merge_odds.py` | Sanity-check only, per `RUN_THE_SLATE.md`'s source-of-truth hierarchy — never the edge target |
| `edge` / `calibratedEdgeVsExecutable` | R (if Accepted/Rejected) | float | `build_edge_fields()` in `build_market_ledger.py` | `(modelProb − kalshiVF) × calibration_factor`, never raw gap, per `RUN_THE_SLATE.md` |
| `calibrationFactor` | R | float | `config/rules.json` `calibration.{tier}.factor` | Which tier's factor was applied |
| `uncertainty` | **missing today** | float | — | **Gap** — no explicit uncertainty/confidence-interval field exists distinct from the tier label (`HIGH`/`MEDIUM`/`PAPER`). Flagged for Phase 4 per the Model V2 objective's calibration requirements. |
| `priceTimestamp` / `priceFreshness` | Partial | ISO string / enum | `capture_closing_lines.py` snapshot metadata | Exists for closing-line capture but not consistently attached to the *entry*-time price used for the recommendation itself — a real gap between "what price did we compare against" and "when was that price last updated." |

---

## 5. `Recommendation` (the `marketLedger` row)

The actual, currently-implemented object — one per game per market
(11 per game).

| Field | R/O | Type | Source | Notes |
|---|---|---|---|---|
| `market` | R | string | `build_market_ledger.py` | One of the 11 canonical market names in `config/rules.json`'s `market_list` |
| `status` | R | enum | `build_market_ledger.py` | Exactly one of `Accepted`/`Rejected`/`Missing Data`/`Evaluation Failed` per `RUN_THE_SLATE.md`'s row-status contract |
| `confidence` / `confidenceTier` | R | enum | `build_market_ledger.py`, later mutated by `risk_gate.py` | `HIGH`/`MEDIUM`/`PAPER` |
| `betSize` | R (if Accepted) | float | `build_market_ledger.py` base size, adjusted by `risk_gate.py` | Units, not dollars — per `config/rules.json` `base_sizes` |
| `rejectionReason` | R (if `Rejected`) | string | `build_market_ledger.py` | Non-empty per the row-status contract |
| `missingFields` | R (if `Missing Data`) | list of strings | `build_market_ledger.py` | At least one field path |
| `evaluationError` | R (if `Evaluation Failed`) | string | `build_market_ledger.py` | Non-empty — empty string on this status is itself a hard-stop bug per `RUN_THE_SLATE.md` |
| `ticker` / `marketTicker` | R (if Accepted) | string | `merge_odds.py` → `build_market_ledger.py` | Two field names currently coexist |
| `bet_eligibility_status` | R | enum | `scripts/bet_eligibility.py: apply_eligibility()` | Independent of `status` — see `docs/DUPLICATE_LOGIC_INVENTORY.md` §5 for why there's a second, unused taxonomy for this concept |
| `clv_capture_status` / `review_integrity_status` | R | enum | Same | See `scripts/bet_eligibility.py` docstring for the full vocabulary |
| `realMoneyBlocked` / `blockReason` | O | bool / string | `risk_gate.py` | Set when `risk_gate.py` downgrades an otherwise-Accepted row |
| `gatesFired` | O | list of strings | `build_market_ledger.py`, `risk_gate.py` | Which T1/T2 rules fired |

---

## 6. `ExecutedBet` (the `bets.json` record)

| Field | R/O | Type | Source | Notes |
|---|---|---|---|---|
| `date` | R | `YYYY-MM-DD` string | `write_pending_bets.py` / `log_session_bets.py` | Slate date, not placement timestamp |
| `game` | R | `"AWAY@HOME"` string | Same | **Not a stable ID** — display-derived, violates RUN_THE_SLATE.md rule #8 ("do not identify games solely by team names and date"); a real `gameId` field is not currently carried through onto the bet record. Flagged as a genuine gap, not fixed this phase. |
| `market` | R | string | Same | |
| `betSide` / `side` | R | string | Same | |
| `confidenceTier` | R | enum | Same | |
| `stake` / `betSize` | R | float | Same | Two field names for the same value |
| `odds` / `kalshiPrice` | R | American odds int | Same | Two field names for the same value |
| `actualEntryPrice` | R (or explicitly null + `realMoneyBlocked`) | float (0-1 implied prob) | `write_pending_bets.py` | Never silently defaulted — `write_pending_bets.py` already does this correctly (sets `realMoneyBlocked=True` + `dataHealthWarning` rather than treating missing price as zero, satisfying the "never treat null/zero as a valid market price" requirement) |
| `ticker` | R (or flagged) | string | Same | Missing → `realMoneyBlocked` |
| `entryTimestamp` | R | ISO 8601 UTC | Same | |
| `status` | R | `"pending"` initially | Same | Mutated to `WIN`/`LOSS`/`PUSH`/`VOID`/`SETTLED` by `clv_update.py` |
| `result` | R (post-settlement) | enum | `clv_update.py` | Per `test_no_unverified_on_complete.py`, must never remain `null` on a Final game past the review-complete gate |
| `closingLine` / `closingLinePct` | O | American odds / float | `capture_closing_lines.py` (settle mode) or `clv_from_snapshot.py` | |
| `trackingType` | **designed but unused** | enum (`REAL`/`MODEL_ONLY`/`PAPER`/`REAL_PROBE`) | `lib/tracking_type.py` | **Confirmed gap**: this schema exists in `lib/tracking_type.py` but no script that writes a bet record actually sets it — current records use a different, simpler `"type": "real"/"paper"` field instead (`log_session_bets.py`). Recommend a future phase either retire `lib/tracking_type.py`'s schema or actually wire it in — not decided here, documented as an open question. |
| `modelVersion` / `calibrationVersion` / `pipelineRunId` | **missing today** | string | — | None of these exist on current bet records. Required for the Model V2 objective's "model plus manual override," "recommendation source," and versioned-evaluation goals. Flagged, not retrofitted. |
| `pregame` / `lineupConfirmedAtEntry` | Partial | bool | Implicit via the live-game gate having already run before write | Not an explicit stored field on the bet record itself — the fact that a bet passed the pregame gate is not recorded as data on the bet, only enforced as a precondition to writing it at all. |

---

## 7. `Settlement`

| Field | R/O | Type | Source | Notes |
|---|---|---|---|---|
| `result` | R | enum (`WIN`/`LOSS`/`PUSH`/`VOID`) | `clv_update.py`, `lib/f5_settlement.py` | |
| `pnl` | R | float | `clv_update.py` | |
| `voidReason` | R (if VOID) | string | `lib/postponed_guard.void_bets_for_game()` | e.g. `"postponed"` |
| `settlementSource` | **missing today** | string | — | No field records *which* settlement path (linescore reconstruction vs. RBI-based vs. manual) produced a result — a Model V2 objective ("settlement source"), not currently tracked. Flagged. |
| `bankrollAtSettlement` | **missing today** | float | — | `lib/tracking_type.py: calculate_bankroll_pl()` exists but current settlement does not persist a bankroll snapshot per bet. Flagged. |

---

## 8. `PipelineStatus`

The one schema from this list that **is** already fully implemented,
introduced in the Phase 1 hardening pass — included here for completeness
since the phase brief explicitly asks for it.

| Field | R/O | Type | Source |
|---|---|---|---|
| `runId` | R | string | `fetch-slate.yml` final step (`github.run_id`) |
| `slateDate` | R | `YYYY-MM-DD` | Same (`env.DATE`) |
| `completedAt` | R | ISO 8601 UTC | Same |
| `status` | R | enum (`success`/`partial`/`failed`) | Computed by the `jq` filter from stage outcomes |
| `stages.{validate,protect,publish,risk_gate,write_pending_bets,validate_bet_logging,write_tracked_tickers,capture_closing_lines}.status` | R | enum (`success`/`failure`/`skipped`) | GitHub Actions' own `steps.<id>.outcome` |

This is the schema every other object above should eventually converge
toward in shape (explicit required/optional fields, a version/run
identifier, and a small closed enum for status) — it's the newest part of
the pipeline and was designed with these Phase 4 goals in mind from the
start.

---

## Cross-cutting gaps found while writing these schemas

1. **No object in the entire pipeline carries a `modelVersion`,
   `calibrationVersion`, or `pipelineRunId` field** except the brand-new
   `PipelineStatus`. This is the single largest schema gap relative to the
   original Model V2 objective's evaluation/reporting goals.
2. **`gameId`/`gamePk` is inconsistently named and not consistently carried
   through onto `ExecutedBet`** — bet records identify games by
   `"AWAY@HOME"` string, not a stable ID, which is a direct violation of a
   documented rule (RUN_THE_SLATE.md / the original system prompt's rule
   #9) that nobody has enforced in the schema itself.
3. **`lib/tracking_type.py`'s `trackingType` enum is entirely
   disconnected from what `bets.json` actually stores.** Either the schema
   or the writer is wrong; Phase 3 should resolve which.
4. **No settlement provenance field** (`settlementSource`) despite the
   Model V2 objective explicitly requiring one.

None of these gaps were fixed this phase — they are exactly the kind of
finding Part 4 is meant to surface for Phase 4 planning.
