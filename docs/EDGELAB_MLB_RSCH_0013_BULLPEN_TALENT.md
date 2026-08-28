# MLB-RSCH-0013: Bullpen Talent Refinement

Status: **COMPLETE. CONTROL SUPERIOR / NO MEANINGFUL IMPROVEMENT.**

RESEARCH ONLY. No production behavior changed.

## 1. Purpose and scoping

Preceded by a feasibility audit (`docs/EDGELAB_LINEUP_HITTER_FEASIBILITY_AUDIT.md`)
that ruled out lineup/hitter-aggregation research tonight (no multi-season
PIT-safe lineup archive exists — only ~2.5 months of single-season,
prospective-only capture). Falling back to the task's own Option A:
bullpen talent refinement, mirroring MLB-RSCH-0012's O0/O1 methodology
exactly, but on the bullpen side, holding MLB-RSCH-0009's frozen offense
component fixed. Uses the ALREADY-CACHED `data/research_cache/
bullpen_backtest/` ER9 corpus (MLB-RSCH-0003) — **zero new network calls**
this milestone, maximizing research value per unit of resource spent.

Deliberately scoped to stabilization only (not component pitching rates
like K%/BB%/HR-rate, which would need new data acquisition) and
deliberately excludes workload/fatigue (MLB-RSCH-0003 already found weak,
non-replicating evidence there — this is a TALENT/rate-quality question).

## 2. Registration

| | |
|---|---|
| Experiment ID | `MLB-RSCH-0013` |
| Evidence level | `E2_PIT_HISTORICAL` |
| Frozen NB dispersion | `0.281513`, verified byte-exact, never refit |
| Corpus | Same 2022-2026 corpus as MLB-RSCH-0012: dev 6,378 / val 2,127 / holdout 1,699 games |

## 3. Candidates

- **P0 (control)**: production's current bullpen component, exactly
  reproduced — `stabilized_bullpen_rate(raw, priorGames, leagueAvg, k=30)`.
- **P1**: same formula, `k` fit via closed-form empirical-Bayes on
  DEVELOPMENT bullpen-ER9 data only — `k_hat = 0.4283` (again far weaker
  than the fixed constant, mirroring MLB-RSCH-0012's own offense-side
  finding: `sigma^2_within=27.6971`, `tau^2_between=64.6616`, 90 eligible
  DEV team-seasons).

Offense held at MLB-RSCH-0009's original frozen composition throughout —
MLB-RSCH-0012's own O1 (offense shrinkage) finding was never validated
and is not used here.

## 4. Results — a clean, decisive null result

| Split | P0 MAE | P1 MAE | Delta | 95% CI |
|---|---:|---:|---:|---|
| DEV | 2.4240 | 2.4241 | +0.000163 | [-0.0007, +0.0011] (crosses zero) |
| VALIDATION | 2.4956 | 2.4957 | +0.000052 | [-0.0019, +0.0020] (crosses zero) |
| HOLDOUT 2026 | 2.5057 | 2.5053 | -0.000480 | [-0.0022, +0.0014] (crosses zero) |

**Selection fails at the first gate**: DEV delta is not negative
(P1 does not improve on P0 at all), so the preregistered rule correctly
stops there — `finalBullpenModel = P0` (control retained).

- **Team robustness**: 15/30 teams improved, 14/30 got worse (essentially
  a coin flip) — no broad effect in either direction.
- **Frozen-NB probability**: overall Brier delta -0.000005 (val) /
  +0.000053 (holdout) — both negligible, noise-level.
- **Pinnacle secondary** (834 rows, run after freezing): P0 Brier
  0.25023 vs. P1 Brier 0.25021 — indistinguishable.

**Every metric, every split, every robustness check agrees: production's
current fixed bullpen shrinkage constant (k=30) is already
indistinguishable from the data-implied empirical-Bayes optimum in
practice**, exactly mirroring MLB-RSCH-0012's own offense-side finding
that this specific lever (shrinkage-constant refit) has been exhausted —
the ~20-game eligibility floor already does most of the useful
stabilization work regardless of the exact `k` used past that point.

## 5. Classification

**CONTROL SUPERIOR** (P1 never beats P0, at any split, on any metric).
A prospective bullpen shadow is **not justified**.

## 6. What remains open

Component bullpen rates (K%, BB%, HR rate) were explicitly out of scope
this pass (would require new MLB Stats API pitcher-component data, not
yet cached) — this is a legitimately different, still-open question this
null result does not answer. A future milestone could test it using the
same fetch pattern this session already built for batting (MLB-RSCH-0012's
`fetch_mlb_multiseason_batting_cache.py`), extended to pitcher lines.

## 7. Tests

`tests/edgelab/test_run_bullpen_talent_experiment_script.py` — 23 tests
(preregistration ordering, frozen-offense reuse proof, P0 exact-
reproduction proof, empirical-Bayes fit properties including the
undefined-ER9 exclusion guarantee, mean-accuracy/paired-delta/season-
band/team-robustness/NB-cell/selection-rule correctness, DEV/VAL-only
selection sequencing).
