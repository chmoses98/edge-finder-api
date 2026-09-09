# 2026-08-21 Postmortem — MLB Kalshi Slate

**Positions:** 13 (12 straight + 1 multi-leg combo), all 13 now canonically imported. **Record (straight):** 9-3 | **Combo:** 0-1 (canonically imported, MULTI_LEG)
**Canonical totals (complete slate):** Risk $243.00, Paid $365.32, P/L +$122.32, ROI +50.34%
**Straight-only subtotal (12 positions, for comparison):** Risk $241.00, Paid $365.32, P/L +$124.32, ROI +51.59%
**User-reported full-slate totals (incl. $2 combo):** Risk $243.00, Paid $365.32, P/L +$122.32, ROI +50.34%

## Summary

The best day of the six reconciled. Team totals (BOS 4+, SEA 4+, PIT 3+) and F5 expressions (WSH-MIA NO/Miami, CLE F5 YES) both produced clean wins. SEA ML + SEA 4+ was a successful correlated same-game exposure. Yamamoto 19+ outs won on exactly 19 outs — a clean workload expression. Chris Sale 8+ Ks lost (finished 6), showing aggressive-ladder variance versus the cleaner workload expression that won the same day. The 6-leg combo (CWS F5/CLE F5/DET ML/LAD ML/BOS 4+/HOU 5+) is now canonically imported as a single MULTI_LEG wager — see Multi-Leg Combo.

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

## Multi-Leg Combo (canonically imported)

`2026-08-21|COMBO|CWS_F5+CLE_F5+DET_ML+LAD_ML+BOS_TT_4PLUS+HOU_TT_5PLUS|2.00` — betId `5dbad7ab6e08a7c3a25b6b5e11ee172abff2524d`, import batch `mlb-manual-2026-08-21-combo-v1`. 6-leg combo, **wagerStructure MULTI_LEG**, stake $2.00, paid $0.00, realized P/L **-$2.00**, result LOSS.

- leg-01 Chicago White Sox first five innings — `KXMLBF5-26AUG211940NYMCWS-CWS` — **LOSS**
- leg-02 Cleveland first five innings — `KXMLBF5-26AUG212040CLECOL-CLE` — **WIN**
- leg-03 Detroit moneyline — `KXMLBGAME-26AUG212010DETKC-DET` — **LOSS**
- leg-04 Los Angeles Dodgers moneyline — `KXMLBGAME-26AUG212210PITLAD-LAD` — **WIN**
- leg-05 Boston 4+ runs (over 3.5 team runs) — `KXMLBTEAMTOTAL-26AUG211910SFBOS-BOS4` — **WIN**
- leg-06 Houston 5+ runs (over 4.5 team runs) — `KXMLBTEAMTOTAL-26AUG212010ATHHOU-HOU5` — **LOSS**

Share-card evidence: raw Initial Cost $1.99, rounded risk $2, max payout $88.88, paid out $0.00. Previously `BLOCKED_SCHEMA_LIMITATION` (blocked artifact id `2026-08-21-combo-6leg-001`) purely because the ledger had no multi-leg representation; PR #196 added one. Every leg ticker resolved uniquely (1 archived candidate each) and every leg outcome is independently corroborated by archived settlement records. No fee, contract cost, or executed entry price is evidenced, so all of those fields are null — none was back-solved to make totals reconcile. Combo CLV is UNAVAILABLE (no entry price).
