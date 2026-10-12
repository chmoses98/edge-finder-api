# Long-leash bias of the workload challenger `mlb_pitcher_workload_joint_v1`: quantification and diagnosis

Research only. The frozen challenger (PR #283) is not modified. Its code, `params.json` (sha256 `b36ad628…035e70c`)
and held-out sidecars are untouched. Nothing here is wired into an export, a gate, a stake or a published surface.

**Reproduce:** `python3 scripts/research/mlb_pitcher_long_leash.py diagnose`, which writes
`data/research/pitcher_long_leash/diagnosis.json`.

## Data and method

The rows are #283's own held-out population: the 2025 + 2026 regular seasons, with the same eligibility, the same
178 exclusions and the same arm construction, all imported from `scripts/research/mlb_pitcher_workload_joint.py`.
That gives **9,025 starter-games in 4,839 games**.

Before anything was scored, the frozen challenger's re-run means were checked against the committed #283 sidecar.
They matched on **27,075 / 27,075 rows** (0 mismatches).

* **Bias** = forecast mean − actual, so negative means under-projection.
* **Intervals** are 95% game-clustered bootstrap intervals (gamePk), 1,000 reps, seed 20261012.
* **Tiers** come only from as-of features (no outcome):
  * pitch leash: the recency-weighted prior pitches `w_pitches`, with the preregistered #283 cut-offs < 80 /
    80–95 / ≥ 95 plus a finer split;
  * innings depth: the recency-weighted prior outs `w_outs`, < 15 / 15–18 / ≥ 18 (under 5 IP, 5–6 IP, 6+ IP).

These held-out rows were already scored once in #283; that is where the long-leash BF weakness was reported. The
tables below describe the frozen arms on that same data. The mechanism checks are repeated on the development
seasons (2022–2024, 12,958 starts). The frozen params were fitted on those seasons, so this check is in-sample,
but it uses no held-out outcome.

## 1. Bias by pitch-leash tier (held-out 2025 + 2026)

| tier | starts / games | stat | challenger | champion | naive baseline | challenger − champion |
|---|---|---|---|---|---|---|
| short (< 80) | 1,521 / 1,393 | outs | +0.113 [−0.077, +0.302] | +0.359 [+0.158, +0.572] | −0.694 [−0.888, −0.484] | −0.247 [−0.316, −0.190] |
| | | BF | **+1.058 [+0.841, +1.285]** | +0.447 [+0.207, +0.687] | −0.877 [−1.107, −0.631] | +0.611 [+0.533, +0.672] |
| | | K | +0.197 [+0.091, +0.309] | +0.077 [−0.030, +0.188] | −0.339 [−0.450, −0.227] | +0.119 [+0.101, +0.133] |
| mid (80–95) | 6,592 / 4,312 | outs | +0.115 [+0.030, +0.212] | +0.123 [+0.038, +0.222] | +0.207 [+0.114, +0.306] | −0.007 [−0.024, +0.009] |
| | | BF | +0.080 [−0.008, +0.168] | +0.091 [+0.002, +0.179] | +0.224 [+0.126, +0.319] | −0.011 [−0.028, +0.006] |
| | | K | +0.099 [+0.045, +0.156] | +0.121 [+0.067, +0.177] | +0.086 [+0.029, +0.144] | −0.022 [−0.026, −0.017] |
| **long (≥ 95)** | **912 / 862** | outs | **−0.258 [−0.474, −0.035]** | −0.205 [−0.424, +0.017] | +0.676 [+0.451, +0.891] | −0.053 [−0.096, −0.008] |
| | | BF | **−0.651 [−0.827, −0.467]** | −0.216 [−0.390, −0.024] | +0.722 [+0.539, +0.912] | −0.435 [−0.483, −0.390] |
| | | K | **−0.201 [−0.357, −0.038]** | −0.038 [−0.198, +0.124] | +0.383 [+0.221, +0.563] | −0.163 [−0.176, −0.150] |
| all | 9,025 / 4,839 | outs | +0.077 [+0.003, +0.155] | +0.129 [+0.054, +0.209] | +0.102 [+0.023, +0.184] | −0.052 [−0.071, −0.035] |
| | | BF | +0.171 [+0.096, +0.247] | +0.120 [+0.043, +0.196] | +0.089 [+0.010, +0.169] | +0.051 [+0.032, +0.070] |
| | | K | +0.085 [+0.038, +0.132] | +0.097 [+0.050, +0.143] | +0.045 [−0.004, +0.094] | −0.012 [−0.017, −0.008] |

The bias is monotone in leash, as the finer split shows for the challenger's BF:

| tier | < 75 | 75–85 | 85–90 | 90–95 | 95–100 | ≥ 100 |
|---|---|---|---|---|---|---|
| starts | 647 | 2,627 | 2,713 | 2,126 | 838 | 74 |
| BF bias | +1.663 [+1.320, +2.037] | +0.499 [+0.364, +0.645] | −0.011 [−0.147, +0.124] | −0.104 [−0.249, +0.049] | −0.604 [−0.812, −0.402] | −1.186 [−1.809, −0.516] |

The ≥ 100 tier has only 74 starts.

By innings depth the pattern is the same. Starters averaging 6+ IP (1,049 starts / 991 games) have challenger
bias of outs −0.286 [−0.515, −0.060], BF −0.695 [−0.900, −0.483] and K −0.180 [−0.324, −0.039]. The champion's
corresponding biases are +0.145, −0.051 and +0.019, all with intervals spanning 0. Starters under 5 IP
(2,844 / 2,408) have challenger BF bias +0.894 [+0.743, +1.048].

The naive baseline has the opposite sign in every tier, because it regresses nothing: its means are recent
averages. Both models shrink toward the league. The challenger shrinks BF further than the champion at both ends.

## 2. Diagnosis: where the bias comes from (frozen challenger)

The challenger's chain is outs → BF | outs → K | BF, so each stage can be isolated against the actual value of
the stage before it:

| tier (held-out) | outs bias | latent E[outs] (μ) − actual | mixture mean − μ | non-outs given the actual outs | BF bias | K given the actual BF | K bias |
|---|---|---|---|---|---|---|---|
| short | +0.113 | −0.115 [−0.303, +0.080] | **+0.228 [+0.192, +0.262]** | **+0.950 [+0.830, +1.074]** | +1.058 | +0.008 [−0.093, +0.105] | +0.197 |
| mid | +0.115 | +0.091 [+0.006, +0.187] | +0.024 [+0.018, +0.030] | −0.001 [−0.052, +0.053] | +0.080 | +0.113 [+0.062, +0.169] | +0.099 |
| long | −0.258 | −0.011 [−0.225, +0.213] | **−0.247 [−0.258, −0.236]** | **−0.402 [−0.555, −0.272]** | −0.651 | −0.012 [−0.171, +0.138] | −0.201 |

The same check on development 2022–2024 (in-sample, 12,958 starts) gives:

| tier | mixture mean − μ | non-outs given the actual outs | BF bias | K given the actual BF |
|---|---|---|---|---|
| short (2,132) | +0.266 | +0.733 [+0.627, +0.839] | +0.650 | +0.168 |
| long (2,167) | −0.290 | −0.582 [−0.683, −0.493] | −0.778 | +0.023 |

So the mechanism replicates on data the held-out evaluation never touched. It has three parts.

1. **The duration mean is not the problem.** The latent μ is unbiased for long-leash starters: −0.011 held-out,
   +0.049 in development.

2. **BF | outs ignores the leash.** This is the largest share: −0.40 of the −0.65 held-out long-leash BF bias and
   +0.95 of the short-leash +1.06. `E[BF − outs | outs]` is one league curve. The hook is pitch-count driven, and
   non-outs cost pitches, so at the same outs count:
   * a long-leash starter has been allowed to carry more baserunners;
   * a short-leash starter who got that deep must have been efficient.

   Actual − curve non-outs by actual-outs band confirm it:

   | actual outs | short, held-out | mid, held-out | long, held-out | short, dev | mid, dev | long, dev |
   |---|---|---|---|---|---|---|
   | 9–11 | −1.35 | +0.33 | +0.74 | −0.56 | +0.31 | +1.14 |
   | 12–14 | −0.61 | +0.20 | +1.13 | −0.68 | +0.44 | +1.26 |
   | 15–17 | −0.76 | +0.01 | +0.64 | −0.85 | +0.15 | +1.04 |
   | 18–20 | −1.05 | −0.21 | +0.29 | −0.52 | −0.08 | +0.40 |
   | 21–23 | −0.82 | −0.37 | −0.21 | −0.68 | −0.25 | +0.13 |

   The gap between leash tiers narrows as outs rise, so a constant leash multiplier would overstate the effect on
   deep outings.

3. **The kernel outs mixture pulls the tails toward the centre.** This is the whole outs bias: −0.247 of the
   −0.258 long-leash outs bias, and +0.228 for short leashes. Each development start gets equal weight, so near
   the tails of the fitted-μ density the neighbours on the dense side dominate. That outs bias then carries into
   BF, at roughly 1.4 BF per out.

4. **K | BF is calibrated by tier.** K given the actual BF has a long-leash bias of −0.012 [−0.171, +0.138] and a
   short-leash bias of +0.008. The K bias is inherited from BF.

**Why the champion shows less of it.** The champion's BF uses a pitcher non-out rate × outs with a Poisson. It
has neither the league curve's selection effect nor the kernel mixture. It does have its own over-dispersed BF
(p10–p90 coverage 0.959 in #283).

## 3. What follows

One correction was preregistered: `docs/research/PITCHER_LONG_LEASH_PREREG.md`. It targets cause 2, BF | outs
conditioned on the leash, with an effect that varies with outs. The form and every parameter were chosen on
2022–2024 only. The kernel correction (cause 3) was tried on development and **not adopted**, because it did not
improve 2024 outs log loss (`development_2024.json`). Cause 3 therefore remains and is recorded here as an open
item.
