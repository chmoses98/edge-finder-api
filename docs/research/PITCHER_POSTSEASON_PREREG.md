# Preregistration: sports-only 2026 postseason evaluation of the pitcher workload challenger

**Frozen 2026-10-11, before any 2026 postseason outcome is scored for this evaluation.** This is research only. Nothing
changes in live flows, staking, the app export or publication. Postseason projections stay **BLOCKED** unless
the strict rule in section 5 passes. Even a pass is research only and is not a promotion.

## 0. Precondition: complete coverage

The evaluation runs only if `docs/research/pitcher_postseason_coverage.json` reports `complete: true` for
every season from 2022 to 2026, 2026 counting through the latest completed game. Complete means two things:

- every final game in the official StatsAPI postseason schedule is cached;
- every cached side has a starter line with outs, BF, K and pitches.

If any season is incomplete, the verdict is **BLOCKED_DATA**, and nothing is scored.

**Status at this commit:** complete (`PITCHER_POSTSEASON_COVERAGE.md`, commit `2b1d3c5`). 2026 runs through ALDS
Game 5 (2026-10-10): 25 final games, 50 starts. No 2026 postseason projection has been scored against these
labels.

## 1. Data

**Source.** `data/research_cache/starter_workload/<season>/postseason_boxscores.jsonl.gz`, written by
`scripts/research/mlb_postseason_boxscore_backfill.py` on GitHub Actions.

- The data is official MLB StatsAPI box-score lines, cross-checked against the `/boxscore` endpoint.
- Each game records its `fetched_at` time and source URL.
- There are no market, odds or settlement inputs.

**Fit set** (for the optional regime term only): every starter in every final postseason game, 2022–2025.

**Evaluation set (untouched):**

- Population: every final 2026 postseason game in the cache, through the latest completed game, and both
  actual starters of each game (`orderIndex` 0).
- The population is full, not market-selected. It is the same "actual starter" convention as the
  regular-season holdout in `PITCHER_WORKLOAD_PREREG.md`.
- Labels: the sports-source outs, BF and K from the same cache.

**Cutoff.** `as_of` = scheduled first pitch − 60 min.

- Regular-season history follows the frozen rule in #283.
- A cached postseason game may enter a projection only if its `gameEnd` < `as_of`. The cache's `fetched_at` is
  an archive time, not an availability time.

**Exclusions** are counted and reported:

- a starter line missing outs, BF or K;
- an unknown team id;
- fewer than 3 prior starts for the challenger or the champion;
- the champion's history containing a suspended game completed after `as_of`;
- missing baseline inputs;
- any source observed after `as_of`.

## 2. Arms

| Arm | Definition | Role |
|---|---|---|
| `challenger` | `mlb_pitcher_workload_joint_v1`, exactly as frozen in #283 (`data/research/pitcher_workload_joint/params.json`, sha256 `b36ad628…035e70c`). Its loader reads regular-season sports history only. | primary challenger |
| `challenger_regime` | Same model plus **one** sports-only postseason term: an additive shift δ on the latent expected-outs location, clipped to [1, 26] like the frozen model. BF given outs and K given BF follow through the unchanged joint chain. δ is fitted on the 2022–2025 postseason starts only, by grid search over [−6.0, +2.0] in steps of 0.1, minimising mean outs log loss. Ties go to the smaller \|δ\|. Written to `data/research/pitcher_postseason/regime_params.json`. | secondary challenger (optional term) |
| `champion` | `lib/research/pitcher_prop_projection.py` + `data/edgelab/analytics/mlb_pitcher_prop_model_params.json`, unchanged, with `postseason=True`. Its history is regular-season box score / Statcast lines **plus sports-source postseason starts with `gameEnd` < `as_of`**, which replace its settlement-evidence history. | incumbent comparator |
| `baseline` | `naive_recent5_v1`, frozen. | simple baseline |
| `champion_deployed_path` | The production postseason path, which reads settlement evidence as history. | **Reference only.** It is not PURE and never a gate comparator. |

The champion's frozen `postseason_shift_outs` (−1.209) was fitted on settlement-evidence observations. It is
kept because it is part of the incumbent as frozen. This is recorded as an indirect market dependency of the
champion, not of the challenger.

## 3. Metrics

These are computed per statistic (outs, BF, K), with every arm scored on identical rows:

- MAE, median absolute error, RMSE, bias, p10–p90 coverage and log loss.
- Threshold Brier on **one** rung per starter-game-stat. The rung is the ladder strike where the
  champion/challenger mean probability is closest to 0.5. It is chosen without looking at outcomes and is
  the same for every arm.
- CRPS from `tools/pure_gate/pure_gate.py compare`, the vendored copy of sift-sports-intelligence 558c592.
- Paired bootstrap over whole games (gamePk), 1,000 reps, seed 20261010, for these differences:
  - challenger − champion;
  - challenger − baseline;
  - champion − baseline;
  - challenger_regime − champion;
  - challenger_regime − baseline;
  - challenger_regime − challenger.
- Preregistered subgroups: game type (F, D, L, W) only.
- `pure_gate validate --require-v1` must pass on every arm's sidecar.

## 4. Hypotheses

- **H1.** The frozen challenger is no worse than the champion on postseason outs and K. Background: in the
  market-selected diagnostic of #283 it was worse, with outs ΔMAE +0.198 [+0.001, +0.422].
- **H2.** One sports-only regime term fitted on earlier postseasons reduces the challenger's postseason outs
  over-projection without hurting BF or K.

## 5. Decision rule (strict; applied separately to `challenger` and `challenger_regime`)

| # | Requirement |
|---|---|
| 1 | For outs or K, ΔMAE or ΔBrier vs `champion` has its 95% CI entirely below 0. |
| 2 | **No preregistered metric worsens significantly.** For each of outs, BF and K, none of ΔMAE, ΔBrier and Δlog loss vs `champion` **or** vs `baseline` may have its CI entirely above 0. |
| 3 | p10–p90 coverage for outs and K is within [0.75, 0.92]. |
| 4 | Outs and K MAE point estimates beat `baseline`. |
| 5 | At least 40 scored starts. |

**Verdicts:**

- If 2, 3 or 4 fails, the verdict is **REJECTED**.
- Otherwise, if 1 or 5 fails, the verdict is **INCONCLUSIVE**.
- Otherwise, the verdict is **POSTSEASON_RESEARCH_PASS**.

Postseason status is **BLOCKED** unless the verdict is POSTSEASON_RESEARCH_PASS. A pass means research only,
with no promotion. Promotion would also need prospective postseason shadow evidence and separate approval.

The regular-season verdict of #283 is unaffected either way.

## 6. Development disclosure

Before this preregistration was committed, the evaluation code path was smoke-tested once on the **2025**
postseason, which is fit data and not the holdout. The test used a dummy δ = −1.0 and 100 bootstrap reps.

What that showed on 2025 (91 starts):

- The frozen challenger's outs p10–p90 coverage was 0.67.
- Its BF metrics were significantly worse than the sports-history champion.

No 2026 postseason outcome was scored before this commit, and the rule in section 5 was written before that
smoke test.

## 7. Commands

```
python3 scripts/research/mlb_postseason_coverage_report.py
python3 scripts/research/mlb_pitcher_postseason_eval.py fit-regime
python3 scripts/research/mlb_pitcher_postseason_eval.py evaluate --outdir data/research/pitcher_postseason/eval_2026 --reference-arm --prereg <this commit>
```
