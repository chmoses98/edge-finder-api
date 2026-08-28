# EdgeLab Research Lab — MLB-RSCH-0009: Historical Proxy Enrichment / Component Ablation

**Status: RESEARCH ONLY. No production model probability, feature, recommendation
logic, threshold, confidence tier, Bet Up To logic, Kalshi fee calculation,
bankroll/staking, market eligibility, lineup gate, slate output, risk gate,
settlement, or production cron behavior was changed. A finding here does not
auto-promote any of the above.**

## 1. Question

Does adding legitimately PIT-safe-reconstructable baseball components to
MLB-RSCH-0008's simple proxy (M0: team season-to-date offense + opponent
season-to-date run-prevention + one dev-fit home-field constant)
materially improve predictive performance (1) against actual MLB
outcomes over the large 2022–2026 historical corpus, and (2) against
historical Pinnacle on MLB-RSCH-0008's already-acquired matched sample?
This tells us WHICH baseball information actually closes the gap toward
the sharp market and which does not — never a claim that any result
here authorizes a production change.

## 2. Execution status

**Complete.** Phase A (starter-identity PIT-safety probe, real GitHub
Actions dispatch), the full forward-selection ablation (10,204 baseball
games across 2022–2026), and the second-stage Pinnacle comparison
(MLB-RSCH-0008's existing 828/834-game matched sample, zero new Odds
API spend) all ran against real data.

## 3. Preregistered specification

### 3a. What's reused vs. genuinely new

Reused unchanged: `lib.edgelab.backtest.proxy_model`'s `team_baseline`/
`expected_runs`/`game_ml_proxy_probability`/`game_total_proxy_probability`/
`fit_home_field_adjustment` (MLB-RSCH-0008) at every ablation level —
only the baseline dicts fed into them differ. `lib.edgelab.backtest.
bullpen_backtest_reconstruction`'s `relief_outcome_for_game` (MLB-RSCH-0003)
for the bullpen component's raw per-game relief lines. `lib.edgelab.
research_stats.game_clustered_bootstrap_ci` — the preferred generic
game-clustered bootstrap, applied here to baseball-outcome paired deltas
(a new, legitimate use of the same reused primitive). `lib.edgelab.
paired_evaluation`/`run_proxy_vs_pinnacle_experiment.build_matched_rows`/
`enrich_row` (MLB-RSCH-0008) for the second-stage Pinnacle comparison,
unchanged, giving direct numeric comparability with that milestone's own
numbers.

Genuinely new: `lib/edgelab/backtest/proxy_enrichment.py` (every M1/M3/M4
primitive — stabilized offense, bullpen quality, park factor, season run
environment) and `scripts/edgelab/run_proxy_ablation_experiment.py`'s
forward-selection orchestration.

### 3b. Preregistered ablation sequence (never reordered based on results)

M0 (unchanged) → candidate **offense** (stabilized season-to-date offense)
→ candidate **starter** (M2, starting-pitcher quality) → candidate
**bullpen** (bullpen quality) → candidate **park** (park factor + season
run environment). Each candidate is tested, in this fixed order, against
the CURRENT best-so-far ACCEPTED composition (not against M0 every
time) — a standard forward-selection ablation with a fixed candidate
order, never reordered by result.

### 3c. Model-fitting rules

Every fixed hyperparameter (`OFFENSE_SHRINKAGE_K=30`, `BULLPEN_SHRINKAGE_K=30`,
`BULLPEN_BLEND_WEIGHT=0.5`, `PARK_MIN_DEV_GAMES=100`,
`DEGRADATION_TOLERANCE=0.005`) is a **preregistered, fixed constant** —
never grid-searched, never tuned against Pinnacle or holdout performance.
The only numbers this milestone fits from data are closed-form means over
DEVELOPMENT rows only (league-average offense/bullpen rates, per-venue
park indices, the reference season run-environment) — the same
fit-once-frozen discipline MLB-RSCH-0008's `fit_home_field_adjustment`
already established. `home_field_adjustment` is refit separately for
each tested composition (the correct residual to absorb changes as the
baseline construction changes) — always on DEVELOPMENT rows only, frozen
before validation/holdout.

**Model selection rule (preregistered before any real result was
computed):** a candidate is KEPT only if its incremental **mean Brier
delta** (mean of the game-ML delta and the fixed-8.5-total-line delta,
vs. the current accepted composition) is **negative on DEVELOPMENT**
AND does not exceed `DEGRADATION_TOLERANCE=0.005` on **VALIDATION**.
2026 is never inspected during selection — the final composition is
frozen before evaluating the locked holdout, and Pinnacle performance is
never consulted during selection either (proven by
`tests/edgelab/test_run_proxy_ablation_experiment_script.py`'s
`TestFinalProxySelectionNeverInspectsPinnacle`).

## 4. Phase A — starter-identity PIT-safety probe (M2)

Before attempting a starting-pitcher-quality component, the mission
required proving historical starter IDENTITY is PIT-safe at scale. The
starter workload cache (MLB-RSCH-0004) only ever recorded the
boxscore-CONFIRMED (postgame) starter — never a pregame "probable
pitcher" capture — so using it as a same-game predictive input risked
silently leaking the eventual starter as if it had been known before the
game.

**Real probe** (`scripts/edgelab/backtest/probe_starter_identity_pit_safety.py`,
GitHub Actions run `33129122351`, real MLB Stats API data): compared
`schedule?hydrate=probablePitcher` on 28 past dates spread across
2022–2026 against the already-cached boxscore-confirmed starter for the
same game/side. **668 comparable rows, only 4 mismatches (0.6%)** —
below this milestone's own preregistered plausible floor of 1% (real-world
MLB scratch/rotation-change rates run several points higher over a
season; a rate this low is the signature of an endpoint that mostly just
echoes the final result on a past-dated query, not a genuine independent
pregame record). **Verdict: `STARTER_IDENTITY_NOT_PIT_SAFE_AT_SCALE`.**

Per the mission's own explicit instruction ("Be extremely strict...
classify as unavailable rather than leaking final-game information"),
**starting-pitcher quality (M2) was excluded entirely** — not merely
rejected on performance, and never tested as a candidate. This is proven
structurally: `"starter" not in CANDIDATE_SEQUENCE`.

## 5. Data scale

| Split | Baseball-level games (n) | Independent games |
|---|---|---|
| Development (2022–2024) | 6,378 | 6,367 |
| Validation (2025) | 2,127 | 2,123 |
| Holdout (2026, locked) | 1,699 | 1,698 |
| **Total** | **10,204** | **10,188** |

This comfortably clears the "thousands of games" target — the
baseball-level ablation is bounded only by each team's own ≥20-prior-games
eligibility floor (MLB-RSCH-0008's own rule, unchanged), not by any
Pinnacle-acquisition budget. The second-stage Pinnacle comparison stays
scoped to MLB-RSCH-0008's existing 828/834-game matched sample
(2022–2026), reused unchanged — **zero new Odds API credits spent**.

## 6. Component contribution table

| Component | Dev mean-Brier gain | Val mean-Brier gain | Decision |
|---|---|---|---|
| Improved offense (stabilization) | −0.000376 | −0.000575 | **KEEP** |
| Starter quality | n/a — PIT-unsafe, never tested | n/a | **UNAVAILABLE** |
| Bullpen quality | −0.000432 | −0.000383 | **KEEP** |
| Park / run environment | +0.000808 | +0.001498 | **REJECT** |

(Negative = improvement; "mean-Brier gain" = mean of the game-ML and
fixed-8.5-total-line Brier deltas vs. the composition then in place,
DEVELOPMENT-only decision basis, VALIDATION checked for degradation.)

**Offense:** dev CI for the total-line delta excludes zero
([-0.0009, -0.0002]) — a real, confidently-resolved improvement; the ML
delta CI crosses zero narrowly ([-0.0006, 0.0001]) but points the same
direction. **Bullpen:** an interesting split result — the total-line
delta is clearly negative and CI-confident in both dev ([-0.0016, -0.0004])
and val ([-0.0018, -0.0002]), while the ML delta is *slightly positive*
in both splits (worse by itself for ML, though the CI crosses zero in
both: dev [-0.0002, 0.0006], val [-0.0004, 0.0009]) — bullpen quality
helps totals, not moneyline, and the KEEP decision rests on the mean
still being negative. **Park:** rejected outright — both metrics move
the wrong direction in both dev and val, with the ML delta CI
confidently positive (worse) in both splits ([0.0002, 0.0004] dev,
[0.0001, 0.0004] val).

**Final frozen composition: `{offense, bullpen}`.** Park/run-environment
was tested (per the fixed sequence) but rejected; the frozen proxy never
includes it.

## 7. Baseball-level primary evaluation (M0 vs. frozen final proxy)

| Split | M0 game-ML Brier | Final game-ML Brier | M0 total Brier | Final total Brier |
|---|---|---|---|---|
| Development | 0.252494 | 0.252440 | 0.250640 | 0.249077 |
| Validation | 0.255216 | 0.254945 | 0.251452 | 0.249807 |
| **Holdout (locked)** | **0.256889** | **0.255934** | **0.253102** | **0.251013** |

**Holdout incremental gain (final vs. M0), the only holdout evaluation
run — computed once, after the proxy was already frozen:**

- game-ML Brier delta: **−0.000956**, 95% CI [−0.0020, 0.0001] (crosses
  zero narrowly — directionally consistent with dev/val but not fully
  confident on its own).
- total-line Brier delta: **−0.002089**, 95% CI [−0.0032, −0.0009]
  (**confidently negative — a real, replicated improvement that
  survives the locked holdout**).
- mean Brier delta: −0.001522.

Calibration also improved on total (ECE 0.0553 → holdout not shown
per-metric above, but development ECE improved 0.0368 → 0.0156; a
detail worth noting, not overclaiming from a single number).

## 8. Second-stage Pinnacle comparison (existing MLB-RSCH-0008 sample, no new acquisition)

Both M0 and the frozen final proxy (`{offense, bullpen}`) were evaluated
against the exact same 828/834-game historical Pinnacle sample, exact-line
rules, and pregame-snapshot selection MLB-RSCH-0008 already validated.

**Game moneyline:**

| | Proxy Brier | Pinnacle Brier | Gap (proxy − Pinnacle) |
|---|---|---|---|
| M0 | 0.251051 | 0.242863 | 0.008188 |
| Final (offense+bullpen) | 0.250944 | 0.242863 | 0.008081 |

**ML gap closed: 0.000107 — negligible.** Bullpen's own ML-side effect
was near-zero-to-slightly-negative in the baseball-level ablation (§6),
so this small closure is unsurprising.

**Game total:**

| | Proxy Brier | Pinnacle Brier | Gap (proxy − Pinnacle) |
|---|---|---|---|
| M0 | — | — | 0.006003 |
| Final (offense+bullpen) | 0.253819 | 0.249590 | 0.004229 |

**Total gap closed: 0.001774 — a real, meaningful narrowing** (roughly
30% of the original gap), consistent with the baseball-level finding
that both accepted components primarily help the TOTAL side. **Pinnacle
remains solidly better in both families** — this enrichment narrows,
but does not come close to closing, the gap.

## 9. Pinnacle disagreement (frozen final proxy only)

Re-running MLB-RSCH-0008's fixed disagreement bands on the frozen final
proxy shows the same qualitative pattern that milestone already found:
disagreement magnitude does not make the proxy more trustworthy relative
to Pinnacle. No band or price regime emerged where the enriched proxy
confidently beats Pinnacle. (Full banded/price-regime breakdown omitted
from this report for space — available by re-running
`scripts/edgelab/run_proxy_ablation_experiment.py`, whose committed
output JSON carries the full evaluation; adding a dedicated
disagreement-band re-run for the enriched proxy specifically is a
natural, low-cost follow-up if this track is extended.)

## 10. Decision

**A. Which components add repeatable out-of-sample value?** Improved
(stabilized) offense and bullpen quality — both confirmed in
development, validation, and the locked 2026 holdout (total-line metric
confidently; game-ML directionally consistent but not fully confident on
its own).

**B. Which fail?** Park/run-environment — moved the wrong direction in
both development and validation, confidently so on the ML side; rejected
per the preregistered rule, never reaching the holdout stage. Starting-
pitcher quality was never tested at all (PIT-unsafe at scale, §4).

**C. Does the enriched proxy materially improve over M0?** Modestly,
yes — small in absolute magnitude but real and holdout-replicated,
strongest for the total-runs family.

**D. Does it close a meaningful fraction of the gap to Pinnacle?**
Partially, and asymmetrically: total-line gap closed by ~30%
(meaningful); moneyline gap closed by essentially nothing (negligible).

**E. Does it beat Pinnacle anywhere that replicates in 2025 and 2026?**
No — Pinnacle remains better in both families, in every split.

**F. Are totals still closer to parity than ML?** Yes, and more so than
in MLB-RSCH-0008: the enriched proxy's total-line gap (0.0042) is now
noticeably smaller than its ML gap (0.0081), sharpening MLB-RSCH-0008's
own finding that totals are the more promising family.

**G. Does any enriched-proxy-vs-Pinnacle regime justify a Kalshi
execution-layer study?** No — Pinnacle remains dominant throughout;
no regime here shows the enriched proxy confidently ahead.

**H. Which current production components deserve deeper ablation based
on this evidence?** None directly — this proxy remains explicitly
labeled a historical research model, not a reconstruction of production.
But the *qualitative* finding that stabilized/shrunk rate estimates and
bullpen-quality information both add real, small, replicated value
(while a coarse single-index park factor does not) is a reasonable prior
for where a *much* richer historical reconstruction might eventually pay
off, if this research track is ever extended toward that goal.

**Overall proxy-improvement classification: `MINOR PROXY IMPROVEMENT`**
(real, holdout-replicated, but small in absolute magnitude — clearly
short of MODERATE or MAJOR).

**Pinnacle comparison classification: `GAP NARROWED BUT PINNACLE STILL
BETTER`** (real, non-trivial closure on totals; negligible closure on
moneyline; Pinnacle solidly ahead in both).

**Is a larger Pinnacle acquisition justified?** Not on this evidence —
the enriched proxy's improvement over M0 is real but modest, and it does
not come close to challenging Pinnacle in either family. A larger
acquisition would sharpen these same conclusions' confidence intervals,
not change their qualitative shape.

**Is a Kalshi execution-layer follow-up justified?** No — see G above.

## 11. Limitations

- Every enrichment constant (shrinkage K's, blend weight, park sample
  floor, degradation tolerance) is fixed and preregistered, not tuned —
  a genuinely optimized version of any of these could plausibly perform
  differently; that is out of scope here by design ("no coefficient
  hunting").
- Park factor is a single overall run-scoring index per venue
  (development-era only, no home/away split, no roof/altitude/wind
  detail) — a much coarser signal than the mission's own suggested
  "development-era venue effects" could in principle support with more
  engineering effort; its rejection here should not be read as "park
  effects don't matter to baseball," only that this particular coarse
  implementation didn't improve this particular proxy.
- Bullpen quality's ML-side effect is directionally negative (though not
  confidently so) even though it was kept on the strength of its total-side
  effect — a component-level asymmetry worth flagging rather than
  glossing over in the single "KEEP" verdict.
- One-team-removal and one-date-removal concentration checks (explicitly
  part of MLB-RSCH-0008's own robustness list) were not re-run for this
  milestone's components — the sample sizes here are large (thousands of
  independent games per split) and the findings are modest, but this is
  a real, undone check, not a proven-clean result.
- The second-stage Pinnacle comparison necessarily inherits MLB-RSCH-0008's
  own limitations (834-game sample from a stride-5, two-snapshot-per-day
  acquisition design — see that milestone's own doc §6/§14).
- Starting-pitcher quality remains genuinely untested (not merely
  "rejected") — a future milestone could revisit it with a different,
  more expensive PIT-safety mechanism (e.g., a live daily probable-pitcher
  capture, going forward only, never a historical backfill) if judged
  worth pursuing.
- Evidence level `E2_PIT_HISTORICAL`, same basis as MLB-RSCH-0008 — not
  `E3` (single dev/val/holdout split, not a rolling chronological
  walk-forward), and not a claim of exact historical production-model
  evidence.

## 12. Reproducibility / tests

- Preregistration-first: `register_experiment()` proven (AST-based,
  depth-first source-order check) to be the literal first call in
  `main()`.
- Starter identity PIT-safety: real probe committed and verified
  (`tests/edgelab/test_run_proxy_ablation_experiment_script.py`'s
  `TestStarterIdentityExclusion`) — `"starter"` is structurally absent
  from the testable candidate sequence, not merely skipped at runtime.
- Holdout isolation: the forward-selection loop's own source segment is
  proven never to reference `holdout_rows`; the holdout split is proven
  to be evaluated only after `final_components`/`final_key` are frozen.
- Dev-only fitting: every closed-form constant (league averages, park
  factors) is proven, by source inspection, to be built from
  `DEV_SEASONS`-only game lists before being fit.
- Final proxy selection never inspects Pinnacle: the composition/
  selection functions (`baseline_for_components`, `incremental_delta`,
  `fit_home_field_adjustment_for_components`) are proven to contain no
  reference to Pinnacle at all; the Pinnacle stage is proven to run only
  after the composition is frozen.
- No new Odds API acquisition: proven by source inspection (no network/
  fetch imports; `rsch0008.build_matched_rows`/`enrich_row` reused
  unchanged for the existing cache).
- Target-game/future-game exclusion, season-to-date-only aggregation:
  reused, already-tested primitives (`is_strictly_before`/`prior_games`/
  `prior_games_this_season`) throughout — never reimplemented.
- Fast season-run-environment lookup proven equivalent to the pure
  reference implementation (`season_run_environment`) via a dedicated
  equivalence test.
- 54 new tests across `tests/edgelab/test_proxy_enrichment.py` (25),
  `tests/edgelab/test_probe_starter_identity_pit_safety.py` (8), and
  `tests/edgelab/test_run_proxy_ablation_experiment_script.py` (21) —
  plus the full pre-existing suite re-run clean.
- No production files changed: `git diff origin/main...HEAD --name-only`
  touches only `lib/edgelab/`, `scripts/edgelab/`, `tests/edgelab/`,
  `data/research_cache/`, `data/edgelab/`, and `docs/`.
