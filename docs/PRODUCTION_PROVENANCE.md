# PRODUCTION_PROVENANCE.md

Forward Replay Corpus and Production Provenance milestone. Builds on
`docs/SNAPSHOT_ARCHITECTURE.md` (capture) and `docs/REPLAY_ENGINE.md`
(consumption): this milestone closes the gap between those two — every
future production slate run now automatically captures a complete,
provenance-bound decision snapshot AND generates a research-only
candidate replay against it, so the trustworthy replay corpus grows
every day without manual intervention.

This is capture and orchestration work only. It does not change live
probabilities, recommendations, tiers, staking, risk decisions,
settlement rules, CLV calculations, market selection, or production
betting behavior — every new script here is either read-only or writes
exclusively to `data/edgelab/` paths no production code reads.

## 1. Production provenance chain

Three distinct commit-SHA concepts exist in this system, deliberately
never conflated:

| Field | What it identifies | Where it lives |
|---|---|---|
| `productionCommitSha` | The commit whose code produced THIS run's model/pricing/recommendation output | `SnapshotManifest.productionCommitSha` / `.productionProvenance` |
| `snapshotWriterCommitSha` | The commit checked out when the snapshot manifest itself was written | `SnapshotManifest.snapshotWriterCommitSha` |
| `candidateModelCommitSha` | The commit that RE-EXECUTED a snapshot's inputs later, during replay (possibly a different checkout entirely, possibly `-dirty`) | `ReplayRun.candidateModelCommitSha` |

**Capture mechanism** (`scripts/capture_production_provenance.py`): the
FIRST step `fetch-slate.yml` runs after the slate date is known, before
any data fetch or model-execution step. Writes
`data/pipeline/<date>/provenance.json` (a normal
`lib.pipeline_artifacts` stage artifact) containing `commitSha`
(`GITHUB_SHA`), `workflowRunId`, `workflowRunAttempt`, `ref`, `refName`,
`repository`, `workflow`, `job`, `eventName`, `capturedAt` — every field
read directly from a GitHub Actions environment variable, never guessed.

**Why this is authoritative, not reconstructed**: within one workflow
job, `actions/checkout` runs exactly once — the working tree's actual
files never change mid-job even though `fetch-slate.yml` commits to
`main` several times during its own run (Kalshi snapshot, fetch_status,
slate publication, pipeline status, snapshot). So `GITHUB_SHA` captured
at the very start of the job IS the commit whose code executed
`build_market_ledger.py`/`risk_gate.py` later in that same job — this
script exists to record that fact durably and as early as possible, not
to make it true.

**Consumption** (`lib.edgelab.snapshot._production_provenance()`, called
from `build_pre_game_manifest`): resolves `provenance.json` and
cross-checks its `commitSha` against `snapshotWriterCommitSha` (computed
independently, later in the same job) — since both come from the same
checkout with no re-checkout in between, they must always agree. Three
outcomes:

- **`CAPTURED`** — the artifact exists and agrees. `productionCommitSha`
  is set, `PRODUCTION_PROVENANCE` component is `AVAILABLE`.
- **`AMBIGUOUS`** — the artifact exists but disagrees with the live git
  state (e.g. a stale leftover from an earlier, different run).
  `productionCommitSha` stays `null` — **never trusted**.
  `PRODUCTION_PROVENANCE` is `PARTIAL` (`PRODUCTION_COMMIT_AMBIGUOUS`).
- **`MISSING`** — no artifact at all (e.g. a pre-this-milestone snapshot,
  or `capture_production_provenance.py` itself failed).
  `productionCommitSha` stays `null`. `PRODUCTION_PROVENANCE` is
  `MISSING`.

`PRODUCTION_PROVENANCE` is a **REQUIRED** component for `PRE_GAME_DECISION`
— a `MISSING` provenance record downgrades `completenessStatus` to
`MISSING_REQUIRED_INPUT` (never silently ignored); an `AMBIGUOUS` one
caps it at `PARTIAL_REPLAY` (same treatment `EFFECTIVE_CONFIG` already
gets). Never reconstructed after the fact from any other source — a
missing/ambiguous value stays `null`, full stop.

**Downstream unlock**: `lib.edgelab.replay.derive_replay_fidelity_from_eligibility()`
already compared `productionCommitSha` against a replay's own
`candidateModelCommitSha` to decide `LEVEL_3_BIT_FOR_BIT` — that logic
existed since the Level 2 Historical Replay Engine milestone but could
never fire, because `productionCommitSha` was always `null`. It is now a
real, working capability: replay a same-day snapshot on the same commit
that produced it (no code changes since), and fidelity correctly
promotes to `LEVEL_3_BIT_FOR_BIT`.

## 2. Effective-config semantics

`lib.edgelab.snapshot.capture_effective_config()` (called once per
`PRE_GAME_DECISION` build) now captures three layers, each honestly
bounded:

1. **`rulesConfigContents`/`rulesConfigVersion`** — `config/rules.json`
   verbatim (pre-existing; NOT the complete production rule set).
2. **`liveConstants`** — every hardcoded module-level constant this
   milestone found by direct inspection of
   `scripts/build_market_ledger.py`/`scripts/risk_gate.py` that actually
   gates a recommendation/tier/staking/eligibility decision
   (`THRESHOLD_HIGH/MEDIUM/PAPER`, `CAL_HIGH/MEDIUM/PAPER`,
   `REQUIRED_MARKETS`, `F5_PRICING_VERSION_CURRENT`, `REAL_MONEY_TIERS`,
   `TT_MARKETS`, `ML_F5_MARKETS`, `TT_MIN_EDGE_PCT`, `TT_MAX_BETS`,
   `TT_MAX_STAKE`, `TT_MAX_STAKE_PCT`, `ML_F5_MIN_STAKE_PCT`,
   `DAILY_RISK_CAP`) — read via `getattr` on the live-imported module,
   never copy-pasted, so a future code change is automatically reflected
   with zero maintenance burden.
3. **`hardcodedLogicSourceHashes`** — SHA-256 of
   `scripts/build_market_ledger.py`, `scripts/risk_gate.py`,
   `lib/postponed_guard.py` — the mechanical, no-refactor answer for
   logic this milestone can't yet name as a constant (inline gate
   conditionals, e.g. Rule 51/52/71). Can't say WHAT changed, but proves
   WHETHER the file containing it changed between two captures — the
   same signal `classify_mismatch_reason()` already uses for
   `f5PricingVersion`.
4. **`unrepresentedLogic`** — an explicit, reviewed list of concrete
   gates this milestone still cannot structurally represent (lineup-
   confirmation ratio, TT critical-field list, postponed/live-status
   classification) — classified honestly rather than silently absent.

`effectiveConfigHash` = SHA-256 of the record's own canonical content
(excluding the hash field itself). `productionCommitSha`/`productionRunId`
are embedded directly in the record too, so it is self-describing even
read in isolation outside its manifest.

**Still PARTIAL, permanently, by design**: neither
`scripts/build_market_ledger.py` nor `scripts/risk_gate.py` reads
`config/rules.json` at runtime at all (confirmed by inspection — no
`open()`/`json.load()` of it anywhere in either module). The thresholds
that execute are whatever is hardcoded in the current checkout,
identically for original capture and for a later replay's own
re-execution — `EFFECTIVE_CONFIG`'s completeness is an **audit/provenance**
signal (can we explain a hardcoded-threshold change after the fact?),
never an **input-fidelity** signal (replay always has what the code
needs to run, regardless of this record's completeness). This is why
`EFFECTIVE_CONFIG_PARTIAL` never blocks `ELIGIBLE_LEVEL_2` — see
`docs/REPLAY_ENGINE.md` §1.

## 3. Daily capture lifecycle

```
fetch-slate.yml (per production slate date):
  1. Set date
  2. Record production provenance   -- scripts/capture_production_provenance.py (NEW)
  3. Fetch/build the full slate + market ledger (unchanged)
  4. Publish authoritative slate + meta.json (unchanged)
  5. Risk gate / bet logging / closing-line capture chain (unchanged)
  6. Create immutable PRE_GAME_DECISION snapshot  -- scripts/create_snapshot.py
  7. Run automatic forward candidate replay        -- scripts/run_forward_replay.py (NEW)
  8. Commit snapshot + replay artifacts together
```

Steps 2, 6, 7 all use `continue-on-error: true` — new capture/research
infrastructure must never be able to block production slate publication
or bet placement. A failure at any of these three steps surfaces as:

- a downgraded `completenessStatus`/`gateStatus` (steps 2, 6), or
- a recorded `run_forward_replay.py` failure status (step 7),

visible in the workflow's job summary and in
`data/edgelab/{snapshot_capture_status,forward_replay_status}.json` —
never a silent gap.

**One snapshot per production decision moment, not merely per date**:
`lib.edgelab.snapshot._production_run_key()` derives a distinct
`productionRunKey` from `recommendations.json`'s own `meta.createdAt`
for every genuine rerun (lineup recheck, retry) — confirmed unaffected
by this milestone, still correct. `scripts/check_snapshot_capture.py`'s
missing-snapshot detection was tightened this milestone
(`_has_pregame_snapshot_for_current_run`, was
`_has_any_pregame_snapshot`): a date with two production runs where the
FIRST run's snapshot captured successfully but the SECOND (current) run's
capture failed is now correctly flagged as a gap — "any snapshot exists
for this date" previously hid that.

**Recovery**: `.github/workflows/snapshot-capture-check.yml` (unchanged
schedule/logic) attempts safe recovery for any missing snapshot by
calling `build_snapshot()` again against whatever source data still
exists — never fabricates data that's since been overwritten/pruned.
Recovery events are now durably logged to
`data/edgelab/snapshot_recovery_log.jsonl` (append-only, new this
milestone) — previously only visible in that one run's job logs, never
retained for `scripts/corpus_health_report.py` to count historically.

## 4. Automatic replay lifecycle

`scripts/run_forward_replay.py`, the step immediately after snapshot
capture in `fetch-slate.yml`:

1. Loads the just-created `PRE_GAME_DECISION` snapshot from the local
   filesystem (does not need it committed to git first).
2. If no snapshot exists (capture failed), records `no_snapshot` and
   exits 0 — never attempts a replay against nothing.
3. Runs `lib.edgelab.replay.execute_replay(manifest, replay_mode=CANDIDATE_MODEL, allow_level_1=False)`
   — same engine as the Level 2 Historical Replay Engine milestone,
   unmodified. Verifies all hashes first (eligibility assessment),
   never silently downgrades fidelity (an `ELIGIBLE_LEVEL_1_ONLY`
   snapshot is honestly `REJECTED_INELIGIBLE`, never auto-approximated).
4. Writes output via `write_replay_outputs()` — exclusively under
   `data/edgelab/replay_runs/`, write-once, verified no-op on repeat.
5. Any unexpected exception is caught and recorded, never crashes the
   workflow — research-only automation must not be able to fail
   production.

`data/edgelab/forward_replay_status.json` (new, overwritten-in-place,
keyed by date) records the outcome of every attempt — `completed`,
`rejected_ineligible`, `no_snapshot`, `unexpected_error` — for
`scripts/corpus_health_report.py` to read.

## 5. Replay timing and leakage (item 7)

Ordering is guaranteed two independent ways:

1. **Workflow ordering**: `run_forward_replay.py` runs during the SAME
   pregame job as slate publication, hours before any postgame workflow
   (`edgelab-postgame.yml`, closing-line capture) could possibly run for
   that date. No settlement/final-score/closing-line data exists
   anywhere in this repository yet at the moment this step executes.
2. **Structural guard** (unchanged from the Level 2 Historical Replay
   Engine milestone, `lib.edgelab.replay.run_candidate_replay()`): reads
   ONLY the `PRE_GAME_DECISION` manifest's own components; raises
   `ReplayError` if ever handed a `POST_GAME_SETTLEMENT`/`CLOSING_LINE`
   manifest, or if that manifest's own `SETTLEMENT`/`CLV` components are
   ever `AVAILABLE` (they must always be `NOT_APPLICABLE_FOR_STAGE`
   placeholders on a pregame manifest).

Belt and suspenders: even if a future workflow change ever reordered
steps, guard #2 makes look-ahead bias structurally impossible, not just
procedurally unlikely. See `docs/REPLAY_ENGINE.md` §6 for the full
proof, including the adversarial test that places real enticing
postgame data in a genuinely linked snapshot and confirms replay's own
numeric output is unaffected.

## 6. Closing and settlement supplements (items 8/9)

**Closing-line linkage** (`lib.edgelab.replay._closing_clv_by_ticker`,
fixed this milestone): a ticker can have MULTIPLE `ClvQuote` rows — one
per observation checkpoint (`FIRST_DAILY`, `T_MINUS_90`, ..., `CLOSING`)
— confirmed against real 2026-08-02 data: 620 of 4,844 tickers had more
than one row. Only the row with `isClosingQuote=True` is the genuine
closing quote (the `checkpoint` label alone is NOT reliable — real rows
exist where `isClosingQuote=True` but `checkpoint` is `T_MINUS_90`, not
`"CLOSING"`, when a market suspended early). The previous
implementation silently kept whichever row happened to be last in file
iteration order — this milestone fixes it to filter strictly on
`isClosingQuote=True`: zero such rows is `NO_CLV_QUOTE_FOR_THIS_MARKET`
(honestly unresolved), more than one is reported as ambiguous and
excluded (a genuine upstream data-quality issue, never guessed at).

CLV's entry price (also fixed in the Level 2 milestone's maintainer
review) is the executable price (`executableMarketProb`), never the
midpoint — see `docs/REPLAY_ENGINE.md` §10.

**Settlement linkage**: unchanged from the Level 2 Historical Replay
Engine milestone — `lib.edgelab.replay._settlement_linkage_for_ticker`
joins the linked `POST_GAME_SETTLEMENT` snapshot's already-computed
`SETTLEMENT` records by exact `marketTicker`, never re-derives a result
from raw scores, never infers one market's outcome from a related
market. Settlement RULES (full-game=final score, F5=inning-5 evidence,
NRFI/YRFI=first-inning evidence, props=player-stat evidence) live
entirely in `lib.edgelab.settlement`/`scripts/settle_markets.py`,
unmodified by either milestone — replay only joins, never reimplements.

**This milestone's actual contribution to settlement/CLV coverage**:
none of the linkage code changed except the CLV disambiguation fix
above — coverage goes from 0% to non-zero **going forward** simply
because production snapshots now automatically link to
`POST_GAME_SETTLEMENT`/`CLOSING_LINE` snapshots that
`edgelab-postgame.yml`/`capture-closing-lines.yml` (pre-existing,
unmodified workflows) already capture the following day, once those
workflows run for dates this milestone's forward corpus produces. No
historical outcome is fabricated to force this number up.

## 7. Corpus health statuses (item 11)

`scripts/corpus_health_report.py` derives one status per date,
first-match-wins (same convention as `assess_replay_eligibility`):

| # | Status | Condition |
|---|---|---|
| 1 | `INTEGRITY_FAILURE` | `verify_snapshot()` fails for the date's manifest |
| 2 | `DEGRADED_MISSING_SNAPSHOT` | no snapshot exists, or `completenessStatus == MISSING_REQUIRED_INPUT` |
| 3 | `DEGRADED_CONFIG_PARTIAL` | `productionCommitSha` unknown (provenance `MISSING`/`AMBIGUOUS`) |
| 4 | `DEGRADED_REPLAY_FAILURE` | no forward-replay attempt recorded, or it did not complete |
| 5 | `DEGRADED_CLOSING_DATA` | no linked `CLOSING_LINE` snapshot yet |
| 6 | `DEGRADED_SETTLEMENT_DATA` | no linked `POST_GAME_SETTLEMENT` snapshot yet |
| 7 | `HEALTHY` | otherwise |

A date with neither a production run nor a snapshot never appears in
the report at all — structurally excluded rather than labeled, so a
genuine no-slate day (e.g. MLB offseason) can never be mistaken for a
capture failure.

`consecutiveDegradedRuns` counts backward from the most recent date
until the first `HEALTHY` one — a free (no paid infrastructure), always-
visible alert signal: `scripts/corpus_health_report.py` prints an
`ALERT:` line to stderr once this reaches 3, and the JSON/Markdown
report always shows the count regardless of threshold.

## 8. Failure and recovery procedures

| Failure | Where it's visible | Recovery |
|---|---|---|
| Provenance capture fails | Step exits non-fatally (`continue-on-error`); snapshot's `PRODUCTION_PROVENANCE` ends up `MISSING` | None automatic — a future workflow run captures cleanly; the gap for this specific run is permanent (never reconstructed) |
| Snapshot capture fails/missing | `data/edgelab/snapshot_capture_status.json`, job summary `:warning:`/`:rotating_light:` banner | `snapshot-capture-check.yml` (daily) attempts safe recovery from still-existing source data; logs to `snapshot_recovery_log.jsonl` |
| Forward replay fails | `data/edgelab/forward_replay_status.json`, job summary | None automatic this milestone — rerunning `scripts/run_forward_replay.py <date>` manually is safe (verified no-op or fresh completion) |
| Corpus degraded 3+ consecutive dates | `scripts/corpus_health_report.py` stderr `ALERT:` line + report `consecutiveDegradedRuns` field | Manual investigation — run `scripts/check_snapshot_capture.py`/`scripts/run_forward_replay.py` directly against the affected dates |

## 9. Storage and retention policy (item 12)

`scripts/snapshot_storage_report.py` now reports two buckets:

- **Snapshots** (unchanged methodology from `SNAPSHOT_ARCHITECTURE.md`):
  `FROZEN_COPY` + manifest bytes only — `REFERENCED_IMMUTABLE`
  components cost ~0 marginal bytes.
- **Replay runs** (new): total bytes under `data/edgelab/replay_runs/`,
  observed-days-based marginal cost.

Both project to 1/3/5-season horizons explicitly (`--days-per-season`
configurable, default 185 ≈ 6 MLB months). Real observed numbers as of
this milestone (4 historical snapshot dates + 2 replay runs):
~9.3 MB observed, ~452 MB/season, ~1.36 GB/3 seasons, ~2.26 GB/5 seasons
combined — see `data/edgelab/reports/storage_health_report.json` for the
exact current figures.

**Retention policy**: manifests are retained permanently (no script in
this repository deletes one). Decision-time components (`FROZEN_COPY`
bytes) are retained alongside their manifest for multi-season research —
no deletion mechanism exists in this milestone, and none is
implemented (item 12 explicitly forbids deleting existing history).
Research outputs (`ReplayRun`/`ReplayResult`) MAY someday use a
different retention policy than source snapshots, since they are
mechanically reproducible from the retained snapshot + current
replay-engine code (deterministic rerun) — unlike a snapshot's own
frozen decision-time bytes, which cannot be regenerated once live
sources are overwritten. This is documented as a future option, not
implemented as an active policy.

## 10. Historical versus forward-captured fidelity (item 13)

Three distinct fidelity claims, never conflated:

- **Contemporaneous forward capture** (this milestone, going forward):
  `productionCommitSha` genuinely known, `effectiveConfigHash` genuinely
  bound to that exact run, automatic same-day candidate replay. The
  honest, strongest claim this repository can make.
- **Historical backfill** (`scripts/backfill_snapshots.py`, prior
  milestone): real snapshots built from real already-archived pipeline
  artifacts for dates before snapshot capture existed —
  `captureMode=HISTORICAL_BACKFILL`, distinct from `LIVE_CAPTURE`.
  `productionCommitSha` is `null` for these (no provenance artifact was
  ever captured at the time) and stays `null` — never fabricated
  retroactively.
- **Approximate historical reconstruction**: explicitly NOT done. This
  milestone does not attempt to infer or guess a historical commit SHA,
  a historical effective-config value, or any other provenance fact for
  a run that predates real capture.

The four existing historical snapshots (2026-07-30 through 2026-08-02)
are **untouched** by this milestone except for the schema-compatible,
mechanically-honest consequence of the new `PRODUCTION_PROVENANCE`
required component: since none of them ever had a `provenance.json`
artifact, their `completenessStatus` is now correctly read as
`MISSING_REQUIRED_INPUT` (2026-07-30/07-31, which were already
`ELIGIBLE_LEVEL_1_ONLY` for other reasons) or downgrades their
provenance-specific gate status (2026-08-01/08-02, still
`ELIGIBLE_LEVEL_2` for replay purposes — see §1, `EFFECTIVE_CONFIG`/
provenance completeness is an audit signal, not an input-fidelity one).
**No historical snapshot was rebuilt, mutated, or had a
`productionCommitSha` value invented for it.** Write-once discipline
means `build_snapshot()` would refuse to overwrite them with different
content even if invoked again.

## 11. How this corpus supports future uncertainty/Kelly research

The Level 2 Historical Replay Engine milestone's `performance` scoring
(Brier score, log loss, calibration error) already exists — what it
lacked was volume: `CALIBRATED` sample-size status needs n≥100 resolved
decisions, and the historical sample (2 eligible dates, 0% settlement
resolved) is far below that. This milestone's automatic daily capture +
replay is the mechanism that grows that sample size WITHOUT manual
intervention going forward, and the `productionCommitSha`/
`effectiveConfigHash` provenance chain means a future model-fitting
milestone can finally distinguish "the model changed" from "a
hardcoded threshold changed" from "genuine market movement" when
explaining why a fitted model's calibration shifts over time — a
distinction that was structurally impossible to make honestly before
this milestone (every commit/config field was `null`).

See `scripts/corpus_health_report.py`'s output for exactly how large the
trustworthy (Level 2, settlement-resolved) sample is at any point in
time — that report, not a fixed calendar date, is the correct gate for
"is the corpus large enough to begin uncertainty estimation."
