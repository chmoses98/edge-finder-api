# PROJECTION_UPGRADE_ROADMAP.md

Model Performance Phase 1 (Market Audit) — Parts 6, 7, and 9.

**CORRECTION (Model Performance Phase 1 amendment):** this document
previously assumed F3/F7 have no real Kalshi market to bind a
projection to, and scoped the "next phase" (originally "Phase 2A")
as F5-only for that reason. That assumption was **false** — a user
with direct Kalshi account access confirmed placing real wagers on
both MLB F3 and F7 markets. See
`docs/research/KALSHI_MARKET_TAXONOMY.md`'s "F3/F7 correction" section
and `docs/research/PROJECTION_AUDIT.md`'s root-cause section for the
full corrected findings. **Wave 1's scope below is unchanged** (the
projection math already supported F3/F5/F7 generically); what changes
is the recommended next phase's scope, corrected at the bottom of this
document.

## Part 6: Dynamic market-discovery architecture (design + partial implementation)

Implemented this phase (research-only, not wired into production):

```
Kalshi discovery (already-saved snapshot files this phase)
  -> raw preservation           (data/research/kalshi_mlb_market_inventory.json:
                                  every raw ticker/title/price kept verbatim)
  -> normalization/classification (lib/research/market_taxonomy.py)
  -> settlement-rule mapping    (SETTLEMENT_VERIFIED_FAMILIES in
                                  lib/research/market_handler_registry.py --
                                  a family/scope pair must be explicitly
                                  added here, separate from having a
                                  handler function, before it can ever
                                  reach STATUS_EVALUATED)
  -> projection-handler lookup  (MARKET_HANDLERS registry)
  -> evaluation                 (lib/research/three_way_projection.py
                                  for game_result/inning_result;
                                  placeholder for every other family)
  -> research-ledger output     (one status-tagged row per discovered
                                  market, never silently dropped --
                                  proven in tests/research/
                                  test_market_handler_registry.py)
```

`lib/research/market_handler_registry.py`'s `MARKET_HANDLERS` dict
matches the mission's suggested shape, using the family names actually
confirmed by this phase's taxonomy work. Every discovered market gets
exactly one of: `Evaluated`, `Unsupported Market`, `Missing Data`,
`Classification Failed`, `Settlement Rule Unresolved`, or `Evaluation
Failed` — proven via `evaluate_market_batch_research()`'s
`len(output) == len(input)` guarantee test.

**This registry is NOT wired into `scripts/build_market_ledger.py`.**
Doing so would be exactly the "replace `evaluate_game()` wholesale"
change every phase (including this one) has explicitly avoided. It
exists as proven, tested scaffolding for a future phase to adopt
incrementally, the same way `lib/pipeline_artifacts.py` was adopted
incrementally by Phases 3-10.

## Part 7: Projection upgrade candidates — evaluated and ranked

| # | Candidate | Expected improvement | Data required | Currently available? | Difficulty | Leakage risk | Sample-size need | Affected markets | Safe for research-only now? | Priority |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Corrected-horizon Poisson (retain tie, no renormalization) | Materially better F5 tie pricing; honest full-game reasoning | None beyond current inputs | **Yes** | Low | None | None (pure math) | game_result, inning_result (F3/F5/F7) | **Yes — implemented this phase** | **P0** |
| 2 | Exact tail accounting / truncation reporting | Removes an unmeasured (if small) blind spot | None | Yes | Low | None | None | All Poisson-based markets | **Yes — implemented this phase** (`truncationMass`) | P0 |
| 3 | Negative-binomial team scoring | Corrects real-world overdispersion vs. pure Poisson | Historical per-team run-count variance | Partially (raw game logs would need assembling from historical slates/bets) | Medium | Low | ~1 season minimum for stable variance estimate | All run-based markets | No — needs a fitting pass against real historical data this phase did not have time to assemble | P1 |
| 4 | Bivariate Poisson (score correlation) | Better joint-outcome pricing (e.g. weather-driven co-movement) | Historical paired away/home score correlation | No | Medium-high | Low | Multiple seasons for a stable correlation estimate | game_result, inning_result, totals | No | P2 |
| 5 | Empirical joint-score distribution | No distributional assumption at all | A large historical joint-score matrix | No (current historical footprint is ~2 months of slates) | High | Low if strictly held-out | Large (thousands of games ideally) | All | No | P3 |
| 6 | Monte Carlo simulation | Flexible, can add arbitrary correlation/variance structure | A validated underlying generative model first | No (would sit on top of #3/#4, not available yet) | High | Low | Depends on generative model | All | No | P3 |
| 7 | Market-informed priors (vig-free sharp-book) | Faster convergence to accurate probabilities | A leakage-safe sharp-book price feed | **No such feed is currently ingested** | Medium | **High if not carefully isolated from the eligibility path** | N/A | All | No — leakage risk must be resolved architecturally first | P2 |
| 8 | Confirmed-lineup player-level recalculation | More accurate offense projection post-lineup-confirmation | Player-level projections (not currently modeled at all — team-level only) | No | High | Low | Player-season history | Full game, F5 | No | P2 |
| 9 | Batting-order PA weighting | Marginal accuracy gain | Batting-order + per-slot PA distributions | Partially (lineup order is fetched; PA-weighting logic not found) | Medium | Low | Moderate | Full game, F5 | No | P3 |
| 10 | Platoon-specific offense | Marginal accuracy gain, especially vs. weak-side starters | Team platoon splits (partially present) | Partially | Medium | Low | Moderate | Full game, F5 | No | P2 |
| 11 | Starter pitch-count/workload distributions | Better F5/F7 horizon scaling | Historical pitch-count-vs-innings distributions | Partially (avgIPperStart exists; distributional form not modeled) | Medium | Low | Moderate | F5 (F7 has no market) | No | P2 |
| 12 | Times-through-the-order modeling | Already partially present for F5 | TTO splits | Partially present | Low-medium | Low | Low | F5 | Partially safe (extend existing `home_tto_adj`) | P1 |
| 13 | Opener/bulk-pitcher modeling | Already present (Rule 24 gate) | N/A | Yes | N/A | N/A | N/A | F5 | Already done | Done |
| 14 | Bullpen availability/fatigue | Better late-innings accuracy | `bullpen.fatigued`/`last3DaysIP` (captured, use unconfirmed) | Partially | Medium | Low | Moderate | Full game | No | P2 |
| 15 | Umpire effects | Marginal accuracy gain | No umpire data source found in this repo | No | High (new data source needed) | Low | Large | Totals, first-inning | No | P3 |
| 16 | Park/weather interaction | Marginal accuracy gain (park already modeled; weather-interaction not confirmed) | Weather data (fetched; interaction unconfirmed) | Partially | Medium | Low | Moderate | Totals | No | P2 |
| 17 | Defense/baserunning | Marginal accuracy gain | No defensive/baserunning metric source found | No | High | Low | Large | Full game, totals | No | P3 |
| 18 | Travel/rest | Marginal accuracy gain | No travel/rest data source found | No | Medium | Low | Moderate | Full game | No | P3 |
| 19 | Rolling vs. season-long blending | Already partially present (`last7RpG`/`last15RpG`/season blend, confirmed in `enrich_data.py`) | N/A | Yes | N/A | N/A | N/A | Full game | Already done | Done |
| 20 | Market-family-specific calibration | Better calibration per family instead of one global factor | Per-family historical outcome/edge history | Partially (bets.json has family info; not yet segmented) | Medium | Low | Moderate-large per family | All | Partially safe (analysis only, no production change) | P1 |
| 21 | Probability shrinkage for sparse markets | Avoids overconfident probabilities on thin-sample outcomes | Per-outcome historical sample counts | Partially | Low-medium | Low | N/A (shrinkage is itself the mitigation) | F5 Tie, alternates, props | Safe for research-only design | P1 |
| 22 | Player-prop-specific distribution models | Enables pitcher/hitter markets | Player-level historical distributions (not modeled at all currently) | No | High | Medium (small-sample player props are leakage-prone) | Large per player | Pitcher/hitter families | No | P3 |
| 23 | Correlation handling across related outcomes | Avoids overexposure across correlated bets | Historical co-occurrence data | Partially (bets.json exists; not analyzed for this) | Medium | Low | Moderate | All | Partially safe (analysis only) | P2 |

## Part 9: Implementation waves

### WAVE 1 (this phase's scope; no new real-money activation)
- Dynamic market discovery + raw preservation — **done**
  (`data/research/kalshi_mlb_market_inventory.json`).
- Normalized market taxonomy — **done**
  (`lib/research/market_taxonomy.py`).
- Settlement-rule mapping (documented, not Kalshi-rules-text-verified
  — see the taxonomy doc's "documented gaps") — **partially done**.
- Unsupported-market ledger visibility + no-silent-drop — **done**
  (`lib/research/market_handler_registry.py`).
- Three-way full-game/F3/F5/F7 result projections — **done**
  (`lib/research/three_way_projection.py`).
- Alternate totals/run lines using current team distributions — **not
  done this phase** (handler placeholders only; genuine implementation
  deferred to Wave 2 since it requires reading the real strike-ladder
  structure per market, which this phase's time budget did not cover
  beyond classification).
- Research-only projection comparison — **done**
  (`data/research/projection_outcome_comparison.json`, 4 synthetic
  fixtures x 4 horizons, comparing production's current renormalized
  method against the candidate tie-retaining method).
- **No new real-money activation performed or proposed this phase.**

Files likely changed in a future phase that ACTIVATES Wave 1's
findings: `scripts/build_market_ledger.py` (to stop discarding
`f5_tie_am`), `scripts/merge_odds.py` (no change needed — already
captures the data), `docs/CANONICAL_SCHEMAS.md` (a new `F5_Tie` ledger
row schema). Historical proof requirement: at minimum one full season
of F5 TIE closing-price-vs-actual-outcome data before even PAPER
activation, per Part 8's backtest design.

### RECOMMENDED NEXT PHASE (revised scope — corrected)

**This section previously recommended an F5-only next phase** (named
"Phase 2A: F5 three-way outcome integration"), reasoning that F3/F7
had no real Kalshi market to ever integrate. **That reasoning was
false** and has been retracted — see the "F3/F7 correction" section in
`docs/research/KALSHI_MARKET_TAXONOMY.md`. The corrected recommendation:

> **F3/F5/F7 three-way result ingestion, canonical probability
> integration, dynamic market retention, shadow evaluation and
> paper-only collection.**

This broadens the original F5-only scope to cover all three
user/repository-confirmed inning-result horizons uniformly, since the
underlying architecture (dynamic market-discovery registry, canonical
three-way projection function, shadow/paper-only evaluation ledger)
was never F5-specific to begin with — `lib/research/three_way_projection.py`
and `lib/research/market_handler_registry.py` already treat F3/F5/F7
identically today. The concrete differences a future phase must still
resolve per horizon:

- **F5**: real market data already flows into `data/slate.json`
  (`odds.kalshi.f5ml`, including the currently-unused
  `tie_american`) — no new ingestion work needed, only the
  wiring/activation-gate work described above.
- **F3/F7**: real ticker prefixes are NOT yet confirmed (this
  repository's `KXMLBF3`/`KXMLBF7` are an unverified guess) — a future
  phase must independently confirm the real series ticker(s) (live API
  access or user-provided ticker strings) BEFORE adding them to any
  fetcher's allowlist, then build the same fetch → registry → merge →
  ledger path F5 already has. Outcome structure (three-way vs. other)
  and settlement rules for F3/F7 remain `UNVERIFIED` and must be
  confirmed before any paper or real-money activation — do not assume
  they match F5 merely by analogy.
- This phase does NOT implement that ingestion work — it only corrects
  the prior false conclusion and revises the roadmap so the next phase
  is scoped correctly from the start.

### WAVE 2
- Improved horizon-specific run distributions (negative binomial
  and/or bivariate Poisson, once fitted against real historical
  variance/correlation data).
- Corrected F3/F5/F7 means (F3/F7 are CONFIRMED to have real Kalshi
  markets as of this correction -- see
  `docs/research/KALSHI_MARKET_TAXONOMY.md`'s "F3/F7 correction"
  section -- they are simply not yet ingested by this repository;
  refining F3/F5/F7 means is valuable both for F5's existing
  sophistication AND for the F3/F7 ingestion work now recommended
  below, not a "possible future launch").
- F3/F5/F7 totals (F5 totals market — `KXMLBF5TOTAL` — already exists
  and is unsupported by production today; genuine near-term value).
- Team totals and run thresholds beyond the currently-consumed single
  strike.
- Winning-margin markets beyond the currently-consumed full-game
  `RL_Away`/`RL_Home` pair (`KXMLBSPREAD`'s full per-team margin
  ladder, and `KXMLBF5SPREAD` entirely, are both currently
  unsupported).
- Market-family calibration (segmented, not global).
- Files likely changed: `scripts/build_market_ledger.py`,
  `lib/research/*` (promoted out of research/ once validated),
  `docs/CANONICAL_SCHEMAS.md`. Requires: historical data assembly
  (see Part 8), a paper-trading period per new market family,
  an explicit activation gate (backtested Brier/log-loss/calibration
  meeting a documented bar), and a rollback plan (feature-flag the new
  family off, fall back to `Unsupported Market` status).

### WAVE 3
- Pitcher workload model, strikeouts, outs, hits allowed, earned runs,
  walks. Requires: a pitcher-prop Kalshi series to even exist and be
  fetched (none confirmed discovered this phase — see the taxonomy
  doc's inventory gap) and player-identity/starter-change safeguards
  (a real risk: a probable-starter scratch after market close would
  invalidate any pre-computed player projection).

### WAVE 4
- Hitter hits/total bases/home runs/runs/RBIs/walks/strikeouts, less-
  liquid game-event contracts, correlated/exact outcomes. Same
  player-prop data-availability gate as Wave 3, plus materially higher
  leakage risk from small per-player samples.

Every wave beyond Wave 1 requires, before any real-money activation:
parsed + settlement-verified + projection-supported + historically
backtested + calibrated + paper-traded + explicitly enabled, in that
order, per the mission's Part 6 requirement — none of that gating is
weakened or bypassed by this phase's scaffolding.
