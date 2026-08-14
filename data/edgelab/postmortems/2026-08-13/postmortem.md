# 2026-08-13 Postmortem

## Summary

- 7 confirmed wagers, all Kalshi MLB.
- Record: 3-4.
- True rounded risk (each screenshot's Initial cost rounded up to the nearest whole dollar): **$127.00**.
- Total user-confirmed returned: **$87.36**.
- Net P/L: **-$39.64**.
- ROI: **-31.21%**.

## Market-family results (true-cost basis)

| Family | Record | Risk | Return | Net P/L | ROI |
|---|---|---|---|---|---|
| Strikeout props (pitcher_strikeouts) | 0-3 | $60.00 | $0.00 | -$60.00 | -100% |
| Pitcher outs | 1-0 | $15.00 | $25.54 | +$10.54 | +70.27% |
| F5 winner | 1-1 | $40.00 | $38.64 | -$1.36 | -3.40% |
| Full-game ML | 1-0 | $12.00 | $23.18 | +$11.18 | +93.17% |
| **All non-strikeout markets combined** | 3-1 | $67.00 | $87.36 | +$20.36 | ~+30.39% |

## Key findings

1. The day's loss was concentrated entirely in pitcher strikeout overs. All three K props lost and consumed $60, while all non-K markets combined were profitable.
2. Do NOT conclude from one slate that strikeout props should be abandoned. Treat the result as evidence that the K-prop manual process needs stronger decomposition.
3. Future K analysis should explicitly separate: projected batters faced/workload; recent pitch count and injury/return context; opponent lineup-specific K propensity and handedness; pitcher K%, whiff/swinging-strike skill and pitch mix; command/walk/pitch-efficiency downside; early-hook and unusual-venue risk.
4. **Payton Tolle** is the clearest diagnostic example: he threw eight dominant scoreless innings but finished with only four strikeouts. The K loss cannot be blamed on workload. Good run prevention must not be treated as equivalent to strikeout production.
5. **Max Fried** also pitched effectively from a run-prevention standpoint but failed the 6+ K threshold. His five-inning/88-pitch outing and recent return from the injured list were relevant workload/efficiency risks.
6. **Taj Bradley** 6+ K should receive a meaningful process downgrade. Field-of-Dreams pitcher-usage uncertainty was raised pregame; the recommendation was already reduced but remained the favorite wager. Bradley lasted only four innings, allowing five runs, seven hits and four walks -- performance independently justified the early hook (do NOT attribute it to the venue itself); the lesson is that workload-dependent K probability was still too confidently estimated under elevated uncertainty.
7. **Aaron Nola NO** on 18+ outs was well structured: Nola pitched effectively and struck out nine yet still recorded only 15 outs. The bet could win without requiring poor pitching performance.
8. **Philadelphia ML** was a cleaner expression of the PHI-MIN handicap than Bradley's K prop. Philadelphia won 7-1.
9. **Boston F5** was a cleaner expression of the Boston/Tolle game handicap than Tolle 6+ Ks. Boston led after five even though the K prop lost.
10. **Chicago Cubs F5** was a substantive handicap miss rather than a close variance loss. Washington won 7-0; Cade Cavalli dominated and Kevin Gausman struggled. (This repository's archived pregame research corpus was consulted for this date/game and did not surface a specific, evidence-backed pregame miss beyond the general Cubs-favorable pricing already reflected in the entry price -- no hindsight explanation is invented here.)

## CLV / closing-price audit

**Result: CLV is UNAVAILABLE for all 7 bets in this batch (and, on the same investigation, for all 13 2026-08-12 bets as well). This is a corpus-level data gap, not a per-bet judgment call, and no CLV number is reported below.**

Evidence for this conclusion:

- `data/edgelab/games/2026-08-13.jsonl` (and `2026-08-12.jsonl`) has `scheduledStartTime: null` and `actualStartTime: null` for every game on both dates -- no game-start boundary is recorded anywhere in the archived corpus for either date.
- `data/edgelab/observations/2026-08-13.jsonl.gz` (and `2026-08-12`) likewise never populates `scheduledStart` on any MarketObservation for these dates (checked directly).
- The repository's own closing-quote selector (`lib.edgelab.checkpoints.select_closing_quote`) only excludes a candidate quote when `capturedAt >= start_dt` -- with `start_dt` unresolvable for these dates, that guard never fires, so it silently falls back to the LAST observation captured that day, with no pregame/post-start distinction at all.
- Directly inspecting that "last observation" for several tickers confirms it is a **post-result** quote, not a closing line: e.g. the Yordan Alvarez 2+ hits market's (2026-08-12) last captured quote is `yesBid: 0.0` at 22:08 UTC -- consistent with the market having already traded to its settled value after the bet lost, not a legitimate pregame close.
- Running the sanctioned `scripts/edgelab/collect_clv.py` for both dates confirmed this empirically (`clv_computed=13` / `clv_computed=7`, 0 marked unavailable) -- but several of the resulting "CLV" values were extreme (e.g. +50, -59 percentage points) in the direction consistent with the known result, exactly what an in-progress/post-start capture produces and exactly what must never be reported as CLV per this task's own instruction. That run's writes were reverted in this session (not committed) rather than left in the ledger, and every affected bet's `clv`/`closingPrice`/`clvQuoteId` fields were explicitly reset to `null`.

No CLV is fabricated or inferred from the winning/losing side of any bet. If a future ingestion run backfills `scheduledStartTime`/`actualStartTime` for these dates, `scripts/edgelab/collect_clv.py --date 2026-08-13` (and `--date 2026-08-12`) can be safely rerun to compute real CLV then.

## Settlement/accounting note

All 7 bets were recorded through the canonical manual-bet importer (`scripts/edgelab/import_bet_batch.py`, `importBatchId=manual-2026-08-13-postmortem-v1`) and settled economically via the ONE sanctioned manual-receipt path (`lib.edgelab.bets.confirm_realized_return`, `source=MANUAL_POSTMORTEM_RECEIPT`) from the user's confirmed Kalshi screenshots. Automatic settlement (`scripts/edgelab/settle_markets.py`) could not be (re)run against a live MLB Stats API in this sandboxed session (network egress to that host is blocked here), so canonical `result`/`status` remain `pending`/`None` on these bets; only `confirmedReceiptReturn`/`confirmedReceiptNetProfitLoss` carry the real, user-confirmed settled economics used throughout this postmortem. GitHub issue #43 (automatic pitcher/hitter player-prop settlement) is in fact CLOSED/merged in this repository (PR #44) -- the limitation here is this session's lack of network access to refresh box-score evidence, not a missing prop-settlement feature, and no one-off automatic settlement logic was added to work around either.

Each bet's `stake` is the user's rounded whole-dollar TRUE COST (displayed Kalshi "Initial cost" rounded up), per the user's accounting rule. The exact unrounded displayed Initial cost and Paid Out are preserved verbatim on each bet's `shareCardEvidence` field for audit.
