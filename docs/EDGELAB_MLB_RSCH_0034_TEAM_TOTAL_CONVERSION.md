# MLB-RSCH-0034 — Team-Total Probability Conversion

**Experiment:** `MLB-RSCH-0034` · **Evidence level:** `E1_RECONSTRUCTED_RETROSPECTIVE`
**Control:** `CTRL-1cce63c95bcfeb2f`
**Status:** RESEARCH ONLY. No production change. Nothing fitted.

> **This artifact has been corrected.** Two analytical statements in the first version did not survive review and are withdrawn below, with the corrections computed rather than asserted. The correction **changes the conclusion**: the pooled deficit against Kalshi turns out to be largely a threshold-mix artifact, and the branch is *not* demonstrably exhausted.

---

## What stands unchanged

- Production already contained the team-total **semantic correction** (`v1.2`).
- Archived rows reproduce under the **old v1.1** convention through **2026-08-20**.
- **v1.2** rows begin **2026-08-21** — a boundary *read off* the round-trip, not assumed.
- Prior research (MLB-RSCH-0031 / 0032) mixed the two versions and is superseded **for pricing only**; those merged artifacts are **not rewritten**.
- The semantic correction **materially helped** pre-fix rows.
- The **frozen NB dispersion materially improves** conversion versus Poisson.
- **No production change is justified.**

## Round-trip (unchanged)

| Bucket | n |
|---|---:|
| EXACT_MATCH (v1.2) | 110 |
| TOLERANCE_MATCH (v1.2) | 18 |
| MODEL_VERSION_MISMATCH (v1.1) | 300 |
| SEMANTIC_MISMATCH | 65 |
| MISSING_INPUTS | 0 |
| UNRESOLVED | 0 |

**86.8%** reproducible once model version is respected; no date reproduces under both conventions.

---

# CORRECTION 1 — the r² → AUC ceiling claim is WITHDRAWN

The first version stated that production's r² of 0.0377 **"caps attainable AUC near 0.55"** and used it as evidence that further distributional work is futile.

**That is not a valid inference.** r² of a continuous prediction against a noisy continuous outcome does **not** determine the AUC of that prediction for a *thresholded binary* event — the mapping depends on the generative distribution and on the threshold, neither of which r² encodes.

### The refutation is computed, not asserted

One simulated predictor, one fixed r² = **0.0741**, AUC measured for AT_LEAST_N at several N:

| N | base rate | AUC |
|---:|---:|---:|
| 3 | 0.812 | 0.6285 |
| 4 | 0.649 | 0.6210 |
| 5 | 0.465 | 0.6234 |
| 6 | 0.300 | 0.6320 |
| 7 | 0.176 | 0.6450 |

**A single r² corresponds to a range of AUCs (0.6210 – 0.6450), all far above the asserted cap.** The claim is withdrawn and is not used as evidence anywhere.

### What the question *should* have been — with assumptions stated

If production's archived `teamProj` were the true conditional mean, and outcomes came from the model's own distributional family, what AUC would ranking by `teamProj` achieve?

| Family | AT_LEAST_3 | AT_LEAST_4 | AT_LEAST_5 | AT_LEAST_6 |
|---|---:|---:|---:|---:|
| POISSON | 0.6361 | 0.6316 | 0.6338 | 0.6550 |
| FROZEN_NB | 0.5725 | 0.5914 | 0.5998 | 0.6152 |

**This is a reference value under stated assumptions, not a ceiling.** Both assumptions are load-bearing: `teamProj` is treated as the *true* mean (any error in it lowers achievable AUC), and outcomes are conditionally Poisson / NB given that mean.

Note that the **frozen-NB** rows reproduce production's *observed* r² (0.035–0.046 against a measured 0.0377) and imply achievable AUC ≈ **0.57–0.62** — materially above the withdrawn 0.55, and above the measured post-fix AUC of 0.4950 whose CI is [0.4163, 0.5821]. The sample cannot separate "production achieves what is achievable" from "it achieves nothing", which is itself the finding.

### What *does* still hold

Every candidate here is **monotone in `teamProj` at a fixed threshold**, so none can reorder teams *within* a threshold. That constrains what a **distribution** change can do. It says nothing about a ceiling on AUC.

---

# CORRECTION 2 — the pooled comparison was a threshold-mix artifact

The pooled corpus mixes `AT_LEAST_2 … AT_LEAST_8`, whose base rates differ materially. The first version noticed the resulting Simpson effect and then **dismissed** it — *"the pooled comparison is the one that counts."* That dismissal was wrong.

| Aggregation | C2 − Kalshi | 95% CI | |
|---|---:|---|---|
| **Raw pooled** | +0.0141 | [+0.0027, +0.0262] | **significant** |
| **Threshold-standardized** | -0.001730 | [-0.009052, 0.009409] | **not significant** |

**The pooled deficit against Kalshi does not survive standardization.** Weights are the threshold distribution of the full evaluation corpus, fixed a priori and recorded in the artifact; they were not chosen by outcome.

What *does* survive:

| Aggregation | C2 − production | 95% CI | |
|---|---:|---|---|
| **Threshold-standardized** | -0.026292 | [-0.037195, -0.016579] | **significant** |

The negative-binomial improvement over production's Poisson body is **robust to threshold mix**.

---

## Stratified results — ALL ERA

| Stratum | rows | games | dates | YES base | stratum const | production | C2 | Kalshi | C2−Kalshi (95% CI) | C2−const (95% CI) | AUC C2 | AUC Kalshi |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---:|---:|
| AT_LEAST_2 | 13 | — | — | — | *below 30-row floor* | | | | | | | |
| **AT_LEAST_3** | 41 | 38 | 18 | 0.610 | 0.2380 | 0.2681 | **0.2447** | 0.2473 | -0.0032 [-0.0351, +0.0292] ns | +0.0114 [-0.0126, +0.0413] ns | — | — |
| **AT_LEAST_4** | 234 | 174 | 21 | 0.444 | 0.2469 | 0.2799 | **0.2521** | 0.2533 | -0.0011 [-0.0104, +0.0085] ns | +0.0064 [-0.0024, +0.0168] ns | 0.4977 | 0.5136 |
| **AT_LEAST_5** | 157 | 136 | 20 | 0.446 | 0.2471 | 0.2741 | **0.2492** | 0.2516 | -0.0023 [-0.0180, +0.0137] ns | +0.0037 [-0.0080, +0.0180] ns | 0.5394 | 0.4696 |
| AT_LEAST_6 | 29 | — | — | — | *below 30-row floor* | | | | | | | |
| AT_LEAST_7 | 9 | — | — | — | *below 30-row floor* | | | | | | | |
| AT_LEAST_8 | 10 | — | — | — | *below 30-row floor* | | | | | | | |

## Stratified results — CURRENT ERA (v1.2 only)

| Stratum | rows | games | dates | YES base | stratum const | production | C2 | Kalshi | C2−Kalshi (95% CI) | C2−const (95% CI) | AUC C2 | AUC Kalshi |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---:|---:|
| AT_LEAST_2 | 11 | — | — | — | *below 30-row floor* | | | | | | | |
| AT_LEAST_3 | 27 | — | — | — | *below 30-row floor* | | | | | | | |
| **AT_LEAST_4** | 90 | 64 | 8 | 0.378 | 0.2351 | 0.2810 | **0.2502** | 0.2554 | -0.0050 [-0.0179, +0.0086] ns | +0.0186 [-0.0028, +0.0541] ns | 0.5037 | 0.4921 |
| **AT_LEAST_5** | 46 | 43 | 7 | 0.435 | 0.2457 | 0.2441 | **0.2350** | 0.2520 | -0.0167 [-0.0463, +0.0109] ns | -0.0055 [-0.0232, +0.0204] ns | — | — |
| AT_LEAST_6 | 11 | — | — | — | *below 30-row floor* | | | | | | | |
| AT_LEAST_7 | 2 | — | — | — | *below 30-row floor* | | | | | | | |
| AT_LEAST_8 | 6 | — | — | — | *below 30-row floor* | | | | | | | |

## Home / Away — all era

| Stratum | rows | games | dates | YES base | stratum const | production | C2 | Kalshi | C2−Kalshi (95% CI) | C2−const (95% CI) | AUC C2 | AUC Kalshi |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---:|---:|
| **SIDE_AWAY** | 216 | 214 | 21 | 0.454 | 0.2479 | 0.2839 | **0.2614** | 0.2464 | +0.0149 [-0.0038, +0.0356] ns | +0.0147 [-0.0032, +0.0338] ns | 0.5262 | 0.5371 |
| **SIDE_HOME** | 215 | 214 | 21 | 0.484 | 0.2497 | 0.2890 | **0.2581** | 0.2489 | +0.0090 [-0.0068, +0.0255] ns | +0.0093 [-0.0059, +0.0266] ns | 0.534 | 0.5256 |
| **SIDE_UNKNOWN** | 62 | 31 | 18 | 0.484 | 0.2497 | 0.3291 | **0.2789** | 0.2504 | +0.0290 [-0.0004, +0.0619] ns | +0.0347 [+0.0062, +0.0744] **sig** | 0.4443 | 0.5214 |

**No stratum shows a significant C2-vs-Kalshi effect in either direction.** The honest reading is neither "C2 beats the market" nor "C2 loses to it" — this corpus cannot tell them apart. The reversal was discovered *after* scoring and remains **EXPLORATORY**; it is audited here rather than promoted or dismissed.

---

## Sample: pre-fix vs current era

| Era | rows | independent games | independent dates |
|---|---:|---:|---:|
| v1.1 (pre-fix, obsolete) | 300 | 150 | 13 |
| **v1.2 (current production)** | **219** | **108** | **9** |

**The current era now clears the fixed 100-game floor**, so the verdict below rests on evidence rather than on absence of sample.

### Current-era comparisons

| Comparison | Brier delta | 95% CI | |
|---|---:|---|---|
| C2 − production (raw pooled) | -0.0244 | [-0.0354, -0.0134] | **significant** |
| C2 − Kalshi (raw pooled) | +0.0154 | [-0.0014, +0.0339] | not significant |
| C2 − pooled constant | +0.0138 | [-0.0045, +0.0333] | not significant |
| **C2 − Kalshi (threshold-standardized)** | **-0.009980** | [-0.021734, 0.002647] | **not significant** |

---

# DECISION: `CASE_3_SINGLE_THRESHOLD_ONLY`

2 of 2 scored strata favour C2 BY SIGN ONLY -- no stratum interval excludes zero and the threshold-standardized aggregate does not either, so nothing here is distinguishable from the market. Exploratory only; a favourable sign is not a result.

- Strata favouring C2 **by sign only**: AT_LEAST_4, AT_LEAST_5
- Strata reaching **significance**: **none**
- Threshold-standardized aggregate supports C2: **False**

### Why this is not CASE 2

An earlier version of the decision function returned `CASE_2_CONSISTENT_WITHIN_THRESHOLD_VALUE` as soon as every scored stratum had a *negative point estimate*, with no reference to whether any interval excluded zero. On this corpus that would have promoted two strata whose CIs both span zero, under a standardized aggregate that also spans zero — the exact reasoning Methodology V3 exists to refuse.

The function now requires statistical support for CASE 2. **A favourable sign is not a result.**

**No production candidate is created.** The frozen-NB result is held as an exploratory hypothesis; establishing it would require an independent prospective or holdout study, with multiplicity and sample standards unchanged.

---

## Methodology V3

All four labels pass — C2 **is** genuinely better than production, and that survives standardization (-0.025852, CI [-0.035723, -0.015632]). Promotion is blocked separately, and the blocker has been **rewritten**: it no longer rests on the withdrawn pooled claim.

Dispersion is RSCH-0010's frozen **0.281513**, imported, never estimated on the evaluation sample. No threshold-specific tuning, no new fitting, no new dispersion.

## Why the numbers moved between revisions

This artifact was regenerated after syncing onto post-#165 main, which brought newly settled rows into the archive. Eligible rows rose 493 → 519 and current-era independent games rose 95 → 108, crossing the 100-game floor. The verdict therefore moved from `CASE_4_INSUFFICIENT_CURRENT_ERA_SAMPLE` to a decision on evidence. Everything else — the v1.1/v1.2 boundary, the round-trip, the attribution — is unchanged in direction.

## Data-quality note

64 rows across 32 games could not be resolved to HOME or AWAY by the ticker parser and are reported as `SIDE_UNKNOWN` rather than silently dropped or assigned.
