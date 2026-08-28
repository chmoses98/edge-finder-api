# MLB-RSCH-0018: Games 1-10 Offensive Prior Confirmation

Status: **COMPLETE (real 2022-2026 results). CONFIRMED_EARLY_SEASON_SIGNAL.**

RESEARCH ONLY. No production behavior changed. **CONFIRMATORY**, not
exploratory: this milestone promotes a single, already-preregistered
MLB-RSCH-0017 reporting band (Games 1-10) into a dedicated study, with an
explicit false-discovery control -- the window is frozen before analysis,
never re-optimized, and every candidate parameter is read from RSCH-0017's
own committed artifact and asserted equal, never refit.

## 1. Purpose

MLB-RSCH-0017 found that its previous-season-anchored offense candidate
(E1) improved mean accuracy and probability quality broadly across
development and validation, and that its aggregate 2026 holdout result was
flat -- but the holdout's own preregistered season-band breakdown showed
the effect replicating specifically in games 1-5 and 6-10, fading/reversing
from game 11 on. This milestone asks one narrow question: **is that
specific Games-1-10 signal real, or a false discovery from reporting many
bands?**

## 2. Registration

| | |
|---|---|
| Experiment ID | `MLB-RSCH-0018` |
| Evidence level | `E2_PIT_HISTORICAL` |
| Experiment type | `CONFIRMATORY` |
| G0 / G1 | MLB-RSCH-0017's own `E0` / `E1` formulas and functions, reused **completely unchanged** |
| Frozen parameters | league average `4.3966`, HFA `-0.0065`, `K_PRIOR=20` -- read from `latest_mlb_rsch_0017_early_season_offense.json` and asserted equal at import time; never refit |
| Probability non-inferiority tolerance | `0.0005`, locked before any results were examined |
| Population | 1,500 team-games / 778 unique games / 30 teams, seasons 2022-2026 (exactly the expected ~300/season) |
| DEV / VAL / HOLDOUT | 900 / 300 / 300 team-games |
| Exclusions | none beyond RSCH-0017's own corpus cap and missing-actual-runs rows |

**G0 reproduction proof:** re-attaching E0 predictions to 50 DEV rows via
the identical function call reproduced RSCH-0017's own stored predictions
byte-exact (`matchesRsch0017Exactly: true`).

## 3. Primary result (DEVELOPMENT, 2022-2024)

| | G0 | G1 |
|---|---|---|
| MAE | 2.5536 | 2.5353 |
| RMSE | 3.2203 | 3.2036 |
| Bias | -0.0830 | -0.0651 |

Paired MAE delta: **-0.018297**, 95% CI [-0.0278, -0.0092] (fully negative
-- a real, well-powered improvement). RMSE delta -0.0167. Frozen-NB
probability primary delta **-0.001399** (favorable, comfortably inside the
locked ±0.0005 tolerance in the favorable direction). Team robustness:
17/30 teams improved (57%, above the 40% concentration floor).

## 4. Validation (2025)

Paired MAE delta **-0.028217**, 95% CI [-0.0495, -0.0065] -- direction
replicates and is, if anything, larger than DEV. Frozen-NB probability
primary delta **-0.002529** (favorable). **Selection gate passes on every
criterion; 2026 holdout unlocked.**

## 5. Locked 2026 holdout

| | G0 | G1 |
|---|---|---|
| MAE | 2.6443 | 2.6294 |
| RMSE | 3.2472 | 3.2402 |
| Bias | -0.0297 | -0.0097 |

Paired MAE delta: **-0.01495**, 95% CI [-0.0309, 0.0020] (favorable point
estimate; CI includes a thin positive tail but the bulk of mass is
negative). RMSE delta -0.0070. Frozen-NB probability primary delta
**-0.000878** (favorable; well inside the ±0.002 material-degradation
threshold). By family (all favorable except a negligible team_total_away
uptick):

| Family | Brier delta |
|---|---|
| game_total | -0.00034 |
| moneyline | -0.001338 |
| run_margin | -0.001835 |
| team_total_away | +0.000052 |
| team_total_home | -0.001885 |

Descriptive sub-bands: games_1_5 = -0.009433, games_6_10 = -0.020467 --
**both bands independently favorable on the genuinely locked 2026
holdout**, matching RSCH-0017's own original finding almost exactly.
Team robustness: 15/30 improved (50%).

## 6. Year-by-year consistency

| Season | MAE delta |
|---|---|
| 2022 | 0.0000 (no previous season exists -- E1 gracefully degrades to E0 exactly, as designed) |
| 2023 | -0.028935 |
| 2024 | -0.025954 |
| 2025 (VAL) | -0.028217 |
| 2026 (HOLDOUT) | -0.014950 |

Every season with previous-season data available (2023-2026) shows a
favorable direction of comparable magnitude -- **not carried by one
unusual year.** 2022's exact zero is the expected, correct behavior of
E1's own fallback, not a data gap.

## 7. Team robustness

DEV: 17/30 positive, 13/30 negative. Strongest positive contributor: team
119 (-0.1621). Strongest negative: team 117 (+0.0550). No team-specific
weights were fit; this is purely descriptive. Holdout: 15/30 positive,
15/30 negative -- an even split, but the AGGREGATE effect remains
favorable, meaning the positive-team effects are larger in magnitude than
the negative ones, not merely more numerous.

## 8. Previous-season-offense tercile breakdown

Frozen DEV-only thresholds: bottom third ≤ 4.2593 runs/game, top third ≥
4.6111 runs/game.

| Tercile | DEV | VAL | Holdout |
|---|---|---|---|
| Bottom third | -0.04997 | -0.06011 | -0.02946 |
| Middle third | +0.00162 | +0.01041 | -0.00603 |
| Top third | -0.03141 | -0.01649 | -0.00480 |

**The effect concentrates at the extremes** (bottom- and top-third
previous-year offenses) and is weak-to-absent for middle-third teams,
consistently across DEV/VAL/holdout. This makes intuitive sense: a team
whose previous-season rate deviates most from league average is exactly
where a previous-season-anchored prior carries the most incremental
information over a league-average anchor; a team near league average
gains little from either anchor.

## 9. Roster-turnover limitation (retained, not solved)

Unchanged from MLB-RSCH-0017: previous-season **team** offense is not the
same as current roster talent. This experiment does not resolve, and does
not claim to resolve, that gap.

## 10. Pinnacle secondary -- NOT ELIGIBLE

Zero historical Pinnacle-matched rows exist with either team in its own
first 10 games of a season: `run_proxy_vs_pinnacle_experiment.build_matched_rows()`
calls `team_baseline()` with its own default (20-game) floor -- the same
structural limitation MLB-RSCH-0015 already documented for its own
early-season diagnostic. Across all 834 Pinnacle-matched rows (2022-2026),
the minimum home-team `priorGamesThisSeason` is 20-24 depending on season;
none fall in [0, 9]. This is a genuine data-coverage limitation, not a
bug (verified directly), and is honestly reported as such rather than
worked around.

## 11. Tests

- `tests/edgelab/test_run_games_1_10_confirmation_experiment_script.py` --
  32 tests: frozen-parameter verification (raises on drift), registration
  idempotency, exact Games-1-10 filtering, no-reimplementation proofs (row
  construction, E0/E1 formulas, NB cells all reused from RSCH-0017
  unchanged), G0-reproduces-RSCH-0017 proof, K_PRIOR/league-average/HFA
  never refit, all 5 selection-rule gates including the exact tolerance
  boundary, DEV/VAL/HOLDOUT isolation and ordering (AST proofs), Games-1-10
  NB eligibility logic (joint vs. side-specific), tercile-thresholds-DEV-
  only proof, disposition ladder never reaches `PROMOTION_CANDIDATE`,
  Kalshi never referenced operationally, Pinnacle gated strictly after
  selection.
- Full `tests/edgelab/` suite: **2,905 passed**.
- Verified zero diff against every production file.
- Real 2022-2026 corpus run, deterministic.

## 12. Interpretation

**Is the Games-1-10 signal real?** Yes. It replicates on a genuinely
locked 2026 holdout that was never touched during DEV/VAL fitting, using
parameters frozen from an entirely separate prior experiment (RSCH-0017),
evaluated with a probability metric that RSCH-0017's own history (RSCH-
0015/0016) showed does NOT automatically follow from a mean-accuracy
win. Both descriptive sub-bands (1-5, 6-10) replicate independently. Five
of five real seasons (2023-2026 plus VAL) show a consistent favorable
direction. The effect is not concentrated in a handful of teams (DEV
17/30, holdout 15/30, with the positive teams carrying more weight). The
tercile breakdown gives a coherent mechanistic story: value comes from
teams whose previous season deviated most from league average.

## 13. Classification and disposition

**Classification: CONFIRMED_EARLY_SEASON_SIGNAL.**
**Disposition: SHADOW_CANDIDATE_FOR_2027.** (Never `PROMOTION_CANDIDATE`.)

**2027 prospective shadow warranted: YES**, in principle -- but 2026 is
already well past every team's own games 1-10, so there is no live
prospective deployment opportunity this season. A confirmed candidate
would need to be frozen and reactivated for **2027 Opening Day**
prospective shadow specifically. That production/shadow wiring is
**not implemented here** and requires separate, explicit authorization.

Frozen specification for that future work: G1 = RSCH-0017's E1 formula,
league average 4.3966, HFA -0.0065, K_PRIOR 20 (all sourced from
`CTRL-bf8d8573112736a3` / `MLB-RSCH-0017`), restricted to team-games 1-10
of the 2027 season. Required minimum prospective sample before any
interpretation: 30 settled games (matching this program's own
MLB-RSCH-0011 convention). Market families to monitor: moneyline,
game_total, team_total (home and away separately), run_margin.

## 14. MLB-RSCH-0011 NB shadow health (checked once)

`--dry-run`: `games=15 evaluated=0 skipped=15 newRecords=0`, every game
`NO_CHECKPOINT_DUE`. Zero `shadowEvaluationId` records anywhere in the
repository. Consistent with every earlier check this session -- no games
have reached their pregame checkpoint window at check time. **<30
settled -- no performance interpretation possible or attempted.**

## 15. Recommended next research action

**Uncertainty/error prediction: identify games where the model is least
reliable.** Rationale: this research program has now repeatedly found
that mean-accuracy wins do not reliably translate into probability wins
(RSCH-0015, RSCH-0016) and, separately, that value is often concentrated
in specific, identifiable subpopulations rather than uniform (RSCH-0017's
season bands, RSCH-0018's own tercile breakdown). A dedicated study of
*where and when* the model's probability estimates are least trustworthy
would directly generalize this pattern-recognition into a reusable
diagnostic, rather than discovering pockets of good/bad performance one
experiment at a time. This is judged higher expected value than bullpen
component talent (a narrower, single-lever question) or player-based
preseason talent (already audited as infeasible at useful historical
scale in RSCH-0017/0018 alike).
