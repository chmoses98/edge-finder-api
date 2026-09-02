# 2026-09-01 Manual Postmortem

**User-reported record (10 positions):** 6-4 · **Exact cost:** $259.98 · **Exact paid out:** $328.33
· **Exact P/L:** +$68.35 · **Canonical whole-dollar P/L:** +$68.33 · **ROI:** +26.29%

## Import gap -- one position not canonically importable

Of the 10 user-confirmed 9/1 positions, **9 were successfully written** through
`scripts/edgelab/import_bet_batch.py` under `importBatchId=manual-postmortem-20260901-v1`
(8 straight + 1 MANUALCOMBO parlay). The 10th, **SD ML** (`manual-20260901-sdcin-sd-ml-003`,
stake $30, paid $0, LOSS), was returned as an explicit **UNRESOLVED** receipt: no
displayed probability, cents price, or maxPayout was ever reported for it, and
`entryPrice`/`entryOdds` (a required field) is never invented by this system. This is
consistent with the postmortem's own finding below that the SD ML market picture was
cross-book conflicted pregame -- fittingly, it is also the one bet in this sample with
no recorded execution price.

**This repository's canonical totals for the 9 linked bets are therefore lower than the
user's full 10-position summary by exactly SD ML's own contribution** ($30 stake / -$30
net P/L): 9-bet canonical net P/L is **+$98.33**; adding back SD ML's -$30 reproduces
the user's original +$68.33 exactly. `totalsMatch` on the stored Postmortem record is
expected to read `False` for this reason -- the reported/canonical figures are
intentionally NOT forced to agree; see the family table below for exactly where the gap
sits (entirely inside the ML family: 3-1 canonical vs. 3-2 as user-reported).

To close this gap, the user needs to supply SD ML's displayed probability or cents
price from the original execution; it can then be imported as a follow-up row under a
new `sourceBetKey`/batch without touching any of the 9 already-written bets.

## Family breakdown (9 linked bets)

| Family | Record | Risk | P/L | ROI |
|---|---|---|---|---|
| F5 sides | 3-0 | $75 (canonical) | +$71.10 | +94.80% |
| ML | 3-1 (SD ML excluded, see above) | $135 | +$47.23 | +34.99% |
| Team total | 0-1 | $10 | -$10.00 | -100% |
| Parlay | 0-1 | $10 (canonical) | -$10.00 | -100% |

## Process postmortem

1. **CWS was the cleanest analytical win of the two-day sample.** Sean Burke threw 5.2
   scoreless (1 H, 4 BB, 6 K); the independent pregame read liked his underlying profile
   relative to the opposing starter and liked the CWS offense. Both the F5 and ML
   expression of that thesis won.
2. **CLE F5** was a clean starter-driven thesis -- Gavin Williams delivered an excellent
   outing (13 K).
3. **PHI F5** was a clean starter/matchup win.
4. **PIT ML WON the ticket (13-12), but the mechanism was not clean.** RESULT=WIN,
   PROCESS=MIXED -- a winning ticket is not by itself proof the handicap mechanism worked.
5. **NYY ML:** a starter/team edge translated cleanly into a full-game win.
6. **BAL:** BAL ML + BAL 6+ repeated essentially the same Baltimore/Coors narrative as
   8/31. Both lost both days -- SERIES/NARRATIVE ANCHORING, not fresh independent evidence.
7. **SD ML:** the cross-book market picture was conflicted before the bet. Meaningful
   cross-book disagreement should trigger INVESTIGATE/REDUCE/PASS, not a full-size wager
   pushed through the disagreement.
8. **The 9/1 5-leg parlay lost only because SD lost.** Combined with 8/31's 5-leg combo
   also going 4/5, this is NOT evidence that 5-leg parlays are good. Future fun parlays
   should generally use only the 3-4 strongest independent legs; do not add a marginal
   5th leg solely to increase payout.

## Combined 8/31 + 9/1 (24 canonically-linked positions, excluding SD ML)

23 successfully linked positions (14 + 9): F5 7-2 (+$116.32 canonical), ML 4-3
(canonical), team totals 0-4 (-$65.00), parlays 0-3 (-$30.00). See the repository's own
recomputed aggregates in the final report for the exact combined roll-up, which
deliberately differs from the user's supplied 24-position combined summary by the same
single SD ML gap described above.
