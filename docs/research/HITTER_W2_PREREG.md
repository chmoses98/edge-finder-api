# MLB hitter W2 challenger: preregistration (frozen before any held-out scoring)

**Status: research only.** Nothing here changes production hitter outputs, the app export, publication,
settlement, staking or model authority. The incumbent engine (`docs/HITTER_SIMULATION_ENGINE.md`, code at
`origin/main` f7371b2) is not modified.

This document is committed on its own, before the challenger's held-out forecasts are scored. The audit that
motivates it is `docs/research/HITTER_ENGINE_AUDIT.md` (previous commit).

## 1. Errors the audit isolated (the only things this challenger changes)

On the full eligible population (every confirmed-lineup starter, 2026-08-16 to 09-27, not only market-listed
hitters), the incumbent's errors decompose into four mechanisms:

| Isolated error | Incumbent mechanism | Challenger change |
|---|---|---|
| PA count too high (about +0.4 PA per starter) | 9 full innings for both teams, no home-9th skip, no walk-off, no pinch-hitting or substitution, BB and K too frequent | A two-team game simulation. The home team skips the bottom of the 9th when it leads, and the game ends on a walk-off. Extra innings use the automatic runner. The starter's PA ordinal in each slot follows an empirical survival curve fitted on 2024-25. |
| Per-PA hit rate too high vs the bullpen | Bullpen PAs use a generic pitch mix, no bullpen quality, and the starter's platoon and quality nudge | Bullpen PAs use the opposing team's bullpen allowed rates, as-of and shrunk |
| Per-PA rates built on 1 to 80 archived PAs (Statcast archive starts 2026-08-11) | The "season" baseline is the archive window | Season-to-date plus previous-season counts from official play-by-play, with a fitted shrinkage |
| RBI and R far too low; non-HR RBI per runner on base about half of actual | "Advance exactly N bases": no sacrifice flies, productive outs, extra bases taken, errors or double plays | Base-out transitions are sampled from the empirical (event, outs, bases) table fitted on 2024-25 play-by-play. A sampled transition gives post-bases, outs added, runs and RBI. |

HR are also too low under the incumbent (contact-model HR). In the challenger, HR comes from the same per-PA
rates (batter x pitcher log5 + park). No separate HR fix is attempted.

## 2. Models compared

* **Challenger** `MLB_HITTER_W2_PURE_V1`: `lib/research/hitter_w2_engine.py` plus
  `scripts/research/hitter_w2/{features,model,run_hitter_w2}.py`.
  * Per-PA category rates over {K, BB, HBP, 1B, 2B, 3B, HR, OUT}:
    `(cur + w*prev + m_c*league) / (n_cur + w*n_prev + m_c)` for batters, with `w` and per-category `m_c`
    chosen by grid log-likelihood on 2025 using 2024 as the previous season.
  * Opposing starter and opposing bullpen: the same form, with `w` and `m` chosen by grid on 2025.
  * Matchup by log5 against the previous season's league rates.
  * League platoon factor by (batter side, starter hand), applied vs the starter only.
  * Home/road park factor per venue, shrunk with 6,000 PA.
  * Starter BF budget = shrunk expected BF + an empirical 2025 residual.
  * 500 simulations per game, seed `20261011 + gamePk mod 100000`.
* **Baseline** `MLB_HITTER_W2_BASELINE_V1`: season-to-date per-PA rates, shrunk with 50 PA toward the previous
  season's league rates. No previous-season player data, no opponent and no park. The PA distribution comes from
  the previous season's slot x home/away empirical PA.
  * H and HR: binomial given PA.
  * TB: per-PA categorical convolution.
  * RBI and R: Poisson with mean PA x rate.
* **Incumbent**: the existing engine, rerun on the full eligible population with its own production functions
  (`scripts/research/hitter_w2/incumbent_rerun.py`, 500 simulations). It is available only where its inputs
  exist: pregame slate captures with an official lineup, plus the Statcast archive from 2026-08-11.

Parameters are fitted once, deterministically, by `run_hitter_w2.py fit` on 2024 and 2025 only. They are frozen
to `data/research/hitter_w2/params_v1.json`, and its sha256 is the `model_frozen_hash`. No parameter is
fitted, tuned or selected on 2026 data, on market prices or on market agreement.

## 3. Data, population and holdout

* **Sources.** Sports only: MLB Stats API game feeds (`data/research/hitter_w2/mlb_feed/`), fetched by
  `.github/workflows/research-hitter-w2-backfill.yml`. The incumbent rerun also reads the repository's
  pregame slate captures and Statcast archive, exactly as production does.
* **As-of.** Every input comes from games on earlier dates. Lineup and slot are the official starting lineup.
* **Population.** Every starting batter (slots 1 to 9) in every 2026 regular-season game whose feed is Final.
  Forecasts are conditional on starting.
  * No market listing, market threshold or settlement is used for selection.
  * The market-listed cohort is a reported subgroup only. It is defined pregame: a PROJECTED
    hitter-projection snapshot row exists for that player-game.
* **Holdout.** The whole 2026 regular season. It is untouched by fitting.
  * **Primary holdout window H1**: 2026-03-25 to 2026-08-11. Nothing in this window was inspected by the
    audit.
  * **Secondary window H2**: 2026-08-12 to 2026-09-27. This is the window the audit used to diagnose the
    incumbent. The incumbent comparison is only possible here, on matched player-games where the incumbent
    has a PROJECTED forecast.
* **Statistics** (one row per player-game-statistic): PA, H, TB, HR, RBI, R.

## 4. Metrics

Every metric is computed per statistic, never pooled:

* bias (mean signed error of the mean);
* MAE and RMSE of the mean;
* discrete CRPS from the full pmf;
* Brier score and log loss at ONE outcome-blind threshold per statistic: PA>=5, H>=1, TB>=2, HR>=1,
  RBI>=1, R>=1;
* p10-p90 coverage and randomized-PIT 10-90 coverage;
* reliability deciles and ECE at the threshold.

Uncertainty comes from a paired percentile bootstrap that resamples whole games (1,000 reps, seed 20261011).
Joint coherence and correlation checks are reported as diagnostics:

* every challenger draw satisfies TB >= H >= HR;
* team RBI <= team runs, and the sum of starters' R + substitutes' R = team runs;
* simulated E[H*RBI] and E[H*R] within a player, and E[R_i * RBI_(i+1)] for adjacent teammates, compared with
  the empirical moments.

## 5. Decision rule (strict, frozen)

**Primary metrics** are CRPS, MAE, RMSE, Brier and log loss for H, TB, HR, RBI and R: 25 metrics.

**ACCEPTED_CHALLENGER (research only)** requires all of the following.

1. **vs incumbent (H2, matched).**
   * No primary metric is worse with a 95% CI excluding 0.
   * CRPS for H and for RBI are both better with a CI excluding 0.
   * |bias| for H and for RBI are both smaller, with a CI excluding 0.
2. **vs baseline (H1, primary window).**
   * No primary metric is worse with a CI excluding 0.
   * CRPS for RBI is better with a CI excluding 0.
3. **vs baseline (H2).** No primary metric is worse with a CI excluding 0.
4. **Runtime non-leakage.** The pure_gate `rerun` verdict is `PASS_RUNTIME_NONLEAKAGE`, and the negative
   control responds to the mutation.

**Other verdicts.**

* **REJECTED**: any criterion in 1 to 3 fails.
* **INCONCLUSIVE**: no criterion fails outright, but a required improvement has a CI that includes 0.
* **BLOCKED_DATA**: data are missing.

PA metrics, subgroups and coverage are reported but are not promotion criteria. A result is never promoted:
the best outcome is research-only shadow collection.

## 6. Preregistered subgroups (reported, not used for the decision)

* lineup slot (1-2, 3-5, 6-9);
* opposing starter hand (L/R);
* home/away;
* month;
* pregame-confirmed-lineup capture available in `data/slates` vs not (2026);
* market-listed cohort vs not (H2);
* challenger vs incumbent by slot on H2.

## 7. Known limitations, accepted in advance

* **Lineup.** The official starting lineup comes from the post-game feed. It stands in for the pregame confirmed
  lineup, which `data/slates` records for 2026 dates only. Late scratches are therefore invisible. The size of
  this proxy is reported as a subgroup.
* **Weather.** Not used: no as-of weather history exists in the repository.
* **Postseason.** Not evaluated: the feed backfill is regular season only.
