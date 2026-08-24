# 2026-08-23 Postmortem — MLB Kalshi Slate

**Positions:** 22 (21 imported straight + 1 combo). **Profitable:** 14 straight / **Negative:** 7 straight (plus 1 early-closed realized loss).
**Canonical (straight-only) totals:** Risk $431.00, Paid $519.41, P/L +$88.41, ROI +20.51%
**User-reported full-slate totals (incl. $10 parlay):** Risk $441, Paid $519.41, P/L +$78.41, ROI +17.78%

## Summary

A strong, diversified handicapping day — profit came from multiple market families (team totals, moneylines, pitcher strikeouts, F5) instead of one concentrated thesis. Team totals carried the largest exposure and still finished positive, spread across many teams/games rather than one correlated cluster. Pitcher strikeouts went 3-0 (Bennett 5+, McLean 6+, Drohan 5+), each selected near the modal expected outcome. A 3-leg combo (PHI 4+/BAL 4+/MIA 4+) is documented but could not be imported — see Blocked Wagers. The TB/BAL F5 position was closed early for a realized -$2.84 — see Early-Close Positions.

## Findings

1. Strong diversified handicapping day.
2. Profit came from multiple market families instead of one concentrated thesis.
3. Team totals carried the largest exposure and still finished positive.
4. Winning team totals were spread across multiple teams/games, more encouraging than one single correlated cluster.
5. Pitcher Ks were 3-0, but do not declare the family proven.
6. Bennett 5+, McLean 6+, Drohan 5+ suggest selecting a ladder near the modal expected strikeout outcome may be preferable to stretching for high plus-money rungs.
7. The F3 Tie lost, but the modest stake and plus-money structure were preferable to paying heavy protected-F5 juice merely for tie insurance.
8. Milwaukee was the clearest same-game concentration miss: MIL ML -$10, MIL 4+ -$25, combined -$35.
9. The 3-leg parlay duplicated straight PHI 4+ and BAL 4+ exposure and was moderately correlated with MIA F5 via MIA 4+.
10. Parlays should remain small and should not quietly increase major straight-position exposure unless intentionally tagged.
11. TB/BAL F5 closed early for a realized -$2.84; actual early-exit economics preserved, held-to-settlement result not inferred.
12. The card's 21 non-combo positions produced +20.51% ROI.
13. Do not retune core model probabilities from this one strong slate.

## Family Breakdown (canonical, straight-only)

- **Team total:** 7-4, $239.00 risk, +$14.56
- **Moneyline:** 3-1, $90.00 risk, +$37.81
- **Pitcher strikeouts:** 3-0, $45.00 risk, +$35.33
- **F5:** 1 win / 1 early-close, $42.00 risk, +$15.71
- **F3 Tie:** 0-1, $15.00 risk, -$15.00

## Same-Game Concentration

ATL @ MIL: MIL ML (-$10) and MIL 4+ (-$25), combined -$35 — the clearest same-game concentration miss of the day.

## Early-Close Position: TB @ BAL F5

`2026-08-23-tb-bal-bal-f5-no-closed-001` — NO on Baltimore wins F5 (economic exposure while held: TB leads or ties after five). Risk $22, closed-position paid out $19.16, realized P/L -$2.84. Represented via `executionStatus=SOLD_EARLY` / `exitSaleProceeds=19.16` rather than a binary settlement — the held-to-settlement counterfactual is deliberately never inferred.

## Blocked Wager: 3-Leg Combo

`2026-08-23-combo-phi4-bal4-mia4-001` — **BLOCKED_SCHEMA_LIMITATION**. 3-market combo (raw Initial Cost $9.99, rounded risk $10, paid $0, max payout $40.20; legs: PHI 4+ W, BAL 4+ L, MIA 4+ W). Duplicates straight PHI 4+ and BAL 4+ exposure; moderately correlated with MIA F5 via MIA 4+. No canonical representation for a multi-leg combo exists; excluded from canonicalTotals. Accounts for the $10 gap between the user-reported full-slate P/L (+$78.41 across 22 positions) and the canonical straight-only P/L (+$88.41 across 21 positions).
