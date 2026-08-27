# EdgeLab Research Lab — MLB-RSCH-0004: Multi-Season Starter Workload/Rest Backtest

**Status: RESEARCH ONLY. No production model probability, feature, recommendation
logic, threshold, confidence tier, Bet Up To logic, Kalshi fee calculation,
bankroll/staking, market eligibility, lineup gate, slate output, risk gate,
settlement, or production cron behavior was changed.**

## 1. Question

Over multiple MLB seasons and thousands of individual pitcher-starts: does a
starting pitcher's recent rest and workload (days since previous start,
previous-start pitch count, rolling 2/3-start workload) predict that
pitcher's performance in the *next* start, after accounting for the
pitcher's own baseline quality?

This is a **baseball-level historical study**, not a Kalshi profitability
study — no market data is used or required. There is no existing production
starter-workload/rest formula analogous to the bullpen fatigue multiplier
studied in MLB-RSCH-0003; this experiment establishes whether one would even
be justified.

## 2. Execution status

**Complete.** `.github/workflows/research-multiseason-starter-workload-backtest.yml`
was dispatched against `claude/mlb-rsch-0004-starter-workload` (run
[33105908191](https://github.com/chmoses98/edge-finder-api/actions/runs/33105908191)).
The fetch reused MLB-RSCH-0003's already-committed per-team schedule cache
read-only and fetched fresh, schema-extended boxscores for all five seasons
(2022–2026 through the latest completed date at run time). The backtest ran
the preregistered specification unchanged against that real data, and the
results were committed back to this branch (commit `147e77d`). No execution
bugs occurred this round — the dependency-install and protected-branch-guard
fixes discovered during MLB-RSCH-0003 were included in this workflow from
its first version, so the run completed green on the first dispatch.

## 3. Preregistered specification

### 3a. Feature families

All PIT-safe, reconstructed via `lib.edgelab.backtest.
starter_workload_reconstruction.reconstruct_starter_features`, which filters
a pitcher's own start history via `is_strictly_before()` (imported unchanged
from the MLB-RSCH-0003 module — reused, not reimplemented) — proven by that
module's own test suite to exclude the target start itself, every future
start, and any same-date later start unless `gameNumber` ordering is
actually known.

**Rest**: `daysSincePreviousStart`, `restCategory` (SHORT ≤4 / NORMAL = 5 /
EXTENDED ≥6, fixed preregistered thresholds), `returnFromUnusuallyLongRest`
(≥10 days). **Previous start**: pitches, innings pitched, batters faced,
`highPitchCountPreviousStart` (≥100 pitches, fixed threshold),
`stressfulPreviousStart` (≥4.5 pitches/out, fixed threshold). **Rolling
workload**: pitches and innings over the prior 2 and 3 starts. **Season
workload**: prior season-to-date starts/pitches/innings,
`workloadRelativeToOwnBaseline` (recent workload ÷ that pitcher's own
season-to-date average). **Schedule context**: doubleheader/game number
(sourced from the reused schedule cache, known pregame). **Confound
control**: `ownBaselineRunsPer9` — the pitcher's own mean earned-run rate
across prior starts this season (a transparent, non-tuned, PIT-safe
within-pitcher de-meaning), used to compute `residualEarnedRunsPer9` for H5.

A pitcher's first start of a season is **excluded**, not approximated with a
fabricated prior-start value — the same eligibility rule MLB-RSCH-0003 used
for a team's first game of a season.

### 3b. Outcome

Primary: **starter earned runs allowed per 9 innings**
(`starterEarnedRunsPer9`), from `starter_outcome_for_start`. Also recorded:
starter runs allowed, earned runs, innings pitched, strikeouts, walks, hits
allowed, a WHIP-like rate, and whether the starter completed 5 innings. A
start with missing runs/earnedRuns/outs data, or zero outs recorded, is
excluded from that start's outcome entirely (never approximated).
**Not implemented**: first-five-innings *team* runs allowed — not directly
derivable from per-pitcher aggregate boxscore stats (would require an
inning-by-inning line score, a heavier fetch this experiment's fixed cache
design did not pull).

### 3c. Hypotheses — preregistered, not changed after results

H1 (short rest → worse next start), H2 (high previous-start pitch count →
worse), H3 (higher rolling 2/3-start workload → worse), H4 (nonlinear/extreme
workload matters more), H5 (H1/H2 effects survive pitcher-baseline
adjustment).

### 3d. Chronological split

Development = 2022–2024, validation = 2025, holdout = 2026 (locked). The
same fixed `run_hypothesis_tests` function is applied unchanged to all three
groups — proven structurally (`TestHoldoutIsolation` in
`tests/edgelab/test_run_multiseason_starter_workload_experiment_script.py`).

### 3e. Evidence level

`E2_PIT_HISTORICAL` — the feature reconstruction pathway is proven
point-in-time-safe by tests, the same evidence level MLB-RSCH-0003 used for
its own reconstructed-from-dated-raw feature pathway.

## 4. Coverage (real data)

| Season | Games | Pitcher-starts | Unique pitchers |
|---|---|---|---|
| 2022 | 2,347 | 4,488 | 299 |
| 2023 | 2,348 | 4,473 | 318 |
| 2024 | 2,353 | 4,485 | 317 |
| 2025 | 2,356 | 4,488 | 308 |
| 2026 (partial, through latest completed date) | 1,929 | 3,663 | 287 |
| **Total** | **11,333** | **21,597** | — |

**21,597 / 3,000 minimum expected pitcher-starts — well above target.**
Development (2022–2024) = 13,446 starts / 7,048 games / 510 unique pitchers;
validation (2025) = 4,488 / 2,356 / 308; locked holdout (2026) = 3,663 /
1,929 / 287.

**Rest-category distribution** (a preregistered but pre-execution-unknown
fact about modern MLB scheduling): EXTENDED (≥6 days) rest dominates in
every period (64% development, 69% validation, 68% holdout) — driven by
off-days, six-man-rotation stretches, and rainouts — with NORMAL (5-day) rest
the minority (35% / 31% / 31%), and true SHORT (≤4-day) rest genuinely rare
among starting pitchers: **62 starts in development (0.46%), 7 in validation
(0.16%), 20 in holdout (0.55%)**. This small SHORT-rest sample size directly
limits H1's statistical power — flagged in §8.

## 5. Results

Sign convention throughout: a **positive** effect (Spearman r > 0 / mean
difference > 0) means *more* workload or shorter rest is associated with
*worse* subsequent starter performance (higher earned runs allowed per 9) —
the direction H1/H2/H3/H5 predict. All CIs are 95%, game-clustered
bootstrap, but **clustered by `playerId`** (not `gamePk` as in
MLB-RSCH-0003) to respect repeated observations from the same pitcher, per
the mission's explicit instruction
(`lib.edgelab.research_stats.game_clustered_bootstrap_ci`, `cluster_key="playerId"`).

### 5a. Development (2022–2024, n=13,446 starts / 7,048 games / 510 pitchers) — PRIMARY

| Check | Result | 95% CI | Read |
|---|---|---|---|
| H1 — short rest vs earned runs/9 | mean diff **+0.94** runs/9 (n=62 vs 13,384) | [-2.27, 5.59] | not confident — crosses zero, tiny SHORT-rest sample |
| Five-innings probability, short rest | mean diff **-0.63** (9.7% vs 73.0% reached 5 IP) | [-0.70, -0.54] | confidently negative — but n=62, see §8 |
| H2 — previous-start high pitch count vs earned runs/9 | mean diff **-0.50** runs/9 (n=2,091 vs 11,355) | [-0.80, -0.17] | confidently **negative** — opposite of hypothesized direction |
| H3 — rolling 2-start workload (pitches) vs earned runs/9 | Spearman r = **-0.029** | [-0.050, -0.006] | confidently **negative**, small — opposite of hypothesized direction |
| H3 — rolling 3-start workload (pitches) vs earned runs/9 | Spearman r = **-0.033** | [-0.053, -0.011] | confidently **negative**, small — opposite direction |
| H5 — short rest, pitcher-baseline-adjusted | mean diff **+1.86** residual runs/9 (n=62 vs 13,383) | [-1.39, 6.16] | not confident |
| H5 — high pitch count, pitcher-baseline-adjusted | mean diff **+0.21** residual runs/9 (n=2,091 vs 11,354) | [-0.10, 0.53] | not confident — sign **flips positive** vs raw H2, magnitude shrinks toward zero |

**H2 and H3 both show a statistically confident effect in the *opposite*
direction from what was hypothesized**: higher previous-start pitch count
and higher rolling 2/3-start workload are associated with *better* (lower
earned-run-rate) subsequent performance, not worse. Reported as
preregistered, not adjusted after seeing this.

**H5's baseline adjustment materially changes the H2 picture**: once
controlling for each pitcher's own season-to-date baseline quality, the
confident negative raw effect (-0.50, entire CI negative) shrinks to a small,
no-longer-confident positive effect (+0.21, CI crosses zero). This is
consistent with a **survivorship/quality confound** — pitchers who are
throwing well and are more durable both accumulate more pitches *and* tend
to pitch well again, which drives most of H2's raw (backwards-signed)
association. This is exactly the kind of confound the mission's "critical
confounding control" instruction anticipated, and the baseline-adjustment is
what surfaces it.

**H4 — extreme/nonlinear workload** (decile buckets, rolling-3-start pitches
vs earned runs/9): bucket 1 (0–152 pitches) mean **5.31** runs/9 → bucket 10
(296–336 pitches) mean **4.59** runs/9 — a **-0.72 runs/9** decline from
lowest to highest workload decile, generally decreasing (with a small early
rise from bucket 1→3) — consistent with H3's negative correlation, i.e. the
opposite of the hypothesized "extreme workload predicts worse performance."
Descriptive, not a formal nonlinearity test (see §8).

### 5b. Validation (2025, n=4,488 starts / 2,356 games / 308 pitchers)

| Check | Result | 95% CI |
|---|---|---|
| H1 short rest vs earned runs/9 | mean diff -1.69 (n=7 vs 4,481) | [-5.35, 5.23] |
| H2 previous-start high pitch count | mean diff **-1.02** (n=543 vs 3,945) | [-1.56, -0.54] |
| H3 rolling 2-start workload | Spearman r = **-0.053** | [-0.085, -0.020] |
| H3 rolling 3-start workload | Spearman r = **-0.047** | [-0.080, -0.015] |
| H5 short rest, baseline-adjusted | mean diff +0.99 (n=7 vs 4,481) | [-4.11, 7.32] |
| H5 high pitch count, baseline-adjusted | mean diff -0.35 | [-0.82, 0.10] |

**H2 and H3's backwards-signed, confident effects replicate in validation**
(both still exclude zero, same negative sign, similar or larger magnitude).
H1 and H5 remain not confident (wide CIs, tiny SHORT-rest samples).

### 5c. Locked holdout (2026, n=3,663 starts / 1,929 games / 287 pitchers) — evaluated once, untouched during development

| Check | Result | 95% CI |
|---|---|---|
| H1 short rest vs earned runs/9 | mean diff -1.01 (n=20 vs 3,643) | [-4.17, 3.16] |
| H2 previous-start high pitch count | mean diff -0.35 (n=423 vs 3,240) | [-0.96, 0.28] |
| H3 rolling 2-start workload | Spearman r = -0.016 | [-0.054, 0.022] |
| H3 rolling 3-start workload | Spearman r = -0.018 | [-0.056, 0.018] |
| H5 short rest, baseline-adjusted | mean diff +0.81 (n=20 vs 3,643) | [-2.33, 4.95] |
| H5 high pitch count, baseline-adjusted | mean diff +0.46 (n=423 vs 3,240) | [-0.09, 1.09] |

**Every finding that was confident in both development and validation
(H2 raw, H3-2, H3-3) loses significance in the untouched 2026 holdout** —
all CIs cross zero. H1 and H5 remain not confident throughout, consistent
with their small SHORT-rest samples.

## 6. Conclusions

- **A. Does starter workload/rest contain repeatable predictive
  information?** **No — not in the hypothesized direction, and not
  repeatably.** The only effects that were statistically confident in
  development (H2 raw, H3-2, H3-3) were **backwards-signed** (more workload
  associated with *better* subsequent performance), consistent with a
  pitcher-quality/durability confound that H5's baseline adjustment
  partially explains away. H1 (rest) never reached statistical confidence in
  any period, limited by a genuinely small SHORT-rest sample (0.16–0.55% of
  starts across all three periods) — modern MLB rotations rarely give a
  starter fewer than 5 days' rest.
- **B. Which components replicate in 2025?** H2 (raw) and H3 (both rolling
  windows) replicate their development-set direction and statistical
  confidence in validation — but backwards from the hypothesis, and this
  reflects a confound H5 substantially attenuates once pitcher baseline
  quality is controlled for.
- **C. Which components survive the untouched 2026 holdout?** **None.**
  Every development+validation-confident finding (H2 raw, H3-2, H3-3) loses
  significance in holdout. H1 and H5 were never confident in any period.
- **D. Is there enough evidence to justify a later model-component ablation
  or candidate formula experiment?** **No.** `classify_signal()` returns
  **NO_USEFUL_SIGNAL** — dev H1 and H2 are both not confidently *positive*
  (H1 is not confident at all; H2 is confidently *negative*), which is the
  classifier's threshold for ruling out a useful signal, independent of
  whether some raw correlation happens to be statistically significant in
  the wrong direction. There is no evidence here to justify building a
  starter-workload-based production adjustment analogous to the bullpen
  fatigue multiplier.

**Signal classification: NO_USEFUL_SIGNAL.**

## 7. Market relevance (secondary, descriptive only — no Kalshi optimization performed)

Per the mission's "secondary only" instruction: had a repeatable signal been
found, starter rest/workload would plausibly affect pitcher-prop markets
directly (strikeouts, earned runs, outs recorded) and first-five-inning /
full-game team markets indirectly (a shorter expected outing shifts bullpen
exposure earlier). Because the signal classification here is
**NO_USEFUL_SIGNAL**, no market-horizon analysis, feature design, or Kalshi
profitability work is warranted from this result, and none was performed.

## 8. Limitations

- The SHORT-rest sample is genuinely small in every period (62 / 7 / 20
  starts in development/validation/holdout) — a structural feature of modern
  5-and-6-man MLB rotations, not a data-collection gap. H1's wide,
  zero-crossing CIs in every period are consistent with underpowered
  detection, not necessarily "no effect" — a dedicated, larger-sample or
  longer-horizon short-rest study could still be worth registering
  separately, but this study cannot distinguish "no effect" from "effect too
  small/rare to detect at this sample size" for H1 specifically.
- H2 and H3's backwards-signed, replicated (dev+validation) findings are
  most plausibly explained by a pitcher-quality/durability confound; H5's
  baseline adjustment is a **simple within-pitcher de-meaning** (residual
  vs. that pitcher's own prior-start-this-season average earned-run rate),
  not a fitted mixed-effects or regression model — a more rigorous
  confound-control design is one candidate for a future, separately
  registered experiment, but was not built or tuned here.
- First-five-innings *team* runs allowed not implemented — not directly
  derivable from per-pitcher aggregate boxscore stats (would need an
  inning-by-inning line score).
- H4's nonlinearity check is descriptive (decile table), not a formal
  nonlinearity hypothesis test.
- `data/research_cache/starter_workload/` stores a **compact extraction**
  (per-pitcher lines only, via the same `extract_pitcher_lines` function
  MLB-RSCH-0003 uses, schema-extended additively), not raw MLB API payloads
  — keeps the committed cache small (2.0MB for all 5 seasons), reusing
  MLB-RSCH-0003's already-committed schedule cache read-only rather than
  re-fetching it.
- 2026 is a partial season (through the latest completed date at run time,
  1,929 of an eventual ~2,430 games) — the locked holdout's own sample will
  grow if this experiment's cache is ever refreshed later in the season, but
  per this study's own rule the 2026 holdout must not be re-inspected or
  re-tuned against as it grows.
