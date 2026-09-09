# 2026-08-20 Postmortem — MLB Kalshi Slate

**Positions:** 8 (7 straight + 1 multi-leg combo), all 8 now canonically imported. **Record (straight):** 1-6 | **Combo:** 0-1 (canonically imported, MULTI_LEG)
**Canonical totals (complete slate):** Risk $116.00, Paid $31.20, P/L -$84.80, ROI -73.10%
**Straight-only subtotal (7 positions, for comparison):** Risk $114.00, Paid $31.20, P/L -$82.80, ROI -72.63%
**User-reported full-slate totals (incl. $2 combo):** Risk $116.00, Paid $31.20, P/L -$84.80, ROI -73.10%

## Summary

Five F5 positions ($82 risk, 70.7% of the $116 slate) went 0-5: SF@CLE NO (Cleveland wins F5), TOR@TB YES (TB wins F5), ATL@CWS YES (CWS wins F5), ATH@KC NO (KC wins F5), WSH@TEX YES (WSH wins F5). The only winner was Kansas City 5+ runs (+$17.20) on the same ATH@KC game whose F5 NO leg lost. Athletics 4+ runs also lost (final KC 6, ATH 2). The 7-leg combo (STL ML/STL 5+/SF ML/ATH ML/ATH 4+/TB F5/CWS F5) is now canonically imported as a single MULTI_LEG wager — see Multi-Leg Combo.

## Key Finding

Primary failure was portfolio construction and F5-family concentration, not any single bad handicap. A protected F5 (buying NO on the opponent's F5-win contract) only protects against a tie in the first five innings — it does not protect against a genuine wrong-side outcome, and both protected-style NO legs here (SF@CLE, ATH@KC) lost outright alongside the plain F5-YES legs. Do not retune model probabilities from this result, and do not treat this day's F5 cluster as evidence that protected F5 should be a default expression.

## Family Breakdown (canonical, straight-only)

- **F5 (inning_result, F5 horizon):** 0-5, $82.00 risk, -$82.00 P/L
- **Team total:** 1-1, $32.00 risk, -$0.80 P/L (ATH 4+ lost, KC 5+ won)

## Same-Game Concentration

ATH @ KC carried three separate wagers (F5 NO-KC, ATH 4+, KC 5+): the F5 leg and ATH 4+ leg both lost while KC 5+ won — a mixed but still concentrated same-game exposure ($58 combined risk, -$26.80 net).

## Process Notes

- Protected F5 protects only tie risk, not a wrong-side handicap; it should not be treated as inherently safe or as a default expression.
- Compare F5 team YES / F5 Tie YES / F5 protected NO against full-game ML and team-total ladders before qualifying any F5 expression as a default; require a fee-adjusted positive-EV check with a conservative uncertainty haircut.
- One correlated market-family cluster (F5) was able to erase an otherwise survivable slate — tighter family-concentration limits are warranted.

## Multi-Leg Combo (canonically imported)

`2026-08-20|COMBO|STL_ML+STL_TT_5PLUS+SF_ML+ATH_ML+ATH_TT_4PLUS+TB_F5+CWS_F5|2.00` — betId `492da1d63ce32c607472ff13980fb8ffe5169f6b`, import batch `mlb-manual-2026-08-20-combo-v1`. 7-leg combo, **wagerStructure MULTI_LEG**, stake $2.00, paid $0.00, realized P/L **-$2.00**, result LOSS.

- leg-01 St. Louis moneyline — `KXMLBGAME-26AUG201240STLCIN-STL` — **WIN**
- leg-02 St. Louis 5+ runs (over 4.5 team runs) — `KXMLBTEAMTOTAL-26AUG201240STLCIN-STL5` — **WIN**
- leg-03 San Francisco moneyline — `KXMLBGAME-26AUG201310SFCLE-SF` — **LOSS**
- leg-04 Athletics moneyline — `KXMLBGAME-26AUG201410ATHKC-ATH` — **LOSS**
- leg-05 Athletics 4+ runs (over 3.5 team runs) — `KXMLBTEAMTOTAL-26AUG201410ATHKC-ATH4` — **LOSS**
- leg-06 Tampa Bay first five innings — `KXMLBF5-26AUG201310TORTB-TB` — **LOSS**
- leg-07 Chicago White Sox first five innings — `KXMLBF5-26AUG201410ATLCWS-CWS` — **LOSS**

Share-card evidence: rounded risk $2 (**no raw Initial Cost is recorded in the original evidence** — none was invented), max payout $93.45, paid out $0.00. Previously `BLOCKED_SCHEMA_LIMITATION` (blocked artifact id `2026-08-20-combo-7leg-001`) purely because the ledger had no multi-leg representation; PR #196 added one. Every leg ticker resolved uniquely (1 archived candidate each) and every leg outcome is independently corroborated by archived settlement records. No fee, contract cost, or executed entry price is evidenced, so all of those fields are null — none was back-solved to make totals reconcile. Combo CLV is UNAVAILABLE (no entry price).
