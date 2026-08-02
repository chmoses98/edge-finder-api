# OPERATIONAL_RUNBOOK.md
# Last updated: June 17, 2026 — v1.0
# Canonical operational process for every slate run going forward.
# This file is authoritative for bet persistence, session ingestion, and the
# GO/PAPER/NO-GO slip format. It is subordinate to RUN_THE_SLATE.md for
# analysis execution order, and to RULES.md/MODEL_CORE.md for rule logic.

---

## SECTION 1 — AUTOMATED PREGAME SLATE (standard path)

### Pipeline gate checklist (complete in order before producing any slip)

Every automated slate run must clear all 7 checkpoints.
**No real-money slip is produced until all 7 are green.**

```
CHECKPOINT 1 — fetch-slate completed
  ✅ PASS: workflow run shows "success"; fetchedAt in meta.json = today ET
  ❌ FAIL: re-trigger once; if still failing, NO-GO for today

CHECKPOINT 2 — risk_gate.py executed
  ✅ PASS: risk_gate.py ran in the workflow steps without error
  ✅ PASS (alternate): execution_slip shows "risk_gate: passed"
  ❌ FAIL: STOP — TT and concentration gates may be unenforced

CHECKPOINT 3 — real-money bets written by write_pending_bets.py
  ✅ PASS: script ran without exit 1; real-money count ≥ 0 (0 is valid)
  ❌ FAIL: STOP — bets.json is not updated; do not produce slip

CHECKPOINT 4 — validate_bet_logging.py passed
  ✅ PASS: exit code 0; "validation passed" in output
  ❌ FAIL: STOP — logged bets have integrity errors; do not distribute slip

CHECKPOINT 5 — write_tracked_tickers.py ran
  ✅ PASS: tickers file written (or no real-money bets = 0 tickers is valid)
  ❌ FAIL: CLV will not be captured; downgrade all real-money bets to Paper
            unless --warn-only was passed intentionally

CHECKPOINT 6 — commit includes bets.json and tracked_tickers.json
  ✅ PASS: both files appear in the commit diff when real-money bets exist
  ✅ PASS (alternate): no real-money bets → neither file needs updating
  ❌ FAIL: STOP — bets are not persisted; re-run write_pending_bets.py and commit

CHECKPOINT 7 — stale-date guard passed
  ✅ PASS: meta.json date == today ET; lineupCheckedAt within 4 hours of first pitch
  ❌ FAIL: run enrich_lineup_confirmed.py to refresh from lineup_audit_{date}.json
```

### Final GO/PAPER/NO-GO decision criteria

```
GO       — all 7 checkpoints green AND at least 1 real-money bet with edge ≥ 1.5%
PAPER    — all 7 green BUT 0 real-money bets (valid; log papers only)
NO-GO    — any checkpoint red OR stale date OR no valid slate for today
```

---

## SECTION 2 — LATE LINEUP / SESSION SLATE (night slate path)

Used when lineups confirm after the automated pipeline run (typical: 4–7 PM ET
for games starting 7–10 PM ET). The automated pipeline may not capture these.

### Step-by-step

**Step 1 — Verify lineup audit is fresh**
```bash
# Check lineup_audit_{date}.json generatedAt vs lineupCheckedAt in slate.json
# If audit is newer (it usually is): enrich_lineup_confirmed.py will fix slate
python3 scripts/enrich_lineup_confirmed.py
```

**Step 2 — Run session analysis**
Identify qualifying bets using RULES.md + MODEL_CORE.md.
For each qualifying bet, confirm:
- ticker exists in Kalshi (active, status=active)
- entry price confirmed
- stake sized per tier + multiplier
- all T1/T2 gates explicitly checked

**Step 3 — Create session bets file**
```
data/session_bets/YYYY-MM-DD.json
```
Schema (required fields marked *):
```json
[
  {
    "date": "YYYY-MM-DD",          *
    "game": "AWAY@HOME",           *
    "market": "F5 ML",             *
    "side": "HOME",                *
    "ticker": "KXMLBF5-...",       *
    "entryPrice": -111,            *  American odds integer
    "stake": 4.50,                 *  dollars
    "modelPct": 68.0,              *  0-100
    "marketPct": 52.8,             *  Kalshi VF 0-100
    "edgePct": 2.84,               *  calibrated edge
    "confidence": "MEDIUM",        *  HIGH | MEDIUM | PAPER
    "betTeam": "...",
    "scheduledStartTime": "...Z",
    "factors": {},
    "notes": "...",
    "source": "session_analysis",
    "timestamp": "...Z",
    "post_entry_manual_review": false  ← set true only for backfills
  }
]
```

**Step 4 — Run ingestion script**
```bash
python3 scripts/log_session_bets.py data/session_bets/YYYY-MM-DD.json
```

Expected output for a clean run:
```
[log_session_bets] Processing N session bet(s)
  Validation: all N bets passed
  Bets to add: N
  Bets skipped (duplicates): 0
  CLV tickers to add: N
  ✅ Written N bet(s) → data/bets.json
  ✅ Written N ticker(s) → data/clv_snapshots/YYYY-MM-DD/tracked_tickers.json
[log_session_bets] Done.
```

**Step 5 — Verify outputs**
```bash
# Confirm bet count
python3 -c "
import json
bets = json.load(open('data/bets.json'))
today = [b for b in bets if b.get('date')=='YYYY-MM-DD' and b.get('source')=='session_analysis']
print(f'Session bets logged: {len(today)}')
for b in today:
    print(f'  {b[\"game\"]} | {b[\"market\"]} | {b[\"entryPrice\"]:+d} | source={b[\"source\"]}')
"
```

**Step 6 — Commit before first pitch (strongly preferred)**
```bash
git add data/bets.json data/clv_snapshots/YYYY-MM-DD/tracked_tickers.json \
        data/session_bets/YYYY-MM-DD.json
git commit -m "session bets YYYY-MM-DD: N F5 bets pre-first-pitch"
git push origin HEAD:main
```

Committing before first pitch allows `clv_capture.yml` (runs every 10 min)
to snapshot pregame Kalshi prices = valid CLV data.

**Step 7 — If logging happens after first pitch**
- Set `"post_entry_manual_review": true` on the bet
- Set `"clvStatus": "unavailable"` and `"clvReason": "session_bet_not_tracked_pregame: ..."`
- This is the June 17 scenario — honest accounting, no fabrication

---

## SECTION 3 — GUARDRAILS (non-negotiable, every path)

### G1 — No unlogged real-money bets
Every real-money bet must be in `bets.json` before first pitch.
A bet exists only when it is in `bets.json`. A bet discussed in a session
output but absent from `bets.json` does not exist for model evaluation purposes.

### G2 — No CLV-uncapturable real-money bets (without explicit marking)
If a bet cannot have CLV captured (late logging, session ingestion after pitch):
- `clvStatus` must be `"unavailable"` (not null, not `"not_yet_captured"`)
- `clvReason` must be a specific string — not blank
- `clvSource` must be `"unavailable"` — not `"pending"`

Marking CLV unavailable is **correct accounting**. Leaving it as `"not_yet_captured"`
when it is permanently uncapturable is **incorrect accounting**.

### G3 — No Team Total real-money unless all three pass
TT real-money requires explicit confirmation of:
1. Corrected run projection (offenseBaselineAdj applied, lineup gate clear)
2. TT Kalshi line confirmed (not estimated from game total)
3. Risk gate passed (risk_gate.py exit 0)

If any of the three is absent → TT is Paper only regardless of edge.

### G4 — No stale lineupConfirmed fields
`lineupConfirmed` in `slate.json` must reflect the lineup_audit file, not
the stale teamStats fields from an earlier pipeline run.
If `lineupCheckedAt` is more than 3 hours before first pitch → run
`enrich_lineup_confirmed.py` to refresh from the lineup audit.

### G5 — No real-money slip without persistence confirmation
The slip (GO/PAPER/NO-GO block) is only produced after:
- `bets.json` is committed
- `tracked_tickers.json` is committed (when bets exist)
- Both are confirmed in the GitHub commit diff

---

## SECTION 4 — REAL-MONEY SLIP FORMAT

Every real-money session produces exactly one slip in this format.
No slip is produced before checkpoints are cleared.

```
═══════════════════════════════════════════════════════════════
REAL-MONEY SLIP — [DATE] — [HH:MM ET]
═══════════════════════════════════════════════════════════════

PIPELINE STATUS
  Workflow run ID  : [run ID from GitHub Actions]
  Commit SHA       : [12-char SHA]
  Fetch date       : [YYYY-MM-DD] (must match today ET)
  fetchedAt        : [ISO timestamp]
  lineupCheckedAt  : [ISO timestamp] (freshness indicator)
  Lineup audit     : [used / not found / stale]

CHECKPOINTS
  [✅/❌] fetch-slate completed
  [✅/❌] risk_gate.py executed
  [✅/❌] write_pending_bets.py ran
  [✅/❌] validate_bet_logging.py passed
  [✅/❌] write_tracked_tickers.py ran
  [✅/❌] bets.json committed
  [✅/❌] tracked_tickers.json committed
  [✅/❌] stale-date guard (lineupCheckedAt fresh)

SLATE SUMMARY
  Games on slate   : N
  Games included   : N  (confirmed lineups + upcoming + valid starters)
  Games excluded   : N
  Exclusion reasons:
    - [game]: [reason]
    ...

RISK GATE
  Decision         : PASS / FAIL
  TT real-money    : [count] (must be 0 if gate unenforced)
  RL suspended     : YES (Rule 81)
  Max concentration: $X across N correlated bets

BETS
  Real-money count : N
  Paper count      : N
  Total stake      : $X

REAL-MONEY BETS:
  [#]  [GAME] | [MARKET] | [SIDE] | [PRICE] | $[STAKE] | edge=[X.X]% | [CONF]
       Ticker: [TICKER]
       Thesis: [one sentence]
       Gates:  [any T1/T2 fired and cleared, or NONE]

PAPER BETS:
  [#]  [GAME] | [MARKET] | [SIDE] | [PRICE] | $1 | edge=[X.X]% | reason=[why paper]

EXCLUDED GAMES (with reason):
  [GAME] — [reason: in progress / lineup unconfirmed / pitcher data null / etc.]

WARNINGS / BLOCKERS:
  [description, or NONE]

═══════════════════════════════════════════════════════════════
DECISION: [GO / PAPER / NO-GO]
  [One sentence justification]
═══════════════════════════════════════════════════════════════
```

---

## SECTION 5 — CLV REVIEW WORKFLOW

After all games on the slip settle (or at end of session):

```
1. Say "review today's bets"
2. → triggers update-clv workflow_dispatch
3. Wait 60 seconds
4. Pull data/bets.json
5. Produce CLV Summary Block:

   CLV SUMMARY — [DATE]
   ┌─────────────────────────────────────────────────────────────┐
   │ Bet             │ Entry  │ Closing │ CLV    │ Result │ P/L  │
   ├─────────────────┼────────┼─────────┼────────┼────────┼──────┤
   │ [game/market]   │ [odds] │ [odds]  │ [±%]   │ W/L/P  │ $X   │
   │ ...             │        │         │        │        │      │
   ├─────────────────┴────────┴─────────┴────────┴────────┴──────┤
   │ Session totals  │ stake: $X │ P/L: $X │ avg CLV: ±X.X%      │
   └─────────────────────────────────────────────────────────────┘

   CLV unavailable bets (session_bet_not_tracked_pregame):
   [list any bets with clvStatus='unavailable' and reason]
```

---

## SECTION 6 — SESSION INGESTION PATH DECISION TREE

```
Is this an automated pipeline slate?
  YES → Follow Section 1 (checkpoints 1-7) → produce slip per Section 4
  NO  → Is this a late-lineup night slate?
    YES → Did lineups confirm after 18:00Z?
      YES → Run enrich_lineup_confirmed.py first
             Create data/session_bets/YYYY-MM-DD.json
             Run log_session_bets.py
             Commit before first pitch if possible
             Produce slip per Section 4 (session path)
      NO  → Same as automated path (lineups were already current)
    NO  → Is this a backfill / post-game correction?
      YES → Set post_entry_manual_review: true
             Set clvStatus: unavailable
             Set clvReason: specific explanation
             Run log_session_bets.py
             Mark in slip: "BACKFILL — CLV unavailable"
```

---

## SECTION 7 — QUICK REFERENCE

### Scripts and their roles in the persistence chain

```
fetch-slate.yml              Pipeline orchestrator
  ↓
fetch_lineups.py             Writes awayTeamStats.lineupConfirmed
  ↓
enrich_lineup_confirmed.py   Promotes to game-level; v2.0 reads lineup_audit (freshest)
  ↓
build_market_ledger.py       Writes g['marketLedger'] — 11 rows per game
  ↓
risk_gate.py                 TT safety + portfolio concentration checks
  ↓
write_pending_bets.py        Writes real-money bets to bets.json
  ↓
validate_bet_logging.py      Hard integrity gate on bets.json
  ↓
write_tracked_tickers.py     Writes CLV ticker registry
  ↓
clv_capture.yml              Snapshots live Kalshi prices every 10 min
  ↓
update-clv workflow          Settles bets post-game


Session path (night slates / manual):
  enrich_lineup_confirmed.py  ← refresh stale fields from lineup_audit_{date}.json
  [session analysis]
  data/session_bets/YYYY-MM-DD.json  ← analyst-authored input
  log_session_bets.py         ← writes bets.json + tracked_tickers.json
  [commit before first pitch] ← clv_capture.yml picks up tickers on next 10-min run
```

### Key file locations

```
bets.json                                        bet ledger (flat array, 87+ records)
data/bets.json                                   same file, repo root alias
data/session_bets/YYYY-MM-DD.json               session ingestion input
data/clv_snapshots/YYYY-MM-DD/tracked_tickers.json  CLV ticker registry
data/lineup_audit_YYYY-MM-DD.json               freshest lineup status (primary source)
data/execution_slip_YYYY-MM-DD.json             automated pipeline slip
data/meta.json                                  fetchedAt / date / status
```

### File freshness requirements at time of slip production

| File | Max age at slip time |
|---|---|
| meta.json fetchedAt | < 2 hours |
| lineupCheckedAt | < 4 hours before first pitch |
| lineup_audit generatedAt | within same pipeline run as fetchedAt |
| tracked_tickers.json | updated by this run if real-money bets exist |
| bets.json | updated by this run if real-money bets exist |

---

## SECTION 8 — RECOVERING FROM A FAILED SLATE, SETTLEMENT, OR CLV RUN

Added by the Production Reliability and Settlement Recovery milestone.
See `docs/POSTMORTEM_PRODUCTION_RELIABILITY_2026.md` for the full
investigation this section is based on.

### 8.1 — First, read the workflow's own summary

`clv-update.yml` writes a step summary (visible on the workflow run page,
"Summary" tab) on every run — success, partial, or hard failure. It tells
you, without digging through logs:
- which stage the run reached (settlement, Kalshi CLV, identity audit,
  Rule 71 report, commit)
- a plain-English failure category if something failed
- records read/settled/pending for the target date
- whether files were actually mutated **and pushed** to `main`
- the pending-bet backlog count before and after
- an explicit "safe to re-trigger?" statement

Do this before touching anything by hand. Most failures are exactly what
the summary says, and the fix is simply re-running the workflow (see
8.2).

### 8.2 — Is it safe to just re-run?

**Yes, for all three ledger-writing workflows** (`fetch-slate.yml`,
`clv-update.yml`, `lineup-recheck.yml`), under normal circumstances:

- Settlement is idempotent — `clv_update.py` skips any bet whose
  `result`/`status` is already `WIN`/`LOSS`/`PUSH`/`VOID`/`NO_ACTION`; it
  never re-grades or double-counts an already-settled bet.
- `bets.json` writes are atomic (`lib/atomic_json.write_json_atomic`) —
  a killed job mid-write cannot leave a truncated or corrupted ledger
  file.
- All three workflows share the `edge-finder-ledger-writer` concurrency
  group, so a re-triggered run queues behind (never races) a
  still-in-progress run of any of the other two.
- Multiple bet tranches on the same ticker are each settled
  independently — a rerun will not merge, drop, or double-settle them.

**When it is NOT simply "re-run and move on":**
- If the summary shows `recordsPendingAfterRun` unexpectedly high with no
  `zeroRecordsReason` explaining it, something upstream (missing
  registry data, an unparseable game string) is silently skipping bets —
  read the "Run CLV update" step's own log output for the specific
  `?`/`✗`-prefixed lines identifying which bet and why.
- If a run failed at "Commit all updates" specifically, the run's
  computed output is on the (now-destroyed) runner, never pushed — this
  is a **lost run**, not a corrupted one. Re-triggering re-computes from
  the same current `bets.json` and is safe; nothing needs manual repair.
- NRFI/YRFI and F5 ML bets are **never** auto-graded by design (see
  `clv_update.py`'s `determine_result()`) — a growing count of these in
  the backlog is expected, not a bug, and requires manual settlement, not
  a rerun.

### 8.3 — Classifying an existing backlog (don't assume it's all one cause)

If `bets.json` accumulates a growing number of non-terminal (pending)
records, do NOT assume they all share one root cause. Run the dry-run
classifier:

```bash
python3 scripts/remediate_bet_backlog.py
```

This reports every non-terminal bet's classification
(`legitimately_pending` / `missing_source_data` / `pipeline_failure` /
`requires_manual_review` / `unsupported_market_family` /
`settleable_from_evidence` / `duplicate` / `malformed_record`) with the
evidence behind it, and writes a full plan to
`data/bet_backlog_remediation_plan.json`. It never modifies `bets.json`
unless you pass `--execute`, and even then it only ever applies changes
already listed in the plan's `autoSafeChanges` (which will be empty
unless a genuine, evidence-backed settlement source is available).

### 8.4 — Storage/retention issues (`data/kalshi_registry_snapshots/` growing unbounded)

```bash
python3 scripts/prune_kalshi_snapshots.py                       # dry run, reports what WOULD be pruned
python3 scripts/prune_kalshi_snapshots.py --execute              # actually prunes
```

Dated (no-timestamp) snapshots are always kept forever. Only timestamped
intraday snapshots older than the retention window (default 21 days) are
ever eligible for pruning — see `lib/snapshot_retention.py` for the full
rationale, including why the previous `find -mtime` cleanup step never
actually worked.

### 8.5 — `config/rules.json` failing to load

If a script that reads `config/rules.json` starts raising
`RulesConfigError`, the file itself has a structural problem (a missing
required section, an out-of-range value, a wrong type) — read the error
message, which lists every problem found, not just the first. This is
almost always a hand-edit mistake in `config/rules.json` itself, not a
code bug; fix the file, don't work around the validator.

---

## CHANGE LOG

| Version | Date | Change |
|---|---|---|
| v1.1 | 2026-08-02 | Production Reliability and Settlement Recovery milestone. Added SECTION 8 (recovering from a failed slate/settlement/CLV run, backlog classification, storage retention, config validation errors). See `docs/POSTMORTEM_PRODUCTION_RELIABILITY_2026.md` and `docs/INCIDENT_2026-07-31_CLV_COMMIT_FAILURE.md` for the incidents this codifies lessons from. |
| v1.0 | 2026-06-17 | Initial operational runbook. Session ingestion path (log_session_bets.py), lineup audit fix (enrich_lineup_confirmed v2.0), GO/PAPER/NO-GO slip format, CLV review workflow. Codifies lessons from June 17 night slate: UTC→ET error, stale lineupConfirmed, unlogged session bets. |
