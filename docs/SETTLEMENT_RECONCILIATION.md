# Automatic Settlement Catch-Up (Destination-Side Reconciliation)

**Goal:** be able to ask *"how did we do yesterday?"* and read a finished
canonical ledger/report, without anyone manually rebuilding yesterday's wagers.

---

## The gap this closes

`EdgeLab Postgame Settlement` runs once a night, chained off
`Update CLV (Post-Slate Review)` (~06:00 UTC). Any wager that reaches the
canonical ledger `data/edgelab/bets/bets.jsonl` **after** that pass — exactly
what happens when the Kalshi bet router imports a wager later in the evening —
used to sit ungraded until a human noticed and dispatched a settlement rerun by
hand.

The fix is **destination-side**. It works regardless of which entry surface
wrote the wager (router, `Import Manual Bet Batch`, `Record Placed Bet`,
`log_bet.py`), and it does **not** depend on any of them remembering to fire a
"now settle this" event. `chmoses98/kalshi-bet-router` was deliberately left
unchanged.

---

## What causes reconciliation to occur

Exactly three triggers, all in
[`.github/workflows/edgelab-settlement-reconcile.yml`](../.github/workflows/edgelab-settlement-reconcile.yml):

| # | Trigger | Fires when | Dates it reconciles |
|---|---------|-----------|---------------------|
| 1 | **`push`** on `main`, `paths: data/edgelab/bets/bets.jsonl` | the canonical ledger changes on main — a router import, a manual batch, a single recorded bet | derived from the real `before..after` diff of that one file (`--changed-from ${{ github.event.before }}`) |
| 2 | **`schedule`** `0 13 * * *` and `0 3 * * *` UTC | twice a day, after the nightly chain has had its chance | every date in a 5-day lookback window that still holds an ungraded real wager |
| 3 | **`workflow_dispatch`** | a human asks | an explicit `date`, or an inclusive `start_date`/`end_date` range, or the lookback sweep; `dry_run: true` computes everything, writes only the receipt (never settlements, the ledger, the rolling status, or a commit) |

Trigger 1 handles *late* imports. Trigger 2 handles the case trigger 1
structurally **cannot**: a bet imported **before** its game ends. Nothing about
that import is late — the result is simply not knowable yet, and no push is
coming later to say it now is. The sweep keeps looking until it is.

---

## Lifecycle of one reconciliation run

```
  ledger change on main ─┐
  scheduled sweep ───────┼─→ resolve affected dates  (lib/edgelab/settlement_reconciliation.py, pure)
  manual dispatch ───────┘            │
                                      ▼
                        for each affected date, oldest first:
                          1. ingest_market_observations.py --date D   (Market/Game rows from the
                                                                       already-archived Kalshi
                                                                       snapshots; ZERO API calls)
                          2. repair_game_identity.py      --date D    (resolve mlbGamePk)
                          3. settle_date(D)                           ← THE canonical settler,
                                                                        imported from
                                                                        scripts/edgelab/settle_markets.py
                          4. generate_daily_report.py     --date D    ONLY if settlement actually
                                                                       changed canonical state
                                      ▼
                        receipt + rolling status  →  git_data_commit.py (no-op if nothing changed)
```

Step 1 matters more than it looks: settlement iterates the **markets partition**
for the date. A bet naming a ticker whose `Market` row was never ingested can
never be found, no matter how final the game is.

Step 4 is gated because `lib/edgelab/reports.py` stamps a fresh `generatedAt` on
every build — an unconditional regeneration would produce a byte-diff, and
therefore a commit, on every single run with no new information in it. The gate
is `settlementsMeaningfullyChanged > 0 or betsSettled > 0`, using
`settle_markets.py`'s own already-computed counters.

---

## What it will never do

* **It never decides an outcome.** There is no grading logic in the reconciler at
  all. Every grade comes from `settle_date()`, which records
  `SETTLEMENT_UNRESOLVED` with an explicit reason when the game is not final,
  the linescore/boxscore fetch fails, the gamePk is unresolved, or an identity
  conflict is detected.
* **It never invents settlement support.** A market family the canonical
  settlement system does not handle stays unresolved here too — the reconciler
  adds no family-specific knowledge of its own, so it *cannot* manufacture
  support that does not exist.
* **It never infers that a recommendation was placed.** It only ever reads rows
  a user-confirmed entry surface already wrote to the canonical ledger.
* **It never writes outside `data/edgelab/`.** Not `bets.json`, not
  `data/bets.json`, not `data/slate.json`, not `marketLedger`.

---

## Recursion and idempotence

Settlement writes back onto the very file the push trigger watches. Two
independent brakes, kept because they fail in different directions:

1. **Data-level.** A ledger row whose *only* changed fields are ones settlement
   itself maintains (`status`, `result`, `netProfitLoss`, `returnAmount`,
   `updatedAt`, the refusal annotations, …) is classified `SETTLEMENT_ECHO` and
   never makes a date affected. Survives a commit-message change.
2. **Workflow-level.** The job's `if:` skips any push whose head commit message
   starts with `edgelab settlement reconcile`. Survives a bet-schema change.

Plus: `git_data_commit.py` commits nothing when nothing changed, reports
regenerate only on real change, the receipt is never committed, and the status
file is only rewritten on a material change — so a no-op run produces no
commit, and therefore no further push event.

Idempotence itself comes from the canonical layer: `storage.upsert_records`
keyed by `settlementId`/`betId`, and `bet_needs_settlement_update()`, which
refuses to rewrite an already-correct settled bet (so its `updatedAt` does not
churn). `reconcile_settlement_catchup.py --verify-idempotent` proves it in
process by running the same date set twice and failing if the second pass
changed anything.

Concurrency: the workflow shares the **`edgelab-postgame`** group, so it can
never run alongside `EdgeLab Postgame Settlement` — both call `settle_date()`
and upsert the same partitions. `cancel-in-progress: false`: a queued run waits
for the current settler, it never kills it mid-write.

Safety ceiling: a run refuses outright (rather than truncating) if more than
`MAX_DATES_PER_RUN = 45` dates are selected, and an unresolvable push base
revision falls back to the bounded sweep instead of treating the whole ledger as
new.

---

## Outputs

| Path | What it is |
|------|-----------|
| `data/edgelab/reports/<date>.md` / `.json` | the canonical daily report — **this is what "how did we do yesterday" reads** |
| `data/edgelab/operational_health/settlement_reconciliation_status.json` | **GLOBAL outstanding-work snapshot** (committed) — see below |
| the per-run receipt (Actions artifact, `$RUNNER_TEMP`) | **ONE run's** record: dates considered and why, per-date counts, unresolved reasons by family, warnings. **Never committed.** |

### The two are different things, on purpose

`settlement_reconciliation_status.json` is a **GLOBAL** snapshot of everything
the canonical ledger still owes settlement for, derived from the **whole**
ledger — not from the dates the last run happened to touch. A push-triggered
run that reconciles only 2026-09-17 can never erase 2026-09-16's unresolved
wager from it. It carries `pendingTotal`, `pendingByDate`, `pendingWagers`
(betId/ticker/family/refusal class), `oldestPendingDate`, `newestPendingDate`
and `refusalClassCounts`, plus a `lastRun` block that is provenance only.

A **receipt** describes ONE run. Every run produces one for observability, and
it is uploaded as a GitHub Actions artifact rather than committed — it carries
that run's own timestamps by definition, so committing it would turn every
no-op sweep into a repository commit.

### No timestamp-only churn

The versioned status file is rewritten **only when its material content
changes**. `material_status_fingerprint()` strips `asOf` and `lastRun` before
hashing, so a twice-daily sweep that found nothing new leaves its bytes
untouched and `git_data_commit.py` has nothing to commit. Versioned state
changes when a wager is newly imported, newly settled, materially changes its
unresolved/refusal state, or the global pending set changes — and not
otherwise.
| `data/edgelab/settlements/<date>.jsonl` | canonical settlement records, including every explicit `SETTLEMENT_UNRESOLVED` + reason |
| `data/edgelab/bets/bets.jsonl` | the canonical ledger, with newly graded wagers |

`data/edgelab/markets/` and `data/edgelab/observations/` are regenerated by the
ingest step but are deliberately **not** committed here — they belong to
`EdgeLab Market Capture`, which is not in this job's concurrency group and
gzips finalized partitions. `git_data_commit.py` cannot prove a `.jsonl.gz`
conflict is a pure append, so including them would let a benign concurrent
capture fail this commit closed and discard the settlement work with it.
Re-ingesting is cheap and idempotent; losing a night's settlement is not.

---

## Running it by hand

```bash
# everything the recent window still owes
python3 scripts/edgelab/reconcile_settlement_catchup.py --lookback-days 5

# one date / a range (recovery)
python3 scripts/edgelab/reconcile_settlement_catchup.py --date 2026-09-15
python3 scripts/edgelab/reconcile_settlement_catchup.py --start-date 2026-09-10 --end-date 2026-09-15

# exactly what a push changed
python3 scripts/edgelab/reconcile_settlement_catchup.py --changed-from <sha>

# look, don't touch
python3 scripts/edgelab/reconcile_settlement_catchup.py --lookback-days 3 --dry-run

# prove a run is idempotent
python3 scripts/edgelab/reconcile_settlement_catchup.py --date 2026-09-15 --verify-idempotent
```

Or dispatch the workflow:

```
POST /repos/chmoses98/edge-finder-api/actions/workflows/edgelab-settlement-reconcile.yml/dispatches
Body: {"ref":"main","inputs":{"date":"2026-09-15"}}
Body: {"ref":"main","inputs":{"start_date":"2026-09-10","end_date":"2026-09-15"}}
Body: {"ref":"main","inputs":{"lookback_days":"7","dry_run":"true"}}
```

---

## Tested scenarios

`tests/edgelab/test_settlement_reconciliation.py` covers, end to end against a
sandboxed data tree, all eight scenarios this system exists for:

1. a bet imported **after** the postgame workflow already ran → settled by the
   next reconciliation;
2. a bet imported **before** the game ends → stays pending while the game is
   live, settles on a later sweep;
3. the workflow **rerun twice** → the second pass changes nothing, byte-for-byte;
4. **no affected wagers** → clean no-op, no report regenerated;
5. **settlement evidence unavailable** (fetch failure; and separately an
   unresolved gamePk) → wager left ungraded, market recorded
   `SETTLEMENT_UNRESOLVED` *with a reason*;
6. **multiple affected dates** → all reconciled, oldest first;
7. an **already-settled wager** → never rewritten, `updatedAt` unchanged;
8. a **partially supported slate** containing an unsupported market family →
   the supported wager settles, the unsupported one stays explicitly unresolved.

Plus, from the pre-merge correction pass:

9. **no versioned churn** — a first meaningful reconciliation writes state; an
   immediate identical rerun changes no versioned bytes; a scheduled sweep with
   nothing pending changes no versioned bytes;
10. **global pending status** — date A is pending, only date B is reconciled,
    and date A is still reported as outstanding afterwards.

`tests/edgelab/test_settlement_reconcile_workflow_structure.py` pins the
structural properties that make the push trigger safe (path scoping, the
recursion guard matching the real commit marker, the shared concurrency group,
the fail-closed branch resolver, env-only input handling, the production-file
boundary, and the receipt staying out of the commit set).
