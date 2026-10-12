# MLB hitter V2 challenger: preregistration (frozen before any held-out scoring)

**Status: research only.** Nothing here changes production hitter outputs, the hitter snapshot scheduler, the app
export, publication, settlement, staking, model authority or bet routing. No challenger is promoted by this work,
whatever the verdict. The production hitter engine (`docs/HITTER_SIMULATION_ENGINE.md`, code at `git:f7371b2`) and
the frozen W2 challenger (`lib/research/hitter_w2_engine.py`, `scripts/research/hitter_w2/`) are imported, never
modified.

This document is committed on its own, after the design work on training data and **before** any 2026 held-out
forecast of the V2 challenger is produced or scored. The commit that adds it is the preregistration commit; the
results document cites it.

## 0. Lineage and what is new

* **Audit and first challenger:** PR #285 (`claude/w2-mlb-hitter-engine`), `docs/research/HITTER_ENGINE_AUDIT.md`,
  `docs/research/HITTER_W2_PREREG.md`, `docs/research/HITTER_W2_RESULTS.md`. The W2 challenger
  `MLB_HITTER_W2_PURE_V1` fixed the champion's biggest errors (two-team game, home-9th skip, walk-off, empirical
  base-out transitions, bullpen team rates) and was **REJECTED** only on "MAE of the mean" for HR/RBI/R, a metric
  that rewards the champion's downward-biased means on zero-heavy counts.
* **Open structural errors after W2** (from #285's results and next steps, and from the dev loop below):
  residual PA over-projection (~+0.06 per starter); substitution blind to score margin, game stage and the
  batter; inning-ending runner outs never simulated; per-PA walk/strikeout rates not calibrated; platoon applied
  as a league factor vs the starter only (no individual splits, no reliever handedness); bullpen = one
  season-long team rate with no recency and no hand mix; BB, K and H+R+RBI not projected by W2.
* **Branch base.** This branch is cut from `origin/claude/w2-mlb-hitter-engine` (head `fd1c6e9`), not from
  `main`, because the harness is not cleanly separable: the sports-only feed backfill
  (`data/research/hitter_w2/mlb_feed/`), the frozen W2 engine/model/params that this study must run as an arm,
  the champion rerun tooling and its committed output all exist only on that branch. Everything V2 adds is in new
  files (`lib/research/hitter_v2_engine.py`, `scripts/research/hitter_v2/`, `data/research/hitter_v2/`,
  `tests/research/test_hitter_v2_engine.py`, these docs); no W2 file is edited. The PR targets the W2 branch so
  its diff shows only V2.

## 1. Arms

| Arm | Version | Frozen as |
|---|---|---|
| **V2 challenger** | `MLB_HITTER_V2_PURE` | `data/research/hitter_v2/params_v2.json`, sha256 in Section 8 (fitted on 2024–2025 only by `run_v2.py fit`) |
| **Frozen W2 challenger** (#285, rejected) | `MLB_HITTER_W2_PURE_V1` | `data/research/hitter_w2/params_v1.json`, `sha256:e34c413e9d96727f581090710d43c54bc515276479176e8286d3b6e4d73c73e5`, its own code imported unchanged |
| **Production champion** | hitter simulation engine, `git:f7371b2` | Rerun with its own production functions exactly as the #285 audit rerun did (same capture selection, seeds, 500 sims), plus the H+R+RBI pmf from the same simulated draws (`scripts/research/hitter_v2/champion_hrr_rerun.py`). Fidelity requirement: every PA/H/TB/HR/RBI/R/BB/K pmf identical to the committed #285 rerun. |
| **Simple baseline** | `MLB_HITTER_W2_BASELINE_V1+BB_K_HRR` | The #285 baseline (season-to-date per-PA rates shrunk 50 PA to the previous-season league, previous-season slot×home PA pmf) for PA/H/TB/HR/RBI/R; extended for BB and K (binomial given PA, same shrinkage) and HRR (Poisson with mean PA × season-to-date (H+R+RBI)/PA, same shrinkage). |

W2 does not track BB or K in its simulator, so W2 has no BB/K forecasts (its HRR is taken from the same draws).
All four arms are run on **identical inputs** within each population (same lineup, same opposing starter).

## 2. The V2 challenger: structural fixes, in the preregistered priority order

V2 keeps the W2 simulator skeleton (two-team game, home team skips the bottom of the 9th when leading,
walk-off, extra innings with the automatic runner, starter batters-faced budget then bullpen, empirical
(event, outs, bases) → (outs added, post bases, runs, RBI) transition table) and changes only the following.

**1. Plate-appearance count (drives volume error in every stat)**

* **1a. State-dependent substitution hazard** (replaces W2's single survival curve by slot × PA ordinal).
  For each PA of a lineup slot whose starter took the slot's previous PA, P(starter replaced before this PA) is a
  logistic model with one-hot slot (9), slot-PA ordinal bucket (2nd/3rd/4th/5th+), inning bucket (≤4,5,6,7,8,9+)
  × batting-team run-margin bucket (≤−5, −4..−3, −2..−1, 0, +1..+2, +3..+4, ≥+5), and a September indicator
  (expanded 28-man rosters; calendar-known pregame), plus a per-batter log-odds offset
  `log((O + a) / (E + a))` from his as-of observed vs expected removals (current season to date + `w` × previous
  season). Fitted on 2024–2025 at-risk rows (IRLS); (a, w) by fitting-season log-likelihood on the grid
  a ∈ {0.25, 0.5, 1, 2, 5, 10, 20, ∞}, w ∈ {0.5, 1}; then one back-fitting step refits the cell model with the offsets
  held fixed so the offsets do not shift the mean hazard (the dev loop showed the un-refit offsets lowered the
  hazard by ~10%). The sim evaluates the hazard at the simulated inning and margin.
* **1b. Inning-ending runner outs.** Before a PA with 2 outs and runners on, the inning ends (caught stealing /
  pickoff for the 3rd out; the batter leads off the next inning) with the empirical probability q(bases) from
  2024–2025 play-by-play. W2 never ends an inning without a PA.
* **1c. Home-9th truncation / walk-off / extra innings:** kept from W2 (already structural).

**2. Walk-rate (per-PA category) calibration.** A home/away batting factor per category (square root of the
2024–2025 home/away per-PA rate ratio, applied ± to home/away batters), and per-category multiplicative
calibration factors κ (separately vs the starter and vs the bullpen) fitted by iterative proportional scaling so
that the summed predicted category probabilities equal observed counts on the fitting season. A league
environment prior (blend of previous-season league rates with the current season's league counts to date,
strength K_env ∈ {∞, 60,000, 20,000} PA) is selected by fitting-season per-PA log-loss.

**3. Platoon splits.** The batter's rates vs the opposing starter's hand use his own split counts vs that hand
(current + w × previous), shrunk with strength m_bsplit toward (his overall rates × the league platoon factor for
his side and that hand); the starter's rates vs the batter's effective hand (switch hitters bat opposite) are
shrunk with m_psplit toward (his overall rates × the league pitcher-platoon factor). They are combined as
`b_h × p_e / L(h, e)` with the league rates of that hand matchup, so the platoon effect is counted once. Grids:
m_bsplit ∈ {100, 200, 400, 800, 1600, ∞}, m_psplit ∈ {200, 400, 800, 1600, ∞}. Starter handedness = the
handedness of the **probable starter** named in the pregame capture (handedness treated as a static attribute).

**4. Bullpen quality.** The opposing bullpen's rates vs the batter's effective hand from recency-weighted current
season counts (half-life ∈ {∞, 60, 30, 15} days) + w_pen × previous season, shrunk with m_pen toward the league
bullpen rates vs that hand; the batter's rates vs the bullpen mix his L/R splits by the bullpen's as-of share of
LHP relievers vs his side (shrunk with m_mix to the league share), divided by the league rates at the same hand mix.

**5. Baserunner advancement.** Kept from W2 (empirical transition table, which already contains sacrifice flies,
productive outs, extra bases taken, errors, double plays and in-PA steals/caught stealing), plus 1b. Runner speed
is **not** added: the feed extract carries no as-of sprint-speed history, and the only speed proxy (SB attempts)
is confounded with team strategy; this is a declared limitation, not a fix.

All rate hyper-parameters are chosen by coordinate grid search on the fitting season's per-PA multinomial log-loss
(vs starter and vs bullpen PAs of 2025, with 2024 as the previous season), exactly like W2's shrinkage; the W2
shrinkage parameters themselves are re-derived by W2's own routine on the same data and must equal the frozen W2
values (asserted in `run_v2.py fit`). 500 simulations per game, seed `20261012 + gamePk mod 100000`.

**Projected statistics:** PA, H, TB, HR, RBI, R, BB (incl. IBB, as in the box score), K, and HRR = H + R + RBI
from the same simulated draw. Every draw satisfies TB ≥ H ≥ HR, team RBI ≤ team runs, and starters' R +
substitutes' R = team runs (checked on every held-out draw).

## 3. Design loop on training data only (already run; reported here, not a decision input)

`run_v2.py dev`: fit on 2024 + 2025-03..06-30, forecast 2025-07-01..09-28 (1,164 games, 300 sims), W2 structure
refit identically, V2 ablation chain. Output: `data/research/hitter_v2/dev_2025/dev_report.json`.

CRPS (bias) per arm on the dev window (20,952 starter-games); last column paired game-clustered bootstrap
(300 resamples). `v2_1_pa` = W2 rates + V2 PA structure (1a, 1b); `v2_2_pa_cal` adds calibration (2);
`v2_3_pa_cal_platoon` adds platoon (3); `v2_full` adds bullpen (4) = the preregistered V2.

| Stat | actual mean | W2 | baseline | v2_1_pa | v2_2_pa_cal | v2_3_pa_cal_platoon | v2_full | V2 full − W2 ΔCRPS [95% CI] |
|---|---|---|---|---|---|---|---|---|
| PA | 4.015 | 0.3525 (+0.060) | 0.3523 (+0.020) | 0.3463 (+0.041) | 0.3461 (+0.034) | 0.3464 (+0.034) | 0.3461 (+0.033) | −0.0064 [−0.0080, −0.0047] |
| H | 0.886 | 0.4549 (+0.023) | 0.4559 (+0.018) | 0.4540 (+0.019) | 0.4544 (+0.011) | 0.4542 (+0.011) | 0.4542 (+0.012) | −0.0007 [−0.0016, +0.0001] |
| TB | 1.483 | 0.8930 (+0.017) | 0.8945 (+0.012) | 0.8922 (+0.010) | 0.8926 (−0.014) | 0.8931 (−0.013) | 0.8920 (−0.010) | −0.0010 [−0.0026, +0.0003] |
| HR | 0.132 | 0.1151 (−0.004) | 0.1150 (−0.003) | 0.1151 (−0.004) | 0.1151 (−0.008) | 0.1151 (−0.008) | 0.1148 (−0.008) | −0.0002 [−0.0005, +0.0000] |
| RBI | 0.463 | 0.3564 (+0.008) | 0.3639 (+0.006) | 0.3564 (+0.005) | 0.3563 (−0.003) | 0.3567 (−0.003) | 0.3561 (−0.002) | −0.0003 [−0.0010, +0.0002] |
| R | 0.480 | 0.3227 (+0.013) | 0.3238 (+0.002) | 0.3220 (+0.009) | 0.3216 (+0.002) | 0.3221 (+0.002) | 0.3218 (+0.002) | −0.0009 [−0.0016, −0.0003] |
| BB | 0.330 | n/a | 0.2435 (+0.014) | 0.2430 (+0.012) | 0.2429 (+0.017) | 0.2430 (+0.017) | 0.2429 (+0.015) | n/a |
| K | 0.898 | n/a | 0.4401 (−0.025) | 0.4377 (−0.009) | 0.4381 (−0.018) | 0.4381 (−0.019) | 0.4377 (−0.018) | n/a |
| HRR | 1.829 | 1.0065 (+0.044) | 1.0451 (+0.025) | 1.0047 (+0.033) | 1.0049 (+0.010) | 1.0057 (+0.010) | 1.0047 (+0.012) | −0.0019 [−0.0038, −0.0002] |

What the loop changed in the design (all on training data):

* The first dev run used the per-batter removal propensity on top of an un-refit cell model: V2's PA bias was
  +0.062, no better than W2's +0.060. A team-level decomposition showed (i) too few substitutions (0.92 vs 1.09
  replaced slots per away team-game; the propensity offsets averaged −0.29 log-odds, lowering the mean hazard by
  ~10%) and (ii) a September jump in removals (4.1% vs 3.3% per at-risk PA). The back-fitting step and the
  September indicator were added, and the propensity grid was widened (a ∈ … 0.25, 0.5); the run above is the
  final procedure. PA bias fell from +0.060 (W2) to +0.033 (V2), PA CRPS by 0.0064 [0.0047, 0.0080].
* Remaining: starter PA is still ~+0.03 too high. On the fitting half the sim's away team scores 4.44 runs per
  game vs 4.27 actual while its per-PA category rates are calibrated to within ±3 per 1,000; the excess is in
  run/PA generation given the events (sequencing), not in rates or game shape (simulated extra-inning share 10.0%
  vs 10.6% actual, home-skips-the-9th 42% vs 45%, home win 52% vs 54.5%). It is not fixed here.
* Individual batter platoon splits did not help (m_bsplit = ∞ in the final fit: the league platoon factor is used
  for the batter, the pitcher's own split with m_psplit = 1,600); the K_env league-environment blend was not
  selected (∞). These are reported, not removed: V2 is the fitted procedure.
* BB is over- and K under-projected in the dev window (+0.015, −0.018 per starter) with in-sample-calibrated κ:
  the rates drift between season halves. This is a known risk for C5.

## 4. Data, inputs and point-in-time rules

* **Sources (sports only).** MLB Stats API game-feed extracts committed by #285
  (`data/research/hitter_w2/mlb_feed/<season>/{games,batting,pitching,pa}.csv.gz`), and the pregame confirmed
  lineups / probable starters extracted from the committed slate captures into
  `data/research/hitter_v2/pregame_lineups_2026.csv.gz` by `extract_pregame_lineups.py`, which reads only
  gameId, startTime, lineupConfirmedOfficial, confirmedLineup order/playerId/position and the two probable-pitcher
  ids (unit-tested with a key-access spy). No odds, totals, Kalshi quotes or prices, settlements, market-model
  outputs, market listing or market-selected populations are read, directly or indirectly, by V2, W2 or the
  baseline. (The champion's own inputs are those production uses; its known market-selected-population gate is
  bypassed by the rerun, as in #285.)
* **As-of.** Every count (batter, pitcher, bullpen, league, removal propensity, starter BF history) comes from
  games on **earlier dates**: the accumulators are updated only after a whole day has been emitted (unit-tested
  against a brute-force recount).
* **Lineups.** Held-out primary population: the **pregame confirmed lineup** of the latest slate capture strictly
  before first pitch (selection function identical to the champion rerun). A team-game without a capture is not
  scored; when only one side of a game was captured, the other side's lineup in the simulation is that team's
  previous-game official lineup (known pregame), and its starter is the captured probable.
* **Opposing starter.** The **probable starter** of the same capture (not the actual starter). Starter BF
  budgets use the probable's as-of history.
* **Training** uses official starting lineups and actual starters (2024–2025 only).

## 5. Chronological split and populations

* **Training / fitting:** 2024 and 2025 regular seasons only (params frozen, Section 8).
* **Held-out:** 2026 regular season, untouched by fitting and by the design loop.
  * **PIT (primary, point-in-time):** every 2026 team-game with a pregame confirmed-lineup capture whose feed is
    Final — captures exist only from 2026-08-16 (expected ≈ 1,019 team-games, ≈ 520 games, ≈ 9,100
    player-games). Scored player-games: captured batters who started the game (box score); others are excluded
    for every arm alike and counted.
  * **PIT_matched:** PIT player-games where the champion rerun is PROJECTED (expected ≈ 8,880). All champion
    comparisons use this set.
  * **OFF_H1 (secondary, consistency):** every 2026 regular-season team-game 2026-03-25..08-11 with the official
    starting lineup from the post-game feed as the lineup proxy and the feed's probable starter (a documented
    timing proxy, not point-in-time; used only as a "must not be worse" check and for diagnostics). Expected
    ≈ 32,400 player-games, 1,799 games.
* **No subsample.** Compute is small (≈ 0.15 s per game for V2 + W2 at 500 sims; ≈ 2,300 games in total), so the
  full populations are evaluated.
* **Disclosure: 2026 is not pristine for design.** PR #285 published aggregate 2026 results of W2 and the champion
  (both windows), and the structural error list this study works on comes from them; I read those documents. I
  have not produced, inspected or scored any V2 forecast on 2026 data before this commit. Before this commit I did
  run, on 2026 data, only (a) the pregame lineup extraction (no outcomes) and (b) the champion HRR rerun
  (forecasts only, unscored; its fidelity check compares forecasts with forecasts).

## 6. Metrics (per statistic, never pooled across statistics except the stated composite)

* **Primary objective — distribution quality:** discrete **CRPS** = ranked probability score summed over the full
  count ladder 0..kmax (equivalently, the sum over thresholds t ≥ 1 of the Brier score of P(X ≥ t)).
* **Composite:** per player-game sum of CRPS over a set of decision statistics (used where stated).
* **MAE guardrail:** MAE of the **predictive median** (the point forecast that minimizes expected absolute error;
  the #285 failure mode was MAE of the mean, which rewards biased means on zero-heavy counts).
* **Calibration:** randomized-PIT central-80% coverage; for every rung t of the statistic's ladder
  (H 1–4, TB 1–8, HR 1–2, RBI 1–5, R 1–4, BB 1–3, K 1–4, HRR 1–7, PA 1–7), 10-bin reliability ECE and
  calibration-in-the-large (mean p − observed rate).
* Also reported: bias, MAE and RMSE of the mean, ladder-mean Brier, p10–p90 coverage, PIT deciles, the #285
  single-threshold Brier and log loss.
* **Uncertainty:** paired, game-clustered percentile bootstrap, 1,000 resamples of games, seed 20261012,
  difference = V2 − comparator (negative = V2 better for CRPS/MAE).

## 7. Decision rule (strict, frozen)

Decision statistics **D** = {H, TB, HR, RBI, R, BB, K, HRR}. PA is reported, not decided. "Better" = 95% CI
upper bound < 0; "worse" = 95% CI lower bound > 0.

**ACCEPTED_RESEARCH_ONLY** requires every criterion below. It would authorize nothing beyond research-only shadow
collection; it is not a promotion.

* **C1 — vs champion (PIT_matched).** CRPS **better** for H, TB, RBI and R; CRPS **not worse** for HR, BB, K and
  HRR.
* **C2 — vs frozen W2.** On PIT and on OFF_H1: CRPS not worse for any statistic in D that W2 projects (H, TB, HR,
  RBI, R, HRR). Composite CRPS over those six: **better** on PIT, not worse on OFF_H1.
* **C3 — vs baseline.** On PIT and on OFF_H1: CRPS not worse for any statistic in D. Composite CRPS over D:
  **better** on PIT, not worse on OFF_H1.
* **C4 — MAE guardrail (non-inferiority).** For every statistic in D, the upper 95% CI bound of
  ΔMAE(median) ≤ 2% of the comparator's MAE(median): vs the champion on PIT_matched, and vs the baseline on PIT
  and on OFF_H1. Failing to demonstrate non-inferiority is a FAIL.
* **C5 — calibration guardrail (V2 itself, PIT and OFF_H1, every statistic in D).** |PIT-in-80 − 0.80| ≤ 0.03;
  max-over-rungs ECE ≤ 0.025; max-over-rungs |calibration-in-the-large| ≤ 0.02.
* **C6 — runtime non-leakage.** `pure_gate rerun` verdict `PASS_RUNTIME_NONLEAKAGE` with the market inputs of
  #285 (slate sportsbook totals, Kalshi hitter snapshot rows) deleted / randomized, and the negative control
  (`--control-read-market`) responding to every mutation.
* **C7 — point-in-time audit.** Every scored PIT record's lineup capture time < scheduled first pitch; as-of and
  extractor tests pass.

**Verdicts.** **REJECTED** if any criterion FAILS (a "better"/"not worse" CI on the wrong side, a guardrail
outside tolerance, non-inferiority not shown). **INCONCLUSIVE** if nothing fails but a required "better" has a CI
that includes 0. **BLOCKED_DATA** if a required input is missing. The rule is applied mechanically by
`scripts/research/hitter_v2/evaluate_v2.py::decide` (committed in the code commit just before this document) for
C1–C5; C6 and C7 are recorded from the `pure_gate` report and the test run.

**Strictness relative to #285.** Stricter: three comparators instead of two (adds the frozen W2), eight decision
statistics instead of five (adds BB, K, HRR), required CRPS improvements on four statistics vs the champion (#285:
two), composite improvements vs W2 and the baseline, a non-inferiority bound on MAE that must be *demonstrated*
(upper CI ≤ margin, not merely "not significantly worse"), and absolute calibration guardrails on every ladder
rung (#285 had none). Different: the MAE criterion uses the predictive median instead of the mean (the mean is the
wrong point forecast for absolute error). Because that is a metric change, the **#285 rule is also evaluated
verbatim** (no metric among CRPS, MAE of the mean, RMSE, single-threshold Brier and log loss worse vs the champion
or vs the baseline in either window; CRPS and |bias| for H and RBI better vs the champion) and reported next to
the verdict; it cannot rescue a rejection and is not the decision rule.

## 8. Frozen artifacts

* `data/research/hitter_v2/params_v2.json` — `sha256:b016ea53eb9708817810147e06aedebd198909fe8c58c4a284013e6ca497f934`
  (`run_v2.py fit`; fitted on 2024–2025 only; the W2 shrinkage re-derived inside it equals the frozen W2 values).
  Fitted values: batter/pitcher/bullpen W2 shrinkage as in `params_v1.json`; hazard on 273,397 at-risk rows
  (9,573 removals); propensity a = 1, w = 0.5; K_env = ∞; m_bsplit = ∞; m_psplit = 1,600; bullpen half-life 30
  days, w_pen = 0.25, m_pen = 2,000, m_mix = 50; κ_sp, κ_pen in the file. Fitting-season per-PA log-loss 1.46427
  (W2 rate form) → 1.46391 (V2).
* `data/research/hitter_w2/params_v1.json` — `sha256:e34c413e9d96727f581090710d43c54bc515276479176e8286d3b6e4d73c73e5` (W2, unchanged).
* `data/research/hitter_v2/pregame_lineups_2026.csv.gz` — `sha256:197b881a81d99d91eb8b88618c2d0b11cfbe72ad0b253267e8467cd239e8f298`: 1,029 captured team-games (9,261
  lineup rows), 527 games, captures 2026-08-16..09-30 (10 postseason team-games have no feed and drop out).
* Champion: `scripts/research/hitter_v2/champion_hrr_rerun.py` at this commit; its output is committed with the
  results and must pass the fidelity check (identical marginals to `data/research/hitter_w2/audit/incumbent_rerun.jsonl.gz`).
* Code: `lib/research/hitter_v2_engine.py`, `scripts/research/hitter_v2/{data_v2,model_v2,run_v2,scoring_v2,evaluate_v2}.py`
  at this commit. Forecast commands:
  `run_v2.py forecast --population PIT --out <pit.pkl.gz>`, `run_v2.py forecast --population OFF_H1 --out <off.pkl.gz>`
  (500 sims, seed 20261012), then `evaluate_v2.py --pit-cache … --off-cache … --champion … --out-dir data/research/hitter_v2/holdout_2026`.

## 9. Preregistered diagnostics (reported, not decision inputs)

One per structural error, V2 vs W2 vs champion vs baseline where available:

* D1 predicted vs actual PA (mean and P(PA ≥ 5)) by lineup slot × home/away; PA pmf.
* D2 home vs away starter PA (home-9th truncation, walk-offs).
* D3 substitution: V2's P(starter replaced) by slot and for catchers vs the actual rate (a non-starter batted in
  that slot).
* D4 walk rate: forecast BB per PA vs actual, per arm; BB rung reliability.
* D5 platoon: bias and CRPS for H, TB, BB, K, HR by same-hand / opposite-hand / switch matchup vs the probable
  starter.
* D6 bullpen: H/TB/R/RBI bias by tercile of the opposing bullpen's as-of OBP allowed.
* D7 baserunning: R, RBI and HRR bias by slot group.
* Coherence counts; subgroup tables by month.

## 10. Known limitations accepted in advance

* PIT covers only 2026-08-16..09-27 (no earlier pregame captures exist) and is the window the #285 audit
  diagnosed; OFF_H1 uses a post-game lineup proxy.
* Weather is not used (no as-of history). Runner speed and intentional-walk state dependence are not modelled.
* Postseason is not evaluated (not in the backfill).
* Forecasts are conditional on starting; late scratches after the capture are excluded for all arms alike.
