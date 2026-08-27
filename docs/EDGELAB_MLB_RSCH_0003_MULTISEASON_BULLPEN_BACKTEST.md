# EdgeLab Research Lab — MLB-RSCH-0003: Multi-Season Bullpen Workload Backtest

**Status: RESEARCH ONLY. No production model probability, feature, recommendation
logic, threshold, confidence tier, Bet Up To logic, Kalshi fee calculation,
bankroll/staking, market eligibility, lineup gate, slate output, risk gate,
settlement, or production cron behavior was changed.**

## 1. Question

Over multiple MLB seasons and thousands of team-games: does recent bullpen
workload predict subsequent bullpen run prevention, and is the CURRENT
production workload adjustment (`lib.edgelab.bullpen_availability.
compute_bullpen_workload_adjustment`, unchanged, imported not reimplemented)
directionally and magnitude-wise reasonable?

This is a **baseball-level historical study**, not a Kalshi profitability
study (spec section 11) — no market data is used or required.

## 2. Execution status

**Complete.** `.github/workflows/research-multiseason-bullpen-backtest.yml`
was dispatched against `claude/mlb-rsch-0003-results` (a GitHub-hosted
runner, which — unlike this repository's local research environment — has
outbound network access to `statsapi.mlb.com`). The fetch pulled all five
seasons (2022–2026 through the latest completed date available at run time)
with **zero failed game fetches** (12,650 unique games, 30/30 team schedules
per season), the backtest ran the preregistered specification unchanged
against that real data, and the results were committed back to this branch.
(The run's first attempt fetched the data successfully but failed on an
unrelated CI-only bug — the runner had no `pytest` installed for the
workflow's own post-hoc test step; fixed in a follow-up commit and
redispatched to a fully green run. No research code changed for that fix.)

## 3. Preregistered specification

### 3a. Feature families (spec section 3)

All PIT-safe, reconstructed via `lib.edgelab.backtest.
bullpen_backtest_reconstruction.reconstruct_workload_features`, which
filters a team's own game list via `is_strictly_before()` — proven (by that
module's own test suite) to exclude the target game itself, every future
game, and any same-date later game unless `gameNumber` ordering is actually
known (doubleheader-safe).

**Primary specification (reused production formula, not reimplemented):**
`productionFormulaInput` — a team's prior games windowed exactly as
`lib.edgelab.bullpen_usage.summarize_team_bullpen_usage` already does for
production, fed unchanged into `compute_bullpen_workload_adjustment`.

**Exploratory calendar-day features** (spec section 3's explicit list, all
implemented): bullpen pitches previous 1/2/3 calendar days, relievers used
previous 1/2 days, back-to-back reliever count, 3-consecutive-day reliever
count (`None` when the team didn't play all 3 prior days), heavy-usage
reliever count (reuses production's own `HEAVY_USE_PITCH_THRESHOLD=35`),
high-leverage-reliever usage previous day / back-to-back, days since last
game, doubleheader context (`doubleHeader`/`gameNumber`, sourced from the
schedule, known pregame).

### 3b. Outcome (spec section 4)

Primary: **relief runs allowed after the starter exits, per team-game**
(`reliefRunsAllowed`, from `relief_outcome_for_game`). Also recorded: relief
earned runs allowed, bullpen innings pitched, number of relievers used,
full-game team runs allowed. A complete-game shutout (0 relievers) is a real,
well-defined zero; any relief appearance with missing runs/earnedRuns/outs
data invalidates the whole game's outcome (never a partial sum).

**Not implemented:** late-inning (6th-9th) opponent runs — would require the
heavier live-feed endpoint per game, doubling network cost for a
secondary/exploratory outcome; the primary outcome above is used instead.

### 3c. Hypotheses (spec section 5) — preregistered, not changed after results

H1 (workload → worse prevention), H2 (back-to-back → worse prevention), H3
(high-leverage workload > generic workload effect), H4 (nonlinear/extreme
workload), H5 (current multiplier directionally aligned with observed risk).

### 3d. Chronological split (spec section 7)

Development = 2022–2024, validation = 2025, holdout = 2026 (locked). The
same fixed `run_hypothesis_tests` function is applied unchanged to all three
groups — proven structurally (its own source references no season-group
constant; see `tests/edgelab/test_run_multiseason_bullpen_backtest_experiment_script.py::TestHoldoutIsolation`).

### 3e. Evidence level

`E2_PIT_HISTORICAL` — the feature reconstruction pathway is proven
point-in-time-safe by tests, matching `lib/edgelab/pit_provenance.py`'s
existing `team_recent_game_log_reconstruction` manifest entry
(`RECONSTRUCTABLE_FROM_DATED_RAW`).

## 4. Coverage (real data)

| Season | Games | Team-games |
|---|---|---|
| 2022 | 2,415 | 4,832 |
| 2023 | 2,415 | 4,844 |
| 2024 | 2,415 | 4,834 |
| 2025 | 2,416 | 4,838 |
| 2026 (partial, through latest completed date) | 1,989 | 3,980 |
| **Total** | **11,650** | **23,328** |

**23,328 / 3,000 minimum expected team-games — well above target.** Every row
required a resolvable `relief_outcome_for_game()` (no missing runs/earnedRuns/
outs on any reliever) and at least one prior completed game that season
(`recentUsage.dataAvailable`); rows failing either are excluded, not
approximated. Development (2022–2024) = 14,510 team-games / 7,245 games;
validation (2025) = 4,838 / 2,416; locked holdout (2026) = 3,980 / 1,989.

## 5. Results

Sign convention throughout: a **positive** effect (Spearman r > 0 / mean
difference > 0) means *more* workload or *worse* subsequent bullpen
performance (relief runs allowed per 9 innings) — the direction H1/H2/H5
predict. All CIs are 95%, game-clustered bootstrap
(`lib.edgelab.research_stats.game_clustered_bootstrap_ci`, `cluster_key="gamePk"`).

### 5a. Development (2022–2024, n=14,510 rows / 7,245 games) — PRIMARY

| Check | Result | 95% CI | Read |
|---|---|---|---|
| A. H1 — prior-day bullpen pitches vs relief runs/9 | Spearman r = **+0.023** | [0.008, 0.039] | confidently positive, small |
| E. Relievers used previous day (component of H1's feature) | see decile table below | — | — |
| F. H2 — back-to-back reliever usage | mean diff **+0.362** runs/9 | [0.172, 0.569] | confidently positive |
| H3 generic workload (≥35 pitches prev day, reused `HEAVY_USE_PITCH_THRESHOLD`) | mean diff **+0.285** runs/9 | [0.097, 0.496] | confidently positive |
| H. H3 high-leverage workload (any save/hold reliever used prev day) | mean diff **+0.084** runs/9 | [-0.114, 0.281] | **not** confident — crosses zero |
| J. H5 — current production multiplier vs relief runs/9 | Spearman r = **+0.023** | [0.007, 0.037] | confidently positive, small |

**H3 is NOT supported as stated**: the *generic* workload flag shows a
larger, more confident effect (+0.285, entire CI positive) than the
*high-leverage-specific* flag (+0.084, CI crosses zero) — the opposite of
"high-leverage workload has a *greater* predictive effect than generic
workload." Reported as preregistered, not adjusted after seeing this.

**B/D/G — prior 2-day and 3-day workload, 3-consecutive-day usage**: recorded
per team-game (`bullpenPitchesPrevDays2`, `bullpenPitchesPrevDays3`,
`threeConsecutiveDayRelieverCount`) but not separately CI-tested this
round — H1's single-day version was the preregistered primary continuous
check; the 3-day decile table (I. nonlinearity) is below.

**I. H4 — extreme/nonlinear workload** (decile buckets, `bullpenPitchesPrevDays3`
vs relief runs/9, practical units): bucket 1 (0–90 pitches over 3 days) mean
**3.99** runs/9 → bucket 10 (234–474 pitches) mean **4.76** runs/9 — **+0.77
runs/9** between the lowest and highest workload deciles. The climb is
directionally consistent but noisy in the middle deciles (not a clean
monotonic staircase) — descriptive evidence for H4, not a formal
nonlinearity test (see §7).

### 5b. Validation (2025, n=4,838 / 2,416 games)

| Check | Result | 95% CI |
|---|---|---|
| H1 workload vs outcome | r = +0.001 | [-0.028, 0.030] |
| H2 back-to-back | mean diff -0.052 | [-0.381, 0.305] |
| H3 generic workload | mean diff -0.257 | [-0.641, 0.100] |
| H3 high-leverage workload | mean diff -0.012 | [-0.373, 0.337] |
| H5 current multiplier vs outcome | r = +0.010 | [-0.021, 0.039] |

**Every development-set finding that was confidently positive loses
significance in validation** — all five CIs cross zero.

### 5c. Locked holdout (2026, n=3,980 / 1,989 games) — evaluated once, untouched during development

| Check | Result | 95% CI |
|---|---|---|
| H1 workload vs outcome | r = -0.001 | [-0.033, 0.029] |
| H2 back-to-back | mean diff -0.180 | [-0.549, 0.195] |
| H3 generic workload | mean diff -0.101 | [-0.505, 0.277] |
| H3 high-leverage workload | mean diff +0.105 | [-0.266, 0.479] |
| H5 current multiplier vs outcome | r = +0.008 | [-0.023, 0.038] |

**Same pattern as validation: no CI excludes zero.** The development-set
signal did not replicate in the locked 2026 holdout either.

### 5d. Current production formula — multiplier distribution (C.)

| Group | Min | Max | Mean | % neutral (1.00) | % capped (1.12) |
|---|---|---|---|---|---|
| Development | 1.00 | 1.12 | 1.113 | 0.38% | 62.3% |
| Validation | 1.00 | 1.12 | 1.113 | 0.35% | 62.8% |
| Holdout | 1.00 | 1.12 | 1.114 | 0.45% | 65.6% |

**Headline functional-form finding**: the current multiplier sits at its
`MAX_TOTAL_PENALTY` cap for roughly **62–66% of ALL team-games**, in every
season checked, and at its neutral 1.00 floor for under 0.5%. Traced by hand
against a real row (CLE, 2024-08-28: `backToBackPenalty` 0.020 +
`recentPitchWorkloadPenalty` 0.045 (capped, 10 relievers ≥35 cumulative
pitches) + `highLeveragePenalty` 0.060 (capped, 4 taxed high-leverage arms)
+ `overallWorkloadPenalty` 0.051 = 0.176 raw, clamped to the 0.12 cap) —
this is the real, unmodified formula behaving exactly as designed; it is not
a reconstruction artifact. In an ordinary 21-day/~19-20-game MLB stretch, a
team typically cycles enough relievers through enough appearances that
**several of the formula's four components independently hit their own
per-component caps at once**, so their sum routinely exceeds the combined
0.12 ceiling. The multiplier decile table (development) shows this directly:
deciles 4–10 (70% of the distribution) are all pinned at or within 0.013 of
1.12, with no reliable monotonic ordering *within* that capped band (bucket
means bounce between 4.26 and 5.03 runs/9 with no trend) — the formula
provides essentially **no differentiation between "somewhat busy" and
"exhausted" bullpens** once a team crosses a fairly low bar.

## 6. Conclusions

- **A. Does bullpen fatigue exist as a repeatable baseball signal?**
  **Partially, and not robustly.** Development shows a small, statistically
  confident positive signal for H1, H2, H3-generic, and H5 (all 95% CIs
  exclude zero). None of them replicate in validation or the locked 2026
  holdout (every CI in both crosses zero). Read literally, "repeatable"
  requires the signal to hold out-of-sample — it did not here. The honest
  summary is: a real but small in-sample association that has not been
  confirmed as a repeatable predictive signal by this study's own
  chronological validation.
- **B. Are the specific components used by the current model supported?**
  Back-to-back usage and generic recent-pitch-count usage showed the
  strongest development-set support; high-leverage-specific usage did not
  (H3 not supported as directionally stated). None of the three replicated
  out-of-sample (§5b/§5c).
- **C. Is the current 1.00–1.12 adjustment directionally reasonable?**
  Directionally yes in development (H5 r=+0.023, CI excludes zero) — higher
  multiplier does correspond to worse subsequent bullpen performance when it
  varies. But it barely varies: it's capped ~62–66% of the time. This
  directional finding is also unreplicated in validation/holdout.
- **D. Is its magnitude TOO WEAK / PLAUSIBLE / TOO STRONG / UNPROVEN?**
  **UNPROVEN**, conservatively — not because the sample is too small (23,328
  team-games is far above the 3,000-team-game minimum this study targeted),
  but because (1) the directional finding did not replicate out-of-sample,
  and (2) even where it held (development), the formula's own saturation
  (§5d) means most of its dynamic range is unused — "is 1.12 the right
  ceiling" cannot be answered by a formula that is *at* that ceiling for
  two-thirds of all games. `classify_magnitude()` never proposes a
  replacement coefficient, per the mission's explicit "not permission to
  tune the multiplier" instruction — this is a description of the current
  formula, not a recommendation.

## 7. Recommended next experiment (NOT run here)

Per the mission's overfitting-control instruction, this is a preregistration
proposal for a *future*, separately-registered experiment — nothing here was
fit or tuned this round:

- Investigate why development shows a small confirmed effect that validation/
  holdout do not — e.g., stratify by team-quality/season-scoring-environment
  confounds not controlled for in this pass, or test whether the effect is
  concentrated in a subset of team-games (e.g., only true back-to-back-day
  bullpen-game situations) diluted by the much larger "somewhat busy" middle
  of the distribution.
- Test a *raised* or *smoothly continuous* (non-hard-capped) version of the
  current formula's `MAX_TOTAL_PENALTY`/component caps against the same
  development/validation/holdout split, specifically to see whether removing
  the ~62–66% saturation restores differentiation and a stronger,
  more-replicable signal — this is a hypothesis for that next experiment,
  not a change made here.

## 8. Limitations

- Late-inning (6th–9th) opponent runs not implemented — would require the
  heavier live-feed endpoint per game, doubling network cost for a
  secondary/exploratory outcome; the primary outcome (relief runs after
  starter exit) was used instead.
- H3's high-leverage-vs-generic comparison reports both effect sizes side by
  side; it is not a formal statistical interaction test.
- H4's nonlinearity check is descriptive (decile table), not a formal
  nonlinearity hypothesis test.
- The development-set positive findings (§5a), while statistically confident
  by this study's own 95% CI standard, are small in absolute/practical terms
  (Spearman r ≈ 0.02–0.03; mean differences of 0.28–0.36 relief runs per 9
  innings) and did not replicate out-of-sample — see §6A.
- `data/research_cache/bullpen_backtest/` stores a **compact extraction**
  (per-pitcher lines only, via `extract_pitcher_lines`), not raw MLB API
  payloads — keeps the committed cache small (39MB for all 5 seasons), per
  spec section 13's "do not create enormous noisy commits" instruction, at
  the cost of needing a versioned, tested extraction schema (documented in
  `lib/edgelab/backtest/bullpen_backtest_reconstruction.py`) rather than the
  raw wire format.
- 2026 is a partial season (through the latest completed date at run time,
  1,989 of an eventual ~2,430 games) — the locked holdout's own sample will
  grow if this experiment's cache is ever refreshed later in the season, but
  per this study's own rule the 2026 holdout must not be re-inspected or
  re-tuned against as it grows.
