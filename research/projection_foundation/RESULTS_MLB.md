# MLB Projection Foundation — results

Research only (`authority: research_only`). Generated 2026-10-17T06:59:24Z from code `7e66d2379f`, pf_core 1.0.0. Machine-readable twin: `results/summary.json`; per-model records: `results/experiments/`; full tables: `results/tables_mlb.md`.

## 1. Bottom line

* **Pitcher strikeouts, batters faced and outs are READY** (for research display in place of the current engine). On the untouched 2026 season the frozen strikeout model is better than the production pitcher engine on the same 4524 starts (ranked-probability score -0.0323 [-0.0390, -0.0255], MAE -0.051) and is calibrated at every Kalshi rung (see §7). Batters faced improves the most: the old engine ignores that a 'starter' who pitched 1–4 days ago is an opener.
* **Hitter families are better than the production hitter engine on every probability score** (CRPS for hits -0.0110 [-0.0140, -0.0081], PA -0.0888 [-0.1038, -0.0756]), mainly by removing its known level biases (hits +0.135 → -0.010, PA +0.421 → -0.013, RBI -0.151 → -0.014). They stay at **SHADOW VALIDATION** because the backtest uses the post-game batting order, and because for HR/R/RBI the *MAE of the mean* is worse (a downward-biased mean sits closer to the modal 0).
* **Team runs: SHADOW VALIDATION.** The model removes the archived production projection's -0.34-run bias (RSCH-0033 found −0.33) but the probability-score difference on the 614 genuinely-pregame archived team-games is not significant (-0.0169 [-0.0497, +0.0140]).
* **What did not work:** pitch-level Statcast features (7 weeks of data, null), schedule-adjusted ridge team ratings (no gain), and a black-box model for walks (no significant gain, transparent model frozen). Early exits (injury/blow-up) remain the largest unforecastable error.
* Nothing here is a betting signal: projection accuracy was measured, market edge was not (see RSCH-0028 / props 2026-10: pitcher K/outs trail Kalshi; hitters at parity).

| Family | Frozen model | Holdout n | Holdout MAE | Holdout CRPS vs baseline | vs incumbent (holdout) | 80% cover | Readiness |
|---|---|---:|---:|---|---|---:|---|
| Pitcher batters faced | E | 4,854 | 2.617 | -0.3785 [-0.4275, -0.3317] | -0.2488 [-0.2676, -0.2288] | 0.808 | **READY** |
| Pitcher outs | F | 4,854 | 2.709 | -0.2552 [-0.2862, -0.2241] | -0.0763 [-0.0914, -0.0631] | 0.819 | **READY** |
| Pitcher strikeouts | F | 4,854 | 1.734 | -0.0658 [-0.0767, -0.0552] | -0.0323 [-0.0390, -0.0255] | 0.802 | **READY** |
| Pitcher walks | D | 4,854 | 1.000 | -0.0114 [-0.0153, -0.0074] | — | 0.806 | **SHADOW VALIDATION** |
| Pitcher hits allowed | F | 4,854 | 1.669 | -0.0749 [-0.0863, -0.0634] | — | 0.796 | **SHADOW VALIDATION** |
| Pitcher runs allowed | F | 4,854 | 1.614 | -0.0319 [-0.0386, -0.0256] | — | 0.806 | **SHADOW VALIDATION** |
| Hitter plate appearances | F | 43,686 | 0.572 | -0.0234 [-0.0254, -0.0216] | -0.0888 [-0.1038, -0.0756] | 0.780 | **SHADOW VALIDATION** |
| Hitter hits | F | 43,686 | 0.683 | -0.0028 [-0.0035, -0.0022] | -0.0110 [-0.0140, -0.0081] | 0.801 | **SHADOW VALIDATION** |
| Hitter total bases | F | 43,686 | 1.333 | -0.0050 [-0.0062, -0.0039] | -0.0142 [-0.0175, -0.0109] | 0.804 | **SHADOW VALIDATION** |
| Hitter home runs | F | 43,686 | 0.217 | -0.0006 [-0.0008, -0.0004] | -0.0026 [-0.0034, -0.0019] | 0.803 | **SHADOW VALIDATION** |
| Hitter runs | F | 43,686 | 0.575 | -0.0024 [-0.0029, -0.0018] | -0.0091 [-0.0116, -0.0065] | 0.801 | **SHADOW VALIDATION** |
| Hitter RBIs | F | 43,686 | 0.626 | -0.0013 [-0.0018, -0.0009] | -0.0139 [-0.0166, -0.0111] | 0.800 | **SHADOW VALIDATION** |
| Hitter H+R+RBI | F | 43,686 | 1.493 | -0.0059 [-0.0075, -0.0043] | — | 0.803 | **SHADOW VALIDATION** |
| Team runs | D | 4,854 | 2.498 | -0.0197 [-0.0272, -0.0123] | -0.0169 [-0.0497, +0.0140] | 0.789 | **SHADOW VALIDATION** |
| Game total | D_convolution | 2,427 | 3.551 | — | — | 0.792 | **SHADOW VALIDATION** |

Model letters: A season+prior-season mean (EB-shrunk) · B EWMA (shrunk) · C opportunity × shrunk per-unit rate · D C × opponent/park(/platoon) factors with elasticities fitted on development · D_rc D + root-cause workload terms · E LightGBM walk-forward · F non-negative stack of B/D/E (weights fitted on development).

## 2. Data inventory (what exists, and how trustworthy its timestamps are)

| Dataset | Seasons / dates | Rows | PIT class | Notes |
|---|---|---:|---|---|
| games / schedule (date, start time, venue, teams, final score) | 2022-2026 RS | 12,138 | reconstructable | 2022-23 from research_cache schedules, 2024-26 from MLB feed; start_utc used as the forecast cutoff. |
| pitcher box lines per game (BF, outs, pitches, K, BB, H, R; ER; HR) | 2022-2026 RS | 103,822 | reconstructable | Feed (2024-26) and research-cache box agree on starters: BF 1.0000, K 1.0000 (n=13706). ER missing after 2026-08-26 (box cache ends) -> runs-allowed family uses R. HR only 2024+. |
| hitter box lines per game (PA..RBI, batting order) | 2024-2026 RS | 153,144 | reconstructable | MLB feed/live, 4 rain-shortened games missing per season. No player-level hitter lines for 2022-2023 (team-level only) -> 2024 is a cold-start development season. |
| per-PA events with batter side / pitcher hand | 2024-2026 RS | 550,007 | reconstructable | Used for platoon multipliers and opponent K% vs handedness. |
| team batting lines per game | 2022-2026 RS | 24,276 | reconstructable | Opponent / park / league context. |
| pitch-level Statcast (velocity, whiff, CSW) | 2026 2026-08-11..2026-09-27 | 190,107 | reconstructable | 643 games only (~7 weeks); Savant is blocked so no backfill. Too short for a multi-season walk-forward; used in one sub-study. |
| batting order (post-game official lineup) | 2024-2026 | 131,004 | retrospective | Equals the confirmed lineup except late scratches; models using it are labelled 'known slot'. |
| archived PREGAME lineups | 2026 2026-08-16..2026-10-01 | 9,261 | archived | 527 games; captured before first pitch 1.000; player started 0.984; same slot as post-game lineup 0.980. |
| probable pitchers | 2024-2026 (feed field) / 2026-06-15.. (slates) | 88 | retrospective | Feed 'probable' field was fetched post-game and equals the actual starter 0.9990 of the time (it is overwritten, so it cannot measure scratches). Archived pregame probables exist only in data/slates (88 slate dates from 2026-06-15). |
| handedness (bats/throws) | 2022-2026 |  | reconstructable | Static identity; 2022-23-only pitchers lack throws (24% of 2022 appearances) -> treated as R. |
| parks (venue id) | 2022-2026 |  | reconstructable | Park factors are estimated point-in-time from prior games at the venue (both teams), shrunk (EB). |
| weather | - | 0 | missing | No per-date archive (Milestone-2 PIT audit, unchanged). |
| injuries / IL / pitch-count restrictions | - | 0 | missing | No source in repo; layoff (>=15 days) is used as a proxy. |
| bullpen usage | 2022-2026 | 79,546 | reconstructable | Relief lines derivable from box scores. |
| archived pregame TEAM-RUN projections (production) | 2026-07-30.. | 1,520 | archived | data/pipeline/<date>/projections.json; createdAt before first pitch 0.404. |
| archived pregame HITTER projections (production snapshots) | 2026 2026-08-19..2026-10-16 | 11,876 | archived | 24 dates; incumbent also re-run over 2026-08-16..09-27 (hitter_w2/audit, fidelity ~0.014). |
| PITCHER projections in the archive | 2025-2026 sidecars; 2026-10-12.. shadow |  | retrospective | pitcher_workload_joint sidecars are retroactive walk-forward reruns (NOT archived pregame forecasts). Genuinely prospective, hash-chained pitcher projections exist only in data/shadow_pure/mlb_pitcher (5 dates, postseason). |
| Kalshi market observations | 2026 2026-08-01..2026-10-17 |  | archived | Not used: this initiative measures projection accuracy, not market edge. |
| sportsbook game lines (Pinnacle sample) | 2022-2026 | 178 | archived | Every ~5 days; not used. |
| 2026 postseason box lines | 2026 PS | 25 | reconstructable | Pitcher lines only (25 games to 2026-10-10); no postseason hitter lines -> forward hitter projections use regular-season form. |

Timestamp-integrity findings: (1) only **40%** of archived team-run projection rows (`data/pipeline/<date>/projections.json`) were written before first pitch — the file is overwritten during the day, so any audit that treats it as pregame includes post-start rewrites; comparisons here use only the pregame subset. (2) The feed's 'probable pitcher' equals the actual starter 99.90% of the time because it is overwritten after the game — scratches cannot be measured from it, so every backtest here is 'starter known'. (3) Archived pregame lineups (527 games) name a player who started 98.4% of the time and the same slot 98.0% of the time. (4) Pitcher projections in the archive (pitcher_workload_joint sidecars) are retroactive reruns; they are used only as a walk-forward reproduction of the incumbent, never as archived forecasts.

## 3. Method

* **development:** pitchers & teams: 2023-2024 regular seasons (2022 = history only); hitters: 2024 (fits use 2024-05-01 on; no 2022-23 player-level hitter lines)
* **validation:** 2025 regular season (model selection)
* **holdout:** 2026 regular season (frozen winners scored once; incumbent comparisons on matched rows)
* Every feature uses only games completed on earlier dates (player features shift one game; team/park/league context uses decayed sums over strictly earlier dates, so the two games of a doubleheader never see each other).
* Shrinkage strengths come from pf_core's method-of-moments estimator on development seasons (e.g. strikeout rate k ≈ 111 batters faced, hitter hit rate k ≈ 279 PA). Opponent/park elasticities, distribution shapes and stack weights are fitted on development only.
* E is refitted every two months, training only on earlier months. The frozen winner is the lowest **validation** CRPS; a black-box winner must beat the best transparent model with a game-clustered 95% CI excluding zero, otherwise the transparent model is frozen. Holdout is reported for the winner, baseline and incumbent only.
* Metrics are per independent player-game (never ladder rungs). CIs are game-clustered bootstraps (pf_core.metrics). CRPS is the primary score; '80% cover' is randomised-PIT coverage (0.80 = calibrated); PIT variance > 1 = over-confident.

## 4. Starting pitchers (validation = 2025, holdout = 2026; every starter incl. openers)

### Pitcher batters faced

| Model | Distribution | n | MAE | RMSE | Bias | CRPS | 80% cover | PIT var | CRPS vs A [95% CI] | vs incumbent [95% CI] | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | empirical_resid | 4,852 | 2.872 | 3.954 | +0.035 | 2.0694 | 0.812 | 0.978 | — | -0.1313 [-0.1474, -0.1150] | baseline |
| B | empirical_resid | 4,852 | 2.834 | 3.903 | -0.009 | 2.0438 | 0.812 | 0.963 | -0.0256 [-0.0424, -0.0097] | — | rejected |
| C | empirical_resid | 4,852 | 2.814 | 3.890 | +0.004 | 2.0342 | 0.814 | 0.968 | -0.0352 [-0.0520, -0.0195] | — | rejected |
| D | empirical_resid | 4,852 | 2.813 | 3.890 | -0.000 | 2.0345 | 0.814 | 0.965 | -0.0349 [-0.0515, -0.0187] | -0.1632 [-0.1764, -0.1500] | rejected |
| D_rc | empirical_resid | 4,852 | 2.605 | 3.537 | +0.112 | 1.8851 | 0.814 | 0.975 | -0.1843 [-0.2188, -0.1481] | -0.2019 [-0.2195, -0.1839] | rejected |
| E | empirical_resid | 4,852 | 2.527 | 3.424 | +0.072 | 1.8268 | 0.814 | 0.958 | -0.2426 [-0.2787, -0.2048] | -0.2486 [-0.2692, -0.2288] | best |
| F | empirical_resid | 4,852 | 2.527 | 3.432 | +0.080 | 1.8286 | 0.813 | 0.965 | -0.2408 [-0.2760, -0.2042] | — | competitive |

**Holdout 2026 (frozen E, scored once):** n=4,854, MAE 2.617, bias +0.046, CRPS 1.8919, 80% cover 0.808, PIT variance 0.976; CRPS vs baseline A -0.3785 [-0.4275, -0.3317]; vs incumbent -0.2488 [-0.2676, -0.2288] — rung RPS over incumbent rungs 15..30 (matched n=4524); MAE difference -0.2287 [-0.2628, -0.1944].  
Selection: lowest validation CRPS: E; beats best transparent D_rc by -0.0583 CRPS [-0.0732, -0.0416]. **Readiness: READY** — holdout CRPS vs baseline -0.3785 [-0.42755, -0.33174]; 80% PIT coverage 0.8078, PIT variance ratio 0.9757; incumbent: better (-0.2488 [-0.26765, -0.22882], rung RPS over incumbent rungs 15..30 (matched n=4524)); ready for research-only publication in place of the incumbent; prospective (archived pregame) confirmation still recommended

### Pitcher outs

| Model | Distribution | n | MAE | RMSE | Bias | CRPS | 80% cover | PIT var | CRPS vs A [95% CI] | vs incumbent [95% CI] | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | empirical_resid | 4,852 | 2.907 | 3.857 | +0.013 | 2.0863 | 0.811 | 0.956 | — | +0.0179 [+0.0053, +0.0295] | baseline |
| B | empirical_resid | 4,852 | 2.885 | 3.823 | -0.030 | 2.0676 | 0.819 | 0.952 | -0.0188 [-0.0317, -0.0055] | — | rejected |
| C | empirical_resid | 4,852 | 2.732 | 3.619 | +0.058 | 1.9555 | 0.816 | 0.962 | -0.1308 [-0.1538, -0.1055] | — | rejected |
| D | empirical_resid | 4,852 | 2.732 | 3.619 | +0.056 | 1.9557 | 0.815 | 0.965 | -0.1306 [-0.1540, -0.1043] | -0.0403 [-0.0537, -0.0286] | rejected |
| E | empirical_resid | 4,852 | 2.698 | 3.574 | +0.023 | 1.9297 | 0.814 | 0.946 | -0.1567 [-0.1832, -0.1296] | — | competitive |
| F | empirical_resid | 4,852 | 2.696 | 3.572 | +0.035 | 1.9281 | 0.813 | 0.956 | -0.1582 [-0.1825, -0.1322] | -0.0639 [-0.0789, -0.0506] | best |

**Holdout 2026 (frozen F, scored once):** n=4,854, MAE 2.709, bias +0.064, CRPS 1.9360, 80% cover 0.819, PIT variance 0.961; CRPS vs baseline A -0.2552 [-0.2862, -0.2241]; vs incumbent -0.0763 [-0.0914, -0.0631] — rung RPS over incumbent rungs 9..21 (matched n=4524); MAE difference -0.1246 [-0.1501, -0.1018].  
Selection: lowest validation CRPS: F; beats best transparent C by -0.0274 CRPS [-0.035, -0.0195]. **Readiness: READY** — holdout CRPS vs baseline -0.2552 [-0.28619, -0.22407]; 80% PIT coverage 0.8193, PIT variance ratio 0.9605; incumbent: better (-0.0763 [-0.09137, -0.06308], rung RPS over incumbent rungs 9..21 (matched n=4524)); ready for research-only publication in place of the incumbent; prospective (archived pregame) confirmation still recommended

### Pitcher strikeouts

| Model | Distribution | n | MAE | RMSE | Bias | CRPS | 80% cover | PIT var | CRPS vs A [95% CI] | vs incumbent [95% CI] | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | empirical_resid | 4,852 | 1.830 | 2.298 | -0.046 | 1.2818 | 0.803 | 0.987 | — | +0.0088 [+0.0009, +0.0176] | baseline |
| B | empirical_resid | 4,852 | 1.827 | 2.293 | -0.005 | 1.2786 | 0.804 | 0.985 | -0.0033 [-0.0087, +0.0025] | — | rejected |
| C | nb_binned | 4,852 | 1.797 | 2.264 | +0.069 | 1.2570 | 0.802 | 0.999 | -0.0248 [-0.0331, -0.0163] | — | rejected |
| D | binom_mix | 4,852 | 1.765 | 2.229 | +0.026 | 1.2386 | 0.784 | 1.027 | -0.0432 [-0.0536, -0.0320] | -0.0217 [-0.0282, -0.0159] | competitive |
| E | nb_binned | 4,852 | 1.762 | 2.222 | +0.069 | 1.2339 | 0.799 | 0.994 | -0.0479 [-0.0590, -0.0360] | — | competitive |
| F | binom_mix | 4,852 | 1.758 | 2.219 | +0.046 | 1.2320 | 0.787 | 1.019 | -0.0498 [-0.0607, -0.0383] | -0.0283 [-0.0353, -0.0217] | best |

**Holdout 2026 (frozen F, scored once):** n=4,854, MAE 1.734, bias +0.061, CRPS 1.2049, 80% cover 0.802, PIT variance 0.996; CRPS vs baseline A -0.0658 [-0.0767, -0.0552]; vs incumbent -0.0323 [-0.0390, -0.0255] — rung RPS over incumbent rungs 2..10 (matched n=4524); MAE difference -0.0513 [-0.0628, -0.0397].  
Selection: lowest validation CRPS: F; beats best transparent D by -0.0065 CRPS [-0.0094, -0.0038]. **Readiness: READY** — holdout CRPS vs baseline -0.0658 [-0.07666, -0.05521]; 80% PIT coverage 0.802, PIT variance ratio 0.9956; incumbent: better (-0.0323 [-0.03901, -0.02553], rung RPS over incumbent rungs 2..10 (matched n=4524)); ready for research-only publication in place of the incumbent; prospective (archived pregame) confirmation still recommended

### Pitcher walks

| Model | Distribution | n | MAE | RMSE | Bias | CRPS | 80% cover | PIT var | CRPS vs A [95% CI] | vs incumbent [95% CI] | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | binom_mix | 4,852 | 1.023 | 1.265 | +0.017 | 0.6911 | 0.800 | 1.002 | — | — | baseline |
| B | binom_mix | 4,852 | 1.025 | 1.267 | +0.018 | 0.6923 | 0.796 | 1.004 | +0.0013 [-0.0005, +0.0034] | — | rejected |
| C | empirical_resid | 4,852 | 1.016 | 1.258 | +0.022 | 0.6863 | 0.795 | 1.010 | -0.0047 [-0.0076, -0.0019] | — | competitive |
| D | empirical_resid | 4,852 | 1.015 | 1.255 | +0.023 | 0.6844 | 0.794 | 1.018 | -0.0066 [-0.0099, -0.0030] | — | best |
| E | binom_mix | 4,852 | 1.010 | 1.251 | -0.010 | 0.6825 | 0.802 | 0.998 | -0.0085 [-0.0127, -0.0037] | — | competitive |
| F | empirical_resid | 4,852 | 1.014 | 1.252 | +0.027 | 0.6831 | 0.798 | 1.015 | -0.0080 [-0.0115, -0.0041] | — | competitive |

**Holdout 2026 (frozen D, scored once):** n=4,854, MAE 1.000, bias -0.020, CRPS 0.6806, 80% cover 0.806, PIT variance 0.989; CRPS vs baseline A -0.0114 [-0.0153, -0.0074].  
Selection: lowest validation CRPS: E; advantage over transparent D not significant (-0.0019 [-0.004926731731763374, 0.0010387264644427113]) -> D. **Readiness: SHADOW VALIDATION** — holdout CRPS vs baseline -0.0114 [-0.01527, -0.00744]; 80% PIT coverage 0.8063, PIT variance ratio 0.9887; incumbent: none; no incumbent engine exists for this family (Kalshi lists <100 markets); prospective shadow needed

### Pitcher hits allowed

| Model | Distribution | n | MAE | RMSE | Bias | CRPS | 80% cover | PIT var | CRPS vs A [95% CI] | vs incumbent [95% CI] | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | binom_mix | 4,852 | 1.750 | 2.178 | +0.035 | 1.2185 | 0.804 | 0.988 | — | — | baseline |
| B | empirical_resid | 4,852 | 1.747 | 2.174 | +0.015 | 1.2198 | 0.816 | 0.963 | +0.0013 [-0.0018, +0.0049] | — | rejected |
| C | empirical_resid | 4,852 | 1.709 | 2.123 | +0.046 | 1.1872 | 0.812 | 0.981 | -0.0313 [-0.0400, -0.0230] | — | rejected |
| D | empirical_resid | 4,852 | 1.702 | 2.110 | +0.053 | 1.1807 | 0.809 | 0.984 | -0.0378 [-0.0466, -0.0294] | — | rejected |
| E | empirical_resid | 4,852 | 1.684 | 2.096 | +0.051 | 1.1702 | 0.809 | 0.976 | -0.0482 [-0.0585, -0.0375] | — | competitive |
| F | binom_mix | 4,852 | 1.681 | 2.090 | +0.045 | 1.1677 | 0.801 | 1.000 | -0.0507 [-0.0601, -0.0409] | — | best |

**Holdout 2026 (frozen F, scored once):** n=4,854, MAE 1.669, bias +0.040, CRPS 1.1613, 80% cover 0.796, PIT variance 1.003; CRPS vs baseline A -0.0749 [-0.0863, -0.0634].  
Selection: lowest validation CRPS: F; beats best transparent D by -0.0129 CRPS [-0.0167, -0.009]. **Readiness: SHADOW VALIDATION** — holdout CRPS vs baseline -0.0749 [-0.08634, -0.0634]; 80% PIT coverage 0.7963, PIT variance ratio 1.0032; incumbent: none; no incumbent engine exists for this family (Kalshi lists <100 markets); prospective shadow needed

### Pitcher runs allowed

| Model | Distribution | n | MAE | RMSE | Bias | CRPS | 80% cover | PIT var | CRPS vs A [95% CI] | vs incumbent [95% CI] | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | empirical_resid | 4,852 | 1.653 | 2.033 | -0.003 | 1.1205 | 0.803 | 0.977 | — | — | baseline |
| B | empirical_resid | 4,852 | 1.659 | 2.038 | +0.020 | 1.1233 | 0.803 | 0.979 | +0.0028 [-0.0000, +0.0055] | — | rejected |
| C | empirical_resid | 4,852 | 1.640 | 2.018 | +0.039 | 1.1101 | 0.797 | 1.004 | -0.0104 [-0.0154, -0.0059] | — | competitive |
| D | empirical_resid | 4,852 | 1.632 | 2.010 | +0.041 | 1.1055 | 0.799 | 1.003 | -0.0151 [-0.0198, -0.0104] | — | competitive |
| E | empirical_resid | 4,852 | 1.629 | 2.006 | +0.069 | 1.1023 | 0.803 | 0.989 | -0.0182 [-0.0243, -0.0127] | — | competitive |
| F | empirical_resid | 4,852 | 1.621 | 2.003 | +0.048 | 1.1009 | 0.795 | 0.998 | -0.0196 [-0.0251, -0.0143] | — | best |

**Holdout 2026 (frozen F, scored once):** n=4,854, MAE 1.614, bias +0.059, CRPS 1.0923, 80% cover 0.806, PIT variance 0.986; CRPS vs baseline A -0.0319 [-0.0386, -0.0256].  
Selection: lowest validation CRPS: F; beats best transparent D by -0.0045 CRPS [-0.0073, -0.0019]. **Readiness: SHADOW VALIDATION** — holdout CRPS vs baseline -0.0319 [-0.03857, -0.0256]; 80% PIT coverage 0.8057, PIT variance ratio 0.9855; incumbent: none; no incumbent engine exists for this family (Kalshi lists <100 markets); prospective shadow needed

### Incumbent comparison (production pitcher engine re-run walk-forward; and the W1-D research challenger)

| Family | Split | Matched n | Our MAE | Incumbent MAE | Our rung RPS | Incumbent rung RPS | RPS diff [95% CI] | Kalshi-rung Brier ours / incumbent | Bias ours / incumbent | vs W1-D challenger (RPS) |
|---|---|---:|---:|---:|---:|---:|---|---|---|---|
| Pitcher batters faced (E) | validation | 4,490 | 2.496 | 2.663 | 1.6796 | 1.9282 | -0.2486 [-0.2692, -0.2288] | 0.1476 / 0.1646 | +0.080 / +0.062 | -0.0787 [-0.0947, -0.0621] |
| Pitcher batters faced (E) | holdout | 4,524 | 2.593 | 2.821 | 1.6889 | 1.9378 | -0.2489 [-0.2677, -0.2288] | 0.1473 / 0.1642 | +0.018 / +0.176 | -0.0808 [-0.0982, -0.0625] |
| Pitcher outs (F) | validation | 4,490 | 2.675 | 2.772 | 1.7401 | 1.8040 | -0.0639 [-0.0789, -0.0506] | 0.1784 / 0.1848 | +0.021 / +0.064 | -0.0232 [-0.0347, -0.0126] |
| Pitcher outs (F) | holdout | 4,524 | 2.710 | 2.835 | 1.7372 | 1.8135 | -0.0763 [-0.0914, -0.0631] | 0.1760 / 0.1821 | +0.010 / +0.184 | -0.0331 [-0.0446, -0.0221] |
| Pitcher strikeouts (F) | validation | 4,490 | 1.763 | 1.802 | 1.1870 | 1.2154 | -0.0283 [-0.0353, -0.0217] | 0.1555 / 0.1593 | +0.053 / +0.068 | -0.0199 [-0.0268, -0.0136] |
| Pitcher strikeouts (F) | holdout | 4,524 | 1.747 | 1.798 | 1.1735 | 1.2059 | -0.0324 [-0.0390, -0.0255] | 0.1536 / 0.1578 | +0.037 / +0.125 | -0.0245 [-0.0313, -0.0171] |

The incumbent sidecars cover starters with ≥3 prior starts (the incumbent's own population), so matched n is slightly below the full holdout.

## 5. Starting hitters (validation = 2025, holdout = 2026; post-game batting order = 'known slot')

### Hitter plate appearances

| Model | Distribution | n | MAE | RMSE | Bias | CRPS | 80% cover | PIT var | CRPS vs A [95% CI] | vs incumbent [95% CI] | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | empirical_resid | 43,668 | 0.560 | 0.749 | +0.001 | 0.3738 | 0.798 | 1.009 | — | — | baseline |
| B | empirical_resid | 43,668 | 0.564 | 0.745 | -0.007 | 0.3713 | 0.797 | 1.010 | -0.0025 [-0.0033, -0.0016] | — | rejected |
| C | empirical_resid | 43,668 | 0.554 | 0.720 | -0.007 | 0.3556 | 0.794 | 1.017 | -0.0182 [-0.0202, -0.0163] | — | rejected |
| D | empirical_resid | 43,668 | 0.552 | 0.717 | -0.013 | 0.3537 | 0.795 | 1.014 | -0.0201 [-0.0221, -0.0181] | — | rejected |
| E | empirical_resid | 43,668 | 0.546 | 0.707 | -0.008 | 0.3482 | 0.800 | 1.004 | -0.0256 [-0.0273, -0.0238] | — | competitive |
| F | empirical_resid | 43,668 | 0.547 | 0.706 | -0.010 | 0.3478 | 0.796 | 1.012 | -0.0260 [-0.0276, -0.0244] | — | best |
| C_hist_slot | empirical_resid | 43,668 | 0.560 | 0.736 | -0.007 | 0.3656 | 0.795 | 1.014 | -0.0082 [-0.0102, -0.0064] | — | rejected |
| D_hist_slot | empirical_resid | 43,668 | 0.557 | 0.733 | -0.012 | 0.3636 | 0.796 | 1.012 | -0.0102 [-0.0122, -0.0083] | — | rejected |
| E_hist_slot | empirical_resid | 43,668 | 0.547 | 0.718 | -0.007 | 0.3554 | 0.799 | 1.008 | -0.0185 [-0.0201, -0.0168] | — | rejected |

**Holdout 2026 (frozen F, scored once):** n=43,686, MAE 0.572, bias -0.002, CRPS 0.3662, 80% cover 0.780, PIT variance 1.040; CRPS vs baseline A -0.0234 [-0.0254, -0.0216]; vs incumbent -0.0888 [-0.1038, -0.0756] — CRPS vs production hitter engine rerun, matched n=8883 (2026-08-16..2026-09-27); MAE difference -0.0885 [-0.1082, -0.0703].  
Selection: lowest validation CRPS: F; beats best transparent D by -0.0058 CRPS [-0.0067, -0.005]. **Readiness: SHADOW VALIDATION** — holdout CRPS vs baseline -0.0234 [-0.02539, -0.02159]; 80% PIT coverage 0.7799, PIT variance ratio 1.0404; incumbent: better (-0.0888 [-0.10382, -0.07561], CRPS vs production hitter engine rerun, matched n=8883 (2026-08-16..2026-09-27)); inputs use the post-game official batting order (equivalent to a confirmed lineup); where an archived pregame lineup exists (527 games) it gives the same slot 99.5% of the time, but a prospective confirmed-lineup shadow is still required

### Hitter hits

| Model | Distribution | n | MAE | RMSE | Bias | CRPS | 80% cover | PIT var | CRPS vs A [95% CI] | vs incumbent [95% CI] | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | empirical_resid | 43,668 | 0.684 | 0.875 | +0.006 | 0.4577 | 0.798 | 1.008 | — | — | baseline |
| B | empirical_resid | 43,668 | 0.684 | 0.874 | +0.000 | 0.4574 | 0.797 | 1.010 | -0.0003 [-0.0006, -0.0000] | — | competitive |
| C | binom_mix | 43,668 | 0.684 | 0.873 | +0.004 | 0.4565 | 0.796 | 1.013 | -0.0012 [-0.0017, -0.0008] | — | competitive |
| D | binom_mix | 43,668 | 0.685 | 0.871 | +0.001 | 0.4552 | 0.796 | 1.012 | -0.0025 [-0.0032, -0.0018] | — | competitive |
| E | binom_mix | 43,668 | 0.684 | 0.870 | +0.004 | 0.4547 | 0.796 | 1.011 | -0.0030 [-0.0037, -0.0022] | — | competitive |
| F | binom_mix | 43,668 | 0.684 | 0.870 | +0.000 | 0.4547 | 0.797 | 1.011 | -0.0030 [-0.0037, -0.0023] | — | best |
| D_hist_slot | binom_mix | 43,668 | 0.685 | 0.872 | +0.001 | 0.4558 | 0.796 | 1.013 | -0.0019 [-0.0026, -0.0011] | — | competitive |

**Holdout 2026 (frozen F, scored once):** n=43,686, MAE 0.683, bias +0.006, CRPS 0.4518, 80% cover 0.801, PIT variance 1.001; CRPS vs baseline A -0.0028 [-0.0035, -0.0022]; vs incumbent -0.0110 [-0.0140, -0.0081] — CRPS vs production hitter engine rerun, matched n=8883 (2026-08-16..2026-09-27); MAE difference -0.0122 [-0.0166, -0.0082].  
Selection: lowest validation CRPS: F; beats best transparent D by -0.0005 CRPS [-0.0006, -0.0004]. **Readiness: SHADOW VALIDATION** — holdout CRPS vs baseline -0.0028 [-0.00353, -0.00215]; 80% PIT coverage 0.8014, PIT variance ratio 1.0009; incumbent: better (-0.0110 [-0.01401, -0.0081], CRPS vs production hitter engine rerun, matched n=8883 (2026-08-16..2026-09-27)); inputs use the post-game official batting order (equivalent to a confirmed lineup); where an archived pregame lineup exists (527 games) it gives the same slot 99.5% of the time, but a prospective confirmed-lineup shadow is still required

### Hitter total bases

| Model | Distribution | n | MAE | RMSE | Bias | CRPS | 80% cover | PIT var | CRPS vs A [95% CI] | vs incumbent [95% CI] | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | tb_mix | 43,668 | 1.343 | 1.767 | -0.038 | 0.8831 | 0.795 | 1.023 | — | — | baseline |
| B | tb_mix | 43,668 | 1.337 | 1.766 | -0.047 | 0.8825 | 0.793 | 1.025 | -0.0006 [-0.0012, +0.0001] | — | rejected |
| C | tb_mix | 43,668 | 1.351 | 1.763 | +0.007 | 0.8809 | 0.797 | 1.019 | -0.0022 [-0.0030, -0.0013] | — | competitive |
| D | tb_mix | 43,668 | 1.347 | 1.761 | +0.005 | 0.8797 | 0.797 | 1.018 | -0.0034 [-0.0045, -0.0023] | — | competitive |
| E | empirical_resid | 43,668 | 1.346 | 1.761 | +0.003 | 0.8804 | 0.801 | 1.005 | -0.0027 [-0.0039, -0.0015] | — | competitive |
| F | tb_mix | 43,668 | 1.345 | 1.759 | +0.005 | 0.8785 | 0.797 | 1.017 | -0.0046 [-0.0057, -0.0036] | — | best |
| D_hist_slot | tb_mix | 43,668 | 1.349 | 1.762 | +0.004 | 0.8804 | 0.797 | 1.018 | -0.0027 [-0.0037, -0.0016] | — | competitive |

**Holdout 2026 (frozen F, scored once):** n=43,686, MAE 1.333, bias +0.023, CRPS 0.8652, 80% cover 0.804, PIT variance 1.006; CRPS vs baseline A -0.0050 [-0.0062, -0.0039]; vs incumbent -0.0142 [-0.0175, -0.0109] — CRPS vs production hitter engine rerun, matched n=8883 (2026-08-16..2026-09-27); MAE difference -0.0075 [-0.0124, -0.0021].  
Selection: lowest validation CRPS: F; beats best transparent D by -0.0012 CRPS [-0.0015, -0.0009]. **Readiness: SHADOW VALIDATION** — holdout CRPS vs baseline -0.0050 [-0.00622, -0.00385]; 80% PIT coverage 0.8036, PIT variance ratio 1.0058; incumbent: better (-0.0142 [-0.01753, -0.01094], CRPS vs production hitter engine rerun, matched n=8883 (2026-08-16..2026-09-27)); inputs use the post-game official batting order (equivalent to a confirmed lineup); where an archived pregame lineup exists (527 games) it gives the same slot 99.5% of the time, but a prospective confirmed-lineup shadow is still required

### Hitter home runs

| Model | Distribution | n | MAE | RMSE | Bias | CRPS | 80% cover | PIT var | CRPS vs A [95% CI] | vs incumbent [95% CI] | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | binom_mix | 43,668 | 0.217 | 0.356 | -0.003 | 0.1107 | 0.798 | 1.000 | — | — | baseline |
| B | binom_mix | 43,668 | 0.216 | 0.355 | -0.004 | 0.1104 | 0.798 | 1.000 | -0.0003 [-0.0004, -0.0002] | — | competitive |
| C | binom_mix | 43,668 | 0.219 | 0.355 | +0.000 | 0.1103 | 0.800 | 0.996 | -0.0004 [-0.0005, -0.0003] | — | competitive |
| D | binom_mix | 43,668 | 0.220 | 0.355 | +0.002 | 0.1102 | 0.801 | 0.994 | -0.0005 [-0.0006, -0.0003] | — | competitive |
| E | empirical_resid | 43,668 | 0.217 | 0.355 | -0.002 | 0.1104 | 0.797 | 1.000 | -0.0003 [-0.0004, -0.0001] | — | competitive |
| F | binom_mix | 43,668 | 0.219 | 0.355 | +0.001 | 0.1100 | 0.800 | 0.995 | -0.0006 [-0.0008, -0.0005] | — | best |
| D_hist_slot | binom_mix | 43,668 | 0.220 | 0.355 | +0.002 | 0.1102 | 0.801 | 0.994 | -0.0005 [-0.0006, -0.0003] | — | competitive |

**Holdout 2026 (frozen F, scored once):** n=43,686, MAE 0.217, bias +0.005, CRPS 0.1074, 80% cover 0.803, PIT variance 0.988; CRPS vs baseline A -0.0006 [-0.0008, -0.0004]; vs incumbent -0.0026 [-0.0034, -0.0019] — CRPS vs production hitter engine rerun, matched n=8883 (2026-08-16..2026-09-27); MAE difference +0.0279 [0.0264, 0.0296].  
Selection: lowest validation CRPS: F; beats best transparent D by -0.0001 CRPS [-0.0002, -0.0001]. **Readiness: SHADOW VALIDATION** — holdout CRPS vs baseline -0.0006 [-0.00075, -0.00043]; 80% PIT coverage 0.8031, PIT variance ratio 0.9883; incumbent: better (-0.0026 [-0.00343, -0.0019], CRPS vs production hitter engine rerun, matched n=8883 (2026-08-16..2026-09-27)); inputs use the post-game official batting order (equivalent to a confirmed lineup); where an archived pregame lineup exists (527 games) it gives the same slot 99.5% of the time, but a prospective confirmed-lineup shadow is still required; NOTE MAE of the mean is worse than the incumbent (+0.0279 [0.0264, 0.0296]) because the incumbent's downward-biased mean sits nearer the modal 0 for this zero-heavy count; CRPS/Brier/bias all favour the new model

### Hitter runs

| Model | Distribution | n | MAE | RMSE | Bias | CRPS | 80% cover | PIT var | CRPS vs A [95% CI] | vs incumbent [95% CI] | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | poisson | 43,668 | 0.580 | 0.680 | -0.009 | 0.3203 | 0.798 | 1.006 | — | — | baseline |
| B | poisson | 43,668 | 0.577 | 0.680 | -0.012 | 0.3199 | 0.798 | 1.008 | -0.0005 [-0.0007, -0.0003] | — | competitive |
| C | poisson | 43,668 | 0.580 | 0.679 | +0.002 | 0.3192 | 0.800 | 0.999 | -0.0011 [-0.0014, -0.0008] | — | competitive |
| D | binom_mix | 43,668 | 0.576 | 0.677 | -0.005 | 0.3182 | 0.794 | 1.012 | -0.0022 [-0.0027, -0.0016] | — | competitive |
| E | empirical_resid | 43,668 | 0.578 | 0.678 | -0.006 | 0.3189 | 0.797 | 1.008 | -0.0014 [-0.0019, -0.0009] | — | competitive |
| F | binom_mix | 43,668 | 0.576 | 0.676 | -0.002 | 0.3178 | 0.795 | 1.010 | -0.0025 [-0.0031, -0.0019] | — | best |
| D_hist_slot | binom_mix | 43,668 | 0.577 | 0.678 | -0.005 | 0.3186 | 0.794 | 1.013 | -0.0017 [-0.0023, -0.0011] | — | competitive |

**Holdout 2026 (frozen F, scored once):** n=43,686, MAE 0.575, bias +0.002, CRPS 0.3164, 80% cover 0.801, PIT variance 0.995; CRPS vs baseline A -0.0024 [-0.0029, -0.0018]; vs incumbent -0.0091 [-0.0116, -0.0065] — CRPS vs production hitter engine rerun, matched n=8883 (2026-08-16..2026-09-27); MAE difference +0.0152 [0.0121, 0.0187].  
Selection: lowest validation CRPS: F; beats best transparent D by -0.0004 CRPS [-0.0005, -0.0002]. **Readiness: SHADOW VALIDATION** — holdout CRPS vs baseline -0.0024 [-0.0029, -0.00184]; 80% PIT coverage 0.8009, PIT variance ratio 0.9951; incumbent: better (-0.0091 [-0.01162, -0.00648], CRPS vs production hitter engine rerun, matched n=8883 (2026-08-16..2026-09-27)); inputs use the post-game official batting order (equivalent to a confirmed lineup); where an archived pregame lineup exists (527 games) it gives the same slot 99.5% of the time, but a prospective confirmed-lineup shadow is still required; NOTE MAE of the mean is worse than the incumbent (+0.0152 [0.0121, 0.0187]) because the incumbent's downward-biased mean sits nearer the modal 0 for this zero-heavy count; CRPS/Brier/bias all favour the new model

### Hitter RBIs

| Model | Distribution | n | MAE | RMSE | Bias | CRPS | 80% cover | PIT var | CRPS vs A [95% CI] | vs incumbent [95% CI] | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | empirical_resid | 43,668 | 0.633 | 0.844 | -0.004 | 0.3524 | 0.799 | 1.004 | — | — | baseline |
| B | empirical_resid | 43,668 | 0.633 | 0.844 | -0.002 | 0.3521 | 0.800 | 1.004 | -0.0002 [-0.0004, -0.0000] | — | competitive |
| C | empirical_resid | 43,668 | 0.631 | 0.844 | -0.003 | 0.3519 | 0.800 | 1.004 | -0.0004 [-0.0007, -0.0001] | — | competitive |
| D | empirical_resid | 43,668 | 0.629 | 0.843 | -0.005 | 0.3514 | 0.799 | 1.006 | -0.0009 [-0.0014, -0.0005] | — | competitive |
| E | empirical_resid | 43,668 | 0.628 | 0.843 | -0.011 | 0.3512 | 0.799 | 1.007 | -0.0012 [-0.0016, -0.0008] | — | competitive |
| F | empirical_resid | 43,668 | 0.627 | 0.842 | -0.008 | 0.3510 | 0.799 | 1.007 | -0.0014 [-0.0018, -0.0010] | — | best |
| D_hist_slot | empirical_resid | 43,668 | 0.630 | 0.843 | -0.004 | 0.3515 | 0.799 | 1.005 | -0.0009 [-0.0013, -0.0005] | — | competitive |

**Holdout 2026 (frozen F, scored once):** n=43,686, MAE 0.626, bias -0.002, CRPS 0.3476, 80% cover 0.800, PIT variance 0.996; CRPS vs baseline A -0.0013 [-0.0018, -0.0009]; vs incumbent -0.0139 [-0.0166, -0.0111] — CRPS vs production hitter engine rerun, matched n=8883 (2026-08-16..2026-09-27); MAE difference +0.0473 [0.0436, 0.0513].  
Selection: lowest validation CRPS: F; beats best transparent D by -0.0005 CRPS [-0.0006, -0.0003]. **Readiness: SHADOW VALIDATION** — holdout CRPS vs baseline -0.0013 [-0.00175, -0.00092]; 80% PIT coverage 0.8001, PIT variance ratio 0.9963; incumbent: better (-0.0139 [-0.01661, -0.0111], CRPS vs production hitter engine rerun, matched n=8883 (2026-08-16..2026-09-27)); inputs use the post-game official batting order (equivalent to a confirmed lineup); where an archived pregame lineup exists (527 games) it gives the same slot 99.5% of the time, but a prospective confirmed-lineup shadow is still required; NOTE MAE of the mean is worse than the incumbent (+0.0473 [0.0436, 0.0513]) because the incumbent's downward-biased mean sits nearer the modal 0 for this zero-heavy count; CRPS/Brier/bias all favour the new model

### Hitter H+R+RBI

| Model | Distribution | n | MAE | RMSE | Bias | CRPS | 80% cover | PIT var | CRPS vs A [95% CI] | vs incumbent [95% CI] | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | empirical_resid | 43,668 | 1.513 | 1.932 | +0.002 | 1.0042 | 0.798 | 1.008 | — | — | baseline |
| B | empirical_resid | 43,668 | 1.510 | 1.931 | -0.008 | 1.0035 | 0.797 | 1.010 | -0.0007 [-0.0013, -0.0000] | — | competitive |
| C | empirical_resid | 43,668 | 1.507 | 1.928 | -0.004 | 1.0019 | 0.797 | 1.010 | -0.0022 [-0.0032, -0.0012] | — | competitive |
| D | empirical_resid | 43,668 | 1.501 | 1.923 | -0.009 | 0.9993 | 0.797 | 1.013 | -0.0049 [-0.0066, -0.0032] | — | competitive |
| E | empirical_resid | 43,668 | 1.500 | 1.923 | -0.015 | 0.9990 | 0.798 | 1.009 | -0.0052 [-0.0067, -0.0037] | — | competitive |
| F | empirical_resid | 43,668 | 1.500 | 1.921 | -0.010 | 0.9976 | 0.798 | 1.011 | -0.0066 [-0.0083, -0.0049] | — | best |
| D_hist_slot | empirical_resid | 43,668 | 1.505 | 1.925 | -0.008 | 1.0005 | 0.798 | 1.011 | -0.0036 [-0.0053, -0.0019] | — | competitive |

**Holdout 2026 (frozen F, scored once):** n=43,686, MAE 1.493, bias +0.001, CRPS 0.9911, 80% cover 0.803, PIT variance 1.001; CRPS vs baseline A -0.0059 [-0.0075, -0.0043].  
Selection: lowest validation CRPS: F; beats best transparent D by -0.0016 CRPS [-0.0022, -0.0011]. **Readiness: SHADOW VALIDATION** — holdout CRPS vs baseline -0.0059 [-0.00752, -0.00427]; 80% PIT coverage 0.8032, PIT variance ratio 1.0011; incumbent: none; inputs use the post-game official batting order (equivalent to a confirmed lineup); where an archived pregame lineup exists (527 games) it gives the same slot 99.5% of the time, but a prospective confirmed-lineup shadow is still required

### Incumbent comparison (production hitter engine re-run with archived pregame inputs, 2026-08-16..09-27)

| Stat | Matched n | Our CRPS | Incumbent CRPS | CRPS diff [95% CI] | Our MAE | Incumbent MAE | MAE diff [95% CI] | Bias ours / incumbent | Rung Brier ours / incumbent | 80% cover ours / incumbent |
|---|---:|---:|---:|---|---:|---:|---|---|---|---|
| PA | 8,883 | 0.3706 | 0.4595 | -0.0888 [-0.1038, -0.0756] | 0.5775 | 0.6659 | -0.0885 [-0.1082, -0.0703] | -0.013 / +0.421 | 0.1500 / 0.1911 | 0.780 / 0.666 |
| H | 8,883 | 0.4535 | 0.4645 | -0.0110 [-0.0140, -0.0081] | 0.6827 | 0.6948 | -0.0122 [-0.0166, -0.0082] | -0.010 / +0.135 | 0.1489 / 0.1525 | 0.788 / 0.798 |
| TB | 8,883 | 0.8623 | 0.8765 | -0.0142 [-0.0175, -0.0109] | 1.3221 | 1.3296 | -0.0075 [-0.0124, -0.0021] | -0.000 / -0.015 | 0.1850 / 0.1882 | 0.792 / 0.747 |
| HR | 8,883 | 0.1072 | 0.1098 | -0.0026 [-0.0034, -0.0019] | 0.2148 | 0.1869 | +0.0279 [+0.0264, +0.0296] | +0.003 / -0.037 | 0.1009 / 0.1035 | 0.805 / 0.772 |
| R | 8,883 | 0.3197 | 0.3288 | -0.0091 [-0.0116, -0.0065] | 0.5766 | 0.5614 | +0.0152 [+0.0121, +0.0187] | -0.010 / -0.104 | 0.1539 / 0.1584 | 0.789 / 0.764 |
| RBI | 8,883 | 0.3493 | 0.3632 | -0.0139 [-0.0166, -0.0111] | 0.6221 | 0.5747 | +0.0473 [+0.0436, +0.0513] | -0.014 / -0.151 | 0.1121 / 0.1167 | 0.797 / 0.754 |

### What the confirmed lineup is worth (opportunity accuracy)

Same model D, scored with the official slot vs only the player's previous slot (what you know before lineups post):

| Stat | Split | CRPS known slot | CRPS previous slot | Difference [95% CI] | MAE known / previous | Slot changed |
|---|---|---:|---:|---|---|---:|
| PA | validation | 0.3537 | 0.3636 | +0.0099 [+0.0089, +0.0110] | 0.552 / 0.557 | 41% |
| PA | holdout | 0.3744 | 0.3852 | +0.0108 [+0.0096, +0.0119] | 0.578 / 0.585 | 42% |
| H | validation | 0.4552 | 0.4558 | +0.0006 [+0.0004, +0.0008] | 0.685 / 0.685 | 41% |
| H | holdout | 0.4524 | 0.4531 | +0.0006 [+0.0004, +0.0009] | 0.684 / 0.684 | 42% |
| TB | validation | 0.8797 | 0.8804 | +0.0008 [+0.0004, +0.0011] | 1.347 / 1.349 | 41% |
| TB | holdout | 0.8664 | 0.8674 | +0.0010 [+0.0006, +0.0014] | 1.335 / 1.337 | 42% |
| R | validation | 0.3182 | 0.3186 | +0.0005 [+0.0003, +0.0006] | 0.576 / 0.577 | 41% |
| R | holdout | 0.3169 | 0.3174 | +0.0004 [+0.0003, +0.0006] | 0.575 / 0.576 | 42% |
| RBI | validation | 0.3514 | 0.3515 | +0.0001 [-0.0001, +0.0002] | 0.629 / 0.630 | 41% |
| RBI | holdout | 0.3480 | 0.3482 | +0.0001 [-0.0001, +0.0003] | 0.628 / 0.629 | 42% |
| HRR | validation | 0.9993 | 1.0005 | +0.0013 [+0.0007, +0.0019] | 1.501 / 1.505 | 41% |
| HRR | holdout | 0.9923 | 0.9939 | +0.0016 [+0.0010, +0.0022] | 1.494 / 1.498 | 42% |

The lineup matters for plate appearances (and through PA a little for H/TB/R/H+R+RBI); for RBI the difference is not significant. A LightGBM model without the slot recovers part of the loss (PA validation CRPS: E without slot 0.3554 vs D with previous slot 0.3636 vs D with known slot 0.3537).

## 6. Teams (validation = 2025, holdout = 2026)

### Team runs

| Model | Distribution | n | MAE | RMSE | Bias | CRPS | 80% cover | PIT var | CRPS vs A [95% CI] | vs incumbent [95% CI] | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | empirical_resid | 4,852 | 2.524 | 3.243 | -0.025 | 1.7567 | 0.784 | 1.032 | — | — | baseline |
| B | empirical_resid | 4,852 | 2.521 | 3.237 | -0.008 | 1.7537 | 0.790 | 1.026 | -0.0030 [-0.0052, -0.0004] | — | competitive |
| C | empirical_resid | 4,852 | 2.502 | 3.217 | -0.038 | 1.7432 | 0.787 | 1.028 | -0.0134 [-0.0198, -0.0071] | — | competitive |
| D | nb_binned | 4,852 | 2.499 | 3.205 | +0.015 | 1.7372 | 0.776 | 1.050 | -0.0195 [-0.0283, -0.0108] | — | best |
| D_ridge | empirical_resid | 4,852 | 2.501 | 3.212 | +0.014 | 1.7409 | 0.786 | 1.029 | -0.0158 [-0.0240, -0.0073] | — | competitive |
| E | empirical_resid | 4,852 | 2.513 | 3.216 | +0.072 | 1.7431 | 0.790 | 1.025 | -0.0136 [-0.0222, -0.0056] | — | competitive |
| F | empirical_resid | 4,852 | 2.501 | 3.207 | +0.031 | 1.7385 | 0.789 | 1.029 | -0.0182 [-0.0272, -0.0098] | — | competitive |

**Holdout 2026 (frozen D, scored once):** n=4,854, MAE 2.498, bias +0.027, CRPS 1.7248, 80% cover 0.789, PIT variance 1.042; CRPS vs baseline A -0.0197 [-0.0272, -0.0123]; vs incumbent -0.0169 [-0.0497, +0.0140] — CRPS vs archived production projection (NB alpha 0.2815), matched n=614; MAE difference +0.0233 [-0.0309, 0.0761].  
Selection: lowest validation CRPS: D. **Readiness: SHADOW VALIDATION** — holdout CRPS vs baseline -0.0197 [-0.02723, -0.01228]; 80% PIT coverage 0.7895, PIT variance ratio 1.0418; incumbent: parity (-0.0169 [-0.04966, 0.01403], CRPS vs archived production projection (NB alpha 0.2815), matched n=614); incumbent parity (difference not significant); needs prospective archived-forecast evidence

**Opponent adjustment (paired, game-clustered; negative = the richer model is better):**

| Comparison | Split | CRPS diff [95% CI] | MAE diff [95% CI] |
|---|---|---|---|
| C vs B | validation | -0.0105 [-0.0167, -0.0044] | -0.0191 [-0.0292, -0.0092] |
| D vs C | validation | -0.0061 [-0.0102, -0.0021] | -0.0029 [-0.0095, +0.0036] |
| D_ridge vs D | validation | +0.0037 [+0.0005, +0.0073] | +0.0023 [-0.0030, +0.0077] |
| C vs B | holdout | -0.0116 [-0.0178, -0.0060] | -0.0211 [-0.0307, -0.0116] |
| D vs C | holdout | -0.0085 [-0.0121, -0.0046] | -0.0106 [-0.0170, -0.0041] |
| D_ridge vs D | holdout | +0.0015 [-0.0012, +0.0041] | -0.0006 [-0.0050, +0.0035] |

Distribution (development CRPS for model D): Poisson 1.7592, NB 1.7034, binned NB 1.7029, empirical 1.7030 — NB-type shapes beat Poisson decisively (RSCH-0010 confirmed); NB vs empirical is a tie.

**Incumbent (archived pregame production projections, NB 0.2815):** n=614 team-games, CRPS 1.6209 vs 1.6379 (-0.0169 [-0.0497, +0.0140]), MAE 2.326 vs 2.303 (+0.0233 [-0.0309, +0.0761]), bias +0.084 vs -0.336.

### Game total

* validation: n=2,426 games, MAE 3.606, bias +0.031, CRPS 2.5104, 80% cover 0.782, PIT variance 1.047; convolution minus direct NB (α=0.1295) +0.0001 [-0.0011, +0.0011].
* holdout: n=2,427 games, MAE 3.551, bias +0.054, CRPS 2.4765, 80% cover 0.792, PIT variance 1.027; convolution minus direct NB (α=0.1295) +0.0005 [-0.0006, +0.0015].

**Coherence with hitters (2026):** starters score 94.4% of team runs; the sum of starters' projected runs averages 4.25 vs team projection 4.51 × that share (ratio 0.999, correlation 0.93); pf_core team reconciliation: {'PASS': 4854}. Scoring the scaled hitter sum directly gives MAE 2.495 vs 2.498 for the team model.

## 7. Calibration at Kalshi rungs (2026 holdout, frozen models)

| Family | Rung | Mean predicted | Hit rate | Brier | ECE |
|---|---|---:|---:|---:|---:|
| batters_faced | 18+ | 0.859 | 0.866 | 0.0694 | 0.0099 |
| batters_faced | 20+ | 0.768 | 0.774 | 0.1278 | 0.0163 |
| batters_faced | 22+ | 0.594 | 0.602 | 0.1917 | 0.0179 |
| batters_faced | 24+ | 0.372 | 0.376 | 0.2039 | 0.0198 |
| batters_faced | 26+ | 0.169 | 0.169 | 0.1290 | 0.0182 |
| outs | 12+ | 0.834 | 0.842 | 0.0925 | 0.0139 |
| outs | 15+ | 0.662 | 0.679 | 0.1804 | 0.0249 |
| outs | 16+ | 0.500 | 0.489 | 0.2158 | 0.0156 |
| outs | 17+ | 0.431 | 0.426 | 0.2127 | 0.0165 |
| outs | 18+ | 0.359 | 0.364 | 0.2035 | 0.0208 |
| outs | 19+ | 0.173 | 0.155 | 0.1235 | 0.0193 |
| pitcher_strikeouts | 3+ | 0.812 | 0.801 | 0.1317 | 0.0126 |
| pitcher_strikeouts | 4+ | 0.670 | 0.660 | 0.1909 | 0.0146 |
| pitcher_strikeouts | 5+ | 0.509 | 0.496 | 0.2129 | 0.0145 |
| pitcher_strikeouts | 6+ | 0.357 | 0.350 | 0.1955 | 0.0104 |
| pitcher_strikeouts | 7+ | 0.230 | 0.229 | 0.1535 | 0.0078 |
| pitcher_strikeouts | 8+ | 0.137 | 0.132 | 0.1024 | 0.0098 |
| pitcher_strikeouts | 9+ | 0.076 | 0.080 | 0.0672 | 0.0063 |
| pitcher_walks | 1+ | 0.822 | 0.827 | 0.1347 | 0.0108 |
| pitcher_walks | 2+ | 0.528 | 0.542 | 0.2343 | 0.0217 |
| pitcher_walks | 3+ | 0.259 | 0.260 | 0.1840 | 0.0127 |
| pitcher_walks | 4+ | 0.100 | 0.099 | 0.0869 | 0.0050 |
| pitcher_hits_allowed | 3+ | 0.842 | 0.833 | 0.1143 | 0.0149 |
| pitcher_hits_allowed | 4+ | 0.703 | 0.694 | 0.1844 | 0.0115 |
| pitcher_hits_allowed | 5+ | 0.532 | 0.529 | 0.2253 | 0.0091 |
| pitcher_hits_allowed | 6+ | 0.361 | 0.362 | 0.2152 | 0.0095 |
| pitcher_hits_allowed | 7+ | 0.218 | 0.218 | 0.1632 | 0.0090 |
| pitcher_runs_allowed | 1+ | 0.839 | 0.833 | 0.1291 | 0.0087 |
| pitcher_runs_allowed | 2+ | 0.645 | 0.630 | 0.2214 | 0.0146 |
| pitcher_runs_allowed | 3+ | 0.457 | 0.441 | 0.2378 | 0.0239 |
| pitcher_runs_allowed | 4+ | 0.300 | 0.285 | 0.1989 | 0.0207 |
| pitcher_runs_allowed | 5+ | 0.181 | 0.173 | 0.1408 | 0.0082 |
| plate_appearances | 4+ | 0.799 | 0.795 | 0.1348 | 0.0060 |
| plate_appearances | 5+ | 0.239 | 0.255 | 0.1641 | 0.0162 |
| hits | 1+ | 0.614 | 0.606 | 0.2352 | 0.0072 |
| hits | 2+ | 0.217 | 0.215 | 0.1661 | 0.0039 |
| hits | 3+ | 0.044 | 0.046 | 0.0437 | 0.0019 |
| total_bases | 1+ | 0.621 | 0.606 | 0.2354 | 0.0150 |
| total_bases | 2+ | 0.358 | 0.350 | 0.2241 | 0.0080 |
| total_bases | 3+ | 0.206 | 0.204 | 0.1602 | 0.0023 |
| total_bases | 4+ | 0.146 | 0.143 | 0.1206 | 0.0031 |
| home_runs | 1+ | 0.120 | 0.115 | 0.1000 | 0.0053 |
| runs | 1+ | 0.385 | 0.378 | 0.2312 | 0.0088 |
| runs | 2+ | 0.077 | 0.080 | 0.0728 | 0.0028 |
| rbis | 1+ | 0.293 | 0.294 | 0.2056 | 0.0015 |
| rbis | 2+ | 0.106 | 0.105 | 0.0935 | 0.0006 |
| rbis | 3+ | 0.035 | 0.035 | 0.0342 | 0.0003 |
| hits_runs_rbis | 1+ | 0.677 | 0.675 | 0.2163 | 0.0066 |
| hits_runs_rbis | 2+ | 0.458 | 0.454 | 0.2439 | 0.0056 |
| hits_runs_rbis | 3+ | 0.288 | 0.291 | 0.2030 | 0.0027 |
| hits_runs_rbis | 4+ | 0.171 | 0.172 | 0.1409 | 0.0042 |
| hits_runs_rbis | 5+ | 0.098 | 0.097 | 0.0863 | 0.0012 |
| team_runs | 2+ | 0.841 | 0.828 | 0.1415 | 0.0130 |
| team_runs | 3+ | 0.704 | 0.689 | 0.2131 | 0.0153 |
| team_runs | 4+ | 0.560 | 0.552 | 0.2449 | 0.0094 |
| team_runs | 5+ | 0.428 | 0.432 | 0.2429 | 0.0081 |
| team_runs | 6+ | 0.317 | 0.327 | 0.2176 | 0.0150 |
| team_runs | 7+ | 0.227 | 0.230 | 0.1746 | 0.0164 |

No family is materially over- or under-confident: 80% PIT coverage is 0.78–0.82 everywhere and PIT variance 0.96–1.05 (table §1).

## 8. Error investigations (largest recurring errors)

| Pattern | Frequency | Impact | Cause | Status | Action |
|---|---|---|---|---|---|
| Batters faced: Openers / bulk relievers listed as starters | 6.3% of 2025 starts (n=306) | 13.2% of model-D absolute error; bias +3.84 BF (D) -> +0.88 (D_rc) | role / workload state not represented in a recency-weighted mean | verified defect | fixed in D_rc (rest, opener, debut, layoff terms) and captured by E |
| Batters faced: Season debut (ramp-up) | 7.6% of 2025 starts (n=368) | 12.6% of model-D absolute error; bias +3.19 BF (D) -> +0.80 (D_rc) | role / workload state not represented in a recency-weighted mean | verified defect | fixed in D_rc (rest, opener, debut, layoff terms) and captured by E |
| Batters faced: Spot starter with no start history | 2.2% of 2025 starts (n=107) | 5.5% of model-D absolute error; bias +6.49 BF (D) -> +0.92 (D_rc) | role / workload state not represented in a recency-weighted mean | verified defect | fixed in D_rc (rest, opener, debut, layoff terms) and captured by E |
| Batters faced: Early exit (<=12 BF: injury/blow-up) | 4.3% of 2025 starts (n=211) | 16.5% of model-D absolute error; bias +10.66 BF (D) -> +7.48 (D_rc) | role / workload state not represented in a recency-weighted mean | ruled out | not forecastable from box scores (injury data missing); handled by the empirical residual tail |
| Batters faced: First start after >=15 days (IL return proxy) | 2.6% of 2025 starts (n=124) | 2.9% of model-D absolute error; bias +1.52 BF (D) -> +0.95 (D_rc) | role / workload state not represented in a recency-weighted mean | verified defect | fixed in D_rc (rest, opener, debut, layoff terms) and captured by E |
| Batters faced: Pitched within 2 days | 1.0% of 2025 starts (n=49) | 3.8% of model-D absolute error; bias +10.37 BF (D) -> +2.24 (D_rc) | role / workload state not represented in a recency-weighted mean | verified defect | fixed in D_rc (rest, opener, debut, layoff terms) and captured by E |
| Archived team-run projections written after first pitch | 60% of archived team rows | Any audit that treats data/pipeline/<date>/projections.json as pregame includes post-start rewrites | the per-date file is overwritten during the day; createdAt is the last write | verified defect | incumbent comparison restricted to rows with createdAt < first pitch |
| Probable-pitcher field in the post-game feed | equals the actual starter 99.90% | Late scratches cannot be measured from the feed; starter identity in the backtest is effectively known | the feed overwrites probables after the game | verified defect | use archived slates (data/slates) for prospective evaluation; label backtests 'starter known' |
| PA: Hitter's slot changed from his previous game | 41.0% of 2025 starter-games (n=17901) | 40.5% of PA absolute error; bias -0.022, MAE 0.541 | lineup position / playing time uncertainty | hypothesis | known-slot models need a confirmed lineup; prev-slot variant quantifies the cost |
| PA: Fewer than 10 games this season | 11.6% of 2025 starter-games (n=5046) | 12.2% of PA absolute error; bias +0.035, MAE 0.578 | lineup position / playing time uncertainty | hypothesis | known-slot models need a confirmed lineup; prev-slot variant quantifies the cost |
| PA: Returning after 11+ days (injury proxy) | 1.9% of 2025 starter-games (n=839) | 2.1% of PA absolute error; bias +0.016, MAE 0.586 | lineup position / playing time uncertainty | hypothesis | known-slot models need a confirmed lineup; prev-slot variant quantifies the cost |

## 9. Projection Quality Gate (pf_core.gate on 2026 holdout projections)

| Family | n | PASS | REVIEW | BLOCK | MAE PASS / REVIEW | CRPS PASS / REVIEW | Bias PASS / REVIEW | Top findings |
|---|---:|---:|---:|---:|---|---|---|---|
| pitcher:bf | 4,854 | 4,586 | 268 | 0 | 2.581 / 3.233 | 1.8672 / 2.3140 | -0.007 / +0.943 | role:REVIEW 266, confidence:REVIEW 251, freshness:REVIEW 2 |
| pitcher:outs | 4,854 | 4,586 | 268 | 0 | 2.703 / 2.818 | 1.9304 / 2.0325 | -0.005 / +1.243 | role:REVIEW 266, confidence:REVIEW 251, freshness:REVIEW 2 |
| pitcher:k | 4,854 | 4,586 | 268 | 0 | 1.752 / 1.412 | 1.2193 / 0.9575 | +0.032 / +0.556 | role:REVIEW 266, confidence:REVIEW 251, freshness:REVIEW 2 |
| pitcher:bb | 4,854 | 4,586 | 268 | 0 | 1.000 / 0.995 | 0.6823 / 0.6510 | -0.012 / -0.144 | role:REVIEW 266, confidence:REVIEW 251, freshness:REVIEW 2 |
| pitcher:h | 4,854 | 4,584 | 270 | 0 | 1.679 / 1.493 | 1.1689 / 1.0326 | +0.033 / +0.172 | role:REVIEW 266, confidence:REVIEW 251, freshness:REVIEW 2 |
| pitcher:r | 4,854 | 4,586 | 268 | 0 | 1.631 / 1.328 | 1.1053 / 0.8702 | +0.051 / +0.187 | role:REVIEW 266, confidence:REVIEW 251, freshness:REVIEW 2 |
| hitter:PA | 8,000 | 7,942 | 58 | 0 | 0.568 / 0.556 | 0.3634 / 0.3512 | -0.001 / +0.030 | role:REVIEW 53, confidence:REVIEW 38, freshness:REVIEW 5 |
| hitter:H | 8,000 | 7,942 | 58 | 0 | 0.670 / 0.706 | 0.4437 / 0.4659 | +0.017 / -0.050 | role:REVIEW 53, confidence:REVIEW 38, freshness:REVIEW 5 |
| hitter:TB | 8,000 | 7,931 | 69 | 0 | 1.316 / 1.468 | 0.8523 / 0.9822 | +0.047 / -0.306 | role:REVIEW 53, confidence:REVIEW 38, plausibility:REVIEW 11 |
| hitter:HR | 8,000 | 7,942 | 58 | 0 | 0.215 / 0.238 | 0.1061 / 0.1426 | +0.007 / -0.063 | role:REVIEW 53, confidence:REVIEW 38, freshness:REVIEW 5 |
| hitter:R | 8,000 | 7,942 | 58 | 0 | 0.569 / 0.566 | 0.3114 / 0.3199 | +0.008 / -0.067 | role:REVIEW 53, confidence:REVIEW 38, freshness:REVIEW 5 |
| hitter:RBI | 8,000 | 7,942 | 58 | 0 | 0.622 / 0.594 | 0.3440 / 0.3393 | +0.006 / -0.059 | role:REVIEW 53, confidence:REVIEW 38, freshness:REVIEW 5 |
| hitter:HRR | 8,000 | 7,934 | 66 | 0 | 1.483 / 1.428 | 0.9813 / 0.9665 | +0.029 / -0.232 | role:REVIEW 53, confidence:REVIEW 38, plausibility:REVIEW 8 |
| team_runs | 4,854 | 4,852 | 2 | 0 | 2.498 / 3.014 | 1.7247 / 1.8289 | +0.027 / -0.031 | freshness:REVIEW 2 |

Hitter gates were run on a random 8,000-projection sample per family. Team reconciliation (hitter runs vs team runs): {'PASS': 4854}. REVIEW projections for batters faced carry a large positive bias (role changes the gate flags from the pitcher's own history), which is exactly what the role check is for; no projection was BLOCKed in the backtest.

## 10. Experiments

### MLB-PF-P1 — Why does a transparent workload model (pitch-count leash / pitches-per-BF x opponent) miss batters faced so badly?

* **Hypothesis:** Errors concentrate in identifiable roles: openers/bulk relievers, season debuts, spot starters and short rest.
* **Method:** Segment validation errors of model D (opener flag, days since last appearance, debut, layoff, no start history, early exit); add those terms to a dev-fitted Poisson GLM (D_rc).
* **Data:** 9710 dev starts (2023-24), 4852 validation starts (2025).
* **Result:** D validation MAE 2.813 -> D_rc 2.605 (CRPS 2.0345 -> 1.8851). Rest <=2 days elasticity -0.71, rest 3 -0.49, rest 4 -0.34 (log scale). Early exits (<=12 BF) are 4.3% of starts but 16.5% of D's absolute error.
* **Interpretation:** Days since the pitcher's last appearance is the single strongest workload signal: a 'starter' who pitched 1-4 days ago is an opener. Early exits (injury, blow-up) remain unforecastable from box scores.
* **Decision:** adopt

### MLB-PF-P2 — Is the gradient-boosted model (E) better than the corrected transparent model for batters faced and outs?

* **Hypothesis:** Remaining nonlinear workload interactions (rest x recent pitch counts x role) are learnable.
* **Method:** Walk-forward LightGBM refitted every two months from 2023-04 on PIT features; frozen-winner rule on validation CRPS with a significance tie-break toward the transparent model.
* **Data:** 2022-2026 starter box lines.
* **Result:** BF: lowest validation CRPS: E; beats best transparent D_rc by -0.0583 CRPS [-0.0732, -0.0416]. Outs: lowest validation CRPS: F; beats best transparent C by -0.0274 CRPS [-0.035, -0.0195].
* **Interpretation:** The GBM's gain is broad: ordinary starters MAE D_rc 2.54 vs E 2.48; openers MAE D_rc 3.59 vs E 3.16; debuts MAE D_rc 3.22 vs E 2.97. It is frozen for the opportunity families with D_rc kept as the explainable fallback.
* **Decision:** adopt

### MLB-PF-P3 — Does an opponent strikeout adjustment (team K% vs the pitcher's hand, shrunk) and park help strikeout projections?

* **Hypothesis:** Opponent K tendency scales a pitcher's K rate multiplicatively (elasticity ~1, log5).
* **Method:** C = BF(D_rc) x EB-shrunk K/BF; D = C x exp(b1 log opp_factor + b2 log park_factor), b fitted by Poisson likelihood on 2023-24.
* **Data:** Opponent K% vs LHP/RHP from the 2024-26 PA feed, overall team K% for earlier seasons; park K factor from prior games at the venue.
* **Result:** Fitted elasticities: opponent 1.01, park 0.45. Validation CRPS C 1.2570 -> D 1.2386; MAE 1.797 -> 1.765.
* **Interpretation:** The opponent elasticity is almost exactly the theoretical 1.0, and the adjustment is a clear, significant gain (not a fitted artefact).
* **Decision:** adopt

### MLB-PF-P4 — Which distribution best represents strikeouts given a mean?

* **Hypothesis:** K | BF ~ Binomial(BF, p) mixed over the BF distribution captures both workload and rate uncertainty.
* **Method:** Development CRPS of Poisson, NB, binned NB, empirical residual and binomial-on-BF mixture (beta-binomial phi grid) for model D.
* **Data:** Development starts 2023-24.
* **Result:** poisson 1.2447; nb 1.2446; nb_binned 1.2444; empirical_resid 1.2451; binom_mix(phi=0.005) 1.2443
* **Interpretation:** The BF mixture is best, but every family is within ~0.1% CRPS: the mean is what matters; the shape is not the bottleneck.
* **Decision:** adopt

### MLB-PF-P5 — Do pitch-level quality signals (prior whiff%, fastball velocity change) improve strikeout projections beyond K%?

* **Hypothesis:** Whiff rate beyond what K% shows, and velocity drops, predict strikeouts.
* **Method:** Poisson offset GLM on top of frozen D: fit 2026-08-25..09-08, test 09-09..09-27 (only window with Statcast in the repo).
* **Data:** 379 fit / 468 test starts with >=2 prior Statcast starts.
* **Result:** Test MAE D 1.8326 vs D+Statcast 1.8322; MAE diff -0.0004 [-0.0133, +0.0116]; Poisson NLL diff +0.0018 [-0.0041, +0.0077].
* **Interpretation:** No detectable gain; the window is 7 weeks, so this is 'no evidence of value at this depth', not proof of no value.
* **Decision:** insufficient evidence

### MLB-PF-P6-k — Is the frozen pitcher_strikeouts model better than the production pitcher engine (incumbent)?

* **Hypothesis:** Role-aware workload and opponent-adjusted rates beat the incumbent's single recency-weighted outs mean.
* **Method:** Matched 2026 holdout starts; incumbent = lib/research/pitcher_prop_projection.py re-run walk-forward (retroactive, as-of first pitch-60m). Rung RPS over the incumbent's own rungs, MAE, Brier at Kalshi rungs; game-clustered bootstrap.
* **Data:** 4524 matched starts, 2420 games.
* **Result:** MAE 1.7471 vs 1.7985 (-0.0513 [-0.0628, -0.0397]); rung RPS 1.1735 vs 1.2059 (-0.0324 [-0.0390, -0.0255]); Brier at Kalshi rungs 0.1536 vs 0.1578; bias 0.0367 vs 0.1253. Versus the W1-D research challenger: RPS -0.0245 [-0.0313, -0.0171].
* **Interpretation:** Better than the incumbent on every metric with intervals excluding zero; the incumbent rerun is retroactive, so this is a walk-forward comparison, not an archived-forecast one.
* **Decision:** adopt

### MLB-PF-P6-bf — Is the frozen batters_faced model better than the production pitcher engine (incumbent)?

* **Hypothesis:** Role-aware workload and opponent-adjusted rates beat the incumbent's single recency-weighted outs mean.
* **Method:** Matched 2026 holdout starts; incumbent = lib/research/pitcher_prop_projection.py re-run walk-forward (retroactive, as-of first pitch-60m). Rung RPS over the incumbent's own rungs, MAE, Brier at Kalshi rungs; game-clustered bootstrap.
* **Data:** 4524 matched starts, 2420 games.
* **Result:** MAE 2.5927 vs 2.8214 (-0.2287 [-0.2628, -0.1944]); rung RPS 1.6889 vs 1.9378 (-0.2489 [-0.2677, -0.2288]); Brier at Kalshi rungs 0.1473 vs 0.1642; bias 0.0178 vs 0.1759. Versus the W1-D research challenger: RPS -0.0808 [-0.0982, -0.0625].
* **Interpretation:** Better than the incumbent on every metric with intervals excluding zero; the incumbent rerun is retroactive, so this is a walk-forward comparison, not an archived-forecast one.
* **Decision:** adopt

### MLB-PF-P6-outs — Is the frozen outs model better than the production pitcher engine (incumbent)?

* **Hypothesis:** Role-aware workload and opponent-adjusted rates beat the incumbent's single recency-weighted outs mean.
* **Method:** Matched 2026 holdout starts; incumbent = lib/research/pitcher_prop_projection.py re-run walk-forward (retroactive, as-of first pitch-60m). Rung RPS over the incumbent's own rungs, MAE, Brier at Kalshi rungs; game-clustered bootstrap.
* **Data:** 4524 matched starts, 2420 games.
* **Result:** MAE 2.71 vs 2.8346 (-0.1246 [-0.1501, -0.1018]); rung RPS 1.7372 vs 1.8135 (-0.0763 [-0.0914, -0.0631]); Brier at Kalshi rungs 0.176 vs 0.1821; bias 0.0101 vs 0.1841. Versus the W1-D research challenger: RPS -0.0331 [-0.0446, -0.0221].
* **Interpretation:** Better than the incumbent on every metric with intervals excluding zero; the incumbent rerun is retroactive, so this is a walk-forward comparison, not an archived-forecast one.
* **Decision:** adopt

### MLB-PF-H1 — How much does knowing the batting slot (confirmed lineup) improve hitter projections?

* **Hypothesis:** PA depends strongly on slot; using only the previous slot loses accuracy when lineups change.
* **Method:** Same model D with the post-game official slot vs the player's previous slot (no lineup information).
* **Data:** 2025 validation and 2026 holdout starters.
* **Result:** PA (validation): CRPS prev-slot minus known-slot +0.0099 [+0.0089, +0.0110], slot changed in 41% of games; H (validation): CRPS prev-slot minus known-slot +0.0006 [+0.0004, +0.0008], slot changed in 41% of games; TB (validation): CRPS prev-slot minus known-slot +0.0008 [+0.0004, +0.0011], slot changed in 41% of games; R (validation): CRPS prev-slot minus known-slot +0.0005 [+0.0003, +0.0006], slot changed in 41% of games; RBI (validation): CRPS prev-slot minus known-slot +0.0001 [-0.0001, +0.0002], slot changed in 41% of games; HRR (validation): CRPS prev-slot minus known-slot +0.0013 [+0.0007, +0.0019], slot changed in 41% of games
* **Interpretation:** Lineup confirmation is worth a measurable amount for PA (and less for rate stats). Forecasts made before lineups post must use the prev-slot variant and be labelled.
* **Decision:** adopt

### MLB-PF-H2 — Do opposing-starter quality, park and platoon adjust hitter rates usefully?

* **Hypothesis:** Hit rates scale with the starter's hits-allowed rate over his share of PAs, park and handedness.
* **Method:** D = C x exp(sum b_i x share x log factor_i); b fitted on 2024 (from May).
* **Data:** Starter PIT rates from the pitcher frame; park factors from prior games at the venue; league platoon multipliers fitted on 2024 PAs.
* **Result:** Hits elasticities sp_h +0.07, park_h +0.74, plat -0.14, sp_k -0.59, icpt -0.01. Validation CRPS C 0.4565 -> D 0.4552.
* **Interpretation:** Context adds a small, real improvement; most hitter variance is irreducible at the game level.
* **Decision:** adopt

### MLB-PF-H3 — Which hitter distributions are best: Poisson/NB on the mean, empirical, binomial-on-PA, or TB convolution?

* **Hypothesis:** Hits ~ Binomial(PA, p) mixed over PA; total bases = convolution of per-PA base outcomes.
* **Method:** Development CRPS by candidate for model D.
* **Data:** 2024 (from May) starters.
* **Result:** Hits: poisson 0.4525; nb 0.4525; empirical_resid 0.4516; binom_mix(phi=0.01) 0.4512. TB: poisson 0.9147; nb 0.8772; empirical_resid 0.8762; tb_mix 0.8751
* **Interpretation:** Structural (binomial / convolution) shapes are at least as good as fitted ones and are coherent by construction.
* **Decision:** adopt

### MLB-PF-H4 — Are the frozen hitter models better than the production hitter engine?

* **Hypothesis:** Per-PA EB rates with structural PA beat the incumbent's simulation (known +0.135 hits / -0.151 RBI bias, HITTER_W2_RESULTS).
* **Method:** Matched 2026 player-games where the incumbent re-run (archived pregame inputs) is PROJECTED; full CRPS from both pmfs.
* **Data:** PA: n=8883; H: n=8883; TB: n=8883; HR: n=8883; R: n=8883; RBI: n=8883
* **Result:** PA: CRPS 0.3706 vs 0.4595 (-0.0888 [-0.1038, -0.0756]), MAE 0.5775 vs 0.6659, bias -0.0129 vs 0.4208; H: CRPS 0.4535 vs 0.4645 (-0.0110 [-0.0140, -0.0081]), MAE 0.6827 vs 0.6948, bias -0.01 vs 0.1352; TB: CRPS 0.8623 vs 0.8765 (-0.0142 [-0.0175, -0.0109]), MAE 1.3221 vs 1.3296, bias -0.0004 vs -0.0155; HR: CRPS 0.1072 vs 0.1098 (-0.0026 [-0.0034, -0.0019]), MAE 0.2148 vs 0.1869, bias 0.0025 vs -0.0366; R: CRPS 0.3197 vs 0.3288 (-0.0091 [-0.0116, -0.0065]), MAE 0.5766 vs 0.5614, bias -0.0097 vs -0.1041; RBI: CRPS 0.3493 vs 0.3632 (-0.0139 [-0.0166, -0.0111]), MAE 0.6221 vs 0.5747, bias -0.0142 vs -0.1511
* **Interpretation:** Consistent with the W2 audit: the incumbent's level biases dominate its error.
* **Decision:** adopt

### MLB-PF-T1 — What does each layer of team-run context add (offence x opponent run prevention, starter, park, schedule adjustment)?

* **Hypothesis:** Following RSCH-0015, opponent strength helps the mean but may not help probability scores.
* **Method:** B EWMA vs C offence x defence (log5 on runs) vs D (+ starter share x starter runs factor + park + home) vs D_ridge (pf_core rolling ridge, weekly refits).
* **Data:** Team-games 2022-2026.
* **Result:** C_vs_B|validation: CRPS -0.0105 [-0.0167, -0.0044], MAE -0.0191 [-0.0292, -0.0092]; D_vs_C|validation: CRPS -0.0061 [-0.0102, -0.0021], MAE -0.0029 [-0.0095, +0.0036]; D_ridge_vs_D|validation: CRPS +0.0037 [+0.0005, +0.0073], MAE +0.0023 [-0.0030, +0.0077]; C_vs_B|holdout: CRPS -0.0116 [-0.0178, -0.0060], MAE -0.0211 [-0.0307, -0.0116]; D_vs_C|holdout: CRPS -0.0085 [-0.0121, -0.0046], MAE -0.0106 [-0.0170, -0.0041]; D_ridge_vs_D|holdout: CRPS +0.0015 [-0.0012, +0.0041], MAE -0.0006 [-0.0050, +0.0035]
* **Interpretation:** Offence x opponent run prevention (C) improves both CRPS and MAE over the team's own EWMA on both splits; starter + park (D) improves CRPS on both splits and MAE only on holdout. Unlike RSCH-0015 (frozen NB), the probability gain survives because the distribution is fitted per model on development. Ridge schedule adjustment does not beat the raw shrunk factors (validation +0.0037 [+0.0005, +0.0073]).
* **Decision:** adopt

### MLB-PF-T2 — Should the game total be the convolution of team distributions or a direct NB on the summed mean?

* **Hypothesis:** Independence is adequate (RSCH-0010: correlation ~0.13).
* **Method:** Holdout CRPS of the convolution vs a direct NB with alpha fitted on development totals.
* **Data:** 2025/2026 games.
* **Result:** Validation diff +0.0001 [-0.0011, +0.0011]; holdout diff +0.0005 [-0.0006, +0.0015]; direct NB alpha 0.1295.
* **Interpretation:** The two are near-identical; the convolution is kept because it is coherent with the team projections.
* **Decision:** adopt

### MLB-PF-T3 — Is the team-run model better than the archived production projection?

* **Hypothesis:** RSCH-0033 found the production mean biased (-0.33 runs) with slope ~1.
* **Method:** Only archived projections whose file createdAt precedes first pitch (genuinely pregame); incumbent distribution = NB with RSCH-0010's 0.2815 dispersion.
* **Data:** 614 team-games, 307 games (2026-07-30..09-27).
* **Result:** CRPS 1.6209 vs 1.6379 (-0.0169 [-0.0497, +0.0140]); MAE 2.3265 vs 2.3033 (+0.0233 [-0.0309, +0.0761]); bias 0.0838 vs -0.3364.
* **Interpretation:** The model removes the incumbent's level bias (-0.34 runs, matching RSCH-0033's -0.33) but the CRPS difference is not significant on this short archived-pregame window (parity).
* **Decision:** insufficient evidence

### MLB-PF-T4 — Do hitter run projections reconcile with team run projections?

* **Hypothesis:** Sum of starters' projected runs ~= team runs x starters' share of runs.
* **Method:** pf_core.gate.team_reconciliation per team-game on the 2026 holdout.
* **Data:** 4854 team-games.
* **Result:** Starters score 94.4% of runs; mean sum of starters' R 4.2465 vs team 4.5068; ratio 0.9985; correlation 0.9302; reconciliation {'PASS': 4854}.
* **Interpretation:** Hitter and team models are built independently yet agree in level; per-game spread differs because team context (opponent run prevention) is richer than the hitter R model.
* **Decision:** adopt

## 11. Failed experiments and negative results

* **Pitch-level Statcast (whiff%, velocity change) for strikeouts.** No gain on 468 late-2026 test starts (-0.0004 [-0.0133, +0.0116] MAE). → Do not build Statcast features until a multi-season pitch archive exists (Savant blocked; repo holds 7 weeks).
* **Structural pitch-count leash model for batters faced (C/D).** Pitches EWMA / pitches-per-BF ignores role; it over-projects openers by ~3.5 BF. → Workload models must condition on days since last appearance and recent relief usage.
* **Schedule-adjusted ridge team ratings (D_ridge).** No improvement over raw shrunk offence/defence factors: +0.0037 [+0.0005, +0.0073] CRPS on validation. → Keep the simpler PIT factors; MLB schedules are balanced enough that schedule adjustment matters little.
* **Pitcher walks: black-box gain.** lowest validation CRPS: E; advantage over transparent D not significant (-0.0019 [-0.004926731731763374, 0.0010387264644427113]) -> D → Walk rate is dominated by noise at game level; the transparent model is frozen.

## 12. Forward projections (`results/projections_latest.json`, sift.projections.v1)

Slate 2026-10-16: 2026 postseason slate (most recent archived). Generated after the archived slate date using ONLY data dated before it; lineups unconfirmed; may already be final — not a live pregame forecast. Inputs through: {'pitchers': '2026-10-10', 'hitters': '2026-09-27', 'teams': '2026-09-27'}.

**TB @ CLE** (AL Championship Series, 2026-10-17T00:00:00+00:00):
* CLE team_runs: mean 3.95, 10–90% 1–8, P(k+) {'2': 0.7941, '3': 0.637, '4': 0.4852, '5': 0.3546, '6': 0.2508, '7': 0.1727}, reconciliation PASS (ratio 0.942)
* TB team_runs: mean 4.228, 10–90% 1–8, P(k+) {'2': 0.8151, '3': 0.6678, '4': 0.5209, '5': 0.3906, '6': 0.2837, '7': 0.2008}, reconciliation REVIEW (ratio 0.817)
* TOTAL game_total: mean 8.179, 10–90% 3–14, P(k+) {'7': 0.6104, '8': 0.5108, '9': 0.417, '10': 0.3327, '11': 0.26}

| Pitcher | Team | Family | Mean | 10–90% | Selected rungs P(k+) | Gate |
|---|---|---|---:|---|---|---|
| Shawn Armstrong | CLE | batters_faced | 9.003 | 4–14 | {'18': 0.0175, '20': 0.0025, '22': 0.0, '24': 0.0, '26': 0.0} | REVIEW (freshness) |
| Nick Martinez | TB | batters_faced | 19.515 | 15–24 | {'18': 0.73, '20': 0.52, '22': 0.3025, '24': 0.1475, '26': 0.06} | REVIEW (freshness) |
| Shawn Armstrong | CLE | outs | 6.855 | 2–11 | {'12': 0.075, '15': 0.0125, '16': 0.0075, '17': 0.0025, '18': 0.0025, '19': 0.0} | REVIEW (freshness) |
| Nick Martinez | TB | outs | 14.445 | 10–19 | {'12': 0.8075, '15': 0.53, '16': 0.4, '17': 0.2725, '18': 0.2, '19': 0.1225} | REVIEW (freshness) |
| Shawn Armstrong | CLE | pitcher_strikeouts | 1.603 | 0–3 | {'3': 0.2314, '4': 0.0943, '5': 0.0327, '6': 0.0099, '7': 0.0027, '8': 0.0007, '9': 0.0001} | REVIEW (freshness) |
| Nick Martinez | TB | pitcher_strikeouts | 3.457 | 1–6 | {'3': 0.664, '4': 0.454, '5': 0.2716, '6': 0.1431, '7': 0.067, '8': 0.0281, '9': 0.0107} | REVIEW (freshness) |

18 hitters per family are projected from each team's last regular-season lineup (2026-09-27, a rest-day-heavy final game) and every one is REVIEW (lineup unconfirmed; inputs >36 h old). Some hitter names are null because the repo has no player-name table for players never listed in a prop market; ids are exact.

## 13. pf_core observations (library not modified)

* `gate.HARD_MAX_MEAN[('MLB','hits')] = 3.0` is ambiguous between hitter hits and pitcher hits allowed (mean ≈ 5) — a pitcher family named `hits` would be BLOCKed. Worked around by naming the family `pitcher_hits_allowed` (no ceiling applied). Suggest explicit pitcher keys.
* `gate.evaluate` only BLOCKs on `team_verified is False`; `None` (unknown roster) passes silently. Forward hitter projections add an explicit lineup REVIEW finding instead.
* `DistBatch._index` clips outcomes outside the support, so `logpmf`/`pit` silently use the edge mass for y beyond K (CRPS is unaffected). Supports here are sized so this never binds.
* `pit.lagged_features` with `season_col` returns a frame with a reset index (it merges); callers must not rely on index alignment with the input.
* The gate's freshness check uses wall-clock time unless `now_utc` is passed; backtests must pass it (done here).

## 14. Ranked priorities

| Rank | Item | Impact | Evidence | Complexity | Data |
|---:|---|---|---|---|---|
| 1 | Prospective shadow of the frozen pitcher K/BF/outs models against archived pregame probables (data/slates) and the existing hash-chained shadow store | Converts a walk-forward win over the incumbent into archived-forecast evidence; required before any display change | MLB-PF-P6: holdout wins vs champion with CIs excluding zero | low (reuse project.py + workflow) | daily slates (already archived) |
| 2 | Archive confirmed lineups for every game (pregame_lineups_*) and re-score hitters on genuinely pregame slots | Hitter families are SHADOW VALIDATION only because slots are post-game | MLB-PF-H1 slot-knowledge gap | low | LINEUP_CONFIRMATION checkpoint already captures; needs to persist all games |
| 3 | Injury / IL and pitch-count restriction feed | Early exits (<=12 BF) are ~5% of starts but ~18% of BF error and are invisible to every model | MLB-PF-P1 segments | medium | missing (no source in repo) |
| 4 | Postseason leash and postseason hitter box lines | Forward projections in October use regular-season parameters and stale hitter form | projections_latest.json labels | medium | research_cache has only pitcher postseason lines |
| 5 | Multi-season Statcast backfill (whiff/CSW/velo) once GitHub-hosted archives allow | Unknown; 7-week test is null | MLB-PF-P5 | high | blocked network |

## 15. Reproduce

```bash
cd research/projection_foundation
python -m mlb.run_walkforward --stage all   # ~45 min on 2 cores; caches in .cache/ (gitignored)
python -m mlb.project                        # forward projections for the newest archived slate
python -m mlb.report                         # this file
python -m pytest -q tests/
```

Automation: `.github/workflows/projection-foundation.yml` (daily in season + workflow_dispatch; publishes `projections/mlb/latest.json`, a timestamped copy and `summary/mlb/latest.json` to the orphan branch `projection-data` only when content changes; inert until merged).
