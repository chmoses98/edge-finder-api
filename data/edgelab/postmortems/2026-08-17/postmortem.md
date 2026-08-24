# Aug 17, 2026 Kalshi MLB Slate Postmortem

**importBatchId:** `manual-2026-08-17-slate-postmortem-v1`

## Summary

- 8 confirmed bets
- 5-3 record
- $138.00 rounded true risk (user-facing, CEILING of each screenshot Initial Cost to the next whole dollar)
- $154.10 exact screenshot Paid Out returned
- +$16.10 net P/L
- +11.67% ROI

Raw screenshot Initial Costs summed to $135.73; per this user's explicit accounting convention, the true-risk figure used for stake/ROI is the sum of each individual Initial Cost rounded UP to the next whole dollar ($138.00), not the raw sum. The screenshot's exact Paid Out amount is preserved separately, verbatim, from this rounded risk figure.

## Bet-by-bet

| # | Wager | Price | Rounded Stake | Paid Out | Result | User P/L |
|---|-------|-------|---------------|----------|--------|----------|
| 1 | CWS team total over 3.5 (4+ runs) YES | 50c | $20 | $38.64 | WIN | +$18.64 |
| 2 | ATL ML YES | 56c | $10 | $0.00 | LOSS | -$10.00 |
| 3 | DET ML YES | 50c | $14 | $27.05 | WIN | +$13.05 |
| 4 | KC team total over 4.5 (5+ runs) YES | 53c | $18 | $32.88 | WIN | +$14.88 |
| 5 | NYM team total over 3.5 (4+ runs) YES | 56c | $18 | $0.00 | LOSS | -$18.00 |
| 6 | KC F5 winner (three-way) YES | 55c | $10 | $17.62 | WIN | +$7.62 |
| 7 | STL (G1) team total over 4.5 (5+ runs) YES | 53c | $28 | $0.00 | LOSS | -$28.00 |
| 8 | STL (G1) ML YES | 51c | $20 | $37.91 | WIN | +$17.91 |

Totals: 5 wins, 3 losses. Rounded risk $138.00. Paid Out $154.10. Net +$16.10. ROI +11.67%.

## Key findings

1. **Best-expression success -- CWS team total.** The manual initially considered the White Sox moneyline side but correctly upgraded the expression to CWS over 3.5 runs / 4+ runs around 50 cents. This isolated the favorable offensive matchup without requiring Chicago's pitching/defense to produce a game win. It won and produced +$18.64, the largest individual profit in the batch.

2. **Kansas City thesis succeeded across two correlated expressions.** KC over 4.5 runs at 53 cents and KC F5 winner at 55 cents both won. Combined rounded risk was $28 and combined user-facing profit was +$22.50. The common thesis was attacking Oakland starter Mason Barnett early. Recorded as correlated / shared-thesis exposure rather than independent evidence.

3. **Detroit ML was a strong price-sensitive side.** Detroit ML at 50 cents won. The handicap preferred Detroit's starting-pitcher quality/length and viewed a pick'em price as favorable. User-facing profit was +$13.05.

4. **Cardinals Game 1 illustrates expression risk.** STL ML at 51 cents won while STL over 4.5 team runs at 53 cents lost. Combined rounded risk was $48 and combined P/L was -$10.09. The side handicap was directionally correct, but the larger team-total position required a much more specific offensive outcome. Future manuals should be cautious about over-weighting "weak starter + taxed bullpen" into aggressive team-total sizing when a cheap side is also available.

5. **NYM team-total thesis failed without necessarily proving a process flaw.** NYM over 3.5 runs at 56 cents lost. The wager was selected as a cleaner offensive expression than the full-game side, but the offense did not deliver. Marked as a failed thesis/outcome; review CLV before deciding whether the selection process itself was poor. CLV is currently **unavailable** for this ticker -- no valid pre-first-pitch archived observation exists in the corpus (the only late-day capture for this ticker landed after first pitch and was correctly excluded).

6. **Atlanta was a thinner edge and appropriately smaller.** Atlanta ML at 56 cents lost. It was one of the lower-conviction wagers on the final card and had only $10 rounded risk. Do not overreact to one result, but include it in market-selection/CLV review.

7. **Correlation discipline remains important.** The two KC wagers won together, but their outcomes were positively correlated. Do not count the 2-0 KC cluster as two fully independent validations. Conversely, the two STL Game 1 wagers demonstrate how correlated exposure can magnify a partially wrong expression even when the underlying side wins.

8. **Price discipline remained valuable.** Several successful positions were bought around 50-55 cents rather than laying large favorite prices. The decision to pass expensive Dodgers F5 / Sugano-under pricing should remain classified as a disciplined pass, not something to retroactively judge solely by the game result.

## CLV note

CLV was computed only from valid, executable, pre-game archived market observations (never a post-first-pitch quote):

- **Linked (CLV available):** CWS team total, ATL ML, DET ML, KC team total, KC F5 winner.
- **Unavailable (no valid pregame observation in the archived corpus):** NYM team total (only post-first-pitch captures exist for that ticker that day), STL Game 1 team total, STL Game 1 ML (the archived capture pipeline's first observations for the 13:40 ET STL@CIN Game 1 window all post-date that game's actual start). These are recorded as CLV unavailable, not fabricated.

## Postmortem tags / lesson categories

`BEST_EXPRESSION`, `TEAM_TOTAL`, `F5_THREE_WAY`, `PRICE_DISCIPLINE`, `CORRELATED_EXPOSURE`, `STARTER_MISMATCH`, `BULLPEN_THESIS`, `MANUAL_OVERRIDE`, `USER_ROUNDED_COST_ACCOUNTING`
