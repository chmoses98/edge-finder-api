# SLATE_WORKFLOW.md

## Session Start — Pull Model Files from GitHub
Pull latest before anything else:
- RULES.md, MODEL_CORE.md, SLATE_WORKFLOW.md, DATA_SOURCES.md, bets.json

---

## Pre-Slate Review (run first, every session)
1. Pull yesterday's results via `fetch_sports_data`
2. Pull box scores for ALL pending bets via `fetch_sports_data` game_stats by game ID
   → NRFI/YRFI: check inning-by-inning linescore — did a run score in the 1st?
   → K props: verify pitcher K count from box score
   → Totals: verify final score
   → Never ask the user for results — always pull box score directly
3. Mark each pending bet WIN/LOSS/PUSH, record P/L, record closing line → calculate CLV
4. Recalculate cumulative summary (record, P/L, ROI, bankroll)
5. Run calibration check — if 30+ settled bets in any edge bucket, recalculate factor
6. Update bets.json with all settled results
7. Regenerate BET_LOG.md from bets.json
8. Push bets.json + BET_LOG.md to GitHub
9. Flag model adjustment lessons → propose RULES.md additions if pattern is clear

---

## Slate Data Fetch
1. Trigger `fetch-slate` GitHub Action:
   ```
   POST https://api.github.com/repos/chmoses98/edge-finder-api/actions/workflows/fetch-slate.yml/dispatches
   Body: {"ref":"main","inputs":{"date":"YYYY-MM-DD"}}
   ```
2. Wait ~40 seconds
3. Verify `data/meta.json` fetchedAt matches today
4. Read `data/slate.json` — this is the source of truth for all analysis
5. If Action fails → fall back to fallback chain in DATA_SOURCES.md

---

## Slate Analysis
1. From `data/slate.json`, confirm same-day starters for each game
   → Flag any pitcher averaging <3 IP/start as opener role
   → Check `pitcher.firstInningSplit` for opener xERA data
   → If unavailable or sample <5 appearances: mark F5 and K props UNQUALIFIED
2. Run game-by-game analysis per MODEL_CORE output format
3. Scan all markets per game (ML, RL, total, TTs, YRFI, NRFI, F5, props)
4. Calculate edge on all qualified plays
5. Log ALL ≥1.5% edge plays to bets.json as status: PENDING
6. Push bets.json + BET_LOG.md to GitHub
7. Size plays ≥3% per Kelly table; paper-log 1.5–2.9%

---

## Bet Entry Format (bets.json)
```json
{
  "id": "2026-05-27-001",
  "date": "2026-05-27",
  "game": "NYY @ KC",
  "market": "ML",
  "bet": "NYY ML",
  "price": -145,
  "modelPct": 58.2,
  "kalshiPct": 52.0,
  "edgePct": 1.9,
  "size": 5,
  "confidence": "Medium",
  "factors": {"starterXERA": 0.6, "bullpen": 0.2, "streak": 0.1},
  "status": "PENDING",
  "result": null,
  "pl": null,
  "closingLine": null,
  "clv": null,
  "notes": ""
}
```

---

## Calibration Formula
Run when 30+ settled bets exist in an edge bucket:
1. Group bets by edge tier: 1.5–1.9%, 2.0–2.9%, 3.0%+
2. Per tier: actual_win_rate = wins / (wins + losses)
3. Expected win rate from model implied probability
4. Calibration factor = avg(actual) / avg(expected) across tiers
5. If new factor differs from current by >0.03 → update MODEL_CORE.md

---

## Cumulative Record
| Date | W | L | P/L | Bankroll |
|---|---|---|---|---|
| May 21 | 17 | 7 | +$77.00 | |
| May 22 | 3 | 5 | -$19.00 | |
| May 23 | 3 | 5 | -$10.00 | |
| May 24 | 21 | 25 | -$12.97 | |
| May 25 | 14 | 22 | -$50.99 | |
| May 26 | 13 | 4 | +$37.71 | |
| **TOTAL** | **71W** | **68L** | **+$21.75** | **$221.75** |

**ROI: +2.1% | Losing streak broken — Rule 21 cap lifted**

> Note: Per-bet tracking begins May 26, 2026. Prior record is session-level summary only.
