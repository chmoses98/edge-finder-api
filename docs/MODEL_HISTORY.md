# MODEL_HISTORY.md
# Version changelog and operational history for edge-finder-api model
# Last updated: June 6, 2026

---

## MODEL VERSION HISTORY

### v2.4 — June 6, 2026
- **Lineup adjustment pipeline fixed end-to-end** (was the biggest active gap in the model)
  - `fetch_lineups.py` v1.0 bug: delta was computed vs league average xwOBA instead of team's own season xwOBA — understated adjustments for above-average offenses, overstated for below-average
  - `fetch_lineups.py` v1.0 gap: `lineupConfirmed` flag not set, no positional fallbacks for missing batters, no `lineupAdj` field (R/G-ready adjustment not computed)
  - `enrich_data.py` v5.0 gap: `lineupWOBADelta` was written to slate.json but never read back or applied to offense baseline — field was dead weight
  - **Fix:** `fetch_lineups.py` v2.0 now computes delta vs team season xwOBA, sets `lineupConfirmed` (≥6/9 real xwOBA), uses positional fallbacks for missing batters, writes `lineupAdj` in R/G terms (capped ±0.25)
  - **Fix:** `enrich_data.py` v6.0 now reads `lineupAdj` and applies it when `lineupConfirmed=True`, writes `offenseBaselineAdj` to every team block — this is the field Claude analysis uses as the offense input
  - **Downstream:** `offenseBaselineAdj` replaces raw R/G blend as the offense input in all run projections. When lineup not confirmed, `offenseBaselineAdj = offenseBaselineOppAdj` (unchanged from prior behavior)
  - MODEL_CORE.md updated to v2.4 documenting the full automated pipeline

### v2.3 — June 3–4, 2026
- SLATE_WORKFLOW updated to v2.3
- Multiplier table updated: RL SUSPENDED, ML recalibrated to 1.00x, F5 ML upgraded to 1.50x
- Rules 81–83 added (RL paper-only, same-day scratch gate, ML multiplier cap)
- CLV architecture upgraded: Kalshi live capture at slate fetch time via `capture_closing_lines.py`
- clv_update.py v6.3 deployed (major rewrite from v5.x)

### v2.2 — June 1, 2026
- MODEL_CORE and RULES.md updated to v2.2
- Rules 73–80 added
- Calibration factors updated: High 0.198→0.187 (N=52), Medium 0.227→0.255 (N=76)
- starterXERA multiplier downgraded: 1.75x → 1.25x (N now large enough to normalize)
- ML multiplier downgraded: 1.50x → 1.00x (CLV -1.58% over 76 bets)
- RL SUSPENDED: 36% WR, -4.09% avg CLV
- Rule 76 (same-game stack check) added after COL@LAA June 1 catastrophe
- Rule 77 (opposite-side prohibition) added

### v2.1 — May 28–31, 2026
- CLV tracking made operational (standardized formula)
- CLV tracking start date: May 31, 2026
- Historical CLV before May 31 is UNRECOVERABLE — pre-May-31 bets show closingLineSource: "pinnacle" and should be treated as informational only, not comparable to current CLV
- Rule 54 added (live Poisson computation via bash_tool)
- Rule 55 added (betTimeLine capture)
- Rule 56 added (F5 5/8.5 ratio)
- Rule 57 added (park factor FB%/GB% modifier)
- Rule 58 added (calibration N≥50 gate)
- Rule 59 added (Pinnacle primary, Kalshi tertiary)
- Rule 60 added (factor label standardization)

### v2.0 — May 25–27, 2026
- Rules 37 updated (Kalshi divergence — Pinnacle primary, Kalshi tertiary)
- Three-layer analysis framework added (Rule 64)
- Rolling 15-game R/G as primary offense input (Rule 65)
- Bullpen availability check added (Rule 66)
- Mandatory market evaluation checklist (Rule 67)
- Streak weight assignments standardized (Rule 68)
- F5 edge threshold for f5Amplified lowered to 1.0% (Rule 69)
- Rule 70 added (high-edge pick'em gate)
- Rule 71 added (model vs Pinnacle VF >8% block)
- Rule 72 added (suspended/postponed game handling)
- Game Totals designated paper-only (41% WR)

### v1.x — May 21–24, 2026
- Initial model deployed
- Core Poisson engine established
- First bet log entries
- xERAGap signal identified as primary edge source
- Team Total multiplier set to 1.75x (later downgraded)

---

## OPERATIONAL TRANSITIONS

### Odds Architecture (DATA_SOURCES v3.0)
**Before v3.0:** Manual web search for closing lines via OddsPortal screenshots  
**After v3.0 (June 2, 2026):** The Odds API historical endpoint + Kalshi live capture  
**Result:** CLV now fully automated; OddsPortal screenshots no longer needed

### Bet Book (June 2, 2026)
**Confirmed from diagnostic sweep:** Kalshi posts exactly 3 MLB markets via The Odds API (ML, RL, Game Total). Kalshi does NOT post TT, F5, or NRFI/YRFI at the API level.  
**Per-market book assignment:** See DATA_SOURCES.md Table "Per-market bet book and VF source"

### CLV Formula (May 31, 2026)
Standardized to implied probability difference:  
`CLV% = impliedProb(closingLine) − impliedProb(betPrice)`  
Positive = beat the close (good). Negative = paid too much (bad).

---

## KEY DECISIONS LOG

| Date | Decision | Reason |
|---|---|---|
| June 4 | RL market SUSPENDED | 22 bets, 36% WR, -4.09% avg CLV — both process and outcomes negative |
| June 4 | ML multiplier capped at 1.00x | 76 bets, CLV -1.58% — WR was variance not edge |
| June 4 | F5 ML multiplier upgraded to 1.50x | 45 bets, CLV +2.31% — best performing market |
| June 4 | Game Totals remain paper-only | 32 bets, 41% WR, CLV -1.43% |
| June 1 | Rule 76: same-game correlated stack prohibited | COL@LAA: 5 LAA-side bets all lost together |
| June 1 | Rule 77: opposite-side prohibition | CWS/MIN F5, TEX/STL F5 — both sides logged |
| May 31 | Streak signal retired as primary driver | 9 bets, 33% WR, -$20.37 |
| May 30 | Rule 73: both starters required in factors{} | NYY@ATH — J.T. Ginn never analyzed, -16.47% CLV |

---

## LOGGING CONVENTIONS

### bet log ID format
`YYYY-MM-DD-NNN` where NNN is zero-padded sequential within the date.

### bets.json structure
Flat JSON array. Parse directly. No wrapper object.
```python
bets = json.load(f)  # list
# NOT: data.get('bets', [])
```

### CLV data availability
- Bets before May 31, 2026: `closingLineSource: "pinnacle"` — use as informational context only
- Bets May 31 onward: `closingLineSource: "Kalshi"` or `"betTimeLine_proxy"` — use for model health tracking
- Rolling CLV calculations should start from May 31 or later for reliability

---

## June 7, 2026 — v3.0: Source-of-Truth Refactor

**Goal:** Eliminate duplicate workflow instructions, conflicting startup sequences, and Claude-instruction prose scattered across 4+ files.

**Changes:**
- Created `RUN_THE_SLATE.md` — single authoritative execution file (startup sequence, market list, output contract, source-of-truth hierarchy, rejection reason format)
- Created `config/rules.json` — machine-readable numeric thresholds (calibration factors, multipliers, gate thresholds, park factors, signal hierarchy, validation requirements)
- Created `scripts/validate_slate.py` — pre-analysis gate that fails with specific errors if any game is missing required markets, projections, starters, Kalshi prices, or rejection reasons
- Archived `RULES_INDEX.md` → `archive/RULES_INDEX.md` (superseded by config/rules.json)
- Updated `README.md` to point to RUN_THE_SLATE.md as the single entry point
- RULES.md, MODEL_CORE.md, SLATE_WORKFLOW.md, DATA_SOURCES.md remain active as reference documents but no longer define execution order

**Post-refactor audit results:**
- ✅ One startup sequence: S1–S6 in RUN_THE_SLATE.md only
- ✅ One market evaluation list: 11-row table in RUN_THE_SLATE.md only  
- ✅ One source-of-truth hierarchy: table in RUN_THE_SLATE.md
- ✅ Every skipped market requires structured rejection reason: format defined in RUN_THE_SLATE.md
- ✅ No deprecated instructions active: RULES_INDEX.md archived, README updated

