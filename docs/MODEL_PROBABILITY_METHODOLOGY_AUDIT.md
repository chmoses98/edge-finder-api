# Model Probability Methodology Audit — MLB/Kalshi

_Generated 2026-08-21, from current main (post-PR #100). Audit-only: no
projection formula, calibration coefficient, recommendation threshold, fee
logic, bankroll rule, production recommendation eligibility, or hitter-prop
promotion status was changed to produce this document._

## Purpose and scope

Before expanding model-probability coverage to new market families, this
audit determines whether **each currently-modeled family is using the best
appropriate data and methodology for its specific forecasting problem** —
not merely whether a number exists. Every family is read directly from
source code (not inferred from documentation), cross-checked against real
persisted data where relevant, and classified using the rule: **a
probability existing is not sufficient for TRUSTED status** — real
production wiring, sound inputs, and real calibration evidence all matter
independently.

## Two engines exist; this audit treats `scripts/build_market_ledger.py` as authoritative

Two independent probability-computation code paths exist for the same
markets:

- **`scripts/build_market_ledger.py`** — the real-money execution gate
  (per repo-wide convention: "do not change existing model probabilities
  ... unless strictly required" applies to this file absolutely). This is
  the path whose output reaches `data/edgelab/model_evaluations/*.jsonl`
  and therefore the only path this audit treats as "production."
- **`api/slate.js`** — an earlier/parallel Poisson engine serving a
  different surface. It is **materially weaker and, in places,
  self-contradictory** for several families it shares with the gating
  engine (see Finding 6 below) — flagged here as a real methodology risk,
  not audited family-by-family with the same depth since it does not gate
  real money.
- **`lib/kalshi_probability_adapters.py`** + `scripts/discover_kalshi_mlb_markets.py`
  — a third, newer, research/discovery-only path that reuses the gating
  engine's exact primitives (`poisson_pmf`, `p_team_wins`, `p_over_total`,
  never reimplemented) to price every alternate-line ladder plus F3/F7 and
  pitcher K/outs. Real, sound models often live here first — but this path
  writes only to `data/kalshi/discovery/<date>.json`, never to
  `data/edgelab/model_evaluations/`, `bets.json`, or any recommendation
  gate.

## Classification vocabulary

- **TRUSTED_MODEL_PROBABILITY** — production-wired (reaches `model_evaluations`
  with real confidence tiers), sound/appropriate inputs, and real
  calibration evidence that does not contradict trustworthiness.
- **RESEARCH_ONLY_PROBABILITY** — a real, reasoned probability is computed
  (possibly even persisted prospectively), but is either (a) architecturally
  isolated from the production evaluation/recommendation pipeline, or (b)
  production-wired but the system's own evidence (a hard suspension rule,
  a poor calibration measurement) contradicts trusting it.
- **PROXY_PROBABILITY** — a generic/unrelated number stands in for what
  should be a market-specific distribution (e.g., a full-game rate scaled
  down with no dedicated evidence).
- **UNSUPPORTED_MODEL_FAMILY** — no probability distribution of any kind
  exists in this codebase for this exact family, in any code path.

## Classification table

| Market family | Current method | Current inputs | Missing/ignored inputs | Classification | Validation status | Prospective persistence | Recommended next action |
|---|---|---|---|---|---|---|---|
| **Full-game ML** | `p_team_wins()` independent-Poisson joint, push-renormalized, `build_market_ledger.py` | Own-team offense baseline (Bayesian-shrunk), opposing starter xFIP (clamped), opposing bullpen xFIP (workload-adjusted), park factor, confirmed-lineup platoon adj | Weather/wind (confirmed absent repo-wide for this family); umpire zone | TRUSTED_MODEL_PROBABILITY | Market-price calib. ≈0 (PR #98); model-side n=25 DESCRIPTIVE_ONLY, Brier slightly worse than market — thin | Yes, real `modelFairProbability`, real confidence tiers | (A) broaden persistence coverage only |
| **F5 winner (Away/Home)** | `three_way_result_probs()`, genuine 3-way joint (tie retained, no renormalization) | Starter-only IP (capped 5.0, opener→0), starter xFIP, TTO adj, park/platoon scaled 5/9 — **no bullpen** | Dedicated early-game-specific evidence beyond platoon (first-inning Statcast deliberately excluded, correct scope) | TRUSTED_MODEL_PROBABILITY | Only family where model Brier beats market Brier (0.232 vs 0.245, PR #98) | Yes | (A) broaden persistence, this is the strongest family |
| **F5 Tie leg** | Same 3-way call as above | Same | Same | RESEARCH_ONLY_PROBABILITY | Same underlying model as F5 winner; deliberately not recommendable (product decision, not a methodology gap) | Priced, persisted, marked not-recommendable | (A) no methodology work needed; a market-selection decision, not audited further here |
| **F3 / F7 winner** | Same `adapt_f5_result()`/three-way call, via `lib/kalshi_period_projections.py`'s generalized starter-only formula | Same structure as F5 (starter-only, TTO, no bullpen) | **No platoon nudge** (unlike full-game/F5); **no clamp** (deliberately, "uncalibrated horizon"); F7 explicitly documented as a "weaker assumption" (starters pulled before 7 more often than before 5) | RESEARCH_ONLY_PROBABILITY | **None** — `productionEnabled: False`, zero historical sample, "NOT_YET_ACTIVATED_NO_HISTORICAL_PAPER_SAMPLE" | Reachable via adapter only; never reaches `model_evaluations` | (B) add platoon nudge + a period-appropriate clamp, then accumulate a paper sample before any activation decision |
| **Game totals (full-game)** | `p_over_total(total_proj, line)`, `total_proj = away_proj+home_proj` | Same inputs as ML (sum of both teams') | Weather; no dedicated total-specific distribution (reuses the ML lambdas' sum) | RESEARCH_ONLY_PROBABILITY | Rule 71: real WR 41%, CLV −1.43% — system's own evidence says do not trust | Model **computes** a probability every run, but it is **silently discarded to null** at the `ModelEvaluation` persistence layer (`rejected_row()` never populates `kalshiVF`, so `classify_evaluation_status()` returns `MISSING_MARKET_PRICE` — confirmed 0/301 real records) | (C) root-cause whether Rule 71's poor WR reflects a genuine Poisson-sum inadequacy for totals specifically vs. execution/threshold issues, before any reactivation |
| **Alternate totals (full-game)** | `adapt_total()`, same `p_over_total`/`total_proj`, evaluated per line | Same as game totals | Same | RESEARCH_ONLY_PROBABILITY | None — never in production ledger | Reachable via discovery adapter only; never reaches `model_evaluations` | (A) monotonicity already structurally guaranteed; only needs wiring decision, not new modeling |
| **F5/inning totals** | Same `p_over_total`, λ = `f5AwayProj+f5HomeProj` | Same F5 inputs | Same F5 gaps | RESEARCH_ONLY_PROBABILITY | None | Adapter-only | (A) same as alternate totals — wiring, not modeling, is the gap. **PR #100 flagged this family's market pricing as the single most severely mispriced anomaly found** — good candidate to prioritize once wired, per this audit's "use PR #100 to prioritize" instruction |
| **Team totals (Over)** | `p_over_total(team_proj, line)`, `team_proj` = the SAME `away_proj`/`home_proj` used for ML | Opposing starter, opposing bullpen (workload-adjusted), park, platoon — all inherited from the shared ML lambda | Weather; no TT-specific distribution (reuses ML's team lambda verbatim) | **RESEARCH_ONLY_PROBABILITY** (downgraded from its production wiring — see rationale) | **Worst model-side calibration of any covered family**: calibration error +0.175, model Brier 0.282 vs. market's 0.252, n=236 CALIBRATED (PR #98) — real, statistically meaningful evidence of poor fit despite comprehensive inputs | Yes, real, production-wired, real confidence tiers (not Rule-capped) | (C) — inputs are comprehensive; the Poisson-sum structure or its clamps/fallbacks likely need revision, not more data. Highest-priority root-cause target in this audit given it is production-live today |
| **Team totals (Under) + alt lines** | Same `p_over_total`, `1-p_over` for Under | Same | Same | RESEARCH_ONLY_PROBABILITY | Same as Over side | Under = complement of the same contract, not separately modeled; alt lines adapter-only | (A) wiring only |
| **NRFI/YRFI** | `build_first_inning_context()`: dedicated Statcast first-inning xERA blended (appearance-weighted, ±35% capped) with naive proj/9 fallback, plus a ±15%-capped platoon nudge | Dedicated first-inning xERA (now **actively used in ~94% of scored rows**, confirmed by direct query: 178/190 non-null = `FIRST_INNING_NATIVE`), naive full-game proj/9 as fallback, top-3-lineup platoon handedness, confirmed-lineup gate | None material — this is the most first-inning-specific family in the repo | TRUSTED_MODEL_PROBABILITY | n=104/86 games CALIBRATED (PR #98): calibration error −0.119 (moderate overconfidence), model Brier 0.261 vs market 0.246 — real, if imperfect | Yes, production-wired, real confidence tiers, hard-gated (Rule 34 total≥8.0 block, Rule 40 evidence requirement, Rule 52 lineup gate) | (A)/(B) — broaden persistence; consider retuning the blend weights given the -0.119 overconfidence |
| **Run lines / winning margin (full-game)** | `build_market_ledger.py`: **no probability computed at all** — Rule 81 rejects before `modelProb` is ever calculated. A real Poisson cover-probability exists in the separate adapter (`p_wins_by_over`) and in `api/slate.js` | Same joint-Poisson primitives as ML, when computed at all | N/A — not computed in the gating engine | RESEARCH_ONLY_PROBABILITY | Rule 81: real WR 36%, CLV −4.09% — direct evidence of poor historical performance | Paper-tracked (`data/research/paper_spread_ledger.jsonl`); 0/602 `model_evaluations` records carry a `modelFairProbability` | (C) same root-cause question as game totals before any reactivation is even considered |
| **Run lines / winning margin (F3/F5)** | `adapt_winning_margin()`, same joint Poisson, evaluated per exact margin | Same | Same F5/F3 gaps as winner markets | RESEARCH_ONLY_PROBABILITY | None — no historical sample exists ("paper-only from day one") | Adapter-only | (B) accumulate a paper sample before any activation discussion |
| **Pitcher strikeouts** | `lib.research.pitcher_workload_projection.project_pitcher_workload()` — single shared survival curve, binomial tail on expected batters faced | `avgIPperStart`, `kPct` (required); `bbPct`, opener flag, TTO split/risk, recent-workload-restriction (4-tier evidence quality), opponent wRC+ — **all real inputs, confirmed populated in live `data/slate.json`** | `opponentTeamKPct` is coded for but **always None in production today** (inert); no park/umpire strike-zone input | UNSUPPORTED_MODEL_FAMILY (current production reality) | None — 0/2,692 `model_evaluations` records have `modelFairProbability` | Model is reachable via `adapt_contract()`/discovery script (upstream identity resolution now works), but **`classify_contract()`'s pitcher-identity resolution is not wired into the ledger that feeds `model_evaluations`** — a genuinely sound, threshold-monotonic, workload-aware model sits one integration step away from being usable | **(A)-leaning-(B)** — this is NOT a "build a new model" gap. The model exists, is sound (see Market-Specific Expectations below), and real inputs are populated. It needs wiring into `build_model_evaluations_from_pipeline()`'s market list, not new statistical work |
| **Pitcher outs** | Same `project_pitcher_workload()` call, per-out survival product | Same as strikeouts, plus opener hard-cap (8 outs) and TTO-pull-risk penalty | Same `opponentTeamKPct` inertness; no literal pitch-count time series, no real injury/IL data (only a caller-supplied boolean proxy) | UNSUPPORTED_MODEL_FAMILY (current production reality) | None — 0/363 records | Same as strikeouts — sound model, same wiring gap | (A)-leaning-(B), same as strikeouts; both share one survival curve so wiring one effectively wires both |
| **Hitter hits** | `hitter_market_distributions.build_hitter_market_distributions()` — Monte Carlo (1,500 sims) lineup-game simulator, one coherent simulated process, `H` read off each simulated line | Real per-starter pitch mix conditioned on batter handedness, real opposing-starter workload/IP-based innings routing, physical park geometry + wind (down-weighted for unverified orientation), confirmed batting-order slot (drives PA count) | **Named, capped "platoon adjustment" and "pitcher quality adjustment" functions are dead code** — never called by the live pricing path, only by an unlinked diagnostic/validation harness; bat-tracking data fetched but unused | RESEARCH_ONLY_PROBABILITY | None in `model_evaluations` (architecturally excluded, see below) | **Real, non-null, checkpoint-tagged prospective persistence exists** (`data/edgelab/hitter_projection_snapshots/<date>.jsonl`) but is a fully separate schema/pipeline from `ModelEvaluation`, never joined | (B) — reconnect the named platoon/pitcher-quality adjustments to the live path (or remove the dead code + misleading docs); the model foundation is sound |
| **Hitter total bases** | Same simulation, `TB` read off the same lines as hits | Same | Same as hits | RESEARCH_ONLY_PROBABILITY | None in `model_evaluations` | Same real snapshot persistence, same architectural isolation | (B) same as hits |
| **Hitter H+R+RBI (HRR)** | Same simulation, `H+R+RBI` read off the same lines | Same, **plus** genuine within-game correlation across H/R/RBI (all from one simulated line, not summed marginals) | **Every other lineup slot is modeled at generic league-average rates regardless of the real lineup** — materially understates RBI/HRR variance driven by real teammate quality; bullpen innings modeled as facing a generic single-pitch-type arm | RESEARCH_ONLY_PROBABILITY | None in `model_evaluations` | Same real snapshot persistence | (B) — real-lineup-aware teammate rates and real bullpen pitch mix are the two highest-value additions here specifically |
| **Hitter RBI** | Same simulation, `RBI` read off | Same | Same teammates-at-league-average gap as HRR — RBI is the family most directly exposed to it | RESEARCH_ONLY_PROBABILITY | None in `model_evaluations` | Same real snapshot persistence | (B) same as HRR |
| **Hitter stolen bases** | **None** — excluded from the board's own market-discovery filter; no steal-attempt/caught-stealing state anywhere in the simulator | N/A | Catcher pop time, pitcher hold times, sprint speed are collected elsewhere but never assembled into a stolen-base model | UNSUPPORTED_MODEL_FAMILY | None | None | (D) — genuinely new modeling work, not a wiring gap |

## Market-specific expectations — explicit checks

- **"NRFI/YRFI uses first-inning-specific information rather than only
  generic full-game scoring"** — **Confirmed true today.** This family was
  historically a pure `proj/9` proxy (the module's own docstring documents
  this as the prior gap), but the fix is live and active: 178 of 190
  non-null scored rows (93.7%) currently carry `FIRST_INNING_NATIVE`
  dedicated-evidence status, confirmed by direct query against
  `model_evaluations`. This is the strongest example in the codebase of a
  family that was a proxy and has since been properly fixed.
- **"Team totals incorporate full lineup, opposing starter, expected
  starter workload, bullpen quality/workload, park/weather, and full-game
  scoring context"** — **Partially true.** Opposing starter (workload-capped
  IP), bullpen (workload-adjusted), park, and confirmed-lineup platoon are
  all present — inherited directly from the shared ML lambda. **Weather is
  absent** (confirmed: no weather/wind field is read anywhere in
  `build_market_ledger.py`, `api/slate.js`'s team-total path, or any
  `lib/research` module feeding these families — the only weather/wind
  code in the repo feeds the separate hitter engine). More importantly,
  despite comprehensive non-weather inputs, **real calibration evidence
  shows this family is currently poorly fit** (worse Brier than the
  market itself) — comprehensive inputs did not translate into good
  calibration, which is exactly why this audit does not classify it
  TRUSTED.
- **"F3/F5/F7 appropriately weight starter and early-offense context and
  do not unnecessarily inherit full-game bullpen assumptions"** —
  **Confirmed true.** All three horizons use a starter-only innings figure
  (capped at the period length, zeroed for a confirmed opener) with **no
  bullpen term at all** — verified directly in both `compute_projections()`
  (F5, production) and `lib/kalshi_period_projections.py` (F3/F7,
  research-only). This is genuinely period-appropriate, not a scaled-down
  full-game number.
- **"Pitcher Ks use threshold-specific strikeout distributions plus
  expected batters faced/workload and opponent lineup K context"** —
  **Threshold-specific and workload-aware: yes**, confirmed (binomial tail
  over a workload-derived expected-batters-faced figure, monotonic by
  construction). **Opponent lineup K context: coded but currently
  inert** — `opponentTeamKPct` is always `None` in live data today, so this
  signal exists in the formula but never actually influences a real
  computed probability.
- **"Pitcher outs use workload, pitch-count, manager-hook, role/injury/
  restriction context rather than generic pitcher quality"** — **Mostly
  true.** Opener/role status (hard cap), a times-through-order pull-risk
  proxy, and a workload-restriction penalty (with its own 4-tier evidence
  hierarchy) are all real, specific signals — this is not a generic
  season-ERA number. What is **not** present: a literal pitch-count time
  series or real injury/IL data (only a caller-supplied boolean/tier
  proxy stands in for it).
- **"Hitter prop families use event-appropriate player distributions
  rather than one generic hitter-strength number"** — **True in structure,
  false in one important respect.** Hits/total-bases/RBI/HRR are
  genuinely different read-offs of the *same* simulated game process (not
  independent regressions per stat, not a single generic strength score)
  — the model correctly preserves real within-player, within-game
  correlation across these four stats. However, **every teammate other
  than the target hitter is drawn at generic league-average rates**
  regardless of the real lineup around them, which specifically degrades
  the RBI and HRR families (both depend heavily on teammates reaching
  base). `hitter_stolen_bases` gets no model of any kind.
- **"Alternate thresholds come from internally coherent distributions and
  satisfy monotonicity"** — **True everywhere it was checked.** Every
  total/spread/pitcher-workload family evaluates one single coherent
  distribution (one Poisson λ or one survival curve) at each threshold —
  monotonicity holds by mathematical construction, not by a separate
  per-threshold check, for game/alt/F5/team totals, winning margin, and
  pitcher K/outs alike. Hitter props additionally run an **active runtime
  invariant check** (`run_invariant_checks()`) confirming monotonicity and
  cross-stat coherence (e.g., HR≥1 implies hits≥1 and TB≥4) against the
  raw simulated lines, though it was not confirmed whether a failed check
  actually blocks a row from being written (flagged as a follow-up
  question, not fixed here).

## Other findings worth flagging

1. **`api/slate.js` materially diverges from the gating engine for shared
   families.** Its F5 pricing is a hand-tuned linear heuristic with no
   tie probability at all (the file's own comments concede this). Its
   NRFI/YRFI labels itself `poisson_independence` in metadata but the
   actual tradable probability is a discrete point-scoring heuristic that
   never consumes the computed lambda. Both NRFI/YRFI and team totals in
   this file compare against a **hardcoded flat 52% "market implied"**
   constant rather than a real market price. This file does not gate real
   money, but if it serves any live-viewed surface, its numbers there are
   not the same quality as the gating engine's.
2. **`config/rules.json`'s numeric constants are documented as "source of
   truth" but are not actually read at runtime** by `compute_projections()`
   — the floors/ceilings/blend constants are independently hardcoded in
   `build_market_ledger.py`. A future edit to the JSON alone would
   silently do nothing; this is a documentation/config-drift risk, not
   examined further here per this audit's "audit-only" scope.
3. **Game totals' computed probability is silently discarded at
   persistence** (0/301 records), not merely capped — `rejected_row()`
   never populates the market-price field `classify_evaluation_status()`
   requires, so a real, computed number becomes `MISSING_MARKET_PRICE`/
   null rather than a visibly-capped `Paper` value. This does not block
   determining the methodology (fully characterized above), so per this
   audit's scope it is reported, not fixed.
4. **A recurring structural pattern, not a per-family quirk**: several of
   the soundest models in this codebase (F3/F7 winner markets, pitcher
   K/outs, alternate-line totals/margins) live entirely in the
   discovery/research adapter layer and never reach
   `data/edgelab/model_evaluations/`. The blocker in every one of these
   cases is **wiring/identity-resolution, not new statistical modeling** —
   this is the single highest-leverage category of work identified by
   this audit.

## Prioritized implementation backlog

**A. Already sound — only needs broader persistence/wiring**
- Full-game ML, F5 winner, NRFI/YRFI: keep as-is, extend persistence coverage.
- Alternate totals (full-game/F5/F3/F7) and winning-margin alternates:
  monotonicity already guaranteed by construction; only needs a decision
  to wire the existing adapter output into `model_evaluations`.
- Pitcher strikeouts and pitcher outs: the survival-curve model is sound
  and threshold-monotonic by construction, and its required inputs are
  confirmed populated in live data. The one remaining step is completing
  pitcher-identity resolution in `lib.kalshi_mlb_market_classifier` and
  adding the two families to `config/rules.json`'s `market_list` /
  `build_model_evaluations_from_pipeline()`'s source. **No new model
  design needed.**

**B. Sound foundation but missing important inputs**
- F3/F7 winner and F3/F7 winning-margin: add the platoon nudge already
  used by full-game/F5, add a period-appropriate clamp before treating
  the output as production-grade, accumulate a paper sample before any
  activation discussion.
- Hitter hits/total-bases/HRR/RBI: reconnect the already-written,
  already-tested platoon and pitcher-quality adjustment functions to the
  live pricing path (currently dead code); replace the generic
  league-average teammate assumption with real lineup-aware rates,
  highest-value for RBI/HRR specifically; wire the fetched-but-unused
  real bullpen pitch-mix data into bullpen innings.
- Pitcher K/outs: wire `opponentTeamKPct` once real team-level K-rate
  data is populated (currently always `None`, an upstream data gap, not
  a modeling gap).

**C. Proxy/underperforming model needing replacement or investigation**
- Team totals: highest priority in this tier — currently production-live
  with comprehensive inputs, yet the worst measured model-side
  calibration of any covered family. Root-cause whether the shared
  ML-lambda structure, its clamps, or its bullpen-xFIP fallback default
  is the source before any recalibration attempt.
- Game totals and full-game winning margin: both carry real historical
  evidence of poor performance (Rule 71/81). Root-cause whether the
  Poisson-sum/joint approach itself is inadequate for these specific
  markets versus an execution/threshold issue, before considering
  reactivation.

**D. Unsupported, requiring genuinely new modeling work**
- Hitter stolen bases: no model of any kind exists; would require new
  steal-attempt/caught-stealing/catcher-arm/hold-time modeling not
  present anywhere in the current simulator.

## Validation

This is an audit-only pass — no source file was modified. No measurement
or documentation bug was found that prevented determining any family's
current methodology, so no code fix was made or is warranted by this
audit's own scope. No tests were run (no code changed).

## Artifacts

- This document: `docs/MODEL_PROBABILITY_METHODOLOGY_AUDIT.md`
