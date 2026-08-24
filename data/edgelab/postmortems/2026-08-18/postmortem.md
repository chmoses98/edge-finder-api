# 2026-08-18 Postmortem — MLB Kalshi Slate (BLOCKED_MISSING_EVIDENCE)

**No wagers imported.** This date could not be reconciled into the canonical bet ledger.

## Why

Only an aggregate summary was available for 2026-08-18: record 7-5, rounded risk $180, paid $195.96, P/L +$15.96, ROI +8.87% (non-pitcher bets 6-2, +$40.77; pitcher props 1-3, -$24.81). No exact per-wager manifest (team/market/threshold/side/displayed price/stake for each of the ~12 wagers) was ever supplied.

An exhaustive search of the repository — `data/edgelab/bets/bets.jsonl`, `data/session_bets/`, `BET_LOG.md`, `data/lineup_audit_2026-08-18.*`, `data/f5_audit_2026-08-18.*`, `archive/data`, `archive/scripts`, `archive/workflows`, and all git branches/commit history (including a pickaxe search for the string `manual-2026-08-18`) — found no per-wager manual-execution evidence for this date anywhere. The only existing 2026-08-18 ledger row is an unrelated $3 `source=MODEL`/`entryMethod=LEGACY_BACKFILL` bet (MIA@PHI ML_Away) from the automated model pipeline, not the user's manual slate. `data/execution_slip_2026-08-18.{json,txt}` is that same automated model's own paper-trading recommendation slip (1 real-money $3 rec, 64 paper, 100 rejected) — a different system, not evidence of the user's manual wagers.

Per explicit instruction, individual wagers were never reverse-engineered from the aggregate summary or from assistant/model recommendations, and no ticket was ever guessed. All implied wagers for this date are therefore `BLOCKED_MISSING_EVIDENCE`.

## Preserved Qualitative Lessons

- Non-pitcher markets drove profit.
- PIT/LAD team totals were strong expressions.
- ATL/CWS theses were relatively thin.
- Pitcher-prop confidence was too aggressive; favorite pricing does not make a pitcher prop low variance.
- Arizona protected F5 was executed beyond Bet Up To — logged as a price-discipline failure.
- Do not treat that day's apparent protected-F5 success as evidence the family should be default.

## Follow-Up

If the exact 12-wager manifest becomes available (in the same form supplied for 2026-08-20 through 2026-08-23), this date can be reconciled through the same canonical import path (`scripts/edgelab/import_bet_batch.py`, importBatchId `manual-2026-08-18-postmortem-v1`) in a follow-up pass.
