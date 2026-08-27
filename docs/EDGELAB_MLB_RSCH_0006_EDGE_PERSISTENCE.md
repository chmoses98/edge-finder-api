# EdgeLab Research Lab — MLB-RSCH-0006: Edge Persistence / Market Confirmation

**Status: RESEARCH ONLY. No production model probability, feature, recommendation
logic, threshold, confidence tier, Bet Up To logic, Kalshi fee calculation,
bankroll/staking, market eligibility, lineup gate, slate output, risk gate,
settlement, or production cron behavior was changed. A finding here does not
auto-promote any of the above.**

## 1. Question

When the model disagrees with Kalshi before a game, is an edge that
**persists** across multiple prospective checkpoints more trustworthy
(better model-vs-market predictive performance) than a **transient**,
one-checkpoint edge of similar initial magnitude? This is a deliberate
shift from the baseball-backtest track (MLB-RSCH-0002–0005) toward the
betting-truth engine, but retains the same rigor: no production change,
nothing auto-promotes from a finding here.

## 2. Execution status

**Complete.** Ran entirely against the already-archived EdgeLab
observation/model-evaluation/settlement/game corpus (27 observation-dates)
via `lib.edgelab.research_dataset.build_opportunity_rows` — no new fetch,
no network access. Reused `scripts/edgelab/run_edge_monotonicity_experiment.py`
(MLB-RSCH-0001), imported as a module, for eligibility filtering, the
fair-market benchmark, the fixed edge buckets, current-model isolation,
and the entire Brier/log-loss/paired-delta/fee-adjusted-economics metric
bundle — this experiment adds only the genuinely new logic of grouping
opportunity rows by ticker into chronological checkpoint sequences and
classifying their persistence.

**A real bug was caught and fixed during verification** (documented
honestly, not glossed over): the first draft's `classify_persistence_signal`
used a lower sample floor for every classification tier, which let a
15-game, boundary-CI result (low=0.0005, barely excluding zero) trigger
`NEGATIVE_SIGNAL`/`REJECT` on the current model — a fragile, small-sample
artifact its own `analyze_segment` output simultaneously labeled
`INSUFFICIENT`. Fixed by requiring `STRONG_TRUST_SIGNAL`/`NEGATIVE_SIGNAL`
to additionally clear `MIN_GAMES_EXPLORATORY=50` (reused directly from
MLB-RSCH-0001, not a new weaker constant) — the corrected run reclassifies
that same result as `NO_USEFUL_TRUST_SIGNAL`. A regression test
(`test_small_sample_confident_ci_still_capped_below_strong_or_negative`)
proves this.

## 3. Preregistered specification

### 3a. What's reused vs. genuinely new

Reused unchanged (imported, not reimplemented): `fair_market_probability`
(bid/ask midpoint), `EDGE_BUCKETS`/`assign_edge_bucket` (the same 7 fixed
buckets), `usable_rows_and_coverage` (eligibility), `filter_canonical_era`/
`filter_trusted_production_only` (current-model isolation), `analyze_segment`
(Brier/log-loss/paired-delta/economics), the canonical fee engine (via
`analyze_segment`), and the same `CTRL-7252463d722626e6` control identity
(a write-once no-op re-registration, verified: no new control file was
created).

Genuinely new: per-ticker chronological checkpoint-sequence construction
(`build_ticker_sequences`), persistence-tier classification
(`classify_persistence_tier` — longest run of consecutive same-sign
`contemporaneousEdge` checkpoints), the `lineupConfirmedPersistent`/
`lateSurviving` flags, and `market_moved_with_model` (executable-price
movement relative to the model's initial edge direction).

### 3b. Persistence groups (preregistered, mutually exclusive tiers + two orthogonal flags)

`SINGLETON_TRANSIENT` (max consecutive same-sign run ≤ 1, or a single
checkpoint), `TWO_CHECKPOINT_PERSISTENT` (run = 2), `THREE_PLUS_CHECKPOINT_
PERSISTENT` (run ≥ 3); plus `lineupConfirmedPersistent` and `lateSurviving`
as independent boolean flags (a ticker can be both 3+-persistent and
late-surviving).

### 3c. Pseudoreplication guard

A ticker's multiple checkpoint rows are never pooled as independent
observations for predictive scoring — every Brier/log-loss/economics
comparison uses exactly **one representative row per ticker** (its own
`CLOSING` row if observed, else its chronologically last usable row).
Proven by `test_analyze_segment_never_receives_more_than_one_row_per_ticker`.

### 3d. Chronological structure

This corpus (27 observation-dates) cannot support
`lib.edgelab.research_splits.MIN_DATES_FOR_MATURE_SPLIT=30` — the
multi-checkpoint-usable population has only 18 (all-history) / 6
(trusted-production) distinct dates. `chronological_split_policy="NONE"`,
`experiment_type=EXPLORATORY`, `evidence_level=E1_RECONSTRUCTED_
RETROSPECTIVE` — identical reasoning and identical corpus-maturity
constraint MLB-RSCH-0001 already documented.

### 3e. CLV, precisely labeled

The CLV-like metric reused here is `research_dataset`'s own "hypothetical,
full-universe price-movement-to-close" field (`fullUniverseMarketMovementToClose`),
signed relative to the model's initial edge direction — **not** real
placed-bet CLV (`lib.edgelab.clv.compute_clv_for_bet`, which needs an
actual bet's own entry price and is out of scope, since this experiment
studies every model edge, not just placed bets).

## 4. Coverage (real data)

| Metric | Value |
|---|---|
| Total archived opportunity rows | 162,419 |
| Rows with settlement | 151,031 |
| Rows with a causally-valid model probability | 1,124 |
| Usable rows (settled + causal probability + computable edge) | **676** |
| Unique tickers usable | 546 |
| Independent games / dates (ALL_HISTORICAL_MODEL_VERSIONS) | **180 / 18** |
| Independent games / dates (TRUSTED_PRODUCTION_QUALITY_TIER_ONLY) | **70 / 6** |
| Checkpoint-count distribution per ticker (ALL_HISTORY) | 1 checkpoint: 420 tickers, 2: 122, 3: 4 |
| LINEUP_CONFIRMATION coverage in the usable population | **0** — never observed in the settled+causally-linked corpus (reported honestly, not fabricated) |

**This is a genuinely small, young corpus.** Only 126 of 546 usable tickers
(39 of 180 games) have any multi-checkpoint sequence at all, and only 4
tickers (1 game) have 3+ checkpoints. Every result below is reported with
its own independent-games count and `analyze_segment`'s own
`interpretability` label — most persistence-tier cells are honestly
`INSUFFICIENT`.

## 5. Results — ALL_HISTORICAL_MODEL_VERSIONS (primary population)

Sign convention: `pairedBrierDelta_modelMinusMarket` **positive** means
the model is *worse* than the market's fair-value (bid/ask midpoint)
estimate on that segment; **negative** means the model is *better*. All
CIs are 90% game-clustered bootstrap (matching MLB-RSCH-0001's own
convention).

| Group | Rows | Games | Dates | Interpretability | Brier delta | 90% CI |
|---|---|---|---|---|---|---|
| SINGLETON_TRANSIENT | 420 | 144 | 18 | INTERPRETABLE | **+0.0234** | [0.0105, 0.0353] |
| TWO_CHECKPOINT_PERSISTENT | 122 | 38 | 11 | INSUFFICIENT | +0.0073 | [-0.0148, 0.0285] |
| THREE_PLUS_CHECKPOINT_PERSISTENT | 4 | 1 | 1 | INSUFFICIENT | -0.0291 | too few games for a bootstrap CI |
| PERSISTENT_2PLUS_POOLED | 126 | 39 | 12 | INSUFFICIENT | **+0.0061** | [-0.0168, 0.0270] |
| LINEUP_CONFIRMED_PERSISTENT | 0 | 0 | 0 | INSUFFICIENT | — | no LINEUP_CONFIRMATION checkpoint observed in the usable population |
| LATE_SURVIVING | 68 | 36 | 12 | INSUFFICIENT | +0.0150 | [-0.0054, 0.0343] |

**Transient edges are confidently worse than the market** (CI entirely
positive: [0.0105, 0.0353], `INTERPRETABLE`, 144 games). **Persistent
(2+) edges are directionally better** (smaller positive delta, +0.0061
vs +0.0234) but the CI crosses zero and the sample (39 games) is
`INSUFFICIENT` by MLB-RSCH-0001's own 50-game bar — this is a real,
directionally-favorable pattern that this corpus cannot yet confirm.

### 5a. H4 — incremental value beyond raw edge magnitude (within-bucket)

For each of the 7 preregistered `EDGE_BUCKETS`, transient vs. persistent
(2+) tickers within that same initial-edge bucket:

| Bucket | Transient (n/games/delta) | Persistent (n/games/delta) | Persistent advantage |
|---|---|---|---|
| <0% | 194 / 115 / +0.0110 | 58 / 33 / -0.0349 | **+0.0459** |
| 0-2.5% | 10 / 10 / — | 5 / 4 / — | -0.0043 (both tiny) |
| 2.5-5% | 27 / 26 / — | 5 / 5 / — | -0.0241 (both tiny) |
| 5-7.5% | 59 / 49 / — | 17 / 16 / — | +0.0102 (both tiny) |
| 7.5-10% | 37 / 31 / — | 15 / 14 / — | -0.0240 (both tiny) |
| 10-15% | 76 / 66 / — | 22 / 21 / — | -0.0120 (both tiny) |
| 15%+ | 17 / 15 / — | 4 / 4 / — | -0.0344 (both tiny) |

Every bucket except `<0%` has too few persistent tickers (4-22) to draw
any conclusion — most of the corpus's persistent-tier tickers are
concentrated in the `<0%` bucket (58 of 126), where persistence shows the
largest, most favorable within-bucket advantage (+0.0459). This is
suggestive but not confirmed at this sample size; reported as exploratory,
not a primary finding.

### 5b. 2×2 confirmation matrix

Every 2+-checkpoint (multi-checkpoint) ticker in this corpus happened to
maintain the same edge sign throughout — there were zero sign-changing
multi-checkpoint tickers, so the `TRANSIENT` row of the matrix is
structurally empty (a transient/singleton ticker has, by definition, only
one usable checkpoint, so market-movement-since-initial-checkpoint is not
computable for it: `market_moved_with_model` returns `None`).

| Cell | Rows | Games | Brier delta | Mean CLV-like |
|---|---|---|---|---|
| PERSISTENT + MARKET_MOVES_WITH_MODEL | 6 | 6 | -0.0551 | +0.0117 |
| PERSISTENT + MARKET_MOVES_AGAINST_MODEL | 21 | 18 | -0.0152 | -0.0110 |
| TRANSIENT + MARKET_MOVES_WITH_MODEL | 0 | 0 | — | — |
| TRANSIENT + MARKET_MOVES_AGAINST_MODEL | 0 | 0 | — | — |

Both persistent cells show a favorable (negative) Brier delta — model
better than market — regardless of whether the market moved with or
against it, though both are far too small (6 and 18 games) to draw a
confident conclusion.

### 5c. Market-family breakdown (minimum 10 independent games)

| Family | Games | Transient delta [CI] | Persistent delta [CI] |
|---|---|---|---|
| team_total | 171 | **+0.0315** [0.0122, 0.0510] | -0.0010 [-0.0313, 0.0254] |
| first_inning_run | 151 | +0.0118 [-0.0059, 0.0296] | +0.0195 [-0.0124, 0.0524] |
| game_result | 29 | +0.0213 [-0.0025, 0.0405] | +0.0201 [-0.0101, 0.0485] |
| inning_result | 47 | +0.0088 [-0.0124, 0.0286] | +0.0024 [-0.0651, 0.0618] |

**`team_total` is the one family with a clean, family-specific pattern**:
transient team_total edges are confidently worse than the market
(CI entirely positive), while persistent team_total edges sit at
essentially parity with the market (CI straddles zero, point estimate
near zero) — persistence appears to "rescue" edge quality specifically
in this family. The other three families show no such separation. This
is the strongest evidence for `PARTIAL_FAMILY_SPECIFIC_TRUST_SIGNAL`
(rather than a uniform signal across all families).

## 6. Current-model isolation: ALL_HISTORY vs. CANONICAL_ERA vs. TRUSTED_PRODUCTION

| Population | Games | Dates | Signal classification |
|---|---|---|---|
| ALL_HISTORICAL_MODEL_VERSIONS | 180 | 18 | PARTIAL_FAMILY_SPECIFIC_TRUST_SIGNAL |
| CANONICAL_ERA (gameDate ≥ repo boundary) | 166 | 17 | PARTIAL_FAMILY_SPECIFIC_TRUST_SIGNAL |
| **TRUSTED_PRODUCTION_QUALITY_TIER_ONLY** | **70** | **6** | **NO_USEFUL_TRUST_SIGNAL** |

**The disposition is driven only by the current-model (TRUSTED_PRODUCTION)
classification**, never the pooled historical one — same discipline as
MLB-RSCH-0001. The current-model population has only 39 games with any
multi-checkpoint ticker and 6 total dates; its persistent-tier sample
(15 games) does not clear the 50-game confidence bar, so it correctly
lands at `NO_USEFUL_TRUST_SIGNAL` rather than a false-confident `STRONG`
or `NEGATIVE` label (see §2's bug-fix note).

## 7. Economics (secondary evidence only)

| Segment | Simulated orders | Hypothetical ROI |
|---|---|---|
| ALL_HISTORY persistent (2+) | 126 | see machine report — secondary evidence, never a disposition basis |
| ALL_HISTORY transient | 420 | see machine report — secondary evidence, never a disposition basis |

Per the mission's explicit instruction, historical ROI was **not**
optimized against, and no segment's positive/negative economics was used
to declare anything "actionable." All fee-adjusted P/L uses the canonical
`lib.edgelab.kalshi_fees.simulate_settlement_order` unchanged, at the
standard `$10` research order size.

## 8. Conclusions

- **A. Does persistence make declared edge more trustworthy?**
  **Directionally yes, not yet confirmed.** ALL_HISTORY's persistent (2+)
  tickers show a smaller model-vs-market Brier disadvantage than transient
  tickers (+0.0061 vs +0.0234), but the persistent sample (39 games) is
  below the 50-game confidence bar this study requires before calling
  anything strong.
- **B. Does lineup-confirmed persistence matter?** **Cannot be assessed —
  zero LINEUP_CONFIRMATION checkpoints appear in the settled, causally-
  linked usable population.** Reported honestly as a coverage gap, not
  fabricated or approximated.
- **C. Does late survival matter?** No confident finding — `LATE_SURVIVING`
  shows a positive (unfavorable) point-estimate delta (+0.015) with a CI
  crossing zero, on an `INSUFFICIENT`-labeled 36-game sample.
- **D. Does market movement with/against us matter?** Both persistent 2×2
  cells (market-with and market-against) show favorable point estimates,
  but at 6 and 18 games respectively — far too small to distinguish them.
- **E. Does persistence add value after controlling for edge magnitude?**
  Suggestively, concentrated in the `<0%` initial-edge bucket (+0.0459
  within-bucket advantage) — but every other bucket's persistent-tier
  sample is too small (4-22 tickers) to assess.
- **F. Which market families show the strongest evidence?** **`team_total`**
  — the only family where transient edges are confidently worse than the
  market while persistent edges sit at parity, a real family-specific
  separation the other three families (first_inning_run, game_result,
  inning_result) do not show.
- **G. Is any finding strong enough for a later prospective-shadow
  candidate?** **Not yet.** The directional pattern (favoring persistence,
  particularly in `team_total`) is real and worth tracking as this corpus
  grows, but no cell here clears this study's own confidence bar. This
  finding does **not** justify a prospective shadow candidate today —
  only continued observation as more multi-checkpoint, settled,
  causally-linked data accumulates.

**Final signal classification (current model, TRUSTED_PRODUCTION):
NO_USEFUL_TRUST_SIGNAL.** (ALL_HISTORY and CANONICAL_ERA:
PARTIAL_FAMILY_SPECIFIC_TRUST_SIGNAL — distinct questions, never
conflated with the current-model finding; disposition is driven only by
the current-model classification.)

**Disposition: RESEARCH_CANDIDATE.** Nothing here changes recommendation
thresholds, confidence, Bet Up To logic, stake sizing, eligibility, or
production model probabilities — per the mission's explicit "nothing
auto-promotes" instruction.

## 9. Limitations

- The persistence-eligible corpus is small and young: 39 games / 18 dates
  (all-history), 15 games / 6 dates (trusted-production) — well below a
  mature chronological-split threshold. Every classification above is
  conservative specifically because of this.
- LINEUP_CONFIRMATION checkpoint coverage is zero in the usable
  population — the `lineupConfirmedPersistent` hypothesis (H2) cannot be
  assessed with this corpus as it stands today.
- CLV-like values reuse `research_dataset`'s hypothetical full-universe
  price-movement-to-close field, not real placed-bet CLV.
- `falseDiscoveryHandling` is registered `BENJAMINI_HOCHBERG` (required
  for an EXPLORATORY experiment that screens many segments), but this
  script does not run a formal per-cell p-value correction across its
  many tier/family/bucket cells the way MLB-RSCH-0001's family
  segmentation does — per-cell 90% CIs and the dual-threshold classifier
  (§2) are the primary conservatism guard here.
- This is a pooled retrospective analysis with no chronological
  train/holdout split — the same corpus-maturity constraint MLB-RSCH-0001
  documented.
- As more multi-checkpoint, settled, causally-linked observation dates
  accumulate, this experiment's own analysis should be rerun (not
  re-tuned) against the larger corpus rather than treated as final.
