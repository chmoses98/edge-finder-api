# MLB-RSCH-0016: Joint Schedule-Adjusted Mean + Dispersion

Status: **COMPLETE (real 2022-2026 results).**

RESEARCH ONLY. No production behavior changed. NOT a rescue/tuning
continuation of MLB-RSCH-0015 — a new preregistered hypothesis motivated by
it.

## 1. Purpose

MLB-RSCH-0015 found that S1 (its one-hop schedule-adjustment mean) produced
a real, significant, broadly-based mean-accuracy improvement over S0, but
consistently worsened downstream frozen-NB probability scoring. This
milestone tests whether that loss is explained by a mismatched dispersion
parameter: MLB-RSCH-0010's dispersion was fit around S0's own residual
characteristics — does refitting it specifically for S1's mean (with S1's
own construction held completely frozen) recover the probability loss?

## 2. Registration

| | |
|---|---|
| Experiment ID | `MLB-RSCH-0016` |
| Evidence level | `E2_PIT_HISTORICAL` |
| S1 mean | Imported and reused **completely unchanged** from `run_opponent_strength_experiment.py` (MLB-RSCH-0015) — no coefficient, formula, or eligibility rule touched |
| Corpus scale | 10,204 games / 20,408 team-observations (dev 6,378 / val 2,127 / holdout 1,699) |

## 3. Residual diagnostics (DEVELOPMENT, S0 vs S1)

| | S0 | S1 |
|---|---|---|
| Mean residual (predicted − actual) | -0.0897 | **-0.1798** |
| Residual variance | 9.6667 | 9.6842 |
| Residual variance / predicted mean | 2.2259 | 2.2828 |
| DEV-fit NB dispersion | **0.281513** (exact match to the canonical MLB-RSCH-0010 artifact — validates the diagnostic method) | **0.300481** |

S1's fitted dispersion is measurably higher than S0's (+6.7%), confirming
the core mechanism hypothesis: S1's residuals genuinely are more
overdispersed relative to S0's. **But** S1's mean residual is roughly
**double** S0's (-0.18 vs -0.09) — S1 systematically under-predicts more
than S0 does, even though its absolute error (MAE) is lower. This is a
concrete, partial explanation for the probability-scoring loss: a lower
MAE does not imply a smaller or more favorable systematic bias, and
probability calibration is sensitive to bias in a way raw MAE is not.

## 4. J0 vs J1 (DEVELOPMENT)

| | Delta (J1 − J0) |
|---|---|
| game_total | +0.001322 |
| moneyline | +0.000348 |
| run_margin | +0.000198 |
| team_total_away | +0.000949 |
| team_total_home | +0.001053 |
| **Aggregate primary** | **+0.000918** |

**J1 fails the DEV gate** — still worse than J0, across every family. For
comparison, MLB-RSCH-0015's own J2 configuration (S1 mean + the OLD
dispersion, i.e. exactly what MLB-RSCH-0015 tested) had a DEV primary delta
of **+0.000969**. Refitting dispersion specifically for S1 only narrows the
gap by **0.000051** — a negligible fraction of the total loss.

**Conclusion: dispersion mismatch is NOT the primary driver of S1's
probability-scoring loss.** The mean-shift/bias difference documented in
section 3 is a more likely primary contributor, though this experiment does
not fully decompose the remaining gap — a genuine open question (section
10).

Per the preregistered stop rule, **no VALIDATION or HOLDOUT evaluation was
performed** — this specific dispersion-refit path is retired outright, not
re-tuned with an alternate dispersion.

## 5. Tail calibration (DEVELOPMENT)

| Event | Empirical | J0 predicted | J1 predicted |
|---|---|---|---|
| shutout | 0.1322 | 0.1152 | **0.1262** (closer) |
| team 10+ runs | 0.1366 | 0.1324 | 0.1285 (further) |
| game 15+ total | 0.1099 | 0.1029 | 0.0977 (further) |
| margin 5+ | 0.2749 | 0.2774 | 0.2764 (marginally closer) |
| margin 7+ | 0.1326 | 0.1336 | 0.1337 (essentially tied) |

**Mixed, not uniformly worse** — J1 actually calibrates the shutout tail
better than J0, but calibrates high-scoring tails (team 10+, game 15+ total)
worse. This is consistent with S1's larger negative mean bias (section 3):
systematically lower predicted means naturally predict shutouts somewhat
better and high-scoring blowouts somewhat worse.

## 6. Pinnacle secondary

Since J1 did not pass DEV selection, only J0's own existing gap is reported
for reference (unchanged from every prior milestone): ML 0.008149, 95% CI
[0.0003, 0.0156]. No J1 comparison is meaningful since it was never a
candidate for adoption.

## 7. Tests

- `tests/edgelab/test_run_joint_dispersion_experiment_script.py` — 28 tests:
  preregistration idempotency, S1-frozen-reuse proofs (imports and calls
  `rsch0015`'s own functions, never reimplements the schedule-adjustment
  formula), residual-diagnostics correctness (reuses
  `fit_overdispersion_dev_only` unchanged), parameterized-dispersion NB-cell
  determinism/valid-range/asymmetric-dispersion-support, all selection-rule
  gates (DEV gate, VAL noninferiority tolerance including the exact boundary
  case), holdout-gated-by-both-DEV-and-VAL-in-order proof, tail-calibration
  efficiency proof (builds the joint PMF grid once per row, not once per
  predicate — a 5x reduction), Pinnacle-runs-only-after-selection ordering.
- Full `tests/edgelab/` suite: see PR for current pass count.
- Verified zero diff against every production file.
- Frozen old NB dispersion verified byte-exact against the canonical
  MLB-RSCH-0010 artifact at import time; the fitted S0 dispersion
  independently reproduces it exactly, a strong internal consistency check
  on the reused fitting method.

## 8. Final questions

**A. Did S1 materially change residual dispersion?** Yes — a measurable,
real +6.7% increase in fitted NB dispersion.

**B. Was the old NB dispersion inappropriate for S1?** Modestly — the
refit dispersion is genuinely different, but not different enough to
explain more than a small fraction of the observed probability loss.

**C. Does refitting dispersion recover the probability loss?** **No** —
J1's DEV delta (+0.000918) is barely better than the original,
non-dispersion-adjusted comparison (+0.000969); the gap closes by only
~5%.

**D. Does J1 beat J0 in development?** **No.**

**E. Replicate in 2025 / F. Survive locked 2026?** Not evaluated — DEV gate
failed, per the preregistered stop rule.

**G. Which market families benefit?** None on DEV; all five families
remain in the "worse" direction, just by a slightly smaller margin than
without the dispersion refit.

**H. Is schedule adjustment ultimately:** useful only for mean accuracy, not
(yet) for probabilities — refitting dispersion alone does not bridge the
gap. The remaining loss is more likely explained by S1's larger systematic
mean bias (section 3) than by a residual-variance/dispersion mismatch.

## 9. Overall classification

**NO MEANINGFUL IMPROVEMENT / CONTROL SUPERIOR.** J1 does not beat J0 even
on the DEVELOPMENT data it was fit on. This specific dispersion-refit path
is retired — not re-tuned with an alternate dispersion parameter, per the
preregistered stop rule.

**Prospective shadow justified: NO.**

## 10. Recommended follow-up (not run here)

The finding that mean bias (not dispersion) is the more likely driver of
S1's probability loss suggests the next natural question is whether a
bias-correcting calibration layer (in the spirit of MLB-RSCH-0014's own
tested-and-rejected C1/C2/C3, but applied specifically to S1's mean rather
than S0's) could recover value where a dispersion refit alone could not —
though MLB-RSCH-0014's own null result on S0 is a real caution against
assuming this would work. A cleaner, higher-value path is likely
MLB-RSCH-0017 (early-season offense prior), a structurally independent,
still-unresolved question this research program has repeatedly flagged
but never directly tested.
