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

**Capture mechanism** (`scripts/capture_production_provenance.py`): runs
in `fetch-slate.yml` immediately before the model-execution chain begins
(`merge_odds.py` onward), and — as of the PR #37 maintainer review below
— deliberately AFTER every in-job `git rebase --autostash origin/main`
this workflow performs. Writes `data/pipeline/<date>/provenance.json` (a
normal `lib.pipeline_artifacts` stage artifact) containing `commitSha`
(`GITHUB_SHA`), `gitHeadShaAtCapture` (live `git rev-parse HEAD`,
informational only), `workingTreeDirty` (see below), `workflowRunId`,
`workflowRunAttempt`, `ref`, `refName`, `repository`, `workflow`, `job`,
`eventName`, `capturedAt` — every field read directly from a GitHub
Actions environment variable or live git state, never guessed.

**PR #37 maintainer review finding (item 1) and fix**: the original
design captured provenance as the very FIRST step after the date was
known — before three separate in-job rebase points (Commit Kalshi
snapshot, the not-ready-path snapshot commit, Commit fetch_status.json).
Each `git rebase --autostash origin/main` legitimately updates the local
working tree/HEAD if anything else pushed to `main` in the meantime, so
capturing that early could in principle record a commit SHA a concurrent
push+rebase then silently moved past before the code that actually
produces the recommendations ran. The fix repositions capture to the
latest point that still runs strictly before `build_market_ledger.py` —
after all three rebases, immediately before `merge_odds.py`.

That repositioning also exposed (and fixed) a second finding: the
original cross-check compared the captured `commitSha` against
`snapshotWriterCommitSha`, both computed via the same `_git_commit_sha()`
helper that always prefers the static `GITHUB_SHA` env var. In real CI
both sides therefore always read the identical, never-mid-job-updating
value — the check could never actually disagree, making it structurally
inert (it could only theoretically fire in a local/manual run where
`GITHUB_SHA` happens to be unset). A live-HEAD-vs-live-HEAD comparison
was considered and rejected too: HEAD legitimately advances between
capture time and snapshot-write time (end of job) from routine,
code-untouching data commits this same job makes, so that comparison
would false-positive `AMBIGUOUS` on nearly every real production run.

**Consumption** (`lib.edgelab.snapshot._production_provenance()`, called
from `build_pre_game_manifest`): resolves `provenance.json` using the
`workingTreeDirty` signal instead — `git diff --quiet HEAD -- scripts/
lib/ config/`, scoped deliberately to CODE paths (a whole-repo dirty
check would always show dirty at the repositioned capture point, since
`data/` legitimately has uncommitted changes from earlier fetch steps in
the same job). Three outcomes:

- **`CAPTURED`** — the artifact exists and `workingTreeDirty == False`.
  `productionCommitSha` is set, `PRODUCTION_PROVENANCE` component is
  `AVAILABLE`.
- **`AMBIGUOUS`** — the artifact exists but `workingTreeDirty` is `True`
  (a real uncommitted code-path edit) or `None` (git itself failed —
  unknown is never treated as clean). `productionCommitSha` stays `null`
  — **never trusted**. `PRODUCTION_PROVENANCE` is `PARTIAL`
  (`PRODUCTION_COMMIT_AMBIGUOUS`).
- **`MISSING`** — no artifact at all (e.g. a pre-this-milestone snapshot,
  or `capture_production_provenance.py` itself failed).
  `productionCommitSha` stays `null`. `PRODUCTION_PROVENANCE` is
  `MISSING`.

Temporal staleness (an artifact left over from an earlier, different run
whose capture step ran but whose later steps never overwrote it before
this run started) is covered separately by `detect_temporal_skew()`,
which now includes `"provenance"` in `_PIPELINE_STAGES_FOR_SKEW_CHECK` —
reusing the existing, already-tested cross-artifact skew mechanism rather
than a second bespoke one.

**Shallow clones, detached HEAD, PR refs**: no special-casing needed or
added. `git rev-parse HEAD` resolves correctly in both a shallow clone
and a detached-HEAD checkout by construction (verified by adversarial
tests in `tests/edgelab/test_production_provenance.py`). This workflow's
actual trigger surface is `push`/`workflow_dispatch` only (never
`pull_request`), so PR-ref/synthetic-merge-commit ambiguity does not
apply to real runs of this workflow — documented here rather than coded
around, since adding dead branches for an unreachable trigger would be
its own honesty problem.

`PRODUCTION_PROVENANCE` is a **REQUIRED** component for `PRE_GAME_DECISION`
— a `MISSING` provenance record downgrades `completenessStatus` to
`MISSING_REQUIRED_INPUT` (never silently ignored); an `AMBIGUOUS` one
caps it at `PARTIAL_REPLAY` (same treatment `EFFECTIVE_CONFIG` already
gets). Never reconstructed after the fact from any other source — a
missing/ambiguous value stays `null`, full stop.

**Downstream unlock**: `lib.edgelab.replay.derive_replay_fidelity_from_eligibility()`
already compared `productionCommitSha` against a replay's own
`candidateModelCommitSha` to decide `LEVEL_3_CODE_PINNED` — that logic
existed since the Level 2 Historical Replay Engine milestone but could
never fire, because `productionCommitSha` was always `null`. It is now a
real, working capability: replay a same-day snapshot on the same commit
that produced it (no code changes since), and fidelity correctly
promotes to `LEVEL_3_CODE_PINNED`.

### 1a. Level-3 fidelity: what is actually proven (PR #37 maintainer review, item 2)

The original PR #37 description claimed this milestone "unlocks
`LEVEL_3_BIT_FOR_BIT`" reproducibility. That name overclaimed relative to
what the mechanism verifies, and was renamed to `LEVEL_3_CODE_PINNED`
during the maintainer review. The table below lists every prerequisite a
genuine bit-for-bit reproducibility claim would require, and this
repository's actual coverage of each one, as of this milestone:

| Prerequisite for true bit-for-bit reproducibility | Covered? | Mechanism |
|---|---|---|
| Identical commit SHA (code identity) | **Yes** | `productionCommitSha == candidateModelCommitSha`, gated on `workingTreeDirty == False` on both sides |
| Clean (non-dirty) working tree at both capture and replay time | **Yes** | `workingTreeDirty` (production side, scoped to `scripts/`/`lib/`/`config/`); `_git_working_tree_dirty()` + `-dirty` SHA suffix (candidate/replay side, whole tree) |
| Exact Python interpreter version | **No** | Not recorded anywhere in `ProvenanceRecord`, `SnapshotManifest`, or `ReplayRun` |
| Exact dependency versions (`requirements.txt`/lockfile hash) | **No** | Not recorded |
| OS / runtime container image identity | **No** | Not recorded (`runs-on: ubuntu-latest` in workflows floats to whatever image GitHub serves that day) |
| Locale / timezone of the executing process | **No** | Not recorded; `TZ='America/New_York'` is used for date derivation in `fetch-slate.yml` but not captured as an executed-environment fact |
| Other production-relevant environment variables | **No** | Only the GitHub Actions identity vars (`GITHUB_SHA`/`RUN_ID`/etc.) are captured; secrets and other env vars (e.g. `ODDS_API_KEY` presence) are not |
| Deterministic random seeds | **N/A / Yes by inspection** | The production recommendation path (`build_market_ledger.py`, `risk_gate.py`) is deterministic given its inputs — no RNG calls found in either module — so there is no seed to pin. Not independently verified by an automated determinism test in this milestone. |
| Source data ordering | **Partial** | Frozen `FROZEN_COPY` snapshot components preserve exact byte content (including order) of everything they capture; anything read live and not frozen (e.g. a market registry re-fetched rather than replayed from the snapshot) is out of scope for replay, which reads only frozen/referenced snapshot components |
| Wall-clock inputs | **Partial** | `run_candidate_replay()` has an existing guard (Level 2 milestone) against silently falling back to `datetime.now()`; not exhaustively re-audited this review |
| External API payload identity | **N/A for replay** | Replay never re-calls an external API — it reads only the snapshot's own frozen/referenced components, so this class of nondeterminism cannot enter a replay by construction |
| Effective-config completeness (every decision-driving hardcoded value) | **Partial** | See §2 — `liveConstants` + `hardcodedLogicSourceHashes` cover named constants and prove code-identity for the rest, but do not enumerate every effective value (see §2's completeness audit) |

**Conclusion**: `LEVEL_3_CODE_PINNED` is the correct, honestly-earned
label for what this repository verifies today — identical, clean-tree
code executed both the production run and the replay. It is a
**necessary but not sufficient** condition for bit-for-bit reproducible
*output*; several prerequisites above (interpreter/dependency/OS
identity in particular) remain uncaptured. A future milestone that wants
to earn a genuine `LEVEL_3_BIT_FOR_BIT` claim would need to additionally
pin and compare interpreter version, a dependency lockfile hash, and the
runtime image tag — none of which this milestone implements.

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
   `REQUIRED_MARKETS`, `F5_PRICING_VERSION_CURRENT`, `MARKET_MULTIPLIERS`,
   `REAL_MONEY_TIERS`, `TT_MARKETS`, `ML_F5_MARKETS`, `TT_MIN_EDGE_PCT`,
   `TT_MAX_BETS`, `TT_MAX_STAKE`, `TT_MAX_STAKE_PCT`,
   `ML_F5_MIN_STAKE_PCT`, `DAILY_RISK_CAP`, `TT_CRITICAL_FIELDS`,
   `TT_CRITICAL_SIDE_FIELDS` — the last three added under the PR #37
   maintainer review's item 3 completeness audit, which found
   `MARKET_MULTIPLIERS` (per-market-family staking multiplier table) had
   been left uncaptured entirely, and `TT_CRITICAL_FIELDS`/
   `TT_CRITICAL_SIDE_FIELDS` had been wrongly classified as inline/
   unrepresentable when they are real, directly introspectable module
   constants) — read via `getattr` on the live-imported module, never
   copy-pasted, so a future code change is automatically reflected with
   zero maintenance burden.
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
   confirmation gate ratio, TT PAPER-downgrade decision logic,
   postponed/live-status classification, base stake sizes by confidence
   tier) — classified honestly rather than silently absent.

### 2a. Effective-config completeness table (PR #37 maintainer review, item 3)

Every category item 3 asked about, and this repository's actual coverage:

| Category | Coverage | Where |
|---|---|---|
| Projection/pricing constants (calibration thresholds) | Structurally represented | `liveConstants.CAL_HIGH/MEDIUM/PAPER`, `THRESHOLD_HIGH/MEDIUM/PAPER` |
| F5 pricing version | Structurally represented | `liveConstants.F5_PRICING_VERSION_CURRENT` |
| Recommendation thresholds | Structurally represented | `liveConstants.THRESHOLD_HIGH/MEDIUM/PAPER` (same fields as calibration) |
| Tier assignment | Structurally represented | Same threshold/calibration constants directly gate tier assignment in `build_market_ledger.py` |
| Market eligibility (`REQUIRED_MARKETS`) | Structurally represented | `liveConstants.REQUIRED_MARKETS` |
| Lineup gates (confirmation ratio, Rule 51/52/71) | **Hash-only** | `hardcodedLogicSourceHashes["scripts/build_market_ledger.py"]`; logic itself is inline conditionals, named in `unrepresentedLogic` |
| Risk limits / portfolio caps | Structurally represented | `liveConstants.DAILY_RISK_CAP`, `TT_MAX_STAKE`, `TT_MAX_STAKE_PCT`, `ML_F5_MIN_STAKE_PCT`, `TT_MAX_BETS` |
| Staking tables (per-market multiplier) | Structurally represented (fixed this review) | `liveConstants.MARKET_MULTIPLIERS` — previously **uncaptured entirely** |
| Staking tables (base size by confidence tier) | **Uncaptured** (inline dict, not a named constant) | `unrepresentedLogic` entry; `hardcodedLogicSourceHashes["scripts/build_market_ledger.py"]` proves code identity only |
| Family-specific behavior (TT critical-evidence fields) | Structurally represented (fixed this review) | `liveConstants.TT_CRITICAL_FIELDS`, `TT_CRITICAL_SIDE_FIELDS` — previously miscategorized as unrepresentable |
| Family-specific behavior (TT PAPER-downgrade branching logic) | **Hash-only** | `hardcodedLogicSourceHashes["scripts/risk_gate.py"]`; logic named in `unrepresentedLogic` |
| Feature flags | **N/A — none exist** | Searched `scripts/build_market_ledger.py`, `scripts/risk_gate.py`, `lib/postponed_guard.py` for `ENABLE_`/`DISABLE_`/feature-flag/`os.environ.get` patterns; none found. Nothing to capture. |
| Settlement exclusions affecting recommendation generation | **Hash-only** | Postponed/live-status classification governs whether a game is excluded from the slate before recommendations are generated; `hardcodedLogicSourceHashes["lib/postponed_guard.py"]` proves code identity, classification rules themselves are inline and named in `unrepresentedLogic` |
| `config/rules.json` | Structurally represented, but **not authoritative** | `rulesConfigContents`/`rulesConfigVersion` — real file contents, but neither production script reads this file at runtime at all (see below); a duplicate-source-of-truth risk, not a completeness gap: the file exists and is captured, it just isn't what actually executes |

**Duplicate/conflicting sources of truth found**: `config/rules.json` is
captured in full but is **not read by either production script at
runtime** (confirmed by inspection — no `open()`/`json.load()` of it
anywhere in `build_market_ledger.py` or `risk_gate.py`). The values that
actually execute are the hardcoded module constants in `liveConstants`.
This is a pre-existing repository condition (not introduced by this
milestone) but is exactly the kind of "duplicate/conflicting source of
truth" item 3 asks to be identified rather than silently accepted as
complete — `rulesConfigContents` must never be read as "the production
configuration" on its own; `liveConstants` is the authoritative layer.

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
  2. Fetch raw data + archive Kalshi snapshot (unchanged; commits/rebases to main)
  3. Pre-validate + fetch remaining raw data (unchanged; commits/rebases to main)
  4. Record production provenance   -- scripts/capture_production_provenance.py (NEW)
     (positioned here -- AFTER every in-job rebase above, immediately
     before the model-execution chain begins; see §1's maintainer-review
     finding for why)
  5. Merge odds / enrich / build market ledger (unchanged)
  6. Publish authoritative slate + meta.json (unchanged)
  7. Risk gate / bet logging / closing-line capture chain (unchanged)
  8. Create immutable PRE_GAME_DECISION snapshot  -- scripts/create_snapshot.py
  9. Run automatic forward candidate replay        -- scripts/run_forward_replay.py (NEW)
  10. Commit snapshot + replay artifacts together
```

Steps 4, 8, 9 all use `continue-on-error: true` — new capture/research
infrastructure must never be able to block production slate publication
or bet placement. A failure at any of these three steps surfaces as:

- a downgraded `completenessStatus`/`gateStatus` (steps 4, 8), or
- a recorded `run_forward_replay.py` failure status (step 9),

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
fixed the Forward Replay Corpus milestone, re-audited under the PR #37
maintainer review, item 7): a ticker can have MULTIPLE `ClvQuote` rows —
one per observation checkpoint (`FIRST_DAILY`, `T_MINUS_90`, ...,
`CLOSING`) — confirmed against real data: 620 of 4,848 tickers on
2026-08-02, 462 of 4,135 on 2026-08-01, had more than one row. **The
closing-quote convention is**: the final valid tradable quote strictly
before market suspension or actual/scheduled game start, whichever is
earlier/known (`lib.edgelab.checkpoints.select_closing_quote` — a
"latest-before-first-pitch" rule, deterministic, ties broken by stable
input order). Only the row with `isClosingQuote=True` (set by this rule,
not the `checkpoint` label — real rows exist where `isClosingQuote=True`
but `checkpoint` is `T_MINUS_90`, not `"CLOSING"`, when a market
suspended early) is ever treated as the genuine closing quote. The
previous CONSUMER-side implementation (`lib.edgelab.replay`) silently
kept whichever row happened to be last in file iteration order instead
of checking this flag at all — fixed to filter strictly on
`isClosingQuote=True`: zero such rows is `NO_CLV_QUOTE_FOR_THIS_MARKET`
(honestly unresolved), more than one is reported as ambiguous and
excluded (a genuine upstream data-quality issue, never guessed at).

**Item 7's requested resolution count, re-verified this review directly
against the real, currently-committed data** (`data/edgelab/clv_quotes/`):
of the 620 multi-row tickers on 2026-08-02, **620/620 (100%) resolve
uniquely** — 0 are ambiguous. Same result for 2026-08-01's 462 multi-row
tickers: 462/462 resolve uniquely. The 1,237 tickers with **zero**
`isClosingQuote=True` rows on 2026-08-02 (5 on 2026-08-01) are a distinct,
separately-tracked category — `NO_CLV_QUOTE_FOR_THIS_MARKET`, not
ambiguity — meaning no valid pre-suspension/pre-start tradable quote was
ever captured for that market at all (never guessed at either).

**A related, defensive hardening added this review, not itself a fix for
an observed real-data defect**: `scripts/edgelab/collect_clv.py` now
re-derives `isClosingQuote` over a ticker's FULL known quote history
(existing stored rows unioned with each run's freshly projected ones),
not just that run's own freshly-projected subset. Tracing
`project_observations_to_clv_quotes()`'s checkpoint classification found
a theoretical staleness path — a backfill/reprocessing run that ingests a
previously-missing, genuinely earlier observation could reclassify an
already-`isClosingQuote=True` row out of the standard-checkpoint set and
never revisit it again — that is unreachable under ordinary real-time
(always-append) capture (confirmed: 0 tickers currently show this
symptom) but not proven impossible under this repository's own
documented backfill/recovery patterns. See
`tests/edgelab/test_collect_clv.py` for the adversarial reproduction and
fix verification.

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
| Corpus degraded 3+ consecutive dates, or any `INTEGRITY_FAILURE` date | `.github/workflows/corpus-health-check.yml` (new under the PR #37 review — daily) goes **red**; `scripts/corpus_health_report.py` stderr `ALERT:` line + report `consecutiveDegradedRuns` field | Manual investigation — run `scripts/check_snapshot_capture.py`/`scripts/run_forward_replay.py` directly against the affected dates |

**PR #37 maintainer review finding (item 10) and fix**: prior to this
review, `scripts/corpus_health_report.py` computed the
`consecutiveDegradedRuns` alert condition and printed the `ALERT:` line,
but its `main()` always called `sys.exit(0)` regardless — and no workflow
in this repository ever invoked the script at all. A dedicated check that
can neither run automatically nor ever actually fail is not a check; a
real, accumulating corpus degradation had no visible CI signal beyond a
human choosing to run the script manually. Fixed two ways: the script now
exits 1 when `consecutiveDegradedRuns >= 3` or any date shows
`INTEGRITY_FAILURE`; `.github/workflows/corpus-health-check.yml` (new)
runs it daily and, mirroring `snapshot-capture-check.yml`'s established
pattern exactly, has no `continue-on-error` on that step — so this
condition now surfaces as an actual red workflow check, not just a log
line only a human reading the run's stderr would ever see.

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
