# MLB-RSCH-0011: Prospective Negative-Binomial Shadow

Status: **INFRASTRUCTURE COMPLETE, SHADOW STARTED, NO E4 EVIDENCE YET**
Classification: `SHADOW_STARTED_NO_EVIDENCE_YET`
Disposition carried forward from MLB-RSCH-0010: `SHADOW_CANDIDATE` (unchanged by this milestone -- this milestone tests, it does not promote)

RESEARCH ONLY. No production probability, recommendation, edge, confidence,
Bet Up To, stake, bankroll, market eligibility, or slate output changed.

## 1. Purpose

MLB-RSCH-0010 found that a frozen negative-binomial run-scoring distribution
(dispersion = 0.281513) beats independent Poisson on a large 2022-2026
historical corpus, using MLB-RSCH-0009's *research proxy* mean model
(`{offense, bullpen}`). That is a real result, but it leaves one question
open: does the improvement survive contact with **production's actual,
currently-live mean model** -- the real `compute_game_projection_context`
output, not the research reconstruction? A distribution improvement proven
only against a proxy mean could evaporate (or reverse) against the real
one if the two mean models have different error structure.

MLB-RSCH-0011 is the transfer test. Design: for every game/checkpoint,
CONTROL and CANDIDATE receive **identical** `awayProjRuns`/`homeProjRuns`
(production's own, unmodified). Only the run-scoring distribution differs
-- Poisson vs. the frozen MLB-RSCH-0010 negative-binomial. This is a true
paired distribution ablation, never a mean-model change.

## 2. Registration

| | |
|---|---|
| Experiment ID | `MLB-RSCH-0011` |
| Control | `mlb_rsch_0011_production_poisson_control_v1` (production's real mean model + its existing Poisson layer -- **not** MLB-RSCH-0009/0010's proxy control) |
| Candidate | `mlb_rsch_0011_production_mean_plus_nb_0010_v1` (same means, MLB-RSCH-0010's frozen D1 negative-binomial) |
| Evidence level (this experiment's own claim) | `E4_PROSPECTIVE_SHADOW` |
| Frozen dispersion | `0.281513` -- verified byte-exact against `data/edgelab/analytics/latest_mlb_rsch_0010_run_distribution.json`'s `fittedParameters.overdispersion` at registration time (`test_frozen_dispersion_matches_canonical_mlb_rsch_0010_artifact`); no discrepancy found |
| Primary metric | mean Brier across `{game_total@7.5/8.5/9.5/10.5, team_total_away@2.5/3.5/4.5/5.5, team_total_home@2.5/3.5/4.5/5.5}`, candidate minus control, game-clustered 95% CI |
| Minimum sample for `PROMOTION_CANDIDATE`-track consideration | 300 independent games (E4 only -- see section 8) |
| Minimum sample for "early directional" reporting | 30 independent games (E4 only) |
| False-discovery handling | Benjamini-Hochberg (multiple market families examined) |
| PIT requirements | `model_evaluation_probability_prospective_snapshot` (PREDICTIVE_INPUT), `settlement_outcome` (EVALUATION_TARGET) |

Registration is write-once (`scripts/edgelab/run_mlb_rsch_0011_shadow.py::register_experiment`),
verified idempotent on a second run against the already-committed files.

## 3. Existing infrastructure inspected before building anything new

Before writing any new capture code, this milestone inspected (via two
dedicated read-only audits):

- `lib/edgelab/prospective_snapshot.py` + `scripts/edgelab/run_prospective_snapshots.py`
  -- the genuine, running 15-minute-cron intraday re-evaluation of production's
  own `evaluate_game`/`compute_game_projection_context`, at 5 checkpoints
  (`T_MINUS_90/60/30`, `LINEUP_CONFIRMATION`, `MODEL_CLOSING_WINDOW`).
- `lib/edgelab/snapshot.py`'s once-daily `PRE_GAME_DECISION` snapshot, whose
  `raw_projections.json.gz` component freezes that day's real
  `data/pipeline/<date>/projections.json` -- the exact production means.
- `lib/edgelab/model_evaluation.py`'s `ModelEvaluation` schema -- confirmed to
  have **no field for raw expected runs**, only the derived
  `modelFairProbability`, and `additionalProperties: false`.
- **Finding: no genuine `E4_PROSPECTIVE_SHADOW` evidence or `SHADOW_CANDIDATE`/
  `PROMOTION_CANDIDATE` disposition has ever existed in this repository.**
  MLB-RSCH-0011 is the first.

Decision: rather than extend the `ModelEvaluation` schema (risking the
production-facing schema/pipeline `build_model_evaluation_records_for_games`
also uses) or build a third parallel workflow, this milestone (a) extends
`lib/edgelab/prospective_snapshot.py`'s existing cycle minimally and
additively, and (b) persists shadow records to a **new, separate** entity
(`data/edgelab/mlb_rsch_0011_shadow_evaluations/<date>.jsonl`) that the
production `ModelEvaluation` schema/readers never see.

## 4. Capture mechanism

`lib/edgelab/prospective_snapshot.py::run_prospective_snapshot_cycle` now
returns a third value, `evaluated_snapshots` -- one
`{"gameId", "checkpoint", "game"}` entry per game the cycle actually
evaluated this run, where `"game"` is the exact object
`evaluate_game`/`compute_game_projection_context` were called against
(lineup-refreshed copy for `LINEUP_CONFIRMATION`, unchanged otherwise).
This is purely additive -- `new_records`/`run_log` are byte-identical to
before.

`scripts/edgelab/run_prospective_snapshots.py::run_shadow_step`, called
**strictly after** the core `model_evaluations` write, feeds
`evaluated_snapshots` into `lib/edgelab/shadow_distribution.py`, which
independently recomputes `compute_game_projection_context(game)` (the
same pure production function, same object, guaranteed byte-identical
`awayProjRuns`/`homeProjRuns`) and derives paired
Poisson/negative-binomial probabilities for every supported cell.

### Fail-safe isolation

- Per-game: `shadow_distribution.build_shadow_records_for_snapshot_cycle`
  wraps each game in its own `try`/`except`; a bad projection produces one
  explicit `FAILED_ISOLATED` record (no fabricated fallback), never aborts
  the batch.
- Whole-step: `run_shadow_step` wraps the entire shadow call (including the
  module import itself) in `try`/`except`; any failure is logged to
  `stderr` and the function returns `(0, 0, <error string>)`, never raises.
- Ordering: the shadow step is only ever called with data the core cycle
  already produced -- nothing it does can retroactively change
  `new_records`/`run_log`, since those were already computed before it runs.
- Storage: a completely separate path
  (`data/edgelab/mlb_rsch_0011_shadow_evaluations/`), never
  `data/edgelab/model_evaluations/`.

### Production-equivalence test (required by this milestone)

`tests/edgelab/test_run_prospective_snapshots_script.py::TestMlbRsch0011ProductionEquivalence`
runs `main()` end-to-end (real filesystem, `tmp_path`-isolated) twice --
once with the shadow step succeeding, once with it forced to raise -- and
asserts the resulting `data/edgelab/model_evaluations/<date>.jsonl`
content is identical (modulo the pre-existing random run-id suffix) in
both cases, and that this script's exit code is `0` in both cases. This is
the strongest available proof, short of a live production run, that the
shadow mechanism cannot affect production-facing output.

## 5. Families supported / unsupported

| | |
|---|---|
| Primary | `game_total` (4 lines), `team_total_away`/`team_total_home` (4 lines each) |
| Secondary | `moneyline`, `run_margin` (win/lose by 2+/3+) |
| **Never computed** | F3, F5, F7, NRFI, YRFI |

F3/F5/F7/NRFI/YRFI are structurally excluded from
`lib/edgelab/shadow_distribution.py` (not merely filtered) -- MLB-RSCH-0010's
dispersion parameter was fit exclusively on full-game team-run counts;
applying it to a shortened horizon or inning-level scoring without
separate research would violate this milestone's own "do not extrapolate"
instruction. Suspended production run lines (Rule 81) are never activated
by this milestone -- the `run_margin` family here is research-only, never
wired to any real-money or paper recommendation path.

## 6. Replay over existing PRE_GAME_DECISION snapshots

**Can existing prospective snapshots be replayed?** Two candidate sources
were checked:

- Per-checkpoint `ModelEvaluation` rows (`artifactSource=prospective_snapshot`,
  3,520 real rows) -- **no**, these never persisted the raw
  `awayProjRuns`/`homeProjRuns`, only the derived `modelFairProbability`.
  Cannot recompute an alternate distribution from a derived probability.
- Once-daily `PRE_GAME_DECISION` snapshots' `raw_projections.json.gz` --
  **yes**. This component is a frozen copy of that day's real
  `data/pipeline/<date>/projections.json`, captured genuinely
  prospectively (before the games), and contains the exact
  `awayProjRuns`/`homeProjRuns` production used.

### Provenance labeling (kept separate from this experiment's own E4 sample)

The replay's **means** were captured prospectively; the **candidate's own
probability** is computed now, by this script run, not at capture time.
Per this milestone's own instruction ("label evidence by true capture
provenance ... kept analytically separate from new E4 evidence"), the
replay is labeled `PRE_SHADOW_REPLAY_NOT_E4` throughout --
`scripts/edgelab/run_mlb_rsch_0011_shadow.py::run_replay` -- and is
**excluded** from `classify_shadow_evidence` and from
`minimumSampleRequirement`.

### Replay results (real, as of this milestone)

43 `PRE_GAME_DECISION` snapshots discovered (2026-07-30 .. 2026-08-27),
yielding **301 independent 2026 games** with usable projections, matched
against real settled final scores (reusing MLB-RSCH-0010's own
`build_rows_with_frozen_lambdas`' 2026-holdout row corpus for
`actualHomeRuns`/`actualAwayRuns`, unchanged, never re-derived).

| Family | n (cells) | Paired Brier delta (candidate − control) | 95% game-clustered CI |
|---|---:|---:|---|
| `game_total` | 1,204 | **-0.002935** | [-0.0054, -0.0005] |
| `team_total_away` | 1,204 | **-0.006182** | [-0.0105, -0.0018] |
| `team_total_home` | 1,204 | +0.002960 | [-0.0012, +0.0070] (not significant) |
| `moneyline` | 602 | **-0.003801** | [-0.0074, -0.0004] |
| `run_margin` | 1,204 | **-0.004231** | [-0.0069, -0.0017] |
| Overall (5 primary cells) | -- | **-0.002731** | -- |

Sample-size status: `CALIBRATED` (n ≥ 100, 301 independent games).

**Interpretation:** the candidate improves control on 4 of 5 families with
a confidence interval excluding zero; `team_total_home` alone is
directionally positive but not statistically distinguishable from zero at
this sample size. This is genuinely encouraging early evidence that
MLB-RSCH-0010's finding transfers to production's real mean model -- but
it is **not** E4 evidence, and does not by itself justify any promotion
consideration. It is reported as informative context for interpreting
the (currently empty) genuine E4 sample as it accumulates.

## 7. Current-slate smoke test

`run_current_slate_smoke_test()` (research only, never a wager
recommendation) ran against the live `data/slate.json` at the time of this
milestone (2026-08-27, 7 games), using the real production
`compute_game_projection_context`. Example:

| Game | Expected Runs (away/home/total) | Poisson P(total > 7.5) | NB P(total > 7.5) | Difference |
|---|---|---:|---:|---:|
| COL @ WSH | 4.962 / 4.287 / 9.249 | 0.7044 | 0.6045 | -0.0999 |
| COL @ WSH | -- P(total > 9.5) -- | 0.4454 | 0.4264 | -0.0189 |

(Full table for all 7 games in `data/edgelab/analytics/latest_mlb_rsch_0011_shadow_status.json::currentSlateSmokeTest`.)
The negative-binomial candidate systematically assigns **lower** probability
mass to moderate overs and (per MLB-RSCH-0010's own tail diagnostics)
**higher** mass to the extreme tails -- consistent with overdispersion
correcting Poisson's known under-weighting of blowouts/shutouts.

## 8. This experiment's own genuine E4 prospective sample

As of this milestone: **zero settled games** --
`data/edgelab/mlb_rsch_0011_shadow_evaluations/` did not exist before this
milestone; the capture code was only just wired into
`model-snapshot-scheduler.yml`'s existing cron path. `score_prospective_shadow()`
runs cleanly against zero records (`totalCapturedRecords=0`,
`independentGames=0`) -- this is the honest, expected state on day one,
not a bug.

**Automatic accumulation**: no further manual action is required. Every
future 15-minute `model-snapshot-scheduler.yml` cycle that evaluates a
real game now also (best-effort, fail-safe) writes a paired shadow record.
Re-running `scripts/edgelab/run_mlb_rsch_0011_shadow.py` at any later date
will automatically pick up and score whatever has settled since, with no
code change.

## 9. Classification

`classify_shadow_evidence(prospective_independent_games, prospective_primary_delta)`
is based **exclusively** on section 8's genuine E4 sample -- the section 6
replay never counts toward it, by design.

Current result: **`SHADOW_STARTED_NO_EVIDENCE_YET`** (zero E4 games settled).
This is not `PROMOTION_CANDIDATE` and must never be reported as such --
the preregistered 300-game minimum is far from met.

## 10. Kalshi / Pinnacle secondary research fields -- deferred this pass

The mission asks for archived Kalshi market fields (production Poisson
probability, NB shadow probability, Kalshi fair probability, executable
price, production declared edge, NB hypothetical edge) and a Pinnacle
comparison reusing the MLB-RSCH-0008/0009 matched sample. **Not
implemented in this pass** -- the capture infrastructure, replay, and
production-equivalence proof already represent the full required scope for
one milestone, and wiring a third join (against
`lib.edgelab.research_dataset.build_opportunity_rows`/the Pinnacle cache)
without rushing it risked exactly the kind of "mixed together" analysis
the mission explicitly warns against (candidate-vs-control accuracy,
candidate-vs-sharp-market, and candidate-edge-vs-executable-price must stay
three distinct questions). This is honestly reported as **deferred**, not
silently skipped -- a natural, well-scoped follow-up once real E4 sample
exists to make the Kalshi/Pinnacle join worth doing.

## 11. Production mapping (carried forward from MLB-RSCH-0010, unchanged)

No production code path is modified by this milestone.
`scripts/build_market_ledger.py`'s `poisson_pmf`/`p_team_wins`/`p_over_total`
remain byte-for-byte unchanged (proven by `TestMlbRsch0011ProductionEquivalence`
and by the simple fact of zero diff against those files). Sequence per
MLB-RSCH-0010's own doc: historical confirmation (MLB-RSCH-0010, done) →
current-model shadow probability comparison (this milestone's replay,
done) → prospective shadow (this milestone's capture mechanism, started)
→ a deliberate, separately-reviewed production-promotion PR (not this
milestone, not started).

## 12. Recommended next probability research question

Given MLB-RSCH-0009 (mean model: `{offense, bullpen}`, park rejected) and
MLB-RSCH-0010/0011 (distribution layer now reasonably well-calibrated and
transferring to production), the next highest-value gap is on the **mean
side** again -- specifically **starter quality**. The PIT-provenance audit
performed this milestone found production's actual starter-quality input
(`starter_quality_savant_season_aggregate`, feeding the F5 `STARTER_EDGE`/
`xERAGap` tagging) has **no per-date archive at all**
(`pitStatus=UNAVAILABLE_HISTORICALLY`) -- it cannot be backtested honestly
as currently fetched. A genuinely PIT-safe alternative already exists and
is proven no-lookahead by its own tests:
`lib.research.statcast_pitch_store.load_pitches_for_pitcher(pitcher_id, as_of, since)`
(`pitcher_statcast_raw_archive`, `RECONSTRUCTABLE_FROM_DATED_RAW`, E2-safe).

**Recommendation: MLB-RSCH-0012 -- "Starter Quality PIT-Safe Reconstruction
via Pitcher Statcast Raw Archive."** Build a recent-form starter-quality
feature (e.g. a rolling velocity/pitch-shape-derived proxy) from that
archive and test it as an addition/alternative to the current F5 starter
signal. **Caveat, stated up front rather than discovered mid-experiment:**
the archive is only ~15 gameDates deep as of this audit (2026-08-11 through
2026-08-25) -- this would necessarily start as a small-sample, early-stage
study, growing daily, not a large 2022-2026-scale confirmatory experiment
like MLB-RSCH-0009/0010. A secondary, immediately-larger-sample option:
extend `lib.edgelab.pit_reconstruction`'s already-proven, multi-season-deep
`team_recent_game_log_reconstruction` mechanism to build its own manifest
entry's explicitly-flagged-but-not-yet-built "coarse box-score-derived
team-offense proxy" and starter recent-workload/rest features -- same PIT
safety guarantee, no depth constraint. Not implemented in this PR (out of
scope, per this milestone's own instruction).

## 13. Tests

- `tests/edgelab/test_shadow_distribution.py` -- 18 tests (pure paired-probability computation, fail-safe isolation, no forbidden production imports).
- `tests/edgelab/test_prospective_snapshot.py` -- 50 tests (48 pre-existing + 2 new, covering the additive `evaluated_snapshots` return value).
- `tests/edgelab/test_run_prospective_snapshots_script.py` -- 21 tests (13 pre-existing + 8 new, including the production-equivalence end-to-end proof).
- `tests/edgelab/test_run_mlb_rsch_0011_shadow_script.py` -- 25 tests (registration ordering, outcome derivation, scoring, classification, replay discovery, real-archive integration smoke test).
- Full `tests/edgelab/` suite: **2,546 passed**, 0 failed.

## 14. Files

- `lib/edgelab/shadow_distribution.py` (new)
- `lib/edgelab/prospective_snapshot.py` (additive: `evaluated_snapshots` third return value)
- `scripts/edgelab/run_prospective_snapshots.py` (additive: `run_shadow_step`, called after the core write)
- `scripts/edgelab/run_mlb_rsch_0011_shadow.py` (new: registration, replay, E4 scoring, smoke test, report)
- `data/edgelab/control_models/CTRL-ab911285249ece27.json`, `data/edgelab/candidate_variants/CAND-67f0a63ef5398130.json`, `data/edgelab/experiments/MLB-RSCH-0011.json`, `data/edgelab/analytics/latest_mlb_rsch_0011_shadow_status.json` (real committed artifacts)
