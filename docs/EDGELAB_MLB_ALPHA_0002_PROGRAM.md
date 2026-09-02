# EdgeLab — MLB-ALPHA-0002: Predictive Signal + External Data Discovery

**Status: RESEARCH ONLY — DEVELOPMENT evidence. No production change.**
Nothing in this program places a wager, creates a recommendation, changes
staking / Bet Up To / confidence / risk gates / family status / C01-PIT,
or modifies production probabilities. Kalshi is read-only. Any real-money
activation requires explicit CEO approval after **prospective** evidence.

MLB-ALPHA-0001 A+B is CLOSED and not reopened here (no price-band mining,
no YES/NO bias mining, no arbitrage scans, no C01 tuning). C01-PIT remains
PROSPECTIVE SHADOW ONLY; its 2026-08-26..08-31 dates are SPENT and are
treated as ordinary development data, never as unseen evidence.

## Mission

Find information available **before** an MLB Kalshi trade that repeatedly
predicts (a) where Kalshi's price will move, (b) where Kalshi is mispriced
relative to settlement, (c) when an executable post-fee trade has positive
expected value. Search aggressively, test ruthlessly, promote reluctantly.

## Validation rule (binding)

The August archive is **development data**. There is no remaining clean
historical holdout for this program. Any candidate intended to become
actionable must prove itself on **forward data captured only after its
exact rule/model/pipeline is frozen** (freeze timestamp, code SHA, feature
schema hash, rule hash, source versions, execution assumptions, evaluation
protocol recorded first; no retroactive change). Default first material
checkpoint: ≥100 independent games AND ≥10 independent dates; sparse
candidates must preregister an alternate minimum before outcomes exist.

## Conventions

- **CLV**: `POSITIVE_IS_GOOD_V1`, computed only through
  `lib.edgelab.clv_convention` (closing executable − entry executable, side
  relevant). **Executable CLV** (fill economics) and **fair-mid CLV**
  (midpoint diagnostic) are kept distinct; midpoint is never a fill.
- **Execution**: BUY YES at the ask, BUY NO at 100 − bid; $10 standardized
  taker order through `lib.edgelab.kalshi_fees` (whole contracts,
  actual-cash-consumed denominator).
- **Settlement truth**: settlement store + the research-layer `≥N` ladder
  correction; `KXMLBF5SPREAD` excluded (settled on the wrong horizon).
- **Identity**: `lib.edgelab.mlb_alpha_identity` (exact ticker parsing,
  doubleheaders, never fuzzy); external games join by `oddsApiEventId`.
- **Independence**: GAME is the cluster; every CI is a game-cluster
  bootstrap; evaluation is walk-forward by date (burn-in 5–6 dates); no
  random row splits; contracts of one game never straddle folds.

## Phase 0 — the alpha data map

Machine-readable: `data/edgelab/research_artifacts/mlb_alpha_0002/data_source_manifest.json`.

| Source | Historical | Prospective | Verdict |
|---|---|---|---|
| Kalshi observation archive (`data/edgelab/observations`) | 08-01→09-02, 452k rows; ~60-min pregame cadence mid-Aug, ~1 quote/ticker after 08-25; no depth, no NO book, no trade count | running (~30 min) | too coarse for microstructure |
| **Kalshi 1-minute candlesticks** (exchange record) | **recovered** via public `/series/{s}/markets/{t}/candlesticks?period_interval=1`; smoke 186 tickers → 105,736 candles | on demand | **primary Family C source** |
| **Kalshi public trade tape** (`/markets/trades`) | **recovered**; smoke 267,832 prints with taker side, size, µs timestamps | on demand + 10-min capture | **order-flow features now possible** |
| Kalshi order book | none (no history endpoint) | new capture built | PROSPECTIVE_ONLY |
| Pinnacle via The Odds API `/historical` | provider holds 5-min snapshots to 2022; **pilot recovered** 08-19/08-20, 81 snapshots, 0 errors, 1,600 credits | live endpoint in new capture | credits: full August ≈ 24k vs 18,100 remaining → CEO decision |
| Multi-book slate snapshots (Pinnacle/DK/FD/BetMGM) | 46 dates; 2/day early Aug → 4–14/day from 08-22; per-book `updated` ts | running | coarse lead/lag only |
| Production model evaluations | 07-30→09-01; probabilities for team_total, K's, totals, F5, ML; hitters null; ~8 dates/family | */15 min | Family E limited |
| Lineup / pitcher confirmation **timestamps** | **none** (state only, 1/day; slate flips bounded by hours) | new capture (MLB Stats API) | PROSPECTIVE_ONLY |
| Weather | current conditions at 1–2 snapshots/day (frozen) | forecast revisions not captured | PROSPECTIVE_ONLY |
| Umpires | none | day-of feed possible | low priority |
| Statcast postgame archive | pitch-level, no wall clock | n/a | not a pregame signal |

**Exact blockers**: sandbox has no egress (all acquisition runs in GitHub
Actions); a new `workflow_dispatch` file is not dispatchable until it is on
the default branch (recovery therefore runs through the registered generic
research probe workflow in 45-minute resumable rounds); Odds API quota;
no historical event timestamps; no order-book history.

## PIT feature warehouse

- `pit_candle_panel.jsonl.gz` — one row per contract per decision time on a
  5-minute grid T-240..T-5 from the exchange record. Features: price state,
  momentum (5/10/30/60), spread dynamics, volume/OI deltas, staleness,
  same-direction run, **order-flow imbalance** (taker YES − NO qty over
  10/30/60 min), block trades, last-trade-vs-mid. Targets: fair-mid move
  to close and +15/30/60, executable CLV YES/NO, corrected settlement,
  $10 post-fee P/L both sides.
- `pit_kalshi_panel.jsonl.gz` — the same idea on the coarse observation
  archive (148,883 rows, 361 games, 28 dates); reproducible, not committed.
- `pit_sharp_panel.jsonl.gz` — multi-book vig-free probabilities per
  (game, capture, book) from every slate snapshot (6.7k rows, 46 dates).
- As-of rule everywhere: a value is known at the time **we captured it**
  (book `updated` retained only as event time). Leakage tests: synthetic
  future jump never reaches a feature; future trades excluded from OFI.

## Results (development data; updated as recovery rounds land)

See `family_c_results.json`, `family_d_results.json`, `family_e_results.json`
and `hypothesis_registry.json` (22 registered hypotheses, winners and
losers alike).

- **Family E (team_total, 72 OOS games, 18 dates)**: the production model
  adds **no** incremental settlement information after conditioning on
  Kalshi (Δ log loss 1.5e-5, CI −3e-5..7e-5). Price discovery goes the
  *wrong* way: the fair mid moves **away** from the model's side (−0.30¢,
  CI −0.53..−0.07) and executable CLV on the model's side is −0.84¢
  (CI −1.14..−0.57). REJECTED. Other families INSUFFICIENT (≤8 dates).
- **Family D pilot (game_result, 2 dates, 9 games, 416 snapshot rows)**:
  mean |Kalshi − Pinnacle| is only **0.49pp** — Kalshi is not naïvely
  stale on moneylines. Disagreement drifts toward Pinnacle (slope 0.12/pp
  at +30 min → 0.28/pp at close) but every CI includes 0 at this size;
  lead/lag correlation 0.08. Totals: no exactly matching x.5 lines on the
  pilot dates. Needs the full Pinnacle pull (credits) to resolve.
- **Family C**: pipeline validated on 08-20 (7,208 rows); coarse-rule
  tests and walk-forward require ≥6 dates → pending recovery rounds.

## Prospective capture built (not yet running)

`scripts/research/mlb_alpha_0002/prospective_capture.py` +
`.github/workflows/research-mlb-alpha-0002-capture.yml`: every 10 minutes
in the MLB window (schedule inert until the file is on main; dispatch
available now) — Kalshi quotes + **full order book** + trade tape delta,
live Pinnacle/DK/FD/BetMGM h2h+totals with book `last_update`, and MLB
probable-pitcher / lineup-posted state so the first capture that shows a
fact is a timestamped information event. Append-only, read-only, commits
to a research branch only.
