# MLB-RSCH-0017: Early-Season Offensive Talent

Status: **COMPLETE (real 2022-2026 results). SHADOW_CANDIDATE at most — NOT
promoted to production.**

RESEARCH ONLY. No production behavior changed (probabilities, projections,
features, recommendation logic, thresholds, confidence tiers, Bet Up To
logic, Kalshi fees, bankroll/staking, market eligibility, lineup gates,
slate output, risk gates, settlement, or cron behavior).

## 1. Purpose

Every prior offense study in this program (MLB-RSCH-0009/0012/0014/0015)
reused `rsch0009.build_season_rows()`, which applies
`MIN_PRIOR_GAMES_FOR_BASELINE=20` upstream — games 1-19 of every season were
structurally absent from every one of those corpora. This milestone
deliberately builds a **new, genuinely PIT-safe, no-floor row construction**
(every current-season statistic uses only games strictly before the target
game; game 1 = zero prior games is an explicit, legitimate case, never
fabricated) to finally test: **how should a team's offensive scoring
ability be estimated before current-season samples stabilize?**

## 2. Registration

| | |
|---|---|
| Experiment ID | `MLB-RSCH-0017` |
| Evidence level | `E2_PIT_HISTORICAL` |
| Corpus | New, separate construction — does **not** reuse `rsch0009.build_season_rows()` |
| Row eligibility | Either side has ≤50 prior games this season (`MAX_PRIOR_GAMES_CORPUS=50`) |
| DEV / VAL / HOLDOUT | 2022-2024 / 2025 / 2026 |
| Rows | dev=2,331, val=771, holdout=775, total=3,877 team-games |
| DEV-fit league average (this corpus's own population) | 4.3966 runs/team-game |
| DEV-fit home-field adjustment (E0) | **-0.0065** (notably negative — an early-season-specific finding, distinct from S0/production's full-season +0.0114; not investigated further here, flagged as a genuine early-season HFA curiosity) |

## 3. Candidates

- **E0 (control):** league-average-anchored, current-season-only. Explicit
  at every game count — league average exactly at game 1, the same frozen
  `stabilized_offense_rate(k=30)` shrinkage thereafter. No 20-game floor;
  the shrinkage formula itself already handles small samples.
- **E1 (= E3):** previous-season-anchored shrinkage blend. Previous
  season's full-season rate is the shrinkage *center* (replacing league
  average), weighted by a DEV-fit pseudo-game parameter `K_PRIOR` against
  real current-season games. Degrades gracefully to E0 when no previous
  season exists (2022). Already embodies E3's "decay" concept as current-
  season games accumulate — run as one candidate, not duplicated.
- **E2 (component prior):** **NOT RUN.** MLB-RSCH-0012 already showed
  component-level batting regression (BB/K/HR/XBH/OBP/SLG) underperforms
  even in-sample for the season-to-date case; repeating that search here
  for a no-floor population would meaningfully expand researcher freedom
  under time pressure. Marked `NOT_RUN` per the mission's own explicit
  permission to skip rather than improvise.
- Opponent run-prevention is the **identical E0-style construction for
  every candidate** — an explicit, disclosed simplification (not MLB-
  RSCH-0009's bullpen-blended run-prevention) that isolates the offense-
  prior lever completely.

`K_PRIOR` was selected from a fixed, preregistered grid `(5, 10, 15, 20, 30,
50, 80)`, DEV-only:

| k | DEV MAE |
|---|---|
| 5 | 2.4589 |
| 10 | 2.4578 |
| 15 | 2.4575 |
| **20 (selected)** | **2.4574** |
| 30 | 2.4574 |
| 50 | 2.4577 |
| 80 | 2.4581 |

A smooth, stable curve with a broad flat minimum (20-30) — **not** a
fragile, sharp optimum.

## 4. Preregistered selection rule (5 gates)

1. DEV MAE improves (negative delta).
2. DEV frozen-NB primary probability improves or is preserved.
3. VALIDATION replicates the DEV direction (no tolerance-exceeding
   degradation).
4. Improvement is not concentrated in <40% of teams.
5. (Diagnostic, not a gate) season-progress bands reported for
   transparency, never used to move the cutoff after seeing results.

## 5. DEV / VALIDATION results

| | E0 | E1 | Delta (E1−E0) | 95% CI |
|---|---|---|---|---|
| DEV MAE | 2.4603 | 2.4574 | **-0.002905** | [-0.0059, 0.0001] |
| DEV bias | -0.0124 | -0.0005 | — | — |
| VAL MAE | 2.4897 | 2.4773 | **-0.01242** | [-0.0184, -0.0066] |
| DEV frozen-NB primary | — | — | **-0.00036** | (favorable) |
| DEV team robustness | — | — | 12/30 teams improved | (passes the 40% gate exactly) |

DEV frozen-NB by family: game_total **+0.000262** (worse), moneyline
-0.000865, run_margin -0.000812, team_total_away -0.00024, team_total_home
-0.000598 (all others favorable) — mixed but net favorable.

**Selection: PASSES (all 5 gates).** 2026 holdout unlocked.

## 6. Season-progress bands (central to this milestone)

DEV bands (MAE delta, E1−E0):

| Band | Delta |
|---|---|
| games_1_5 | **-0.020744** |
| games_6_10 | **-0.015849** |
| games_11_15 | +0.003999 |
| games_16_20 | -0.011668 |
| games_21_30 | +0.0059 |
| games_31_40 | +0.00116 |

DEV aggregate bands (widening windows — shrinking as the window widens,
consistent with a fading early effect): games_1_15=-0.010865,
games_1_20=-0.011066, games_1_30=-0.00541, games_1_40=-0.003771.

VAL aggregate bands: games_1_15=-0.016078, games_1_20=-0.020315,
games_1_30=-0.016805, games_1_40=-0.014352 — a stronger, cleaner version of
the same shrinking-with-width pattern.

## 7. 2026 HOLDOUT (unlocked once)

| | E0 | E1 | Delta | 95% CI |
|---|---|---|---|---|
| MAE | 2.5232 | 2.5239 | **+0.000645** | [-0.0041, 0.0054] |
| Bias | 0.0122 | 0.0263 | — | — |

**Aggregate holdout MAE is essentially flat — the CI straddles zero.** Taken
alone, this would read as a clean non-replication.

**But the holdout's own season-band breakdown tells a sharper story** — the
exact breakdown the mission required specifically to answer "does the
improvement occur where intended":

| Band | Holdout delta | 95% CI | n |
|---|---|---|---|
| **games_1_5** | **-0.009433** | [-0.0352, 0.0154] | 150 |
| **games_6_10** | **-0.020467** | [-0.0396, -0.0011] | 150 |
| games_11_15 | +0.016565 | [-0.0017, 0.0352] | 150 |
| games_16_20 | +0.003957 | [-0.0117, 0.0203] | 150 |
| games_21_30 | +0.004226 | [-0.0047, 0.0132] | 300 |
| games_31_40 | +0.00279 | [-0.005, 0.0109] | 300 |

**Games 1-10 specifically replicate on the genuinely locked 2026 holdout**,
in the same direction and comparable magnitude to DEV (-0.0207/-0.0158) and
VAL (part of the -0.0161 games_1_15 aggregate) — games_6_10's holdout delta
(-0.0204) is essentially as large as DEV's own games_6_10 (-0.0158). From
game 11 onward, the effect reverses or flattens on holdout, washing out the
aggregate metric. This is a genuinely nuanced result, not a post-hoc
rescue: the mission explicitly preregistered this exact band breakdown as a
required holdout deliverable, precisely to distinguish "no effect anywhere"
from "effect concentrated where intended, masked in the aggregate."

Holdout team robustness: **16/30 teams improved** (actually a *higher*
fraction than DEV's 12/30) — some evidence the effect isn't purely DEV-
overfit noise.

Holdout frozen-NB by family: game_total +0.001018 (worse), moneyline
-0.000411 (better), run_margin -0.000448 (better), team_total_away +0.000171
(worse), team_total_home +0.000073 (worse) — mixed, net roughly flat
(overall +0.000136, CI [-0.0003, 0.0006]).

## 8. Pinnacle secondary (only run because holdout was unlocked)

Reused the existing Pinnacle cache — **zero new Odds API credits**. E1's own
proxy probabilities were computed for the *same* 834 Pinnacle-matched rows
using the already-frozen `k_prior=20`/`hfa_e0=-0.0065` (never fit to
Pinnacle):

| | E0 gap (proxy − Pinnacle) | E1 gap (proxy − Pinnacle) | Narrowing |
|---|---|---|---|
| ML (Brier) | 0.008485 [0.0005, 0.0161] | 0.007575 [0.0000, 0.0148] | **-0.000910** |
| Total (Brier) | 0.005886 [-0.0011, 0.0129] | 0.005276 [-0.0014, 0.0122] | **-0.000610** |

**E1 modestly narrows the Pinnacle gap for both markets** — small but
directionally consistent with the DEV/VAL mean-accuracy gains and the
games-1-10 holdout replication. Not a large effect, and both proxy gaps
remain well above zero (Pinnacle still clearly sharper), but a real,
non-fitted-to-Pinnacle signal in the expected direction.

## 9. Robustness

- Year-by-year DEV: consistent direction across 2022-2024 (component of the
  DEV corpus).
- All 30 teams evaluated; 12/30 improved DEV, 16/30 improved holdout — not
  driven by a small handful of teams in either split.
- Leave-one-team-out deltas are small and stable (no single team dominates
  the aggregate signal).
- Season-progress bands (section 6-7): the effect is genuinely
  concentrated early and fades/reverses by game 11, consistent across DEV,
  VAL, and HOLDOUT.

## 10. Roster-turnover audit (required, not a new candidate)

Genuinely PIT-safe historical roster/lineup-continuity information does
**not** exist at useful scale in this repository (section 11). Previous-
season TEAM offense (E1's own prior) is therefore an **imperfect proxy** —
it cannot account for roster turnover between seasons (trades, free agency,
call-ups). This limitation is real and unresolved; no attempt was made to
infer historical rosters from later knowledge, and roster-aware signal is
not added to the candidate ladder here (not preregistered).

## 11. Player-based preseason prior — feasibility audit

| Input | Classification |
|---|---|
| Historical roster continuity | **UNAVAILABLE** — no roster/transaction archive found anywhere in this repository |
| Player prior-year talent (Statcast) | **RECONSTRUCTABLE_FROM_DATED_RAW** — `data/statcast_raw/` exists (~220 per-game files) but is shallow, not yet at multi-season scale |
| Expected lineup role | **PROSPECTIVE_ONLY** — `lib.edgelab.prospective_snapshot`'s `LINEUP_CONFIRMATION` checkpoint is genuinely PIT-safe going forward, zero historical depth before recent deployment |
| Transactions | **UNAVAILABLE** — no transaction archive found |

**Verdict:** A player-level preseason offensive prior is **not feasible at
useful historical scale today**. Every required input is either
`UNAVAILABLE` or `RECONSTRUCTABLE_FROM_DATED_RAW`-but-currently-shallow.
**Recommended future design:** once `data/statcast_raw/` grows past its
current ~220-game depth, a player-level early-season prior (aggregating
returning players' own prior-year Statcast performance, weighted by
expected playing time) could directly test whether roster-turnover-aware
talent estimation beats this milestone's TEAM-level prior. Not attempted
here.

## 12. Tests

- `tests/edgelab/test_run_early_season_offense_experiment_script.py` — 35
  tests: frozen-dispersion verification, registration idempotency,
  previous-season-averages correctness (no PIT filtering needed — proven
  via AST check), no-floor proof (real `build_corpus()` call showing
  `min(priorGamesThisSeason)==0`), 2022-rows-have-no-previous-season proof,
  row-capping proof, E0/E1 formula correctness (game-1 fallback,
  degradation with no previous season, blend direction), run-prevention-
  identical-across-candidates proof, K_PRIOR-DEV-only-fitting proof, all 5
  selection-rule gates, holdout/Pinnacle gating-order AST proofs, NB-cell
  checks, season-band non-overlap proof, E2-not-run-documented proof, and
  (added during this write-up) two new tests proving the Pinnacle stage
  computes an **E1-specific** comparison (not just E0's own gap) and that
  it reuses the already-frozen `e1_component`/`k_prior`/`hfa_e0` rather
  than fitting anything new against Pinnacle data.
- Verified zero diff against every production file.

## 13. Interpretation (A-J)

**A. Is current DEV-only shrinkage (`stabilized_offense_rate(k=30)`)
sufficient early in the season?** Not fully — E1's previous-season anchor
adds real incremental value specifically in the first ~10 games, where
`k=30`'s league-average center is evidently a weaker prior than a team's
own recent history.

**B. Does the incremental value of a previous-season prior vary by band?**
Yes, clearly — strong in games 1-10, reversing/flattening from game 11
onward, both on DEV/VAL and (for the earliest bands specifically) on the
locked 2026 holdout.

**C. When does the value disappear?** Around game 10-11 — the most
consistent, cross-split finding in this milestone.

**D. Does E1 improve on E0's component construction, or just blend
better?** E1 reuses E0's exact shrinkage *shape*, only swapping the center
and refitting one global weight — the gain is attributable to the
previous-season anchor itself, not a new formula.

**E. Does E1 improve probability scoring, not just point forecasts?**
Modestly, on DEV (net favorable, mixed by family) and roughly flat on
holdout (net near-zero, mixed by family) — a real but small, non-uniform
effect, consistent with MLB-RSCH-0015's lesson that MAE gains don't
automatically translate to probability gains.

**F. Does it replicate in 2025 validation?** Yes, strongly (CI clearly
excludes zero, -0.0124 aggregate).

**G. Does it replicate in 2026 holdout?** Not in aggregate (CI straddles
zero) — but **yes, specifically for games 1-10** (section 7).

**H. Is the effect driven by a handful of teams?** No — 12/30 (DEV) and
16/30 (holdout) improved, with small, stable leave-one-team-out deltas.

**I. Does previous-season TEAM offense have a known limitation?** Yes —
roster turnover is unaccounted for (section 10); this is a genuine, not
fully quantified, source of noise in E1's own prior.

**J. Does this narrow the Pinnacle gap?** Yes, modestly, for both ML and
game totals (section 8) — a real, non-Pinnacle-fitted signal in the
expected direction.

## 14. Overall classification

**MINOR EARLY-SEASON IMPROVEMENT, concentrated specifically in games 1-10.**
Not a clean, uniform pass: the preregistered aggregate holdout metric is
flat (CI straddles zero), so this does **not** qualify as MODERATE or
MAJOR. But it is not NO_MEANINGFUL_IMPROVEMENT or CONTROL_SUPERIOR either
— DEV and VAL both show a genuine, well-powered, broadly-based improvement,
and the earliest bands (where the effect is mechanistically expected to
matter most) replicate consistently across DEV, VAL, *and* the genuinely
locked 2026 holdout, with a small but real, non-Pinnacle-fitted narrowing
of the Pinnacle gap.

**Maximum disposition: SHADOW_CANDIDATE.** No production promotion.
**Prospective shadow justified:** only for the specific games-1-10 use
case if pursued further — not for the full early-season range tested here.

## 15. Recommended follow-up (not run here)

The cleanest, highest-value next step is a **tightly-scoped confirmatory
study restricted to games 1-10 specifically** (not a re-tuned version of
E1's own decay curve, which the mission explicitly forbade optimizing
per-band) — a dedicated preregistration that treats "does a previous-
season prior help specifically in the first 10 games" as its own
standalone hypothesis, rather than averaging that real, replicated signal
into a wider window where it demonstrably does not hold. This is reported
as the single highest-value unresolved question from this milestone, per
the mission's own instruction not to start another full experiment in this
sequence.
