# EdgeLab Phase 2 Milestone 6: Market Intelligence Engine

Status: a RESEARCH-ONLY Market Intelligence Engine
(`lib/edgelab/market_intelligence.py`) that turns "tracking what
happened" (Milestones 1-3), "how well-calibrated is the model"
(Milestone 2), and "which expression of an edge was cleanest"
(Milestone 5) into "what historically works": expression performance
profiles, opportunity-cost measurement, pass analysis, labeled
hypothetical strategy replays, edge stability, and market health scores.
**Does not change production recommendations, staking, or bet
selection.**

## 0. Why this remains research-only

Every function here reads existing `lib.edgelab.analytics` views and
`lib.edgelab.market_comparison`'s comparison output; nothing is written
back to `data/edgelab/<entity>/`, and nothing here is consulted by
`scripts/edgelab/build_recommendations.py` or any other production code
path. `strategy_experiments()`'s output carries a literal
`SIMULATION_LABEL` marker on every result so no downstream consumer can
mistake a hypothetical replay for a real recorded outcome. Every
sample-size-sensitive report reuses `lib.edgelab.calibration`'s existing
three-tier gate (`calibration_status()`) rather than inventing a new one
— this module never claims statistical significance or recommends a
strategy change; it measures.

## 1. Expression performance profiles (`expression_performance_profiles()`)

One profile per `canonicalMarketFamily`, combining
`lib.edgelab.calibration.market_family_calibration()`'s existing
`n`/`winRate`/`roi`/`avgClv`/`calibrationError`/`status` (never
re-derived) with four new frequency measures:

| Field | Numerator | Denominator |
|---|---|---|
| `recommendationFrequency` | `BET_PLACED` recommendations | total `ModelEvaluation` rows in that family |
| `passFrequency` | `PASS_*` recommendations | total `ModelEvaluation` rows in that family |
| `bestExpressionFrequency` | `BEST_EXPRESSION` comparisons | total CLUSTERED comparisons in that family |
| `dominatedFrequency` | `DOMINATED_MARKET` comparisons | total CLUSTERED comparisons in that family |

## 2. Opportunity cost analysis (`opportunity_cost_analysis()`)

For every **placed** bet whose market belongs to a multi-member
`lib.edgelab.market_comparison` cluster where it was **not** the
top-ranked (`comparisonRank == 1`) expression:

- `lostEstimatedEdge`: the top-ranked market's `estimatedEdge` minus this
  bet's own.
- `lostClv` / `lostRoi`: the top-ranked alternative's realized CLV/ROI
  minus this bet's own — **only** when that alternative was ALSO itself
  placed and settled (never fabricated for a market that was never
  actually bet).
- `dominatedByBestExpression`: whether this bet's own market carried
  `comparisonStatus == DOMINATED_MARKET`.

Gated by `calibration_status()` on the number of placed-and-clustered
bets; "how often" is reported, but no superiority claim is made below
the sample threshold. **This never recommends a change — measurement
only.**

## 3. Pass analysis (`pass_analysis()`)

Groups `Recommendation` rows by the REAL `status` vocabulary this
repo's pipeline actually writes (confirmed against committed data):

| Category | Real `Recommendation.status` |
|---|---|
| `RECOMMENDED_NOT_BET` | `RECOMMENDED` (always carries `betPlaced=False`) |
| `PASS_NO_EDGE` | `PASS_NO_EDGE` |
| `INSUFFICIENT_SUPPORT` | `PASS_DATA_QUALITY` |
| `DOMINATED` | linked `ModelEvaluation`'s `comparisonStatus == DOMINATED_MARKET` |

(`lib.edgelab.calibration.recommendation_path_calibration()` checks for a
literal `'RECOMMENDED_NOT_BET'` status string that never appears in real
data — a pre-existing, out-of-scope mismatch this module does not
inherit; it uses the real `RECOMMENDED` string instead.)

### A real-data finding that reshaped this function

This function was originally designed to compute a hypothetical
win/loss and return for these never-bet markets using `Settlement`.
**That design was wrong and was corrected before shipping**:
`Settlement.result` is `YES`/`NO` — whether *this specific ticker's* YES
side settled true — not `WIN`/`LOSS`. Turning a `YES`/`NO` into a
win/loss requires knowing which side (`YES`/`NO`) the recommendation
implicitly favored. `Recommendation` carries no `side` field at all, and
`ModelEvaluation.side` is documented as "usually null at evaluation
time" — and is in fact `null` on every real committed record. There is
no non-fabricated side to attribute a settlement outcome to for a market
that was never actually bet.

So `pass_analysis()` reports only `settlementStatusCounts` (did the
market eventually reach `SETTLED`/`VOID`/`UNAVAILABLE`/
`SETTLEMENT_UNRESOLVED` at all — purely descriptive) and never a
hypothetical return for these categories. Contrast with
`strategy_experiments()` below, which only ever replays REAL settled
`PlacedBet` rows — those always carry a real `side` and a real
pipeline-derived `WIN`/`LOSS` `result` (via `lib.edgelab.settlement`), so
no side is ever guessed there either.

## 4. Strategy experiments (`strategy_experiments()`) — labeled simulations

Every result carries `SIMULATION_LABEL = "HYPOTHETICAL_SIMULATION"`.
Each experiment replays the REAL settled `PlacedBet` ledger under a named
rule and reports the ACTUAL baseline alongside the simulated variant
(`deltaRoiVsBaseline`), gated by `calibration_status()`:

- **`DOMINATED_MARKETS_REPLACED_WITH_BEST_EXPRESSION`**: every settled
  bet on a `DOMINATED_MARKET` is replaced with its dominant
  alternative's realized return-per-dollar — ONLY when that alternative
  was itself actually placed and settled (a real recorded outcome, never
  fabricated). The swap preserves the ORIGINAL bet's stake and
  substitutes the target's `netProfitLoss/stake`: a claim about which
  expression paid better per dollar risked, not a claim about what stake
  would have been used.
- **`ALWAYS_PREFER_F5`**: same swap mechanism, targeting the cluster's F5
  alternative for every settled non-F5 WIN-thesis bet.
- **`NEVER_FULL_GAME_ML_WITH_BULLPEN_DISADVANTAGE`**: excludes every
  settled full-game ML bet tagged `BULLPEN_DISADVANTAGE`
  (`lib.edgelab.model_evaluation`'s own evidence-backed tag) — a pure
  subtraction, no substitute bet fabricated.
- **`REMOVE_NEGATIVE_CLV_MARKETS`**: excludes every settled bet with a
  recorded negative CLV — a pure subtraction.

## 5. Edge stability (`edge_stability()` / `_classify_market_edge_stability()`)

Groups **all** `ModelEvaluation` snapshots for a market (the full
history, unlike `market_comparison.latest_evaluations_per_market`, which
collapses to the most recent) and classifies the market's first-snapshot
edge bucket (width `EDGE_BUCKET_WIDTH = 2`, matching
`lib.edgelab.calibration.edge_bucket_calibration`'s own convention) as:

- **`STABLE`**: the edge bucket held across every available checkpoint
  (time / lineup confirmation), and — when a real settled bet exists —
  it won.
- **`VOLATILE`**: the edge bucket changed across time or lineup
  confirmation.
- **`FALSE_EDGE`**: the edge bucket held steady everywhere, but a real
  settled bet on this exact market LOST.
- **`UNKNOWN`**: not enough checkpoints to classify (the common real-data
  case today — this repo currently has only one `ModelEvaluation`
  snapshot per market).

**Settlement checkpoint correctness**: this function uses
`_settled_bet_result_by_eval_id()` — a real, pipeline-derived
`WIN`/`LOSS` from a linked `PlacedBet` (which always carries a real
`side`) — **never** a raw `Settlement.result` (`YES`/`NO`) compared
against a guessed side. An unbet market's edge is never scored
`STABLE`/`FALSE_EDGE` from settlement data alone, for the same reason
described in §3.

## 6. Market health scores (`market_health_scores()`)

Per `canonicalMarketFamily`, a transparent weighted score (same
"visible named components, no black box" principle as
`lib.edgelab.market_comparison.comparison_score`):

| Component | Weight | Source |
|---|---|---|
| `sampleQuality` | 0.25 | `n / MIN_N_CALIBRATED`, clamped [0,1] |
| `clvQuality` | 0.20 | fraction of settled bets in that family with `clv > 0` |
| `calibrationQuality` | 0.25 | `1 - min(1, abs(calibrationError))` |
| `stability` | 0.20 | `STABLE / (STABLE+VOLATILE+FALSE_EDGE)` among that family's classified markets |
| `recommendationQuality` | 0.10 | `bestExpressionFrequency` from §1 |

A missing component is excluded from the weighted average and the
remaining weights renormalized — never imputed with a guessed neutral
value. A `healthScore` of `0.0` means "measured, and zero" — a real
value; `null` means "not measurable at all" for that family yet.

## 7. Reports

`scripts/edgelab/run_market_intelligence_report.py` builds
`lib.edgelab.market_comparison.build_comparisons()` **once** and threads
it through every function above (avoiding redundant recomputation),
bundles in `lib.edgelab.calibration`'s existing daily/weekly/season-to-date
trend reports (reused, not reimplemented), and writes
`data/edgelab/analytics/latest_market_intelligence_report.json` +
`data/edgelab/reports/phase2_market_intelligence.md`.

## 8. Known limitations

- **No hypothetical win/loss for never-bet markets** (§3) — a structural
  gap in this schema (no recorded side for a Recommendation/
  ModelEvaluation), not something this milestone can fix without
  fabricating data.
- **`strategy_experiments()`'s swap-based simulations require a REAL
  settled bet on the alternative market** to compute a substitute
  return — with the current small real dataset (14 settled bets), most
  swap-based experiments report `swappedBetCount: 0` and
  `INSUFFICIENT_SAMPLE`. This is expected and correctly gated, not a
  bug.
- **`HEALTH_WEIGHTS` are illustrative defaults**, not tuned or
  backtested, mirroring `lib.edgelab.market_comparison.SCORE_WEIGHTS`'s
  own documented limitation.
- **Edge stability's lineup-confirmation and settlement checkpoints
  require multiple real snapshots/settled bets per market** — this
  repo's real data currently has only one `ModelEvaluation` snapshot
  per market and few settled bets, so most edge buckets today report
  `UNKNOWN`. The classification logic is validated by this milestone's
  synthetic-fixture tests.
- **This report is research-only**: it does not change, and is never
  consulted by, production recommendation, staking, or bet-selection
  logic.

## 9. Files

- `lib/edgelab/market_intelligence.py` — the engine (this doc).
- `scripts/edgelab/run_market_intelligence_report.py` — CLI report.
- `tests/edgelab/test_market_intelligence.py` — unit + integration coverage.
