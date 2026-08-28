# MLB-RSCH-0012: Offensive Talent Estimation

Status: **O0/O1 COMPLETE (real 2022-2026 results). O2/O3/O4 BLOCKED** on the
new multi-season batting-boxscore acquisition workflow landing on `main`
(GitHub only allows dispatching a `workflow_dispatch` workflow once it
exists on the default branch — see section 5).

RESEARCH ONLY. No production behavior changed.

## 1. Purpose

MLB-RSCH-0009 found that a better offense proxy and bullpen quality both
improved the expected-run mean model, and the improvement survived 2026.
MLB-RSCH-0010/0011 then showed the *distribution* layer on top of that mean
compounds real gains. This milestone asks the natural next question:
**can offense quality itself be made materially better** — holding
MLB-RSCH-0009's frozen bullpen component fixed, and scoring every candidate
both on direct mean accuracy and on probability quality under the frozen
MLB-RSCH-0010 negative-binomial distribution (dispersion never refit).

## 2. Registration

| | |
|---|---|
| Experiment ID | `MLB-RSCH-0012` |
| Control | `mlb_rsch_0012_offense_talent_control_v1` |
| Evidence level | `E2_PIT_HISTORICAL` (same basis as MLB-RSCH-0008/0009/0010) |
| Frozen NB dispersion | `0.281513` — verified byte-exact against `data/edgelab/analytics/latest_mlb_rsch_0010_run_distribution.json` at import time; never refit here |
| Primary metric | paired MAE delta on next-game team runs scored (candidate minus O0), game-and-team-clustered 95% CI |
| Chronological split | DEV 2022-2024, VAL 2025, HOLDOUT 2026 (locked) |
| Corpus scale | **10,204 games → 20,408 team-observations** (dev 6,378 / val 2,127 / holdout 1,699 games) — matches the ~20,000 team-games target |

## 3. Candidates

- **O0 (control)**: production's current offense component, reproduced
  EXACTLY — `stabilized_offense_rate(raw, priorGames, leagueAvg, k=30)`,
  the fixed (never dev-fit) shrinkage constant `lib/edgelab/backtest/
  proxy_enrichment.py` already uses. `test_o0_uses_current_fixed_shrinkage_constant_unchanged`
  proves byte-identical reuse.
- **O1**: the SAME shrinkage formula, but `k` is a genuine closed-form
  empirical-Bayes estimate fit on DEVELOPMENT team-seasons only —
  `k_hat = sigma^2 / tau^2` (within-team-game variance over between-team
  talent variance, standard normal-normal conjugate shrinkage constant).
- **O2/O3/O4 (component / stabilized-component / opponent-adjusted
  batting offense)**: **not evaluated this pass** — see section 5.

## 4. O0 vs O1 — real, complete results

### Empirical-Bayes fit (DEV only)

`k_hat = 0.2492` (vs. production's current fixed `k = 30`) — fit from 90
eligible DEV team-seasons, `sigma^2_withinTeamGame = 9.6896`,
`tau^2_betweenTeamTalent = 38.8872`. **The data-implied optimal shrinkage
is dramatically WEAKER than production's current constant** — teams'
real talent differences (tau^2) are large relative to game-to-game noise
(sigma^2), so heavy shrinkage toward league average loses real signal, in
principle. In practice (next section) this barely matters, because
MLB-RSCH-0009's own `MIN_PRIOR_GAMES_FOR_BASELINE = 20` eligibility floor
already means every row has a reasonably stable ≥20-game raw estimate
before any shrinkage is even applied.

### Mean accuracy (MAE on next-game team runs, O1 minus O0)

| Split | O0 MAE | O1 MAE | Paired delta | 95% CI |
|---|---:|---:|---:|---|
| DEV | 2.4240 | 2.4227 | -0.001268 | [-0.0026, 0.0001] |
| VALIDATION | 2.4956 | 2.4930 | -0.002710 | [-0.0051, -0.0000] |
| **HOLDOUT 2026** | 2.5057 | 2.5089 | **+0.003189** | **[+0.0008, +0.0056]** |

The DEV/VAL deltas are negative (small improvement) and pass the
preregistered selection gate (see section 6). **The 2026 holdout delta
reverses sign, and its CI excludes zero** — a small but real degradation,
not noise. This is reported honestly rather than selectively.

### Season-progress bands (DEV)

`games_1_15` is **structurally empty (n=0)** at every split — MLB-RSCH-0009's
own `MIN_PRIOR_GAMES_FOR_BASELINE = 20` eligibility rule (reused unchanged)
means no eligible row ever has fewer than 20 prior games. This is a real
limitation of this preregistered band scheme given the eligibility floor
it inherits, not a bug — documented rather than silently worked around.

| Band | n | MAE delta | 95% CI |
|---|---:|---:|---|
| games_1_15 | 0 | n/a | n/a |
| games_16_40 | 1,843 | +0.00207 | [-0.0047, +00.0089] (not significant) |
| games_41_80 | 3,597 | -0.00075 | [-0.0034, +0.0018] (not significant) |
| games_81_plus | 7,296 | -0.00236 | [-0.0033, -0.0014] |

The only significant band is the LATE-season one — the opposite of what
"shrinkage matters most early" would predict, since the early band is
unobservable here.

### Team robustness (DEV)

**13 of 30 teams improved, 17 of 30 got worse** under O1 — the aggregate
improvement is a slim-majority-weighted average, not a broad consensus.
Leave-one-team-out range is narrow (-0.0015 to -0.0004), so no single team
is driving the aggregate result, but the near-even team split is itself a
meaningful caveat against a strong "O1 is simply better" claim.

### Frozen-NB probability evaluation (dispersion unchanged)

| | VALIDATION overall delta | 2026 HOLDOUT overall delta |
|---|---:|---:|
| Brier | +0.000023 (negligible) | **+0.000358** |
| Log loss | +0.000061 (negligible) | +0.000799 |

On 2026 holdout, **every family** (game_total, moneyline, team_total_away,
team_total_home) is positive (worse) under O1, by small but consistent
amounts (+0.00025 to +0.00041 Brier). Validation-stage deltas are
essentially noise-level and mixed in sign.

### Pinnacle secondary check (existing MLB-RSCH-0008/0009 sample, 834 rows, run AFTER selection/holdout — never used for selection)

| | O0 proxy-minus-Pinnacle Brier | O1 proxy-minus-Pinnacle Brier |
|---|---:|---:|
| ML | 0.007367 | 0.008140 |

O1's gap versus Pinnacle is **slightly wider**, not narrower — O1 does not
close additional sharp-market gap; if anything it widens it marginally.

## 5. O2/O3/O4 — blocked on data acquisition

This repository's own CI/local dev environments have **no outbound network
access to statsapi.mlb.com** (reconfirmed this milestone, matching
MLB-RSCH-0003's original finding: `gateway answered 403 to CONNECT`).
Building component-batting candidates (BB/K/HR/XBH rates, OBP/SLG proxies)
requires a new multi-season team batting-boxscore cache, which can only be
fetched on GitHub-hosted runners.

Built and committed this milestone (all fully tested — see section 8):

- `lib/edgelab/backtest/team_batting_reconstruction.py` — PIT-safe
  extraction (`extract_team_batting_line`) and derived-rate computation
  (`derived_batting_rates`: bbRate, kRate, hrRate, xbhRate, obpProxy,
  sluggingProxy, isoProxy — every rate a transparent ratio of officially
  reported counts, never a wOBA-style linear-weights approximation, per
  the mission's own instruction).
- `scripts/edgelab/backtest/fetch_mlb_multiseason_batting_cache.py` — reuses
  MLB-RSCH-0003's ALREADY-CACHED 2022-2026 team schedules read-only (no
  re-fetch); only new per-game boxscore fetches are needed.
- `.github/workflows/research-multiseason-batting-backtest.yml` — manual
  `workflow_dispatch`, mirrors `research-multiseason-bullpen-backtest.yml`'s
  exact safety pattern (protected-branch refusal guard, commits only to
  the dispatching research branch, never `main`).

**Why it hasn't run yet**: GitHub only allows dispatching a
`workflow_dispatch`-triggered workflow once its YAML file exists on the
repository's *default* branch — a workflow that exists only on a feature
branch returns `404 Not Found` on dispatch. This is a GitHub platform
constraint, not a design choice. Since pushing directly to `main` outside
normal PR review is a class of action this session does not take
unilaterally, this workflow will become dispatchable once this PR (or a
small separate infra PR carrying just the workflow file) is reviewed and
merged. **O2/O3/O4 are ready to implement the moment the cache exists** —
this doc will be updated in a follow-up push once that happens.

## 6. Selection (preregistered, DEV+VAL only, holdout/Pinnacle never consulted)

Per the preregistered rule (module docstring, `selection_passes`): O1
**mechanically passes** — DEV MAE improved, VAL degradation well within
tolerance, improvement not confined to the (empty) `games_1_15` band, VAL
frozen-NB primary delta within tolerance. **Final offense model per the
mechanical rule: O1.**

**However**, the rule deliberately never consults 2026 holdout or Pinnacle
during selection — by design (no holdout-driven tuning). Examined
*after* freezing, both the 2026 holdout (significant MAE reversal, every
NB-probability family worse) and the Pinnacle secondary check (wider gap,
not narrower) argue the DEV/VAL improvement is small, inconsistent across
teams, and does not survive genuinely out-of-sample evidence. This is
reported as the honest overall picture, not hidden behind the mechanical
pass — see section 9's classification.

## 7. Production offense mapping (read-only; no production code changed)

Production's real, currently-live offense signal
(`scripts/enrich_data.py::compute_offense_baseline`) is **already
substantially more sophisticated** than either O0 or O1 tested here:

```
raw_blend = L7*0.30 + L15*0.30 + Season*0.40          (fixed weights)
bayesian  = (15*raw_blend + 20*LEAGUE_AVG_RPG) / 35    (FIXED-weight shrinkage)
final     = bayesian + oppQualityAdj + lineupAdj        (opponent- and lineup-adjusted)
```

Concrete comparison to this milestone's own candidates:

| Signal | Production | This experiment |
|---|---|---|
| Recency blend (L7/L15) | YES, fixed 30/30/40 weights | NOT tested (MLB-RSCH-0005 found recency windows carry no useful signal *in the research proxy path* — production's blend is a different mechanism, not directly contradicted or confirmed here) |
| Shrinkage toward league average | YES, but **FIXED** 15-vs-20 weight regardless of games played | O0: FIXED k=30 (same fixed-weight shape). O1: **sample-size-ADAPTIVE** k=0.2492 (shrinkage strength changes as `priorGamesThisSeason` grows) — production's shrinkage is not adaptive on this axis at all, a genuine, concrete, actionable difference |
| Opponent-quality adjustment | YES (`oppQualityAdj`, rolling 15-game opponent xFIP) | O4 not built this pass; production already does something in this spirit |
| Component batting stats (Savant xwOBA/FB%/BB%/K%/hardHit/barrel) | YES, captured (`teamWOBA`, `teamFBPct`, etc.) — not shown wired into `compute_offense_baseline` itself in the code read this pass | O2/O3 (blocked, see section 5) test a transparent, PIT-safe-reconstructable version of this same idea |
| Lineup-level adjustment | YES (`lineupAdj`, confirmed-lineup wOBA delta) | Explicitly out of scope — this is TEAM baseline talent, not lineup-specific forecasting (mission's own instruction) |

**Does this experiment SUPPORT / CONTRADICT / NOT DIRECTLY INFORM
production?** NOT DIRECTLY INFORM, with one exception: the shrinkage-
adaptivity finding (production's fixed 15/20 weight vs. this experiment's
sample-size-adaptive `k`) is a concrete, well-evidenced observation a
future production review could act on — though this milestone's own O1
result (adaptive k barely changes accuracy and reverses on 2026 holdout)
argues that difference alone is not obviously worth pursuing in
production either.

## 8. Tests

- `tests/edgelab/test_run_offense_talent_experiment_script.py` — 28 tests (preregistration ordering, O0 exact-reproduction proof, O1 empirical-Bayes fit properties, mean-accuracy/paired-delta/season-band/team-robustness/NB-cell/selection-rule correctness, DEV/VAL-only selection + holdout/Pinnacle sequencing proofs).
- `tests/edgelab/test_team_batting_reconstruction.py` — 17 tests (batting-line extraction, derived-rate computation, PIT-safe game attachment, `season_to_date_rate` field-agnostic reuse proof).
- `tests/edgelab/test_fetch_mlb_multiseason_batting_cache.py` — 4 tests (schedule reuse, missing-schedule handling, idempotent rerun, non-fatal per-game failure).
- Full `tests/` suite: see PR for current pass count (unchanged pre-existing environment-only failures expected — shallow-clone SHA-pinned scope tests, unrelated to this branch).

## 9a. Starter-quality feasibility (lightweight, in parallel — not a full experiment)

**Verdict: NO** — a genuinely PIT-safe, multi-season-scale, announced/
probable starting-pitcher identity dataset is **not** available from MLB
Stats API's schedule endpoint for historical dates, and this milestone did
not build a starter experiment on top of it.

This reuses MLB-RSCH-0009's own already-run, already-committed probe
(`scripts/edgelab/backtest/probe_starter_identity_pit_safety.py`, result
at `data/research_cache/sharp_market_probe/starter_identity_probe_result.json`)
rather than re-investigating from scratch: 28 dates sampled across
2022-2026, 668 comparable rows, comparing `schedule?hydrate=probablePitcher`
against the boxscore-CONFIRMED starter for the same historical game. Real
pregame-announced-vs-actual-starter mismatches run a documented few percent
to low double digits over a season; this probe found a **0.6% mismatch
rate (4/668)** — implausibly low, the signature of an endpoint that
backfills/echoes the FINAL confirmed starter for past dates rather than
preserving a genuine pregame record. Verdict:
`STARTER_IDENTITY_NOT_PIT_SAFE_AT_SCALE`.

This does not contradict MLB-RSCH-0011's own recommended path
(`pitcher_statcast_raw_archive`, `RECONSTRUCTABLE_FROM_DATED_RAW`,
E2-safe) — that pathway reconstructs pitch-level CHARACTERISTICS from
archived Statcast data (not schedule-endpoint starter identity), and
remains the viable, if currently shallow (~15 gameDates), route for a
future starter-quality experiment. See MLB-RSCH-0013 recommendation below.

## 9. Final questions and classification

See the final report's item-by-item answers (A-L) delivered alongside this
PR. Headline: **NO MEANINGFUL IMPROVEMENT** — O1 mechanically passes the
preregistered DEV/VAL selection gate, but the effect size is tiny
(thousandths of a run), only 13/30 teams improve, and the improvement
**reverses with a significant confidence interval on the 2026 locked
holdout** and **widens (not narrows) the Pinnacle gap**. A prospective
offense shadow is **not justified** by this evidence. O2/O3/O4 (component
batting) remain the more promising, not-yet-tested direction — see
section 5.

## 10. Recommended next experiment

**MLB-RSCH-0013: finish O2/O3/O4 (component batting offense) once the
batting boxscore cache lands**, rather than a new starter-quality
experiment (feasibility for the schedule-endpoint approach is a confirmed
NO — section 9a). If a genuine starter-quality experiment is wanted
later, the viable path is `pitcher_statcast_raw_archive`
(RECONSTRUCTABLE_FROM_DATED_RAW, E2-safe) once its archive depth grows
materially beyond the current ~15 gameDates — not yet, per MLB-RSCH-0011's
own caveat.
