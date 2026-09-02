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

## Results (development data: 29 dates, 391 games, 497,132 decision rows)

Artifacts: `family_c_results.json`, `family_d_results.json`,
`family_d_multibook_results.json`, `family_e_results.json`,
`family_t_results.json`, `event_study_lineups.json`,
`candidate_eval_f5_reversal.json`, `discovery_summary.json`,
`hypothesis_registry.json` (22 registered hypotheses, all reported).

**Headline: Kalshi's next move is partly predictable from its own order
flow and recent price path, but never by more than the spread-plus-fee
hurdle at taker; no candidate has positive post-fee economics on
development data.**

- **Family C (105 coarse cells, BH-FDR q=0.10, game-cluster CIs)**:
  43 survivors on fair-mid direction and 47 on executable CLV — taker
  order-flow imbalance (30/60 min), last-trade-vs-mid, and 30/60-minute
  **reversal** on totals and F5 moneylines all predict the direction of
  the next move by +0.1…+0.8¢. **21 survivors on post-fee P/L — every one
  negative; zero cells with post-fee P/L > 0 at 95%.** Momentum
  continuation is rejected (moves revert). Walk-forward ridge: OOS
  correlation 0.31 on F5 moneylines (directional accuracy 0.61 when the
  market moved), ≈0 elsewhere; signalled-row P/L not > 0.
- **F5 moneyline reversal (candidate C01)**: fair-mid reversal positive
  with CI excluding 0 in **all 12** predeclared variants (+0.7…+5.8¢);
  executable CLV +0.8…+4.7¢; $10 post-fee P/L never significant (best
  h60/k3 DOWN: +2.19 [−0.44, +4.59], p=0.089, 55 games; the 21-date
  interim of this cell, +5.39 [2.46, 7.76], **did not survive** the final
  8 dates — spreads widened to ~7¢). Robust price discovery, unproven fill
  economics → prospective proof required.
- **Family D (Pinnacle lead/lag, pilot 2 dates / 20 games / 1,250
  snapshot rows at 15-min)**: mean |Kalshi − Pinnacle| **0.50pp**;
  corr(Pinnacle past 15 m, Kalshi next 15 m) = 0.05. The supported claim
  is exactly **"no detectable Pinnacle → Kalshi lag at 15-minute
  resolution in a 2-date pilot"** — INCONCLUSIVE, not a refutation. An
  exploitable lead may live on a shorter timescale that 15-minute
  snapshots cannot resolve, so `D01-SHARPLAG` is retained as
  PROSPECTIVE_ONLY / HISTORICAL-PILOT-INCONCLUSIVE and is not demoted.
  Multi-book slate panel (315 rows, 103 games): |consensus − Kalshi|
  0.58pp; ≥2pp disagreements too rare to test at that cadence.
- **Family E (production model)**: team_total (72 OOS games): Δ log loss
  1.5e-5 (CI −3e-5..7e-5) — **no incremental information**; fair mid
  moves *away* from the model's side (−0.30¢, CI −0.53..−0.07). Other
  families ≤8 evaluation dates → INSUFFICIENT.
- **Topology**: F5/full-game expected-total ratio extremes do not
  predict F5-total or team-total moves (wrong sign or CI includes 0).
- **Lineup confirmation (bounded by slate captures, 163 rows / 82
  games)**: event-window |move| 0.51¢ vs control 0.62¢ — nothing
  measurable at 181-minute windows; only prospective capture can test it.

## Frozen candidates (`frozen_candidates.json`, 5 ≤ 10)

| ID | Status | Prospective test |
|---|---|---|
| C01-F5REV — F5 ML 60-min ≥3¢ reversal, both sides | HISTORICALLY_SUPPORTED price discovery, post-fee unproven | forward episodes; preregistered sparse minimum 60 episodes / 40 games / 12 dates |
| C02-OFI — taker order-flow follow-through | price discovery, NOT taker-tradable | shadow only |
| D01-SHARPLAG — Pinnacle → Kalshi minutes-scale lag | PROSPECTIVE_ONLY | needs 10-min capture or credit-gated 5-min history |
| I01-LINEUP — confirmation repricing lag | PROSPECTIVE_ONLY | first-seen timestamps from capture |
| C03-BOOKIMB — order-book imbalance | PROSPECTIVE_ONLY | order-book capture |

None is authorized for real money.

## Prospective capture built (not yet running)

`scripts/research/mlb_alpha_0002/prospective_capture.py` +
`.github/workflows/research-mlb-alpha-0002-capture.yml`: every 10 minutes
in the MLB window (schedule inert until the file is on main; dispatch
available now) — Kalshi quotes + **full order book** + trade tape delta,
live Pinnacle/DK/FD/BetMGM h2h+totals with book `last_update`, and MLB
probable-pitcher / lineup-posted state so the first capture that shows a
fact is a timestamped information event. Append-only, read-only, commits
to a research branch only.
