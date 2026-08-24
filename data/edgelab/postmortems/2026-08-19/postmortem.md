# 2026-08-19 Postmortem — MLB Kalshi Slate (BLOCKED_MISSING_EVIDENCE)

**No wagers imported.** This date could not be reconciled into the canonical bet ledger.

## Why

Only an aggregate summary was available for 2026-08-19: 20 positions, 7 positive / 13 negative, risk $340, paid $274.35, P/L -$65.65, ROI -19.31%, with a market-family P/L breakdown (F5 +$24.30, pitcher outs +$11.18, team totals -$9.13, full-game ML -$60, game total -$18, NRFI -$12, parlay -$2). No exact per-wager manifest (team/market/threshold/side/displayed price/stake for each of the 20 wagers) was ever supplied.

An exhaustive search of the repository — `data/edgelab/bets/bets.jsonl`, `data/session_bets/`, `BET_LOG.md`, `data/lineup_audit_2026-08-19.*`, `data/f5_audit_2026-08-19.*`, `archive/data`, `archive/scripts`, `archive/workflows`, `data/research/wagers.csv`, and all git branches/commit history (including a pickaxe search for the string `manual-2026-08-19`) — found no per-wager manual-execution evidence for this date anywhere. The only existing 2026-08-19 ledger row is an unrelated $4.5 `source=MODEL`/`entryMethod=LEGACY_BACKFILL` bet (WSH@TEX F5_ML_Away) from the automated model pipeline. `data/execution_slip_2026-08-19.{json,txt}` is that same automated model's own paper-trading recommendation slip, not evidence of the user's manual wagers.

Per explicit instruction, individual wagers were never reverse-engineered from the aggregate summary or its market-family breakdown, and no ticket was ever guessed. All implied wagers for this date are therefore `BLOCKED_MISSING_EVIDENCE`.

## Preserved Qualitative Lessons

- Too many mediocre full-game MLs were stacked.
- Precise starter/workload props sometimes expressed the thesis better than ML.
- Cap same-game and family concentration.
- Opposing-team three-way F5 YES often proved inefficient.
- Do not treat favorite or plus-money status as a substitute for calibrated edge.

## Follow-Up

If the exact 20-wager manifest becomes available (in the same form supplied for 2026-08-20 through 2026-08-23), this date can be reconciled through the same canonical import path (`scripts/edgelab/import_bet_batch.py`, importBatchId `manual-2026-08-19-postmortem-v1`) in a follow-up pass.
