# Preregistration: W1-D sports-only joint pitcher workload challenger

**Frozen 2026-10-11, before any 2025 or 2026 outcome was scored for this model.** Research only. There is no
live, staking or publication change.

## Arms

| Arm | Version | Frozen artifact |
|---|---|---|
| Champion | `lib/research/pitcher_prop_projection.py` + `data/edgelab/analytics/mlb_pitcher_prop_model_params.json` | Unchanged from `origin/main` 29abfc8 |
| Challenger | `mlb_pitcher_workload_joint_v1` (`lib/research/pitcher_workload_joint.py`, `lib/research/pitcher_workload_sources.py`) | Code commit `74de33e`; `data/research/pitcher_workload_joint/params.json` sha256 `b36ad6280d69edb593878fdfe371905014eef9fa33cc49398aebe398b035e70c` |
| Baseline | `naive_recent5_v1` | Mean of the last 5 starts. Outs and BF use a discretised normal with development sd (4.0107 and 3.956). K uses a Poisson. Same params file. |

For regular-season games the champion runs on its own sports-only history: box scores plus Statcast lines from
`scripts/research/mlb_pitcher_prop_calibration.py`'s loaders. Its postseason shift is never applied on regular-season
rows.

## Hypothesis

As-of workload context improves held-out starter forecasts over the frozen champion. The context is pitch-count
leash, rest and return-from-layoff, season debut, the last three days of bullpen load, and opponent on-base. The
model is joint: outs (a kernel empirical distribution that keeps inning-boundary hooks) → BF | outs (the league
curve of non-outs by outs) → K | BF.

## Development evidence (no 2025 or 2026 data used)

The model was fitted on 2022–23 and validated on 2024 regular season (4,564 starts, n_hist ≥ 3). Challenger vs
refitted champion:

| Statistic | Metric | Challenger | Champion |
|---|---|---:|---:|
| Outs | MAE | 2.758 | 2.831 |
| Outs | Log loss | 2.426 | 2.545 |
| K | MAE | 1.818 | 1.824 |
| K | Log loss | 2.211 | 2.225 |
| BF | MAE | 2.606 | 2.669 |
| BF | Log loss | 2.614 | 2.834 |

The dev exploration ran before the observed_at ≤ as_of suspended-game filter was added. That filter affects about 1% of rows. The direction replicated when fitting on 2022 and validating on 2023. Outs MAE was 2.865 vs 2.912 and K MAE was
1.832 vs 1.840.

Feature-group ablations on 2024 kept these groups: pitch-count (pitch_shr, last_pitches, max3, pitches/BF),
rest/return/debut, bullpen_3d and opponent non-out. Three were dropped: team hook (organisation leash),
early-season and few-history. Removing each of those changed 2024 outs log loss by less than 0.001.

The kernel bandwidth h = 0.5 was chosen on 2024 from {0.35, 0.5, 0.75, 1.0}. Earlier dev variants were tried and
rejected on 2024: a BF-first chain with outs | BF beta-binomial (worse outs log loss, 2.677), and a Poisson
BF | outs (over-dispersed, BF bias −0.18). The final parameters were refitted on 2022–2024 (13,950 starts).

## Held-out data (evaluated once)

- The 2025 and 2026 regular seasons, every starter-game with ≥ 3 prior starts and observed BF. Outcomes come from
  box scores, and from Statcast lines for 2026-08-27 to 09-27.
- Forecast cutoff `as_of` = scheduled first pitch − 60 min. A source game is usable only if it is dated before the
  projected game and its observed proxy (first pitch + 5 h) is ≤ `as_of`.
- Rows are excluded, and counted, when the champion's date-ordered history contains a suspended game completed
  after `as_of`, or when a baseline input is missing.
- One row per starter-game-stat. Stats are outs, BF and K. Ladder rungs are never pooled.

## Metrics (per statistic, arms on identical rows)

- MAE, median absolute error, median signed error, RMSE, bias, p10–p90 coverage and log loss.
- Threshold Brier on **one** rung per starter-game-stat: the ladder strike where the champion/challenger mean
  probability is closest to 0.5. The choice is outcome-blind and the same rung is used for all arms. Ladders are
  outs 9–21, BF 15–30, K 2–10.
- Paired bootstrap resampling whole games (gamePk), 1,000 reps, seed 20261010. Reported for challenger − champion,
  challenger − baseline and champion − baseline.
- Subgroups:
  - Season.
  - Outcome source.
  - Short leash: recency-weighted prior pitches < 80. Long leash: ≥ 95.
  - Low K: shrunk prior K/BF < 0.20. High K: > 0.26.
  - First start after return: ≥ 15 days within the season.
  - Season debut.
- Lineup confirmed / unconfirmed is **BLOCKED_DATA** (no as-of lineup status before 2026-07-30).
- The kit gate (`prop_projection_gate.py validate|compare`) and `pure_gate.py validate|compare` are run on the
  sidecars.

## Postseason

The 2026 postseason is **diagnostic only and not part of the verdict**. Sports-source postseason box scores are
not committed (see `PITCHER_WORKLOAD_SOURCE_AUDIT.md`). The only outcome labels are settlement-evidence values
from a market-selected population.

## Decision rule

**ACCEPTED_CHALLENGER** requires all of the following on the held-out regular season:

1. For outs or K, ΔMAE or ΔBrier (challenger − champion) has its 95% CI entirely below 0.
2. No significant regression. For each of outs, BF and K, none of ΔMAE, ΔBrier and Δlog loss has its CI
   entirely above 0.
3. Challenger p10–p90 coverage for outs and K is within [0.75, 0.92]. The intervals are discrete and inclusive.
4. No preregistered subgroup with ≥ 100 starts shows a significant outs or K ΔMAE regression (CI lower bound > 0).
5. The challenger's outs and K MAE point estimates beat the naive baseline.

If 1 fails while 2–5 hold, the verdict is **REJECTED (inconclusive, champion kept)**. If 2, 3, 4 or 5 fails, the
verdict is **REJECTED**.

Independently of the verdict, `scripts/research/mlb_pitcher_workload_mutation.py` must return
`PASS_RUNTIME_NONLEAKAGE`. Otherwise the challenger cannot be labelled PURE and the verdict is REJECTED.

Accepted still means research only. Promotion needs prospective validation and a separate approval.
