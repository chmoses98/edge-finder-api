# Preregistration: leash-conditioned BF | outs variant of the workload challenger

**Frozen 2026-10-12, before the variant was scored on any 2025 or 2026 outcome.** Research only. There is no live,
staking, publication or authority change, and nothing is promoted under any outcome.

## Disclosure: this is a reused holdout

* The 2025 + 2026 regular seasons were already scored once for the frozen arms in PR #283. The long-leash BF
  weakness that motivates this work was found there. `docs/research/PITCHER_LONG_LEASH_DIAGNOSIS.md` quantifies
  it on the same rows for the frozen arms only.
* The variant's **form and all its parameters were chosen on 2022–2024 only**:
  * candidates were compared by fitting on 2022–23 and validating on 2024
    (`data/research/pitcher_long_leash/development_2024.json`);
  * the final fit used 2022–2024 (`variant_params.json`).
* No 2025 or 2026 outcome entered any fit or choice. The decision to work on long-leash BF did come from the
  holdout. So even a pass is weaker evidence than a fresh holdout, and it would need prospective confirmation.

## Arms

| arm | version | frozen artifact |
|---|---|---|
| variant | `mlb_pitcher_workload_joint_leash_v1` (`lib/research/pitcher_workload_joint_leash.py`, `nonout_mode="leash_gamma_by_outs"`, `kernel_mode="frozen"`) | `data/research/pitcher_long_leash/variant_params.json`, sha256 `0dcd2d3d856ea20f3bc5b6d514dce355a268be9c7767acdeb10faf14bde5a384`, fitted on 2022–2024 on top of the frozen v1 params (sha256 `b36ad628…035e70c`). The code is this commit's. |
| frozen challenger | `mlb_pitcher_workload_joint_v1` (#283), unchanged | `params.json` sha256 `b36ad628…035e70c` |
| champion | `lib/research/pitcher_prop_projection.py` + `data/edgelab/analytics/mlb_pitcher_prop_model_params.json`, unchanged | as #283 (`sha256:5741820d…b546d9`) |
| naive baseline | `naive_recent5_v1`, unchanged | as #283 |

**What the variant changes.** Only BF | outs. The variant computes
`E[BF − outs | outs = o, L] = c[o] · (r0 / league)^α · exp(γ(o) · L)`, where:

* L is the frozen design's own pitch-count leash term, `(shrunk recency-weighted prior pitches − league) / 10`,
  clamped to ±3;
* `γ(o) = 0.075 − 0.075 · (o − 18) / 6`, so 0.15 at 12 outs, 0.075 at 18 and 0 at 24;
* the curve c is re-estimated jointly with γ;
* the BF | outs block and the K | BF block were refitted with the frozen v1's own grids.

The fitted values are k_nonout_prior 500, α 0.5, var ratio 0.6, opp_alpha_o 0.5, k_bf_prior 100, opp_alpha_k
1.25, k_resid_slope −0.01 and φ_k 100.

The outs distribution, mean model, features and data are the frozen challenger's. Outs forecasts are therefore
identical to the frozen challenger's by construction, which a test asserts.

## Hypothesis

H1: conditioning non-outs on the pitch-count leash removes most of the leash-tier BF bias. It lowers long-leash BF
error without making any statistic or leash tier significantly worse.

## Development evidence (fit 2022–23, validate 2024 regular season, 4,564 starts; no 2025/2026 data)

| candidate | BF ΔMAE vs v1 refit [95% CI] | BF Δlog loss | K ΔMAE | outs ΔMAE | 2024 BF bias short / mid / long |
|---|---|---|---|---|---|
| v1 refit | — | — | — | — | +0.890 / −0.080 / −0.164 |
| B1: constant γ | −0.0298 [−0.0442, −0.0144] | −0.0122 [−0.0176, −0.0066] | −0.0026 [−0.0065, +0.0009] | 0 | −0.055 / −0.003 / +0.673 |
| **B2: γ by outs (chosen)** | **−0.0363 [−0.0500, −0.0219]** | **−0.0148 [−0.0196, −0.0100]** | −0.0026 [−0.0060, +0.0007] | 0 | +0.022 / +0.025 / +0.548 |
| A: bin-normalised kernel | −0.0106 [−0.0136, −0.0077] | −0.0021 | −0.0004 | −0.0034 [−0.0071, +0.0002]; Δlog loss +0.0013 [−0.0006, +0.0030] | +0.803 / −0.077 / −0.022 |
| A + B2 | −0.0380 [−0.0529, −0.0221] | −0.0145 | −0.0024 | as A | −0.069 / +0.000 / +0.674 |

**Choice.** B2 has the best BF log loss of the B candidates. A was not adopted because it did not improve 2024
outs log loss, and A + B2 is not better than B2 on BF log loss.

**Risk, recorded before scoring.** In 2024 the v1 refit's long-leash BF bias was small (−0.16), and B2
*over*-corrected it to +0.55. Long-leash BF MAE still improved, 2.507 → 2.482. Year-to-year variation in leash
behaviour can therefore flip the sign of the long-tier residual.

## Held-out data (evaluated once, after this commit is pushed)

The held-out data is #283's population: the 2025 and 2026 regular seasons, every starter-game with ≥ 3 prior starts
and observed BF, with the same 178 exclusions. That is 9,025 starts in 4,839 games.

* `as_of` = scheduled first pitch − 60 min. A source game counts only if it is observed (first pitch + 5 h) at or
  before `as_of`.
* Before scoring, the frozen challenger's means must reproduce the #283 sidecar on all 27,075 rows. Otherwise the
  run aborts.

**Command:** `python3 scripts/research/mlb_pitcher_long_leash.py evaluate`, which writes
`data/research/pitcher_long_leash/holdout_2025_2026.json`.

## Metrics

Metrics are reported per statistic (outs, BF, K) and are never pooled.

* MAE, RMSE, bias, p10–p90 coverage and log loss.
* Threshold Brier on #283's single outcome-blind rung: the ladder strike where the champion and frozen challenger
  mean probability is closest to 0.5. It is used identically for every arm.
* Paired game-clustered bootstrap (gamePk), 1,000 reps, seed 20261012.
* Comparisons: variant − frozen challenger, variant − champion and variant − baseline. Each is computed overall,
  by pitch-leash tier (`w_pitches` < 80 / 80–95 / ≥ 95) and by innings-depth tier (`w_outs` < 15 / 15–18 / ≥ 18).

## Primary objective

Long-leash (`w_pitches` ≥ 95) BF ΔMAE, variant − frozen challenger, with its 95% CI entirely below 0.

## Decision rule

**ACCEPTED_RESEARCH_VARIANT** requires every item below. Otherwise the verdict is **REJECTED**, and the frozen
challenger stays as it is.

1. The primary objective is met.
2. The long-leash BF |bias| point estimate is smaller for the variant than for the frozen challenger.
3. **No significant regression vs the frozen challenger, overall.** For each of outs, BF and K, none of ΔMAE,
   ΔBrier and Δlog loss has its CI entirely above 0.
4. **No significant regression vs the frozen challenger in any pitch-leash tier.** For each of short, mid and
   long, and each of outs, BF and K, the ΔMAE CI is not entirely above 0.
5. **#283's acceptance re-applied vs the champion:**
   * outs or K ΔMAE or ΔBrier has its CI entirely below 0;
   * no statistic has ΔMAE, ΔBrier or Δlog loss with its CI entirely above 0;
   * no pitch-leash tier with ≥ 100 starts has an outs or K ΔMAE CI lower bound above 0.
6. The variant's p10–p90 coverage for outs, BF and K is within [0.75, 0.92].
7. The variant's outs, BF and K MAE point estimates beat the naive baseline.

These are reported but are not gates:

* long-leash BF ΔMAE vs the champion (the original #283 weakness, +0.061 for the frozen challenger);
* the depth tiers;
* the per-tier bias tables.

**Status under any outcome:** research only. `mlb_pitcher_workload_joint_v1` stays the frozen challenger, and
the champion is unchanged. An accepted variant would still need prospective shadow collection, the
cause-3 (kernel) follow-up, and separate approval before any use.
