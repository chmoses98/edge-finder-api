# EdgeLab Research Trustworthiness milestone

Status: research-only, read-only. Nothing in this milestone changes
model probabilities, projection formulas, production recommendation
logic, bet/tier thresholds, risk gates, bankroll rules, stake sizing,
production market selection, Kalshi live execution, lineup gates, or any
production slate output. Nothing produced here feeds automatically into
production betting decisions.

Goal: make the historical research system trustworthy enough to answer
*"Across every MLB Kalshi contract we observed — including contracts we
never recommended and never bet — where was Kalshi mispriced, where did
our model have genuine predictive value, and which apparent historical
edges survive proper out-of-sample and correlation-aware analysis?"*

## 0. Running it locally

```bash
python3 scripts/edgelab/run_research_reports.py
```

Rebuilds the canonical opportunity dataset over every committed date
under `data/edgelab/observations/` and writes eight machine-readable
reports to `data/edgelab/analytics/latest_research_<name>.json` plus one
human-readable summary to
`data/edgelab/reports/research_trustworthiness_summary.md`. Never writes
to `data/edgelab/observations/`, `model_evaluations/`, `settlements/`,
`recommendations/`, `games/`, or `bets/`.

## 1. The calibration scale bug (confirmed and fixed)

`ModelEvaluation.modelFairProbability` is stored 0-100
(`model_evaluation.schema.json`). `PlacedBet.modelFairProbability` is
stored 0-1, matching `entryPrice`
(`lib/edgelab/bets.py:resolve_recommendation_context`'s own `/100`
conversion documents this). `lib/edgelab/calibration.py`'s
`actualWinRate` is always a 0-1 fraction (`wins / n`).

The bug was not in `calibration.py`'s own arithmetic — it was in
`lib/edgelab/analytics.py`'s `v_placed_bets` view, which reads
`modelFairProbability` from a linked `ModelEvaluation` (0-100 scale)
whenever a bet's `modelEvaluationId` resolves, via
`COALESCE(em.modelFairProbability, bet's own 0-1 copy)` — no conversion.
Every calibration bucket that read a bet with a resolved link therefore
compared `actualWinRate` (0-1) against `expectedWinRate` (occasionally
0-100), producing exactly the failure mode described in this milestone's
brief: `actualWinRate=0.538` vs `expectedWinRate=47.99` →
`calibrationError≈-47.45`.

**Fix**: `analytics.py`'s `v_placed_bets` view now divides
`em.modelFairProbability` by 100 before the `COALESCE`, so
`v_placed_bets.modelFairProbability` is always 0-1 regardless of which
source resolved. Every function in `calibration.py` (edge-bucket,
confidence, market-family, CLV-bucket, timing-bucket, daily/weekly/
monthly/season trend, model-version/data-quality/correlation-group) and
`market_intelligence.py`'s `calibrationQuality` inherit the fix
automatically, since they all read from this one view. Regression tests:
`tests/edgelab/test_calibration.py::test_linked_model_evaluation_probability_normalized_to_0_1_scale`
and the corrected expectation in
`tests/edgelab/test_model_evaluation.py::test_calibration_prefers_linked_model_evaluation_over_bet_own_copy`
(previously asserted the buggy 0-100 pass-through as correct behavior).

`market_comparison.py` was audited too: it deliberately stays in 0-100
space throughout and never subtracts a 0-1 value from a 0-100 one, so it
does not have this bug. A **separate, narrower** scale issue was found
there during this audit (`bidAskSpread` is computed from `ClvQuote`'s
`yesBid`/`yesAsk`, which are genuinely 0-100 on disk — confirmed against
every committed `clv_quotes` file — while `LOW_LIQUIDITY_SPREAD` and the
score component's `/0.20` divisor assume 0-1; the module's own header
comment and its tests both currently encode the *wrong* 0-1 assumption).
This is a liquidity/ranking heuristic, not a calibration/win-rate
computation, and fixing it correctly requires rewriting the many
existing tests that bake in the wrong convention — left as a documented,
flagged follow-up rather than folded into this milestone's fix, to avoid
scope creep into an unrelated module under time pressure.

## 2. No-look-ahead temporal alignment

`lib/edgelab/query.py`'s `build_research_rows` picks
`evals_for_ticker[-1]` — the last element of a list in whatever order the
caller loaded it, not sorted by time. Confirmed against real data: 15
tickers in a single day's file have 2 `ModelEvaluation` rows with
identical `createdAt`/`pipelineRunId` (different `selection`s mapping to
the same Kalshi ticker), so list order there is genuinely arbitrary.

Two candidate ModelEvaluation timestamps exist:
- `createdAt` — EdgeLab's own **ingestion** timestamp, second-resolution,
  stamped once per ingestion batch (confirmed: many rows share the exact
  same value). Not a decision-time signal.
- `pipelineRunId` — copied verbatim from the upstream
  `recommendations.json` artifact's own `meta.createdAt`, i.e. the
  moment the **production pipeline** actually ran and froze that day's
  fair probabilities, before EdgeLab ever ingested them. Confirmed
  against every committed `model_evaluations/*.jsonl` file: exactly one
  non-null `pipelineRunId` per date (the pipeline runs once per day).

`lib/edgelab/temporal_alignment.select_temporally_valid_evaluation`
implements the rule: for a checkpoint captured at T, the eligible
evaluation is the latest one whose `pipelineRunId <= T`, using
`pipelineRunId` **only** — never `createdAt`, never a fabricated
timestamp. A `ModelEvaluation` with no `pipelineRunId` (e.g. a
`NOT_EVALUATED` full-universe-extension row) can never be proven causal
and is never selected. Disambiguates same-ticker/different-selection
rows deterministically (YES-side preferred, else lexicographic), and
returns every temporally-eligible candidate so nothing is silently
dropped. See `tests/edgelab/test_temporal_alignment.py` for the
no-look-ahead proof tests (list-order independence, future-evaluation
rejection, missing-timestamp handling).

`lib/edgelab/query.py` itself is left unchanged in this milestone — it
is a distinct, already-tested, widely-used module — but the new
`lib/edgelab/research_dataset.py` uses the causal selector exclusively
and never the ticker-only `[-1]` pattern.

## 3. Canonical opportunity dataset

`lib/edgelab/research_dataset.build_opportunity_rows` is the core
deliverable: one row per (marketTicker, standardized checkpoint) across
the **full** observed universe, joined by ticker + causal time, not
merely by ticker. Reuses `lib.edgelab.checkpoints.select_closing_quote`
for the canonical closing-quote definition (never "just the last
observation"), `lib.edgelab.market_family_mapping.canonicalize_market_family`,
and `lib.edgelab.settlement.hypothetical_yes_return`. A market's own
`checkpoint` classification (`FIRST_DAILY`/`T_MINUS_*`/
`LINEUP_CONFIRMATION`) is kept as-is; `isClosingQuote` is a separate
boolean so a market never gets two rows for the same underlying price
tick. `POST_START`/`INTERMEDIATE` observations never become their own
row unless the `INTERMEDIATE` tick is genuinely the selected closing
quote. Every `*Price`/`*Probability` field is normalized to 0-1.
`contemporaneousEdge` = model probability minus **this row's own**
executable price (never a different observation's price — see
`estimatedEdgeAtEvaluationTime`, kept separate, for the pipeline's own
stale-snapshot figure).

## 4. Correlation-aware uncertainty

`lib/edgelab/research_stats.py` never treats a raw contract count as an
independent sample. `game_clustered_bootstrap_ci` resamples whole GAMES
(not rows) with replacement — every row belonging to a resampled game
moves together — a documented, deterministic (fixed seed) block
bootstrap. `sample_size_status` reports the existing
n<20/20-99/100+ tiers (`lib.edgelab.calibration.calibration_status`,
reused unchanged) **plus** an explicit `gameConcentrationWarning` when
contracts-per-game is high, with conservative, non-promotional
interpretation text (never "profitable"/"validated"/"proven"/
"actionable"). Brier score and log loss are reused verbatim from
`lib.edgelab.replay` — not reimplemented a third time.

## 5. Chronological out-of-sample framework

`lib/edgelab/research_splits.py` splits strictly by **game date**
(never by individual contract — same-game/same-date contracts are
highly correlated and would leak across a random split), default
60/20/20 DEVELOPMENT/VALIDATION/HOLDOUT, configurable. Below 30 distinct
dates the split is labeled `FRAMEWORK_ONLY_INSUFFICIENT_DATES` — the
real current corpus (13 dates) reads this way. `strategy_validation`
runs the identical `edge_backtest` methodology independently on each
partition and never selects/tunes anything based on any partition's
result.

## 6. Reports

`scripts/edgelab/run_research_reports.py` writes:

| File | Spec item |
|---|---|
| `latest_research_market_calibration.json` | A: full-universe Kalshi calibration by price bucket / family / horizon / checkpoint |
| `latest_research_model_calibration.json` | B: model calibration on causally-valid rows, with contemporaneous market comparison |
| `latest_research_edge_backtest.json` | C: performance by edge bucket, YES and NO sides both represented |
| `latest_research_market_family_research.json` | D: coverage/calibration by (family, horizon, threshold) |
| `latest_research_checkpoint_research.json` | E: FIRST_DAILY/T-90.../CLOSING comparison |
| `latest_research_ladder_research.json` | F: alternate-threshold monotonicity |
| `latest_research_research_data_quality.json` | G: exact coverage/missingness |
| `latest_research_strategy_validation.json` | H: DEVELOPMENT/VALIDATION/HOLDOUT framework |
| `reports/research_trustworthiness_summary.md` | concise human-readable summary |

## 7. Can we run a causal, no-look-ahead backtest of "model edge at time T" against the Kalshi price at time T, across the full observed universe?

**Infrastructure: yes. Empirically, on the current corpus: not yet, and
the report says so explicitly.**

The join is now provably causal (`pipelineRunId <= T`, verified
immutable and pre-ingestion) and covers the full observed universe, not
only bet/recommended markets. But the current corpus's real capture
cadence and the production pipeline's own once-daily, evening run time
mean very few checkpoints were actually captured **after** that day's
pipeline ran: of 75,280 opportunity rows (13 dates, 241 games, 47,948
tickers), only 264 rows (68 games) have a causally-valid model
evaluation at all — `FIRST_DAILY` checkpoints (the large majority of
rows) average ~7 hours before scheduled start, well before the pipeline
runs. This is a genuine, correctly-surfaced **data-coverage** gap, not a
code gap: `research_data_quality`'s `rowsLackingTemporallyValidModelEvaluation`
field makes it explicit rather than hiding it behind a look-ahead join
that would have produced a much larger but untrustworthy "coverage"
number. More capture checkpoints close to/after the pipeline's daily run
time (or a pipeline that runs more than once a day) would directly grow
this number; nothing further is required in the join logic itself.
