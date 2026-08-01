# EdgeLab Phase 2 Milestone 5: market comparison engine

Status: a RESEARCH-ONLY same-game market comparison engine
(`lib/edgelab/market_comparison.py`) that compares different ways of
expressing the same underlying baseball edge (e.g. full-game ML vs F5 ML
vs run line for the same team). **Does not change production
recommendations, staking, or bet selection.** It is read-only over
`lib.edgelab.analytics`'s existing views and writes nothing back to
`data/edgelab/<entity>/`.

## 0. Why this remains research-only

This module answers *"if the model already evaluated several markets
about the same underlying belief, which one was the cleanest way to
express it?"* — a descriptive question about the model's own outputs,
not a new prediction. It never re-runs any model math, never changes
`Recommendation`/`PlacedBet` records, and is never consulted by
`scripts/edgelab/build_recommendations.py` or any other production code
path. Its comparison score (§4) is an illustrative, hand-set, un-tuned
weighting — explicitly not backtested or optimized — and its historical
analysis (§6) applies the same three-tier sample-size gate
`lib.edgelab.calibration` already established, so it never claims
statistical significance from a handful of games. Milestone 6 (staking/
portfolio optimization/automated recommendation changes) is future work
this milestone deliberately does not touch.

## 1. Clustering rules

A **cluster** groups markets within one game that express *substantially
the same underlying edge*. Clustering is built ONLY from stable
structured fields already on `ModelEvaluation` (`gameId`, `marketTicker`,
`canonicalMarketFamily`, `selection`, `correlationGroups`) plus a
deterministic horizon/team derivation — **never from title text**.

### 1.1 Horizon and team derivation (`market_horizon_and_team()`)

`lib.research.market_taxonomy.classify_market()` (the repo's one
existing ticker classifier) supplies the horizon whenever a real
`marketTicker` resolved. This is combined with, not overridden by,
`selection`'s own naming convention (`config/rules.json`'s
`ML_Away`/`F5_ML_Home`/etc., the same mapping
`lib.edgelab.model_evaluation`'s thesis-tag/correlation-group functions
already established) — a **real-data finding** made while building this
module: `classify_market()` reliably resolves `scope` (horizon) for a
`game_result` ticker but always returns `team: None` for that family (the
parser doesn't extract a side for it), and team is only known from
`selection` for the ~53% of real `ModelEvaluation` rows that never
resolved a ticker at all (`PARSER_UNRESOLVED`, Milestone 3/4 finding). An
early design that preferred `classify_market()`'s result outright and
never fell back to `selection` for team specifically would have silently
dropped the team on most real `game_result` rows; `market_horizon_and_team()`
takes the horizon from whichever source resolved one, and the team from
whichever source resolved one, independently.

### 1.2 Thesis groups (`thesis_group()`)

| Thesis | Canonical families | Meaning |
|---|---|---|
| `WIN` | `game_result`, `inning_result`, `winning_margin` | "will this team win/cover" |
| `TEAM_TOTAL` | `team_total` | "will this team score over/under N" |
| `GAME_TOTAL` | `game_total` | "will the combined score go over/under N" |
| `FIRST_INNING` | `first_inning_run` | NRFI/YRFI |
| `PLAYER_PROP` | `pitcher_strikeouts`, `pitcher_outs` | one pitcher's box-score line |

### 1.3 `cluster_key()`

- **WIN** and **TEAM_TOTAL**: one cluster per `(gameId, side)`. Full-game
  ML, F3/F5/F7 ML, and run line for one team are alternate
  horizons/instruments backing the SAME side — this is the "full-game ML
  vs F3/F5/F7" and "ML vs run line" grouping the milestone asks for.
- **PLAYER_PROP**: joins the **opposing** side's `TEAM_TOTAL` cluster. A
  pitcher's outs/strikeouts suppress the *batting* side's run production
  — i.e. the same underlying edge ("this side's offense will be
  limited") expressed via the opposing pitcher's box-score line instead
  of a team-total line. This is the "team total vs opposing pitcher outs"
  and "pitcher strikeouts vs pitcher outs" grouping (the latter falls out
  for free: both prop families for the same pitcher side land in the same
  cluster).
- **GAME_TOTAL** and **FIRST_INNING**: one cluster per `gameId`
  (game-level, not side-level) — a combined-score or first-inning bet is
  a genuinely different claim from one team's own total/win, so these are
  *visibly grouped* with the game's other markets for the historical
  "related markets" report but are **never domination-tested** against a
  side-level cluster (see §3's `DISTINCT_THESIS`). This is a deliberate
  choice: "group related markets" (item 3) and "substantially the same
  thesis" (item 6's domination gate) are different bars, and a combined
  total is genuinely not the same claim as either team's own total.
- Returns `None` (`NOT_COMPARABLE`) when the thesis is unmapped or the
  side can't be resolved at all.

Doubleheader isolation falls out for free: `gameId` is always part of the
cluster key, and Phase 1 already assigns distinct `gameId`s per game of a
doubleheader.

## 2. Normalization and comparability rules

`normalize_market_input()` builds one flat dict per market from its
`ModelEvaluation` row plus its optionally-linked `PlacedBet` (entry/
closing price, CLV) and most recent `ClvQuote` (bid/ask spread — CLV
quotes only exist for markets with a placed bet, so `bidAskSpread`/
`liquidity` are usually unknown, not zero). `latest_evaluations_per_market()`
collapses multiple evaluations of the same market over time down to the
single most recent one (comparisons measure the *current* edge
landscape; `lib.edgelab.calibration` already measures how estimates
evolved historically) — keyed by `marketTicker` when a real ticker
resolved, else `(gameId, selection, side, threshold)`, so
`PARSER_UNRESOLVED` rows for *different* markets in the same game are
never collapsed into one.

**`REQUIRED_INPUT_FIELDS`** — `modelFairProbability`, `marketImpliedProbability`,
`estimatedEdge`, `dataQuality`, `confidence` — must all be present or the
market is `INCOMPLETE_COMPARISON`; nothing is ever guessed.
`liquidity`/`bidAskSpread`/`starterExposure` are deliberately **not**
required (rarely available for evaluated-but-never-bet markets); their
absence degrades the score/status instead of blocking the comparison
(`LOW_LIQUIDITY` only fires when a wide spread IS known).

### Scale note (a real-data finding, not an assumption)

`modelFairProbability`/`marketImpliedProbability` are stored as **0-100
percentages** throughout this codebase (e.g. `64.93`), confirmed against
real committed `ModelEvaluation` records — not 0-1 fractions.
`estimatedEdge` is a smaller "percentage-edge" figure (real observed
range roughly -11..+5, consistent with `lib.edgelab.calibration`'s own
edge-bucket width of 2). This module's own `winProbability`/
`tieProbability`/`lossProbability` outputs are kept on that same 0-100
scale for internal consistency. `bidAskSpread` (from `ClvQuote.yesBid`/
`yesAsk`) IS a genuine 0-1 dollar fraction (`lib.edgelab.clv`'s `1 -
yesBid` NO-side derivation only makes sense on that scale) and is
**not** rescaled.

## 3. Three-way market treatment (F3/F5/F7)

`lib.research.market_taxonomy.HORIZON_MARKET_STATUS` confirms F3/F5/F7
have `outcomeStructureStatus == "CONFIRMED_THREE_WAY"` — a real,
settleable Tie leg exists (the horizon can end level; the full game
cannot, it continues to extra innings). `apply_three_way_adjustment()`
takes the AWAY and HOME `modelFairProbability` for the same
`(gameId, horizon)` pair and computes:

- `tieProbability = max(0, 100 - awayFairProb - homeFairProb)`
- `winProbability` / `lossProbability` per side
- `tieAdjustedFairPrice = winProbability / (winProbability + lossProbability)`
  — the fair two-way price this side would carry *if* the tie were
  excluded, which is what makes an F5 side comparable to a full-game
  (structurally tie-free) ML on a like-for-like basis.
- `comparisonEligibility = False` on **both** sides (never a guessed
  number) when either side lacks a real `modelFairProbability`.

Two-way markets (full game) never get a tie adjustment at all —
`tieProbability` stays `None` — so a two-way and three-way market are
never compared using unadjusted implied probabilities: a three-way row
with insufficient data to adjust becomes `INCOMPLETE_COMPARISON`
(`comparisonEligibility=False`), not silently treated as two-way.

## 4. Dominated-market detection (`is_dominated_by()`)

`other` dominates `candidate` only when **all four** hold:
1. `other.estimatedEdge >= candidate.estimatedEdge`
2. `other`'s data-quality rank >= `candidate`'s (`data_quality_rank()`:
   full > partial > insufficient > none)
3. `other`'s material risk <= `candidate`'s (`_material_risk()`: +1 for a
   bullpen-exposed horizon i.e. `FULL_GAME`, +1 for a known tie
   probability >= `HIGH_TIE_RISK_THRESHOLD`)
4. `other`'s bid/ask spread is no wider than `candidate`'s (when both
   known — unknown never counts against `other`)

...**and at least one of the four is strictly better** (so two identical
markets never call each other dominated). Requires a real `estimatedEdge`
on both sides — an incomplete comparison never dominates or is dominated.
**Correlation is never a factor**: correlated-but-distinct-thesis markets
(e.g. `team_total` vs `game_total`) never even share a `clusterId` (§1.3),
so they can't reach this function together at all.

This directly implements the milestone's worked examples: F5 ML
dominates full-game ML when its edge is >= and it structurally avoids
bullpen risk (`_material_risk` scores `FULL_GAME` +1); ML vs run line and
run line vs ML resolve purely on edge/quality/risk, in either direction;
alternate totals and team-total-vs-opposing-pitcher-outs fall out of the
same rule once both rows share a cluster (§1.3).

## 5. Score formula (`comparison_score()`)

A **transparent, deterministic** weighted average of 12 named
components (`SCORE_WEIGHTS`, illustrative defaults summing to 1.0, **not
tuned or backtested**). A missing component is excluded from the
weighted average and the remaining weights renormalized — never imputed.

| Component | Formula | Notes |
|---|---|---|
| `ev` | `(estimatedEdge - (-15)) / 30`, clamped [0,1] | documented illustrative edge scale |
| `confidence` | HIGH=1.0, MEDIUM=0.6, PAPER=0.2 | |
| `dataQuality` | `data_quality_rank / 3` | |
| `liquidity` | always `None` | no volume/depth field exists anywhere in this schema |
| `bidAskSpread` | `1 - spread/0.20`, clamped [0,1] | None if unknown |
| `priceSensitivity` | `abs(marketImpliedProbability - 50) / 50` | proxy for estimation-error sensitivity, not real greeks |
| `tieRisk` | `1 - tieProbability/50` | None for `HORIZON_UNKNOWN`; 1.0 for a known two-way market |
| `bullpenExposure` | 0.0 if `FULL_GAME` else 1.0 | None for `HORIZON_UNKNOWN` |
| `starterExposure` | 1.0/0.0 from `STARTER_EDGE`/`STARTER_FADE` thesisTags | None if neither tag present |
| `horizonFit` | 1.0 unless horizon unknown | |
| `historicalCalibration` | CALIBRATED=1.0, DESCRIPTIVE_ONLY=0.5 | None for INSUFFICIENT_SAMPLE/unknown -- no claim from a small sample |
| `correlationConcentration` | `1 - (overlapping peers / total peers)` in-cluster | simple count-based proxy, not a real covariance model |

## 6. Comparison statuses (fixed precedence order)

`INCOMPLETE_COMPARISON` (missing required field, or a three-way pair that
couldn't be tie-adjusted) → `NO_MODEL_SUPPORT` (evaluationStatus in
`NO_MODEL_SUPPORT`/`INVALID_PROBABILITY`/`MISSING_MARKET_PRICE`/
`DATA_QUALITY_BLOCK`/`PARSER_UNRESOLVED`) → `LOW_DATA_QUALITY`
(`data_quality_rank <= 1`, i.e. insufficient/none) → `HIGH_TIE_RISK`
(`tieProbability >= 20`) → `LOW_LIQUIDITY` (`bidAskSpread > 0.15`, only
when known) → `DISTINCT_THESIS` (thesis is `GAME_TOTAL`/`FIRST_INNING`,
never domination-tested) → `NOT_COMPARABLE` (unresolved cluster) →
`DOMINATED_MARKET` (another cluster member dominates it, per §4) →
otherwise ranked by score: `BEST_EXPRESSION` (rank 1) /
`ALTERNATIVE_EXPRESSION` (rank 2+), ties broken by `marketTicker` for
full determinism.

## 7. Historical analysis (`historical_analysis()`)

Reports: games with comparable markets, expression-cluster counts,
best-expression/dominated-market counts by canonical family, missing-data
blockers (grouped by which required fields were absent), and a
placed-bet-vs-top-alternative audit (CLV comparison when both are known).
The audit sample size is gated by `lib.edgelab.calibration.calibration_status()`'s
existing three-tier scheme (n<20 `INSUFFICIENT_SAMPLE` / 20<=n<100
`DESCRIPTIVE_ONLY` / n>=100 `CALIBRATED`) — individual examples are always
returned for manual review, but no aggregate rate or superiority claim is
computed below `INSUFFICIENT_SAMPLE`, and this module never phrases
anything as a superiority claim regardless of sample size.

## 8. Known limitations

- **`liquidity` is never populated** — no volume/depth field exists
  anywhere in this schema. `bidAskSpread` (only known for markets with a
  placed bet's CLV quote) is the best available proxy, used for
  `LOW_LIQUIDITY`.
- **`SCORE_WEIGHTS` are illustrative defaults**, hand-set and documented,
  not tuned or backtested against any outcome data.
- **Pitcher strikeouts/outs markets are not in the current 11-market
  production set** (`config/rules.json`'s `market_list`), so
  `PLAYER_PROP` clustering/domination is structurally implemented and
  covered by this milestone's tests but is not yet exercised by real
  historical data — the same "structural support for a currently-empty
  case" pattern Milestone 4 established for unsupported thesis tags.
- **`hypotheticalReturn`/full P&L simulation for non-placed alternatives
  is out of scope for this milestone** — the historical audit compares
  CLV (a field already captured for every placed bet) rather than
  simulating a fill at an assumed price for a market that was never
  traded; a real backtest is Milestone 6+ territory.
- **This report is research-only**: it does not change, and is never
  consulted by, production recommendation, staking, or bet-selection
  logic.

## 9. Files

- `lib/edgelab/market_comparison.py` — the engine (this doc).
- `scripts/edgelab/run_market_comparison_report.py` — CLI: writes
  `data/edgelab/analytics/latest_market_comparison_report.json` (machine-
  readable) and `data/edgelab/reports/phase2_market_comparison.md`
  (human-readable).
- `tests/edgelab/test_market_comparison.py` — unit + end-to-end coverage.
