# EdgeLab Research Lab — MLB-RSCH-0008: Proxy Model vs. Historical Pinnacle

**Status: RESEARCH ONLY. No production model probability, feature, recommendation
logic, threshold, confidence tier, Bet Up To logic, Kalshi fee calculation,
bankroll/staking, market eligibility, lineup gate, slate output, risk gate,
settlement, or production cron behavior was changed. A finding here does not
auto-promote any of the above.**

## 1. Question

The first true multi-season market backtest in this program. Does a
strictly PIT-safe, historically reconstructed MLB proxy model add
predictive value relative to historical Pinnacle fair probabilities, over
a large multi-season sample? This is explicitly **not** a claim that the
proxy recreates production's actual historical probability — Milestone
2's own PIT audit found season-aggregate team offense/starter quality/
bullpen talent `UNAVAILABLE_HISTORICALLY`. The proxy is a modest,
transparent, historical research model built entirely from components
already proven PIT-safe reconstructable in this program, evaluated
against real, timestamped Pinnacle market data.

## 2. Execution status

**Complete.** Phase A data validation, real historical Pinnacle
acquisition (5 seasons via GitHub Actions), and the full paired
proxy-vs-Pinnacle analysis all ran against real data.

**A real bug was caught and fixed during verification** (documented
honestly, not glossed over): the first draft of `robustness_checks`'
season-by-season breakdown reconstructed each row's proxy/Pinnacle/
outcome dictionary keys by string-formatting a `market_label` (e.g.
`f"proxy{market_label}Prob"` → `"proxyMlProb"`), but the actual keys
`enrich_row` sets are `"proxyMlHomeProb"`/`"pinnacleMlHomeFair"`/
`"actualHomeWin"` (moneyline) and `"proxyTotalOverProb"`/
`"pinnacleTotalOverFair"`/`"actualOver"` (totals) — a mismatch that
silently produced `n=0` for every one of the three development seasons
(2022/2023/2024) in the season-by-season robustness check, despite the
pooled development result showing n=522. **Fixed** by passing the same
explicit `proxy_key`/`pinnacle_key`/`outcome_key` triple every other
analysis function in this script already takes, instead of reconstructing
them from a label string. The corrected run produces real, non-zero
season-by-season results for all three development seasons (see §7).

## 3. Preregistered specification

### 3a. What's reused vs. genuinely new

Reused unchanged (imported, not reimplemented):
- `lib.edgelab.backtest.bullpen_backtest_reconstruction.extract_team_games_from_schedule`
  and MLB-RSCH-0003's own schedule cache — the same cache read-only by a
  fourth consumer, zero new MLB-side fetch.
- MLB-RSCH-0005's `season_to_date_rate`/prior-games eligibility rule
  (≥20 games) for team offense/run-prevention baselines.
- `scripts/build_market_ledger.py`'s `p_team_wins`/`p_over_total`/
  `poisson_pmf` — the same reuse MLB-RSCH-0002 already established —
  UNCHANGED, for the proxy's Poisson win/total probabilities.
- `lib.edgelab.paired_evaluation.pair_eligible_observations`/
  `evaluate_probability_model_pair` — the same MODEL-vs-MARKET
  repurposing MLB-RSCH-0001 established, here repurposed a second time
  as PROXY-vs-PINNACLE.
- `clv_update.py`'s `to_abbr`/`TEAM_TO_ABBR` for team-name matching
  between Odds-API event payloads and the MLB schedule cache.

Genuinely new: `lib/edgelab/backtest/pinnacle_reconstruction.py`
(per-game closest-valid-pregame-snapshot selection, two-sided de-vig,
exact-line total matching), `lib/edgelab/backtest/proxy_model.py`
(`team_baseline`, `expected_runs`, `fit_home_field_adjustment` — the
proxy's own one dev-fit parameter), and
`scripts/edgelab/run_proxy_vs_pinnacle_experiment.py`'s orchestration,
disagreement-band/direction/price-band/calibration/robustness analysis.

### 3b. Snapshot timing rule (Phase A item 1)

Preregistered before any acquisition: for every game, select the Pinnacle
snapshot with the smallest positive `minutesBeforeStart` within
`(0, MAX_MINUTES_BEFORE_START]`, `MAX_MINUTES_BEFORE_START = 60`. Never
post-start, never in-progress, never a later game's snapshot substituted
for an earlier one. Proven by 29 tests in
`tests/edgelab/test_pinnacle_reconstruction.py`, including explicit
"never post-start"/"never a different game's snapshot" cases.

### 3c. Pinnacle fair probability

Moneyline: `american_to_implied_probability` on both sides, then
`devig_two_sided` (proportional method, sums to exactly 1.0). Totals:
`matched_total_line` requires the exact same line quoted on both Over
and Under before any comparison is eligible — never a cross-line
comparison (e.g. model's Over 8.5 vs. Pinnacle's Over 9 is never paired).

### 3d. Proxy model design

Game moneyline and game total both derive from one `expected_runs()`
call: each team's season-to-date offense rate averaged with the
opponent's season-to-date run-prevention rate (standard sabermetric
combination), plus a single closed-form home-field runs adjustment
(`fit_home_field_adjustment`, the mean residual between actual and
naive-predicted home-minus-away run differential — fit ONCE on
development rows only, frozen before validation/holdout, never
iterative, never coefficient-hunted). `game_ml_proxy_probability`/
`game_total_proxy_probability` feed those expected runs into production's
own unchanged Poisson math (`p_team_wins`/`p_over_total`) to produce a
full scoring distribution, not a single point estimate — giving
`P(Over X)`/`P(Under X)` at Pinnacle's exact historical line. No starter
or bullpen recency baseline was incorporated in this first proxy version,
per the mission's explicit "keep the proxy modest" instruction.

### 3e. Model fitting rules

Development = 2022–2024, validation = 2025, locked holdout = 2026 — the
originally-planned split, confirmed usable by Phase A (§4). The one
dev-fit parameter (home-field runs adjustment,
**-0.1107 runs**) was fit exclusively on 2022–2024 rows
and applied identically, frozen, to validation and holdout. 2026 was
never touched during fitting, feature selection, or the choice of count
distribution.

## 4. Phase A data validation (before acquisition)

Run via GitHub Actions (`scripts/edgelab/backtest/probe_phase_a_validation.py`,
workflow run `33125988263`, real API calls, real data):

**2025 resolution** — CONFIRMED reachable. All 5 representative dates
(2025-04-15, 05-15, 06-20, 07-15, 08-15) returned real historical events:
9, 2, 13, 1, and 11 events respectively (36 total). `VALIDATION_SEASONS
= [2025]` required no revision — the originally planned split held.

**F5 empirical coverage** — NOT available. All 3 test events
(2024-06-10/11/12, `h2h_1st_5_innings`/`spreads_1st_5_innings`/
`totals_1st_5_innings` requested) were reachable, but **0 of 3** returned
any F5 market in the historical snapshot. Per the mission's SECONDARY-only
F5 framing, F5 is documented here as unconfirmed/unavailable for a future
milestone and was not pursued or acquired this round.

## 5. Historical Pinnacle acquisition

Credit guard: 18,064 credits remained after Phase A's own consumption
(down from ~18,071 before Phase A). Dry-run estimate for the finalized
5-season list (2022, 2023, 2024, 2025, 2026) was **7,120 credits** (39%
of remaining — within the mission's 50% guard). Real acquisition
(workflow run `33126675202`) used **6,900 credits** (11,164 remaining
afterward), fetching all 178 requested dates (37 each for 2022–2025, 30
for the partial 2026 season) at two fixed daily snapshot times
(`18:15`/`21:15` ET) — matching the dry-run's `wouldFetch` counts exactly.

## 6. Data scale and coverage

| Season | Matched games | ML-eligible | Total-eligible | Valid pregame snapshot rate |
|---|---|---|---|---|
| 2022 | 165 | 162 | 162 | 100% |
| 2023 | 184 | 181 | 181 | 100% |
| 2024 | 179 | 179 | 179 | 100% |
| 2025 | 162 | 162 | 162 | 100% |
| 2026 | 144 | 144 | 144 | 100% |
| **Total** | **834** | **828** | **828** | — |

**This is below the mission's "thousands of games" target — investigated,
not glossed over.** The shortfall is a direct, expected consequence of the
preregistered acquisition design chosen for credit conservation: sampling
every 5th calendar day (not every day) across each ~183-day regular
season, at only two fixed snapshot times per sampled day, with a strict
±60-minute pregame window. Every game that had a qualifying snapshot was
matched (validPregameSnapshotRate = 100% in every season — the games
"lost" are ones no sampled date/snapshot-time pair ever touched, not
games where a snapshot existed but fell outside the window). A future
milestone could trade credits for coverage (daily sampling, more snapshot
times) if this proxy track is judged worth extending. Per-season and
per-bucket counts still clear the `MIN_GAMES_EXPLORATORY`/
`MIN_GAMES_CONFIDENT` floors (50) used throughout this report's
robustness checks in most (not all) cells — cells below 50 are
explicitly marked `INSUFFICIENT_SAMPLE`/`DESCRIPTIVE_ONLY` rather than
treated as evidence.

## 7. Primary Question 1 — proxy vs. Pinnacle (Brier/log-loss, paired, 95% CI)

Negative delta = proxy better than Pinnacle. All CIs are 500-resample
game-clustered bootstraps (`DEFAULT_BOOTSTRAP_SEED=20260813`).

**Game moneyline (game_result):**

| Split | n (indep. games) | Paired Brier delta | 95% CI | Paired log-loss delta |
|---|---|---|---|---|
| Development (2022–2024) | 522 (528) | **+0.0098** | [0.0002, 0.0194] | +0.0199 |
| Validation (2025) | 162 | +0.0173 | [-0.0027, 0.0380] | +0.0355 |
| Holdout (2026, locked) | 144 | +0.0062 | [-0.0134, 0.0287] | +0.0146 |

Development's CI excludes zero on the "proxy worse" side — a real,
statistically distinguishable finding that Pinnacle's fair probability
beat this proxy on moneyline in-sample. Validation and holdout point in
the same direction (Pinnacle better) but their CIs cross zero.

**Game total (game_total):**

| Split | n (indep. games) | Paired Brier delta | 95% CI | Paired log-loss delta |
|---|---|---|---|---|
| Development (2022–2024) | 522 (528) | +0.0072 | [-0.0016, 0.0176] | +0.0158 |
| Validation (2025) | 162 | **-0.0047** | [-0.0225, 0.0118] | -0.0086 |
| Holdout (2026, locked) | 144 | +0.0096 | [-0.0088, 0.0251] | +0.0211 |

No split's CI excludes zero for totals — parity within noise across
development, validation, and holdout, with the point estimate actually
flipping sign in validation (proxy nominally better, not significantly).

## 8. Primary Question 2 — disagreement magnitude

Absolute `|proxy − Pinnacle|` bands, development rows only:

**ML:** `<2.5%` n=35 delta +0.0012 (descriptive only); `2.5-5%` n=41
delta +0.0051 (descriptive only); `5-7.5%` n=60 delta +0.0036
(descriptive only); `7.5-10%` n=80 delta **+0.0188**, CI [0.0001, 0.0375]
(significant, proxy worse); `10%+` n=306 delta +0.0103, CI
[-0.0079, 0.0279] (not significant). **Finding: disagreement magnitude
does not make the proxy more trustworthy — if anything, the one
confidently-resolved band (7.5–10%) shows the proxy getting *worse*, the
opposite of the hoped-for H1 direction.**

**Total:** `<2.5%` n=104 delta +0.0023, CI [-0.0005, 0.0052] (not quite
significant); `2.5-5%` n=78 delta +0.0031 (descriptive only); `5-7.5%`
n=75 delta -0.0059 (descriptive only); `7.5-10%` n=78 delta -0.0098
(descriptive only); `10%+` n=187 delta **+0.0241**, CI [0.0023, 0.0455]
(significant, proxy worse). Same pattern as ML: the largest-disagreement
band is the one confidently resolved, and it favors Pinnacle, not the
proxy.

## 9. Primary Question 3 — direction asymmetry

**ML:** proxy-favors-home-more-than-Pinnacle (`PROXY_HIGHER`) is rare
(n=21, descriptive only, delta +0.0090); proxy-favors-home-less
(`PROXY_LOWER`) dominates (n=501, delta +0.0099, CI
[-0.0010, 0.0214]). The proxy is heavily asymmetric in *which* direction
it disagrees with Pinnacle (24× more often lower than higher on the home
team), but the two directions' predictive performance deltas are similar
in magnitude — no evidence one direction is more trustworthy than the
other.

**Total:** roughly balanced counts (proxy-higher n=262, proxy-lower
n=260) and both directions' CIs cross zero (+0.0091 and +0.0054
respectively) — no asymmetry signal for totals.

## 10. Primary Question 4 — price regime

**ML price bands** (Pinnacle fair-probability bands): `0-20%` and
`80-100%` had zero eligible games (this proxy/matching design never
produced Pinnacle-implied moneylines that extreme). `20-35%` n=14
(insufficient). `35-50%` n=174 delta +0.0058, CI [-0.0081, 0.0197] (not
significant). `50-65%` n=271 delta +0.0053, CI [-0.0105, 0.0233] (not
significant). `65-80%` n=63 delta **+0.0440**, CI [0.0086, 0.0781]
(significant, proxy notably worse) — this is the single largest, most
confidently negative regime found anywhere in this study.

**Total price bands:** `0-20%`, `20-35%`, `65-80%`, `80-100%` all empty
(totals' de-vigged fair probabilities cluster near 50% by construction).
`35-50%` n=238 delta **+0.0130**, CI [0.0002, 0.0275] (borderline
significant, proxy worse). `50-65%` n=284 delta +0.0025, CI
[-0.0094, 0.0147] (not significant).

## 11. Pinnacle calibration (independent evaluation)

| Family | Overall ECE | Favorite ECE (n) | Underdog ECE (n) |
|---|---|---|---|
| Moneyline | 0.0348 | 0.0505 (334) | 0.0069 (188) |
| Total | 0.0175 | 0.0052 (284) | 0.0322 (238) |

Pinnacle is well-calibrated on both families (ECE well under 0.05 in
every cell) — confirming this is a genuinely hard benchmark, consistent
with the audit's and this study's headline finding that it is difficult
to beat.

## 12. Robustness (critical, per mission — no regime called real from pooled significance alone)

- **Direction across dev/val/holdout:** ML — Pinnacle favored in all
  three splits (though only development's CI excludes zero).
  `developmentDirectionFavorable`/`validationDirectionFavorable`/
  `holdoutDirectionFavorable` (i.e., proxy confidently *beating*
  Pinnacle) are all `false` for both families — the proxy never
  confidently beats Pinnacle in any split.
- **Season-by-season within development (ML):** 2022 delta +0.0229, CI
  [0.0045, 0.0403] (significant, Pinnacle better); 2023 delta -0.0073, CI
  [-0.0283, 0.0111] (not significant, points the other way); 2024 delta
  +0.0153, CI [-0.0015, 0.0329] (borderline). **Not perfectly
  consistent** — 2 of 3 development seasons favor Pinnacle, one (2023)
  points (non-significantly) toward the proxy. Documented as a real
  limitation, not smoothed over.
- **Season-by-season within development (Total):** 2022 +0.0169, CI
  [-0.0008, 0.0347]; 2023 -0.0022, CI [-0.0147, 0.0125]; 2024 +0.0081, CI
  [-0.0064, 0.0220] — all three cross zero, consistent with the pooled
  PARITY finding.
- **Adequate independent-game count:** `true` for both families
  (development clears `MIN_GAMES_CONFIDENT=50` easily at n=522).
- **One-team-removal / one-date-removal:** not run as a separate
  analysis this round — the pooled result for both families is already
  either dominated by Pinnacle (ML) or at parity (Total), so a
  concentration check was not required to avoid overclaiming a
  nonexistent proxy edge. Flagged as a limitation (§14) rather than
  silently skipped.

## 13. Decision classification

- **game_result / moneyline: `SHARP_MARKET_DOMINANT`.** Development's
  paired Brier-delta CI excludes zero in Pinnacle's favor
  (`confident_worse`), and the proxy never confidently beats Pinnacle in
  any split or band.
- **game_total: `PARITY_NO_INCREMENTAL_SIGNAL`.** No split or band shows
  the proxy confidently beating Pinnacle; the point estimate is mixed in
  sign across splits, consistent with noise around zero rather than a
  real edge in either direction.
- **F5 (game_result/total, first 5 innings): not evaluated.** Phase A
  found F5 markets unavailable historically via this endpoint (§4) — no
  F5 data was acquired, per the mission's explicit instruction not to
  force this market family.

Answering the mission's lettered sub-questions: (A) No, the proxy does
not beat Pinnacle overall on either primary family. (B) No — disagreement
magnitude does not contain positive information; the most
confidently-resolved high-disagreement bands favor Pinnacle. (C) No
strong directional asymmetry found for either family. (D) One clear
adverse regime was found (ML, Pinnacle fair probability 65–80%,
proxy notably worse) — a regime *against* the proxy, not a value regime
*for* it. (E) Nothing found here replicates favorably in validation —
both families' validation point estimates still favor Pinnacle or sit at
parity. (F) Nothing favorable survives the locked 2026 holdout either.
(G) **No regime here deserves a Kalshi execution-layer follow-up** — this
proxy, in its current modest form, does not show a market
disagreement worth testing against Kalshi's actual execution price.

## 14. Limitations

- This proxy does **not** reconstruct any production model's actual
  historical probability — season-aggregate team offense/starter
  quality/bullpen talent remain `UNAVAILABLE_HISTORICALLY` (Milestone 2,
  unchanged). It is a historical research model only.
- Starter and bullpen recency baselines (MLB-RSCH-0003/0004) were **not**
  incorporated in this first proxy version, per the mission's "keep the
  proxy modest" instruction — a richer proxy could plausibly perform
  differently.
- Total matched-game count (834) is below the "thousands of games"
  target, for the credit-conservation reasons detailed in §6.
- Several disagreement/price bands and the F5 family are unpopulated or
  below the confident-sample floor; those cells are marked
  `INSUFFICIENT_SAMPLE`/`DESCRIPTIVE_ONLY` and excluded from any claim.
- One-team-removal/one-date-removal concentration checks were not run
  (§12) — not needed to avoid overclaiming here since no favorable
  regime survived to check, but a genuine gap if this track is revisited.
- Home-field is a single dev-fit additive runs constant, not
  park-specific or team-specific.
- Open-to-close/movement analysis (secondary, optional per the mission)
  was not pursued — acquisition already used snapshot budget on the
  primary closest-pregame benchmark.
- Evidence level `E2_PIT_HISTORICAL`: PIT-safe reconstruction with real
  timestamped market data, but not `E3` (no chronological walk-forward
  beyond the single dev/val/holdout split) and not a claim of exact
  historical production-model evidence.

## 15. Reproducibility / tests

- Preregistration-first: `register_experiment()` proven (AST-based,
  depth-first source-order check) to be the literal first call in
  `main()`.
- Strict pregame snapshot selection, post-start rejection, exact game
  matching, exact totals-line matching, two-sided de-vig: 29 tests in
  `tests/edgelab/test_pinnacle_reconstruction.py`.
- No future MLB features (proxy inputs are season-to-date only, ≥20
  prior-game eligibility reused unchanged from MLB-RSCH-0005): proven in
  `tests/edgelab/test_proxy_model.py` (18 tests).
- Development-only fitting, frozen validation/holdout, 2026 untouched
  during fitting: proven in `tests/edgelab/test_run_proxy_vs_pinnacle_experiment_script.py`
  (24 tests).
- Deterministic reconstruction: fixed bootstrap seed
  (`DEFAULT_BOOTSTRAP_SEED=20260813`), no randomness in proxy math.
- No production files changed: `git diff origin/main...HEAD --name-only`
  touches only `lib/edgelab/`, `scripts/edgelab/`, `tests/edgelab/`,
  three pre-existing workflow-scope-lock test files (extended
  allowlists only), `.github/workflows/research-sharp-market-probe.yml`,
  `data/research_cache/`, `data/edgelab/`, and `docs/`.
- Full local suite (`python3 -m pytest tests/ -q`) run before this PR
  was opened.
