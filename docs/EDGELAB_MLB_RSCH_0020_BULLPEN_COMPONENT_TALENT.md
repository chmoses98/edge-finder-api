# MLB-RSCH-0020: Bullpen Component Talent

Status: **COMPLETE (real 2022-2026 results). NO_MEANINGFUL_IMPROVEMENT / REJECT.**

RESEARCH ONLY. No production behavior changed.

## 1. Purpose

Can we estimate true bullpen run-prevention talent better using pitching
COMPONENTS (strikeouts, walks) than using MLB-RSCH-0009's own existing
bullpen ERA/ER9 baseline? MLB-RSCH-0009 established bullpen quality
carries real signal; MLB-RSCH-0013 established that simply re-shrinking
the SAME statistic doesn't help. This milestone asks whether a
DIFFERENT statistic (K-BB%) measures bullpen talent better.

## 2. Data (existing caches only, zero new acquisition)

- B0's exact source: `data/research_cache/bullpen_backtest/` (via
  `run_proxy_ablation_experiment.load_relief_er9_games`, unchanged).
- B1's source: `data/research_cache/starter_workload/` (MLB-RSCH-0004's
  own richer cache) -- carries strikeOuts/baseOnBalls/battersFaced for
  every pitcher (starter orderIndex=0, relievers orderIndex>0) across
  2022-2026, ~2,430 games/season. Joined onto MLB-RSCH-0009's own
  per-team schedule (`load_all_team_games_with_venue`) by gamePk.
- **B2 (K/BB/HR): NOT_RUN.** Verified directly -- neither cache
  contains a per-pitcher `homeRuns` field at any scale (checked all 5
  seasons of the starter_workload cache). Building it would require a
  genuinely new MLB Stats API acquisition via a dedicated GitHub Actions
  workflow whose correctness this sandboxed session has no way to
  verify (direct MLB API calls here return 403 Forbidden). Applying the
  milestone's own explicit B4 allowance to this genuine data gap.
- **B4 (reliever-level aggregation): NOT_RUN.** Would require inferring
  individual-reliever availability without target-game usage -- a
  materially larger modeling project than this milestone's own "tiny
  parameter count" philosophy allows.

## 3. Registration

| | |
|---|---|
| Experiment ID | `MLB-RSCH-0020` |
| Evidence level | `E2_PIT_HISTORICAL` |
| B0 | MLB-RSCH-0009's own bullpen component, exact reproduction verified (50-row spot check, byte-identical total-runs match against `rsch0009.attach_predictions`'s own output) |
| B1 | K-BB% (strikeouts minus walks per batter faced), DEV-fit shrinkage `K=80` (from grid `(10,20,30,50,80)`), DEV-fit linear mapping to predicted relief ER9 (slope -25.79, intercept 8.26) |
| B3 | Single DEV-fit blend weight (0.5, from grid 0.0-1.0 step 0.1) of B0 + B1 |
| Corpus | 12,794 DEV / 4,266 VAL / 3,408 HOLDOUT bullpen-outcome observations; 6,378 DEV / 2,127 VAL / 1,699 HOLDOUT team-mean rows |

## 4. Primary bullpen-outcome result (DEV)

| | B1 delta | B3 delta |
|---|---|---|
| MAE delta | -0.0021 (CI [-0.0107, 0.0065], crosses zero) | **-0.0061** (CI [-0.0103, -0.0017], fully negative) |

B3 (the blend) shows a real, significant mean-accuracy improvement on
DEV; B1 alone is directionally favorable but not clearly significant.

## 5. Team-mean integration (DEV)

| | B1 delta | B3 delta |
|---|---|---|
| MAE delta | -0.0013 (CI crosses zero) | **-0.0043** (CI [-0.0063, -0.0024], fully negative) |

Team robustness: B1 17/30 teams improved, B3 22/30 -- broad, not
concentrated in a handful of bullpens.

## 6. Frozen-NB probability (DEV and VAL) -- the result that matters

| | DEV | VAL |
|---|---|---|
| B1 primary delta | +0.000649 (unfavorable) | +0.001019 (unfavorable) |
| B3 primary delta | +0.000641 (unfavorable) | +0.000405 (unfavorable) |

**Both candidates worsen frozen-NB probability scoring, consistently on
both DEV and VAL**, despite real, significant mean-accuracy gains (B3
especially). **This is the same pattern this research program already
found with MLB-RSCH-0015's S1 schedule-adjustment candidate: a genuine
mean-accuracy win does not automatically translate into a probability
win.** Per the preregistered selection rule, both candidates **fail on
the DEV and VAL probability gate** -- **2026 holdout was NOT unlocked**,
no rescue attempted.

Validation direction also didn't fully replicate for B1's own bullpen-
outcome metric specifically (VAL delta +0.015, unfavorable) even though
DEV was favorable -- a second, independent reason this candidate would
not have survived even without the probability failure.

## 7. Sample-depth bands (DEV, descriptive)

| Band | MAE delta (B0 vs B3) | n |
|---|---|---|
| first_20_ip | n/a (0 observations -- B0's own 20-game floor structurally excludes this band, same limitation this program has repeatedly documented) | 0 |
| 20_50_ip | **-0.0221** (largest favorable delta) | 2,700 |
| 50_100_ip | +0.0060 (unfavorable) | 4,500 |
| 100_plus_ip | +0.0011 (~flat) | 5,500 |

An interesting, honestly-reported nuance: the component-based candidate's
benefit concentrates in the band closest to the eligibility floor
(20-50 prior relief-appearance games), partially consistent with H1's
own hypothesis that K-BB% might help most before ERA-style estimates
stabilize -- but this does not rescue the overall failed probability
gate, and per preregistration no new candidate was spun out of this
observation.

## 8. Production mapping (read-only)

Production's live pipeline (`scripts/build_market_ledger.py`) **already
uses an xFIP-style bullpen metric** (`away_bp.get("xFIP")` /
`home_bp.get("xFIP")`, with an `xFIPMethod` provenance flag and a
separate high-leverage-reliever-specific xFIP signal --
`hlXFIP`/`hlGrade`/`hlAvailable`/`hlDivergence`/`hlSamplePA`) -- **not**
the simple ERA/ER9 baseline this whole EdgeLab research program has used
as its historical control. xFIP is itself a K/BB/HR-based component
metric, conceptually the same family as B1/B3's own K-BB% construction,
just richer (includes HR, which this milestone could not build at
scale). **Verdict: PARTIALLY_INFORM** -- this result neither confirms
nor contradicts production's existing xFIP choice (this milestone's own
narrower K-BB%-only construction did not clear its own probability bar,
but production's richer xFIP was never itself tested here).

## 9. Tests

- `tests/edgelab/test_run_bullpen_component_talent_experiment_script.py`
  -- 34 tests: frozen-dispersion verification, registration idempotency,
  relief-only classification (orderIndex>0 excludes the starter),
  zero-relievers-yields-None-never-fabricated-zero, K-BB% denominator
  correctness, no-future-leakage proofs, B0 exact-reproduction proof,
  DEV-only fitting (mapping + blend weight, both AST-verified to never
  reference VAL/holdout), validation-never-refits ordering proof,
  holdout-inaccessible-before-gate proof, frozen-NB-unchanged proof,
  Pinnacle-after-holdout-only gating proof, B2/B4 NOT_RUN documented in
  the report and never referencing a `homeRuns` field operationally,
  fixed sample-depth bands, the full preregistered selection rule, and
  the closed-form OLS helper's correctness.
- Full `tests/edgelab/` suite: **3,017 passed**.
- Verified zero diff against every production file.
- **One real bug found and fixed before the final run**: the per-game
  records from `load_all_team_games_with_venue()` carry no `"season"`
  key at all -- the first full run's `build_bullpen_rows()` silently
  read `g.get("season")` as `None` on every row, making the team-mean
  integration's `(season, gamePk, teamId)` lookup keys never match
  (B1/B3 team-mean deltas were exactly `0.0` and team robustness was
  exactly `0/30` for both candidates -- the unmistakable signature of a
  silent lookup miss, not a real null result). Fixed by passing `season`
  explicitly into `build_bullpen_rows`/`build_bullpen_rows_multi_season`
  rather than reading a field that was never there; re-run produced the
  real, coherent, non-degenerate results reported above.

## 10. Interpretation

**A. Is bullpen ERA/ER9 sufficient?** For probability quality, this
milestone did not find a better alternative among what it tested.

**B. Does K-BB% improve true talent estimation?** For mean accuracy,
modestly yes (B3's blend especially, significant on both the bullpen-
outcome and team-mean metrics). For probability quality, no.

**C. Does HR information add incremental value?** Untested -- no data
at scale (B2 NOT_RUN).

**D. Does blending components + actual runs work best?** Among the two
candidates tested, yes -- B3 (the blend) consistently outperforms B1
alone on mean-accuracy metrics, though both fail the probability gate
together.

**E. Is value concentrated at low sample depth?** Suggestively yes
(20-50 IP band shows the largest favorable delta) but not confirmed as
a robust, gate-passing finding.

**F. Does the winner survive 2025?** No candidate passed DEV, so this
was never reached for the probability metric; B1's own bullpen-outcome
metric also failed to replicate on VAL independently.

**G. Does it survive locked 2026?** Not evaluated -- gate never
unlocked, per preregistration.

**H. Does it improve actual probabilities?** No -- worse on both DEV
and VAL, consistently, for both candidates.

**I. Which market families benefit most?** Not decomposed further --
the aggregate primary delta already failed the gate on both splits, so
per-family decomposition was not pursued (consistent with this
program's practice of stopping at a failed preregistered gate rather
than searching for a rescuing subset).

**J. Does it justify prospective shadow?** No.

## 11. Overall classification

**NO_MEANINGFUL_IMPROVEMENT. Disposition: REJECT.**

Per the preregistered selection rule, the tested component definitions
(B1, B3) are retired -- not rescued with alternate shrinkage constants,
mapping forms, or blend weights.

**Prospective shadow justified: NO.**

## 12. Recommended next research action

This is the SECOND time this research program has found a genuine,
significant mean-accuracy improvement that fails to translate into a
probability-scoring improvement (after MLB-RSCH-0015's S1). Combined
with MLB-RSCH-0016's own finding that a dispersion refit does not close
that gap for S1, and MLB-RSCH-0019's finding that simple pregame
uncertainty factors don't predict error heterogeneity either, the
highest-value next question is no longer "can we find another candidate
with a better point estimate" -- it's **why mean-accuracy gains keep
failing to reach probability gains**, a structural question this
program has now surfaced three separate times without resolving. The
recommended next experiment is a dedicated study of the mean-to-
probability translation gap itself: for every past candidate that
improved MAE but not Brier (S1, B1/B3 here), characterize what
specifically breaks -- bias direction, variance structure, or
calibration curvature -- using the SAME frozen NB engine, rather than
building yet another new mean-accuracy candidate ladder.
