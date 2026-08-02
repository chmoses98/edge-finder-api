# SNAPSHOT_ARCHITECTURE.md

Historical Capture Completeness and Immutable Snapshot Foundation
milestone. Implements the design reviewed in the prior "Snapshot
Architecture" design review turn (no separate doc file was produced for
that review; its conclusions are carried forward and implemented here).

This is capture-and-reproducibility infrastructure only. It does not
change model probabilities, recommendation logic, thresholds, staking,
market selection, settlement outcomes, or any production handicapping
behavior — confirmed by `tests/edgelab/test_snapshot.py`'s
`TestNoProductionRecommendationChanges` (zero working-tree diff on every
core handicapping file this milestone touched).

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
milestone builds only the capture side — see item 9's roadmap for what a
replay engine consumes from here.

It differs from every existing artifact in this repository:
`data/pipeline/<date>/*.json` is six *separate* files with no manifest
tying them together or proving they're mutually consistent;
`data/edgelab/*/​<date>.jsonl` files are per-entity time series, not a
per-moment bundle; `data/weather.json`/`data/bullpen.json` are single
overwritten live files with **no history at all**. A Snapshot is the
first cross-entity, single-manifest, content-addressed bundle for one
moment that this repository has ever had.

## 2. Why a manifest alone is insufficient for mutable data

A manifest that only references `bets.json`, `data/weather.json`, or any
other file that gets rewritten later is not an immutable snapshot — a
future reader following that reference would see *today's* content, not
what production actually saw on the snapshotted date. See §3 below for
the exact rule this milestone uses to decide which sources need their
bytes physically duplicated at capture time (`FROZEN_COPY`) versus which
can be safely referenced by path + hash (`REFERENCED_IMMUTABLE`).

## 3. REFERENCED_IMMUTABLE vs FROZEN_COPY

**REFERENCED_IMMUTABLE** (path + SHA-256 only, no duplicate bytes) when
a second read of the same path, for the same logical `(date, key)`, is
*believed* to return the same bytes it did at capture time, because the
writer uses one of:

- `lib.pipeline_artifacts`'s write-once-per-`(stage, date)` convention
  (`data/pipeline/<date>/{normalized_slate,projections,recommendations,execution,validation,protection}.json`);
- an append-only or "dated, kept forever" storage convention
  (`data/kalshi_registry_snapshots/kalshi_search_<date>.json`,
  `data/edgelab/<entity>/<date>.jsonl(.gz)`);
- an explicitly protected, non-overwritable path
  (`data/slates/<date>/authoritative.json`, which `scripts/protect_slate.py`
  never rewrites once written for a date whose sentinel check passed).

This is a strong belief, **not** a filesystem-enforced fact — every
`REFERENCED_IMMUTABLE` component's hash is re-checked at
`verify_snapshot()` time, and a violation is reported loudly
(`INTEGRITY_FAILURE`), never silently trusted forever.

**FROZEN_COPY** (exact bytes physically copied into the snapshot's own
`frozen/` directory at capture time) when the source is a **single,
live, overwritten-in-place file with no per-date history at all**:
`data/weather.json`, `data/bullpen.json`, the legacy `data/slate.json`
(only used as a fallback when no `authoritative.json` exists yet for the
date), and `config/rules.json` (bundled into a synthesized
`EFFECTIVE_CONFIG` record — see §7). For these, a path reference would
silently describe *today's* content whenever a future reader opens the
path. Byte duplication is a deliberate, narrow exception to the general
"don't copy bulky immutable files unnecessarily" preference, applied
only where it's unavoidable.

`lib/edgelab/snapshot.py`'s module docstring is the canonical statement
of this rule; every component builder in that file implements it.

## 4. Snapshot contents (by stage)

| componentType | PRE_GAME_DECISION | POST_GAME_SETTLEMENT | CLOSING_LINE | storageMode |
|---|---|---|---|---|
| PRODUCTION_SLATE_INPUT | REQUIRED | — | — | authoritative.json (referenced) or legacy slate.json (frozen) |
| RAW_PROJECTIONS | REQUIRED | — | — | referenced |
| RECOMMENDATION_OUTPUT | REQUIRED | — | — | referenced |
| MARKET_UNIVERSE | REQUIRED | — | — | referenced (dated kalshi snapshot) |
| EFFECTIVE_CONFIG | REQUIRED | — | — | frozen (synthesized record) |
| RISK_GATE_OUTPUT | REQUIRED | — | — | referenced |
| NORMALIZED_SLATE, EXECUTABLE_PRICES, BID_ASK, LINEUP_STATE, BULLPEN_STATE, WEATHER, PARK_FACTORS, MODEL_EVALUATIONS, RECOMMENDATIONS, MARKET_OBSERVATIONS, EXECUTION_SLIP, VALIDATION_ARTIFACT, PROTECTION_ARTIFACT | NICE_TO_HAVE | — | — | mixed (see below) |
| SETTLEMENT | NOT_APPLICABLE_FOR_STAGE | REQUIRED | — | referenced |
| CLV | NOT_APPLICABLE_FOR_STAGE | REQUIRED | — | referenced |
| MARKET_OBSERVATIONS (closing quotes) | — | — | REQUIRED | referenced |

`WEATHER`, `BULLPEN_STATE`, and `EFFECTIVE_CONFIG` are `FROZEN_COPY`
(§3). Everything else that is available is `REFERENCED_IMMUTABLE`.
`LINEUP_STATE` and `PARK_FACTORS` are denormalized pointers to
`RECOMMENDATION_OUTPUT` and `EFFECTIVE_CONFIG` respectively — the same
evidence, never a second freeze/hash of the same bytes.

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

## 5. Snapshot lifecycle

**Creation**: `lib.edgelab.snapshot.build_snapshot(stage, date)` builds
every component into a private staging directory first, computes the
manifest and its hash, then calls the write-once commit step. Frozen
bytes are never written directly into the final `frozen/` directory
during the build phase — only at commit time, and only if the commit
actually succeeds as a first-time creation (see §8).

**Validation**: `data/edgelab/schema_v1/snapshot_manifest.schema.json`
and `snapshot_component.schema.json`, validated the same way every other
EdgeLab entity is (`lib.edgelab.schema.validate_record`) — required
fields, enums, `additionalProperties: false`.

**Immutability / retention**: manifests are kept forever (small, a few
KB each). Frozen bytes follow the same retention posture as everything
else under `data/edgelab/` — no separate pruning is introduced by this
milestone. `REFERENCED_IMMUTABLE` sources keep whatever retention policy
they already have (e.g. `data/kalshi_registry_snapshots`'s existing
21-day rolling window for timestamped files, dated files kept forever).

**Compression**: none added by this milestone — frozen files here are
small (a few KB each; see §11's real measured sizes). If a future
frozen-source candidate turns out to be large, follow
`lib.edgelab.storage`'s existing deterministic-gzip convention
(`mtime=0`), not a new format.

**Versioning**: `schemaVersion: "1"`, following the existing
`data/edgelab/schema_v1` policy (additive fields never bump; breaking
changes do).

**Lookup**: `lib.edgelab.snapshot.load_manifest(stage, date)` (direct),
`find_manifest_by_id(snapshot_id)` (scans the small `data/edgelab/snapshots/`
tree — no index is warranted at this data volume).

**Restoration**: `load_frozen_component(manifest, component_type)`
returns the parsed JSON of a frozen component; `REFERENCED_IMMUTABLE`
components are resolved by opening `sourcePath` directly. There is no
separate "restore" step — a manifest is a pointer graph, not a backup.

**Future migrations**: not built speculatively. `schema.py`'s existing
"no `schema_v2` until one is actually needed" discipline applies here
too.

## 6. Write-once / integrity guarantees

- **SHA-256** component hashes (`lib.edgelab.snapshot.sha256_file` /
  `sha256_bytes`), computed over raw file bytes (`REFERENCED_IMMUTABLE`)
  or the frozen copy (`FROZEN_COPY`), never over a re-serialized form
  that could silently normalize away a real difference.
- **Deterministic canonical serialization**
  (`canonical_json_bytes`: sorted keys, no incidental whitespace) for
  every synthesized record (e.g. `EFFECTIVE_CONFIG`) and for the
  manifest-hash computation itself.
- **manifestHash** deliberately excludes `capturedAt`,
  `workflowRunId`, `productionRunId`, `snapshotWriterCommitSha`, and
  `provenance` — those are "when/how this was captured" facts that
  legitimately differ between two otherwise-identical captures of the
  same underlying data (a manual rerun minutes later has a later
  timestamp but must still be recognized as a true no-op if every
  component's content is unchanged). Only fields describing **what**
  was captured participate in the hash.
- **Atomic writes**: every file this module writes (`manifest.json`,
  frozen copies) uses temp-file + `fsync` + `os.replace`, the same
  pattern as `lib.pipeline_artifacts.write_stage_artifact()` and
  `lib.edgelab.storage`.
- **Strict write-once refusal**: `_commit_snapshot()` never overwrites
  an existing `manifest.json`. A rerun with byte-identical content
  (per the hash above) is a **no-op** — verified, not rewritten. A
  rerun whose content actually differs is a **conflict**: the existing
  manifest and its `frozen/` directory are left completely untouched;
  the candidate manifest and its staged frozen bytes are moved into
  `<snapshot_dir>/conflicts/<UTC timestamp>/` as diagnostic evidence,
  and the CLI exits non-zero so a workflow step (or a human) notices.
- **Missing-component validation**: every assessed `componentType` gets
  an explicit entry, even when `MISSING`/`NOT_APPLICABLE_FOR_STAGE` — a
  gap is a labeled row, never a silent omission (`missingComponents` is
  a denormalized index over exactly these rows).
- **Deterministic reruns**: proven by
  `tests/edgelab/test_snapshot.py::TestWriteOnceImmutability` — same
  inputs twice produces the identical `manifestHash` and does not touch
  the file's mtime.

## 7. Effective production configuration

`config/rules.json` is **not** the complete production rule set — its
own reader (`lib/rules_config.py`) documents that the live betting/
pricing pipeline hardcodes some thresholds directly in code, not via
this file. This milestone does not pretend otherwise.
`capture_effective_config()` is the narrowest truthful mechanism
available: it freezes (a) `config/rules.json`'s own contents and
`_version` field verbatim, (b) the one real, live-importable versioned
constant that exists today (`F5_PRICING_VERSION_CURRENT` from
`scripts/build_market_ledger.py`, imported and read directly — never
re-derived from source text), and (c) whichever `rulesVersion` literal
that date's own `data/pipeline/<date>/execution.json` artifact already
recorded (`risk_gate.py` writes `"rulesVersion": "1.0"` into it —
read back verbatim, best-effort). Nothing here is fabricated or
text-scraped from source; every field is either read from a real file
or read from a real, live code object.

## 8. Storage design and retention

```
data/edgelab/snapshots/<date>/<stage>/
  manifest.json
  frozen/
    weather.json
    bullpen.json
    effective_config.json
  conflicts/<UTC-timestamp>/        (only present if a genuine conflicting rerun occurred)
    candidate_manifest.json
    frozen/...
```

`<stage>` is `pre_game_decision`, `post_game_settlement`, or
`closing_line` (lowercased, matching the rest of this repo's directory
naming convention). Manifests are plain, uncompressed, sorted-key JSON
(small, git-diffable, deliberately not gzipped — unlike
`observations/<date>.jsonl.gz`, which is bulky by comparison). No new
retention policy is needed for the manifest layer itself (kept forever,
negligible size); frozen/referenced sources keep whatever policy they
already have.

**Real measured storage** (from the 4 backfilled dates,
`scripts/snapshot_storage_report.py`): marginal cost (frozen bytes +
manifest bytes; `REFERENCED_IMMUTABLE` duplicates zero bytes) is
currently ~1.5–2 KB/day for `CLOSING_LINE`/`POST_GAME_SETTLEMENT` and
~43 KB/day for `PRE_GAME_DECISION` (dominated by manifest overhead on a
small sample — will amortize down as more days accumulate). Projected
over a 185-day season × 3 seasons: **~25 MB total marginal storage**
across all three stages — negligible against this repository's existing
`data/` footprint. See `data/edgelab/reports/snapshot_backfill_classification.json`
for the exact per-stage numbers this estimate is based on.

## 9. Replay integration (for a future milestone)

A future replay engine reads exactly one file per `(stage, date)` —
`manifest.json` — to know what's available, with hash-verified
certainty about completeness, rather than re-deriving "does this date
qualify?" per script the way
`scripts/research/f5_historical_impact_study.py` currently has to. It
should call `verify_snapshot()` before trusting any frozen/referenced
content, and must never consume a manifest whose
`completenessStatus` is `MISSING_REQUIRED_INPUT` as if it were
`COMPLETE_FOR_PRODUCTION_REPLAY`. **This milestone does not implement
that engine** — only `load_manifest`, `find_manifest_by_id`,
`list_components`, `completeness_report`, `verify_snapshot`, and
`load_frozen_component` (the smallest possible read interface, per this
milestone's explicit scope limit).

## 10. Historical limitations (confirmed, not assumed)

Real backfill results (`scripts/backfill_snapshots.py`, run against this
repository's actual data as of 2026-08-02):

- **54 candidate historical dates** found any evidence for (from
  `data/pipeline/`, `data/slates/`, and dated
  `data/kalshi_registry_snapshots/` files).
- **4 dates** (`2026-07-30`, `2026-07-31`, `2026-08-01`, `2026-08-02`)
  have real `data/pipeline/<date>/` artifacts and were actually
  backfilled with real, committed Snapshots.
- **2 of those 4** classify `APPROXIMATE_ONLY` for `PRE_GAME_DECISION`
  (missing only `NICE_TO_HAVE` components); the other 2 classify
  `MISSING_REQUIRED_INPUT` because `data/slates/<date>/authoritative.json`
  never existed for those dates — the slate protection step quarantined
  every run for `2026-07-31`/`2026-08-01` as `rejected_contaminated_*`
  (confirmed via `ls data/slates/<date>/`), a genuine, independently
  confirmed data-quality finding, not a bug introduced by this
  milestone.
- **52 of the 54 candidate dates** classify `NOT_RECONSTRUCTABLE`
  (`MISSING_REQUIRED_INPUT`) for `PRE_GAME_DECISION` — every date before
  `data/pipeline/<date>/` archival began. This is not a bug in the
  classifier; it mechanically reflects that raw run projections
  (`RAW_PROJECTIONS`) were genuinely never preserved for those dates.
  No historical input is fabricated to paper over this gap.

## 11. Workflow wiring

- `.github/workflows/fetch-slate.yml`: a new **BLOCK 9** step,
  `Create immutable PRE_GAME_DECISION snapshot`, runs last (`if: always()`),
  after every artifact this run could possibly produce already exists
  or has definitively failed to. `continue-on-error: true` — this new,
  additive capture infrastructure must never be able to fail the
  overall workflow or block production betting/handicapping (the
  "safest behavior" this milestone was asked to choose and document).
  A failed/incomplete capture is surfaced as a warning in the job
  summary (`scripts/create_snapshot.py` writes to `$GITHUB_STEP_SUMMARY`
  on both success and failure paths), never a silent gap.
- `.github/workflows/edgelab-postgame.yml`: two new steps,
  `Create immutable POST_GAME_SETTLEMENT snapshot` and
  `Create immutable CLOSING_LINE snapshot`, run after settlement/CLV
  ingestion, same `continue-on-error` posture.
- Both workflows' existing commit steps were extended to `git add` the
  new `data/edgelab/snapshots/<date>/` paths alongside what they
  already commit — no new commit step, no change to existing commit
  cadence or concurrency groups.
- Manual slate operation (`workflow_dispatch` on both workflows) is
  unaffected — snapshot creation runs identically for a manual or
  scheduled trigger.
