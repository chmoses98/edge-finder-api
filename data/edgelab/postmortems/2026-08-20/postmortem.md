# 2026-08-20 Postmortem — MLB Kalshi Slate

**Record (straight):** 1-6 | **Parlay:** 0-1 (BLOCKED_SCHEMA_LIMITATION, excluded from canonical ledger)
**Canonical (straight-only) totals:** Risk $114.00, Paid $31.20, P/L -$82.80, ROI -72.63%
**User-reported full-slate totals (incl. $2 parlay):** Risk $116, Paid $31.20, P/L -$84.80, ROI -73.10%

## Summary

Five F5 positions ($82 risk, 70.7% of the $116 slate) went 0-5: SF@CLE NO (Cleveland wins F5), TOR@TB YES (TB wins F5), ATL@CWS YES (CWS wins F5), ATH@KC NO (KC wins F5), WSH@TEX YES (WSH wins F5). The only winner was Kansas City 5+ runs (+$17.20) on the same ATH@KC game whose F5 NO leg lost. Athletics 4+ runs also lost (final KC 6, ATH 2). A 7-leg combo/parlay (rounded risk $2, paid $0) is documented below but could not be imported into the canonical ledger — see Blocked Wagers.

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

## Blocked Wagers

- **2026-08-20-combo-7leg-001** — `BLOCKED_SCHEMA_LIMITATION`. 7-leg combo/parlay (rounded risk $2, paid $0, max payout $93.45; legs: STL ML W, STL 5+ W, SF ML L, ATH ML L, ATH 4+ L, TB F5 L, CWS F5 L). The canonical bet schema represents one wager as exactly one marketTicker+side+entryPrice against one archived market and has no representation for a single Kalshi multi-leg combo spanning 7 distinct tickers/games with one combined stake/payout; no per-leg entry price was supplied, so it was neither force-fit onto one leg's ticker nor split into fabricated independent rows. Excluded from canonicalTotals — accounts for the full $2 gap between the user-reported full-slate P/L (-$84.80) and the canonical straight-only P/L (-$82.80).
