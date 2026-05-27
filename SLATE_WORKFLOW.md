# SLATE_WORKFLOW.md

## Pre-Slate (run first, every session)
1. Pull yesterday's results via `fetch_sports_data`
2. Pull box scores for all pending prop bets → verify K counts, team totals, YRFI/NRFI
3. Mark pending bets WIN/LOSS, calculate CLV vs closing line
4. Flag model adjustment lessons from yesterday
5. Update cumulative record and bankroll

## Slate Analysis (verified starters only)
1. Confirm same-day starters via `/api/pitchers` or fallback chain.
   → Flag any pitcher averaging <3 IP/start as opener role.
   → For flagged pitchers: pull 1st-inning xERA from Baseball Savant before proceeding.
   → If Savant data unavailable or sample <5 appearances: mark F5 and K props UNQUALIFIED for that game.
2. Pull odds: `/api/slate` → Pinnacle vig-free + Kalshi ML probs
3. Pull team stats: `/api/teamstats` or `fetch_sports_data` standings
4. Pull weather for all open-air parks: `/api/weather` (postponement flag only)
5. Run game-by-game analysis per MODEL_CORE output format
6. Scan all markets per game (ML, RL, total, TTs, YRFI, NRFI, F5, props)
7. Calculate edge on all qualified plays
8. Log ALL ≥1.5% edge plays to bet tracker
9. Size plays ≥3% per Kelly table; paper-log 1.5–2.9%

## Cumulative Record
| Date | W | L | P/L |
|---|---|---|---|
| May 21 | 17 | 7 | +$77.00 |
| May 22 | 3 | 5 | -$19.00 |
| May 23 | 3 | 5 | -$10.00 |
| May 24 | 21 | 25 | -$12.97 |
| May 25 | 14 | 22 | -$50.99 |
| **TOTAL** | **58W** | **64L** | **-$15.96** |

**Bankroll: $184.04 | ROI: -8.0% | Active losing streak — Rule 21 medium-bet cap in effect**
