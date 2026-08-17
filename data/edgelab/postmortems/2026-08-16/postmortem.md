# Postmortem — 2026-08-16

**Strong overall day: 12-3, +$116.86 user P/L, +49.73% ROI.**

Market-expression selection was the biggest success of the day.

## Findings

- Protected F5 winner-NO remains useful when the handicap is starter-driven; SEA/HOU demonstrated the value of tie protection. The ATL loss was a handicap failure, not a contract-structure failure.
- Seattle/Houston was a correlated cluster: HOU F5 NO, SEA ML, and Brown under 18 outs all won. Do not infer that winning justifies unrestricted same-thesis concentration.
- Logan Henderson 6+ K hit exactly six. Continue preferring central ladder thresholds with real edge rather than extreme K tails.
- STL team total 4+ was strong process: Cabrera return/workload risk materialized and STL scored 11 anyway.
- Miami ML + Miami TT were correlated but the thesis was supported by Perez/run prevention plus Cincinnati bullpen vulnerability.
- NYY/TOR U6.5 lost only after extra innings; the game was 2-2 after nine and finished 4-3. For starter-driven low-total theses, explicitly compare full-game under with F5 under, since extras/bullpens add risk.
- COL/SF U8.5 was the clearest bad handicap: both starters were hit hard and the final was 13-7. Avoid overweighting attractive xERA/xFIP on volatile or lightly established starters.
- By family: team totals 3-0, moneyline 3-0, pitcher props 2-0, protected F5 sides 2-1, F5 totals 1-0, YRFI 1-0, full-game totals 0-2. Treat this as descriptive, not a sufficient sample to change calibration rules by itself.
- Do not change staking/model/recommendation production logic based solely on this one day's results.

## Settlement provenance

Authoritative settlement (scripts/edgelab/settle_markets.py) was run for 2026-08-16 and returned SETTLEMENT_UNRESOLVED for all 5,143 observed markets -- this sandboxed session's network egress to statsapi.mlb.com is policy-blocked (confirmed: `CONNECT tunnel failed, response 403`), so no live linescore/boxscore fetch could complete. All 15 bets were instead settled through the sanctioned manual-receipt path (`lib.edgelab.bets.confirm_realized_return`, source=`MANUAL_POSTMORTEM_RECEIPT`) from user-confirmed Kalshi screenshots -- canonical `result`/`status` fields remain `pending`/`None` on these bets; only `confirmedReceiptReturn`/`confirmedReceiptNetProfitLoss` carry the real settled economics, exactly the same pattern already used for the 2026-08-11 postmortem. Pitcher-prop markets (Henderson 6+ K, Brown 18+ outs) are covered by the repo's existing issue #43 automatic settlement logic once a live boxscore fetch is available -- no special handling was needed or invented here; they simply share the same network-blocked SETTLEMENT_UNRESOLVED state as every other family this run.
