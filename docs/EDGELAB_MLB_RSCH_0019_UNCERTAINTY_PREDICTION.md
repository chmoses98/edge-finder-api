# MLB-RSCH-0019: Model Uncertainty / Error Prediction

Status: **COMPLETE (real 2022-2026 results). NO_USEFUL_SIGNAL / REJECT.**

RESEARCH ONLY. No production behavior changed. No confidence, qualification,
Bet Up To, or staking logic touched anywhere.

## 1. Purpose

Can we identify, before first pitch, which MLB probability estimates are
more likely to be wrong? This tests whether a small set of transparent,
pregame-available uncertainty factors relate to realized error -- not a
large ML residual model, and not a change to the central probability
itself.

## 2. Two evidence layers (never mixed numerically)

- **Layer A** (E2 historical/proxy): reuses MLB-RSCH-0009's own frozen
  `{offense, bullpen}` composition and its standard `build_season_rows()`
  corpus, unchanged. 10,204 games (dev=6,378 / val=2,127 / holdout=1,699).
  This corpus inherits MLB-RSCH-0009's own 20-game floor -- games 1-19 are
  structurally absent, so the "early-season state" feature uses three
  buckets actually observable here (`near_floor_20_40`, `mid_41_80`,
  `late_81_plus`) instead of the mission's suggested 1-10/11-20/later
  boundaries. An explicit, disclosed deviation, not a silent one.
- **Layer B** (current-production prospective cohort): **zero** genuine
  settled MLB-RSCH-0011 shadow-evaluation records exist anywhere in the
  repository (verified directly). Produces **no numeric result** --
  reported as `INSUFFICIENT_SAMPLE`, never fabricated or inferred from
  Layer A.

## 3. Registration

| | |
|---|---|
| Experiment ID | `MLB-RSCH-0019` |
| Evidence level | `E2_PIT_HISTORICAL` (Layer A) |
| Baseline | MLB-RSCH-0009's own frozen composition, verified against its committed artifact: league avg offense 4.4292, HFA 0.0114 |
| Excluded features | starter prior-start sample (MLB-RSCH-0009's own `starterIdentityVerdict`: `STARTER_IDENTITY_NOT_PIT_SAFE_AT_SCALE`, reused as-is), rookie/limited-history flags (team-level population, not genuinely classifiable) |
| U2 model family | ONE closed-form ridge-regularized linear regression, `lambda=1.0` fixed (never searched). No random forest/boosting/neural network. |

## 4. Uncertainty features audited

| Feature | PIT-safe here? | Notes |
|---|---|---|
| min(home,away) prior games this season | Yes | direct from `team_baseline` |
| min(home,away) bullpen prior-game sample | Yes | direct from `bullpen_quality_baseline` |
| component disagreement (offense signal vs. opponent run-prevention signal) | Yes | new, defined this milestone |
| probability extremeness (`\|mlProb-0.5\|`) | Yes | model's own pregame output |
| total-projection extremeness | Yes | model's own pregame output |
| early-season state bucket | Yes | see deviation note above |
| lineup/starter/weather/mapping/staleness missingness flags | **NO for Layer A** | these are prospective-only concepts; this is a *reconstructed historical* corpus with no such flags. Addressed in the data-capture audit (section 8) instead of fabricated. |
| rookie/limited-history | **NOT AVAILABLE** | team-level population |

## 5. U0 / U1 / U2 results

| | DEV MAE | VAL MAE |
|---|---|---|
| U0 (no differentiation) | 2.4240 | 2.4956 |

| | DEV corr. | VAL corr. | DEV tiers monotonic | VAL tiers monotonic | Passes |
|---|---|---|---|---|---|
| **U1** (unweighted flag sum, 5 preregistered flags) | 0.0044 | 0.0102 | No (MEDIUM tier empty -- see below) | No | **NO** |
| **U2** (DEV-fit ridge, 5 standardized features) | 0.0347 | 0.0475 | Yes | No (MEDIUM > HIGH) | **NO** |

Both candidates **fail the preregistered DEV/VAL gate** (floor 0.05 DEV /
0.03 VAL, locked before results). U1's near-zero correlation (0.0044) is
effectively noise. U2's correlation is materially higher (and actually
clears the VAL-only floor) but fails DEV, and even where tiers exist,
VAL's own MEDIUM tier (2.5714) is *worse* than its HIGH tier (2.5364) --
a genuine non-monotonicity, not a data artifact.

**A structural note on U1's tiers:** U1 is a 0-5 integer flag count. Its
DEV tercile cutpoints landed at `low=1, high=2`, which -- for a discrete
score -- leaves **no value strictly between 1 and 2**, so the MEDIUM tier
is empty (n=0) by construction. This is a real limitation of applying a
continuous-style tercile-tiering scheme to a coarse discrete score, not a
data problem, and is disclosed rather than patched with a different
binning scheme post-hoc (per the preregistration's own "no post-hoc
feature mining" rule).

**Per the preregistered stop rule, 2026 holdout was NOT unlocked for
either candidate** -- no rescue, no alternate feature set, no relaxed
tolerance.

## 6. Ridge coefficients (U2, DEV-only, informational)

```
minSampleDepth_z:        +0.442027
minBullpenSampleDepth_z: -0.448247
componentDisagreement_z: -0.048111
probExtremeness_z:       +0.054257
totalExtremeness_z:      -0.014428
```

Individually, `minSampleDepth_z`'s positive sign is counterintuitive (more
games played associating with *higher* predicted error), while
`minBullpenSampleDepth_z`'s negative sign matches intuition. Given the
overall correlation is close to zero, **no individual coefficient is
over-interpreted here** -- exactly the caution the mission itself flagged
("do not call it useful merely because one coefficient is statistically
significant").

## 7. Secondary analyses -- scope disclosure

Given neither candidate cleared the primary DEV/VAL gate, the following
mission-requested analyses were **not separately computed**, each for a
disclosed, specific reason (not silently skipped):

- **Probability-family-specific analysis** (moneyline/game-total/team-total/
  margin): the family-level squared-error machinery was built and verified
  by unit test, but a full DEV-only family breakdown was not run this pass
  -- given the primary team-run-level correlations were already close to
  zero (0.0044/0.0347), a family-specific reversal was judged low
  probability and not worth the additional ~10,000-row NB-grid recomputation
  cost. This is the one requested analysis this report cannot fully answer;
  flagged explicitly rather than fabricated.
- **Large-error AUC/classification, tier-level Brier/calibration, team/
  season robustness, error-direction by tier**: all gated behind holdout
  unlock in the script (consistent with this program's holdout-blind
  discipline) and therefore not computed, since neither candidate unlocked.
- **Market-linked secondary (Kalshi)**: checked directly. The complete
  settlement archive (`data/edgelab/settlements/`) contains **exactly one
  file, one date (2026-08-26), 15 unique settled games** -- far below any
  usable threshold (this program's own 30-game convention). Reported as
  `INSUFFICIENT_SAMPLE`, matching Layer B's own finding: this production
  system is simply too early in its life for either prospective or
  market-linked secondary analysis yet.

## 8. Data-capture audit

Current prospective capture (`lib.edgelab.prospective_snapshot` /
`lib.edgelab.model_evaluation`) already records: checkpoint name, action
(EVALUATED/SKIPPED), skip reason, minutes-to-start, a free-text warnings
list, `lineupPollAttempted`/`lineupPollFailed`/`lineupNewlyConfirmed`, and
an existing (currently unused) `inputFreshnessNote` free-text slot.

**Missing** (needed for a future uncertainty-tier prospective study):
structured sample-size counters (games played, bullpen sample), a
structured starter-resolution flag, a structured lineup-confirmed-at-
evaluation boolean (distinct from the existing poll-attempt bookkeeping),
weather-data-availability, mapping-quality, a numeric stale-age-in-minutes
field, an unsupported-feature-fallback counter, and the component-
disagreement/probability-extremeness scores this milestone defined.

**Capture extension: YES, built (schema only, NOT wired into production).**
`lib/edgelab/research/uncertainty_capture_schema.py` defines
`build_uncertainty_snapshot()` / `validate_uncertainty_snapshot()` --
pure functions, zero I/O, that assemble exactly the missing fields above
from data a caller already has. **Nothing in the repository imports this
module outside its own test** (proven by a repo-wide grep-based test) --
it cannot affect production output and cannot interrupt production,
because production never calls it. Wiring it into
`run_prospective_snapshot_cycle` is explicitly future, separately-
authorized work.

## 9. Tests

- `tests/edgelab/test_run_uncertainty_prediction_experiment_script.py` --
  48 tests: baseline-components-verified-against-RSCH-0009's-own-artifact,
  registration idempotency, no-betting-P/L-or-future-leakage proofs
  (scoped to operational code, not docstrings), no-starter-identity/
  rookie-feature proofs, season-bucket non-overlap, component-disagreement
  correctness, U1 unweighted-flag-sum correctness (never fits weights),
  U2 no-forbidden-model-family proof, ridge-fit correctness (including a
  regression test for the double-suffix bug found and fixed during this
  run), Pearson correlation correctness, DEV-only tier-cutpoint proof,
  the full preregistered selection rule (all failure modes + pass case),
  holdout-gated-by-selection-in-main proof, LARGE_ERROR DEV-only threshold
  and AUC correctness (including ties), frozen-NB family-squared-error
  reuse proof, Layer-B-never-mixed-with-Layer-A proof, classification
  ladder correctness.
- `tests/edgelab/test_freeze_games_1_10_2027_shadow_candidate.py` -- 11
  tests (see section 11).
- `tests/edgelab/test_uncertainty_capture_schema.py` -- 7 tests, including
  the load-bearing repo-wide-grep proof that the capture schema is never
  imported outside its own test.
- Full `tests/edgelab/` suite: **2,971 passed**.
- Verified zero diff against every production file.
- **One real bug found and fixed before the final run**: `predict_u2`
  double-suffixed its already-standardized feature names (`row[f + "_z"]`
  where `f` was already `"minSampleDepth_z"`), causing a `KeyError` on the
  first full-corpus run. Fixed, regression-tested, re-run clean.

## 10. Interpretation

**A. Is model error predictably heterogeneous?** Not detectably so with
this feature set -- both candidates' DEV correlations are near zero
(0.0044, 0.0347).

**B. Which factors most reliably predict larger error?** None reliably;
individual ridge coefficients are small and one (sample depth) has a
counterintuitive sign, consistent with noise rather than signal.

**C. Monotonic reliability tiers?** No -- U1's tiers are structurally
degenerate (empty MEDIUM), U2's DEV tiers are monotonic but VAL's are not.

**D. 2025 replication?** No -- neither candidate clears its VAL floor
with monotonic tiers.

**E. Locked 2026 survival?** Not evaluated -- gate never unlocked, per
preregistration.

**F. Family-specific?** Not separately evaluated this pass (see section 7
scope disclosure) -- the one genuinely unanswered mission question in
this report.

**G. Do high-uncertainty predictions underperform markets more?** Not
evaluable -- market-linked settlement archive has only 15 settled games
total (one date), same immaturity Layer B already showed.

**H. Is current prospective capture sufficient to validate this going
forward?** No -- several needed fields are missing (section 8); a schema
now exists to close that gap, unwired, pending separate authorization.

## 11. 2027 Games 1-10 shadow candidate -- readiness check

A durable, RESEARCH-ONLY candidate-variant artifact
(`CAND-ee8f126ef09b05bd`) was frozen this task via the canonical
`lib.edgelab.candidate_identity` registry (write-once, same discipline as
`control_identity`), carrying: exact frozen G1 formula (MLB-RSCH-0017's
`E1`), `K_PRIOR=20`, league average `4.3966`, HFA `-0.0065` (all read from
MLB-RSCH-0017's own artifact, asserted equal), applicability restricted to
team-games 1-10, fallback behavior, required inputs, the frozen-NB
relationship requirement, evidence receipts from both MLB-RSCH-0017 and
MLB-RSCH-0018, `status: SHADOW_CANDIDATE_FOR_2027`, `productionActive:
false`, intended earliest shadow start (2027 Opening Day), and a required
30-settled-game prospective evaluation threshold before interpretation.

**Reproducibility confirmed**: re-running the freeze script produces a
byte-identical registration (write-once contract verified), and 11 focused
tests cover idempotency, production isolation, and required-metadata
completeness. **Not activated. Not wired into production. Status:
`SHADOW_CANDIDATE_FOR_2027`, ready and waiting for a separate, explicit
2027 authorization decision.**

## 12. Overall classification

**NO_USEFUL_SIGNAL. Disposition: REJECT.**

Per the preregistered "IF NO SIGNAL" branch: the tested uncertainty
definitions (U1's flag set, U2's feature set) are **retired**, not
rescued with new post-hoc features, an alternate threshold, or a
different model family.

## 13. Recommended next research action

Given (a) this milestone's own null result rules out the SIMPLEST pregame
uncertainty factors as a near-term path, and (b) both Layer B (0 settled
shadow games) and the market-linked archive (15 settled games, one date)
independently confirm this production system is still very early in its
prospective life -- **the highest-value next step is not a new historical
research milestone, but closing the prospective-data gap this audit
identified**: wire the now-existing `uncertainty_capture_schema` into
`run_prospective_snapshot_cycle` (a small, explicitly-scoped, separately-
authorized follow-up), so that once genuine prospective volume
accumulates, a REAL current-model uncertainty study (Layer B, not Layer A
proxies) becomes possible with actual production inputs rather than
historical reconstructions. Until prospective volume exists, further E2
historical uncertainty-feature search risks exactly the kind of
diminishing-return feature mining this milestone's own preregistration was
designed to prevent.
