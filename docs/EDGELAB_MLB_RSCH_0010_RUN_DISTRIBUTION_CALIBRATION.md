# EdgeLab Research Lab — MLB-RSCH-0010: Run Distribution / Probability Calibration

**Status: RESEARCH ONLY. No production model probability, feature, recommendation
logic, threshold, confidence tier, Bet Up To logic, Kalshi fee calculation,
bankroll/staking, market eligibility, lineup gate, slate output, risk gate,
settlement, or production cron behavior was changed. A finding here does not
auto-promote any of the above.**

## 1. Question

MLB-RSCH-0009 showed that better expected-run *inputs* help. This milestone
asks the complementary question: are we converting expected runs into
event *probabilities* with the correct scoring distribution? Current
research machinery relies on Poisson-style run modeling — if the
distribution is misspecified, a good mean estimate can still produce
systematically wrong probabilities for game totals, team totals,
moneyline, run lines, and alternate/tail markets. This milestone
deliberately separates **mean quality** (frozen, MLB-RSCH-0009's own
{offense, bullpen} proxy — never touched here) from **distribution
quality** (this milestone's entire scope), so a finding here can never
be confused with "a better mean projection."

## 2. Execution status

**Complete.** Ran against the same 10,204-game 2022–2026 baseball
corpus MLB-RSCH-0009 built (bounded only by that milestone's own
eligibility floor), plus a secondary stage reusing MLB-RSCH-0008/0009's
existing 828/834-game Pinnacle sample — **zero new Odds API credits
spent**.

**A real bug was caught and fixed during verification**: `poisson_pmf`
(reused unchanged from `scripts/build_market_ledger.py`) returns `0.0`
for any `lam<=0` at *every* `k`, including `k=0` — correct for `lam<0`
but wrong for the legitimate degenerate case `lam==0`, where a
Poisson(0) is a point mass at 0 (`P(X=0)=1`). This silently broke
`bivariate_poisson_joint_pmf` whenever the fitted correlation parameter
(or, at a boundary, one team's own independent component) was exactly
0 — which is the *expected* value whenever the dev-only fit floors a
non-positive empirical covariance to 0. Fixed with a local, zero-safe
Poisson substitute inside that one function's own convolution, without
touching the reused production `poisson_pmf`. Proven by a dedicated
regression test.

## 3. Preregistered specification

### 3a. Mean model (frozen, unchanged)

`lambda_home`/`lambda_away` for every row come from MLB-RSCH-0009's own
frozen `{offense, bullpen}` composition — reconstructed by calling that
milestone's own `load_all_team_games_with_venue`/`load_relief_er9_games`/
`build_season_rows`/`attach_stabilized_components`/
`fit_home_field_adjustment_for_components`/`baseline_for_components`
functions **unchanged**, feeding into `proxy_model.expected_runs`
**unchanged**. Reconstructed frozen constants matched MLB-RSCH-0009's
own committed values exactly: home-field adjustment **0.0114**,
league-average offense **4.4292** runs/game, league-average bullpen
**4.1248** ER/9. Not one line of offense/bullpen feature construction
was touched.

### 3b. Candidates (`lib/edgelab/backtest/run_distributions.py`, new)

- **D0 (control)**: independent Poisson marginals — proven, via a
  dedicated equivalence test, to reproduce `scripts/build_market_ledger.py`'s
  own `p_team_wins`/`p_over_total` exactly.
- **D1**: independent negative-binomial marginals, identical means,
  **one** dev-fit overdispersion parameter (closed-form method-of-moments:
  mean of `((actual−λ)² − λ) / λ²` pooled across home+away development
  rows).
- **D2**: bivariate Poisson (shared-environment construction) — a
  structural property of this construction (not fit, not estimated) is
  that its marginals are *exactly* Poisson(λ_home)/Poisson(λ_away),
  identical to D0's; only the joint/correlation differs. **One** dev-fit
  correlation parameter (closed-form method-of-moments: mean of
  `(actual_home−λ_home)(actual_away−λ_away)` over development rows).
- **D3**: not included. A genuinely parsimonious, cleanly-preregistrable
  combined overdispersion+correlation family would require a real
  bivariate negative-binomial (multiple competing parameterizations
  exist, none with as clean a single-parameter closed-form fit as D1/D2
  individually) — exactly the "would materially complicate
  interpretation or create tuning freedom" case the mission's own
  instruction says to skip.

Every derived probability (moneyline, game total, team total, margin)
is computed by **one shared, generic** set of functions applied to
whichever candidate's own joint distribution is passed in — not three
separate implementations that could silently disagree.

### 3c. Selection rule (preregistered, before any real result)

A candidate may become the final distribution only if, versus D0, its
primary-cells-averaged Brier delta is (1) negative on **development**,
(2) not degraded by more than `DEGRADATION_TOLERANCE=0.005` on
**validation**, and (3) improved in a **majority** (≥3 of 5) of the
individual primary cells on development — never confined to one cell.
Primary cells: `{game_result, game_total@7.5, @8.5, @9.5, @10.5}`.
Between D1 and D2, if both pass, the more negative development delta
wins. Pinnacle and 2026 are never consulted during selection.

## 4. Critical distribution diagnostics (development)

| | Empirical mean | Empirical variance | Poisson-implied variance |
|---|---|---|---|
| Home runs | 4.4414 | **9.3253** | 4.4414 |
| Away runs | 4.4310 | **10.3641** | 4.4310 |
| Game total | 8.8724 | **19.7159** | 8.8724 |

**Team-run variance is ~2.1–2.3× what Poisson implies — MLB scoring is
materially overdispersed relative to Poisson.**

**Home/away run correlation: 0.0014** — essentially zero. After
conditioning on expected runs, home and away scores show no meaningful
correlation in this sample.

| Team-run tail | Empirical | Poisson-implied | Ratio |
|---|---|---|---|
| Exactly 0 runs (shutout) | 6.61% | 1.37% | **4.8×** |
| Exactly 1 run | 10.52% | 5.80% | 1.8× |
| 5+ runs | 42.66% | 43.76% | ~1.0× |
| 7+ runs | 22.45% | 15.23% | **1.5×** |
| 10+ runs | 7.13% | 1.50% | **4.8×** |

**Poisson badly underestimates BOTH tails** — shutouts and blowouts are
each ~5× more common than Poisson predicts, while the middle of the
distribution (5+ runs) is nearly correctly predicted. This is the
textbook signature of overdispersion, not a mean-estimation problem.

## 5. Candidate fitting and development/validation results

**Fitted parameters (development-only, closed-form, frozen)**:
overdispersion = **0.281513**, correlation λ_c = **0.130999** (small,
consistent with the ~0 empirical correlation found in §4).

| Candidate | Dev aggregate Δ (vs D0) | Dev cells improved | Val aggregate Δ | Passes selection rule? |
|---|---|---|---|---|
| D1 (negative binomial) | **−0.002045** | 4/5 | −0.003063 | **YES** |
| D2 (bivariate Poisson) | −0.000121 | 4/5 | −0.000152 | YES (barely) |

Both candidates technically pass, but D1's improvement is an order of
magnitude larger than D2's — consistent with §4's finding that
overdispersion is real and substantial while correlation is nearly
zero. **D1 (negative binomial) is selected as the final distribution.**

## 6. Locked 2026 holdout — the key evidence

**Aggregate primary Brier delta (D1 minus D0): −0.003288, 95% CI
[−0.0042, −0.0024] — confidently negative, replicated exactly out of
sample.**

| Family | D0 Brier | D1 Brier | Delta |
|---|---|---|---|
| Game result (moneyline) | 0.255934 | 0.253142 | **−0.002792** |
| Game total @7.5 | 0.253706 | 0.245570 | **−0.008136** |
| Game total @8.5 | 0.251013 | 0.249283 | **−0.001730** |
| Game total @9.5 | 0.238746 | 0.238182 | −0.000564 |
| Game total @10.5 | 0.223156 | 0.219938 | **−0.003218** |
| Team total home@2.5 | — | — | **−0.0112** |
| Team total away@2.5 | — | — | **−0.017863** |
| Team total home@3.5 | — | — | **−0.004929** |
| Team total away@3.5 | — | — | **−0.010648** |
| Team total home@4.5 | — | — | +0.000193 (noise) |
| Team total away@4.5 | — | — | −0.001593 |
| Team total home@5.5 | — | — | −0.001741 |
| Team total away@5.5 | — | — | −0.001081 |
| Win by 2+ (run line ±1.5) | — | — | **−0.003262** |
| Win by 3+ | — | — | **−0.004543** |
| Lose by 2+ | — | — | **−0.003494** |

**The improvement is broad, not confined to one threshold**: 15 of 16
individual holdout cells improve (the one exception, home team-total
@4.5, is a negligible +0.0002, statistical noise). Calibration (ECE)
also improves substantially, not just Brier — e.g. game total @7.5's
ECE falls from 0.088 to 0.008, @8.5 from 0.035 to 0.010.

**Note on run lines**: for integer run scoring, a standard ±1.5 run
line is *mathematically identical* to "win/lose by 2+" — computed once
here, not duplicated as a separate market family.

## 7. Tail calibration on locked holdout — does D1 actually fix Poisson's error?

| Team-run tail | Empirical | D0 (Poisson) implied | D1 (final) implied |
|---|---|---|---|
| Exactly 0 (shutout) | 6.30% | 1.28% | **5.78%** |
| Exactly 1 | 11.04% | 5.54% | **11.30%** |
| 5+ | 42.47% | 44.75% | 41.20% |
| 7+ | 22.72% | 15.79% | **21.70%** |
| 10+ | 7.45% | 1.58% | **7.09%** |

**Yes — dramatically.** D1's negative-binomial marginal closes nearly
all of Poisson's tail-calibration gap at both extremes: shutout
frequency prediction improves from a 4.9× underestimate to within 8% of
the true rate; 10+-run games improve from a 4.7× underestimate to
within 5%. This directly explains *why* D1 wins — it is not a diffuse
aggregate-Brier artifact, it is a specific, mechanistic fix to a
severe, well-defined mispricing of extreme outcomes.

(D2's team-run tail predictions are, by the bivariate Poisson
construction's own structural property, identical to D0's — see
`bivariate_poisson_joint_pmf`'s docstring — so only D1 can ever change
a team-run tail prediction versus D0; not separately tabulated.)

Game-total tails (holdout): Over 7.5 in 56.15% of games, Over 12.5 in
21.78%, Over 14.5 in 12.18%. Margin tails: 5+ runs in 29.19%, 7+ runs
in 14.24% of games — both real, substantial mass in what a Poisson-only
model would underweight.

## 8. Secondary Pinnacle comparison (existing sample, no new acquisition)

| | D0+Poisson gap | D0(mean)+D1(dist) gap | Gap closed |
|---|---|---|---|
| Game ML | 0.008081 | 0.006431 | 0.001650 |
| Game total (exact Pinnacle line) | 0.004229 | **0.000843** | **0.003386** |

The distribution swap alone (holding the mean model fixed) closes the
game-total gap to Pinnacle by an additional ~80% on top of what
MLB-RSCH-0009's mean-model improvement already achieved — from
MLB-RSCH-0008's original 0.0060 gap, the combined effect of a better
mean (MLB-RSCH-0009) and a better distribution (this milestone) leaves
only **0.000843** separating the historical proxy from Pinnacle on
totals. The moneyline gap narrows much less (as expected — D1's own
holdout gain was smaller and less confident for game result than for
totals in §6).

## 9. Decision

**A. Is MLB scoring materially overdispersed relative to Poisson?**
Yes — team-run variance is ~2.1–2.3× the Poisson-implied value, with a
clean, textbook signature (both tails underestimated by ~5×).

**B. Are home/away scores meaningfully correlated after conditioning on
expected runs?** No — empirical correlation is 0.0014, essentially
zero; the fitted correlation parameter (0.13) is small and D2's
resulting improvement is an order of magnitude smaller than D1's.

**C. Which candidate best predicts held-out outcomes?** D1 (negative
binomial), decisively.

**D. Does it survive 2025 validation?** Yes — validation aggregate
delta −0.003063, consistent with development.

**E. Does it survive locked 2026?** Yes — confidently: 95% CI
[−0.0042, −0.0024], excludes zero.

**F. How much does it improve each family?** Broad and consistent:
game total the strongest (up to −0.0081 at @7.5), team totals mostly
strong (7/8 cells meaningfully improved), margins/run-lines
consistently improved (−0.003 to −0.0045), moneyline improved but more
modestly (−0.0028).

**G. Does it improve the markets where Kalshi gives unique
opportunity?** Alternate totals and tail-sensitive markets (extreme
game totals, large margins) are exactly where this milestone's finding
is strongest — the severe tail miscalibration D1 fixes (§7) is directly
the kind of mispricing that would most affect alternate-total and
extreme-margin market pricing. Notably, run lines (RL_Away/RL_Home) —
one obvious candidate for "Kalshi-unique opportunity" — are currently
`Rejected` unconditionally in the real-money ledger (Rule 81, suspended
after a measured 36% win rate/−4.09% CLV) and only priced in a
PAPER-only pipeline today (§10); this milestone's margin-probability
improvement (§6, win-by-2+/3+ deltas of −0.003 to −0.0045) is directly
relevant evidence for whether that suspended family deserves
reconsideration, though re-enabling it is a separate, deliberate
decision this milestone does not make.

**H. Does it close more of the Pinnacle gap?** Yes, substantially for
totals (~80% additional closure, §8); modestly for moneyline.

**I. Is the result strong enough to justify prospective shadow?** The
holdout evidence is real, replicated, and mechanistically explained
(not a spurious aggregate artifact) — see §11 for the recommended next
step and its own caveats.

**Overall classification: `MODERATE DISTRIBUTION IMPROVEMENT`** (holdout
aggregate delta magnitude 0.0033, confidently resolved, broad across
15/16 cells — clearly beyond MINOR, not large enough to call MAJOR
given the still-modest moneyline effect).

## 10. Production mapping (read-only inventory — no changes made)

**1. Which market families currently use Poisson?**

`scripts/build_market_ledger.py` (the real-money ledger, run nightly by
`.github/workflows/fetch-slate.yml`) is the central engine:
`p_team_wins(away_proj, home_proj)` → **game moneyline only**
(ML_Away/ML_Home). `p_over_total(proj, line)` → both **Game Total**
and **Team Total Over** (each team's own total is the same single-team
Poisson tail sum, just called with that team's own `proj`; a v1.2 fix
documented in that file corrects a real off-by-one in Kalshi's
`tt_line` convention — `p_over_total(proj, tt_line - 1)`, not
`tt_line`, since Kalshi's ticker digit N means "≥N," not "&gt;N").
**NRFI/YRFI** uses Poisson differently — not the double-sum joint, but
the simpler product of two independent Poisson(0) point masses
(`p_nrfi = poisson_pmf(0, inning1_home_lambda) * poisson_pmf(0, inning1_away_lambda)`),
fed by dedicated first-inning lambdas from
`lib.research.first_inning_context`. **F3/F5/F7 three-way winner**
markets use a separate module, `lib.research.three_way_projection`
(its own re-implemented `poisson_pmf`, same independent-Poisson
double-sum, but keeps the tie cell) — hard-imported into
`build_market_ledger.py`, despite that module's own docstring still
(stale) claiming it is "never imported by any production script."

**2. Which use Skellam or derived Poisson assumptions?** Zero hits for
"skellam" anywhere in this repository, production or research. What
production actually does — computing P(away−home) outcomes via the
double-sum product of two independent Poisson PMFs — is mathematically
the Skellam-difference construction, but it is always built from the
raw joint grid, never a named/closed-form Skellam PMF. This
milestone's own margin functions (`margin_at_least_prob`) follow the
same direct-summation convention, not a closed-form Skellam
implementation either.

**3. Which rely on independent team-score assumptions?** All of them,
without exception. **Run lines (RL_Away/RL_Home) are the one market
family where this doesn't matter today** — they are unconditionally
`Rejected` in the real-money ledger under "Rule 81" (suspended after a
measured 36% win rate / −4.09% CLV), with no Poisson evaluation run at
all for that family in the real-money path. A separate, **PAPER-only**
pipeline (`lib.kalshi_probability_adapters.p_wins_by_over`, via
`scripts/discover_kalshi_mlb_markets.py` → `scripts/build_paper_spread_ledger.py`,
a second scheduled workflow) does compute winning-margin probabilities
from the same independent-Poisson joint — explicitly
`trackingType: "PAPER"`, `countsTowardBankroll: False`, never
real-money-eligible. Grepping for correlation/bivariate/joint near any
production probability code turns up only `scripts/risk_gate.py`'s
`CORRELATION_RULES`/`evaluate_correlation_gate` — that is a
portfolio/bet-*thesis* correlation gate (capping combined stake across
markets on the same game), an entirely different concept from
team-*score* correlation. No production code models covariance between
home and away run distributions.

**4. Which probability outputs would theoretically change if a
candidate like D1 were eventually promoted?** Every Poisson-derived
production probability: game moneyline, game total, team totals,
NRFI/YRFI (all via `p_team_wins`/`p_over_total`/the NRFI product in
`scripts/build_market_ledger.py`), F3/F5/F7 winner markets (via
`lib.research.three_way_projection`), and the PAPER-only winning-margin
pricing (via `lib.kalshi_probability_adapters.p_wins_by_over`). The
full production blast-radius chain: `scripts/build_market_ledger.py`
→ `scripts/build_projection_board.py` →
`scripts/discover_kalshi_mlb_markets.py`/`lib/kalshi_probability_adapters.py`
→ `lib/kalshi_projection_board.py` → `scripts/validate_slate_final.py`.

No production code was changed by this milestone. This mapping is
informational only and does not itself authorize any production
change.

## 11. Next steps (not executed by this milestone)

Per the mission's own explicit instruction: a better historical
distribution does **not** immediately authorize production replacement.
If this track is pursued further, the correct sequence is: historical
confirmation (this milestone, done) → current-model shadow probability
comparison → prospective shadow → a deliberate, separately-reviewed
production-promotion PR. No automatic promotion follows from this
report.

## 12. Limitations

- The overdispersion/correlation parameters are each a single,
  league-wide, dev-fit constant — no home/away split, no team-specific
  variance, no season-specific refit. A more granular (but still
  parsimonious) parameterization might perform differently; untested
  here by design.
- D2's near-zero correlation finding is specific to *this* mean model
  (MLB-RSCH-0009's frozen {offense, bullpen} proxy) — a richer or
  different mean model could in principle leave more residual
  correlation for D2 to capture. Not something this milestone can
  distinguish.
- D3 (combined overdispersion + correlation) was not attempted — see
  §3b's own reasoning. A cleaner combined family, if one exists, is
  future work.
- The one non-improving holdout cell (home team-total @4.5, +0.0002)
  is noise, not a systematic weakness, but is reported rather than
  hidden.
- Tail definitions were fixed before any result was inspected, but the
  choice of which specific thresholds to preregister (0/1/5+/7+/10+ team
  runs, 7.5/12.5/14.5 game total, 5+/7+ margin) still reflects this
  milestone's own judgment about which tails matter for Kalshi-relevant
  alternate markets.
- Evidence level `E2_PIT_HISTORICAL`, same basis as MLB-RSCH-0008/0009
  — not a claim of exact historical production-model evidence, and not
  itself an authorization for any production change (§11).

## 13. Reproducibility / tests

- Preregistration-first: `register_experiment()` proven (AST-based,
  depth-first source-order check) to be the literal first call in
  `main()`.
- Exact reuse of the frozen MLB-RSCH-0009 mean model: proven by source
  inspection (no reimplementation of `offenseRunsPerGame`/
  `runPreventionRunsAllowedPerGame` arithmetic anywhere in this
  milestone's own row-building function) and by the reconstructed
  frozen constants matching MLB-RSCH-0009's committed values exactly.
- Development-only distribution fitting, validation never refits,
  holdout inaccessible during selection, no Pinnacle use during
  selection: all proven by source-segment inspection of the
  forward-selection block and the Pinnacle-stage code's position in
  `main()`.
- Probability mass sums correctly, numerical stability, moneyline
  complement consistency, total Over/Under complement consistency,
  margin probability consistency, tail calculations deterministic: all
  proven across D0/D1/D2 simultaneously in
  `tests/edgelab/test_run_distributions.py`.
- D0 exactly reproduces production's own `p_team_wins`/`p_over_total`;
  D2's marginals are structurally proven identical to D0's regardless
  of the correlation parameter; the real zero-dispersion/zero-correlation
  edge-case bug (§2) is covered by regression tests.
- No production files modified: `git diff origin/main...HEAD --name-only`
  scoped to `lib/edgelab/`, `scripts/edgelab/`, `tests/edgelab/`,
  `data/edgelab/`, and `docs/` only.
- 79 new tests (54 in `test_run_distributions.py`, 25 in
  `test_run_distribution_calibration_experiment_script.py`) plus the
  full pre-existing suite re-run clean.
