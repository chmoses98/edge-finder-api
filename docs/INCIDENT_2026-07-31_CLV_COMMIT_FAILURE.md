# INCIDENT_2026-07-31_CLV_COMMIT_FAILURE.md

**Status:** Fixed (Production Reliability and Settlement Recovery
milestone). See `docs/POSTMORTEM_PRODUCTION_RELIABILITY_2026.md` for the
full milestone context; this document is the narrow, single-incident
record.

## Summary

`.github/workflows/clv-update.yml`'s "Commit all updates" step silently
failed to push settlement/CLV work on 2026-07-31 and 2026-08-01 (and
intermittently since at least 2026-06-16), because its `git add` list
never included `data/clv_report.json`, a file `scripts/fetch_kalshi_clv_v2.py`
unconditionally writes.

## Root cause

```
git add bets.json BET_LOG.md data/identity_audit.json data/rule71_report.json
```

`data/clv_report.json` is written by `fetch_kalshi_clv_v2.py`'s
API-fallback path but was never in this list. Once the file existed as a
tracked file (from an earlier successful commit) and was locally
re-modified by a later run, `git pull --rebase origin main` failed
outright:

```
error: cannot pull with rebase: You have unstaged changes.
```

The step — and the whole job — failed at that point, after
`clv_update.py`, the identity audit, and the Rule 71 report had already
computed correct output locally, but before any of it was pushed. The
ephemeral GitHub Actions runner is destroyed immediately after the job
ends, so that run's work was lost — not corrupted, never pushed.

## Is it deterministic?

Intermittent. It only triggers when `data/clv_report.json`'s content
actually changes between the commit step's `git add` (which excluded it)
and the following `git pull --rebase`. A run where the API-fallback path
wasn't exercised, or produced byte-identical output to what's already
committed, does not trigger it.

## Detection gap

`clv-update.yml` had no `$GITHUB_STEP_SUMMARY` output before this
incident's fix, and no artifact recorded what a run had actually read,
settled, or written. A failed "Commit all updates" step surfaced only as
a red X on the Actions tab — nothing compared expected vs. actual output,
so the loss went unnoticed for an unknown number of days before this
milestone's investigation found it.

## Data integrity

No data was corrupted. `bets.json` and `BET_LOG.md` were computed
correctly by `clv_update.py` in every affected run; they were simply
never committed. No partial writes, no duplicate records — confirmed by
inspecting the affected dates' state in `bets.json` before this
milestone's fix.

## Fix

`.github/workflows/clv-update.yml`, "Commit all updates" step:

1. Added `data/clv_report.json` to the `git add` list.
2. Replaced `git pull --rebase` with `git fetch origin main` +
   `git rebase --autostash origin/main` (the same pattern already
   reviewed and merged in `fetch-slate.yml`), so any future untracked
   local modification from any script in this job degrades to a
   stash/pop around the rebase instead of a hard failure.

## Safeguards added (this milestone, broader than this one incident)

- Shared `edge-finder-ledger-writer` concurrency group across
  `fetch-slate.yml`, `clv-update.yml`, and `lineup-recheck.yml`.
- Every `bets.json` writer migrated to `lib/atomic_json.write_json_atomic()`.
- `clv-update.yml` now writes a `$GITHUB_STEP_SUMMARY` on every run
  reporting stage reached, records processed, backlog before/after,
  whether files were mutated and pushed, and rerun safety — see
  `docs/POSTMORTEM_PRODUCTION_RELIABILITY_2026.md` §7 and
  `OPERATIONAL_RUNBOOK.md` §8.

## Regression tests

- `tests/test_workflow_concurrency_groups.py` — confirms the shared
  concurrency group.
- `tests/test_clv_update_run_summary.py`,
  `tests/test_clv_update_workflow_summary.py` — confirm the new
  observability output and that a future silent-failure of this shape
  would be visible in the workflow summary.
