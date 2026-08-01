# EdgeLab Phase 2 Milestone 4: research-complete ModelEvaluation metadata

Status: makes `ModelEvaluation` research-complete by reliably populating
model identity, lineup state, thesis tags, data quality, confidence, and
correlation metadata **from existing pipeline evidence only**. **Does not
change production recommendations, probabilities, staking, or bet
selection.** Builds directly on Milestone 3
(`docs/EDGELAB_MODEL_EVALUATION.md`).

## 0. How this milestone was scoped

Before writing any code, every field this milestone was asked to
populate was traced back to its actual source in the pipeline codebase
(scripts, `api/` handlers, config). The finding that shaped everything
below: **roughly a third of the 19-tag controlled vocabulary has no real
producer anywhere in the production pipeline today.** `PLATOON_EDGE`,
`WEATHER_OVER`/`WEATHER_UNDER`, `UMPIRE_FACTOR`, `WORKLOAD_OVER`/
`WORKLOAD_UNDER`, `STRIKEOUT_MATCHUP`, `CONTACT_MATCHUP`, and
`CORRELATED_POSITION` are never assigned — not because the mapping logic
is missing, but because the underlying signal (a computed weather
adjustment, an umpire factor, a workload/strikeout-matchup verdict, an
operationalized same-game-correlation rule) simply does not exist in any
script, API handler, or artifact field. Per this milestone's explicit
"never invent new handicapping logic" constraint, these tags are
permanently absent rather than approximated. This is stated up front
because it's the single most important thing to understand about the
rest of this document.

## 1. Metadata sources (the audit, condensed)

| Signal | Real producer | Notes |
|---|---|---|
| Starter/F5 edge (`xERAGap`, `f5Amplified`, `favoredSide`) | `scripts/build_market_ledger.py` (`xera_gap = abs(away_xfip - home_xfip)`, `f5Amplified = xera_gap >= 1.5`) | `config/rules.json`'s `edge_thresholds.f5Amplified.condition` documents the same 1.5 threshold. |
| Bullpen vulnerability | `api/bullpen.js`/`api/slate.js` (`elite: xFIP<3.50`, `vulnerable: xFIP>4.50`) | `fatigued` is hardcoded `false` everywhere — never actually computed. |
| Platoon splits | `api/savant.js` + `scripts/enrich_data.py` estimate platoon **stats**, but no script ever computes a directional "platoon edge" verdict. | No producer for `PLATOON_EDGE`. |
| Lineup confirmation + adjustment | `scripts/fetch_lineups.py` (`lineupConfirmedOfficial`, `lineupDataQuality`, `lineupAdjApplied`, the `xwOBA`-based `lineup_adj` applied in `scripts/enrich_data.py`) | The one dimension with a genuinely rich, multi-field signal. |
| Weather | `api/weather.js` fetches real data into `data/weather.json` | **Confirmed orphaned** — never merged into `slate.json`/`recommendations.json` by any script. `risk_gate.py` hardcodes `weatherAdjustment: None`. No producer for `WEATHER_OVER`/`WEATHER_UNDER`. |
| Park factor | `api/slate.js`'s static per-team lookup table (`park.parkFactor`) | Real, but static — not dynamically computed from conditions. |
| Umpire | Only appears as a whitelisted input-key name in `lib/yrfi_nrfi_validator.py`'s docstring | No producer anywhere. No producer for `UMPIRE_FACTOR`. |
| Workload / TTO / strikeout matchup | `api/enrich.js`'s `computeTTO` (`ttoSplit`, `ttoRisk`) is real and populated | No script diffs batter-vs-pitcher K% into a matchup verdict. No producer for `WORKLOAD_OVER`/`WORKLOAD_UNDER`/`STRIKEOUT_MATCHUP`/`CONTACT_MATCHUP`. |
| Market-expression rationale | `api/slate.js`'s `teamTotals.{away,home}TTReason`, `lineNote`, `paperReason`; `scripts/build_market_ledger.py`'s `rejectionReason`/`gatesFired` rule citations; **`scripts/reason_codes.py`'s already-controlled `reasonCodes` vocabulary** | The strongest existing "already-controlled-vocabulary" signal in the whole pipeline — reused directly for `PRICE_DISLOCATION` (`RAW_EDGE_STRONG`). |
| Correlation | `RULES.md`'s Rule 76 (human-authored, same-game-bet correlation) is **never operationalized in code** — `scripts/bet_eligibility.py` only string-matches for the literal substring "Rule 76" in already-fired gates, but nothing ever fires it. | No producer for `CORRELATED_POSITION`. |
| Model version / commit / config | No script takes CLI args or reads env vars for a version identifier. `config/rules.json._version` ("1.0") is the one real, manually-bumped config version that exists. | See §3. |
| Confidence | `scripts/build_market_ledger.py`: `HIGH`/`MEDIUM`/`PAPER` (not `LOW` — a 3-tier vocabulary, `PAPER` is a first-class pipeline tier, not an EdgeLab bolt-on). | |
| Data quality | `lineupDataQuality` (`full`/`partial`/`insufficient`/`none`) is the one real, populated data-quality signal reaching a marketLedger row. A separate, richer `dataQualityStatus` enum exists in `scripts/data_quality_gate.py` but **is not wired into the live pipeline** (only used by a downstream reporting script). | |

## 2. Model identity and provenance

| Field | Source | Meaning |
|---|---|---|
| `modelCommitSha` | `GITHUB_SHA` (Actions), else local `git rev-parse HEAD` | The git commit of **this repo checkout at ingestion time** — not necessarily the commit that produced the upstream `recommendations.json` (a separate, earlier workflow run: `fetch-slate.yml` builds it, `edgelab-postgame.yml` ingests it, and these can straddle a same-day push). Documented as a precision limitation, never claimed as more. |
| `modelConfigVersion` | `config/rules.json._version` | Real, existing, manually-bumped — not tied to git. |
| `probabilityAdapter` | Which of `kalshiVF`/`marketProbVF`/`executableMarketProb` actually supplied `marketImpliedProbability`, in that priority order | Lets a reader distinguish "this row's market price came from the raw Kalshi VF vs. an executable-price adjustment" without re-deriving it. |
| `modelSource` | The artifact's own `meta.producedBy` | Unchanged from Milestone 3. |
| `pipelineRunId` | The artifact's own `meta.createdAt` | The closest thing the pipeline exposes to a run identifier — already used as the idempotent versioning key, now also a queryable column. |
| `artifactSource` | The artifact's own `meta.stage` (`"recommendations"`) | Distinguishes today's transitional-status `recommendations.json` source from a future migration to the narrower, canonical `projections.json`, without string-parsing `provenance.sourceFile`. |
| `modelVersion` | — | **Stays null for every real record.** No script anywhere captures a model-algorithm version. An honest, unfixable-from-EdgeLab's-side gap (see §6). |

**Deliberately not new fields** (avoiding a second name for something that already exists — the same discipline Milestone 3 applied to `evPerDollar`/`modelSource`):
- `evaluationSource` ≡ the existing required `source` field (`"pipeline_recommendations"` / `"market_universe_extension"`).
- `generatedAt` ≡ the existing `createdAt` field.

## 3. Lineup confirmation state

Five-value controlled vocabulary, mapped from `scripts/fetch_lineups.py`'s
own fields (`lineupConfirmedOfficial`, `lineupPosted`, `lineupDataQuality`,
`lineupStatus`):

| Value | Condition |
|---|---|
| `CONFIRMED` | `lineupConfirmedOfficial=True` and `lineupDataQuality=='full'` |
| `PARTIAL` | Official but not fully resolved (`quality != 'full'`), or a real posted-but-non-official lineup with `partial`/`insufficient` quality |
| `PROJECTED` | A real, posted (or any non-"missing" status) lineup exists but isn't official |
| `UNCONFIRMED` | Actively checked and found not available (`lineupStatus=='missing'`, or `lineupPosted=False` with no other signal) |
| `UNKNOWN` | No lineup evidence fields present at all (checked too early) |

Only the `CONFIRMED`/`UNCONFIRMED`/`UNKNOWN` combinations have been
observed in the two real committed dates so far; `PARTIAL`/`PROJECTED`
are reachable per `fetch_lineups.py`'s own documented logic
(`MIN_BATTERS_FOR_CONFIRMED`, `lineupDataQuality` degrees) and covered by
synthetic fixtures in `tests/edgelab/test_evaluation_metadata.py`.

## 4. Thesis tags: what's populated, what isn't, and why

Every assigned tag has a one-line evidence string in `tagEvidence`
citing the exact source field(s)/threshold that justified it — never an
outcome-based inference. `lib.edgelab.model_evaluation.thesis_tags_and_evidence_for_row()`
validates every tag it emits against the Phase 1 controlled vocabulary
(`lib.edgelab.tags.validate_tags`) before returning, so a typo fails
loudly rather than silently writing an unrecognized tag.

| Tag | Rule | Source |
|---|---|---|
| `STARTER_EDGE` / `STARTER_FADE` | F5 ML markets only; `f5.f5Amplified=True`, tagged EDGE when `f5.favoredSide` matches this row's side, FADE when it opposes | `f5.xERAGap`/`favoredSide` |
| `BULLPEN_EDGE` | ML/F5-ML markets; **opponent's** bullpen is `vulnerable` | `away/home.bullpen.vulnerable` |
| `BULLPEN_DISADVANTAGE` | ML/F5-ML markets; this side's **own** bullpen is `vulnerable` | same |
| `LINEUP_EDGE` | `lineupConfirmedOfficial=True` and `lineupAdjApplied=True` and `lineupDataQuality=='full'` | `scripts/fetch_lineups.py` fields |
| `LINEUP_DOWNGRADE` | `lineupDataQuality` in `(partial, insufficient)` with real (incomplete) lineup evidence present | same |
| `PRICE_DISLOCATION` | `"RAW_EDGE_STRONG" in reasonCodes` | `scripts/reason_codes.py`'s already-controlled vocabulary |
| `MARKET_EXPRESSION` | Team-total markets with a non-null `{away,home}TTReason` | `api/slate.js`'s real rationale strings |
| `F5_OVER_FULL_GAME` | Any F5 market with `f5.f5Amplified=True` | same F5 signal, framed as "this F5 expression is preferred" |
| `PARK_FACTOR` | Total-type markets where `park.parkFactor != 100` | `api/slate.js`'s static park table |
| `PLATOON_EDGE`, `WEATHER_OVER`, `WEATHER_UNDER`, `UMPIRE_FACTOR`, `WORKLOAD_OVER`, `WORKLOAD_UNDER`, `STRIKEOUT_MATCHUP`, `CONTACT_MATCHUP`, `CORRELATED_POSITION` | **Never assigned** | No real producer exists anywhere in the pipeline (see §1) |

## 5. Confidence and data quality

- `confidence` reads `confidenceTier` first, falling back to a bare
  `confidence` key (the same priority `lib.edgelab.recommendations`
  already uses) — `confidenceSource` records which one supplied it.
  Vocabulary is the pipeline's own `HIGH`/`MEDIUM`/`PAPER` (not `LOW` —
  confirmed by tracing `build_market_ledger.py` directly).
- `dataQuality` is the marketLedger row's own `lineupDataQuality` — the
  only real, populated data-quality signal reaching this level today.
  `dataQualityReasons` copies `missingFields`/`lineupStatusReason`
  verbatim, never reworded or summarized.
- No label is ever converted into a probability — `confidence` and
  `dataQuality` remain categorical throughout the query layer (see
  `market_family_report`'s ordinal `avgConfidenceScore` in
  `docs/EDGELAB_CALIBRATION.md` for the one place a numeric encoding
  exists, and why it's explicitly flagged as an averaging convenience,
  not a probability).

## 6. Correlation-group semantics

Purely deterministic, name-based grouping — **no numerical correlation
estimate**, and never used to filter recommendations or size stakes.
`lib.edgelab.model_evaluation.correlation_groups_for_row()` assigns zero
or more of:

`GAME_SIDE_<team>`, `F5_SIDE_<team>`, `TEAM_RUNS_OVER_<team>`,
`STARTER_SUCCESS_<pitcher>`, `STARTER_FAILURE_<opposing pitcher>`,
`GAME_OVER`, `YRFI`, `NRFI`.

**Two-sided single-ticker markets** (e.g. a run-line spread's
`RL_Away`/`RL_Home`, sharing one Kalshi contract — the exact collision
Milestone 3 found and fixed for IDs) each map to **their own team's**
group, not a shared one: `RL_Away` → `GAME_SIDE_<away>`, `RL_Home` →
`GAME_SIDE_<home>`. This is deliberate — it's exactly what Rule 76 (never
operationalized in code, see §1) is actually concerned about: "ML + RL +
F5 + TT on the same team are the same bet" falls out naturally because
`ML_Away` and `RL_Away` both land in `GAME_SIDE_<away>`.

**Alternate-line markets** (the same team-total direction at a different
threshold) intentionally **collapse into the same group** regardless of
`threshold` — two `TT_Away_Over` rows at lines 4.5 and 5.5 are both
`TEAM_RUNS_OVER_<away>`, since they represent the same directional thesis
at a different price, not an independent one.

`Game_Total` maps to `GAME_OVER` unconditionally: `config/rules.json`'s
`market_list` has exactly one `Game_Total` entry (no paired
`Game_Total_Under`), so the unqualified name represents the over
expression by this config's own naming convention, matching
`TT_*_Over`'s explicit suffix. If a future config ever adds a
`Game_Total_Under`-named market, this mapping needs revisiting.

`CORRELATED_POSITION` (the thesis tag, distinct from `correlationGroups`
the metadata array) has no producer — see §1 and §4.

## 7. Historical backfill

`scripts/edgelab/backfill_evaluation_metadata.py` is a **one-time
migration tool**, not part of the ongoing production path. It exists
because the *computation itself* changed underneath 275 already-committed
`ModelEvaluation` records (Milestone 3's version never computed
`modelCommitSha`/`thesisTags`/`correlationGroups`/etc.) — not because a
new pipeline run occurred. It recomputes each record fresh from the
**same, unchanged** `data/pipeline/<date>/recommendations.json` artifact
and uses `storage.upsert_records` to replace each row **in place, by the
same `modelEvaluationId`** — never inventing new information, never
duplicating rows. It flags (never silently overwrites) any record whose
immutable core facts (`evaluationStatus`, `modelFairProbability`,
`estimatedEdge`, `marketTicker`) differ from the freshly recomputed
version, which would indicate the source artifact itself changed.

Regular production ingestion (`scripts/edgelab/build_recommendations.py`)
is unaffected and continues using `storage.append_records` (a deliberate
no-op on rerun against an unchanged artifact) for pipeline-derived rows —
once a `ModelEvaluation` is recorded going forward, it stays immutable,
exactly as designed in Milestone 3.

### Results (2026-07-30 + 2026-07-31, 275 records total, 0 conflicts)

| Field | Before | After |
|---|---|---|
| `modelCommitSha` | 0% | 100% |
| `modelConfigVersion` | 0% | 100% |
| `probabilityAdapter` | 0% | 18.2% (07-30) / 30.3% (07-31) |
| `confidenceSource` | 0% | 10.9% (07-30) / 14.6% (07-31) |
| `thesisTags` | 0% | 57.3% (07-30) / 74.5% (07-31) |
| `correlationGroups` | 0% | 100% |
| `dataQualityReasons` | 71.8% (07-30) / 58.2% (07-31) | (unchanged shape, now populated per-row) |
| `pipelineRunId` / `artifactSource` | 0% | 100% |

## 8. Analytics and calibration integration

`lib/edgelab/analytics.py`'s `v_model_evaluations` canonical view exposes
every new column (with the same column-existence-safe casting Milestone
1-3 established, so a file missing any of these columns entirely still
queries cleanly). `v_placed_bets` gains three new **join-only**
pass-through columns with no `PlacedBet`-side equivalent to fall back to
(`modelSource`, `dataQuality`, `correlationGroups` — null/empty when no
link resolves, never fabricated).

Three new calibration dimensions in `lib/edgelab/calibration.py`, using
the **exact same** `INSUFFICIENT_SAMPLE`/`DESCRIPTIVE_ONLY`/`CALIBRATED`
gate as every existing dimension — Milestone 4 does not loosen or bypass
it:

- `model_version_source_calibration()` — groups decided bets by
  `(modelVersion, modelSource)`.
- `data_quality_calibration()` — groups by `dataQuality`.
- `correlation_group_calibration()` — `UNNEST`s `correlationGroups` (a
  bet can belong to more than one group and contributes to each).

`tagEvidence` (a per-record dict, not a scalar/array) is deliberately
**not** exposed as a SQL column — it's an audit trail meant to be read
directly off a specific record's JSONL line, not aggregated.

## 9. Data-quality report

`scripts/edgelab/run_model_evaluation_report.py` (extended from
Milestone 3) now also breaks down population rates by:

- **Date** (`population_by_date`) — via the same filename-regexp
  convention every other date-partitioned entity uses.
- **Recommendation status** (`population_by_recommendation_status`) —
  joins to `v_recommendations` by `recommendationId`.
- **Unresolved/conflicting metadata** (`unresolved_metadata_report`) — a
  concrete, groundable query: how many fully `EVALUATED` records are
  missing `confidence` or all lineup evidence. A non-zero count is a
  genuine upstream data gap for that specific row, not a query defect.

(Breakdowns by canonical family and model version/source already
existed from Milestone 3.)

## 10. Honest limitations

- `modelVersion` is null for every real record — no fix possible from
  EdgeLab's side alone; the upstream pipeline would need to start
  stamping a model-algorithm version somewhere.
- 7 of the 19 controlled thesis tags have no real producer and are
  permanently absent (§1/§4) — this is a pipeline gap, not something a
  smarter mapping function could close without inventing new
  handicapping logic (explicitly out of scope).
- `CORRELATED_POSITION` (the tag) has no producer; `correlationGroups`
  (the metadata array) is fully implemented as a **descriptive**
  grouping, deliberately not a replacement for the still-unimplemented
  Rule 76 gate.
- Weather data is fetched (`data/weather.json`) but orphaned — never
  merged into the pipeline artifacts this module reads. Populating
  `WEATHER_OVER`/`WEATHER_UNDER` would require a pipeline change outside
  this milestone's scope (wiring the fetch into `build_market_ledger.py`),
  not a smarter EdgeLab-side mapping.
- `modelConfigVersion` only differentiates when `config/rules.json`'s own
  `_version` is bumped — today it's a single value ("1.0") across all
  real data, so this dimension can't yet show a config-version
  comparison in practice.
