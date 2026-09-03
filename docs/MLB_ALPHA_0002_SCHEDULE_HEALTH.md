# MLB-ALPHA-0002 schedule health and the accumulation clock

Operational, not statistical. Nothing here says anything about whether any
strategy works; it answers only whether the prospective collector produces
a dense enough persisted panel to start a formal accumulation clock.

## Frozen definitions

| | |
|---|---|
| `scheduleVersion` | `V2_OFFSET_3_13_23_33_43_53` (frozen 2026-09-03T18:04:09Z) |
| `expectedCadenceMinutes` | 10 |
| capture window | 15:00–23:59 and 00:00–04:59 UTC (84 slots/full day) |
| `healthGateVersion` | `INFRA_GATES_V1_2026_09_03` |

Gates, frozen — **not to be moved after seeing results**. Moving a
threshold to fit an outcome is how an infrastructure check quietly becomes
a rubber stamp:

- persisted schedule coverage ≥ **90 %**
- median capture gap ≤ **15 min**
- p90 capture gap ≤ **25 min**
- no unexplained in-window gap > **45 min**

## Why coverage is measured from the persisted corpus

A run that fires, captures, and then fails to persist is not coverage —
that exact failure already happened once (run 33693708429 collected 7,081
quotes and discarded all of them behind a green check). So a slot counts
as covered only when a capture manifest for it exists in the durable
corpus.

Manual dispatches are excluded from the coverage rate entirely. Counting
them would flatter the cadence with runs a human triggered. That exclusion
needs the trigger recorded on the row itself, so `prospective_capture.py`
stamps `triggerEvent` (and `githubRunId`) on every manifest. Manifests
written before that stamp are reported as `unknownTriggerCaptures` and are
**never** silently counted as scheduled.

Expected slots are constructed from the cron spec, never inferred from
observed runs — a missed slot has no run to infer from, which is precisely
what is being counted.

## Schedule V1 result (2026-09-02T23:08:44Z → 2026-09-03T18:04:09Z)

Schedule V1 was `*/10`, i.e. the round `:00/:10/:20` minutes that every
other repository also schedules on.

| metric | measured | gate | |
|---|---:|---:|---|
| expected slots | 54 | — | |
| scheduled firings | 3 | — | |
| persisted schedule coverage | **5.6 %** | ≥ 90 % | **FAIL** |
| median capture gap | **30.0 min** | ≤ 15 | **FAIL** |
| p90 capture gap | **149.1 min** | ≤ 25 | **FAIL** |
| worst in-window gap | **110.3 min** | ≤ 45 | **FAIL** |

The gap figures are measured across *all* captures including seven manual
dispatches, so they flatter the schedule; scheduled-only would be worse.

Worst single observation: the 04:50Z slot fired at **07:54Z — 184 minutes
late**, outside the capture window entirely.

**Accumulation clock: NOT started.** Starting it here would backdate Day 0
into a period known to be sparse.

## Clock bookkeeping rules (corrected 2026-09-03)

The first implementation of the health check had six bookkeeping defects.
The capture data was never affected — these are measurement and clock-state
rules only.

1. **Sticky Day 0.** `accumulationStartUtc` is written **once**, on the
   first eligible pass, and preserved verbatim thereafter. The original
   recomputed it as "now" every cycle, which would have marched Day 0
   forward forever and defeated the clock entirely.
2. **Real elapsed time.** `healthyDaysElapsed` is measured from the frozen
   start to the newest evidence. It was previously hardcoded — both
   branches of the ternary returned `0.0`. Also reports
   `healthyHoursElapsed` and `calendarDatesTouchedSinceStart`.
3. **Schedule-only gates.** The four gates read **scheduled captures
   only**. A human dispatching runs by hand must not be able to make
   GitHub scheduling look healthy. `allCaptureGapMetrics` is reported
   alongside for data density and feeds no gate.
4. **One-to-one slot matching.** `SLOT_MATCH_SECONDS` equals the cadence,
   so a capture landing exactly on slot N is also exactly 600 s after slot
   N−1; the naive "does any capture fall within 600 s" test credited both.
   Each capture now claims at most one unmatched slot — the most recent
   eligible one — so `coveredSlots <= scheduledCaptures` always holds.
5. **No backdated V2.** `SCHEDULE_V2_EFFECTIVE_FROM_UTC` =
   `2026-09-03T18:22:12Z`, the commit that put the V2 cron on the default
   branch. V2 is never scored against slots that could not have fired.
   Historical V1 reporting is separate and immutable.
6. **Delay metrics in the artifact.** `scheduledDelayMedian/P90/Max` plus
   per-slot `expectedSlotUtc` / `captureUtc` / `delaySeconds` /
   `githubRunId`, derived from the one-to-one assignment, so scheduling
   can be audited without replaying GitHub history later.

### Minimum evidence before Day 0

`MIN_EXPECTED_SLOTS_FOR_ACCUMULATION_START = 84`, and the evaluated
interval must contain **one complete contiguous operating window**
(15:03Z → next-day 04:53Z). A lucky partial day may be reported but cannot
start the clock. This defines the minimum sample needed to judge the gates
honestly; **the four gate thresholds are unchanged**.

### Clock persistence

The artifact is regenerated every cycle, so it reads the prior artifact
before writing. Once started it preserves `accumulationStartUtc`,
`scheduleVersionAtStart` and `healthGateVersionAtStart`. A later
infrastructure failure is recorded as `currentHealthGatePassed: false` and
`accumulationHealthWarning: true` — it does **not** rewrite historical
Day 0. Any clock-invalidation policy is a separate future decision and is
deliberately not invented here.

## Schedule V2 — one frozen offset, evaluated on its own evidence

GitHub delays scheduled Actions under load, and round-minute slots are the
most contended. V2 moves the minute offset off the round boundary:

```
3,13,23,33,43,53 15-23 * * *
3,13,23,33,43,53 0-4  * * *
```

This is frozen **once**. It is deliberately not re-tuned after seeing
results — trying offsets until one looks good fits noise, not signal. If
coverage does not materially improve under V2, the answer is the fallback
below, not another offset.

## First V2 window eligible for judgment

V2 became live at `2026-09-03T18:22:12Z`, after that day's window had
already opened at 15:03Z. So the Sep 3 window is incomplete under V2 and
cannot be judged. The first complete V2 operating window is:

**`2026-09-04T15:03Z → 2026-09-05T04:53Z` — 84 expected slots.**

The accumulation clock stays stopped until that window completes and is
evaluated. Do not manually dispatch the collector to manufacture health;
let the window happen naturally.

## Fallback design (NOT implemented — only if V2 also fails)

Implement only if independent GitHub schedule events cannot reliably
produce ≈10-minute sampling. The smallest repo-native option:

**A less-frequent scheduled job that stays alive for a bounded period and
performs several internally timed capture cycles.**

- one scheduled job per hour (a cadence GitHub honours far more reliably
  than six per hour), running ~50 minutes
- inside it, a loop invoking the **same** `prospective_capture.py` every
  10 minutes — same research branch, same append-only provenance
- each cycle gets its own `runId` / `capturedAt`, and each cycle is
  persistence-verified before the next begins, so a mid-job failure can
  never produce a dangling reference chain
- `concurrency.cancel-in-progress: false` plus the existing concurrency
  group prevents overlapping jobs
- bounded well below the GitHub job timeout (currently 15 min → would rise
  to ~55 with a hard cap)
- no orders, no production dependency, Odds API bounded by the same
  2-credits-per-cycle measurement

Costs to weigh before implementing: one long-running job burns more
Actions minutes than six short ones, and a crash mid-job loses the
remainder of that hour rather than a single slot.

**No paid infrastructure without explicit authorization.**

## Accumulation plan, once the gate passes

`FORMAL DAY 0 = accumulationStartUtc`, set to the first timestamp after
which the pipeline demonstrably entered the healthy regime — never
backdated into a sparse period.

- **after 7 healthy days** — infrastructure/coverage review only: schedule
  coverage, market coverage, signal counts, queue-evaluable rate,
  external-odds completeness, lineup-event count, no integrity
  regression. **Alpha is not judged at 7 days.**
- **after 14 healthy days** — the corpus becomes eligible for the first
  open-ended discovery session.

Those 14 days are **development data**. Any strategy discovered from them
must be frozen with a rule hash and an exact economic/execution spec, then
prove itself on data collected *after* discovery. The discovery corpus can
never validate a rule invented from that same corpus.

Already-frozen candidates (C01-F5REV, C02-OFI, D01-SHARPLAG, I01-LINEUP,
C03-BOOKIMB, and MLB-ALPHA-0001's C01-PIT) continue accumulating toward
their own predeclared checkpoints independently and are untouched by any
of this. C01-F5REV retains ≥60 episodes / ≥40 independent games / ≥12
dates before its first sparse prospective checkpoint.

## D01 — unchanged

The existing limitation stands: 10-minute Pinnacle snapshots cannot prove
or disprove a 1–5 minute Pinnacle → Kalshi lag. D01 remains
PROSPECTIVE_ONLY / HISTORICAL-PILOT-INCONCLUSIVE. A costed proposal for a
higher-frequency forward experiment is due once schedule reliability and a
representative full day's actual Odds API burn are both established — and
a representative full day does not yet exist.
