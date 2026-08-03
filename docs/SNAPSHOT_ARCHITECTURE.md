# SNAPSHOT_ARCHITECTURE.md

Historical Capture Completeness and Immutable Snapshot Foundation
milestone. This document has been revised once already, after a
maintainer review of the first version of this milestone found several
real defects (documented inline below wherever they materially changed
the design) — see the "Maintainer review revisions" section (§12) for a
consolidated summary of what changed and why.

This is capture-and-reproducibility infrastructure only. It does not
change model probabilities, recommendation logic, thresholds, staking,
market selection, settlement outcomes, or any production handicapping
behavior — confirmed by `tests/edgelab/test_snapshot.py`'s
`TestNoProductionRecommendationChanges` (zero working-tree diff on every
core handicapping file this milestone touched, plus a direct determinism
check on the pure pricing functions it doesn't modify).

## 1. What is a Snapshot

A **Snapshot** is an immutable, write-once, hash-verifiable manifest of
everything the production model knew (or explicitly did *not* know) at
one point in time, for one MLB slate date and one **stage**
(`PRE_GAME_DECISION`, `POST_GAME_SETTLEMENT`, `CLOSING_LINE`).

It differs from **replay**: a Snapshot is a *capture* operation (write
once, no historical judgment involved beyond honestly recording what
exists); replay is a future *consumption* operation that reads one or
more Snapshots to answer a different question (was this well
calibrated? would a different model version have done better?). This
milestone builds only the capture side — see §9 for what a replay
engine consumes from here.

It differs from every existing artifact in this repository:
`data/pipeline/<date>/*.json` is six *separate* files with no manifest
tying them together or proving they're mutually consistent;
`data/edgelab/*/​<date>.jsonl` files are per-entity time series, not a
per-moment bundle; `data/weather.json`/`data/bullpen.json` are single
overwritten live files with **no history at all**. A Snapshot is the
first cross-entity, single-manifest, content-addressed bundle for one
production decision moment that this repository has ever had.

## 2. Why a manifest alone is insufficient for mutable data

A manifest that only references `bets.json`, `data/weather.json`, or any
other file that gets rewritten later is not an immutable snapshot — a
future reader following that reference would see *today's* content, not
what production actually saw on the snapshotted date. See §3 for the
exact rule this milestone uses to decide which sources need their bytes
physically duplicated at capture time (`FROZEN_COPY`) versus which could,
in principle, be safely referenced by path + hash
(`REFERENCED_IMMUTABLE`).

## 3. REFERENCED_IMMUTABLE vs FROZEN_COPY (revised after maintainer review)

**Original design** classified several sources as `REFERENCED_IMMUTABLE`
based on the belief that they were write-once. **A maintainer review
audited every such source against its actual writer code and found every
one of them is, in fact, mutable in some real scenario:**

| Source | Believed | Actually |
|---|---|---|
| `data/pipeline/<date>/*.json` | write-once per (stage, date) | `lib.pipeline_artifacts.write_stage_artifact()`'s own docstring admits "a rerun that calls this again for the same stage/date overwrites the artifact" — a same-day re-dispatch of `fetch-slate.yml` genuinely does this |
| `data/kalshi_registry_snapshots/kalshi_search_<date>.json` | dated, kept forever, never rewritten | `fetch-slate.yml`'s archive step does an unconditional `cp` — a second run for the same date overwrites it |
| `data/slates/<date>/authoritative.json` | write-once, protected | `scripts/protect_slate.py`'s own docstring says a `LINEUP_RECHECK` run legitimately **updates** `authoritative.json` for new games |
| `data/edgelab/<entity>/<date>.jsonl(.gz)` | append-only | `lib.edgelab.storage`'s `append_records()`/`upsert_records()` both **rewrite the whole file** on every call (read-existing, merge, atomic-write-all) — even the "append-only" entities' full file bytes change whenever a later capture adds rows for the same date, and upsertable entities can have existing rows replaced in place |

**Conclusion: this repository currently has no source that is safely
`REFERENCED_IMMUTABLE` by construction.** Every component with a real
source path is therefore `FROZEN_COPY` as of this revision. The
`REFERENCED_IMMUTABLE` mechanism (`build_referenced_component`) is kept
as a primitive for a future genuinely write-once-and-never-touched-again
source — it is directly unit-tested (hash-in-place, tamper detection,
pruned-source detection) even though no current stage builder uses it.

`lib/edgelab/snapshot.py`'s module docstring is the canonical statement
of this rule.

**FROZEN_COPY** (exact bytes physically copied into the snapshot's own
`frozen/` directory at capture time, gzip-compressed for everything
larger than a few KB — see §8) now covers every real-source component:
`PRODUCTION_SLATE_INPUT`, `NORMALIZED_SLATE`, `RAW_PROJECTIONS`,
`RECOMMENDATION_OUTPUT`, `MARKET_UNIVERSE`, `MARKET_OBSERVATIONS`,
`BULLPEN_STATE`, `WEATHER`, `EFFECTIVE_CONFIG`, `MODEL_EVALUATIONS`,
`RECOMMENDATIONS`, `RISK_GATE_OUTPUT`, `EXECUTION_SLIP`,
`VALIDATION_ARTIFACT`, `PROTECTION_ARTIFACT`, `SETTLEMENT`, `CLV`.
`EXECUTABLE_PRICES`, `BID_ASK`, `LINEUP_STATE`, and `PARK_FACTORS` are
denormalized pointers into an already-frozen component's bytes (same
underlying evidence — `MARKET_OBSERVATIONS`, `RECOMMENDATION_OUTPUT`, and
`EFFECTIVE_CONFIG` respectively) — never a second freeze/hash of the same
bytes.

Every `FROZEN_COPY` component's hash is still re-checked at
`verify_snapshot()` time against its **own frozen bytes** (proving the
snapshot itself wasn't tampered with after the fact) — freezing removes
the "was the live source rewritten" risk, not the "was my own archive
corrupted/tampered with" risk, which integrity verification still
guards against.

## 4. Snapshot identity / granularity (revised after maintainer review)

**Original design** keyed a Snapshot purely by `(stage, date)` — one
canonical slot per calendar date. **A maintainer review found this
insufficient**: a real production date can have more than one distinct
decision moment (an early slate vs. a later lineup-confirmed rerun, a
doubleheader, a retry after a partial failure). Under the original
design, a second legitimate run for the same date would either silently
overwrite the first valid decision snapshot, or (with write-once
protection in place) be misclassified as an anomalous "conflict" against
it — neither is correct.

**Revised design**: `PRE_GAME_DECISION` snapshots are keyed by
`(snapshotStage, snapshotDate, productionRunKey)`.
`productionRunKey` is derived from
`data/pipeline/<date>/recommendations.json`'s own `meta.createdAt` —
which genuinely changes every time `scripts/build_market_ledger.py`
actually reruns for that date. A second, legitimately different
production run gets its **own** snapshot slot
(`data/edgelab/snapshots/<date>/pre_game_decision/<runKeySlug>/`)
instead of conflicting with the earlier one. A genuine conflict (same
`productionRunKey`, different bytes — which should only happen if
capture itself is non-deterministic) is still caught and quarantined as
diagnostic evidence, never silently applied.

`POST_GAME_SETTLEMENT`/`CLOSING_LINE` remain date-keyed (not run-keyed):
they represent "the current best-known settled truth for this date," not
a decision moment, and are explicitly out of scope for the
look-ahead-bias concern this distinction exists to address. Both link
**backward** to every `PRE_GAME_DECISION` `snapshotId` that exists for
their date (`linkedSnapshotIds`), since there may now be more than one.

## 5. Snapshot contents (by stage)

| componentType | PRE_GAME_DECISION | POST_GAME_SETTLEMENT | CLOSING_LINE | storageMode |
|---|---|---|---|---|
| PRODUCTION_SLATE_INPUT | REQUIRED | — | — | frozen |
| RAW_PROJECTIONS | REQUIRED | — | — | frozen |
| RECOMMENDATION_OUTPUT | REQUIRED | — | — | frozen |
| MARKET_UNIVERSE | REQUIRED | — | — | frozen |
| EFFECTIVE_CONFIG | REQUIRED (always PARTIAL — see §7) | — | — | frozen (synthesized record) |
| RISK_GATE_OUTPUT | REQUIRED | — | — | frozen |
| NORMALIZED_SLATE, EXECUTABLE_PRICES, BID_ASK, LINEUP_STATE, BULLPEN_STATE, WEATHER, PARK_FACTORS, MODEL_EVALUATIONS, RECOMMENDATIONS, MARKET_OBSERVATIONS, EXECUTION_SLIP, VALIDATION_ARTIFACT, PROTECTION_ARTIFACT | NICE_TO_HAVE | — | — | frozen or denormalized pointer |
| SETTLEMENT | NOT_APPLICABLE_FOR_STAGE | REQUIRED | — | frozen |
| CLV | NOT_APPLICABLE_FOR_STAGE | REQUIRED | — | frozen |
| MARKET_OBSERVATIONS (closing quotes) | — | — | REQUIRED | frozen |

**Postgame data is never a PRE_GAME_DECISION component.** `SETTLEMENT`
and `CLV` are always emitted as explicit `NOT_APPLICABLE_FOR_STAGE`
entries with `limitationReason: POSTGAME_DATA_EXCLUDED_FROM_PREGAME_SNAPSHOT`
— never silently absent, and never merged into the pregame manifest.
This is the look-ahead-bias safeguard, enforced structurally (the
builder function for `PRE_GAME_DECISION` never even attempts to read
settlement/CLV sources) rather than by a filter applied after the fact.

**Never reconstructable historically** (confirmed absent, not merely
un-retained): umpire data (no source anywhere in the repository),
injury status (no schema field exists), and the real historical F5 Tie
executable price (production discarded it before the F5 Three-Way
Pricing Correction milestone). These are not attempted by this
milestone's capture logic at all.

## 6. Temporal / run consistency (new — maintainer review item 2)

A `PRE_GAME_DECISION` manifest's components are drawn from up to seven
separate `data/pipeline/<date>/*.json` artifacts (including
`provenance.json` as of the PR #37 maintainer review). Nothing in this
repo guarantees they were all written by the *same* production run (a
partial failure could leave a stale `validation.json` from an earlier
attempt sitting next to a fresh `recommendations.json`).
`lib.edgelab.snapshot.detect_temporal_skew()` compares every pipeline
artifact's own `meta.createdAt` against the `productionRunKey` reference
timestamp; if any artifact is more than `MAX_RUN_SKEW_HOURS` (1h — tightened
from the original 6h under the PR #37 review, item 4, since a real
fetch-slate.yml job completes in well under an hour; see
`lib/edgelab/snapshot.py`'s `MAX_RUN_SKEW_HOURS` comment for the full
rationale and the documented residual limitation) away,
`temporalConsistency.skewDetected` is set `true` and
`derive_completeness_status()` forces the result to at most
`PARTIAL_REPLAY` — a snapshot whose components may span two different
runs can never claim `COMPLETE_FOR_PRODUCTION_REPLAY`. This is a
timestamp-proximity heuristic, not a cryptographic run-identity guarantee
— two production runs less than an hour apart on the same date could
still mix components undetected; see the limitation note in
`lib/edgelab/snapshot.py`.

## 7. Effective production configuration (revised — always PARTIAL)

`config/rules.json` is **not** the complete production rule set — its
own reader (`lib/rules_config.py`) documents that the live betting/
pricing pipeline hardcodes some thresholds directly in code (recommendation
tiering, market eligibility gates, staking tables in
`scripts/risk_gate.py`/`scripts/build_market_ledger.py`), not via this
file. **Maintainer review finding**: the original design marked the
`EFFECTIVE_CONFIG` component `AVAILABLE`, which overstated what it
actually captures. It is now always marked `PARTIAL` (never `AVAILABLE`)
— this is a `REQUIRED` component, so per §8's rule table this alone caps
every `PRE_GAME_DECISION` manifest at `PARTIAL_REPLAY` today, honestly
reflecting that a complete effective-configuration extractor does not
yet exist.

`capture_effective_config()` remains the narrowest truthful mechanism
available: it freezes (a) `config/rules.json`'s own contents and
`_version` field verbatim, (b) the one real, live-importable versioned
constant that exists today (`F5_PRICING_VERSION_CURRENT` from
`scripts/build_market_ledger.py`, imported and read directly — never
re-derived from source text), and (c) whichever `rulesVersion` literal
that date's own `data/pipeline/<date>/execution.json` artifact already
recorded. Nothing here is fabricated or text-scraped from source; every
field is either read from a real file or read from a real, live code
object.

## 8. Completeness rule table (revised — maintainer review item 7)

`lib.edgelab.snapshot.derive_completeness_status()`, evaluated in order,
first match wins:

1. **`MISSING_REQUIRED_INPUT`** — any `REQUIRED` component is `MISSING`.
2. **`PARTIAL_REPLAY`** — any `REQUIRED` component is `PARTIAL` (this
   always includes `EFFECTIVE_CONFIG` today — see §7).
3. **`APPROXIMATE_ONLY`** — no `REQUIRED` gap, but some `NICE_TO_HAVE`
   component is `MISSING` or `PARTIAL`.
4. **`PARTIAL_REPLAY`** — every component `AVAILABLE`, but the
   production commit is ambiguous (`productionCommitSha` is `None` —
   always true today, see §9's commit-skew note) or components show
   workflow-run skew (§6) — a snapshot must never claim `COMPLETE` when
   it cannot prove internal consistency, even if every field is
   individually populated.
5. **`COMPLETE_FOR_PRODUCTION_REPLAY`** — otherwise.

`INTEGRITY_FAILURE` is **never** produced by the builder — a frozen
copy's hash failing to match its source immediately after copy raises
`SnapshotIntegrityError` and aborts the build entirely (nothing is ever
committed). `INTEGRITY_FAILURE` is exclusively a **read-time** verdict:
`completeness_report()` re-verifies a stored manifest and overrides the
reported `completenessStatus` to `INTEGRITY_FAILURE` if verification
fails *now*, regardless of what the manifest's own stored field says
(that field is a historical record of completeness *at capture time*).

Given §7's finding, **every `PRE_GAME_DECISION` manifest this repository
can produce today is capped at `PARTIAL_REPLAY` at best** — `COMPLETE_FOR_PRODUCTION_REPLAY`
is currently unreachable until a future milestone either closes the
effective-config gap or the commit-provenance gap. This is intentional
and honest, not a bug.

## 9. Storage design and retention (revised — everything frozen, gzip-compressed)

```
data/edgelab/snapshots/<date>/pre_game_decision/<productionRunKeySlug>/
  manifest.json
  frozen/
    production_slate_input.json.gz
    normalized_slate.json.gz
    raw_projections.json.gz
    recommendation_output.json.gz
    market_universe.json.gz
    market_observations.jsonl.gz        (already gzip at the source -- not double-compressed)
    model_evaluations.jsonl.gz
    recommendations_ledger.jsonl.gz
    risk_gate_output.json.gz
    execution_slip.json.gz
    validation_artifact.json.gz
    protection_artifact.json.gz
    bullpen.json                        (small -- left uncompressed)
    weather.json                        (small -- left uncompressed)
    effective_config.json               (small -- left uncompressed)
  conflicts/<UTC-timestamp>/             (only present if a genuine conflicting rerun occurred)
    candidate_manifest.json
    frozen/...

data/edgelab/snapshots/<date>/post_game_settlement/
  manifest.json
  frozen/settlement.jsonl.gz
  frozen/clv_quotes.jsonl.gz

data/edgelab/snapshots/<date>/closing_line/
  manifest.json
  frozen/market_observations.jsonl.gz

data/edgelab/snapshot_capture_status.json   (operational telemetry -- NOT part of any manifest; see §11)
```

**Compression (new — maintainer review item 9).** The original estimate
(~25 MB / 3 seasons) assumed most components stayed `REFERENCED_IMMUTABLE`
(near-zero marginal cost). Once every real-source component was
reclassified to `FROZEN_COPY` (§3), the true marginal cost of the
biggest JSON/JSONL artifacts (`recommendation_output.json`,
`market_universe.json`, `clv_quotes.jsonl`) dominated storage growth —
measured at **~27 MB across the 4 real backfilled days** before
compression (~3.6 GB/3-season projection). `freeze_file_component(...,
compress=True)` now gzips every frozen JSON/JSONL component above a
few KB, using the same deterministic (`mtime=0`) convention as
`lib.edgelab.storage`'s existing `MarketObservation` gzip writer —
`data/edgelab/schema_v1/snapshot_component.schema.json`'s `contentHash`
is the hash of the bytes actually at `snapshotPath` (compressed or not),
which is what `verify_snapshot()` re-checks; a separate immediate
decompress-and-compare against the source hash at freeze time (see
`_atomic_gzip_copy`) additionally proves compression didn't corrupt
anything.

**Recalculated storage estimate** (`scripts/snapshot_storage_report.py`,
same 4 real days, post-compression): **~5.76 MB observed marginal
storage → ~800 MB projected across all three stages over a 185-day
season × 3 seasons** (down from the pre-compression ~3.6 GB estimate —
roughly a 4.7× reduction). `CLV_quotes.jsonl` remains the single biggest
per-day contributor even after compression; worth monitoring as more
real days accumulate.

**Self-containment (item 9's other concern)**: since every real-source
`REQUIRED` component is now `FROZEN_COPY` (§3), a permanently-retained
manifest never depends on a component subject to retention pruning
(e.g. `data/kalshi_registry_snapshots`'s 21-day rolling window for
timestamped files) — the manifest's own `frozen/` directory is
self-contained regardless of what happens to the original mutable
source later. `tests/edgelab/test_snapshot.py::TestFrozenMutableComponent::test_required_components_never_depend_on_a_prunable_reference`
asserts this directly.

**Manifests** are plain, uncompressed, sorted-key JSON (small,
git-diffable) — kept forever, no retention policy needed for the
manifest layer itself.

## 10. Write-once / integrity guarantees

- **SHA-256** component hashes, computed over the exact bytes at
  `snapshotPath` (frozen, compressed or not) — never over a
  re-serialized form that could silently normalize away a real
  difference.
- **Deterministic canonical serialization** (`canonical_json_bytes`:
  sorted keys, no incidental whitespace) for every synthesized record
  (`EFFECTIVE_CONFIG`) and for the manifest-hash computation itself.
- **manifestHash** deliberately excludes `capturedAt`, `workflowRunId`,
  `snapshotWriterCommitSha`, and `provenance` — those are "when/how this
  was captured" facts that legitimately differ between two otherwise-
  identical captures of the same underlying data. **`productionRunId`
  IS included** (unlike the excluded fields above) — it identifies
  *which* production run's artifacts were frozen, which is part of
  *what* was captured, not incidental capture-time metadata.
- **Atomic writes**: every file this module writes (`manifest.json`,
  frozen copies) uses temp-file + `fsync` + `os.replace`.
- **Strict write-once refusal**: `_commit_snapshot()` never overwrites
  an existing `manifest.json` for the same `(stage, date, runKey)`. A
  rerun with byte-identical content is a **no-op** (verified, not
  rewritten, frozen bytes discarded from staging). A rerun whose content
  actually differs under the *same* identity key is a **conflict**: the
  existing manifest and its `frozen/` directory are left completely
  untouched; the candidate manifest and its staged frozen bytes are
  moved into `<snapshot_dir>/conflicts/<UTC timestamp>/` as diagnostic
  evidence.
- **Missing-component validation**: every assessed `componentType` gets
  an explicit entry, even when `MISSING`/`NOT_APPLICABLE_FOR_STAGE`.
- **Deterministic reruns**: proven by
  `tests/edgelab/test_snapshot.py::TestWriteOnceImmutability`.

## 11. Workflow failure policy (revised — maintainer review item 1)

**The objective** ("every future production slate run preserves a
snapshot automatically with no silent missing dates") is **not**
satisfied by `continue-on-error: true` alone — that only guarantees the
production workflow keeps running; it does nothing to make a capture
failure *visible* or *recoverable*. The revised design adds three layers
on top of the original non-fatal step:

1. **Machine-readable status, always written.** Every invocation of
   `scripts/create_snapshot.py` (success or failure) writes/updates
   `data/edgelab/snapshot_capture_status.json` — keyed by `date|stage`,
   recording outcome, timestamp, and `workflowRunId`. This is git-committed
   telemetry about the *capture process* (not a claim about snapshot
   content, and NOT part of any manifest — legitimately overwritten in
   place), so "did capture succeed for date D" is answerable by reading
   one small file.
2. **Prominent job-summary banner on failure.** A `:rotating_light:`
   section is written to `$GITHUB_STEP_SUMMARY` on any non-success
   outcome, visible on the workflow run's summary page even though the
   step itself is `continue-on-error`.
3. **A dedicated, separately-failing check.**
   `.github/workflows/snapshot-capture-check.yml` (driven by
   `scripts/check_snapshot_capture.py`) is **not** `continue-on-error` —
   it is the thing actually allowed to go red. It scans for any date with
   real evidence a production run occurred (`data/pipeline/<date>/recommendations.json`
   exists; or `data/edgelab/settlements|clv_quotes/<date>.jsonl`/
   `observations/<date>.jsonl.gz` exist for the postgame/closing stages)
   but has no corresponding Snapshot, attempts **safe recovery** by
   simply calling `build_snapshot()` again (idempotent, reads whatever
   source data still exists on disk *right now*, never fabricates a
   historical value for data since overwritten/pruned — `build_snapshot()`
   already reports `MISSING_REQUIRED_INPUT` honestly in that case), and
   fails the job only if a gap remains unrecovered. Runs daily
   (`30 6 * * *` UTC) plus `workflow_dispatch`.

**Documented tradeoff**: the production slate workflow's (`fetch-slate.yml`/
`edgelab-postgame.yml`) own green checkmark can never be trusted alone to
prove "every snapshot was captured" — that assurance now lives in the
dedicated check's own pass/fail state, deliberately decoupled so a
capture bug can never block manual slate access or bet placement, while
still being unable to hide behind an unrelated green run.

- `.github/workflows/fetch-slate.yml`: `Create immutable PRE_GAME_DECISION
  snapshot` runs last (`if: always()`, `continue-on-error: true`), after
  every artifact this run could possibly produce already exists or has
  definitively failed to.
- `.github/workflows/edgelab-postgame.yml`: `Create immutable
  POST_GAME_SETTLEMENT snapshot` and `Create immutable CLOSING_LINE
  snapshot`, same posture.
- Both workflows' existing commit steps `git add` the new
  `data/edgelab/snapshots/<date>/` paths alongside what they already
  commit.
- Manual slate operation (`workflow_dispatch`) is unaffected.

## 12. Historical limitations / backfill (confirmed, not assumed)

Real backfill results (`scripts/backfill_snapshots.py`, run against this
repository's actual data as of 2026-08-02), using
`build_snapshot_as_backfill()` — which stamps
`captureMode: HISTORICAL_BACKFILL`, structurally distinguishing a
backfilled manifest from a contemporaneous production capture
(`captureMode: LIVE_CAPTURE`), so a reader can never confuse the two:

- **54 candidate historical dates** found any evidence for.
- **4 dates** (`2026-07-30`, `2026-07-31`, `2026-08-01`, `2026-08-02`)
  have real `data/pipeline/<date>/` artifacts and were actually
  backfilled with real, committed Snapshots (`captureMode: HISTORICAL_BACKFILL`).
- **2 of those 4** (`2026-07-30`, `2026-08-02`) classify `PARTIAL_REPLAY`
  for `PRE_GAME_DECISION` (every `REQUIRED` component available, but
  `EFFECTIVE_CONFIG` is always `PARTIAL` and the production commit is
  always ambiguous — see §7/§8); the other 2 (`2026-07-31`, `2026-08-01`)
  classify `MISSING_REQUIRED_INPUT` because `data/slates/<date>/authoritative.json`
  never existed for those dates — the slate protection step quarantined
  every run for those dates as `rejected_contaminated_*` (confirmed via
  `ls data/slates/<date>/`), a genuine, independently confirmed
  data-quality finding, not a bug introduced by this milestone.
- **52 of the 54 candidate dates** classify `NOT_RECONSTRUCTABLE`
  (`MISSING_REQUIRED_INPUT`) — every date before `data/pipeline/<date>/`
  archival began. This mechanically reflects that raw run projections
  were genuinely never preserved for those dates. No historical input is
  fabricated to paper over this gap.

`captureMode` distinguishes recovery too: the dedicated check's automatic
recovery (§11) stamps `LIVE_CAPTURE`, not `HISTORICAL_BACKFILL` — a
same-window automated recovery is part of the *live* operational capture
system, not a one-time historical backfill; `HISTORICAL_BACKFILL` is
reserved exclusively for `scripts/backfill_snapshots.py`'s output.

## 13. Replay integration (for a future milestone)

A future replay engine reads exactly one file per `(stage, date, runKey)`
— `manifest.json` — to know what's available, with hash-verified
certainty about completeness. It should call `verify_snapshot()` (or the
higher-level `completeness_report()`, which already folds verification
in) before trusting any frozen content, and must never consume a
manifest whose reported `completenessStatus` is anything other than
`COMPLETE_FOR_PRODUCTION_REPLAY` as if it were fully faithful — per §8,
that status is currently unreachable in this repository, so a replay
engine built against today's snapshots should expect to work with
`PARTIAL_REPLAY`-or-worse manifests and reason explicitly about
`missingComponents`/`limitationReasons` rather than assuming completeness.
**This milestone does not implement that engine** — only `load_manifest`,
`load_latest_pregame_manifest`, `find_manifest_by_id`, `list_components`,
`completeness_report`, `verify_snapshot`, and `load_frozen_component`
(the smallest possible read interface).
