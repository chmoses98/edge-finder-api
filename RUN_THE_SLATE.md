# RUN_THE_SLATE.md
# The one file to rule them all.
# Last updated: June 17, 2026 — v1.2 (bet persistence chain + session ingestion path)
#
# USAGE: When the user says "run the slate", execute this document top-to-bottom.
# Every other doc is either archived or subordinate to this one.
# ─────────────────────────────────────────────────────────────────────────────

---

## MANDATORY STALE-DATE SAFETY SEQUENCE

**This section is non-negotiable. Follow it before every slate run.**

**Step 1: Pull latest main**
```bash
git pull origin main
```

**Step 2: Determine requested slate date in America/New_York**
```bash
TZ='America/New_York' date +%Y-%m-%d
# Use this date (YYYY-MM-DD) for all subsequent steps
```

**Step 3: Trigger fetch-slate workflow for the requested date**
```
POST /repos/chmoses98/edge-finder-api/actions/workflows/fetch-slate.yml/dispatches
Body: {"ref":"main", "inputs":{"date":"YYYY-MM-DD"}}
```
Wait for the workflow to complete successfully before proceeding.

**Step 4: Run stale-date validation**
```bash
python3 scripts/validate_current_slate_date.py YYYY-MM-DD
```
This script checks:
-  status == "OK"
-  date matches requested date
-  date matches requested date
- All game start times map to the requested date in America/New_York
-  date matches (if present)
- Kalshi data date matches

**Step 5: Only if Step 4 passes, proceed**
- Run slate validation
- Run Poisson engine
- Produce real-money slip
- Produce paper bets

**If stale-date validation fails, stop. Do not use web-searched pitchers plus stale repo files. Do not manually run the model on old data/slate.json. Do not produce paper or real bets from stale data.**

---

## WHAT THIS FILE IS

This is the **single authoritative execution sequence** for every slate session.
It replaces the startup sections of RULES.md, MODEL_CORE.md, SLATE_WORKFLOW.md, and DATA_SOURCES.md.
Those files are now **reference-only** — they define the math and rules but never the execution order.

---

## STARTUP SEQUENCE (exactly once, in this order)

### S1 — Pull model files
```python
files = ["RULES.md", "MODEL_CORE.md", "SLATE_WORKFLOW.md", "DATA_SOURCES.md"]
# fetch each from: https://raw.githubusercontent.com/chmoses98/edge-finder-api/main/{file}
# Authorization: token ${WORKFLOW_TOKEN}
```

### S2 — Trigger fetch-slate Action
```
POST /repos/chmoses98/edge-finder-api/actions/workflows/fetch-slate.yml/dispatches
Body: {"ref":"main"}
```
Poll `data/meta.json` every 15s until `fetchedAt` contains today's date (ET). Cap: 3 min. Re-trigger once if stale.

### S3 — Read slate.json and validate
Pull `data/slate.json` via GitHub contents API. Then run validation:
```bash
python3 scripts/validate_slate_final.py "$DATE"   # checks schema + marketLedger completeness
# Failure = STOP. Fix before analysis.
```
The Action writes `g['marketLedger']` for every game via `build_market_ledger.py`.
Validation confirms: starters present, projections computed, marketLedger populated,
all required markets have a row, all rows have a valid status.

### S4 — Run Poisson engine (bash_tool)
```python
import math
def poisson_pmf(k, lam): return (lam**k * math.exp(-lam)) / math.factorial(k)
def game_probs(away, home, max_r=20):
    wa=wh=push=0
    for a in range(max_r+1):
        for h in range(max_r+1):
            p = poisson_pmf(a,away)*poisson_pmf(h,home)
            if a>h: wa+=p
            elif a<h: wh+=p
            else: push+=p
    return round(wa/(1-push)*100,1), round(wh/(1-push)*100,1), round(push*100,1)
def p_over(proj, line, max_r=30):
    return round(sum(poisson_pmf(r,proj) for r in range(int(line)+1,max_r+1))*100,1)
```

### S5 — Produce full output (no abbreviation, every game, every market)
See OUTPUT CONTRACT below.

### S6 — Push bets.json to GitHub
Only after full output is confirmed. Status: open (real) or paper.

---

## MARKET EVALUATION LIST (canonical — exactly these 8 markets per game)

| # | Market | Kalshi Series | Rule Gate |
|---|---------|--------------|-----------|
| 1 | NRFI | KXMLBRFI | Rule 34: blocked if total ≥8.0 without dual sub-3.00 1st-inn xERA |
| 2 | YRFI | KXMLBRFI | Four-factor composite required |
| 3 | F5 ML (Away) | KXMLBF5 | **Mandatory** — model failure if absent (Rule 25) |
| 4 | F5 ML (Home) | KXMLBF5 | Rule 77: if both qualify, log higher edge real / lower paper |
| 5 | Team Total Away Over | KXMLBTEAMTOTAL | Rule 44: Paper if line unconfirmed |
| 6 | Team Total Home Over | KXMLBTEAMTOTAL | Rule 44: Paper if line unconfirmed |
| 7 | ML (Both sides) | KXMLBGAME | Rule 71: blocked if model vs Pinnacle VF >8% unexplained |
| 8 | Game Total | KXMLBTOTAL | Paper-only (WR 41%) |

**RL** (KXMLBSPREAD): paper/suspended per Rule 81. Always evaluate; always paper until suspension lifts.

**Every game gets all 11 rows in `marketLedger`. A missing row is a pipeline failure, not an acceptable gap. `allEdges` is not the coverage source of truth — `marketLedger` is.**

---

## SOURCE-OF-TRUTH HIERARCHY

| What | Authority |
|------|-----------|
| Bet prices / edge target | Kalshi VF |
| Sanity check | Pinnacle VF |
| Closing lines / CLV | Kalshi historical |
| Edge formula | `(modelProb − kalshiVF) × calibration_factor` |
| Calibration factors | `config/rules.json` → `calibration` |
| Market list | This file (above) |
| Rule definitions | `RULES.md` (T1/T2/T3 tiers) |
| Math engine | `MODEL_CORE.md` Sections 1–8 |
| Market coverage | `g['marketLedger']` in `data/slate.json` — 11 rows per game, written by `build_market_ledger.py` |
| Bet ledger | `bets.json` (flat array, parse directly) |

**FD/DK are banned as bet sources or fallbacks. Never used.**

---

## EDGE THRESHOLDS AND SIZING

| Tier | Calibrated Edge | Cal Factor | Base Size | Multiplier Applied? |
|------|----------------|-----------|-----------|---------------------|
| HIGH | ≥3.0% | 0.187 | $4 | Per config/rules.json |
| MEDIUM | ≥1.5% | 0.255 | $3 | Per config/rules.json |
| PAPER | ≥1.0% | 0.18 | $1 | Always $1, no multiplier |

F5 when f5Amplified=true (xERAGap ≥1.5): MEDIUM threshold drops to 1.0%.

`edge = (modelProb − kalshiVF) × calibration_factor` — never raw gap.

Current market multipliers (from `config/rules.json` → `multipliers`):
- F5 ML: 1.5x | Team Total: 1.25x | YRFI: 1.25x | ML: 1.0x | NRFI: 1.0x | RL: SUSPENDED | Game Total: PAPER ONLY

---

## OUTPUT CONTRACT (mandatory, no abbreviation)

For every game on the slate, produce in this exact structure:

```
PRE-SCAN: [Team] | L7: X.X | L15: X.X | Szn: X.X | Flag: BOUNCEBACK/REGRESSION/NEUTRAL
(one line per team — required before any game analysis)

GAME: [AWAY @ HOME] — Date/Time
LINEUP CHECK:
  AWAY: lineupConfirmed=T/F | lineupAdj=±X.XX R/G applied=T/F | offenseBaselineAdj=X.XX
  HOME: lineupConfirmed=T/F | lineupAdj=±X.XX R/G applied=T/F | offenseBaselineAdj=X.XX
  Gate: [TT Paper-only {team} / All markets clear]

STARTERS:
  AWAY: [Name] true_xFIP=X.XX (xFIP=X.XX, xERA=X.XX, K/9=X.X, BB/9=X.X, GS=N)
  HOME: [Name] true_xFIP=X.XX (xFIP=X.XX, xERA=X.XX, K/9=X.X, BB/9=X.X, GS=N)

RUN PROJECTION:
  AWAY offense_baseline_adj: X.XX → off_factor: X.XX
  HOME starter: xFIP X.XX → X.XX R/inn × X.X IP = X.XX; bullpen: X.XX R/inn × X.X IP = X.XX; park ±X.XX
  AWAY proj: X.X runs
  HOME offense_baseline_adj: X.XX → off_factor: X.XX
  AWAY starter: xFIP X.XX → X.XX R/inn × X.X IP = X.XX; bullpen: X.XX R/inn × X.X IP = X.XX; park ±X.XX
  HOME proj: X.X runs
  TOTAL proj: X.X | F5: AWAY X.X / HOME X.X

MARKET LEDGER (read from g['marketLedger'] — 11 rows required, no exceptions):
Market         | Status       | Kalshi | KalVF% | Model% | Edge   | Conf   | Note
NRFI           | ...          | ...    | ...    | ...    | ...    | ...    | ...
YRFI           | ...          | ...    | ...    | ...    | ...    | ...    | ...
F5_ML_Away     | ...          | ...    | ...    | ...    | ...    | ...    | ...
F5_ML_Home     | ...          | ...    | ...    | ...    | ...    | ...    | ...
TT_Away_Over   | ...          | ...    | ...    | ...    | ...    | ...    | ...
TT_Home_Over   | ...          | ...    | ...    | ...    | ...    | ...    | ...
ML_Away        | ...          | ...    | ...    | ...    | ...    | ...    | ...
ML_Home        | ...          | ...    | ...    | ...    | ...    | ...    | ...
Game_Total     | Rejected     | ...    | ...    | ...    | ...    | PAPER  | Rule 71 suspension
RL_Away        | Rejected     | ...    | ...    | ...    | ...    | PAPER  | Rule 81 suspended
RL_Home        | Rejected     | ...    | ...    | ...    | ...    | PAPER  | Rule 81 suspended

Status must be exactly one of: Accepted | Rejected | Missing Data | Evaluation Failed
Evaluation Failed = hard stop. Investigate before logging any bets for this game.

STACK CHECK: N bets | Correlated: Yes→reduced / No | Aggregate: $X | Independent angles: [list]

QUALIFYING BETS:
[bet] | $X | Conf | Gate: [any T1/T2 fired] | Thesis: [one sentence]
```

Any game block missing any section above = model failure. Do not push until complete.

---

## MARKET LEDGER EXECUTION STANDARD

### What the market ledger is

`g['marketLedger']` is the required execution output of every slate run. It is written by
`scripts/build_market_ledger.py` and read by `scripts/validate_slate_final.py` and
`scripts/regression_test.py`. It is the source of truth for market coverage.

`allEdges` is a pipeline artifact. It is not the coverage source of truth. A market
absent from `allEdges` with no entry in `marketLedger` is a pipeline failure, not
an acceptable omission.

### Completeness rule

A slate is not complete unless:

```
len(g['marketLedger']) == 11  for every game g
total ledger rows == games × 11
```

The 11 required markets are those in `config/rules.json` → `market_list`. Any deviation
is a hard stop before logging bets.

### Row status rules

Every row must have exactly one of these statuses:

| Status | When used | Required fields |
|--------|-----------|-----------------|
| `Accepted` | Edge ≥ threshold, no gates blocked | `kalshiPrice`, `edge`, `confidence`, `market` — all non-null |
| `Rejected` | Evaluated; below threshold or gate blocked | `rejectionReason` — non-empty string |
| `Missing Data` | Kalshi price not in slate | `missingFields` — list with at least one field path |
| `Evaluation Failed` | Unexpected error during evaluation | `evaluationError` — non-empty string |

**`Evaluation Failed` is a hard stop.** Do not log any bet for that game until the
error is diagnosed. `Evaluation Failed` with an empty `evaluationError` is also
a hard stop — it means the error was silently swallowed.

### Rejection reason formats

Every `Rejected` row must include one of:

- `Rule N: [specific reason]` — a named rule gate fired
- `edge X.X% below X.X% floor` — evaluated, no qualifying edge
- `Missing Data — [field path]` — price not posted (use `Missing Data` status instead)
- `Rule 71 market suspension: [market] WR X% — Paper only until WR>=X% N>=X`
- `Rule 81: RL suspended — WR X%, CLV X%. Paper until WR>=48% N>=20 AND CLV>=0% N>=15`

Blank `rejectionReason` on a `Rejected` row = validation failure.

### Post-run report (required after every slate)

After every run, before logging any bets, report exactly this block:

```
LEDGER REPORT — [DATE] — [N] games
  Total rows    : [N] / [games × 11] expected
  Accepted      : [N]
  Rejected      : [N]
  Missing Data  : [N]
  Eval Failed   : [N]  ← must be 0 to proceed

  Missing Data rows:
    [game] / [market] — [missingFields]    (or NONE)

  Validation failures:
    [description]                           (or NONE)

  Warnings:
    [description]                           (or NONE)
```

Do not log any bets until `Eval Failed = 0` and `Validation failures = NONE`.

---

## DEPRECATED / ARCHIVED FILES

These files are **no longer authoritative** and are moved to `archive/`. Claude must not treat them as current instructions:

| File | Status | Reason |
|------|--------|--------|
| `archive/RULES_INDEX.md` | Archived | Superseded by config/rules.json |
| (prior README workflow sections) | Archived | Startup sequence is now here only |

Model files (RULES.md, MODEL_CORE.md, SLATE_WORKFLOW.md, DATA_SOURCES.md) remain active as **reference documents** — they define math and rules but not execution order. Execution order is defined exclusively in this file.

---

## EXECUTION STANDARD AUDIT (June 7, 2026 — v1.1)

✅ **One startup sequence** — S1–S6 above. No other file defines startup order.
✅ **One market evaluation list** — 11-market table above, mirrored in `config/rules.json → market_list`.
✅ **One coverage source of truth** — `g['marketLedger']`. `allEdges` is not the audit source.
✅ **Market ledger completeness enforced** — `regression_test.py` asserts `games × 11` rows every run.
✅ **Every non-Accepted row has a documented reason** — enforced by `validate_slate_final.py` and `regression_test.py`.
✅ **Evaluation Failed is a hard stop** — stated in output contract and post-run report format.
✅ **Accepted rows require price, edge, confidence, market** — asserted by regression_test.py (A7).
✅ **Post-run report format is fixed** — LEDGER REPORT block above is required before any bet logging.
✅ **No deprecated instructions active** — RULES_INDEX.md in archive/.


---

## SECTION: BET PERSISTENCE CHAIN (added June 17, 2026 — v1.2)

**This section supersedes any prior guidance on bet logging order.**

### Automated path (standard slate)

The pipeline writes bets in this exact order. Each step is a gate — failure
stops the chain and means no real-money slip.

```
1. risk_gate.py          → TT safety + portfolio concentration
2. write_pending_bets.py → writes bets.json
3. validate_bet_logging.py → hard integrity check
4. write_tracked_tickers.py → CLV ticker registry
5. COMMIT: data/ + bets.json
```

**No slip is produced unless all 5 complete successfully.**

### Session / late-lineup path (added June 17, 2026)

When lineups confirm after the automated run:

```
1. python3 scripts/enrich_lineup_confirmed.py
   # Refreshes lineupConfirmed from lineup_audit_{date}.json (v2.0 primary source)
   # Fixes the June 17 stale-field bug: audit at 22:45Z was newer than slate 18:19Z

2. [run session analysis, identify bets]

3. Create data/session_bets/YYYY-MM-DD.json (one entry per bet, see schema below)

4. python3 scripts/log_session_bets.py data/session_bets/YYYY-MM-DD.json
   # Writes bets.json (idempotent — no duplicates on re-run)
   # Appends tickers to data/clv_snapshots/YYYY-MM-DD/tracked_tickers.json

5. Verify:
   - bets.json contains the new bets
   - tracked_tickers.json contains the new tickers
   - no duplicates (re-run the script to confirm "Bets skipped: N")

6. COMMIT before first pitch → clv_capture.yml picks up tickers on next 10-min run

7. If logging after first pitch:
   - set "post_entry_manual_review": true
   - set "clvStatus": "unavailable"
   - set "clvReason": "session_bet_not_tracked_pregame: [explanation]"
```

**Session bet schema (minimum required fields):**
```json
{
  "date": "YYYY-MM-DD",
  "game": "AWAY@HOME",
  "market": "F5 ML",
  "side": "HOME",
  "ticker": "KXMLBF5-...",
  "entryPrice": -111,
  "stake": 4.50,
  "modelPct": 68.0,
  "marketPct": 52.8,
  "edgePct": 2.84,
  "confidence": "MEDIUM",
  "scheduledStartTime": "2026-06-17T23:05:00Z",
  "source": "session_analysis"
}
```

### Guardrails (both paths)

| Guardrail | Rule |
|---|---|
| No unlogged real-money bets | Every real-money bet → bets.json before first pitch |
| No CLV-uncapturable bets without marking | clvStatus="unavailable" + clvReason required |
| No TT real-money without risk gate | risk_gate.py must pass; TT line must be confirmed |
| No stale lineupConfirmed | Run enrich_lineup_confirmed.py if lineupCheckedAt > 3h before pitch |
| No slip without persistence confirmation | bets.json + tracked_tickers.json committed |

---

## SECTION: REAL-MONEY SLIP FORMAT (added June 17, 2026 — v1.2)

Every real-money session ends with exactly one slip in this format.
See `OPERATIONAL_RUNBOOK.md` for full detail.

```
═══════════════════════════════════════════════════════════════
REAL-MONEY SLIP — [DATE] — [HH:MM ET]
═══════════════════════════════════════════════════════════════

PIPELINE STATUS
  Workflow run ID  : [run ID]
  Commit SHA       : [12-char SHA]
  Fetch date       : [YYYY-MM-DD]
  lineupCheckedAt  : [ISO timestamp] (must be ≤ 4h before first pitch)
  Lineup audit     : [used / not found / stale]

CHECKPOINTS
  [✅/❌] fetch-slate completed
  [✅/❌] risk_gate.py executed
  [✅/❌] write_pending_bets.py ran
  [✅/❌] validate_bet_logging.py passed
  [✅/❌] write_tracked_tickers.py ran
  [✅/❌] bets.json committed
  [✅/❌] tracked_tickers.json committed
  [✅/❌] stale-date guard passed

SLATE SUMMARY
  Games on slate : N | Included: N | Excluded: N
  Exclusion reasons: [game: reason, ...]

RISK GATE: PASS / FAIL

BETS
  Real-money: N | Paper: N | Total stake: $X

REAL-MONEY BETS:
  [#] [GAME] | [MARKET] | [SIDE] | [PRICE] | $[STAKE] | edge=[X.X]% | [CONF]
      Ticker: [TICKER]
      Thesis: [one sentence]
      Gates:  [T1/T2 gates checked, or NONE]

PAPER BETS:
  [#] [GAME] | [MARKET] | [SIDE] | [PRICE] | $1 | edge=[X.X]% | [reason]

EXCLUDED GAMES: [game — reason, ...]
WARNINGS: [description, or NONE]

═══════════════════════════════════════════════════════════════
DECISION: [GO / PAPER / NO-GO]
  [One sentence justification]
═══════════════════════════════════════════════════════════════
```

**Decision rules:**
- **GO**: all 8 checkpoints green + ≥1 real-money bet with edge ≥ 1.5%
- **PAPER**: all 8 green + 0 real-money bets (valid; log papers, no real action)
- **NO-GO**: any checkpoint red OR stale date OR no valid slate

