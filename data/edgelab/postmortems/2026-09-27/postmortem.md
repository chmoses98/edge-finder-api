# Postmortem — MLB 2026-09-21 → 2026-09-27 (recovered-week REAL wager review)

Scope: the 13 REAL wagers on the canonical ledger for game dates 2026-09-21 → 2026-09-28. All 13 were delivered by kalshi-bet-router (`importBatchId=kalshi-router-v1`) and all are settled. 2026-09-21 and 2026-09-28 had **no** MLB executions. The router's authenticated fill walk (audit run 36513184809) found 223 post-cutover orders: 78 MLB (all on main), 75 CFB, 56 NFL, 5 soccer and 9 NFL combos. None of the refused orders is MLB. Legacy, model and paper rows are excluded throughout.

## Result

**10-3, +$157.28 on $515.14 risked, +30.53% ROI. Fees $7.45 (1.45% of risked).**

| date | REAL | W-L | risked | P/L | ROI |
|---|---|---|---|---|---|
| 2026-09-22 | 1 | 1-0 | $50.7145 | +28.7555 | +56.70% |
| 2026-09-23 | 1 | 0-1 | $79.4797 | -79.4797 | -100.00% |
| 2026-09-24 | 1 | 1-0 | $79.9943 | +58.2657 | +72.84% |
| 2026-09-25 | 1 | 1-0 | $34.9893 | +41.9307 | +119.84% |
| 2026-09-26 | 5 | 4-1 | $149.9797 | +81.1203 | +54.09% |
| 2026-09-27 | 4 | 3-1 | $119.9870 | +26.6830 | +22.24% |
| **combined** | **13** | **10-3** | **$515.1445** | **+$157.2755** | **+30.53%** |

**This is a small, lucky-looking sample, not evidence of edge.** At the prices paid, expected wins were 7.54 of 13. A result of 10 or more would happen about 13% of the time with zero edge. A bootstrap 95% interval on ROI is roughly −20% to +75%.

## By market family

| family | n | W-L | risked | share | P/L | ROI |
|---|---|---|---|---|---|---|
| team_total (KXMLBTEAMTOTAL) | 12 | 9-3 | $464.4300 | 90.2% | +128.5200 | +27.67% |
| game_result (KXMLBGAME) | 1 | 1-0 | $50.7145 | 9.8% | +28.7555 | +56.70% |

Every team-total wager was a YES "over". Validated research currently has team totals **trailing** the market: RSCH-0027 Δ Brier +0.0447; frozen forward scorecard 2026-09-28 +0.0062 [0.0027, 0.0098]. The negative-binomial conversion fix is unpromoted and still in shadow (RSCH-0035). A good week in the one family the evidence rates worst is variance until proven otherwise.

## By price band (entry price)

| band | n | W-L | risked | P/L |
|---|---|---|---|---|
| <0.45 | 1 | 1-0 | $34.99 | +41.93 |
| 0.45–0.55 | 4 | 4-0 | $129.98 | +117.19 |
| 0.55–0.65 | 6 | 4-2 | $295.18 | +19.68 |
| 0.65–0.80 | 2 | 1-1 | $54.99 | -21.52 |

The highest entry was 0.74 (AZ 3+, +$8.48); there was nothing near the 0.98 of the prior review. Stake-weighted entry was 0.581.

## Games, correlation, repeat theses

- **No same-game multi-wager exposure**: 13 wagers on 13 different games.
- **Repeat theses across consecutive days in the same series**:
  - CWS over vs COL on 9/26 and 9/27: $74.99, both won, +$54.11.
  - TB over at PHI on 9/26 and 9/27: $59.99, both won, +$59.53.
  - CIN@TOR, opposite teams' overs on 9/26 (TOR 4+) and 9/27 (CIN 3+): both lost, −$55.00.
  - LAA@SEA, opposite teams' overs on 9/24 (SEA 4+) and 9/25 (LAA 4+): both won.

  Series-level "runs environment" theses behave like correlated exposure even across days.
- Largest positions: SEA 4+ $79.99 (won +58.27) and ATL 4+ $79.48 (lost −79.48, the single largest loss). The two biggest bets roughly cancelled.

## Sizing

Stakes ran $24.99–$79.99, median $30.00, mean $39.63. The prior review ran $18.00–$140.00, median $52.50, mean $57.51. Total capital deployed was 24% of the prior four days despite covering seven days.

## Expression quality

Every team-total entry was on a single rung, and no alternative rung or game total was bought for the same thesis. Archived evidence is not sufficient to score whether a different rung or a game total would have been the more efficient expression. That comparison is **not made** here.

## CLV

**Not usable.** Of the 13 rows, only 3 have TRUE_CLOSE (T−30) closing evidence (CLV 0.0, −1.94, 0.0). 9 are PRE_CLOSE/FIRST_DAILY and 1 has none. Near-close capture delivered 23.9% of windows over the trailing seven days (capture_health 2026-09-28). No average CLV is reported.

## What happened operationally

Twelve of these wagers (9/23–9/27) were captured by the router within hours but sat on its proposal branch (PR #247) until 2026-09-28 15:54Z. That was up to 4.7 days late, while every health surface stayed green. Settlement run 36447165882 then graded all 12 with 0 pending. Fixes: kalshi-bet-router #99/#100/#101 and edge-finder-api #250/#252/#253.

## Do not conclude

- That team totals have become profitable, or that the model improved. The market-comparison evidence says the opposite, and n=12.
- That smaller stakes caused the result.
- Anything from CLV.
