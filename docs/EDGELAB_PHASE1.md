# EdgeLab Phase 1: durable MLB/Kalshi research collection and linkage

Status: foundation phase — collection and linkage only. No staking
engine, no Kelly sizing, no auto-betting. Branch: see the PR this
document ships with; not merged.

> See also `docs/MARKET_RESEARCH_CORPUS_AND_MANUAL_LOGGING.md` for the
> MLB Market Research Corpus & Frictionless Manual Logging milestone,
> which wires up this document's previously-unpopulated
> checkpoint/pregame-validity fields, adds a growth-controlled retention
> filter, and extends collection to unclassified MLB series.

## 0. Why this exists

The production Kalshi price-checker and slate pipeline are correct and
should stay untouched. What's missing is a durable, linked research
layer: every market observed (not just recommended/bet ones), every
decision made about it, every bet actually placed, its price history,
its closing line, its settlement, and its CLV — all traceable back to
raw evidence with stable IDs. EdgeLab Phase 1 builds that layer by
**ingesting from the systems that already exist**, not by replacing them.

## 1. Audit findings (what already existed, reused rather than duplicated)

| Existing system | What it does | How EdgeLab uses it |
|---|---|---|
| `lib/kalshi_mlb_single_game_registry.py` + `lib/research/market_taxonomy.py` | Strict 17-family MLB market allowlist, family/scope classification | `lib/edgelab/market_universe.py` calls `classify_series_for_price_check()` and `classify_market()` directly — the exact same gate and classifier production uses |
| `lib/kalshi_mlb_contract_parser.py` | Ticker → canonical fields (gameId fallback, away/home, prices) | Reused via `parse_contract()`, not re-derived |
| `data/kalshi_registry_snapshots/*.json` | Every Kalshi fetch archived as a new timestamped file (true time-series, 359+ files, ~109MB) | The raw evidence EdgeLab's `provenance.sourceFile` points at — never re-fetched, never duplicated in full |
| `data/pipeline/<date>/normalized_slate.json`, `recommendations.json`, `execution.json` | Per-date immutable pipeline-stage artifacts (`lib/pipeline_artifacts.py`) | `gameId`/`scheduledStartTime` join source; `recommendations.json`'s `marketLedger` rows and `execution.json`'s candidates are the input to EdgeLab's Recommendation ledger |
| `bets.json` / `data/bets.json` | Two pre-existing, differently-shaped placed-bet ledgers | Reconciled field-by-field into one canonical `PlacedBet` shape (`lib/edgelab/bets.py`); neither legacy file is modified |
| `scripts/clv_from_snapshot.py` | Primary CLV formula + closing-quote selection ladder | Formula and `implied_to_american()` reused verbatim by `lib/edgelab/clv.py` |
| `lib/f5_settlement.py`, `lib/research/inning_result_settlement.py` | F5 (and now F3/F5/F7, since `market_taxonomy.HORIZON_MARKET_STATUS` confirms all three `CONFIRMED_THREE_WAY`) three-way settlement | `settle_inning_result()` reused directly by `lib/edgelab/settlement.py` |
| `scripts/build_wager_research_db.py` | Existing calibration-ready wager export from `bets.json` | Left untouched — EdgeLab's calibration export (`reports/<date>_calibration.jsonl`) is a distinct, narrower export (model-prob-vs-settled-result only), not a replacement |
| 10 existing `.github/workflows/*.yml` | Production capture/settlement cadence | None modified. New EdgeLab workflows chain off their `workflow_run` completion, following the exact same concurrency/rebase-retry/diff-guarded-commit pattern already used by `discover-kalshi-mlb-markets.yml`/`build-wager-research.yml` |

Full findings (exact field names, file:line references) are in this
PR's audit summary; not duplicated here.

## 2. Schema (`data/edgelab/schema_v1/`)

Nine entities, each with `schemaVersion`, `createdAt`/`updatedAt`,
`source`, `validationStatus`, and a `provenance` pointer back to raw
evidence: `Game`, `Market`, `MarketObservation`, `ModelEvaluation`,
`Recommendation`, `PlacedBet`, `ClvQuote`, `Settlement`,
`ResearchRunMetadata`. Full field tables and the ID strategy are in
`data/edgelab/schema_v1/README.md`. Highlights:

- **Stable IDs are deterministic hashes of stable inputs** (ticker +
  capturedAt, runId + ticker, gameId + ticker, …), not random UUIDs —
  re-ingesting the same upstream file always produces the same IDs, so
  dedup and idempotent reruns fall out for free (`lib/edgelab/ids.py`).
- **`gameId`** prefers the MLB Stats API's own `gamePk`; falls back to a
  `date_away_home` string, documented as doubleheader-collision-prone
  (same known gap `docs/CANONICAL_SCHEMAS.md` already flagged) — never a
  fourth, different convention.
- **`marketTicker` is optional on `Recommendation`** — a market the
  pipeline never even mapped to a ticker (`NOT_EVALUATED`/
  `PASS_DATA_QUALITY`) must still be recordable.
- **`Settlement.result` is always YES/NO**, never a 3-way AWAY/HOME/TIE
  value — every Kalshi ticker (including each team's own moneyline leg)
  is its own independent binary market, so settlement resolves straight
  to that ticker's own answer.
- **`PlacedBet.trackingType`** (REAL/PAPER/REAL_PROBE) was added during
  implementation to close a gap `docs/CANONICAL_SCHEMAS.md` flagged:
  `lib/tracking_type.py`'s enum exists but nothing writing a bet record
  ever sets it.

## 3. Storage strategy and measured growth

JSONL, partitioned by UTC date, with append-with-dedup or upsert
semantics depending on whether the entity is a time series
(`MarketObservation`, `ClvQuote` — append, dedup by ID) or a
revised-in-place record (`Recommendation`, `Settlement`, `PlacedBet` —
upsert by ID). Every write is atomic (temp file + fsync + `os.replace`,
same pattern as `lib/atomic_json.py`). See
`lib/edgelab/storage.py`.

**Measured, not guessed** (from this session's real end-to-end run
against 2026-07-31's actual slate — 14 games, 2760 legitimate markets).
`MarketObservation` is shown both uncompressed and as actually committed
(gzip, see below):

| Entity | Rows/day | Bytes/row (measured) | Daily | Monthly | Season (~180 days) |
|---|---|---|---|---|---|
| `MarketObservation` (uncompressed) | 2760 × ~28 ticks/day (30-min cadence) = **~77,000** | ~1,320 B | ~100 MB | ~3 GB | ~18 GB |
| `MarketObservation` (**gzip, as committed**) | same ~77,000 | **~64 B** | **~4.9 MB** | **~150 MB** | **~0.9 GB** |
| `Market` (dimension, upserted) | ~2,760 | ~500 B | ~1.4 MB | ~40 MB | ~250 MB |
| `Game` (dimension, upserted) | ~15 | ~400 B | ~6 KB | ~0.2 MB | ~1 MB |
| `ClvQuote` (checkpoint-filtered) | ~2,760 × ~8 checkpoints ≈ **~22,000** | ~500 B | ~11 MB | ~330 MB | ~2 GB |
| `Recommendation` (extension rows upserted, not per-tick) | ~2,760 | ~600 B | ~1.6 MB | ~48 MB | ~290 MB |
| `Settlement` (upserted) | ~2,760 | ~700 B | ~1.9 MB | ~57 MB | ~340 MB |
| `PlacedBet` ledger | ~1–10 new/day | ~1,300 B | negligible | negligible | ~1–2 MB/season |
| Daily report (md+json+calibration) | 1/day | ~16 KB | 16 KB | ~0.5 MB | ~3 MB |

**Original finding (pre-fix): `MarketObservation` was the one entity
that did not fit a "commit everything to git" strategy at season
scale** — ~18GB/season uncompressed is well beyond what's reasonable to
add to this repository's git history, even though the *raw* evidence it's
derived from (`kalshi_registry_snapshots/`) already grows at a comparable
rate today (~109MB observed across ~30 days so far) and the repo has
evidently accepted that tradeoff for raw snapshots already.
`MarketObservation` was worse because it's a fully-keyed, verbose JSON
record (~1.3KB) per market per tick, not a compact snapshot array.

**Fix applied in this PR (implemented, not deferred)**: `MarketObservation`
is now stored gzip-compressed (`data/edgelab/observations/<date>.jsonl.gz`).
Measured on this repo's real 2026-07-31 data: **20.5x compression**
(3,644,300 bytes → 177,212 bytes), bringing the season estimate from
~18GB down to **under 1GB** — solidly in the same sustainable tier as
every other entity, without moving anything out of git or introducing an
external store. `lib/edgelab/storage.py` handles this transparently:
any path ending in `.gz` is read/written compressed with no separate API,
and the gzip header's `mtime` is pinned to `0` specifically so
byte-identical logical content produces a byte-identical compressed
file — without that, gzip's default timestamp embedding would silently
break the "a rerun against unchanged input is a true no-op" guarantee
every append-with-dedup call relies on (verified in
`tests/edgelab/test_storage.py::test_gzip_rerun_with_unchanged_content_is_byte_identical`).
`ClvQuote` was left uncompressed — its ~2GB/season is already
comfortably sustainable and compressing it would add complexity (every
reader needs to agree on the extension) for no growth-relevant benefit.

**Also applied**: `edgelab-capture.yml` (which writes `MarketObservation`)
was split off from CLV collection so it only triggers on the 30-minute
`Capture Kalshi Snapshots (Scheduled)` cadence, not the 10-minute `CLV
Pregame Snapshot Capture` cadence — keeping its commit frequency
proportional to the existing raw snapshot growth rate rather than ~3.5x
higher. See `edgelab-clv-collect.yml`'s docstring and
`tests/edgelab/test_workflow_safety.py::test_bulk_observation_ingestion_does_not_ride_the_10_minute_cadence`.

**Still a reasonable Phase 2 idea, no longer urgent**: a content-based
dedup for `MarketObservation` (skip writing a new row when a ticker's
price/status is byte-identical to its immediately-preceding observation)
would shrink this further, but with gzip already landing the entity in
the sustainable tier, this is now a nice-to-have rather than a
before-merge blocker.

## 4. End-to-end example (real, run against this repo's own 2026-07-31 data)

```
1. Market observed:
   scripts/edgelab/ingest_market_observations.py --date 2026-07-31
   -> 2760 legitimate MarketObservation rows from data/kalshi_registry_snapshots/
      kalshi_search_2026-07-31_2234.json (0 excluded — this snapshot source is
      already MLB-narrowed; the strict-registry gate still runs on every row)

2. Evaluated / recommended or passed:
   scripts/edgelab/build_recommendations.py --date 2026-07-31
   -> 151 pipeline-derived rows (the 11-market model config) + 2682 full-
      universe extension rows (441 NOT_EVALUATED, 2241 INSUFFICIENT_MODEL_SUPPORT)
   -> 115 PASS_NO_EDGE, 12 PASS_DATA_QUALITY, 24 RECOMMENDED/BET_PLACED/WATCH

3. Bet placed or not placed:
   data/edgelab/bets/bets.jsonl (backfilled from bets.json/data/bets.json —
   77 of 608 legacy bets carried an exact ticker + entry price)

4. Price history captured:
   scripts/edgelab/collect_clv.py --date 2026-07-31
   -> 2760 ClvQuote rows projected from that date's MarketObservation history

5. Closing quote selected:
   lib.edgelab.checkpoints.select_closing_quote() -> 2560 of 2760 tickers had
   a valid pre-start/pre-suspension candidate (the other 200 were already
   Final/live by ingestion time, correctly excluded, never guessed)

6. Market settled:
   scripts/edgelab/settle_markets.py --date 2026-07-31
   -> in this sandbox: 0 SETTLED (no outbound network access to MLB Stats
      API here — see the honest SETTLEMENT_UNRESOLVED reason breakdown in
      data/edgelab/reports/2026-07-31.json/.md); in CI with real network
      access this populates via the same fetch_mlb_linescore() call
      production F5 settlement already uses.

7. CLV and P/L calculated:
   4 of the 77 backfilled bets had a marketTicker present in 2026-07-31's
   own observed universe (the rest belong to earlier dates); CLV computed
   for all 4 (avg -0.26 cents, 1 positive / 3 negative — see the committed
   data/edgelab/reports/2026-07-31.md for the full report).
```

Fixture-based unit tests exercise every step of this chain without
network access — see `tests/edgelab/fixtures/kalshi_search_sample.json`
and the corresponding test files listed in section 6.

## 5. Known limitations

1. **`MarketObservation` storage growth** (section 3) — addressed via
   gzip compression (measured 20.5x, ~18GB/season down to <1GB), no
   longer a before-merge blocker. Content-based dedup remains a
   reasonable Phase 2 refinement, not an urgent one.
2. **~83% of legacy bets (531 of 608) have no `marketTicker`** in either
   pre-existing ledger and are therefore not represented in the new
   canonical `PlacedBet` ledger — a real, pre-existing gap in
   `bets.json`/`data/bets.json`, not something EdgeLab retroactively
   fixes by fabricating a ticker.
3. **Player prop settlement (pitcher K's/outs, hitter hits/TB/HRR/RBI/SB
   — 7 of the 17 strict-registry families) is entirely unimplemented.**
   `lib/edgelab/settlement.py` returns an explicit
   `SETTLEMENT_UNRESOLVED`/`player_prop_settlement_not_implemented` for
   all of them rather than a fabricated result. This is the single
   largest settlement-coverage gap.
4. **`ModelEvaluation` is not populated by a dedicated ingestion path
   yet** — `Recommendation.modelFairProbability`/`marketImpliedProbability`
   are read directly off `marketLedger` rows; a first-class
   `ModelEvaluation` record (with `modelVersion`/`calibrationVersion`)
   would need those fields added upstream first (see
   `docs/CANONICAL_SCHEMAS.md`'s own "no object carries modelVersion"
   gap — unchanged by this phase).
5. **Doubleheader `gameId` collisions**: the `date_away_home` fallback
   `gameId` (used only when a real MLB `gamePk` isn't resolvable) cannot
   distinguish two games between the same teams on the same date — an
   inherited, documented gap, not introduced here.
6. **Settlement network calls run inside this sandbox with no outbound
   MLB Stats API access** — verified gracefully degrading (warnings +
   `SETTLEMENT_UNRESOLVED`, never a crash), but not verified against a
   real live response in this session. Should be smoke-tested once in
   an environment with real network access before relying on it.

## 6. Follow-up Phase 2 recommendations

1. Content-based dedup for `MarketObservation` (skip a new row when a
   ticker's price/status hasn't changed since the last tick) — a further
   optimization on top of the gzip fix already applied in this PR, not
   a blocker.
2. Player prop settlement (boxscore-based: strikeouts, outs, hits, total
   bases, HRR, RBIs, stolen bases).
3. A first-class `ModelEvaluation` record + `modelVersion`/
   `calibrationVersion` fields upstream (shared gap with the existing
   pipeline, not EdgeLab-specific).
4. A calibration model consuming `reports/<date>_calibration.jsonl`
   (Phase 1 only exports the joined model-prob/settled-result rows; it
   does not compute calibration bins/curves itself, mirroring
   `scripts/build_wager_research_db.py`'s existing
   `calibration_bins.json` approach but keep them separate exports for
   now).
5. Export to SQLite/DuckDB for actual research queries — the JSONL
   format here was chosen specifically so this is a one-line
   `read_json`/`read_ndjson` call away, not a redesign.
6. Reconcile `Recommendation.marketName` (the 11-market model's own
   naming) against the ~14 duplicate-ticker collisions found in
   `data/pipeline/<date>/recommendations.json` where `RL_Away`/`RL_Home`
   resolve to the same physical Kalshi ticker (see
   `build_recommendations_from_pipeline`'s dedup-by-market-key
   behavior) — worth a closer look at whether that's the model config's
   intent or an unrelated pre-existing bug.

## 7. Testing

`tests/edgelab/` (91 tests) covers schema versioning/required-optional-
field/enum validation, deterministic ID generation, full-universe
market capture with forbidden-market exclusion and new-series
detection, dedup/idempotent-rerun/time-series-preservation, bet ledger
(manual entry, legacy reconciliation including the NRFI YES/NO-side fix,
multiple bets per market), recommendation status vocabulary (pass-reason
mapping, NOT_EVALUATED vs INSUFFICIENT_MODEL_SUPPORT), CLV (entry/close,
YES/NO sides, bid/ask selection, suspended/delayed/stale/wide-spread/
missing-close), settlement (win/loss/void, F3/F5/F7 three-way, explicit
player-prop non-support, price-dependent hypothetical returns for unbet
markets), report aggregation, and GitHub Actions workflow safety
(no push-triggered loop, concurrency, diff-guarded commits, cadence
separation). Run with `python3 -m pytest tests/edgelab -q`.

The full pre-existing repository suite (`python3 -m pytest tests/ -q`)
was run after every EdgeLab commit in this PR: 2715 passed, 6 skipped,
5 pre-existing failures unrelated to this work (they `git diff` against
two historical commit SHAs — `fe0a19c`/`b006c39` — pinned in
`tests/test_validate_slate_final_pr9_changed_file_scope.py` and
`tests/test_risk_gate_review_parts_v_to_y.py` from a prior PR; those
commits aren't reachable in this session's git history depth, an
environment/shallow-clone limitation, not a regression from this
branch).

## 8. Pre-merge review findings (fixed in this PR)

A final maintainer review before merge deliberately did not trust the
green test suite at face value and found three real defects the unit
tests had missed — all three because a hand-rolled test fixture happened
to match a *buggy* function's expectations rather than what the real
upstream producer actually writes:

1. **`settle_market()` read a field key (`market.get("outcome")`) that
   `Market`/`MarketObservation` records never populate** (the value is
   written under a field previously named `side`, itself confusingly
   named since it's not a betting side). Every TIE-suffixed ticker for
   game_result/inning_result families would have silently settled
   `SETTLEMENT_UNRESOLVED`/`ticker_team_not_resolved` in production
   instead of its real result. Fixed by renaming the field to
   `outcomeLabel` (clearer, and can no longer be confused with
   `PlacedBet.side`/`comparisonOperator`'s YES/NO) and correcting the
   read site. Caught only once `tests/edgelab/test_integration_end_to_end.py`
   ran `settle_market()` against a `Market` record produced by the real
   `build_market_records()` pipeline instead of a hand-built dict.
2. **`scripts/edgelab/settle_markets.py` only ever settled
   `matching_bets[0]`** — a ticker with multiple bet tranches (a
   supported, tested scenario) would have left every bet after the first
   pending forever, with no result or P/L. Fixed by extracting a pure
   `settle_bets_for_ticker()` that processes every matching bet.
3. **`Recommendation.betId` was always `None`**, even when
   `betPlaced=True`/`status=BET_PLACED` — breaking the
   recommendation-to-bet traceability link section G requires. Compounding
   this, **`extend_with_full_universe()` never checked the bet ledger at
   all**, so "a bet placed on a market the model never evaluated" (an
   explicit section G research target) was unrepresentable — such a bet
   always showed `NOT_EVALUATED`/`betPlaced=False` regardless of real
   ledger state. Fixed by threading a `{marketTicker: betId}` map through
   both `build_recommendations_from_pipeline()` and
   `extend_with_full_universe()`; the latter now reports `BET_PLACED`
   with `modelFairProbability` left `null`, so a query can distinguish
   "bet placed without a model recommendation" from a model-driven bet.

Also addressed in the same pass: the `MarketObservation` storage-growth
finding (section 3) — gzip compression, measured 20.5x, implemented
rather than left as a Phase 2 recommendation.

15 new tests were added specifically to cover these fixes and to close
the integration-testing gap that let them through initially
(`tests/edgelab/test_integration_end_to_end.py`,
`tests/edgelab/test_storage.py`, plus additions to
`test_settlement.py`/`test_recommendations.py`). Full EdgeLab suite after
these fixes: 105 tests, all passing.
