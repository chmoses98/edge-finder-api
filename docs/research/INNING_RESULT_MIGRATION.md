# INNING_RESULT_MIGRATION.md

Model Performance Phase 2A -- F3/F5/F7 inning-result discovery and
ingestion, canonical probability integration, dynamic market
retention, shadow evaluation, and paper-only historical collection.

**No production formula, calibration factor, confidence threshold,
bet-sizing rule, bankroll rule, or real-money eligibility behavior was
changed by this phase.** Every execution-critical production file
(`scripts/build_market_ledger.py`, `scripts/risk_gate.py`,
`scripts/write_pending_bets.py`, `scripts/protect_slate.py`,
`scripts/validate_slate_final.py`, `scripts/merge_odds.py`,
`scripts/enrich_data.py`, `lib/f5_settlement.py`,
`lib/slate_manager.py`, `lib/sentinel_validator.py`,
`lib/postponed_guard.py`, `lib/promotion_engine.py`, and every other
`lib/*.py` module outside `lib/research/`) is byte-for-byte identical
to `main` -- confirmed via direct SHA-256 comparison against
`origin/main`'s blobs, not merely "no diff shown."

## 1. F3/F5/F7 market existence status

| Horizon | Exists on Kalshi | Source |
|---|---|---|
| Full game | Confirmed | Real repository snapshot (`data/kalshi_registry_snapshots/*.json`) |
| F5 | Confirmed | Real repository snapshot, explicit `-TIE` ticker on every event |
| F3 | **Confirmed** | User-reported direct observation and real wagers placed -- NOT independently API-verified this phase (see item 3 below) |
| F7 | **Confirmed** | Same as F3 |

## 2. Verified vs. unresolved structures

| Horizon | Outcome structure | Confidence |
|---|---|---|
| Full game | Two-way (Away/Home) | Confirmed via direct ticker-count inspection |
| F5 | Three-way (Away/Tie/Home) | Confirmed via direct ticker-count inspection (explicit `-TIE` leg) |
| F3 | **Unresolved** | Existence confirmed by user; outcome structure NOT independently verified -- never assumed to match F5 |
| F7 | **Unresolved** | Same as F3 |

This distinction is recorded in exactly one place,
`lib/research/market_taxonomy.py`'s `HORIZON_MARKET_STATUS`, reused
verbatim by every downstream artifact (inventory, shadow ledger,
handler registry, projection comparison) so no artifact can drift back
into asserting F3/F7 match F5's structure.

## 3. Current repository discovery defects (root cause, re-confirmed this phase)

Re-audited on merged main (commit `671dd47`), including the scheduled
snapshot from commit `5a14130` (`kalshi_search_2026-07-29_1722.json`)
and all 336 snapshot files spanning 2026-06-08 through 2026-07-29:
**zero genuine F3/F7 ticker or title evidence found anywhere** (a
handful of substring false-positives inside spread/team-total ticker
suffixes like `-SF3`/`-SF7` were checked and ruled out). This is
consistent with -- not contradictory to -- the user's confirmed
real-money F3/F7 wagers: this repository's own fetchers never queried
Kalshi for these series in the first place.

Root cause (all four discovery entry points independently traced):

1. **`api/kalshisearch.js`** -- `ALL_SERIES` is a fixed array of
   exactly 8 series tickers; the per-series loop calls
   `/markets?series_ticker=<name>` only for names already in that
   array. This is the SOLE feeder of
   `data/kalshi_registry_snapshots/*.json` (confirmed via
   `.github/workflows/fetch-slate.yml` and
   `capture-snapshots-scheduled.yml`), so that archive could never
   contain F3/F7 regardless of Kalshi's real catalogue.
2. **`scripts/build_kalshi_registry.py`** -- `SERIES_CATALOGUE` is the
   same fixed 8-series allowlist, independently.
3. **`scripts/fetch_kalshi_markets.py`** -- a single hardcoded
   `SERIES_TICKER = 'KXMLBGAME'` (full-game only).
4. **`api/odds.js`** -- a SEPARATE, independent failure mode: its
   broader unfiltered `/markets?status=open&limit=1000` call would
   return F3/F7 raw data if fetched, but its per-market classification
   `if/else` chain had no F3/F7 branch and no catch-all, so such a
   market fell through every branch and was silently never added to
   the returned game object.

## 4. New broad-discovery design

Each entry point above was fixed ADDITIVELY (existing allowlists and
output shapes preserved unchanged, for the exact reason production
code -- `scripts/build_kalshi_registry.py`'s backfill,
`scripts/merge_odds.py` -- depends on them):

- `api/kalshisearch.js`: added an unfiltered `/markets?status=open&limit=1000`
  broad pass (capped at 500 entries) that retains any market whose
  series is NOT in `ALL_SERIES` under a new additive
  `discoveredUnknownSeriesMarkets` field; also added F3/F7 title-text
  classification (`f3_moneyline`/`f7_moneyline`) to `classifyMarket()`.
- `scripts/build_kalshi_registry.py`: added
  `discover_unknown_series()` (imported from `lib/kalshi_discovery.py`,
  a pure, independently unit-tested module -- extracted specifically
  because this script has no `if __name__` guard and executes real
  network calls at import time, so the logic could not be tested
  in-process without the extraction) -- writes a new additive
  `discoveredUnknownSeries` list into the registry JSON.
- `scripts/fetch_kalshi_markets.py`: added the same broad unfiltered
  pass, writing `discoveredUnknownSeries` into
  `data/kalshi_market_index.json` additively.
- `api/odds.js`: added explicit F3/F7 title-text branches (`f3ml`/
  `f7ml`) AND a genuine catch-all `else` that stores any
  still-unmatched market under `game.unclassified` instead of doing
  nothing.

## 5. Discovery vs. activation separation

Every one of the four fixes above is READ-ONLY discovery-visibility
scaffolding. None of them:
- adds a new series to any PRODUCTION allowlist (`SERIES_CATALOGUE`,
  `REQUIRED_MARKETS` in `scripts/build_market_ledger.py` -- untouched),
- changes what `scripts/merge_odds.py` injects into `data/slate.json`,
- changes what `scripts/build_market_ledger.py` evaluates,
- is imported by any execution-layer script.

`lib/research/market_handler_registry.py`'s `evaluate_market_research()`
enforces this same separation at the classification layer: a
classified F3/F7 market gets `STATUS_STRUCTURE_UNRESOLVED` (a new,
more specific status than the generic settlement-unresolved one),
never `STATUS_EVALUATED`, and `productionEnabled` is hardcoded `False`
in `classify_inning_result_market()`'s output regardless of input.

## 6. F5 three-way probability math

`lib/research/three_way_projection.py`'s `canonical_inning_result_probs()`
wraps the existing (Phase 1) `three_way_result_probs_for_horizon()` and
renames its output to the Part 7 canonical schema:

```json
{
  "awayLeadProb": 0.427, "tieProb": 0.186, "homeLeadProb": 0.388,
  "probabilitySum": 1.0, "truncationMass": 4.4e-16,
  "method": "independent_poisson_joint_distribution_tie_retained",
  "horizon": "F5", "horizonInnings": 5
}
```

Never renormalizes Away/Home after computing Tie; `probabilitySum` is
reported explicitly so a caller never has to recompute it to prove the
Critical Three-Way Market Requirement.

## 7. Legacy conditional F5 math

`legacy_conditional_probs()` computes, from a canonical result:

```
awayLeadGivenNoTieProb = awayLeadProb / (awayLeadProb + homeLeadProb)
homeLeadGivenNoTieProb = homeLeadProb / (awayLeadProb + homeLeadProb)
```

This is the EXACT same probability space production's current
renormalization (`p_win / (1 - p_push)`) computes -- confirmed
mathematically equivalent, just derived from the canonical output
instead of a second independent computation. `f5_migration_safe_output()`
returns both under unambiguous field names:

```json
{
  "f5ThreeWay": {"awayLeadProb": ..., "tieProb": ..., "homeLeadProb": ...},
  "f5LegacyConditional": {"awayLeadGivenNoTieProb": ..., "homeLeadGivenNoTieProb": ...}
}
```

No field named `f5WinProb` (or any other ambiguous name) exists
anywhere in this phase's schema.

## 8. Why conditional probabilities cannot be directly compared with three-way prices

`P(Away | not Tie)` and `P(Away)` are different probability spaces --
the former is always numerically larger (it excludes the tie mass from
its denominator). Comparing a legacy conditional probability directly
against a three-way Kalshi price without adjustment overstates the
model's edge. Every report this phase produces
(`lib/research/inning_result_report.py`'s `format_f5_result_report()`)
carries the mandated warning verbatim: *"Legacy probabilities are
conditional on no tie and are not directly comparable with
unconditional three-way Kalshi contract prices."*

## 9. F3/F7 verification status

Existence: confirmed (user-reported). Outcome structure: **UNVERIFIED**.
Settlement rules: **UNVERIFIED**. Real ticker prefix: **UNCONFIRMED**
(this repository's `KXMLBF3`/`KXMLBF7` guesses, added speculatively in
Phase 1, have never been checked against a real Kalshi response). A
title-text classification fallback
(`_infer_unconfirmed_inning_scope_from_text()` in
`lib/research/market_taxonomy.py`) allows F3/F7 to be classified
correctly even if the real ticker prefix differs from the guess,
provided the title text uses recognizable horizon language.

## 10. Official settlement-rule limitations

Kalshi's real `rules_primary`/`rules_secondary` API fields are not
captured by any of this repository's fetch scripts (a pre-existing,
still-open gap documented in Phase 1). F5's settlement basis
("after 5 complete innings") is inferred from ticker/title structure,
not read from Kalshi's own rules text --
`settlementStatus: "inferred_from_ticker_structure_not_kalshi_rules_field"`
records this honestly. F3/F7 settlement is `settlementStatus: "UNVERIFIED"`
-- `lib/research/inning_result_settlement.py` REFUSES to settle F3/F7
under any input (always returns `(SETTLEMENT_UNRESOLVED,
"structure_unverified")`), only implementing real settlement logic for
F5 (Away/Tie/Home from the score after 5 complete innings, settleable
even if the game is later suspended/postponed/shortened, since those
only affect the full game's outcome).

## 11. No-silent-drop guarantee

`lib/research/market_handler_registry.py`'s `evaluate_market_batch_research()`
still guarantees `len(output) == len(input)` (Phase 1 property,
re-verified this phase with mixed known/unknown-horizon fixtures).
Every discovered market resolves to exactly one of:
`Evaluated`, `Unsupported Market`, `Missing Data`,
`Classification Failed`, `Settlement Rule Unresolved`,
**`Structure Unresolved`** (new this phase, more specific than
Settlement Rule Unresolved for F3/F7), or `Evaluation Failed`.

## 12. Shadow/paper-only behavior

`lib/research/inning_result_shadow_ledger.py`'s `build_shadow_ledger_row()`
attaches eligibility flags derived solely from verified structure
status:

```json
{"researchEligible": true, "paperEligible": true,  "realMoneyEligible": false,
 "activationStatus": "PAPER_ONLY", "activationReason": "INSUFFICIENT_HISTORICAL_CALIBRATION"}
```
for F5 (structure verified), or
```json
{"researchEligible": true, "paperEligible": false, "realMoneyEligible": false,
 "activationStatus": "UNRESOLVED", "activationReason": "MARKET_STRUCTURE_OR_SETTLEMENT_UNVERIFIED"}
```
for F3/F7 (structure unresolved) -- and an unresolved-structure row
gets NO synthetic model edge attached (`canonicalModelProb`,
`legacyConditionalProb`, `executableYesEdge`, `executableNoEdge` are
all forced to `None`), even if projection inputs happen to be
available. `realMoneyEligible` is `false` in every single row
generated this phase, by construction -- no code path can flip it.

## 13. Historical snapshot design

`lib/research/inning_result_snapshot_archive.py`'s
`build_snapshot_record()`/`merge_snapshots()` implement an append-safe,
idempotent, date-partitioned archive
(`data/research/inning_result_snapshots/<date>.json`), keyed by a
stable composite `recordId` (`date:gameId:scope:outcome:ticker`).
`settlementResult`/`settlementTimestamp` are ALWAYS `None` at
projection-snapshot time -- attached later, by a separate
`apply_settlement()` call with its own timestamp, never backfilled
into the same write (no future-data leakage). Verified idempotent by
direct test: rerunning `scripts/research/append_inning_result_snapshots.py`
against unchanged inputs produces byte-identical output files.

## 14. Future activation requirements

See "Activation gate design" below -- gates are DEFINED, not activated,
in this phase. At minimum: verified contract structure, verified
settlement rules, a minimum settled sample, lineup-confirmed subset,
multiclass Brier score, log loss, reliability calibration, CLV,
liquidity, spread, probability stability, starter-change exclusions,
settlement integrity, no material leakage, and comparison against
legacy production -- ROI is never used alone.

## Activation gate design (defined, NOT activated, this phase)

| Outcome | Minimum gate criteria (all required, none sufficient alone) |
|---|---|
| F5 Away (canonical) | Verified structure (done) + verified settlement rules (partial -- ticker-inferred only) + >= 1 full season of walk-forward calibration showing no material miscalibration in any probability bucket + lineup-confirmed subset meeting the same bar + multiclass Brier/log-loss within pre-registered tolerance of current production + CLV stability + no material leakage + explicit comparison against legacy conditional showing canonical is not worse |
| F5 Home (canonical) | Same as F5 Away |
| F5 Tie | Same as F5 Away/Home, PLUS may remain paper-only strictly longer than the team-side outcomes (Kalshi Tie liquidity/spread not yet independently characterized) |
| F3 Away / F3 Home | Real ticker independently confirmed (not a guess) + outcome structure independently verified + official settlement rules retrieved + all F5-equivalent criteria above, evaluated from scratch (F5's calibration does NOT transfer to F3) |
| F3 Tie | Only if F3 is confirmed three-way; same bar as F3 Away/Home, evaluated independently for the Tie leg specifically |
| F7 Away / F7 Home / F7 Tie | Same requirements as F3, evaluated independently for F7 |

Recommended minimum paper sample (unchanged methodology from Phase 1's
`BACKTEST_FRAMEWORK_DESIGN.md`): ~1 full season or ~1,000+ resolved
market instances per outcome before even proposing real-money
activation review -- this is a design recommendation, not a validated
number, since no walk-forward backtest has been run yet for any
inning-result outcome.

## Explicitly not performed this phase

No production formula changed. No market newly activated (F3, F5 Tie,
F7 all remain real-money ineligible by construction). No calibration
factor changed. No bet-sizing rule changed. No bankroll rule changed.
No production recommendation changed (execution-layer files are
byte-identical to `main`). No production workflow dispatched. No bet
executed, submitted, or simulated against production data.
