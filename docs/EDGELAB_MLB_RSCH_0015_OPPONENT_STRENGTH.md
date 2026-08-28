# MLB-RSCH-0015: PIT-Safe Opponent-Strength / Schedule Adjustment

Status: **COMPLETE (real 2022-2026 results).**

RESEARCH ONLY. No production behavior changed.

## 1. Purpose

MLB-RSCH-0012's own O4 candidate was marked `NOT_EVALUABLE_IN_THIS_EXPERIMENT`
because a genuine, historical, PIT-safe opponent-quality snapshot for every
prior game did not yet exist. This milestone builds that capability and
tests it directly: does adjusting a team's raw offense/run-prevention rate
for the quality of opponents actually faced improve prediction of future
scoring, beyond MLB-RSCH-0009's frozen unadjusted baseline?

## 2. Registration

| | |
|---|---|
| Experiment ID | `MLB-RSCH-0015` |
| Evidence level | `E2_PIT_HISTORICAL` |
| Frozen mean composition (S0) | MLB-RSCH-0009's `{"offense","bullpen"}`, unchanged — HFA `0.0114`, byte-identical to the canonical artifact |
| Corpus scale | 10,204 games / 20,408 team-observations (dev 6,378 / val 2,127 / holdout 1,699) |

## 3. PIT-safe construction

`build_raw_baseline_lookup` calls `team_baseline()` (MLB-RSCH-0009, unchanged)
with `min_prior_games=0` for **every** game of every team, producing an
O(1)-indexed table of each team's own raw rate *as of that exact game*. For a
target game, each of a team's own strictly-prior opponents' quality is
looked up at the **specific meeting date**, never a season-final or
target-game-adjacent value — genuinely PIT-safe, with no future leakage
through indirect opponent ratings. `compute_schedule_adjustment` is a single
deterministic function, called twice: once against the raw lookup (S1,
one-hop) and once against S1's own output (S2, a bounded two-hop
approximation) — never a per-game fixed-point convergence solve, which would
be both far more expensive and add exactly the kind of many-tuning-knob
complexity this milestone was told to avoid.

**Eligibility**: the primary corpus keeps `MIN_PRIOR_GAMES_MAIN=20`
(matches S0/production exactly, for apples-to-apples comparison). Opponent
snapshots use a much lower `MIN_PRIOR_GAMES_OPPONENT=5` — an opponent below
that bar is excluded from the average, never fabricated.

## 4. Candidates and formula

- **S1**: `adjusted_offense = raw_offense + (league_avg_run_prevention −
  avg_opponent_run_prevention_faced)`, symmetric for run-prevention. Fed
  through the SAME frozen `stabilized_offense_rate(k=30)` shrinkage and SAME
  frozen bullpen blend as S0 — isolating the schedule-adjustment lever from
  the already-tested-and-rejected shrinkage-constant lever (MLB-RSCH-0012/13).
- **S2**: identical formula, but each opponent's own "quality" input is its
  S1-adjusted rate rather than raw — a bounded two-hop extension.
- **S3** (component-batting schedule adjustment): **not run**, per its own
  preregistered instruction ("if S3 substantially expands researcher
  freedom, do not run it") — under this milestone's time budget, building a
  second independent PIT-safe component-rate schedule-adjustment layer on
  top of an already-substantial new capability was judged excessive scope.

## 5. Real DEV/VAL results

| Candidate | DEV MAE delta (95% CI) | VAL MAE delta (95% CI) | DEV NB primary delta | VAL NB primary delta | Team robustness (dev) | Passes? |
|---|---|---|---|---|---|---|
| S1 (one-hop) | **-0.006327** [-0.0086, -0.0041] | **-0.006497** [-0.0087, -0.0043] | **+0.000969** | +0.000282 | 22/30 improved | **NO** |
| S2 (two-hop) | **-0.002691** [-0.0039, -0.0015] | **-0.005533** [-0.0079, -0.0033] | **+0.000175** | +0.000239 | 22/30 improved | **NO** |

Both candidates produce a **real, statistically significant, broadly-based**
improvement in mean-run accuracy (CI entirely on the "improved" side, 22/30
teams improved — the most consistent team-level robustness of any candidate
tested across this research program to date, versus 13-17/30 typical for
prior candidates). S1's DEV season-band breakdown shows improvement across
every populated band (games_16_40: -0.0040, games_41_80: -0.0054,
games_81_plus: -0.0073) — genuinely broad, not confined to one range.

**However, both candidates make DEV frozen-NB probability scoring
measurably worse**, consistently across every market family (S1's DEV
per-family deltas: game_total +0.0013, moneyline +0.0003, run_margin
+0.0002, team_total_away +0.0008, team_total_home +0.0008 — small but
uniformly in the "worse" direction, not concentrated in one family). This
is the mission's own explicitly preregistered gate #2 ("improves/preserves
DEV probability scoring") — and it is not met.

**This is a genuinely important, nuanced result**: a real, well-powered,
broadly-consistent improvement in point-accuracy (MAE) does not translate
into an improvement in probability-scoring quality under the frozen
MLB-RSCH-0010 NB distribution. A plausible explanation: the frozen NB
dispersion parameter was fit against the ERROR CHARACTERISTICS of S0's own
unadjusted mean; a mean construction with a different error-distribution
shape (even one with lower average absolute error) may interact worse with
a dispersion parameter calibrated for a different error profile. This
milestone deliberately holds NB dispersion frozen (per its own isolation
requirement) rather than exploring that interaction further — a legitimate
follow-up question for a future, separately preregistered milestone.

**Selection: neither S1 nor S2 passes.** Frozen winner: **S0 (control)**.

### Note on the added tie-break logic

Both S1 and S2 independently passed the DEV/VAL MAE-and-band criteria before
the DEV probability gate was added mid-session (see commit history) — since
this created a genuine "multiple candidates pass" scenario the original
`selection_passes()` did not specify a tie-break for, one was added
**before any holdout access occurred anywhere in this run**: prefer the
largest-magnitude DEV MAE improvement (which also favors the simpler
one-hop construction). Once the missing DEV probability gate was correctly
added (completing the mission's own already-stated four-criterion spec,
not a reaction to a favorable result), neither candidate reaches that
scenario — both are rejected outright on gate #2, and the tie-break was
never actually exercised in the final run. Documented here for full
transparency about the experiment's own development history.

## 6. Locked 2026 holdout

**Not evaluated** — neither candidate passed DEV/VAL selection, so per the
preregistered design, holdout is never unlocked for either (only a winning
candidate is ever evaluated on 2026; S0-vs-S0 is trivially zero).

## 7. Early-season diagnostic — known limitation discovered

The `MIN_PRIOR_GAMES_EARLY_DIAGNOSTIC=5` diagnostic path was preregistered
to report S1's own standalone accuracy on rows below the main 20-game floor.
It returned **zero observations** — not because no signal exists, but
because of a genuine implementation limitation discovered during this run:
`dev_rows` is built via `rsch0009.build_season_rows()`, which itself calls
`team_baseline()` with the DEFAULT `min_prior_games=MIN_PRIOR_GAMES_FOR_BASELINE=20`
internally — rows for games with either team below 20 prior games are
**never added to the row list in the first place**, upstream of this
experiment's own eligibility check. Lowering the floor when attaching
predictions to an already-20-floor-filtered row list has no effect, since
the early rows were never present to begin with. This is an honest, harmless
non-result (zero fabricated observations), not a wrong finding — but it
means this milestone's early-season question remains genuinely open. A
proper test requires building a dedicated low-floor row-construction pass
(not reusing `build_season_rows()` as-is), which is recommended as a
targeted follow-up rather than attempted under this session's remaining
time budget.

## 8. Robustness

Team-level: 22/30 teams improved under S1 (dev), 8/30 worse — the most
consistent effect direction across teams of any candidate tested in this
research program to date. Season-band: broad, not confined to one range
(section 5). Leave-one-team-out and per-team deltas are recorded in the
committed JSON artifact for full auditability.

## 9. Pinnacle secondary check

Since S0 is the frozen winner (no schedule adjustment adopted), the gap is
unchanged from prior milestones: **ML 0.008149**, **game total 0.006019**
(834 matched rows, existing cache, zero new Odds API spend).

## 10. Production mapping (read-only; no production code changed)

Production's `scripts/enrich_data.py::compute_offense_baseline` **already
has** an opponent-quality adjustment (`oppQualityAdj`, described per
MLB-RSCH-0012's own prior mapping as a rolling-window opponent xFIP-based
adjustment) — but it is applied differently: production adjusts the FINAL
prediction for the SPECIFIC upcoming opponent (forward-looking, per-matchup),
while this experiment adjusts the INPUT rate for opponents already faced
(backward-looking, a correction to the season-to-date rate itself, PIT-safe
using the actual schedule played). Classification: **PARTIALLY_INFORMS** —
both concepts are "adjust for opponent quality" but at different points in
the pipeline; a genuinely apples-to-apples comparison would require reading
production's exact `oppQualityAdj` formula in full detail, out of scope for
this read-only mapping.

## 11. Tests

- `tests/edgelab/test_run_opponent_strength_experiment_script.py` — 30
  tests: preregistration idempotency, raw-baseline-lookup PIT-safety proofs
  (first game has zero prior games, later games reflect only strictly-prior
  games), schedule-adjustment correctness (credits offense against strong
  pitching, excludes opponents below the minimum, output shape composable
  as the next level's own input), no-market-data-in-construction proofs,
  all four selection-rule gates independently tested, holdout-inaccessible-
  during-selection AST-verified ordering, NB-cell determinism/valid-range/
  run-margin-family checks, early-season-diagnostic-never-drives-selection
  proof, production-mapping read-only proof.
- Full `tests/edgelab/` suite: see PR for current pass count.
- Verified zero diff against every production file.
- Frozen NB dispersion verified byte-exact against the canonical
  MLB-RSCH-0010 artifact at import time, never refit.

## 12. Final questions and classification

**A. Does schedule/opponent quality improve future scoring prediction?**
Yes, on point-accuracy (MAE) — a real, significant, broadly-based effect.
No, on downstream probability quality under the frozen NB distribution —
consistently, if modestly, worse across every market family.

**B. Does offensive opponent adjustment help?** Contributes to S1's overall
MAE gain (component-level breakdown not separately isolated in this pass —
a candidate for a future, more granular follow-up).

**C. Does defensive opponent adjustment help?** Same as B — contributes to
the combined S1 effect, not separately isolated here.

**D. Does iterative (two-hop) adjustment outperform simple (one-hop)?**
**No** — S1 (one-hop) shows a LARGER DEV MAE improvement (-0.0063 vs
-0.0027) and a cleaner season-band pattern than S2 (whose games_16_40 band
actually gets worse, +0.0071). The simpler construction wins.

**E. Is the effect larger early season?** Not determined — see section 7's
discovered limitation.

**F. Survives 2025?** Yes, on MAE (both candidates); the probability
degradation is present on VAL too, smaller in magnitude than DEV.

**G. Survives locked 2026?** Not evaluated — neither candidate reached
holdout.

**H. Which probabilities improve most?** None improve — all five families
move consistently in the "worse" direction on DEV for both candidates.

**I. Closes the historical Pinnacle gap further?** No — gap unchanged
(S0 retained).

**J. Justifies a prospective shadow candidate?** **NO.**

**Overall classification: NO MEANINGFUL IMPROVEMENT / CONTROL SUPERIOR**
(on the mission's own preregistered decision criteria, which require BOTH
mean-accuracy AND probability-scoring improvement). This is nonetheless the
single most scientifically interesting result in this research program to
date: a real, well-powered, broadly-consistent point-accuracy improvement
that does not survive the probability-scoring gate — a genuine, informative
tension worth a dedicated follow-up rather than a simple pass/fail verdict.

## 13. Recommended follow-up (not run tonight)

The MAE-vs-probability tension in section 5 is the highest-value open
thread this milestone surfaces: investigate whether the frozen MLB-RSCH-0010
NB dispersion, fit against S0's own error characteristics, is the right
distribution-layer companion for a schedule-adjusted mean with a different
error shape — a structurally different question from "should the mean
itself be schedule-adjusted," and one that requires its own careful
preregistration (this milestone deliberately held dispersion frozen per its
own isolation requirement). A dedicated low-floor row-construction pass to
finally resolve the early-season question (section 7) is the second
highest-value follow-up.
