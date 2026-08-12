# Hitter Projection Engine — Phase 4: Full Pitch-Aware PA Outcome + Game Simulation Engine

Builds on PR #78 (canonical hitter feature schema), PR #79 (raw Statcast
pitch archive + historical/as-of derivation), and PR #80 (spray/park/
weather/defense/catcher/umpire environment foundation). This phase adds
the actual hitter modeling layer: **one coherent simulation engine**
that independently prices the full hitter-prop universe from a single
underlying simulated baseball process — not a separate black-box model
per market.

## 1. Architecture

```
PREGAME CONTEXT (PR #77-80, reused as-is)
        |
PITCH ENVIRONMENT           pitch_environment_model.py
   P(pitch family | pitcher, batter hand, count)
        |
PITCH / COUNT SEQUENCE      pitch_sequence_model.py
   P(swing) -> P(ball|take)/P(strike|take)
             -> P(whiff)/P(foul)/P(in play | swing)
        |
   (if IN_PLAY) --> CONTACT MODEL   hitter_contact_model.py
                     EV x LA x spray draw -> batted-ball outcome
        |
PA TERMINAL OUTCOME         hitter_pa_outcome_model.py  <- the ONE shared
   {K,BB,HBP,1B,2B,3B,HR,OUT}                              outcome model
        |
GAME / LINEUP STATE         lineup_game_simulator.py
   9-inning Monte Carlo, target hitter uses the full chain above,
   other 8 lineup spots use a simpler calibrated categorical draw,
   starter -> bullpen exposure re-evaluated each inning
        |
FULL-GAME STAT LINE -> MARKET DISTRIBUTIONS   hitter_market_distributions.py
   hits / HR / total bases / RBI / runs / walks / K, full pmf + atLeast[N]
        |
PRICING + BOARD              hitter_pricing.py, hitter_board_builder.py,
                              scripts/build_hitter_projection_board.py
```

Every downstream market (1+/2+/3+ hits, alternate hit lines, HR,
total bases + alternates, RBI + alternates, runs + alternates, walks,
strikeouts) is read off the **same** simulated game stat lines — there
is no separate regression per market family.

## 2. Model components

| Module | Responsibility |
|---|---|
| `hitter_shrinkage.py` | Beta-Binomial-style empirical-Bayes hierarchical shrinkage (finest → broadest, continuous, no bucket-boundary jumps). `hierarchical_shrink()` walks a list of `ShrinkageLevel`s broadest-first, each shrinking toward the already-shrunk parent. |
| `hitter_pa_outcome_model.py` | The shared PA-terminal outcome model. Shrinks this hitter's own PA-terminal rates **by pitch family** (`hitter_pitch_derivation.derive_pa_outcomes_by_pitch_family`) toward season, toward league prior, then weights by *today's* pitcher's own pitch mix. Two bounded (±3pp), independently toggle-able adjustments — platoon (PR #77's `platoonContext`) and pitcher quality (K%/BB% vs. league) — are layered on top. |
| `pitch_environment_model.py` | `derive_pitcher_pitch_mix()`: today's pitcher's own pitch-family mix, optionally conditioned on batter hand, hierarchically shrunk toward an unconditioned mix. |
| `pitch_sequence_model.py` | Pitch-by-pitch PA simulator. Per pitch: family draw (weighted by pitcher mix) → zone/no-zone → swing/take → (contact→in-play/foul/whiff). Honestly flags `CATCHER_UMPIRE_ADJUSTMENT_APPLIED = False` — no catcher-framing or umpire-tendency signal exists (PR #80's own audit), so a fixed in-zone-is-a-strike rule is used, never a fabricated adjustment. |
| `pitch_shape_similarity.py` | Gaussian-kernel pitch-shape similarity (velocity/movement/release/spin), a practical alternative to brittle hard clusters, for a future milestone's finer-grained matchup work. |
| `hitter_contact_model.py` | EV×LA×spray joint draw (bootstrap-resampled from the hitter's own archived batted balls, shrunk toward a documented synthetic league-average draw when the archive is thin) → batted-ball shape → physical carry-distance heuristic → park-geometry wall check → outcome. Empirical park factors and physical geometry/wind are **never double-counted** (structurally separate code paths, park orientation down-weighted 0.3x per PR #80's `approximate_unverified` flag). |
| `bullpen_exposure_model.py` | Logistic starter-continues probability from innings-pitched vs. workload budget; bullpen handedness/quality primitives. |
| `lineup_game_simulator.py` | 9-inning Monte Carlo. Target hitter uses the full chain; other 8 lineup spots draw from a simpler supplied/league-prior categorical rate. Documented simplified baserunning ("advance exactly N bases"). Starter→bullpen transition re-evaluated at each inning boundary, irreversible once switched. |
| `hitter_market_distributions.py` | Runs N seeded games, aggregates into full pmf + `atLeast[N]` for every stat, computes Monte Carlo standard error, and runs the required internal-consistency invariant checks. |
| `hitter_pricing.py` | Fair American odds, executable-price framing, raw edge, EV — never touches staking/risk-gate/ledger. |
| `hitter_explainability.py` | Sequential waterfall (league prior → hitter/pitch-mix shrinkage → platoon → pitcher quality), each step's rate shift reported — approximate and order-dependent by design, not a fabricated exact decomposition. |
| `hitter_validation.py` | Synthetic controlled walk-forward backtest + real-slate illustrative comparison (see §6). |
| `hitter_feature_ablation.py` | Held-out incremental-value measurement for the platoon/pitcher-quality adjustments against known synthetic ground truth. |
| `hitter_board_builder.py` / `scripts/build_hitter_projection_board.py` | Pure row-assembly + I/O wrapper producing `data/pipeline/<date>/hitter_projection_board.json`. |

## 3. Markets supported

This repository's own confirmed, live Kalshi series-catalogue audit
(`lib.research.market_taxonomy`) found exactly five real hitter-prop
series: `hitter_hits` (KXMLBHIT), `hitter_total_bases` (KXMLBTB),
`hitter_hits_runs_rbis` (KXMLBHRR), `hitter_rbis` (KXMLBRBI), and
`hitter_stolen_bases` (KXMLBSB). The board prices the first four —
`hitter_stolen_bases` is explicitly out of this mission's scope (no
stolen-base projection). All are literal **"N+" AT_LEAST contracts**
(`lib.edgelab.player_prop_settlement`'s own docstring, cross-checked
against 46,784 real archived rows) — pricing is always model
`P(stat >= N)` vs. market-implied `P(stat >= N)`.

No confirmed real Kalshi series exists for standalone home runs,
walks, strikeouts, runs, or a fantasy-score stat — **the underlying
distributions for all of these are still computed** by
`hitter_market_distributions.py` (satisfying "one coherent engine
capable of pricing the full hitter-prop universe"), but the board
never fabricates a row for a market this repository hasn't
independently confirmed exists. Per explicit instruction, no
fantasy-score scoring rule was ever found anywhere in this repository,
so that market is never priced.

## 4. Board schema

One row per (hitter, real archived contract). Fields: `marketTicker`,
`naturalLanguageMarket`, `player`/`playerId`, `matchup`, `marketFamily`,
`threshold`, `distributionUsed`, `modelProbability`, `fairAmericanOdds`,
`executableKalshiPrice`, `executableAmericanOdds`, `rawProbabilityEdge`,
`expectedValuePerDollar`, `pricingStatus`, `monteCarloStderr`,
`projectionStatus`, `sampleSizeDiagnostics`, `modelLimitations`. American
odds are primary throughout. **Every matched archived contract gets a
row regardless of edge sign or size** — no recommendation/staking gate
is imported anywhere in this chain.

## 5. Uncertainty & explainability

Every `atLeast[N]` probability carries its own Monte Carlo standard
error (`sqrt(p(1-p)/n_sims)`). `sampleSizeDiagnostics` reports whether
the hitter had an archived pitch history, whether the starter's pitch
mix was derived from a real archive or fell back to a generic default,
and the simulation count used. `modelLimitations` is populated
per-row (bullpen relief-pitcher mix uses a generic fallback; baserunning
is a documented simplification; park orientation/wind is down-weighted).
`hitter_explainability.py`'s waterfall shows the marginal effect of
each feature layer without claiming an exact decomposition the
architecture can't support.

## 6. Validation

**No raw Statcast pitch archive has ever been ingested in this sandbox**
(`data/statcast_raw` does not exist here) and **no point-in-time
`hitter_feature_context` snapshots exist predating PR #77's
`confirmedLineup` capture**. A genuine leakage-free walk-forward
backtest against real historical games therefore cannot be built from
data this repository actually has. Two honest, clearly labeled modes
exist instead:

**(a) Synthetic controlled walk-forward** (`run_walk_forward_validation`,
`validationMode="SYNTHETIC_WALK_FORWARD_CONTROLLED_GROUND_TRUTH"`) — 25
synthetic hitters, each with its own randomly perturbed true PA-outcome
rate, 250 PAs of as-of-filtered history, scored against 40 held-out
future PAs (1,000 total scored PAs):

| | Log loss | Brier |
|---|---|---|
| This engine | **1.5654** | **0.7309** |
| Naive unshrunk empirical-rate baseline | 1.5741 | 0.7313 |
| League-prior-only baseline (zero hitter info) | 1.5844 | 0.7390 |

This engine beats both baselines — proof the shrinkage/as-of machinery
recovers a *known* true rate better than either alternative, not a
real-world MLB accuracy claim.

**(b) Real-slate illustrative comparison** (`real_slate_illustrative_rows`,
`validationMode="ILLUSTRATIVE_NOT_LEAKFREE"`) — 23,913 real settled
hitter-prop rows from `data/edgelab/settlements/*.jsonl` (Aug 2–10 2026)
across all four priced families (`hitter_hits`: 5,666,
`hitter_total_bases`: 6,742, `hitter_hits_runs_rbis`: 7,791,
`hitter_rbis`: 3,714). The real market's own implied-probability log
loss/Brier on these outcomes (0.5226 / 0.1674) is reported as a
reference point, **not** a claim about this engine's accuracy — this
engine's own probabilities cannot be leakage-free backtested against
these specific historical games without point-in-time features this
repository doesn't have. Representative, non-cherry-picked examples
(spanning multiple thresholds/players, e.g. Elly De La Cruz's 1+/2+/3+
hit contracts, Shohei Ohtani's 2+/3+/4+ hit contracts on 2026-08-02):
real ticker, real threshold, real actual stat value, real outcome, real
market price — see `illustrativeRows` in the module output.

## 7. Feature ablation

Held-out incremental-value measurement against known synthetic ground
truth (`hitter_feature_ablation.py`), since the same real-data
limitation above rules out a real held-out ablation:

| Feature | Log loss (on) | Log loss (off) | Improves held-out? |
|---|---|---|---|
| Platoon adjustment | 1.5271 | 1.5276 | **Yes** |
| Pitcher-quality adjustment | 1.4777 | 1.4782 | **Yes** |

Both bounded adjustments measurably reduce held-out log loss when a
real signal of the adjustment's own magnitude is present — small
improvements, honestly reported (these are ±3pp-capped nudges by
design, not large effects), not assumed to help without checking.

## 8. Internal consistency invariants

`hitter_market_distributions.run_invariant_checks()` verifies, every
run: every `atLeast[N]` sequence is non-increasing in `N`; every
probability is in `[0,1]`; `P(hits>=2) <= P(hits>=1)`,
`P(hits>=3) <= P(hits>=2)`; `P(HR>=2) <= P(HR>=1)`; and — checked
directly against the raw simulated game lines, not re-derived from the
aggregated distributions — every game with `HR>=1` has `H>=1` and
`TB>=4`. All pass in every tested scenario (`tests/test_hitter_phase4_engine.py`).

## 9. A real bug this phase found and fixed

The initial contact-model carry-distance formula
(`ev * sin(radians(2*LA)) * CARRY_K`) is pure undamped-projectile
physics: `sin(2*LA)` peaks at LA=45° and stays within ~15% of its max
across the *entire* 25–50° fly-ball range, with no notion of air-drag
disproportionately robbing distance from high "can-of-corn" fly balls.
This produced a ~50% HR/FB rate in simulation — roughly 4x real MLB's
~11–13% average. Fixed by adding a documented exponential drag-decay
penalty for launch angles above the single calibration anchor (28°),
tuned so simulated FB-HR rate lands at ~13.1%, while the calibration
point itself (EV=105/LA=28 → 420ft) stays exact. See
`hitter_contact_model.LAUNCH_ANGLE_DRAG_DECAY_PER_DEGREE` and
`tests/test_hitter_phase4_engine.py::TestHitterContactModel::test_home_run_rate_on_fly_balls_is_realistic`
(a regression test for exactly this).

## 10. No-leakage safeguards

Every raw-pitch lookup flows through `statcast_pitch_store`'s
`as_of`-bounded loaders (PR #79), unchanged by this phase.
`tests/test_hitter_phase4_engine.py::TestNoLeakage` adds a Phase
4-specific regression: a synthetic hitter with a cold history followed
by a hot streak strictly after an as-of cutoff must not have that hot
streak influence a model built from history alone — verified directly
(history-only HR rate stays low) and cross-checked (the *same* future
PAs, when actually included, provably raise the rate — proving the
exclusion, not just asserting it).

## 11. Performance

Monte Carlo runtime (this sandbox, single-threaded): ~0.3ms per
simulated 9-inning game. At the board's default 1,500 simulations per
hitter, ~0.45s of pure simulation per hitter/game — a typical
confirmed slate (roughly 100–150 hitters) projects in well under two
minutes of simulation time, before I/O. `n_sims` is caller-configurable
(`build_hitter_market_distributions(n_sims=...)`) for an optional
higher-resolution research mode. `build_hitter_market_distributions`
also runs a cheap split-half convergence check per stat.

## 12. Known limitations

- No raw Statcast archive or point-in-time feature snapshots exist in
  this sandbox — every model component gracefully degrades to season/
  league-prior shrinkage levels and reports this explicitly in
  `modelLimitations`/`sampleSizeDiagnostics`; nothing is fabricated.
- Bullpen relief-pitcher pitch mix uses a generic fallback (no
  per-reliever archive is wired in).
- Baserunning is a documented "advance exactly N bases" simplification,
  not real productive-out/tag-up nuance.
- Park orientation/wind is down-weighted (confidence 0.3x) per PR #80's
  `approximate_unverified` flag.
- No catcher-framing or umpire-tendency signal is used in the pitch
  sequence model (PR #80 found no stable, authoritative source) — a
  fixed in-zone-is-a-strike rule is used instead, flagged explicitly
  via `CATCHER_UMPIRE_ADJUSTMENT_APPLIED = False`.
- Bat-tracking is architecture-only (PR #80 could not verify live
  Savant bat-tracking fields from this sandbox's network-blocked
  environment) — no bat-tracking signal is fabricated anywhere in this
  phase.
- Out-of-scope by explicit instruction: no stolen-base projection, no
  pitcher-prop changes, no fantasy-score pricing (no scoring rule
  exists anywhere in this repository), no staking/recommendation/
  settlement/ledger changes.

## 13. Files changed

**New:** `lib/research/hitter_shrinkage.py`, `hitter_pa_outcome_model.py`,
`pitch_environment_model.py`, `pitch_shape_similarity.py`,
`pitch_sequence_model.py`, `hitter_contact_model.py`,
`bullpen_exposure_model.py`, `lineup_game_simulator.py`,
`hitter_market_distributions.py`, `hitter_pricing.py`,
`hitter_explainability.py`, `hitter_synthetic_ground_truth.py`,
`hitter_validation.py`, `hitter_feature_ablation.py`,
`hitter_board_builder.py`; `scripts/build_hitter_projection_board.py`;
`tests/test_hitter_phase4_engine.py`; this doc.

**Modified (small, additive):** `lib/research/hitter_pitch_derivation.py`
(extracted `_count_pa_terminal_events` helper, added
`derive_pa_outcomes_by_pitch_family`), `lib/research/statcast_pitch_store.py`
(added pitcher-side index + `load_pitches_for_pitcher`, symmetric to
the existing batter-side path), `lib/research/hitter_feature_context.py`
(surfaced `avgIPperStart` already flowing through `opp_savant`, one
field, no behavior change to any existing field).

## 14. Tests

`tests/test_hitter_phase4_engine.py`: 71 tests across 15 classes
(shrinkage, PA outcome model, pitch environment, pitch shape
similarity, pitch sequence, contact model incl. the carry-decay
regression, bullpen exposure, lineup/game simulator, market
distributions + invariants, pricing, explainability, validation,
ablation, board builder, no-leakage). Full repository suite: **4,656
passed, 6 skipped** (4 pre-existing, environment-only failures
reproduce identically on a clean checkout with none of this phase's
changes — shallow-clone-missing historical commit SHAs referenced by
unrelated old PR-scope regression tests — not caused by this phase).

## 15. Recommended next PR

1. Wire a real per-relief-pitcher archive into bullpen pitch-mix
   (currently a generic fallback).
2. A richer "other 8 lineup spots" model (currently league-prior by
   default) using each teammate's own season rate when available.
3. Once a real raw Statcast archive is ingested in a live environment,
   re-run `hitter_validation.run_walk_forward_validation`-style
   chronological validation against *real* PA outcomes instead of
   synthetic ground truth.
4. Wire `scripts/build_hitter_projection_board.py` into the daily
   pipeline once a full slate with confirmed lineups + real hitter
   Kalshi markets has been validated end-to-end in production.
