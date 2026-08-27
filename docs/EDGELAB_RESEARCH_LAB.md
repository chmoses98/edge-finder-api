# EdgeLab Research Lab — Milestone 0A: Control & Experiment Contract

Status: **research infrastructure only.** This milestone does not
change, and cannot change, production model probabilities, projection
formulas, production feature calculations, recommendation qualification,
edge thresholds, confidence tiers, Bet Up To logic, Kalshi fee
calculations, bankroll logic, stake sizing, market eligibility,
market-family eligibility, live/recommended market selection, lineup
gates, production slate output, risk gates, authoritative betting-slate
behavior, production cron behavior, settlement behavior, or real-money
recommendation behavior. `productionBehaviorChanged` is hardcoded
`False` on every artifact this milestone produces and cannot be set to
anything else (see `lib/edgelab/experiment_report.py`).

No new baseball research question is answered in this milestone. No new
model variant is implemented. This is governance/contract
infrastructure for the *next* several hundred experiments, not an
experiment itself.

**Revision note:** a narrow post-review hardening pass closed four
governance gaps before approval — PIT compatibility (not just
existence), experiment/control/candidate registration consistency,
objective-validity gating on favorable dispositions, and a no-evidence-
self-upgrade rule. See §7, §9/§11, and §6 below for the specifics; no
architecture was redesigned and no production behavior changed.

---

## 1. What the Research Lab is

A governed way to run many controlled MLB model experiments against the
existing EdgeLab research corpus (see `docs/EDGELAB_RESEARCH_TRUSTWORTHINESS.md`,
`docs/EDGELAB_PROSPECTIVE_MODEL_SNAPSHOTS.md`) without ever letting a
favorable result silently become production behavior. Every experiment
declares, up front and immutably:

1. what "Control" means (`lib/edgelab/control_identity.py`)
2. what "Candidate" means, if any (`lib/edgelab/candidate_identity.py`)
3. a registered hypothesis and research question (`lib/edgelab/experiment_registry.py`)
4. an honest evidence level (`lib/edgelab/evidence_levels.py`)
5. point-in-time provenance for every input it depends on (`lib/edgelab/pit_provenance.py`)
6. deterministic eligibility rules and a chronological split policy (reusing `lib/edgelab/research_splits.py`)
7. that Control and Candidate are evaluated on identical eligible observations (`lib/edgelab/paired_evaluation.py`)
8. explicit sample-size accounting (raw rows vs. independent games/dates/players)
9. explicit missingness and limitations
10. predefined primary/secondary metrics
11. a disposition from a fixed, non-production-capable vocabulary (`lib/edgelab/dispositions.py`)
12. `productionBehaviorChanged: false`, always (`lib/edgelab/experiment_report.py`)

## 2. What the Research Lab is NOT

- **Not a second EdgeLab.** It reuses `research_dataset.py` (the
  canonical opportunity-row builder), `research_stats.py` (game-clustered
  bootstrap, Brier/log-loss reuse), `research_splits.py` (chronological
  DEVELOPMENT/VALIDATION/HOLDOUT), `kalshi_fees.py` (the one fee engine),
  `market_family_mapping.py`, `checkpoints.py`, and `temporal_alignment.py`
  unchanged. See §15 "What existing infrastructure was reused" below.
- **Not a production-promotion mechanism.** Nothing in this milestone
  can write `disposition: PRODUCTION` — see §11.
- **Not a historical-ROI optimizer.** Proper scoring rules (Brier score,
  log loss, calibration error) are the default primary metric for a
  probability-model experiment; ROI/P&L are always reported as
  supplementary (`lib/edgelab/paired_evaluation.py`'s
  `evaluate_market_economics_pair`, whose result always carries an
  explicit `warning` field saying so).
- **Not the first substantive experiment.** The first planned research
  question ("Does declared edge from the current model monotonically
  correspond to genuine incremental predictive advantage beyond
  executable Kalshi prices, by market family?") is explicitly deferred
  to a future milestone, after this foundation is reviewed.

## 3. Control identity

`lib/edgelab/control_identity.py`. A control registration resolves to:
`controlModelId` (deterministic, `lib/edgelab/research_lab_ids.py`),
`sourceGitCommitSha`, `modelConfigVersion`, `configFingerprint` (a
content hash — never a hash of a file path), `probabilityAdapterIdentity`,
`modelEngineFamily`, `registeredAt`, `requiredInputProvenance` (a list of
`pit_provenance` manifest keys, validated eagerly), `identityConfidence`,
and a human-readable `description`.

**Why `identityConfidence` exists.** Historical `ModelEvaluation`
metadata already carries `modelCommitSha`/`modelConfigVersion`/
`pipelineRunId`/`modelSource`/`artifactSource`, but `modelVersion` is
null for every real record — no script anywhere captures a
model-*algorithm* version (`docs/EDGELAB_EVALUATION_METADATA.md` §2/10).
A control registration created *at the time* a control is defined can
honestly claim `IDENTITY_EXACT`. A control registration reconstructing
identity for **past** records must use `IDENTITY_HISTORICAL_AMBIGUOUS`
(commit/config known, algorithm identity not independently provable) or
`IDENTITY_HISTORICAL_UNKNOWN` (even commit/config is null in the source
records) — never `IDENTITY_EXACT`. This milestone does not retroactively
assign a false precision to historical data.

Control registration is **write-once**: re-registering the same
`controlModelId` with different content raises rather than silently
overwriting — an experiment that already cites a control id is never
retroactively repointed at different code.

## 4. Candidate identity

`lib/edgelab/candidate_identity.py`. A candidate variant declares a
`name`, the `baseControlModelId` it varies from, a `changeDescription`,
a `changeType` (`FEATURE_ADDITION`/`FEATURE_REMOVAL`/
`DISTRIBUTION_CHANGE`/`PARAMETER_CHANGE`/`OTHER`), and an
`implementationRef` (a module/function path, or the literal string
`"NOT_YET_IMPLEMENTED"` for a 0A-style placeholder that only reserves
identity). `productionCodePathsModified` is **always `[]`** — the
registration contract has no parameter capable of setting it otherwise,
and `validate_candidate_registration` raises if it is ever non-empty.
A real production code change is not a candidate variant; it is a
production change and belongs to a completely separate human review
process. This milestone implements **zero** new baseball variants (per
spec) — the contract exists for future experiments to use.

## 5. Evidence levels

`lib/edgelab/evidence_levels.py`. Six levels, E0 (weakest) through E5
(strongest):

| Level | Name | Meaning |
|---|---|---|
| E0 | DESCRIPTIVE | Historical descriptive analysis only. No causal/replay claim. |
| E1 | RECONSTRUCTED_RETROSPECTIVE | Inputs reconstructed after the fact; true PIT availability not fully proven. |
| E2 | PIT_HISTORICAL | Every input demonstrably available as of the checkpoint time. |
| E3 | WALK_FORWARD_HOLDOUT | Chronological out-of-sample / locked holdout, PIT-safe inputs. |
| E4 | PROSPECTIVE_SHADOW | Captured before event start; no production betting activation. |
| E5 | REAL_MONEY_EXECUTION | An actual, user-confirmed wager / real-money outcome. |

**Structural rules, not just documentation:**
- E0/E1 are never `is_promotable()` — a report can never carry
  `disposition=SHADOW_CANDIDATE`/`PROMOTION_CANDIDATE` at E0/E1
  (`experiment_report.validate_experiment_report` enforces this).
- `SHADOW_CANDIDATE` requires evidence ≥ E3 (`MIN_EVIDENCE_LEVEL_FOR_SHADOW_CANDIDATE`).
- `PROMOTION_CANDIDATE` requires evidence ≥ E4 (`MIN_EVIDENCE_LEVEL_FOR_PROMOTION_CANDIDATE`).

**Reconciliation with existing repo concepts** (spec: reconcile, don't
duplicate) — see `evidence_levels.py`'s module docstring for the full
writeup; condensed:

| Existing concept | Axis it actually measures | Why it is not reused as this ladder |
|---|---|---|
| `lib.edgelab.replay`'s `ELIGIBLE_LEVEL_2`/`ELIGIBLE_LEVEL_1_ONLY` | Replay-INPUT-completeness | A candidate-model replay at Level 2 is at best E2 evidence for the *control's own historical behavior* — it says nothing about a new candidate's real predictive value. |
| `ModelEvaluation.sourceCaptureType` (`PROSPECTIVE_LIVE`/etc.) | Which capture pathway produced a row | Necessary but not sufficient for E4 — a prospective row can still reuse day-old persisted inputs for most fields (`inputFreshnessNote`); each experiment must audit its *own* inputs via `pit_provenance`, never inherit E4 from the pathway alone. |
| `ModelEvaluation.qualityTier` (`TRUSTED_PRODUCTION`/etc.) | Production-pipeline membership | Orthogonal to evidence level entirely — a `TRUSTED_PRODUCTION` market's historical rows are still only E1/E2 evidence for a *new* research question about them. |

## 6. Experiment registration

`lib/edgelab/experiment_registry.py`. Stable IDs (`MLB-RSCH-0001`, ...),
deterministic given the current registry directory state (never
wall-clock/random). An experiment definition is a single JSON file at
`data/edgelab/experiments/<experimentId>.json` and is **write-once** —
a confirmatory experiment's eligibility/holdout/primary-metric can never
be quietly edited after seeing results. Only a *disposition* is allowed
to evolve over an experiment's life, and that lives on separately
accumulating `ExperimentReport` records (§9), never on the registration.

Required fields (spec section "EXPERIMENT REGISTRY", verbatim list):
`experimentId`, `title`, `hypothesis`, `researchQuestion`, `registeredAt`,
`owner`, `controlModelId`, `candidateModelId`/`candidateVariantId`,
`evidenceLevel`, `targetPopulation`, `marketFamilies`,
`eligibilityCriteria`, `exclusionCriteria`, `predictionCheckpoints`,
`primaryMetric`, `secondaryMetrics`, `expectedDirection`,
`chronologicalSplitPolicy`, `minimumSampleRequirement`, `clusteringUnit`,
`permittedParameterVariants`, `experimentType`, `falseDiscoveryHandling`,
`pitRequirements`, `status`, `notes`.

Registering an experiment requires the `controlModelId` to already be a
registered control (`control_identity_is_registered`), and every
`pitRequirements` entry to be both a known manifest input AND
role/evidence-level **compatible** (`pit_provenance.validate_pit_requirements`
— see §7) — an experiment can never register against an undocumented,
unaudited, *or* PIT-incompatible input silently. `candidateModelId` and
`candidateVariantId` can never both be set on one experiment
(`_validate_candidate_identifiers`) — there is no defined meaning for a
single experiment carrying two distinct candidate identities at once.

`minimumSampleRequirement` accepts either a plain positive number
(interpreted as a minimum `independentGames` count) or a dict with one
or both of `{"independentGames", "independentDates"}` → positive number
— deliberately not a general sample-size DSL, just the two dimensions
the spec asks for.

## 7. Point-in-time (PIT) provenance

`lib/edgelab/pit_provenance.py`. Answers "could this feature/data source
legitimately have been known at checkpoint T?" for the canonical inputs
the spec names. Six-value status vocabulary:
`OBSERVED_AT_DECISION_TIME`, `RECONSTRUCTABLE_FROM_DATED_RAW`,
`RETROSPECTIVELY_RECONSTRUCTED_WITH_LIMITATIONS`, `PROSPECTIVE_ONLY`,
`UNAVAILABLE_HISTORICALLY`, `UNKNOWN_REQUIRES_AUDIT`.

This milestone does **not** audit every possible baseball feature (not
required by spec). It populates the manifest for the inputs explicitly
named, using only evidence already established elsewhere in this repo —
never a fresh guess:

| Input | Status | Basis |
|---|---|---|
| Archived Kalshi market observation | OBSERVED_AT_DECISION_TIME | Real, immutable, append-only capture archive. |
| Kalshi bid/ask/executable price | OBSERVED_AT_DECISION_TIME | Same archive; top-of-book only, no fill/depth data. |
| Kalshi closing market quote | OBSERVED_AT_DECISION_TIME (as an evaluation target); **never** as a predictive feature for any checkpoint before it | `select_closing_quote` is only knowable after the full series exists. |
| ModelEvaluation probability (pipeline-derived) | OBSERVED_AT_DECISION_TIME, only for rows the causal join actually selects | `temporal_alignment` — sparse historical coverage (264/75,280 rows, per `EDGELAB_RESEARCH_TRUSTWORTHINESS.md`). |
| ModelEvaluation probability (prospective snapshot) | PROSPECTIVE_ONLY | Genuinely live only for the lineup-refreshed fields; other fields reuse the day's single earlier pipeline fetch. |
| Settlement outcome | UNAVAILABLE_HISTORICALLY (as a feature — a leakage guard entry, never a valid predictive input) | By construction, known only post-resolution. |
| Lineup status (official) | PROSPECTIVE_ONLY | Live-poll-safe only at the `LINEUP_CONFIRMATION` checkpoint itself. |
| Sharp sportsbook observation | **UNKNOWN_REQUIRES_AUDIT** | No archived, dated capture found during this milestone's audit — fetched live, not persisted per-date. |
| Weather input | UNAVAILABLE_HISTORICALLY | Confirmed orphaned from the pipeline (`EDGELAB_EVALUATION_METADATA.md` §1) — never merged into any research-persisted record. |
| Season-to-date stats | **UNKNOWN_REQUIRES_AUDIT** | Plausibly reconstructable, but this milestone did not trace `scripts/enrich_data.py`'s date-bounding behavior field-by-field — a rolling stat recomputed "as of today" against a past game date is a real, unruled-out leakage risk. |
| Hitter / pitcher snapshot | **UNKNOWN_REQUIRES_AUDIT** | Not traced this milestone. |

`assert_known_inputs()` raises for any input not in the manifest —
nothing can be silently treated as PIT-safe by omission.

**PIT compatibility, not just existence (hardening pass item 1).** Every
`pitRequirements` entry an experiment declares is now `{dataFamilyKey:
role}`, `role` one of three (`ROLE_PREDICTIVE_INPUT`,
`ROLE_EVALUATION_TARGET`, `ROLE_AUXILIARY_METADATA`) — a data source is
not equally trustworthy for every use it might be put to.
`validate_pit_requirements()` enforces:
- `UNAVAILABLE_HISTORICALLY` can never be a `PREDICTIVE_INPUT`, at any
  evidence level (no archive to draw a feature from at all).
- `UNKNOWN_REQUIRES_AUDIT` / `RETROSPECTIVELY_RECONSTRUCTED_WITH_LIMITATIONS`
  / `PROSPECTIVE_ONLY` can never support a `PREDICTIVE_INPUT` role at E2
  or E3 (`HISTORICAL_PIT_CLAIM_EVIDENCE_LEVELS`) — those are exactly the
  two levels that assert historical point-in-time safety. All three
  remain usable at E0/E1 (no PIT claim made) and E4/E5 (a live,
  forward-looking claim, not a historical one).
- `settlement_outcome` and `kalshi_closing_market_quote` have an
  `allowedRoles` of `{EVALUATION_TARGET}` only — a manifest entry can
  restrict which roles it may ever serve, regardless of evidence level;
  attempting to register either as a `PREDICTIVE_INPUT` always raises.

This check runs at registration time (`experiment_registry`) **and**
again at report time for any favorable disposition (§9/§11) — defense
in depth, so a future relaxation elsewhere can never silently let an
incompatible input through into a promotable result.

## 8. Paired Control-vs-Candidate evaluation

`lib/edgelab/paired_evaluation.py`. `pair_eligible_observations()` is
the **only** function that produces a paired row set — every metric
function operates exclusively on its output, never on the raw
`control_rows`/`candidate_rows`. A control-only or candidate-only
observation is always reported (`controlOnlyKeys`/`candidateOnlyKeys`),
never silently dropped. A duplicate key within one side (an ambiguous
pairing) is excluded from the pairing and reported separately
(`controlDuplicateKeys`/`candidateDuplicateKeys`) — never resolved by
"whichever the dict happened to keep."

`evaluate_probability_model_pair()` is the **primary** metric set for a
probability-model experiment: n rows, independent games, independent
dates, (optionally) player-games, Brier score, log loss, calibration
error (all reused from `research_stats.py`/`replay.py`, never
reimplemented), a paired delta (candidate-minus-control), and a
game-clustered bootstrap confidence interval on that delta (reusing
`research_stats.game_clustered_bootstrap_ci`, deterministic given a
seed — never a naive per-row interval).

`evaluate_market_economics_pair()` is explicitly **supplementary** —
its result always carries a `warning` field stating historical ROI must
never become the default optimization target. It reuses
`lib.edgelab.kalshi_fees` exclusively; no second fee formula exists
anywhere in this milestone.

## 9. Standard research report

`lib/edgelab/experiment_report.py`. `build_experiment_report()` is the
one function every future evaluation script should call. Fields (spec
section "STANDARD RESEARCH REPORT CONTRACT", verbatim):
`experimentId`, `controlModelId`, `candidateId`, `evidenceLevel`,
`experimentType`, registration/date-range metadata, `nRows`/
`nIndependentGames`/`nIndependentDates`/`nPlayers`, `missingDataSummary`,
`unpairedObservationSummary`, PIT status/limitations, `primaryMetric`/
`primaryResult`, `pairedDeltaVsControl`, `uncertainty`,
`secondaryMetrics`, `marketEconomicMetrics`, `falseDiscoveryTreatment`,
`minimumSampleRequirement`/`sampleRequirementMet`, `disposition`,
`methodologicalLimitations`/`leakageWarnings`/`overfittingWarnings`,
and `productionBehaviorChanged` (always `False`).

Reports **accumulate** (unlike registration) — a development-stage look
and a later confirmatory holdout look are both legitimate, permanently
retained, separate reports under `data/edgelab/experiment_reports/<experimentId>/`.

**Four hardening-pass gates, all enforced inside `build_experiment_report`/
`validate_experiment_report` (never optional, never bypassable via a
parameter):**

1. **Registration consistency** (`_validate_registration_consistency`).
   `control_registration.controlModelId` must equal
   `experiment.controlModelId`. If the experiment declares a
   `candidateVariantId`, a matching `candidate_registration` is
   *required*, its `candidateVariantId` must match, and its
   `baseControlModelId` must equal `experiment.controlModelId` — a
   candidate registered against a *different* control is rejected even
   if its id string happens to match. A control-only experiment (no
   `candidateVariantId`) must not receive an unexpected candidate
   registration either. Both registrations are also run through their
   own module's structural validator first, so an arbitrary/malformed
   dict can never stand in for a real registration.
2. **No evidence self-upgrade.** `evidence_level` (the report's own
   claim) can never exceed `experiment.evidenceLevel` (registered) —
   rank-compared via `lib.edgelab.evidence_levels`. A genuine E2 → E3 →
   E4 progression requires a **new experiment registration/stage**, never
   a report simply asserting a stronger level than what was
   preregistered. A *weaker* claim than registered remains allowed (an
   honest downgrade is not an upgrade).
3. **Favorable dispositions are gated on objective validity, not a
   "winner detector"** (`_validate_favorable_disposition_gates`).
   `SHADOW_CANDIDATE`/`PROMOTION_CANDIDATE` fail if: `sampleRequirementMet`
   is not `True`; `blockingLeakageWarnings` (distinct from the purely
   advisory `leakageWarnings`) is non-empty; the primary evaluation is
   empty (`nRows` is falsy, or either side's primary result is missing);
   or any declared `pitRequirements` entry is incompatible with the
   report's own `evidenceLevel` (§7's check, re-run here). `REJECT` and
   `RESEARCH_CANDIDATE` are **never** gated — both remain valid at any
   evidence level, any sample size, any leakage-warning state. This is
   deliberately *not* an automatic "this candidate wins" detector —
   whether a real metric improvement is good enough is still a
   human/research-review judgment call.

## 10. Exploratory vs. confirmatory

`experiment_registry.EXPERIMENT_TYPE_EXPLORATORY` /
`EXPERIMENT_TYPE_CONFIRMATORY`, each experiment declares one, plus a
`falseDiscoveryHandling` value (`NONE_SINGLE_HYPOTHESIS`,
`BENJAMINI_HOCHBERG`, `BONFERRONI`, `OTHER_DOCUMENTED`). An
`EXPLORATORY` experiment (which may screen many hypotheses/segments)
must not declare `NONE_SINGLE_HYPOTHESIS` — `build_experiment_definition`
raises if it does. A `CONFIRMATORY` experiment (fixed candidate, fixed
primary endpoint, fixed eligibility, fixed holdout, no post-hoc
threshold hunting) may use `NONE_SINGLE_HYPOTHESIS` when it genuinely
tests one pre-registered hypothesis.

## 11. Dispositions and the production-promotion firewall

`lib/edgelab/dispositions.py`. `REJECT`, `RESEARCH_CANDIDATE`,
`SHADOW_CANDIDATE`, `PROMOTION_CANDIDATE`, `PRODUCTION`.

**`PRODUCTION` exists only as a documented name.** No function anywhere
in this milestone can assign it:
- `assign_disposition(PRODUCTION)` always raises
  `ProductionDispositionForbiddenError` — no override parameter exists
  anywhere in its signature (proven by
  `test_assign_disposition_has_no_override_parameter`).
- `experiment_report.build_experiment_report()` calls
  `assign_disposition()` internally — a caller cannot bypass it.
- `experiment_report.validate_experiment_report()` independently
  re-checks `disposition != PRODUCTION` and `productionBehaviorChanged
  is False` before accepting any report for write.
- If a real production-promotion process is ever built, it must live
  **entirely outside this milestone's code**, reading a
  `PROMOTION_CANDIDATE` report as its own separate input.

## 12. Chronological splits (reused, not duplicated)

`lib/edgelab/research_splits.py` already implements exactly what the
spec asks for: deterministic, strictly by game date (never by
individual contract — same-date/same-game contracts are correlated),
default 60/20/20 DEVELOPMENT/VALIDATION/HOLDOUT, an explicit
`FRAMEWORK_ONLY_INSUFFICIENT_DATES` maturity flag below 30 distinct
dates, no silent fallback to a random split. This milestone reuses it
unchanged — an experiment's `chronologicalSplitPolicy` field names the
policy (e.g. `"DEVELOPMENT_60_VALIDATION_20_HOLDOUT_20"`) rather than
re-implementing the mechanism.

## 13. Reproducibility

Every persisted artifact (control, candidate, experiment, report) is
deterministic JSON (`sort_keys=True`), write-once (registration) or
content-addressed (reports include `generatedAt` in their id
deliberately, so distinct evaluation runs over time are distinct
records, not overwrites). `evaluate_probability_model_pair`'s bootstrap
CI takes an explicit `seed` (default `research_stats.DEFAULT_BOOTSTRAP_SEED`,
itself a fixed constant, never wall-clock-derived) — the same rows and
seed always produce the same interval. No function in this milestone
enumerates a filesystem directory and relies on its order for anything
except `next_experiment_id()`'s highest-sequence scan, which is itself
deterministic given the directory's *content* (not its enumeration
order — the max is taken over parsed sequence numbers, not first-seen
order).

## 14. How a future researcher creates a new experiment

```python
from lib.edgelab import control_identity as ci, experiment_registry as reg
from lib.edgelab import evidence_levels as ev, pit_provenance as pit

control = ci.build_control_registration(
    name="rules_v1_11_market", source_git_commit_sha="<git sha>",
    model_config_version="1.0", config_fingerprint="<hash of config/rules.json>",
    probability_adapter_identity="lib.kalshi_probability_adapters.adapt_contract",
    model_engine_family="rules_based_v1",
    required_input_provenance=["archived_kalshi_market_observation", "model_evaluation_probability_pipeline_derived"],
    identity_confidence=ci.IDENTITY_EXACT,
    description="Current production 11-market rules-based model.",
)
ci.register_control(control)

definition = reg.build_experiment_definition(
    title="...", hypothesis="...", research_question="...", owner="you",
    control_model_id=control["controlModelId"], evidence_level=ev.E2_PIT_HISTORICAL,
    target_population="...", market_families=[...], eligibility_criteria=[...],
    exclusion_criteria=[...], prediction_checkpoints=[...], primary_metric="brierScore",
    secondary_metrics=[...], chronological_split_policy="DEVELOPMENT_60_VALIDATION_20_HOLDOUT_20",
    minimum_sample_requirement={"independentGames": 100, "independentDates": 30}, clustering_unit="gameId",
    experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY, false_discovery_handling=reg.FDR_NONE_SINGLE_HYPOTHESIS,
    pit_requirements={
        "archived_kalshi_market_observation": pit.ROLE_PREDICTIVE_INPUT,
        "settlement_outcome": pit.ROLE_EVALUATION_TARGET,
    },
)
reg.register_experiment(definition)
```

Then build `control_rows`/`candidate_rows` (each a list of dicts with at
least `gameId`, `marketTicker`, `researchCheckpoint`, a probability
field, `outcome`, `gameDate`) — typically by filtering
`research_dataset.build_opportunity_rows()`'s output — and:

```python
from lib.edgelab import paired_evaluation as pe, experiment_report as er, dispositions as disp

pairing = pe.pair_eligible_observations(control_rows, candidate_rows)
evaluation = pe.evaluate_probability_model_pair(pairing, seed=20260813)
report = er.build_experiment_report(
    experiment=definition, control_registration=control, candidate_registration=None,
    pairing_result=pairing, probability_evaluation=evaluation,
    disposition=disp.RESEARCH_CANDIDATE, evidence_level=ev.E2_PIT_HISTORICAL,
)
er.write_experiment_report(report)
```

## 15. Worked example (synthetic data only)

See `tests/edgelab/test_experiment_report.py`'s `_setup()` fixture for a
complete, runnable, synthetic-data worked example exercising every layer
of this contract end to end (control registration → candidate
registration → experiment registration → pairing → probability
evaluation → report build → write/list). No historical research result
is presented as part of this milestone — every number in the test suite
is synthetic, deterministic fixture data, never a real baseball
dataset's output being smuggled in as a finding.

## 16. What existing EdgeLab infrastructure was reused (not duplicated)

| Need | Existing module reused |
|---|---|
| Full-universe opportunity dataset | `lib.edgelab.research_dataset` |
| No-look-ahead ModelEvaluation↔checkpoint join | `lib.edgelab.temporal_alignment` |
| Chronological DEVELOPMENT/VALIDATION/HOLDOUT split | `lib.edgelab.research_splits` |
| Brier score / log loss | `lib.edgelab.replay.brier_score`/`log_loss` |
| Game-clustered bootstrap CI, calibration error, sample-size status | `lib.edgelab.research_stats` |
| Fee-aware execution economics | `lib.edgelab.kalshi_fees` |
| Canonical market-family vocabulary | `lib.edgelab.market_family_mapping` |
| Checkpoint classification / closing-quote selection | `lib.edgelab.checkpoints` |
| Deterministic ID hashing convention | Mirrored (not imported — see `lib.edgelab.research_lab_ids`'s isolation rationale) from `lib.edgelab.ids` |

## 17. Production-safety protections

- Every new module lives under `lib/edgelab/`, imports no
  production-decision module (`scripts.build_market_ledger`,
  `scripts.risk_gate`, `lib.edgelab.bets`, `lib.edgelab.bankroll`,
  `lib.edgelab.recommendations`, ...), and writes only under its own
  `data/edgelab/{control_models,candidate_variants,experiments,experiment_reports}/`
  subdirectories — structurally verified by
  `tests/edgelab/test_research_lab_production_safety.py`, not merely by
  the absence of a counterexample today.
- `git diff` against `main` for this PR touches **zero** existing files
  — every change is a new file under `lib/edgelab/`, `tests/edgelab/`,
  or `docs/`.
- The `PRODUCTION` disposition firewall (§11) is enforced at multiple
  independent choke points (`assign_disposition`,
  `validate_experiment_report`, `validate_experiment_definition`'s
  `status` field), not just documented.
