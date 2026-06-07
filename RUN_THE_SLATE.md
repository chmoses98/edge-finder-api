# RUN_THE_SLATE.md
# The one file to rule them all.
# Last updated: June 7, 2026 — v1.0 (source-of-truth refactor)
#
# USAGE: When the user says "run the slate", execute this document top-to-bottom.
# Every other doc is either archived or subordinate to this one.
# ─────────────────────────────────────────────────────────────────────────────

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
# If scripts/validate_slate.py exists:
python3 scripts/validate_slate.py
# Failure = STOP. Fix the missing fields before analysis.
```
Validation checks (see config/rules.json → `validation`): every game must have starters, projections, Kalshi prices for all 8 markets, and a rejection reason for any missing field.

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

**Every game gets all 8 rows. A missing row = model failure. Silence is not a rejection.**

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
| Data fields | `data/slate.json` (written by Action) |
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

MARKET TABLE:
Market              | Kalshi | Kal VF% | PinVF% | Model% | Edge  | Conf
NRFI                | ...    | ...     | ...    | ...    | ...   | ...
YRFI                | ...    | ...     | ...    | ...    | ...   | ...
F5 ML Away          | ...    | ...     | ...    | ...    | ...   | ...
F5 ML Home          | ...    | ...     | ...    | ...    | ...   | ...
TT Away Over        | ...    | ...     | N/A    | ...    | ...   | ...
TT Home Over        | ...    | ...     | N/A    | ...    | ...   | ...
ML Away             | ...    | ...     | ...    | ...    | ...   | ...
ML Home             | ...    | ...     | ...    | ...    | ...   | ...
Game Total          | ...    | ...     | ...    | ...    | ...   | PAPER
RL Away             | ...    | ...     | ...    | ...    | ...   | PAPER
RL Home             | ...    | ...     | ...    | ...    | ...   | PAPER

STACK CHECK: N bets | Correlated: Yes→reduced / No | Aggregate: $X | Independent angles: [list]

QUALIFYING BETS:
[bet] | $X | Conf | Gate: [any T1/T2 fired] | Thesis: [one sentence]
```

Any game block missing any section above = model failure. Do not push until complete.

---

## REJECTION REASON REQUIREMENTS

Every market row that does not produce a qualifying bet must show one of:
- `BLOCKED — Rule N: [specific reason]`
- `No edge — model X.X% vs KalVF X.X% = X.X% calibrated (below 1.0% floor)`
- `N/A — starter unconfirmed`
- `N/A — TT/F5 line not posted on Kalshi`
- `PAPER — Game Total WR 41% (Rule 71 market suspension)`
- `PAPER/SUSPENDED — RL (Rule 81, WR 36%)`

Blank rows are not acceptable.

---

## DEPRECATED / ARCHIVED FILES

These files are **no longer authoritative** and are moved to `archive/`. Claude must not treat them as current instructions:

| File | Status | Reason |
|------|--------|--------|
| `archive/RULES_INDEX.md` | Archived | Superseded by config/rules.json |
| (prior README workflow sections) | Archived | Startup sequence is now here only |

Model files (RULES.md, MODEL_CORE.md, SLATE_WORKFLOW.md, DATA_SOURCES.md) remain active as **reference documents** — they define math and rules but not execution order. Execution order is defined exclusively in this file.

---

## POST-REFACTOR AUDIT (June 7, 2026)

✅ **One startup sequence** — S1–S6 above. No other file defines startup order.
✅ **One market evaluation list** — the 8-market table above. SLATE_WORKFLOW.md STEP E matches exactly.
✅ **One source-of-truth hierarchy** — the table above. DATA_SOURCES.md is reference; this is canonical.
✅ **Every skipped market requires a structured rejection reason** — the rejection format list above is exhaustive.
✅ **No deprecated instructions active** — RULES_INDEX.md moved to archive/.
