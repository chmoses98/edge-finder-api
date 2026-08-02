# POSTMORTEM_PRODUCTION_RELIABILITY_2026.md

**Milestone:** Production Reliability and Settlement Recovery
**Scope:** Restore confidence in the production slate, bet-ledger,
settlement, and CLV workflows before any additional research or
model-development work. No probability calculations, recommendation
thresholds, betting tiers, stake sizing, market selection, or F5 tie
pricing were changed by this milestone (F5 tie pricing is a documented,
separate future milestone).

---

## 1. Timeline

| Date (2026) | Event |
|---|---|
| ~06-07 through 07-24 | Baseline period. `fetch-slate.yml`, `clv-update.yml`, and `lineup-recheck.yml` operate with no shared concurrency group and no atomic writes on `bets.json`. |
| 07-25 – 07-26 | **Incident A** (fetch-slate/bet-logging): `validate_bet_logging.py` hard-fails on live/final games that `write_pending_bets.py` had already, correctly, excluded from that run. The job dies before the "Write meta and commit authoritative slate" step runs, so `data/meta.json` goes stale — while `data/fetch_status.json` (committed unconditionally, earlier in the same job) still reports success. Detection gap: nothing compares the two, so the stale state is invisible to any automated check. |
| ~07-29 | Incident A's root cause is fixed and merged to `main` — `validate_bet_logging.py`'s handling of live/final games is corrected, and the authoritative-slate publish step is moved to run immediately after slate protection, before the optional execution/logging chain, so a downstream failure there can no longer leave `meta.json` stale. (Confirmed via `fetch-slate.yml`'s own code comments, dated to this incident, and via GitHub Actions run history: every `fetch-slate.yml` run since 2026-07-30 shows `success`.) |
| since ~06-16, confirmed active 07-31 and 08-01 | **Incident B** (clv-update/CLV-report commit failure, previously undocumented): `clv-update.yml`'s "Commit all updates" step never `git add`ed `data/clv_report.json`. Once that file existed as a tracked file from an earlier successful run, any run that re-modified it hit `git pull --rebase origin main` failing outright ("You have unstaged changes"), and the whole step failed — silently discarding that day's settlement/CLV work when the ephemeral runner was torn down. Detection gap: no workflow summary reported what had (or hadn't) been written, so this went unnoticed for weeks. |
| 07-30 – 08-01 (this milestone begins) | Architecture review identifies both the confirmed-fixed Incident A and flags Incident B as still-open; ~109-119 pending/unsettled bet records exist in `bets.json` with no prior classification of *why* each is unsettled. |
| This milestone | Incident B fixed (§2). Every `bets.json`/CLV-report writer migrated to atomic writes. Shared concurrency group added across the three ledger-writing workflows. All 119 non-terminal bets classified by evidence-backed category (§4). Backlog remediation tooling built, dry-run only (§5). PR-triggered CI added. `config/rules.json` structural validation added. Storage retention policy added for `data/kalshi_registry_snapshots/` (a second, independently-discovered bug in an existing-but-broken cleanup step, §6). Workflow summary observability added to `clv-update.yml`. |

---

## 2. Root cause (Incident B — the fix this milestone actually made to production settlement)

`scripts/fetch_kalshi_clv_v2.py`'s API-fallback path unconditionally writes
`data/clv_report.json`. `clv-update.yml`'s "Commit all updates" step's
`git add` list never included that file:

```
git add bets.json BET_LOG.md data/identity_audit.json data/rule71_report.json
```

Once `data/clv_report.json` existed as a tracked file (from an earlier
successful commit) and got locally re-modified by a later run,
`git pull --rebase origin main` refused to proceed ("You have unstaged
changes"), and the entire step failed. Nothing was corrupted — the
failure happened before the push — but the ephemeral GitHub Actions
runner is torn down immediately after, so that run's settlement/CLV work
(everything `clv_update.py`, the identity audit, and the Rule 71 report
had just computed) was simply never pushed to `main`. The next scheduled
run started from the same stale `bets.json` and could hit the identical
failure again.

Confirmed via GitHub Actions run history: this recurred intermittently
since at least 2026-06-16, and was actively failing on both 2026-07-31
and 2026-08-01 (the two most recent days at the time of investigation).

**Is it deterministic or intermittent?** Intermittent — it only triggers
when `data/clv_report.json`'s content actually changes between the
commit-step's `git add` (which never included it) and the subsequent
`git pull --rebase`. A run where the API-fallback path in
`fetch_kalshi_clv_v2.py` wasn't exercised, or produced byte-identical
output, would not trigger it.

**Was any data corrupted?** No. `bets.json` and `BET_LOG.md` were
computed correctly in every affected run; they were simply never
committed. No partial writes, no duplicate records, no corrupted files —
confirmed by inspecting the affected dates' `bets.json` state before and
after this milestone's fix.

**Fix applied** (`.github/workflows/clv-update.yml`):
1. Added `data/clv_report.json` to the step's `git add` list, so it is
   actually committed instead of left as an untracked/modified local
   change.
2. Switched from `git pull --rebase` to `git fetch` + `git rebase
   --autostash origin/main`, the same pattern already reviewed and
   merged in `fetch-slate.yml` — so any *future* untracked modification
   from any script in this job degrades to a stash/pop around the
   rebase instead of a hard failure.

---

## 3. Impact and affected records

- **Affected workflow:** `clv-update.yml` only. `fetch-slate.yml`'s slate
  publication was unaffected (Incident A was already fixed by this
  point).
- **Confirmed-affected dates:** 2026-07-31, 2026-08-01 (both directly
  observed as failed runs in the GitHub Actions history at the start of
  this milestone). Intermittent recurrence probable back to 2026-06-16
  based on `data/clv_report.json`'s own commit history, but not every
  date in that range is confirmed affected — see §4 for how each
  individual pending bet was actually classified, rather than assuming
  the whole range is uniformly Incident-B-caused.
- **No duplicated, skipped, or corrupted ledger records** were found.
  `find_duplicates()` (see `lib/bet_backlog_classifier.py`) returns zero
  true content-duplicates across the entire 519-entry `bets.json` — every
  apparent same-ticker repeat is a legitimate multi-tranche bet (verified
  against the real 2026-06-19 SD@TEX F5_ML_Away example, where three
  separate tranches on the same ticker are correctly three separate
  records).

---

## 4. Pending-bet classification (do not assume all pending bets share one cause)

119 of `bets.json`'s 519 records were non-terminal (no `WIN`/`LOSS`/
`PUSH`/`VOID`/`NO_ACTION` result) at the start of this milestone. Each was
classified into exactly one of 8 evidence-backed categories by
`lib/bet_backlog_classifier.py` (dry-run output reproduced verbatim,
`scripts/remediate_bet_backlog.py`, no `--execute`):

| Category | Count | What the evidence shows |
|---|---|---|
| `requires_manual_review` | 44 | No automated evidence either way — genuinely needs a human to look at game logs/schedule data this repo doesn't have access to. |
| `unsupported_market_family` | 35 | NRFI/YRFI bets — `clv_update.py`'s own `determine_result()` permanently routes these to manual settlement (never auto-graded from a score); this is a structural, intentional design, not a bug (see §"lessons" below). |
| `missing_source_data` | 30 | Predates `clv-update.yml`'s existence, or falls on a date this repo has no local settlement-evidence source for (no live network access, no local post-game score archive in this environment). |
| `legitimately_pending` | 7 | Within the legitimate-pending window (recent enough that non-settlement is expected, not a failure). |
| `pipeline_failure` | 3 | Falls on a date matching a confirmed-failed `clv-update.yml` run (Incident B or an earlier recurrence). |
| `settleable_from_evidence` | 0 | None found — this environment has no live network access or local post-game score archive to derive a settlement result from, so this category is (correctly) empty rather than fabricated. |
| `malformed_record` | 0 | None found. |
| `duplicate` | 0 | None found (see §3). |

**Auto-safe remediation changes proposed: 0.** The remediation tool
(`scripts/remediate_bet_backlog.py`) was built, tested, and run for real
against the actual repository data — but its `autoSafeChanges` list is
correctly empty, because settling any of these 119 records requires real
game-outcome evidence this environment cannot access. Fabricating a
settlement result to make the backlog number smaller was explicitly
rejected as unsafe (see the milestone's own "do not fabricate settlement
results" constraint). Manual review or a genuine live-data source is
required to close out the remaining 112 non-`legitimately_pending`
records.

---

## 5. Remediation tooling built (dry-run only, not executed against real data)

`scripts/remediate_bet_backlog.py` / `lib/bet_backlog_classifier.py`:
- Dry-run by default; `--execute` only ever applies changes already
  listed in a plan's `autoSafeChanges` (currently always empty — see
  §4).
- Produces a machine-readable plan (`data/bet_backlog_remediation_plan.json`,
  committed as part of this milestone) before touching anything.
- Deterministic: identical inputs always produce an identical plan
  (tested).
- On `--execute`, writes a timestamped backup to `data/backups/` before
  any mutation, and re-validates each planned change's `before` value
  against current state before applying it (skips silently-changed
  records rather than blindly overwriting).
- 27 tests in `tests/test_bet_backlog_classifier.py`, including a
  real-data regression guard (`test_no_duplicates_in_real_bets_json`,
  `test_build_plan_matches_real_ledger_classification_counts`) that would
  fail if a future change to `bets.json` introduced a genuine duplicate
  or shifted the classification counts unexpectedly.

---

## 6. A second, independently discovered bug: broken snapshot retention

While auditing storage growth (`data/kalshi_registry_snapshots/`, 126MB /
370 files spanning back to 2026-06-08), this milestone found that
`capture-snapshots-scheduled.yml` already contained a "clean up
timestamped snapshots older than 3 days" step — but it used `find
-mtime +3 -delete`, which checks the **filesystem's** modification time.
`actions/checkout` performs a fresh checkout on every job run, and git
checkouts do not preserve historical commit timestamps: every file's
mtime becomes "now" (checkout time), not the date embedded in its own
filename. `-mtime +3` could therefore never match anything on a real
runner — this step had silently pruned nothing since it was written,
which is exactly consistent with the 126MB backlog observed.

**Fix:** `scripts/prune_kalshi_snapshots.py` / `lib/snapshot_retention.py`
parse the retention window from the date embedded in each filename
instead of relying on filesystem mtime, and the workflow step now calls
this script. The originally-intended 3-day window is also widened to 21
days — 3 days is demonstrably too short given this milestone's own
finding that real settlement/CLV failures went unnoticed for weeks; a
snapshot pruned before anyone notices a failed run can't be used to
recover it. Dated (non-timestamped) per-slate-date snapshots are kept
forever, unchanged, since `scripts/backfill_market_identity.py` and
`scripts/clv_from_snapshot.py` still read them.

Dry-run against the real repository (`data/kalshi_snapshot_retention_plan.json`,
committed): 166 of 370 files eligible for pruning, ~55.97MB (of 126MB)
reclaimable, 53 dated files and 149 recent timestamped files kept. **Not
executed** — deleting real historical Kalshi price data is irreversible
(no live network access exists to re-fetch it), so per this milestone's
own "only interrupt for irreversible data loss" instruction, the dry-run
plan and tooling are the deliverable; actual execution is left for a
human decision.

---

## 7. Safeguards added this milestone

- **Concurrency:** `fetch-slate.yml`, `clv-update.yml`, and
  `lineup-recheck.yml` — the three workflows that can write `bets.json`,
  `BET_LOG.md`, `data/slate.json`, or `data/slates/<date>/authoritative.json`
  — now share one concurrency group (`edge-finder-ledger-writer`,
  `cancel-in-progress: false`), closing a cross-workflow race that
  `fetch-slate.yml`'s own prior review comments had flagged and deferred.
  High-frequency, disjoint-path snapshot workflows are deliberately not
  part of this group (see `tests/test_workflow_concurrency_groups.py`).
- **Atomic writes:** every `bets.json` writer (`clv_update.py`,
  `write_pending_bets.py`, `clv_from_snapshot.py`, `fetch_kalshi_clv_v2.py`,
  `backfill_market_identity.py`, `log_manual_bet.py`, `log_session_bets.py`,
  `capture_closing_lines.py` settle mode) now uses
  `lib/atomic_json.write_json_atomic()` (temp file + `os.replace`) instead
  of a plain `open()`+`json.dump()`. A crash or killed job mid-write can
  no longer leave a truncated or partially-written ledger file on disk.
- **PR-triggered CI** (`.github/workflows/pr-ci.yml`): before this
  milestone, none of this repository's workflows ran on `pull_request` —
  3000+ tests existed and ran only when a human remembered to run them
  locally. This workflow gates merges on the full deterministic suite,
  network-free, secrets-free, with no commit step (`permissions:
  contents: read`).
- **Configuration validation** (`lib/rules_config.py`): `config/rules.json`
  is now structurally validated at load time by the one call site whose
  output actually gates production behavior
  (`lib.edgelab.recommendations.load_model_covered_series`) — fails
  loudly on a missing required section or an out-of-range value, while
  remaining backward-compatible with every genuinely optional field.
  Validated clean against the real, current `config/rules.json`; no rule
  value was read, altered, or reinterpreted.
- **Storage retention:** see §6.
- **Observability:** `clv-update.yml` now writes a `$GITHUB_STEP_SUMMARY`
  on every run (success, partial failure, or hard failure) reporting: the
  stage reached, a failure category, records read/settled/pending,
  whether files were mutated and pushed, the pending-bet backlog count
  before and after, and an explicit statement of rerun safety. A
  successful run that processes zero records now explains why (via
  `data/clv_update_run_summary.json`'s `zeroRecordsReason`).

---

## 8. Remaining unresolved records / risks

- **112 of 119 non-terminal bets remain unsettled** after this milestone
  (7 are `legitimately_pending` and expected to resolve normally). Closing
  these out requires either a live game-outcome data source this
  environment doesn't have, or manual review — see §4.
- **`clv_update.py` processes only one date per run** (the CLI argument,
  or "yesterday ET" by default) with no backfill sweep across missed
  dates. This is a structural gap distinct from Incident B — even with
  Incident B fixed, a day where the workflow simply never ran (holiday,
  infra outage, etc.) will never be automatically revisited. Flagged, not
  fixed, in this milestone.
- **`data/bets.json`** (the stale, 92-entry duplicate ledger, last written
  2026-06-18) remains unreconciled, per the long-standing "do not delete
  historical data without an explicit decision" guidance. Also
  unresolved: `scripts/log_session_bets.py`'s `BETS_PATH` constant still
  points at this stale duplicate file rather than the real root
  `bets.json` — a genuine, pre-existing behavioral bug, flagged for a
  future milestone rather than fixed here (too invasive/uncertain-intent
  for this pass).
- **The F5 tie-pricing bug** (three-way F5 market pricing) is confirmed
  still present and deliberately **not** touched by this milestone — see
  the separate, dedicated future milestone this repository has already
  scoped for it.
- **`data/kalshi_registry_snapshots/` retention** is dry-run only (§6) —
  the real 126MB backlog is not yet reduced; only the ongoing-growth bug
  is fixed and a safe cleanup path exists.

---

## 9. Lessons for future workflow design

1. **A workflow's own `git add` list must be kept in sync with every
   script it runs.** Incident B existed because a script's output file
   was added to the repo (via an earlier successful run) without anyone
   updating the commit step's file list. Prefer `git add -u` scoped to a
   known directory, or an explicit test asserting the `git add` list
   covers every file path any step in the job can write, over a
   hand-maintained literal list that silently drifts.
2. **`find -mtime` against a fresh CI checkout is never meaningful.**
   Any future cleanup/retention logic in this repository must parse
   dates from data (filenames, file contents, git history) rather than
   filesystem timestamps, which a checkout resets on every run.
3. **A workflow that reports "success" is not the same as a workflow that
   did anything.** Both incidents in this postmortem were invisible
   because nothing compared what a run was supposed to produce against
   what it actually produced. The observability work in this milestone
   (§7) is a direct response — every future workflow touching a
   system-of-record file should emit a step summary answering "what
   stage did this reach, what did it read/write/skip, and is a rerun
   safe" by default, not as an afterthought.
4. **Test coverage does not equal reliability if nothing runs it before
   merge.** 3000+ tests existed in this repository with zero PR-triggered
   CI. A regression could sit undetected indefinitely if no human
   happened to run the suite locally before merging.
5. **"No fix needed" is a real, valid finding — and still needs a
   regression test.** Several concerns this milestone investigated
   (idempotent settlement reruns, multi-tranche same-ticker settlement,
   manual-bet duplicate detection) were already correct. Rather than
   reflexively "fixing" already-correct code, this milestone documented
   the finding and added a regression test locking in the existing
   correct behavior — the safer response to "I checked and it's fine" is
   proof it stays fine, not a defensive rewrite.

---

## 10. Recommendation for the separate F5 three-way pricing milestone

Not attempted here by design. Recommend treating it as its own,
narrowly-scoped milestone, now that this one has restored the observability
and testing infrastructure (PR CI, config validation, workflow summaries)
that milestone will benefit from having in place — any pricing-logic
change to `build_market_ledger.py`/`lib/f5_settlement.py` will now run
through `pr-ci.yml` before merge, and any config value it touches in
`config/rules.json` will be structurally validated at load time.
