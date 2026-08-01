# EdgeLab Phase 2: Analytics & Research Platform — Architecture Design

Status: **design only, not implemented.** PR #26 (Phase 1: collection and
linkage) is approved in principle. This document is the architecture
review that should guide the next several small PRs. No Phase 2 code is
in this document; every recommendation below was checked against the
actual Phase 1 code and the actual committed data, not assumed.

---

## 1. Current capability assessment

### 1.1 What can already be answered today

Directly, with existing fields, no schema change:

| Question | Method | Confidence |
|---|---|---|
| What was bet, when, at what price, for how much? | `PlacedBet` alone | High |
| Did a specific bet win or lose, and what was the P/L? | `PlacedBet.result`/`netProfitLoss` (once `scripts/edgelab/settle_markets.py` runs) | High |
| What was the CLV on a specific bet? | `PlacedBet.clv` (tested, formula documented) | High |
| Was a specific market observed at all, and what did its price do over time? | `MarketObservation` time series, keyed by `marketTicker` | High |
| What was the official closing quote for a market? | `ClvQuote.isClosingQuote` | High |
| Was a market recommended, passed, or never evaluated, and why? | `Recommendation.status`/`passReason` | High |
| Did a market settle, and what would a flat $1 YES stake have returned at each checkpoint? | `Settlement.result`/`hypotheticalReturnsByCheckpoint` | High, but only for `game_result`/`inning_result`(F3/F5/F7)/`game_total`/`inning_total`/`team_total`/`winning_margin`/`first_inning_run` — **7 of 17 families (all player props) are `SETTLEMENT_UNRESOLVED` by design, not a bug** |

All of the above are answerable **for one date at a time**, by reading
that date's JSONL partition(s) directly in Python. This is what
`scripts/edgelab/generate_daily_report.py` already does.

### 1.2 What cannot yet be answered — and precisely why

This is the important part: for each question in the prompt, the gap is
either (a) an **architecture gap** (the data exists per-day but nothing
can query across days), (b) a **data gap** (the field exists but is
never populated by any current writer), or (c) a **missing
implementation** (settlement logic that doesn't exist yet). Conflating
these would lead to the wrong fix, so each is labeled.

| Question | Gap type | Detail |
|---|---|---|
| Which market family produces the highest ROI? | **(a) + data quality** | `PlacedBet.marketFamily` is free text copied at ingestion time. Measured on the real, committed `bets.jsonl` right now: **11 different spellings across 77 rows** — `KXMLBTEAMTOTAL`, `ML`, `F5_ML_Away`, `ML_Away`, `KXMLBGAME`, `TT_Away_Over`, `KXMLBRFI`, `F5 ML`, `YRFI`, `KXMLBF5`, `ML_Home` — several of these refer to the *same* family under a different naming convention (`KXMLBGAME`/`ML`/`ML_Away`/`ML_Home` are all moneyline). Grouping by this field directly would silently fragment the aggregate. There is also no cross-date query surface at all today. |
| Which market family produces the best CLV? | **(a)** only — `PlacedBet.clv` itself is clean and tested; just needs cross-date aggregation, joined to the *canonical* family (see below) |
| Which market family is most mispriced? | **(a)** — needs `Recommendation.modelFairProbability` vs `marketImpliedProbability` vs `Settlement.result`, joined by `marketTicker`, across all dates. The daily calibration export (`reports/<date>_calibration.jsonl`) already computes the per-market join correctly; it just isn't aggregated across dates yet. |
| Which market expressions outperform alternatives? | **(b)** — `Recommendation.comparisonMarkets` and `PlacedBet.correlationGroup` exist in the schema specifically for this, but **are never populated by any code path** (`comparisonMarkets` is hardcoded `[]` in both `recommendations.py` call sites; `correlationGroup` defaults to `None` everywhere except the manual-entry CLI, and 0 of the 77 real bets have it set). This is not fixable by querying harder — the clustering logic itself doesn't exist yet. |
| Which recommendation types perform best? | **(a)** — `Recommendation.status` joined to eventual `Settlement`/`PlacedBet` outcome, across dates |
| Which thesis tags are predictive? | **(b), severe** — `PlacedBet.thesisTags` is a real, tested, controlled vocabulary, but **0 of 77 real bets have any tag set** (only reachable via the manual-entry CLI's `--tag` flag; legacy-ledger ingestion never sets it and never can, since neither legacy ledger recorded a thesis at the time). This is not a code gap, it's an **adoption/retro-tagging gap** — no amount of query engine work fixes it. |
| Which confidence levels are calibrated? | **(a)** only — confidence vocabulary is actually already clean (`HIGH`/`MEDIUM`/`LOW`/`PAPER`, 4 consistent values across all 77 real bets, no normalization needed). Just needs cross-date aggregation. |
| Which pass reasons are correct? | **(a)** — requires joining `Recommendation.status='PASS_*'` rows to `Settlement` for the *same ticker* to see what would have happened. Both sides of this join already exist; only the cross-date query surface is missing. |
| Which markets consistently beat the close? | **(a)** — `PlacedBet.clv` trend by family/ticker over time; needs history depth (currently 1 day of committed reports, 77 bets total, 12 with CLV computed) before this is statistically meaningful at all |
| Which markets should we stop betting? | **(a) + new capability** — this is a *synthesis* of ROI + CLV + calibration + **sample-size significance**, which does not exist in Phase 1 at all (no statistical test, no minimum-n gate anywhere). This is the one question that needs genuinely new analytical logic, not just aggregation. |

### 1.3 The one-sentence summary

**Phase 1 built the right joins; Phase 2's job is (1) a cross-date query
surface, (2) a canonical-family/canonical-vocabulary normalization layer
so the join keys are trustworthy, and (3) the market-expression-grouping
and statistical-significance logic that genuinely doesn't exist yet.**
Nothing here requires re-collecting data or breaking the Phase 1 schema.

---

## 2. Should any schema changes happen before historical data accumulates?

Current real data volume: **77 bets, 1 day of committed reports, ~2,760
markets/day**. This is the cheapest this migration will ever be. Two
changes are recommended *now* (as the first Phase 2 PR, before volume
grows), and several candidate changes are explicitly rejected below with
reasoning — over-migrating the schema before the actual analytics
queries are written is its own risk (see §9, Risks).

### 2.1 Recommended additive schema changes (low risk, do first)

1. **Add `sport` (default `"MLB"`) to `Game`, `Market`,
   `MarketObservation`, `PlacedBet`, `Recommendation`, `Settlement`.**
   Every module today is implicitly MLB-only (`lib/kalshi_mlb_*`,
   `data/edgelab/<entity>/<date>.jsonl` with no sport in the path). If
   EdgeLab ever covers a second sport, retrofitting this after a season
   of MLB-only data means either a lossy backfill or two incompatible
   schema eras. Adding it now, as an optional field defaulting to
   `"MLB"`, costs nothing today and is nearly free to migrate (77 rows).
2. **Add `platform` (default `"KALSHI"`) to the same entities.** The
   existing `source` field means "which internal EdgeLab
   system/script produced this record" (`"pipeline_recommendations"`,
   `"manual_entry"`, etc.) — it is not a venue/bookmaker discriminator
   and reusing it for that would be a genuine naming collision. If a
   second prediction market or sportsbook is ever added, this field is
   needed and is equally cheap now.

Both are pure additive fields (JSON Schema optional, default value at
write time) — no migration script needed beyond re-running the existing
backfill scripts once, no consumer breaks (old readers ignore new
fields, per the schema's own versioning policy in
`data/edgelab/schema_v1/README.md`).

### 2.2 Explicitly NOT recommended yet (and why)

- **Do not pre-populate `correlationGroup`/`comparisonMarkets` via a
  schema/ingestion change yet.** The clustering algorithm that would
  decide "which tickers express the same thesis" doesn't exist yet
  (§5). Writing *something* into these fields now, before the algorithm
  is designed, risks baking in a wrong grouping that then has to be
  migrated again. Recommendation: derive these relationships at
  **analytics query time** (a SQL `GROUP BY`/self-join over `gameId` +
  `team`/`player`), not at ingestion time, until the grouping logic is
  validated. Revisit populating the field for real once the algorithm
  is proven.
- **Do not add a `ModelEvaluation` ingestion path yet.**
  `Recommendation.modelFairProbability`/`marketImpliedProbability`/
  `estimatedEdge` already carry everything Phase 2's calibration work
  needs. A separate `ModelEvaluation` record only earns its keep once
  there's a concrete need to track *multiple model versions* predicting
  the same market — building it speculatively now is exactly the kind
  of premature abstraction Phase 1 correctly avoided elsewhere.
- **Do not try to fix historical `PlacedBet.marketFamily`/`thesisTags`
  values.** The family-name inconsistency is a property of two legacy
  ledgers that no longer exist as active write paths — future bets
  logged via `log_bet.py` or ingested against a canonicalized join don't
  have this problem. Retroactively guessing which of 11 spellings maps
  to which canonical family for 77 old rows is low-value, error-prone
  work; the analytics layer should instead **always re-derive the
  canonical family via a join to `Market.marketFamily`** (see §4) and
  treat `PlacedBet.marketFamily` as informational/display-only, never
  as a GROUP BY key.
- **Do not fix `MarketObservation.lineupConfirmationState` as a schema
  change.** The field is correctly designed; it's simply never wired up
  (`lib/edgelab/market_universe.py:201` hardcodes `None`). This is an
  **implementation gap**, tracked in the roadmap (§10, milestone 6), not
  a schema gap.

---

## 3. Analytics layer architecture

### 3.1 Design principle: views over files, not a second copy of the data

The append-only JSONL(.gz)/upserted partitions built in Phase 1 remain
the single source of truth — nothing in Phase 2 should introduce a
second place that "owns" bet/observation/settlement data. The analytics
layer is a **read-side transformation**: a set of canonicalizing SQL
views computed over the existing files, materialized only as
disposable, regeneratable exports (Parquet) or query-time results
(reports), never as a second mutable ledger.

### 3.2 The canonical fact/dimension model

All of it computed via SQL views (DuckDB, see §4), not new stored files:

```
dim_market   (from Market, one row per marketTicker, EVER — canonical
              marketFamily/marketHorizon/team/player/threshold/outcomeLabel)
dim_game     (from Game, one row per gameId)

fact_observation    (MarketObservation, grain: one row per
                     marketTicker × capturedAt — the full price history)
fact_clv_quote      (ClvQuote, grain: one row per marketTicker × capturedAt,
                     checkpoint-filtered/bet-prioritized subset of the above)
fact_recommendation (Recommendation, grain: one row per marketTicker per
                     research run/day — the decision history)
fact_settlement     (Settlement, grain: one row per marketTicker EVER —
                     UNNEST hypotheticalReturnsByCheckpoint for
                     per-checkpoint hypothetical-return analysis)
fact_bet            (PlacedBet, grain: one row per betId)
```

`fact_bet` and `fact_recommendation` are **always joined to `dim_market`
by `marketTicker` to get the canonical `marketFamily`/`marketHorizon`/
`team`/`player`** — their own denormalized `marketFamily`/`team` fields
are convenience copies, never the join key for aggregation. This one
rule directly fixes the 11-spellings-across-77-rows problem without
touching a single historical row.

### 3.3 Canonicalization view (the actual fix for §1.2's #1 finding)

A single, versioned SQL view — e.g. `v_canonical_family` — maps every
messy historical spelling to one of the 17 real family names, built
once from the *known* mapping (`ML`/`ML_Away`/`ML_Home`/`KXMLBGAME` →
`game_result`, etc.), with an explicit `UNRECOGNIZED_FAMILY_SPELLING`
bucket for anything that doesn't match (never silently dropped or
guessed). This view is the only place the legacy vocabulary mess is
handled; every other query reads through it.

### 3.4 What the analytics layer is *not*

Not a recommendation engine, not a staking model, not a live dashboard.
Phase 2 (per this design) produces **descriptive statistics and
reports**, consumed by a human deciding what to research next — the
same posture Phase 1 held for collection.

---

## 4. Query engine: recommendation and evaluation

### 4.1 Options considered

| Option | Verdict | Reasoning |
|---|---|---|
| **DuckDB** | **Recommended, primary** | Embedded (no server/daemon), single `pip install duckdb`, reads JSONL/gzip-JSON/Parquet directly via SQL with zero ETL step, genuinely fast columnar analytics (window functions, `GROUP BY`, `UNNEST` for the settlement checkpoint arrays), trivial `COPY ... TO 'x.parquet'` export. Runs fine in an ubuntu GH Actions runner with no special setup, no lingering process. |
| **SQLite** | Rejected as primary, fine as a niche cache | Row-oriented; workable at our volumes but meaningfully worse ergonomics for the analytical queries this needs (bucketed calibration aggregates, `UNNEST`-style array expansion, multi-way joins across 6 entities). Zero extra dependency (stdlib), which is its one advantage — not enough to outweigh DuckDB's fit for this specific workload. |
| **Parquet** | Recommended, as the *export format*, not a query engine | Not a competitor to DuckDB — DuckDB reads/writes it natively. Columnar + dictionary encoding compresses this repetitive, low-cardinality data (tickers, families, enums) even better than gzip'd JSONL, and gives predicate/column pushdown so a query scanning "all settled bets in July" doesn't have to parse every field of every row. |
| **JSONL (current)** | Keep, as the source of truth, not the query layer | Exactly right for what it already does — git-diffable, append-only, human-inspectable, dedup-friendly. Wrong tool for repeated cross-date aggregation (no indexing, full-file parse every query). |
| **Hybrid (recommended)** | JSONL(.gz) source of truth → DuckDB reads it directly (or via a disposable Parquet export) → small result tables committed to git | See §4.2. |

### 4.2 Recommended architecture

```
data/edgelab/*/<date>.jsonl(.gz)   <- unchanged, Phase 1, git-committed, source of truth
              │
              │  DuckDB reads these paths directly via glob
              │  (read_json_auto('data/edgelab/observations/*.jsonl.gz'), etc.)
              ▼
     [ ephemeral in-process DuckDB session, one per analytics run ]
              │
              │  canonicalization views (§3.3) + fact/dim views (§3.2)
              │  + the actual named queries (ROI by family, calibration
              │  bins, market comparison, etc.)
              ▼
   small, human-readable RESULT tables (JSON + Markdown + CSV)
              │
              ▼
   data/edgelab/reports/{daily,weekly,monthly,season}/...  <- git-committed
```

**Nothing new is committed to git except more (small) reports.** DuckDB
itself is never persisted as a `.duckdb` file in the repository — binary
database files diff terribly in git and would reintroduce exactly the
storage problem gzip just solved for `MarketObservation`. Each analytics
run builds its views fresh from the committed JSONL/gzip source (this
takes seconds at current and even season-scale volume) and discards the
DuckDB session on exit.

**Parquet's role**: an *optional*, regeneratable, GitHub Actions
**artifact** (not a git commit) produced by a "build analytics export"
step, for ad hoc offline exploration (a notebook, a BI tool) — never the
system of record, never git-committed, consistent with the original
Phase 1 storage guidance's "GitHub Actions artifacts for
raw/high-frequency data, Parquet for longer-term analytical exports."

### 4.3 Why not commit Parquet to git anyway?

It was tempting (Parquet compresses this data even better than gzip), but:
- It's fully derived from data already in git — committing it is pure
  duplication with no new information, just a different encoding.
  git already lost that argument once (`kalshi_registry_snapshots/` +
  `MarketObservation` both encode the same underlying prices in two
  formats) — Phase 2 shouldn't repeat it a third time.
- A committed Parquet file is a binary blob; every regeneration is a
  full-file diff in git history forever, unlike JSONL's line-level diffs.
- Regenerating it from source takes seconds, not hours — there's no
  performance reason to persist it permanently.

---

## 5. Calibration design

### 5.1 Shared methodology (applies to every dimension below)

A single deterministic function, parameterized by *bucketing dimension*:

```
for each bucket:
  n                     = count of settled bets/markets in this bucket
  mean_model_prob        = avg(modelFairProbability) for this bucket
  actual_win_rate        = wins / n
  calibration_gap        = actual_win_rate - mean_model_prob
  status                 = "CALIBRATED" | "OVERCONFIDENT" | "UNDERCONFIDENT"
                           | "INSUFFICIENT_SAMPLE" (n < MIN_N)
```

`MIN_N` (proposed default: **20**) is a hard, documented, non-negotiable
gate — a bucket with 3 settled bets showing "100% win rate" is noise, not
a finding, and must be reported as `INSUFFICIENT_SAMPLE`, never as a
green light. This directly serves "which markets should we stop
betting?" — a confident answer requires the sample-size gate to exist at
all, which nothing in Phase 1 has.

No ML model is trained here — this is descriptive statistics (a
reliability table), matching Phase 1's own "no black-box optimizer"
posture. A full Brier-score decomposition can be layered on later if
warranted; the reliability table alone already answers every calibration
question in the prompt.

### 5.2 Per-dimension application

| Dimension | Bucket key | Data readiness |
|---|---|---|
| Edge bucket | `estimatedEdge` binned (e.g. every 2 points, or quartiles) | Ready — `Recommendation.estimatedEdge` populated for all model-evaluated markets |
| Confidence | `confidence` (already clean: `HIGH`/`MEDIUM`/`LOW`/`PAPER`) | Ready |
| Market family | canonical family via `dim_market` join (§3.3) | Ready once the canonicalization view exists |
| Thesis tag | `thesisTags` (array — one bet can belong to multiple buckets) | **Not ready** — 0% real coverage today (§1.2). Report as `INSUFFICIENT_SAMPLE` for all tags until retro-tagging or new tagged volume accumulates; do not skip the dimension, just be honest about its emptiness |
| Lineup status | `MarketObservation.lineupConfirmationState` | **Not ready** — field never populated (§2.2); needs the wiring fix in the roadmap before this dimension can report anything |
| Timing (checkpoint) | `ClvQuote.checkpoint` | Ready — already populated and tested |
| CLV bucket | `PlacedBet.clv` binned (e.g. every 5 cents) | Ready |

---

## 6. Market comparison design

### 6.1 What's needed, and what already exists

Every comparison in the prompt (ML vs F3/F5/F7, run line vs ML, team
total vs pitcher outs, strikeouts vs outs, correlated markets) reduces to
one operation: **cluster markets for the same underlying game/player by
shared metadata, then compare their price/edge/outcome across the
cluster.** The metadata needed is already on `dim_market`:

| Comparison | Cluster key |
|---|---|
| ML vs F3 vs F5 vs F7 (same team) | `gameId` + `team` + `outcomeLabel` (excluding horizon) |
| Run line vs ML | `gameId` + `team`, across `winning_margin` and `game_result` families |
| Team total vs pitcher outs | `gameId` + `team` (team total) joined loosely to the opposing/same pitcher's props via `player` — genuinely two different bet types being compared, not a natural cluster; treat as a cross-family correlation study, not a same-thesis group |
| Strikeouts vs outs (same pitcher) | `gameId` + `player`, across `pitcher_strikeouts`/`pitcher_outs` |
| Correlated markets generally | `gameId` (coarsest), refined by `team`/`player` |

**No new stored field is required for this** — it's a `GROUP BY`/
self-join at query time over existing `dim_market` columns (§2.2's
reasoning for not pre-populating `correlationGroup` yet). Once a
clustering approach is validated against real data, *then* it's worth
considering writing the resolved group id back onto `PlacedBet`/
`Recommendation` for fast lookup — but that's an optimization for later,
not a Phase 2 prerequisite.

### 6.2 What a comparison query actually measures

For a cluster, compute the same reliability/ROI/CLV statistics as §5,
grouped by family/horizon within the cluster, so "which expression
outperforms" is answered by the same deterministic methodology, not a
bespoke one.

---

## 7. Reporting design

### 7.1 One generator, parameterized by window

Rather than four bespoke scripts, one generator
(`scripts/edgelab/generate_periodic_report.py`, Phase 2 roadmap item)
parameterized by `--period {daily,weekly,monthly,season,historical}`
and an end date, computing the appropriate start date and reusing the
exact same DuckDB queries `generate_daily_report.py` already established
the pattern for. Every period gets both a human-readable Markdown and a
machine-readable JSON output, exactly like the existing daily report —
no new output convention to learn.

### 7.2 What's added at wider windows (not meaningful at daily grain)

- Calibration tables (§5) — need enough settled volume to mean anything.
- ROI/CLV by canonical family (§3.3) and by cluster (§6).
- Trend lines (week-over-week CLV drift by family).
- "Should we stop betting this?" flags (§5.1's `MIN_N`-gated
  `OVERCONFIDENT`/negative-ROI buckets) — surfaced only at weekly+
  granularity, never daily (a single bad day is not a signal).

### 7.3 Historical report

A special case of "season" with no end date — effectively "as far back
as the JSONL history goes." Same generator, same queries, just the
widest date range. No separate code path.

---

## 8. Future-proofing

Addressed primarily by §2.1's `sport`/`platform` fields. Beyond that:

- **New market family**: already handled — `marketFamily` is an open
  string, not a hardcoded enum in the JSON Schema, and the strict
  registry's `NEW_UNCLASSIFIED_MLB_SERIES` warning path (already built in
  Phase 1) is the correct model for "flag it, never silently include or
  silently exclude it" when Kalshi adds a new series.
- **New sportsbook/platform**: `platform` field (§2.1) plus, if odds
  format ever differs from Kalshi's 0-100 cents convention, a documented
  price-normalization step at ingestion (not needed yet — no second
  platform exists).
- **New sport**: `sport` field (§2.1) plus sport-specific settlement
  logic living in its own module (mirroring how `lib/f5_settlement.py`
  is MLB-specific already) — the schema itself doesn't need to know
  about innings vs quarters vs sets, only that a `sport` discriminator
  exists so per-sport settlement code can dispatch correctly.

---

## 9. Risks

1. **Premature schema churn.** Adding fields "just in case" beyond
   `sport`/`platform` (e.g., speculatively designing a `correlationGroup`
   population scheme before the clustering algorithm is validated) risks
   a second, wrong migration later. Mitigation: this document explicitly
   scopes schema changes to the two low-risk additive fields and defers
   everything else to query-time logic.
2. **Sample size.** 77 bets, 14 settled, 12 with CLV, 1 day of committed
   reports. Every "calibration"/"ROI by family" query will be
   statistically weak for months. Mitigation: the `MIN_N` gate (§5.1) is
   mandatory, not optional, specifically to prevent shipping a confident-
   sounding but noise-driven "stop betting X" recommendation.
3. **Thesis tag adoption.** Without a deliberate retro-tagging effort or
   a habit change in how bets are logged going forward, `thesisTags`
   analytics will report `INSUFFICIENT_SAMPLE` indefinitely. This is a
   process gap, not something the query engine can fix.
4. **Canonicalization view maintenance.** The `v_canonical_family`
   mapping (§3.3) is a manually maintained lookup table; a new legacy
   spelling that appears in future ingested data (unlikely, since the
   two legacy ledgers are no longer active write paths, but possible via
   manual entry typos) needs an explicit `UNRECOGNIZED_FAMILY_SPELLING`
   bucket, never a silent drop — already accounted for above, but worth
   re-stating as an operational risk to watch.
5. **DuckDB as a new dependency.** Low risk (mature, widely used,
   embedded, no daemon), but it is a new third-party dependency this
   repo doesn't currently have anywhere. Should be pinned to a specific
   version in whatever the analytics scripts' requirements file ends up
   being, same as any other dependency.
6. **Reports proliferation.** Four new report cadences × two output
   formats is 8 new file types; without care this could sprawl the way
   the pre-EdgeLab repo's `data/` directory already has (documented in
   the original Phase 1 audit as `docs/SOURCE_OF_TRUTH_MAP.md`'s
   "deprecation candidate" findings). Mitigation: one generator, one
   naming convention (`data/edgelab/reports/<period>/<date-or-range>.{md,json}`),
   documented once.

---

## 10. Phase 2 roadmap — small, PR-sized milestones

Ordered so each milestone is independently mergeable and testable, and
later milestones depend only on earlier ones (never forward references).

1. **Schema PR**: add `sport`/`platform` optional fields (§2.1) to the 6
   affected schemas + `README.md`; update the existing Phase 1 writers to
   set the defaults; no behavior change otherwise. Smallest possible PR,
   unblocks nothing else but should land first while it's cheapest.
2. **DuckDB analytics scaffold**: add `duckdb` as a dependency,
   `lib/edgelab/analytics.py` with a single `connect()` helper that
   registers the standard views over the current JSONL(.gz) glob paths
   (§3.2), and one trivial query (e.g. "count of bets by canonical
   family") proving the plumbing end to end. No reports yet.
3. **Canonicalization view** (§3.3): the `v_canonical_family` mapping,
   built and tested directly against the real 11-spelling finding from
   §1.2 — this is the PR that actually fixes the ROI-by-family question.
4. **Cross-date aggregate queries**: ROI by family, CLV by family, pass-
   reason correctness (§1.2's "which pass reasons are correct"), each as
   a small, independently tested query function — no report output yet,
   just the SQL + Python wrapper + unit tests against fixture data.
5. **Calibration module** (§5): the shared bucketing/`MIN_N`-gate
   function, applied first to the two dimensions that are actually
   ready today (edge bucket, CLV bucket) — defer thesis-tag/lineup-status
   calibration to milestone 8/9 below, once their data gaps are closed.
6. **Lineup status wiring**: populate
   `MarketObservation.lineupConfirmationState` at ingestion time by
   joining the slate's `lineupConfirmed` boolean — a small, contained fix
   to `lib/edgelab/market_universe.py`, unblocks calibration-by-lineup-
   status.
7. **Market comparison queries** (§6): the cluster-by-`gameId`/`team`/
   `player` queries, tested against fixture data with a synthetic
   multi-family same-game scenario.
8. **Periodic report generator** (§7): parameterized daily/weekly/
   monthly/season/historical, reusing milestones 3-7's queries.
9. **Thesis-tag retro-tagging tool** (process fix, small script): a
   CLI to attach `thesisTags`/`rationale` to existing settled bets after
   the fact, so calibration-by-tag has *some* real data to report on
   sooner than "wait for new tagged bets to accumulate."
10. **Parquet export as a GH Actions artifact** (§4.3): optional,
    lowest priority — only worth doing once someone actually wants to
    explore the data in a notebook/BI tool outside the report pipeline.

---

## 11. Suggested implementation order (condensed)

`1 → 2 → 3 → 4 → 6 → 5 → 7 → 8 → 9 → 10`

(6 before 5's full scope, since calibration-by-lineup-status needs 6's
wiring fix; 9 can happen any time after 1, in parallel with 4-8, since it
doesn't depend on the analytics layer at all — it's just a ledger-editing
CLI.)

---

## 12. Deliverables recap

- **Current capability assessment**: §1.
- **Remaining architecture gaps**: §1.2's table (cross-date query
  surface is the dominant one; a few unpopulated fields; two genuinely
  missing capabilities — market-expression clustering and statistical
  significance gating).
- **Recommended analytics architecture**: §3 (view-based fact/dimension
  model over existing files, canonicalization view as the fix for the
  vocabulary-inconsistency finding).
- **Storage/query recommendation**: §4 — DuckDB (primary, embedded,
  reads JSONL/Parquet natively) + Parquet (optional export artifact, not
  git-committed) + JSONL (unchanged source of truth). SQLite rejected
  as primary; fine as a future niche cache if ever needed.
- **Proposed schema adjustments**: §2 — two additive fields
  (`sport`, `platform`) now; everything else deferred with reasoning.
- **Phase 2 roadmap**: §10, 10 small milestones.
- **Risks**: §9.
- **Suggested implementation order**: §11.
