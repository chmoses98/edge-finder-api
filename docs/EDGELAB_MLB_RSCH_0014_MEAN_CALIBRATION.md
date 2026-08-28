# MLB-RSCH-0014: Expected-Run Mean Calibration

Status: **COMPLETE (real 2022-2026 results).**

RESEARCH ONLY. No production behavior changed.

## 1. Purpose

MLB-RSCH-0009 found a better offense proxy and bullpen quality both improved
the expected-run mean model (frozen: offense/bullpen shrinkage k=30 each,
home-field adjustment DEV-fit). MLB-RSCH-0010 then materially improved the
*scoring distribution* on top of that mean (frozen negative-binomial,
dispersion never refit). MLB-RSCH-0012/0013 then tested and **rejected** two
further ways to change the mean's own *inputs* (a refit shrinkage constant on
each side, and a component-batting regression). This milestone asks a
structurally different question: holding MLB-RSCH-0009's frozen mean
construction completely fixed, **is the mean itself systematically
miscalibrated**, and does a simple post-hoc transform of the
already-computed prediction help?

## 2. Registration

| | |
|---|---|
| Experiment ID | `MLB-RSCH-0014` |
| Control | `mlb_rsch_0014_mean_calibration_control_v1` |
| Evidence level | `E2_PIT_HISTORICAL` |
| Frozen NB dispersion | `0.281513` — verified byte-exact at import time; never refit here |
| Frozen mean composition (C0) | MLB-RSCH-0009's own `{"offense","bullpen"}` composition, unchanged — HFA re-fit on this experiment's own DEV corpus reproduces `0.0114`, byte-identical to RSCH-0009's own canonical artifact |
| Chronological split | DEV 2022-2024, VAL 2025, HOLDOUT 2026 (locked) |
| Corpus scale | **10,204 games → 20,408 team-observations** (dev 6,378 / val 2,127 / holdout 1,699 games) — same scale as MLB-RSCH-0012/0013 |

## 3. Critical isolation

This experiment changes **only** the calibration layer applied *after*
MLB-RSCH-0009's frozen mean is computed. It never touches: the offense
component, the bullpen component, the home-field adjustment fit method, the
NB dispersion, or any production code. Pinnacle is used only as a secondary
check, strictly after freezing.

## 4. C0 calibration diagnostics (before any candidate was examined)

By fixed predicted-team-run band, DEVELOPMENT only, overall:

| Band | n | independentGames | meanPredicted | meanActual | bias | MAE | RMSE |
|---|---|---|---|---|---|---|---|
| <3.0 | 0 | — | — | — | — | — | — |
| 3.0–3.75 | 358 | 356 | 3.642 | 3.665 | -0.023 | 2.128 | 2.762 |
| 3.75–4.5 | 8,482 | 5,860 | 4.197 | 4.212 | -0.014 | 2.380 | 3.038 |
| 4.5–5.25 | 3,788 | 3,454 | 4.710 | 4.966 | -0.256 | 2.547 | 3.295 |
| 5.25–6.0 | 126 | 126 | 5.428 | 5.818 | -0.389 | 2.561 | 3.184 |
| 6.0+ | 2 | 2 | 6.034 | 4.500 | +1.534 | 1.534 | 1.613 |

Home and away bands are nearly identical in shape and magnitude (e.g.
4.5–5.25 band: bias -0.183 home vs -0.329 away; 3.75–4.5 band: -0.048 home vs
+0.019 away) — **no material home/away asymmetry**.

**The key diagnostic finding**: per-band *bias* is small everywhere it can be
estimated with meaningful sample (well under half a run in every band with
n > 100), while per-band *MAE/RMSE* (~2.1–2.6 / ~2.8–3.3 runs) is an order of
magnitude larger — individual-game run totals are simply very noisy (team-game
variance ≈ 9.7 runs², matching MLB-RSCH-0012's own empirical-Bayes
diagnostics). A single global linear regression of actual-on-predicted
(`_simple_ols`) reports a slope of **1.294** and intercept **-1.187** — a
number that LOOKS like large compression bias, but is substantially an
artifact of fitting a line through a narrow x-range (predicted values cluster
tightly between ~3.75–5.25) against very noisy y-values, not a robust,
generalizable structural bias. This is exactly the pattern the mean-band
table above is designed to reveal honestly rather than over-interpreting a
single global slope estimate.

## 5. Candidates C1/C2/C3 — real DEV/VAL results

| Candidate | Fitted params | DEV MAE delta (95% CI) | VAL MAE delta (95% CI) | DEV NB primary delta | VAL NB primary delta | Passes selection? |
|---|---|---|---|---|---|---|
| C1 (global affine) | a=-1.187, b=1.294 | **+0.00808** [0.0062, 0.0101] | +0.00902 [0.0057, 0.0126] | -0.0005 | -0.00032 | **NO** |
| C2 (home/away affine) | a_h=-0.421, b_h=1.117, a_a=-1.934, b_a=1.466 | **+0.00753** [0.0055, 0.0097] | +0.00765 | -0.00057 | -0.00051 | **NO** |
| C3 (quadratic) | a=-1.321, b=1.355, c=-0.0069 | **+0.00809** [0.0062, 0.0100] | +0.00902 | -0.0005 | -0.00032 | **NO** |

All three candidates **degrade DEV mean accuracy**, with the 95% CI entirely
positive (worse) — a small but statistically significant, real effect, not
noise. Interestingly, the frozen-NB *probability* metrics move slightly in
the *improving* direction for all three on both DEV and VAL — but the
preregistered selection rule's first gate (DEV MAE must improve) is a hard
requirement precisely to prevent a marginal, possibly-coincidental
probability-metric wobble from justifying a point-accuracy regression. All
three additionally fail the "not confined to one band" criterion: the ONLY
band where any candidate improves is 3.0–3.75 (n≈357, a small band), while
every other band (including the two largest, 3.75–4.5 and 4.5–5.25) gets
worse.

C2's home/away split params (b_h=1.117 vs b_a=1.466) are noticeably
different in isolation, but given the home/away diagnostic symmetry in
section 4, this reflects overfitting to DEV-only home/away sampling noise
rather than a real asymmetry — consistent with C2 failing identically to C1.

## 6. Selection (preregistered, DEV+VAL only)

**No candidate passes.** Frozen winner: **C0 (no calibration)** — the
control's own frozen mean is retained unchanged. Since the frozen winner is
C0 itself, no separate 2026 holdout evaluation was performed (per the
preregistered design: holdout is unlocked *only* for a winning calibration
candidate; C0-vs-C0 is trivially zero).

## 7. Early-season (games 1-20) feasibility diagnostic

The inherited `MIN_PRIOR_GAMES_FOR_BASELINE=20` eligibility floor (**not
changed here**, per instruction) structurally excludes every team-game with
fewer than 20 prior games this season — confirmed: `minPriorGamesObservedInCorpus=20`,
i.e. **zero observations exist in this corpus for games 1-20**. No
calibration bias estimate for that range is possible from this experiment.
A genuine early-season calibration/prior study is feasible in principle (the
underlying caches already cover every game) but requires a **separately
preregistered milestone** that deliberately lowers the floor and defines a
PIT-safe early-season prior before examining results — not attempted here.

## 8. Pinnacle secondary check (existing cache, no new spend)

Since C0 is the frozen winner (no calibration adopted), "control" and
"winner" are identical by construction — the Pinnacle gap is unchanged:

| Market | Gap (proxy Brier minus Pinnacle Brier) |
|---|---|
| Moneyline | 0.008149 (95% CI [0.0003, 0.0156]) |
| Game total (Pinnacle's own quoted line) | 0.006019 (95% CI [-0.0009, 0.0130]) |

No calibration candidate was available to test against Pinnacle since none
survived DEV/VAL selection — there is nothing to "close the gap" with.

## 9. Kalshi-relevant family impact

Since no calibration candidate was adopted, there is **no probability change
to map to any market family** (moneyline, game totals, team totals, alternate
totals, margins/run lines) — production's existing probabilities are
unaffected by this experiment in every respect.

## 10. Production mapping (read-only; no production code changed)

1. **Explicit run-mean calibration in production?** NO — `scripts/enrich_data.py::compute_offense_baseline`
   and its callers apply recency blending, shrinkage, and opponent/lineup
   adjustments, but never a post-hoc affine/quadratic correction fit against
   actual outcomes.
2. **Home/away bias adjusted?** Production applies a single home-field
   adjustment (a baseball effect, additive to the home side) — not a
   calibration correction for home/away *prediction* bias, the distinct
   question this experiment tested (and found no material asymmetry to
   correct for).
3. **Compression/expansion explicitly calibrated?** NO — production's
   shrinkage weight (15-vs-20, per MLB-RSCH-0012's own mapping) is a fixed,
   un-validated-against-actual-outcomes constant, not a fit calibration
   slope.
4. **Markets depending on these means:** every market probability derived
   from expected-run means — moneyline, game total (all lines), team total
   (all lines), and any run-margin-derived market.

## 11. Tests

- `tests/edgelab/test_run_mean_calibration_experiment_script.py` — 50 tests:
  preregistration ordering (idempotent re-registration proof), C0 exact
  reproduction (byte-identical to a direct `rsch0009` call), closed-form
  simple-OLS and quadratic-OLS correctness, DEV-only fitting proofs (never
  examines VAL/HOLDOUT), calibration-floor non-negativity, determinism,
  selection-rule correctness (all four gates independently tested),
  holdout-inaccessible-during-selection proofs (only the frozen winner's own
  key is ever passed to the holdout-evaluation function; AST-verified
  ordering: registration → corpus → selection → holdout unlock → Pinnacle),
  NB-cell determinism/valid-probability-range/run-margin-family checks,
  early-season diagnostic never builds a candidate, production-mapping
  function never writes.
- Full `tests/edgelab/` suite: 2,692 passed.
- Full `tests/` suite: see PR for current pass count (same 4 pre-existing,
  unrelated shallow-clone-artifact failures expected, reproducible
  independent of this branch).
- Verified zero diff against every production file.
- Frozen NB dispersion verified byte-exact against the canonical MLB-RSCH-0010
  artifact at import time, never refit.

## 12. Final questions and classification

**A. Are expected-run means systematically biased?** Only marginally, and
not in a way any of the three tested transforms could productively correct.
Per-band bias is small (< 0.4 runs) everywhere it can be estimated with
meaningful sample; the apparent global slope/intercept signal is dominated
by genuine per-game scoring noise, not a robust structural pattern.

**B. Primary issue?** **No meaningful bias** — closest to "no meaningful
bias" among the listed options; what small band-level bias exists doesn't
generalize into a global affine or quadratic correction that beats the
control.

**C. Which candidate wins DEV/VAL?** None — C0 (no calibration) is retained.

**D. Survives 2026?** N/A — no candidate reached the holdout stage.

**E. Improves downstream probabilities?** No candidate was adopted, so no.
(Note: DEV/VAL frozen-NB *point deltas* were marginally favorable for all
three candidates even as their MAE regressed — an interesting but
non-actionable observation, since the primary mean-accuracy gate correctly
vetoes point-accuracy regressions regardless.)

**F. Which market families benefit most?** None — no calibration was
adopted.

**G. Closes the historical Pinnacle gap further?** No — gap is unchanged
(0.008149 ML / 0.006019 total), since C0 is retained.

**H. Justifies prospective shadow?** **NO.**

**Overall classification: NO MEANINGFUL IMPROVEMENT / CONTROL SUPERIOR.**
MLB-RSCH-0009's frozen expected-run mean, holding offense and bullpen
construction fixed, is not improved on by any of the three preregistered
post-hoc calibration transforms tested here. Combined with MLB-RSCH-0012's
and MLB-RSCH-0013's own null results on refitting the mean's *inputs*, this
experiment closes off a third, structurally distinct lever (a calibration
*layer* on the mean's *output*) — production's existing mean construction is
not obviously improvable by any of the three simple approaches this research
program has now tested.

## 13. Recommended next experiment

Given MLB-RSCH-0012 (offense inputs), MLB-RSCH-0013 (bullpen inputs), and
MLB-RSCH-0014 (mean calibration layer) have now all returned null results on
the *mean* side of the model, the highest-value next probability experiment
is a genuinely different lever rather than a fourth attempt at the mean:
**a PIT-safe opponent-strength (schedule-adjustment) experiment** — MLB-RSCH-0012's
own O4 was marked `NOT_EVALUABLE_IN_THIS_EXPERIMENT` because it requires a
new per-prior-game, per-date opponent-quality snapshot lookup that doesn't
exist yet. Building that lookup and testing whether a team's raw season-to-date
rate should be adjusted for the quality of competition actually faced is a
structurally new, well-motivated, and not-yet-tested hypothesis — distinct
from every lever tested across MLB-RSCH-0012/0013/0014, and directly
actionable once the snapshot infrastructure exists. Other viable candidates
noted for completeness: bullpen component talent (K%/BB%/HR-rate, distinct
from the already-rejected shrinkage-refit lever), early-season offense prior
(section 7, feasible but needs its own preregistration), and lineup/hitter
aggregation once the PIT-safe archive grows past its current ~2.5-month
depth.
