# EdgeLab canonical closing-quote policy

Companion to `docs/EDGELAB_CLV_SIGN_AUDIT.md` (sign) and
`docs/EDGELAB_CLV_PRICE_UNIT_AUDIT.md` (scale). Those fixed *what the
number means*. This one fixes *which quote the number is computed
against*, which turned out to be the larger problem.

## The defect

Of the 94 wagers from 2026-09-11 onward carrying a CLV value, **57 were
scored against a quote labelled `FIRST_DAILY`**. Measured against each
market's own scheduled start:

| closing-quote checkpoint | n | min | median | max (minutes before start) |
|---|---|---|---|---|
| `FIRST_DAILY` | 57 | 89.2 | **863.2** | 1087.2 |
| `T_MINUS_15` | 12 | 10.6 | 17.6 | 21.3 |
| `T_MINUS_90` | 11 | 82.6 | 89.2 | 96.4 |
| `T_MINUS_30` | 5 | 26.3 | 26.9 | 29.2 |
| `T_MINUS_5` | 4 | 2.9 | 5.6 | 7.8 |
| `INTERMEDIATE` | 3 | 43.3 | 45.7 | 75.7 |
| `T_MINUS_60` | 2 | 63.0 | 63.0 | 63.0 |

A median of **863 minutes — 14.4 hours — before first pitch**. Across all
94, only 7 (7.4%) fall within 15 minutes of start and 21 (22.3%) within
30. That is not closing-line value. It is close to an opening line, and
it was being reported as "Average CLV" without qualification.

## What was NOT wrong

`lib/edgelab/checkpoints.py::select_closing_quote()` is correct and was
never the bug. It already selects the **latest** valid pre-start quote by
timestamp, ignores the checkpoint label entirely, refuses post-start
quotes, and returns `None` rather than guessing when start time is
unknown. Measured on the same 94 rows: **zero selected quotes are
post-start.**

## The actual root cause: capture coverage

**49 of the 57 `FIRST_DAILY` rows have exactly one archived quote for
their ticker.** There was no later quote to select. The remaining 8 had
2-4 quotes, none later and still pre-start.

The upstream cause is scheduled-run delivery, not code. The capture
workflows request a dense cadence:

| workflow | requested cron | slots/day |
|---|---|---|
| `clv_capture.yml` | `*/10` over 13h | ~78 |
| `capture-snapshots-scheduled.yml` | `0,30` over 14h | ~28 |
| `edgelab-capture.yml` | `20,50` over 14h | ~28 |

Actual timestamped snapshots on disk: **5-7 per day, every day, for 13
consecutive days** (2026-09-08 → 2026-09-20). The delivered runs are also
displaced from their slots (observed 18:39, 21:23, 23:24, 01:29, 02:42)
and cluster away from the 17:00-23:00 UTC window when MLB games actually
start. GitHub drops and delays scheduled workflows under load; that is
the constraint, and raising the cron frequency does not fix it.

Ruled out as causes:
- **Not ingestion loss.** Every timestamped snapshot on disk is present in
  the observations partition (the undated `kalshi_search_<date>.json`
  base file is a duplicate of the latest and is correctly skipped).
- **Not retention.** Pruning keeps 21 days; the affected window is 13.
- **Not the finalizer.** No post-start quote was ever selected.
- **Not missing start times.** All 94 rows had a resolvable
  `scheduledStart`.

## The policy

A closing quote is the **latest valid, executable, strictly-pre-start
quote for the exact market ticker**, chosen by timestamp. Checkpoint
labels describe provenance only and never determine selection.

Every result additionally carries a **coverage class**, so that "we had a
quote" and "we had closing-line evidence" can never again be the same
statement:

| class | meaning |
|---|---|
| `TRUE_CLOSE` | latest valid executable pre-start quote, within the near-close threshold |
| `PRE_CLOSE` | valid executable pre-start quote, but older than the threshold |
| `NO_VALID_PRESTART_QUOTE` | nothing valid and executable before start |

### The near-close threshold: 30 minutes

Chosen from the repository's own capture architecture, not from the data
we wish we had. The **coarsest scheduled capture cadence is 30 minutes**
(`capture-snapshots-scheduled.yml`, `edgelab-capture.yml`). A scheduler
that delivers its requested runs therefore guarantees at least one
observation within 30 minutes of any start time. 30 minutes is the
tightest bound the intended architecture can actually promise.

It is deliberately not tighter. A 5- or 15-minute threshold would be
aspirational: it depends on `clv_capture.yml`'s `*/10` cron being
delivered, which measurement shows it is not.

**The exact distance is always stored** (`secondsBeforeStart`), so the
threshold is a reporting convention rather than a lossy bucket. Any
consumer can re-bucket at 5 or 15 minutes without recomputation, and the
audit trail does not depend on this choice being right forever.

### Executability

The quote must be executable **for the side actually bought**: YES pays
the ask, NO pays the NO-side ask. Never a bid substituted for an ask,
never a midpoint, never the opposite side. Enforced by
`lib.edgelab.clv_convention.executable_price`, which also requires a
declared `priceUnit` and fails closed without one.

### Fail-closed cases

- Start time unknown or unresolvable → no CLV, reason recorded.
- No pre-start quote → `NO_VALID_PRESTART_QUOTE`, no fabricated number.
- Undeclared price unit → `CLOSING_QUOTE_PRICE_UNIT_UNDECLARED`.
- Market suspended at capture → not a valid candidate.

A missing closing quote is reported as missing. It is never replaced by
the nearest available number.

---

# Appendix: running PR CI on an agent-created PR

## What is established

PR #230 received **no check run at any point in its life** — `total_count: 0`,
combined status `pending`, zero workflow runs on its branch, both when opened
(04:29Z) and after a later push. The run-number sequence confirms it: runs
jump from **373** (2026-09-20 19:55Z, an unrelated branch) straight to **375**.
There is no run 374. Actions itself was healthy throughout — scheduled
workflows kept running on `main` the whole time.

## What is NOT established — an earlier claim of mine was wrong

I initially attributed this to GitHub not triggering workflows from events
raised with a GitHub App installation token. **The evidence contradicts that.**
PR #231 was created through the same app, by the same actor, against the same
base, roughly 100 minutes later — and received a `pull_request` run
(**376**) immediately.

So the app-token theory does not survive contact with the data, and the true
cause of #230's non-delivery **remains unexplained**. It presents as a one-off
event-delivery failure. Nothing in `pr-ci.yml`'s triggers, branch filters,
path filters or permissions differs between the two PRs; the workflow has no
path filter at all and `on: pull_request` is unconditional.

Do not treat "it was the app token" as the answer. It is not.

## Why the remediation still stands

The fix is not aimed at the unexplained cause — it removes the dependency on
event delivery altogether. `pr-ci.yml` now also accepts `workflow_dispatch`
with an optional `ref`:

```
workflow_id: pr-ci.yml
ref:         <the PR branch>
inputs:      {}            # or {"ref": "<branch or SHA>"}
```

Verified by real invocation on this branch (run **375**, `event:
workflow_dispatch`). Before the change this call was impossible — the
workflow had no dispatch entry point, so a PR whose `pull_request` event
never arrived could only be merged unevaluated or have an empty commit
pushed to bait the trigger.

Same job, same deselects, same read-only permissions. Branch protection is
unchanged: a dispatch run is not a `pull_request` check run and cannot
satisfy a required status check by itself. It supplies evidence, not an
override.

**For future sessions:** if a PR shows no check run a few minutes after
opening, dispatch `pr-ci.yml` against its branch rather than assuming the
absence is benign or pushing a commit to force it.
