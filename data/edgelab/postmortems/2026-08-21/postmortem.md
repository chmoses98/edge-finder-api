# 2026-08-21 Postmortem — MLB Kalshi Slate

**Record (straight):** 9-3 | **Parlay:** 0-1 (BLOCKED_SCHEMA_LIMITATION, excluded from canonical ledger)
**Canonical (straight-only) totals:** Risk $241.00, Paid $365.32, P/L +$124.32, ROI +51.59%
**User-reported full-slate totals (incl. $2 parlay):** Risk $243, Paid $365.32, P/L +$122.32, ROI +50.34%

## Summary

The best day of the six reconciled. Team totals (BOS 4+, SEA 4+, PIT 3+) and F5 expressions (WSH-MIA NO/Miami, CLE F5 YES) both produced clean wins. SEA ML + SEA 4+ was a successful correlated same-game exposure. Yamamoto 19+ outs won on exactly 19 outs — a clean workload expression. Chris Sale 8+ Ks lost (finished 6), showing aggressive-ladder variance versus the cleaner workload expression that won the same day. A 6-leg combo/parlay (rounded risk $2, paid $0) is documented but could not be imported — see Blocked Wagers.

## Lessons

- Team totals produced clean expressions.
- SEA ML + SEA 4+ was a successful correlated exposure (not two independent validations).
- Protected F5 can work but should not be default — see Aug 20's counterexample.
- Yamamoto outs was a clean workload expression.
- Sale 8+ Ks demonstrated aggressive-ladder variance.
- Add F3/F5 Tie YES into expression review.
- Do not retune model probabilities from a single great day.

## Family Breakdown (canonical, straight-only)

- **F5 (inning_result):** 2-0, $42.00 risk, +$36.64
- **Team total:** 4-1, $102.00 risk, +$55.09
- **First-inning run (NRFI):** 1-0, $20.00 risk, +$15.25 (CLV_UNAVAILABLE)
- **Game total:** 1-0, $25.00 risk, +$20.66
- **Moneyline:** 1-1, $50.00 risk, +$11.68
- **Pitcher strikeouts:** 0-1, $10.00 risk, -$10.00 (CLV_UNAVAILABLE)
- **Pitcher outs:** 1-0, $12.00 risk, +$13.60 (CLV_UNAVAILABLE)

## Same-Game Concentration

ATL @ MIL carried three wagers (NRFI, Under 6.5, Sale 8+Ks): NRFI and Under 6.5 are correlated low-scoring theses that both won; Sale Ks is a distinct pitcher-ladder thesis that lost. $55 combined risk, +$25.91 net.

## CLV Coverage

3 of 12 straight bets are CLV_UNAVAILABLE: ATL-MIL NRFI, CHC@SEA ML, and PIT@LAD Yamamoto 19+ outs. In each case no archived MarketObservation for that exact ticker ever recorded a resolved scheduledStart on 2026-08-21 (or the adjacent UTC date), so `collect_clv.py` could not determine a valid closing quote. This was verified against the raw observation archive (not assumed) and is not fabricated.

## Blocked Wagers

- **2026-08-21-combo-6leg-001** — `BLOCKED_SCHEMA_LIMITATION`. 6-leg combo/parlay (raw Initial Cost $1.99, rounded risk $2, paid $0, max payout $88.88; legs: CWS F5 vs NYM L, CLE F5 vs COL W, DET ML vs KC L, LAD ML vs PIT W, BOS 4+ runs W, HOU 5+ runs L). No canonical representation for a multi-leg combo exists; excluded from canonicalTotals. Accounts for the $2 gap between the user-reported full-slate P/L (+$122.32) and the canonical straight-only P/L (+$124.32).
