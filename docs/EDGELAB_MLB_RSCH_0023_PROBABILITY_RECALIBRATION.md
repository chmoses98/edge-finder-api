# MLB-RSCH-0023: Production Probability Recalibration

Status: **COMPLETE. NO VALIDATED CORRECTION — path retired at LEVEL 0.**

RESEARCH ONLY. No production changes. Nothing fit to market prices or
ROI. Zero new API calls. FORWARD window (settle > 2026-08-28) untouched.

## 1. Question

MLB-RSCH-0022 found a large, systematic overconfidence signature in
production's archived pregame Kalshi probabilities. This experiment,
preregistered separately, asked the natural corrective question: **can a
tiny, monotone, DEV-fit logit-affine recalibration of production's
probability surface improve proper scoring on later, unseen days?**

## 2. Design (all locked before any fit ran)

- **R0** control: archived probabilities as-is.
- **R1**: one global map `p' = sigmoid(a + b·logit(p))`, 2 parameters,
  fit on DEV by log-loss (deterministic damped Newton — see §6 for a
  real divergence bug found and fixed during development).
- **R2**: the same map fit per three preregistered market-structure
  tiers (GAME / LOCAL / PROPS; 6 parameters).
- **Split**: DEV = settle ≤ 08-22 (fit), VAL = 08-23..08-28
  (replication, never refit), FORWARD = settle > 08-28 (fully blind,
  not computed).
- **Contamination disclosure**: RSCH-0022 already observed the pooled
  miscalibration direction through 08-28, so VAL is not direction-blind
  — the classification was hard-capped at LEVEL 1 even for a clean
  pass. (It did not pass, so the cap was never reached.)
- **Selection**: DEV Brier & log-loss improve; **VAL Brier must strictly
  improve**; DEV ECE must not worsen; R2 must improve ≥2 tiers.

Corpus: MLB-RSCH-0022's loaders reused unchanged — 3,137 rows;
DEV = 601, VAL = 2,536.

## 3. Results

| | R1 (global) | R2 (tiers) |
|---|---|---|
| DEV-fit params | a=−0.051, **b=0.127** (extreme shrink) | GAME: b=1.05; LOCAL: b=0.133; PROPS: **no fit possible (n=0)** |
| DEV paired Brier delta | **−0.0190** [−0.0285, −0.0102] | −0.0196 [−0.029, −0.0108] |
| DEV ECE | 0.117 → **0.0066** | 0.117 → 0.031 |
| VAL paired Brier delta | **+0.0101** [−0.0042, +0.0235] | **+0.0097** [+0.0035, +0.0156] |
| VAL ECE | 0.101 → 0.121 (worse) | 0.101 → 0.116 (worse) |
| VAL log loss | 0.657 → 0.647 (better) | 0.657 → 0.683 (worse) |
| Gap to market (VAL, Brier) | +0.062 → +0.073 (wider) | +0.062 → +0.072 (wider) |
| **Selection** | **FAIL (VAL non-replication)** | **FAIL (VAL non-replication)** |

Per the preregistered rule, **both candidates are retired. No re-fit
with different windows or parameters was attempted** — that would be
exactly the post-hoc rescue this program forbids.

## 4. Why it failed — the diagnosis is itself the finding

The DEV window (capture-system ramp-up) contained only **four** families
— team_total (352), first_inning_run (155), inning_result (54),
game_result (40) — with **zero** pitcher props, winning margins, game
totals, or inning totals. The VAL window contains nine families
including 625 prop rows. A global shrink of b≈0.13, fit on that narrow
early mix (whose measured miscalibration was severe), drastically
overcorrects the very different late-August family mix. In-sample the
map does exactly what it should (DEV ECE 0.117 → 0.007); it simply does
not transfer across a shifted composition and period.

Two honest nuances:
- R1 *did* improve VAL **log loss** (0.657 → 0.647) while worsening VAL
  Brier — a heavy shrink protects the extreme-error tail that log loss
  punishes hardest. Mixed evidence, not a pass under the preregistered
  Brier-primary rule, and not reinterpreted post hoc.
- The preregistered null (H3: the miscalibration magnitude/composition
  is unstable across weeks at current archive depth) is the hypothesis
  the data supported.

## 5. What this means for 2026 profitability

1. **No probability correction is validated for use.** Anyone tempted to
   "just shrink the probabilities" now has preregistered evidence that a
   correction fit on one window overcorrects the next at current data
   depth.
2. The protective conclusions of MLB-RSCH-0022 stand unmodified:
   distrust model edges (especially props/margins); complete-input rows
   are least-bad.
3. **The right next attempt is mechanical, not clever**: let the archive
   accumulate 2–3 more weeks of the CURRENT balanced family mix, then
   re-run this exact preregistered design with a composition-balanced
   DEV window and the (still-untouched) FORWARD window as the blind
   test. That re-run is a NEW experiment; this one is closed.

## 6. Engineering note — real bug found and fixed

The first implementation used undamped Newton for the 2-parameter
logistic fit; on a synthetic overconfident test case it diverged to
parameters ~1e7. Fixed with a deterministic backtracking line search
(halve the step until log-loss does not increase, max 25 halvings);
regression-tested (`test_damping_regression_never_diverges`), and
verified to recover b≈0.32 on the overconfident synthetic and a≈0,
b≈0.96 on a calibrated synthetic.

## 7. Tests

`tests/edgelab/test_run_probability_recalibration_experiment_script.py`
— 22 tests: registration idempotency, loaders-reused-from-RSCH-0022
proofs, fit correctness on both synthetic regimes, the damping
regression test, map monotonicity/identity/bounds, preregistered tier
constants, all selection gates (including strict-zero VAL boundary and
the R2 concentration gate), FORWARD-untouched proofs, no-market-price
and no-ROI-terms-in-fit proofs, LEVEL-1 classification cap proof.

## 8. Classification

**LEVEL 0 — NO VALIDATED CORRECTION.** R1/R2 retired as specified.
Production unchanged. FORWARD window remains blind for the successor
re-run.
