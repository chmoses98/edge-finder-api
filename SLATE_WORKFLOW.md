# SLATE_WORKFLOW.md

## Session Start — Pull Model Files
Pull latest from GitHub before anything else:
- RULES.md, MODEL_CORE.md, SLATE_WORKFLOW.md, DATA_SOURCES.md, bets.json

## Pre-Slate Review (run first, every session)
1. Pull yesterday's results via `fetch_sports_data`
2. Pull box scores for all pending bets → verify K counts, totals, YRFI/NRFI outcomes
3. For each pending bet in bets.json:
   - Mark WIN/LOSS/PUSH
   - Record actual P/L
   - Record closing line → calculate CLV
4. Recalculate cumulative summary (record, P/L, ROI, bankroll)
5. Run calibration check — if 30+ settled bets in any edge bucket, recalculate factor
6. Update bets.json with all settled results
7. Regenerate BET_LOG.md from bets.json
8. Push both files to GitHub
9. Flag any model adjustment lessons → propose RULES.md additions if pattern is clear

## Slate Analysis (verified starters only)
1. Confirm same-day starters via `/api/pitchers` or fallback chain
   → Flag any pitcher averaging <3 IP/start as opener role
   → For flagged pitchers: pull 1st-inning xERA from Baseball Savant before proceeding
   → If Savant data unavailable or sample <5 appearances: mark F5 and K props UNQUALIFIED
2. Pull odds: `/api/slate` → Pinnacle vig-free + Kalshi ML probs
3. Pull team stats: `/api/teamstats` or `fetch_sports_data` standings
4. Pull weather for all open-air parks: `/api/weather` (postponement flag only)
5. Run game-by-game analysis per MODEL_CORE output format
6. Scan all markets per game (ML, RL, total, TTs, YRFI, NRFI, F5, props)
7. Calculate edge on all qualified plays
8. Log ALL ≥1.5% edge plays:
   - Add to bets.json as status: "PENDING"
   - Include: date, game, market, bet, price, modelPct, kalshiPct, edgePct, size, confidence, factors
9. Regenerate BET_LOG.md
10. Push bets.json + BET_LOG.md to GitHub
11. Size plays ≥3% per Kelly table; paper-log 1.5–2.9%

## Bet Entry Format (bets.json)
Each bet logged as:
```json
{
  "id": "2026-05-26-001",
  "date": "2026-05-26",
  "game": "NYY @ BOS",
  "market": "ML",
  "bet": "BOS ML",
  "price": -115,
  "modelPct": 54.2,
  "kalshiPct": 48.0,
  "edgePct": 1.9,
  "size": 5,
  "confidence": "MEDIUM",
  "factors": {"starterXERA": 0.8, "bullpen": 0.3, "streak": 0.2},
  "status": "PENDING",
  "result": null,
  "pl": null,
  "closingLine": null,
  "clv": null,
  "notes": ""
}
```

## Calibration Formula
Run when 30+ settled bets exist in an edge bucket:
1. Group bets by edge tier: 1.5-1.9%, 2.0-2.9%, 3.0%+
2. Per tier: actual_win_rate = wins / (wins + losses)
3. Expected win rate from model price implied probability
4. Calibration factor = avg(actual) / avg(expected) across tiers
5. If new factor differs from current by >0.03 → update MODEL_CORE.md

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

**ROI: +2.1% overall | May 26: +$37.71 | Bankroll: $221.75 | Losing streak broken — Rule 21 cap lifted**

> Note: Bets before bets.json was implemented (pre May 26) are summarized above.
> Full per-bet tracking begins May 26, 2026.
